/* Deleted-job JD prune-snippet highlighting (orange marks).
   Dummy jobs / fixture JD only — no applicant PII.
   Run: node dashboard/test_jd_prune_highlight.js */
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const src = fs.readFileSync(path.join(__dirname, "static", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "static", "index.html"), "utf8");

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
  escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  },
  MAX_ACCEPTABLE_MIN_YOE: 6,
  searchText: "",
});

vm.runInContext(
  extractConstThrough("SENIORITY_EXCLUDE_RE", "US_LOCATION_STRONG_RE"),
  ctx,
);
vm.runInContext(
  extractConstThrough("CLEARANCE_REQUIREMENT_RE", "CLEARANCE_EXPLICITLY_NOT_REQUIRED_RE"),
  ctx,
);
vm.runInContext(
  extractConstThrough("INTEL_AGENCY_COMPANY_RE", "INTEL_AGENCY_URL_RE"),
  ctx,
);
vm.runInContext(
  extractConstThrough("INDIA_LOCATION_RE", "GEORGIA_COUNTRY_CITY_RE"),
  ctx,
);
vm.runInContext(
  extractConstThrough("YOE_OF_EXP", "CITIZENSHIP_OR_GC_REQUIREMENT_RE"),
  ctx,
);
vm.runInContext(
  (() => {
    const startTok = "const CITIZENSHIP_OR_GC_REQUIREMENT_RE = ";
    const start = src.indexOf(startTok);
    if (start === -1) throw new Error("missing CITIZENSHIP_OR_GC_REQUIREMENT_RE");
    const end = src.indexOf("\nfunction foldAccents(", start);
    if (end === -1) throw new Error("missing foldAccents after salary consts");
    return src.slice(start, end);
  })(),
  ctx,
);
vm.runInContext(
  (() => {
    const token = "const JD_PRUNE_MARK_OPEN = ";
    const start = src.indexOf(token);
    if (start === -1) throw new Error("missing JD_PRUNE_MARK_OPEN");
    const end = src.indexOf("\nfunction searchNeedlesForSurface(", start);
    if (end === -1) throw new Error("missing searchNeedlesForSurface after mark consts");
    return src.slice(start, end);
  })(),
  ctx,
);
for (const name of [
  "normalizeDeletedReasonCode",
  "deletedReasonCodes",
  "mergePruneMatchRanges",
  "searchNeedlesForSurface",
  "activeSearchNeedles",
  "collectSearchMatchRanges",
  "applySearchHighlightMarks",
  "finalizeSearchHighlightHtml",
  "applyLayeredHighlightMarks",
  "highlightSearchInText",
  "yoePruneMatchOk",
  "isYoePruneHighlightRegex",
  "isYoeTagHighlightRegex",
  "yoeMatchIsEducationEquivalent",
  "yoeMatchIsSoft",
  "jdTagHighlightRegexes",
  "salaryRangeHighlightGate",
  "collectSalaryHighlightRanges",
  "collectRegexMatchRanges",
  "collectJdHighlightRanges",
  "collectPruneMatchRanges",
  "finalizePruneHighlightHtml",
  "pruneHighlightRegexesForReason",
  "jobPruneHighlightRegexes",
  "formatJdInline",
  "classifyJdLine",
  "formatJobDescriptionHtml",
  "jdCopySourceText",
  "jdIdentityFields",
  "jdIdentityPlainText",
  "jdCopyText",
]) {
  vm.runInContext(extractFunction(name), ctx);
}

const {
  formatJobDescriptionHtml,
  jobPruneHighlightRegexes,
  pruneHighlightRegexesForReason,
  jdCopyText,
} = ctx;

const FIXTURE_JD = [
  "Dummy Staff ML Engineer at Fixture Co.",
  "Build models. No real applicant data.",
  "Visa: USC and GC only",
  "Nice to have: Python.",
].join("\n");

function test_deleted_citizenship_marks_usc_gc_html() {
  const job = {
    status: "deleted",
    deleted_reason: "citizenship_or_greencard",
  };
  const regexes = jobPruneHighlightRegexes(job);
  assert.ok(regexes.length, "citizenship reason should map to regexes");
  const rendered = formatJobDescriptionHtml(FIXTURE_JD, regexes);
  assert.match(rendered, /<mark class="jd-prune-hit">/);
  assert.match(rendered, /<mark class="jd-prune-hit">[^<]*USC and GC only[^<]*<\/mark>/i);
  assert.doesNotMatch(rendered, /<mark class="jd-prune-hit">[^<]*Nice to have/);
}

