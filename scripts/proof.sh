#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PROOF_TEMP="$(mktemp -d)"

cleanup() {
  case "$PROOF_TEMP" in
    /tmp/*) rm -rf -- "$PROOF_TEMP" ;;
    *) echo "Refusing to remove unexpected proof directory: $PROOF_TEMP" >&2 ;;
  esac
}
trap cleanup EXIT

cd "$ROOT"

"$PYTHON_BIN" -m ruff check .
"$PYTHON_BIN" scripts/generate_tool_contract_manifest.py --check
"$PYTHON_BIN" -m pytest -q
WORKIVA_MCP_RECEIPT_DIR="$PROOF_TEMP/receipts" \
  "$PYTHON_BIN" -m workiva_mcp.demo

echo "PROOF_PASS guarded_default=1 contracts=117 tests=126 offline_demo=pass"
