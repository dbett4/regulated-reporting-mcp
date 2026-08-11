"""End-to-end mock-mode demo: list → read → gated write → verified readback.

Runs entirely offline against the in-memory FakeWorkiva transport — no
credentials, no network. Exercises the real code paths: catalog discovery,
contract gating (a write without `confirm_write=true` is refused), the 202 +
Operation-Location polling loop, the one-shot 429 backoff ladder, post-write
readback verification, and the redacted execution receipt.

    python -m workiva_mcp.demo        (or: workiva-mcp-demo)
"""

from __future__ import annotations

import asyncio
import json
import os
import warnings


def _silence_known_third_party_warnings() -> None:
    """Suppress one known, non-actionable warning from the dependency stack.

    On Python 3.14, importing the mcp SDK triggers a pydantic_settings
    ``IncompleteFieldDefinitionWarning`` about the SDK's own ``lifespan``
    settings field. It is upstream noise, not something this package can fix,
    so it is filtered by exact category — everything else still surfaces.
    """
    try:
        from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning
    except ImportError:  # older pydantic_settings: warning does not exist
        return
    warnings.filterwarnings("ignore", category=IncompleteFieldDefinitionWarning)


def _print_step(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


async def _run() -> int:
    _silence_known_third_party_warnings()
    os.environ.setdefault("WORKIVA_MCP_MOCK", "1")
    os.environ.setdefault("WORKIVA_MCP_MOCK_429", "1")   # arm one 429 to show the backoff ladder
    os.environ.setdefault("WORKIVA_MCP_MOCK_POLL_TICKS", "2")

    from workiva_mcp import compact_tools, mock_transport
    from workiva_mcp.receipt_store import receipt_root

    mock_transport.reset(rearm_429=True)
    ss_id = mock_transport.RIVERTON_SPREADSHEET_ID

    _print_step("1. Discover tools (compact catalog)")
    stats = json.loads(compact_tools.search_tools(detail="stats"))
    print(f"full catalog: {stats['full_catalog_tools']} tools -> compact surface: "
          f"{stats['compact_catalog_tools']} tools ({stats['tool_count_reduction_pct']}% reduction)")

    _print_step("2. List spreadsheets (read tool — no confirmation needed)")
    print(await compact_tools.call_tool("workiva_list_spreadsheets"))

    _print_step("3. List sheets in the City of Riverton workbook")
    print(await compact_tools.call_tool("workiva_list_sheets", {"spreadsheet_id": ss_id}))

    _print_step("4. Read the Statement of Net Position cells")
    token = "tok-riverton-net-position"
    print(await compact_tools.call_tool("workiva_read_cells", {"table_id": token, "end_row": 8}))

    sheet_id = "b2c3d4e5f60718293a4b5c6d7e8f90a1"
    cells = [
        {"row": 4, "col": 1, "value": "12750000"},   # revised cash balance
        {"row": 8, "col": 0, "value": "Deferred outflows of resources"},
        {"row": 8, "col": 1, "value": "310000"},
    ]

    _print_step("5. Attempt the write WITHOUT confirm_write (contract blocks it)")
    blocked = await compact_tools.call_tool(
        "workiva_write_verified",
        {"spreadsheet_id": ss_id, "sheet_id": sheet_id, "cells": cells},
    )
    print(blocked)
    if "requires_confirmation" not in blocked:
        print("ERROR: expected the tool contract to block the unconfirmed write")
        return 1

    _print_step("6. Re-issue with confirm_write=true (verified write + readback)")
    result_text = await compact_tools.call_tool(
        "workiva_write_verified",
        {"spreadsheet_id": ss_id, "sheet_id": sheet_id, "cells": cells},
        confirm_write=True,
    )
    result = json.loads(result_text)
    print(json.dumps(result, indent=2))
    if result.get("status") != "verified" or result.get("state") != "verified":
        print("ERROR: expected a readback-verified write")
        return 1

    _print_step("7. Redacted execution receipt")
    receipt_id = result["receipt_uri"].removeprefix("workiva-receipt://")
    receipt_path = receipt_root() / f"{receipt_id}.json"
    print(f"receipt: {receipt_path}")
    print(receipt_path.read_text(encoding="utf-8"))

    _print_step("8. Read back the updated statement")
    print(await compact_tools.call_tool("workiva_read_cells", {"table_id": token, "end_row": 9}))

    _print_step("Transport evidence (FakeWorkiva)")
    print(json.dumps(mock_transport.stats, indent=2))
    print("\nDemo complete: contract gate, 429 backoff, 202 polling, readback "
          "verification, and receipt all exercised offline.")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
