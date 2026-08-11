from __future__ import annotations

import importlib
import os
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any, Mapping

import pytest

from workiva_mcp.reapi import (
    Clock,
    PollPolicy,
    ReapiResponse,
    RetryPolicy,
    Transport,
    WorkivaBadRequestError,
    WorkivaForbiddenError,
    WorkivaNotFoundError,
    WorkivaOperationError,
    WorkivaOperationFailedError,
    WorkivaOperationTimeoutError,
    WorkivaPreconditionFailedError,
    WorkivaRateLimitError,
    WorkivaServerError,
    WorkivaTransportError,
    WorkivaUnauthorizedError,
    WorkivaUnsupportedMediaTypeError,
    compute_backoff,
    error_from_response,
    get_header,
    operation_location,
    parse_retry_after,
    poll_operation,
    raise_for_status,
    request_with_retry,
)


@dataclass(frozen=True)
class RecordedCall:
    method: str
    url: str
    headers: Mapping[str, str] | None
    json: Any | None
    data: Any | None
    timeout: float | None


class FakeTransport:
    """Queue-backed fake transport.

    Responses are popped in order. Exhaustion raises AssertionError so tests
    fail deterministically instead of silently repeating a stale response.
    """

    def __init__(self, items: list[ReapiResponse | Exception]) -> None:
        self.items = list(items)
        self.calls: list[RecordedCall] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Any | None = None,
        data: Any | None = None,
        timeout: float | None = None,
    ) -> ReapiResponse:
        self.calls.append(RecordedCall(method, url, headers, json, data, timeout))
        if not self.items:
            raise AssertionError("FakeTransport queue exhausted")
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClock:
    def __init__(self, monotonic_values: list[float] | None = None) -> None:
        self.current = 0.0
        self.sleeps: list[float] = []
        self.monotonic_values = list(monotonic_values or [])

    def monotonic(self) -> float:
        if self.monotonic_values:
            self.current = self.monotonic_values.pop(0)
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds


def resp(
    status_code: int,
    *,
    headers: Mapping[str, str] | None = None,
    body: Any = None,
    url: str = "",
) -> ReapiResponse:
    return ReapiResponse(
        status_code=status_code,
        headers={} if headers is None else headers,
        body=body,
        url=url,
    )


def test_imports_public_api() -> None:
    import workiva_mcp.reapi as reapi

    for name in reapi.__all__:
        assert getattr(reapi, name)
    assert ReapiResponse
    assert Transport
    assert Clock


def test_raise_for_status_allows_success() -> None:
    for status_code in (200, 202, 204):
        raise_for_status(resp(status_code))


def test_error_from_response_maps_400_bad_request() -> None:
    err = error_from_response(resp(400, body={"message": "bad"}))
    assert isinstance(err, WorkivaBadRequestError)
    assert err.status_code == 400


def test_error_from_response_maps_401_unauthorized() -> None:
    err = error_from_response(resp(401))
    assert isinstance(err, WorkivaUnauthorizedError)


def test_error_from_response_maps_403_forbidden() -> None:
    err = error_from_response(resp(403))
    assert isinstance(err, WorkivaForbiddenError)


def test_error_from_response_maps_404_not_found() -> None:
    err = error_from_response(resp(404))
    assert isinstance(err, WorkivaNotFoundError)


def test_error_from_response_maps_409_conflict() -> None:
    from workiva_mcp.reapi import WorkivaConflictError

    err = error_from_response(resp(409))
    assert isinstance(err, WorkivaConflictError)


def test_error_from_response_maps_412_precondition_failed() -> None:
    err = error_from_response(resp(412))
    assert isinstance(err, WorkivaPreconditionFailedError)


def test_error_from_response_maps_415_unsupported_media_type() -> None:
    err = error_from_response(resp(415))
    assert isinstance(err, WorkivaUnsupportedMediaTypeError)


def test_error_from_response_maps_429_rate_limit_retryable() -> None:
    err = error_from_response(resp(429))
    assert isinstance(err, WorkivaRateLimitError)
    assert err.retryable is True


def test_error_from_response_maps_503_server_retryable() -> None:
    err = error_from_response(resp(503))
    assert isinstance(err, WorkivaServerError)
    assert err.retryable is True


def test_case_insensitive_header_lookup() -> None:
    assert get_header({"Retry-After": "1"}, "retry-after") == "1"
    assert get_header({"RETRY-AFTER": "2"}, "Retry-After") == "2"
    assert get_header({"Location": "a"}, "location") == "a"
    assert get_header({"location": "b"}, "Location") == "b"
    assert get_header({"X-Request-ID": "c"}, "x-request-id") == "c"
    assert get_header({"x-request-id": "d"}, "X-Request-ID") == "d"


