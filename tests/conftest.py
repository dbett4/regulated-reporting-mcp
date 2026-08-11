from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_artifact_dirs(tmp_path, monkeypatch):
    """Keep receipts/results out of the real ~/.workiva_mcp during tests."""
    monkeypatch.setenv("WORKIVA_MCP_RECEIPT_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("WORKIVA_MCP_RESULT_DIR", str(tmp_path / "results"))
    monkeypatch.delenv("WORKIVA_MCP_MOCK", raising=False)
    monkeypatch.delenv("WORKIVA_MCP_MOCK_429", raising=False)
    monkeypatch.delenv("WORKIVA_MCP_MOCK_POLL_TICKS", raising=False)
