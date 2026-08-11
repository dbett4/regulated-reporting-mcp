from __future__ import annotations

from typing import Any, ClassVar, Mapping

from .types import ReapiResponse, get_header


class WorkivaRESTError(Exception):
    """Base REST exception for offline Workiva response classification.

    Grounding: the string shape intentionally follows the current live
    WorkivaAPIError constructor in workiva_mcp/http_client.py:33-37. Retryable
    status classification follows the requested offline retry set
    408/429/500/502/503/504, with local grounding for 429 handling at
    workiva_mcp/http_client.py:204-208 and a recorded live bulk-format 429
    throttling incident.
    """

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        request_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.body = body
        self.headers = headers
        self.request_id = request_id
        self.retryable = retryable
        super().__init__(str(self))

    def __str__(self) -> str:
        message = self.message or self.__class__.__name__
        if self.status_code is None:
            rendered = f"Workiva REST error: {message}"
        else:
            rendered = f"Workiva REST {self.status_code}: {message}"
        if self.request_id:
            rendered = f"{rendered} (request_id={self.request_id})"
        return rendered


class WorkivaTransportError(WorkivaRESTError):
    """Transport failure with no HTTP status.

    This class is offline-only and does not assert any Workiva endpoint
    behavior. It exists so request_with_retry can wrap injected transport
    exceptions without importing a real network client.
    """

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        request_id: str | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            body=body,
            headers=headers,
            request_id=request_id,
            retryable=retryable,
        )


class WorkivaClientError(WorkivaRESTError):
    """Base class for non-retryable 4xx classifications by default."""


class _StatusClientError(WorkivaClientError):
    status: ClassVar[int]

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        request_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            message,
            status_code=self.status if status_code is None else status_code,
            body=body,
            headers=headers,
            request_id=request_id,
            retryable=retryable,
        )


class WorkivaBadRequestError(_StatusClientError):
    """HTTP 400 Bad Request classification.

    Grounding: a recorded live incident shows a Workiva 400 BadRequest for an
    invalid sheet-create payload. This class does not assert endpoint-wide
    Workiva 400 semantics.
    """

    status = 400


class WorkivaUnauthorizedError(_StatusClientError):
    """HTTP 401 Unauthorized classification.

    Grounding: the live client clears the token, re-authenticates, and retries
    once on status 401 in workiva_mcp/http_client.py:190-202. This offline
    package only classifies the final response and performs no auth work.
    """

    status = 401


class WorkivaForbiddenError(_StatusClientError):
    """HTTP 403 Forbidden classification.

    Workiva-endpoint-specific causes such as scope or workspace mismatch are
    UNVERIFIED — needs live confirmation in the current local playbooks.
    """

    status = 403


class WorkivaNotFoundError(_StatusClientError):
    """HTTP 404 Not Found classification.

    Grounding: multiple recorded live incidents include Workiva 404 cases,
    including spurious 404s on writes that actually applied. This class
    does not assert endpoint-wide eventual-consistency behavior.
    """

    status = 404


class WorkivaConflictError(_StatusClientError):
    """HTTP 409 Conflict classification.

    Workiva-endpoint-specific stale ETag or concurrent-edit behavior is
    UNVERIFIED — needs live confirmation in the current local playbooks.
    """

    status = 409


class WorkivaPreconditionFailedError(_StatusClientError):
    """HTTP 412 Precondition Failed classification.

    Generic status handling is supported, but exact Workiva endpoint usage for
    412 is UNVERIFIED — needs live confirmation.
    """

    status = 412


class WorkivaPayloadTooLargeError(_StatusClientError):
    """HTTP 413 Payload Too Large classification.

    Payload-size handling is included for generic HTTP classification. Exact
    Workiva endpoint use of 413 is UNVERIFIED — needs live confirmation.
    """

    status = 413


