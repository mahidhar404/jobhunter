const STAGES = ["discovered", "tailoring", "navigating", "filling", "ready_for_review", "applied"];
const STATUS_COLORS = {
  discovered: "#7a8494",
  tailoring: "#3ba7ff",
  navigating: "#3ba7ff",
  filling: "#3ba7ff",
  resuming: "#3ba7ff",
  stuck: "#e2a33d",
  blocked_captcha: "#e2a33d",
  ready_for_review: "#35c98f",
  applied: "#35c98f",
  cancelled: "#7a8494",
  skipped_manual: "#7a8494",
  skipped_duplicate: "#7a8494",
  skipped_contract: "#7a8494",
  skipped_easy_apply: "#7a8494",
};
const TERMINAL = ["applied", "skipped_duplicate", "skipped_contract", "skipped_easy_apply", "cancelled", "skipped_manual"];
// Most urgent first - used to pick the single indicator dot a collapsed
// company group shows, and to decide which groups float to the top of
// the list (see groupPriorityStatus/render()).
const PRIORITY_ORDER = [
  "stuck", "blocked_captcha", "filling", "navigating", "tailoring", "resuming",
  "ready_for_review", "discovered", "applied",
  "cancelled", "skipped_manual", "skipped_duplicate", "skipped_contract", "skipped_easy_apply",
];
const IN_PROGRESS_OR_NEEDS_ATTENTION = ["tailoring", "navigating", "filling", "resuming", "stuck", "blocked_captcha"];
// Management-track roles, not IC ones - hidden from a fresh, never-
// touched "discovered" listing. New discoveries are already blocked at
// the source (see dedup_listings.py's SENIORITY_EXCLUDE_HINTS); this
// catches anything already sitting in jobs.json from before that rule
// existed. Scoped to status === "discovered" only, on purpose - a title
// match is a substring check, not perfect (real example: "Sr. Lead
// Machine Learning Engineer (IC)" matched "lead" but is a genuine IC
// role with real completed work behind it) - once you've actually
// engaged with a job, it must never disappear just because of its title.
const SENIORITY_EXCLUDE_HINTS = ["lead", "manager", "vice president"];
const STALE_LISTING_MAX_AGE_DAYS = 30;

function isExcludedTitle(title) {
  const t = (title || "").toLowerCase();
  return SENIORITY_EXCLUDE_HINTS.some(h => t.includes(h));
}

function isStaleListing(job) {
  if (!job.date_posted) return false; // unknown date - benefit of the doubt, don't hide it
  const t = Date.parse(job.date_posted);
  if (Number.isNaN(t)) return false;
  const ageDays = (Date.now() - t) / 86400000;
  return ageDays > STALE_LISTING_MAX_AGE_DAYS;
}

function isHiddenUntouchedListing(job) {
  // Only ever applies to a job that's still exactly where discovery left
  // it - never to anything already started, stuck, reviewed, or applied.
  return job.status === "discovered" && (isExcludedTitle(job.title) || isStaleListing(job));
}

function statusPriorityIndex(status) {
  const idx = PRIORITY_ORDER.indexOf(status);
  return idx === -1 ? PRIORITY_ORDER.length : idx;
}

let jobs = [];
let selectedId = null;
let activityEvents = [];
let expandedCompanies = new Set();
let statusFilterMode = "open"; // "open" | "stuck" | "ready" | "applied" - set by clicking a stat

function statusLabel(s) {
  return (s || "unknown").replaceAll("_", " ");
}

