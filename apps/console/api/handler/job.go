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
//   - Calls the metadata service /v1/batches API via the official OpenAI Go
//     SDK (openai-go v3). Talking to the metadata service through the SDK
//     keeps it honest about being OpenAI-compatible — schema drift on the
//     upstream side surfaces immediately as a deserialization or 4xx error.
//   - Persists Console-owned fields (id, display name, created_by, future:
//     organization, tags ...) in the local store.
//   - Aggregates both sources into the wire-level *pb.Job returned to the UI.
//
// The AIBrix-only extension `aibrix.model_template` is passed via the SDK's
// `option.WithJSONSet`, which is the OpenAI-recommended `extra_body` channel.
//
// When the metadata service is unreachable the handler propagates the error
// (codes.Unavailable). The frontend renders its mock fallback in that case.
package handler

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/openai/openai-go"
	"github.com/openai/openai-go/option"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"k8s.io/klog/v2"

	pb "github.com/vllm-project/aibrix/apps/console/api/gen/console/v1"
	"github.com/vllm-project/aibrix/apps/console/api/middleware"
	"github.com/vllm-project/aibrix/apps/console/api/store"
	"github.com/vllm-project/aibrix/pkg/planner"
)

const (
	metadataDisplayName = "display_name"
	defaultListLimit    = 20

	// Fallback GPU shape when the ModelDeploymentTemplate cannot be resolved
	// against the Console store (e.g. STORE_TYPE=mysql templates not migrated
	// yet). Matches the PlannerJob validation constraints (positive values).
	defaultPlannerGPUType       = "H100-SXM"
	defaultPlannerGPUCount      = 1
	defaultPlannerDurationHours = 24
)

// JobHandler implements console.v1.JobService.
type JobHandler struct {
	pb.UnimplementedJobServiceServer

	store                          store.Store
	openai                         openai.Client
	defaultModelDeploymentTemplate string
	// When non-nil, CreateJob runs through Scheduler (RM reserve → batch to
	// MDS) instead of calling the metadata service synchronously alone.
	scheduler *planner.Scheduler
}

// NewJobHandler creates a JobHandler.
// mds must be the openai.Client from planner.NewOpenAIClientForMetadataService,
// wired once in server.Server (shared with HTTPMDSSubmitter when planner runs).
//
// sched may be nil: when PLANNER_ENABLED=false the scheduler is unset and
// CreateJob behaves as a direct POST /v1/batches + overlay (legacy split).
func NewJobHandler(s store.Store, mds openai.Client, defaultModelDeploymentTemplate string, sched *planner.Scheduler) *JobHandler {
	return &JobHandler{
		store:                          s,
		openai:                         mds,
		defaultModelDeploymentTemplate: defaultModelDeploymentTemplate,
		scheduler:                      sched,
	}
}

// ListJobs proxies to GET /v1/batches and merges with store.
func (h *JobHandler) ListJobs(ctx context.Context, req *pb.ListJobsRequest) (*pb.ListJobsResponse, error) {
	params := openai.BatchListParams{}
	if req.After != "" {
		params.After = openai.String(req.After)
	}
	limit := defaultListLimit
	if req.Limit > 0 {
		limit = int(req.Limit)
	}
	params.Limit = openai.Int(int64(limit))

	page, err := h.openai.Batches.List(ctx, params)
	if err != nil {
		return nil, mapSDKError(err, "list batches")
	}

	batches := page.Data
	ids := make([]string, 0, len(batches))
	for i := range batches {
		ids = append(ids, batches[i].ID)
	}
	overlays, err := h.store.ListJobs(ctx, ids)
	if err != nil {
		klog.Warningf("store.ListJobs failed; returning batch state without overlay: %v", err)
		overlays = map[string]*pb.Job{}
	}

	jobs := make([]*pb.Job, 0, len(batches))
	for i := range batches {
		jobs = append(jobs, mergeJob(&batches[i], overlays[batches[i].ID]))
	}
	// SDK CursorPage exposes Data and HasMore. first_id / last_id ride along
	// in the upstream JSON but are not surfaced as named fields; the UI
	// doesn't consume them yet, so leave empty and revisit if pagination
	// becomes user-visible.
	return &pb.ListJobsResponse{
		Jobs:    jobs,
		HasMore: page.HasMore,
	}, nil
}

