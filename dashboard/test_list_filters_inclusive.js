/* Inclusive list filters: missing metrics and ~approx values pass for
   YOE / pay / date / region / source; work-mode is categorical (Remote
   keeps Remote/~Remote only). Explicit mismatches still fail.
   Dummy jobs only — no applicant PII.
   Run: node dashboard/test_list_filters_inclusive.js */
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { jobPostedDisplay } = require("./static/job_sort.js");

const src = fs.readFileSync(path.join(__dirname, "static", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "static", "index.html"), "utf8");

function extractFunction(name) {
  const token = `function ${name}(`;
  const start = src.indexOf(token);
  if (start === -1) return "";
  const end = src.indexOf("\nfunction ", start + 1);
  return src.slice(start, end === -1 ? src.length : end);
}

const NOW = Date.parse("2026-08-18T12:00:00Z");
const DAY = 86400000;

const ctx = vm.createContext({
  jobPostedDisplay,
  Date: class extends Date {
    static now() {
      return NOW;
    }
  },
  jdCache: new Map(),
  workModeFilter: "",
  yoeFilter: "",
  dateFilter: "",
  salaryFilter: "",
  extrasFilter: "",
  sourceFilter: "",
  regionFilter: "",
  jobWorkModeDisplay(j) {
    return j._mode || { mode: "unknown", approx: false };
  },
  jobMinYoeDisplay(j) {
    return j._yoe || { n: null, approx: false };
  },
  jobSalaryDisplay(j) {
    return j._sal || { min: null, max: null, approx: false };
  },
  jobSourceNames(j) {
    const n = j.source || "";
    return n ? [n] : [];
  },
  applicationHref(j) {
    return j.apply_url || "";
  },
  regionForLocation(loc) {
    const s = String(loc || "").toLowerCase();
    if (s.includes("india") || s.includes("bangalore")) return "india";
    if (s.includes("new york") || s.includes("ny") || s === "nyc") return "us";
    return "unknown";
  },
  isClearlyNonUsLocation(loc) {
    const s = String(loc || "").toLowerCase();
    return s.includes("london") || s.includes("india") || s.includes("bangalore");
  },
  isIndiaLocation(loc) {
    const s = String(loc || "").toLowerCase();
    return s.includes("india") || s.includes("bangalore");
  },
});

for (const name of [
  "stampedApproxPrefix",
  "listFilterUnsurePasses",
  "jobHasDescription",
  "jobMatchesRegion",
  "jobMatchesWorkModeFilter",
  "jobMatchesYoeFilter",
  "jobMatchesDateFilter",
  "jobMatchesSalaryFilter",
  "jobMatchesExtrasFilter",
  "jobMatchesSourceFilter",
]) {
  const body = extractFunction(name);
  assert.ok(body, `${name} missing`);
  vm.runInContext(body, ctx);
}

function withFilter(key, value, fn) {
  const prev = ctx[key];
  ctx[key] = value;
  try {
    return fn();
  } finally {
    ctx[key] = prev;
  }
}

// --- Pipeline dropdown removed; No / incomplete JD extra present ---
assert.doesNotMatch(html, /id="status-filter"/);
assert.doesNotMatch(html, /Filter by pipeline status/);
assert.match(html, /id="extras-filter"/);
assert.match(html, /value="no_jd"/);
assert.match(html, />No \/ incomplete JD</);

// --- Work mode (categorical: stamped match only; unknown does not pass) ---
assert.match(html, /id="work-mode-filter"[\s\S]*?<option value="remote">Remote<\/option>/);
assert.doesNotMatch(html, /<option[^>]*>\s*Renote\s*<\/option>/i);
assert.strictEqual(
  withFilter("workModeFilter", "remote", () => ctx.jobMatchesWorkModeFilter({})),
  false,
  "missing work mode excluded from remote",
);
assert.strictEqual(
  withFilter("workModeFilter", "remote", () =>
    ctx.jobMatchesWorkModeFilter({ _mode: { mode: "unknown", approx: false } })),
  false,
  "unknown work mode excluded from remote",
);
assert.strictEqual(
  withFilter("workModeFilter", "remote", () =>
    ctx.jobMatchesWorkModeFilter({ _mode: { mode: "remote", approx: false } })),
  true,
  "exact remote included in remote",
);
assert.strictEqual(
  withFilter("workModeFilter", "remote", () =>
    ctx.jobMatchesWorkModeFilter({ _mode: { mode: "remote", approx: true } })),
  true,
  "~remote included in remote",
);
assert.strictEqual(
  withFilter("workModeFilter", "remote", () =>
    ctx.jobMatchesWorkModeFilter({ work_mode: "~remote" })),
  true,
  "stamped ~remote included in remote",
);
assert.strictEqual(
  withFilter("workModeFilter", "remote", () =>
    ctx.jobMatchesWorkModeFilter({ _mode: { mode: "onsite", approx: false } })),
  false,
  "explicit onsite excluded from remote",
);
assert.strictEqual(
  withFilter("workModeFilter", "remote", () =>
    ctx.jobMatchesWorkModeFilter({ _mode: { mode: "hybrid", approx: false } })),
  false,
  "explicit hybrid excluded from remote",
);
assert.strictEqual(
  withFilter("workModeFilter", "remote", () =>
    ctx.jobMatchesWorkModeFilter({ _mode: { mode: "hybrid", approx: true } })),
  false,
  "~hybrid excluded from remote",
);
assert.strictEqual(
  withFilter("workModeFilter", "remote", () =>
    ctx.jobMatchesWorkModeFilter({ work_mode: "~onsite" })),
  false,
  "stamped ~onsite excluded from remote",
);
assert.strictEqual(
  withFilter("workModeFilter", "unknown", () =>
    ctx.jobMatchesWorkModeFilter({ _mode: { mode: "unknown", approx: false } })),
  true,
  "unknown mode bucket keeps unknown",
);
assert.strictEqual(
  withFilter("workModeFilter", "unknown", () =>
    ctx.jobMatchesWorkModeFilter({ _mode: { mode: "remote", approx: true } })),
  false,
  "unknown mode bucket does not keep ~remote",
);

