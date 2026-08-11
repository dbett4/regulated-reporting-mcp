import json

import pytest

from workiva_mcp import compact_tools
from workiva_mcp.compact_tools import ToolDefinition
from workiva_mcp.tool_contracts import ToolContract, manifest_tool_names


def test_manifest_covers_exactly_the_registered_tool_catalog():
    discovered = compact_tools.get_tool_definitions()
    assert manifest_tool_names() == set(discovered)
    assert all(definition.contract is not None for definition in discovered.values())


def test_manifest_contracts_drive_discovery_not_name_inference(monkeypatch):
    contract = ToolContract(effect="read", confirmation="none", proof="none")
    monkeypatch.setattr(compact_tools, "get_contract", lambda name: contract if name == "workiva_rename_unusual" else None)
    monkeypatch.setattr(compact_tools, "_registered_module_names", lambda: ("cells",))
    monkeypatch.setattr(
        compact_tools,
        "_decorated_functions",
        lambda path: [{"name": "workiva_rename_unusual", "docstring": "Test.", "parameters": []}],
    )
    definitions = compact_tools._discover_from_ast()
    definition = definitions["workiva_rename_unusual"]
    assert definition.risk == "read"
    assert definition.requires_confirmation is False
    assert definition.contract == contract


@pytest.mark.asyncio
async def test_missing_contract_is_fail_closed(monkeypatch):
    async def fake_tool():
        return {"unexpected": "execution"}

    monkeypatch.setattr(
        compact_tools,
        "_TOOL_CACHE",
        {
            "unclassified": ToolDefinition(
                name="unclassified",
                module_name="tests.fake",
                family="fake",
                description="Unclassified future tool.",
                parameters=[],
                requires_confirmation=True,
            )
        },
    )
    monkeypatch.setattr(compact_tools, "_load_callable", lambda definition: fake_tool)

    result = json.loads(await compact_tools.call_tool("unclassified", {}, confirm_write=True))
    assert result["policy_blocked"] is True
    assert "lacks a reviewed execution contract" in result["error"]


@pytest.mark.asyncio
async def test_confirmation_classes_are_not_interchangeable(monkeypatch):
    async def fake_tool():
        return {"ok": True}

    destructive = ToolContract(effect="destructive", confirmation="destructive", proof="receipt")
    external = ToolContract(effect="external", confirmation="external", proof="receipt")
    monkeypatch.setattr(
        compact_tools,
        "_TOOL_CACHE",
        {
            "destroy": ToolDefinition("destroy", "tests.fake", "fake", "", [], True, contract=destructive),
            "send": ToolDefinition("send", "tests.fake", "fake", "", [], True, contract=external),
        },
    )
    monkeypatch.setattr(compact_tools, "_load_callable", lambda definition: fake_tool)

    assert json.loads(await compact_tools.call_tool("destroy", {}, confirm_write=True))["confirmation"] == "destructive"
    assert json.loads(await compact_tools.call_tool("send", {}, confirm_write=True))["confirmation"] == "external"
    destroy_result = json.loads(await compact_tools.call_tool("destroy", {}, confirm_destructive=True))
    send_result = json.loads(await compact_tools.call_tool("send", {}, confirm_external=True))
    for result in (destroy_result, send_result):
        assert result["ok"] is True
        assert result["state"] == "applied_unverified"
        assert result["receipt_uri"].startswith("workiva-receipt://")


@pytest.mark.asyncio
async def test_batches_block_destructive_and_external_steps(monkeypatch):
    destructive = ToolContract(effect="destructive", confirmation="destructive", proof="receipt")
    monkeypatch.setattr(
        compact_tools,
        "_TOOL_CACHE",
        {
            "destroy": ToolDefinition("destroy", "tests.fake", "fake", "", [], True, contract=destructive),
        },
    )

    result = json.loads(await compact_tools.batch_call([{"name": "destroy", "arguments": {}}], confirm_write=True))
    assert result["results"][0]["policy_blocked"] is True
    assert "plan-digest approval" in result["results"][0]["error"]


def test_manifest_distinguishes_receipts_from_readback():
    definitions = compact_tools.get_tool_definitions()

    assert definitions["workiva_write_cells"].contract.proof == "receipt"
    assert definitions["workiva_format_cells"].contract.proof == "receipt"
    assert definitions["workiva_write_verified"].contract.proof == "readback"
