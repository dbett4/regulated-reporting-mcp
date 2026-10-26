"""Known-answer tie-outs: signed sums, units, rounding, and unavailable evidence."""

import json
from decimal import localcontext

import pytest

from workiva_mcp import mock_transport
from workiva_mcp.compact_tools import call_tool, search_tools
from workiva_mcp.reporting_demo import demo_rules
from workiva_mcp.tools import reporting


def rule(**overrides):
    def ref(cell, **options):
        return {"spreadsheet_id": "book", "sheet_id": "sheet", "cell": cell, **options}

    return {
        "id": "example",
        "description": "Known-answer reconciliation",
        "reported": ref("A1"),
        "supporting": [ref("B1"), ref("C1")],
        **overrides,
    }


def transport(monkeypatch, values):
    calls = []

    def read(path, *, method, tool_name):
        assert method == "GET"
        calls.append(path)
        value = values[path.rsplit("/", 1)[-1]]
        if isinstance(value, Exception):
            raise value
        return 200, {}, {"values": [[value]]}

    monkeypatch.setattr(reporting, "api_request", read)
    return calls


@pytest.mark.asyncio
async def test_riverton_through_guarded_dispatch(monkeypatch):
    monkeypatch.setenv("WORKIVA_MCP_MOCK", "1")
    mock_transport.reset(rearm_429=False)
    try:
        result = json.loads(
            await call_tool(
                "workiva_check_tie_outs",
                {"rules": demo_rules()},
                max_chars=100000,
                store_over_chars=100000,
            )
        )
        assert result["summary"] == {"passed": 1, "failed": 2, "not_evaluated": 1}
        findings = {item["rule_id"]: item for item in result["findings"]}
        assert findings["total-assets"]["expected"] == "62500000.00"
        assert findings["cash-tie-out"]["difference"] == "5000.00"
        assert findings["balance-rollforward"]["expected"] == "820000.00"
        assert findings["balance-rollforward"]["difference"] == "5000.00"
        assert "expected" not in findings["missing-support"]
        assert all(item["review_status"] == "unreviewed" for item in findings.values())
        assert mock_transport.stats["writes"] == 0
        assert "workiva_check_tie_outs" in search_tools(
            query="tie-out", family="reporting", detail="schema"
        )
    finally:
        mock_transport.reset(rearm_429=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reported,tolerance,status,difference",
    [
        ("0.010", "0", "passed", "0.00"),
        ("0.025", "0.02", "passed", "0.02"),
        ("0.035", "0.02", "failed", "0.03"),
        ("-0.015", "0.02", "failed", "-0.03"),
    ],
)
async def test_round_after_sum_half_up_and_inclusive_tolerance(
    monkeypatch, reported, tolerance, status, difference
):
    transport(monkeypatch, {"A1": reported, "B1": "0.005", "C1": "0.005"})
    with localcontext() as ctx:
        ctx.prec = 3  # Ambient precision must not change the result.
        result = json.loads(await reporting.workiva_check_tie_outs([rule(tolerance=tolerance)]))[
            "findings"
        ][0]
    assert (result["expected"], result["difference"], result["status"]) == (
        "0.01",
        difference,
        status,
    )


@pytest.mark.asyncio
async def test_units_negative_values_and_signed_rollforward(monkeypatch):
    transport(monkeypatch, {"A1": "1,219.875", "B1": "1.25", "C1": "(20.125)", "D1": "10"})
    check = rule(decimal_places=3)
    check["supporting"][0]["scale"] = "thousands"
    check["supporting"].append({**check["reported"], "cell": "D1", "sign": "subtract"})
    result = json.loads(await reporting.workiva_check_tie_outs([check]))["findings"][0]
    assert result["expected"] == "1219.875"
    assert result["status"] == "passed"
    assert result["evidence"][1]["normalized_value"] == "1250.00"
    assert result["evidence"][2]["value"] == "(20.125)"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "-",
        "#REF!",
        "=SUM(B1:B2)",
        "NaN",
        "Infinity",
        True,
        "12,34",
        "10%",
        "$10",
        TimeoutError(),
    ],
)
async def test_unavailable_is_not_zero_or_failed_check(monkeypatch, bad):
    transport(monkeypatch, {"A1": "0", "B1": bad, "C1": "0"})
    finding = json.loads(await reporting.workiva_check_tie_outs([rule()]))["findings"][0]
    assert finding["status"] == "not_evaluated"
    assert "expected" not in finding
    assert "error" in finding["evidence"][1]


@pytest.mark.asyncio
async def test_one_read_per_unique_source_and_independent_rule_scales(monkeypatch):
    calls = transport(monkeypatch, {"A1": "3", "B1": "1", "C1": "2"})
    second = rule(id="scaled")
    second["reported"]["scale"] = "thousands"
    result = json.loads(await reporting.workiva_check_tie_outs([rule(), second]))
    assert len(calls) == 3
    assert result["summary"] == {"passed": 1, "failed": 1, "not_evaluated": 0}
    assert result["findings"][1]["difference"] == "2997.00"


@pytest.mark.asyncio
async def test_same_address_in_distinct_workbooks_and_sheets(monkeypatch):
    values = {
        "/spreadsheets/book/sheets/sheet/values/A1": "3.125001",
        "/spreadsheets/other/sheets/sheet/values/A1": "1.000001",
        "/spreadsheets/book/sheets/other/values/A1": "2.125",
    }
    calls = []

    def read(path, **kwargs):
        calls.append(path)
        return 200, {}, {"values": [[values[path]]]}

    monkeypatch.setattr(reporting, "api_request", read)
    check = rule()
    check["reported"]["scale"] = "millions"
    check["supporting"] = [
        {**check["reported"], "spreadsheet_id": "other"},
        {**check["reported"], "sheet_id": "other"},
    ]
    with localcontext() as ctx:
        ctx.prec = 3
        finding = json.loads(await reporting.workiva_check_tie_outs([check]))["findings"][0]
    assert len(calls) == 3
    assert finding["status"] == "passed"
    assert finding["expected"] == "3125001.00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rules",
    [
        [],
        [rule(), rule()],
        [rule(tolerance="-1")],
        [rule(supporting=[])],
        [rule(decimal_places=True)],
        [rule(unexpected="ignored?")],
        [rule()] * 101,
    ],
)
async def test_invalid_plans_rejected_before_read(monkeypatch, rules):
    calls = transport(monkeypatch, {})
    with pytest.raises(ValueError):
        await reporting.workiva_check_tie_outs(rules)
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body", [(403, {}), (200, {"values": []}), (200, {"values": [[1, 2]]})]
)
async def test_bad_read_does_not_abort_unrelated_checks(monkeypatch, status, body):
    def read(path, **kwargs):
        if path.endswith("/A1"):
            return status, {}, body
        return 200, {}, {"values": [["2"]]}

    monkeypatch.setattr(reporting, "api_request", read)
    good = rule(id="good")
    good["reported"]["cell"] = "B1"
    good["supporting"] = [good["supporting"][0]]
    result = json.loads(await reporting.workiva_check_tie_outs([rule(), good]))
    assert result["summary"] == {"passed": 1, "failed": 0, "not_evaluated": 1}
