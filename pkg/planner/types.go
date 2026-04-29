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

// PlannerJob is a single user-submitted scheduling request.
//
// It carries the resource shape (gpu_type, gpu_count, duration) needed for
// reservation, plus a BatchPayload describing the workload MDS should run on
// the reserved capacity once the reservation is granted.
type PlannerJob struct {
	JobID         string
	ModelID       string
	GPUType       string
	GPUCount      int
	StartHour     int64
	DurationHours int
	RegionID      string
	BatchPayload  BatchPayload
}

// BatchPayload is the OpenAI-batch-format submission MDS will execute.
//
// The InputFileID must already exist in MDS (uploaded out-of-band, typically
// via the console's FileHandler proxy). The scheduler does not upload files;
// it only creates the batch record referencing an existing file.
//
// ModelTemplateName / ModelTemplateVersion select the ConfigMap-registered
// ModelDeploymentTemplate the metadata service uses to render the K8s Job.
// They ride the wire as “aibrix.model_template“ under OpenAI's extra_body
// channel, mirroring how the console's JobHandler submits jobs. MDS rejects
// submissions without a template (renderer requires model_template_name);
// callers that don't know which template they want can leave Version empty
// to mean "latest active version of Name".
type BatchPayload struct {
	InputFileID          string
	Endpoint             string
	CompletionWindow     string
	Metadata             map[string]string
	ModelTemplateName    string
	ModelTemplateVersion string
}

// SchedulingDecision is the outcome of one Scheduler.Schedule call.
//
// Status transitions across the pipeline:
//
//	Pending   -> reservation in flight
//	Reserved  -> RM accepted, MDS submission in flight
//	Submitted -> MDS accepted; ReservationID and BatchID populated
//	Failed    -> any step failed; partial fields may be populated for diagnostics
type SchedulingDecision struct {
	JobID         string
	ClusterID     string
	GPUType       string
	GPUCount      int
	StartHour     int64
	DurationHours int
	ReservationID string
	BatchID       string
	Status        DecisionStatus
}

// DecisionStatus enumerates the lifecycle states of a SchedulingDecision.
type DecisionStatus string

const (
	DecisionStatusPending   DecisionStatus = "pending"
	DecisionStatusReserved  DecisionStatus = "reserved"
	DecisionStatusSubmitted DecisionStatus = "submitted"
	DecisionStatusFailed    DecisionStatus = "failed"
)

// ReservationRequest is the RMClient input for creating a reservation.
type ReservationRequest struct {
	GPUType       string
	GPUCount      int
	StartHour     int64
	DurationHours int
	RegionID      string
}

// Reservation is the RMClient response for a successful CreateReservation.
type Reservation struct {
	ID        string
	ClusterID string
	Status    ReservationStatus
}

// ReservationStatus enumerates RM-side reservation states surfaced to the planner.
type ReservationStatus string

const (
	ReservationPending   ReservationStatus = "pending"
	ReservationConfirmed ReservationStatus = "confirmed"
	ReservationFailed    ReservationStatus = "failed"
)