function renderStats() {
  const counts = {};
  for (const j of jobs) counts[j.status] = (counts[j.status] || 0) + 1;
  const open = jobs.filter(j => !TERMINAL.includes(j.status) && !isHiddenUntouchedListing(j)).length;
  const stuckCount = (counts.stuck || 0) + (counts.blocked_captcha || 0);
  const items = [
    ["Open", open, "open"],
    ["Stuck", stuckCount, "stuck"],
    ["Ready", counts.ready_for_review || 0, "ready"],
    ["Applied", counts.applied || 0, "applied"],
  ];
  document.getElementById("stats").innerHTML = items.map(([l, n, mode]) => `
    <div class="stat ${statusFilterMode === mode ? "stat-active" : ""}" onclick="setStatusFilter('${mode}')" title="Click to show just these jobs">
      <div class="n" style="color:${l==="Stuck"&&n>0?"var(--amber)":"var(--text)"}">${n}</div><div class="l">${l}</div>
    </div>
  `).join("");
}

function setStatusFilter(mode) {
  statusFilterMode = mode;
  render();
}

function jobMatchesFilter(j) {
  switch (statusFilterMode) {
    case "stuck": return j.status === "stuck" || j.status === "blocked_captcha";
    case "ready": return j.status === "ready_for_review";
    case "applied": return j.status === "applied";
    case "open":
    default: return !TERMINAL.includes(j.status);
  }
}

function groupPriorityStatus(items) {
  let best = null, bestIdx = Infinity;
  for (const j of items) {
    const idx = PRIORITY_ORDER.indexOf(j.status);
    const effIdx = idx === -1 ? PRIORITY_ORDER.length : idx;
    if (effIdx < bestIdx) { bestIdx = effIdx; best = j.status; }
  }
  return best;
}

function datePostedSortKey(job) {
  const t = job.date_posted ? Date.parse(job.date_posted) : NaN;
  return Number.isNaN(t) ? -Infinity : t;
}