// --- YOE ---
assert.strictEqual(
  withFilter("yoeFilter", "le5", () => ctx.jobMatchesYoeFilter({})),
  true,
  "missing YOE included in ≤5",
);
assert.strictEqual(
  withFilter("yoeFilter", "le5", () =>
    ctx.jobMatchesYoeFilter({ _yoe: { n: 8, approx: true } })),
  true,
  "~8 YOE included in ≤5",
);
assert.strictEqual(
  withFilter("yoeFilter", "le5", () =>
    ctx.jobMatchesYoeFilter({ _yoe: { n: 8, approx: false } })),
  false,
  "explicit 8 YOE excluded from ≤5",
);
assert.strictEqual(
  withFilter("yoeFilter", "has", () => ctx.jobMatchesYoeFilter({})),
  false,
  "Has YOE still requires a value",
);

// --- Posted date ---
assert.strictEqual(
  withFilter("dateFilter", "2d", () => ctx.jobMatchesDateFilter({})),
  true,
  "missing posted date included in 2d",
);
assert.strictEqual(
  withFilter("dateFilter", "2d", () => ctx.jobMatchesDateFilter({
    date_posted_fallback: new Date(NOW - 40 * DAY).toISOString(),
  })),
  true,
  "~ posted date included even when outside 2d",
);
assert.strictEqual(
  withFilter("dateFilter", "2d", () => ctx.jobMatchesDateFilter({
    date_posted: new Date(NOW - 40 * DAY).toISOString(),
  })),
  false,
  "explicit old posted date excluded from 2d",
);
assert.strictEqual(
  withFilter("dateFilter", "older", () => ctx.jobMatchesDateFilter({})),
  true,
  "missing posted date included in older",
);

// --- Salary ---
assert.strictEqual(
  withFilter("salaryFilter", "ge100", () => ctx.jobMatchesSalaryFilter({})),
  true,
  "missing salary included in ≥$100K",
);
assert.strictEqual(
  withFilter("salaryFilter", "ge100", () =>
    ctx.jobMatchesSalaryFilter({ _sal: { min: 80000, approx: true } })),
  true,
  "~$80K included in ≥$100K",
);
assert.strictEqual(
  withFilter("salaryFilter", "ge100", () =>
    ctx.jobMatchesSalaryFilter({ _sal: { min: 80000, approx: false } })),
  false,
  "explicit $80K excluded from ≥$100K",
);

// --- Region / location ---
assert.strictEqual(
  withFilter("regionFilter", "us", () => ctx.jobMatchesRegion({})),
  true,
  "empty location included in US",
);
assert.strictEqual(
  withFilter("regionFilter", "us", () => ctx.jobMatchesRegion({ location: "~NY" })),
  true,
  "~NY included in US",
);
assert.strictEqual(
  withFilter("regionFilter", "us", () => ctx.jobMatchesRegion({ location: "London" })),
  false,
  "London excluded from US",
);
assert.strictEqual(
  withFilter("regionFilter", "us", () => ctx.jobMatchesRegion({ region: "india" })),
  false,
  "stamped India excluded from US",
);

// --- Source ---
assert.strictEqual(
  withFilter("sourceFilter", "greenhouse", () => ctx.jobMatchesSourceFilter({})),
  true,
  "missing source included",
);
assert.strictEqual(
  withFilter("sourceFilter", "greenhouse", () =>
    ctx.jobMatchesSourceFilter({ source: "lever" })),
  false,
  "explicit other source excluded",
);

// --- Extras: No / incomplete JD uses server jd_incomplete, not preview length ---
assert.strictEqual(ctx.jobHasDescription({ has_description: false }), false);
assert.strictEqual(ctx.jobHasDescription({ has_description: true }), true);
assert.strictEqual(ctx.jobHasDescription({ job_description: "  Build APIs.  " }), true);
assert.strictEqual(ctx.jobHasDescription({ job_description: "   " }), false);
assert.strictEqual(
  withFilter("extrasFilter", "no_jd", () =>
    ctx.jobMatchesExtrasFilter({ jd_incomplete: true })),
  true,
  "empty / truncated JD included",
);
assert.strictEqual(
  withFilter("extrasFilter", "no_jd", () =>
    ctx.jobMatchesExtrasFilter({
      jd_incomplete: true,
      has_description: true,
      job_description: "Short intro paragraph.",
    })),
  true,
  "truncated JD included even when preview has text",
);
assert.strictEqual(
  withFilter("extrasFilter", "no_jd", () =>
    ctx.jobMatchesExtrasFilter({
      jd_incomplete: false,
      has_description: true,
      job_description: "Short dashboard preview … [full text in resumes/<id>/jd_full.txt]",
    })),
  false,
  "short preview + complete jd_full excluded",
);
assert.strictEqual(
  withFilter("extrasFilter", "has_url", () =>
    ctx.jobMatchesExtrasFilter({})),
  false,
  "Has URL stays a presence filter",
);

console.log("OK test_list_filters_inclusive.js");
