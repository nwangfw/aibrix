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

from pathlib import Path
from typing import Optional

from fastapi import FastAPI

from .policy import FCFSPolicy, SchedulingPolicy
from .profile_store import LocalFileProfileStore, ProfileStore
from .rm_client import RMClient
from .scheduler import Scheduler


async def init_planner_engine(
    app: FastAPI,
    rm_base_url: str,
    policy: Optional[SchedulingPolicy] = None,
    profile_store: Optional[ProfileStore] = None,
    profiles_path: Optional[str] = None,
) -> None:
    """Initialize the planner engine on ``app.state``.

    By default the MVP ``FCFSPolicy`` is used. Pass a different
    :class:`~aibrix.planner.policy.SchedulingPolicy` (e.g. ``ScoringPolicy``)
    to swap the scheduling algorithm without changing the call sites.

    Profile sourcing precedence:
      1. ``profile_store`` if supplied (caller already constructed one),
      2. else a :class:`LocalFileProfileStore` built from ``profiles_path``,
      3. else no store — callers must include ``profiles`` in every
         ``PlanRequest`` or the policy will skip all jobs.
    """
    import httpx

    httpx_client = httpx.AsyncClient(timeout=30.0)
    rm_client = RMClient(base_url=rm_base_url, client=httpx_client)

    store: Optional[ProfileStore] = profile_store
    if store is None and profiles_path:
        store = LocalFileProfileStore(Path(profiles_path))

    scheduler = Scheduler(
        rm_client=rm_client,
        policy=policy or FCFSPolicy(),
        profile_store=store,
    )
    app.state.planner_httpx_client = httpx_client
    app.state.planner_scheduler = scheduler


async def shutdown_planner_engine(app: FastAPI) -> None:
    if hasattr(app.state, "planner_httpx_client"):
        await app.state.planner_httpx_client.aclose()