function jsStringEscape(s) {
  return String(s).replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

function renderJobRow(job, { nested = false, showCompany = true } = {}) {
  const color = STATUS_COLORS[job.status] || "#7a8494";
  const meta = [job.source, job.location, formatDate(job.date_posted)].filter(Boolean);
  return `
    <div class="row ${nested ? "nested" : ""} ${job.id === selectedId ? "active" : ""}" onclick="event.stopPropagation(); selectJob('${job.id}')">
      ${showCompany ? `<div class="company">${escapeHtml(job.company) || "(fetching details…)"}</div>` : ""}
      <div class="title">${escapeHtml(job.title) || ""}</div>
      ${meta.length ? `<div class="meta">${meta.map(m => `<span>${escapeHtml(m)}</span>`).join("")}</div>` : ""}
      <span class="badge" style="background:${color}22;color:${color}">${statusLabel(job.status)}</span>
    </div>
  `;
}

function toggleCompany(company) {
  if (expandedCompanies.has(company)) expandedCompanies.delete(company);
  else expandedCompanies.add(company);
  render();
}

function populateSourceFilter() {
  const sel = document.getElementById("source-filter");
  if (!sel) return;
  const current = sel.value;
  const sources = Array.from(new Set(jobs.map(j => j.source).filter(Boolean))).sort();
  sel.innerHTML = '<option value="">All sources</option>' +
    sources.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
  if (sources.includes(current)) sel.value = current;
}

function sortItems(items, sortBy) {
  if (sortBy === "company") items.sort((a, b) => (a.company || "").localeCompare(b.company || ""));
  else if (sortBy === "status") items.sort((a, b) => statusPriorityIndex(a.status) - statusPriorityIndex(b.status));
  else items.sort((a, b) => datePostedSortKey(b) - datePostedSortKey(a));
  return items;
}

function render() {
  renderStats();
  populateSourceFilter();
  const list = document.getElementById("list");
  const filterText = (document.getElementById("filter-input")?.value || "").trim().toLowerCase();
  const sourceFilter = document.getElementById("source-filter")?.value || "";
  const groupBy = document.getElementById("group-by")?.value || "company";
  const sortBy = document.getElementById("sort-by")?.value || "date";

  let openJobs = jobs.filter(jobMatchesFilter).filter(j => !isHiddenUntouchedListing(j));
  if (filterText) {
    openJobs = openJobs.filter(j =>
      (j.company || "").toLowerCase().includes(filterText) ||
      (j.title || "").toLowerCase().includes(filterText)
    );
  }
  if (sourceFilter) {
    openJobs = openJobs.filter(j => (j.source || "") === sourceFilter);
  }

  const emptyHtml = `<div class="empty">${jobs.length ? "No jobs match this filter." : "No jobs yet. Run discovery to populate the queue."}</div>`;

  if (groupBy === "none") {
    sortItems(openJobs, sortBy);
    // In-progress/needs-attention jobs always float to the top, regardless
    // of sort mode - that's what you actually want to see first. Array.sort
    // is stable, so this preserves the sortItems() order within each tier.
    openJobs.sort((a, b) => {
      const aIP = IN_PROGRESS_OR_NEEDS_ATTENTION.includes(a.status);
      const bIP = IN_PROGRESS_OR_NEEDS_ATTENTION.includes(b.status);
      return aIP === bIP ? 0 : (aIP ? -1 : 1);
    });
    list.innerHTML = openJobs.map(job => renderJobRow(job, {})).join("") || emptyHtml;
    renderDetail();
    return;
  }

  const groupKeyFn = groupBy === "source" ? (j => j.source || "(unknown source)") : (j => j.company || "(unknown)");
  const groups = new Map();
  for (const j of openJobs) {
    const key = groupKeyFn(j);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(j);
  }
  const groupEntries = Array.from(groups.entries()).map(([key, items]) => {
    sortItems(items, sortBy);
    const priorityStatus = groupPriorityStatus(items);
    return {
      key, items, priorityStatus,
      sortKey: datePostedSortKey(items[0]),
      inProgress: IN_PROGRESS_OR_NEEDS_ATTENTION.includes(priorityStatus),
    };
  });
  // In-progress/needs-attention groups always float to the top, regardless
  // of sort mode - that's what you actually want to see first.
  groupEntries.sort((a, b) => {
    if (a.inProgress !== b.inProgress) return a.inProgress ? -1 : 1;
    if (sortBy === "company") return a.key.localeCompare(b.key);
    if (sortBy === "status") return statusPriorityIndex(a.items[0].status) - statusPriorityIndex(b.items[0].status);
    return b.sortKey - a.sortKey;
  });

  list.innerHTML = groupEntries.map(({ key, items, priorityStatus }) => {
    if (items.length === 1) {
      return renderJobRow(items[0], {});
    }
    const expanded = expandedCompanies.has(key);
    const latest = items[0];
    const groupActive = !expanded && items.some(j => j.id === selectedId);
    const meta = groupBy === "source"
      ? [`${items.length} roles`]
      : [latest.source, `latest ${formatDate(latest.date_posted)}`].filter(Boolean);
    const dotColor = STATUS_COLORS[priorityStatus] || "#7a8494";
    return `
      <div class="row group-header ${groupActive ? "active" : ""}" onclick="toggleCompany('${jsStringEscape(key)}')">
        <span class="expand-icon">${expanded ? "▾" : "▸"}</span>
        <div class="company">
          ${!expanded && priorityStatus ? `<span class="status-dot" style="background:${dotColor}" title="${statusLabel(priorityStatus)}"></span>` : ""}
          ${escapeHtml(key) || "(fetching details…)"} <span class="count">${items.length} roles</span>
        </div>
        <div class="meta">${meta.map(m => `<span>${escapeHtml(m)}</span>`).join("")}</div>
      </div>
      ${expanded ? items.map(job => renderJobRow(job, { nested: true, showCompany: groupBy === "source" })).join("") : ""}
    `;
  }).join("") || emptyHtml;
  renderDetail();
}

function formatDate(d) {
  if (!d) return "";
  const t = Date.parse(d);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function selectJob(id) {
  selectedId = id;
  const job = jobs.find(j => j.id === id);
  if (job) expandedCompanies.add(job.company || "(unknown)");
  activityEvents = [];
  render();
  loadActivity();
}

function stepperHtml(job) {
  let stageIdx = STAGES.indexOf(job.status);
  if (job.status === "resuming") stageIdx = STAGES.indexOf("filling");
  if (stageIdx === -1) return ""; // stuck/blocked_captcha/cancelled/skipped_* aren't points on this stepper
  return `<div class="stepper">${STAGES.map((s, i) => `
    <div class="step ${i < stageIdx ? "done" : ""} ${i === stageIdx ? "current" : ""}">
      <div class="dot"></div>
      <div class="lbl">${statusLabel(s)}</div>
    </div>
  `).join("")}</div>`;
}

function renderDetail() {
  const detail = document.getElementById("detail");
  const job = jobs.find(j => j.id === selectedId);
  if (!job) {
    detail.innerHTML = '<div class="empty">Select a job to see its live status.</div>';
    return;
  }
  const color = STATUS_COLORS[job.status] || "#7a8494";
  const blocked = ["stuck", "blocked_captcha"].includes(job.status);
  let html = `
    <h2>${job.company || "(fetching details…)"} — ${job.title || ""}</h2>
    <div class="subhead">${[job.location, job.source, formatDate(job.date_posted)].filter(Boolean).join(" · ")} &nbsp;·&nbsp;
      <span class="badge" style="background:${color}22;color:${color}">${statusLabel(job.status)}</span>
    </div>
    ${stepperHtml(job)}
    <p>${job.status_detail || ""}</p>
    <p>
      <a href="${job.apply_url || job.job_url}" target="_blank">Application link ↗</a>
    </p>
    ${job.job_description ? `<div class="panel"><div class="panel-title">Job description</div><div style="max-height:160px;overflow-y:auto;white-space:pre-wrap;font-size:12px">${escapeHtml(job.job_description)}</div></div>` : ""}
  `;

  if (job.pending_command) {
    html += `
      <div class="panel command-box">
        <div class="panel-title">Agent wants to run a command not on its allowlist</div>
        <pre class="command">${escapeHtml(job.pending_command)}</pre>
        <div style="opacity:0.75">Approving remembers this command for next time (scoped to this agent only).</div>
        <div class="btn-row">
          <button class="primary" onclick="decideCommand('${job.id}', true)">Approve &amp; remember</button>
          <button class="danger" onclick="decideCommand('${job.id}', false)">Deny</button>
        </div>
      </div>
    `;
  } else if (blocked) {
    html += `
      <div class="panel command-box">
        <div class="panel-title">Agent needs your help</div>
        <div>${job.question || "(no question recorded)"}</div>
        <textarea id="answer" rows="3" placeholder="Type your answer..."></textarea>
        <div class="btn-row"><button class="primary" onclick="submitAnswer('${job.id}')">Send answer</button></div>
      </div>
    `;
  }

  html += `<div class="btn-row">`;
  if (["discovered", "stuck", "blocked_captcha", "cancelled"].includes(job.status)) {
    html += `<button class="primary" onclick="startJob('${job.id}')">${job.status === "discovered" ? "Start" : "Retry"}</button>`;
  }
  if (job.status === "ready_for_review") {
    html += `<button class="primary" onclick="markSubmitted('${job.id}')">Mark Submitted</button>`;
  }
  if (!TERMINAL.includes(job.status)) {
    html += `<button onclick="skipJob('${job.id}')">Skip</button>`;
    html += `<button class="danger" onclick="cancelJob('${job.id}')">Cancel run</button>`;
  }
  if (job.resume_path) {
    html += `<a class="btn-link" href="/resume/${job.id}" target="_blank">View Resume</a>`;
  }
  html += `<button onclick="deleteJob('${job.id}')">Delete</button>`;
  html += `</div>`;

  html += `<div class="panel"><div class="panel-title">Live activity</div><div class="feed" id="activity-feed"></div></div>`;

  if (job.qa_log && job.qa_log.length) {
    html += '<div class="panel"><div class="panel-title">History</div><div class="qa-log">';
    for (const qa of job.qa_log) {
      html += `<div class="item"><div class="q">${escapeHtml(qa.question || "")}</div><div class="a">→ ${escapeHtml(qa.answer)}</div></div>`;
    }
    html += "</div></div>";
  }

  detail.innerHTML = html;
  renderActivityFeed();
}

function renderActivityFeed() {
  const el = document.getElementById("activity-feed");
  if (!el) return;
  if (!activityEvents.length) {
    el.innerHTML = '<div style="color:var(--text-dim)">No activity recorded yet.</div>';
    return;
  }
  el.innerHTML = activityEvents.map(e => `
    <div class="ev"><span class="t">${e.time}</span><span class="k">${e.event}</span><span class="d">${escapeHtml(e.detail || "")}</span></div>
  `).join("");
  el.scrollTop = el.scrollHeight;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

async function submitAnswer(jobId) {
  const answer = document.getElementById("answer").value.trim();
  if (!answer) return;
  await fetch(`/api/jobs/${jobId}/answer`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
  await poll();
}

async function decideCommand(jobId, approve) {
  await fetch(`/api/jobs/${jobId}/approve_command`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approve }),
  });
  await poll();
}

async function startJob(jobId) {
  const res = await fetch(`/api/jobs/${jobId}/start`, { method: "POST", headers: {"Content-Type":"application/json"}, body: "{}" });
  if (res.status === 409) {
    const d = await res.json();
    alert(d.error || "Can't start - this job is already running.");
  }
  await poll();
}

async function cancelJob(jobId) {
  await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST", headers: {"Content-Type":"application/json"}, body: "{}" });
  await poll();
}

async function skipJob(jobId) {
  await fetch(`/api/jobs/${jobId}/skip`, { method: "POST", headers: {"Content-Type":"application/json"}, body: "{}" });
  await poll();
}

async function markSubmitted(jobId) {
  if (!confirm("Mark this as submitted? Only do this after you've actually clicked Submit yourself on the real application.")) return;
  await fetch(`/api/jobs/${jobId}/submitted`, { method: "POST", headers: {"Content-Type":"application/json"}, body: "{}" });
  await poll();
}

async function deleteJob(jobId) {
  if (!confirm("Delete this job entry permanently?")) return;
  await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
  if (selectedId === jobId) selectedId = null;
  await poll();
}

async function addJobByUrl() {
  const input = document.getElementById("add-job-url");
  const url = input.value.trim();
  if (!url) return;
  const res = await fetch("/api/jobs/add", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || "Could not add job.");
    return;
  }
  input.value = "";
  await poll();
  selectJob(data.id);
}

