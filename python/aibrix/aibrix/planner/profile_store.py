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

"""Profile sourcing for the planner.

The planner depends on `ModelGPUProfile` records to know how to size workers
and reservations. Reference data of this kind should belong to the planner,
not be re-uploaded by every caller of `POST /v1/planner/plan`. The
`ProfileStore` Protocol is the seam: the MVP ships a local-file
implementation, and a registry-backed implementation can be dropped in later
without touching the scheduler.

Callers can still pass `profiles` in `PlanRequest` to override the store for
a single cycle (useful for canary/test); see `Scheduler.plan` for the merge
rule.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Protocol, runtime_checkable

import yaml
from pydantic import ValidationError

from .errors import PlannerError
from .models import ModelGPUProfile


class ProfileStoreError(PlannerError):
    """Raised when the configured profile source cannot be loaded.

    Surfaced loudly at startup so the planner refuses to come up with no
    profiles instead of silently degrading every plan cycle to a no-op.
    """

    http_status: int = 500


@runtime_checkable
class ProfileStore(Protocol):
    """Source of `ModelGPUProfile` records for the planner.

    Implementations must be safe to call from the request path; expensive
    fetches should be cached and refreshed out of band.
    """

    def snapshot(self) -> List[ModelGPUProfile]:
        ...


class LocalFileProfileStore:
    """Loads profiles from a JSON or YAML file on disk.

    File format (top-level object, both formats):

        profiles:
          - model_id: qwen32
            gpu_type: H20
            throughput: 100.0
            single_model_gpu_number: 8
            gpu_cost: 1.0

    Loaded once at construction; call `reload()` to re-read from disk.
    """

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._profiles: List[ModelGPUProfile] = []
        self.reload()

    @property
    def path(self) -> Path:
        return self._path

    def reload(self) -> None:
        if not self._path.exists():
            raise ProfileStoreError(
                f"Planner profile file not found: {self._path}"
            )

        suffix = self._path.suffix.lower()
        try:
            text = self._path.read_text()
            if suffix in (".yaml", ".yml"):
                raw = yaml.safe_load(text)
            elif suffix == ".json":
                raw = json.loads(text)
            else:
                raise ProfileStoreError(
                    f"Unsupported profile file extension {suffix!r}; "
                    "expected .yaml, .yml, or .json"
                )
        except (yaml.YAMLError, json.JSONDecodeError) as e:
            raise ProfileStoreError(
                f"Failed to parse planner profile file {self._path}: {e}"
            ) from e

        if not isinstance(raw, dict) or "profiles" not in raw:
            raise ProfileStoreError(
                f"Planner profile file {self._path} must be an object with a "
                f"top-level 'profiles' list"
            )
        items = raw["profiles"]
        if not isinstance(items, list):
            raise ProfileStoreError(
                f"'profiles' in {self._path} must be a list, got {type(items).__name__}"
            )

        try:
            self._profiles = [ModelGPUProfile(**item) for item in items]
        except ValidationError as e:
            raise ProfileStoreError(
                f"Invalid profile entry in {self._path}: {e}"
            ) from e

    def snapshot(self) -> List[ModelGPUProfile]:
        # Return a shallow copy so callers can't mutate our internal list.
        return list(self._profiles)
