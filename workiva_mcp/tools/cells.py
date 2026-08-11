"""
Cell read/write/format tools for Workiva spreadsheet tables.
"""

import json
from typing import Any, Callable, Literal

from workiva_mcp.config import MAX_CELL_ROWS
from workiva_mcp.execution import ExecutionState, execute_mutation
from workiva_mcp.http_client import WorkivaAPIError, _error, api_request, paginate, poll_operation
from workiva_mcp.server import mcp


def _extract_value(cell: dict) -> str:
    """Extract display value from a cell, handling all four types."""
    val = cell.get("value", {})
    if not val or not isinstance(val, dict):
        return ""

    vtype = val.get("type", "")

    if vtype == "formula":
        cv = val.get("calculatedValue", {})
        if isinstance(cv, dict):
            return str(cv.get("value", ""))
        return str(cv) if cv else ""

    if vtype == "plainText":
        return str(val.get("value", ""))

    if vtype == "richText":
        spans = val.get("value", [])
        if isinstance(spans, list):
            parts = []
            for span in spans:
                if isinstance(span, dict):
                    parts.append(span.get("text", ""))
                elif isinstance(span, str):
                    parts.append(span)
            return "".join(parts)
        return str(spans)

    if vtype == "number":
        return str(val.get("value", ""))

    # Fallback
    v = val.get("value", val.get("calculatedValue", ""))
    return str(v) if v else ""


def _cell_position(cell: dict) -> tuple[int, int]:
    """Return (row, col) from cell position."""
    pos = cell.get("position", {})
    return pos.get("row", 0), pos.get("column", 0)


@mcp.tool()
async def workiva_read_cells(
    table_id: str,
    start_row: int = 0,
    end_row: int = -1,
    start_col: int = 0,
    end_col: int = -1,
) -> str:
    """Read cell values from a spreadsheet table. Returns data in TSV format.

    Args:
        table_id: The table UUID (get from workiva_spreadsheet_sheets)
        start_row: First row to read (0-based, default 0)
        end_row: Last row to read (-1 = up to MAX_CELL_ROWS rows)
        start_col: First column to read (0-based, default 0)
        end_col: Last column to read (-1 = all columns)
    """
    if end_row < 0:
        end_row = start_row + MAX_CELL_ROWS

    path = f"/content/tables/{table_id}/cells"
    try:
        all_cells = paginate(path, max_pages=20, tool_name="workiva_read_cells")
    except WorkivaAPIError as e:
        return _error(e.status, e.body)

    if not all_cells:
        return "No cells found in this table."

    # Filter to requested bounds
    filtered = []
    max_row = 0
    max_col = 0
    for cell in all_cells:
        r, c = _cell_position(cell)
        if r < start_row or r > end_row:
            continue
        if start_col > 0 and c < start_col:
            continue
        if end_col >= 0 and c > end_col:
            continue
        filtered.append(cell)
        max_row = max(max_row, r)
        max_col = max(max_col, c)

    if not filtered:
        return f"No cells in range (rows {start_row}-{end_row}, cols {start_col}-{end_col})."

    # Build grid
    grid: dict[tuple[int, int], str] = {}
    for cell in filtered:
        r, c = _cell_position(cell)
        grid[(r, c)] = _extract_value(cell)

    # Determine bounds
    min_r = min(r for r, c in grid)
    min_c = min(c for r, c in grid)

    # Render as TSV
    lines = []
    total_rows_in_table = max_row + 1
    shown_rows = max_row - min_r + 1

    lines.append(f"# Table {table_id} — rows {min_r}-{max_row} of {total_rows_in_table}, cols {min_c}-{max_col}")

    for r in range(min_r, max_row + 1):
        row_vals = []
        for c in range(min_c, max_col + 1):
            row_vals.append(grid.get((r, c), ""))
        lines.append("\t".join(row_vals))

    if total_rows_in_table > end_row + 1:
        lines.append(f"\n# {total_rows_in_table - end_row - 1} more rows available. Use start_row={end_row + 1} to continue.")

    return "\n".join(lines)


def _col_to_a1(col0: int) -> str:
    letters = ""
    c = col0
    while True:
        letters = chr(ord("A") + (c % 26)) + letters
        c = c // 26 - 1
        if c < 0:
            break
    return letters


def _a1(row0: int, col0: int) -> str:
    return f"{_col_to_a1(col0)}{row0 + 1}"


def _coerce_write_value(value: Any) -> str:
    return "" if value is None else str(value)