def test_parse_retry_after_seconds() -> None:
    assert parse_retry_after("5") == 5.0


def test_parse_retry_after_http_date() -> None:
    now = 1_700_000_000.0
    header = "Tue, 14 Nov 2023 22:13:25 GMT"
    assert parse_retry_after(header, now=now) == 5.0


def test_compute_backoff_exponential_without_retry_after() -> None:
    policy = RetryPolicy(base_delay=1.0, jitter_fraction=0.1)
    assert compute_backoff(0, policy=policy, rng=lambda: 0.0) == 1.0


def test_compute_backoff_prefers_retry_after() -> None:
    policy = RetryPolicy(max_retry_after=10.0, jitter_fraction=1.0)
    assert (
        compute_backoff(3, policy=policy, retry_after="99", rng=lambda: 1.0)
        == 10.0
    )


def test_request_with_retry_returns_after_transient_503() -> None:
    transport = FakeTransport([resp(503), resp(200, body={"ok": True})])
    clock = FakeClock()
    result = request_with_retry(
        transport, "GET", "/x", policy=RetryPolicy(), clock=clock, rng=lambda: 0.0
    )
    assert result.status_code == 200
    assert len(clock.sleeps) == 1


def test_request_with_retry_stops_after_max_attempts() -> None:
    transport = FakeTransport([resp(503), resp(503), resp(503)])
    clock = FakeClock()
    with pytest.raises(WorkivaServerError):
        request_with_retry(
            transport,
            "GET",
            "/x",
            policy=RetryPolicy(max_attempts=3),
            clock=clock,
            rng=lambda: 0.0,
        )
    assert len(clock.sleeps) == 2


def test_request_with_retry_does_not_retry_400() -> None:
    transport = FakeTransport([resp(400)])
    clock = FakeClock()
    with pytest.raises(WorkivaBadRequestError):
        request_with_retry(transport, "GET", "/x", clock=clock, rng=lambda: 0.0)
    assert len(transport.calls) == 1
    assert clock.sleeps == []


def test_request_with_retry_retries_429_with_retry_after() -> None:
    transport = FakeTransport(
        [resp(429, headers={"Retry-After": "7"}), resp(200, body={"ok": True})]
    )
    clock = FakeClock()
    result = request_with_retry(transport, "GET", "/x", clock=clock, rng=lambda: 0.0)
    assert result.status_code == 200
    assert clock.sleeps == [7.0]


def test_request_with_retry_does_not_retry_post_by_default() -> None:
    transport = FakeTransport([resp(503)])
    clock = FakeClock()
    with pytest.raises(WorkivaServerError):
        request_with_retry(transport, "POST", "/x", clock=clock, rng=lambda: 0.0)
    assert len(transport.calls) == 1
    assert clock.sleeps == []


def test_request_with_retry_retries_post_when_opted_in() -> None:
    transport = FakeTransport([resp(503), resp(200)])
    clock = FakeClock()
    result = request_with_retry(
        transport,
        "POST",
        "/x",
        clock=clock,
        rng=lambda: 0.0,
        retry_mutating=True,
    )
    assert result.status_code == 200
    assert len(clock.sleeps) == 1


def test_request_with_retry_wraps_transport_exception() -> None:
    cause = TimeoutError("slow")
    transport = FakeTransport([cause])
    clock = FakeClock()
    with pytest.raises(WorkivaTransportError) as exc_info:
        request_with_retry(
            transport,
            "GET",
            "/x",
            policy=RetryPolicy(max_attempts=1),
            clock=clock,
            rng=lambda: 0.0,
        )
    assert exc_info.value.__cause__ is cause


def test_operation_location_accepts_location_header() -> None:
    assert operation_location(resp(202, headers={"Location": "/op"})) == "/op"


def test_operation_location_accepts_operation_location_header() -> None:
    assert operation_location(resp(202, headers={"Operation-Location": "/op"})) == "/op"


def test_operation_location_accepts_body_operation_location() -> None:
    assert operation_location(resp(202, body={"operationLocation": "/operations/123"})) == "/operations/123"


def test_operation_location_prefers_header_over_body_for_legacy_callers() -> None:
    assert (
        operation_location(
            resp(
                202,
                headers={"Operation-Location": "/header-op"},
                body={"operationLocation": "/body-op"},
            )
        )
        == "/header-op"
    )


