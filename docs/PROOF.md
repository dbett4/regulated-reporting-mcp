# How to check the claims

Run every check from a development install with:

```bash
./scripts/proof.sh
```

The script runs lint, checks exact contract-manifest coverage, executes all
credential-free tests, all three offline demos, and the reporting evaluation.
It stops at the first failure and prints `PROOF_PASS` only when everything passes.

| Claim | Run | Pass condition | Limit |
|---|---|---|---|
| The public entry point uses the guarded compact dispatcher | `pytest tests/test_entrypoint_safety.py` | Default server name is `workiva-mcp-compact` | A raw registry remains available behind a named unsafe opt-in for isolated development |
| Every registered implementation has a reviewed contract | `python scripts/generate_tool_contract_manifest.py --check` | Manifest and the 122-tool registry have exact set equality | Coverage does not mean every tool has readback; the contract says `receipt` or `readback` explicitly |
| Unconfirmed mutations are refused before tool execution | `pytest tests/test_tool_contracts.py tests/test_compact_mcp_catalog.py` | Confirmation-class tests and a no-call policy boundary | Confirmation is caller-supplied; it is not a human approval workflow |
| A `readback` promise cannot silently return unverified success | `pytest tests/test_execution_receipts.py` | Missing verification and formula-only readback become `indeterminate` with `proof_satisfied=false` | The mutation may already have reached the upstream API, so the result is not mislabeled as a clean failure |
| The verified cell writer reads its own values back | `pytest tests/test_cells_verified_write.py tests/test_mock_mode.py` | Exact values match or the result is `verification_failed` | Formula text can require a different raw-value oracle and is reported separately |
| 202 responses are not automatically treated as applied | `pytest tests/test_execution.py tests/test_async_operation_location_tools.py` | Terminal polling, missing-operation-location, timeout, and failure cases | Only tools routed through the execution state machine inherit this guarantee |
| OAuth recovery, 429 backoff, pagination, and binary exports work | `pytest tests/test_http_client.py tests/test_reapi.py` | Injected transport assertions | Credential-free transport proof, not a live Workiva availability claim |
| A complete gated write emits a payload-redacted, create-once local receipt | `python -m workiva_mcp.demo` and `pytest tests/test_execution_receipts.py` | Demo verifies gate refusal, retry, polling, and readback; tests verify secret/payload redaction, collision refusal, and exception receipts | FakeWorkiva uses synthetic data; local receipts are not signed or remotely immutable |

## Limits

- Local workbench: `pytest tests/test_workbench.py tests/test_workbench_server.py`
  exercises XLSX safety limits, formula caches, shared Decimal arithmetic,
  evidence-driven reopening, unaffected-review retention, transactional stale
  submission rejection, restart persistence, and HTTP origin/Host boundaries.
  Snapshot preview tests verify fixed-size paging, worksheet selection, Excel
  bounds, cached/raw formula separation, read-only storage, and stale revisions.
  `PYTHONPATH=. python scripts/check_workbench_ui.py --chromium /path/to/chrome`
  runs the optional repeatable visual-mapping browser regression with synthetic
  data and a disposable loopback server (requires Playwright and Chromium).
  Browser checks exercise the two-version demo, mapping confirmation, review,
  filtering, export, errors, and narrow layout. These are local single-user
  controls, not authenticated approval or live Workiva integration. See the
  [workbench walkthrough](WORKBENCH.md).
- Evidence packets: `pytest tests/test_evidence_packet.py` verifies complete
  source export, correction linking, preserved execution states, no additional
  writes or journal updates, and private create-once files. The packet is an
  unreviewed collection of selected evidence, not independent signature proof
  or a resolution decision. See [packet boundaries](EVIDENCE_PACKETS.md).
- Reporting evaluation: `workiva-mcp-evaluate` scores 18 public synthetic cases
  against hand-annotated outcomes and exact amounts. All cases must match and
  writes must remain zero. `pytest tests/test_evaluation.py` injects incorrect
  amounts, missing/duplicate/wrong-rule findings, exceptions, and a write counter
  to verify that failures cannot disappear from the score. This is a deterministic
  regression corpus, not a held-out LLM or live-workspace benchmark. See
  [metric definitions and reproduction](EVALUATION.md).
- Recovery inspection: `pytest tests/test_correction_recovery.py` includes real
  child-process exits at three post-write boundaries, preserved replay blocking,
  and read-only comparisons after restart. `python -m workiva_mcp.approval_demo`
  also prints `RECOVERY_PASS`. Matching current values never upgrade the original
  outcome. Synthetic upstream state is transferred explicitly between test
  processes; this is not a live Workiva crash test or an exactly-once guarantee.
- Signed corrections: `pytest tests/test_corrections.py` and
  `python -m workiva_mcp.approval_demo` exercise signature binding, expiry,
  stale raw/calculated source checks, durable replay prevention, CLI signing,
  readback, and bypass blocking in reviewer-key mode. The private key must stay
  outside the agent environment; preflight is not an atomic conditional write.
  See [approval boundaries](APPROVALS.md).
- Reporting tie-outs: `pytest tests/test_reporting.py` checks known discrepancies,
  signed sums, scale conversion, rounding/tolerance boundaries, read failures,
  and pre-read validation. `python -m workiva_mcp.reporting_demo` must report
  one pass, two failures, one unevaluated rule, and zero writes. This proves
  arithmetic on caller-supplied mappings, not completeness or accounting validity
  of those mappings, and not an atomic snapshot or live Workiva compatibility.
- The raw 122-tool registry is not policy-safe when served directly.
- `proof: receipt` records a confirmed dispatch and the outcome it reported. It
  does not verify upstream state.
- The offline demo is not evidence of Workiva uptime, customer authorization, or a production deployment.
- No client data, client credentials, or client-owned source code are included.
