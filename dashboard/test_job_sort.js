/* Focused tests for the Posted sort comparator (node dashboard/test_job_sort.js). */
const assert = require("node:assert");
const { compareByPosted, datePostedTime } = require("./static/job_sort.js");

function order(jobs) {
  return jobs.slice().sort(compareByPosted).map(j => j.id);
}

// Newest posted date first.
assert.deepStrictEqual(
  order([
    { id: "old", date_posted: "2026-07-28" },
    { id: "new", date_posted: "2026-08-05" },
    { id: "mid", date_posted: "2026-08-01" },
  ]),
  ["new", "mid", "old"],
);

// ISO timestamps and plain YYYY-MM-DD compare on the same scale.
assert.deepStrictEqual(
  order([
    { id: "date-only", date_posted: "2026-08-04" },
    { id: "timestamp", date_posted: "2026-08-05T09:00:00Z" },
  ]),
  ["timestamp", "date-only"],
);

// Jobs without date_posted use created_at (discovery date) for posted sort.
assert.deepStrictEqual(
  order([
    { id: "missing", created_at: "2026-08-05T12:00:00Z" },
    { id: "invalid", date_posted: "not-a-date", created_at: "2026-08-05T12:00:00Z" },
    { id: "posted", date_posted: "2026-07-01" },
  ]),
  ["invalid", "missing", "posted"],
);
assert.strictEqual(datePostedTime({ date_posted: "not-a-date" }), null);
assert.strictEqual(datePostedTime({}), null);
assert.ok(
  datePostedTime({ created_at: "2026-08-05T12:00:00Z" }) > datePostedTime({ date_posted: "2026-07-01" }),
);

// Equal posted dates fall back to updated_at/created_at, newest first.
assert.deepStrictEqual(
  order([
    { id: "stale", date_posted: "2026-08-04", updated_at: "2026-08-04T01:00:00Z" },
    { id: "fresh", date_posted: "2026-08-04", updated_at: "2026-08-05T01:00:00Z" },
  ]),
  ["fresh", "stale"],
);

// Fully tied rows use title then id so ordering is deterministic.
assert.deepStrictEqual(
  order([
    { id: "b", title: "Data Engineer", date_posted: "2026-08-04" },
    { id: "a", title: "AI Engineer", date_posted: "2026-08-04" },
    { id: "c", title: "AI Engineer", date_posted: "2026-08-04" },
  ]),
  ["a", "c", "b"],
);

// Comparator is consistent when every row lacks a posted date (no NaN).
const unknownOnly = [
  { id: "z", title: "Zeta" },
  { id: "y", title: "Alpha" },
];
assert.deepStrictEqual(order(unknownOnly), ["y", "z"]);
assert.strictEqual(Number.isNaN(compareByPosted(unknownOnly[0], unknownOnly[1])), false);

// A Built In row with freshly extracted datePosted outranks older known rows.
assert.deepStrictEqual(
  order([
    { id: "ats", date_posted: "2026-07-30" },
    { id: "builtin", date_posted: "2026-08-05", source: "builtin" },
  ]),
  ["builtin", "ats"],
);

// Integration: run the real sortItems from each shipped UI file against
// job_sort.js loaded as globals, mirroring the browser's script order.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const staticDir = path.join(__dirname, "static");
const ctx = vm.createContext({ console });
vm.runInContext(fs.readFileSync(path.join(staticDir, "job_sort.js"), "utf8"), ctx);

for (const file of ["app.js"]) {
  const src = fs.readFileSync(path.join(staticDir, file), "utf8");
  const start = src.indexOf("function sortItems(");
  assert.notStrictEqual(start, -1, `sortItems not found in ${file}`);
  vm.runInContext(src.slice(start, src.indexOf("\n}\n", start) + 3), ctx);
  const sorted = vm.runInContext("sortItems", ctx)([
    { id: "no-date", created_at: "2026-08-05T12:00:00Z" },
    { id: "old", date_posted: "2026-07-28" },
    { id: "newest", date_posted: "2026-08-05T08:00:00Z" },
    { id: "mid", date_posted: "2026-08-01" },
  ], "date").map(j => j.id);
  assert.deepStrictEqual(sorted, ["no-date", "newest", "mid", "old"], file);
}

console.log("ok: job sort tests passed");
