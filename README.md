# regulated-reporting-mcp

[![CI](https://github.com/dbett4/regulated-reporting-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/dbett4/regulated-reporting-mcp/actions/workflows/ci.yml)

An MCP server for a regulated reporting platform (Workiva), extracted from production tooling used on live government financial reports — with policy-gated writes, receipt-backed verification, and a credential-free mock mode you can run in under a minute.

![Mock-mode demo: list, read, gated write, verified readback, receipt](docs/demo.gif)

```
$ pip install -e . && workiva-mcp-demo

=== 5. Attempt the write WITHOUT confirm_write (contract blocks it) ===
{"error": "Tool requires confirmation: workiva_write_verified",
 "requires_confirmation": true, "risk": "write", ...}

=== 6. Re-issue with confirm_write=true (verified write + readback) ===
{"status": "verified", "written_cells": 3, "mismatches": [],
 "state": "verified", "receipt_uri": "workiva-receipt://2026...-workiva-write-verified-..."}
```

## Why this exists

Financial statements filed by governments and public companies are produced on platforms like Workiva, and agents are increasingly asked to write into them. A wrong number in a statement workbook is not a UI glitch — it is a misstatement. So this server is built around one idea: **an accepted API call is not an applied change, and an applied change is not a verified one.** Every mutating tool is policy-gated, every 202 is polled to a terminal state, every write is read back, and every mutation leaves a redacted, immutable receipt.

## The three patterns this demonstrates

1. **OAuth2 client-credentials + token lifecycle** — stdlib-only auth with a module token cache, expiry-window refresh, and 401 clear-and-retry-once (`auth.py`, `http_client.py`).
2. **Async job polling and pagination at production scale** — 202 + `Operation-Location` polling (header and body variants), `@nextLink`/`next_url` pagination, parallel row-range cell fan-out, 429 backoff ladders with `Retry-After`, and binary-safe export handling that never UTF-8-mangles an xlsx/PDF body (`http_client.py`, `reapi/`).
3. **Policy-manifest confirm-before-write with receipts** — every registered tool carries a reviewed contract (`effect` / `confirmation` / `proof`) validated at load; the compact dispatcher fail-closes on tools without contracts; writes produce secret-redacted, write-once receipts; verified-write tools read their own changes back and report mismatches instead of retrying blind (`tool_contracts.py`, `compact_server.py`, `execution.py`, `receipt_store.py`).

## Architecture

```mermaid
flowchart LR
    C[MCP client] --> CS["compact server (3 tools):<br/>search / call / batch"]
    C --> FS["full server<br/>(117-tool catalog)"]
    CS --> TC["tool contracts<br/>effect · confirmation · proof<br/>(fail-closed)"]
    TC --> T["tool families:<br/>cells · spreadsheets · documents ·<br/>files · linking · wdata · chains ·<br/>tasks · presentations · admin"]
    FS --> T
    T --> EX["execution state machine<br/>202 ≠ applied ≠ verified"]
    EX --> H["http client<br/>OAuth2 · 401 retry · 429 backoff ·<br/>202 polling · pagination"]
    H --> W[(Workiva API)]
    H -. WORKIVA_MCP_MOCK=1 .-> M[(FakeWorkiva<br/>in-memory)]
    EX --> R["redacted receipts +<br/>result artifacts<br/>(MCP resources)"]
```

Key design decisions:

- **A 202 without an operation location is `indeterminate`, never success.** The mutation state machine (`execution.py`) is injected with its request/poll functions, so it unit-tests with zero credentials.
- **The contract manifest is the runtime authority.** Name-based risk classification (leading-verb, not any-token) exists only as a drift diagnostic; dispatch refuses any tool without a reviewed contract, and `scripts/generate_tool_contract_manifest.py --check` keeps the manifest exactly covering the registered catalog.
- **The compact server keeps model context small.** Three tools front the full catalog (~97% fewer tools, ~90%+ fewer listing bytes, measured by `catalog_benchmark()`); large results are stored as MCP resources (`workiva-result://…`) instead of flooding chat.
- **`reapi/` is a pure-offline hardening layer** — typed 4xx/5xx error hierarchy, retry/poll policies, and a receipt validator with zero I/O and zero auth, grounded line-by-line in observed live API behavior.

## Quickstart (no credentials needed)

```bash
git clone https://github.com/dbett4/regulated-reporting-mcp
cd regulated-reporting-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

workiva-mcp-demo        # end-to-end mock demo: list → read → gated write → verified readback
pytest                  # 116 credential-free tests
```

*Repo URL resolves once this repository is published.*

The demo runs against **FakeWorkiva**, an in-memory mock transport serving a synthetic "City of Riverton" ACFR-shaped workbook. It exercises the real code paths — contract gate refusal, one-shot 429 backoff, 202 + `Operation-Location` polling, readback verification, and the redacted receipt — entirely offline.

The demo's verified write stores its receipt under `~/.workiva_mcp/receipts/` (the same
write-once receipt store used against a real workspace). Set `WORKIVA_MCP_RECEIPT_DIR` to
redirect receipts to any other directory.

### Use it from Claude (mock mode)

```json
{
  "mcpServers": {
    "workiva": {
      "command": "workiva-mcp-compact",
      "env": { "WORKIVA_MCP_MOCK": "1" }
    }
  }
}
```

### Against a real Workiva workspace

Provide OAuth2 client credentials (created in Workiva under an org API grant) and drop the mock flag:

```bash
export WORKIVA_CLIENT_ID=...
export WORKIVA_CLIENT_SECRET=...
export WORKIVA_REGION=us        # us | eu | apac
workiva-mcp                     # full 117-tool catalog
workiva-mcp-compact             # 3-tool compact facade (recommended)
```

Credentials can also live in a `.env` file next to the package or pointed at with `WORKIVA_ENV_FILE`.

## Testing

`pytest` runs 116 tests, all credential-free: transport behaviors are covered with injected/monkeypatched fakes, the offline `reapi` layer needs no I/O by design, and `tests/test_mock_mode.py` drives the real tool stack end-to-end through FakeWorkiva.

## Status

Extracted from production tooling used on live government financial reports (annual comprehensive financial reports built and tied out on Workiva). The transport, governance, and verification layers ship here intact; engagement-management layers that ran alongside them in production are out of scope for this repository. All workbook content and identifiers in this repository are synthetic.

## License

MIT — see [LICENSE](LICENSE).

---

Built by [Dave Bettner](https://davebettner.com).
