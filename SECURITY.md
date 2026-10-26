# Security notes

## Supported surface

`workiva-mcp` and `python -m workiva_mcp` default to the three-tool compact
dispatcher. That is the supported surface for autonomous callers. Before
dispatch it checks the tool's reviewed contract, requires the right
confirmation, redacts receipt targets, and refuses to call missing readback a
success.

The raw implementation registry is intentionally not the default. Serving it requires both `WORKIVA_MCP_CATALOG_MODE=full` and `WORKIVA_MCP_ALLOW_UNGATED_FULL_CATALOG=1`. Direct calls there bypass the confirmation and receipt middleware.

For independent approval of single-cell corrections, configure
`WORKIVA_REVIEW_PUBLIC_KEY`, `WORKIVA_APPROVAL_AUDIENCE`, and durable
`WORKIVA_APPROVAL_DB` storage. Reviewer-key mode blocks all other non-read
contracts in compact dispatch and disables the raw entrypoint. The offline
reviewer signs exact plans with an Ed25519 private key **outside the agent/server
environment**; the server verifies signatures using only the public key.
Without this configuration, the legacy booleans remain caller confirmation,
not independent approval. See [setup and limitations](docs/APPROVALS.md).

## What the controls cover

| Asset | Control | Residual risk |
|---|---|---|
| OAuth client secret and access token | Environment or `.env`; secret-shaped receipt fields are redacted; 401 clears the token and retries once | A compromised host or process can still read its own environment |
| Workiva state | Contract confirmation, distinct execution states, and tool-specific readback where promised | Caller confirmation is not independent human authorization; many tools produce a receipt but do not read state back |
| Mutation outcome | `accepted`, `applied_unverified`, `verified`, `verification_failed`, and `indeterminate` are kept distinct | Upstream timeouts can leave reality indeterminate and require an operator readback |
| Receipts | Atomic create-once file creation, secret/payload redaction, argument digest, and correlation ID | Local files are not signed, remote, or tamper-evident after creation; identifiers and structural targets remain visible |
| Result artifacts | Stored outside model context and addressed by opaque IDs | Host filesystem access remains the security boundary |
| Approved corrections | Exact-plan signatures, 15-minute expiry, deployment binding, selected-source preflight and durable single-use claims | Agent access to the signing key, server configuration, journal or upstream credentials defeats the boundary; preflight cannot eliminate concurrent-write races |

## Deployment guidance

- The optional `workiva-review-workbench` is a loopback-only, single-user XLSX
  application, not an authenticated approval service. Local callers and any
  explicitly allowed reverse proxy share one workspace. Restrict access; never
  expose it publicly without a separate authorization design. Names in review
  notes are unauthenticated labels and cannot authorize Workiva corrections.
  The SQLite database retains original workbook bytes, cell snapshots and notes;
  protect it and browser-downloaded exports as financial data. Imports have ZIP,
  XML and iteration bounds, but dependency parsers are not a malware sandbox.
  See [workbench limits](docs/WORKBENCH.md).
- Use a dedicated least-privilege Workiva OAuth grant.
- Run the compact surface in an isolated process or container with only required network access.
- Mount receipt storage on a restricted, durable volume. Outside a lab, forward
  logs and receipts to a store controlled separately from this process.
- Treat `indeterminate` and `verification_failed` as stop states. Read back before any retry.
- Never expose raw full-catalog mode to an autonomous or untrusted caller.
- Protect the reviewer public-key path and server configuration from agent writes.
  All executors for one approval audience must share the same durable claim DB;
  deleting or restoring old journal data can permit replay. Private signing keys,
  signed plan files, and full result artifacts need restricted storage. Approval
  signatures do not make execution receipts or the local journal tamper-evident.
- The correction journal now retains full before/after plans and selected source
  evidence for recovery. Restrict the database, sidecars, and backups as financial
  data. Read-only recovery checks do not release approvals or establish that a
  past write caused currently matching values. An original worker or upstream
  operation may still be running; resolve that before preparing another write.
- Journal writes add evidence columns to legacy databases without deleting
  consumed IDs. Authorize and back up shared-storage upgrades before deployment.
  Legacy claims without saved plans remain blocked from replay but cannot supply
  automatic source comparisons.

## Reporting

Do not open a public issue containing credentials, workspace identifiers, or customer data. Send a minimal reproduction to the repository owner through the contact on [davebettner.com](https://davebettner.com).