class WorkivaUnsupportedMediaTypeError(_StatusClientError):
    """HTTP 415 Unsupported Media Type classification.

    Generic status handling is supported, but exact Workiva endpoint usage for
    415 is UNVERIFIED — needs live confirmation.
    """

    status = 415


class WorkivaRateLimitError(_StatusClientError):
    """HTTP 429 Rate Limit classification, retryable by default.

    Grounding: the live client special-cases 429 at
    workiva_mcp/http_client.py:204-208, and bulk-format 429 throttling has
    been observed live.
    """

    status = 429

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        request_id: str | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            body=body,
            headers=headers,
            request_id=request_id,
            retryable=retryable,
        )


class WorkivaServerError(WorkivaRESTError):
    """Base for retryable 500/502/503/504 classifications.

    Grounding: a recorded live incident shows a Workiva 500 flake that
    succeeded on retry. Other 5xx retryability is generic HTTP
    transient handling in this offline primitive and not an endpoint-specific
    Workiva claim.
    """

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        request_id: str | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            body=body,
            headers=headers,
            request_id=request_id,
            retryable=retryable,
        )


class WorkivaOperationError(WorkivaRESTError):
    """Base class for async operation polling errors.

    Grounding: the live client polls async operation locations at
    workiva_mcp/http_client.py:221-270, and 202 + operationLocation flows
    (header and body variants) are recorded live behavior.
    """


class WorkivaOperationFailedError(WorkivaOperationError):
    """Terminal failed or canceled operation status."""


class WorkivaOperationTimeoutError(WorkivaOperationError):
    """Local polling timeout.

    This is an internal 408-style timeout classification. It is NOT a claim
    that Workiva returned an HTTP 408 response.
    """


_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_ERROR_MAP: dict[int, type[WorkivaRESTError]] = {
    400: WorkivaBadRequestError,
    401: WorkivaUnauthorizedError,
    403: WorkivaForbiddenError,
    404: WorkivaNotFoundError,
    409: WorkivaConflictError,
    412: WorkivaPreconditionFailedError,
    413: WorkivaPayloadTooLargeError,
    415: WorkivaUnsupportedMediaTypeError,
    429: WorkivaRateLimitError,
    500: WorkivaServerError,
    502: WorkivaServerError,
    503: WorkivaServerError,
    504: WorkivaServerError,
}


def _request_id_from_response(response: ReapiResponse) -> str | None:
    request_id = (
        get_header(response.headers, "X-Request-Id")
        or get_header(response.headers, "X-Request-ID")
        or get_header(response.headers, "Request-Id")
    )
    if request_id:
        return request_id
    if isinstance(response.body, dict):
        body_id = response.body.get("requestId") or response.body.get("request_id")
        if body_id:
            return str(body_id)
    return None


def _message_from_body(body: Any) -> str:
    if isinstance(body, dict):
        for key in ("message", "error"):
            value = body.get(key)
            if value is not None:
                return str(value)
    return ""


def error_from_response(
    response: ReapiResponse, *, message: str | None = None
) -> WorkivaRESTError:
    """Build the best-fit offline exception from a response.

    No network, auth, or live client import is performed. Request id extraction
    is case-insensitive for common request id header spellings and also checks
    dict bodies for requestId/request_id.
    """

    status_code = response.status_code
    retryable = status_code in _RETRYABLE_STATUSES
    error_message = _message_from_body(response.body) if message is None else message
    request_id = _request_id_from_response(response)

    error_type = _ERROR_MAP.get(status_code)
    if error_type is None:
        if 400 <= status_code < 500:
            error_type = WorkivaClientError
        elif 500 <= status_code < 600:
            error_type = WorkivaServerError
        else:
            error_type = WorkivaRESTError

    return error_type(
        error_message,
        status_code=status_code,
        body=response.body,
        headers=response.headers,
        request_id=request_id,
        retryable=retryable,
    )


def raise_for_status(response: ReapiResponse) -> None:
    """Raise a classified error for HTTP statuses 400 and above."""

    if response.status_code >= 400:
        raise error_from_response(response)
