/*
Copyright 2026 The Aibrix Team.

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

// Package impl provides Planner implementations.
//
// Passthrough is a synchronous, non-persistent Planner used to wire the
// Console -> Planner -> RM -> MDS path end-to-end before the durable
// task store and async worker land. Enqueue inlines Provisioner.Provision
// followed by BatchClient.CreateBatch on the calling goroutine; reads
// forward straight to BatchClient. There is no queue, no retry, no
// expiry sweeper, and no telemetry.
package impl

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/google/uuid"

	plannerapi "github.com/vllm-project/aibrix/apps/console/api/planner/api"
	plannerclient "github.com/vllm-project/aibrix/apps/console/api/planner/client"
	"github.com/vllm-project/aibrix/apps/console/api/resource_manager/provisioner"
	rmtypes "github.com/vllm-project/aibrix/apps/console/api/resource_manager/types"
)

// Passthrough is a synchronous Planner that calls Provisioner.Provision
// and BatchClient.CreateBatch inline. It keeps a process-local
// JobID -> BatchID map so Planner.GetJob can resolve a Console-visible
// JobID to the MDS batch even before MDS round-trips aibrix.job_id.
type Passthrough struct {
	bc   plannerclient.BatchClient
	prov provisioner.Provisioner

	mu          sync.RWMutex
	jobToBatch  map[string]string
}

// NewPassthrough constructs a Passthrough Planner. Both bc and prov are required.
func NewPassthrough(bc plannerclient.BatchClient, prov provisioner.Provisioner) *Passthrough {
	return &Passthrough{
		bc:         bc,
		prov:       prov,
		jobToBatch: map[string]string{},
	}
}

var _ plannerapi.Planner = (*Passthrough)(nil)

func (p *Passthrough) Enqueue(ctx context.Context, req *plannerapi.EnqueueRequest) (*plannerapi.EnqueueResult, error) {
	if req == nil {
		return nil, fmt.Errorf("%w: nil request", plannerapi.ErrInvalidJob)
	}
	if req.JobID == "" {
		return nil, fmt.Errorf("%w: missing job_id", plannerapi.ErrInvalidJob)
	}
	if req.BatchPayload.InputFileID == "" {
		return nil, fmt.Errorf("%w: missing input_file_id", plannerapi.ErrInvalidJob)
	}
	if req.BatchPayload.Endpoint == "" {
		return nil, fmt.Errorf("%w: missing endpoint", plannerapi.ErrInvalidJob)
	}

	taskID := "tsk-" + uuid.NewString()
	now := time.Now().UTC()

	provReq := &rmtypes.ResourceProvision{
		Spec: rmtypes.ResourceProvisionSpec{
			Credential: rmtypes.ResourceCredential{Provider: p.prov.Type()},
			Groups: &[]rmtypes.ResourceGroupSpec{
				{GpusPerReplica: req.Accelerator.Count},
			},
		},
		IdempotencyKey: taskID,
	}
	provResult, err := p.prov.Provision(ctx, provReq)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", plannerapi.ErrInsufficientResources, err)
	}

	submission := &plannerclient.MDSBatchSubmission{
		InputFileID:      req.BatchPayload.InputFileID,
		Endpoint:         req.BatchPayload.Endpoint,
		CompletionWindow: req.BatchPayload.CompletionWindow,
		Metadata:         req.BatchPayload.Metadata,
		ExtraBody: plannerclient.MDSExtraBody{
			AIBrix: plannerclient.AIBrixExtraBody{
				JobID: req.JobID,
				PlannerDecision: &struct {
					ProvisionID               string `json:"provision_id,omitempty"`
					ProvisionResourceDeadline int64  `json:"provision_resource_deadline,omitempty"`
				}{
					ProvisionID: provResult.ProvisionID,
				},
				ModelTemplate: req.ModelTemplate,
				Profile:       req.Profile,
			},
		},
	}

	view, err := p.bc.CreateBatch(ctx, submission)
	if err != nil {
		return nil, err
	}
	if view == nil || view.Batch == nil {
		return nil, fmt.Errorf("%w: batch client returned empty view", plannerclient.ErrMDSSubmitFailed)
	}

	p.mu.Lock()
	p.jobToBatch[req.JobID] = view.Batch.ID
	p.mu.Unlock()

	return &plannerapi.EnqueueResult{
		TaskID:     taskID,
		JobID:      req.JobID,
		State:      plannerapi.PlannerTaskStateSubmitted,
		EnqueuedAt: now,
	}, nil
}

func (p *Passthrough) GetJob(ctx context.Context, jobID string) (*plannerapi.JobView, error) {
	if jobID == "" {
		return nil, fmt.Errorf("%w: empty job_id", plannerapi.ErrInvalidJob)
	}
	batchID := p.lookupBatchID(jobID)
	if batchID == "" {
		// Fall back to treating jobID as the MDS batch ID. This makes
		// passthrough usable across server restarts (when the in-memory
		// map is empty) and on Console reads that bypass the in-memory
		// map.
		batchID = jobID
	}
	view, err := p.bc.GetBatch(ctx, batchID)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", plannerapi.ErrJobNotFound, err)
	}
	return p.toJobView(view), nil
}

func (p *Passthrough) ListJobs(ctx context.Context, req *plannerapi.ListJobsRequest) (*plannerapi.ListJobsResponse, error) {
	listReq := &plannerclient.ListBatchesRequest{}
	if req != nil {
		listReq.Limit = req.Limit
		listReq.After = req.After
	}
	resp, err := p.bc.ListBatches(ctx, listReq)
	if err != nil {
		return nil, err
	}
	views := make([]*plannerapi.JobView, 0, len(resp.Data))
	for _, bv := range resp.Data {
		views = append(views, p.toJobView(bv))
	}
	out := &plannerapi.ListJobsResponse{Data: views, HasMore: resp.HasMore}
	if resp.HasMore && len(views) > 0 {
		out.NextAfter = views[len(views)-1].JobID
		if out.NextAfter == "" && resp.Data[len(resp.Data)-1].Batch != nil {
			out.NextAfter = resp.Data[len(resp.Data)-1].Batch.ID
		}
	}
	return out, nil
}

// GetQueueStats returns a zero-valued snapshot. Passthrough has no queue.
func (p *Passthrough) GetQueueStats(ctx context.Context, req *plannerapi.GetQueueStatsRequest) (*plannerapi.QueueStatsView, error) {
	return &plannerapi.QueueStatsView{SampledAt: time.Now().UTC()}, nil
}

// GetProvisionResourceStats returns a zero-valued snapshot. Passthrough does not
// retain per-task provision accounting.
func (p *Passthrough) GetProvisionResourceStats(ctx context.Context, req *plannerapi.GetProvisionResourceStatsRequest) (*plannerapi.ProvisionResourceStatsView, error) {
	return &plannerapi.ProvisionResourceStatsView{SampledAt: time.Now().UTC()}, nil
}

func (p *Passthrough) lookupBatchID(jobID string) string {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.jobToBatch[jobID]
}

func (p *Passthrough) toJobView(bv *plannerapi.BatchView) *plannerapi.JobView {
	if bv == nil {
		return nil
	}
	jobID := bv.JobID
	if jobID == "" && bv.Batch != nil {
		// MDS hasn't round-tripped aibrix.job_id yet; surface the
		// batch ID as the job ID so Console reads stay self-consistent.
		jobID = bv.Batch.ID
	}
	jv := &plannerapi.JobView{
		TaskID:         "",
		JobID:          jobID,
		PlannerState:   plannerapi.PlannerTaskStateSubmitted,
		LifecycleState: lifecycleFromBatch(bv),
		Batch:          bv,
	}
	return jv
}

func lifecycleFromBatch(bv *plannerapi.BatchView) plannerapi.JobLifecycleState {
	if bv == nil || bv.Batch == nil {
		return plannerapi.JobLifecycleStateSubmitted
	}
	switch string(bv.Batch.Status) {
	case "validating":
		return plannerapi.JobLifecycleStateValidating
	case "in_progress":
		return plannerapi.JobLifecycleStateInProgress
	case "finalizing":
		return plannerapi.JobLifecycleStateFinalizing
	case "completed":
		return plannerapi.JobLifecycleStateCompleted
	case "failed":
		return plannerapi.JobLifecycleStateFailed
	case "expired":
		return plannerapi.JobLifecycleStateExpired
	case "cancelling":
		return plannerapi.JobLifecycleStateCancelling
	case "cancelled":
		return plannerapi.JobLifecycleStateCancelled
	default:
		return plannerapi.JobLifecycleStateSubmitted
	}
}
