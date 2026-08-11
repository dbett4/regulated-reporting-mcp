import json

import pytest

from workiva_mcp import compact_tools, result_store
from workiva_mcp.compact_tools import ToolDefinition, classify_tool
from workiva_mcp.tool_contracts import ToolContract

READ_CONTRACT = ToolContract(effect="read", confirmation="none", proof="none")


def test_compact_catalog_discovers_full_catalog_tools():
    definitions = compact_tools.get_tool_definitions()

    assert len(definitions) >= 100
    assert "workiva_read_cells" in definitions
    assert "workiva_format_cells" in definitions
    assert definitions["workiva_read_cells"].requires_confirmation is False
    assert definitions["workiva_format_cells"].requires_confirmation is True


def test_search_tools_supports_progressive_detail():
    result = json.loads(compact_tools.search_tools("read cells", detail="summary", limit=5))

    assert result["returned"] <= 5
    assert any(tool["name"] == "workiva_read_cells" for tool in result["tools"])
    assert all("parameters" not in tool for tool in result["tools"])

    schema = json.loads(compact_tools.search_tools("read cells", detail="schema", limit=1))
    assert schema["tools"][0]["parameters"]


def test_catalog_stats_quantifies_tool_count_reduction():
    stats = compact_tools.catalog_stats()

    assert stats["full_catalog_tools"] >= 100
    assert stats["compact_catalog_tools"] == 3
    assert stats["tool_count_reduction_pct"] >= 97.0


@pytest.mark.asyncio
async def test_call_tool_blocks_mutating_tools_without_confirmation(monkeypatch):
    monkeypatch.setattr(
        compact_tools,
        "_TOOL_CACHE",
        {
            "mutating_tool": ToolDefinition(
                name="mutating_tool",
                module_name="tests.fake",
                family="fake",
                description="Write something.",
                parameters=[],
                requires_confirmation=True,
            )
        },
    )

    result = json.loads(await compact_tools.call_tool("mutating_tool", {}))

    assert result["policy_blocked"] is True
    assert "lacks a reviewed execution contract" in result["error"]


@pytest.mark.asyncio
async def test_call_tool_dispatches_registered_tool(monkeypatch):
    async def fake_tool(value: str) -> dict:
        return {"value": value}

    monkeypatch.setattr(
        compact_tools,
        "_TOOL_CACHE",
        {
            "fake_tool": ToolDefinition(
                name="fake_tool",
                module_name="tests.fake",
                family="fake",
                description="Fake test tool.",
                parameters=[{"name": "value", "required": True, "default": None, "annotation": "str"}],
                requires_confirmation=False,
                contract=READ_CONTRACT,
            )
        },
    )
    monkeypatch.setattr(compact_tools, "_load_callable", lambda definition: fake_tool)

    result = json.loads(await compact_tools.call_tool("fake_tool", {"value": "ok"}))

    assert result == {"value": "ok"}


@pytest.mark.asyncio
async def test_call_tool_stores_large_results(monkeypatch, tmp_path):
    async def fake_tool() -> str:
        return "x" * 2000

    monkeypatch.setenv("WORKIVA_MCP_RESULT_DIR", str(tmp_path))
    monkeypatch.setattr(
        compact_tools,
        "_TOOL_CACHE",
        {
            "fake_tool": ToolDefinition(
                name="fake_tool",
                module_name="tests.fake",
                family="fake",
                description="Fake test tool.",
                parameters=[],
                requires_confirmation=False,
                contract=READ_CONTRACT,
            )
        },
    )
    monkeypatch.setattr(compact_tools, "_load_callable", lambda definition: fake_tool)

    result = json.loads(
        await compact_tools.call_tool(
            "fake_tool",
            {},
            max_chars=500,
            store_over_chars=100,
        )
    )

    assert result["stored"] is True
    assert result["uri"].startswith("workiva-result://")
    assert (tmp_path / f"{result['result_id']}.txt").read_text(encoding="utf-8") == "x" * 2000


