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

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from aibrix.planner.errors import PlannerError, RMContractError, RMUnavailable
    from aibrix.planner.models import (
        ClusterResourceView,
        DecisionAction,
        HourlyResource,
        JobStatus,
        ModelGPUProfile,
        PlannerJob,
        ReservationStatus,
    )
    from aibrix.planner.policy import FCFSPolicy
    from aibrix.planner.rm_client import RMClient
    from aibrix.planner.scheduler import AvailableResources, Scheduler
    from aibrix.planner.snapshot import PlannerSnapshot

    DEPENDENCIES_AVAILABLE = True
except ModuleNotFoundError:
    DEPENDENCIES_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not DEPENDENCIES_AVAILABLE,
    reason="Dependencies not available",
)


def make_job(
    job_id: str,
    *,
    model_id: str = "qwen32",
    remaining_workload: int = 100000,
    deadline_offset: int = 3600,
    status: JobStatus | None = None,
    gpu_type: str | None = None,
    priority: int = 5,
    submit_offset: int = 60,
) -> PlannerJob:
    now = int(time.time())
    if status is None:
        status = JobStatus.PENDING
    return PlannerJob(
        job_id=job_id,
        model_id=model_id,
        gpu_type=gpu_type,
        total_workload=remaining_workload,
        remaining_workload=remaining_workload,
        deadline_unix=now + deadline_offset,
        priority=priority,
        job_status=status,
        submit_time=now - submit_offset,
    )


def make_profile(
    model_id: str = "qwen32",
    gpu_type: str = "H20",
    throughput: float = 100.0,
    single_model_gpu_number: int = 8,
    gpu_cost: float = 1.0,
) -> ModelGPUProfile:
    return ModelGPUProfile(
        model_id=model_id,
        gpu_type=gpu_type,
        throughput=throughput,
        single_model_gpu_number=single_model_gpu_number,
        gpu_cost=gpu_cost,
    )


class TestPlannerModels:
    def test_planner_job_defaults(self):
        job = make_job("j1")
        assert job.job_status == JobStatus.PENDING
        assert job.priority == 5
        assert job.submit_time is not None

    def test_snapshot_defaults(self):
        snapshot = PlannerSnapshot()
        assert snapshot.jobs == []
        assert snapshot.profiles == []


class TestAvailableResources:
    def test_allocate_and_release(self):
        avail = AvailableResources(
            [
                ClusterResourceView(
                    cluster_id="c1",
                    gpu_type="H20",
                    hours=[HourlyResource(hour=0, free=32)],
                )
            ]
        )
        assert avail.consume("c1", "H20", 16) is True
        assert avail.get_free("c1", "H20") == 16
        avail.release("c1", "H20", 8)
        assert avail.get_free("c1", "H20") == 24


@pytest.fixture
def mock_rm_client():
    client = AsyncMock(spec=RMClient)
    client.base_url = "http://rm.test"
    client.get_resources.return_value = [
        ClusterResourceView(
            cluster_id="cluster-a",
            gpu_type="H20",
            hours=[HourlyResource(hour=0, quota_cap=128, supply=72, used=0, free=72)],
        ),
        ClusterResourceView(
            cluster_id="cluster-b",
            gpu_type="A100",
            hours=[HourlyResource(hour=0, quota_cap=64, supply=32, used=0, free=32)],
        ),
    ]
    client.create_reservation.return_value = {"reservation_id": "res-test-001"}
    client.get_reservations.return_value = []
    return client


