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
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/require"
)

// fakeMDSEndpoint is a minimal MDS POST /v1/batches stand-in. It mirrors the
// validation rules in python/aibrix/aibrix/metadata/api/v1/batch.py at the
// BatchSpec level (endpoint enum, completion_window enum, metadata size).
//
// Tests assert against LastRawBody (a generic map[string]any view of the
// request) so that wire-shape regressions surface here rather than only in
// production. If anything in the JSON shape, URL path, HTTP method, or
// metadata mapping is wrong, the fake MDS rejects with 400 and the test
// fails with a clear signal.
type fakeMDSEndpoint struct {
	server               *httptest.Server
	LastRawBody          map[string]any
	LastEndpoint         string
	LastInputFileID      string
	LastCompletionWindow string
	LastMetadata         map[string]string
	Calls                int
}

func newFakeMDSEndpoint(t *testing.T) *fakeMDSEndpoint {
	t.Helper()
	e := &fakeMDSEndpoint{}
	e.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		e.Calls++

		// SDK path is /v1/batches; the SDK prepends /v1 itself when
		// option.WithBaseURL is set without a trailing /v1.
		if r.URL.Path != "/v1/batches" || r.Method != http.MethodPost {
			http.Error(w, `{"detail":"unexpected route"}`, http.StatusNotFound)
			return
		}
		body, _ := io.ReadAll(r.Body)
		if err := json.Unmarshal(body, &e.LastRawBody); err != nil {
			http.Error(w, `{"detail":"invalid json"}`, http.StatusBadRequest)
			return
		}

		// Pull the wire-format fields out of the parsed body so tests can
		// assert on them without re-parsing.
		if v, ok := e.LastRawBody["endpoint"].(string); ok {
			e.LastEndpoint = v
		}
		if v, ok := e.LastRawBody["input_file_id"].(string); ok {
			e.LastInputFileID = v
		}
		if v, ok := e.LastRawBody["completion_window"].(string); ok {
			e.LastCompletionWindow = v
		}
		if md, ok := e.LastRawBody["metadata"].(map[string]any); ok {
			e.LastMetadata = make(map[string]string, len(md))
			for k, v := range md {
				if s, ok := v.(string); ok {
					e.LastMetadata[k] = s
				}
			}
		}

		switch e.LastEndpoint {
		case "/v1/chat/completions", "/v1/embeddings", "/v1/completions", "/v1/rerank":
		default:
			http.Error(w, `{"detail":"invalid endpoint"}`, http.StatusBadRequest)
			return
		}
		if e.LastCompletionWindow != "" && e.LastCompletionWindow != "24h" {
			http.Error(w, `{"detail":"invalid completion_window"}`, http.StatusBadRequest)
			return
		}
		if len(e.LastMetadata) > 16 {
			http.Error(w, `{"detail":"too many metadata entries"}`, http.StatusBadRequest)
			return
		}

		// Minimal OpenAI-shaped Batch response. The SDK only deserializes
		// what it knows; extra fields are ignored, missing optional fields
		// are tolerated.
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id":                "batch-real-1",
			"object":            "batch",
			"endpoint":          e.LastEndpoint,
			"input_file_id":     e.LastInputFileID,
			"completion_window": e.LastCompletionWindow,
			"status":            "validating",
			"created_at":        1714150800,
			"expires_at":        1714237200,
			"metadata":          e.LastRawBody["metadata"],
		})
	}))
	t.Cleanup(e.server.Close)
	return e
}