@pytest.mark.asyncio
async def test_call_tool_stores_any_result_that_would_be_truncated(monkeypatch, tmp_path):
    async def fake_tool() -> str:
        return "x" * 700

    monkeypatch.setenv("WORKIVA_MCP_RESULT_DIR", str(tmp_path))
    monkeypatch.setattr(
        compact_tools,
        "_TOOL_CACHE",
        {
            "fake_tool": ToolDefinition(
                name="fake_tool",
                module_name="tests.fake",
                family="fake",
                description="Fake test tool.",
                parameters=[],
                requires_confirmation=False,
                contract=READ_CONTRACT,
            )
        },
    )
    monkeypatch.setattr(compact_tools, "_load_callable", lambda definition: fake_tool)

    result = json.loads(
        await compact_tools.call_tool(
            "fake_tool",
            {},
            max_chars=500,
            store_over_chars=10000,
        )
    )

    assert result["stored"] is True
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_batch_call_stores_step_results(monkeypatch, tmp_path):
    async def fake_tool(value: str) -> dict:
        return {"value": value}

    monkeypatch.setenv("WORKIVA_MCP_RESULT_DIR", str(tmp_path))
    monkeypatch.setattr(
        compact_tools,
        "_TOOL_CACHE",
        {
            "fake_tool": ToolDefinition(
                name="fake_tool",
                module_name="tests.fake",
                family="fake",
                description="Fake test tool.",
                parameters=[{"name": "value", "required": True, "default": None, "annotation": "str"}],
                requires_confirmation=False,
                contract=READ_CONTRACT,
            )
        },
    )
    monkeypatch.setattr(compact_tools, "_load_callable", lambda definition: fake_tool)

    result = json.loads(await compact_tools.batch_call([
        {"name": "fake_tool", "arguments": {"value": "one"}},
        {"name": "fake_tool", "arguments": {"value": "two"}},
    ]))

    assert result["count"] == 2
    assert result["results"][0]["result"]["stored"] is True
    assert result["results"][1]["result"]["stored"] is True


# ── Catalog is a faithful mirror of the full server (no drift) ───────────────


def test_catalog_matches_server_module_tools():
    """Discovery must equal the full server's registry restricted to the modules
    server.py imports — the exact catalog the default server exposes to clients."""
    import workiva_mcp.server as full_server

    allowed = {f"workiva_mcp.tools.{m}" for m in compact_tools._registered_module_names()}
    expected = sorted(
        tool.name
        for tool in full_server.mcp._tool_manager.list_tools()
        if getattr(getattr(tool, "fn", None), "__module__", "") in allowed
    )
    discovered = sorted(compact_tools._discover_from_registry())
    assert discovered == expected
    assert "workiva_read_cells" in discovered
    assert "workiva_write_verified" in discovered  # legit cells.py tool stays


def test_registry_pollution_does_not_leak_into_catalog():
    """Durability guard: FastMCP's registry is process-global mutable state.

    Anything that registers a tool on the shared singleton from a module
    server.py does not import (a stray import, a test) must stay out of the
    discovered catalog, because discovery is restricted to server.py's import
    list.
    """
    import workiva_mcp.server as full_server

    @full_server.mcp.tool(name="test_pollution_tool")
    def _pollution_tool() -> str:  # pragma: no cover - never dispatched
        return "polluted"

    try:
        discovered = compact_tools._discover_from_registry()
        allowed = {f"workiva_mcp.tools.{m}" for m in compact_tools._registered_module_names()}

        assert "test_pollution_tool" not in discovered
        assert all(definition.module_name in allowed for definition in discovered.values())
    finally:
        full_server.mcp._tool_manager._tools.pop("test_pollution_tool", None)


def test_ast_fallback_matches_registry():
    """The import-free AST fallback yields the same catalog and the same risk
    gating as the registry path — no second source of truth to drift."""
    registry_defs = compact_tools._discover_from_registry()
    ast_defs = compact_tools._discover_from_ast()

    assert sorted(ast_defs) == sorted(registry_defs)
    assert all(
        ast_defs[name].requires_confirmation == registry_defs[name].requires_confirmation
        for name in ast_defs
    )


def test_discovery_source_is_registry(monkeypatch):
    monkeypatch.setattr(compact_tools, "_TOOL_CACHE", None)
    monkeypatch.setattr(compact_tools, "_DISCOVERY_SOURCE", None)
    compact_tools.get_tool_definitions()
    assert compact_tools.discovery_source() == "registry"


def test_registered_module_names_track_server_imports():
    names = compact_tools._registered_module_names()
    assert "cells" in names and "wdata" in names and "linking" in names
    assert "pm" not in names  # engagement/PM layer is not part of this server


