from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from .models import ModelGPUProfile, PlannerJob


class PlannerSnapshot(BaseModel):
    jobs: List[PlannerJob] = Field(default_factory=list)
    profiles: List[ModelGPUProfile] = Field(default_factory=list)
