from __future__ import annotations

import json

import pytest

from workiva_mcp import compact_tools
from workiva_mcp.compact_tools import ToolDefinition
from workiva_mcp.receipt_store import read_receipt
from workiva_mcp.tool_contracts import ToolContract


@pytest.mark.asyncio
async def test_compact_write_returns_redacted_applied_unverified_receipt(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKIVA_MCP_RECEIPT_DIR", str(tmp_path))

    def fake_write(value: str, api_key: str) -> dict[str, object]:
        return {"ok": True, "value": value, "api_key": api_key}

    contract = ToolContract(effect="write", confirmation="write", proof="readback")
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
    assert receipt["proof"] == "pending_readback"
    assert receipt["targets"]["api_key"] == "[REDACTED]"
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