// TestSchedulerEndToEnd_CreatesBatchAtMDS proves the full pipeline:
//
//	Scheduler.Schedule
//	  -> InMemoryRMClient (reserve)
//	  -> HTTPMDSSubmitter (build BatchNewParams + correlation metadata)
//	    -> openai-go v3 SDK (HTTP)
//	      -> fake MDS HTTP server (validates as real MDS would)
//	  <- batch_id
//
// If any link in this chain produces a wire shape MDS would reject, the fake
// MDS returns 400 and the test fails. This is the most direct answer to
// "can MDS create the batch correctly" without needing a real MDS process.
func TestSchedulerEndToEnd_CreatesBatchAtMDS(t *testing.T) {
	fake := newFakeMDSEndpoint(t)

	rm := NewInMemoryRMClient()
	submitter := NewHTTPMDSSubmitter(NewOpenAIClientForMetadataService(fake.server.URL))
	s := NewScheduler(rm, submitter)

	job := PlannerJob{
		JobID:         "job-e2e",
		ModelID:       "llama-3-70b",
		GPUType:       "H20",
		GPUCount:      8,
		StartHour:     1714150800,
		DurationHours: 4,
		RegionID:      "us-east-1",
		BatchPayload: BatchPayload{
			InputFileID:      "file-abc",
			Endpoint:         "/v1/chat/completions",
			CompletionWindow: "24h",
			Metadata: map[string]string{
				"user_tag": "demo",
			},
		},
	}

	decision, err := s.Schedule(context.Background(), job)
	require.NoError(t, err)
	require.Equal(t, DecisionStatusSubmitted, decision.Status)
	require.Equal(t, "batch-real-1", decision.BatchID)
	require.NotEmpty(t, decision.ReservationID)
	require.Equal(t, "cluster-stub", decision.ClusterID)

	require.Equal(t, 1, fake.Calls)

	// Wire-format invariants the real MDS will check.
	require.Equal(t, "file-abc", fake.LastInputFileID)
	require.Equal(t, "/v1/chat/completions", fake.LastEndpoint)
	require.Equal(t, "24h", fake.LastCompletionWindow)

	// Caller metadata is preserved.
	require.Equal(t, "demo", fake.LastMetadata["user_tag"])

	// Planner correlation IDs are stamped onto metadata so a running batch
	// can be traced back to its scheduling decision and reservation.
	require.Equal(t, "job-e2e", fake.LastMetadata["planner_decision_id"])
	require.Equal(t, decision.ReservationID, fake.LastMetadata["planner_reservation_id"])
	require.Equal(t, "cluster-stub", fake.LastMetadata["planner_cluster_id"])
	require.Equal(t, "H20", fake.LastMetadata["planner_gpu_type"])
	require.Equal(t, "8", fake.LastMetadata["planner_gpu_count"])
	require.Equal(t, "1714150800", fake.LastMetadata["planner_start_hour"])
	require.Equal(t, "4", fake.LastMetadata["planner_duration_hours"])

	// No template fields were set on the payload, so aibrix.model_template
	// should NOT appear in the body. (Unset templates are still rejected
	// by the MDS renderer at runtime, but that's an MDS-side concern; the
	// submitter must faithfully reflect what the caller asked for.)
	_, hasAibrix := fake.LastRawBody["aibrix"]
	require.False(t, hasAibrix, "aibrix block must be omitted when no template is set")
}

// TestHTTPMDSSubmitter_SendsAibrixModelTemplate asserts that the
// ModelTemplateName / ModelTemplateVersion fields on BatchPayload show up on
// the wire under extra_body.aibrix.model_template, matching how
// JobHandler.CreateJob submits jobs through the same SDK.
func TestHTTPMDSSubmitter_SendsAibrixModelTemplate(t *testing.T) {
	fake := newFakeMDSEndpoint(t)
	submitter := NewHTTPMDSSubmitter(NewOpenAIClientForMetadataService(fake.server.URL))
	s := NewScheduler(NewInMemoryRMClient(), submitter)

	job := validJob()
	job.BatchPayload.ModelTemplateName = "llama3-70b-prod"
	job.BatchPayload.ModelTemplateVersion = "v1.3.0"

	_, err := s.Schedule(context.Background(), job)
	require.NoError(t, err)

	aibrix, ok := fake.LastRawBody["aibrix"].(map[string]any)
	require.True(t, ok, "aibrix block must be present on the wire")
	tpl, ok := aibrix["model_template"].(map[string]any)
	require.True(t, ok, "aibrix.model_template must be a JSON object")
	require.Equal(t, "llama3-70b-prod", tpl["name"])
	require.Equal(t, "v1.3.0", tpl["version"])
}