def _group_cells_by_row(cells: list[dict]) -> dict[int, dict[int, str]]:
    by_row: dict[int, dict[int, str]] = {}
    for cell in cells:
        row = int(cell["row"])
        col = int(cell["col"])
        if row < 0 or col < 0:
            raise ValueError("row and col must be 0-based non-negative integers")
        by_row.setdefault(row, {})[col] = _coerce_write_value(cell.get("value"))
    return by_row


def _row_payload(row: int, cols: dict[int, str]) -> tuple[str, list[str], int]:
    min_c = min(cols)
    max_c = max(cols)
    values_row = [cols.get(c, "") for c in range(min_c, max_c + 1)]
    range_str = f"{_col_to_a1(min_c)}{row + 1}:{_col_to_a1(max_c)}{row + 1}"
    return range_str, values_row, min_c


def _extract_values_payload(body: Any) -> list[list[Any]]:
    if isinstance(body, dict):
        values = body.get("values")
        if isinstance(values, list):
            return values
        data = body.get("data")
        if isinstance(data, dict) and isinstance(data.get("values"), list):
            return data["values"]
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and isinstance(item.get("values"), list):
                    return item["values"]
    return []


def _normalize_read_value(value: Any) -> str:
    return "" if value is None else str(value)


def _value_mismatches(
    row: int,
    min_col: int,
    expected_row: list[str],
    actual_rows: list[list[Any]],
) -> tuple[list[dict[str, Any]], int]:
    actual_row = actual_rows[0] if actual_rows else []
    mismatches: list[dict[str, Any]] = []
    verified = 0
    for offset, expected in enumerate(expected_row):
        col = min_col + offset
        if expected.startswith("="):
            continue
        actual = actual_row[offset] if offset < len(actual_row) else ""
        actual_text = _normalize_read_value(actual)
        if actual_text == expected:
            verified += 1
            continue
        mismatches.append({
            "a1": _a1(row, col),
            "row": row,
            "col": col,
            "expected": expected,
            "actual": actual_text,
        })
    return mismatches, verified


_RequestFn = Callable[..., tuple[int, dict, Any]]


def _put_values_once(
    write_path: str,
    values_row: list[str],
    *,
    row: int,
    min_col: int,
    tool_name: str,
    idempotency_key: tuple[str, str],
    spurious_404_keys: set[tuple[str, str]],
    reader: _RequestFn | None = None,
    writer: _RequestFn | None = None,
) -> tuple[int, dict, Any, bool, list[dict[str, Any]], int]:
    if idempotency_key in spurious_404_keys:
        return (
            409,
            {},
            {
                "error": "duplicate write blocked after spurious-404 readback",
                "range": idempotency_key[1],
            },
            False,
            [],
            0,
        )
    writer_fn = writer or api_request
    reader_fn = reader or api_request
    result = execute_mutation(
        writer_fn,
        path=write_path,
        method="PUT",
        data={"values": [values_row]},
        tool_name=tool_name,
        label=f"write values {idempotency_key[1]}",
        poll=poll_operation,
    )
    s = result.status_code if result.status_code == 404 or (result.state is ExecutionState.APPLIED and result.status_code is not None) else 409
    headers: dict = {}
    body = result.body if result.state is ExecutionState.APPLIED else result.as_dict()
    if s != 404:
        return s, headers, body, False, [], 0

    spurious_404_keys.add(idempotency_key)
    rs, read_headers, read_body = reader_fn(
        write_path,
        tool_name=f"{tool_name}:readback_after_404",
    )
    if rs == 200:
        mismatches, verified = _value_mismatches(
            row,
            min_col,
            values_row,
            _extract_values_payload(read_body),
        )
        if not mismatches:
            return (
                200,
                read_headers,
                {"success_on_readback": True, "readback": read_body},
                True,
                mismatches,
                verified,
            )
    return s, headers, body, False, [], 0


