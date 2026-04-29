# Planner Package

`pkg/planner` is the AIBrix scheduling core. It accepts a `PlannerJob`, reserves
capacity through an `RMClient`, and submits the resulting workload to the
Metadata Service (MDS) as an OpenAI-format batch via an `MDSSubmitter`. On MDS
submission failure it rolls back the reservation so capacity is not leaked.

External collaborators are interfaces (`RMClient`, `MDSSubmitter`) so callers
can substitute in-memory fakes in tests and HTTP-backed clients in production.
The package ships baseline implementations (`InMemoryRMClient`,
`LoggingMDSSubmitter`, `HTTPMDSSubmitter`) so the console binary can boot
without real backends.

## Running tests locally

The planner test suite is **pure Go**: no Docker, Kubernetes, MySQL, or live
MDS is required. The end-to-end tests use `net/http/httptest` to stand up a
fake MDS HTTP server in-process.

### Prerequisites

- Go 1.22+ (the repo's `go.mod` pins `go 1.22.5`)
- Module dependencies fetched: `go mod download` (only needed once after
  cloning or when `go.sum` changes)

### Quick run

From the repo root:

```bash
# All planner tests (~12 tests, finishes in <1s)
go test ./pkg/planner/...

# Verbose, with sub-test names
go test -v ./pkg/planner/...

# Race detector — recommended before pushing
go test -race ./pkg/planner/...

# Coverage report for the package
go test -coverprofile=cover.out ./pkg/planner/...
go tool cover -html=cover.out
```

### Running a single test

```bash
# By exact name
go test -v -run TestSchedulerEndToEnd_CreatesBatchAtMDS ./pkg/planner/

# By regex (e.g. all HTTP-submitter tests)
go test -v -run 'TestHTTPMDSSubmitter' ./pkg/planner/

# A specific sub-test of a table-driven test
go test -v -run 'TestScheduler_InvalidJob/missing_endpoint' ./pkg/planner/
```

### Repository-wide test target

The Makefile target `make test` runs unit tests across the whole repo (it also
sets up envtest assets for controller-manager packages — not needed for the
planner). For planner-only iteration, prefer `go test ./pkg/planner/...`.

## What the tests cover

| File | Layer | What it exercises |
|------|-------|-------------------|
| `scheduler_test.go` | Orchestration | Happy path, RM failure, MDS failure → reservation rollback, rollback failure preserves the original error, validation rules, dev-stub round-trip |
| `http_mds_submitter_test.go` | Wire shape | Real HTTP roundtrip through the openai-go SDK to a fake MDS server. Asserts the on-the-wire JSON shape (path, method, `endpoint`/`completion_window` enums, `metadata` cardinality, `extra_body.aibrix.model_template.*`) and that caller metadata overrides planner-injected correlation keys |

The end-to-end test path is:

```
Scheduler.Schedule
  -> InMemoryRMClient.CreateReservation
  -> HTTPMDSSubmitter.SubmitBatch
    -> openai-go v3 SDK (HTTP)
      -> httptest fake MDS  (validates payload as real MDS would)
  <- batch_id
```

Wire-shape regressions (e.g. SDK upgrade changes a JSON tag, `aibrix.*`
extension fields move) surface as 4xx from the fake server, not as silent
zero-value parses.

## Pointing the package at a real MDS

When iterating against a running MDS instead of the in-process fake, construct
the submitter with `NewOpenAIClientForMetadataService(<host>)`:

```go
client := planner.NewOpenAIClientForMetadataService("http://localhost:8000")
submitter := planner.NewHTTPMDSSubmitter(client)
scheduler := planner.NewScheduler(planner.NewInMemoryRMClient(), submitter)
```

The base URL accepts either a host (`http://localhost:8000`) or a `…/v1` form;
`NewOpenAIClientForMetadataService` normalizes to `…/v1/` as the openai-go SDK
expects.

## What the planner submits to MDS

`HTTPMDSSubmitter.SubmitBatch` produces an **OpenAI-batch-shaped request** at
`POST /v1/batches`. The body is built from two sources:

1. The caller's `BatchPayload` (`input_file_id`, `endpoint`, `completion_window`,
   plus optional `metadata` and the `aibrix.model_template.*` extension fields).
