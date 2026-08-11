# Security and trust boundaries

## Supported surface

`workiva-mcp` and `python -m workiva_mcp` default to the three-tool compact dispatcher. That is the supported surface for autonomous callers. It validates a reviewed contract before dispatch, requires the contract's confirmation class, redacts receipt targets, and refuses to represent missing readback as verified success.

The raw implementation registry is intentionally not the default. Serving it requires both `WORKIVA_MCP_CATALOG_MODE=full` and `WORKIVA_MCP_ALLOW_UNGATED_FULL_CATALOG=1`. Direct calls there bypass the confirmation and receipt middleware.

## Assets and boundaries

| Asset | Control | Residual risk |
|---|---|---|
| OAuth client secret and access token | Environment or `.env`; secret-shaped receipt fields are redacted; 401 clears the token and retries once | A compromised host or process can still read its own environment |
| Workiva state | Contract confirmation, typed execution state, and tool-specific readback where promised | Caller confirmation is not independent human authorization; many tools provide receipt proof only |
| Mutation outcome | `accepted`, `applied_unverified`, `verified`, `verification_failed`, and `indeterminate` are kept distinct | Upstream timeouts can leave reality indeterminate and require an operator readback |
| Receipts | Atomic create-once file creation, secret/payload redaction, argument digest, and correlation ID | Local files are not signed, remote, or tamper-evident after creation; identifiers and structural targets remain visible |
| Result artifacts | Stored outside model context and addressed by opaque IDs | Host filesystem access remains the security boundary |

## Deployment guidance

- Use a dedicated least-privilege Workiva OAuth grant.
- Run the compact surface in an isolated process or container with only required network access.
- Mount receipt storage on a restricted, durable volume; forward logs and receipts to an independently controlled store when operating beyond a lab.
- Treat `indeterminate` and `verification_failed` as stop states. Read back before any retry.
- Never expose raw full-catalog mode to an autonomous or untrusted caller.

## Reporting

Do not open a public issue containing credentials, workspace identifiers, or customer data. Send a minimal reproduction to the repository owner through the contact on [davebettner.com](https://davebettner.com).
