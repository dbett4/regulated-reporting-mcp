"use strict";

const $ = (id) => document.getElementById(id);
let state = null;
let selectedRuleId = null;
let activeView = "overview";
let busy = false;
let mappingsDirty = false;
let draftRules = null;
let editingRuleId = null;
let mappingNeedsReview = false;
let advancedJsonInvalid = false;
let draftRevision = null;
let preview = null;
let previewVersion = null;
let previewRequest = 0;
let previewPosition = { sheet: "", row: 1, column: 1 };
const reviewDrafts = new Map();
const MAX_FILE_SIZE = 10_000_000;

function setMessage(kind, message) {
  const other = kind === "alert" ? $("status") : $("alert");
  other.hidden = true;
  const target = $(kind);
  target.textContent = message;
  target.hidden = !message;
}

function clearMessages() {
  $("alert").hidden = true;
  $("status").hidden = true;
}
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined && text !== null) el.textContent = String(text);
  if (className) el.className = className;
  return el;
}
function value(value) {
  return value === null || value === undefined || value === ""
    ? "—"
    : String(value);
}
function safeValue(input) {
  if (input === null || input === undefined || input === "") return "—";
  if (typeof input !== "object") return String(input);
  try {
    return JSON.stringify(input);
  } catch (_) {
    return "[unserializable value]";
  }
}
function displayStatus(text) {
  return String(text || "").replaceAll("_", " ");
}

async function readResponse(response) {
  let body;
  try {
    body = await response.json();
  } catch (_) {
    body = {};
  }
  if (!response.ok) {
    const error = new Error(
      body.error || `Request failed (${response.status})`,
    );
    error.stale = response.status === 409;
    throw error;
  }
  return body;
}

async function fetchState() {
  captureReviewDraft();
  const response = await fetch("/api/state", {
    headers: { Accept: "application/json" },
  });
  state = await readResponse(response);
  render();
}

async function mutate(path, body, successMessage) {
  if (busy || !state) return;
  captureReviewDraft();
  busy = true;
  renderBusy();
  clearMessages();
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Workbench-Request": "1",
        Accept: "application/json",
      },
      body: JSON.stringify({ ...body, expected_revision: state.revision }),
    });
    state = await readResponse(response);
    if (path === "/api/rules") {
      mappingsDirty = false;
      mappingNeedsReview = false;
      draftRevision = state.revision;
      draftRules = structuredClone(state.rules);
    }
    if (path === "/api/review" && body.rule_id) {
      reviewDrafts.delete(body.rule_id);
      document.querySelector(".review-form")?.remove();
    }
    setMessage("status", successMessage);
    render();
  } catch (error) {
    if (error.stale) {
      if (path === "/api/rules") mappingNeedsReview = true;
      try {
        await fetchState();
      } catch (_) {
        /* retain the actionable stale message */
      }
      setMessage(
        "alert",
        `The workspace changed before this submission. Fresh state was loaded; review your unsaved entry and submit it again. ${error.message}`,
      );
    } else setMessage("alert", error.message);
  } finally {
    busy = false;
    renderBusy();
  }
}

function renderBusy() {
  document.querySelectorAll("button").forEach((button) => {
    if (button.id === "run-checks")
      button.disabled = busy || !state || !mappingConfirmed();
    else if (button.closest("#confirm-form"))
      button.disabled = busy || !state || mappingsDirty;
    else if (button.matches(".review-form button"))
      button.disabled = busy || !state || !state.checks_current;
    else if (button.id === "remove-rule")
      button.disabled = busy || !state || !draftRules || draftRules.length <= 1;
    else if (button.matches(".remove-support"))
      button.disabled = busy || draftRules?.find((rule) => rule.id === editingRuleId)?.supporting.length <= 1;
    else if (button.matches(".sheet-cell"))
      button.disabled = busy || advancedJsonInvalid || !preview || preview.revision !== state?.revision;
    else button.disabled = busy || !state;
  });
  document.querySelectorAll("#rules-form input, #rules-form select, #rules-form textarea").forEach((control) => {
    control.disabled = busy || (advancedJsonInvalid && control.id !== "rules-json");
  });
  if (advancedJsonInvalid) {
    $("add-rule").disabled = true;
    $("remove-rule").disabled = true;
    document.querySelectorAll("#rule-fields button").forEach((button) => { button.disabled = true; });
  }
}

