"""Adversarial approval-boundary tests, including the real compact dispatcher."""

import copy
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from workiva_mcp import __main__, approvals, compact_tools, mock_transport
from workiva_mcp.approvals import CorrectionPlan, Source, canonical_plan, plan_digest
from workiva_mcp.receipt_store import read_receipt
from workiva_mcp.tools import corrections


@pytest.fixture
def review(monkeypatch, tmp_path):
    key = Ed25519PrivateKey.generate()
    public = tmp_path / "reviewer.pub"
    public.write_text(key.public_key().public_bytes_raw().hex())
    monkeypatch.setenv("WORKIVA_REVIEW_PUBLIC_KEY", str(public))
    monkeypatch.setenv("WORKIVA_APPROVAL_AUDIENCE", "riverton-test")
    monkeypatch.setenv("WORKIVA_APPROVAL_DB", str(tmp_path / "claims.sqlite3"))
    monkeypatch.setenv("WORKIVA_MCP_MOCK", "1")
    monkeypatch.setenv("WORKIVA_MCP_MOCK_POLL_TICKS", "0")
    monkeypatch.setenv("WORKIVA_REGION", "us")
    mock_transport.reset(rearm_429=False)
    yield key
    mock_transport.reset(rearm_429=None)


def target():
    return {
        "spreadsheet_id": mock_transport.RIVERTON_SPREADSHEET_ID,
        "sheet_id": "c3d4e5f60718293a4b5c6d7e8f90a1b2",
        "cell": "B4",
    }


async def propose():
    response = json.loads(
        await compact_tools.call_tool(
            "workiva_preview_correction",
            {
                "target": target(),
                "new_value": "4195000",
                "reason": "Tie cash to reviewed support",
                "supporting": [{**target(), "sheet_id": "riverton-review-schedule", "cell": "B2"}],
            },
            max_chars=100000,
        )
    )
    return response["plan"]


def signed(key, plan):
    return {
        "plan": plan,
        "signature": key.sign(canonical_plan(CorrectionPlan.model_validate(plan))).hex(),
    }


async def execute(envelope, **options):
    return json.loads(
        await compact_tools.call_tool(
            approvals.EXECUTE_TOOL, envelope, confirm_write=True, **options
        )
    )


def receipt(response):
    return read_receipt(response["receipt_uri"].removeprefix("workiva-receipt://"))


@pytest.mark.asyncio
async def test_approved_write_readback_and_replay(review):
    plan = await propose()
    assert plan["target"]["raw_value"] == "4200000"
    assert plan["supporting"][0]["value"] == "4195000"
    assert mock_transport.stats["writes"] == 0
    envelope = signed(review, plan)
    result = await execute(envelope, store_result_artifact=True, max_chars=1)
    assert result["state"] == "verified"
    assert receipt(result)["state"] == "verified"
    assert receipt(result)["proof"] == "readback_verified"
    assert "4195000" not in json.dumps(receipt(result))
    assert corrections.read_snapshot(Source.model_validate(target())).raw_value == "4195000"
    requests = mock_transport.stats["requests"]
    replay = await execute(envelope)
    assert replay["state"] == receipt(replay)["state"] == "blocked"
    assert receipt(replay)["proof"] == "not_dispatched"
    assert "consumed" in replay["error"]
    assert mock_transport.stats["requests"] == requests
    assert mock_transport.stats["writes"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem", ["wrong_key", "missing_key", "unreadable_key", "journal_unavailable"]
)
async def test_unavailable_or_untrusted_approval_configuration_is_fail_closed(
    review, monkeypatch, tmp_path, problem
):
    plan = await propose()
    envelope = signed(Ed25519PrivateKey.generate() if problem == "wrong_key" else review, plan)
    if problem == "missing_key":
        monkeypatch.delenv("WORKIVA_REVIEW_PUBLIC_KEY")
    elif problem == "unreadable_key":
        monkeypatch.setenv("WORKIVA_REVIEW_PUBLIC_KEY", str(tmp_path / "missing.pub"))
    elif problem == "journal_unavailable":
        monkeypatch.setenv("WORKIVA_APPROVAL_DB", str(tmp_path))  # Cannot open a directory as a DB.
    requests = mock_transport.stats["requests"]
    assert (await execute(envelope))["state"] == "blocked"
    assert mock_transport.stats["requests"] == requests
    assert mock_transport.stats["writes"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["new_value", "target", "before", "supporting", "reason", "plan_id", "expiry"]
)
async def test_approval_binds_every_part_of_plan(review, field):
    plan = await propose()
    envelope = signed(review, plan)
    altered = copy.deepcopy(plan)
    if field == "new_value":
        altered["new_value"] = "4196000"
    elif field == "target":
        altered["target"]["source"]["cell"] = "C4"
    elif field == "before":
        altered["target"]["raw_value"] = "4196000"
    elif field == "supporting":
        altered["supporting"] = []
    elif field == "reason":
        altered["reason"] = "Different accounting justification"
    elif field == "plan_id":
        altered["plan_id"] = "f" * 32
    else:
        altered["expires_at"] -= 1
    envelope["plan"] = altered
    assert plan_digest(CorrectionPlan.model_validate(altered)) != plan_digest(
        CorrectionPlan.model_validate(plan)
    )
    requests = mock_transport.stats["requests"]
    result = await execute(envelope)
    assert result["state"] == "blocked"
    assert "signature" in result["error"]
    assert mock_transport.stats["requests"] == requests
    assert mock_transport.stats["writes"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env,value",
    [("WORKIVA_APPROVAL_AUDIENCE", "other"), ("WORKIVA_REGION", "eu"), ("WORKIVA_MCP_MOCK", "0")],
)
async def test_signed_approval_not_portable_between_deployments(review, monkeypatch, env, value):
    envelope = signed(review, await propose())
    monkeypatch.setenv(env, value)
    requests = mock_transport.stats["requests"]
    result = await execute(envelope)
    assert result["state"] == "blocked"
    assert "deployment" in result["error"]
    assert mock_transport.stats["requests"] == requests  # No accidental live network I/O.