// GetJob proxies to GET /v1/batches/{id} and merges with store.
func (h *JobHandler) GetJob(ctx context.Context, req *pb.GetJobRequest) (*pb.Job, error) {
	if req.Id == "" {
		return nil, status.Error(codes.InvalidArgument, "id is required")
	}
	batch, err := h.openai.Batches.Get(ctx, req.Id)
	if err != nil {
		return nil, mapSDKError(err, "get batch")
	}
	overlay, _ := h.store.GetJob(ctx, batch.ID)
	return mergeJob(batch, overlay), nil
}

// CreateJob either runs the unified planner pipeline (reserve → POST
// /v1/batches) when a scheduler was injected at construction time, or
// otherwise POST /v1/batches synchronously plus the Console-owned overlay,
// preserving prior behavior when PLANNER_ENABLED=false.
//
// max_tokens / temperature / top_p / n on the request are intentionally NOT
// forwarded yet — per-request JSONL values win. They're reserved on the
// proto so the Console contract is stable; a follow-up will route them into
// aibrix.overrides.engine_args.
func (h *JobHandler) CreateJob(ctx context.Context, req *pb.CreateJobRequest) (*pb.Job, error) {
	if req.InputDataset == "" {
		return nil, status.Error(codes.InvalidArgument, "input_dataset is required")
	}
	if req.Endpoint == "" {
		return nil, status.Error(codes.InvalidArgument, "endpoint is required")
	}

	if h.scheduler != nil {
		return h.createJobViaPlanner(ctx, req)
	}

	completionWindow := req.CompletionWindow
	if completionWindow == "" {
		completionWindow = string(openai.BatchNewParamsCompletionWindow24h)
	}

	params := openai.BatchNewParams{
		InputFileID:      req.InputDataset,
		Endpoint:         openai.BatchNewParamsEndpoint(req.Endpoint),
		CompletionWindow: openai.BatchNewParamsCompletionWindow(completionWindow),
	}
	if req.Name != "" {
		params.Metadata = map[string]string{metadataDisplayName: req.Name}
	}

	// AIBrix extension fields ride along via OpenAI's `extra_body` channel.
	// The console wizard always picks a template (model_template_name); legacy
	// callers may still hit this path with empty fields, in which case we fall
	// back to the configured default.
	var opts []option.RequestOption
	if req.ModelTemplateName != "" {
		opts = append(opts, option.WithJSONSet("aibrix.model_template.name", req.ModelTemplateName))
		if req.ModelTemplateVersion != "" {
			opts = append(opts, option.WithJSONSet("aibrix.model_template.version", req.ModelTemplateVersion))
		}
	} else if h.defaultModelDeploymentTemplate != "" {
		opts = append(opts, option.WithJSONSet("aibrix.model_template.name", h.defaultModelDeploymentTemplate))
	}

	batch, err := h.openai.Batches.New(ctx, params, opts...)
	if err != nil {
		return nil, mapSDKError(err, "create batch")
	}

	overlay := &pb.Job{
		Id:                   batch.ID,
		Name:                 req.Name,
		CreatedBy:            currentUserEmail(ctx),
		ModelTemplateName:    req.ModelTemplateName,
		ModelTemplateVersion: req.ModelTemplateVersion,
	}
	if err := h.store.UpsertJob(ctx, overlay); err != nil {
		// Don't fail the request: the metadata service already created the
		// batch. The Console row will be filled by a future reconcile.
		klog.Warningf("store.UpsertJob failed for %s: %v", batch.ID, err)
	}
	return mergeJob(batch, overlay), nil
}

