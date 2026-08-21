/* List chips: Clearance / US Person appear at most once per row.
   Fixture jobs only — no applicant PII.
   Run: node dashboard/test_list_tag_dedupe.js */
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const src = fs.readFileSync(path.join(__dirname, "static", "app.js"), "utf8");

function extractFunction(name) {
  const token = `function ${name}(`;
  const start = src.indexOf(token);
  if (start === -1) throw new Error(`missing function ${name}`);
  const end = src.indexOf("\nfunction ", start + 1);
  return src.slice(start, end === -1 ? src.length : end);
}

function extractConstThrough(startName, endNameExclusive) {
  const startTok = `const ${startName} = `;
  const start = src.indexOf(startTok);
  if (start === -1) throw new Error(`missing const ${startName}`);
  const endTok = `const ${endNameExclusive} = `;
  const end = src.indexOf(endTok, start + 1);
  if (end === -1) throw new Error(`missing trailing const ${endNameExclusive}`);
  return src.slice(start, end);
}

const ctx = vm.createContext({
  selectedId: null,
  searchText: "",
  jobWorkModeDisplay: () => ({ mode: "unknown", approx: false }),
  jobMinYoeDisplay: () => ({ n: null, approx: false }),
  jobSalaryDisplay: () => ({ min: null, max: null, approx: false }),
  formatWorkMode: (m) => m,
  formatYoeLabel: () => "",
  formatSalaryLabel: () => "",
  jobActivityDot: () => "",
  fillOutcome: () => "",
  postedAgeLabel: () => "",
  statusLabel: (s) => s,
  queueBucket: (status) => (status === "deleted" ? "deleted" : "open"),
  companyApplyCountBadgeHtml: () => "",
  sourceChipsHtml: () => "",
  escapeHtml: (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;"),
  highlightSearchInText: (text) => {
    const raw = text == null ? "" : String(text);
    return String(raw)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  },
});

vm.runInContext(extractConstThrough("DELETED_REASON_LABELS", "DELETED_REASON_ORDER"), ctx);
vm.runInContext(
  [
    extractFunction("normalizeDeletedReasonCode"),
    extractFunction("deletedReasonCodes"),
    extractFunction("deletedReasonLabel"),
    extractFunction("formatDeletedReasons"),
    extractFunction("pushUniqueListTag"),
    extractFunction("renderJobRow"),
  ].join("\n"),
  ctx,
);

function clearanceChipCount(html) {
  const matches = html.match(/>Clearance</g) || [];
  return matches.length;
}

function usPersonChipCount(html) {
  const matches = html.match(/>US Person</g) || [];
  return matches.length;
}

// clearance=true + deleted_reason clearance_or_intel → one Clearance chip
{
  const row = ctx.renderJobRow({
    id: "fixture-clearance-dup",
    title: "Dummy ML Engineer",
    company: "Fixture Defense Co",
    status: "deleted",
    clearance: true,
    us_person: false,
    deleted_reason: "clearance_or_intel",
  });
  assert.strictEqual(clearanceChipCount(row), 1, row);
  assert.match(row, /class="tag clearance"/);
  assert.doesNotMatch(row, /class="tag deleted-reason"[^>]*>Clearance</);
}

// Both clearance + US Person + deleted clearance → Clearance once, US Person kept
{
  const row = ctx.renderJobRow({
    id: "fixture-voyager-style",
    title: "Dummy Engineer",
    company: "Fixture Voyager",
    status: "deleted",
    clearance: true,
    us_person: true,
    deleted_reason: "clearance_or_intel",
  });
  assert.strictEqual(clearanceChipCount(row), 1, row);
  assert.strictEqual(usPersonChipCount(row), 1, row);
  assert.match(row, /class="tag clearance"/);
  assert.match(row, /class="tag us-person"/);
}

// Alias pair clearance + clearance_or_intel still one Clearance
{
  const row = ctx.renderJobRow({
    id: "fixture-alias-pair",
    title: "Dummy Role",
    company: "Fixture Anduril",
    status: "deleted",
    clearance: true,
    deleted_reasons: ["clearance", "clearance_or_intel"],
  });
  assert.strictEqual(clearanceChipCount(row), 1, row);
}

// Non-deleted queue: clearance stamp alone still shows one chip
{
  const row = ctx.renderJobRow({
    id: "fixture-open-clearance",
    title: "Dummy Role",
    company: "Fixture Leidos",
    status: "discovered",
    clearance: true,
  });
  assert.strictEqual(clearanceChipCount(row), 1, row);
}

// Deleted with clearance reason only (no stamp) still shows one via deleted-reason
{
  const row = ctx.renderJobRow({
    id: "fixture-reason-only",
    title: "Dummy Role",
    company: "Fixture Co",
    status: "deleted",
    clearance: false,
    deleted_reason: "clearance_or_intel",
  });
  assert.strictEqual(clearanceChipCount(row), 1, row);
  assert.match(row, /class="tag deleted-reason"/);
}

// Extra deleted reason kept when Clearance already stamped
{
  const row = ctx.renderJobRow({
    id: "fixture-multi-reason",
    title: "Dummy Role",
    company: "Fixture Co",
    status: "deleted",
    clearance: true,
    deleted_reasons: ["clearance_or_intel", "excessive_yoe"],
  });
  assert.strictEqual(clearanceChipCount(row), 1, row);
  assert.match(row, /Excessive YOE/);
  assert.doesNotMatch(row, /deleted-reason"[^>]*>Clearance/);
}

console.log("OK test_list_tag_dedupe");