class TestFCFSScheduler:
    """MVP default: FCFS policy, first-fit placement, no rescale."""

    @pytest.fixture
    def scheduler(self, mock_rm_client):
        return Scheduler(rm_client=mock_rm_client, policy=FCFSPolicy())

    def test_default_policy_is_fcfs(self, mock_rm_client):
        scheduler = Scheduler(rm_client=mock_rm_client)
        assert isinstance(scheduler.policy, FCFSPolicy)
        assert scheduler.policy.name == "fcfs"

    @pytest.mark.asyncio
    async def test_places_pending_job_with_single_worker(
        self, scheduler, mock_rm_client
    ):
        snapshot = PlannerSnapshot(
            jobs=[make_job("j1")],
            profiles=[make_profile()],
        )

        decisions = await scheduler.plan(snapshot)

        assert len(decisions) == 1
        d = decisions[0]
        assert d.job_id == "j1"
        assert d.action == DecisionAction.PLACE
        assert d.cluster_id == "cluster-a"
        assert d.gpu_type == "H20"
        assert d.worker_num == 1, "FCFS always places with a single worker"
        assert d.reservation_ids == ["res-test-001"]
        mock_rm_client.create_reservation.assert_called_once()

    @pytest.mark.asyncio
    async def test_orders_pending_jobs_by_submit_time(self, scheduler):
        # j-late submitted after j-early; FCFS must serve j-early first.
        snapshot = PlannerSnapshot(
            jobs=[
                make_job("j-late", submit_offset=10),
                make_job("j-early", submit_offset=500),
            ],
            profiles=[make_profile()],
        )

        decisions = await scheduler.plan(snapshot)
        assert [d.job_id for d in decisions] == ["j-early", "j-late"]

    @pytest.mark.asyncio
    async def test_skips_job_when_no_cluster_fits(self, scheduler, mock_rm_client):
        mock_rm_client.get_resources.return_value = [
            ClusterResourceView(
                cluster_id="cluster-a",
                gpu_type="H20",
                hours=[HourlyResource(hour=0, free=4)],  # < 8 gpus required
            ),
        ]
        snapshot = PlannerSnapshot(
            jobs=[make_job("j1")],
            profiles=[make_profile()],
        )
        decisions = await scheduler.plan(snapshot)
        assert decisions == []
        mock_rm_client.create_reservation.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_pending_reservations_observes_rm_state(
        self, scheduler, mock_rm_client
    ):
        mock_rm_client.get_reservations.return_value = [
            {"reservation_id": "r1", "status": "DELIVERED", "delivered_gpus": 16}
        ]
        updates = await scheduler._poll_pending_reservations()
        assert updates["r1"][0] == ReservationStatus.DELIVERED
        assert updates["r1"][1] == 16


class TestRMErrorHandling:
    """RM-integration correctness for the MVP PLACE-only path.

    * Resource fetch failure must surface as a typed PlannerError (HTTP 503).
    * Reservation creation failure must release GPUs and drop the decision.
    * Missing ``reservation_id`` in RM response must raise, not fabricate.
    """

    @pytest.mark.asyncio
    async def test_resource_fetch_failure_raises_rm_unavailable(self, mock_rm_client):
        mock_rm_client.get_resources.side_effect = ConnectionError("RM down")
        scheduler = Scheduler(rm_client=mock_rm_client, policy=FCFSPolicy())
        snapshot = PlannerSnapshot(
            jobs=[make_job("j1")], profiles=[make_profile()]
        )
        with pytest.raises(RMUnavailable):
            await scheduler.plan(snapshot)

    @pytest.mark.asyncio
    async def test_reservation_creation_failure_rolls_back(self, mock_rm_client):
        mock_rm_client.create_reservation.side_effect = ConnectionError("rm 500")
        scheduler = Scheduler(rm_client=mock_rm_client, policy=FCFSPolicy())
        snapshot = PlannerSnapshot(
            jobs=[make_job("j1")], profiles=[make_profile()]
        )
        decisions = await scheduler.plan(snapshot)
        # Decision must be dropped entirely — never emit a decision without
        # an authoritative reservation id.
        assert decisions == []

    @pytest.mark.asyncio
    async def test_reservation_creation_failure_releases_consumed_gpus(
        self, mock_rm_client
    ):
        # First reservation creation fails; second succeeds. If #9's rollback
        # didn't return the consumed GPUs, the second job might be starved.
        mock_rm_client.get_resources.return_value = [
            ClusterResourceView(
                cluster_id="cluster-a",
                gpu_type="H20",
                hours=[HourlyResource(hour=0, free=8)],  # just enough for ONE job
            ),
        ]
        mock_rm_client.create_reservation.side_effect = [
            ConnectionError("transient RM 500"),
            {"reservation_id": "res-ok"},
        ]
        scheduler = Scheduler(rm_client=mock_rm_client, policy=FCFSPolicy())
        snapshot = PlannerSnapshot(
            jobs=[
                make_job("j-first", submit_offset=500),
                make_job("j-second", submit_offset=100),
            ],
            profiles=[make_profile()],
        )
        decisions = await scheduler.plan(snapshot)
        # j-first fails reservation (dropped); j-second must still succeed
        # because consumed GPUs were returned to the pool.
        assert [d.job_id for d in decisions] == ["j-second"]
        assert decisions[0].reservation_ids == ["res-ok"]

    @pytest.mark.asyncio
    async def test_missing_reservation_id_raises_contract_error(self, mock_rm_client):
        # RM returns 200 but omits reservation_id — do NOT fabricate a UUID.
        mock_rm_client.create_reservation.return_value = {"status": "accepted"}
        scheduler = Scheduler(rm_client=mock_rm_client, policy=FCFSPolicy())
        snapshot = PlannerSnapshot(
            jobs=[make_job("j1")], profiles=[make_profile()]
        )
        decisions = await scheduler.plan(snapshot)
        # The contract violation is logged and the decision is dropped.
        assert decisions == []

    def test_rm_unavailable_and_contract_error_are_planner_errors(self):
        assert issubclass(RMUnavailable, PlannerError)
        assert issubclass(RMContractError, PlannerError)
        assert RMUnavailable("x").http_status == 503
        assert RMContractError("x").http_status == 502


