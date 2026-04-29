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

import "errors"

// Sentinel errors. Wrapped with %w by Schedule so callers can use errors.Is
// to map planner failures to HTTP/gRPC status codes without parsing strings.
var (
	// ErrInvalidJob indicates the submitted PlannerJob failed validation
	// (missing required fields, non-positive counts, etc.).
	ErrInvalidJob = errors.New("planner: invalid job")

	// ErrCapacityUnavailable indicates RM accepted the request but no
	// matching capacity is currently available.
	ErrCapacityUnavailable = errors.New("planner: rm capacity unavailable")

	// ErrRMUnavailable indicates RM is unreachable, returning 5xx, or
	// otherwise unable to process reservations.
	ErrRMUnavailable = errors.New("planner: rm unavailable")

	// ErrMDSSubmitFailed indicates the reservation succeeded but submitting
	// the OpenAI batch to MDS failed; the reservation has been rolled back
	// (best effort).
	ErrMDSSubmitFailed = errors.New("planner: mds submit failed")
)
