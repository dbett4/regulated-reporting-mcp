"""FakeWorkiva — in-memory mock transport for credential-free runs.

Enabled with WORKIVA_MCP_MOCK=1. `http_client._transport()` swaps the live
urllib transport for `mock_raw_http`, which serves a synthetic "City of
Riverton" ACFR-shaped workbook entirely in memory:

- GET  /spreadsheets                                   — list workbooks
- GET  /spreadsheets/{ss}                              — workbook detail
- GET  /spreadsheets/{ss}/sheets                       — list sheets
- GET  /spreadsheets/{ss}/sheets/{sid}                 — sheet detail (table token)
- GET  /spreadsheets/{ss}/sheets/{sid}/values/{range}  — read values
- PUT  /spreadsheets/{ss}/sheets/{sid}/values/{range}  — write values (async 202)
- GET  /content/tables/{token}/cells                   — cell read with rawValue
- GET  /operations/{op}                                — canned 202 poll flow

Writes deliberately return **202 + Operation-Location** with a configurable
number of `in_progress` poll ticks (WORKIVA_MCP_MOCK_POLL_TICKS, default 2) so
the real async-polling code path executes, and WORKIVA_MCP_MOCK_429=1 arms a
one-shot 429 so the rate-limit backoff ladder runs visibly too.

All identifiers are synthetic; no live data is embedded.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any

# ── Synthetic fixture: City of Riverton FY2025 ACFR workbook ────────────────

RIVERTON_SPREADSHEET_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"

_SHEETS: list[dict[str, Any]] = [
    {
        "id": "b2c3d4e5f60718293a4b5c6d7e8f90a1",
        "name": "Statement of Net Position",
        "table_token": "tok-riverton-net-position",
        "grid": {
            (0, 0): "City of Riverton",
            (1, 0): "Statement of Net Position",
            (2, 0): "As of June 30, 2025",
            (4, 0): "Cash and investments",
            (4, 1): "12500000",
            (5, 0): "Receivables, net",
            (5, 1): "1250000",
            (6, 0): "Capital assets, net",
            (6, 1): "48750000",
            (7, 0): "Total assets",
            (7, 1): "=SUM(B5:B7)",
        },
    },
    {
        "id": "c3d4e5f60718293a4b5c6d7e8f90a1b2",
        "name": "Governmental Funds Balance Sheet",
        "table_token": "tok-riverton-gov-funds",
        "grid": {
            (0, 0): "City of Riverton",
            (1, 0): "Balance Sheet — Governmental Funds",
            (3, 0): "General Fund cash",
            (3, 1): "4200000",
            (4, 0): "Special Revenue Fund cash",
            (4, 1): "1875000",
        },
    },
    {
        "id": "d4e5f60718293a4b5c6d7e8f90a1b2c3",
        "name": "Trial Balance",
        "table_token": "tok-riverton-trial-balance",
        "grid": {
            (0, 0): "Account",
            (0, 1): "Balance",
            (1, 0): "101-Cash",
            (1, 1): "4200000",
            (2, 0): "121-Receivables",
            (2, 1): "1250000",
        },
    },
]

# Live mutable state (module-level so a process sees consistent readbacks).
_grids: dict[tuple[str, str], dict[tuple[int, int], str]] = {
    (RIVERTON_SPREADSHEET_ID, sheet["id"]): dict(sheet["grid"]) for sheet in _SHEETS
}
_operations: dict[str, int] = {}  # op_id -> remaining in_progress ticks

# Observability counters (used by the demo to prove which paths ran).
stats: dict[str, int] = {"requests": 0, "writes": 0, "rate_limited": 0, "polls": 0}

_429_armed: bool | None = None


def reset(rearm_429: bool | None = None) -> None:
    """Restore the pristine fixture (used by tests and repeated demo runs)."""
    global _429_armed
    _grids.clear()
    _grids.update(
        {(RIVERTON_SPREADSHEET_ID, sheet["id"]): dict(sheet["grid"]) for sheet in _SHEETS}
    )
    _operations.clear()
    for key in stats:
        stats[key] = 0
    _429_armed = rearm_429


def _poll_ticks() -> int:
    try:
        return max(0, int(os.environ.get("WORKIVA_MCP_MOCK_POLL_TICKS", "2")))
    except ValueError:
        return 2


def _maybe_rate_limit() -> bool:
    """One-shot 429 when WORKIVA_MCP_MOCK_429=1 (arms once per reset)."""
    global _429_armed
    if _429_armed is None:
        _429_armed = os.environ.get("WORKIVA_MCP_MOCK_429", "").strip() == "1"
    if _429_armed:
        _429_armed = False
        stats["rate_limited"] += 1
        return True
    return False


# ── A1 helpers ───────────────────────────────────────────────────────────────

_A1_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _col_to_idx(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _idx_to_col(idx: int) -> str:
    s = ""
    c = idx
    while True:
        s = chr(ord("A") + (c % 26)) + s
        c = c // 26 - 1
        if c < 0:
            return s


def _parse_range(a1_range: str) -> tuple[int, int, int, int]:
    """Return (start_row, start_col, end_row, end_col), all 0-based inclusive."""
    parts = a1_range.split(":", 1)
    m1 = _A1_RE.match(parts[0])
    m2 = _A1_RE.match(parts[1]) if len(parts) == 2 else m1
    if not (m1 and m2):
        raise ValueError(f"bad A1 range: {a1_range!r}")
    r1, c1 = int(m1.group(2)) - 1, _col_to_idx(m1.group(1))
    r2, c2 = int(m2.group(2)) - 1, _col_to_idx(m2.group(1))
    return min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


# ── Fixture lookups ──────────────────────────────────────────────────────────


def _sheet_meta(sheet: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": sheet["id"],
        "name": sheet["name"],
        "table": {"table": sheet["table_token"]},
    }


def _find_sheet(ss_id: str, sheet_id: str) -> dict[str, Any] | None:
    if ss_id != RIVERTON_SPREADSHEET_ID:
        return None
    for sheet in _SHEETS:
        if sheet["id"] == sheet_id:
            return sheet
    return None


def _find_sheet_by_token(token: str) -> dict[str, Any] | None:
    for sheet in _SHEETS:
        if sheet["table_token"] == token:
            return sheet
    return None


def _display_value(grid: dict[tuple[int, int], str], r: int, c: int) -> str:
    raw = grid.get((r, c), "")
    if raw.startswith("="):
        m = re.fullmatch(r"=SUM\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)", raw)
        if m:
            col = _col_to_idx(m.group(1))
            total = 0.0
            numeric = False
            for row in range(int(m.group(2)) - 1, int(m.group(4))):
                cell = grid.get((row, col), "")
                try:
                    total += float(cell.replace(",", ""))
                    numeric = True
                except ValueError:
                    continue
            if numeric:
                return f"{total:.0f}" if total == int(total) else str(total)
        return raw
    return raw


# ── Endpoint handlers ────────────────────────────────────────────────────────


def _list_spreadsheets_body() -> dict[str, Any]:
    return {
        "data": [
            {
                "id": RIVERTON_SPREADSHEET_ID,
                "name": "City of Riverton FY2025 ACFR Workbook",
                "modified": {"dateTime": "2025-06-30T17:00:00Z"},
            }
        ]
    }


def _spreadsheet_body() -> dict[str, Any]:
    return {
        "id": RIVERTON_SPREADSHEET_ID,
        "name": "City of Riverton FY2025 ACFR Workbook",
        "modified": {"dateTime": "2025-06-30T17:00:00Z"},
        "sheets": {"data": [_sheet_meta(sheet) for sheet in _SHEETS]},
    }


def _values_body(grid: dict[tuple[int, int], str], a1_range: str) -> dict[str, Any]:
    r1, c1, r2, c2 = _parse_range(a1_range)
    values = [
        [_display_value(grid, r, c) for c in range(c1, c2 + 1)]
        for r in range(r1, r2 + 1)
    ]
    return {"data": [{"values": values}]}


def _write_values(
    grid: dict[tuple[int, int], str], a1_range: str, payload: Any
) -> tuple[int, dict[str, str], Any]:
    rows = (payload or {}).get("values") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return 400, {}, {"error": {"message": "values payload required"}}
    r1, c1, _r2, _c2 = _parse_range(a1_range)
    for dr, row in enumerate(rows):
        if not isinstance(row, list):
            continue
        for dc, value in enumerate(row):
            grid[(r1 + dr, c1 + dc)] = "" if value is None else str(value)
    stats["writes"] += 1
    op_id = uuid.uuid4().hex
    _operations[op_id] = _poll_ticks()
    return 202, {"Operation-Location": f"/operations/{op_id}"}, {}


def _cells_body(sheet: dict[str, Any], query: str) -> dict[str, Any]:
    grid = _grids[(RIVERTON_SPREADSHEET_ID, sheet["id"])]
    start_row, stop_row = 0, 10**9
    for part in (query or "").split("&"):
        if part.startswith("startRow="):
            start_row = int(part.split("=", 1)[1])
        elif part.startswith("stopRow="):
            stop_row = int(part.split("=", 1)[1])
    cells = []
    for (r, c), raw in sorted(grid.items()):
        if not (start_row <= r <= stop_row):
            continue
        if raw.startswith("="):
            value: dict[str, Any] = {
                "type": "formula",
                "calculatedValue": {"value": _display_value(grid, r, c)},
            }
        else:
            value = {"type": "plainText", "value": raw}
        cells.append({"position": {"row": r, "column": c}, "value": value, "rawValue": raw})
    return {"data": cells}


def _operation_body(op_id: str) -> tuple[int, dict[str, str], Any]:
    stats["polls"] += 1
    if op_id not in _operations:
        return 404, {}, {"error": {"message": f"unknown operation {op_id}"}}
    if _operations[op_id] > 0:
        _operations[op_id] -= 1
        return 200, {}, {"status": "in_progress"}
    return 200, {}, {"status": "completed"}


# ── Transport entry point ────────────────────────────────────────────────────


def mock_raw_http(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    data: Any = None,
    timeout: int = 60,
    raw_on_pk_prefix: bool = False,
) -> tuple[int, dict[str, str], Any]:
    """Drop-in replacement for http_client._raw_http, fully in-memory."""
    stats["requests"] += 1

    # Strip scheme/host; keep path + query.
    path = re.sub(r"^https?://[^/]+", "", url)
    path, _, query = path.partition("?")
    path = path.rstrip("/") or "/"

    if _maybe_rate_limit():
        return 429, {"Retry-After": "1"}, {"error": {"message": "mock rate limit"}}

    if path == "/spreadsheets":
        return 200, {}, _list_spreadsheets_body()

    m = re.fullmatch(r"/spreadsheets/([^/]+)", path)
    if m:
        if m.group(1) != RIVERTON_SPREADSHEET_ID:
            return 404, {}, {"error": {"message": "spreadsheet not found"}}
        return 200, {}, _spreadsheet_body()

    m = re.fullmatch(r"/spreadsheets/([^/]+)/sheets", path)
    if m:
        if m.group(1) != RIVERTON_SPREADSHEET_ID:
            return 404, {}, {"error": {"message": "spreadsheet not found"}}
        return 200, {}, {"data": [_sheet_meta(sheet) for sheet in _SHEETS]}

    m = re.fullmatch(r"/spreadsheets/([^/]+)/sheets/([^/]+)", path)
    if m:
        sheet = _find_sheet(m.group(1), m.group(2))
        if sheet is None:
            return 404, {}, {"error": {"message": "sheet not found"}}
        return 200, {}, _sheet_meta(sheet)

    m = re.fullmatch(r"/spreadsheets/([^/]+)/sheets/([^/]+)/values/([^/]+)", path)
    if m:
        sheet = _find_sheet(m.group(1), m.group(2))
        if sheet is None:
            return 404, {}, {"error": {"message": "sheet not found"}}
        grid = _grids[(m.group(1), m.group(2))]
        if method.upper() == "PUT":
            return _write_values(grid, m.group(3), data)
        return 200, {}, _values_body(grid, m.group(3))

    m = re.fullmatch(r"/content/tables/([^/]+)/cells", path)
    if m:
        sheet = _find_sheet_by_token(m.group(1))
        if sheet is None:
            return 404, {}, {"error": {"message": "table not found"}}
        return 200, {}, _cells_body(sheet, query)

    m = re.fullmatch(r"/operations/([^/]+)", path)
    if m:
        return _operation_body(m.group(1))

    return 404, {}, {"error": {"message": f"mock transport has no route for {method} {path}"}}
