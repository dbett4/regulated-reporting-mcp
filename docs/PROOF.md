# How to check the claims

Run every check from a development install with:

```bash
./scripts/proof.sh
```

The script runs lint, checks exact contract-manifest coverage, executes all 126
credential-free tests, and runs the full mock demo. It stops at the first
failure and prints `PROOF_PASS` only when everything passes.

| Claim | Run | Pass condition | Limit |
|---|---|---|---|
| The public entry point uses the guarded compact dispatcher | `pytest tests/test_entrypoint_safety.py` | Default server name is `workiva-mcp-compact` | A raw registry remains available behind a named unsafe opt-in for isolated development |
| Every registered implementation has a reviewed contract | `python scripts/generate_tool_contract_manifest.py --check` | Manifest and the 117-tool registry have exact set equality | Coverage does not mean every tool has readback; the contract says `receipt` or `readback` explicitly |
| Unconfirmed mutations are refused before tool execution | `pytest tests/test_tool_contracts.py tests/test_compact_mcp_catalog.py` | Confirmation-class tests and a no-call policy boundary | Confirmation is caller-supplied; it is not a human approval workflow |
| A `readback` promise cannot silently return unverified success | `pytest tests/test_execution_receipts.py` | Missing verification and formula-only readback become `indeterminate` with `proof_satisfied=false` | The mutation may already have reached the upstream API, so the result is not mislabeled as a clean failure |
| The verified cell writer reads its own values back | `pytest tests/test_cells_verified_write.py tests/test_mock_mode.py` | Exact values match or the result is `verification_failed` | Formula text can require a different raw-value oracle and is reported separately |
| 202 responses are not automatically treated as applied | `pytest tests/test_execution.py tests/test_async_operation_location_tools.py` | Terminal polling, missing-operation-location, timeout, and failure cases | Only tools routed through the execution state machine inherit this guarantee |
| OAuth recovery, 429 backoff, pagination, and binary exports work | `pytest tests/test_http_client.py tests/test_reapi.py` | Injected transport assertions | Credential-free transport proof, not a live Workiva availability claim |
| A complete gated write emits a payload-redacted, create-once local receipt | `python -m workiva_mcp.demo` and `pytest tests/test_execution_receipts.py` | Demo verifies gate refusal, retry, polling, and readback; tests verify secret/payload redaction, collision refusal, and exception receipts | FakeWorkiva uses synthetic data; local receipts are not signed or remotely immutable |

## Limits

- The raw 117-tool registry is not policy-safe when served directly.
- `proof: receipt` records a confirmed dispatch and the outcome it reported. It
  does not verify upstream state.
- The offline demo is not evidence of Workiva uptime, customer authorization, or a production deployment.
- No client data, client credentials, or client-owned source code are included.
