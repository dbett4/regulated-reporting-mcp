"""Recovery observations must never become execution proof or retry authority."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_corrections import execute, propose, signed, target
from tests.test_corrections import review as review
from workiva_mcp import approvals, compact_tools, mock_transport
from workiva_mcp.approvals import CorrectionPlan
from workiva_mcp.tools import corrections


async def inspect(plan_id):
    return json.loads(
        await compact_tools.call_tool(
            "workiva_inspect_correction",
            {"plan_id": plan_id},
            max_chars=100000,
            store_over_chars=100000,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current,classification",
    [
        ("4200000", "matches_before"),
        ("4195000", "matches_approved"),
        ("4197000", "diverged"),
    ],
)
async def test_observations_preserve_original_unknown_outcome(
    review, monkeypatch, current, classification
):
    plan = await propose()
    approvals.claim_plan(CorrectionPlan.model_validate(plan))
    mock_transport._grids[(target()["spreadsheet_id"], target()["sheet_id"])][(3, 1)] = current
    monkeypatch.setattr(approvals.time, "time", lambda: plan["expires_at"] + 60)
    before = approvals.read_attempt(plan["plan_id"])
    database = Path(os.environ["WORKIVA_APPROVAL_DB"])
    before_bytes = database.read_bytes()
    result = await inspect(plan["plan_id"])
    assert result["execution_state"] == "indeterminate"
    assert result["evidence"][0]["classification"] == classification
    assert result["evidence"][1]["classification"] == "unchanged"
    assert result["retry_allowed"] is False
    assert result["approval_consumed"] is True
    assert "state" not in result and "receipt_uri" not in result
    assert approvals.read_attempt(plan["plan_id"]) == before
    assert database.read_bytes() == before_bytes
    assert mock_transport.stats["writes"] == 0


@pytest.mark.asyncio
async def test_supporting_change_and_unavailable_target_are_separate(review, monkeypatch):
    plan = await propose()
    approvals.claim_plan(CorrectionPlan.model_validate(plan))
    original = corrections.read_snapshot

    def read(source):
        if source == CorrectionPlan.model_validate(plan).target.source:
            raise OSError("do not leak upstream content")
        return original(source).model_copy(update={"raw_value": "=NEW_FORMULA()"})

    monkeypatch.setattr(corrections, "read_snapshot", read)
    result = await inspect(plan["plan_id"])
    assert result["evidence"][0]["classification"] == "unavailable"
    assert "matches_approved" not in result["evidence"][0]
    assert result["evidence"][1]["classification"] == "changed"
    assert "do not leak" not in json.dumps(result)
    assert mock_transport.stats["writes"] == 0


@pytest.mark.asyncio
async def test_unchanged_noop_plan_does_not_imply_execution(review):
    plan = await propose()
    plan["new_value"] = plan["target"]["raw_value"]
    approvals.claim_plan(CorrectionPlan.model_validate(plan))
    result = await inspect(plan["plan_id"])
    assert result["evidence"][0]["classification"] == "matches_before_and_approved"
    assert result["execution_state"] == "indeterminate"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem", ["corrupt_plan", "wrong_deployment", "unknown_id", "invalid_id"]
)
async def test_inspection_checks_retained_targets_before_network(review, monkeypatch, problem):
    plan = await propose()
    approvals.claim_plan(CorrectionPlan.model_validate(plan))
    plan_id = plan["plan_id"]
    if problem == "corrupt_plan":
        plan["target"]["source"]["sheet_id"] = "other-sheet"
        with sqlite3.connect(os.environ["WORKIVA_APPROVAL_DB"]) as db:
            db.execute("UPDATE claims SET plan_json = ?", (json.dumps(plan),))
    elif problem == "wrong_deployment":
        monkeypatch.setenv("WORKIVA_MCP_MOCK", "0")
    elif problem == "unknown_id":
        plan_id = "0" * 32
    else:
        plan_id = "../../other"
    requests = mock_transport.stats["requests"]
    result = await inspect(plan_id)
    assert "error" in result
    assert mock_transport.stats["requests"] == requests


@pytest.mark.asyncio
async def test_legacy_upgrade_preserves_replay_and_does_not_invent_evidence(review):
    plan = await propose()
    db_path = Path(os.environ["WORKIVA_APPROVAL_DB"])
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE claims (plan_id TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO claims VALUES (?, ?, ?)", (plan["plan_id"], "old-digest", "indeterminate")
        )
    before = db_path.read_bytes()
    assert (await inspect(plan["plan_id"]))["status"] == "recovery_unavailable"
    assert approvals.list_attempts()[0]["execution_phase"] == "legacy"
    assert db_path.read_bytes() == before  # Read-only tools never migrate.
    result = await execute(signed(review, plan))  # Write path performs additive upgrade.
    assert "consumed" in result["error"]
    saved = approvals.read_attempt(plan["plan_id"])
    assert saved["digest"] == "old-digest" and saved["plan_json"] is None
    assert (await inspect(plan["plan_id"]))["status"] == "recovery_unavailable"
    assert mock_transport.stats["writes"] == 0


@pytest.mark.asyncio
async def test_list_attempts_is_bounded_metadata_only_and_does_not_create_db(review):
    path = Path(os.environ["WORKIVA_APPROVAL_DB"])
    assert approvals.list_attempts() == []
    assert not path.exists()
    plans = [await propose(), await propose()]
    for plan in plans:
        approvals.claim_plan(CorrectionPlan.model_validate(plan))
    listing = json.loads(
        await compact_tools.call_tool("workiva_list_correction_attempts", {"limit": 1})
    )
    assert len(listing["attempts"]) == 1
    assert listing["attempts"][0]["plan_id"] == plans[1]["plan_id"]
    assert "4195000" not in json.dumps(listing)
    for limit in (0, 101, True):
        with pytest.raises(ValueError):
            approvals.list_attempts(limit)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase,writes,expected", [("submitting", 0, "blocked"), ("polling", 1, "indeterminate")]
)
async def test_checkpoint_failure_never_causes_retry(review, monkeypatch, phase, writes, expected):
    plan = await propose()
    original = corrections.record_progress

    def checkpoint(plan, current_phase, *args):
        if current_phase == phase:
            raise OSError("checkpoint unavailable")
        return original(plan, current_phase, *args)

    monkeypatch.setattr(corrections, "record_progress", checkpoint)
    envelope = signed(review, plan)
    assert (await execute(envelope))["state"] == expected
    assert "consumed" in (await execute(envelope))["error"]
    assert mock_transport.stats["writes"] == writes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "crash_at,phase",
    [("before_response", "submitting"), ("polling", "polling"), ("readback", "readback")],
)
async def test_real_process_exit_after_applied_write_recovers_without_resubmission(
    review, tmp_path, crash_at, phase
):
    plan = await propose()
    envelope = signed(review, plan)
    upstream = tmp_path / "synthetic-upstream.json"
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio, json, os, sys
from pathlib import Path
from workiva_mcp import mock_transport
from workiva_mcp.tools import corrections
envelope = json.load(sys.stdin)
saved_request, saved_read, saved_poll = corrections.api_request, corrections.read_snapshot, corrections.poll_operation
def request(*args, **kwargs):
    result = saved_request(*args, **kwargs)
    if kwargs.get('method') == 'PUT':
        source = envelope['plan']['target']['source']
        value = mock_transport._grids[(source['spreadsheet_id'], source['sheet_id'])][(3, 1)]
        Path(sys.argv[1]).write_text(json.dumps({'value': value, 'writes': mock_transport.stats['writes']}))
        if sys.argv[2] == 'before_response': os._exit(73)
    return result
def poll(*args, **kwargs):
    if sys.argv[2] == 'polling': os._exit(73)
    return saved_poll(*args, **kwargs)
def read(*args, **kwargs):
    if mock_transport.stats['writes'] and sys.argv[2] == 'readback': os._exit(73)
    return saved_read(*args, **kwargs)
corrections.api_request, corrections.read_snapshot, corrections.poll_operation = request, read, poll
asyncio.run(corrections.workiva_execute_correction(**envelope))
raise SystemExit('Crash point not reached')
""",
            str(upstream),
            crash_at,
        ],
        input=json.dumps(envelope),
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert child.returncode == 73, child.stderr
    persisted_upstream = json.loads(upstream.read_text())
    assert persisted_upstream == {"value": "4195000", "writes": 1}
    # FakeWorkiva is process-local. Restore only simulated upstream state,
    # not executor state; recovery must load that from the durable journal.
    mock_transport._grids[(target()["spreadsheet_id"], target()["sheet_id"])][(3, 1)] = (
        persisted_upstream["value"]
    )
    saved = approvals.read_attempt(plan["plan_id"])
    assert saved["phase"] == phase and saved["state"] == "indeterminate"
    assert saved["plan_json"]
    assert bool(saved["operation_location"]) == (crash_at != "before_response")
    result = await inspect(plan["plan_id"])
    assert result["evidence"][0]["classification"] == "matches_approved"
    assert result["execution_state"] == "indeterminate"
    assert result["retry_allowed"] is False
    assert "consumed" in (await execute(envelope))["error"]
    assert mock_transport.stats["writes"] == 0  # No additional writes after restart.