document.getElementById("add-job-url").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); addJobByUrl(); }
});

async function runDiscover() {
  const btn = document.getElementById("discover-btn");
  btn.disabled = true;
  btn.textContent = "Running…";
  const res = await fetch("/api/discover", { method: "POST", headers: {"Content-Type":"application/json"}, body: "{}" });
  if (res.status === 409) {
    const d = await res.json();
    alert(d.error || "Can't run discovery - it's already running.");
  }
  setTimeout(() => { btn.disabled = false; btn.textContent = "Run discovery"; }, 3000);
  await poll();
}

async function loadActivity() {
  if (!selectedId) return;
  try {
    const res = await fetch(`/api/jobs/${selectedId}/activity`);
    const data = await res.json();
    activityEvents = data.events || [];
    renderActivityFeed();
  } catch (e) { /* ignore */ }
}

let lastJobsJSON = null;

async function poll() {
  try {
    const res = await fetch("/api/jobs");
    const data = await res.json();
    const newJobsJSON = JSON.stringify(data.jobs || []);
    if (newJobsJSON === lastJobsJSON) return;
    lastJobsJSON = newJobsJSON;
    jobs = data.jobs || [];
    render();
  } catch (e) { console.error("poll failed", e); }
}

// ------------------------------------------------------------ Utility pane