@mcp.tool()
async def workiva_write_cells(
    spreadsheet_id: str,
    sheet_id: str,
    cells: list[dict],
) -> str:
    """Write values to cells via the v2026 values API.

    PUT /spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/values/{A1Range}.
    Formulas pass through as text starting with "=". Cells are grouped by row
    and written as one PUT per row with empty strings filling any column gaps.

    Args:
        spreadsheet_id: The spreadsheet UUID
        sheet_id: The sheet UUID (from workiva_spreadsheet_sheets)
        cells: [{row, col, value}] with 0-based row/col
    """
    if not cells:
        return "No cells to write."

    try:
        by_row = _group_cells_by_row(cells)
    except (KeyError, TypeError, ValueError) as exc:
        return _error(400, {}, cause=str(exc))

    written = 0
    success_on_readback_ranges: list[str] = []
    spurious_404_keys: set[tuple[str, str]] = set()
    for row in sorted(by_row):
        cols = by_row[row]
        range_str, values_row, min_c = _row_payload(row, cols)
        write_path = f"/spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/values/{range_str}"
        s, _, body, success_on_readback, _, _ = _put_values_once(
            write_path,
            values_row,
            row=row,
            min_col=min_c,
            tool_name="workiva_write_cells",
            idempotency_key=(f"{spreadsheet_id}:{sheet_id}", range_str),
            spurious_404_keys=spurious_404_keys,
        )
        if s not in (200, 201, 202):
            return _error(s, body, cause=f"error writing {range_str}: {str(body)[:200]}")
        if success_on_readback:
            success_on_readback_ranges.append(range_str)
        written += len(values_row)
    if success_on_readback_ranges:
        return (
            f"Wrote {written} cell(s) across {len(by_row)} row(s). "
            f"success-on-readback: {', '.join(success_on_readback_ranges)}"
        )
    return f"Wrote {written} cell(s) across {len(by_row)} row(s)."


@mcp.tool()
async def workiva_write_verified(
    spreadsheet_id: str,
    sheet_id: str,
    cells: list[dict],
    retry_misses: bool = False,
) -> str:
    """Write values/formulas, read back each row range, and report mismatches.

    Formula cells are written, but Values API read-back may return calculated
    results rather than raw formula text. Those cells are reported separately
    as unverified so callers know when table-cell rawValue inspection is still
    needed.

    Args:
        spreadsheet_id: The spreadsheet UUID
        sheet_id: The sheet UUID (from workiva_spreadsheet_sheets)
        cells: [{row, col, value}] with 0-based row/col
        retry_misses: Deprecated compatibility flag. Retries are intentionally disabled;
            mismatches are returned for an explicit, separately approved repair.
    """
    if not cells:
        return json.dumps({
            "status": "noop",
            "written_cells": 0,
            "verified_cells": 0,
            "row_count": 0,
            "ranges": [],
            "retried_ranges": [],
            "mismatches": [],
            "formula_cells_unverified": [],
        })

    try:
        by_row = _group_cells_by_row(cells)
    except (KeyError, TypeError, ValueError) as exc:
        return _error(400, {}, cause=str(exc))

    written = 0
    verified = 0
    ranges: list[str] = []
    retried_ranges: list[str] = []
    mismatches: list[dict[str, Any]] = []
    formula_cells_unverified: list[dict[str, Any]] = []
    success_on_readback_ranges: list[str] = []
    spurious_404_keys: set[tuple[str, str]] = set()

    for row in sorted(by_row):
        cols = by_row[row]
        range_str, values_row, min_c = _row_payload(row, cols)
        ranges.append(range_str)
        for offset, expected in enumerate(values_row):
            if expected.startswith("="):
                formula_cells_unverified.append({
                    "a1": _a1(row, min_c + offset),
                    "row": row,
                    "col": min_c + offset,
                })

        write_path = f"/spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/values/{range_str}"
        s, _, body, success_on_readback, row_mismatches, row_verified = _put_values_once(
            write_path,
            values_row,
            row=row,
            min_col=min_c,
            tool_name="workiva_write_verified",
            idempotency_key=(f"{spreadsheet_id}:{sheet_id}", range_str),
            spurious_404_keys=spurious_404_keys,
        )
        if s not in (200, 201, 202):
            return _error(s, body, cause=f"error writing {range_str}: {str(body)[:200]}")
        written += len(values_row)

        if success_on_readback:
            success_on_readback_ranges.append(range_str)
        else:
            s, _, body = api_request(write_path, tool_name="workiva_write_verified:readback")
            if s != 200:
                return _error(s, body, cause=f"error reading back {range_str}: {str(body)[:200]}")
            row_mismatches, row_verified = _value_mismatches(
                row,
                min_c,
                values_row,
                _extract_values_payload(body),
            )

        # Do not automatically reissue a write after a mismatch. The Workiva
        # values API provides no durable idempotency key, so retrying an
        # ambiguous mutation can overwrite an operator's concurrent change.
        # `retry_misses` remains accepted only for one-release compatibility.
        _ = retry_misses

        verified += row_verified
        mismatches.extend(row_mismatches)

    if mismatches:
        status = "mismatch"
    elif formula_cells_unverified:
        status = "indeterminate"
    else:
        status = "verified"

    return json.dumps({
        "status": status,
        "written_cells": written,
        "verified_cells": verified,
        "row_count": len(by_row),
        "ranges": ranges,
        "retried_ranges": retried_ranges,
        "success_on_readback_ranges": success_on_readback_ranges,
        "mismatches": mismatches,
        "formula_cells_unverified": formula_cells_unverified,
        "verification_complete": not formula_cells_unverified,
    })