2. **Planner-injected correlation tags** merged into `metadata` from the
   `SchedulingDecision` — there are up to seven of them, all prefixed
   `planner_*`, all string-encoded.

### Field mapping

| Wire field (POST /v1/batches body) | Source | Notes |
|---|---|---|
| `input_file_id` | `BatchPayload.InputFileID` | Must already exist in MDS — planner does not upload. |
| `endpoint` | `BatchPayload.Endpoint` | Validated by MDS against its enum (`/v1/chat/completions`, `/v1/embeddings`, `/v1/completions`, `/v1/rerank`). |
| `completion_window` | `BatchPayload.CompletionWindow` | Defaults to `"24h"` (`openai.BatchNewParamsCompletionWindow24h`) when empty. |
| `metadata.planner_decision_id` | `decision.JobID` | The planner's identifier for this scheduling decision. |
| `metadata.planner_reservation_id` | `decision.ReservationID` | Returned by `RMClient.CreateReservation`. |
| `metadata.planner_cluster_id` | `decision.ClusterID` | Currently `"cluster-stub"` from the in-memory RM. |
| `metadata.planner_gpu_type` | `decision.GPUType` | e.g. `"H20"`, `"H100-SXM"`. |
| `metadata.planner_gpu_count` | `decision.GPUCount` | Stringified via `strconv.Itoa`; only set when `> 0`. |
| `metadata.planner_start_hour` | `decision.StartHour` | Stringified via `strconv.FormatInt`; epoch-hours; only set when `> 0`. |
| `metadata.planner_duration_hours` | `decision.DurationHours` | Only set when `> 0`. |
| `metadata.<caller-key>` | `BatchPayload.Metadata` | Caller keys are overlaid last and **win on collision** with planner keys (intentional — the test `TestHTTPMDSSubmitter_CallerMetadataOverridesPlannerKeys` pins this). |
| `extra_body.aibrix.model_template.name` | `BatchPayload.ModelTemplateName` | Only set when non-empty. The metadata service uses this to render the K8s Job. |
| `extra_body.aibrix.model_template.version` | `BatchPayload.ModelTemplateVersion` | Only set when both name and version are non-empty. Empty version means "latest active". |

The 16-key MDS metadata cap is enforced in `mergeMetadata`: planner tags are
silently skipped (caller intent wins) when adding them would push past the
limit. Caller-only overflow (`> 16` keys) is left for MDS to reject — the
planner does not silently truncate user data.

### Concrete example

Given this `PlannerJob` posted to `POST /api/v1/planner/jobs`:

```json
{
  "job_id": "demo-1777477409",
  "model_id": "echo-model",
  "gpu_type": "H20",
  "gpu_count": 1,
  "start_hour": 493743,
  "duration_hours": 1,
  "region_id": "local",
  "batch_payload": {
    "input_file_id": "89a92954-9453-4aab-a82a-4dc77f2f5aed",
    "endpoint": "/v1/chat/completions",
    "completion_window": "24h",
    "metadata": { "user_tag": "smoke" },
    "model_template_name": "echo-template"
  }
}
```

…the planner sends this body to MDS at `POST /v1/batches` (the openai-go SDK
serializes `extra_body` siblings into the top-level JSON object — `aibrix` is
the AIBrix-only extension):

