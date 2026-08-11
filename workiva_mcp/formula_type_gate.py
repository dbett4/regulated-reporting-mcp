"""formula_type_gate.py — post-write formula-type readback gate.

The failure class this closes (observed twice on live production statement
workbooks): an automated writer satisfies a NUMERIC tie-out by writing literal
values into cells that should hold formulas. The values API cannot detect this
(it returns computed values either way) — the content API can: a formula
cell's `rawValue` is its '=' formula text.

Usage (post-write, inside a write script):

    from workiva_mcp.formula_type_gate import assert_formula_types
    assert_formula_types(ss_id, sheet_id, ["F17", "F18", "F50"],
                         exceptions={"F65"})   # raises FormulaTypeViolation

Or standalone census / verifier CLI:

    python -m workiva_mcp.formula_type_gate --ss <ss_id> --sheet <sheet_id> \
        --cells F7:F66 [--exceptions F65,F53] [--census]

Design notes:
- On violation this RAISES with a receipt; it does NOT auto-restore. A
  before-snapshot taken through the values API holds COMPUTED values, so a
  blind restore would itself write literals over formulas. Rollback belongs to
  the calling script's intended-payload artifact.
- `--census` mode reports literals without exiting 1 for blanks/labels — only
  NUMERIC literals count as violations either way. Text cells (labels) and empty
  cells never violate.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Iterable

from workiva_mcp.http_client import api_request


class FormulaTypeViolation(RuntimeError):
    def __init__(self, violations: list[dict]):
        self.violations = violations
        lines = ", ".join(f"{v['cell']}={v['rawValue']!r}" for v in violations[:8])
        more = "" if len(violations) <= 8 else f" (+{len(violations) - 8} more)"
        super().__init__(
            f"FormulaTypeGate: {len(violations)} numeric literal(s) in face cells: "
            f"{lines}{more}. Fix the write (formula-driven) or register each cell "
            f"as an accepted variance with owner + expiry — do not bypass."
        )


_CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _expand(cells: Iterable[str]) -> set[str]:
    """Expand A1 refs and simple ranges (F7:F66) into a cell set."""
    out: set[str] = set()
    for token in cells:
        token = token.strip()
        if ":" in token:
            a, b = token.split(":", 1)
            ma, mb = _CELL_RE.match(a), _CELL_RE.match(b)
            if not (ma and mb):
                raise ValueError(f"bad range {token!r}")
            c1, r1 = ma.group(1), int(ma.group(2))
            c2, r2 = mb.group(1), int(mb.group(2))
            if c1 != c2:
                raise ValueError(f"only single-column ranges supported, got {token!r}")
            for r in range(min(r1, r2), max(r1, r2) + 1):
                out.add(f"{c1}{r}")
        else:
            if not _CELL_RE.match(token):
                raise ValueError(f"bad cell {token!r}")
            out.add(token)
    return out


def _sheet_table_id(ss_id: str, sheet_id: str) -> str:
    status, _h, body = api_request(
        f"/spreadsheets/{ss_id}/sheets/{sheet_id}",
        api="content", timeout=90, tool_name="formula_type_gate_sheet_detail",
    )
    if status >= 400 or not isinstance(body, dict):
        raise RuntimeError(f"sheet detail failed sheet={sheet_id} status={status}")
    table = body.get("table") or {}
    tid = str(table.get("table") or table.get("id") or "")
    if not tid:
        raise RuntimeError(f"no content table id for sheet {sheet_id}")
    return tid


def read_cell_types(ss_id: str, sheet_id: str, cells: Iterable[str]) -> dict[str, dict]:
    """Return {A1: {"rawValue":…, "is_formula":bool, "kind":formula|number|text|empty}}."""
    want = _expand(cells)
    tid = _sheet_table_id(ss_id, sheet_id)
    status, _h, body = api_request(
        f"/content/tables/{tid}/cells?$maxcellsperpage=50000",
        api="content", timeout=120, tool_name="formula_type_gate_cells",
    )
    if status >= 400 or not isinstance(body, dict):
        raise RuntimeError(f"cells read failed table={tid} status={status}")
    rows = body.get("data") if isinstance(body.get("data"), list) else []
    result: dict[str, dict] = {}
    for row_idx, row_obj in enumerate(rows, start=1):
        row_cells = row_obj.get("cells") if isinstance(row_obj, dict) else None
        if not isinstance(row_cells, list):
            continue
        for col_idx, cell in enumerate(row_cells, start=1):
            a1 = f"{_col_letter(col_idx)}{row_idx}"
            if a1 not in want:
                continue
            raw = cell.get("rawValue") if isinstance(cell, dict) else None
            raw_s = "" if raw is None else str(raw)
            if raw_s.startswith("="):
                kind = "formula"
            elif raw_s.strip() == "":
                kind = "empty"
            else:
                try:
                    float(str(raw_s).replace(",", ""))
                    kind = "number"
                except ValueError:
                    kind = "text"
            result[a1] = {"rawValue": raw_s, "is_formula": kind == "formula", "kind": kind}
    for a1 in want - set(result):
        result[a1] = {"rawValue": "", "is_formula": False, "kind": "empty"}
    return result


def assert_formula_types(ss_id: str, sheet_id: str, cells: Iterable[str],
                         exceptions: set[str] | None = None) -> dict[str, dict]:
    """Raise FormulaTypeViolation if any requested cell holds a NUMERIC literal.

    Text labels and empty cells pass (they are not face values). Cells in
    `exceptions` (registered accepted variances / Override rows) pass but are
    still reported in the returned census.
    """
    exceptions = exceptions or set()
    census = read_cell_types(ss_id, sheet_id, cells)
    violations = [
        {"cell": a1, **info}
        for a1, info in sorted(census.items(), key=lambda kv: int(_CELL_RE.match(kv[0]).group(2)))
        if info["kind"] == "number" and a1 not in exceptions
    ]
    if violations:
        raise FormulaTypeViolation(violations)
    return census


def main() -> int:
    ap = argparse.ArgumentParser(description="Formula-type readback gate / census")
    ap.add_argument("--ss", required=True)
    ap.add_argument("--sheet", required=True, help="sheet id")
    ap.add_argument("--cells", required=True, help="comma-separated A1 refs / single-col ranges")
    ap.add_argument("--exceptions", default="", help="comma-separated A1 refs that may stay literal")
    ap.add_argument("--census", action="store_true",
                    help="report-only: print the census, exit 0 even with literals")
    args = ap.parse_args()
    cells = [c for c in args.cells.split(",") if c.strip()]
    exceptions = {c.strip() for c in args.exceptions.split(",") if c.strip()}
    try:
        census = assert_formula_types(args.ss, args.sheet, cells, exceptions)
        print(json.dumps({"status": "PASS", "census": census}, indent=2))
        return 0
    except FormulaTypeViolation as exc:
        census = read_cell_types(args.ss, args.sheet, cells)
        print(json.dumps({"status": "CENSUS" if args.census else "FAIL",
                          "violations": exc.violations,
                          "census": census}, indent=2))
        return 0 if args.census else 1


if __name__ == "__main__":
    sys.exit(main())
