# Financial reporting review workbench

The workbench turns the project's deterministic reporting engine into a local,
single-user review workflow. It imports XLSX snapshots without credentials,
records human-supplied mappings and dispositions, compares versions, and reopens
reviews when their evidence changes. It never edits the imported workbook.

## Run the complete two-version demo

```bash
pip install -e '.[dev]'
workiva-review-workbench --port 8765
```

On your own machine, open `http://127.0.0.1:8765`. In an Amp orb, use its portal
URL instead. The default data directory is `~/.workiva_mcp/workbench`. Use a new
`--data-dir` for a separate workspace; there is intentionally no destructive reset.

1. Click **Load Riverton v1**. This only works in an empty workspace.
2. In **Mappings**, inspect the supplied rules and worksheet preview, enter an operator name, and
   **Confirm mappings**. These are explicit demo mappings, not AI discoveries.
3. Return to **Overview / Findings** and **Run checks**. Two checks pass; the raw
   formula and missing-source checks are not evaluated.
4. Select cash, formula, and unaffected findings. Record a note and disposition
   for each. A disposition records review work, not approval or resolution.
5. In **Changes & history**, click **Import Riverton v2**. Cash changes from
   1,000,000 to 1,005,000 while supporting balances still total 1,000,000. A raw
   formula changes too. Cash and formula reviews reopen; the unaffected review
   stays recorded. Provisional results are visibly marked stale.
6. Confirm the mappings for v2 and run checks again before recording further
   dispositions. Filter **Reopened only** to see the two affected reviews.
7. Export the JSON packet. It contains both version snapshots, source hashes,
   rules, confirmation records, current findings, changes, and review history.
   Its top-level status remains `unreviewed`.

The two fixture downloads are generated real XLSX files. For your own workbook,
use **Import XLSX version**, then **New rule** in Mappings. Set the description,
unit, decimal precision and tolerance. Choose **Reported value** under **Assign
selected cell to**, select a worksheet, and click its cell. Repeat for each
supporting value; choose Add or Subtract and the appropriate scale. You can also
type explicit sheet/cell references. **Save all mapping drafts**, then confirm
the mappings before running checks. Rule IDs stay stable in the visual editor.

The preview shows 20 rows × 8 columns from the retained snapshot, with paging
and **Jump to cell** for distant references. Scroll inside the grid for columns
outside the visible panel. Clicking a cell also displays its raw value, cached
value and any error. Formulas remain literal evidence, never executed code.

Drafts survive tab/rule changes and **Refresh workspace (keep draft)**, but not
a full page reload. Concurrent workspace changes reject a stale save. The draft
is retained, and saving again requires acknowledging review against the refreshed
revision; nothing is retried automatically. The optional **Advanced JSON editor**
requires explicitly applying or discarding its edits before visual editing resumes.
Backend validation and version-specific confirmation remain mandatory.

Sheet names may contain spaces. An equivalent minimal advanced rule is:

```json
[
  {
    "id": "cash",
    "description": "Reported cash agrees to support",
    "reported": {"sheet": "Report", "cell": "B2", "scale": "units"},
    "supporting": [
      {"sheet": "Support", "cell": "B2", "scale": "units", "sign": "add"}
    ],
    "unit": "USD",
    "decimal_places": 2,
    "tolerance": "0.00"
  }
]
```

Mappings must represent the same period and currency; the operator is responsible
for accounting meaning. The editor does not infer period/currency compatibility
or automatically match labels. A passing tie-out does not establish those facts.

## What the review state means

Arithmetic status (`passed`, `failed`, `not_evaluated`) is separate from review
status (`unreviewed`, `reviewed`, `reopened`). Neither is an approval flag.
Evidence digests include the per-rule contract, source coordinates, raw values,
cached/calculated values and errors—not timestamps. Changed evidence reopens
the prior disposition even if the calculated result still passes. Unchanged
evidence retains its disposition. Changing one rule does not reopen other rules.

Every imported version requires mapping reconfirmation. If the previous mappings
were confirmed, import computes a provisional comparison so changes are visible
immediately. No disposition is accepted until mappings and checks are current.
Revision and evidence-digest checks reject stale submissions; the browser never
silently retries a rejected disposition. Draft notes survive in-page filtering
and refreshes, but are not persisted until submitted and do not survive a page reload.

## Import and trust boundaries

- SQLite retains original XLSX bytes and parsed snapshots; files use `0600` and
  the workspace directory `0700` on POSIX. These are sensitive financial data,
  not payload-redacted receipts. Exports downloaded by a browser inherit that
  browser's file permissions; protect them separately.