function showView(name) {
  captureReviewDraft();
  activeView = name;
  document.querySelectorAll(".view").forEach((view) => {
    view.hidden = view.id !== `${name}-view`;
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    const active = tab.dataset.view === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
}

function render() {
  if (!state) return;
  const populated = state.workbooks.length > 0;
  $("empty-state").hidden = populated;
  document.querySelector(".tabs").hidden = !populated;
  $("version-block").hidden = !populated;
  $("choose-file-populated").hidden = !populated;
  $("checks-warning").hidden = !populated || state.checks_current !== false;
  if (!populated) {
    document.querySelectorAll(".view").forEach((v) => {
      v.hidden = true;
    });
    renderBusy();
    return;
  }
  const currentIndex = state.workbooks.findIndex(
    (book) => book.id === state.active_version,
  );
  const current =
    state.workbooks[currentIndex] ||
    state.workbooks[state.workbooks.length - 1];
  const previous = currentIndex > 0 ? state.workbooks[currentIndex - 1] : null;
  $("version-label").textContent = current
    ? current.label
    : value(state.active_version);
  $("predecessor-label").textContent = previous
    ? `Predecessor: ${previous.label}`
    : "No predecessor";
  showView(activeView);
  renderSummary();
  renderFindings();
  renderMappings();
  renderHistory();
  renderBusy();
}

function renderSummary() {
  const wrap = $("summary-row");
  wrap.replaceChildren();
  [
    ["passed", "Passed"],
    ["failed", "Failed"],
    ["not_evaluated", "Not evaluated"],
    ["reopened", "Reopened"],
  ].forEach(([key, label]) => {
    const card = node("div", null, `card metric ${key}`);
    card.append(node("span", label), node("strong", state.summary[key] || 0));
    wrap.append(card);
  });
  $("queue-caption").textContent = state.findings.length
    ? `${state.findings.length} ${state.checks_current ? "current" : "provisional / stale"} check results.`
    : "Run checks to create findings.";
}

function renderFindings() {
  captureReviewDraft();
  const wrap = $("findings");
  wrap.replaceChildren();
  const status = $("status-filter").value,
    reopened = $("reopened-filter").checked;
  const findings = state.findings.filter(
    (finding) =>
      (status === "all" || finding.status === status) &&
      (!reopened || finding.review_status === "reopened"),
  );
  if (!findings.length)
    wrap.append(
      node(
        "p",
        state.findings.length
          ? "No findings match these filters."
          : "No checks have been run yet.",
        "placeholder",
      ),
    );
  findings.forEach((finding) => {
    const button = node(
      "button",
      null,
      `finding-button${selectedRuleId === finding.rule_id ? " selected" : ""}`,
    );
    button.type = "button";
    button.dataset.ruleId = finding.rule_id;
    button.setAttribute(
      "aria-pressed",
      String(selectedRuleId === finding.rule_id),
    );
    button.append(node("strong", finding.description || finding.rule_id));
    const meta = node("span", null, "finding-meta");
    meta.append(
      node("span", displayStatus(finding.status), `badge ${finding.status}`),
      node(
        "span",
        displayStatus(finding.review_status),
        `badge ${finding.review_status}`,
      ),
    );
    button.append(meta);
    wrap.append(button);
  });
  const selected = state.findings.find(
    (finding) => finding.rule_id === selectedRuleId,
  );
  if (selected) renderDetail(selected);
  else {
    $("detail-empty").hidden = false;
    $("detail-content").hidden = true;
  }
}

function renderDetail(finding) {
  $("detail-empty").hidden = true;
  const root = $("detail-content");
  root.hidden = false;
  root.replaceChildren();
  const head = node("div", null, "detail-head");
  const title = node("div");
  const heading = node("h2", finding.description);
  heading.id = "detail-title";
  title.append(node("p", finding.rule_id, "eyebrow"), heading);
  head.append(
    title,
    node("span", displayStatus(finding.status), `badge ${finding.status}`),
  );
  root.append(head);
  const calc = node("div", null, "calculation");
  [
    ["Reported", finding.reported],
    ["Expected", finding.expected],
    ["Difference", finding.difference],
  ].forEach(([label, val]) => {
    const box = node("div");
    box.append(
      node("span", label),
      node("strong", `${value(val)}${finding.unit ? ` ${finding.unit}` : ""}`),
    );
    calc.append(box);
  });
  root.append(calc);
  if (finding.reason) root.append(node("p", finding.reason, "reason"));
  const table = node("table"),
    caption = node("caption", "Calculation evidence");
  caption.className = "sr-only";
  const tr = node("tr");
  ["Role", "Source", "Raw", "Calculated", "Normalized", "Error"].forEach((h) =>
    tr.append(node("th", h)),
  );
  const thead = node("thead");
  thead.append(tr);
  table.append(caption, thead);
  const tbody = node("tbody");
  (finding.evidence || []).forEach((item) => {
    const row = node("tr");
    const src = item.source || {};
    [
      item.role,
      `${value(src.sheet)}!${value(src.cell)} · scale ${value(src.scale)}${src.sign === undefined ? "" : ` · sign ${src.sign}`}`,
      safeValue(item.raw_value),
      safeValue(item.value),
      safeValue(item.normalized_value),
      safeValue(item.error),
    ].forEach((v, i) => row.append(node("td", v, i > 1 ? "mono" : "")));
    tbody.append(row);
  });
  table.append(tbody);
  root.append(table);
  if (finding.review) {
    const record = node("div", null, "review-record");
    const prefix =
      finding.review_status === "reopened"
        ? "Previous disposition: "
        : "Disposition: ";
    record.append(
      node(
        "strong",
        `${prefix}${displayStatus(finding.review.disposition)} · ${finding.review.reviewer}`,
      ),
      node("p", finding.review.note),
      node(
        "small",
        `${finding.review.at} · Status: ${displayStatus(finding.review_status)}`,
      ),
    );
    root.append(record);
  }
  const form = node("form", null, "review-form");
  form.dataset.ruleId = finding.rule_id;
  form.dataset.digest = finding.evidence_digest;
  const draft = reviewDrafts.get(finding.rule_id) || {};
  const disposition = selectDisposition();
  disposition.value = draft.disposition || "noted";
  const reviewer = input("text", true);
  reviewer.maxLength = 100;
  reviewer.value = draft.reviewer || "";
  const note = textarea();
  note.maxLength = 2000;
  note.value = draft.note || "";
  form.append(
    field("Disposition", disposition, false),
    field("Reviewer name", reviewer, false),
    field("Required note", note, true),
  );
  if (state.checks_current === false)
    form.append(
      node(
        "p",
        "Stale evidence requires re-review after mappings are confirmed and checks are run again.",
        "reason wide",
      ),
    );
  const submit = node("button", "Record disposition", "button primary wide");
  submit.type = "submit";
  submit.disabled = busy || state.checks_current === false;
  form.append(submit);
  root.append(form);
}
function captureReviewDraft() {
  const form = document.querySelector(".review-form");
  if (!form) return;
  const controls = form.querySelectorAll("select,input,textarea");
  reviewDrafts.set(form.dataset.ruleId, {
    disposition: controls[0].value,
    reviewer: controls[1].value,
    note: controls[2].value,
  });
}
function field(labelText, control, wide) {
  const label = node("label", null, wide ? "wide" : "");
  label.append(node("span", labelText), control);
  return label;
}
function input(type, required) {
  const el = document.createElement("input");
  el.type = type;
  el.required = required;
  return el;
}
function textarea() {
  const el = document.createElement("textarea");
  el.rows = 3;
  el.required = true;
  return el;
}
function selectDisposition() {
  const select = document.createElement("select");
  [
    ["noted", "Noted"],
    ["needs_follow_up", "Needs follow-up"],
  ].forEach(([v, t]) => {
    const o = node("option", t);
    o.value = v;
    select.append(o);
  });
  return select;
}

function renderMappings() {
  if (!draftRules || !mappingsDirty) {
    draftRules = structuredClone(state.rules);
    draftRevision = state.revision;
  } else if (draftRevision !== state.revision) {
    mappingNeedsReview = true;
  }
  if (!editingRuleId || !draftRules.some((rule) => rule.id === editingRuleId))
    editingRuleId = draftRules[0]?.id || null;
  renderRuleSelector();
  renderRuleFields();
  syncJsonFromDraft();
  if (!preview || preview.revision !== state.revision) {
    if (preview && preview.active_version !== state.active_version)
      previewPosition = { sheet: "", row: 1, column: 1 };
    loadPreview();
  }
  $("rebase-draft").hidden = !mappingNeedsReview;
  const confirmed = mappingConfirmed();
  $("mapping-draft-state").textContent = mappingNeedsReview
    ? "Workspace changed. Your draft is retained; review it before saving again."
    : mappingsDirty
      ? "Unsaved mapping changes"
      : "Draft matches the saved mappings";
  $("confirmation-state").textContent = mappingsDirty
    ? "Save draft before confirming"
    : confirmed
      ? "Confirmed"
      : "Confirmation required";
  $("confirmation-state").className =
    `pill ${confirmed && !mappingsDirty ? "reviewed" : "not_evaluated"}`;
}

function markMappingsDirty() {
  mappingsDirty = true;
  syncJsonFromDraft();
  renderRuleSelector();
  renderTargetOptions();
  renderBusy();
  $("mapping-draft-state").textContent = mappingNeedsReview
    ? "Workspace changed. Review your retained draft against the refreshed workbook."
    : "Unsaved mapping changes";
  $("confirmation-state").textContent = "Save draft before confirming";
  $("confirmation-state").className = "pill not_evaluated";
}

function renderRuleSelector() {
  const select = $("rule-select");
  select.replaceChildren();
  draftRules.forEach((rule, index) => {
    const option = node("option", rule.description || rule.id || `Rule ${index + 1}`);
    option.value = rule.id;
    option.selected = rule.id === editingRuleId;
    select.append(option);
  });
  $("remove-rule").disabled = busy || draftRules.length <= 1;
}

function labeledControl(labelText, control, className) {
  const label = node("label", null, className);
  label.append(node("span", labelText), control);
  return label;
}

function textControl(valueText, name, maxLength) {
  const control = input("text", true);
  control.name = name;
  control.value = valueText ?? "";
  control.maxLength = maxLength;
  return control;
}

function optionSelect(values, selected, name) {
  const control = document.createElement("select");
  control.name = name;
  values.forEach((entry) => {
    const option = node("option", entry[1]);
    option.value = entry[0];
    option.selected = entry[0] === selected;
    control.append(option);
  });
  return control;
}

function renderRuleFields() {
  const root = $("rule-fields");
  root.replaceChildren();
  const rule = draftRules.find((item) => item.id === editingRuleId);
  if (!rule) return;
  const basics = node("div", null, "rule-basics");
  basics.append(
    labeledControl("Rule ID", textControl(rule.id, "id", 100)),
    labeledControl("Description", textControl(rule.description, "description", 500), "span-2"),
    labeledControl("Unit (for example USD)", textControl(rule.unit ?? "USD", "unit", 30)),
    labeledControl("Decimal precision", optionSelect([["0","0"],["1","1"],["2","2"],["3","3"],["4","4"],["5","5"],["6","6"]], String(rule.decimal_places ?? 2), "decimal_places")),
    labeledControl("Tolerance in base units", textControl(rule.tolerance ?? "0.00", "tolerance", 25)),
  );
  // Stable identifiers keep review history attached to the same rule.
  basics.querySelector('[name="id"]').readOnly = true;
  root.append(basics, referenceEditor("Reported value", rule.reported, "reported", -1));
  const supporting = node("div", null, "supporting-list");
  supporting.append(node("h3", "Supporting values"));
  rule.supporting.forEach((ref, index) => supporting.append(referenceEditor(`Supporting value ${index + 1}`, ref, "supporting", index)));
  const add = node("button", "+ Add supporting value", "button ghost add-support");
  add.type = "button";
  add.addEventListener("click", () => {
    rule.supporting.push({ sheet: previewPosition.sheet || preview?.sheets?.[0] || "Sheet1", cell: "A1", scale: "units", sign: "add" });
    markMappingsDirty();
    renderRuleFields();
  });
  supporting.append(add);
  root.append(supporting);
  renderTargetOptions();
}

function referenceEditor(title, ref, kind, index) {
  const section = node("fieldset", null, "reference-editor");
  section.dataset.kind = kind;
  section.dataset.index = String(index);
  section.append(node("legend", title));
  const sheet = textControl(ref.sheet, "sheet", 100);
  const cell = textControl(ref.cell, "cell", 10);
  cell.pattern = "[A-Z]{1,3}[1-9][0-9]{0,6}";
  section.append(
    labeledControl("Worksheet", sheet),
    labeledControl("Cell", cell),
    labeledControl("Scale", optionSelect([["units","Units"],["thousands","Thousands"],["millions","Millions"]], ref.scale || "units", "scale")),
  );
  if (kind === "supporting") {
    section.append(labeledControl("Operation", optionSelect([["add","Add"],["subtract","Subtract"]], ref.sign || "add", "sign")));
    const remove = node("button", "Remove", "button ghost danger remove-support");
    remove.type = "button";
    remove.disabled = draftRules.find((item) => item.id === editingRuleId).supporting.length <= 1;
    remove.addEventListener("click", () => {
      if (draftRules.find((item) => item.id === editingRuleId).supporting.length <= 1) return;
      draftRules.find((item) => item.id === editingRuleId).supporting.splice(index, 1);
      markMappingsDirty();
      renderRuleFields();
    });
    section.append(remove);
  }
  return section;
}

function syncJsonFromDraft() {
  if (!advancedJsonInvalid && document.activeElement !== $("rules-json")) $("rules-json").value = JSON.stringify(draftRules, null, 2);
}

function renderTargetOptions() {
  const select = $("mapping-target");
  const prior = select.value;
  select.replaceChildren();
  const reported = node("option", "Reported value");
  reported.value = "reported";
  select.append(reported);
  const rule = draftRules?.find((item) => item.id === editingRuleId);
  (rule?.supporting || []).forEach((_, index) => {
    const option = node("option", `Supporting value ${index + 1}`);
    option.value = `supporting:${index}`;
    select.append(option);
  });
  if ([...select.options].some((option) => option.value === prior)) select.value = prior;
}

function columnName(column) {
  let name = "";
  while (column) { column--; name = String.fromCharCode(65 + (column % 26)) + name; column = Math.floor(column / 26); }
  return name;
}
function coordinateParts(coordinate) {
  const match = /^([A-Z]{1,3})([1-9][0-9]{0,6})$/.exec(coordinate.toUpperCase());
  if (!match) return null;
  let column = 0;
  for (const char of match[1]) column = column * 26 + char.charCodeAt(0) - 64;
  const row = Number(match[2]);
  return row <= 1048576 && column <= 16384 ? { row, column } : null;
}

async function loadPreview() {
  if (!state?.active_version) return;
  const request = ++previewRequest;
  const revision = state.revision;
  const version = state.active_version;
  if (previewVersion !== version) previewPosition = { sheet: "", row: 1, column: 1 };
  previewVersion = version;
  preview = null;
  $("sheet-grid").replaceChildren();
  $("cell-inspector").replaceChildren(node("span", "Select a cell from the current preview to inspect and assign it."));
  $("preview-status").textContent = "Loading worksheet preview…";
  const query = new URLSearchParams({ expected_revision: String(revision), start_row: String(previewPosition.row), start_column: String(previewPosition.column) });
  if (previewPosition.sheet) query.set("sheet", previewPosition.sheet);
  try {
    const response = await fetch(`/api/preview?${query}`, { headers: { Accept: "application/json" } });
    const result = await readResponse(response);
    if (request !== previewRequest || state.revision !== revision || state.active_version !== version) return;
    preview = result;
    previewPosition = { sheet: result.sheet, row: result.start_row, column: result.start_column };
    renderPreview();
    renderBusy();
  } catch (error) {
    if (request !== previewRequest) return;
    $("preview-status").textContent = error.stale ? "Preview is stale. Refresh the workspace before selecting cells." : `Preview unavailable: ${error.message}`;
  }
}

function renderPreview() {
  const sheetSelect = $("sheet-select");
  sheetSelect.replaceChildren();
  preview.sheets.forEach((name) => { const option = node("option", name); option.value = name; option.selected = name === preview.sheet; sheetSelect.append(option); });
  $("preview-status").textContent = `${preview.sheet}: rows ${preview.start_row}–${preview.start_row + 19}, columns ${columnName(preview.start_column)}–${columnName(preview.start_column + 7)} · used range ${preview.total_rows} × ${preview.total_columns}`;
  const table = $("sheet-grid");
  table.replaceChildren();
  const head = node("thead"), headRow = node("tr");
  headRow.append(node("th", ""));
  for (let column = preview.start_column; column < preview.start_column + 8; column++) headRow.append(node("th", columnName(column)));
  head.append(headRow); table.append(head);
  const body = node("tbody");
  preview.rows.forEach((row, rowIndex) => {
    const tr = node("tr"); tr.append(node("th", preview.start_row + rowIndex));
    row.forEach((cell, columnIndex) => {
      const coordinate = `${columnName(preview.start_column + columnIndex)}${preview.start_row + rowIndex}`;
      const td = node("td");
      const button = node("button", cell ? displayCellValue(cell) : "", `sheet-cell${cell?.error ? " has-error" : ""}`);
      button.type = "button"; button.dataset.cell = coordinate;
      button.setAttribute("aria-label", `${preview.sheet} ${coordinate}: ${cell ? displayCellValue(cell) : "blank"}`);
      button.title = cell ? displayCellValue(cell) : "Blank cell";
      td.append(button); tr.append(td);
    });
    body.append(tr);
  });
  table.append(body);
}
function displayCellValue(cell) {
  if (cell.error !== null && cell.error !== undefined) return String(cell.error);
  if (cell.value === null || cell.value === undefined || cell.value === "") return cell.raw_value === 0 ? "0" : (cell.raw_value ?? "") === "" ? "" : String(cell.raw_value);
  return String(cell.value);
}

function inspectAndAssignCell(coordinate) {
  if (busy || advancedJsonInvalid || !preview || preview.revision !== state.revision) return;
  const row = Number(coordinate.match(/\d+$/)[0]) - preview.start_row;
  const letters = coordinate.match(/^[A-Z]+/)[0];
  const column = coordinateParts(`${letters}1`).column - preview.start_column;
  const cell = preview.rows[row]?.[column] || null;
  const inspector = $("cell-inspector"); inspector.replaceChildren(node("strong", `${preview.sheet}!${coordinate}`));
  inspector.append(node("span", `Raw: ${cell ? safeValue(cell.raw_value) : "Blank"}`), node("span", `Cached value: ${cell ? safeValue(cell.value) : "Blank"}`), node("span", `Error: ${cell ? safeValue(cell.error) : "None"}`));
  const rule = draftRules.find((item) => item.id === editingRuleId);
  if (!rule) return;
  const target = $("mapping-target").value;
  const ref = target === "reported" ? rule.reported : rule.supporting[Number(target.split(":")[1])];
  ref.sheet = preview.sheet; ref.cell = coordinate;
  markMappingsDirty(); renderRuleFields();
}
function mappingConfirmed() {
  return Boolean(
    state && (state.mapping_confirmation || state.mappings_confirmed),
  );
}

function renderHistory() {
  $("demo-v2").hidden = state.demo_version !== 1;
  const changes = $("changes");
  changes.replaceChildren();
  if (!state.changes.length)
    changes.append(
      node("p", "No predecessor differences are available yet.", "placeholder"),
    );
  state.changes.forEach((change) => {
    const row = node("div", null, "change-row");
    row.append(
      node("span", `${change.sheet}!${change.cell}`),
      changeValue("Before", change.before, "before"),
      changeValue("After", change.after, "after"),
    );
    changes.append(row);
  });
  const events = $("events");
  events.replaceChildren();
  if (!state.events.length) events.append(node("li", "No activity recorded."));
  state.events
    .slice()
    .reverse()
    .forEach((event) => {
      const li = node("li");
      li.append(
        node("strong", displayStatus(event.kind)),
        node("time", event.at),
        node("div", JSON.stringify(event.detail), "event-detail"),
      );
      events.append(li);
    });
}
function changeValue(label, entry, className) {
  const box = node("span", null, className);
  box.append(
    node("strong", label),
    node("span", `Raw: ${safeValue(entry && entry.raw_value)}`),
    node("span", `Calculated: ${safeValue(entry && entry.value)}`),
    node("span", `Error: ${safeValue(entry && entry.error)}`),
  );
  return box;
}

document
  .querySelectorAll(".tab")
  .forEach((tab) =>
    tab.addEventListener("click", () => showView(tab.dataset.view)),
  );
$("demo-v1").addEventListener("click", () =>
  mutate("/api/demo", { version: 1 }, "Riverton v1 loaded."),
);
$("demo-v2").addEventListener("click", () =>
  mutate(
    "/api/demo",
    { version: 2 },
    "Riverton v2 loaded. Confirm mappings and run checks before recording dispositions.",
  ),
);
$("choose-file").addEventListener("click", () => {
  $("import-form").hidden = false;
  $("import-label").focus();
});
$("choose-file-populated").addEventListener("click", () => {
  $("import-form").hidden = false;
  $("import-label").focus();
});
$("cancel-import").addEventListener("click", () => {
  $("import-form").hidden = true;
});
$("import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = $("import-file").files[0];
  if (!file || busy || !state) return;
  if (file.size > MAX_FILE_SIZE) {
    setMessage("alert", "The XLSX file exceeds the 10 MB client limit.");
    return;
  }
  busy = true;
  renderBusy();
  clearMessages();
  try {
    const query = new URLSearchParams({
      filename: file.name,
      label: $("import-label").value,
      expected_revision: String(state.revision),
    });
    const response = await fetch(`/api/import?${query}`, {
      method: "POST",
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "X-Workbench-Request": "1",
        Accept: "application/json",
      },
      body: file,
    });
    state = await readResponse(response);
    setMessage(
      "status",
      "Workbook imported. Confirm mappings and run checks before recording dispositions.",
    );
    render();
  } catch (error) {
    if (error.stale) {
      try {
        await fetchState();
      } catch (_) {}
      setMessage(
        "alert",
        `The workspace changed. Fresh state was loaded; select the file and review the unsaved import before trying again. ${error.message}`,
      );
    } else setMessage("alert", error.message);
  } finally {
    busy = false;
    renderBusy();
  }
});
$("rule-select").addEventListener("change", (event) => {
  editingRuleId = event.target.value;
  renderRuleFields();
});
$("rule-fields").addEventListener("input", (event) => {
  const rule = draftRules.find((item) => item.id === editingRuleId);
  const section = event.target.closest(".reference-editor");
  if (section) {
    const ref = section.dataset.kind === "reported" ? rule.reported : rule.supporting[Number(section.dataset.index)];
    ref[event.target.name] = event.target.name === "cell" ? event.target.value.toUpperCase() : event.target.value;
    if (event.target.name === "cell") event.target.value = ref.cell;
  } else if (event.target.name) {
    const priorId = rule.id;
    rule[event.target.name] = event.target.name === "decimal_places" ? Number(event.target.value) : event.target.value;
    if (event.target.name === "id") editingRuleId = rule.id;
    if (priorId !== editingRuleId) $("rule-select").value = editingRuleId;
  }
  markMappingsDirty();
});
$("add-rule").addEventListener("click", () => {
  const ids = new Set(draftRules.map((rule) => rule.id));
  let number = draftRules.length + 1;
  while (ids.has(`rule-${number}`)) number++;
  const sheet = previewPosition.sheet || preview?.sheets?.[0] || "Sheet1";
  const rule = { id: `rule-${number}`, description: "New tie-out rule", reported: { sheet, cell: "A1", scale: "units" }, supporting: [{ sheet, cell: "A1", scale: "units", sign: "add" }], unit: "USD", decimal_places: 2, tolerance: "0.00" };
  draftRules.push(rule); editingRuleId = rule.id; markMappingsDirty(); renderRuleFields();
});
$("remove-rule").addEventListener("click", () => {
  if (draftRules.length <= 1) return;
  const index = draftRules.findIndex((rule) => rule.id === editingRuleId);
  draftRules.splice(index, 1);
  editingRuleId = draftRules[Math.max(0, index - 1)].id;
  markMappingsDirty(); renderRuleFields();
});
$("apply-json").addEventListener("click", () => {
  try {
    const rules = JSON.parse($("rules-json").value);
    // Check the renderable shape here; the backend owns full rule validation.
    const refValid = (ref) => ref && typeof ref.sheet === "string" && typeof ref.cell === "string";
    if (!Array.isArray(rules) || !rules.length || rules.some((rule) =>
      !rule || typeof rule.id !== "string" || !rule.id ||
      typeof rule.description !== "string" || !refValid(rule.reported) ||
      !Array.isArray(rule.supporting) || !rule.supporting.length || !rule.supporting.every(refValid)
    ) || new Set(rules.map((rule) => rule.id)).size !== rules.length)
      throw new Error("Each rule needs a unique ID, description, reported reference and supporting references.");
    draftRules = rules; editingRuleId = rules[0].id; mappingsDirty = true; advancedJsonInvalid = false;
    renderMappings();
    renderBusy();
  } catch (error) { setMessage("alert", `Mappings are not valid JSON: ${error.message}`); }
});
$("rules-json").addEventListener("input", () => {
  mappingsDirty = true;
  advancedJsonInvalid = true;
  renderBusy();
  $("mapping-draft-state").textContent = "Apply or discard the JSON edits before using the visual editor.";
});
$("discard-json").addEventListener("click", () => {
  advancedJsonInvalid = false;
  $("rules-json").value = JSON.stringify(draftRules, null, 2);
  renderMappings(); renderBusy();
});
$("refresh-mappings").addEventListener("click", () => {
  fetchState().catch((error) => setMessage("alert", error.message));
});
$("rebase-draft").addEventListener("click", () => {
  draftRevision = state.revision;
  mappingNeedsReview = false;
  renderMappings(); renderBusy();
});
$("rules-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (advancedJsonInvalid) return setMessage("alert", "Fix or discard the invalid advanced JSON before saving mappings.");
  if (mappingNeedsReview) return setMessage("alert", "Review the retained draft against the refreshed workspace and acknowledge it before saving.");
  if (!draftRules.length) return setMessage("alert", "At least one mapping rule is required.");
  mutate(
    "/api/rules",
    { rules: draftRules },
    "Mapping draft saved. Reviewer confirmation is required before checks run.",
  );
});
$("sheet-select").addEventListener("change", (event) => {
  previewPosition = { sheet: event.target.value, row: 1, column: 1 }; loadPreview();
});
$("sheet-grid").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-cell]");
  if (button) inspectAndAssignCell(button.dataset.cell);
});
[["page-up",-20,0],["page-down",20,0],["page-left",0,-8],["page-right",0,8]].forEach(([id, rows, columns]) => {
  $(id).addEventListener("click", () => {
    previewPosition.row = Math.max(1, Math.min(1048576, previewPosition.row + rows));
    previewPosition.column = Math.max(1, Math.min(16384, previewPosition.column + columns));
    loadPreview();
  });
});
$("cell-jump-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const parts = coordinateParts($("cell-jump").value.trim().toUpperCase());
  if (!parts) return setMessage("alert", "Enter a valid XLSX cell reference, such as A1 or BC42.");
  $("cell-jump").value = `${columnName(parts.column)}${parts.row}`;
  previewPosition.row = parts.row; previewPosition.column = parts.column; loadPreview();
});
$("confirm-form").addEventListener("submit", (event) => {
  event.preventDefault();
  mutate(
    "/api/confirm",
    { reviewer: $("mapping-reviewer").value },
    "Mappings confirmed.",
  );
});
$("run-checks").addEventListener("click", () =>
  mutate("/api/run", {}, "Checks completed."),
);
$("status-filter").addEventListener("change", renderFindings);
$("reopened-filter").addEventListener("change", renderFindings);
$("findings").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-rule-id]");
  if (button) {
    selectedRuleId = button.dataset.ruleId;
    renderFindings();
    $("finding-detail").scrollIntoView({
      behavior: "smooth",
      block: "nearest",
    });
  }
});
$("detail-content").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  if (!form.matches(".review-form")) return;
  if (!state.checks_current) {
    setMessage(
      "alert",
      "This evidence is stale. Confirm mappings, run checks, and re-review the finding before submitting.",
    );
    return;
  }
  const controls = form.querySelectorAll("select,input,textarea");
  mutate(
    "/api/review",
    {
      rule_id: form.dataset.ruleId,
      disposition: controls[0].value,
      reviewer: controls[1].value,
      note: controls[2].value,
      evidence_digest: form.dataset.digest,
    },
    "Reviewer disposition recorded.",
  );
});
$("export").addEventListener("click", async () => {
  if (busy) return;
  busy = true;
  renderBusy();
  clearMessages();
  try {
    const response = await fetch("/api/export");
    if (!response.ok) await readResponse(response);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "review-packet.json";
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setMessage("status", "JSON review packet downloaded.");
  } catch (error) {
    setMessage("alert", error.message);
  } finally {
    busy = false;
    renderBusy();
  }
});

renderBusy();
fetchState().catch((error) =>
  setMessage("alert", `Could not load the local workbench: ${error.message}`),
);
