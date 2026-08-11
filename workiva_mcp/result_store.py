"""Local result artifact storage for compact MCP calls."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workiva_mcp._io import atomic_write_text


def result_root() -> Path:
    configured = os.getenv("WORKIVA_MCP_RESULT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".workiva_mcp" / "results"


def store_result(tool_name: str, text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    result_id = _new_result_id(tool_name)
    root = result_root()
    text_path = root / f"{result_id}.txt"
    meta_path = root / f"{result_id}.json"
    meta = {
        "result_id": result_id,
        "tool": tool_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bytes": len(text.encode("utf-8")),
        "path": str(text_path),
        "uri": f"workiva-result://{result_id}",
    }
    if metadata:
        meta.update(metadata)

    atomic_write_text(text_path, text)
    atomic_write_text(meta_path, json.dumps(meta, indent=2, default=str))
    return meta


def read_result(result_id: str) -> str:
    safe_id = _safe_result_id(result_id)
    path = result_root() / f"{safe_id}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Unknown Workiva MCP result: {result_id}")
    return path.read_text(encoding="utf-8")


def read_result_metadata(result_id: str) -> dict[str, Any]:
    """Return the stored sidecar metadata for a result artifact."""
    safe_id = _safe_result_id(result_id)
    path = result_root() / f"{safe_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Unknown Workiva MCP result: {result_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_results(limit: int | None = 50, tool: str | None = None) -> list[dict[str, Any]]:
    """List stored result artifacts, newest first.

    Args:
        limit: Maximum entries to return. None or <= 0 returns all.
        tool: Optional filter on the originating tool name.
    """
    root = result_root()
    if not root.exists():
        return []
    metas: list[dict[str, Any]] = []
    for meta_path in root.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict):
            continue
        if tool and meta.get("tool") != tool:
            continue
        metas.append(meta)
    metas.sort(key=lambda m: str(m.get("created_at", "")), reverse=True)
    if limit and int(limit) > 0:
        metas = metas[: int(limit)]
    return metas


def prune_results(keep_latest: int | None = None, max_age_days: float | None = None) -> list[str]:
    """Delete old result artifacts. Explicit maintenance only — never auto-run.

    Removes both the .txt and .json files for each pruned result. Returns the
    list of removed result_ids. With no arguments this is a no-op (returns []),
    so a caller must opt in to deletion by passing a retention bound.

    Args:
        keep_latest: Keep only the N newest artifacts; delete the rest.
        max_age_days: Delete artifacts older than this many days.
    """
    if keep_latest is None and max_age_days is None:
        return []

    metas = list_results(limit=None)
    doomed: list[dict[str, Any]] = []

    if keep_latest is not None and int(keep_latest) >= 0:
        doomed.extend(metas[int(keep_latest):])

    if max_age_days is not None:
        cutoff = datetime.now(timezone.utc).timestamp() - float(max_age_days) * 86400.0
        for meta in metas:
            if meta in doomed:
                continue
            created = _parse_created(meta.get("created_at"))
            if created is not None and created < cutoff:
                doomed.append(meta)

    root = result_root()
    removed: list[str] = []
    for meta in doomed:
        rid = meta.get("result_id")
        if not rid:
            continue
        try:
            safe_id = _safe_result_id(str(rid))
        except ValueError:
            continue
        for suffix in (".txt", ".json"):
            target = root / f"{safe_id}{suffix}"
            if target.exists():
                target.unlink()
        removed.append(str(rid))
    return removed


def _parse_created(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def _new_result_id(tool_name: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in tool_name.lower()).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)[:48] or "result"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{slug}-{uuid.uuid4().hex[:10]}"


def _safe_result_id(result_id: str) -> str:
    candidate = (result_id or "").strip()
    if not candidate:
        raise ValueError("result_id is required")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if any(ch not in allowed for ch in candidate) or "/" in candidate or "\\" in candidate:
        raise ValueError("invalid result_id")
    return candidate