@pytest.mark.asyncio
@pytest.mark.parametrize("offset,expected", [(-1, "blocked"), (899, "verified"), (900, "blocked")])
async def test_expiry_boundaries(review, monkeypatch, offset, expected):
    plan = await propose()
    envelope = signed(review, plan)
    monkeypatch.setattr(approvals.time, "time", lambda: plan["created_at"] + offset)
    result = await execute(envelope)
    assert result["state"] == expected
    assert mock_transport.stats["writes"] == (1 if expected == "verified" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["target", "supporting"])
async def test_stale_source_blocks_and_consumes_approval(review, source):
    plan = await propose()
    envelope = signed(review, plan)
    item = plan["target"] if source == "target" else plan["supporting"][0]
    ref = item["source"]
    grid = mock_transport._grids[(ref["spreadsheet_id"], ref["sheet_id"])]
    position = (3, 1) if source == "target" else (1, 1)
    grid[position] = "4197000"  # Simulated independent operator edit, not a tool write.
    result = await execute(envelope)
    assert result["state"] == "blocked"
    assert "stale" in result["error"]
    grid[position] = item["raw_value"]
    assert "consumed" in (await execute(envelope))["error"]
    assert mock_transport.stats["writes"] == 0


@pytest.mark.asyncio
async def test_raw_formula_change_with_same_calculated_value_is_stale(review, monkeypatch):
    plan = await propose()
    original = corrections.read_snapshot

    def changed(source):
        result = original(source)
        if source.sheet_id == "riverton-review-schedule":
            return result.model_copy(update={"raw_value": "=SUM(B9:B10)"})
        return result

    monkeypatch.setattr(corrections, "read_snapshot", changed)
    result = await execute(signed(review, plan))
    assert result["state"] == "blocked"
    assert mock_transport.stats["writes"] == 0


@pytest.mark.asyncio
async def test_expiry_during_preflight_blocks_write(review, monkeypatch):
    plan = await propose()
    original = corrections.read_snapshot

    def slow(source):
        result = original(source)
        monkeypatch.setattr(approvals.time, "time", lambda: plan["expires_at"])
        return result

    monkeypatch.setattr(corrections, "read_snapshot", slow)
    assert (await execute(signed(review, plan)))["state"] == "blocked"
    assert mock_transport.stats["writes"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,expected",
    [
        ("before", "blocked"),
        ("after", "indeterminate"),
        ("mismatch", "verification_failed"),
        ("poll", "indeterminate"),
    ],
)
async def test_failure_states_survive_receipts_artifacts_and_replay(
    review, monkeypatch, failure, expected
):
    plan = await propose()
    original = corrections.read_snapshot

    def read(source):
        if failure == "before":
            raise OSError("sensitive upstream failure")
        if mock_transport.stats["writes"]:
            if failure == "after":
                raise OSError("sensitive upstream failure")
            if failure == "mismatch":
                return original(source).model_copy(update={"raw_value": "999"})
        return original(source)

    def poll(*args, **kwargs):
        raise TimeoutError("sensitive upstream timeout")

    monkeypatch.setattr(corrections, "read_snapshot", read)
    if failure == "poll":
        monkeypatch.setattr(corrections, "poll_operation", poll)
    envelope = signed(review, plan)
    result = await execute(envelope, store_result_artifact=True, max_chars=1)
    assert result["state"] == receipt(result)["state"] == expected
    assert "sensitive upstream" not in json.dumps(result)
    assert "error" in result
    assert "consumed" in (await execute(envelope))["error"]
    assert mock_transport.stats["writes"] == (0 if failure == "before" else 1)


