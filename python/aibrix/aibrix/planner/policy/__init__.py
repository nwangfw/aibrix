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

"""Scheduling policies for the planner.

The planner separates *mechanism* (reservation bookkeeping, RM calls,
`AvailableResources` accounting) from *policy* (ordering, placement).
Mechanism lives in ``aibrix.planner.scheduler``. Policies live here and
implement the :class:`SchedulingPolicy` Protocol so the MVP FCFS algorithm can
be swapped for more sophisticated ones (e.g. deadline/priority/demand scoring)
without touching the orchestration layer.

MVP ships only :class:`FCFSPolicy`. The deferred ``ScoringPolicy`` (and the
scaling Protocol method it required) will return alongside elasticity work —
see ``MVP_DESIGN.md``.
"""

from .base import PlacementProposal, PlanningContext, SchedulingPolicy
from .fcfs import FCFSPolicy

__all__ = [
    "FCFSPolicy",
    "PlacementProposal",
    "PlanningContext",
    "SchedulingPolicy",
]
