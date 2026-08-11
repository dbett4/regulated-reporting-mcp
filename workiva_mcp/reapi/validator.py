from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

STATUS_ALIASES = {
    "DOCS-ONLY": "DOCS_ONLY",
    "BLOCKED_PENDING_GATE": "BLOCKED-PENDING-GATE",
    "UNSUPPORTED": "UNSUPPORTED_BY_API",
    "SKIPPED": "SKIPPED_NOT_PROVISIONED",
}

VALID_STATUSES = frozenset(
    {
        "VERIFIED",
        "REFUTED",
        "UNSUPPORTED_BY_API",
        "SKIPPED_NOT_PROVISIONED",
        "GATED",
        "FLAKY_RETRYABLE",
        "DOCS_ONLY",
        "BLOCKED-PENDING-GATE",
    }
)

COMMON_REQUIRED = (
    "claim_id",
    "run_id",
    "fixture_id",
    "operator",
    "mono_seq",
    "status_enum",
    "layer",
    "classification",
    "tos_gate",
    "poll_trail",
    "reconcile_verdict",
)

STATUS_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "VERIFIED": ("request", "response", "before_readback", "after_readback", "workspace_hash", "repro_cmd"),
    "REFUTED": ("request", "response", "before_readback", "after_readback", "workspace_hash", "repro_cmd"),
    "UNSUPPORTED_BY_API": ("request", "response", "workspace_hash", "repro_cmd"),
    "SKIPPED_NOT_PROVISIONED": (),
    "GATED": (),
    "FLAKY_RETRYABLE": ("request", "response", "before_readback", "after_readback", "workspace_hash", "repro_cmd"),
    "DOCS_ONLY": ("source_url",),
    "BLOCKED-PENDING-GATE": ("request", "repro_cmd"),
}

FAMILY_CLAIM_PREFIXES = {
    "auth": ("C-auth-",),
    "cells": ("C-cells-",),
    "documents": ("C-documents-",),
    "wdata": ("C-wdata-",),
    "chains": ("C-chains-",),
    "tasks": ("C-tasks-",),
    "presentations": ("C-presentation-",),
    "async": ("C-async-",),
    "pagination": ("C-pagination-",),
    "errors": ("C-errors-",),
    "scriptrunner": ("C-scriptrunner-",),
    "link_graph": ("C-linkgraph-",),
    "formula": ("C-formula-",),
    "files": ("C-files-",),
    "data_validation": ("C-datavalidation-",),
    "structural_mutation": ("C-structural-",),
}

FAMILY_ALIASES = {
    "presentation": "presentations",
    "presentations": "presentations",
    "linkgraph": "link_graph",
    "link_graph": "link_graph",
    "data_validation": "data_validation",
    "datavalidation": "data_validation",
    "structural": "structural_mutation",
    "structural_mutation": "structural_mutation",
}

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
WRITE_STATUSES = frozenset({"VERIFIED", "REFUTED", "UNSUPPORTED_BY_API", "FLAKY_RETRYABLE"})
WRITE_REQUIRED_FIELDS = ("before_readback", "after_readback", "restore_status")


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    message: str


class ReceiptValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        message = "; ".join(f"{issue.field}: {issue.message}" for issue in issues)
        super().__init__(message)


def normalize_status(status: Any) -> str:
    text = str(status or "").strip().upper()
    return STATUS_ALIASES.get(text, text)


def normalize_family(family: Any) -> str:
    text = str(family or "").strip().lower().replace("-", "_")
    return FAMILY_ALIASES.get(text, text)


def infer_family_from_claim_id(claim_id: Any) -> str | None:
    claim_text = str(claim_id or "")
    for family, prefixes in FAMILY_CLAIM_PREFIXES.items():
        if claim_text.startswith(prefixes):
            return family
    return None


def required_fields_for_status(status: Any) -> tuple[str, ...]:
    normalized = normalize_status(status)
    if normalized not in VALID_STATUSES:
        return COMMON_REQUIRED
    return COMMON_REQUIRED + STATUS_REQUIRED_FIELDS[normalized]