@pytest.mark.asyncio
async def test_bypass_tools_and_raw_entrypoint_disabled(review, monkeypatch):
    result = json.loads(
        await compact_tools.call_tool(
            "workiva_write_verified",
            {
                "spreadsheet_id": target()["spreadsheet_id"],
                "sheet_id": target()["sheet_id"],
                "cells": [{"row": 3, "col": 1, "value": "1"}],
            },
            confirm_write=True,
        )
    )
    assert result["policy_blocked"]
    assert mock_transport.stats["requests"] == 0
    monkeypatch.setenv("WORKIVA_MCP_CATALOG_MODE", "full")
    monkeypatch.setenv("WORKIVA_MCP_ALLOW_UNGATED_FULL_CATALOG", "1")
    with pytest.raises(RuntimeError, match="Reviewer-key"):
        __main__._load_mcp()
    assert not any("reviewer" in name for name in compact_tools.get_tool_definitions())


@pytest.mark.asyncio
async def test_batch_stops_on_rejected_approval(review):
    plan = await propose()
    steps = [
        {"name": approvals.EXECUTE_TOOL, "arguments": {"plan": plan, "signature": "00"}},
        {"name": "workiva_list_spreadsheets", "arguments": {}},
    ]
    result = json.loads(await compact_tools.batch_call(steps, confirm_write=True))
    assert result["count"] == 1
    assert result["results"][0]["result"]["state"] == "blocked"
    assert mock_transport.stats["writes"] == 0


@pytest.mark.asyncio
async def test_formula_target_and_missing_raw_cell_rejected(review):
    for cell in ("B8", "Z99"):
        response = json.loads(
            await compact_tools.call_tool(
                "workiva_preview_correction",
                {
                    "target": {
                        **target(),
                        "sheet_id": "b2c3d4e5f60718293a4b5c6d7e8f90a1",
                        "cell": cell,
                    },
                    "new_value": "1",
                    "reason": "Invalid target",
                },
            )
        )
        assert "error" in response
        assert "plan" not in response
    assert mock_transport.stats["writes"] == 0


@pytest.mark.asyncio
async def test_concurrent_claims_and_process_restart(review):
    plan = CorrectionPlan.model_validate(await propose())

    def claim():
        try:
            approvals.claim_plan(plan)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: claim(), range(4))).count(True) == 1
    # A new process must still refuse after a crash between claim and submission.
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json, sys
from workiva_mcp.approvals import CorrectionPlan, claim_plan
try:
    claim_plan(CorrectionPlan.model_validate(json.load(sys.stdin)))
except ValueError:
    print('REPLAY_BLOCKED')
else:
    raise SystemExit(1)
""",
        ],
        input=json.dumps(plan.model_dump()),
        text=True,
        capture_output=True,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "REPLAY_BLOCKED"
    with sqlite3.connect(os.environ["WORKIVA_APPROVAL_DB"]) as db:
        assert db.execute("SELECT state FROM claims").fetchone() == ("indeterminate",)


@pytest.mark.asyncio
async def test_reviewer_cli_signs_only_displayed_confirmed_plan(review, tmp_path):
    proposal = await propose()
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({"plan": proposal}))
    private = tmp_path / "reviewer.key"
    private.write_text(review.private_bytes_raw().hex())
    output = tmp_path / "signed.json"
    command = [
        sys.executable,
        "-m",
        "workiva_mcp.reviewer",
        "approve",
        str(plan_file),
        "--private-key",
        str(private),
        "--output",
        str(output),
    ]
    canceled = subprocess.run(command, input="yes\n", text=True, capture_output=True)
    assert canceled.returncode == 1
    assert not output.exists()
    digest = plan_digest(CorrectionPlan.model_validate(proposal))
    approved = subprocess.run(command, input=f"APPROVE {digest}\n", text=True, capture_output=True)
    assert approved.returncode == 0, approved.stderr
    assert '"new_value": "4195000"' in approved.stdout
    envelope = json.loads(output.read_text())
    assert envelope["plan"] == proposal
    assert (await execute(envelope))["state"] == "verified"
    assert output.stat().st_mode & 0o777 == 0o600


def test_keygen_refuses_overwrite_and_protects_private_file(tmp_path):
    private, public = tmp_path / "key", tmp_path / "pub"
    command = [
        sys.executable,
        "-m",
        "workiva_mcp.reviewer",
        "keygen",
        "--private-key",
        str(private),
        "--public-key",
        str(public),
    ]
    first = subprocess.run(command, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    saved = public.read_text()
    assert private.stat().st_mode & 0o777 == 0o600
    second = subprocess.run(command, text=True, capture_output=True)
    assert second.returncode == 1
    assert public.read_text() == saved