function test_open_job_has_no_prune_regexes() {
  const job = { status: "discovered", deleted_reason: "citizenship_or_greencard" };
  assert.equal(jobPruneHighlightRegexes(job).length, 0);
}

function test_copy_path_stays_plain() {
  const job = {
    id: "dummy-deleted-role",
    status: "deleted",
    deleted_reason: "citizenship_or_greencard",
    title: "Dummy Staff ML Engineer",
    company: "Fixture Co",
    location: "Remote, US",
    job_description: FIXTURE_JD,
  };
  ctx.jdCache = new Map();
  const copied = jdCopyText(job);
  assert.ok(copied.includes("Visa: USC and GC only"));
  assert.ok(!copied.includes("jd-prune-hit"));
  assert.ok(!copied.includes("<mark"));
  assert.ok(!copied.includes("<span"));
  assert.ok(!copied.includes("&lt;"));
}

function test_reason_maps_to_citizenship_regex() {
  const rxs = pruneHighlightRegexesForReason("citizenship_or_greencard");
  const blob = "Visa: USC and GC only";
  assert.ok(rxs.length >= 1);
  assert.ok(rxs.some((rx) => {
    const sticky = new RegExp(rx.source, "iy");
    for (let i = 0; i < blob.length; i++) {
      sticky.lastIndex = i;
      if (sticky.exec(blob)) return true;
    }
    return false;
  }));
}

function regexHits(rxs, blob) {
  return rxs.some((rx) => {
    const sticky = new RegExp(rx.source, `i${rx.flags.includes("g") ? "g" : ""}`);
    sticky.lastIndex = 0;
    return sticky.test(blob);
  });
}

function test_unable_to_sponsor_is_not_citizenship_highlight() {
  const rxs = pruneHighlightRegexesForReason("citizenship_or_greencard");
  assert.ok(!regexHits(rxs, "We are unable to sponsor visas for this role."));
  assert.ok(!regexHits(rxs, "no visa sponsorship"));
  assert.ok(regexHits(rxs, "Visa: USC and GC only"));
  assert.ok(regexHits(rxs, "U.S. Person status is required"));
  assert.ok(regexHits(rxs, "access export controlled data"));
  assert.ok(regexHits(rxs, "ITAR REQUIREMENTS"));
  assert.ok(!regexHits(rxs, "International Traffic in Arms Regulations (ITAR), and OFAC"));
}

function test_css_orange_on_dark() {
  assert.match(html, /\.jd-prune-hit\s*\{/);
  const rule = html.split(".jd-prune-hit")[1].split("}")[0];
  assert.match(rule, /var\(--orange\)/);
  assert.match(rule, /var\(--orange-dim\)/);
}

function test_evidence_wires_highlight_only_when_formatting_jd() {
  assert.match(src, /formatJobDescriptionHtml\(\s*text,\s*jobPruneHighlightRegexes\(job\)\s*\)/);
  assert.match(src, /function jobPruneHighlightRegexes\(/);
  const copyFn = extractFunction("jdCopyText");
  assert.doesNotMatch(copyFn, /formatJobDescriptionHtml/);
  assert.doesNotMatch(copyFn, /jd-prune-hit/);
  const editFn = extractFunction("jdEvidenceHtml");
  assert.match(editFn, /jd-edit-textarea/);
  assert.match(editFn, /escapeHtml\(jdEditDraft\)/);
  assert.doesNotMatch(editFn.split("jd-edit-textarea")[1].split("textarea>")[0], /jd-prune-hit/);
}

const tests = [
  test_deleted_citizenship_marks_usc_gc_html,
  test_open_job_has_no_prune_regexes,
  test_copy_path_stays_plain,
  test_reason_maps_to_citizenship_regex,
  test_unable_to_sponsor_is_not_citizenship_highlight,
  test_css_orange_on_dark,
  test_evidence_wires_highlight_only_when_formatting_jd,
];
for (const fn of tests) {
  fn();
  console.log(`ok ${fn.name}`);
}
console.log("OK test_jd_prune_highlight");