```json
{
  "input_file_id": "89a92954-9453-4aab-a82a-4dc77f2f5aed",
  "endpoint": "/v1/chat/completions",
  "completion_window": "24h",
  "metadata": {
    "planner_decision_id":     "demo-1777477409",
    "planner_reservation_id":  "res-c8dfb559-c652-4007-8507-ba95c5538f3b",
    "planner_cluster_id":      "cluster-stub",
    "planner_gpu_type":        "H20",
    "planner_gpu_count":       "1",
    "planner_start_hour":      "493743",
    "planner_duration_hours":  "1",
    "user_tag":                "smoke"
  },
  "aibrix": {
    "model_template": { "name": "echo-template" }
  }
}
```

MDS stores the full `metadata` map verbatim on the batch record, so a `GET
/v1/batches/{id}` will return all `planner_*` tags alongside any caller
metadata (verified in the smoke test under "End-to-end" below).

Things **not** in the wire payload, even though the planner knows them:

- `model_id` and `region_id` from `PlannerJob` are *not* submitted to MDS —
  they're consumed handler-side (`region_id` would gate `RMClient` once a
  real RM client lands; `model_id` is used by the handler to resolve the
  `ModelDeploymentTemplate` before calling the planner).
- The reservation itself — only its ID is propagated. MDS has no concept of
  the reservation; the linkage is via the `planner_reservation_id` metadata
  tag.
- Anything from the caller's HTTP request beyond what's in `BatchPayload` —
  the planner does not forward auth headers, cookies, or `User-Agent`.

## End-to-end: local MDS + planner round trip

The unit tests already prove the wire shape; this section is for manually
driving the full path against a real Python MDS. The planner runs in-process
inside the console binary, so the topology is:

```
curl -> console (:8090) /api/v1/planner/jobs
          -> planner.Scheduler
            -> InMemoryRMClient (stub reservation)
            -> HTTPMDSSubmitter
              -> openai-go SDK
                -> MDS (:8000)  /v1/batches
```

### 1. Start a local MDS in dry-run mode

The Python `aibrix` package ships an MDS server with a `--dry-run` flag that
forces local file storage and an echo inference client — no Redis, no S3/TOS,
no Kubernetes required.

In a fresh terminal:

```bash
cd python/aibrix

# Editable install (only the first time, or after deps change)
pip install -e .

# Run MDS on port 8000 to match the console's METADATA_SERVICE_URL default.
# --dry-run = local storage + echo backend; safe for laptops.
aibrix_metadata --host 0.0.0.0 --port 8000 --dry-run
```

Sanity-check it's up:

```bash
curl -s http://localhost:8000/v1/files | head
```

> **Note:** MDS rejects batch submissions whose `aibrix.model_template.name`
> is not in the `aibrix-model-deployment-templates` ConfigMap. In `--dry-run`
> mode the in-process registry is empty by default; you can either point the
> planner's payload at a template the registry exposes, or run MDS without
> `--dry-run` after seeding the ConfigMap. The `TestSchedulerEndToEnd_*`
> Go tests sidestep this by talking to a fake MDS that does not enforce the
> template registry.

### 2. Build and start the console (with the planner enabled)

In a second terminal, from the repo root:

```bash
make build-console

PLANNER_ENABLED=true \
METADATA_SERVICE_URL=http://localhost:8000 \
AUTH_MODE=dev \
./bin/console --grpc-addr=:50060 --http-addr=:8090
```

You should see logs like:

```
Planner enabled (in-process; MDS at http://localhost:8000; stub RM)
HTTP gateway listening on :8090
```

`PLANNER_ENABLED` defaults to `true`, but setting it explicitly makes the
intent obvious in shell history. To disable the planner and verify the legacy
direct-batch path, set `PLANNER_ENABLED=false`.

### 3. Upload an input file to MDS

The planner only schedules an existing `input_file_id`; it does not upload.
Create a tiny JSONL batch input and POST it through the SDK-compatible files
API:

