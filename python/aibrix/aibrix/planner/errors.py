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

"""Typed planner errors.

The planner wraps all failures from external collaborators (today: the
Resource-Manager) into a small error hierarchy so upper layers — in
particular the HTTP API — can translate them into accurate status codes
without stringly-typed ``except Exception`` blocks.

The hierarchy is intentionally shallow for the MVP. New error classes should
be added only when a caller needs to branch on them.
"""

from __future__ import annotations


class PlannerError(Exception):
    """Base class for all planner-originated errors.

    Subclasses should carry only the information needed for the API layer to
    decide on a response status code and a human-readable message.
    """

    http_status: int = 500

    def __init__(self, message: str, *, cause: Exception | None = None):
        super().__init__(message)
        self.__cause__ = cause


class RMUnavailable(PlannerError):
    """Raised when the Resource-Manager cannot be reached or returns 5xx.

    The planner deliberately treats this as a *transient* failure: no
    decisions are emitted and the caller should retry. Mapped to HTTP 503 by
    the API layer so callers can distinguish "nothing to do" (200 OK with an
    empty decisions list) from "RM is down" (503).
    """

    http_status: int = 503


class RMContractError(PlannerError):
    """Raised when RM responds successfully but violates the expected schema.

    Examples: a ``create_reservation`` response that omits the
    ``reservation_id`` field. We surface these loudly instead of papering over
    them with fabricated identifiers, so the bug is diagnosed at the RM
    boundary rather than much later when MDS tries to look up a phantom
    reservation.
    """

    http_status: int = 502
