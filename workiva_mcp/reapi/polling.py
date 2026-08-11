from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .errors import (
    WorkivaOperationError,
    WorkivaOperationFailedError,
    WorkivaOperationTimeoutError,
)
from .retry import RetryPolicy, parse_retry_after, request_with_retry
from .types import Clock, ReapiResponse, Transport, get_header


@dataclass(frozen=True)
class PollPolicy:
    """Offline async operation polling policy.

    Grounding: the live client polls operation locations with POLL_TIMEOUT and
    POLL_INTERVAL defaults from workiva_mcp/config.py:44-45 and treats
    completed/succeeded as terminal success in workiva_mcp/http_client.py:252-263.
    Failed operations raise in workiva_mcp/http_client.py:264-266; local timeout
    raises a 408-style WorkivaAPIError in workiva_mcp/http_client.py:270.
    """

    timeout_seconds: float = 300.0
    interval_seconds: float = 2.0
    success_statuses: tuple[str, ...] = ("completed", "succeeded")
    failure_statuses: tuple[str, ...] = ("failed", "error", "cancelled", "canceled")
    status_field: str = "status"
    resource_url_field: str = "resourceUrl"


def operation_location_from_parts(
    headers: Mapping[str, str] | None,
    body: object,
    *,
    status_code: int = 202,
) -> str:
    """Return async operation URL from raw response parts."""

    return operation_location(
        ReapiResponse(status_code=status_code, headers=headers or {}, body=body)
    )


def operation_location(response: ReapiResponse) -> str:
    """Return the async operation URL from headers or the Workiva response body.

    Grounding: Phase 1 reverse-engineering receipts confirmed Workiva export
    responses return HTTP 202 with ``operationLocation`` in the JSON body rather
    than an ``Operation-Location`` response header. Legacy scripts in this repo
    also tolerate ``Operation-Location``/``Location`` headers, so all three
    locations are accepted here for safe staged adoption.
    """

    location = get_header(response.headers, "Operation-Location") or get_header(
        response.headers, "Location"
    )
    if location:
        return location

    if isinstance(response.body, dict):
        body_location = response.body.get("operationLocation") or response.body.get(
            "operation_location"
        ) or response.body.get("operationUrl")
        operation = response.body.get("operation")
        if not body_location and isinstance(operation, dict):
            body_location = (
                operation.get("operationLocation")
                or operation.get("operation_location")
                or operation.get("operationUrl")
            )
        if body_location:
            return str(body_location)

    raise WorkivaOperationError(
        "No operation location on response headers or body",
        status_code=response.status_code,
        headers=response.headers,
        body=response.body,
    )


def poll_operation(
    location: str,
    *,
    transport: Transport,
    clock: Clock,
    headers: Mapping[str, str] | None = None,
    request_timeout: float | None = None,
    retry_policy: RetryPolicy = RetryPolicy(),
    poll_policy: PollPolicy = PollPolicy(),
    follow_resource: bool = True,
) -> ReapiResponse:
    """Poll an async operation URL until terminal status or local timeout.

    The 202 + resourceUrl follow behavior mirrors the live client at
    workiva_mcp/http_client.py:254-263 and recorded live operation flows.
    Timeout is enforced only by the injected clock; no real wall-clock sleep
    is used.
    """

    start = clock.monotonic()
    success_statuses = tuple(status.lower() for status in poll_policy.success_statuses)
    failure_statuses = tuple(status.lower() for status in poll_policy.failure_statuses)

    while True:
        if (clock.monotonic() - start) > poll_policy.timeout_seconds:
            raise WorkivaOperationTimeoutError(
                "operation polling timed out", status_code=None
            )

        poll_resp = request_with_retry(
            transport,
            "GET",
            location,
            headers=headers,
            timeout=request_timeout,
            policy=retry_policy,
            clock=clock,
        )

        body = poll_resp.body
        status = ""
        if isinstance(body, dict):
            status = str(body.get(poll_policy.status_field, "")).strip().lower()

        if status in success_statuses:
            if (
                follow_resource
                and isinstance(body, dict)
                and body.get(poll_policy.resource_url_field)
            ):
                resource_url = str(body[poll_policy.resource_url_field])
                return request_with_retry(
                    transport,
                    "GET",
                    resource_url,
                    headers=headers,
                    timeout=request_timeout,
                    policy=retry_policy,
                    clock=clock,
                )
            return poll_resp

        if status in failure_statuses:
            raise WorkivaOperationFailedError(
                f"operation {status}",
                status_code=poll_resp.status_code,
                body=poll_resp.body,
                headers=poll_resp.headers,
            )

        # Non-terminal labels such as "started", "running", and "in_progress"
        # are tolerated, but those exact Workiva semantics are UNVERIFIED — needs live confirmation.
        delay = parse_retry_after(get_header(poll_resp.headers, "Retry-After"))
        if delay is None:
            delay = poll_policy.interval_seconds
        clock.sleep(delay)
