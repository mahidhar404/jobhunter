/* Fielded list search: prefixes, comma-OR within a field, bare AND.
   Dummy jobs only — no applicant PII.
   Run: node dashboard/test_list_search.js */
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const src = fs.readFileSync(path.join(__dirname, "static", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "static", "index.html"), "utf8");

function extractFunction(name) {
  const token = `function ${name}(`;
  const start = src.indexOf(token);
  if (start === -1) return "";
  const end = src.indexOf("\nfunction ", start + 1);
  return src.slice(start, end === -1 ? src.length : end);
}

function extractConst(name) {
  const token = `const ${name} =`;
  const start = src.indexOf(token);
  if (start === -1) return "";
  // Object or string const ending at next blank-line-ish top-level const/function
  const from = src.slice(start);
  const m = from.match(/^const [^=]+=\s*[\s\S]*?;\n(?=\n|const |function |let |var )/);
  return m ? m[0] : "";
}

const ctx = vm.createContext({
  jdCache: new Map(),
  searchText: "",
  jdSearchTokenHits: null, // Map token -> Set(jobId)
  jobSourceNames(j) {
    const n = j.source || "";
    return n ? [n] : [];
  },
  jobWorkModeDisplay(j) {
    return j._mode || { mode: j.work_mode || "unknown", approx: false };
  },
  jobMinYoeDisplay(j) {
    return j._yoe || { n: j.min_yoe ?? null, approx: false };
  },
  jobSalaryDisplay(j) {
    return j._sal || { min: null, max: null, approx: false };
  },
  formatWorkMode(mode, approx = false) {
    if (!mode || mode === "unknown") return "";
    const label = mode.charAt(0).toUpperCase() + mode.slice(1);
    return approx ? `~${label}` : label;
  },
  formatYoeLabel(n, approx = false) {
    if (n == null) return "";
    return approx ? `~${n}y` : `${n}y`;
  },
  formatSalaryLabel() {
    return "";
  },
  deletedReasonCodes() {
    return [];
  },
  deletedReasonLabel() {
    return "";
  },
  queueBucket() {
    return "open";
  },
  fetch() {
    return Promise.resolve({ ok: false });
  },
  render() {},
});

for (const name of ["SEARCH_PLACEHOLDER", "SEARCH_FIELD_ALIAS"]) {
  const body = extractConst(name);
  assert.ok(body, `${name} missing`);
  vm.runInContext(body, ctx);
}

for (const name of [
  "tokenizeSearchInput",
  "parseSearchQuery",
  "jobSearchSlimHaystack",
  "jobSearchLocalJd",
  "searchFieldOrMatch",
  "searchTokenInJd",
  "searchTokensNeedingJd",
  "jobMatchesSearchQuery",
  "scheduleJdSearch",
  "jobMatchesSearch",
]) {
  const body = extractFunction(name);
  assert.ok(body, `${name} missing`);
  vm.runInContext(body, ctx);
}

// jdSearchTokenHits / jdSearchGen are lets in app.js — seed on ctx
vm.runInContext("var jdSearchGen = 0;", ctx);

function eq(actual, expected, msg) {
  assert.strictEqual(JSON.stringify(actual), JSON.stringify(expected), msg);
}

// Placeholder hints fielded syntax
assert.match(html, /company:\s*x\s+jd:\s*y/i);
assert.match(src, /company:\s*x\s+jd:\s*y/i);

// --- Parser: bare whitespace AND ---
{
  const q = ctx.parseSearchQuery("google seo");
  eq(q.bare, ["google", "seo"]);
  eq(q.fields, {});
}

// Bare commas are NOT OR — still AND tokens (comma ≈ whitespace)
{
  const q = ctx.parseSearchQuery("google, seo");
  eq(q.bare, ["google", "seo"]);
}

// --- Parser: field prefixes (case-insensitive), comma-OR within field ---
{
  const q = ctx.parseSearchQuery("company: google, meta jd: sponsorship, flexible, food");
  eq(q.fields.company, ["google", "meta"]);
  eq(q.fields.jd, ["sponsorship", "flexible", "food"]);
  eq(q.bare, []);
}

{
  const q = ctx.parseSearchQuery("TITLE:Engineer COMPANY:Acme");
  eq(q.fields.title, ["engineer"]);
  eq(q.fields.company, ["acme"]);
}

