"""Single-cell corrections with externally signed approval and preflight reads."""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid

from pydantic import TypeAdapter

from workiva_mcp.approvals import (
    EXECUTE_TOOL,
    MAX_PLAN_SECONDS,
    CorrectionPlan,
    Snapshot,
    Source,
    check_fresh,
    claim_plan,
    deployment,
    list_attempts,
    plan_digest,
    read_attempt,
    record_outcome,
    record_progress,
    retained_plan,
    verify_approval,
)
from workiva_mcp.execution import ExecutionState, execute_mutation
from workiva_mcp.http_client import api_request, poll_operation
from workiva_mcp.reapi import WorkivaOperationError, operation_location_from_parts
from workiva_mcp.server import mcp
from workiva_mcp.tools.cells import _extract_value


def read_snapshot(source: Source) -> Snapshot:
    """Require explicit positioned raw and display values; never infer blanks."""
    row = int(re.search(r"[0-9]+$", source.cell).group()) - 1
    col = 0
    for letter in re.match(r"[A-Z]+", source.cell).group():
        col = col * 26 + ord(letter) - ord("A") + 1
    status, _, body = api_request(
        f"/spreadsheets/{source.spreadsheet_id}/sheets/{source.sheet_id}",
        tool_name="correction_snapshot",
    )
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("table"), dict):
        raise ValueError("Cannot resolve source table")
    table = body["table"].get("table") or body["table"].get("id")
    if not isinstance(table, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", table):
        raise ValueError("Cannot resolve source table")
    status, _, body = api_request(
        f"/content/tables/{table}/cells?startRow={row}&stopRow={row}&$maxcellsperpage=50000",
        tool_name="correction_snapshot",
    )
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ValueError("Cannot read source cell")
    matches = [
        cell
        for cell in body["data"]
        if isinstance(cell, dict)
        and cell.get("position", {}).get("row") == row
        and cell.get("position", {}).get("column") == col - 1
    ]
    if (
        len(matches) != 1
        or not isinstance(matches[0].get("rawValue"), str)
        or not isinstance(matches[0].get("value"), dict)
    ):
        raise ValueError("Source requires one explicit rawValue and typed display value")
    return Snapshot(
        source=source, raw_value=matches[0]["rawValue"], value=_extract_value(matches[0])
    )


@mcp.tool()
async def workiva_preview_correction(
    target: Source,
    new_value: str,
    reason: str,
    supporting: list[Source] | None = None,
) -> str:
    """Propose one literal numeric cell correction; no upstream writes.

    Snapshot target and optional supporting cells, binding raw and calculated
    values to a 15-minute exact plan. Formula targets are refused. Export the
    returned plan for a human to sign with the offline workiva-mcp-review CLI.
    No approval is created by this tool. Plans contain sensitive financial data.
    """
    target = Source.model_validate(target)
    sources = TypeAdapter(list[Source]).validate_python(supporting or [])
    now = int(time.time())
    # Validate the complete request before any source reads.
    draft = CorrectionPlan(
        plan_id=uuid.uuid4().hex,
        **deployment(),
        created_at=now,
        expires_at=now + MAX_PLAN_SECONDS,
        reason=reason,
        new_value=new_value,
        target=Snapshot(source=target, raw_value="", value=""),
        supporting=[Snapshot(source=s, raw_value="", value="") for s in sources],
    )
    document = draft.model_dump()
    document["target"] = read_snapshot(target).model_dump()
    document["supporting"] = [read_snapshot(source).model_dump() for source in sources]
    plan = CorrectionPlan.model_validate(document)
    check_fresh(plan)
    return json.dumps({"plan": plan.model_dump(), "plan_digest": plan_digest(plan)})


@mcp.tool()
async def workiva_execute_correction(plan: CorrectionPlan, signature: str) -> str:
    """Execute one exact reviewer-signed correction with fresh source checks.

    Requires the configured Ed25519 reviewer public key, deployment audience,
    and durable single-use journal. The caller cannot supply replacement values
    separately. Consumes approval before reading sources; stale/unavailable
    sources require a new proposal and approval. No retry after an uncertain outcome or rollback.
    Preflight is not upstream compare-and-swap: concurrent edits can still race.
    """
    plan = CorrectionPlan.model_validate(plan)
    response = {"plan_id": plan.plan_id, "plan_digest": plan_digest(plan)}
    try:
        verify_approval(plan, signature)
        claim_plan(plan)
    except (ValueError, OSError, sqlite3.Error) as exc:
        return json.dumps(
            {**response, "state": "blocked", "error": str(exc), "policy_blocked": True}
        )

    submitted = False

    def submit(*args, **kwargs):
        status, headers, body = api_request(*args, **kwargs)
        location = None
        if status == 202:
            try:
                location = operation_location_from_parts(headers, body, status_code=status)
            except WorkivaOperationError:
                pass  # execute_mutation reports accepted-without-location as indeterminate.
            response["operation_location"] = location
        # Persist the operation location before execute_mutation can start polling.
        record_progress(plan, "polling" if location else "response_received", location)
        return status, headers, body

    try:
        for expected in [*plan.supporting, plan.target]:
            if read_snapshot(expected.source) != expected:
                raise ValueError("Source changed since proposal; create and review a new plan")
        check_fresh(plan)  # Source reads may outlast the approval window.
        source = plan.target.source
        record_progress(plan, "submitting")
        submitted = True
        result = execute_mutation(
            submit,
            path=f"/spreadsheets/{source.spreadsheet_id}/sheets/{source.sheet_id}/values/{source.cell}",
            method="PUT",
            data={"values": [[plan.new_value]]},
            tool_name=EXECUTE_TOOL,
            label="approved correction",
            poll=poll_operation,
        )
        if result.state != ExecutionState.APPLIED:
            # Even a transport error can follow an applied write. Never offer a retry.
            response.update(
                state="indeterminate",
                error="Submission did not prove completion; inspect upstream state",
                operation_location=result.operation_location,
            )
        else:
            record_progress(plan, "readback")
            observed = read_snapshot(source)
            verified = observed.raw_value == plan.new_value and observed.value == plan.new_value
            response.update(state="verified" if verified else "verification_failed")
            if not verified:
                response["error"] = (
                    "Readback differs from the approved literal value; manual review required"
                )
    except Exception:
        # Do not leak upstream payloads/secrets through exception strings.
        response.update(
            state="indeterminate" if submitted else "blocked",
            error="Outcome unknown; inspect upstream state"
            if submitted
            else "Source is stale, unavailable, or approval expired; create and review a new plan",
        )
    try:
        record_outcome(plan, response["state"])
    except (OSError, sqlite3.Error):
        response["journal_warning"] = "Claim remains consumed; final journal update failed"
    return json.dumps(response)


@mcp.tool()
async def workiva_list_correction_attempts(limit: int = 25) -> str:
    """List recent durable correction claims, including interrupted attempts.

    Reads the local journal only; no upstream calls or journal changes. Returns
    metadata without financial values, newest claim first. Use
    workiva_inspect_correction with a plan_id to compare current source state.
    A nonterminal phase may mean a worker is still running, not that it crashed.
    """
    return json.dumps({"attempts": list_attempts(limit)})


@mcp.tool()
async def workiva_inspect_correction(plan_id: str) -> str:
    """Read-only recovery inspection of a consumed correction, even after expiry.

    Loads the durable plan by ID and compares fresh target/supporting cells.
    Does NOT poll operations, execute writes, release claims, change the journal,
    or upgrade the original execution state. Current matching values do not
    prove this attempt caused them. Stop/coordinate the original executor and
    resolve pending upstream operations before considering any new correction.
    Returns financial evidence; protect stored result artifacts accordingly.
    """
    attempt = read_attempt(plan_id)
    plan = retained_plan(attempt)
    result = {
        "plan_id": plan_id,
        "execution_state": attempt["state"],
        "execution_phase": attempt.get("phase", "legacy"),
        "execution_updated_at": attempt.get("updated_at"),
        "operation_location": attempt.get("operation_location"),
        "approval_consumed": True,
        "retry_allowed": False,
        "warning": "Current observations do not prove the original execution outcome. "
        "Coordinate the original worker and resolve any pending operation before new corrections.",
    }
    if plan is None:
        return json.dumps(
            {
                **result,
                "status": "recovery_unavailable",
                "reason": "Legacy claim has no saved plan; retrieve original evidence for manual review",
            }
        )

    evidence = []
    for index, expected in enumerate([plan.target, *plan.supporting]):
        item = {"role": "target" if index == 0 else "supporting", "before": expected.model_dump()}
        try:
            current = read_snapshot(expected.source)
            item["observed"] = current.model_dump()
            item["matches_before"] = current == expected
            if index == 0:
                item["matches_approved"] = (
                    current.raw_value == plan.new_value and current.value == plan.new_value
                )
                item["classification"] = (
                    "matches_before_and_approved"
                    if item["matches_before"] and item["matches_approved"]
                    else "matches_approved"
                    if item["matches_approved"]
                    else "matches_before"
                    if item["matches_before"]
                    else "diverged"
                )
            else:
                item["classification"] = "unchanged" if item["matches_before"] else "changed"
        except Exception:
            item.update(
                classification="unavailable", error="Source read unavailable; no conclusion drawn"
            )
        item["observed_at"] = int(time.time())
        evidence.append(item)
    result.update(
        status="observed",
        plan_digest=attempt["digest"],
        new_value=plan.new_value,
        evidence=evidence,
        completed_at=int(time.time()),
    )
    return json.dumps(result)
