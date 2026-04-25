# Copyright 2025 The Aibrix Team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# 	http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Minimal fake Resource Manager for planner smoke tests.

Implements just enough of the RM HTTP surface for a planner running with the
bundled `planner-profiles.yaml` to produce a successful `PLACE` decision:

  GET  /v1/resource-manager/resources       — capacity view
  GET  /v1/resource-manager/reservations    — current reservations (empty)
  POST /v1/resource-manager/reservations    — create reservation (returns id)

This is **not** a real RM. The capacity it reports is a single cluster-a / H20
pool with 72 free GPUs; it advertises no other GPU types or clusters. That
intentionally limits what the planner can place, which is useful for
exercising both the happy path (`qwen32 / H20`) and the "no cluster fits" path
(`llama3-70b / H100`) using the same sample profile catalog.

Usage:
    python aibrix/planner/examples/fake_rm.py            # listens on :8080
    python aibrix/planner/examples/fake_rm.py --port 9000

Then start the planner with PLANNER_RM_BASE_URL pointing at this process.
"""

from __future__ import annotations

import argparse

import uvicorn
from fastapi import FastAPI

app = FastAPI(title="aibrix-fake-rm", version="0.0.1")


@app.get("/v1/resource-manager/resources")
def resources(horizon_hours: int = 1):
    """Single cluster, single GPU type, plenty of headroom."""
    return [
        {
            "cluster_id": "cluster-a",
            "gpu_type": "H20",
            "hours": [
                {
                    "hour": 0,
                    "quota_cap": 128,
                    "supply": 72,
                    "used": 0,
                    "free": 72,
                }
            ],
        }
    ]


@app.get("/v1/resource-manager/reservations")
def list_reservations():
    return []


@app.post("/v1/resource-manager/reservations")
def create_reservation(payload: dict):
    return {"reservation_id": "res-demo-001", **payload}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake Resource Manager for planner smoke tests")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
