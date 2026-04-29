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

package handler

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"github.com/grpc-ecosystem/grpc-gateway/v2/runtime"
	"k8s.io/klog/v2"

	"github.com/vllm-project/aibrix/pkg/planner"
)

// PlannerScheduler is the subset of *planner.Scheduler the handler needs.
// Defined as an interface so handler tests can substitute a fake without
// constructing a real scheduler with stub collaborators.
type PlannerScheduler interface {
	Schedule(ctx context.Context, job planner.PlannerJob) (*planner.SchedulingDecision, error)
}

// PlannerHandler exposes the planner over HTTP. It is a thin adapter: it
// parses JSON, calls Scheduler.Schedule, and translates planner errors to
// HTTP status codes.
//
// This handler uses the same mux.HandlePath registration style as
// FileHandler and PlaygroundHandler. When the gRPC migration lands it will
// be replaced by a generated PlannerService server; the planner package
// itself will not change.
type PlannerHandler struct {
	scheduler PlannerScheduler
}

// NewPlannerHandler constructs a PlannerHandler around the given scheduler.
func NewPlannerHandler(scheduler PlannerScheduler) *PlannerHandler {
	return &PlannerHandler{scheduler: scheduler}
}

// RegisterRoutes attaches planner routes to the grpc-gateway mux.
func (h *PlannerHandler) RegisterRoutes(mux *runtime.ServeMux) {
	if err := mux.HandlePath("POST", "/api/v1/planner/jobs", h.handleSubmit); err != nil {
		klog.Fatalf("Failed to register planner submit route: %v", err)
	}
	if err := mux.HandlePath("GET", "/api/v1/planner/status", h.handleStatus); err != nil {
		klog.Fatalf("Failed to register planner status route: %v", err)
	}
}

// submitJobRequest is the JSON shape accepted by POST /api/v1/planner/jobs.
type submitJobRequest struct {
	JobID         string          `json:"job_id"`
	ModelID       string          `json:"model_id,omitempty"`
	GPUType       string          `json:"gpu_type"`
	GPUCount      int             `json:"gpu_count"`
	StartHour     int64           `json:"start_hour"`
	DurationHours int             `json:"duration_hours"`
	RegionID      string          `json:"region_id,omitempty"`
	BatchPayload  batchPayloadDTO `json:"batch_payload"`
}

type batchPayloadDTO struct {
	InputFileID      string            `json:"input_file_id"`
	Endpoint         string            `json:"endpoint"`
	CompletionWindow string            `json:"completion_window,omitempty"`
	Metadata         map[string]string `json:"metadata,omitempty"`
	// ModelDeploymentTemplate selection. The metadata service requires a
	// template to render the K8s Job; submissions without one are rejected
	// at render time. Empty Version means "latest active version of Name".
	ModelTemplateName    string `json:"model_template_name,omitempty"`
	ModelTemplateVersion string `json:"model_template_version,omitempty"`
}

// submitJobResponse is the JSON shape returned by POST /api/v1/planner/jobs.
type submitJobResponse struct {
	JobID         string `json:"job_id"`
	Status        string `json:"status"`
	ReservationID string `json:"reservation_id,omitempty"`
	ClusterID     string `json:"cluster_id,omitempty"`
	BatchID       string `json:"batch_id,omitempty"`
}

type plannerStatusResponse struct {
	Mode    string `json:"mode"`
	Adapter string `json:"adapter"`
}

type plannerErrorResponse struct {
	Error string `json:"error"`
}

func (h *PlannerHandler) handleSubmit(w http.ResponseWriter, r *http.Request, _ map[string]string) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		writePlannerError(w, http.StatusBadRequest, "failed to read request body")
		return
	}
	defer func() { _ = r.Body.Close() }()

	var req submitJobRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writePlannerError(w, http.StatusBadRequest, "invalid json: "+err.Error())
		return
	}

	job := planner.PlannerJob{
		JobID:         req.JobID,
		ModelID:       req.ModelID,
		GPUType:       req.GPUType,
		GPUCount:      req.GPUCount,
		StartHour:     req.StartHour,
		DurationHours: req.DurationHours,
		RegionID:      req.RegionID,
		BatchPayload: planner.BatchPayload{
			InputFileID:          req.BatchPayload.InputFileID,
			Endpoint:             req.BatchPayload.Endpoint,
			CompletionWindow:     req.BatchPayload.CompletionWindow,
			Metadata:             req.BatchPayload.Metadata,
			ModelTemplateName:    req.BatchPayload.ModelTemplateName,
			ModelTemplateVersion: req.BatchPayload.ModelTemplateVersion,
		},
	}

	decision, err := h.scheduler.Schedule(r.Context(), job)
	if err != nil {
		writePlannerError(w, plannerErrorStatus(err), err.Error())
		return
	}

	writePlannerJSON(w, http.StatusOK, submitJobResponse{
		JobID:         decision.JobID,
		Status:        string(decision.Status),
		ReservationID: decision.ReservationID,
		ClusterID:     decision.ClusterID,
		BatchID:       decision.BatchID,
	})
}

func (h *PlannerHandler) handleStatus(w http.ResponseWriter, _ *http.Request, _ map[string]string) {
	writePlannerJSON(w, http.StatusOK, plannerStatusResponse{
		Mode:    "in-process",
		Adapter: "stub",
	})
}

// plannerErrorStatus maps a typed planner error to its HTTP status code.
// Falls back to 500 for unexpected errors so misclassification surfaces in logs.
func plannerErrorStatus(err error) int {
	switch {
	case errors.Is(err, planner.ErrInvalidJob):
		return http.StatusBadRequest
	case errors.Is(err, planner.ErrCapacityUnavailable):
		return http.StatusConflict
	case errors.Is(err, planner.ErrRMUnavailable):
		return http.StatusServiceUnavailable
	case errors.Is(err, planner.ErrMDSSubmitFailed):
		return http.StatusBadGateway
	default:
		return http.StatusInternalServerError
	}
}

func writePlannerJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(body); err != nil {
		klog.Errorf("planner: failed to encode response: %v", err)
	}
}

func writePlannerError(w http.ResponseWriter, status int, msg string) {
	writePlannerJSON(w, status, plannerErrorResponse{Error: msg})
}
