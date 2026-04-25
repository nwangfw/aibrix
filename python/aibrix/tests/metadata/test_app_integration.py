# Copyright 2024 The Aibrix Team.
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

import argparse
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

# Set required environment variable before importing
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aibrix.metadata.app import build_app


def test_build_app_without_k8s_job():
    """Test building app without K8s job support."""
    args = argparse.Namespace(
        enable_fastapi_docs=False,
        disable_batch_api=True,
        disable_file_api=True,
        enable_k8s_job=False,
        e2e_test=False,
    )

    app = build_app(args)

    # App should not have kopf operator wrapper
    assert not hasattr(app.state, "kopf_operator_wrapper")
    assert hasattr(app.state, "httpx_client_wrapper")


def test_build_app_with_k8s_job():
    """Test building app with K8s job support."""
    args = argparse.Namespace(
        enable_fastapi_docs=False,
        disable_batch_api=False,
        disable_file_api=True,
        enable_k8s_job=True,
        k8s_namespace="test-namespace",
        k8s_job_patch=None,
        kopf_startup_timeout=5.0,
        kopf_shutdown_timeout=2.0,
        e2e_test=False,
    )

    with patch("aibrix.metadata.app.JobCache"):
        app = build_app(args)

    # App should have kopf operator wrapper
    assert hasattr(app.state, "kopf_operator_wrapper")
    assert hasattr(app.state, "httpx_client_wrapper")
    assert hasattr(app.state, "batch_driver")

    # Check kopf operator wrapper configuration
    kopf_wrapper = app.state.kopf_operator_wrapper
    assert kopf_wrapper.namespace == "test-namespace"
    assert kopf_wrapper.startup_timeout == 5.0
    assert kopf_wrapper.shutdown_timeout == 2.0


def test_status_endpoint_without_k8s():
    """Test /status endpoint without K8s support."""
    args = argparse.Namespace(
        enable_fastapi_docs=False,
        disable_batch_api=True,
        disable_file_api=True,
        enable_k8s_job=False,
        e2e_test=False,
    )

    app = build_app(args)
    client = TestClient(app)

    response = client.get("/status")
    assert response.status_code == 200

    data = response.json()
    assert "httpx_client" in data
    assert "kopf_operator" in data
    assert "batch_driver" in data

    assert data["httpx_client"]["available"] is True
    assert data["kopf_operator"]["available"] is False
    assert data["batch_driver"]["available"] is False


def test_status_endpoint_with_k8s():
    """Test /status endpoint with K8s support."""
    args = argparse.Namespace(
        enable_fastapi_docs=False,
        disable_batch_api=False,
        disable_file_api=True,
        enable_k8s_job=True,
        k8s_job_patch=None,
        k8s_namespace="test-namespace",
        kopf_startup_timeout=5.0,
        kopf_shutdown_timeout=2.0,
        e2e_test=False,
    )

    with patch("aibrix.metadata.app.JobCache"):
        app = build_app(args)

    client = TestClient(app)

    response = client.get("/status")
    assert response.status_code == 200

    data = response.json()
    assert "httpx_client" in data
    assert "kopf_operator" in data
    assert "batch_driver" in data

    assert data["httpx_client"]["available"] is True
    assert data["kopf_operator"]["available"] is True
    assert data["batch_driver"]["available"] is True

    # Check kopf operator status details
    kopf_status = data["kopf_operator"]
    assert "is_running" in kopf_status
    assert "namespace" in kopf_status
    assert kopf_status["namespace"] == "test-namespace"
    assert kopf_status["startup_timeout"] == 5.0
    assert kopf_status["shutdown_timeout"] == 2.0


def test_healthz_endpoint():
    """Test /healthz endpoint."""
    args = argparse.Namespace(
        enable_fastapi_docs=False,
        disable_batch_api=True,
        disable_file_api=True,
        enable_k8s_job=False,
        e2e_test=False,
    )

    app = build_app(args)
    client = TestClient(app)

    response = client.get("/healthz")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"


def test_ready_endpoint():
    """Test /readyz endpoint."""
    args = argparse.Namespace(
        enable_fastapi_docs=False,
        disable_batch_api=True,
        disable_file_api=True,
        enable_k8s_job=False,
        e2e_test=False,
    )

    app = build_app(args)
    client = TestClient(app)

    response = client.get("/readyz")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ready"


