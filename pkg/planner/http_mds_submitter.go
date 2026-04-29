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
	"strconv"

	"github.com/openai/openai-go"
	"github.com/openai/openai-go/option"
)

// HTTPMDSSubmitter posts batch jobs to the AIBrix Metadata Service through
// the official openai-go v3 SDK. Talking to MDS through the SDK keeps it
// honest about being OpenAI-compatible — wire-shape drift surfaces as a
// deserialization or 4xx error rather than silently parsing into zero
// values.
//
// Use the openai.Client instance returned by NewOpenAIClientForMetadataService
// (wired once in Server alongside JobHandler) so the console BFF and planner
// share one client.
//
// AIBrix-specific fields (the ConfigMap-registered ModelDeploymentTemplate
// to render the K8s Job from) ride along via OpenAI's extra_body channel
// using option.WithJSONSet, the same mechanism the console uses.
type HTTPMDSSubmitter struct {
	client openai.Client
}

// Compile-time check that HTTPMDSSubmitter implements MDSSubmitter.
var _ MDSSubmitter = (*HTTPMDSSubmitter)(nil)

// NewHTTPMDSSubmitter wraps the shared OpenAI client used to reach MDS
// (the same pointer passed to JobHandler — see apps/console/api/server.New).
func NewHTTPMDSSubmitter(client openai.Client) *HTTPMDSSubmitter {
	return &HTTPMDSSubmitter{client: client}
}

// SubmitBatch builds an openai.BatchNewParams, posts it to MDS via the
// SDK, and returns the resulting batch ID.
//
// Planner-side correlation IDs (decision_id, reservation_id, cluster_id,
// gpu_type, gpu_count, start_hour, duration_hours) are merged into the
// caller-supplied payload.Metadata. Caller metadata wins on key collision so
// users can override planner-injected tags if they need to.
//
// MDS limits metadata to 16 entries. The planner injects up to 7 keys; if
// the caller's metadata plus the planner's keys would exceed the limit the
// caller's keys take priority and planner keys are skipped to keep the
// batch submittable.
func (s *HTTPMDSSubmitter) SubmitBatch(ctx context.Context, decision *SchedulingDecision, payload BatchPayload) (string, error) {
	if decision == nil {
		return "", errors.New("planner: SubmitBatch called with nil decision")
	}

	completionWindow := payload.CompletionWindow
	if completionWindow == "" {
		completionWindow = string(openai.BatchNewParamsCompletionWindow24h)
	}

	params := openai.BatchNewParams{
		InputFileID:      payload.InputFileID,
		Endpoint:         openai.BatchNewParamsEndpoint(payload.Endpoint),
		CompletionWindow: openai.BatchNewParamsCompletionWindow(completionWindow),
		Metadata:         mergeMetadata(decision, payload.Metadata),
	}

	// AIBrix extension fields ride via OpenAI's extra_body channel, exactly
	// as JobHandler.CreateJob does. Without a template the MDS renderer
	// rejects the submission with a 400, so this is required for any
	// production submission.
	var opts []option.RequestOption
	if payload.ModelTemplateName != "" {
		opts = append(opts, option.WithJSONSet("aibrix.model_template.name", payload.ModelTemplateName))
		if payload.ModelTemplateVersion != "" {
			opts = append(opts, option.WithJSONSet("aibrix.model_template.version", payload.ModelTemplateVersion))
		}
	}

	batch, err := s.client.Batches.New(ctx, params, opts...)
	if err != nil {
		return "", fmt.Errorf("submit batch: %w", err)
	}
	return batch.ID, nil
}

// mergeMetadata builds the metadata map sent to MDS.
//
// Caller metadata takes priority: planner-injected keys are added first,
// then caller keys overlay them. If adding a planner key would exceed MDS's
// 16-entry limit it is silently dropped (caller intent wins). Caller-only
// overflow (>16 keys before merge) is left to MDS to reject — the planner
// does not silently truncate user data.
func mergeMetadata(decision *SchedulingDecision, caller map[string]string) map[string]string {
	const mdsMetadataLimit = 16

	out := make(map[string]string, len(caller)+7)
	addPlanner := func(k, v string) {
		if v == "" {
			return
		}
		if _, present := caller[k]; present {
			return // caller will set this key during overlay below
		}
		if len(out)+len(caller) >= mdsMetadataLimit {
			return // skip planner keys that would push us over the limit
		}
		out[k] = v
	}

	addPlanner("planner_decision_id", decision.JobID)
	addPlanner("planner_reservation_id", decision.ReservationID)
	addPlanner("planner_cluster_id", decision.ClusterID)
	addPlanner("planner_gpu_type", decision.GPUType)
	if decision.GPUCount > 0 {
		addPlanner("planner_gpu_count", strconv.Itoa(decision.GPUCount))
	}
	if decision.StartHour > 0 {
		addPlanner("planner_start_hour", strconv.FormatInt(decision.StartHour, 10))
	}
	if decision.DurationHours > 0 {
		addPlanner("planner_duration_hours", strconv.Itoa(decision.DurationHours))
	}

	for k, v := range caller {
		out[k] = v
	}
	return out
}
