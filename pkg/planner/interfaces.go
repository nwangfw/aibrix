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

import "context"

// RMClient is the planner's view of the Resource Manager.
//
// Production: an HTTP client in pkg/rmclient (to be added).
// Development: InMemoryRMClient in this package.
// Tests: hand-rolled fakes that count calls and inject errors.
type RMClient interface {
	CreateReservation(ctx context.Context, req ReservationRequest) (*Reservation, error)
	CancelReservation(ctx context.Context, id string) error
}

// MDSSubmitter abstracts batch submission to the Metadata Service.
//
// Production: HTTPMDSSubmitter (this package), which POSTs to MDS /v1/batches
// through the openai-go v3 SDK with the planner's correlation IDs in metadata.
// Development: LoggingMDSSubmitter in this package (logs and returns a fake
// batch_id without contacting MDS).
type MDSSubmitter interface {
	SubmitBatch(ctx context.Context, decision *SchedulingDecision, payload BatchPayload) (string, error)
}
