/* List chips vs full-JD cache: stamp the loaded job id, not selectedId.
   Dummy jobs / fixture JD only — no applicant PII.
   Run: node dashboard/test_list_tag_stamp.js */
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

function extractFrom(startTok, endTok) {
  const start = src.indexOf(startTok);
  if (start === -1) throw new Error(`missing ${startTok}`);
  const end = src.indexOf(endTok, start + 1);
  if (end === -1) throw new Error(`missing ${endTok}`);
  return src.slice(start, end);
}

const ctx = vm.createContext({
  MAX_ACCEPTABLE_MIN_YOE: 6,
  jdCache: new Map(),
  jobs: [],
  selectedId: null,
});

vm.runInContext(
  extractConstThrough("SENIORITY_EXCLUDE_RE", "US_LOCATION_STRONG_RE"),
  ctx,
);
vm.runInContext(
  extractConstThrough("CLEARANCE_REQUIREMENT_RE", "INTEL_AGENCY_COMPANY_RE"),
  ctx,
);
vm.runInContext(
  extractFrom("const INTEL_AGENCY_COMPANY_RE = ", "\nconst YOE_OF_EXP = "),
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

for (const name of [
  "salarySane",
  "salaryIsHourly",
  "salaryIsFundingNoise",
  "salaryPairFromAmounts",
  "salaryBlob",
  "extractSalaryScan",
  "extractSalary",
  "extractSalaryFallback",
  "isIntelAgencyEmployer",
  "requiresSecurityClearance",
  "requiresUsPerson",
  "extractMinRequiredYoeWith",
  "extractMinRequiredYoe",
  "extractMinRequiredYoeFallback",
  "detectWorkModeWith",
  "detectWorkMode",
  "detectWorkModeFallback",
  "jobDescriptionText",
  "jobMinYoe",
  "jobMinYoeDisplay",
  "jobWorkModeDisplay",
  "jobSalaryDisplay",
  "yoeMatchIsEducationEquivalent",
  "formatWorkMode",
  "resolveListWorkMode",
  "stampListTagsFromCachedJd",
  "isDeterminedWorkMode",
  "mergeJobsPreservingListTags",
]) {
  vm.runInContext(extractFunction(name), ctx);
}

const FULL_JD = [
  "Dummy ML Engineer at Fixture Co.",
  "Compensation: $166,000 per year.",
  "3+ years of experience required.",
  "This is a fully remote role.",
  "U.S. Person status is required as this position needs to access export controlled data.",
].join("\n");

function test_live_extract_needs_full_jd_not_slim_preview() {
  const job = {
    id: "pay-hidden",
    title: "Dummy ML Engineer",
    salary_min: null,
    job_description: "",
  };
  ctx.jdCache.clear();
  assert.strictEqual(ctx.jobSalaryDisplay(job).min, null);
  ctx.jdCache.set("pay-hidden", { loading: false, text: FULL_JD, error: null });
  assert.strictEqual(ctx.jobSalaryDisplay(job).min, 166000);
}

function test_stamp_uses_loaded_job_id_not_selectedId() {
  const loaded = {
    id: "job-a",
    title: "Dummy ML Engineer",
    company: "Fixture Co",
    salary_min: null,
    salary_max: null,
    min_yoe: null,
    work_mode: "unknown",
    clearance: false,
    us_person: false,
  };
  const other = {
    id: "job-b",
    title: "Other Dummy Role",
    salary_min: null,
    min_yoe: null,
    work_mode: "unknown",
    clearance: false,
    us_person: false,
  };
  ctx.jobs = [loaded, other];
  ctx.selectedId = "job-b";
  ctx.jdCache = new Map([
    ["job-a", { loading: false, text: FULL_JD, error: null }],
  ]);
  const changed = ctx.stampListTagsFromCachedJd("job-a");
  assert.strictEqual(changed, true);
  assert.strictEqual(loaded.salary_min, 166000);
  assert.strictEqual(loaded.min_yoe, 3);
  assert.strictEqual(loaded.work_mode, "remote");
  assert.strictEqual(loaded.us_person, true);
  assert.strictEqual(other.salary_min, null);
  assert.strictEqual(other.min_yoe, null);
  assert.strictEqual(other.work_mode, "unknown");
  assert.strictEqual(other.us_person, false);
}

function test_loadJobDescription_refreshes_row_for_fetched_id() {
  const loadFn = extractFunction("loadJobDescription");
  assert.match(loadFn, /stampListTagsFromCachedJd\(jobId\)/);
  assert.match(loadFn, /refreshJobListRow\(jobId\)/);
  assert.doesNotMatch(loadFn, /stampListTagsFromCachedJd\(selectedId\)/);
}

function test_list_chip_uses_api_work_mode_without_jd_cache() {
  const job = {
    id: "jack-jill-api",
    title: "AI Research Engineer at BluePill AI",
    company: "Jack & Jill",
    location: "Seattle, WA",
    work_mode: "remote",
    job_description: "",
  };
  ctx.jdCache.clear();
  assert.strictEqual(ctx.jobWorkModeDisplay(job).mode, "remote");
  assert.strictEqual(ctx.formatWorkMode("remote"), "Remote");
  assert.strictEqual(ctx.formatWorkMode("onsite"), "In-person");
}

function test_poll_merge_preserves_local_work_mode_stamp() {
  ctx.jobs = [
    {
      id: "job-a",
      work_mode: "remote",
      salary_min: 166000,
      min_yoe: 3,
      clearance: false,
      us_person: true,
    },
  ];
  const incoming = [
    {
      id: "job-a",
      work_mode: "unknown",
      salary_min: null,
      min_yoe: null,
      clearance: false,
      us_person: false,
    },
  ];
  const merged = ctx.mergeJobsPreservingListTags(incoming);
  assert.strictEqual(merged[0].work_mode, "remote");
  assert.strictEqual(merged[0].salary_min, 166000);
  assert.strictEqual(merged[0].min_yoe, 3);
  assert.strictEqual(merged[0].us_person, true);
}

test_live_extract_needs_full_jd_not_slim_preview();
test_stamp_uses_loaded_job_id_not_selectedId();
test_loadJobDescription_refreshes_row_for_fetched_id();
test_list_chip_uses_api_work_mode_without_jd_cache();
test_poll_merge_preserves_local_work_mode_stamp();

function test_stamped_unknown_work_mode_ignores_remote_location() {
  const job = {
    id: "wm-unknown",
    title: "Dummy Engineer",
    location: "Remote, US",
    work_mode: "unknown",
    job_description: "",
  };
  ctx.jdCache.clear();
  assert.strictEqual(ctx.jobWorkModeDisplay(job).mode, "unknown");
  assert.strictEqual(ctx.jobWorkModeDisplay(job).approx, false);
}

function test_stamped_null_yoe_ignores_title_years() {
  const job = {
    id: "yoe-null",
    title: "Dummy Engineer (10+ years)",
    min_yoe: null,
    job_description: "",
  };
  ctx.jdCache.clear();
  assert.strictEqual(ctx.jobMinYoe(job), null);
  assert.strictEqual(ctx.jobMinYoeDisplay(job).n, null);
}

test_stamped_unknown_work_mode_ignores_remote_location();
test_stamped_null_yoe_ignores_title_years();
console.log("OK test_list_tag_stamp");