function openUtil(tab) {
  const pane = document.getElementById("console-pane");
  pane.classList.remove("hidden");
  for (const b of document.querySelectorAll("#util-tabs button")) {
    b.classList.toggle("active", b.dataset.tab === tab);
  }
  for (const v of document.querySelectorAll(".util-view")) {
    v.classList.toggle("active", v.id === `view-${tab}`);
  }
  if (tab === "profile") loadProfile();
  if (tab === "allowlist") loadAllowlist();
}

async function runConsole(force) {
  const input = document.getElementById("console-input");
  const args = input.value.trim();
  if (!args) return;
  const log = document.getElementById("console-log");
  log.innerHTML += `<div class="console-cmd">$ openclaw ${escapeHtml(args)}</div>`;
  log.scrollTop = log.scrollHeight;
  const res = await fetch("/api/cli", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ args, confirmed: !!force }),
  });
  const data = await res.json();
  if (data.requires_confirm) {
    if (confirm(`"${args}" looks destructive/irreversible. Run it anyway?`)) {
      await runConsole(true);
    }
    return;
  }
  input.value = "";
  const out = data.output !== undefined ? data.output : (data.error || "(no output)");
  log.innerHTML += `<div class="console-out">${escapeHtml(out)}${data.timed_out ? "\n[killed: exceeded 45s console limit]" : ""}</div>`;
  log.scrollTop = log.scrollHeight;
}

