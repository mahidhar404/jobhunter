/* Always-on JD pay / YOE / work-mode / visa orange highlighting.
   Dummy jobs / fixture JD only — no applicant PII.
   Run: node dashboard/test_jd_tag_highlight.js */
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

function extractFrom(startTok, endTok) {
  const start = src.indexOf(startTok);
  if (start === -1) throw new Error(`missing ${startTok}`);
  const end = src.indexOf(endTok, start + 1);
  if (end === -1) throw new Error(`missing ${endTok}`);
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
  extractFrom("const CITIZENSHIP_OR_GC_REQUIREMENT_RE = ", "\nfunction foldAccents("),
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
  "pruneHighlightRegexesForReason",
  "jobPruneHighlightRegexes",
  "collectPruneMatchRanges",
  "finalizePruneHighlightHtml",
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
  collectJdHighlightRanges,
} = ctx;

function marked(htmlOut, snippet) {
  const re = new RegExp(
    `<mark class="jd-prune-hit">[^<]*${snippet.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}[^<]*</mark>`,
    "i",
  );
  return re.test(htmlOut);
}

function test_salary_yoe_remote_marked_on_open_job() {
  const jd = [
    "Dummy ML Engineer at Fixture Co.",
    "Compensation: $120,000 - $150,000 per year",
    "3+ years of experience required",
    "This is a fully remote role.",
    "Nice to have: Python.",
  ].join("\n");
  const job = { status: "discovered" };
  const rendered = formatJobDescriptionHtml(jd, jobPruneHighlightRegexes(job));
  assert.match(rendered, /<mark class="jd-prune-hit">/);
  assert.ok(marked(rendered, "$120,000"));
  assert.ok(marked(rendered, "3+ years of experience"));
  assert.ok(marked(rendered, "fully remote") || marked(rendered, "remote"));
  assert.doesNotMatch(rendered, /<mark class="jd-prune-hit">[^<]*Nice to have/);
}

function test_anduril_us_person_and_ts_marked_on_open_job() {
  const jd = [
    "Dummy Software Engineer - ML Infrastructure at Fixture Defense Co.",
    "U.S. Person status is required as this position needs to access export controlled data. Eligibility to obtain/maintain a US Top Secret clearance is also desirable.",
    "Build training clusters. No real applicant data.",
  ].join("\n");
  const job = { status: "discovered", id: "dummy-anduril-open" };
  const rendered = formatJobDescriptionHtml(jd, jobPruneHighlightRegexes(job));
  assert.ok(marked(rendered, "U.S. Person") || marked(rendered, "Person status"));
  assert.ok(marked(rendered, "export controlled"));
  assert.ok(marked(rendered, "Top Secret"));
  assert.doesNotMatch(rendered, /<mark class="jd-prune-hit">[^<]*Build training/);
}

function test_visa_phrases_marked_on_non_deleted_job() {
  const jd = [
    "Dummy AI Engineer at Fixture Co.",
    "We are unable to sponsor work visas.",
    "H-1B and OPT candidates welcome.",
    "Must be USC or GC.",
    "Green card holders may apply.",
    "Build models. No real applicant data.",
  ].join("\n");
  const job = { status: "discovered", id: "dummy-open-visa" };
  assert.equal(jobPruneHighlightRegexes(job).length, 0);
  const rendered = formatJobDescriptionHtml(jd, jobPruneHighlightRegexes(job));
  assert.ok(marked(rendered, "unable to sponsor"));
  assert.ok(marked(rendered, "H-1B"));
  assert.ok(marked(rendered, "OPT"));
  assert.ok(marked(rendered, "USC") || marked(rendered, "GC"));
  assert.ok(marked(rendered, "Green card"));
  assert.doesNotMatch(rendered, /<mark class="jd-prune-hit">[^<]*Build models/);
}

