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
	"testing"

	"github.com/stretchr/testify/require"
)

// fakeRM is a hand-rolled RMClient test double that counts calls and lets the
// test inject errors at each method.
type fakeRM struct {
	createErr    error
	cancelErr    error
	createCalls  int
	cancelCalls  int
	lastCancelID string
}

func (f *fakeRM) CreateReservation(_ context.Context, _ ReservationRequest) (*Reservation, error) {
	f.createCalls++
	if f.createErr != nil {
		return nil, f.createErr
	}
	return &Reservation{
		ID:        "res-fake-1",
		ClusterID: "cluster-fake",
		Status:    ReservationConfirmed,
	}, nil
}

func (f *fakeRM) CancelReservation(_ context.Context, id string) error {
	f.cancelCalls++
	f.lastCancelID = id
	return f.cancelErr
}

// fakeMDS is a hand-rolled MDSSubmitter that counts calls and lets the test
// inject submission errors.
type fakeMDS struct {
	err            error
	submitCalls    int
	lastDecisionID string
}

func (f *fakeMDS) SubmitBatch(_ context.Context, decision *SchedulingDecision, _ BatchPayload) (string, error) {
	f.submitCalls++
	f.lastDecisionID = decision.JobID
	if f.err != nil {
		return "", f.err
	}
	return "batch-fake-1", nil
}

func validJob() PlannerJob {
	return PlannerJob{
		JobID:         "job-1",
		ModelID:       "llama-3-70b",
		GPUType:       "H20",
		GPUCount:      8,
		StartHour:     1714150800,
		DurationHours: 4,
		BatchPayload: BatchPayload{
			InputFileID: "file-abc",
			Endpoint:    "/v1/chat/completions",
		},
	}
}

func TestScheduler_HappyPath(t *testing.T) {
	rm := &fakeRM{}
	mds := &fakeMDS{}
	s := NewScheduler(rm, mds)

	dec, err := s.Schedule(context.Background(), validJob())

	require.NoError(t, err)
	require.NotNil(t, dec)
	require.Equal(t, DecisionStatusSubmitted, dec.Status)
	require.Equal(t, "res-fake-1", dec.ReservationID)
	require.Equal(t, "cluster-fake", dec.ClusterID)
	require.Equal(t, "batch-fake-1", dec.BatchID)
	require.Equal(t, 1, rm.createCalls)
	require.Equal(t, 0, rm.cancelCalls, "no rollback on happy path")
	require.Equal(t, 1, mds.submitCalls)
}

func TestScheduler_MDSSubmitFailure_RollsBackReservation(t *testing.T) {
	rm := &fakeRM{}
	mds := &fakeMDS{err: errors.New("mds boom")}
	s := NewScheduler(rm, mds)

	dec, err := s.Schedule(context.Background(), validJob())

	require.Error(t, err)
	require.ErrorIs(t, err, ErrMDSSubmitFailed)
	require.NotNil(t, dec)
	require.Equal(t, DecisionStatusFailed, dec.Status)
	require.Equal(t, "res-fake-1", dec.ReservationID, "reservation id should be retained for diagnostics")
	require.Empty(t, dec.BatchID)
	require.Equal(t, 1, rm.createCalls)
	require.Equal(t, 1, rm.cancelCalls, "MDS failure must trigger rollback")
	require.Equal(t, "res-fake-1", rm.lastCancelID)
}

func TestScheduler_MDSSubmitFailure_RollbackFails_OriginalErrorReturned(t *testing.T) {
	rm := &fakeRM{cancelErr: errors.New("rollback boom")}
	mds := &fakeMDS{err: errors.New("mds boom")}
	s := NewScheduler(rm, mds)

	_, err := s.Schedule(context.Background(), validJob())

	require.Error(t, err)
	require.ErrorIs(t, err, ErrMDSSubmitFailed, "callers should see the submission failure, not the rollback failure")
	require.Equal(t, 1, rm.cancelCalls, "rollback was attempted")
}

func TestScheduler_RMFailure_NoMDSSubmit(t *testing.T) {
	rm := &fakeRM{createErr: errors.New("rm boom")}
	mds := &fakeMDS{}
	s := NewScheduler(rm, mds)

	dec, err := s.Schedule(context.Background(), validJob())

	require.Error(t, err)
	require.NotNil(t, dec)
	require.Equal(t, DecisionStatusFailed, dec.Status)
	require.Empty(t, dec.ReservationID)
	require.Equal(t, 1, rm.createCalls)
	require.Equal(t, 0, rm.cancelCalls, "nothing to roll back when reservation never succeeded")
	require.Equal(t, 0, mds.submitCalls, "MDS must not be called after RM failure")
}

func TestScheduler_InvalidJob(t *testing.T) {
	tests := []struct {
		name string
		mut  func(*PlannerJob)
	}{
		{"missing job_id", func(j *PlannerJob) { j.JobID = "" }},
		{"missing gpu_type", func(j *PlannerJob) { j.GPUType = "" }},
		{"non-positive gpu_count", func(j *PlannerJob) { j.GPUCount = 0 }},
		{"non-positive duration_hours", func(j *PlannerJob) { j.DurationHours = 0 }},
		{"missing input_file_id", func(j *PlannerJob) { j.BatchPayload.InputFileID = "" }},
		{"missing endpoint", func(j *PlannerJob) { j.BatchPayload.Endpoint = "" }},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			rm := &fakeRM{}
			mds := &fakeMDS{}
			s := NewScheduler(rm, mds)
			job := validJob()
			tc.mut(&job)

			_, err := s.Schedule(context.Background(), job)

			require.ErrorIs(t, err, ErrInvalidJob)
			require.Equal(t, 0, rm.createCalls, "RM must not be touched for invalid jobs")
			require.Equal(t, 0, mds.submitCalls)
		})
	}
}

// TestInMemoryRMClient_RoundTrip exercises the dev stub end-to-end so the
// development boot path is at least minimally tested.
func TestInMemoryRMClient_RoundTrip(t *testing.T) {
	rm := NewInMemoryRMClient()
	res, err := rm.CreateReservation(context.Background(), ReservationRequest{
		GPUType: "H20", GPUCount: 8, DurationHours: 4,
	})
	require.NoError(t, err)
	require.NotEmpty(t, res.ID)
	require.Equal(t, ReservationConfirmed, res.Status)

	require.NoError(t, rm.CancelReservation(context.Background(), res.ID))
	require.Error(t, rm.CancelReservation(context.Background(), res.ID), "double-cancel should fail")
}
