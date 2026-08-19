/* Resume PDF preview: poll re-render must not recreate/reload the iframe.
   Dummy fixture only — never real resume text.
   Run: node dashboard/test_resume_preview.js
*/
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const APP_JS = path.join(__dirname, "static", "app.js");
const src = fs.readFileSync(APP_JS, "utf8");

function extractFn(name) {
  const token = `function ${name}(`;
  const start = src.indexOf(token);
  if (start < 0) throw new Error(`missing function ${name}`);
  const end = src.indexOf("\nfunction ", start + 1);
  return src.slice(start, end < 0 ? undefined : end);
}

function makeDom() {
  const byId = new Map();

  function unregisterTree(node) {
    if (node.id) byId.delete(node.id);
    for (const child of node.childNodes.slice()) unregisterTree(child);
  }

  function createElement(tag) {
    const el = {
      tagName: String(tag).toUpperCase(),
      _id: "",
      className: "",
      title: "",
      parentNode: null,
      childNodes: [],
      attrs: Object.create(null),
      srcAssigned: 0,
      get id() { return this._id; },
      set id(value) { this.setAttribute("id", value); },
      get firstChild() { return this.childNodes[0] || null; },
      getAttribute(name) {
        if (name === "src" && this.attrs.src == null && this._src) return this._src;
        return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
      },
      setAttribute(name, value) {
        const v = String(value);
        this.attrs[name] = v;
        if (name === "id") {
          if (this._id && byId.get(this._id) === this) byId.delete(this._id);
          this._id = v;
          byId.set(v, this);
        }
        if (name === "src") this._src = v;
      },
      get src() { return this._src || this.attrs.src || ""; },
      set src(value) {
        this.srcAssigned += 1;
        this.setAttribute("src", value);
      },
      appendChild(child) {
        if (child.parentNode) child.parentNode.removeChild(child);
        child.parentNode = this;
        this.childNodes.push(child);
        return child;
      },
      removeChild(child) {
        const i = this.childNodes.indexOf(child);
        if (i >= 0) this.childNodes.splice(i, 1);
        child.parentNode = null;
        return child;
      },
      remove() {
        if (this.parentNode) this.parentNode.removeChild(this);
      },
      contains(node) {
        if (node === this) return true;
        return this.childNodes.some(c => c.contains(node));
      },
      querySelector(sel) {
        if (!sel || sel[0] !== "#") return null;
        const id = sel.slice(1);
        const walk = (n) => {
          if (n.id === id) return n;
          for (const c of n.childNodes) {
            const hit = walk(c);
            if (hit) return hit;
          }
          return null;
        };
        return walk(this);
      },
      set innerHTML(html) {
        for (const child of this.childNodes.slice()) unregisterTree(child);
        this.childNodes = [];
        const text = String(html == null ? "" : html);
        if (!text.trim()) return;
        const ids = [...text.matchAll(/\bid=["']([^"']+)["']/g)].map(m => m[1]);
        let parent = this;
        if (text.includes("resume-panel")) {
          const panel = createElement("div");
          panel.className = "resume-panel";
          this.appendChild(panel);
          parent = panel;
        }
        if (ids.includes("resume-preview-mount") || text.includes("resume-preview-mount")) {
          const mount = createElement("div");
          mount.setAttribute("id", "resume-preview-mount");
          mount.className = "resume-preview-mount";
          parent.appendChild(mount);
        }
      },
      get innerHTML() {
        return this.childNodes.map(c => c.id ? `<${c.tagName.toLowerCase()} id="${c.id}">` : `</>`).join("");
      },
    };
    return el;
  }

  const body = createElement("body");
  const document = {
    body,
    createElement,
    getElementById(id) { return byId.get(id) || null; },
  };
  return { document, createElement, byId };
}

const dummyJob = {
  id: "dummy-emed-1",
  title: "Analytics Engineer",
  company: "eMed Fixture",
  resume_path: "resumes/dummy/EMED_RESUME_47922.PDF",
  resume_display_name: "EMED_RESUME_47922.PDF",
  resume_on_disk: true,
};

const { document } = makeDom();
const ctx = {
  document,
  resumePanelJobId: dummyJob.id,
  jobHasDiskResume: (job) => !!(job && (job.resume_on_disk || job.resume_path)),
  resumeDisplayName: (job) => (job && job.resume_display_name) || "Resume",
};
vm.createContext(ctx);

for (const name of [
  "resumePreviewUrl",
  "resumePreviewIdentity",
  "canReuseResumePreviewFrame",
  "ensureDossierPreviewShell",
  "paintResumePreview",
  "mountResumePreview",
]) {
  vm.runInContext(extractFn(name), ctx);
}

const root = document.createElement("div");
root.setAttribute("id", "dossier");
document.body.appendChild(root);

ctx.ensureDossierPreviewShell(root);
const host = document.getElementById("resume-preview-host");
assert.ok(host, "preview host should exist");

