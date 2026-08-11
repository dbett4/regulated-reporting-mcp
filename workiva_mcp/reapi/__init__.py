from __future__ import annotations

"""Pure-offline hardening layer for the Workiva REST surface.

Provides the typed 4xx/5xx error hierarchy, retry/backoff and 202-polling
policies, and the execution-receipt validator used by the execution state
machine and the tool families. The package guarantees zero I/O and zero auth:
it performs no network calls, credential access, or TLS work, and never
imports workiva_mcp.auth or workiva_mcp.http_client — which is why every
behavior in it unit-tests without credentials.
"""

from .errors import (
    WorkivaBadRequestError,
    WorkivaClientError,
    WorkivaConflictError,
    WorkivaForbiddenError,
    WorkivaNotFoundError,
    WorkivaOperationError,
    WorkivaOperationFailedError,
    WorkivaOperationTimeoutError,
    WorkivaPayloadTooLargeError,
    WorkivaPreconditionFailedError,
    WorkivaRateLimitError,
    WorkivaRESTError,
    WorkivaServerError,
    WorkivaTransportError,
    WorkivaUnauthorizedError,
    WorkivaUnsupportedMediaTypeError,
    error_from_response,
    raise_for_status,
)
from .polling import PollPolicy, operation_location, operation_location_from_parts, poll_operation
from .retry import (
    RetryPolicy,
    compute_backoff,
    is_retryable_status,
    parse_retry_after,
    request_with_retry,
)
from .types import Clock, ReapiResponse, Transport, get_header
from .validator import (
    ReceiptValidationError,
    ValidationIssue,
    assert_valid_receipt,
    infer_family_from_claim_id,
    normalize_family,
    normalize_status,
    required_fields_for_receipt,
    required_fields_for_status,
    validate_receipt,
)

__all__ = [
    "Clock",
    "PollPolicy",
    "ReapiResponse",
    "RetryPolicy",
    "ReceiptValidationError",
    "Transport",
    "ValidationIssue",
    "WorkivaBadRequestError",
    "WorkivaClientError",
    "WorkivaConflictError",
    "WorkivaForbiddenError",
    "WorkivaNotFoundError",
    "WorkivaOperationError",
    "WorkivaOperationFailedError",
    "WorkivaOperationTimeoutError",
    "WorkivaPayloadTooLargeError",
    "WorkivaPreconditionFailedError",
    "WorkivaRESTError",
    "WorkivaRateLimitError",
    "WorkivaServerError",
    "WorkivaTransportError",
    "WorkivaUnauthorizedError",
    "WorkivaUnsupportedMediaTypeError",
    "assert_valid_receipt",
    "compute_backoff",
    "error_from_response",
    "get_header",
    "infer_family_from_claim_id",
    "is_retryable_status",
    "normalize_family",
    "normalize_status",
    "operation_location",
    "operation_location_from_parts",
    "parse_retry_after",
    "poll_operation",
    "raise_for_status",
    "required_fields_for_receipt",
    "required_fields_for_status",
    "request_with_retry",
    "validate_receipt",
]