document.getElementById("console-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); runConsole(); }
});

// -------------------------------------------------------------- Profile

async function loadProfile() {
  const res = await fetch("/api/profile");
  const data = await res.json();
  document.getElementById("profile-editor").value = JSON.stringify(data, null, 2);
}

async function saveProfile() {
  const raw = document.getElementById("profile-editor").value;
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    alert("Not valid JSON: " + e.message);
    return;
  }
  const res = await fetch("/api/profile", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(parsed),
  });
  if (res.ok) alert("Profile saved."); else alert("Save failed.");
}

// ------------------------------------------------------------- Allowlist

async function loadAllowlist() {
  const res = await fetch("/api/allowlist");
  const data = await res.json();
  const el = document.getElementById("allowlist-content");
  const entries = data.allowlist || [];
  el.innerHTML = `
    <div class="kv"><label>security</label><span>${data.security || "-"}</span></div>
    <div class="kv"><label>ask</label><span>${data.ask || "-"}</span></div>
    <div class="panel-title" style="margin-top:14px">Trusted commands (${entries.length})</div>
  ` + (entries.length ? entries.map(e => `
    <div class="panel" style="margin-top:8px">
      <pre class="command" style="margin:0">${escapeHtml(e.pattern)}</pre>
      ${e.lastUsedCommand ? `<div style="color:var(--text-dim);font-size:11px;margin-top:6px">last: ${escapeHtml(e.lastUsedCommand)}</div>` : ""}
    </div>
  `).join("") : '<div class="empty">No commands trusted yet.</div>');
}

// ------------------------------------------------------------------ Cron

let cronJobId = null;

async function loadCron() {
  try {
    const res = await fetch("/api/cron");
    const data = await res.json();
    const btn = document.getElementById("cron-btn");
    if (data.error) { btn.textContent = "Cron: n/a"; return; }
    cronJobId = data.id;
    btn.textContent = `Cron: ${data.enabled ? "ON" : "OFF"} (9am)`;
    btn.style.color = data.enabled ? "var(--green)" : "var(--text-dim)";
  } catch (e) { /* ignore */ }
}

async function toggleCron() {
  const btn = document.getElementById("cron-btn");
  const currentlyOn = btn.textContent.includes("ON");
  await fetch("/api/cron/toggle", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enable: !currentlyOn }),
  });
  await loadCron();
}

poll();
loadCron();
setInterval(poll, 3000);
setInterval(loadCron, 15000);
setInterval(() => { if (selectedId) loadActivity(); }, 4000);
