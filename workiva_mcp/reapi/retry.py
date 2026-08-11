from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping

from .errors import WorkivaTransportError, error_from_response
from .types import Clock, ReapiResponse, Transport, get_header


@dataclass(frozen=True)
class RetryPolicy:
    """Offline retry policy.

    Grounding: live 401 handling is one auth refresh and retry in
    workiva_mcp/http_client.py:190-202, but this package performs no auth.
    Live 429 handling is special-cased at workiva_mcp/http_client.py:204-208
    and current config constants are at workiva_mcp/config.py:44-46. This
    policy is dependency-injected staging logic for later adoption, not the
    production source of truth.
    """

    max_attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 30.0
    max_retry_after: float = 120.0
    jitter_fraction: float = 0.1
    retry_statuses: frozenset[int] = field(
        default_factory=lambda: frozenset({408, 429, 500, 502, 503, 504})
    )
    retry_methods: frozenset[str] = field(
        default_factory=lambda: frozenset({"GET", "HEAD", "OPTIONS"})
    )


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Parse Retry-After seconds or HTTP-date into seconds.

    The HTTP-date behavior follows generic HTTP Retry-After semantics. Workiva
    endpoint-specific Retry-After behavior beyond local 429 throttling entries
    is UNVERIFIED — needs live confirmation.
    """

    if value is None:
        return None

    text = value.strip()
    if not text:
        return None

    try:
        return max(0.0, float(int(text, 10)))
    except ValueError:
        pass

    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    current = time.time() if now is None else now
    return max(0.0, parsed.timestamp() - current)


def is_retryable_status(status_code: int, *, policy: RetryPolicy) -> bool:
    """Return whether a status is in the injected retry set."""

    return status_code in policy.retry_statuses


def compute_backoff(
    attempt_index: int,
    *,
    policy: RetryPolicy,
    retry_after: str | None = None,
    rng: Callable[[], float] | None = None,
    now: float | None = None,
) -> float:
    """Compute retry delay for a zero-based attempt index.

    Retry-After is honored exactly and capped only by max_retry_after. Without
    Retry-After, exponential delay uses base_delay * 2**attempt_index plus
    positive jitter capped at max_delay.
    """

    parsed_retry_after = parse_retry_after(retry_after, now=now)
    if parsed_retry_after is not None:
        return min(parsed_retry_after, policy.max_retry_after)

    delay = min(policy.base_delay * (2**attempt_index), policy.max_delay)
    jitter_source = random.random if rng is None else rng
    jitter = delay * policy.jitter_fraction * jitter_source()
    return min(delay + jitter, policy.max_delay)


def request_with_retry(
    transport: Transport,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    json: Any | None = None,
    data: Any | None = None,
    timeout: float | None = None,
    policy: RetryPolicy = RetryPolicy(),
    clock: Clock,
    rng: Callable[[], float] | None = None,
    retry_mutating: bool = False,
) -> ReapiResponse:
    """Run a request through an injected transport with offline retry logic.

    Total tries equal policy.max_attempts. Non-mutating methods retry by
    default; POST/PUT/PATCH/DELETE retry only when retry_mutating=True.
    Transport exceptions are wrapped as WorkivaTransportError with __cause__
    preserved. All sleeps go through the injected clock.
    """

    if policy.max_attempts < 1:
        raise ValueError("policy.max_attempts must be at least 1")

    method_upper = method.upper()
    method_retryable = method_upper in policy.retry_methods or retry_mutating

    for attempt_index in range(policy.max_attempts):
        last_attempt = attempt_index == policy.max_attempts - 1
        try:
            response = transport.request(
                method,
                url,
                headers=headers,
                json=json,
                data=data,
                timeout=timeout,
            )
        except Exception as exc:
            wrapped = WorkivaTransportError(str(exc))
            if method_retryable and not last_attempt:
                delay = compute_backoff(
                    attempt_index, policy=policy, retry_after=None, rng=rng
                )
                clock.sleep(delay)
                continue
            raise wrapped from exc

        if response.status_code < 400:
            return response

        retryable_response = (
            is_retryable_status(response.status_code, policy=policy)
            and method_retryable
        )
        if retryable_response and not last_attempt:
            delay = compute_backoff(
                attempt_index,
                policy=policy,
                retry_after=get_header(response.headers, "Retry-After"),
                rng=rng,
            )
            clock.sleep(delay)
            continue

        raise error_from_response(response)

    raise RuntimeError("unreachable retry loop exit")
