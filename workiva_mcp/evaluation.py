"""Reproducible offline reporting evaluation; no LLM or live-workspace claims."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

STATES = ("passed", "failed", "not_evaluated")
AMOUNTS = ("reported", "expected", "difference")


def score_cases(rows: list[dict]) -> dict:
    """Score all attempts, including errors and abstentions on known discrepancies."""
    matrix = {expected: {actual: 0 for actual in (*STATES, "error")} for expected in STATES}
    exact = 0
    for row in rows:
        expected, observed = row["expected"], row["observed"]
        actual = observed.get("status", "error")
        if actual not in STATES:
            actual = "error"
        matrix[expected["status"]][actual] += 1
        correct = all(observed.get(key) == value for key, value in expected.items())
        if expected["status"] == "not_evaluated":
            correct = correct and not any(key in observed for key in AMOUNTS)
        exact += correct
    tp = matrix["failed"]["failed"]
    fp = sum(matrix[state]["failed"] for state in ("passed", "not_evaluated"))
    fn = sum(matrix["failed"][state] for state in ("passed", "not_evaluated", "error"))
    return {
        "cases": len(rows),
        "exact_cases": exact,
        "exact_case_accuracy": exact / len(rows) if rows else None,
        "status_correct": sum(matrix[state][state] for state in STATES),
        "true_discrepancies_detected": tp,
        "false_alarms": fp,
        "missed_discrepancies": fn,
        "discrepancy_precision": tp / (tp + fp) if tp + fp else None,
        "discrepancy_recall": tp / (tp + fn) if tp + fn else None,
        "incorrect_passes": matrix["failed"]["passed"] + matrix["not_evaluated"]["passed"],
        "errors": sum(matrix[state]["error"] for state in STATES),
        "confusion_matrix": matrix,
    }


def load_cases() -> tuple[dict, str]:
    content = Path(__file__).with_name("reporting_cases.json").read_bytes()
    corpus = json.loads(content)
    if corpus["schema"] != "reporting.evaluation_cases.v1" or not corpus["cases"]:
        raise ValueError("Expected a nonempty v1 evaluation corpus")
    ids = [case["id"] for case in corpus["cases"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Evaluation case IDs must be unique")
    for case in corpus["cases"]:
        expected = case["expected"]
        if expected["status"] not in STATES:
            raise ValueError("Invalid expected outcome")
        keys = {"status"} if expected["status"] == "not_evaluated" else {"status", *AMOUNTS}
        if set(expected) != keys:
            raise ValueError(
                "Each evaluated case requires independently specified numeric evidence"
            )
    return corpus, hashlib.sha256(content).hexdigest()


async def run_evaluation() -> dict:
    corpus, corpus_hash = load_cases()
    from workiva_mcp import mock_transport

    rows = []
    requests = writes = 0
    with (
        TemporaryDirectory(prefix="workiva-evaluation-") as directory,
        patch.dict(
            os.environ,
            {
                "WORKIVA_MCP_MOCK": "1",
                "WORKIVA_MCP_MOCK_429": "0",
                "WORKIVA_MCP_RESULT_DIR": str(Path(directory) / "results"),
                "WORKIVA_MCP_RECEIPT_DIR": str(Path(directory) / "receipts"),
            },
        ),
        patch.dict(mock_transport._grids),
        patch.dict(mock_transport._operations),
        patch.dict(mock_transport.stats),
        patch.object(mock_transport, "_429_armed", False),
    ):
        from workiva_mcp.compact_tools import call_tool
        from workiva_mcp.tools.reporting import RULE_VERSION

        spreadsheet = mock_transport.RIVERTON_SPREADSHEET_ID
        statement = "b2c3d4e5f60718293a4b5c6d7e8f90a1"
        support = "riverton-review-schedule"
        for case in corpus["cases"]:
            mock_transport.reset(rearm_429=False)
            # Cases get independent synthetic cells in two distinct sheets.
            # The dispatcher receives only source mappings, never expected answers.
            mock_transport._grids[(spreadsheet, statement)] = {(0, 0): case["reported"]["value"]}
            mock_transport._grids[(spreadsheet, support)] = {
                (row, 0): item["value"] for row, item in enumerate(case["supporting"])
            }
            rule = {
                "id": case["id"],
                "description": case["id"],
                "reported": {
                    "spreadsheet_id": spreadsheet,
                    "sheet_id": statement,
                    "cell": "A1",
                    "scale": case["reported"].get("scale", "units"),
                },
                "supporting": [
                    {
                        "spreadsheet_id": spreadsheet,
                        "sheet_id": support,
                        "cell": f"A{row + 1}",
                        "scale": item.get("scale", "units"),
                        "sign": item.get("sign", "add"),
                    }
                    for row, item in enumerate(case["supporting"])
                ],
                "decimal_places": case.get("decimal_places", 2),
                "tolerance": case.get("tolerance", "0.00"),
            }
            try:
                response = json.loads(
                    await call_tool(
                        "workiva_check_tie_outs",
                        {"rules": [rule]},
                        max_chars=100000,
                        store_over_chars=100000,
                    )
                )
                findings = response.get("findings", [])
                if len(findings) != 1 or findings[0].get("rule_id") != case["id"]:
                    raise ValueError("Missing, duplicate, or wrong-rule finding")
                finding = findings[0]
                observed = {key: finding[key] for key in ("status", *AMOUNTS) if key in finding}
                if observed.get("status") not in STATES:
                    raise ValueError("Unknown finding status")
            except Exception as exc:
                observed = {"status": "error", "error_type": type(exc).__name__}
            rows.append(
                {
                    "id": case["id"],
                    "category": case["category"],
                    "expected": case["expected"],
                    "observed": observed,
                }
            )
            requests += mock_transport.stats["requests"]
            writes += mock_transport.stats["writes"]

    metrics = score_cases(rows)
    root = Path(__file__).parent
    implementation = hashlib.sha256()
    for path in sorted([*root.rglob("*.py"), root / "tool_contract_manifest.json"]):
        implementation.update(
            path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes()
        )
    return {
        "schema": "reporting.evaluation_report.v1",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "corpus_schema": corpus["schema"],
            "corpus_sha256": corpus_hash,
            "implementation_sha256": implementation.hexdigest(),
            "python": platform.python_version(),
            "rule_version": RULE_VERSION,
            "transport": "FakeWorkiva",
            "entrypoint": "compact workiva_call_tool",
        },
        "scope": "Public synthetic cases with supplied mappings; no LLM, live API, held-out data, or productivity measurement",
        "metrics": metrics,
        "by_category": {
            category: score_cases([row for row in rows if row["category"] == category])
            for category in sorted({row["category"] for row in rows})
        },
        "tool_calls": len(rows),
        "http_requests": requests,
        "upstream_writes": writes,
        "evaluation_pass": bool(rows) and metrics["exact_cases"] == len(rows) and writes == 0,
        "results": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="Emit the full machine-readable report to stdout"
    )
    args = parser.parse_args()
    report = asyncio.run(run_evaluation())
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for row in report["results"]:
            marker = "OK" if score_cases([row])["exact_cases"] else "FAIL"
            print(
                f"{marker:4} {row['id']:36} expected={row['expected']['status']:13} observed={row['observed']['status']}"
            )
        metrics = report["metrics"]
        print(
            f"Exact cases: {metrics['exact_cases']}/{metrics['cases']}; "
            f"false alarms: {metrics['false_alarms']}; missed discrepancies: {metrics['missed_discrepancies']}; "
            f"incorrect passes: {metrics['incorrect_passes']}; writes: {report['upstream_writes']}"
        )
        print("EVALUATION_PASS" if report["evaluation_pass"] else "EVALUATION_FAIL")
    raise SystemExit(0 if report["evaluation_pass"] else 1)


if __name__ == "__main__":
    main()
