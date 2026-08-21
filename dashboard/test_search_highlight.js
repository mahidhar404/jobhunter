/* Green search-hit highlighting for OmniDex list + JD.
   Dummy strings only — no applicant PII.
   Run: node dashboard/test_search_highlight.js */
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

function extractConst(name) {
  const token = `const ${name} =`;
  const start = src.indexOf(token);
  if (start === -1) return "";
  const from = src.slice(start);
  const m = from.match(/^const [^=]+=\s*[\s\S]*?;\n(?=\n|const |function |let |var )/);
  return m ? m[0] : "";
}

const ctx = vm.createContext({
  searchText: "",
  escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  },
});

for (const name of ["SEARCH_PLACEHOLDER", "SEARCH_FIELD_ALIAS"]) {
  const body = extractConst(name);
  assert.ok(body, `${name} missing`);
  vm.runInContext(body, ctx);
}

vm.runInContext(
  (() => {
    const token = "const JD_PRUNE_MARK_OPEN = ";
    const start = src.indexOf(token);
    if (start === -1) throw new Error("missing JD_PRUNE_MARK_OPEN");
    // Through SEARCH_MARK_CLOSE const, stop before first helper that follows in source
    // (searchNeedlesForSurface). Extract OPEN/CLOSE consts only via consecutive consts.
    const end = src.indexOf("\n/**\n * Needles for a rendered surface", start);
    if (end === -1) {
      const end2 = src.indexOf("\nfunction searchNeedlesForSurface(", start);
      if (end2 === -1) throw new Error("missing searchNeedlesForSurface after mark consts");
      return src.slice(start, end2);
    }
    return src.slice(start, end);
  })(),
  ctx,
);

for (const name of [
  "tokenizeSearchInput",
  "parseSearchQuery",
  "mergePruneMatchRanges",
  "searchNeedlesForSurface",
  "activeSearchNeedles",
  "collectSearchMatchRanges",
  "applySearchHighlightMarks",
  "finalizeSearchHighlightHtml",
  "applyLayeredHighlightMarks",
  "highlightSearchInText",
  "finalizePruneHighlightHtml",
  "formatJdInline",
]) {
  vm.runInContext(extractFunction(name), ctx);
}

assert.match(html, /mark\.search-hit/, "CSS must define mark.search-hit");
assert.match(html, /--green/, "palette green expected for search hits");

function setSearch(q) {
  ctx.searchText = q;
}

function test_bare_term_wraps_title_and_company() {
  setSearch("google");
  const co = ctx.highlightSearchInText("Alphabet Google Inc", "company");
  assert.match(co, /<mark class="search-hit">Google<\/mark>/);
  assert.doesNotMatch(co, /company:/i);

  const title = ctx.highlightSearchInText("Senior Google Cloud Engineer", "title");
  assert.match(title, /<mark class="search-hit">Google<\/mark>/);
}

function test_company_field_only_on_company_surface() {
  setSearch("company: google");
  const co = ctx.highlightSearchInText("Google DeepMind", "company");
  assert.match(co, /<mark class="search-hit">Google<\/mark>/);

  const title = ctx.highlightSearchInText("Google Cloud Engineer", "title");
  assert.doesNotMatch(title, /search-hit/);
  assert.equal(title, "Google Cloud Engineer");
}

function test_jd_field_only_on_jd_surface() {
  setSearch("jd: sponsorship");
  const jd = ctx.formatJdInline("We offer visa sponsorship for this role.", []);
  assert.match(jd, /<mark class="search-hit">sponsorship<\/mark>/i);

  const co = ctx.highlightSearchInText("Sponsorship Corp", "company");
  assert.doesNotMatch(co, /search-hit/);
}

function test_empty_search_no_marks() {
  setSearch("");
  const co = ctx.highlightSearchInText("Google", "company");
  assert.equal(co, "Google");
  assert.doesNotMatch(co, /search-hit/);

  setSearch("   ");
  const title = ctx.highlightSearchInText("Engineer", "title");
  assert.equal(title, "Engineer");
}

function test_escaping_safe_with_script_in_title() {
  setSearch("script");
  const htmlOut = ctx.highlightSearchInText('<script>alert(1)</script>', "title");
  assert.doesNotMatch(htmlOut, /<script>/i);
  assert.match(htmlOut, /&lt;/);
  assert.match(htmlOut, /<mark class="search-hit">script<\/mark>/i);
  // Needle wraps the escaped text content, not raw tags
  assert.ok(htmlOut.includes("&lt;<mark class=\"search-hit\">script</mark>&gt;"));
}

function test_case_insensitive() {
  setSearch("SPONSOR");
  const jd = ctx.formatJdInline("Visa Sponsorship available.", [], "jd");
  assert.match(jd, /<mark class="search-hit">Sponsor<\/mark>/);
}

function test_needles_are_values_not_prefixes() {
  const parsed = ctx.parseSearchQuery("company: acme jd: visa");
  assert.deepEqual(ctx.searchNeedlesForSurface(parsed, "company"), ["acme"]);
  assert.deepEqual(ctx.searchNeedlesForSurface(parsed, "jd"), ["visa"]);
  assert.deepEqual(ctx.searchNeedlesForSurface(parsed, "title"), []);
}

const tests = [
  test_bare_term_wraps_title_and_company,
  test_company_field_only_on_company_surface,
  test_jd_field_only_on_jd_surface,
  test_empty_search_no_marks,
  test_escaping_safe_with_script_in_title,
  test_case_insensitive,
  test_needles_are_values_not_prefixes,
];

let failed = 0;
for (const t of tests) {
  try {
    t();
    console.log(`ok  ${t.name}`);
  } catch (err) {
    failed++;
    console.error(`FAIL ${t.name}:`, err.message);
  }
}
if (failed) {
  console.error(`\n${failed} failed`);
  process.exit(1);
}
console.log(`\n${tests.length} passed`);