# ── Risk classification ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected_risk,expected_confirm",
    [
        ("workiva_read_cells", "read", False),
        ("workiva_get_sheet", "read", False),
        ("workiva_describe_workbook", "read", False),
        ("workiva_export_document", "read", False),  # export OUT is read-only
        ("workiva_wdata_list_tables", "read", False),
        ("workiva_format_cells", "write", True),
        ("workiva_update_values", "write", True),
        ("workiva_import_file", "write", True),  # import writes state
        ("workiva_wdata_create_table", "write", True),
        ("workiva_delete_sheet", "destructive", True),
        ("workiva_wdata_delete_table", "destructive", True),
        ("workiva_send_notification", "external", True),
    ],
)
def test_classify_tool_levels(name, expected_risk, expected_confirm):
    risk = classify_tool(name)
    assert risk.level == expected_risk
    assert risk.requires_confirmation is expected_confirm
    assert risk.reason


def test_leading_verb_not_fooled_by_incidental_safe_token():
    """A write tool that merely contains a read word must still be gated."""
    risk = classify_tool("workiva_purge_then_list_cache")
    assert risk.level == "destructive"
    assert risk.requires_confirmation is True


def test_export_to_destination_is_gated():
    assert classify_tool("workiva_wdata_export_to_spreadsheet").requires_confirmation is True
    assert classify_tool("workiva_export_spreadsheet").requires_confirmation is False


def test_read_tools_are_never_gated_and_writes_always_are():
    for definition in compact_tools.get_tool_definitions().values():
        if definition.risk == "read":
            assert definition.requires_confirmation is False
        else:
            assert definition.requires_confirmation is True


def test_search_surfaces_risk_and_confirm_note():
    result = json.loads(compact_tools.search_tools("delete sheet", detail="summary", limit=5))
    deletes = [tool for tool in result["tools"] if tool["risk"] == "destructive"]
    assert deletes
    assert all(tool["requires_confirmation"] for tool in deletes)
    assert all(tool.get("confirm_note") for tool in deletes)


# ── Benchmark proof ──────────────────────────────────────────────────────────


def test_catalog_benchmark_reports_context_reduction():
    bench = compact_tools.catalog_benchmark()
    assert bench["compact_tools"] == 3
    assert bench["full_tools"] >= 100
    assert bench["full_listing_bytes"] > bench["compact_listing_bytes"]
    assert bench["listing_byte_reduction_pct"] >= 90.0


def test_catalog_stats_reports_discovery_source_and_risk():
    stats = compact_tools.catalog_stats()
    assert stats["discovery_source"] in {"registry", "ast"}
    assert set(stats["risk_breakdown"]) <= {"read", "write", "destructive", "external"}
    assert sum(stats["risk_breakdown"].values()) == stats["full_catalog_tools"]


# ── Result artifact ergonomics ───────────────────────────────────────────────


def test_list_results_and_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKIVA_MCP_RESULT_DIR", str(tmp_path))
    first = result_store.store_result("tool_a", "alpha", {"arguments": {"x": 1}})
    second = result_store.store_result("tool_b", "beta" * 50, {"arguments": {"y": 2}})

    listed = result_store.list_results(limit=10)
    assert {item["result_id"] for item in listed} == {first["result_id"], second["result_id"]}
    assert listed[0]["result_id"] == second["result_id"]  # newest first

    assert len(result_store.list_results(tool="tool_a")) == 1

    meta = result_store.read_result_metadata(second["result_id"])
    assert meta["tool"] == "tool_b"
    assert meta["bytes"] == len(("beta" * 50).encode("utf-8"))


def test_read_result_rejects_path_traversal():
    for bad in ["../etc/passwd", "nested/id", "..\\windows"]:
        with pytest.raises(ValueError):
            result_store.read_result(bad)
        with pytest.raises(ValueError):
            result_store.read_result_metadata(bad)


def test_prune_results_is_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKIVA_MCP_RESULT_DIR", str(tmp_path))
    for i in range(3):
        result_store.store_result(f"tool_{i}", f"payload-{i}")

    assert result_store.prune_results() == []  # no bound -> no deletion
    assert len(result_store.list_results(limit=10)) == 3

    removed = result_store.prune_results(keep_latest=1)
    assert len(removed) == 2
    remaining = result_store.list_results(limit=10)
    assert len(remaining) == 1