- Import limits include 10,000,000 compressed bytes, 50,000,000 expanded bytes,
  500 ZIP entries, 50 sheets, 200,000 source cells, and 25 versions. Sparse or
  inflated worksheet dimensions exceeding the bounded iteration area are refused.
  XML element, depth, text, attribute and relationship limits apply package-wide
  before openpyxl parsing. This is bounded local parsing, not an isolated malware
  sandbox or an assurance that arbitrary hostile files are safe in every dependency.
- Macros and external workbook-link packages are rejected. No formulas execute,
  no external resources are fetched, and no Workiva requests are made. Formula
  cached values can be stale: the workbench preserves them as evidence but does
  not certify their calculation. Missing caches, errors, blanks, dates and booleans
  never silently become zero.
- XLSX numeric cells have Excel's own precision limits. Decimal arithmetic cannot
  recover digits already lost in a workbook. Exact textual numeric inputs retain
  the parser's supported precision. Scale conversion, sum-then-HALF_UP rounding,
  and inclusive tolerance share a pure implementation with the Workiva tie-out tool.
- The HTTP listener binds only to loopback. There is **no application login**;
  local processes and anyone allowed through an explicitly configured reverse
  proxy can read and mutate this one review workspace. Operator names are labels,
  not verified identities. Host/Origin checks and a required custom request header
  mitigate browser cross-origin attacks; they are not authorization.
- `--allowed-host` explicitly trusts a proxy hostname. Do not expose this service
  as a public multi-user application. A proxy must supply authentication/access
  controls. The orb preview is for synthetic data only.
- History is transactional and durable locally, but not signed, remotely immutable,
  or independent audit proof. Anyone controlling the database can alter it. The
  packet hash is SHA-256 of sorted-key, compact ASCII JSON before its `sha256`
  field is added; it detects change only against a trusted copy.
- This workbench does not invoke the signed Workiva correction executor. Its
  local review notes are never accepted as signatures or correction authority.

## Completion goals and next stages

**Delivered in this milestone:** credential-free XLSX review; version-specific
mapping confirmation; evidence-linked dispositions; changed-source reopening;
durable history/export; two-version demonstration; visual rule editing and bounded
worksheet previews; stale-draft retention; backend, HTTP and browser checks.

**Next, not implemented:**

1. Explicit period/currency compatibility checks and a larger public ACFR-style
   corpus. Acceptance: mismatched-period or currency mappings cannot look like
   valid reconciliations merely because their amounts match. The no-JSON visual
   mapping portion is implemented; these accounting-semantic checks are not.
2. AI mapping proposals with citations and abstention, evaluated independently of
   arithmetic against held-out ambiguous/mismatched-period examples. Human mapping
   acceptance remains mandatory; publish both misses and false suggestions.
3. A separately designed multi-user service with authenticated reviewer identities,
   role separation, signature retention and verifiable review history. Acceptance:
   adversarial authorization tests and independently checkable approval artifacts.
4. Optional linkage to the existing signed correction workflow, after source and
   deployment identity are bound explicitly. An XLSX disposition must never grant
   a Workiva write by implication.

The goal is a useful reporting-review system, not a larger tool catalog. No
production deployment, live Workiva certification, compliance opinion, or measured
reviewer time savings is claimed.

## Verify

```bash
pytest tests/test_workbench.py tests/test_workbench_server.py
node --check workiva_mcp/workbench_static/app.js
./scripts/proof.sh
```

Optional repeatable browser regression (requires Playwright and Chromium):

```bash
pip install playwright
PYTHONPATH=. python scripts/check_workbench_ui.py --chromium /path/to/chrome
# Add --screenshots /path/to/output to capture desktop and narrow layouts.
```

This starts a disposable loopback server and synthetic workspace, blocks external
browser requests, and closes both browser and server on exit. It exercises visual
mapping creation through a real check, stale-save rejection and retained-draft
acknowledgment, invalid JSON recovery, formula evidence, Excel edge navigation,
replacement sheet names, literal HTML-like cell text, and narrow-layout overflow.

The tests include formula-cache fixtures, raw-formula-only reopening, independent
numeric expectations, stale revisions, concurrent HTTP submissions, restart
persistence, malformed/oversized XML packages, and cross-origin/Host rejection.
The browser walkthrough covers empty, mapping, reviewed, reopened, missing-evidence,
error and narrow-layout states. See the main proof guide for other project layers.
