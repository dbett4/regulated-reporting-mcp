import json

import pytest

from workiva_mcp.tools import cells


def test_put_values_202_without_operation_location_is_not_reported_success():
    calls = []

    def writer(*args, **kwargs):
        calls.append((args, kwargs))
        return 202, {}, {"accepted": True}

    result = cells._put_values_once(
        "/spreadsheets/s/sheets/h/values/A1:A1",
        ["x"],
        row=0,
        min_col=0,
        tool_name="test",
        idempotency_key=("s:h", "A1:A1"),
        spurious_404_keys=set(),
        writer=writer,
    )
    assert result[0] == 409
    assert result[2]["state"] == "indeterminate"
    assert len(calls) == 1


def test_put_values_spurious_404_uses_readback_without_second_write():
    writes = []

    def writer(*args, **kwargs):
        writes.append((args, kwargs))
        return 404, {}, {"message": "not found"}

    def reader(*args, **kwargs):
        return 200, {}, {"values": [["x"]]}

    result = cells._put_values_once(
        "/spreadsheets/s/sheets/h/values/A1:A1",
        ["x"],
        row=0,
        min_col=0,
        tool_name="test",
        idempotency_key=("s:h", "A1:A1"),
        spurious_404_keys=set(),
        writer=writer,
        reader=reader,
    )
    assert result[0] == 200
    assert result[3] is True
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_write_verified_never_retries_mismatch_even_when_legacy_flag_is_true(monkeypatch):
    writes = 0

    def fake_api_request(path, **kwargs):
        nonlocal writes
        if kwargs.get("method") == "PUT":
            writes += 1
            return 200, {}, {"ok": True}
        return 200, {}, {"values": [["different"]]}

    monkeypatch.setattr(cells, "api_request", fake_api_request)
    result = json.loads(
        await cells.workiva_write_verified(
            "spreadsheet", "sheet", [{"row": 0, "col": 0, "value": "expected"}], retry_misses=True
        )
    )
    assert writes == 1
    assert result["retried_ranges"] == []
    assert result["mismatches"][0]["expected"] == "expected"
    assert result["mismatches"][0]["actual"] == "different"
