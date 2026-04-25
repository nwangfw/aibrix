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

from typing import Any, Dict, List, Optional

import httpx

from aibrix.logger import init_logger

from .models import ClusterResourceView, HourlyResource

logger = init_logger(__name__)


class RMClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient):
        self._base_url = base_url.rstrip("/")
        self._client = client

    @property
    def base_url(self) -> str:
        return self._base_url

    async def get_resources(
        self,
        cluster_id: Optional[str] = None,
        gpu_type: Optional[str] = None,
        horizon_hours: int = 24,
    ) -> List[ClusterResourceView]:
        params: Dict[str, Any] = {"horizon_hours": horizon_hours}
        if cluster_id:
            params["cluster_id"] = cluster_id
        if gpu_type:
            params["gpu_type"] = gpu_type

        resp = await self._client.get(
            f"{self._base_url}/v1/resource-manager/resources",
            params=params,
        )
        resp.raise_for_status()

        result = []
        for item in resp.json():
            hours = [HourlyResource(**h) for h in item.get("hours", [])]
            result.append(
                ClusterResourceView(
                    cluster_id=item["cluster_id"],
                    gpu_type=item["gpu_type"],
                    hours=hours,
                )
            )
        return result

    async def get_reservations(
        self, reservation_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if reservation_id:
            params["reservation_id"] = reservation_id

        resp = await self._client.get(
            f"{self._base_url}/v1/resource-manager/reservations",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    async def create_reservation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = await self._client.post(
            f"{self._base_url}/v1/resource-manager/reservations",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    async def cancel_reservation(self, reservation_id: str) -> Dict[str, Any]:
        resp = await self._client.post(
            f"{self._base_url}/v1/resource-manager/reservations/{reservation_id}/cancel",
        )
        resp.raise_for_status()
        return resp.json()