{
  const q = ctx.parseSearchQuery('company: "foo, bar" jd:baz');
  eq(q.fields.company, ["foo, bar"]);
  eq(q.fields.jd, ["baz"]);
}

{
  const q = ctx.parseSearchQuery("description: remote id: abc source: ashby location: nyc tag: clearance mode: remote");
  eq(q.fields.jd, ["remote"]);
  eq(q.fields.id, ["abc"]);
  eq(q.fields.source, ["ashby"]);
  eq(q.fields.location, ["nyc"]);
  eq(q.fields.tag, ["clearance", "remote"]);
}

// Compact field:value token
{
  const q = ctx.parseSearchQuery("company:google jd:seo");
  eq(q.fields.company, ["google"]);
  eq(q.fields.jd, ["seo"]);
}

// Mixed bare + fields
{
  const q = ctx.parseSearchQuery("staff company: google");
  eq(q.bare, ["staff"]);
  eq(q.fields.company, ["google"]);
}

const jobG = {
  id: "g1",
  company: "Google",
  title: "Staff SEO Engineer",
  location: "Mountain View, CA",
  source: "ashby",
  work_mode: "remote",
  clearance: false,
};
const jobM = {
  id: "m1",
  company: "Meta",
  title: "Data Scientist",
  location: "NYC",
  source: "greenhouse",
  work_mode: "hybrid",
};
const jobOther = {
  id: "o1",
  company: "Acme",
  title: "SWE",
  location: "Austin",
  source: "lever",
  work_mode: "onsite",
};

function matches(query, job, hits = null) {
  const parsed = ctx.parseSearchQuery(query);
  return ctx.jobMatchesSearchQuery(job, parsed, hits);
}

// Company OR
assert.strictEqual(matches("company: google, meta", jobG), true);
assert.strictEqual(matches("company: google, meta", jobM), true);
assert.strictEqual(matches("company: google, meta", jobOther), false);

// Title
assert.strictEqual(matches("title: seo", jobG), true);
assert.strictEqual(matches("title: seo", jobM), false);

// Cross-field AND + within-field OR
assert.strictEqual(
  matches("company: google, meta title: seo", jobG),
  true,
);
assert.strictEqual(
  matches("company: google, meta title: seo", jobM),
  false,
);

// Bare AND across slim haystack
assert.strictEqual(matches("google seo", jobG), true);
assert.strictEqual(matches("google seo", jobM), false);
assert.strictEqual(matches("meta", jobM), true);

// Source / location / id / tag(mode)
assert.strictEqual(matches("source: ashby", jobG), true);
assert.strictEqual(matches("location: nyc", jobM), true);
assert.strictEqual(matches("id: g1", jobG), true);
assert.strictEqual(matches("mode: remote", jobG), true);
assert.strictEqual(matches("tag: remote", jobG), true);
assert.strictEqual(matches("mode: hybrid", jobG), false);

// JD via local cache — OR within jd:
ctx.jdCache.set("g1", {
  loading: false,
  text: "We offer visa sponsorship and flexible hours. Free food.",
  error: null,
});
assert.strictEqual(matches("jd: sponsorship, flexible, food", jobG), true);
assert.strictEqual(matches("jd: sponsorship", jobM), false);
assert.strictEqual(
  matches("company: google, meta jd: sponsorship, flexible, food", jobG),
  true,
);
assert.strictEqual(
  matches("company: google, meta jd: sponsorship, flexible, food", jobM),
  false,
);

// JD via server token hits when not in cache
ctx.jdCache.clear();
const hits = new Map([
  ["sponsorship", new Set(["m1"])],
  ["flexible", new Set(["m1"])],
]);
assert.strictEqual(
  matches("jd: sponsorship, food", jobM, hits),
  true,
  "OR: sponsorship hit is enough",
);
assert.strictEqual(
  matches("jd: food", jobM, hits),
  false,
  "no local/server hit for food",
);

// Bare token can be satisfied by JD hit
assert.strictEqual(matches("sponsorship", jobM, hits), true);
assert.strictEqual(matches("sponsorship missing", jobM, hits), false);

// jobMatchesSearch uses globals
ctx.searchText = "company: google";
ctx.jdSearchTokenHits = null;
assert.strictEqual(ctx.jobMatchesSearch(jobG), true);
assert.strictEqual(ctx.jobMatchesSearch(jobOther), false);

console.log("OK test_list_search");
