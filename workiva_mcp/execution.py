"""Normalized execution boundary for Workiva mutations.

This module deliberately separates transport acceptance from evidence.  A 202 is
only ``submitted`` until its operation location reaches a terminal success
state.  This first packet reports ``applied`` after polling; readback/export
verification is a later proof-layer concern.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Callable, Mapping

from workiva_mcp.reapi.polling import operation_location_from_parts


class ExecutionState(StrEnum):
    BLOCKED = "blocked"
    SUBMITTED = "submitted"
    APPLIED = "applied"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    state: ExecutionState
    status_code: int | None
    body: Any = None
    operation_location: str | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


Request = Callable[..., tuple[int, Mapping[str, str], Any]]
Poll = Callable[..., Any]


def execute_mutation(
    request: Request,
    *,
    path: str,
    method: str,
    data: Any,
    tool_name: str,
    label: str,
    poll: Poll,
    success_statuses: tuple[int, ...] = (200, 201, 202),
) -> ExecutionResult:
    """Submit one mutation and normalize synchronous/202 completion states.

    ``request`` and ``poll`` are injected so the state machine stays unit-testable
    without credentials or network access.  A 202 without an operation location
    is deliberately indeterminate rather than a false completion.
    """

    status_code, headers, body = request(
        path,
        method=method,
        data=data,
        tool_name=tool_name,
    )
    if status_code not in success_statuses:
        return ExecutionResult(
            state=ExecutionState.FAILED,
            status_code=status_code,
            body=body,
            detail=f"{label} request failed",
        )
    if status_code != 202:
        return ExecutionResult(
            state=ExecutionState.APPLIED,
            status_code=status_code,
            body=body,
            detail=f"{label} applied synchronously; readback is still required for verification.",
        )

    try:
        location = operation_location_from_parts(headers, body, status_code=status_code)
    except Exception as exc:
        return ExecutionResult(
            state=ExecutionState.INDETERMINATE,
            status_code=status_code,
            body=body,
            detail=f"{label} accepted (202) without a usable operation location: {exc}",
        )

    try:
        completed_body = poll(location, label=label, tool_name=tool_name)
    except Exception as exc:
        return ExecutionResult(
            state=ExecutionState.INDETERMINATE,
            status_code=status_code,
            body=body,
            operation_location=location,
            detail=f"{label} submitted but polling did not prove completion: {exc}",
        )
    return ExecutionResult(
        state=ExecutionState.APPLIED,
        status_code=status_code,
        body=completed_body,
        operation_location=location,
        detail=f"{label} completed after polling; readback is still required for verification.",
    )
