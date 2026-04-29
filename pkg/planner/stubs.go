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
	"fmt"
	"sync"

	"github.com/google/uuid"
	"k8s.io/klog/v2"
)

// InMemoryRMClient is a development RMClient that records reservations in a
// process-local map. It always accepts valid requests and never reports
// capacity exhaustion. Useful for booting the console binary before the real
// HTTP RM client is available; not for production.
type InMemoryRMClient struct {
	mu           sync.Mutex
	reservations map[string]Reservation
}

// Compile-time check that InMemoryRMClient satisfies RMClient.
var _ RMClient = (*InMemoryRMClient)(nil)

// NewInMemoryRMClient returns a fresh InMemoryRMClient.
func NewInMemoryRMClient() *InMemoryRMClient {
	return &InMemoryRMClient{reservations: make(map[string]Reservation)}
}

// CreateReservation always returns a confirmed reservation with a synthetic ID.
func (c *InMemoryRMClient) CreateReservation(_ context.Context, _ ReservationRequest) (*Reservation, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	res := Reservation{
		ID:        "res-" + uuid.NewString(),
		ClusterID: "cluster-stub",
		Status:    ReservationConfirmed,
	}
	c.reservations[res.ID] = res
	klog.V(2).Infof("planner stub rm: reserved %s on %s", res.ID, res.ClusterID)
	return &res, nil
}

// CancelReservation removes the reservation from the in-memory map.
// Returns an error if the ID is unknown so callers see contract violations.
func (c *InMemoryRMClient) CancelReservation(_ context.Context, id string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, ok := c.reservations[id]; !ok {
		return fmt.Errorf("reservation %q not found", id)
	}
	delete(c.reservations, id)
	klog.V(2).Infof("planner stub rm: cancelled %s", id)
	return nil
}

// LoggingMDSSubmitter is a development MDSSubmitter that logs the submission
// and returns a synthetic batch_id without contacting a real MDS.
type LoggingMDSSubmitter struct{}

// Compile-time check that LoggingMDSSubmitter satisfies MDSSubmitter.
var _ MDSSubmitter = (*LoggingMDSSubmitter)(nil)

// NewLoggingMDSSubmitter returns a LoggingMDSSubmitter.
func NewLoggingMDSSubmitter() *LoggingMDSSubmitter {
	return &LoggingMDSSubmitter{}
}

// SubmitBatch logs the planned submission and returns a fake batch ID.
func (s *LoggingMDSSubmitter) SubmitBatch(_ context.Context, decision *SchedulingDecision, payload BatchPayload) (string, error) {
	batchID := "batch-" + uuid.NewString()
	klog.Infof("planner stub mds: submit_batch job=%s reservation=%s file=%s endpoint=%s -> %s",
		decision.JobID, decision.ReservationID, payload.InputFileID, payload.Endpoint, batchID)
	return batchID, nil
}