func (h *JobHandler) createJobViaPlanner(ctx context.Context, req *pb.CreateJobRequest) (*pb.Job, error) {
	completionWindow := req.CompletionWindow
	if completionWindow == "" {
		completionWindow = string(openai.BatchNewParamsCompletionWindow24h)
	}

	templateName := strings.TrimSpace(req.GetModelTemplateName())
	if templateName == "" && h.defaultModelDeploymentTemplate != "" {
		templateName = h.defaultModelDeploymentTemplate
	}
	if templateName == "" {
		return nil, status.Error(codes.InvalidArgument, "model_template_name is required (or set DEFAULT_BATCH_MODEL_DEPLOYMENT_TEMPLATE)")
	}

	payloadName := templateName
	payloadVersion := strings.TrimSpace(req.GetModelTemplateVersion())

	gpuType := defaultPlannerGPUType
	gpuCount := defaultPlannerGPUCount

	var resolved *pb.ModelDeploymentTemplate
	if req.GetModelId() != "" {
		tpl, err := h.store.ResolveModelDeploymentTemplate(ctx, req.GetModelId(), templateName, req.GetModelTemplateVersion())
		if err != nil {
			st, ok := status.FromError(err)
			if ok && st.Code() == codes.Unimplemented {
				klog.V(2).Infof("ResolveModelDeploymentTemplate unsupported for this store; using GPU defaults for planner")
			} else if ok && (st.Code() == codes.NotFound || st.Code() == codes.InvalidArgument) {
				return nil, err
			} else {
				klog.Warningf("ResolveModelDeploymentTemplate: %v; using GPU defaults for planner", err)
			}
		} else {
			resolved = tpl
			payloadName = resolved.GetName()
			if resolved.GetVersion() != "" {
				payloadVersion = resolved.GetVersion()
			}
			if resolved.GetSpec() != nil && resolved.GetSpec().GetAccelerator() != nil {
				ac := resolved.GetSpec().GetAccelerator()
				if ac.GetType() != "" {
					gpuType = ac.GetType()
				}
				if ac.GetCount() > 0 {
					gpuCount = int(ac.GetCount())
				}
			}
		}
	}

	md := make(map[string]string)
	if strings.TrimSpace(req.GetName()) != "" {
		md[metadataDisplayName] = req.GetName()
	}

	payload := planner.BatchPayload{
		InputFileID:          req.GetInputDataset(),
		Endpoint:             req.Endpoint,
		CompletionWindow:     completionWindow,
		Metadata:             md,
		ModelTemplateName:    payloadName,
		ModelTemplateVersion: payloadVersion,
	}

	pj := planner.PlannerJob{
		JobID:         uuid.New().String(),
		ModelID:       req.GetModelId(),
		GPUType:       gpuType,
		GPUCount:      gpuCount,
		StartHour:     time.Now().UTC().Unix() / 3600,
		DurationHours: defaultPlannerDurationHours,
		BatchPayload:  payload,
	}

	decision, err := h.scheduler.Schedule(ctx, pj)
	if err != nil {
		return nil, mapPlannerScheduleError(err)
	}
	if decision.BatchID == "" {
		return nil, status.Error(codes.Internal, "planner returned empty batch id")
	}

	batch, err := h.openai.Batches.Get(ctx, decision.BatchID)
	if err != nil {
		return nil, mapSDKError(err, "get batch")
	}

	overlay := &pb.Job{
		Id:                   batch.ID,
		Name:                 req.GetName(),
		CreatedBy:            currentUserEmail(ctx),
		ModelTemplateName:    payloadName,
		ModelTemplateVersion: payloadVersion,
	}
	if err := h.store.UpsertJob(ctx, overlay); err != nil {
		klog.Warningf("store.UpsertJob failed for %s: %v", batch.ID, err)
	}
	return mergeJob(batch, overlay), nil
}

