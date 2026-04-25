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

import argparse
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from aibrix.logger import init_logger, logging_basic_config
from aibrix.metadata.app import (
    nullable_str,
)
from aibrix.metadata.app import (
    router as system_router,
)
from aibrix.metadata.core import HTTPXClientWrapper
from aibrix.metadata.setting import settings
from aibrix.planner.api.v1 import planner
from aibrix.planner.service import init_planner_engine, shutdown_planner_engine

logger = init_logger(__name__)


@asynccontextmanager
async def planner_lifespan(app: FastAPI):
    logger.info("Initializing planner-only FastAPI app...")
    if hasattr(app.state, "httpx_client_wrapper"):
        app.state.httpx_client_wrapper.start()
    await init_planner_engine(
        app,
        rm_base_url=settings.PLANNER_RM_BASE_URL,
        profiles_path=settings.PLANNER_PROFILES_PATH,
    )
    yield

    logger.info("Finalizing planner-only FastAPI app...")
    await shutdown_planner_engine(app)
    if hasattr(app.state, "httpx_client_wrapper"):
        await app.state.httpx_client_wrapper.stop()


def build_planner_app(args: argparse.Namespace) -> FastAPI:
    if args.enable_fastapi_docs:
        app = FastAPI(
            lifespan=planner_lifespan,
            debug=False,
            redirect_slashes=False,
        )
    else:
        app = FastAPI(
            lifespan=planner_lifespan,
            debug=False,
            openapi_url=None,
            docs_url=None,
            redoc_url=None,
            redirect_slashes=False,
        )

    app.state.httpx_client_wrapper = HTTPXClientWrapper()
    app.state.planner_enabled = True

    app.include_router(system_router)
    app.include_router(
        planner.router,
        prefix=f"{settings.API_V1_STR}/planner",
        tags=["planner"],
    )
    logger.info("Planner-only API mounted at /v1/planner")
    return app


def main():
    parser = argparse.ArgumentParser(
        description=f"Run {settings.PROJECT_NAME} (planner-only)"
    )
    parser.add_argument("--host", type=nullable_str, default=None, help="host name")
    parser.add_argument("--port", type=int, default=8090, help="port number")
    parser.add_argument(
        "--enable-fastapi-docs",
        action="store_true",
        default=False,
        help="Enable FastAPI's OpenAPI schema, Swagger UI, and ReDoc endpoint",
    )
    args = parser.parse_args()

    global logger
    logging_basic_config(settings)
    logger = init_logger(__name__)

    logger.info(
        f"Using {args} to startup planner-only app",  # type: ignore[call-arg]
        project=settings.PROJECT_NAME,
    )
    app = build_planner_app(args=args)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
