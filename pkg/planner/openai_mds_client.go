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
	"strings"

	"github.com/openai/openai-go"
	"github.com/openai/openai-go/option"
)

// NewOpenAIClientForMetadataService returns an openai-go client for the AIBrix
// metadata service OpenAI-compatible batches API (/v1/batches).
//
// Console and planner share one instance wired from apps/console/api/server
// (see Server.New) so MDS connectivity is configured once. The client's
// built-in base URL expects a path ending in "/v1/" — callers may pass a host
// or full .../v1 URL; normalization matches JobHandler expectations.
//
// The API key is a placeholder — MDS does not currently authenticate
// /v1/batches, but the SDK refuses to issue a request without an
// Authorization header.
func NewOpenAIClientForMetadataService(metadataServiceURL string) openai.Client {
	baseURL := strings.TrimRight(metadataServiceURL, "/")
	if !strings.HasSuffix(baseURL, "/v1") {
		baseURL += "/v1"
	}
	baseURL += "/"
	return openai.NewClient(
		option.WithBaseURL(baseURL),
		option.WithAPIKey("aibrix-console"),
	)
}
