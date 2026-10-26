"""Read-only reviewer packet export from current tie-outs and selected journal attempts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter

from workiva_mcp._io import atomic_create_text
from workiva_mcp.approvals import read_attempt, retained_plan
from workiva_mcp.auth import mock_mode
from workiva_mcp.config import get_region


async def collect_packet(
    rules: list[dict], corrections: dict[str, list[str]] | None = None
) -> dict:
    # Initialize the registry before importing its tool modules.
    from workiva_mcp.compact_tools import call_tool
    from workiva_mcp.result_store import read_result
    from workiva_mcp.tools.reporting import RULE_VERSION, TieOutRule

    validated = TypeAdapter(list[TieOutRule]).validate_python(rules)
    if not 1 <= len(validated) <= 100 or len({r.id for r in validated}) != len(validated):
        raise ValueError("Provide 1 to 100 rules with unique IDs")
    by_id = {r.id: r for r in validated}
    corrections = corrections or {}
    if sum(map(len, corrections.values())) > 100:
        raise ValueError("Provide at most 100 correction links")
    started = datetime.now(timezone.utc).isoformat()
    selected = []
    # Reject wrong mappings and corrupt/cross-deployment plans before network reads.
    for rule_id, plan_ids in corrections.items():
        if rule_id not in by_id or len(set(plan_ids)) != len(plan_ids):
            raise ValueError("Correction links require known rules and unique plan IDs per rule")
        rule = by_id[rule_id]
        sources = [
            s.model_dump(include={"spreadsheet_id", "sheet_id", "cell"})
            for s in [rule.reported, *rule.supporting]
        ]
        for plan_id in plan_ids:
            attempt = read_attempt(plan_id)
            plan = retained_plan(attempt)
            if plan is None:
                raise ValueError("Legacy claim has no retained plan; cannot validate its rule link")
            if plan.target.source.model_dump() not in sources:
                raise ValueError("Correction target is not a source in its linked rule")
            selected.append((rule_id, attempt, plan))

    async def read_tool(name, arguments):
        result = json.loads(await call_tool(name, arguments, store_result_artifact=True))
        if not result.get("stored"):
            raise ValueError(f"Could not collect {name}; no complete result artifact")
        full = json.loads(read_result(result["result_id"]))
        if "error" in full:
            raise ValueError(f"Could not collect {name}; inspect the source separately")
        return full

    report = await read_tool(
        "workiva_check_tie_outs", {"rules": [r.model_dump() for r in validated]}
    )
    if report.get("schema") != RULE_VERSION or [f["rule_id"] for f in report["findings"]] != list(
        by_id
    ):
        raise ValueError("Incomplete or incompatible tie-out report")
    attempts = []
    for rule_id, attempt, plan in selected:
        recovery = await read_tool("workiva_inspect_correction", {"plan_id": plan.plan_id})
        if recovery.get("plan_digest") != attempt["digest"]:
            raise ValueError("Correction journal changed during collection; collect a new packet")
        attempts.append(
            {
                "rule_id": rule_id,
                "plan": plan.model_dump(),
                "plan_digest": attempt["digest"],
                "approval_evidence": "Local consumed-claim record; reviewer signature is not retained or independently verified by this export",
                "journal_at_collection": {
                    key: attempt.get(key)
                    for key in ("state", "phase", "claimed_at", "updated_at", "operation_location")
                },
                "recovery_observation": recovery,
            }
        )
    evidence = {"tie_out_report": report, "correction_attempts": attempts}
    digest = hashlib.sha256(
        json.dumps(
            evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode()
    ).hexdigest()
    return {
        "schema": "reporting.evidence_packet.v1",
        "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "transport": "mock" if mock_mode() else "live",
        "region": get_region(),
        "review_status": "unreviewed",
        "scope": "Only supplied rules and explicitly selected correction attempts; not a complete workspace history",
        "warnings": [
            "Contains sensitive financial values and source identifiers; not a redacted receipt.",
            "Sequential, non-atomic observations may differ in time; original execution states are never upgraded.",
            "Current matching values do not prove a correction caused them or resolve a finding.",
            "No approval, upstream write, mutation retry, rollback, or journal update is performed by this export.",
            "The evidence hash detects changes only against a trusted copy; this packet is not signed or audit certification.",
        ],
        "evidence_sha256": digest,
        "evidence": evidence,
    }


def render_markdown(packet: dict) -> str:
    # All source-controlled prose/values stay in JSON code blocks. Escaping
    # backticks prevents malicious cell text from closing a Markdown fence.
    def block(value):
        return (
            "```json\n"
            + json.dumps(value, indent=2, ensure_ascii=True).replace("`", "\\u0060")
            + "\n```\n"
        )

    report = packet["evidence"]["tie_out_report"]
    return (
        "# Reporting evidence packet\n\n"
        "**Unreviewed. This export is not approval or a resolution decision.**\n\n"
        "## Collection scope and limitations\n\n"
        + block({k: v for k, v in packet.items() if k != "evidence"})
        + "\n## Current tie-out summary\n\n"
        + block(report["summary"])
        + "\n## Findings to review\n\n"
        + block(
            [
                {
                    "rule_id": f["rule_id"],
                    "description": f["rule"]["description"],
                    "status": f["status"],
                    "unit": f["rule"]["unit"],
                    "reported": f.get("reported"),
                    "expected": f.get("expected"),
                    "difference": f.get("difference"),
                    "reason": f.get("reason"),
                }
                for f in report["findings"]
            ]
        )
        + "\n## Full source and correction evidence\n\n"
        + "Execution records and later recovery observations remain separate below.\n\n"
        + block(packet["evidence"])
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rules", type=Path, help="JSON array of tie-out rules")
    parser.add_argument("--correction", action="append", default=[], metavar="RULE_ID=PLAN_ID")
    parser.add_argument(
        "--output", type=Path, required=True, help="New export file; never overwritten"
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    try:
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("Refusing to overwrite an existing export")
        links = {}
        for link in args.correction:
            rule_id, separator, plan_id = link.rpartition("=")
            if not separator or not rule_id or not plan_id:
                raise ValueError("Correction links must be RULE_ID=PLAN_ID")
            links.setdefault(rule_id, []).append(plan_id)
        packet = asyncio.run(collect_packet(json.loads(args.rules.read_text()), links))
        text = (
            render_markdown(packet)
            if args.format == "markdown"
            else json.dumps(packet, indent=2) + "\n"
        )
        atomic_create_text(args.output, text)
        print("PACKET_EXPORTED review_status=unreviewed")
    except (ValueError, OSError, sqlite3.Error) as exc:
        # Input validation may contain financial values; do not echo it to logs.
        parser.exit(
            1,
            f"Packet export failed ({type(exc).__name__}); check inputs and source availability.\n",
        )


if __name__ == "__main__":
    main()
