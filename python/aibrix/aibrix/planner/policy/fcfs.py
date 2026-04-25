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

"""First-Come-First-Served scheduling policy (MVP default).

FCFS is intentionally trivial: jobs are ordered by ``submit_time`` ascending,
each pending job is placed on the first profile/cluster that can host **one**
worker replica, and running jobs are never rescaled. It is suitable for the
MVP because:

* The behavior is easy to reason about and debug.
* It has no knobs (no priorities, no deadline weighting, no elasticity) so
  misconfiguration is impossible.
* Its public contract is identical to richer policies, so swapping it out for
  the :class:`~aibrix.planner.policy.scoring.ScoringPolicy` or a future
  policy is a one-liner in ``service.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from ..models import PlannerJob
from .base import PlacementProposal, PlanningContext, SchedulingPolicy

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from ..scheduler import AvailableResources


class FCFSPolicy(SchedulingPolicy):
    """Order by arrival, first-fit placement, never rescale."""

    name = "fcfs"

    def order_jobs(
        self, jobs: List[PlannerJob], ctx: PlanningContext
    ) -> List[PlannerJob]:
        # Jobs without a submit_time sort last (treated as "just arrived"). We
        # keep the original relative order among ties (stable sort) so callers
        # can influence ordering by controlling input order if they wish.
        return sorted(
            jobs,
            key=lambda j: j.submit_time if j.submit_time is not None else int(ctx.now),
        )

    def select_placement(
        self,
        job: PlannerJob,
        available: "AvailableResources",
        ctx: PlanningContext,
    ) -> Optional[PlacementProposal]:
        profiles = ctx.profiles_for(job)
        if not profiles:
            return None

        # First-fit: iterate profiles in the order MDS provided, and for each
        # profile pick the first cluster with enough GPUs for a single worker
        # replica.
        for profile in profiles:
            gpus_needed = profile.single_model_gpu_number
            cluster_id = available.find_best_cluster(profile.gpu_type, gpus_needed)
            if cluster_id is not None:
                return PlacementProposal(
                    profile=profile,
                    cluster_id=cluster_id,
                    worker_num=1,
                    reason=f"fcfs-placement: model={job.model_id}, gpus={gpus_needed}",
                )
        return None