def test_build_planner_app_only_mounts_planner_routes():
    """Test building planner-only app without unrelated routers."""
    from aibrix.planner.app import build_planner_app

    args = argparse.Namespace(enable_fastapi_docs=False)

    app = build_planner_app(args)

    assert hasattr(app.state, "httpx_client_wrapper")
    assert app.state.planner_enabled is True
    assert not hasattr(app.state, "batch_driver")
    assert not hasattr(app.state, "kopf_operator_wrapper")
    assert not hasattr(app.state, "storage")

    route_paths = {route.path for route in app.routes}
    assert "/healthz" in route_paths
    assert "/readyz" in route_paths
    assert "/status" in route_paths
    assert "/v1/planner/plan" in route_paths
    assert "/v1/planner/schedule" in route_paths
    assert "/v1/models/" not in route_paths
    assert "/CreateUser" not in route_paths


def test_planner_app_status_endpoint():
    """Test planner-only app keeps shared status endpoint."""
    from aibrix.planner.app import build_planner_app

    args = argparse.Namespace(enable_fastapi_docs=False)
    app = build_planner_app(args)
    client = TestClient(app)

    response = client.get("/status")
    assert response.status_code == 200

    data = response.json()
    assert data["httpx_client"]["available"] is True
    assert data["kopf_operator"]["available"] is False
    assert data["batch_driver"]["available"] is False


def test_planner_app_does_not_expose_models_or_users_api():
    """Test planner-only app exposes planner routes but not unrelated metadata APIs."""
    from aibrix.planner.app import build_planner_app

    args = argparse.Namespace(enable_fastapi_docs=False)
    app = build_planner_app(args)
    client = TestClient(app)

    assert client.get("/v1/models/").status_code == 404
    assert client.post("/CreateUser", json={"name": "u1", "rpm": 1, "tpm": 1}).status_code == 404

def _make_fake_metadata_store():
    store = MagicMock()
    store.ping = AsyncMock(return_value=True)
    store.close = AsyncMock()
    store.client = MagicMock()
    return store


def test_metadata_app_lifespan_runs_to_completion_without_planner():
    """Lifespan must enter and exit cleanly when planner is disabled.

    Regression test: a previous refactor introduced a duplicate ``lifespan``
    that called an undefined ``init_redis_client`` helper, crashing startup
    with ``NameError``. ``TestClient(app)`` alone never triggers lifespan;
    the context-manager form does.
    """
    args = argparse.Namespace(
        enable_fastapi_docs=False,
        disable_batch_api=True,
        disable_file_api=True,
        enable_k8s_job=False,
        e2e_test=False,
    )

    app = build_app(args)
    app.state.metadata_store = _make_fake_metadata_store()

    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200


def test_metadata_app_lifespan_runs_to_completion_with_planner():
    """Lifespan must enter and exit cleanly when --enable-planner is set."""
    args = argparse.Namespace(
        enable_fastapi_docs=False,
        disable_batch_api=True,
        disable_file_api=True,
        enable_k8s_job=False,
        enable_planner=True,
        e2e_test=False,
    )

    app = build_app(args)
    app.state.metadata_store = _make_fake_metadata_store()

    with (
        patch("aibrix.metadata.app.init_planner_engine", new=AsyncMock()) as init_planner,
        patch("aibrix.metadata.app.shutdown_planner_engine", new=AsyncMock()) as shutdown_planner,
        TestClient(app) as client,
    ):
        response = client.get("/healthz")
        assert response.status_code == 200

    init_planner.assert_awaited_once()
    shutdown_planner.assert_awaited_once_with(app)


def test_planner_app_lifespan_initializes_and_shuts_down_dependencies():
    """Test planner-only lifespan calls the expected helper hooks."""
    from aibrix.planner.app import build_planner_app

    args = argparse.Namespace(enable_fastapi_docs=False)
    app = build_planner_app(args)

    with (
        patch("aibrix.planner.app.init_planner_engine", new=AsyncMock()) as init_planner,
        patch("aibrix.planner.app.shutdown_planner_engine", new=AsyncMock()) as shutdown_planner,
        TestClient(app) as client,
    ):
        response = client.get("/healthz")
        assert response.status_code == 200

    init_planner.assert_awaited_once()
    init_args, init_kwargs = init_planner.await_args
    assert init_args == (app,)
    assert "rm_base_url" in init_kwargs
    shutdown_planner.assert_awaited_once_with(app)
