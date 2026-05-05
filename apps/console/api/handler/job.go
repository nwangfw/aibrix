/*
Copyright 2025 The Aibrix Team.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

// JobHandler implements the Console BFF JobService:
//
//   - Forwards Create/List/Get to the Planner. The Planner currently runs
//     as a synchronous passthrough that calls the resource manager and
//     submits the OpenAI batch to the metadata service inline; production
//     will swap in a queued + worker-driven implementation behind the same
//     interface.
//   - Cancellation goes through MDS directly (the OpenAI Batches API
//     exposes /cancel and the planner does not own cancellation), so
//     CancelJob calls a thin BatchCanceller adapter.
//   - Persists Console-owned fields (id, display name, created_by, ...) on
//     the OpenAI batch.metadata map under the aibrix.console.* namespace,
//     so a single source of truth (MDS) holds the user-visible fields.
package handler

import (
	"context"
	"errors"
	"crypto/rand"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/openai/openai-go/v3"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"k8s.io/klog/v2"

	pb "github.com/vllm-project/aibrix/apps/console/api/gen/console/v1"
	"github.com/vllm-project/aibrix/apps/console/api/middleware"
	plannerapi "github.com/vllm-project/aibrix/apps/console/api/planner/api"
	"github.com/vllm-project/aibrix/apps/console/api/store"
)

// Console-owned fields we stash on the OpenAI batch.metadata map. Namespaced
// to keep them out of user-supplied metadata's key space. The bare
// "display_name" key is kept for backwards compatibility with batches
// created by older console builds.
const (
	metadataDisplayName            = "display_name" // legacy fallback
	metadataConsoleDisplayName     = "aibrix.console.display_name"
	metadataConsoleCreatedBy       = "aibrix.console.created_by"
	metadataConsoleTemplateName    = "aibrix.console.template_name"
	metadataConsoleTemplateVersion = "aibrix.console.template_version"
	defaultListLimit               = 20
)

// BatchCanceller is the narrow seam JobHandler uses to forward cancel
// directly to MDS. Cancellation deliberately bypasses the Planner per
// the planner contract (the OpenAI Batches API exposes /cancel and the
// planner observes the resulting state on its read overlay).
type BatchCanceller interface {
	CancelBatch(ctx context.Context, batchID string) (*plannerapi.BatchView, error)
}

// JobHandler implements console.v1.JobService.
type JobHandler struct {
	pb.UnimplementedJobServiceServer

	store                          store.Store
	planner                        plannerapi.Planner
	canceller                      BatchCanceller
	defaultModelDeploymentTemplate string
	devMode                        bool
}

// NewJobHandler creates a JobHandler that forwards write/read to the
// Planner and cancel to the BatchCanceller.
func NewJobHandler(s store.Store, planner plannerapi.Planner, canceller BatchCanceller, defaultModelDeploymentTemplate string, devMode bool) *JobHandler {
	return &JobHandler{
		store:                          s,
		planner:                        planner,
		canceller:                      canceller,
		defaultModelDeploymentTemplate: defaultModelDeploymentTemplate,
		devMode:                        devMode,
	}
}

// ListJobs forwards to Planner.ListJobs and translates JobView -> pb.Job.
func (h *JobHandler) ListJobs(ctx context.Context, req *pb.ListJobsRequest) (*pb.ListJobsResponse, error) {
	limit := defaultListLimit
	if req.Limit > 0 {
		limit = int(req.Limit)
	}
	resp, err := h.planner.ListJobs(ctx, &plannerapi.ListJobsRequest{
		Limit: limit,
		After: req.After,
	})
	if err != nil {
		// Dev fallback: serve Console's demo batches so the UI is usable
		// end-to-end without a running MDS.
		if h.devMode {
			if dev, ok := h.store.(interface{ ListDemoJobs() []*pb.Job }); ok {
				klog.Warningf("MDS unreachable, falling back to demo jobs: %v", err)
				return &pb.ListJobsResponse{Jobs: dev.ListDemoJobs(), HasMore: false}, nil
			}
		}
		klog.Warningf("list jobs failed; returning empty list: %v", err)
		return &pb.ListJobsResponse{Jobs: nil, HasMore: false}, nil
	}

	jobs := make([]*pb.Job, 0, len(resp.Data))
	for _, view := range resp.Data {
		jobs = append(jobs, jobFromView(view))
	}
	return &pb.ListJobsResponse{Jobs: jobs, HasMore: resp.HasMore}, nil
}

// GetJob forwards to Planner.GetJob.
func (h *JobHandler) GetJob(ctx context.Context, req *pb.GetJobRequest) (*pb.Job, error) {
	if req.Id == "" {
		return nil, status.Error(codes.InvalidArgument, "id is required")
	}
	view, err := h.planner.GetJob(ctx, req.Id)
	if err != nil {
		if h.devMode {
			if dev, ok := h.store.(interface {
				GetDemoJob(id string) (*pb.Job, bool)
			}); ok {
				if job, found := dev.GetDemoJob(req.Id); found {
					klog.Warningf("MDS unreachable, falling back to demo job %s: %v", req.Id, err)
					return job, nil
				}
			}
		}
		if errors.Is(err, plannerapi.ErrJobNotFound) {
			return nil, status.Error(codes.NotFound, err.Error())
		}
		return nil, mapPlannerError(err, "get job")
	}
	return jobFromView(view), nil
}

// CreateJob translates the Console request into an EnqueueRequest and forwards
// it to the Planner.
func (h *JobHandler) CreateJob(ctx context.Context, req *pb.CreateJobRequest) (*pb.Job, error) {
	if req.InputDataset == "" {
		return nil, status.Error(codes.InvalidArgument, "input_dataset is required")
	}
	if req.Endpoint == "" {
		return nil, status.Error(codes.InvalidArgument, "endpoint is required")
	}

	completionWindow := req.CompletionWindow
	if completionWindow == "" {
		completionWindow = string(openai.BatchNewParamsCompletionWindow24h)
	}

	createdBy := currentUserEmail(ctx)

	metadata := map[string]string{}
	if req.Name != "" {
		metadata[metadataConsoleDisplayName] = req.Name
		metadata[metadataDisplayName] = req.Name // legacy key, kept for back-compat reads
	}
	if createdBy != "" {
		metadata[metadataConsoleCreatedBy] = createdBy
	}
	if req.ModelTemplateName != "" {
		metadata[metadataConsoleTemplateName] = req.ModelTemplateName
	}
	if req.ModelTemplateVersion != "" {
		metadata[metadataConsoleTemplateVersion] = req.ModelTemplateVersion
	}

	templateName := req.ModelTemplateName
	if templateName == "" {
		templateName = h.defaultModelDeploymentTemplate
	}
	var modelTemplate *plannerapi.ModelTemplateRef
	if templateName != "" {
		modelTemplate = &plannerapi.ModelTemplateRef{
			Name:    templateName,
			Version: req.ModelTemplateVersion,
		}
	}

	jobID := "job-" + jobUUID()

	enqueueReq := &plannerapi.EnqueueRequest{
		JobID:         jobID,
		CreatedBy:     createdBy,
		ModelTemplate: modelTemplate,
		BatchPayload: plannerapi.BatchPayload{
			InputFileID:      req.InputDataset,
			Endpoint:         req.Endpoint,
			CompletionWindow: completionWindow,
			Metadata:         metadata,
		},
	}

	if _, err := h.planner.Enqueue(ctx, enqueueReq); err != nil {
		return nil, mapPlannerError(err, "enqueue job")
	}

	view, err := h.planner.GetJob(ctx, jobID)
	if err != nil {
		return nil, mapPlannerError(err, "fetch job")
	}
	return jobFromView(view), nil
}

// CancelJob bypasses the Planner and forwards directly to MDS, matching
// the planner contract (cancellation flows through MDS).
func (h *JobHandler) CancelJob(ctx context.Context, req *pb.CancelJobRequest) (*pb.Job, error) {
	if req.Id == "" {
		return nil, status.Error(codes.InvalidArgument, "id is required")
	}
	view, err := h.canceller.CancelBatch(ctx, req.Id)
	if err != nil {
		return nil, mapSDKError(err, "cancel batch")
	}
	return jobFromView(&plannerapi.JobView{
		JobID:        view.JobID,
		PlannerState: plannerapi.PlannerTaskStateSubmitted,
		Batch:        view,
	}), nil
}

// currentUserEmail returns the authenticated user's email if available, else
// empty. The auth middleware sets this on the HTTP request context; once the
// gateway propagates it to gRPC metadata it will surface here.
func currentUserEmail(ctx context.Context) string {
	if u := middleware.GetUser(ctx); u != nil {
		return u.Email
	}
	return ""
}

// mapSDKError translates an openai-go API error into a gRPC status, preserving
// the upstream message and using the upstream HTTP status to pick a code.
func mapSDKError(err error, op string) error {
	if err == nil {
		return nil
	}
	var apiErr *openai.Error
	if errors.As(err, &apiErr) {
		c := codes.Unknown
		switch apiErr.StatusCode {
		case http.StatusBadRequest:
			c = codes.InvalidArgument
		case http.StatusNotFound:
			c = codes.NotFound
		case http.StatusConflict:
			c = codes.FailedPrecondition
		case http.StatusUnauthorized, http.StatusForbidden:
			c = codes.PermissionDenied
		default:
			if apiErr.StatusCode >= 500 {
				c = codes.Unavailable
			}
		}
		return status.Error(c, apiErr.Error())
	}
	return status.Errorf(codes.Unavailable, "%s: %v", op, err)
}

// mapPlannerError converts planner sentinel errors into gRPC statuses,
// falling back to mapSDKError for transport-level failures wrapped by
// the planner.
func mapPlannerError(err error, op string) error {
	if err == nil {
		return nil
	}
	switch {
	case errors.Is(err, plannerapi.ErrInvalidJob):
		return status.Error(codes.InvalidArgument, err.Error())
	case errors.Is(err, plannerapi.ErrJobNotFound):
		return status.Error(codes.NotFound, err.Error())
	case errors.Is(err, plannerapi.ErrDuplicateEnqueue):
		return status.Error(codes.AlreadyExists, err.Error())
	case errors.Is(err, plannerapi.ErrStoreFull):
		return status.Error(codes.ResourceExhausted, err.Error())
	case errors.Is(err, plannerapi.ErrInsufficientResources):
		return status.Error(codes.ResourceExhausted, err.Error())
	case errors.Is(err, plannerapi.ErrStoreUnavailable):
		return status.Error(codes.Unavailable, err.Error())
	}
	return mapSDKError(err, op)
}

// jobFromView translates a planner JobView (which embeds the OpenAI
// batch fields via *BatchView) into the wire-level pb.Job the Console UI
// renders. Console-owned fields ride on batch.metadata under the
// aibrix.console.* namespace.
func jobFromView(view *plannerapi.JobView) *pb.Job {
	job := &pb.Job{}
	if view == nil {
		return job
	}
	if view.Batch != nil && view.Batch.Batch != nil {
		b := view.Batch.Batch
		job.Id = b.ID
		job.Object = string(b.Object)
		job.Endpoint = b.Endpoint
		job.Model = b.Model
		job.InputDataset = b.InputFileID
		job.CompletionWindow = b.CompletionWindow
		job.Status = string(b.Status)
		job.OutputDataset = b.OutputFileID
		job.ErrorDataset = b.ErrorFileID
		job.CreatedAt = b.CreatedAt
		job.InProgressAt = b.InProgressAt
		job.ExpiresAt = b.ExpiresAt
		job.FinalizingAt = b.FinalizingAt
		job.CompletedAt = b.CompletedAt
		job.FailedAt = b.FailedAt
		job.ExpiredAt = b.ExpiredAt
		job.CancellingAt = b.CancellingAt
		job.CancelledAt = b.CancelledAt
		if len(b.Metadata) > 0 {
			job.Metadata = map[string]string(b.Metadata)
			if v := b.Metadata[metadataConsoleDisplayName]; v != "" {
				job.Name = v
			} else if v := b.Metadata[metadataDisplayName]; v != "" {
				job.Name = v
			}
			if v := b.Metadata[metadataConsoleCreatedBy]; v != "" {
				job.CreatedBy = v
			}
			if v := b.Metadata[metadataConsoleTemplateName]; v != "" {
				job.ModelTemplateName = v
			}
			if v := b.Metadata[metadataConsoleTemplateVersion]; v != "" {
				job.ModelTemplateVersion = v
			}
		}
		if b.JSON.RequestCounts.Valid() {
			job.RequestCounts = &pb.JobRequestCounts{
				Total:     int32(b.RequestCounts.Total),
				Completed: int32(b.RequestCounts.Completed),
				Failed:    int32(b.RequestCounts.Failed),
			}
		}
		if b.JSON.Usage.Valid() {
			job.Usage = &pb.JobUsage{
				InputTokens:  b.Usage.InputTokens,
				OutputTokens: b.Usage.OutputTokens,
				TotalTokens:  b.Usage.TotalTokens,
			}
		}
	}
	if job.Id == "" && view.JobID != "" {
		job.Id = view.JobID
	}
	if view.CreatedBy != "" {
		job.CreatedBy = view.CreatedBy
	}
	return job
}

// jobUUID returns a short, opaque identifier for the planner JobID. The
// crypto/rand path is borrowed from the in-memory store helper to avoid
// pulling in another dependency at this seam.
func jobUUID() string {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		return fmt.Sprintf("ts-%d", time.Now().UnixNano())
	}
	return strings.ToLower(fmt.Sprintf("%x", b))
}
