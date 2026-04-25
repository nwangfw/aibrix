#!/usr/bin/env bash
# Smoke test for a locally-running aibrix planner.
#
# Prereqs:
#   1. The planner is running and reachable on $PLANNER_URL (default 127.0.0.1:8090),
#      configured with the bundled planner-profiles.yaml.
#   2. The bundled fake_rm.py is running and reachable on $RM_URL (default 127.0.0.1:8080),
#      and matches the URL the planner was started with via PLANNER_RM_BASE_URL.
#
# Usage:
#   bash aibrix/planner/examples/smoke_test.sh
#   PLANNER_URL=http://127.0.0.1:8090 RM_URL=http://127.0.0.1:8080 \
#     bash aibrix/planner/examples/smoke_test.sh
#
# Exit code: 0 if every assertion holds, 1 otherwise.

set -uo pipefail

PLANNER_URL="${PLANNER_URL:-http://127.0.0.1:8090}"
RM_URL="${RM_URL:-http://127.0.0.1:8080}"

PASS=0
FAIL=0

green() { printf '\033[32m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
hdr()   { printf '\n\033[1m── %s ──\033[0m\n' "$*"; }

check() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$actual" == "$expected" ]]; then
        green "  PASS  $label  → $actual"
        PASS=$((PASS + 1))
    else
        red   "  FAIL  $label  → got '$actual', expected '$expected'"
        FAIL=$((FAIL + 1))
    fi
}

contains() {
    local label="$1" needle="$2" haystack="$3"
    if grep -q -F -- "$needle" <<<"$haystack"; then
        green "  PASS  $label  → contains '$needle'"
        PASS=$((PASS + 1))
    else
        red   "  FAIL  $label  → did not contain '$needle'"
        red   "        body: $haystack"
        FAIL=$((FAIL + 1))
    fi
}

NOW=$(date +%s)
DEADLINE=$((NOW + 3600))

# ---- Phase 0: planner health and status ---------------------------------
hdr "Phase 0 — planner health and status"

code=$(curl -s -o /dev/null -w "%{http_code}" "$PLANNER_URL/healthz")
check "GET /healthz returns 200" "200" "$code"

body=$(curl -s "$PLANNER_URL/v1/planner/status")
contains "/v1/planner/status reports stateless mode" "stateless-request-driven" "$body"
contains "/v1/planner/status reports configured RM URL" "$RM_URL" "$body"

# ---- Phase 1: reserved-endpoint contract --------------------------------
# POST /schedule has no Pydantic body, so an empty POST reaches the 501
# raise directly. /events/resource-delivery declares a body and is exercised
# by the unit tests instead (an empty payload would 422 before the 501).
hdr "Phase 1 — reserved-endpoint contract"

code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$PLANNER_URL/v1/planner/schedule")
check "POST /v1/planner/schedule    returns 501" "501" "$code"

# ---- Phase 2: fake RM is up (so Phase 3 will succeed) -------------------
hdr "Phase 2 — fake RM reachability"

code=$(curl -s -o /dev/null -w "%{http_code}" "$RM_URL/v1/resource-manager/resources")
check "GET $RM_URL/resources returns 200" "200" "$code"

# ---- Phase 3: happy-path /plan ------------------------------------------
hdr "Phase 3 — happy-path /plan returns a PLACE decision"

plan_body=$(curl -s -X POST "$PLANNER_URL/v1/planner/plan" \
    -H 'content-type: application/json' \
    -d "{
        \"jobs\": [{
            \"job_id\": \"j-smoke\",
            \"model_id\": \"qwen32\",
            \"total_workload\": 100000,
            \"remaining_workload\": 100000,
            \"deadline_unix\": ${DEADLINE},
            \"priority\": 5,
            \"job_status\": \"PENDING\",
            \"submit_time\": ${NOW}
        }],
        \"context\": {\"trigger\": \"smoke_test.sh\"}
    }")

contains "decision action is PLACE"      '"action":"PLACE"'       "$plan_body"
contains "decision picks cluster-a"      '"cluster_id":"cluster-a"' "$plan_body"
contains "decision picks H20"            '"gpu_type":"H20"'         "$plan_body"
contains "reservation id from fake RM"   'res-demo-001'             "$plan_body"

# ---- Phase 4a: catalog gap → empty decisions, no 5xx --------------------
hdr "Phase 4a — unknown model_id is skipped, response is still 200"

resp=$(curl -s -o /tmp/aibrix_smoke_unknown.json -w "%{http_code}" \
    -X POST "$PLANNER_URL/v1/planner/plan" \
    -H 'content-type: application/json' \
    -d "{
        \"jobs\": [{
            \"job_id\": \"j-unknown\",
            \"model_id\": \"this-model-isnt-in-the-catalog\",
            \"total_workload\": 1,
            \"remaining_workload\": 1,
            \"deadline_unix\": ${DEADLINE},
            \"priority\": 5,
            \"job_status\": \"PENDING\",
            \"submit_time\": ${NOW}
        }],
        \"context\": {\"trigger\": \"smoke_test.sh\"}
    }")
check "unknown model returns HTTP 200" "200" "$resp"
contains "decisions list is empty" '"decisions":[]' "$(cat /tmp/aibrix_smoke_unknown.json)"

# ---- summary ------------------------------------------------------------
echo
echo "================================"
printf 'Results: '
green   "$PASS pass"
if [[ $FAIL -gt 0 ]]; then
    printf '         '; red "$FAIL fail"
    exit 1
fi
exit 0
