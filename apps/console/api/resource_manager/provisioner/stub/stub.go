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

// Package stub provides a no-op Provisioner used to exercise the
// Console -> Planner -> RM -> MDS path before any real backend exists.
// Provision returns a synthetic running result keyed by IdempotencyKey;
// Release / List are no-ops.
package stub

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/vllm-project/aibrix/apps/console/api/resource_manager/provisioner"
	"github.com/vllm-project/aibrix/apps/console/api/resource_manager/types"
)

// Provisioner is the stub implementation. Safe for concurrent use.
type Provisioner struct {
	mu      sync.Mutex
	results map[string]*types.ProvisionResult
}

// New returns a fresh stub provisioner.
func New() *Provisioner {
	return &Provisioner{results: map[string]*types.ProvisionResult{}}
}

var _ provisioner.Provisioner = (*Provisioner)(nil)

func (p *Provisioner) Type() types.ResourceProvisionType {
	return types.ResourceProvisionTypeStub
}

func (p *Provisioner) Provision(ctx context.Context, req *types.ResourceProvision) (*types.ProvisionResult, error) {
	if req == nil {
		return nil, fmt.Errorf("stub provisioner: nil request")
	}

	p.mu.Lock()
	defer p.mu.Unlock()

	if req.IdempotencyKey != "" {
		if cached, ok := p.results[req.IdempotencyKey]; ok {
			return cached, nil
		}
	}

	now := time.Now().UTC()
	provisionID := "stub-" + req.IdempotencyKey
	if req.IdempotencyKey == "" {
		provisionID = fmt.Sprintf("stub-%d", now.UnixNano())
	}
	res := &types.ProvisionResult{
		ProvisionID:    provisionID,
		IdempotencyKey: req.IdempotencyKey,
		Status:         types.ProvisionStatusRunning,
		CreatedAt:      now,
		UpdatedAt:      now,
	}
	if req.IdempotencyKey != "" {
		p.results[req.IdempotencyKey] = res
	}
	return res, nil
}

func (p *Provisioner) Release(ctx context.Context, provisionID string) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	for k, v := range p.results {
		if v.ProvisionID == provisionID {
			delete(p.results, k)
			return nil
		}
	}
	return nil
}

func (p *Provisioner) List(ctx context.Context, opts *types.ListOptions) ([]*types.ProvisionResult, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	out := make([]*types.ProvisionResult, 0, len(p.results))
	for _, v := range p.results {
		out = append(out, v)
	}
	return out, nil
}