function test_citizenship_prune_regex_still_skips_unable_to_sponsor() {
  const rxs = pruneHighlightRegexesForReason("citizenship_or_greencard");
  const blob = "We are unable to sponsor visas for this role.";
  const hits = rxs.some((rx) => {
    const sticky = new RegExp(rx.source, "i");
    sticky.lastIndex = 0;
    return sticky.test(blob);
  });
  assert.ok(!hits, "unable to sponsor must not be a citizenship prune regex hit");
  assert.ok(rxs.some((rx) => rx.test("Visa: USC and GC only")));
}

function test_copy_path_stays_plain() {
  const jd = [
    "Compensation: $145,000",
    "5+ years of experience",
    "Hybrid — 3 days a week in the office",
    "Visa sponsorship is not available",
  ].join("\n");
  const job = {
    id: "dummy-open-tags",
    status: "discovered",
    title: "Dummy Staff ML Engineer",
    company: "Fixture Co",
    location: "Remote, US",
    job_description: jd,
  };
  ctx.jdCache = new Map();
  const copied = jdCopyText(job);
  assert.ok(copied.includes("Compensation: $145,000"));
  assert.ok(copied.includes("Visa sponsorship is not available"));
  assert.ok(!copied.includes("jd-prune-hit"));
  assert.ok(!copied.includes("<mark"));
  assert.ok(!copied.includes("&lt;"));
}

function test_deleted_prune_and_tags_share_one_mark() {
  const jd = "Visa: USC and GC only. Citizens only.";
  const job = { status: "deleted", deleted_reason: "citizenship_or_greencard" };
  const rendered = formatJobDescriptionHtml(jd, jobPruneHighlightRegexes(job));
  const opens = rendered.match(/<mark class="jd-prune-hit">/g) || [];
  const closes = rendered.match(/<\/mark>/g) || [];
  assert.equal(opens.length, closes.length);
  assert.doesNotMatch(rendered, /<mark class="jd-prune-hit"><mark class="jd-prune-hit">/);
  assert.ok(marked(rendered, "USC"));
}

function test_no_marks_on_plain_duties() {
  const jd = "Dummy role. Write Python. Collaborate with Fixture Co researchers.";
  const rendered = formatJobDescriptionHtml(jd);
  assert.doesNotMatch(rendered, /jd-prune-hit/);
}

function test_css_orange_on_dark() {
  assert.match(html, /\.jd-prune-hit\s*\{/);
  const rule = html.split(".jd-prune-hit")[1].split("}")[0];
  assert.match(rule, /var\(--orange\)/);
}

function test_evidence_auto_wires_helper() {
  assert.match(src, /function collectJdHighlightRanges\(/);
  assert.match(src, /function jdTagHighlightRegexes\(/);
  assert.match(src, /formatJobDescriptionHtml\(\s*text,\s*jobPruneHighlightRegexes\(job\)\s*\)/);
  assert.match(src, /collectJdHighlightRanges\(piece,/);
  const copyFn = extractFunction("jdCopyText");
  assert.doesNotMatch(copyFn, /formatJobDescriptionHtml/);
  const editFn = extractFunction("jdEvidenceHtml");
  assert.doesNotMatch(editFn.split("jd-edit-textarea")[1].split("textarea>")[0], /jd-prune-hit/);
}

function test_collect_helper_finds_salary_without_job() {
  const ranges = collectJdHighlightRanges("Base salary $120k–$150k", []);
  assert.ok(ranges.length >= 1);
  assert.ok(ranges[0][1] > ranges[0][0]);
}

const tests = [
  test_salary_yoe_remote_marked_on_open_job,
  test_anduril_us_person_and_ts_marked_on_open_job,
  test_visa_phrases_marked_on_non_deleted_job,
  test_citizenship_prune_regex_still_skips_unable_to_sponsor,
  test_copy_path_stays_plain,
  test_deleted_prune_and_tags_share_one_mark,
  test_no_marks_on_plain_duties,
  test_css_orange_on_dark,
  test_evidence_auto_wires_helper,
  test_collect_helper_finds_salary_without_job,
];
for (const fn of tests) {
  fn();
  console.log(`ok ${fn.name}`);
}
console.log("OK test_jd_tag_highlight");