class TestPlannerAPI:
    @pytest.fixture
    def app_with_planner(self):
        from fastapi import FastAPI

        from aibrix.planner.api.v1.planner import router as planner_router

        app = FastAPI(redirect_slashes=False)
        app.include_router(planner_router, prefix="/v1/planner", tags=["planner"])

        mock = AsyncMock(spec=RMClient)
        mock.base_url = "http://rm.test"
        mock.get_resources.return_value = [
            ClusterResourceView(
                cluster_id="cluster-a",
                gpu_type="H20",
                hours=[HourlyResource(hour=0, quota_cap=128, supply=72, used=0, free=72)],
            ),
        ]
        mock.create_reservation.return_value = {"reservation_id": "res-api"}
        mock.get_reservations.return_value = []

        scheduler = Scheduler(rm_client=mock, policy=FCFSPolicy())
        app.state.planner_scheduler = scheduler
        return app

    @pytest.fixture
    def client(self, app_with_planner):
        from fastapi.testclient import TestClient

        return TestClient(app_with_planner)

    def test_reserved_endpoints_return_501(self, client):
        assert client.post(
            "/v1/planner/events/resource-delivery",
            json={
                "reservation_id": "r1",
                "cluster_id": "cluster-a",
                "gpu_type": "H20",
                "requested_cards": 8,
                "delivered_cards": 8,
                "start_hour": 0,
                "duration_hours": 1,
                "status": "DELIVERED",
            },
        ).status_code == 501
        assert client.post("/v1/planner/schedule").status_code == 501

    def test_plan_endpoint(self, client):
        response = client.post(
            "/v1/planner/plan",
            json={
                "jobs": [
                    {
                        "job_id": "j1",
                        "model_id": "qwen32",
                        "total_workload": 100000,
                        "remaining_workload": 100000,
                        "deadline_unix": int(time.time()) + 3600,
                        "job_status": "PENDING",
                        "priority": 5,
                    }
                ],
                "profiles": [
                    {
                        "model_id": "qwen32",
                        "gpu_type": "H20",
                        "throughput": 100.0,
                        "single_model_gpu_number": 8,
                        "gpu_cost": 1.0,
                    }
                ],
                "context": {"trigger": "periodic", "request_id": "req-1"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["trigger"] == "periodic"
        assert data["total_jobs"] == 1
        assert len(data["decisions"]) == 1
        assert data["decisions"][0]["job_id"] == "j1"

    def test_schedule_is_disabled_and_status_reports_request_mode(self, client):
        response = client.post("/v1/planner/schedule")
        assert response.status_code == 501

        status = client.get("/v1/planner/status")
        assert status.status_code == 200
        assert status.json()["mode"] == "stateless-request-driven"
        assert status.json()["planner_loop_enabled"] is False

    def test_plan_returns_503_when_rm_unavailable(self, app_with_planner):
        from fastapi.testclient import TestClient

        scheduler = app_with_planner.state.planner_scheduler
        scheduler.rm_client.get_resources.side_effect = ConnectionError("RM down")
        with TestClient(app_with_planner) as client:
            response = client.post(
                "/v1/planner/plan",
                json={
                    "jobs": [
                        {
                            "job_id": "j1",
                            "model_id": "qwen32",
                            "total_workload": 100000,
                            "remaining_workload": 100000,
                            "deadline_unix": int(time.time()) + 3600,
                            "job_status": "PENDING",
                            "priority": 5,
                        }
                    ],
                    "profiles": [
                        {
                            "model_id": "qwen32",
                            "gpu_type": "H20",
                            "throughput": 100.0,
                            "single_model_gpu_number": 8,
                            "gpu_cost": 1.0,
                        }
                    ],
                },
            )
        assert response.status_code == 503


class TestLocalFileProfileStore:
    """Profile sourcing from disk replaces MDS-pushed profiles for the MVP."""

    def _write(self, tmp_path, name: str, body: str):
        path = tmp_path / name
        path.write_text(body)
        return path

    def test_loads_yaml(self, tmp_path):
        from aibrix.planner.profile_store import LocalFileProfileStore

        path = self._write(
            tmp_path,
            "profiles.yaml",
            (
                "profiles:\n"
                "  - model_id: qwen32\n"
                "    gpu_type: H20\n"
                "    throughput: 100.0\n"
                "    single_model_gpu_number: 8\n"
                "    gpu_cost: 1.0\n"
            ),
        )
        store = LocalFileProfileStore(path)
        snap = store.snapshot()
        assert len(snap) == 1
        assert snap[0].model_id == "qwen32"
        assert snap[0].single_model_gpu_number == 8

    def test_loads_json(self, tmp_path):
        from aibrix.planner.profile_store import LocalFileProfileStore

        path = self._write(
            tmp_path,
            "profiles.json",
            (
                '{"profiles": [{"model_id": "qwen32", "gpu_type": "H20",'
                ' "throughput": 100.0, "single_model_gpu_number": 8,'
                ' "gpu_cost": 1.0}]}'
            ),
        )
        store = LocalFileProfileStore(path)
        assert store.snapshot()[0].gpu_type == "H20"

    def test_snapshot_returns_independent_copy(self, tmp_path):
        from aibrix.planner.profile_store import LocalFileProfileStore

        path = self._write(
            tmp_path,
            "profiles.yaml",
            (
                "profiles:\n"
                "  - model_id: qwen32\n"
                "    gpu_type: H20\n"
                "    throughput: 100.0\n"
                "    single_model_gpu_number: 8\n"
                "    gpu_cost: 1.0\n"
            ),
        )
        store = LocalFileProfileStore(path)
        snap = store.snapshot()
        snap.clear()
        assert len(store.snapshot()) == 1, "mutating returned list must not affect store"

    def test_missing_file_raises(self, tmp_path):
        from aibrix.planner.profile_store import (
            LocalFileProfileStore,
            ProfileStoreError,
        )

        with pytest.raises(ProfileStoreError, match="not found"):
            LocalFileProfileStore(tmp_path / "nope.yaml")

    def test_unsupported_extension_raises(self, tmp_path):
        from aibrix.planner.profile_store import (
            LocalFileProfileStore,
            ProfileStoreError,
        )

        path = self._write(tmp_path, "profiles.txt", "anything")
        with pytest.raises(ProfileStoreError, match="Unsupported"):
            LocalFileProfileStore(path)

    def test_invalid_schema_raises(self, tmp_path):
        from aibrix.planner.profile_store import (
            LocalFileProfileStore,
            ProfileStoreError,
        )

        # Missing required fields (gpu_type, throughput, ...).
        path = self._write(
            tmp_path,
            "profiles.yaml",
            "profiles:\n  - model_id: qwen32\n",
        )
        with pytest.raises(ProfileStoreError, match="Invalid profile entry"):
            LocalFileProfileStore(path)

    def test_top_level_must_have_profiles_key(self, tmp_path):
        from aibrix.planner.profile_store import (
            LocalFileProfileStore,
            ProfileStoreError,
        )

        path = self._write(tmp_path, "profiles.yaml", "[]")
        with pytest.raises(ProfileStoreError, match="top-level 'profiles'"):
            LocalFileProfileStore(path)


class TestSchedulerProfileResolution:
    """Scheduler.plan honours the request → store → empty precedence."""

    @pytest.mark.asyncio
    async def test_uses_store_when_request_omits_profiles(self, mock_rm_client):
        store = AsyncMock()
        store.snapshot = lambda: [make_profile()]
        scheduler = Scheduler(
            rm_client=mock_rm_client,
            policy=FCFSPolicy(),
            profile_store=store,
        )
        snapshot = PlannerSnapshot(jobs=[make_job("j1")])  # no profiles in request

        decisions = await scheduler.plan(snapshot)
        assert len(decisions) == 1
        assert decisions[0].job_id == "j1"

    @pytest.mark.asyncio
    async def test_request_profiles_override_store(self, mock_rm_client):
        # Store says nothing for this model.
        store = AsyncMock()
        store.snapshot = lambda: [make_profile(model_id="other-model")]
        scheduler = Scheduler(
            rm_client=mock_rm_client,
            policy=FCFSPolicy(),
            profile_store=store,
        )
        snapshot = PlannerSnapshot(
            jobs=[make_job("j1")],
            profiles=[make_profile()],  # caller-supplied profile must win
        )

        decisions = await scheduler.plan(snapshot)
        assert len(decisions) == 1, "request profiles must override store"

    @pytest.mark.asyncio
    async def test_no_store_no_request_profiles_skips_job(self, mock_rm_client):
        scheduler = Scheduler(
            rm_client=mock_rm_client,
            policy=FCFSPolicy(),
            profile_store=None,
        )
        snapshot = PlannerSnapshot(jobs=[make_job("j1")])

        decisions = await scheduler.plan(snapshot)
        assert decisions == [], "policy must skip jobs when no profile is available"
        mock_rm_client.create_reservation.assert_not_called()


class TestSchedulerSkipDiagnostics:
    """The MVP doesn't fall back when a profile is missing, but it must log
    *why* a job was skipped so the operator can tell catalog gaps from
    capacity gaps without instrumenting the call site."""

    @pytest.mark.asyncio
    async def test_warns_when_no_profile_for_job(self, mock_rm_client, caplog):
        import logging

        scheduler = Scheduler(rm_client=mock_rm_client, policy=FCFSPolicy())
        snapshot = PlannerSnapshot(jobs=[make_job("j1", model_id="unknown-model")])

        with caplog.at_level(logging.WARNING, logger="aibrix.planner.scheduler"):
            decisions = await scheduler.plan(snapshot)

        assert decisions == []
        assert any(
            "no ModelGPUProfile" in rec.getMessage()
            and rec.levelno == logging.WARNING
            for rec in caplog.records
        ), f"expected missing-profile warning, got {[r.getMessage() for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_logs_no_cluster_fits_with_capacity_summary(
        self, mock_rm_client, caplog
    ):
        import logging

        # One cluster, far too small for the 8-GPU profile.
        mock_rm_client.get_resources.return_value = [
            ClusterResourceView(
                cluster_id="cluster-tiny",
                gpu_type="H20",
                hours=[HourlyResource(hour=0, free=2)],
            )
        ]
        scheduler = Scheduler(rm_client=mock_rm_client, policy=FCFSPolicy())
        snapshot = PlannerSnapshot(
            jobs=[make_job("j1")],
            profiles=[make_profile()],
        )

        with caplog.at_level(logging.INFO, logger="aibrix.planner.scheduler"):
            decisions = await scheduler.plan(snapshot)

        assert decisions == []
        # The new log path differentiates this case from the missing-profile
        # case and surfaces the capacity that *was* available at decision time.
        no_fit = [r for r in caplog.records if "no cluster fits" in r.getMessage()]
        assert no_fit, (
            "expected 'no cluster fits' log, got "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_running_job_is_filtered_with_log(self, mock_rm_client, caplog):
        """MVP plans only PENDING/QUEUED. RUNNING jobs are not skipped silently;
        SCALE for them is deferred — see MVP_DESIGN.md."""
        import logging

        scheduler = Scheduler(rm_client=mock_rm_client, policy=FCFSPolicy())
        snapshot = PlannerSnapshot(
            jobs=[
                make_job("j-running", status=JobStatus.RUNNING),
                make_job("j-pending"),  # control: must still be planned
            ],
            profiles=[make_profile()],
        )

        with caplog.at_level(logging.INFO, logger="aibrix.planner.scheduler"):
            decisions = await scheduler.plan(snapshot)

        # Only the PENDING job produces a decision.
        assert [d.job_id for d in decisions] == ["j-pending"]
        # The RUNNING job leaves a trail so MDS can see what was dropped.
        assert any(
            "status not handled by MVP planner" in rec.getMessage()
            for rec in caplog.records
        ), f"expected MVP-status-skip log, got {[r.getMessage() for r in caplog.records]}"