@mcp.tool()
async def workiva_format_cells(
    table_id: str,
    operations: list[dict],
) -> str:
    """Format cells via POST /content/tables/{tableId}/cells/edit (v2026).

    table_id here is the sheet's OPAQUE content-table TOKEN (sheet.table.table — a
    long base64-ish string), NOT a 32-hex UUID. The legacy UUID path 404s on v2026.
    Colors are RGB objects {red, green, blue} with integer channels 0–255.

    ⚠️ v2026 PERSISTENCE (live-verified 2026-05-31 with before/after readback):
    NOT every op that returns 202 actually applies. On this cells/edit endpoint:
      - setCellsProperties (fillColor) — ✅ PERSISTS. Use this for cell fill.
      - setCellsBorders — borders (use NO_BORDER to clear; dedupe per edge).
      - formatCells (font/bold/color) — ⚠️ returns 202 but does NOT persist on v2026.
        For font/bold/size use the SEPARATE platform path: POST /spreadsheets/{ss}/sheets/{sid}/update
        with applyFormats.textFormat (fontFamily/fontSize/bold) — NOT exposed by this tool; call via api_request.
      - valueFormat / dataValidation / conditionalFormat — NO proven REST route on v2026 (UI-only); cells/edit returns 400 MalformedValue for DV/CF.
    A 202 from this tool is acceptance, NOT proof of apply — read the cell properties back to confirm.

    Args:
        table_id: The opaque content-table token (from describe_workbook / get_sheet → table.table)
        operations: Array of format operations. Recommended types:
            - setCellsProperties (PERSISTS): {"type":"setCellsProperties","setCellsProperties":{"ranges":[...],"fillColor":{"red":230,"green":240,"blue":255}}}
            - setCellsBorders: {"type":"setCellsBorders","setCellsBorders":{"ranges":[...],"bottom":{"color":{"red":0,"green":0,"blue":0},"style":"single","weight":1}}}
            - formatCells (⚠️ 202 but no-persist on v2026 — see note): {"type":"formatCells","formatCells":{"ranges":[...],"color":{...},"fontFamily":"Arial","bold":true}}
    """
    s, _, body = api_request(
        f"/content/tables/{table_id}/cells/edit",
        method="POST",
        data={"data": operations},
        tool_name="workiva_format_cells",
    )
    if s in (200, 201, 202):
        # v2026: formatCells returns 202 but does NOT persist. Warn rather than report a false success.
        op_types = {op.get("type") for op in operations if isinstance(op, dict)}
        non_persisting = op_types & {"formatCells"}
        if non_persisting:
            return (
                f"OK ({s}) — but WARNING: {sorted(non_persisting)} return 202 yet do NOT persist on v2026. "
                "Read the cell properties back to confirm; for font/bold/size use platform applyFormats.textFormat."
            )
        return f"OK ({s})"
    return _error(s, body, hint="verify table_id via workiva_spreadsheet_sheets" if s == 404 else "")


