# Reviewer-approved cell corrections

This workflow supports **one literal numeric cell correction per approval**.
It does not approve arbitrary API tools or replace formulas with literals.

```text
MCP preview → exact proposal → offline reviewer CLI signs → MCP execute
                                                        ↓
                           verify signature → consume approval → read sources
                                                        ↓
                                      unchanged → write → verify readback
```

## Try the credential-free demonstration

```bash
python -m workiva_mcp.approval_demo
```

The demo uses synthetic Riverton data and ephemeral keys, journal and receipts.
It **simulates** a reviewer; it is not evidence of an actual human approval.
An unsigned proposal is blocked. An intervening operator edit invalidates a
signed proposal. A new signed proposal corrects General Fund cash to 4,195,000,
readback verifies it, and replay is refused. Rerunning the tie-outs passes cash
without hiding the unrelated rollforward discrepancy or missing support.

The final scenario simulates interruption after 202 acceptance. Recovery finds
the consumed attempt from the journal, reads its saved plan and operation
location, and observes that the target matches the approved value. The original
execution remains `indeterminate`, and no additional write is issued.

## Configure separate reviewer and server environments

Install the updated package with `pip install -e .` to get `workiva-mcp-review`.
`python -m workiva_mcp.reviewer` is equivalent.

**On the reviewer's machine, outside the agent/server environment:**

```bash
workiva-mcp-review keygen --private-key reviewer.key --public-key reviewer.pub
```

The private key is unencrypted hex-encoded Ed25519 key material, created with
mode 0600. Protect its directory and backups. Do not upload it to the agent,
put it in MCP environment variables, or run the real approval CLI through the
agent's shell. A separate command alone is not a security boundary if the agent
can read its key or invoke it with that key. No signing function is an MCP tool.

Copy **only the public key** to an operator-controlled, read-only server path.
Configure the server process:

```bash
export WORKIVA_REVIEW_PUBLIC_KEY=/restricted/reviewer.pub
export WORKIVA_APPROVAL_AUDIENCE=riverton-reporting-review
export WORKIVA_APPROVAL_DB=/durable/workiva/approvals.sqlite3
workiva-mcp
```

Choose a unique audience for each deployment/credential boundary. Keep it
stable across restarts that share a journal. Also configure ordinary Workiva
credentials, or mock mode for a lab. The approved plan binds the audience,
Workiva region, and mock/live transport, in addition to its cell contents.

**Setting `WORKIVA_REVIEW_PUBLIC_KEY` enables reviewer-key mode:** the compact
dispatcher rejects every non-read contract except `workiva_execute_correction`,
even if a caller supplies all confirmation booleans. The package entrypoint also
refuses raw-catalog mode. Without that setting, legacy confirmation behavior
remains available; the correction executor itself still refuses to run unsigned.

## Propose, review, and execute

Call `workiva_preview_correction` through `workiva_call_tool`:

```json
{
  "name": "workiva_preview_correction",
  "arguments": {
    "target": {"spreadsheet_id": "book-id", "sheet_id": "statement-id", "cell": "B4"},
    "new_value": "4195000",
    "reason": "Resolve cash discrepancy against reviewed supporting schedule",
    "supporting": [
      {"spreadsheet_id": "book-id", "sheet_id": "support-id", "cell": "B2"}
    ]
  }
}
```

The tool captures explicit raw and displayed values. Missing/ambiguous cells
are rejected, not treated as blanks. The proposal expires 15 minutes after
creation. Supporting references are optional (maximum 50); the reviewer must
ensure the chosen evidence is sufficient. Only selected cells are protected
by stale-source checks. `new_value` is a plain decimal string in the cell's
actual units, without commas, currency symbols or formulas.

Save the complete returned object (or just its `plan` object) as `proposal.json`
and transfer it to the reviewer. If compact output is shortened, retrieve the
full result artifact rather than copying the preview. These files contain
financial values and need restricted storage/transport.

**The reviewer runs, independently:**

```bash
workiva-mcp-review approve proposal.json --private-key reviewer.key --output approved.json
```

The CLI displays the full plan with escaped JSON and requires typing
`APPROVE <displayed digest>`. It signs the exact canonical plan, including
before/after values, reason, supporting evidence, target, ID, expiry and
deployment. This digest is deliberately different from the redacted argument
digest used in general-purpose receipts.

Return `approved.json` to the MCP client. Pass its two fields, `plan` and
`signature`, unchanged as the `arguments` to `workiva_execute_correction` via
`workiva_call_tool`, with `confirm_write=true`. The boolean remains a dispatch
acknowledgment; only the external signature grants approval for this workflow.

