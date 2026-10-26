"""Credential-free reporting review: python -m workiva_mcp.reporting_demo."""

from __future__ import annotations

import asyncio
import json
import os

from workiva_mcp.mock_transport import RIVERTON_SPREADSHEET_ID


def demo_rules() -> list[dict]:
    def ref(sheet: str, cell: str, **options) -> dict:
        return {
            "spreadsheet_id": RIVERTON_SPREADSHEET_ID,
            "sheet_id": sheet,
            "cell": cell,
            **options,
        }

    statement = "b2c3d4e5f60718293a4b5c6d7e8f90a1"
    funds = "c3d4e5f60718293a4b5c6d7e8f90a1b2"
    support = "riverton-review-schedule"
    return [
        {
            "id": "total-assets",
            "description": "Assets equal cash plus receivables plus capital assets",
            "reported": ref(statement, "B8"),
            "supporting": [ref(statement, cell) for cell in ("B5", "B6", "B7")],
        },
        {
            "id": "cash-tie-out",
            "description": "General Fund cash agrees to the supporting schedule",
            "reported": ref(funds, "B4"),
            "supporting": [ref(support, "B2")],
        },
        {
            "id": "balance-rollforward",
            "description": "Ending balance equals beginning plus additions less reductions",
            "reported": ref(support, "B6"),
            "supporting": [
                ref(support, "B3", scale="thousands"),
                ref(support, "B4"),
                ref(support, "B5", sign="subtract"),
            ],
        },
        {
            "id": "missing-support",
            "description": "Cash agrees to an incomplete supporting balance",
            "reported": ref(funds, "B4"),
            "supporting": [ref(support, "B7")],
        },
    ]


async def run() -> dict:
    # Force offline transport, even if the invoking environment disables mock mode.
    os.environ["WORKIVA_MCP_MOCK"] = "1"
    from workiva_mcp import mock_transport
    from workiva_mcp.compact_tools import call_tool

    mock_transport.reset(rearm_429=False)
    report = json.loads(
        await call_tool(
            "workiva_check_tie_outs",
            {"rules": demo_rules()},
            max_chars=100000,
            store_over_chars=100000,
        )
    )
    assert report["summary"] == {"passed": 1, "failed": 2, "not_evaluated": 1}
    assert mock_transport.stats["writes"] == 0
    return report


def main() -> None:
    report = asyncio.run(run())
    print(json.dumps(report, indent=2))
    print("TIE_OUT_PASS passed=1 failed=2 not_evaluated=1 upstream_writes=0")


if __name__ == "__main__":
    main()
