"""Read-only reporting tie-outs. Rules are mappings, not accounting opinions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from workiva_mcp.http_client import WorkivaAPIError, api_request
from workiva_mcp.reporting_math import SCALES, number, signed_sum
from workiva_mcp.server import mcp
from workiva_mcp.tools.cells import _extract_values_payload

RULE_VERSION = "reporting.tie_out.v1"


class CellReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spreadsheet_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    sheet_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    cell: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")
    scale: Literal["units", "thousands", "millions"] = "units"


class SupportingCell(CellReference):
    sign: Literal["add", "subtract"] = "add"


class TieOutRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    reported: CellReference
    supporting: list[SupportingCell] = Field(min_length=1, max_length=50)
    unit: str = Field(default="USD", min_length=1, max_length=30)
    decimal_places: int = Field(default=2, ge=0, le=6, strict=True)
    tolerance: str = Field(default="0.00", pattern=r"^\d{1,18}(\.\d{1,6})?$")


@mcp.tool()
async def workiva_check_tie_outs(rules: list[TieOutRule]) -> str:
    """Run reporting tie-outs: read cells and check reported = signed supporting sum.

    Supports totals, rollforwards and cross-sheet/workbook comparisons. All
    references in a rule must represent the same currency/unit and period;
    the caller owns those accounting mappings. Scale each value to base units,
    sum without rounding, then round both sides HALF_UP to decimal_places.
    Pass when abs(reported - expected) <= tolerance (in base units).

    Returns source evidence and passed/failed/not_evaluated for every rule.
    Missing data is not zero. Calculated formula values may be checked, but
    formula text and formula correctness are not verified. Reads are cached
    per call, not an atomic upstream snapshot. Never writes or proposes fixes.
    Reports contain financial values; treat stored result artifacts accordingly.
    """
    rules = TypeAdapter(list[TieOutRule]).validate_python(rules)
    if not 1 <= len(rules) <= 100:
        raise ValueError("Provide 1 to 100 tie-out rules")
    if len({rule.id for rule in rules}) != len(rules):
        raise ValueError("Rule IDs must be unique")

    cache: dict[tuple[str, str, str], dict[str, Any]] = {}
    findings = []
    for rule in rules:
        evidence = []
        with localcontext() as context:
            context.prec = 80
            for ref in [rule.reported, *rule.supporting]:
                key = (ref.spreadsheet_id, ref.sheet_id, ref.cell)
                if key not in cache:
                    observation: dict[str, Any] = {
                        "read_at": datetime.now(timezone.utc).isoformat()
                    }
                    path = f"/spreadsheets/{ref.spreadsheet_id}/sheets/{ref.sheet_id}/values/{ref.cell}"
                    try:
                        status, _, body = api_request(
                            path, method="GET", tool_name="workiva_check_tie_outs"
                        )
                        if status != 200:
                            observation["error"] = f"read returned HTTP {status}"
                        else:
                            rows = _extract_values_payload(body)
                            if len(rows) != 1 or not isinstance(rows[0], list) or len(rows[0]) != 1:
                                observation["error"] = "expected exactly one cell"
                            else:
                                observation["value"] = rows[0][0]
                    except (WorkivaAPIError, OSError, TimeoutError):
                        observation["error"] = "source read unavailable"
                    cache[key] = observation
                item = {"source": ref.model_dump(), **cache[key]}
                if "error" not in item:
                    try:
                        item["normalized_value"] = str(number(item["value"]) * SCALES[ref.scale])
                    except ValueError as exc:
                        item["error"] = str(exc)
                evidence.append(item)

            finding = {
                "rule_id": rule.id,
                "rule_version": RULE_VERSION,
                "rule": rule.model_dump(),
                "review_status": "unreviewed",
                "evidence": evidence,
            }
            if any("error" in item for item in evidence):
                finding.update(
                    status="not_evaluated", reason="One or more source cells could not be evaluated"
                )
            else:
                reported, expected, difference = signed_sum(
                    evidence[0]["normalized_value"],
                    (
                        (item["normalized_value"], ref.sign)
                        for ref, item in zip(rule.supporting, evidence[1:], strict=True)
                    ),
                    rule.decimal_places,
                )
                finding.update(
                    status="passed" if abs(difference) <= Decimal(rule.tolerance) else "failed",
                    reported=str(reported),
                    expected=str(expected),
                    difference=str(difference),
                )
            findings.append(finding)

    return json.dumps(
        {
            "schema": RULE_VERSION,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "consistency": "Non-atomic reads; each unique cell is read once per call",
            "summary": {
                state: sum(item["status"] == state for item in findings)
                for state in ("passed", "failed", "not_evaluated")
            },
            "findings": findings,
        }
    )