@mcp.tool()
async def workiva_edit_table_structure(
    table_id: str,
    operation: Literal[
        "resizeColumns", "resizeRows", "autofitColumns", "autofitRows",
        "hideColumns", "hideRows", "unhideColumns", "unhideRows",
        "mergeCells", "unmergeCells", "insertColumns", "insertRows",
        "deleteColumns", "deleteRows",
    ],
    params: dict,
) -> str:
    """Edit table structure — resize, autofit, hide/unhide, merge, insert/delete rows/columns.

    Args:
        table_id: The table UUID
        operation: One of: resizeColumns, resizeRows, autofitColumns, autofitRows,
                   hideColumns, hideRows, unhideColumns, unhideRows,
                   mergeCells, unmergeCells, insertColumns, insertRows,
                   deleteColumns, deleteRows
        params: Operation-specific parameters including "revision". Examples:
            resizeColumns: {"revision": "...", "columns": [0, 1], "width": 120}
            hideColumns: {"revision": "...", "columns": [2, 3]}
            mergeCells: {"ranges": [{"startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 4}]}
            insertRows: {"revision": "...", "index": 5, "count": 2}

        mergeCells/unmergeCells take a `ranges` LIST of half-open index objects
        {startRowIndex, endRowIndex, startColumnIndex, endColumnIndex} (end-EXCLUSIVE:
        the example above merges A1:D1). A singular `range` is auto-wrapped into `ranges`.
        These ops are ASYNC — the endpoint returns 202 with `operationLocation` in the
        body; poll that URL until status == "completed" to confirm the merge applied.
    """
    _valid_ops = {
        "resizeColumns", "resizeRows", "autofitColumns", "autofitRows",
        "hideColumns", "hideRows", "unhideColumns", "unhideRows",
        "mergeCells", "unmergeCells", "insertColumns", "insertRows",
        "deleteColumns", "deleteRows",
    }
    if operation not in _valid_ops:
        return _error(400, {}, cause=f"invalid operation: {operation}", hint="use one of " + ", ".join(sorted(_valid_ops)))
    operation_params = dict(params)
    operation_params.pop("revision", None)  # v2026 endpoint doesn't require an explicit revision
    # mergeCells/unmergeCells require a `ranges` LIST. A singular `range` produces
    # `mergeCells.range` → 400 MissingRequiredValue target=/mergeCells/ranges. Auto-wrap
    # a single range into the list so callers can pass either shape
    # (proven against a live scratch workbook).
    if operation in {"mergeCells", "unmergeCells"} and "range" in operation_params and "ranges" not in operation_params:
        operation_params["ranges"] = [operation_params.pop("range")]
    payload = {"type": operation, operation: operation_params}

    result = execute_mutation(
        api_request,
        path=f"/content/tables/{table_id}/edit",
        method="POST",
        data=payload,
        tool_name="workiva_edit_table_structure",
        label=operation,
        poll=poll_operation,
    )
    receipt = result.as_dict()
    if result.state is ExecutionState.APPLIED:
        proof = _verify_table_structure(table_id, operation, operation_params)
        receipt["proof"] = proof
        if proof["status"] == "verified":
            receipt["state"] = ExecutionState.VERIFIED
        elif proof["status"] == "verification_failed":
            receipt["state"] = ExecutionState.VERIFICATION_FAILED
    return json.dumps(receipt, default=str)


def _verify_table_structure(table_id: str, operation: str, params: dict[str, Any]) -> dict[str, Any]:
    """Read-only proof for inspectable table operations; fail closed for layout-only work."""
    if operation in {"hideColumns", "unhideColumns", "resizeColumns"}:
        status, _, body = api_request(f"/content/tables/{table_id}/columns", tool_name="workiva_edit_table_structure:proof")
        items = body.get("data", []) if isinstance(body, dict) else []
        wanted = set(params.get("columns", []))
        selected = {item.get("index"): item for item in items if item.get("index") in wanted}
        if status != 200 or set(selected) != wanted:
            return {"status": "verification_failed", "strategy": "column_readback", "detail": "Column metadata could not prove requested targets."}
        if operation == "resizeColumns":
            expected = params.get("width")
            passed = all(item.get("width") == expected for item in selected.values())
        else:
            expected = operation == "hideColumns"
            passed = all(bool(item.get("hidden")) is expected for item in selected.values())
        return {"status": "verified" if passed else "verification_failed", "strategy": "column_readback", "targets": sorted(wanted)}
    if operation in {"hideRows", "unhideRows", "resizeRows"}:
        status, _, body = api_request(f"/content/tables/{table_id}/rows", tool_name="workiva_edit_table_structure:proof")
        items = body.get("data", []) if isinstance(body, dict) else []
        wanted = set(params.get("rows", []))
        selected = {item.get("index"): item for item in items if item.get("index") in wanted}
        if status != 200 or set(selected) != wanted:
            return {"status": "verification_failed", "strategy": "row_readback", "detail": "Row metadata could not prove requested targets."}
        if operation == "resizeRows":
            expected = params.get("height")
            passed = all(item.get("height") == expected for item in selected.values())
        else:
            expected = operation == "hideRows"
            passed = all(bool(item.get("hidden")) is expected for item in selected.values())
        return {"status": "verified" if passed else "verification_failed", "strategy": "row_readback", "targets": sorted(wanted)}
    return {"status": "manual_visual_required", "strategy": "none", "detail": "Rendered layout or merge geometry requires visual/export evidence."}