```bash
cat > /tmp/in.jsonl <<'EOF'
{"custom_id":"r-1","method":"POST","url":"/v1/chat/completions","body":{"model":"echo","messages":[{"role":"user","content":"hi"}]}}
EOF

curl -s -X POST http://localhost:8000/v1/files \
  -F purpose=batch \
  -F file=@/tmp/in.jsonl
```

The response includes `"id": "file-..."`. Save it:

```bash
FILE_ID=$(curl -s -X POST http://localhost:8000/v1/files \
  -F purpose=batch -F file=@/tmp/in.jsonl | jq -r .id)
echo "$FILE_ID"
```

### 4. Submit a job through the planner

```bash
curl -s -X POST http://localhost:8090/api/v1/planner/jobs \
  -H 'Content-Type: application/json' \
  -d "$(cat <<EOF
{
  "job_id": "demo-$(date +%s)",
  "model_id": "echo-model",
  "gpu_type": "H20",
  "gpu_count": 1,
  "start_hour": $(($(date +%s) / 3600)),
  "duration_hours": 1,
  "region_id": "local",
  "batch_payload": {
    "input_file_id": "$FILE_ID",
    "endpoint": "/v1/chat/completions",
    "completion_window": "24h",
    "metadata": { "user_tag": "smoke" },
    "model_template_name": "echo-template"
  }
}
EOF
)" | jq
```

A successful response looks like:

```json
{
  "job_id": "demo-1714150800",
  "status": "submitted",
  "reservation_id": "res-...",
  "cluster_id": "cluster-stub",
  "batch_id": "batch_abc123"
}
```

### 5. Confirm the batch landed in MDS

```bash
curl -s http://localhost:8000/v1/batches | jq '.data[] | {id, status, metadata}'
```

You should see your batch with planner correlation tags injected by
`HTTPMDSSubmitter.mergeMetadata`:

```json
{
  "id": "batch_abc123",
  "status": "validating",
  "metadata": {
    "planner_decision_id": "demo-1714150800",
    "planner_reservation_id": "res-...",
    "planner_cluster_id": "cluster-stub",
    "planner_gpu_type": "H20",
    "planner_gpu_count": "1",
    "planner_start_hour": "475597",
    "planner_duration_hours": "1",
    "user_tag": "smoke"
  }
}
```

### 6. Status, list, fetch by id

```bash
# Planner mode/adapter (handler-side, not the scheduler's view of MDS)
curl -s http://localhost:8090/api/v1/planner/status | jq

# List all batches (via console JobService, which proxies MDS through the SDK)
curl -s http://localhost:8090/api/v1/jobs | jq '.jobs[] | {id, status}'

# Fetch a specific batch
curl -s http://localhost:8090/api/v1/jobs/$BATCH_ID | jq
```

### Failure-path smoke checks

- **Missing required field** (planner returns 400):

  ```bash
  curl -s -i -X POST http://localhost:8090/api/v1/planner/jobs \
    -H 'Content-Type: application/json' \
    -d '{"job_id":"x","gpu_type":"H20"}'
  # HTTP/1.1 400 Bad Request
  # {"error":"planner: invalid job: gpu_count must be positive"}
  ```

- **MDS down / unreachable** (kill the python process, retry the submit):
  the planner reserves, the SDK fails on POST, the scheduler rolls the
  reservation back, and the handler returns `502 Bad Gateway`
  (`ErrMDSSubmitFailed` → `mapSDKError`).

- **MDS returns 4xx** (e.g. unknown `model_template_name`): same path; the
  handler returns the upstream MDS message and an HTTP code derived from the
  upstream status.

### Tear-down

```bash
# Console (Ctrl-C in its terminal) and MDS (Ctrl-C). The dry-run MDS writes
# files under the configured local storage path; check its startup logs for
# the exact directory if you want to clean it up.
```

## Linting

The repo lints with `golangci-lint` via `make lint`. To lint just this package:

```bash
golangci-lint run ./pkg/planner/...
```

`go vet ./pkg/planner/...` is a fast subset and is run as part of `make test`.