def test_operation_location_missing_raises() -> None:
    with pytest.raises(WorkivaOperationError):
        operation_location(resp(202))


def test_poll_operation_returns_completed_body_without_resource_url() -> None:
    poll_resp = resp(200, body={"status": "completed"})
    transport = FakeTransport([poll_resp])
    clock = FakeClock()
    result = poll_operation("/op", transport=transport, clock=clock)
    assert result is poll_resp


def test_poll_operation_follows_resource_url() -> None:
    resource = resp(200, body={"result": True})
    transport = FakeTransport(
        [resp(200, body={"status": "completed", "resourceUrl": "/resource"}), resource]
    )
    clock = FakeClock()
    result = poll_operation("/op", transport=transport, clock=clock)
    assert result is resource
    assert [call.url for call in transport.calls] == ["/op", "/resource"]


def test_poll_operation_failed_raises() -> None:
    transport = FakeTransport([resp(200, body={"status": "failed"})])
    clock = FakeClock()
    with pytest.raises(WorkivaOperationFailedError):
        poll_operation("/op", transport=transport, clock=clock)


def test_poll_operation_timeout_raises() -> None:
    transport = FakeTransport([resp(200, body={"status": "completed"})])
    clock = FakeClock(monotonic_values=[0.0, 301.0])
    with pytest.raises(WorkivaOperationTimeoutError):
        poll_operation(
            "/op",
            transport=transport,
            clock=clock,
            poll_policy=PollPolicy(timeout_seconds=300.0),
        )
    assert transport.calls == []


def test_poll_operation_respects_retry_after_between_polls() -> None:
    transport = FakeTransport(
        [
            resp(200, headers={"Retry-After": "3"}, body={"status": "running"}),
            resp(200, body={"status": "completed"}),
        ]
    )
    clock = FakeClock()
    result = poll_operation("/op", transport=transport, clock=clock)
    assert result.status_code == 200
    assert clock.sleeps == [3.0]


def test_poll_operation_non_retryable_poll_response_raises() -> None:
    transport = FakeTransport([resp(404)])
    clock = FakeClock()
    with pytest.raises(WorkivaNotFoundError):
        poll_operation("/op", transport=transport, clock=clock)


def _clear_reapi_modules() -> None:
    for name in list(sys.modules):
        if name == "workiva_mcp.reapi" or name.startswith("workiva_mcp.reapi."):
            sys.modules.pop(name)


def test_import_reapi_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked_socket(*args: Any, **kwargs: Any) -> socket.socket:
        raise AssertionError("socket opened during import")

    monkeypatch.setattr(socket, "socket", blocked_socket)
    _clear_reapi_modules()
    importlib.import_module("workiva_mcp.reapi")
    for module in (
        "workiva_mcp.reapi.types",
        "workiva_mcp.reapi.errors",
        "workiva_mcp.reapi.retry",
        "workiva_mcp.reapi.polling",
    ):
        importlib.import_module(module)


def test_import_reapi_no_auth_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.modules.pop("workiva_mcp.auth", None)
    sys.modules.pop("workiva_mcp.http_client", None)
    _clear_reapi_modules()

    accessed_keys: list[str] = []
    original_get = type(os.environ).get

    def recording_get(self: os._Environ[str], key: str, default: Any = None) -> Any:
        accessed_keys.append(key)
        return original_get(self, key, default)

    monkeypatch.setattr(type(os.environ), "get", recording_get)
    importlib.import_module("workiva_mcp.reapi")

    assert "workiva_mcp.auth" not in sys.modules
    assert "workiva_mcp.http_client" not in sys.modules
    assert not [key for key in accessed_keys if "WORKIVA" in key.upper()]


def test_no_real_sleep_used(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked_sleep(seconds: float) -> None:
        raise AssertionError("real sleep called")

    monkeypatch.setattr(time, "sleep", blocked_sleep)

    retry_transport = FakeTransport([resp(503), resp(200)])
    retry_clock = FakeClock()
    request_with_retry(
        retry_transport, "GET", "/x", clock=retry_clock, rng=lambda: 0.0
    )
    assert retry_clock.sleeps == [1.0]

    poll_transport = FakeTransport(
        [resp(200, body={"status": "running"}), resp(200, body={"status": "completed"})]
    )
    poll_clock = FakeClock()
    poll_operation("/op", transport=poll_transport, clock=poll_clock)
    assert poll_clock.sleeps == [2.0]
