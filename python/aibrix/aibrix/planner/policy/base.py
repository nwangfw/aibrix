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

"""Base Protocol and data types shared by all scheduling policies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Protocol, runtime_checkable

from ..models import ModelGPUProfile, PlannerJob

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from ..scheduler import AvailableResources


@dataclass
class PlanningContext:
    """Read-only snapshot of the planning-cycle environment handed to a policy.

    A context is built once per ``Scheduler.plan()`` call. Policies must treat
    the context as read-only; all mutable state changes flow through the
    :class:`~aibrix.planner.scheduler.AvailableResources` instance and through
    the proposals returned from :class:`SchedulingPolicy`.
    """

    now: float
    profiles_by_model: Dict[str, List[ModelGPUProfile]] = field(default_factory=dict)

    def profiles_for(self, job: PlannerJob) -> List[ModelGPUProfile]:
        return self.profiles_by_model.get(job.model_id, [])


@dataclass
class PlacementProposal:
    """A policy's proposal for placing a PENDING/QUEUED job.

    Always maps to :class:`~aibrix.planner.models.DecisionAction.PLACE`.
    """

    profile: ModelGPUProfile
    cluster_id: str
    worker_num: int
    reason: str = ""


@runtime_checkable
class SchedulingPolicy(Protocol):
    """Pluggable scheduling policy.

    Policies own two decisions in MVP:

    1. **Ordering** (:meth:`order_jobs`) — the sequence in which jobs are
       considered, which determines resource-allocation priority when
       resources are scarce.
    2. **Placement** (:meth:`select_placement`) — for a non-RUNNING job,
       which ``(profile, cluster)`` pair should accept it and at what
       worker count.

    Scaling (``should_scale``) is deferred — see ``MVP_DESIGN.md``. When it
    returns, this Protocol gets a third method.

    Policies are expected to be *pure* functions of their inputs: they may
    read from ``AvailableResources`` but must not mutate it. The
    :class:`~aibrix.planner.scheduler.Scheduler` is responsible for committing
    consumption/release on the live ``AvailableResources`` based on the
    proposal it accepts.
    """

    name: str

    def order_jobs(
        self, jobs: List[PlannerJob], ctx: PlanningContext
    ) -> List[PlannerJob]:
        ...

    def select_placement(
        self,
        job: PlannerJob,
        available: "AvailableResources",
        ctx: PlanningContext,
    ) -> Optional[PlacementProposal]:
        ...
