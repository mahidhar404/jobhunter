/* JD dossier: show cached text immediately; short placeholder only when empty.
   Dummy jobs only — no applicant PII.
   Run: node dashboard/test_jd_instant_load.js */
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

const ctx = vm.createContext({
  jdCache: new Map(),
  jdEditJobId: null,
  jdEditDraft: "",
  jdEditSaving: false,
  escapeHtml: (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c])),
  escapeAttr: (s) => String(s ?? ""),
  jsStringEscape: (s) => String(s ?? "").replace(/'/g, "\\'"),
  jdIdentityHtml: () => "",
  formatJobDescriptionHtml: (t) => `BODY:${t}`,
  jobPruneHighlightRegexes: () => [],
});

vm.runInContext(extractFunction("jdEvidenceHtml"), ctx);

const job = { id: "j1", has_description: true, title: "Dummy" };

// Cache hit → instant body, no loading placeholder.
ctx.jdCache.set("j1", { loading: false, text: "Full dummy JD", error: null });
let html = ctx.jdEvidenceHtml(job);
assert.ok(html.includes("BODY:Full dummy JD"), html);
assert.ok(!html.includes("jd-loading"), html);

// Prefetch in flight with text already present → still show text.
ctx.jdCache.set("j1", { loading: true, text: "Prefetched JD", error: null });
html = ctx.jdEvidenceHtml(job);
assert.ok(html.includes("BODY:Prefetched JD"), html);
assert.ok(!html.includes("jd-loading"), html);

// Cold miss → short placeholder, not the old long copy.
ctx.jdCache.set("j1", { loading: true, text: "", error: null });
html = ctx.jdEvidenceHtml(job);
assert.ok(html.includes("jd-loading"), html);
assert.ok(!html.includes("Loading job description"), html);
assert.ok(html.includes("…"), html);

// Settled empty with has_description false path.
ctx.jdCache.clear();
html = ctx.jdEvidenceHtml({ id: "j2", has_description: false });
assert.ok(html.includes("jd-empty"), html);

console.log("ok");
