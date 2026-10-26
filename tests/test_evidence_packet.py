"""Packet exports preserve evidence without inventing approval or resolution."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_corrections import execute, propose, signed, target
from tests.test_corrections import review as review
from workiva_mcp import approvals, compact_tools, mock_transport
from workiva_mcp.evidence_packet import collect_packet, render_markdown
from workiva_mcp.reporting_demo import demo_rules


@pytest.mark.asyncio
async def test_current_findings_full_evidence_and_hash(review):
    rules = demo_rules()
    # Exceed the dispatcher's preview limit: export must read the full artifact.
    rules = [{**rules[0], "id": f"assets-{i}"} for i in range(100)]
    packet = await collect_packet(rules)
    report = packet["evidence"]["tie_out_report"]
    assert len(report["findings"]) == 100
    assert report["findings"][-1]["rule_id"] == "assets-99"
    assert report["findings"][0]["evidence"][0]["source"]["cell"] == "B8"
    assert report["findings"][0]["expected"] == "62500000.00"
    evidence = json.dumps(
        packet["evidence"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    assert hashlib.sha256(evidence.encode()).hexdigest() == packet["evidence_sha256"]
    assert packet["review_status"] == "unreviewed"
    assert packet["transport"] == "mock"
    assert mock_transport.stats["writes"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [False, True])
async def test_correction_state_is_separate_from_matching_current_values(review, verified):
    plan = await propose()
    if verified:
        assert (await execute(signed(review, plan)))["state"] == "verified"
    else:
        approvals.claim_plan(approvals.CorrectionPlan.model_validate(plan))
        mock_transport._grids[(target()["spreadsheet_id"], target()["sheet_id"])][(3, 1)] = (
            "4195000"
        )
    database = Path(os.environ["WORKIVA_APPROVAL_DB"])
    before = database.read_bytes()
    writes = mock_transport.stats["writes"]
    packet = await collect_packet(demo_rules(), {"cash-tie-out": [plan["plan_id"]]})
    correction = packet["evidence"]["correction_attempts"][0]
    state = "verified" if verified else "indeterminate"
    assert correction["plan"] == plan
    assert correction["journal_at_collection"]["state"] == state
    assert correction["recovery_observation"]["execution_state"] == state
    assert correction["recovery_observation"]["evidence"][0]["classification"] == "matches_approved"
    assert correction["recovery_observation"]["retry_allowed"] is False
    assert "signature is not retained" in correction["approval_evidence"]
    assert packet["evidence"]["tie_out_report"]["findings"][1]["status"] == "passed"
    assert packet["review_status"] == "unreviewed"
    assert mock_transport.stats["writes"] == writes
    assert database.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem", ["unknown_rule", "wrong_source", "duplicate", "wrong_deployment", "legacy"]
)
async def test_invalid_links_fail_before_network_reads(review, monkeypatch, problem):
    plan = await propose()
    approvals.claim_plan(approvals.CorrectionPlan.model_validate(plan))
    links = {"cash-tie-out": [plan["plan_id"]]}
    if problem == "unknown_rule":
        links = {"not-a-rule": [plan["plan_id"]]}
    elif problem == "wrong_source":
        links = {"total-assets": [plan["plan_id"]]}
    elif problem == "duplicate":
        links["cash-tie-out"] *= 2
    elif problem == "wrong_deployment":
        monkeypatch.setenv("WORKIVA_APPROVAL_AUDIENCE", "other")
    else:
        with approvals._journal() as db, db:
            db.execute("UPDATE claims SET plan_json = NULL")
    requests = mock_transport.stats["requests"]
    with pytest.raises(ValueError):
        await collect_packet(demo_rules(), links)
    assert mock_transport.stats["requests"] == requests


@pytest.mark.asyncio
async def test_dispatch_failure_cannot_be_exported_as_success(review, monkeypatch):
    async def failed(*args, **kwargs):
        return '{"error": "private upstream payload"}'

    monkeypatch.setattr(compact_tools, "call_tool", failed)
    with pytest.raises(ValueError, match="no complete result artifact"):
        await collect_packet(demo_rules())


@pytest.mark.asyncio
async def test_markdown_keeps_untrusted_text_inside_roundtrippable_json(review):
    rules = demo_rules()
    rules[0]["description"] = '```\n<img src="https://example.com/track">\n\x1b[31m'
    packet = await collect_packet(rules)
    markdown = render_markdown(packet)
    blocks = re.findall(r"```json\n(.*?)\n```", markdown, re.DOTALL)
    assert len(blocks) == 4
    assert json.loads(blocks[-1]) == packet["evidence"]
    assert json.loads(blocks[2])[0]["description"] == rules[0]["description"]
    assert "\x1b" not in markdown
    assert "**Unreviewed." in markdown


def test_cli_export_permissions_no_overwrite_and_invalid_input(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKIVA_MCP_MOCK", "1")
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps(demo_rules()))
    output = tmp_path / "packet.json"
    command = [
        sys.executable,
        "-m",
        "workiva_mcp.evidence_packet",
        str(rules),
        "--output",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    packet = json.loads(output.read_text())
    assert packet["evidence"]["tie_out_report"]["summary"] == {
        "passed": 1,
        "failed": 2,
        "not_evaluated": 1,
    }
    assert output.stat().st_mode & 0o777 == 0o600
    before = output.read_bytes()
    assert subprocess.run(command, capture_output=True, timeout=20).returncode == 1
    assert output.read_bytes() == before
    bad = tmp_path / "invalid-output.json"
    rules.write_text('[{"private-invalid-field": "sensitive123"}]')
    command[-1] = str(bad)
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1 and not bad.exists()
    assert "sensitive123" not in result.stderr