const resumeHtml = `<div class="resume-panel">
  <div class="resume-head">
    <span class="micro">EMED_RESUME_47922.PDF · full preview</span>
    <a class="resume-open-tab" href="/resume/dummy-emed-1" target="_blank" rel="noopener">Open in new tab</a>
    <button type="button" class="resume-hide">Hide</button>
  </div>
  <div class="resume-preview-mount" id="resume-preview-mount"></div>
</div>`;

ctx.paintResumePreview(dummyJob, resumeHtml, true);
const frame1 = document.getElementById("resume-preview-frame");
const mount1 = document.getElementById("resume-preview-mount");
assert.ok(frame1, "iframe should mount on first paint");
assert.ok(mount1, "preview mount should exist");
const src1 = frame1.getAttribute("src");
assert.equal(src1, "/resume/dummy-emed-1");
assert.ok(!/\?[tT]=/.test(src1 || ""), "iframe src must not use a timestamp cache buster");
assert.equal(frame1.srcAssigned, 1);

// Poll re-render: same job + same resume_path must keep the same iframe node and src.
ctx.paintResumePreview(dummyJob, resumeHtml, true);
ctx.mountResumePreview(dummyJob);
const frame2 = document.getElementById("resume-preview-frame");
const mount2 = document.getElementById("resume-preview-mount");
assert.equal(frame2, frame1, "must not inject a new iframe on re-render");
assert.equal(mount2, mount1, "must keep the preview mount node");
assert.equal(frame2.parentNode, mount1, "iframe must stay in the original mount");
assert.equal(frame2.getAttribute("src"), src1, "iframe src must stay stable");
assert.equal(frame2.srcAssigned, 1, "must not reassign iframe.src on same resume");

// Different resume_path for the same job should remount once (new file on disk).
const updated = { ...dummyJob, resume_path: "resumes/dummy/EMED_RESUME_47923.PDF" };
ctx.paintResumePreview(updated, resumeHtml, true);
const frame3 = document.getElementById("resume-preview-frame");
assert.notEqual(frame3, frame1, "new resume file may recreate the iframe once");
assert.equal(frame3.getAttribute("src"), "/resume/dummy-emed-1");
assert.ok(!/\?[tT]=/.test(frame3.getAttribute("src") || ""));

console.log("OK test_resume_preview.js");

// Primary RESUME click during FILLING must preview, never the upload/clear alert.
const faceSrc = extractFn("executeResumeFace");
const faceAlerts = [];
const previews = [];
const pickerClicks = [];
const faceCtx = {
  jobs: [],
  ACTIVE_PROGRESS_STATUSES: new Set(["tailoring", "navigating", "filling", "resuming"]),
  jobHasDiskResume: (job) => !!(job && (job.resume_on_disk || job.resume_path)),
  previewJobResume: (jobId) => { previews.push(jobId); },
  alert: (msg) => { faceAlerts.push(String(msg)); },
  document: {
    getElementById(id) {
      if (id !== "resume-upload-input") return null;
      return { click() { pickerClicks.push(id); } };
    },
  },
};
vm.createContext(faceCtx);
vm.runInContext(faceSrc, faceCtx);

function resetFace() {
  faceAlerts.length = 0;
  previews.length = 0;
  pickerClicks.length = 0;
}

faceCtx.jobs = [{
  id: "outpost-1",
  status: "filling",
  resume_on_disk: true,
  resume_path: "resumes/dummy/Outpost_resume_07683.pdf",
}];
resetFace();
faceCtx.executeResumeFace("outpost-1");
assert.deepEqual(previews, ["outpost-1"], "FILLING + PDF on disk must preview");
assert.deepEqual(faceAlerts, [], "preview must not fire the upload/clear alert");
assert.deepEqual(pickerClicks, [], "preview must not open the file picker");

faceCtx.jobs = [{ id: "outpost-2", status: "filling", resume_on_disk: false }];
resetFace();
faceCtx.executeResumeFace("outpost-2");
assert.deepEqual(previews, [], "no PDF during FILLING must not preview");
assert.equal(faceAlerts.length, 1, "no PDF during FILLING still blocks upload");
assert.match(faceAlerts[0], /upload\/clear blocked/i);
assert.deepEqual(pickerClicks, []);

faceCtx.jobs = [{
  id: "outpost-3",
  status: "discovered",
  resume_on_disk: true,
  resume_path: "resumes/dummy/Outpost_resume_07683.pdf",
}];
resetFace();
faceCtx.executeResumeFace("outpost-3");
assert.deepEqual(previews, ["outpost-3"], "idle job with PDF still previews");
assert.deepEqual(faceAlerts, []);

faceCtx.jobs = [{ id: "outpost-4", status: "discovered", resume_on_disk: false }];
resetFace();
faceCtx.executeResumeFace("outpost-4");
assert.deepEqual(previews, []);
assert.deepEqual(faceAlerts, []);
assert.deepEqual(pickerClicks, ["resume-upload-input"], "idle job without PDF opens upload");

console.log("OK test_resume_preview.js executeResumeFace");
