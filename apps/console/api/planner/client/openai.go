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

package client

import (
	"context"
	"fmt"
	"strings"

	"github.com/openai/openai-go/v3"
	"github.com/openai/openai-go/v3/option"

	plannerapi "github.com/vllm-project/aibrix/apps/console/api/planner/api"
)

// OpenAIBatchClient implements BatchClient against an OpenAI-compatible
// /v1/batches endpoint (the metadata service). All AIBrix-namespaced
// fields ride along under the SDK's extra_body channel via
// option.WithJSONSet.
type OpenAIBatchClient struct {
	client openai.Client
}

// NewOpenAIBatchClient constructs a BatchClient pointed at the metadata
// service's base URL (without the trailing /v1).
func NewOpenAIBatchClient(metadataServiceURL string) *OpenAIBatchClient {
	baseURL := strings.TrimRight(metadataServiceURL, "/") + "/v1"
	c := openai.NewClient(
		option.WithBaseURL(baseURL),
		option.WithAPIKey("aibrix-console"),
	)
	return &OpenAIBatchClient{client: c}
}

var _ BatchClient = (*OpenAIBatchClient)(nil)

func (c *OpenAIBatchClient) CreateBatch(ctx context.Context, req *MDSBatchSubmission) (*plannerapi.BatchView, error) {
	if req == nil {
		return nil, fmt.Errorf("openai batch client: nil request")
	}

	completionWindow := req.CompletionWindow
	if completionWindow == "" {
		completionWindow = string(openai.BatchNewParamsCompletionWindow24h)
	}
	params := openai.BatchNewParams{
		InputFileID:      req.InputFileID,
		Endpoint:         openai.BatchNewParamsEndpoint(req.Endpoint),
		CompletionWindow: openai.BatchNewParamsCompletionWindow(completionWindow),
	}
	if len(req.Metadata) > 0 {
		params.Metadata = req.Metadata
	}

	opts := buildExtraBodyOptions(req.ExtraBody.AIBrix)

	batch, err := c.client.Batches.New(ctx, params, opts...)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrMDSSubmitFailed, err)
	}
	return toBatchView(batch, req.ExtraBody.AIBrix.JobID), nil
}

func (c *OpenAIBatchClient) GetBatch(ctx context.Context, batchID string) (*plannerapi.BatchView, error) {
	if batchID == "" {
		return nil, fmt.Errorf("openai batch client: empty batch ID")
	}
	batch, err := c.client.Batches.Get(ctx, batchID)
	if err != nil {
		return nil, err
	}
	return toBatchView(batch, jobIDFromMetadata(batch)), nil
}

func (c *OpenAIBatchClient) ListBatches(ctx context.Context, req *ListBatchesRequest) (*ListBatchesResponse, error) {
	params := openai.BatchListParams{}
	if req != nil {
		if req.After != "" {
			params.After = openai.String(req.After)
		}
		if req.Limit > 0 {
			params.Limit = openai.Int(int64(req.Limit))
		}
	}
	page, err := c.client.Batches.List(ctx, params)
	if err != nil {
		return nil, err
	}
	views := make([]*plannerapi.BatchView, 0, len(page.Data))
	for i := range page.Data {
		b := &page.Data[i]
		views = append(views, toBatchView(b, jobIDFromMetadata(b)))
	}
	return &ListBatchesResponse{Data: views, HasMore: page.HasMore}, nil
}

// CancelBatch is not part of BatchClient (cancel travels through MDS
// directly per the planner contract) but Console needs the same SDK
// hookup, so it is exposed here for reuse by JobHandler.CancelJob.
func (c *OpenAIBatchClient) CancelBatch(ctx context.Context, batchID string) (*plannerapi.BatchView, error) {
	if batchID == "" {
		return nil, fmt.Errorf("openai batch client: empty batch ID")
	}
	batch, err := c.client.Batches.Cancel(ctx, batchID)
	if err != nil {
		return nil, err
	}
	return toBatchView(batch, jobIDFromMetadata(batch)), nil
}

func toBatchView(b *openai.Batch, jobID string) *plannerapi.BatchView {
	return &plannerapi.BatchView{Batch: b, JobID: jobID}
}

// jobIDFromMetadata extracts aibrix.job_id from the batch's echoed
// metadata map, if present. MDS round-tripping job_id via metadata is a
// hard external dependency; until it lands, this returns empty.
func jobIDFromMetadata(b *openai.Batch) string {
	if b == nil || b.Metadata == nil {
		return ""
	}
	return b.Metadata["aibrix.job_id"]
}

// buildExtraBodyOptions projects AIBrixExtraBody fields into option.WithJSONSet
// calls so the SDK serializes them under extra_body.aibrix.* on the wire.
func buildExtraBodyOptions(eb AIBrixExtraBody) []option.RequestOption {
	var opts []option.RequestOption
	if eb.JobID != "" {
		opts = append(opts, option.WithJSONSet("aibrix.job_id", eb.JobID))
	}
	if eb.PlannerDecision != nil {
		if eb.PlannerDecision.ProvisionID != "" {
			opts = append(opts, option.WithJSONSet("aibrix.planner_decision.provision_id", eb.PlannerDecision.ProvisionID))
		}
		if eb.PlannerDecision.ProvisionResourceDeadline != 0 {
			opts = append(opts, option.WithJSONSet("aibrix.planner_decision.provision_resource_deadline", eb.PlannerDecision.ProvisionResourceDeadline))
		}
	}
	if len(eb.ResourceDetails) > 0 {
		opts = append(opts, option.WithJSONSet("aibrix.resource_details", eb.ResourceDetails))
	}
	if eb.ModelTemplate != nil && eb.ModelTemplate.Name != "" {
		opts = append(opts, option.WithJSONSet("aibrix.model_template.name", eb.ModelTemplate.Name))
		if eb.ModelTemplate.Version != "" {
			opts = append(opts, option.WithJSONSet("aibrix.model_template.version", eb.ModelTemplate.Version))
		}
	}
	if eb.Profile != nil && eb.Profile.Name != "" {
		opts = append(opts, option.WithJSONSet("aibrix.profile.name", eb.Profile.Name))
		if eb.Profile.Version != "" {
			opts = append(opts, option.WithJSONSet("aibrix.profile.version", eb.Profile.Version))
		}
	}
	return opts
}