// mapPlannerScheduleError maps planner sentinel errors (+ nested openai SDK
// errors wrapped under ErrMDSSubmitFailed) to gRPC statuses.
func mapPlannerScheduleError(err error) error {
	if err == nil {
		return nil
	}
	switch {
	case errors.Is(err, planner.ErrInvalidJob):
		return status.Error(codes.InvalidArgument, err.Error())
	case errors.Is(err, planner.ErrCapacityUnavailable):
		return status.Error(codes.ResourceExhausted, err.Error())
	case errors.Is(err, planner.ErrRMUnavailable):
		return status.Error(codes.Unavailable, err.Error())
	case errors.Is(err, planner.ErrMDSSubmitFailed):
		// mapSDKError uses errors.As internally, which walks the wrap chain.
		// Falls back to codes.Unavailable when no *openai.Error is present.
		return mapSDKError(err, "create batch")
	default:
		return status.Errorf(codes.Internal, "planner: %v", err)
	}
}

// CancelJob proxies to POST /v1/batches/{id}/cancel and merges with store.
func (h *JobHandler) CancelJob(ctx context.Context, req *pb.CancelJobRequest) (*pb.Job, error) {
	if req.Id == "" {
		return nil, status.Error(codes.InvalidArgument, "id is required")
	}
	batch, err := h.openai.Batches.Cancel(ctx, req.Id)
	if err != nil {
		return nil, mapSDKError(err, "cancel batch")
	}
	overlay, _ := h.store.GetJob(ctx, batch.ID)
	return mergeJob(batch, overlay), nil
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

// batchExtraFields extracts `model` and `usage` from a Batch's raw JSON.
// openai-go v1 does not surface these on the Batch struct; the metadata
// service still returns them, so we parse the response body once and let
// mergeJob populate the gRPC fields.
func batchExtraFields(b *openai.Batch) (string, *pb.JobUsage) {
	if b == nil {
		return "", nil
	}
	raw := b.RawJSON()
	if raw == "" {
		return "", nil
	}
	var x struct {
		Model string `json:"model"`
		Usage *struct {
			InputTokens  int64 `json:"input_tokens"`
			OutputTokens int64 `json:"output_tokens"`
			TotalTokens  int64 `json:"total_tokens"`
		} `json:"usage"`
	}
	if err := json.Unmarshal([]byte(raw), &x); err != nil {
		return "", nil
	}
	var usage *pb.JobUsage
	if x.Usage != nil {
		usage = &pb.JobUsage{
			InputTokens:  x.Usage.InputTokens,
			OutputTokens: x.Usage.OutputTokens,
			TotalTokens:  x.Usage.TotalTokens,
		}
	}
	return x.Model, usage
}

// mergeJob aggregates the OpenAI Batch state with the Console-side overlay.
// Either input may be nil. Console-owned fields override anything that may
// have leaked from the upstream metadata bag.
func mergeJob(b *openai.Batch, overlay *pb.Job) *pb.Job {
	job := &pb.Job{}
	if b != nil {
		job.Id = b.ID
		job.Object = string(b.Object)
		job.Endpoint = b.Endpoint
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
			job.Name = b.Metadata[metadataDisplayName]
		}
		if b.JSON.RequestCounts.Valid() {
			job.RequestCounts = &pb.JobRequestCounts{
				Total:     int32(b.RequestCounts.Total),
				Completed: int32(b.RequestCounts.Completed),
				Failed:    int32(b.RequestCounts.Failed),
			}
		}
		// model / usage aren't on the openai-go v1 Batch struct (v3 added them
		// as extensions). Parse from the raw response so the gRPC contract
		// keeps these fields populated.
		job.Model, job.Usage = batchExtraFields(b)
	}
	if overlay != nil {
		if overlay.Name != "" {
			job.Name = overlay.Name
		}
		if overlay.CreatedBy != "" {
			job.CreatedBy = overlay.CreatedBy
		}
		if overlay.ModelTemplateName != "" {
			job.ModelTemplateName = overlay.ModelTemplateName
		}
		if overlay.ModelTemplateVersion != "" {
			job.ModelTemplateVersion = overlay.ModelTemplateVersion
		}
		if job.Id == "" {
			job.Id = overlay.Id
		}
	}
	return job
}
