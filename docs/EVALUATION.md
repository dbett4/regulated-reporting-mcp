# Reproducible reporting evaluation

Run from an installed checkout, without credentials:

```bash
workiva-mcp-evaluate
workiva-mcp-evaluate --json > evaluation-report.json
# Equivalent module invocation:
python -m workiva_mcp.evaluation
```

The runner exits 0 only when every case matches its expected status and numeric
evidence and the transport records zero writes. Otherwise it exits 1. The full
`scripts/proof.sh` workflow also runs this evaluation.

## What the corpus checks

`workiva_mcp/reporting_cases.json` contains 18 public, synthetic cases with
hand-annotated answers and an arithmetic rationale for each. There are eight
passing tie-outs, five discrepancies, and five cases without usable evidence.
Coverage includes totals, signed rollforwards, units/thousands/millions,
parenthesized negatives, rounding after aggregation, negative HALF_UP rounding,
inclusive tolerance, precision, blanks, malformed numbers, and unresolved formulas.

Each case starts with isolated synthetic source cells. The runner calls the real
compact dispatcher and reporting tool through FakeWorkiva, passing only the
source mappings and rule settings—not the expected answers. It forces mock mode,
uses temporary result/receipt storage, and restores the prior environment and
mock data afterward. Run it as a standalone process, not concurrently with an
active server: mock isolation temporarily changes process-global state.

## How scores are calculated

- **Exact-case accuracy:** proportion matching the expected status and every
  exact normalized amount (`reported`, `expected`, `difference`). Unevaluated
  cases must not invent these amounts. Correct status with wrong arithmetic fails.
- **Discrepancy precision:** correctly flagged discrepancies divided by all
  flagged discrepancies. Flagging a clean or unavailable-evidence case is a
  false alarm.
- **Discrepancy recall:** correctly flagged discrepancies divided by all known
  discrepancies. Passing, abstaining, or erroring on a known discrepancy is a miss.
- **Incorrect passes:** known discrepancies or unavailable-evidence cases
  incorrectly marked passed.
- **Errors:** exceptions or missing, duplicate, wrong-rule, or unknown-status
  findings. These remain in the denominator rather than being discarded.

Undefined ratios are JSON `null`, not a perfect score. Reports include an
expected-versus-observed confusion matrix, category metrics, individual outcomes,
tool/request/write counts, completion time, Python and rule versions, and SHA-256
hashes of the corpus bytes and implementation files. Hashes identify inputs;
they are not signatures or independent certification.

The scorer itself is tested with deliberately wrong results, including wrong
amounts under a correct status, abstentions on discrepancies, dispatcher errors,
and a simulated write counter. Failing CLI runs must return a nonzero exit code.

## Interpret results narrowly

This is a public regression corpus, not a held-out benchmark. A perfect score
demonstrates these specified deterministic cases only. It does not measure LLM
reasoning, inferred mappings, accounting completeness, live Workiva compatibility,
atomic snapshots, audit compliance, or reviewer time savings. No customer data
is used. Correction approval and crash recovery have separate tests and demos.

To extend the corpus, add a stable case ID and rationale, calculate expected
amounts independently, and choose inputs that distinguish plausible wrong
implementations. Never generate expected answers with the tool under test.
Corpus content changes produce a new hash; change the schema version when its
format changes. Compare case sets and hashes before comparing scores across runs.
