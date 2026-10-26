# Reviewer evidence packets

`workiva-mcp-packet` collects current tie-outs and explicitly selected correction
attempts into one JSON or Markdown file. It uses existing read-only tools through
the compact dispatcher and reads complete result artifacts, never truncated previews.
It is a human-operated export CLI, not a new mutation or approval tool.

## Run a credential-free example

From a development install:

```bash
python -c 'import json; from workiva_mcp.reporting_demo import demo_rules; print(json.dumps(demo_rules(), indent=2))' > review-rules.json
WORKIVA_MCP_MOCK=1 workiva-mcp-packet review-rules.json --output review-packet.json
WORKIVA_MCP_MOCK=1 workiva-mcp-packet review-rules.json --format markdown --output review-packet.md
```

Each invocation collects fresh evidence, so collection timestamps and hashes may
differ. The mock example contains one passing rule, two discrepancies, and one
rule without usable evidence. Exit 0 means export succeeded, **not** that the
financial review passed. All packets remain `unreviewed`.

## Include correction attempts

Supply the same deployment configuration used by correction execution:
`WORKIVA_APPROVAL_DB`, `WORKIVA_APPROVAL_AUDIENCE`, `WORKIVA_REGION`, and mock/live
transport selection. The exporter needs no private signing key. Use
`workiva_list_correction_attempts` to discover plan IDs, then link selected IDs
to rules in the input file:

```bash
workiva-mcp-packet review-rules.json \
  --correction cash-tie-out=YOUR_PLAN_ID \
  --output review-with-corrections.json
```

Repeat `--correction` to include multiple attempts. The exporter validates rule
IDs, retained plan digests, deployment bindings, and that each correction target
is a source of its linked rule before making network reads. Legacy claims without
retained plans cannot be linked safely and cause export to fail. A source match
does not establish accounting justification; the operator owns the mapping.

The packet contains:

- Current tie-out rules, statuses, exact amounts, source references, observed
  values, read times, and unavailable-evidence reasons.
- The exact retained correction plan, before/after values, supporting snapshots,
  digest, and local journal state/phase/timestamps.
- A separate recovery observation with current source comparisons and the
  execution state seen during inspection. Matching current values never upgrade
  the original state or mark a finding resolved.
- Collection timestamps, transport, region, scope, warnings, and a SHA-256 hash
  of canonical JSON `evidence` (sorted keys, compact separators, ASCII escaping).

The journal records a consumed claim but does **not** retain the reviewer signature.
The export labels that limitation rather than pretending to independently verify
approval. No signature, approval identity, or resolution decision is fabricated.
The packet itself is not signed; compare hashes only against a trusted copy.

## Handle the export as financial data

Packets and intermediate result artifacts contain financial values and identifiers;
they are not payload-redacted receipts. The output is atomically created with
owner-only permissions on POSIX, and existing files or symlinks are never replaced.
Result artifacts use `WORKIVA_MCP_RESULT_DIR` or the normal local result store.
Apply your organization's access and retention policies to both locations.

Collection is sequential, not an atomic workbook/journal snapshot. A running
executor may advance between the saved journal record and recovery inspection;
both observations remain separate. Coordinate pending operations before making
new corrections. The export performs no upstream writes, polling, approval
consumption, mutation retries, rollback, or journal updates. Normal transport
retry handling still applies to reads. It does write local files.

Only the supplied rules and selected attempts are covered. It is not a complete
workspace history, audit opinion, live API certification, or proof of compliance.
Markdown keeps untrusted descriptions and values inside escaped JSON code blocks.

## Verify the behavior

```bash
pytest tests/test_evidence_packet.py
./scripts/proof.sh
```

Tests cover full-artifact collection beyond preview limits, verified versus
indeterminate attempts with matching current values, no additional writes,
byte-for-byte journal preservation, invalid links before reads, sensitive-error
suppression, Markdown evidence roundtripping, and private, non-overwriting exports.
