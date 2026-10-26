# Read-only financial reporting tie-outs

Run `python -m workiva_mcp.reporting_demo` after installing the project. It
forces mock mode, needs no credentials, and runs the compact dispatcher against
the fictional Riverton workbook. Expected results:

| Rule | Expected outcome | Evidence |
|---|---|---|
| Total assets | passed | 12,500,000 + 1,250,000 + 48,750,000 = 62,500,000 |
| General Fund cash | failed | Reported 4,200,000 versus supporting 4,195,000; difference +5,000 |
| Balance rollforward | failed | 800 thousand + 30,000 − 10,000 = 820,000 versus reported 825,000 |
| Missing support | not_evaluated | Supporting cell is blank; no difference is calculated |

The demo prints JSON findings with workbook/sheet IDs, A1 cell addresses, raw
and normalized values, read timestamps, the full rule, and its evaluation
version. These are source references, not authenticated Workiva browser links.
Every finding starts `unreviewed`; passing arithmetic is not reviewer sign-off.

## Use from an MCP client

Discover the schema with `workiva_search_tools(query="tie-out", detail="schema")`.
Call `workiva_call_tool` with the following shape (replace the example IDs with
your mapped workbook and sheet IDs). No write confirmation is needed:

```json
{
  "name": "workiva_check_tie_outs",
  "arguments": {
    "rules": [{
      "id": "cash-support",
      "description": "General Fund cash agrees to supporting schedule",
      "unit": "USD",
      "decimal_places": 2,
      "tolerance": "0.01",
      "reported": {
        "spreadsheet_id": "book-id",
        "sheet_id": "statement-id",
        "cell": "B4"
      },
      "supporting": [{
        "spreadsheet_id": "book-id",
        "sheet_id": "support-id",
        "cell": "C7",
        "scale": "thousands",
        "sign": "add"
      }]
    }]
  }
}
```

Use multiple supporting cells for totals, and `sign: "subtract"` for reductions
in a rollforward. Cross-workbook references are allowed. Each rule needs a unique
ID, one reported cell, and 1–50 supporting cells; a call accepts 1–100 rules.
Both reported and supporting references accept `units`, `thousands`, or `millions`.

## Arithmetic contract: reporting.tie_out.v1

1. Parse decimal values, optionally with grouping commas or accounting parentheses.
2. Convert each value to base units using its explicit scale.
3. Sum signed supporting values without intermediate rounding.
4. Round the reported amount and supporting sum separately with HALF_UP to
   `decimal_places` (0–6, default 2).
5. Calculate `difference = reported − expected`. Pass when its absolute value
   is **less than or equal to** the nonnegative tolerance, expressed in base units.

For example, 0.005 + 0.005 becomes 0.01, not 0.02. No binary floating-point
arithmetic is used after parsing the API response. If an upstream response
already encodes a rounded floating-point number, lost precision cannot be restored.

Blank cells, dashes, raw formulas, currency symbols, percentages, malformed
numbers and nonfinite numbers are not guessed. An unavailable or invalid cell
makes that rule `not_evaluated`; other rules continue. Numerical input supports
up to 30 integer digits and 12 fractional digits. No expression evaluation,
currency conversion, automatic correction, or model-generated math occurs.

## Boundaries

- The reviewer owns cell mappings, signs, scales, currency, period, and tolerance.
  A passing check does not establish the mapping's accounting validity or coverage.
- Calculated formula values can participate in a check; formula text, freshness,
  dependencies, and correctness are not verified.
- Each unique cell is read once per call. Reads are not an atomic snapshot and
  concurrent edits can make cross-cell comparisons inconsistent. Use stable
  reporting inputs. The separate [correction workflow](APPROVALS.md) binds
  selected raw/display values to approval, but still cannot make reads atomic.
- Findings are sensitive financial output. Compact mode may store large reports
  in local result artifacts. Those artifacts are not redacted mutation receipts,
  signed audit evidence, or a separately authorized review record.
- The synthetic demo and tests are offline evidence, not live Workiva certification.
  Tie-outs remain read-only. Signed corrections are a separate opt-in workflow;
  automatic recovery and a reviewer UI are not implemented.

Verify with `pytest tests/test_reporting.py` or run the full repository proof with
`./scripts/proof.sh`. The rule fixtures are in `workiva_mcp/reporting_demo.py`;
the synthetic source data is in `workiva_mcp/mock_transport.py`.