def required_fields_for_receipt(receipt: Mapping[str, Any]) -> tuple[str, ...]:
    fields = list(required_fields_for_status(receipt.get("status_enum")))
    status = normalize_status(receipt.get("status_enum"))
    if status == "DOCS_ONLY" and "source_url" not in fields:
        fields.append("source_url")
    if status in WRITE_STATUSES and _is_write_receipt(receipt):
        for field in WRITE_REQUIRED_FIELDS:
            if field not in fields:
                fields.append(field)
    return tuple(fields)


def validate_receipt(receipt: Mapping[str, Any]) -> list[ValidationIssue]:
    """Return receipt validation issues without performing I/O.

    The checks are intentionally schema-focused. They validate redacted receipt
    shape and proof requirements, not whether a Workiva result is true.
    """

    issues: list[ValidationIssue] = []
    for field in COMMON_REQUIRED:
        _require_present(receipt, field, issues)
    if "ts_utc" not in receipt and "ts" not in receipt:
        issues.append(ValidationIssue("ts_utc", "required, or provide legacy ts"))

    status = normalize_status(receipt.get("status_enum"))
    if status not in VALID_STATUSES:
        issues.append(ValidationIssue("status_enum", f"unsupported status {receipt.get('status_enum')!r}"))
    else:
        for field in STATUS_REQUIRED_FIELDS[status]:
            _require_present(receipt, field, issues)

    family = normalize_family(receipt.get("family") or receipt.get("surface"))
    inferred_family = infer_family_from_claim_id(receipt.get("claim_id"))
    if not family and inferred_family:
        family = inferred_family
    if not family:
        issues.append(ValidationIssue("family", "required as family/surface or inferable from claim_id"))
    elif family not in FAMILY_CLAIM_PREFIXES:
        issues.append(ValidationIssue("family", f"unsupported family {family!r}"))
    elif inferred_family and inferred_family != family:
        issues.append(
            ValidationIssue(
                "claim_id",
                f"claim_id family {inferred_family!r} does not match receipt family {family!r}",
            )
        )

    if status == "DOCS_ONLY":
        _require_value(receipt, "source_url", issues)

    if status in WRITE_STATUSES and _is_write_receipt(receipt):
        for field in WRITE_REQUIRED_FIELDS:
            _require_value(receipt, field, issues)

    _validate_request(receipt, issues)
    _validate_poll_trail(receipt, issues)
    return issues


def assert_valid_receipt(receipt: Mapping[str, Any]) -> None:
    issues = validate_receipt(receipt)
    if issues:
        raise ReceiptValidationError(issues)


def _require_present(
    receipt: Mapping[str, Any], field: str, issues: list[ValidationIssue]
) -> None:
    if field not in receipt:
        issues.append(ValidationIssue(field, "required"))


def _require_value(
    receipt: Mapping[str, Any], field: str, issues: list[ValidationIssue]
) -> None:
    if field not in receipt:
        issues.append(ValidationIssue(field, "required"))
        return
    value = receipt[field]
    if value is None or value == "" or value == [] or value == {}:
        issues.append(ValidationIssue(field, "required non-empty value"))


def _request_method(receipt: Mapping[str, Any]) -> str:
    request = receipt.get("request")
    if isinstance(request, Mapping):
        return str(request.get("method") or "").upper()
    return str(receipt.get("method") or "").upper()


def _is_write_receipt(receipt: Mapping[str, Any]) -> bool:
    probe_type = str(receipt.get("probe_type") or "").lower()
    return _request_method(receipt) in MUTATING_METHODS or probe_type == "write"


def _validate_request(receipt: Mapping[str, Any], issues: list[ValidationIssue]) -> None:
    request = receipt.get("request")
    if request is None:
        return
    if not isinstance(request, Mapping):
        issues.append(ValidationIssue("request", "must be an object"))
        return
    method = str(request.get("method") or "").upper()
    path = request.get("path")
    if not method:
        issues.append(ValidationIssue("request.method", "required"))
    if path in (None, ""):
        issues.append(ValidationIssue("request.path", "required"))


def _validate_poll_trail(receipt: Mapping[str, Any], issues: list[ValidationIssue]) -> None:
    poll_trail = receipt.get("poll_trail")
    if poll_trail is None:
        return
    if not isinstance(poll_trail, list):
        issues.append(ValidationIssue("poll_trail", "must be a list"))
