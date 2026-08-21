/* Work mode chip labels: sentence case (Remote / Hybrid / In-person).
   Dummy enums only — no applicant PII.
   Run: node dashboard/test_work_mode_label.js */
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("vm");

const src = fs.readFileSync(path.join(__dirname, "static", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "static", "index.html"), "utf8");

function extractFunction(name) {
  const token = `function ${name}(`;
  const start = src.indexOf(token);
  if (start === -1) throw new Error(`missing function ${name}`);
  const end = src.indexOf("\nfunction ", start + 1);
  return src.slice(start, end === -1 ? src.length : end);
}

const ctx = vm.createContext({
  detectWorkMode: () => "unknown",
  detectWorkModeFallback: () => "unknown",
});
vm.runInContext(extractFunction("formatWorkMode"), ctx);
vm.runInContext(extractFunction("resolveListWorkMode"), ctx);

assert.strictEqual(ctx.formatWorkMode("remote"), "Remote");
assert.strictEqual(ctx.formatWorkMode("hybrid"), "Hybrid");
assert.strictEqual(ctx.formatWorkMode("onsite"), "In-person");
assert.strictEqual(ctx.formatWorkMode("on-site"), "In-person");
assert.strictEqual(ctx.formatWorkMode("in-person"), "In-person");
assert.strictEqual(ctx.formatWorkMode("remote", true), "~Remote");
assert.notStrictEqual(ctx.formatWorkMode("onsite"), "In person");
assert.notStrictEqual(ctx.formatWorkMode("onsite"), "IN-PERSON");
assert.notStrictEqual(ctx.formatWorkMode("onsite"), "In-Person");

// Mode filter dropdown label must be "Remote" (never a typo like Renote).
assert.match(html, /<option value="remote">Remote<\/option>/);
assert.doesNotMatch(html, /<option[^>]*>\s*Renote\s*<\/option>/i);

// List chips must not force ALL CAPS via .tag { text-transform: uppercase }.
assert.match(html, /\.tag\.work-mode[\s\S]*?text-transform:\s*none/);
assert.match(html, /\.tag\.mode-tag[\s\S]*?text-transform:\s*none/);

// resolveListWorkMode prefers JD body over conflicting location.
ctx.detectWorkMode = ({ location, description }) => {
  const blob = `${location || ""} ${description || ""}`;
  const remote = /\bremote\b/i.test(blob);
  const onsite = /\bon-?site\b/i.test(blob);
  if (remote && onsite) return "unknown";
  if (remote) return "remote";
  if (onsite) return "onsite";
  return "unknown";
};
assert.strictEqual(
  ctx.resolveListWorkMode({
    title: "Dummy Role",
    location: "remote",
    description: "This role is on-site in San Mateo, CA.",
  }),
  "onsite",
);
assert.strictEqual(
  ctx.resolveListWorkMode({
    title: "Dummy Role",
    location: "Seattle, WA",
    description: "**Location**\n Remote\n",
  }),
  "remote",
);

console.log("OK test_work_mode_label");
