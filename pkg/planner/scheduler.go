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

package planner

import (
	"context"
	"errors"
	"fmt"

	"k8s.io/klog/v2"
)

// Scheduler orchestrates the full per-job pipeline: validate → reserve → submit.
//
// On submission failure it rolls back the reservation so capacity is not held
// indefinitely. The orchestration lives here (not in the HTTP handler) because
// only the scheduler knows the rollback contract; splitting these steps across
// layers would create split-brain failure semantics.
//
// Both collaborators are interfaces (Shape C), so unit tests substitute fakes
// and production code injects HTTP-backed clients.
type Scheduler struct {
	rm  RMClient
	mds MDSSubmitter
}

// NewScheduler constructs a Scheduler with the given collaborators.
// Both arguments are required; passing nil will panic on first use.
func NewScheduler(rm RMClient, mds MDSSubmitter) *Scheduler {
	return &Scheduler{rm: rm, mds: mds}
}

// Schedule runs the full pipeline for one submitted PlannerJob and returns a
// SchedulingDecision describing the outcome.
//
// On error the returned *SchedulingDecision is non-nil and carries whatever
// fields were populated before the failure (e.g., ReservationID present means
// the reservation was created and then rolled back), so callers can log
// diagnostics without relying on the error string.
func (s *Scheduler) Schedule(ctx context.Context, job PlannerJob) (*SchedulingDecision, error) {
	if err := validateJob(job); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrInvalidJob, err)
	}

	decision := &SchedulingDecision{
		JobID:         job.JobID,
		GPUType:       job.GPUType,
		GPUCount:      job.GPUCount,
		StartHour:     job.StartHour,
		DurationHours: job.DurationHours,
		Status:        DecisionStatusPending,
	}

	res, err := s.rm.CreateReservation(ctx, ReservationRequest{
		GPUType:       job.GPUType,
		GPUCount:      job.GPUCount,
		StartHour:     job.StartHour,
		DurationHours: job.DurationHours,
		RegionID:      job.RegionID,
	})
	if err != nil {
		decision.Status = DecisionStatusFailed
		return decision, fmt.Errorf("create reservation: %w", err)
	}

	decision.ReservationID = res.ID
	decision.ClusterID = res.ClusterID
	decision.Status = DecisionStatusReserved

	batchID, err := s.mds.SubmitBatch(ctx, decision, job.BatchPayload)
	if err != nil {
		// Best-effort rollback. We use a context detached from the caller's
		// cancellation so a client disconnect does not also kill cleanup.
		// Rollback errors are logged but not returned: the caller cares about
		// the original submission failure, and a leaked reservation will
		// expire on RM-side TTL anyway.
		rollbackCtx := context.WithoutCancel(ctx)
		if cancelErr := s.rm.CancelReservation(rollbackCtx, res.ID); cancelErr != nil {
			klog.Errorf("planner: rollback failed for reservation %q: %v", res.ID, cancelErr)
		}
		decision.Status = DecisionStatusFailed
		// Wrap both the sentinel and the inner error with %w so callers can
		// errors.As the SDK's *openai.Error out of the chain (mapPlannerScheduleError).
		return decision, fmt.Errorf("%w: %w", ErrMDSSubmitFailed, err)
	}

	decision.BatchID = batchID
	decision.Status = DecisionStatusSubmitted
	return decision, nil
}

func validateJob(j PlannerJob) error {
	if j.JobID == "" {
		return errors.New("job_id required")
	}
	if j.GPUType == "" {
		return errors.New("gpu_type required")
	}
	if j.GPUCount <= 0 {
		return errors.New("gpu_count must be positive")
	}
	if j.DurationHours <= 0 {
		return errors.New("duration_hours must be positive")
	}
	if j.BatchPayload.InputFileID == "" {
		return errors.New("batch_payload.input_file_id required")
	}
	if j.BatchPayload.Endpoint == "" {
		return errors.New("batch_payload.endpoint required")
	}
	return nil
}
