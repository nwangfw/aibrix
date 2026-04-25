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

"""Planner scheduler (mechanism layer).

The :class:`Scheduler` owns the parts of planning that every policy needs:

* Fetching resources and reservation state from the Resource-Manager.
* Bookkeeping free GPU capacity through :class:`AvailableResources`.
* Emitting :class:`~aibrix.planner.models.SchedulingDecision` records,
  creating RM reservations for them, and rolling back cleanly on failure.

Algorithmic choices (how to rank jobs, where to place them, when to rescale)
are delegated to a :class:`~aibrix.planner.policy.SchedulingPolicy`. The MVP
default is :class:`~aibrix.planner.policy.FCFSPolicy`; swap it via the
``policy`` constructor argument when a richer algorithm is desired.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from aibrix.logger import init_logger

from .errors import RMContractError, RMUnavailable
from .models import (
    ClusterResourceView,
    DecisionAction,
    JobStatus,
    ModelGPUProfile,
    PlannerJob,
    ReservationStatus,
    SchedulingDecision,
)
from .policy import (
    FCFSPolicy,
    PlacementProposal,
    PlanningContext,
    SchedulingPolicy,
)
from .profile_store import ProfileStore
from .rm_client import RMClient
from .snapshot import PlannerSnapshot

logger = init_logger(__name__)


class AvailableResources:
    """Tracks free GPU capacity per ``(cluster_id, gpu_type)`` within a plan cycle."""

    def __init__(self, resource_views: List[ClusterResourceView]):
        self._free: Dict[Tuple[str, str], int] = {}
        for view in resource_views:
            current_free = 0
            if view.hours:
                current_free = view.hours[0].free
            self._free[(view.cluster_id, view.gpu_type)] = current_free

    def get_free(self, cluster_id: str, gpu_type: str) -> int:
        return self._free.get((cluster_id, gpu_type), 0)

    def consume(self, cluster_id: str, gpu_type: str, count: int) -> bool:
        key = (cluster_id, gpu_type)
        current = self._free.get(key, 0)
        if current < count:
            return False
        self._free[key] = current - count
        return True

    def release(self, cluster_id: str, gpu_type: str, count: int) -> None:
        key = (cluster_id, gpu_type)
        self._free[key] = self._free.get(key, 0) + count

    def find_best_cluster(self, gpu_type: str, needed: int) -> Optional[str]:
        """Return the cluster (for ``gpu_type``) with the most free GPUs ≥ ``needed``."""
        best_cluster: Optional[str] = None
        best_free = -1
        for (cid, gtype), free in self._free.items():
            if gtype == gpu_type and free >= needed and free > best_free:
                best_cluster = cid
                best_free = free
        return best_cluster

    def all_entries(self) -> Dict[Tuple[str, str], int]:
        return dict(self._free)


@dataclass
class _CandidateDecision:
    """Bookkeeping record for a decision that has consumed resources but not
    yet been committed. Used by :class:`_DecisionBuffer` for rollback on
    reservation-creation failure.
    """

    decision: SchedulingDecision
    profile: ModelGPUProfile
    # GPU consumption that must be rolled back if the decision is dropped.
    # ``None`` for SCALE_DOWN (no consumption; the release already happened
    # and we do not want to consume it back on rollback).
    consumed: Optional[Tuple[str, str, int]]


class _DecisionBuffer:
    """Stages decisions so RM-side failures can be rolled back cleanly.

    The buffer is an MVP-grade abstraction: it captures the minimum state
    needed to undo a decision — the (cluster, gpu_type, gpus) that were
    consumed from :class:`AvailableResources` — and provides a single
    ``drop_last`` hook so callers don't have to coordinate the two data
    structures by hand. If we later need multi-step rollback, retries, or
    two-phase commit against RM, this is the place that should grow.
    """

    def __init__(self, available: AvailableResources):
        self._available = available
        self._candidates: List[_CandidateDecision] = []

    def stage(
        self,
        decision: SchedulingDecision,
        profile: ModelGPUProfile,
        consumed: Optional[Tuple[str, str, int]],
    ) -> None:
        self._candidates.append(
            _CandidateDecision(decision=decision, profile=profile, consumed=consumed)
        )

    def drop_last(self) -> None:
        if not self._candidates:
            return
        last = self._candidates.pop()
        if last.consumed is not None:
            cluster, gpu_type, gpus = last.consumed
            self._available.release(cluster, gpu_type, gpus)

    def committed(self) -> List[_CandidateDecision]:
        return list(self._candidates)


class Scheduler:
    """Orchestrates a single plan cycle given a pluggable policy."""

    def __init__(
        self,
        rm_client: RMClient,
        policy: Optional[SchedulingPolicy] = None,
        profile_store: Optional[ProfileStore] = None,
    ):
        self._rm = rm_client
        self._policy: SchedulingPolicy = policy or FCFSPolicy()
        self._profile_store = profile_store

    @property
    def rm_client(self) -> RMClient:
        return self._rm

    @property
    def policy(self) -> SchedulingPolicy:
        return self._policy

    @property
    def profile_store(self) -> Optional[ProfileStore]:
        return self._profile_store

    # ------------------------------------------------------------------
    # RM interactions (mechanism, not policy)
    # ------------------------------------------------------------------
    async def _poll_pending_reservations(self) -> Dict[str, Tuple[ReservationStatus, int]]:
        """Fetch the current set of reservations from RM and log state changes.

        The planner treats RM as the source of truth for reservation state;
        MDS is not involved and does not need to be told the observed state.
        The returned dict is kept only for internal callers/tests.

        A poll failure is logged but not raised — observing reservation state
        is best-effort and should not fail the whole planning cycle.
        """
        updates: Dict[str, Tuple[ReservationStatus, int]] = {}
        try:
            results = await self._rm.get_reservations()
        except Exception as e:
            logger.warning(
                "Failed to fetch reservations from RM; continuing without updates",
                error=str(e),
            )
            return updates

        for rm_data in results or []:
            reservation_id = rm_data.get("reservation_id")
            if not reservation_id:
                continue
            rm_status = rm_data.get("status", "PENDING")
            delivered = rm_data.get("delivered_gpus", 0)
            try:
                status = ReservationStatus(rm_status)
            except ValueError:
                continue
            updates[reservation_id] = (status, delivered)

            if status == ReservationStatus.DELIVERED:
                logger.info(
                    "Reservation delivered",
                    reservation_id=reservation_id,
                    delivered_gpus=delivered,
                )
            elif status == ReservationStatus.FAILED:
                logger.warning(
                    "Reservation failed",
                    reservation_id=reservation_id,
                )
        return updates

    async def _create_reservation_for_decision(
        self,
        decision: SchedulingDecision,
        job: PlannerJob,
        profile: ModelGPUProfile,
    ) -> str:
        """Create an RM reservation for a PLACE decision.

        Raises:
            RMContractError: RM responded without a ``reservation_id``.
            RMUnavailable: RM call raised (network error, 5xx, etc.).
        """
        gpus_needed = decision.worker_num * profile.single_model_gpu_number
        now_hour = int(time.time()) // 3600 * 3600
        duration = self._estimate_duration_hours(job, profile, decision.worker_num)

        try:
            resp = await self._rm.create_reservation(
                {
                    "gpu_type": decision.gpu_type,
                    "cluster_id": decision.cluster_id,
                    "requested_gpus": gpus_needed,
                    "start_hour": now_hour,
                    "duration_hours": duration,
                }
            )
        except Exception as e:
            raise RMUnavailable(
                f"create_reservation failed for job {job.job_id}", cause=e
            ) from e

        reservation_id = resp.get("reservation_id") if isinstance(resp, dict) else None
        if not reservation_id:
            raise RMContractError(
                f"RM response missing 'reservation_id' for job {job.job_id}: {resp!r}"
            )

        logger.info(
            "Reservation created",
            reservation_id=reservation_id,
            job_id=job.job_id,
            gpus=gpus_needed,
            cluster=decision.cluster_id,
        )
        return reservation_id

    @staticmethod
    def _estimate_duration_hours(
        job: PlannerJob, profile: ModelGPUProfile, workers: int
    ) -> int:
        throughput = profile.throughput * workers
        if throughput <= 0:
            return 1
        seconds_needed = job.remaining_workload / throughput
        return max(math.ceil(seconds_needed / 3600), 1)

    # ------------------------------------------------------------------
    # Plan cycle
    # ------------------------------------------------------------------
    async def plan(self, snapshot: PlannerSnapshot) -> List[SchedulingDecision]:
        """Run a single planning cycle.

        Raises:
            RMUnavailable: The Resource-Manager could not be reached for
                capacity information. Callers should retry.
        """
        reservation_updates = await self._poll_pending_reservations()

        # MVP considers only freshly-arrived jobs. SCALE for RUNNING jobs is
        # deferred (see MVP_DESIGN.md), and terminal jobs are never acted on.
        # Anything else is logged once so MDS can see what was dropped.
        active_jobs: List[PlannerJob] = []
        for job in snapshot.jobs:
            if job.job_status in (JobStatus.PENDING, JobStatus.QUEUED):
                active_jobs.append(job.model_copy(deep=True))
            elif job.job_status not in JobStatus.terminal_states():
                logger.info(
                    "Skipping job: status not handled by MVP planner",
                    job_id=job.job_id,
                    model_id=job.model_id,
                    job_status=job.job_status.value,
                    hint="MVP plans only PENDING/QUEUED jobs; "
                         "SCALE for RUNNING jobs is deferred",
                )
        if not active_jobs:
            return []

        try:
            resource_views = await self._rm.get_resources(horizon_hours=1)
        except Exception as e:
            # Fail loudly: callers must distinguish "nothing to do" (200 with
            # empty decisions) from "RM is down" (503).
            raise RMUnavailable(
                "Failed to fetch resources from RM; cannot plan", cause=e
            ) from e

        available = AvailableResources(resource_views)
        ctx = self._build_context(self._resolve_profiles(snapshot))
        ordered_jobs = self._policy.order_jobs(active_jobs, ctx)

        buffer = _DecisionBuffer(available)
        for job in ordered_jobs:
            await self._plan_single_job(job, available, ctx, buffer)

        if reservation_updates:
            logger.info(
                "Reservation status updates observed from RM",
                updated=len(reservation_updates),
            )
        return [c.decision for c in buffer.committed()]

    def _resolve_profiles(self, snapshot: PlannerSnapshot) -> List[ModelGPUProfile]:
        """Decide which profile catalog to use for this plan cycle.

        Caller-supplied ``snapshot.profiles`` wins (override path used by
        canary tests and ad-hoc callers). When absent, the configured
        ``ProfileStore`` is consulted. With neither, the catalog is empty
        and the policy will skip all jobs.
        """
        if snapshot.profiles:
            return list(snapshot.profiles)
        if self._profile_store is not None:
            return self._profile_store.snapshot()
        return []

    def _build_context(
        self, profiles: List[ModelGPUProfile]
    ) -> PlanningContext:
        profiles_by_model: Dict[str, List[ModelGPUProfile]] = {}
        for profile in profiles:
            profiles_by_model.setdefault(profile.model_id, []).append(profile)
        return PlanningContext(now=time.time(), profiles_by_model=profiles_by_model)

    async def _plan_single_job(
        self,
        job: PlannerJob,
        available: AvailableResources,
        ctx: PlanningContext,
        buffer: _DecisionBuffer,
    ) -> None:
        # Surface the missing-profile case explicitly. Without this branch,
        # the policy returns None for the same reason as "no cluster fits"
        # and the operator can't tell whether to fix the catalog or add
        # capacity. Best-effort placement on a synthetic profile is left
        # to a future iteration; for the MVP we log once per affected job
        # and skip it so the catalog gap is visible.
        if not ctx.profiles_for(job):
            logger.warning(
                "Skipping job: no ModelGPUProfile found",
                job_id=job.job_id,
                model_id=job.model_id,
                job_status=job.job_status.value,
                preferred_gpu_type=job.gpu_type,
                hint=(
                    "add this model to the planner profile catalog "
                    "(see PLANNER_PROFILES_PATH) or include it in the "
                    "PlanRequest.profiles override"
                ),
            )
            return

        placement = self._policy.select_placement(job, available, ctx)
        if placement is None:
            # Profile exists but no cluster has the required GPUs free.
            # Include current free capacity so the operator can tell
            # whether to add nodes or wait for in-flight reservations.
            logger.info(
                "Policy declined to place job: no cluster fits",
                policy=self._policy.name,
                job_id=job.job_id,
                model_id=job.model_id,
                available_capacity={
                    f"{c}/{g}": n
                    for (c, g), n in available.all_entries().items()
                },
            )
            return
        await self._apply_placement(job, placement, available, buffer)

    async def _apply_placement(
        self,
        job: PlannerJob,
        proposal: PlacementProposal,
        available: AvailableResources,
        buffer: _DecisionBuffer,
    ) -> None:
        gpus_needed = proposal.worker_num * proposal.profile.single_model_gpu_number
        if not available.consume(
            proposal.cluster_id, proposal.profile.gpu_type, gpus_needed
        ):
            logger.warning(
                "Policy proposed placement but resources disappeared",
                job_id=job.job_id,
                cluster=proposal.cluster_id,
                gpu_type=proposal.profile.gpu_type,
                requested=gpus_needed,
            )
            return
        decision = SchedulingDecision(
            job_id=job.job_id,
            action=DecisionAction.PLACE,
            cluster_id=proposal.cluster_id,
            gpu_type=proposal.profile.gpu_type,
            worker_num=proposal.worker_num,
            reservation_ids=[],
            reason=proposal.reason,
        )
        consumed = (proposal.cluster_id, proposal.profile.gpu_type, gpus_needed)
        await self._finalize_decision(
            decision, job, proposal.profile, available, buffer, consumed=consumed
        )

    async def _finalize_decision(
        self,
        decision: SchedulingDecision,
        job: PlannerJob,
        profile: ModelGPUProfile,
        available: AvailableResources,
        buffer: _DecisionBuffer,
        consumed: Tuple[str, str, int],
    ) -> None:
        """Create the RM reservation and stage the decision.

        Runs eagerly per job so that reservation failures release GPUs back
        into the pool **before** the next job is considered by the policy.
        Without this, later jobs could be starved by capacity that is
        effectively held by a decision destined to be dropped.
        """
        try:
            rid = await self._create_reservation_for_decision(
                decision, job, profile
            )
        except (RMContractError, RMUnavailable) as e:
            logger.error(
                "Dropping decision because reservation creation failed",
                job_id=decision.job_id,
                action=decision.action.value,
                error=str(e),
            )
            cluster, gpu_type, gpus = consumed
            available.release(cluster, gpu_type, gpus)
            return
        decision.reservation_ids.append(rid)
        buffer.stage(decision, profile, consumed=consumed)
