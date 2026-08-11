"""End-to-end proof that mock mode drives the real tool code paths.

These tests flip WORKIVA_MCP_MOCK=1 and run actual tools against the
FakeWorkiva transport: no credentials, no network, no monkeypatched tool
internals — the full stack (auth shim → http_client → 202 polling → readback)
executes for real.
"""

from __future__ import annotations

import json

import pytest

from workiva_mcp import mock_transport


@pytest.fixture()
def mock_mode(monkeypatch):
    monkeypatch.setenv("WORKIVA_MCP_MOCK", "1")
    monkeypatch.setenv("WORKIVA_MCP_MOCK_POLL_TICKS", "1")
    # Make polling instant for tests.
    from workiva_mcp import http_client

    monkeypatch.setattr(http_client.time, "sleep", lambda _s: None)
    mock_transport.reset(rearm_429=False)
    yield
    mock_transport.reset(rearm_429=None)


@pytest.mark.asyncio
async def test_list_spreadsheets_and_sheets(mock_mode):
    from workiva_mcp.tools import spreadsheets

    listing = await spreadsheets.workiva_list_spreadsheets()
    assert "City of Riverton FY2025 ACFR Workbook" in listing
    assert mock_transport.RIVERTON_SPREADSHEET_ID in listing

    sheets = await spreadsheets.workiva_list_sheets(mock_transport.RIVERTON_SPREADSHEET_ID)
    assert "Statement of Net Position" in sheets
    assert "tok-riverton-net-position" in sheets


@pytest.mark.asyncio
async def test_read_cells_returns_fixture_grid(mock_mode):
    from workiva_mcp.tools import cells

    out = await cells.workiva_read_cells("tok-riverton-net-position", end_row=8)
    assert "City of Riverton" in out
    assert "Cash and investments" in out
    assert "12500000" in out
    assert "62500000" in out  # =SUM(B5:B7) calculated by the fake


@pytest.mark.asyncio
async def test_verified_write_polls_202_and_reads_back(mock_mode):
    from workiva_mcp.tools import cells

    result = json.loads(await cells.workiva_write_verified(
        mock_transport.RIVERTON_SPREADSHEET_ID,
        "b2c3d4e5f60718293a4b5c6d7e8f90a1",
        [
            {"row": 4, "col": 1, "value": "12750000"},
            {"row": 8, "col": 0, "value": "Deferred outflows of resources"},
        ],
    ))

    assert result["status"] == "verified"
    assert result["mismatches"] == []
    assert result["written_cells"] == 2
    assert mock_transport.stats["writes"] == 2      # one PUT per row
    assert mock_transport.stats["polls"] >= 2       # each 202 was polled

    readback = await cells.workiva_read_cells("tok-riverton-net-position", end_row=9)
    assert "12750000" in readback
    assert "Deferred outflows of resources" in readback


@pytest.mark.asyncio
async def test_opt_in_429_exercises_backoff_ladder(mock_mode):
    from workiva_mcp.tools import spreadsheets

    mock_transport.reset(rearm_429=True)
    listing = await spreadsheets.workiva_list_spreadsheets()

    assert "City of Riverton" in listing            # succeeded after retry
    assert mock_transport.stats["rate_limited"] == 1


@pytest.mark.asyncio
async def test_compact_contract_gate_blocks_then_allows_write(mock_mode):
    from workiva_mcp import compact_tools

    args = {
        "spreadsheet_id": mock_transport.RIVERTON_SPREADSHEET_ID,
        "sheet_id": "b2c3d4e5f60718293a4b5c6d7e8f90a1",
        "cells": [{"row": 4, "col": 1, "value": "12750000"}],
    }

    blocked = json.loads(await compact_tools.call_tool("workiva_write_verified", args))
    assert blocked["requires_confirmation"] is True
    assert mock_transport.stats["writes"] == 0      # nothing reached the transport

    allowed = json.loads(
        await compact_tools.call_tool("workiva_write_verified", args, confirm_write=True)
    )
    assert allowed["status"] == "verified"
    assert allowed["state"] == "verified"
    assert allowed["receipt_uri"].startswith("workiva-receipt://")
