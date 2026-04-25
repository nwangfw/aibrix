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

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    MIGRATING = "MIGRATING"
    CANCELLING = "CANCELLING"
    FINALIZING = "FINALIZING"
    DONE = "DONE"
    FAILED = "FAILED"

    @classmethod
    def terminal_states(cls) -> set["JobStatus"]:
        return {cls.DONE, cls.FAILED}


class ReservationStatus(str, Enum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DecisionAction(str, Enum):
    """What the executor should do with a scheduled job.

    MVP carries only ``PLACE``; ``SCALE_UP`` / ``SCALE_DOWN`` / ``NOOP`` /
    ``MIGRATE`` / ``CANCEL`` are deferred to a later iteration (see
    ``MVP_DESIGN.md``). The field is kept as an enum (rather than dropped
    from ``SchedulingDecision``) so adding new actions stays additive.
    """

    PLACE = "PLACE"


class PlannerJob(BaseModel):
    job_id: str
    model_id: str
    gpu_type: Optional[str] = Field(
        default=None, description="Preferred GPU type; Planner may override via profile"
    )
    total_workload: int = Field(description="Total tokens to process")
    remaining_workload: int = Field(description="Tokens still remaining")
    deadline_unix: int = Field(description="Hard deadline as unix timestamp")
    priority: int = Field(default=5, ge=1, le=10)
    job_status: JobStatus = JobStatus.PENDING
    submit_time: Optional[int] = None


class ModelGPUProfile(BaseModel):
    model_id: str
    gpu_type: str
    throughput: float = Field(description="Tokens/sec per worker")
    model_config_params: Optional[Dict] = Field(
        default=None, description="Deployment config such as TP, PP"
    )
    single_model_gpu_number: int = Field(
        description="Number of GPUs per single worker replica"
    )
    gpu_cost: float = Field(description="Relative cost factor")


class HourlyResource(BaseModel):
    hour: int = Field(description="Unix timestamp at hour boundary")
    quota_cap: int = 0
    supply: int = 0
    used: int = 0
    free: int = 0


class ClusterResourceView(BaseModel):
    cluster_id: str
    gpu_type: str
    hours: List[HourlyResource] = Field(default_factory=list)


class SchedulingDecision(BaseModel):
    job_id: str
    action: DecisionAction = Field(description="What the executor should do")
    cluster_id: Optional[str] = None
    gpu_type: Optional[str] = None
    worker_num: int = Field(
        default=0,
        description="Target worker count after applying this decision",
    )
    reservation_ids: List[str] = Field(
        default_factory=list,
        description=(
            "All reservation IDs associated with this job after the decision "
            "is applied. For PLACE/SCALE_UP this is the job's prior list plus "
            "the newly created reservation; for SCALE_DOWN/NOOP it is the "
            "unchanged prior list."
        ),
    )
    reason: str = ""
