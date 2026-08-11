#!/usr/bin/env python3
"""Regenerate tool_contract_manifest.json to exactly cover the registered catalog.

Contract loading validates full coverage (every registered tool must carry a
reviewed contract, and mutating contracts must require confirmation), so the
manifest must be regenerated whenever the tool catalog changes.

The generator reads the live FastMCP registry of workiva_mcp.server (restricted
to the modules server.py imports) and carries forward each tool's existing
reviewed contract from the current manifest. A registered tool with no existing
contract entry fails the run — a new tool needs an explicit human-reviewed
contract, not an inferred one.

Usage:
    python scripts/generate_tool_contract_manifest.py [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

MANIFEST_PATH = REPO_ROOT / "workiva_mcp" / "tool_contract_manifest.json"


def registered_tool_names() -> list[str]:
    import workiva_mcp.server as server
    from workiva_mcp.compact_tools import _registered_module_names

    allowed = {f"workiva_mcp.tools.{stem}" for stem in _registered_module_names()}
    names = [
        tool.name
        for tool in server.mcp._tool_manager.list_tools()
        if getattr(getattr(tool, "fn", None), "__module__", "") in allowed
    ]
    return sorted(names)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify only; do not rewrite")
    args = parser.parse_args()

    current = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    names = registered_tool_names()

    missing = [name for name in names if name not in current]
    if missing:
        print("ERROR: registered tools without a reviewed contract entry:")
        for name in missing:
            print(f"  - {name}")
        print("Add explicit contracts for these tools, then re-run.")
        return 1

    regenerated = {name: current[name] for name in names}
    stale = sorted(set(current) - set(names))

    if args.check:
        if stale:
            print(f"STALE: manifest has {len(stale)} entries for unregistered tools: {stale[:10]}")
            return 1
        print(f"OK: manifest exactly covers the {len(names)}-tool registered catalog.")
        return 0

    MANIFEST_PATH.write_text(
        json.dumps(regenerated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {MANIFEST_PATH.name}: {len(regenerated)} contracts "
          f"({len(stale)} stale entries removed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
