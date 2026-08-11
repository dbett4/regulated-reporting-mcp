"""Immutable, redacted execution receipts for compact Workiva mutations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workiva_mcp._io import atomic_create_text

_SECRET_KEY = re.compile(r"(token|secret|password|api[_-]?key|authorization|cookie)", re.I)
_PAYLOAD_KEY = re.compile(
    r"(^|[_-])(value|values|formula|rawvalue|raw_value|content|body|text|description)($|[_-])",
    re.I,
)
_SAFE_TARGET_KEY = re.compile(
    r"(^|[_-])(id|ids|uuid|range|ranges|row|rows|col|cols|column|columns|index|indices|count|offset|limit|operation|method|mode|status|revision|version|type|action|api|family)($|[_-])",
    re.I,
)


def receipt_root() -> Path:
    configured = os.getenv("WORKIVA_MCP_RECEIPT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".workiva_mcp" / "receipts"


def _redact(value: Any, key: str = "") -> Any:
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    # Preserve identifiers even when their compound name contains a payload
    # word (for example ``rich_text_id`` or ``content_id``). Secret-bearing
    # identifiers remain redacted by the higher-priority check above.
    if _SAFE_TARGET_KEY.search(key):
        return value
    if _PAYLOAD_KEY.search(key):
        return "[REDACTED_PAYLOAD]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    # Receipts retain only structural target fields by default. Unknown scalar
    # arguments are data, not metadata: a blacklist will inevitably miss SQL,
    # runtime inputs, rich-text inserts, and future payload names.
    return "[REDACTED_PAYLOAD]"


def argument_digest(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(_redact(arguments), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def store_receipt(
    *,
    tool: str,
    contract: dict[str, str],
    arguments: dict[str, Any],
    state: str,
    result: Any,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Store a write-once receipt without retaining sensitive request values."""
    correlation_id = correlation_id or uuid.uuid4().hex
    receipt_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{tool.replace('_', '-')[:40]}-{uuid.uuid4().hex[:10]}"
    path = receipt_root() / f"{receipt_id}.json"
    receipt = {
        "schema": "workiva.execution_receipt.v1",
        "receipt_id": receipt_id,
        "uri": f"workiva-receipt://{receipt_id}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        "tool": tool,
        "contract": contract,
        "argument_digest": argument_digest(arguments),
        "targets": _redact(arguments),
        "state": state,
        "proof": _proof_state(state, contract.get("proof", "none")),
        "result_summary": _result_summary(result),
    }
    atomic_create_text(path, json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n")
    return receipt


def _proof_state(state: str, required: str) -> str:
    if required == "none":
        return "none"
    if required == "receipt":
        return "execution_receipt"
    if state == "verified":
        return "readback_verified"
    if state == "verification_failed":
        return "readback_failed"
    return "pending_readback"


def _result_summary(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return {"keys": sorted(str(key) for key in result)[:30], "has_error": bool(result.get("error"))}
    return {"type": type(result).__name__, "sha256": hashlib.sha256(str(result).encode()).hexdigest()}


def read_receipt(receipt_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", receipt_id or ""):
        raise ValueError("invalid receipt_id")
    path = receipt_root() / f"{receipt_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Unknown Workiva MCP receipt: {receipt_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_receipts(limit: int = 25) -> list[dict[str, Any]]:
    root = receipt_root()
    if not root.exists():
        return []
    rows = []
    for path in root.glob("*.json"):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(rows, key=lambda item: item.get("created_at", ""), reverse=True)[:max(1, limit)]
