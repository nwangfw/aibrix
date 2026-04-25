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

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from aibrix.logger import init_logger
from aibrix.planner.errors import PlannerError
from aibrix.planner.models import (
    ModelGPUProfile,
    PlannerJob,
    SchedulingDecision,
)
from aibrix.planner.scheduler import Scheduler
from aibrix.planner.snapshot import PlannerSnapshot

logger = init_logger(__name__)

router = APIRouter()


class ResourceDeliveryEvent(BaseModel):
    reservation_id: str
    region_id: Optional[str] = None
    cluster_id: str
    gpu_type: str
    requested_cards: int
    delivered_cards: int
    start_hour: int
    duration_hours: int
    status: str


class PlannerStatusResponse(BaseModel):
    mode: str
    planner_loop_enabled: bool
    rm_base_url: str


class PlanTriggerContext(BaseModel):
    trigger: str = Field(default="manual", description="manual|periodic|event")
    event_type: Optional[str] = None
    request_id: Optional[str] = None


class PlanRequest(BaseModel):
    jobs: List[PlannerJob] = Field(default_factory=list)
    profiles: List[ModelGPUProfile] = Field(default_factory=list)
    context: Optional[PlanTriggerContext] = None


class PlanResponse(BaseModel):
    decisions: List[SchedulingDecision] = Field(default_factory=list)
    total_jobs: int
    trigger: str


def _get_scheduler(request: Request) -> Scheduler:
    if not hasattr(request.app.state, "planner_scheduler"):
        raise HTTPException(status_code=503, detail="Planner not initialized")
    return request.app.state.planner_scheduler


@router.post("/events/resource-delivery")
async def resource_delivery(request: Request, _event: ResourceDeliveryEvent) -> Dict:
    _get_scheduler(request)
    raise HTTPException(
        status_code=501,
        detail=(
            "Resource-delivery webhook ingest is not implemented in MVP. "
            "The planner observes RM reservation state by polling on each "
            "/v1/planner/plan call; a push-based receiver is on the deferred "
            "roadmap (see MVP_DESIGN.md)."
        ),
    )


@router.post("/plan")
async def plan(request: Request, payload: PlanRequest) -> Dict:
    scheduler = _get_scheduler(request)
    snapshot = PlannerSnapshot(
        jobs=payload.jobs,
        profiles=payload.profiles,
    )
    try:
        decisions = await scheduler.plan(snapshot)
    except PlannerError as e:
        # Translate typed planner errors into accurate HTTP status codes so
        # callers can distinguish "nothing to do" (200 + empty list) from a
        # degraded collaborator (e.g. RM unavailable -> 503).
        logger.warning(
            "Plan cycle aborted with PlannerError",
            error=str(e),
            type=type(e).__name__,
        )
        raise HTTPException(status_code=e.http_status, detail=str(e)) from e
    trigger = payload.context.trigger if payload.context else "manual"
    return PlanResponse(
        decisions=decisions,
        total_jobs=len(payload.jobs),
        trigger=trigger,
    ).model_dump()


@router.post("/schedule")
async def trigger_schedule(request: Request) -> Dict:
    _get_scheduler(request)
    raise HTTPException(
        status_code=501,
        detail=(
            "Planner no longer self-schedules. Let MDS call POST /v1/planner/plan "
            "periodically or on events with the current job snapshot."
        ),
    )


@router.get("/status")
async def planner_status(request: Request) -> Dict:
    scheduler = _get_scheduler(request)
    return PlannerStatusResponse(
        mode="stateless-request-driven",
        planner_loop_enabled=False,
        rm_base_url=scheduler.rm_client.base_url,
    ).model_dump()
