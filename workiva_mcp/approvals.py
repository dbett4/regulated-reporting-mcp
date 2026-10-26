"""Exact-plan signatures and durable single-use claims; no MCP approval tool.

The reviewer owns the Ed25519 private key. The server has only the public key.
The local journal prevents replay across processes sharing the same durable DB,
not across independent deployments or a host able to delete/restore that DB.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, model_validator

from workiva_mcp.auth import mock_mode
from workiva_mcp.config import get_region

EXECUTE_TOOL = "workiva_execute_correction"
MAX_PLAN_SECONDS = 900


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    spreadsheet_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    sheet_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    cell: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source: Source
    raw_value: str = Field(max_length=10000)
    value: str = Field(max_length=10000)


class CorrectionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["reporting.correction.v1"] = "reporting.correction.v1"
    plan_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    audience: str = Field(min_length=1, max_length=200)
    region: Literal["us", "eu", "apac"]
    transport: Literal["mock", "live"]
    created_at: int
    expires_at: int
    reason: str = Field(min_length=1, max_length=2000)
    target: Snapshot
    new_value: str = Field(pattern=r"^-?[0-9]{1,30}(\.[0-9]{1,12})?$")
    supporting: list[Snapshot] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def check_plan(self):
        if not 0 < self.expires_at - self.created_at <= MAX_PLAN_SECONDS:
            raise ValueError("Plan lifetime must be 1 to 900 seconds")
        if self.target.raw_value.lstrip().startswith("="):
            raise ValueError("Formula targets cannot be replaced by literal corrections")
        sources = [self.target.source, *(item.source for item in self.supporting)]
        keys = [(s.spreadsheet_id, s.sheet_id, s.cell) for s in sources]
        if len(set(keys)) != len(keys):
            raise ValueError("Target and supporting source references must be unique")
        return self


def canonical_plan(plan: CorrectionPlan) -> bytes:
    return json.dumps(
        plan.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def plan_digest(plan: CorrectionPlan) -> str:
    # Unlike receipt_store.argument_digest, this binds the actual payload.
    return hashlib.sha256(canonical_plan(plan)).hexdigest()


def approval_mode() -> bool:
    return bool(os.getenv("WORKIVA_REVIEW_PUBLIC_KEY", "").strip())


def deployment() -> dict[str, str]:
    audience = os.getenv("WORKIVA_APPROVAL_AUDIENCE", "").strip()
    if not audience:
        raise ValueError("WORKIVA_APPROVAL_AUDIENCE must identify this deployment")
    return {
        "audience": audience,
        "region": get_region(),
        "transport": "mock" if mock_mode() else "live",
    }


def check_fresh(plan: CorrectionPlan) -> None:
    now = int(time.time())
    if not plan.created_at <= now < plan.expires_at:
        raise ValueError("Plan is expired or not yet valid; create and review a new proposal")


def verify_approval(plan: CorrectionPlan, signature: str) -> None:
    if not approval_mode():
        raise ValueError("No reviewer public key configured")
    if any(getattr(plan, key) != value for key, value in deployment().items()):
        raise ValueError("Approval belongs to a different deployment, region, or transport")
    check_fresh(plan)
    try:
        key = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(
                Path(os.environ["WORKIVA_REVIEW_PUBLIC_KEY"]).expanduser().read_text().strip()
            )
        )
        key.verify(bytes.fromhex(signature), canonical_plan(plan))
    except (InvalidSignature, ValueError, OSError) as exc:
        raise ValueError("Approval signature or configured reviewer key is invalid") from exc


@contextmanager
def _journal(*, readonly: bool = False):
    path = Path(
        os.getenv("WORKIVA_APPROVAL_DB", str(Path.home() / ".workiva_mcp" / "approvals.sqlite3"))
    ).expanduser()
    if readonly:
        if not path.exists():
            raise FileNotFoundError("No correction journal exists")
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute("PRAGMA synchronous=FULL")
        # Serialize additive upgrades across processes; never remove old claims.
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE IF NOT EXISTS claims (plan_id TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL)"
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(claims)")}
        for name, declaration in {
            "plan_json": "TEXT",
            "phase": "TEXT NOT NULL DEFAULT 'legacy'",
            "operation_location": "TEXT",
            "claimed_at": "INTEGER",
            "updated_at": "INTEGER",
        }.items():
            if name not in columns:
                db.execute(f"ALTER TABLE claims ADD COLUMN {name} {declaration}")
        db.commit()
        yield db
    finally:
        db.close()


def claim_plan(plan: CorrectionPlan) -> None:
    """Commit the claim BEFORE preflight/network I/O. Never release a claim."""
    with _journal() as db:
        try:
            with db:
                db.execute(
                    "INSERT INTO claims (plan_id, digest, state, plan_json, phase, claimed_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        plan.plan_id,
                        plan_digest(plan),
                        "indeterminate",
                        canonical_plan(plan).decode(),
                        "preflight",
                        int(time.time()),
                        int(time.time()),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "Approval already consumed; inspect the prior attempt before making a new proposal"
            ) from exc


def record_outcome(plan: CorrectionPlan, state: str) -> None:
    with _journal() as db, db:
        db.execute(
            "UPDATE claims SET state = ?, phase = 'finished', updated_at = ? WHERE plan_id = ?",
            (state, int(time.time()), plan.plan_id),
        )


def record_progress(
    plan: CorrectionPlan, phase: str, operation_location: str | None = None
) -> None:
    """Checkpoint before I/O or polling; a checkpoint failure stops execution."""
    with _journal() as db, db:
        cursor = db.execute(
            "UPDATE claims SET phase = ?, operation_location = COALESCE(?, operation_location), "
            "updated_at = ? WHERE plan_id = ? AND digest = ? AND state = 'indeterminate'",
            (phase, operation_location, int(time.time()), plan.plan_id, plan_digest(plan)),
        )
        if cursor.rowcount != 1:
            raise ValueError("Missing or inconsistent correction claim")


def read_attempt(plan_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", plan_id):
        raise ValueError("Invalid correction plan ID")
    with _journal(readonly=True) as db:
        row = db.execute("SELECT * FROM claims WHERE plan_id = ?", (plan_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown correction attempt")
    return dict(row)


def retained_plan(attempt: dict) -> CorrectionPlan | None:
    """Validate saved content before using its targets for any network reads."""
    if not attempt.get("plan_json"):
        return None  # Legacy claims cannot be reconstructed from their digest.
    plan = CorrectionPlan.model_validate_json(attempt["plan_json"])
    if plan.plan_id != attempt["plan_id"] or plan_digest(plan) != attempt["digest"]:
        raise ValueError("Saved correction plan does not match its claim")
    if any(getattr(plan, key) != value for key, value in deployment().items()):
        raise ValueError("Saved correction belongs to a different deployment, region, or transport")
    return plan


def list_attempts(limit: int = 25) -> list[dict]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Limit must be an integer from 1 to 100")
    try:
        with _journal(readonly=True) as db:
            # rowid also orders legacy records that have no timestamps.
            rows = db.execute(
                "SELECT * FROM claims ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
    except FileNotFoundError:
        return []
    return [
        {
            "plan_id": row["plan_id"],
            "execution_state": row["state"],
            "execution_phase": dict(row).get("phase", "legacy"),
            "claimed_at": dict(row).get("claimed_at"),
            "updated_at": dict(row).get("updated_at"),
            "saved_plan_available": bool(dict(row).get("plan_json")),
        }
        for row in rows
    ]
