from __future__ import annotations

import json

import pytest

from workiva_mcp.tools import cells


@pytest.mark.asyncio
async def test_workiva_write_verified_writes_rows_and_reads_back(monkeypatch):
    calls: list[tuple[str, str, object]] = []

    def fake_api_request(path, **kwargs):
        method = kwargs.get("method", "GET")
        calls.append((method, path, kwargs.get("data")))
        if method == "PUT":
            return 200, {}, {"ok": True}
        if path.endswith("/values/B1:C1"):
            return 200, {}, {"data": [{"values": [["101", 12.5]]}]}
        if path.endswith("/values/A3:A3"):
            return 200, {}, {"data": [{"values": [["42"]]}]}
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(cells, "api_request", fake_api_request)

    out = json.loads(await cells.workiva_write_verified(
        "ss-1",
        "sheet-1",
        [
            {"row": 0, "col": 1, "value": "101"},
            {"row": 0, "col": 2, "value": 12.5},
            {"row": 2, "col": 0, "value": "=SUM(A1:A2)"},
        ],
    ))

    assert out["status"] == "verified"
    assert out["written_cells"] == 3
    assert out["verified_cells"] == 2
    assert out["formula_cells_unverified"] == [{"a1": "A3", "row": 2, "col": 0}]
    assert out["mismatches"] == []
    assert calls == [
        ("PUT", "/spreadsheets/ss-1/sheets/sheet-1/values/B1:C1", {"values": [["101", "12.5"]]}),
        ("GET", "/spreadsheets/ss-1/sheets/sheet-1/values/B1:C1", None),
        ("PUT", "/spreadsheets/ss-1/sheets/sheet-1/values/A3:A3", {"values": [["=SUM(A1:A2)"]]}),
        ("GET", "/spreadsheets/ss-1/sheets/sheet-1/values/A3:A3", None),
    ]


@pytest.mark.asyncio
async def test_workiva_write_verified_reports_missed_row_without_retry(monkeypatch):
    read_count = 0
    put_ranges: list[str] = []

    def fake_api_request(path, **kwargs):
        nonlocal read_count
        method = kwargs.get("method", "GET")
        if method == "PUT":
            put_ranges.append(path.rsplit("/values/", 1)[1])
            return 200, {}, {"ok": True}
        read_count += 1
        values = [["old"]] if read_count == 1 else [["new"]]
        return 200, {}, {"data": [{"values": values}]}

    monkeypatch.setattr(cells, "api_request", fake_api_request)

    out = json.loads(await cells.workiva_write_verified(
        "ss-1",
        "sheet-1",
        [{"row": 4, "col": 3, "value": "new"}],
    ))

    assert out["status"] == "mismatch"
    assert out["retried_ranges"] == []
    assert out["mismatches"] == [{"a1": "D5", "row": 4, "col": 3, "expected": "new", "actual": "old"}]
    assert put_ranges == ["D5:D5"]


@pytest.mark.asyncio
async def test_workiva_write_verified_reports_remaining_mismatch(monkeypatch):
    def fake_api_request(path, **kwargs):
        if kwargs.get("method") == "PUT":
            return 200, {}, {"ok": True}
        return 200, {}, {"data": [{"values": [["wrong"]]}]}

    monkeypatch.setattr(cells, "api_request", fake_api_request)

    out = json.loads(await cells.workiva_write_verified(
        "ss-1",
        "sheet-1",
        [{"row": 0, "col": 0, "value": "right"}],
    ))

    assert out["status"] == "mismatch"
    assert out["mismatches"] == [
        {"a1": "A1", "row": 0, "col": 0, "expected": "right", "actual": "wrong"}
    ]


@pytest.mark.asyncio
async def test_workiva_write_verified_polls_202_before_readback(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_api_request(path, **kwargs):
        method = kwargs.get("method", "GET")
        calls.append((method, path))
        if method == "PUT":
            return 202, {}, {"operationLocation": "/operations/write-1"}
        return 200, {}, {"data": [{"values": [["done"]]}]}

    monkeypatch.setattr(cells, "api_request", fake_api_request)
    monkeypatch.setattr(cells, "poll_operation", lambda location, **kwargs: {"status": "completed"})

    out = json.loads(await cells.workiva_write_verified(
        "ss-1", "sheet-1", [{"row": 0, "col": 0, "value": "done"}]
    ))
    assert out["status"] == "verified"
    assert calls == [
        ("PUT", "/spreadsheets/ss-1/sheets/sheet-1/values/A1:A1"),
        ("GET", "/spreadsheets/ss-1/sheets/sheet-1/values/A1:A1"),
    ]


@pytest.mark.asyncio
async def test_workiva_write_verified_rejects_202_without_operation_location(monkeypatch):
    monkeypatch.setattr(
        cells,
        "api_request",
        lambda path, **kwargs: (202, {}, {"accepted": True}) if kwargs.get("method") == "PUT" else pytest.fail("must not read back indeterminate write"),
    )

    out = await cells.workiva_write_verified(
        "ss-1", "sheet-1", [{"row": 0, "col": 0, "value": "done"}]
    )
    assert "error writing A1:A1" in out
    assert "indeterminate" in out
