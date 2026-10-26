"""The evaluator must expose failure, not manufacture a favorable score."""

import copy
import json
import os
import subprocess
import sys

import pytest

from workiva_mcp import compact_tools, evaluation, mock_transport


def test_scorer_counts_abstentions_errors_false_alarms_and_unsafe_passes():
    pairs = [
        ("failed", "failed"),
        ("failed", "failed"),
        ("failed", "passed"),
        ("failed", "not_evaluated"),
        ("failed", "error"),
        ("passed", "failed"),
        ("not_evaluated", "failed"),
        ("not_evaluated", "passed"),
    ]
    rows = [
        {"expected": {"status": expected}, "observed": {"status": actual}}
        for expected, actual in pairs
    ]
    metrics = evaluation.score_cases(rows)
    assert metrics["cases"] == 8
    assert metrics["true_discrepancies_detected"] == 2
    assert metrics["false_alarms"] == 2
    assert metrics["missed_discrepancies"] == 3  # Includes the abstention and error.
    assert metrics["discrepancy_precision"] == 0.5
    assert metrics["discrepancy_recall"] == 0.4
    assert metrics["incorrect_passes"] == 2
    assert metrics["errors"] == 1
    assert metrics["exact_cases"] == 2


def test_correct_status_cannot_hide_wrong_amounts_or_invented_evidence():
    rows = [
        {
            "expected": {"status": "passed", "difference": "0.00"},
            "observed": {"status": "passed", "difference": "10.00"},
        },
        {
            "expected": {"status": "not_evaluated"},
            "observed": {"status": "not_evaluated", "expected": "0.00"},
        },
    ]
    metrics = evaluation.score_cases(rows)
    assert metrics["status_correct"] == 2
    assert metrics["exact_cases"] == 0
    assert metrics["discrepancy_precision"] is None
    assert metrics["discrepancy_recall"] is None
    assert evaluation.score_cases([])["exact_case_accuracy"] is None


@pytest.mark.asyncio
async def test_bundled_cases_use_real_dispatch_and_restore_mock_state(monkeypatch):
    monkeypatch.setenv("WORKIVA_MCP_MOCK", "0")
    before = copy.deepcopy(mock_transport._grids)
    stats = dict(mock_transport.stats)
    report = await evaluation.run_evaluation()
    assert report["evaluation_pass"] is True
    assert report["metrics"]["cases"] == report["metrics"]["exact_cases"] == 18
    assert report["metrics"]["confusion_matrix"] == {
        "passed": {"passed": 8, "failed": 0, "not_evaluated": 0, "error": 0},
        "failed": {"passed": 0, "failed": 5, "not_evaluated": 0, "error": 0},
        "not_evaluated": {"passed": 0, "failed": 0, "not_evaluated": 5, "error": 0},
    }
    assert report["metrics"]["discrepancy_precision"] == 1.0
    assert report["metrics"]["discrepancy_recall"] == 1.0
    assert report["upstream_writes"] == 0
    assert report["tool_calls"] == 18 and report["http_requests"] > 18
    assert len(report["provenance"]["corpus_sha256"]) == 64
    assert len(report["provenance"]["implementation_sha256"]) == 64
    assert mock_transport._grids == before and mock_transport.stats == stats
    assert os.environ["WORKIVA_MCP_MOCK"] == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["dropped", "duplicate", "wrong_rule", "wrong_amount", "exception", "write"]
)
async def test_runner_does_not_drop_or_ignore_bad_results(monkeypatch, fault):
    original = compact_tools.call_tool
    calls = 0

    async def faulty(*args, **kwargs):
        nonlocal calls
        calls += 1
        assert "expected" not in args[1]["rules"][0]  # Answers never enter dispatch arguments.
        result = json.loads(await original(*args, **kwargs))
        if calls == 1:
            if fault == "dropped":
                result["findings"] = []
            elif fault == "duplicate":
                result["findings"] *= 2
            elif fault == "wrong_rule":
                result["findings"][0]["rule_id"] = "other-case"
            elif fault == "wrong_amount":
                result["findings"][0]["expected"] = "0.00"
            elif fault == "exception":
                raise RuntimeError("simulated dispatcher failure")
            else:
                mock_transport.stats["writes"] += 1  # Simulate a transport mutation counter.
        return json.dumps(result)

    monkeypatch.setattr(compact_tools, "call_tool", faulty)
    report = await evaluation.run_evaluation()
    assert report["evaluation_pass"] is False
    assert report["metrics"]["cases"] == calls == 18
    if fault == "write":
        assert report["upstream_writes"] == 1
    else:
        assert report["metrics"]["exact_cases"] == 17
        assert report["metrics"]["errors"] == (0 if fault == "wrong_amount" else 1)


def test_standalone_json_report_and_failing_exit_code():
    success = subprocess.run(
        [sys.executable, "-m", "workiva_mcp.evaluation", "--json"],
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert success.returncode == 0, success.stderr
    assert json.loads(success.stdout)["evaluation_pass"] is True
    failed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from workiva_mcp import compact_tools, evaluation
async def broken(*args, **kwargs): return '{}'
compact_tools.call_tool = broken
sys.argv = ['evaluation', '--json']
evaluation.main()
""",
        ],
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert failed.returncode == 1, failed.stderr
    report = json.loads(failed.stdout)
    assert report["evaluation_pass"] is False
    assert report["metrics"]["errors"] == 18
    assert report["metrics"]["missed_discrepancies"] == 5
