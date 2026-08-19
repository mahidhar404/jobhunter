/* Company applied-count matching (node dashboard/test_company_key.js).
   Dummy company names only — no applicant PII. */
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const src = fs.readFileSync(path.join(__dirname, "static", "app.js"), "utf8");

function extractFunction(name) {
  const token = `function ${name}(`;
  const start = src.indexOf(token);
  if (start === -1) return "";
  const end = src.indexOf("\nfunction ", start + 1);
  return src.slice(start, end === -1 ? src.length : end);
}

const ctx = vm.createContext({
  console,
  Map,
  jobs: [],
  companyAppliedCounts: new Map(),
  groupBy: "company",
});

for (const name of [
  "normalizeCompanyName",
  "companyKey",
  "rebuildCompanyAppliedCounts",
  "companyApplyCountLookup",
  "companyApplyCountBadgeHtml",
  "escapeAttr",
  "jobGroupKey",
]) {
  const fn = extractFunction(name);
  if (fn) vm.runInContext(fn, ctx);
}

assert.strictEqual(typeof ctx.companyKey, "function", "companyKey missing");

function key(company) {
  return ctx.companyKey({ company });
}

// Bright Vision applied vs Bright Vision Technologies Open listings.
assert.strictEqual(
  key("Bright Vision"),
  key("Bright Vision Technologies"),
  `applied ${JSON.stringify(key("Bright Vision"))} vs open ${JSON.stringify(key("Bright Vision Technologies"))}`,
);
assert.strictEqual(
  typeof ctx.normalizeCompanyName,
  "function",
  "normalizeCompanyName missing — company keys must strip Inc/LLC/Technologies",
);
assert.strictEqual(key("Bright Vision"), "brightvision");
assert.strictEqual(key("Bright Vision Technologies"), "brightvision");
assert.notStrictEqual(key("Apple"), key("Apple Hospital"));

// Legal suffixes, punctuation, case, extra spaces.
assert.strictEqual(key("Acme Inc."), key("Acme"));
assert.strictEqual(key("Acme, LLC"), key("ACME"));
assert.strictEqual(key("  Bright   Vision  Technologies, Inc.  "), key("Bright Vision"));

// Persist company_key; JS normalize is only the fallback.
assert.strictEqual(
  ctx.companyKey({ company: "Other Corp", company_key: "brightvision" }),
  "brightvision",
);
assert.strictEqual(
  ctx.companyKey({ company: "Bright Vision Technologies", company_key: "" }),
  "brightvision",
);

// Applied "Bright Vision" must badge Open "Bright Vision Technologies" cards.
ctx.jobs = [
  { id: "applied-1", status: "applied", company: "Bright Vision" },
  { id: "open-1", status: "discovered", company: "Bright Vision Technologies" },
  { id: "open-2", status: "discovered", company: "Bright Vision Technologies" },
];
ctx.rebuildCompanyAppliedCounts();
assert.strictEqual(ctx.companyApplyCountLookup({ company: "Bright Vision Technologies" }), 1);
assert.strictEqual(ctx.companyApplyCountLookup("Bright Vision Technologies"), 1);
const badge = ctx.companyApplyCountBadgeHtml({ company: "Bright Vision Technologies" });
assert.match(badge, /class="tag applied-count"/);
assert.match(badge, />1x</);

// Persisted keys: applied Bright Vision counts on discovered Bright Vision Technologies.
ctx.jobs = [
  { id: "applied-1", status: "applied", company: "Bright Vision", company_key: "brightvision" },
  {
    id: "open-1",
    status: "discovered",
    company: "Bright Vision Technologies",
    company_key: "brightvision",
  },
];
ctx.rebuildCompanyAppliedCounts();
assert.strictEqual(
  ctx.companyApplyCountLookup({
    company: "Bright Vision Technologies",
    company_key: "brightvision",
  }),
  1,
);

assert.strictEqual(typeof ctx.jobGroupKey, "function", "jobGroupKey missing");
assert.strictEqual(
  ctx.jobGroupKey({ company: "Bright Vision Technologies", company_key: "brightvision" }),
  "brightvision",
);
assert.strictEqual(
  ctx.jobGroupKey({ company: "Bright Vision" }),
  ctx.jobGroupKey({ company: "Bright Vision Technologies" }),
);

const { execFileSync } = require("node:child_process");
const pyJson = execFileSync(
  process.env.PYTHON || "python3",
  [
    "-c",
    "from text_normalize import normalize_company; import json; names=['Bright Vision','Bright Vision Technologies','Acme Inc.','Apple','Apple Hospital']; print(json.dumps({n: normalize_company(n) for n in names}))",
  ],
  { cwd: path.join(__dirname, "..", "scripts"), encoding: "utf8" },
);
const pyKeys = JSON.parse(pyJson);
for (const [name, expected] of Object.entries(pyKeys)) {
  assert.strictEqual(ctx.normalizeCompanyName(name), expected, `JS/Python parity for ${name}`);
}

const listSrc = src.slice(
  src.indexOf("function renderList("),
  src.indexOf("\nfunction ", src.indexOf("function renderList(") + 1),
);
assert.match(listSrc, /companyKey\(j\)/);

// List-card path (not dossier-only).
const row = src.slice(
  src.indexOf("function renderJobRow("),
  src.indexOf("\nfunction ", src.indexOf("function renderJobRow(") + 1),
);
assert.match(row, /class="co"/);
assert.match(row, /companyApplyCountBadgeHtml\(job\)/);

console.log("OK test_company_key.js");
