from __future__ import annotations

import json

import pytest

from workiva_mcp import compact_tools
from workiva_mcp._io import atomic_create_text
from workiva_mcp.compact_tools import ToolDefinition
from workiva_mcp.receipt_store import read_receipt
from workiva_mcp.tool_contracts import ToolContract
from workiva_mcp.tools import cells


@pytest.mark.asyncio
async def test_compact_write_returns_redacted_applied_unverified_receipt(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKIVA_MCP_RECEIPT_DIR", str(tmp_path))

    def fake_write(value: str, api_key: str) -> dict[str, object]:
        return {"ok": True, "echo": value, "api_key": api_key}

    contract = ToolContract(effect="write", confirmation="write", proof="receipt")
    monkeypatch.setattr(
        compact_tools,
        "get_tool_definitions",
        lambda: {
            "fake_write": ToolDefinition(
                name="fake_write", module_name="tests", family="tests", description="", parameters=[],
                requires_confirmation=True, contract=contract, func=fake_write,
            )
        },
    )

    response = json.loads(await compact_tools.call_tool(
        "fake_write", {"value": "ok", "api_key": "should-not-persist"}, confirm_write=True,
    ))

    assert response["state"] == "applied_unverified"
    assert response["receipt_uri"].startswith("workiva-receipt://")
    receipt = read_receipt(response["receipt_uri"].removeprefix("workiva-receipt://"))
    assert receipt["state"] == "applied_unverified"
    assert receipt["proof"] == "execution_receipt"
    assert receipt["targets"]["api_key"] == "[REDACTED]"
    assert receipt["targets"]["value"] == "[REDACTED_PAYLOAD]"
    assert "should-not-persist" not in json.dumps(receipt)


@pytest.mark.asyncio
async def test_compact_read_does_not_emit_receipt(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKIVA_MCP_RECEIPT_DIR", str(tmp_path))
    contract = ToolContract(effect="read", confirmation="none", proof="none")
    monkeypatch.setattr(
        compact_tools,
        "get_tool_definitions",
        lambda: {
            "fake_read": ToolDefinition(
                name="fake_read", module_name="tests", family="tests", description="", parameters=[],
                requires_confirmation=False, contract=contract, func=lambda: {"ok": True},
            )
        },
    )

    assert json.loads(await compact_tools.call_tool("fake_read"))["ok"] is True
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.asyncio
async def test_compact_receipt_preserves_tool_readback_verification(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKIVA_MCP_RECEIPT_DIR", str(tmp_path))
    contract = ToolContract(effect="write", confirmation="write", proof="readback")
    monkeypatch.setattr(
        compact_tools,
        "get_tool_definitions",
        lambda: {
            "verified_write": ToolDefinition(
                name="verified_write", module_name="tests", family="tests", description="", parameters=[],
                requires_confirmation=True, contract=contract, func=lambda: {"status": "verified"},
            )
        },
    )

    response = json.loads(await compact_tools.call_tool("verified_write", {}, confirm_write=True))
    receipt = read_receipt(response["receipt_uri"].removeprefix("workiva-receipt://"))
    assert response["state"] == "verified"
    assert receipt["state"] == "verified"
    assert receipt["proof"] == "readback_verified"


@pytest.mark.asyncio
async def test_readback_contract_fails_closed_when_tool_returns_no_proof(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKIVA_MCP_RECEIPT_DIR", str(tmp_path))
    contract = ToolContract(effect="write", confirmation="write", proof="readback")
    monkeypatch.setattr(
        compact_tools,
        "get_tool_definitions",
        lambda: {
            "unverified_write": ToolDefinition(
                name="unverified_write", module_name="tests", family="tests", description="", parameters=[],
                requires_confirmation=True, contract=contract, func=lambda: {"ok": True},
            )
        },
    )

    response = json.loads(await compact_tools.call_tool(
        "unverified_write", {}, confirm_write=True,
    ))

    assert response["state"] == "indeterminate"
    assert response["proof_required"] == "readback"
    assert response["proof_satisfied"] is False
    assert "promised readback proof" in response["error"]
    receipt = read_receipt(response["receipt_uri"].removeprefix("workiva-receipt://"))
    assert receipt["state"] == "indeterminate"
    assert receipt["proof"] == "pending_readback"


@pytest.mark.asyncio
async def test_mutation_exception_emits_indeterminate_receipt(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKIVA_MCP_RECEIPT_DIR", str(tmp_path))
    contract = ToolContract(effect="write", confirmation="write", proof="receipt")

    def raises_after_dispatch(value: str) -> None:
        raise RuntimeError(f"upstream outcome unknown for {value}")

    monkeypatch.setattr(
        compact_tools,
        "get_tool_definitions",
        lambda: {
            "raising_write": ToolDefinition(
                name="raising_write", module_name="tests", family="tests", description="", parameters=[],
                requires_confirmation=True, contract=contract, func=raises_after_dispatch,
            )
        },
    )

    response = json.loads(await compact_tools.call_tool(
        "raising_write", {"value": "regulated-payload"}, confirm_write=True,
    ))

    assert response["state"] == "indeterminate"
    assert response["proof_satisfied"] is False
    receipt = read_receipt(response["receipt_uri"].removeprefix("workiva-receipt://"))
    assert receipt["state"] == "indeterminate"
    assert receipt["targets"]["value"] == "[REDACTED_PAYLOAD]"
    assert "regulated-payload" not in json.dumps(receipt)


def test_atomic_create_refuses_to_replace_existing_receipt(tmp_path):
    path = tmp_path / "receipt.json"
    atomic_create_text(path, "first")

    with pytest.raises(FileExistsError):
        atomic_create_text(path, "second")

    assert path.read_text(encoding="utf-8") == "first"


@pytest.mark.asyncio
async def test_receipt_redacts_unknown_scalar_payloads_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKIVA_MCP_RECEIPT_DIR", str(tmp_path))
    contract = ToolContract(effect="write", confirmation="write", proof="receipt")
    monkeypatch.setattr(
        compact_tools,
        "get_tool_definitions",
        lambda: {
            "payload_write": ToolDefinition(
                name="payload_write", module_name="tests", family="tests", description="", parameters=[],
                requires_confirmation=True, contract=contract, func=lambda **kwargs: {"ok": True},
            )
        },
    )

    response = json.loads(await compact_tools.call_tool(
        "payload_write",
        {
            "query_id": "query-123",
            "rich_text_id": "rich-text-456",
            "content_id": "content-789",
            "sql": "SELECT secret_salary FROM private_payroll",
            "runtime_inputs": {"employee": "Ada Private"},
            "data": [{"insert": "confidential rich text", "row": 7}],
        },
        confirm_write=True,
    ))
    receipt = read_receipt(response["receipt_uri"].removeprefix("workiva-receipt://"))
    rendered = json.dumps(receipt)

    assert receipt["targets"]["query_id"] == "query-123"
    assert receipt["targets"]["rich_text_id"] == "rich-text-456"
    assert receipt["targets"]["content_id"] == "content-789"
    assert receipt["targets"]["data"][0]["row"] == 7
    assert receipt["targets"]["sql"] == "[REDACTED_PAYLOAD]"
    assert receipt["targets"]["runtime_inputs"]["employee"] == "[REDACTED_PAYLOAD]"
    assert receipt["targets"]["data"][0]["insert"] == "[REDACTED_PAYLOAD]"
    for secret in ("secret_salary", "private_payroll", "Ada Private", "confidential rich text"):
        assert secret not in rendered


@pytest.mark.asyncio
async def test_formula_write_is_indeterminate_through_public_compact_dispatch(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKIVA_MCP_RECEIPT_DIR", str(tmp_path))

    def fake_api_request(path, **kwargs):
        if kwargs.get("method") == "PUT":
            return 200, {}, {"ok": True}
        return 200, {}, {"data": [{"values": [["42"]]}]}

    monkeypatch.setattr(cells, "api_request", fake_api_request)
    contract = ToolContract(effect="write", confirmation="write", proof="readback")
    monkeypatch.setattr(
        compact_tools,
        "get_tool_definitions",
        lambda: {
            "workiva_write_verified": ToolDefinition(
                name="workiva_write_verified", module_name="workiva_mcp.tools.cells", family="cells",
                description="", parameters=[], requires_confirmation=True, contract=contract,
                func=cells.workiva_write_verified,
            )
        },
    )

    response = json.loads(await compact_tools.call_tool(
        "workiva_write_verified",
        {
            "spreadsheet_id": "ss-1",
            "sheet_id": "sheet-1",
            "cells": [{"row": 0, "col": 0, "value": "=SUM(A2:A3)"}],
        },
        confirm_write=True,
    ))

    assert response["status"] == "indeterminate"
    assert response["state"] == "indeterminate"
    assert response["proof_satisfied"] is False
    assert response["formula_cells_unverified"] == [{"a1": "A1", "row": 0, "col": 0}]