## Inspect an interrupted attempt without retrying it

Call these through the compact dispatcher's `workiva_call_tool`. Neither needs
write confirmation or a fresh signature:

```json
{"name": "workiva_list_correction_attempts", "arguments": {"limit": 25}}
```

The list returns metadata for the newest claims in the configured local journal,
including `plan_id`, execution state, last phase, and timestamps. It does not
return financial values. Inspect a returned ID with:

```json
{"name": "workiva_inspect_correction", "arguments": {"plan_id": "<plan_id from the list>"}}
```

Inspection loads the retained plan and checks its digest and deployment binding
before any network read. It works after approval expiry because it does not
authorize a write. The target observation is one of:

| Observation | Meaning—not an execution verdict |
|---|---|
| `matches_approved` | Raw and displayed values currently match the proposed literal |
| `matches_before` | Current raw and displayed values match the before-snapshot |
| `matches_before_and_approved` | The plan proposed no observable change |
| `diverged` | Current state matches neither snapshot nor proposed literal |
| `unavailable` | The source could not be read; no comparison is made |

Supporting cells are checked separately as unchanged, changed, or unavailable.
Reports contain the before/current evidence, operation location if retained,
original execution state, and `retry_allowed: false`. Inspection never changes
the journal, releases approval, polls a saved URL, or upgrades the original
attempt to verified. Even `matches_before` does **not** prove that nothing was
written: an operation could be pending, or someone could have restored the value.

Coordinate or stop the original worker and resolve any pending upstream operation
before considering a new correction. A nonterminal phase does not prove a crash.
Recovery evidence is a current, non-atomic observation, not proof of causation.

### Journal storage and upgrades

New claims persist the **full financial plan** before preflight, then checkpoint
`submitting` before network I/O, `polling` after receiving an operation location,
and `readback` before verification. Final outcomes have phase `finished`.
If the process disappears before receiving/saving the operation location, the
saved plan still supports targeted inspection, but that location may be absent.

The next journal write adds nullable evidence/timestamp columns to older databases
inside a transaction. Existing consumed IDs are preserved. Read-only tools do not
create or migrate databases. Older claims without a retained plan return
`recovery_unavailable`; the tool does not reconstruct evidence from a digest.
Back up and authorize upgrades before deploying to shared storage. Treat the
database, its sidecar files, and backups as sensitive financial data, not redacted
receipts. Removing or restoring journal claims defeats replay prevention.

## Execution and recovery limits

- Signature/configuration errors and expired plans cause no source reads or writes.
- A valid approval is consumed durably **before** source preflight. Stale sources
  or preflight failures consume it without writing; make and review a new proposal.
- SQLite's unique plan ID claim rejects concurrent/restarted attempts sharing
  the same durable database. Do not delete, restore an old copy of, or independently
  replicate the journal: that defeats replay prevention. This is not exactly-once
  upstream execution, and it does not lock different proposals for the same cell.
- A crash after consumption leaves `indeterminate` in the journal. The plan cannot
  be retried. Use read-only inspection and existing receipts before preparing any
  new correction. Automatic resume, rollback and a graphical recovery UI are not implemented.
- Target and supporting reads are not an atomic snapshot or upstream conditional
  write. An operator can still edit between preflight and submission. Coordinate
  edits during execution; readback alone does not solve that race.
- A completed write must match both raw and displayed literal text to be
  `verified`. Normalized formatting can conservatively produce
  `verification_failed`. Submission/polling/readback uncertainty produces
  `indeterminate`, with no automatic repair. Normal HTTP 401/429 handling remains.
- A signature proves possession of the configured key, not accounting correctness,
  a person's identity, or a compliant segregation-of-duties deployment. Server
  filesystem/configuration access and Workiva credentials remain trust boundaries.
- The approval is signed; the local execution receipt and journal are not signed
  audit logs. Receipts redact financial payloads. The journal and full plan/result
  artifacts do not. Digest checks detect inconsistency, not an attacker editing
  both the plan and digest in the local database.

Run `pytest tests/test_corrections.py` for tampering, deployment binding, expiry,
staleness, replay, concurrency, process restart, CLI signing, failure states,
and bypass prevention. All checks are offline; live Workiva behavior remains
unverified.

`pytest tests/test_correction_recovery.py` additionally terminates real child
processes after a simulated upstream write (before response handling, during
polling, and before readback), then inspects through the compact dispatcher with
zero additional writes. FakeWorkiva is process-local, so these tests transfer
only synthetic upstream cell state between processes; execution evidence is
loaded from the durable journal. They also cover legacy upgrades, corrupt plans,
expired approvals, unavailable reads, and preservation of original outcomes.
