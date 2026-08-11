from __future__ import annotations

import pytest

from workiva_mcp import __main__ as entrypoint


def test_default_entrypoint_is_guarded_compact_server(monkeypatch):
    monkeypatch.delenv("WORKIVA_MCP_CATALOG_MODE", raising=False)
    monkeypatch.delenv("WORKIVA_MCP_ALLOW_UNGATED_FULL_CATALOG", raising=False)

    assert entrypoint._load_mcp().name == "workiva-mcp-compact"


def test_full_catalog_requires_explicit_unsafe_opt_in(monkeypatch):
    monkeypatch.setenv("WORKIVA_MCP_CATALOG_MODE", "full")
    monkeypatch.delenv("WORKIVA_MCP_ALLOW_UNGATED_FULL_CATALOG", raising=False)

    with pytest.raises(RuntimeError, match="bypasses compact-dispatch"):
        entrypoint._load_mcp()


def test_full_catalog_opt_in_is_available_for_isolated_trusted_clients(monkeypatch):
    monkeypatch.setenv("WORKIVA_MCP_CATALOG_MODE", "full")
    monkeypatch.setenv("WORKIVA_MCP_ALLOW_UNGATED_FULL_CATALOG", "1")

    assert entrypoint._load_mcp().name == "workiva-mcp"


def test_unknown_catalog_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("WORKIVA_MCP_CATALOG_MODE", "typo")

    with pytest.raises(ValueError, match="must be compact"):
        entrypoint._load_mcp()