// TestHTTPMDSSubmitter_OmitsVersionWhenUnpinned confirms that an empty
// ModelTemplateVersion is not serialized — MDS treats absence of version
// as "latest active", and a literal empty string would resolve differently.
func TestHTTPMDSSubmitter_OmitsVersionWhenUnpinned(t *testing.T) {
	fake := newFakeMDSEndpoint(t)
	submitter := NewHTTPMDSSubmitter(NewOpenAIClientForMetadataService(fake.server.URL))
	s := NewScheduler(NewInMemoryRMClient(), submitter)

	job := validJob()
	job.BatchPayload.ModelTemplateName = "llama3-70b-prod"

	_, err := s.Schedule(context.Background(), job)
	require.NoError(t, err)

	aibrix, ok := fake.LastRawBody["aibrix"].(map[string]any)
	require.True(t, ok)
	tpl, ok := aibrix["model_template"].(map[string]any)
	require.True(t, ok)
	require.Equal(t, "llama3-70b-prod", tpl["name"])
	_, hasVersion := tpl["version"]
	require.False(t, hasVersion, "version must be omitted when not pinned")
}

// TestSchedulerEndToEnd_MDSRejects_RollsBackReservation proves that when MDS
// returns 4xx, the scheduler unwinds the RM reservation. Same rollback
// contract as TestScheduler_MDSSubmitFailure_RollsBackReservation in
// scheduler_test.go, but exercising the real HTTP/SDK path.
func TestSchedulerEndToEnd_MDSRejects_RollsBackReservation(t *testing.T) {
	fake := newFakeMDSEndpoint(t)
	rm := &fakeRM{}
	s := NewScheduler(rm, NewHTTPMDSSubmitter(NewOpenAIClientForMetadataService(fake.server.URL)))

	job := validJob()
	job.BatchPayload.Endpoint = "/v1/totally-invented" // fake MDS will return 400

	_, err := s.Schedule(context.Background(), job)
	require.Error(t, err)
	require.ErrorIs(t, err, ErrMDSSubmitFailed)
	require.Equal(t, 1, fake.Calls, "MDS was contacted")
	require.Equal(t, 1, rm.cancelCalls, "rollback must run when MDS rejects the batch")
}

// TestHTTPMDSSubmitter_CallerMetadataOverridesPlannerKeys asserts the
// documented merge behavior: when a caller-supplied metadata key collides
// with a planner-injected key, the caller's value wins.
func TestHTTPMDSSubmitter_CallerMetadataOverridesPlannerKeys(t *testing.T) {
	fake := newFakeMDSEndpoint(t)
	s := NewScheduler(NewInMemoryRMClient(), NewHTTPMDSSubmitter(NewOpenAIClientForMetadataService(fake.server.URL)))

	job := validJob()
	job.BatchPayload.Metadata = map[string]string{
		"planner_gpu_type": "OVERRIDE",
	}

	_, err := s.Schedule(context.Background(), job)
	require.NoError(t, err)
	require.Equal(t, "OVERRIDE", fake.LastMetadata["planner_gpu_type"],
		"caller metadata should overlay planner-injected keys on collision")
}

// TestHTTPMDSSubmitter_NilDecision_ReturnsError checks the defensive
// nil-check on the decision argument; an unwired caller must produce a
// returnable error rather than a panic.
func TestHTTPMDSSubmitter_NilDecision_ReturnsError(t *testing.T) {
	s := NewHTTPMDSSubmitter(NewOpenAIClientForMetadataService("http://example.invalid"))
	_, err := s.SubmitBatch(context.Background(), nil, BatchPayload{})
	require.Error(t, err)
}
