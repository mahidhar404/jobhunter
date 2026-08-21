/* Posted-date list filter windows + Today preset payload (node).
   Dummy jobs only — no applicant PII.
   Run: node dashboard/test_posted_date_filter.js */
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { datePostedTime, jobPostedDisplay } = require("./static/job_sort.js");

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
  datePostedTime,
  jobPostedDisplay,
  dateFilter: "",
  Date: class extends Date {
    static now() {
      return NOW;
    }
  },
});
vm.runInContext(extractFunction("stampedApproxPrefix"), ctx);
vm.runInContext(extractFunction("listFilterUnsurePasses"), ctx);
vm.runInContext(extractFunction("jobMatchesDateFilter"), ctx);
assert.strictEqual(typeof ctx.jobMatchesDateFilter, "function", "jobMatchesDateFilter missing");

function jobDaysAgo(days) {
  return { date_posted: new Date(NOW - days * DAY).toISOString() };
}

function matches(filter, job) {
  ctx.dateFilter = filter;
  return ctx.jobMatchesDateFilter(job);
}

assert.strictEqual(matches("", jobDaysAgo(40)), true, "All posted keeps old jobs");
assert.strictEqual(matches("1d", jobDaysAgo(0.5)), true, "1 day includes 12h");
assert.strictEqual(matches("1d", jobDaysAgo(1)), true, "1 day includes exactly 1d");
assert.strictEqual(matches("1d", jobDaysAgo(1.01)), false, "1 day excludes just over 1d");
assert.strictEqual(matches("2d", jobDaysAgo(2)), true, "2 days includes exactly 2d");
assert.strictEqual(matches("2d", jobDaysAgo(2.01)), false, "2 days excludes just over 2d");
assert.strictEqual(matches("3d", jobDaysAgo(3)), true, "3 days includes exactly 3d");
assert.strictEqual(matches("3d", jobDaysAgo(3.01)), false, "3 days excludes just over 3d");
assert.strictEqual(matches("7d", jobDaysAgo(7)), true, "Last 7d still includes exactly 7d");
assert.strictEqual(matches("7d", jobDaysAgo(7.01)), false, "Last 7d still excludes just over 7d");
assert.strictEqual(matches("2d", {}), true, "missing posted date is included in 2d");
assert.strictEqual(
  matches("2d", { date_posted_fallback: new Date(NOW - 40 * DAY).toISOString() }),
  true,
  "~ posted date outside 2d is still included",
);
assert.strictEqual(
  matches("2d", { date_posted: new Date(NOW - 40 * DAY).toISOString() }),
  false,
  "explicit posted date outside 2d is excluded",
);
assert.strictEqual(
  matches("2d", { created_at: new Date(NOW - 1 * DAY).toISOString() }),
  true,
  "created_at alone is undated (unsure) — still included, not treated as posted",
);
assert.strictEqual(
  jobPostedDisplay({ created_at: new Date(NOW - 1 * DAY).toISOString() }).time,
  null,
  "created_at must not become jobPostedDisplay time",
);
assert.strictEqual(
  matches("2d", { date_posted_fallback: new Date(NOW - 1 * DAY).toISOString() }),
  true,
  "real fallback within 2d is unsure (~) so included",
);
assert.strictEqual(matches("older", jobDaysAgo(31)), true, "older than 30d still works");
assert.strictEqual(matches("older", {}), true, "missing posted date is included in older");
assert.strictEqual(
  matches("older", { created_at: new Date(NOW - 40 * DAY).toISOString() }),
  true,
  "created_at alone is undated (unsure) — included in older, not a posted age",
);

const dateBlock = (html.split('id="date-filter"', 2)[1] || "").split("</select>", 1)[0];
assert.match(dateBlock, /<option value="1d">1 day<\/option>/);
assert.match(dateBlock, /<option value="2d">2 days<\/option>/);
assert.match(dateBlock, /<option value="3d">Last 3d<\/option>/);
assert.match(dateBlock, /<option value="7d">Last 7d<\/option>/);

assert.match(html, /id="filters-today"/);
assert.match(html, />Today</);

assert.match(src, /TODAY_FILTER_PRESET/);
const presetMatch = src.match(/const TODAY_FILTER_PRESET = \{([\s\S]*?)\};/);
assert.ok(presetMatch, "TODAY_FILTER_PRESET object missing");
const presetSrc = presetMatch[1];
assert.match(presetSrc, /source:\s*""/);
assert.match(presetSrc, /group:\s*"none"/);
assert.match(presetSrc, /sort:\s*"date"/);
assert.match(presetSrc, /mode:\s*""/);
assert.match(presetSrc, /yoe:\s*"le5"/);
assert.match(presetSrc, /date:\s*"2d"/);
assert.match(presetSrc, /salary:\s*""/);
assert.doesNotMatch(presetSrc, /status:/);
assert.match(presetSrc, /extras:\s*""/);
assert.match(presetSrc, /region:\s*"us"/);

console.log("OK test_posted_date_filter.js");
