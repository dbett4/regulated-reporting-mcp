from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class ReapiResponse:
    """Offline response value object.

    Grounding: this mirrors the status/body shape consumed by the current live
    client error and polling code in workiva_mcp/http_client.py:33-37 and
    workiva_mcp/http_client.py:247-270. It carries no auth or transport logic.
    """

    status_code: int
    headers: Mapping[str, str]
    body: Any = None
    method: str = ""
    url: str = ""


@runtime_checkable
class Transport(Protocol):
    """Dependency-injected transport contract; implementations live elsewhere.

    This package never imports workiva_mcp.http_client or opens sockets. The
    call shape is staged to mirror a generic request interface only.
    """

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
        ...


@runtime_checkable
class Clock(Protocol):
    """Clock abstraction used to keep retry and polling tests offline."""

    def monotonic(self) -> float:
        ...

    def sleep(self, seconds: float) -> None:
        ...


def get_header(headers: Mapping[str, str | None] | None, name: str) -> str | None:
    """Return a header value using case-insensitive lookup.

    Header casing tolerance is required by the offline primitives because the
    live polling path reads operation headers from Workiva responses
    (workiva_mcp/http_client.py:221-236) while tests must not depend on a
    particular casing. Falsy mappings and None values are tolerated.
    """

    if not headers or not name:
        return None

    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return None if value is None else str(value)
    return None
