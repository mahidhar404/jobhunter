#!/usr/bin/env python3
"""Local-only command-center dashboard for the job-hunter agent.

No external deps (stdlib http.server). Reads/writes jobs.json as the
source of truth. Answering a stuck job resumes that job's own agent
session via `openclaw agent --agent job-hunter --session-key <key> --message <answer>`.
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import fcntl
import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "fastfill"))
from partyrock_config import (  # noqa: E402
    load_partyrock_urls,
    partyrock_mode_label,
    partyrock_url,
)
from partyrock_tabs import (  # noqa: E402
    close_idle_partyrock_tabs,
    close_job_partyrock_tab,
)
from chrome_for_testing import (  # noqa: E402
    ensure_partyrock_browser_direct,
)
# OpenClaw-free replacement modules (sibling files in dashboard/). These make
# the whole pipeline run with the `openclaw` binary/runtime completely absent:
#   agent_runner   → direct DeepSeek tool-loop (replaces `openclaw agent`)
#   scheduler_mod  → in-process daily discovery (replaces `openclaw cron`)
#   run_guard      → local flock double-start guard (replaces `sessions list`)
#   approvals_store→ local exec-approvals JSON (replaces `approvals allowlist add`)
import agent_runner  # noqa: E402
import approvals_store  # noqa: E402
import run_guard  # noqa: E402
import scheduler as scheduler_mod  # noqa: E402
from stats_aggregate import aggregate_stats  # noqa: E402
from copy_kit import build_copy_kit  # noqa: E402
from apply_urls import normalize_url  # noqa: E402
from blocked_urls import (  # noqa: E402
    block_deleted_job,
    block_deleted_jobs_batch,
    is_url_blocked,
    unblock_job,
)
from resume_publish import (  # noqa: E402
    conventional_resume_filename,
    publish_resume_to_by_company,
)
from jobs_lock import (  # noqa: E402
    JobsWriteRefused,
    backup_jobs_file,
    jobs_list_count,
    locked_jobs_for_write as _jl_locked_jobs_for_write,
    refuse_jobs_collapse,
)
import jobs_lock as _jobs_lock_mod  # noqa: E402
from text_normalize import stamp_company_key, backfill_company_keys  # noqa: E402

JOBS_FILE = ROOT / "jobs.json"
# Same lock file scripts/jobs_lock.py uses - update_job.py and
# write_discovered_jobs.py run as separate OS processes with no visibility
# into this server's in-memory _lock, so a real file lock is what actually
# keeps a status update here from racing a discovery run's bulk write (or
# vice versa). _lock still serializes this process's own threads; this
# additionally guards against every other process that touches the file.
JOBS_LOCK_FILE = JOBS_FILE.with_suffix(".json.lock")
# Keep module paths aligned so flock + RMW hit the same files as scripts/.
_jobs_lock_mod.JOBS_FILE = JOBS_FILE
_jobs_lock_mod.LOCK_FILE = JOBS_LOCK_FILE
PROFILE_FILE = ROOT / "profile.json"
STATIC_DIR = Path(__file__).parent / "static"


def _resolve_bin(env_var: str, name: str, default: str) -> str:
    """Resolve an external binary: explicit env override → PATH lookup →
    the macOS Homebrew default. Keeps the existing macOS behavior as the
    final fallback while letting Linux/containers point at their own path
    (or find it on PATH). A missing binary is not fatal here — the value is
    only invoked on demand, so the dashboard still starts if it's absent."""
    override = (os.environ.get(env_var) or "").strip()
    if override:
        return override
    found = shutil.which(name)
    if found:
        return found
    return default


TECTONIC_BIN = _resolve_bin("JOBHUNTER_TECTONIC_BIN", "tectonic", "/opt/homebrew/bin/tectonic")
PYTHON_BIN = str(ROOT / ".venv" / "bin" / "python3")
SKYVERN_PYTHON = ROOT / "skyvern_runtime" / "venv" / "bin" / "python"
FASTFILL_SCRIPT = ROOT / "scripts" / "fastfill" / "fast_fill.py"
HYBRID_FILL_SCRIPT = ROOT / "skyvern_runtime" / "scripts" / "hybrid_fill.py"
SCOUT_SCRIPT = ROOT / "scripts" / "scout.py"
LISTINGS_DIR = ROOT / "listings"
EXEC_APPROVALS_FILE = Path.home() / ".openclaw" / "exec-approvals.json"
CRON_JOB_NAME = "job-hunter-daily"
DISCOVERY_SESSION_KEY = "agent:job-hunter:discovery"
DISCOVERY_LAST_RUN_FILE = Path(__file__).parent / "discovery_last_run.json"
DISCOVERY_SETTINGS_FILE = ROOT / "logs" / "discovery_settings.json"
BUILTIN_SUPPORTED_DAYS = (1, 3, 7, 30)
BUILTIN_DEFAULT_DAYS = 1
PRUNE_SETTINGS_FILE = ROOT / "logs" / "prune_settings.json"
PRUNE_REASON_CODES = (
    "management_track",
    "non_us_location",
    "clearance_or_intel",
    "excessive_yoe",
    "citizenship_or_greencard",
    "stale_listing",
)
STALE_LISTING_MAX_AGE_DAYS = 10
PRUNE_INTERVALS_S = (0, 300, 900, 3600, 86400)
# Per-source progress for crash/quit resume (under logs/ — gitignored).
DISCOVERY_CHECKPOINT_FILE = ROOT / "logs" / "discovery_checkpoint.json"
SCOUT_TIMEOUT_S = 1500  # raised alongside SEARCH_TERMS growing from 6 to 14 terms
TAILOR_SCRIPT = ROOT / "scripts" / "tailor_resume.py"
RESUMES_DIR = ROOT / "resumes"
TAILOR_TIMEOUT_S = 700
RESUME_LATEX_MAX_CHARS = 1_000_000
RESUME_LATEX_SAMPLE = r"""\documentclass[10pt]{article}
\usepackage[margin=0.75in]{geometry}
\usepackage{setspace}
\setstretch{1.10}
\pagestyle{empty}

\begin{document}
\section*{Candidate Name}
City, ST \textbar{} candidate@example.com \textbar{} (555) 010-0000

\section*{Summary}
Data and machine learning professional focused on reliable, measurable systems.

\section*{Experience}
\textbf{Sample Company} \hfill 2023--Present\\
\textit{Data Scientist}
\begin{itemize}
  \item Built a production analytics workflow and documented measurable results.
  \item Partnered with engineering and product teams to improve data quality.
\end{itemize}

\section*{Skills}
Python, SQL, machine learning, data pipelines, cloud platforms
\end{document}
"""
INBOUND_MEDIA_DIR = Path.home() / ".openclaw" / "media" / "inbound"
INBOUND_RESUME_MAX_AGE_S = 7 * 24 * 3600
ATS_NOTES_DIR = ROOT / "ats_notes"
PLAYBOOK_FILE = ROOT / "PLAYBOOK.md"
# Dummy/test fill timeouts — Playwright path must cover fill + headed hold
# + Flash refill. Hold itself is indefinite (--hold-open); once hold starts
# the subprocess waiter stops applying this deadline so review isn't killed.
# Observed live fills ~114–240s elapsed including old 90s hold — 180s falsely
# killed mid-review and left jobs looking hung.
DUMMY_FILL_PLAYWRIGHT_TIMEOUT_S = 420
DUMMY_FILL_HYBRID_TIMEOUT_S = 1800
# After hold_review begins, wait this long before treating as abandoned.
DUMMY_FILL_HOLD_GRACE_S = 7 * 24 * 3600  # effectively until Cancel / browser close
# User/terminal decisions the Start/fill daemon must never clobber.
# `cancelled` is a legacy holding-pen status (migrated to Open). Live Cancel
# parks in-queue via fill_gen + ``_park_job_after_cancel`` (not this set).
# Legacy skipped_* remain until triage migration maps them to deleted.
FILL_ABORT_STATUSES = frozenset({
    "cancelled",
    "skipped_manual",
    "skipped_duplicate",
    "skipped_contract",
    "skipped_easy_apply",
    "deleted",
    "applied",
})
# Per-job fill generation: bumped on Start and on Cancel/Delete/Skip/Mark-applied
# so a pipeline thread that captured an older gen cannot clobber the parked
# status after Cancel (parked statuses are not in FILL_ABORT_STATUSES).
_fill_run_ctx: contextvars.ContextVar[tuple[str, int] | None] = contextvars.ContextVar(
    "_fill_run_ctx", default=None
)
# One active tailor+fill pipeline thread per job (parallel across jobs OK).
_fill_job_guard = threading.Lock()
_active_fill_jobs: set[str] = set()


def _job_fill_gen(job_id: str) -> int:
    with _lock:
        data = read_jobs()
        job = next((j for j in data.get("jobs") or [] if j.get("id") == job_id), None)
        if job is None:
            return 0
        return int(job.get("fill_gen") or 0)


def _bump_job_fill_gen_locked(job: dict) -> int:
    """Invalidate in-flight fill/tailor threads for this job (caller holds _lock)."""
    job["fill_gen"] = int(job.get("fill_gen") or 0) + 1
    return job["fill_gen"]


def _fill_run_stale(job_id: str, *, fill_gen: int | None = None) -> bool:
    """True when the current thread's captured fill_gen no longer matches the job.

    Pass ``fill_gen`` when the caller already holds ``_lock`` / the jobs write
    flock and has the job dict loaded — ``_job_fill_gen`` re-acquires ``_lock``
    and takes a shared flock on a second fd, which deadlocks (observed live
    after tectonic: status stuck at "Converting resume to PDF…", PDF on disk,
    fill never starts).
    """
    ctx = _fill_run_ctx.get()
    if ctx is None or ctx[0] != job_id:
        return False
    live = int(fill_gen) if fill_gen is not None else _job_fill_gen(job_id)
    return live != ctx[1]


def _persist_compiled_resume_after_tectonic(
    job_id: str,
    *,
    resume_pdf: Path,
    compile_ok: bool,
    compile_exit: int = 0,
    compile_log: Path | str | None = None,
    resume_only: bool = False,
) -> None:
    """Post-tectonic jobs.json handoff under ``_lock`` + EX flock.

    Intended lock order: ``_lock`` then ``locked_jobs_for_write``. Stale-gen
    checks MUST pass ``fill_gen=`` from the in-lock job dict — never call bare
    ``_fill_run_stale(job_id)`` here (non-reentrant ``_lock`` self-deadlock).
    """
    log_name = Path(compile_log).name if compile_log else "tectonic.log"
    with _lock:
        with locked_jobs_for_write() as data:
            job = next((j for j in data["jobs"] if j["id"] == job_id), None)
            if job is None or job.get("status") in FILL_ABORT_STATUSES:
                return
            # fill_gen from locked job — never bare _fill_run_stale(job_id).
            if _fill_run_stale(job_id, fill_gen=int(job.get("fill_gen") or 0)):
                return
            if compile_ok and resume_pdf.exists():
                job["resume_path"] = str(resume_pdf.relative_to(ROOT))
                sync_job_resume_on_disk(job)
                job["question"] = None
                if resume_only:
                    # Stay in tailoring until the pipeline parks on resume_ready.
                    job["status_detail"] = (
                        "Resume tailored and compiled."
                        if compile_exit == 0
                        else "Resume PDF fixed after tectonic failure."
                    )
                else:
                    job["status"] = "navigating"
                    job["status_detail"] = (
                        "Resume tailored and compiled. Preparing fill…"
                        if compile_exit == 0
                        else "Resume PDF fixed after tectonic failure. Preparing fill…"
                    )
            else:
                job["status"] = "stuck"
                job["question"] = (
                    f"resume.tex was produced but tectonic failed to "
                    f"compile it (exit {compile_exit}, see {log_name}). The LaTeX likely has "
                    "a real syntax error - can you check it, or should I have the agent fix it?"
                )
            job["updated_at"] = now_iso()


def _claim_fill_job(job_id: str) -> bool:
    """Register this job as having an active tailor/fill pipeline thread."""
    with _fill_job_guard:
        if job_id in _active_fill_jobs:
            return False
        _active_fill_jobs.add(job_id)
        return True


# Prior Cancel/Start threads may still hold the claim while exiting; wait briefly
# so a newer Start with the live fill_gen can take over instead of demoting to stuck.
_FILL_CLAIM_WAIT_S = 15.0


def _claim_fill_job_for_run(job_id: str, run_gen: int) -> bool:
    """Claim the pipeline slot for ``run_gen``, waiting out a prior thread's exit.

    Returns False when superseded (stale gen), hard-aborted, or the wait expires
    while another thread still holds the claim.
    """
    deadline = time.monotonic() + float(_FILL_CLAIM_WAIT_S)
    while True:
        if _job_fill_gen(job_id) != run_gen:
            return False
        if _claim_fill_job(job_id):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _release_fill_job(job_id: str) -> None:
    with _fill_job_guard:
        _active_fill_jobs.discard(job_id)


def _bind_fill_run_ctx(job_id: str, fill_run_gen: int | None = None) -> object:
    """Capture fill_gen for this pipeline thread (must match Start bump)."""
    run_gen = fill_run_gen if fill_run_gen is not None else _job_fill_gen(job_id)
    return _fill_run_ctx.set((job_id, run_gen))
# Pre-redesign holding-pen statuses (no longer a visible Skipped queue).
LEGACY_SKIP_STATUSES = frozenset({
    "skipped_manual",
    "skipped_duplicate",
    "skipped_contract",
    "skipped_easy_apply",
})
# Skip reason → (deleted_reason code, status_detail).
SKIP_REASON_TO_DELETED = {
    "duplicate": ("duplicate", "Skipped: duplicate company/role."),
    "not_us": ("non_us_location", "Skipped: not US."),
    "too_senior": ("management_track", "Skipped: too senior."),
    "contract": ("contract", "Skipped: contract/C2C."),
    "easy_apply": ("easy_apply", "Skipped: easy apply."),
    "dead_link": ("dead_link", "Skipped: dead link."),
}


def playbook_preamble() -> str:
    """PLAYBOOK.md is the agent's whole set of operating instructions for
    this project - observed live, a brand-new job session's very first
    tool call is always `read PLAYBOOK.md` before anything else happens.
    Handing it over directly in the first message (the same pre-injection
    pattern already used for the job record/address/ats_notes) skips that
    guaranteed extra round-trip. Only worth doing for a message that's
    likely to be turn 0 of a job's session - a continuation message on an
    already-running session already has this in its own history."""
    try:
        text = PLAYBOOK_FILE.read_text()
    except OSError:
        return ""
    return (
        "Here is PLAYBOOK.md in full - you haven't seen it yet this "
        "session, so this saves you the read() call you'd otherwise make "
        f"as your first action:\n\n{text}\n\n---\n\n"
    )
ATS_URL_PATTERNS = {
    "workday": re.compile(r"myworkdayjobs\.com|myworkdaysite\.com"),
    "greenhouse": re.compile(r"(?:boards|job-boards)\.greenhouse\.io"),
    "lever": re.compile(r"jobs\.lever\.co"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com"),
    "icims": re.compile(r"icims\.com"),
    "recruitee": re.compile(r"\.recruitee\.com"),
    "personio": re.compile(r"\.jobs\.personio\.(?:com|de)"),
    "linkedin": re.compile(r"linkedin\.com/jobs"),
}

_lock = threading.Lock()
_running_procs: dict[str, subprocess.Popen] = {}
# Last job-list metadata keyed by session key. /api/status uses this in-memory
# snapshot instead of reparsing the multi-megabyte jobs.json on every poll.
_runtime_job_snapshots: dict[str, dict] = {}
# Serialized /api/jobs response. mtime catches writes from sibling processes;
# write_jobs invalidates eagerly for writes made by this server.
_jobs_list_cache = {
    "mtime": None,
    "body_bytes": None,
    "etag": None,
    "fill_hold": None,
}
_prune_settings_lock = threading.Lock()
_discovery_settings_lock = threading.Lock()
_prune_schedule_wakeup = threading.Event()
# Live fill-step stream for dashboard "Live activity" (fast fill path).
# Keyed by job_id; independent of OpenClaw session tail used by Start/agent.
_fill_activity_lock = threading.Lock()
_fill_activity: dict[str, list[dict]] = {}
_FILL_ACTIVITY_MAX = 500

# UI lifecycle: after the first dashboard tab heartbeats, the server exits when
# every client goes quiet (tab closed or crashed). Closing one of N tabs does
# not shut down while others keep heartbeating. CLI-only runs never arm.
# Set JOB_HUNTER_UI_LIFECYCLE=0 to keep a headless/dev server up forever.
UI_HEARTBEAT_TIMEOUT_S = 20
_ui_lock = threading.Lock()
_ui_clients: dict[str, float] = {}  # client_id -> last_seen (time.time)
_ui_lifecycle_armed = False
_shutdown_lock = threading.Lock()
_shutdown_requested = False
_shutdown_reason = ""
_restart_requested = False
# CHR3-001/002: Refresh with CAPTCHA/Ready hold — finally must not undo preserve.
_preserve_fill_cft_on_exit = False
_http_server: ThreadingHTTPServer | None = None
RESTART_FLAG_PATH = ROOT / "logs" / "dashboard_restart.flag"
LAUNCHER_PID_PATH = ROOT / "logs" / "dashboard_launcher.pid"
LAUNCH_DASHBOARD_SH = Path(__file__).resolve().parent / "launch_dashboard.sh"
# Dedicated Chrome profiles / CDP — never the user's daily Chrome profile.
DASHBOARD_CHROME_PROFILE = ROOT / "dashboard_chrome_profile"  # legacy (Google Chrome era)
# The UI window runs on Chrome-for-Testing under this profile so that
# /Applications/Google Chrome.app stays free for the user's daily profile.
DASHBOARD_UI_PROFILE = ROOT / "dashboard_ui_profile"
PARTYROCK_CHROME_PROFILE = ROOT / "partyrock_chrome_profile"
# OpenClaw managed browser (PartyRock tailor via tailor_resume.py CDP).
OPENCLAW_BROWSER_USER_DATA = Path.home() / ".openclaw" / "browser" / "openclaw" / "user-data"
OPENCLAW_BROWSER_CDP_PORT = 18800
# Each job gets its **own** PartyRock CDP tab via /json/new (see partyrock_tabs.py).
# Tabs close after the resume is collected; Cancel/stuck closes that job's
# target early. Parallel tailor + fill across jobs is allowed.

# Discovery progress for the dashboard status bar. Separate from
# _running_procs so the UI still shows a phase during the brief gap
# between "thread started" and the first subprocess, and after a step
# exits before the next one starts.
_discovery_lock = threading.Lock()
# Exit code returned by _run_subprocess_step when cooperatively aborted.
DISCOVERY_ABORT_EXIT = -2
FILL_ABORT_EXIT = -3
# Listing sources discovery actually scrapes (JobSpy sites + ATS boards + Built In).
# Each enabled catalog source runs as its own subprocess (scout --sites / scrape_ats
# --platforms / scrape_builtin) with a per-source listing file and abort track key.
DISCOVERY_SOURCE_DEFS: list[tuple[str, str]] = [
    ("indeed", "Indeed"),
    ("linkedin", "LinkedIn"),
    ("greenhouse", "Greenhouse"),
    ("lever", "Lever"),
    ("ashby", "Ashby"),
    ("recruitee", "Recruitee"),
    ("personio", "Personio"),
    ("smartrecruiters", "SmartRecruiters"),
    ("workable", "Workable"),
    ("rippling", "Rippling"),
    ("breezy", "Breezy"),
    ("bamboohr", "BambooHR"),
    ("builtin", "Built In"),
    # India-only sources (see INDIA_ONLY_SOURCE_IDS) — only run when the India
    # region is enabled; force-disabled / greyed in the UI otherwise.
    ("internshala", "Internshala"),
    ("hirist", "Hirist"),
    ("cutshort", "Cutshort"),
    ("adzuna", "Adzuna (IN)"),
]
SCOUT_SOURCE_IDS = ("indeed", "linkedin")
ATS_SOURCE_IDS = (
    "greenhouse", "lever", "ashby", "recruitee", "personio",
    "smartrecruiters", "workable", "rippling", "breezy", "bamboohr",
)
# India-only discovery sources: only meaningful when the India region is on.
# They are force-disabled (and hidden/greyed in the UI) when India is off,
# and auto-enabled by the Discover popover when India is first turned on.
INDIA_ONLY_SOURCE_IDS = ("internshala", "hirist", "cutshort", "adzuna")
# Standalone scraper script per India-only source (each reads public pages /
# an official API at low volume; Adzuna self-skips without keys).
INDIA_SOURCE_SCRIPTS = {
    "internshala": ROOT / "scripts" / "scrape_internshala.py",
    "hirist": ROOT / "scripts" / "scrape_hirist.py",
    "cutshort": ROOT / "scripts" / "scrape_cutshort.py",
    "adzuna": ROOT / "scripts" / "scrape_adzuna.py",
}
# Polite per-source delays mean these run a few minutes at most.
INDIA_SOURCE_TIMEOUT_S = 600
_SCOUT_GOT_RE = re.compile(r"got (\d+) new results from (indeed|linkedin)/")
_ATS_GOT_RE = re.compile(
    r"got (\d+) relevant results from ("
    + "|".join(ATS_SOURCE_IDS)
    + r")/"
)
_ATS_PROGRESS_RE = re.compile(r"\((\d+)/(\d+) done\)")
_INDIA_GOT_RE = re.compile(
    r"got (\d+) results from ("
    + "|".join(INDIA_ONLY_SOURCE_IDS)
    + r")/"
)
_BUILTIN_PROC_RE = re.compile(r"processed (\d+)/(\d+) \((\d+) usable so far\)")
_WROTE_LISTINGS_RE = re.compile(r"wrote (\d+) listings")


DISCOVERY_SOURCE_IDS = tuple(sid for sid, _ in DISCOVERY_SOURCE_DEFS)


def _empty_discovery_sources(enabled: set[str] | None = None) -> list[dict]:
    """Build per-source rows. Disabled sources start as skipped."""
    enabled = set(DISCOVERY_SOURCE_IDS) if enabled is None else set(enabled)
    rows = []
    for sid, label in DISCOVERY_SOURCE_DEFS:
        on = sid in enabled
        rows.append({
            "id": sid,
            "label": label,
            "status": "pending" if on else "skipped",
            "count": 0,
            "detail": "" if on else "Disabled",
            "enabled": on,
        })
    return rows


def _parse_enabled_sources(payload: dict | None) -> set[str] | None:
    """Return enabled source ids from a discover POST body, or None for all.

    Accepts ``sources`` as a list of ids, or ``enabled_sources`` as a
    ``{id: bool}`` map. Unknown ids are ignored. Empty selection is an error
    at the handler (not here) — None means "caller omitted → default all".
    """
    if not isinstance(payload, dict):
        return None
    if "enabled_sources" in payload:
        raw = payload.get("enabled_sources")
        if isinstance(raw, dict):
            return {sid for sid in DISCOVERY_SOURCE_IDS if raw.get(sid, True)}
        if isinstance(raw, list):
            return {sid for sid in raw if sid in DISCOVERY_SOURCE_IDS}
        return None
    if "sources" in payload:
        raw = payload.get("sources")
        if isinstance(raw, list):
            return {sid for sid in raw if sid in DISCOVERY_SOURCE_IDS}
        if isinstance(raw, dict):
            return {sid for sid in DISCOVERY_SOURCE_IDS if raw.get(sid, True)}
    return None


_discovery_state: dict = {
    "running": False,
    "phase": None,
    "phase_label": None,
    "started_at": None,
    "finished_at": None,
    "last_finished_at": None,
    "ok": None,
    "error": None,
    # Last completed run: success | failed | interrupted | partial
    "last_outcome": None,
    "last_summary": None,
    "last_jobs_added": None,
    "sources": [],
    "enabled_sources": list(DISCOVERY_SOURCE_IDS),
    "can_abort": False,
    "abort_requested": False,
    "resumed": False,
    "resume_available": False,
    "run_id": None,
}
# Merge bookkeeping for the active run (also persisted in the checkpoint).
_discovery_checkpoint_meta: dict = {
    "run_id": None,
    "date": None,
    "merged_paths": set(),
    "merges_ok": 0,
    "jobs_added": 0,
}
# Active discovery procs keyed by track_key (per-source: …:src:{id}).
_discovery_procs_by_key: dict[str, subprocess.Popen] = {}
# Per-source aborts (do not set global abort_requested / do not finish discovery).
_discovery_source_aborts: set[str] = set()
_discovery_protect_proc = False  # True during write — finish write instead of killing mid-file.


class _DiscoveryProcSetView:
    """Set-like view over `_discovery_procs_by_key` values (tests + kill-all)."""

    def clear(self) -> None:
        _discovery_procs_by_key.clear()

    def __len__(self) -> int:
        return len(_discovery_procs_by_key)

    def __iter__(self):
        return iter(list(_discovery_procs_by_key.values()))

    def __bool__(self) -> bool:
        return bool(_discovery_procs_by_key)

    def discard(self, proc: subprocess.Popen) -> None:
        dead = [k for k, p in _discovery_procs_by_key.items() if p is proc]
        for k in dead:
            _discovery_procs_by_key.pop(k, None)


# Back-compat: len/list/clear/discard used by tests and older call sites.
_discovery_current_procs = _DiscoveryProcSetView()


def _discovery_source_track_key(source_id: str) -> str:
    return f"{DISCOVERY_SESSION_KEY}:src:{source_id}"


def _register_discovery_proc(track_key: str, proc: subprocess.Popen) -> None:
    with _discovery_lock:
        _discovery_procs_by_key[track_key] = proc


def _unregister_discovery_proc(track_key: str, proc: subprocess.Popen) -> None:
    with _discovery_lock:
        if _discovery_procs_by_key.get(track_key) is proc:
            _discovery_procs_by_key.pop(track_key, None)


def _kill_discovery_proc_by_key(track_key: str) -> None:
    with _discovery_lock:
        proc = _discovery_procs_by_key.get(track_key)
    if proc is not None:
        _kill_process_tree(proc)


def _source_abort_requested(source_id: str) -> bool:
    with _discovery_lock:
        return source_id in _discovery_source_aborts


DISCOVERY_PHASE_LABELS = {
    "starting": "Starting discovery…",
    "resuming": "Continuing previous run…",
    "scraping": "Scraping sources…",
    "scout": "Scouting Indeed/LinkedIn…",
    "ats": "Scraping ATS boards…",
    "builtin": "Scraping Built In…",
    "dedup": "Deduplicating listings…",
    "tracker": "Checking tracked companies…",
    "write": "Writing jobs…",
    "dedup_jobs": "Merging duplicate jobs…",
    "agent_recovery": "Agent recovering from error…",
    "aborting": "Aborting discovery…",
}

# Statuses that mean the run still has leftover work.
_DISCOVERY_SOURCE_INCOMPLETE = frozenset({"pending", "collecting", "stopped"})


def ats_notes_for_url(url: str) -> tuple[Path, str] | None:
    """Same known-quirks-per-platform reasoning already applied to date
    spinbuttons/comboboxes in PLAYBOOK.md, extended into a real per-platform
    reference: every company on Workday/Greenhouse/Lever/Ashby/iCIMS runs
    the same underlying form software, so a field-selector lesson learned
    on one company's form applies directly to the next company on the same
    platform. Returns (notes_file_path, content), or None if the URL
    doesn't match a known platform."""
    for platform, pattern in ATS_URL_PATTERNS.items():
        if pattern.search(url or ""):
            notes_file = ATS_NOTES_DIR / f"{platform}.md"
            if notes_file.exists():
                return notes_file, notes_file.read_text()
    return None

RISKY_VERBS = [
    "reset", "delete", "rm", "remove", "restart", "daemon", "logout",
    "unset", "cancel", "stop", "clean",
]


def is_risky(args_str: str) -> bool:
    tokens = args_str.lower().split()
    return any(v in tokens for v in RISKY_VERBS)


def gateway_running_session_keys() -> set[str]:
    """Session keys with an agent turn currently running.

    Historically this shelled out to ``openclaw sessions list`` because a turn
    could outlive the local CLI client on the gateway. With OpenClaw removed,
    agent turns run in-process via ``agent_runner``, so its active-turn
    registry is authoritative — no subprocess round-trip. (Name kept for the
    call sites; there is no gateway anymore.)"""
    try:
        return agent_runner.active_turn_keys()
    except Exception as e:
        print(f"warn: gateway_running_session_keys failed: {e}")
        return set()


def is_session_running(session_key: str) -> bool:
    """Whether a specific session (one job, or the discovery run) is
    currently active. Used to stop a double-Start on the same job or a
    double-trigger of discovery - NOT a blanket "only one thing at a
    time" rule anymore. That used to be here because update_job.py and
    write_discovered_jobs.py raced each other with no coordination at all
    (see scripts/jobs_lock.py, now fixed with a real file lock) - jobs no
    longer need to be serialized against each other or against discovery
    just to keep jobs.json from getting corrupted."""
    if session_key == DISCOVERY_SESSION_KEY:
        with _discovery_lock:
            if _discovery_state["running"]:
                return True
    return session_key in _running_session_keys()


def _session_running_local(session_key: str) -> bool:
    """Fast, local-only 'is this session running?' — no gateway subprocess.

    ``is_session_running`` shells out to ``openclaw sessions list`` (timeout
    15s) to catch turns still alive on the gateway after the CLI client
    exited. That authoritative check is worth it in some places, but on the
    Start request path it adds seconds of lag before tailoring can even
    begin. The double-Start race it guards is already closed by the in-lock
    status claim (``IN_PROGRESS_STATUSES``) plus this local tracked-proc
    check, so Start uses the fast path and skips the gateway round-trip."""
    if session_key == DISCOVERY_SESSION_KEY:
        with _discovery_lock:
            if _discovery_state["running"]:
                return True
    if any(k == session_key and p.poll() is None for k, p in _running_procs.items()):
        return True
    try:
        return agent_runner.is_turn_active(session_key)
    except Exception:
        return False


def _prewarm_openclaw_browser_async() -> None:
    """Kick the OpenClaw CDP browser start concurrently so its subprocess
    overlaps with status writes + tailor_resume.py startup instead of
    running sequentially right before tailoring. ``openclaw browser start``
    is idempotent, so the tailor thread's own call becomes a fast no-op."""
    threading.Thread(
        target=_ensure_openclaw_managed_browser,
        daemon=True,
        name="partyrock-browser-prewarm",
    ).start()


def _running_session_keys() -> set[str]:
    local_keys = {k for k, p in _running_procs.items() if p.poll() is None}
    gateway_keys = gateway_running_session_keys()
    return local_keys | gateway_keys


def active_job() -> dict | None:
    """Return the currently-running job, if any - used only for display
    (e.g. showing what's in progress), not to block starting anything
    else. See is_session_running() for the actual per-session check."""
    running_keys = _running_session_keys()
    if not running_keys:
        with _discovery_lock:
            if _discovery_state["running"]:
                return {"id": None, "company": "(discovery run)", "title": ""}
        return None
    data = read_jobs()
    for job in data["jobs"]:
        if job.get("session_key") in running_keys:
            return job
    if DISCOVERY_SESSION_KEY in running_keys:
        return {"id": None, "company": "(discovery run)", "title": ""}
    return None


def _set_discovery_phase(phase: str, error: str | None = None) -> None:
    with _discovery_lock:
        _discovery_state["phase"] = phase
        _discovery_state["phase_label"] = DISCOVERY_PHASE_LABELS.get(phase, phase)
        if error is not None:
            _discovery_state["error"] = error


def _discovery_abort_requested() -> bool:
    with _discovery_lock:
        return bool(_discovery_state.get("abort_requested"))


def _update_discovery_sources(
    source_ids: tuple[str, ...] | list[str],
    *,
    status: str | None = None,
    detail: str | None = None,
    counts: dict[str, int] | None = None,
    add_counts: dict[str, int] | None = None,
    only_if_status: tuple[str, ...] | None = None,
) -> None:
    """Update one or more source rows in _discovery_state['sources']."""
    id_set = set(source_ids)
    with _discovery_lock:
        for src in _discovery_state.get("sources") or []:
            if src.get("id") not in id_set:
                continue
            if only_if_status and src.get("status") not in only_if_status:
                continue
            if status is not None:
                src["status"] = status
            if detail is not None:
                src["detail"] = detail
            sid = src["id"]
            if counts and sid in counts:
                src["count"] = int(counts[sid])
            if add_counts and sid in add_counts:
                src["count"] = int(src.get("count") or 0) + int(add_counts[sid])


def _set_source_fields(source_id: str, **fields) -> None:
    with _discovery_lock:
        for src in _discovery_state.get("sources") or []:
            if src.get("id") == source_id:
                src.update(fields)
                return


def _mark_incomplete_sources_stopped() -> None:
    with _discovery_lock:
        for src in _discovery_state.get("sources") or []:
            if src.get("status") in ("pending", "collecting"):
                src["status"] = "stopped"
                if not src.get("detail"):
                    src["detail"] = "Stopped"


def _count_listings_by_site(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    counts: dict[str, int] = {}
    if not isinstance(data, list):
        return counts
    for item in data:
        if not isinstance(item, dict):
            continue
        site = (item.get("site") or "").lower().strip()
        if site:
            counts[site] = counts.get(site, 0) + 1
    return counts


def _apply_site_counts(
    counts: dict[str, int],
    source_ids: tuple[str, ...] | list[str],
    *,
    status: str = "completed",
    zero_detail: str = "",
) -> None:
    for sid in source_ids:
        n = int(counts.get(sid, 0))
        detail = f"{n} listings" if n else zero_detail
        _set_source_fields(sid, status=status, count=n, detail=detail)


def _load_discovery_last_run() -> None:
    """Hydrate last discovery finish time from disk (survives server restart)."""
    try:
        if not DISCOVERY_LAST_RUN_FILE.exists():
            return
        data = json.loads(DISCOVERY_LAST_RUN_FILE.read_text())
        finished = data.get("finished_at") or data.get("last_finished_at")
        if not finished:
            return
        outcome = data.get("outcome")
        if not outcome:
            if data.get("ok") is True:
                outcome = "success"
            elif data.get("ok") is False:
                outcome = "failed"
        with _discovery_lock:
            if not _discovery_state.get("last_finished_at"):
                _discovery_state["last_finished_at"] = finished
            if not _discovery_state.get("finished_at"):
                _discovery_state["finished_at"] = finished
            if _discovery_state.get("ok") is None and "ok" in data:
                _discovery_state["ok"] = data.get("ok")
            if not _discovery_state.get("last_outcome") and outcome:
                _discovery_state["last_outcome"] = outcome
            if not _discovery_state.get("last_summary") and data.get("summary"):
                _discovery_state["last_summary"] = data.get("summary")
            if _discovery_state.get("last_jobs_added") is None and data.get("jobs_added") is not None:
                _discovery_state["last_jobs_added"] = data.get("jobs_added")
    except Exception as e:
        print(f"warn: load discovery_last_run failed: {e}")


def _persist_discovery_last_run(
    finished_at: str,
    ok: bool | None,
    *,
    outcome: str | None = None,
    summary: str | None = None,
    jobs_added: int | None = None,
) -> None:
    try:
        if not outcome:
            if ok is True:
                outcome = "success"
            elif ok is False:
                outcome = "failed"
        payload = {
            "finished_at": finished_at,
            "last_finished_at": finished_at,
            "ok": ok,
            "outcome": outcome,
            "summary": summary,
            "jobs_added": jobs_added,
        }
        DISCOVERY_LAST_RUN_FILE.write_text(json.dumps(payload, indent=2) + "\n")
    except Exception as e:
        print(f"warn: persist discovery_last_run failed: {e}")


def _parse_jobs_added_from_log(log_path: Path) -> int:
    """Parse ``added: N`` lines from write_discovered_jobs logs."""
    try:
        text = log_path.read_text()
    except OSError:
        return 0
    total = 0
    for line in text.splitlines():
        m = re.search(r"\badded:\s*(\d+)\b", line)
        if m:
            total += int(m.group(1))
    return total


def _discovery_compute_outcome(
    *,
    ok: bool,
    error: str | None,
    fully_done: bool,
    aborted: bool,
) -> tuple[str, str]:
    """Return (outcome, short summary) for last-run UI."""
    with _discovery_lock:
        sources = [dict(s) for s in (_discovery_state.get("sources") or [])]
        jobs_added = int(_discovery_checkpoint_meta.get("jobs_added") or 0)
    enabled = [s for s in sources if s.get("enabled")]
    completed = sum(1 for s in enabled if s.get("status") in ("completed", "skipped"))
    failed = sum(1 for s in enabled if s.get("status") == "failed")
    incomplete = sum(
        1 for s in enabled
        if s.get("status") in _DISCOVERY_SOURCE_INCOMPLETE
    )
    parts: list[str] = []
    if jobs_added:
        parts.append(f"+{jobs_added} jobs")
    elif fully_done and ok:
        parts.append("+0 jobs")
    if completed:
        parts.append(f"{completed} source{'s' if completed != 1 else ''} done")
    if failed:
        parts.append(f"{failed} failed")
    if incomplete:
        parts.append(f"{incomplete} incomplete")
    if error and not aborted:
        parts.append(error[:80])
    summary = " · ".join(parts) if parts else ("ok" if ok else (error or "finished"))

    if aborted and not fully_done:
        outcome = "interrupted" if jobs_added == 0 and completed == 0 else "partial"
    elif fully_done and ok and failed == 0:
        outcome = "success"
    elif fully_done and failed and completed:
        outcome = "partial"
    elif ok and not fully_done:
        outcome = "partial"
    else:
        outcome = "failed"
    return outcome, summary


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def _load_discovery_checkpoint() -> dict | None:
    try:
        if not DISCOVERY_CHECKPOINT_FILE.exists():
            return None
        data = json.loads(DISCOVERY_CHECKPOINT_FILE.read_text())
        return data if isinstance(data, dict) else None
    except Exception as e:
        print(f"warn: load discovery_checkpoint failed: {e}")
        return None


def _clear_discovery_checkpoint() -> None:
    try:
        DISCOVERY_CHECKPOINT_FILE.unlink(missing_ok=True)
    except Exception as e:
        print(f"warn: clear discovery_checkpoint failed: {e}")


def _today_local_iso() -> str:
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def _checkpoint_has_leftover(checkpoint: dict | None, enabled: set[str] | None = None) -> bool:
    """True if checkpoint is an incomplete same-day run with work left."""
    if not checkpoint:
        return False
    if checkpoint.get("status") not in ("running", "incomplete"):
        return False
    if checkpoint.get("date") != _today_local_iso():
        return False
    sources = checkpoint.get("sources") or {}
    if not isinstance(sources, dict) or not sources:
        return False
    enabled = set(DISCOVERY_SOURCE_IDS) if enabled is None else set(enabled)
    for sid in enabled:
        info = sources.get(sid) or {}
        st = info.get("status") or "pending"
        if st in _DISCOVERY_SOURCE_INCOMPLETE or st == "failed":
            return True
        # Enabled now but missing from prior run → leftover.
        if sid not in sources:
            return True
    return False


def _reset_checkpoint_meta(
    *,
    run_id: str | None = None,
    date: str | None = None,
    merged_paths: set[str] | None = None,
    merges_ok: int = 0,
    jobs_added: int = 0,
) -> None:
    _discovery_checkpoint_meta["run_id"] = run_id
    _discovery_checkpoint_meta["date"] = date
    _discovery_checkpoint_meta["merged_paths"] = set(merged_paths or ())
    _discovery_checkpoint_meta["merges_ok"] = int(merges_ok or 0)
    _discovery_checkpoint_meta["jobs_added"] = int(jobs_added or 0)


def _flush_discovery_checkpoint(status: str = "running") -> None:
    """Persist per-source progress so a crash/quit can resume later.

    Safe to call often; uses atomic replace. Does nothing if discovery is not
    running and status is still 'running' (idle banner uses incomplete).
    """
    try:
        with _discovery_lock:
            running = bool(_discovery_state.get("running"))
            sources_live = [dict(s) for s in (_discovery_state.get("sources") or [])]
            enabled = list(_discovery_state.get("enabled_sources") or DISCOVERY_SOURCE_IDS)
            started_at = _discovery_state.get("started_at")
            run_id = (
                _discovery_state.get("run_id")
                or _discovery_checkpoint_meta.get("run_id")
                or started_at
                or now_iso()
            )
            date = _discovery_checkpoint_meta.get("date") or _today_local_iso()
            merged_paths = sorted(_discovery_checkpoint_meta.get("merged_paths") or [])
            merges_ok = int(_discovery_checkpoint_meta.get("merges_ok") or 0)
            if not running and status == "running":
                # Only mark incomplete from an active flush path.
                status = "incomplete"
        sources_map: dict[str, dict] = {}
        for src in sources_live:
            sid = src.get("id")
            if not sid:
                continue
            listing = ""
            try:
                listing = str(_source_listing_path(date, sid))
            except ValueError:
                listing = ""
            sources_map[sid] = {
                "status": src.get("status") or "pending",
                "count": int(src.get("count") or 0),
                "detail": src.get("detail") or "",
                "enabled": bool(src.get("enabled", True)),
                "listing_path": listing,
                "merged": listing in merged_paths if listing else False,
            }
        payload = {
            "version": 1,
            "run_id": run_id,
            "date": date,
            "started_at": started_at,
            "updated_at": now_iso(),
            "status": status,
            "enabled_sources": enabled,
            "sources": sources_map,
            "merged_paths": merged_paths,
            "merges_ok": merges_ok,
            "jobs_added": int(_discovery_checkpoint_meta.get("jobs_added") or 0),
        }
        _atomic_write_json(DISCOVERY_CHECKPOINT_FILE, payload)
    except Exception as e:
        print(f"warn: flush discovery_checkpoint failed: {e}")


def _sources_from_checkpoint(checkpoint: dict, enabled: set[str]) -> list[dict]:
    """Build UI source rows from a checkpoint, applying current enabled set."""
    rows = _empty_discovery_sources(enabled)
    prior = checkpoint.get("sources") or {}
    if not isinstance(prior, dict):
        return rows
    for src in rows:
        sid = src["id"]
        if not src.get("enabled"):
            continue
        info = prior.get(sid)
        if not isinstance(info, dict):
            continue
        st = info.get("status") or "pending"
        if st == "completed":
            n = int(info.get("count") or 0)
            src["status"] = "completed"
            src["count"] = n
            src["detail"] = info.get("detail") or (f"{n} listings" if n else "Done")
        elif st in ("stopped", "failed", "collecting", "pending"):
            src["status"] = "stopped" if st == "collecting" else st
            src["count"] = int(info.get("count") or 0)
            src["detail"] = info.get("detail") or (
                "Interrupted" if st in ("collecting", "pending") else ""
            )
    return rows


def _hydrate_discovery_resume_banner() -> None:
    """On server start: surface incomplete discovery for click-to-resume.

    Prefer explicit Discover click over auto-resume (safer after a crash —
    user may have closed the dashboard intentionally mid-run).
    """
    checkpoint = _load_discovery_checkpoint()
    if not checkpoint:
        return
    if checkpoint.get("date") != _today_local_iso():
        # Stale day — drop so tomorrow starts clean.
        _clear_discovery_checkpoint()
        return
    if checkpoint.get("status") not in ("running", "incomplete"):
        return
    # Process died while status said running → normalize to incomplete.
    sources = checkpoint.get("sources") or {}
    if isinstance(sources, dict):
        changed = checkpoint.get("status") == "running"
        for sid, info in sources.items():
            if not isinstance(info, dict):
                continue
            if info.get("status") in ("pending", "collecting"):
                info["status"] = "stopped"
                if not info.get("detail"):
                    info["detail"] = "Interrupted"
                changed = True
        if changed:
            checkpoint["status"] = "incomplete"
            checkpoint["updated_at"] = now_iso()
            try:
                _atomic_write_json(DISCOVERY_CHECKPOINT_FILE, checkpoint)
            except Exception as e:
                print(f"warn: normalize discovery_checkpoint failed: {e}")
    enabled_raw = checkpoint.get("enabled_sources")
    if isinstance(enabled_raw, list) and enabled_raw:
        enabled = {sid for sid in enabled_raw if sid in DISCOVERY_SOURCE_IDS}
    else:
        enabled = set(DISCOVERY_SOURCE_IDS)
    if not _checkpoint_has_leftover(checkpoint, enabled):
        _clear_discovery_checkpoint()
        return
    rows = _sources_from_checkpoint(checkpoint, enabled)
    with _discovery_lock:
        if _discovery_state.get("running"):
            return
        last_finished = _discovery_state.get("last_finished_at") or _discovery_state.get("finished_at")
        last_outcome = _discovery_state.get("last_outcome") or "interrupted"
        last_summary = _discovery_state.get("last_summary")
        jobs_added = checkpoint.get("jobs_added")
        if jobs_added is None:
            jobs_added = _discovery_state.get("last_jobs_added")
        _discovery_state.update({
            "running": False,
            "phase": None,
            "phase_label": None,
            "started_at": checkpoint.get("started_at"),
            "finished_at": None,
            "last_finished_at": last_finished,
            "ok": False,
            "error": "Incomplete — click Discover to continue",
            "last_outcome": last_outcome if last_outcome != "success" else "interrupted",
            "last_summary": last_summary or "Incomplete — will continue",
            "last_jobs_added": jobs_added,
            "sources": rows,
            "enabled_sources": sorted(enabled, key=lambda s: DISCOVERY_SOURCE_IDS.index(s)),
            "can_abort": False,
            "abort_requested": False,
            "resumed": False,
            "resume_available": True,
            "run_id": checkpoint.get("run_id"),
        })
    _reset_checkpoint_meta(
        run_id=checkpoint.get("run_id"),
        date=checkpoint.get("date"),
        merged_paths=set(checkpoint.get("merged_paths") or []),
        merges_ok=int(checkpoint.get("merges_ok") or 0),
        jobs_added=int(checkpoint.get("jobs_added") or 0),
    )


def _discovery_run_fully_complete() -> bool:
    """True when every enabled source finished successfully or was skipped.

    ``failed`` / ``stopped`` keep the checkpoint so Discover can resume.
    """
    with _discovery_lock:
        sources = list(_discovery_state.get("sources") or [])
    enabled_rows = [s for s in sources if s.get("enabled")]
    if not enabled_rows:
        return True
    for src in enabled_rows:
        if src.get("status") not in ("completed", "skipped"):
            return False
    return True


def _begin_discovery(enabled: set[str] | None = None, *, fresh: bool = False) -> bool:
    """Mark discovery as running. Returns False if already running.

    Default: resume incomplete same-day checkpoint (skip completed sources).
    Pass fresh=True to clear the checkpoint and start a new pass (still
    skips already-known URLs at scrape/write time).
    """
    enabled_ids = set(DISCOVERY_SOURCE_IDS) if enabled is None else (set(enabled) & set(DISCOVERY_SOURCE_IDS))
    if fresh:
        _clear_discovery_checkpoint()
        checkpoint = None
        resuming = False
    else:
        checkpoint = _load_discovery_checkpoint()
        resuming = _checkpoint_has_leftover(checkpoint, enabled_ids)
    with _discovery_lock:
        if _discovery_state["running"]:
            return False
        # Preserve last_finished_at / last_outcome across a new run so the UI
        # can still show prior status until this run completes.
        last_finished = _discovery_state.get("last_finished_at") or _discovery_state.get("finished_at")
        last_outcome = _discovery_state.get("last_outcome")
        last_summary = _discovery_state.get("last_summary")
        last_jobs_added = _discovery_state.get("last_jobs_added")
        if resuming and checkpoint:
            sources = _sources_from_checkpoint(checkpoint, enabled_ids)
            # Mark leftover enabled sources pending so the UI shows work ahead;
            # completed ones keep their counts from the checkpoint.
            for src in sources:
                if not src.get("enabled"):
                    continue
                if src.get("status") in _DISCOVERY_SOURCE_INCOMPLETE or src.get("status") == "failed":
                    src["status"] = "pending"
                    src["detail"] = "Continuing…"
            run_id = checkpoint.get("run_id") or now_iso()
            started_at = checkpoint.get("started_at") or now_iso()
            phase = "resuming"
            merged_paths = set(checkpoint.get("merged_paths") or [])
            merges_ok = int(checkpoint.get("merges_ok") or 0)
            jobs_added = int(checkpoint.get("jobs_added") or 0)
            date = checkpoint.get("date") or _today_local_iso()
        else:
            sources = _empty_discovery_sources(enabled_ids)
            run_id = now_iso()
            started_at = run_id
            phase = "starting"
            merged_paths = set()
            merges_ok = 0
            jobs_added = 0
            date = _today_local_iso()
        _discovery_state.update({
            "running": True,
            "phase": phase,
            "phase_label": DISCOVERY_PHASE_LABELS[phase],
            "started_at": started_at,
            "finished_at": None,
            "last_finished_at": last_finished,
            "last_outcome": last_outcome,
            "last_summary": last_summary,
            "last_jobs_added": last_jobs_added,
            "ok": None,
            "error": None,
            "sources": sources,
            "enabled_sources": sorted(enabled_ids, key=lambda s: DISCOVERY_SOURCE_IDS.index(s)),
            "can_abort": True,
            "abort_requested": False,
            "resumed": resuming,
            "resume_available": False,
            "run_id": run_id,
        })
        _discovery_procs_by_key.clear()
        _discovery_source_aborts.clear()
    _reset_checkpoint_meta(
        run_id=run_id, date=date, merged_paths=merged_paths, merges_ok=merges_ok,
        jobs_added=jobs_added,
    )
    _flush_discovery_checkpoint("running")
    return True


def _discovery_enabled_set() -> set[str]:
    with _discovery_lock:
        raw = _discovery_state.get("enabled_sources")
        if isinstance(raw, list) and raw:
            return {sid for sid in raw if sid in DISCOVERY_SOURCE_IDS}
        return set(DISCOVERY_SOURCE_IDS)


def _finish_discovery(ok: bool, error: str | None = None) -> None:
    global _discovery_protect_proc
    finished = now_iso()
    fully_done = _discovery_run_fully_complete()
    aborted = _discovery_abort_requested() or (
        isinstance(error, str) and "abort" in error.lower()
    )
    outcome, summary = _discovery_compute_outcome(
        ok=ok, error=error, fully_done=fully_done, aborted=aborted,
    )
    jobs_added = int(_discovery_checkpoint_meta.get("jobs_added") or 0)
    with _discovery_lock:
        _discovery_state.update({
            "running": False,
            "phase": None,
            "phase_label": None,
            "finished_at": finished,
            "last_finished_at": finished,
            "ok": ok,
            "error": error,
            "last_outcome": outcome,
            "last_summary": summary,
            "last_jobs_added": jobs_added,
            "can_abort": False,
            "abort_requested": False,
            "resumed": False,
            "resume_available": not fully_done,
        })
        # Keep sources for post-run hover inspection.
        _discovery_procs_by_key.clear()
        _discovery_source_aborts.clear()
        _discovery_protect_proc = False
    _persist_discovery_last_run(
        finished, ok, outcome=outcome, summary=summary, jobs_added=jobs_added,
    )
    if fully_done:
        _clear_discovery_checkpoint()
        _reset_checkpoint_meta()
    else:
        _flush_discovery_checkpoint("incomplete")
        with _discovery_lock:
            if not _discovery_state.get("error"):
                _discovery_state["error"] = "Incomplete — click Discover to continue"


def normalize_builtin_days_since_updated(value) -> int:
    """Validate Built In's UI-supported New Jobs filter values."""
    if value is None:
        return BUILTIN_DEFAULT_DAYS
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"builtin_days_since_updated must be one of {BUILTIN_SUPPORTED_DAYS}"
        ) from None
    if days not in BUILTIN_SUPPORTED_DAYS:
        raise ValueError(
            f"builtin_days_since_updated must be one of {BUILTIN_SUPPORTED_DAYS}"
        )
    return days


def _coerce_bool(value, default: bool) -> bool:
    """Best-effort bool from JSON / query values; ``default`` when absent."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def load_discovery_settings() -> dict:
    # US discovery is the default; India is opt-in (see India region model).
    defaults = {
        "builtin_days_since_updated": BUILTIN_DEFAULT_DAYS,
        "discover_us": True,
        "discover_india": False,
    }
    with _discovery_settings_lock:
        try:
            raw = json.loads(DISCOVERY_SETTINGS_FILE.read_text())
        except (OSError, json.JSONDecodeError, TypeError):
            return dict(defaults)
    if not isinstance(raw, dict):
        return dict(defaults)
    try:
        days = normalize_builtin_days_since_updated(
            raw.get("builtin_days_since_updated")
        )
    except (AttributeError, TypeError, ValueError):
        days = BUILTIN_DEFAULT_DAYS
    return {
        "builtin_days_since_updated": days,
        "discover_us": _coerce_bool(raw.get("discover_us"), True),
        "discover_india": _coerce_bool(raw.get("discover_india"), False),
    }


def save_discovery_settings(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    current = load_discovery_settings()
    days = normalize_builtin_days_since_updated(
        payload.get("builtin_days_since_updated")
        if "builtin_days_since_updated" in payload
        else current["builtin_days_since_updated"]
    )
    discover_us = _coerce_bool(
        payload.get("discover_us"), current["discover_us"]
    ) if "discover_us" in payload else current["discover_us"]
    discover_india = _coerce_bool(
        payload.get("discover_india"), current["discover_india"]
    ) if "discover_india" in payload else current["discover_india"]
    # Guard: never persist "no regions" — that would drop every listing.
    if not discover_us and not discover_india:
        discover_us = True
    settings = {
        "builtin_days_since_updated": days,
        "discover_us": discover_us,
        "discover_india": discover_india,
    }
    with _discovery_settings_lock:
        DISCOVERY_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DISCOVERY_SETTINGS_FILE.with_suffix(
            DISCOVERY_SETTINGS_FILE.suffix + ".tmp"
        )
        tmp.write_text(json.dumps(settings, indent=2) + "\n")
        tmp.replace(DISCOVERY_SETTINGS_FILE)
    return settings


def enabled_discovery_regions() -> list[str]:
    """Ordered region ids from persisted settings (US first, then India)."""
    s = load_discovery_settings()
    regions = []
    if s.get("discover_us", True):
        regions.append("us")
    if s.get("discover_india", False):
        regions.append("india")
    return regions or ["us"]


def _discovery_status_in_memory() -> dict:
    with _discovery_lock:
        state = dict(_discovery_state)
        state["sources"] = [dict(s) for s in (_discovery_state.get("sources") or [])]
        state["source_catalog"] = [
            {
                "id": sid,
                "label": label,
                "india_only": sid in INDIA_ONLY_SOURCE_IDS,
            }
            for sid, label in DISCOVERY_SOURCE_DEFS
        ]
        state["india_only_sources"] = list(INDIA_ONLY_SOURCE_IDS)
        enabled = _discovery_state.get("enabled_sources")
        state["enabled_sources"] = list(enabled) if isinstance(enabled, list) else list(DISCOVERY_SOURCE_IDS)
        # Always surface a stable last-run field for the Discover button.
        if not state.get("last_finished_at") and state.get("finished_at"):
            state["last_finished_at"] = state["finished_at"]
        state["resume_available"] = bool(state.get("resume_available"))
        state["resumed"] = bool(state.get("resumed"))
        return state


def discovery_status() -> dict:
    state = _discovery_status_in_memory()
    state.update(load_discovery_settings())
    return state


def _kill_process_tree(proc: subprocess.Popen | None) -> None:
    """SIGTERM (then SIGKILL) a tracked child — prefer its process group.

    Discovery/fill/agent children are started with start_new_session=True so
    killpg only hits that tree (Chrome-for-Testing, JobSpy, etc.), never the
    user's unrelated browser.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=4)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=3)
    except Exception:
        pass


# Back-compat alias used by discovery abort paths.
_kill_discovery_process_tree = _kill_process_tree


def _kill_all_discovery_procs() -> None:
    """Kill every registered discovery subprocess process group."""
    with _discovery_lock:
        procs = list(_discovery_procs_by_key.values())
    for proc in procs:
        _kill_process_tree(proc)


def _kill_all_tracked_child_procs(*, preserve_fill_procs: bool = False) -> None:
    """Kill fill/agent/discovery children registered on this server.

    Only process groups we started (start_new_session=True) — never the user's
    unrelated Chrome profile. Gap: browsers/agents not registered in
    `_running_procs` / `_discovery_current_procs` are not killed — see
    `_kill_jh_associated_browsers` for orphaned Chrome-for-Testing / PartyRock.

    CHR3-002: when *preserve_fill_procs* (Refresh + CAPTCHA/Ready hold), leave
    fill/agent process groups alive so stdin wait / hold continues; still abort
    discovery scrapes. Entries are detached from this server (dict cleared).
    """
    if preserve_fill_procs:
        _running_procs.clear()
        _kill_all_discovery_procs()
        return
    # _running_procs is mutated without _lock elsewhere; snapshot then clear.
    procs = [p for p in list(_running_procs.values()) if p is not None]
    _running_procs.clear()
    for proc in procs:
        _kill_process_tree(proc)
    _kill_all_discovery_procs()


def _pgrep_f_pids(pattern: str) -> list[int]:
    """PIDs matching ``pgrep -f pattern`` (best-effort; empty on miss/error)."""
    try:
        out = subprocess.check_output(
            ["/usr/bin/pgrep", "-f", "--", pattern],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line.split(None, 1)[0]))
        except ValueError:
            continue
    return pids


def _signal_pids(pids: list[int], sig: int) -> list[int]:
    """Send *sig* to each pid; return those we signaled without ProcessLookupError."""
    signaled: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, sig)
            signaled.append(pid)
        except (ProcessLookupError, PermissionError, OSError):
            continue
    return signaled


def _kill_pids_term_then_kill(pids: list[int], *, wait_s: float = 2.0) -> list[int]:
    """SIGTERM then SIGKILL lingering PIDs. Returns PIDs we attempted to kill."""
    uniq = sorted({int(p) for p in pids if p})
    if not uniq:
        return []
    _signal_pids(uniq, signal.SIGTERM)
    deadline = time.time() + wait_s
    alive = list(uniq)
    while alive and time.time() < deadline:
        still: list[int] = []
        for pid in alive:
            try:
                os.kill(pid, 0)
                still.append(pid)
            except (ProcessLookupError, PermissionError, OSError):
                continue
        alive = still
        if alive:
            time.sleep(0.15)
    if alive:
        _signal_pids(alive, signal.SIGKILL)
    return uniq


def _fill_cft_exclude_markers() -> tuple[str, ...]:
    """Argv markers for CfT mains that are NOT Playwright form-fill (CHR3-003).

    Dashboard UI and OpenClaw PartyRock share the Chrome-for-Testing binary;
    counting/killing them as fill Chrome orphans login tabs or false-caps.
    """
    return (
        f"--user-data-dir={DASHBOARD_UI_PROFILE}",
        "--app=http://127.0.0.1:8787",
        f"--user-data-dir={OPENCLAW_BROWSER_USER_DATA}",
        f"--remote-debugging-port={OPENCLAW_BROWSER_CDP_PORT}",
        "openclaw/user-data",
    )


def _chrome_for_testing_main_pids() -> list[int]:
    """Main Google Chrome for Testing processes (Playwright / fast_fill headed).

    Excludes the dashboard UI window (``dashboard_ui_profile`` / ``--app=:8787``)
    and OpenClaw PartyRock CDP (``~/.openclaw/browser/openclaw/user-data`` /
    ``:18800``). UI teardown is ``launch_dashboard.sh``; PartyRock stop is
    ``_stop_openclaw_managed_browser`` only.
    """
    exclude = _fill_cft_exclude_markers()
    try:
        out = subprocess.check_output(
            ["/usr/bin/pgrep", "-lf", "Google Chrome for Testing"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        if "Helper" in line or "crashpad" in line:
            continue
        if "MacOS/Google Chrome for Testing" not in line and not re.search(
            r"/chrome(?:\s|$)", line
        ):
            continue
        if any(marker in line for marker in exclude):
            continue
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        try:
            pids.append(int(parts[0]))
        except ValueError:
            continue
    return pids


def _kill_chrome_for_testing() -> list[int]:
    """Tear down Playwright Chrome-for-Testing (form-fill), never daily Chrome."""
    return _kill_pids_term_then_kill(_chrome_for_testing_main_pids())


def _pids_for_user_data_dir(user_data_dir: Path | str) -> list[int]:
    """Chrome processes whose argv includes ``--user-data-dir=<path>``."""
    path = str(user_data_dir)
    return _pgrep_f_pids(f"--user-data-dir={path}")


def _kill_chrome_user_data_dir(user_data_dir: Path | str) -> list[int]:
    return _kill_pids_term_then_kill(_pids_for_user_data_dir(user_data_dir))


_openclaw_browser_start_lock = threading.Lock()


def _ensure_openclaw_managed_browser(*, required: bool = False) -> dict:
    """Start OpenClaw PartyRock CDP only when tailor needs it (CHR2-001).

    Forces Chrome for Testing / Chromium — never daily Google Chrome.app —
    via ``scripts/chrome_for_testing.py`` (pins ``browser.executablePath``
    and falls back to a direct CfT launch on the OpenClaw user-data dir).

    Dashboard launch / refresh / idle must not start PartyRock CDP.

    Serialized so the Start-path pre-warm and the tailor thread never race
    two concurrent starts.

    *required* (PR2-002): raise ``RuntimeError`` on failure instead of warn-only
    (tailor path). Prewarm / best-effort callers keep ``required=False``.
    """
    with _openclaw_browser_start_lock:
        try:
            # OpenClaw-free: launch Chrome-for-Testing directly on the same
            # persistent user-data dir + CDP :18800 (login/cookies persist as
            # before). No `openclaw browser start` call.
            result = ensure_partyrock_browser_direct()
            if not result.get("ok"):
                msg = (
                    "PartyRock Chrome-for-Testing CDP start failed: "
                    f"{result.get('error') or result}"
                )
                if required:
                    raise RuntimeError(msg)
                print(f"warn: {msg}")
            if result.get("ok"):
                # Cold start restores leftover PartyRock tabs; an already-running
                # CfT also accumulates idle app tabs from failed closes / prior
                # runs. Sweep on every tailor-path ensure (not cold-start only).
                # Live concurrent tailors are protected via in_use + live pid.
                try:
                    swept = close_idle_partyrock_tabs(resumes_dir=RESUMES_DIR)
                    if swept.get("closed"):
                        print(
                            "PartyRock cleanup closed "
                            f"{len(swept['closed'])} idle tab(s)"
                        )
                except Exception as e:
                    print(f"warn: PartyRock idle-tab cleanup failed: {e}")
            return result if isinstance(result, dict) else {"ok": bool(result)}
        except (FileNotFoundError, OSError, RuntimeError) as e:
            if required:
                raise
            print(f"warn: PartyRock browser start failed: {e}")
            return {"ok": False, "error": str(e)[:300]}


def _fill_hold_browser_active() -> bool:
    """True when a headed fill/CAPTCHA/Ready hold should protect CfT (CHR2-002/003).

    Signals: fresh captcha wait marker (TTL — CHR2-005), live fast_fill /
    run_fill_visible / hybrid_fill / real_job_test, or any Playwright fill
    Chrome-for-Testing main (excludes dashboard UI + OpenClaw PartyRock —
    CHR3-004). PartyRock-alone is never a fill hold.
    """
    try:
        from captcha_pause import captcha_waiting_marker_active

        if captcha_waiting_marker_active():
            return True
    except Exception:
        # Fallbacks if captcha_pause import fails mid-shutdown.
        for marker in (
            ROOT / "skyvern_runtime" / "real_job_results" / ".captcha_waiting.json",
            ROOT / "logs" / ".captcha_waiting.json",
        ):
            try:
                if marker.is_file():
                    return True
            except OSError:
                pass
    for pattern in (
        "fast_fill.py",
        "run_fill_visible.sh",
        "hybrid_fill.py",
        "real_job_test.py",
    ):
        try:
            out = subprocess.check_output(
                ["/usr/bin/pgrep", "-lf", pattern],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            continue
        if out.strip():
            return True
    # Fill CfT only — PartyRock OpenClaw alone must not look like a fill hold.
    return bool(_chrome_for_testing_main_pids())


def _stop_openclaw_managed_browser() -> dict:
    """Stop the PartyRock tailor CDP Chrome (port 18800).

    OpenClaw-free: there is no ``openclaw browser stop`` anymore — we launched
    Chrome-for-Testing directly, so we tear it down directly too, by killing
    only processes that carry the persistent user-data-dir or
    ``--remote-debugging-port=18800`` (never the user's daily Chrome).
    """
    result: dict = {"cli_stop": False, "killed": []}
    # Match identifiable argv for the PartyRock CfT main(s) only.
    patterns = [
        f"--user-data-dir={OPENCLAW_BROWSER_USER_DATA}",
        f"--remote-debugging-port={OPENCLAW_BROWSER_CDP_PORT}",
    ]
    leftover: list[int] = []
    for pat in patterns:
        leftover.extend(_pgrep_f_pids(pat))
    # Drop helpers — killing the main Chrome is enough; helpers die with it.
    # Still include all matched PIDs: Helpers share the same user-data-dir argv
    # and can linger in Dock if the main exits oddly; SIGTERM on helpers is safe.
    killed = _kill_pids_term_then_kill(leftover)
    result["killed"] = killed
    return result


def _kill_jh_associated_browsers(
    *,
    stop_openclaw_browser: bool = True,
    preserve_fill_cft: bool = False,
) -> dict:
    """Kill JH form-fill + PartyRock browsers only (never daily Chrome).

    Identifiers:
      - Chrome-for-Testing binary (Playwright fast_fill / hybrid leftovers),
        unless *preserve_fill_cft* (CHR2-003: Refresh while CAPTCHA/Ready hold)
      - Legacy ``partyrock_chrome_profile/`` leftovers (manual window retired;
        PartyRock now uses OpenClaw CfT profile)
      - OpenClaw managed browser (``~/.openclaw/browser/openclaw/user-data``,
        CDP :18800) when *stop_openclaw_browser* is True

    Dashboard ``dashboard_ui_profile`` is left to ``launch_dashboard.sh``
    so Refresh can keep the UI window.
    """
    summary: dict = {
        "chrome_for_testing": [],
        "partyrock_chrome_profile": [],
        "openclaw_browser": None,
        "fill_cft_preserved": False,
    }
    if preserve_fill_cft:
        summary["fill_cft_preserved"] = True
        summary["chrome_for_testing"] = []
    else:
        try:
            summary["chrome_for_testing"] = _kill_chrome_for_testing()
        except Exception as e:
            print(f"warn: chrome-for-testing cleanup: {e}")
    try:
        summary["partyrock_chrome_profile"] = _kill_chrome_user_data_dir(
            PARTYROCK_CHROME_PROFILE
        )
    except Exception as e:
        print(f"warn: partyrock chrome profile cleanup: {e}")
    if stop_openclaw_browser:
        try:
            summary["openclaw_browser"] = _stop_openclaw_managed_browser()
        except Exception as e:
            print(f"warn: openclaw browser cleanup: {e}")
            summary["openclaw_browser"] = {"error": str(e)[:200]}
    return summary


def _launcher_is_alive() -> bool:
    """True if launch_dashboard.sh recorded a live PID (waiting on this server).

    Prefers ``logs/dashboard_launcher.lockdir/pid`` (single-instance lock), then
    falls back to ``logs/dashboard_launcher.pid``. Avoids spawning a second
    ``--restart`` copy that would race the primary and kill dashboard Chrome.
    """
    candidates = [
        ROOT / "logs" / "dashboard_launcher.lockdir" / "pid",
        LAUNCHER_PID_PATH,
    ]
    for path in candidates:
        try:
            raw = path.read_text().strip()
            if not raw:
                continue
            pid = int(raw)
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            continue
    return False


def _write_restart_flag() -> None:
    RESTART_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESTART_FLAG_PATH.write_text(f"{time.time()}\n", encoding="utf-8")


def _spawn_relaunch_fallback() -> None:
    """When no launcher is waiting, spawn launch_dashboard.sh --restart."""
    if not LAUNCH_DASHBOARD_SH.is_file():
        print(f"warn: missing launcher script: {LAUNCH_DASHBOARD_SH}")
        return
    log_path = ROOT / "logs" / "dashboard_launcher.out"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 — kept for child lifetime
    try:
        subprocess.Popen(
            ["/bin/bash", str(LAUNCH_DASHBOARD_SH), "--restart"],
            cwd=str(ROOT),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=os.environ.copy(),
        )
        print("spawned launch_dashboard.sh --restart (no live launcher)")
    except Exception as e:
        print(f"warn: could not spawn relaunch: {e}")
        try:
            log_f.close()
        except Exception:
            pass


def ui_lifecycle_enabled() -> bool:
    raw = (os.environ.get("JOB_HUNTER_UI_LIFECYCLE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _prune_stale_ui_clients(now: float | None = None) -> None:
    """Drop clients that have not heartbeated within UI_HEARTBEAT_TIMEOUT_S.

    Caller must hold _ui_lock.
    """
    now = time.time() if now is None else now
    cutoff = now - UI_HEARTBEAT_TIMEOUT_S
    stale = [cid for cid, last in _ui_clients.items() if last < cutoff]
    for cid in stale:
        _ui_clients.pop(cid, None)


def ui_lifecycle_status() -> dict:
    with _ui_lock:
        _prune_stale_ui_clients()
        return {
            "enabled": ui_lifecycle_enabled(),
            "armed": _ui_lifecycle_armed,
            "client_count": len(_ui_clients),
            "heartbeat_timeout_s": UI_HEARTBEAT_TIMEOUT_S,
            "shutdown_requested": _shutdown_requested,
            "shutdown_reason": _shutdown_reason or None,
        }


def record_ui_heartbeat(client_id: str) -> tuple[dict, int]:
    """Register/refresh a dashboard tab. Arms UI-tied lifecycle on first pulse."""
    global _ui_lifecycle_armed
    cid = (client_id or "").strip()
    if not cid:
        return {"ok": False, "error": "client_id required"}, 400
    if not ui_lifecycle_enabled():
        return {
            "ok": True,
            "armed": False,
            "client_count": 0,
            "enabled": False,
            "heartbeat_timeout_s": UI_HEARTBEAT_TIMEOUT_S,
        }, 200
    with _ui_lock:
        _ui_clients[cid] = time.time()
        _ui_lifecycle_armed = True
        _prune_stale_ui_clients()
        count = len(_ui_clients)
        armed = _ui_lifecycle_armed
    return {
        "ok": True,
        "armed": armed,
        "client_count": count,
        "enabled": True,
        "heartbeat_timeout_s": UI_HEARTBEAT_TIMEOUT_S,
    }, 200


def request_ui_shutdown(client_id: str | None = None, *, force: bool = False) -> tuple[dict, int]:
    """Fast-path quit from sendBeacon / explicit shutdown.

    Removes this client (if given). Shuts down only when no live clients remain
    (or force=True). Other open tabs keep the stack alive.
    """
    if not ui_lifecycle_enabled() and not force:
        return {"ok": True, "shutdown": False, "enabled": False, "reason": "lifecycle disabled"}, 200
    cid = (client_id or "").strip()
    with _ui_lock:
        if cid:
            _ui_clients.pop(cid, None)
        _prune_stale_ui_clients()
        remaining = len(_ui_clients)
        armed = _ui_lifecycle_armed
    if force or (armed and remaining == 0):
        shutdown_dashboard_stack(
            "ui shutdown" if not force else "forced shutdown",
            client_id=cid or None,
        )
        return {
            "ok": True,
            "shutdown": True,
            "client_count": 0,
            "reason": _shutdown_reason,
        }, 200
    return {
        "ok": True,
        "shutdown": False,
        "client_count": remaining,
        "reason": "other clients still heartbeating",
    }, 200


def shutdown_dashboard_stack(reason: str, client_id: str | None = None) -> bool:
    """Abort discovery, kill tracked children + JH browsers, stop HTTP, exit.

    Idempotent. Returns True if this call initiated shutdown.

    Always tears down form-fill Chrome-for-Testing and legacy
    ``partyrock_chrome_profile`` on quit. On Refresh (``ui restart``): keeps
    OpenClaw PartyRock CDP (never counted as fill CfT — CHR3-003), and also
    preserves fill CfT + fill/agent procs when a CAPTCHA / Ready hold is live
    (CHR2-003 / CHR3-001 / CHR3-002). Dashboard UI Chrome is closed by
    ``launch_dashboard.sh``, not here.
    """
    global _shutdown_requested, _shutdown_reason, _preserve_fill_cft_on_exit
    with _shutdown_lock:
        if _shutdown_requested:
            return False
        _shutdown_requested = True
        _shutdown_reason = reason
    print(f"dashboard shutdown: {reason}" + (f" (client={client_id})" if client_id else ""))
    try:
        if _discovery_state.get("running"):
            request_discovery_abort()
            # Flush before process exit — discovery thread may not finish.
            _mark_incomplete_sources_stopped()
            _flush_discovery_checkpoint("incomplete")
    except Exception as e:
        print(f"warn: discovery abort on shutdown: {e}")
    # Orphaned Playwright windows left after process-group kill.
    is_restart = "restart" in (reason or "").lower() or _restart_requested
    preserve_fill = False
    if is_restart:
        try:
            preserve_fill = _fill_hold_browser_active()
        except Exception as e:
            print(f"warn: fill-hold probe on restart: {e}")
            preserve_fill = True  # fail closed — keep review window
    _preserve_fill_cft_on_exit = bool(preserve_fill)
    try:
        # CHR3-002: do not SIGTERM fill/agent while CAPTCHA/Ready hold is live.
        _kill_all_tracked_child_procs(preserve_fill_procs=preserve_fill)
    except Exception as e:
        print(f"warn: child cleanup on shutdown: {e}")
    try:
        browser_summary = _kill_jh_associated_browsers(
            stop_openclaw_browser=not is_restart,
            preserve_fill_cft=preserve_fill,
        )
        print(f"dashboard browser cleanup: {browser_summary}")
    except Exception as e:
        print(f"warn: JH browser cleanup on shutdown: {e}")

    def _stop_http() -> None:
        srv = _http_server
        if srv is not None:
            try:
                srv.shutdown()
            except Exception as e:
                print(f"warn: HTTP shutdown: {e}")

    threading.Thread(target=_stop_http, daemon=True, name="dashboard-http-shutdown").start()
    return True


def request_ui_restart(client_id: str | None = None) -> tuple[dict, int]:
    """Refresh path: same child cleanup as shutdown, then relaunch the server.

    Writes `logs/dashboard_restart.flag` so `launch_dashboard.sh` respawns the
    server after exit *without* opening a new Chrome window (UI reloads in
    place). If no launcher is waiting, spawns `launch_dashboard.sh --restart`
    as a fallback. Forces cleanup even when other tabs are open.
    """
    global _restart_requested
    cid = (client_id or "").strip() or None
    _restart_requested = True
    try:
        _write_restart_flag()
    except OSError as e:
        return {"ok": False, "error": f"could not write restart flag: {e}"}, 500
    with _ui_lock:
        _ui_clients.clear()
    launcher_alive = _launcher_is_alive()
    if not launcher_alive:
        try:
            _spawn_relaunch_fallback()
        except Exception as e:
            print(f"warn: relaunch fallback failed: {e}")
    shutdown_dashboard_stack("ui restart", client_id=cid)
    return {
        "ok": True,
        "restart": True,
        "shutdown": True,
        "launcher_will_respawn": launcher_alive,
        "reason": _shutdown_reason or "ui restart",
    }, 200


def _ui_watchdog_loop() -> None:
    """Prune stale UI heartbeats; do not auto-quit on idle.

    Heartbeats still track connected clients for multi-tab shutdown
    (last explicit Quit / pagehide / Cmd+Q). Stalled pulses alone must not
    call shutdown_dashboard_stack — laptop sleep / background tabs used to
    kill the stack after UI_HEARTBEAT_TIMEOUT_S.
    """
    while True:
        time.sleep(2)
        if _shutdown_requested or not ui_lifecycle_enabled():
            return
        with _ui_lock:
            if not _ui_lifecycle_armed:
                continue
            _prune_stale_ui_clients()


def check_ui_heartbeat_timeout_for_tests(now: float | None = None) -> bool:
    """Test helper: prune stale clients. Idle never starts shutdown (returns False)."""
    if not ui_lifecycle_enabled():
        return False
    with _ui_lock:
        if not _ui_lifecycle_armed:
            return False
        _prune_stale_ui_clients(now)
    return False


def request_discovery_abort(source_id: str | None = None) -> tuple[dict, int]:
    """Abort discovery: all sources, or a single catalog source_id.

    Global abort (source_id None / \"all\"): flag the runner, kill all active
    scrape trees unless mid-write. Per-source: kill only that source's
    process group and mark it stopped; other scrapes continue (no global
    abort_requested / discovery stays running).
    """
    sid = (str(source_id).strip() if source_id is not None else "") or None
    if sid and sid.lower() == "all":
        sid = None

    if sid:
        if sid not in DISCOVERY_SOURCE_IDS:
            return {"error": f"unknown source_id: {sid}", "discovery": discovery_status()}, 400
        with _discovery_lock:
            if not _discovery_state.get("running"):
                return {"error": "discovery is not running", "discovery": discovery_status()}, 409
            _discovery_source_aborts.add(sid)
        _kill_discovery_proc_by_key(_discovery_source_track_key(sid))
        _update_discovery_sources(
            (sid,), status="stopped", detail="Stopped",
            only_if_status=("pending", "collecting"),
        )
        _flush_discovery_checkpoint("running")
        return {"ok": True, "aborting": False, "source_id": sid, "discovery": discovery_status()}, 200

    with _discovery_lock:
        if not _discovery_state.get("running"):
            running = False
            already = False
            protect = False
        elif _discovery_state.get("abort_requested"):
            running = True
            already = True
            protect = False
        else:
            running = True
            already = False
            _discovery_state["abort_requested"] = True
            _discovery_state["can_abort"] = False
            _discovery_state["phase"] = "aborting"
            _discovery_state["phase_label"] = DISCOVERY_PHASE_LABELS["aborting"]
            protect = _discovery_protect_proc
    if not running:
        return {"error": "discovery is not running", "discovery": discovery_status()}, 409
    if already:
        return {"ok": True, "aborting": True, "discovery": discovery_status()}, 200
    if not protect:
        _kill_all_discovery_procs()
    _flush_discovery_checkpoint("incomplete")
    return {"ok": True, "aborting": True, "discovery": discovery_status()}, 200


def _parse_discovery_log_line(line: str, mode: str | None) -> None:
    """Update live per-source counts from scout/ats/builtin stdout lines."""
    if not mode:
        return
    if mode == "scout":
        m = _SCOUT_GOT_RE.search(line)
        if m:
            n, site = int(m.group(1)), m.group(2)
            with _discovery_lock:
                for src in _discovery_state.get("sources") or []:
                    if src.get("id") == site:
                        src["status"] = "collecting"
                        src["count"] = int(src.get("count") or 0) + n
                        src["detail"] = f"{src['count']} listings"
                        break
        return
    if mode == "ats":
        m = _ATS_GOT_RE.search(line)
        if m:
            n, ats = int(m.group(1)), m.group(2)
            prog = _ATS_PROGRESS_RE.search(line)
            detail = f"{prog.group(1)}/{prog.group(2)} boards" if prog else ""
            with _discovery_lock:
                for src in _discovery_state.get("sources") or []:
                    if src.get("id") == ats:
                        src["status"] = "collecting"
                        src["count"] = int(src.get("count") or 0) + n
                        if detail:
                            src["detail"] = f"{src['count']} · {detail}"
                        else:
                            src["detail"] = f"{src['count']} listings"
                        break
        return
    if mode == "india":
        m = _INDIA_GOT_RE.search(line)
        if m:
            n, sid = int(m.group(1)), m.group(2)
            with _discovery_lock:
                for src in _discovery_state.get("sources") or []:
                    if src.get("id") == sid:
                        src["status"] = "collecting"
                        src["count"] = int(src.get("count") or 0) + n
                        src["detail"] = f"{src['count']} listings"
                        break
        return
    if mode == "builtin":
        m = _BUILTIN_PROC_RE.search(line)
        if m:
            done, total, usable = int(m.group(1)), int(m.group(2)), int(m.group(3))
            _set_source_fields(
                "builtin",
                status="collecting",
                count=usable,
                detail=f"{done}/{total} pages · {usable} listings",
            )
            return
        m2 = _WROTE_LISTINGS_RE.search(line)
        if m2:
            n = int(m2.group(1))
            _set_source_fields("builtin", status="collecting", count=n, detail=f"{n} listings")


class _LogTail:
    """Incrementally read new lines from a growing log file."""

    def __init__(self, path: Path):
        self.path = path
        self._pos = 0
        self._buf = ""

    def poll_lines(self) -> list[str]:
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._pos)
                chunk = f.read()
                self._pos = f.tell()
        except FileNotFoundError:
            return []
        if not chunk:
            return []
        self._buf += chunk
        lines = self._buf.split("\n")
        self._buf = lines.pop() if lines else ""
        return lines


def _run_subprocess_step(cmd: list[str], log_name: str, timeout_s: int,
                          track_key: str = DISCOVERY_SESSION_KEY,
                          *, allow_abort: bool = False,
                          protect_from_abort: bool = False,
                          log_parse_mode: str | None = None,
                          activity_job_id: str | None = None) -> tuple[int, Path]:
    """Run one pipeline step as a plain subprocess with real logging (not
    /dev/null - piping output away makes it impossible to check progress
    mid-run). Tracked under track_key for is_session_running()'s
    double-start check and for the dashboard's "what's currently running"
    display - callers working on a specific job should pass that job's
    own session_key, not the default, so it's attributed to the right job
    instead of reporting it as a stray discovery run.

    Discovery steps pass allow_abort=True so POST /api/discover/abort can
    kill the process group(s). Parallel scrapes use distinct track_key
    suffixes so they don't overwrite each other in _running_procs.
    protect_from_abort=True (write step) finishes the current write instead
    of killing mid-jobs.json.

    activity_job_id: when set, tee new log lines into the job's Live activity
    buffer (same feed Fast fill uses)."""
    global _discovery_protect_proc
    ROOT.joinpath("logs").mkdir(exist_ok=True)
    log_path = ROOT / "logs" / log_name
    log_file = open(log_path, "w")
    step_start = time.monotonic()
    # New session = own process group so abort can kill children (JobSpy, etc.).
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _running_procs[track_key] = proc
    if allow_abort:
        _register_discovery_proc(track_key, proc)
        with _discovery_lock:
            _discovery_protect_proc = protect_from_abort
    need_tail = bool(log_parse_mode or activity_job_id)
    tail = _LogTail(log_path) if need_tail else None

    def _consume_tail_lines() -> None:
        if tail is None:
            return
        for line in tail.poll_lines():
            if log_parse_mode:
                _parse_discovery_log_line(line, log_parse_mode)
            if activity_job_id:
                ingest_pipeline_stdout_line(activity_job_id, line)

    exit_code = -1
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            if allow_abort and _discovery_abort_requested() and not protect_from_abort:
                _kill_discovery_process_tree(proc)
                exit_code = DISCOVERY_ABORT_EXIT
                break
            if activity_job_id and _job_fill_hard_aborted(activity_job_id):
                _kill_discovery_process_tree(proc)
                exit_code = FILL_ABORT_EXIT
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_discovery_process_tree(proc)
                exit_code = -1
                break
            try:
                exit_code = proc.wait(timeout=min(0.4, remaining))
                # Abort may have killed us from another thread; normalize.
                if allow_abort and _discovery_abort_requested() and not protect_from_abort:
                    exit_code = DISCOVERY_ABORT_EXIT
                break
            except subprocess.TimeoutExpired:
                _consume_tail_lines()
                continue
        _consume_tail_lines()
    finally:
        _running_procs.pop(track_key, None)
        if allow_abort:
            _unregister_discovery_proc(track_key, proc)
            with _discovery_lock:
                if protect_from_abort or not _discovery_procs_by_key:
                    _discovery_protect_proc = False
        log_file.close()
    _log_timing(log_name.removesuffix(".log"), time.monotonic() - step_start, f"exit={exit_code}")
    return exit_code, log_path


def _listing_file_nonempty(path: Path) -> bool:
    """True if path exists and parses as a non-empty JSON list."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        data = json.loads(path.read_text())
    except Exception:
        return False
    return isinstance(data, list) and len(data) > 0


def _source_listing_path(today: str, source_id: str) -> Path:
    if source_id in SCOUT_SOURCE_IDS:
        return LISTINGS_DIR / f"{today}-{source_id}.json"
    if source_id in ATS_SOURCE_IDS:
        return LISTINGS_DIR / f"{today}-ats-{source_id}.json"
    if source_id == "builtin":
        return LISTINGS_DIR / f"{today}-builtin.json"
    # Adzuna's file is suffixed -adzuna-in to make its India origin explicit;
    # the others use their bare source id.
    if source_id == "adzuna":
        return LISTINGS_DIR / f"{today}-adzuna-in.json"
    if source_id in INDIA_ONLY_SOURCE_IDS:
        return LISTINGS_DIR / f"{today}-{source_id}.json"
    raise ValueError(f"unknown discovery source: {source_id}")


def _source_qualified_tag(source_id: str) -> str:
    if source_id in ATS_SOURCE_IDS:
        return f"ats-{source_id}"
    return source_id


def _builtin_scrape_cmd(
    listing: Path,
    *,
    skip_urls_file: Path | None,
    days_since_updated: int,
) -> list[str]:
    days = normalize_builtin_days_since_updated(days_since_updated)
    cmd = [
        PYTHON_BIN,
        "-u",
        str(ROOT / "scripts" / "scrape_builtin.py"),
        "--out",
        str(listing),
        "--days-since-updated",
        str(days),
    ]
    if skip_urls_file is not None:
        cmd.extend(["--skip-urls", str(skip_urls_file)])
    return cmd


def _incremental_merge_listing(listing_path: Path, today: str, skip_file: Path,
                               source_tag: str) -> bool:
    """Dedup one source listing and merge into jobs.json. Returns True on write success."""
    if not _listing_file_nonempty(listing_path):
        return False
    qualified_file = LISTINGS_DIR / f"{today}-qualified-{source_tag}.json"
    _set_discovery_phase("dedup")
    dedup_exit, dedup_log = _run_subprocess_step(
        [PYTHON_BIN, "-u", str(ROOT / "scripts" / "dedup_listings.py"),
         str(listing_path), "--out", str(qualified_file)],
        f"dedup_listings_{source_tag}.log",
        120,
        track_key=f"{DISCOVERY_SESSION_KEY}:dedup:{source_tag}",
        allow_abort=True,
        protect_from_abort=True,
    )
    if dedup_exit != 0 or not qualified_file.exists():
        print(f"warn: incremental dedup failed for {source_tag}: exit={dedup_exit} ({dedup_log})")
        return False
    if not _listing_file_nonempty(qualified_file):
        return False
    _set_discovery_phase("write")
    write_exit, write_log = _run_subprocess_step(
        [PYTHON_BIN, "-u", str(ROOT / "scripts" / "write_discovered_jobs.py"),
         str(qualified_file), "--skip-companies", str(skip_file)],
        f"write_discovered_jobs_{source_tag}.log",
        60,
        track_key=f"{DISCOVERY_SESSION_KEY}:write:{source_tag}",
        allow_abort=True,
        protect_from_abort=True,
    )
    if write_exit != 0:
        print(f"warn: incremental write failed for {source_tag}: exit={write_exit} ({write_log})")
        return False
    added = _parse_jobs_added_from_log(write_log)
    if added:
        _discovery_checkpoint_meta["jobs_added"] = (
            int(_discovery_checkpoint_meta.get("jobs_added") or 0) + added
        )
    return True


def _finalize_discovery_source(
    source_id: str, exit_code: int, listing_path: Path, *, aborted: bool,
) -> None:
    """Mark one catalog source done/failed/stopped from its exit code + listing file."""
    if aborted:
        if listing_path.exists():
            _apply_site_counts(
                _count_listings_by_site(listing_path), (source_id,),
                status="stopped", zero_detail="Stopped")
        else:
            _update_discovery_sources(
                (source_id,), status="stopped", detail="Stopped",
                only_if_status=("pending", "collecting", "stopped"))
        return
    if listing_path.exists():
        _apply_site_counts(_count_listings_by_site(listing_path), (source_id,))
    elif exit_code != 0:
        _update_discovery_sources((source_id,), status="failed", detail="Failed")
    else:
        _apply_site_counts({}, (source_id,))


def run_scout_scrape_then_dedup() -> None:
    """Scraping, dedup/qualify filtering, the already-tracked-company check,
    and the jobs.json write are all pure mechanical work with no judgment
    calls in them - run them as plain subprocesses, not inside an LLM turn.
    Babysitting a 5-15min scrape token-by-token wastes agent turn time/
    budget and can blow past the gateway's hard per-turn timeout, silently
    killing the whole run mid-work (observed: a combined scrape+dedup turn
    aborted with 'request timed out' after exactly the --timeout value,
    having already scraped successfully but never reaching the jobs.json
    write). Re-deriving dedup/filter logic from scratch every run is also
    unreliable - a plain script keeps the matching consistent.

    The already-tracked-company check used to need an agent turn to query
    Notion. Now that the tracker is a local Excel file (application_tracker
    .xlsx, via scripts/tracker.py), that check is just as mechanical as
    everything else - so a normal discovery run needs zero agent/LLM
    involvement end to end. The agent only gets pulled in if one of these
    steps actually fails and needs a human-judgment fix.

    Each enabled catalog source runs as its own subprocess. As each finishes
    (or is aborted with a partial listing file), that file is deduped and
    merged into jobs.json. A final dedup_jobs pass runs if any writes landed.
    Global abort still flushes completed/partial listing files before finish.

    Resume: if ``logs/discovery_checkpoint.json`` has leftover work for
    today, completed sources are skipped; incomplete ones continue after
    merging any partial listing file already on disk. Scrapers receive a
    shared skip-urls file (jobs.json + blocked + prior listings) so known
    JDs are not re-fetched."""
    try:
        today = _today_local_iso()
        enabled = _discovery_enabled_set()
        # Propagate enabled regions (US default, India opt-in) to every
        # discovery child (scout / scrape_ats / scrape_builtin / dedup /
        # write) via env — they inherit os.environ (no env= on Popen).
        regions = enabled_discovery_regions()
        os.environ["JOBHUNTER_DISCOVERY_REGIONS"] = ",".join(regions)
        print(f"discovery regions: {', '.join(regions)}")
        LISTINGS_DIR.mkdir(parents=True, exist_ok=True)
        with _discovery_lock:
            resumed = bool(_discovery_state.get("resumed"))
            sources_snap = [dict(s) for s in (_discovery_state.get("sources") or [])]
        skip_ids = {
            s["id"] for s in sources_snap
            if s.get("enabled") and s.get("status") == "completed"
        }
        merged_paths: set[str] = set(_discovery_checkpoint_meta.get("merged_paths") or ())
        merges_ok = int(_discovery_checkpoint_meta.get("merges_ok") or 0)

        # Known URLs — scrapers skip detail fetches / duplicate rows.
        skip_urls_file = ROOT / "logs" / "discovery_skip_urls.json"
        try:
            from known_job_urls import (  # noqa: E402
                load_known_url_keys,
                write_skip_urls_file,
            )
            listing_paths = []
            for sid in enabled:
                try:
                    listing_paths.append(_source_listing_path(today, sid))
                except ValueError:
                    pass
            known = load_known_url_keys(extra_listing_paths=listing_paths)
            write_skip_urls_file(skip_urls_file, known)
            print(f"discovery skip-urls: {len(known)} known key(s) -> {skip_urls_file}")
        except Exception as e:
            print(f"warn: building discovery skip-urls failed: {e}")
            skip_urls_file = None

        # Build one scrape job per enabled catalog source that still needs work.
        # Built In has no public API / JobSpy support — direct HTML scrape
        # (see scrape_builtin.py). Timeout 5400s: filtered search + sequential
        # page fetch can run ~45 minutes in the wild.
        source_jobs: list[tuple[str, Path, list[str], int, str, str]] = []
        for sid in SCOUT_SOURCE_IDS:
            if sid not in enabled or sid in skip_ids:
                continue
            listing = _source_listing_path(today, sid)
            cmd = [PYTHON_BIN, "-u", str(SCOUT_SCRIPT),
                   "--sites", sid, "--out", str(listing)]
            source_jobs.append(
                (sid, listing, cmd, SCOUT_TIMEOUT_S, f"scout_{sid}.log", "scout"))
        for sid in ATS_SOURCE_IDS:
            if sid not in enabled or sid in skip_ids:
                continue
            listing = _source_listing_path(today, sid)
            cmd = [PYTHON_BIN, "-u", str(ROOT / "scripts" / "scrape_ats.py"),
                   "--platforms", sid, "--out", str(listing)]
            if skip_urls_file is not None:
                cmd.extend(["--skip-urls", str(skip_urls_file)])
            source_jobs.append(
                (sid, listing, cmd, 300, f"scrape_ats_{sid}.log", "ats"))
        if "builtin" in enabled and "builtin" not in skip_ids:
            listing = _source_listing_path(today, "builtin")
            days = load_discovery_settings()["builtin_days_since_updated"]
            cmd = _builtin_scrape_cmd(
                listing,
                skip_urls_file=skip_urls_file,
                days_since_updated=days,
            )
            source_jobs.append(
                ("builtin", listing, cmd, 5400, "scrape_builtin.log", "builtin"))
        # India-only sources: only meaningful when the India region is on.
        # (_handle_discover already strips them from `enabled` when India is
        # off; this guard is belt-and-suspenders for direct/API callers.)
        if "india" in regions:
            for sid in INDIA_ONLY_SOURCE_IDS:
                if sid not in enabled or sid in skip_ids:
                    continue
                listing = _source_listing_path(today, sid)
                cmd = [PYTHON_BIN, "-u", str(INDIA_SOURCE_SCRIPTS[sid]),
                       "--out", str(listing)]
                source_jobs.append(
                    (sid, listing, cmd, INDIA_SOURCE_TIMEOUT_S,
                     f"scrape_{sid}.log", "india"))

        if skip_ids:
            # Keep completed rows visible; clarify they were resumed/skipped.
            for sid in skip_ids:
                with _discovery_lock:
                    for src in _discovery_state.get("sources") or []:
                        if src.get("id") == sid and src.get("status") == "completed":
                            detail = src.get("detail") or ""
                            if "already done" not in detail.lower():
                                n = int(src.get("count") or 0)
                                src["detail"] = (
                                    f"{n} listings (skipped — already done)"
                                    if n else "Skipped — already done"
                                )
                            break

        _set_discovery_phase("resuming" if resumed else "scraping")
        if source_jobs:
            _update_discovery_sources(
                tuple(sid for sid, *_ in source_jobs),
                status="collecting",
                detail="Continuing…" if resumed else "Starting…")
        _flush_discovery_checkpoint("running")

        # Tracker once before first merge (skip-companies for write_discovered_jobs).
        # Deliberately NOT under listings/ — scrape_ats --seed-from globs *.json there.
        skip_file = ROOT / "logs" / "tracked-companies-skip.json"
        _set_discovery_phase("tracker")
        tracker_exit, tracker_log = _run_subprocess_step(
            [PYTHON_BIN, "-u", str(ROOT / "scripts" / "tracker.py"),
             "list-companies", "--out", str(skip_file)],
            "tracker_list.log", 30,
            allow_abort=True,
        )
        if tracker_exit == DISCOVERY_ABORT_EXIT or _discovery_abort_requested():
            _mark_incomplete_sources_stopped()
            _flush_discovery_checkpoint("incomplete")
            _finish_discovery(False, "Aborted by user")
            return
        if tracker_exit != 0 or not skip_file.exists():
            err = (
                f"scripts/tracker.py list-companies exited with code {tracker_exit} "
                f"(see {tracker_log})."
            )
            _set_discovery_phase("agent_recovery", error=err)
            run_agent_message(
                DISCOVERY_SESSION_KEY,
                f"{err} Check for a bug in tracker.py and fix it.",
                timeout_s=600,
            )
            _flush_discovery_checkpoint("incomplete")
            _finish_discovery(False, err)
            return

        _set_discovery_phase("scraping")
        scrape_results: dict[str, tuple[int, Path, Path]] = {}

        def _note_merge(listing: Path) -> None:
            nonlocal merges_ok
            key = str(listing)
            if key in merged_paths:
                return
            merged_paths.add(key)
            merges_ok += 1
            _discovery_checkpoint_meta["merged_paths"] = set(merged_paths)
            _discovery_checkpoint_meta["merges_ok"] = merges_ok

        def _try_merge(sid: str, listing: Path) -> bool:
            key = str(listing)
            if key in merged_paths:
                return False
            if not _listing_file_nonempty(listing):
                return False
            tag = _source_qualified_tag(sid)
            if _incremental_merge_listing(listing, today, skip_file, tag):
                _note_merge(listing)
                _flush_discovery_checkpoint("running")
                return True
            return False

        # Completed-but-unmerged leftovers (crash between finalize and merge).
        for sid in skip_ids:
            try:
                _try_merge(sid, _source_listing_path(today, sid))
            except ValueError:
                pass

        # Before re-scraping incomplete sources: merge leftover partials, then
        # drop those paths from merged_paths so the post-scrape listing merges.
        for sid, listing, *_ in source_jobs:
            _try_merge(sid, listing)
            merged_paths.discard(str(listing))
        _discovery_checkpoint_meta["merged_paths"] = set(merged_paths)
        _discovery_checkpoint_meta["merges_ok"] = merges_ok

        def _run_one_source(
            sid: str, listing: Path, cmd: list[str], timeout_s: int,
            log_name: str, mode: str,
        ) -> tuple[str, int, Path, Path]:
            if _source_abort_requested(sid) or _discovery_abort_requested():
                return sid, DISCOVERY_ABORT_EXIT, Path(""), listing
            code, log = _run_subprocess_step(
                cmd, log_name, timeout_s,
                track_key=_discovery_source_track_key(sid),
                allow_abort=True, log_parse_mode=mode)
            return sid, code, log, listing

        workers = max(1, len(source_jobs)) if source_jobs else 1
        if source_jobs:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(_run_one_source, sid, listing, cmd, timeout_s, log_name, mode)
                    for sid, listing, cmd, timeout_s, log_name, mode in source_jobs
                ]
                for fut in concurrent.futures.as_completed(futures):
                    sid, code, log, listing = fut.result()
                    scrape_results[sid] = (code, log, listing)
                    aborted = (
                        _discovery_abort_requested()
                        or _source_abort_requested(sid)
                        or code == DISCOVERY_ABORT_EXIT
                    )
                    _finalize_discovery_source(sid, code, listing, aborted=aborted)
                    # Merge on success or abort-with-partial listing file.
                    _try_merge(sid, listing)
                    _flush_discovery_checkpoint("running")

        # After all sources settle: still merge any leftover partial files
        # (global abort must not skip flushing completed/partial listings).
        for sid, listing, *_ in source_jobs:
            if sid not in scrape_results:
                _finalize_discovery_source(
                    sid, DISCOVERY_ABORT_EXIT, listing,
                    aborted=True)
            _try_merge(sid, listing)

        _mark_incomplete_sources_stopped()
        aborted = _discovery_abort_requested()
        _flush_discovery_checkpoint("incomplete" if aborted else "running")

        if merges_ok > 0:
            _set_discovery_phase("dedup_jobs")
            _run_subprocess_step(
                [PYTHON_BIN, "-u", str(ROOT / "scripts" / "dedup_jobs.py")],
                "dedup_jobs.log",
                120,
                allow_abort=True,
                protect_from_abort=True,
            )

        if aborted:
            if merges_ok > 0:
                _finish_discovery(True)
            else:
                _finish_discovery(False, "Aborted by user")
            return

        _finish_discovery(True)
    except Exception as e:
        if _discovery_abort_requested():
            _mark_incomplete_sources_stopped()
            _flush_discovery_checkpoint("incomplete")
            _finish_discovery(False, "Aborted by user")
            return
        _flush_discovery_checkpoint("incomplete")
        _finish_discovery(False, str(e))
        raise


def _metrics_timeline_payload() -> dict:
    """Ops fill-quality trend + ratchet floors (counts only; no PII).

    Reads ``scripts/fastfill/learning_store/metrics_timeline.jsonl``. Missing
    file → empty rows with ratchet ok. Import failures never break the dashboard.
    """
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "fastfill"))
        from metrics_timeline import load_timeline, ratchet_check  # type: ignore

        rows = load_timeline()
        latest = rows[-1] if rows else None
        ok, violations = (True, [])
        if latest is not None:
            ok, violations = ratchet_check(latest, rows[:-1])
        # Slim rows for the chart (last 30).
        slim = [
            {
                "iso": r.get("iso"),
                "label": r.get("label"),
                "pass_rate": r.get("pass_rate"),
                "n": r.get("n"),
                "passed": r.get("passed"),
                "safety_fail_n": r.get("safety_fail_n"),
                "never_submit_all": r.get("never_submit_all", True),
                "by_platform": r.get("by_platform") or {},
            }
            for r in rows[-30:]
        ]
        return {
            "ok": True,
            "n": len(rows),
            "latest": slim[-1] if slim else None,
            "ratchet_ok": ok,
            "ratchet_violations": violations,
            "rows": slim,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "n": 0,
            "latest": None,
            "ratchet_ok": True,
            "ratchet_violations": [],
            "rows": [],
        }


def runtime_status() -> dict:
    """Dashboard status bar payload: discovery phase + what's actively
    running. Deliberately excludes profile/PII — ids, company, title,
    status only."""
    local_keys = {k for k, p in _running_procs.items() if p.poll() is None}
    try:
        turn_keys = agent_runner.active_turn_keys()
    except Exception as e:
        print(f"warn: active_turn_keys failed: {e}")
        turn_keys = set()
    running_keys = local_keys | turn_keys
    running_jobs = []
    job_prefix = "agent:job-hunter:job-"
    for session_key in sorted(running_keys):
        if session_key == DISCOVERY_SESSION_KEY or not session_key.startswith(job_prefix):
            continue
        snap = _runtime_job_snapshots.get(session_key)
        if snap is None:
            snap = {
                "id": session_key[len(job_prefix):],
                "company": "",
                "title": "",
                "status": "running",
            }
        running_jobs.append(dict(snap))
    disc = _discovery_status_in_memory()
    discovery_running = disc.get("running") or (DISCOVERY_SESSION_KEY in running_keys)
    aj = None
    if running_jobs:
        aj = running_jobs[0]
    elif discovery_running:
        aj = {"id": None, "company": "(discovery run)", "title": "", "status": "discovery"}
    return {
        "discovery": disc,
        "discovery_running": discovery_running,
        "active_job": aj,
        "running_jobs": running_jobs,
        "running_job_ids": [j["id"] for j in running_jobs],
        "ui_lifecycle": ui_lifecycle_status(),
    }


def _parse_jobs_payload(raw: str) -> dict:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise json.JSONDecodeError("jobs root must be object", raw, 0)
    if not isinstance(data.get("jobs"), list):
        data["jobs"] = []
    return data


def _recover_jobs_json_from_backup() -> dict | None:
    """Best-effort restore from jobs.json.bak* when the live file is corrupt."""
    backups = sorted(
        JOBS_FILE.parent.glob("jobs.json.bak*"),
        key=lambda p: p.stat().st_mtime if p.is_file() else 0,
        reverse=True,
    )
    for bak in backups:
        try:
            data = _parse_jobs_payload(bak.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        try:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            corrupt_path = JOBS_FILE.with_name(f"jobs.json.corrupt-{ts}")
            if JOBS_FILE.exists():
                JOBS_FILE.rename(corrupt_path)
            JOBS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"warn: restored jobs.json from {bak.name} (corrupt copy → {corrupt_path.name})")
            return data
        except OSError as e:
            print(f"warn: could not restore jobs.json from {bak.name}: {e}")
    return None


def read_jobs() -> dict:
    if not JOBS_FILE.exists():
        return {"jobs": []}
    JOBS_LOCK_FILE.touch(exist_ok=True)
    with open(JOBS_LOCK_FILE, "r+") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_SH)
        try:
            try:
                return _parse_jobs_payload(JOBS_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                print(f"warn: jobs.json corrupt ({e})")
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)
    recovered = _recover_jobs_json_from_backup()
    if recovered is not None:
        return recovered
    try:
        if JOBS_FILE.exists():
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            JOBS_FILE.rename(JOBS_FILE.with_name(f"jobs.json.corrupt-{ts}"))
    except OSError as e:
        print(f"warn: could not quarantine corrupt jobs.json: {e}")
    print("warn: jobs.json unreadable — using empty job list")
    return {"jobs": []}


# Truncation note written by write_discovered_jobs.trim_description / manual add.
_JD_PREVIEW_SUFFIX_RE = re.compile(
    r"\s*(?:…|\.\.\.)\s*\[full text in resumes/[^\]]+\]\s*$",
    re.IGNORECASE,
)
# Common markdown/CommonMark backslash-escapes that look like junk in plain text.
_JD_MD_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|&<>])")
_JD_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class _StripHtmlTags(HTMLParser):
    """Stdlib-only tag stripper (dashboard has no BeautifulSoup dep)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self._parts.append(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self._parts.append(html.unescape(f"&#{name};"))

    def get_text(self) -> str:
        return "".join(self._parts)


def _maybe_fix_mojibake(text: str) -> str:
    """If UTF-8 was decoded as Latin-1/cp1252, recover the original UTF-8."""
    if not any(marker in text for marker in ("Ã", "Â", "â", "ð")):
        return text
    bad = lambda s: s.count("Ã") + s.count("Â") + s.count("â") + s.count("\ufffd")
    for enc in ("cp1252", "latin-1"):
        try:
            fixed = text.encode(enc).decode("utf-8")
        except UnicodeError:
            continue
        if bad(fixed) < bad(text):
            return fixed
    return text


def sanitize_job_description_for_display(text: str) -> str:
    """Clean JD text for safe dashboard display (never execute HTML/JS).

    Handles double-encoded entities (&lt;p&gt;…), leftover HTML tags,
    markdown backslash escapes (\\-, \\&), control/NUL bytes, classic
    mojibake, and the jobs.json preview suffix that points at jd_full.txt.
    """
    if not text:
        return ""
    if text.lstrip().startswith("%PDF") or "/Type /Page" in text[:4000]:
        return "[Job description looks like binary/PDF data and cannot be shown as text.]"

    text = _maybe_fix_mojibake(text)
    # Unescape repeatedly for double-encoded Greenhouse-style markup.
    for _ in range(3):
        nxt = html.unescape(text)
        if nxt == text:
            break
        text = nxt

    if "<" in text and ">" in text:
        parser = _StripHtmlTags()
        try:
            parser.feed(text)
            parser.close()
            stripped = parser.get_text()
            if stripped.strip():
                text = stripped
        except Exception:
            text = re.sub(r"<[^>]+>", "", text)

    text = _JD_CONTROL_RE.sub("", text)
    text = _JD_PREVIEW_SUFFIX_RE.sub("", text)
    text = _JD_MD_ESCAPE_RE.sub(r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_raw_job_description(job: dict) -> tuple[str, str]:
    """Prefer resumes/<id>/jd_full.txt; fall back to jobs.json preview field.

    Returns (raw_text, source) where source is jd_full.txt | jobs.json | none.
    """
    job_id = job.get("id") or ""
    full_path = RESUMES_DIR / job_id / "jd_full.txt"
    if job_id and full_path.is_file():
        try:
            return full_path.read_text(encoding="utf-8", errors="replace"), "jd_full.txt"
        except OSError:
            pass
    preview = job.get("job_description") or ""
    if isinstance(preview, str) and preview.strip():
        return preview, "jobs.json"
    return "", "none"


def slim_job_for_list(job: dict) -> dict:
    """List payloads omit full/preview JD bodies — fetch on demand instead."""
    # timeline can be long; dossier loads it via /api/jobs/<id>/activity.
    out = {
        k: v
        for k, v in job.items()
        if k not in ("job_description", "timeline")
    }
    # Hint only (no per-poll filesystem scan). Description endpoint still
    # prefers resumes/<id>/jd_full.txt when the user expands a job.
    out["has_description"] = bool((job.get("job_description") or "").strip())
    # Re-resolve when flag claims on-disk resume so a missing file cannot
    # leave a stale True. Explicit False is trusted on the list hot path;
    # Start / fill paths call sync_job_resume_on_disk to clear false flags.
    if job.get("resume_on_disk"):
        disk = resolve_job_resume_file(job)
        resume_on_disk = disk is not None
    elif "resume_on_disk" not in job:
        disk = resolve_job_resume_file(job)
        resume_on_disk = disk is not None
    else:
        disk = None
        resume_on_disk = False
    out["resume_on_disk"] = resume_on_disk
    out["resume_display_name"] = (
        conventional_resume_filename(job) if resume_on_disk else None
    )
    if not resume_on_disk:
        out["resume_path"] = None
    elif disk is not None:
        try:
            out["resume_path"] = str(disk.relative_to(ROOT))
        except ValueError:
            out["resume_path"] = str(disk)
    elif job.get("resume_path"):
        out["resume_path"] = job.get("resume_path")
    return out


def sync_job_resume_on_disk(job: dict) -> bool:
    """Set resume_on_disk True only when resolve_job_resume_file succeeds."""
    disk = resolve_job_resume_file(job)
    if disk is None:
        job["resume_on_disk"] = False
        return False
    job["resume_on_disk"] = True
    try:
        job["resume_path"] = str(disk.relative_to(ROOT))
    except ValueError:
        job["resume_path"] = str(disk)
    return True


def _remember_runtime_job(job: dict) -> None:
    session_key = job.get("session_key")
    if not session_key:
        job_id = job.get("id")
        if not job_id:
            return
        session_key = f"agent:job-hunter:job-{job_id}"
    _runtime_job_snapshots[session_key] = {
        "id": job.get("id") or session_key.rsplit("job-", 1)[-1],
        "company": job.get("company") or "",
        "title": job.get("title") or "",
        "status": job.get("status") or "",
    }


def jobs_list_response(data: dict, *, fill_hold: bool | None = None) -> dict:
    jobs = data.get("jobs") or []
    for job in jobs:
        _remember_runtime_job(job)
    return {
        "jobs": [slim_job_for_list(j) for j in jobs],
        # UI-008: multi-job busy gate needs hold signal without gateway round-trip.
        "fill_hold_active": (
            _fill_hold_browser_active() if fill_hold is None else fill_hold
        ),
    }


def _invalidate_jobs_list_cache() -> None:
    _jobs_list_cache.update(
        {"mtime": None, "body_bytes": None, "etag": None, "fill_hold": None}
    )


def _cached_jobs_list_response() -> tuple[bytes, str]:
    try:
        mtime = JOBS_FILE.stat().st_mtime_ns
    except OSError:
        mtime = -1
    fill_hold = _fill_hold_browser_active()
    cached_body = _jobs_list_cache.get("body_bytes")
    if (
        cached_body is not None
        and _jobs_list_cache.get("mtime") == mtime
        and _jobs_list_cache.get("fill_hold") == fill_hold
    ):
        return cached_body, str(_jobs_list_cache["etag"])

    data = read_jobs()
    try:
        mtime = JOBS_FILE.stat().st_mtime_ns
    except OSError:
        mtime = -1
    revision = int(data.get("revision") or 0)
    body = json.dumps(
        jobs_list_response(data, fill_hold=fill_hold), separators=(",", ":")
    ).encode()
    etag = f'"{mtime:x}-{revision:x}-{1 if fill_hold else 0}"'
    _jobs_list_cache.update(
        {"mtime": mtime, "body_bytes": body, "etag": etag, "fill_hold": fill_hold}
    )
    return body, etag


def write_jobs(data: dict, *, allow_purge: bool = False) -> None:
    """Write jobs.json under EX flock with collapse protection.

    Prefer ``locked_jobs_for_write()`` for read-modify-write so discovery
    adds cannot be wiped by a stale snapshot. This helper remains for
    callers that already hold a fresh locked dict; it still refuses empty
    or dramatic collapses unless ``allow_purge=True`` (empty-deleted).
    """
    JOBS_LOCK_FILE.touch(exist_ok=True)
    with open(JOBS_LOCK_FILE, "r+") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            on_disk_n = 0
            if JOBS_FILE.exists():
                try:
                    on_disk_n = jobs_list_count(
                        _parse_jobs_payload(JOBS_FILE.read_text(encoding="utf-8"))
                    )
                except json.JSONDecodeError as e:
                    raise JobsWriteRefused(
                        f"refusing to write over unreadable jobs.json ({e})"
                    ) from e
            refuse_jobs_collapse(
                on_disk_n, jobs_list_count(data), allow_purge=allow_purge
            )
            backup_jobs_file()
            data["revision"] = int(data.get("revision") or 0) + 1
            JOBS_FILE.write_text(
                json.dumps(data, separators=(",", ":")), encoding="utf-8"
            )
            _invalidate_jobs_list_cache()
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


@contextmanager
def locked_jobs_for_write(*, allow_purge: bool = False):
    """Hold EX flock for an entire read-mutate-write (scripts/jobs_lock).

    Mutate the yielded dict in place; it is written on clean exit. Always
    prefer this over ``read_jobs()`` → mutate → ``write_jobs(snapshot)``.
    """
    # Keep jobs_lock paths synced when tests patch dashboard JOBS_FILE.
    _jobs_lock_mod.JOBS_FILE = JOBS_FILE
    _jobs_lock_mod.LOCK_FILE = JOBS_LOCK_FILE
    with _jl_locked_jobs_for_write(allow_purge=allow_purge) as data:
        yield data
    _invalidate_jobs_list_cache()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Load persisted last-run + incomplete checkpoint once at import
# (after now_iso — hydrate may rewrite checkpoint timestamps).
_load_discovery_last_run()
_hydrate_discovery_resume_banner()


def submit_job_answer(job_id: str, answer: str) -> bool:
    """Shared by the dashboard's own Send-answer button and the desktop
    answer dialog (see _send_answer_dialog) - both need to log the
    question/answer pair, flip status back to resuming, and kick off the
    agent turn with the answer as its new message. Returns False if the
    job doesn't exist."""
    with _lock:
        with locked_jobs_for_write() as data:
            job = next((j for j in data["jobs"] if j["id"] == job_id), None)
            if job is None:
                return False
            job.setdefault("qa_log", []).append(
                {"question": job.get("question"), "answer": answer, "ts": now_iso()}
            )
            job["question"] = None
            job["status"] = "resuming"
            job["updated_at"] = now_iso()
            session_key = job["session_key"]
    threading.Thread(target=run_agent_message, args=(session_key, answer), daemon=True).start()
    return True


def _ensure_job_hunter_ask_off() -> None:
    """Keep job-hunter on ask=off so its exec flow never hangs on an
    unattended approval prompt. Backed by the local approvals store
    (``approvals_store``) — no ``openclaw approvals`` involvement."""
    try:
        approvals_store.ensure_ask_off("job-hunter", path=EXEC_APPROVALS_FILE)
    except Exception as e:
        print(f"warn: could not repair exec-approvals ask field: {e}")


TIMING_LOG = ROOT / "logs" / "timing.log"


def _log_timing(step: str, duration_s: float, detail: str = "") -> None:
    """One line per pipeline step in a single shared file - the point isn't
    any one step's number, it's being able to scan a whole run's timeline
    at a glance and see which step actually ate the time, instead of
    reconstructing it after the fact from scattered per-step logs."""
    ROOT.joinpath("logs").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {step}: {duration_s:.1f}s"
    if detail:
        line += f" ({detail})"
    with open(TIMING_LOG, "a") as f:
        f.write(line + "\n")


def _cleanup_old_inbound_resumes() -> None:
    """~/.openclaw/media/inbound only exists so the browser tool's
    file-upload has a path it's allowed to read from - once a resume's
    been uploaded during its fill turn, that copy has no further purpose
    (the permanent, user-facing copy lives in resumes/by_company/, linked
    from the Excel tracker, and is never touched here). Left alone these
    would just accumulate forever, so sweep anything past a week old
    whenever a new one is about to be added."""
    if not INBOUND_MEDIA_DIR.exists():
        return
    cutoff = time.time() - INBOUND_RESUME_MAX_AGE_S
    for f in INBOUND_MEDIA_DIR.glob("*.pdf"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def _try_extract_manual_job_details(job_id: str, url: str) -> None:
    """Best-effort: try to scrape company/title/location/description
    programmatically right away (see scripts/extract_job_posting.py),
    instead of leaving a manually-added job blank until the agent's own
    turn eventually visits the page at Start time. Runs in its own thread
    so a slow or hanging fetch never blocks the dashboard's response to
    the Add click. If nothing usable comes back (an unreachable platform
    like Workday/iCIMS/LinkedIn, a JS-heavy page, a network hiccup), the
    job is left exactly as _handle_add_job created it - the agent's own
    Start-time fallback (run_tailor_then_fill's manually-added-job
    branch) still applies unchanged."""
    try:
        proc = subprocess.run(
            [PYTHON_BIN, "-u", str(ROOT / "scripts" / "extract_job_posting.py"), url],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return
    if proc.returncode != 0:
        return
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return

    description = (result.get("description") or "").strip()
    if not description:
        return  # nothing worth saving - leave the job for the agent to fetch at Start time

    job_dir = RESUMES_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "jd_full.txt").write_text(description)
    # Matches write_discovered_jobs.py's own trim_description reasoning -
    # jobs.json only ever needs a short preview; the full text lives in
    # jd_full.txt, which run_tailor_then_fill already prefers when present.
    preview = description if len(description) <= 500 else (
        description[:description.rfind(" ", 0, 500)] + " … [full text in resumes/<id>/jd_full.txt]")

    with _lock:
        with locked_jobs_for_write() as data:
            job = next((j for j in data["jobs"] if j["id"] == job_id), None)
            if job is None:
                return
            if result.get("company"):
                job["company"] = result["company"].strip()
                stamp_company_key(job)
            if result.get("title"):
                job["title"] = result["title"].strip()
            if result.get("location"):
                job["location"] = result["location"].strip()
            job["job_description"] = preview
            try:
                from multi_opening import detect_multi_opening

                job["multi_opening"] = detect_multi_opening(
                    title=job.get("title") or "",
                    description=description,
                )
            except Exception as e:
                print(f"warn: multi_opening detect failed for manual job {job_id}: {e}")
            # Prefer company/ATS apply_url from extract; keep pasted aggregator as
            # job_url / source_url. On failure to resolve, leave aggregator apply.
            try:
                from apply_urls import enrich_listing_urls, is_aggregator_url, prefer_apply_url

                enriched = enrich_listing_urls({
                    "job_url": url,
                    "apply_url": result.get("apply_url") or url,
                    "description": description,
                    "alternate_urls": job.get("alternate_urls") or [],
                })
                best = prefer_apply_url(job.get("apply_url"), enriched.get("apply_url"), result.get("apply_url"))
                if best:
                    if is_aggregator_url(url) and not is_aggregator_url(best):
                        job["source_url"] = job.get("source_url") or url
                        job["job_url"] = url
                    job["apply_url"] = best
                if enriched.get("alternate_urls"):
                    job["alternate_urls"] = enriched["alternate_urls"]
            except Exception as e:
                print(f"warn: apply_url enrich failed for manual job {job_id}: {e}")
            job["status_detail"] = "Added manually via dashboard - details fetched automatically."
            job["updated_at"] = now_iso()


_TIMELINE_MAX = 200


def _activity_clock() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _timeline_clock_from_iso(iso_s: str | None) -> str:
    """HH:MM:SS for dossier rail; empty if unparseable (never invent a clock)."""
    if not iso_s:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_s).replace("Z", "+00:00"))
        return dt.astimezone().strftime("%H:%M:%S")
    except Exception:
        s = str(iso_s)
        return s[11:19] if len(s) >= 19 and s[10:11] == "T" else ""


def _timeline_entry(
    *,
    event: str,
    detail: str = "",
    at: str | None = None,
    time_s: str | None = None,
    reconstructed: bool = False,
) -> dict:
    at_iso = at or now_iso()
    entry = {
        "at": at_iso,
        "time": time_s or _timeline_clock_from_iso(at_iso) or _activity_clock(),
        "event": (event or "event")[:48],
        "detail": (detail or "")[:500],
    }
    if reconstructed:
        entry["reconstructed"] = True
    return entry


def _append_timeline_locked(job: dict, entry: dict) -> None:
    """Append a durable timeline event. Caller must hold _lock."""
    tl = job.setdefault("timeline", [])
    if not isinstance(tl, list):
        tl = []
        job["timeline"] = tl
    # Skip exact consecutive duplicates (status+milestone double-fire).
    if tl:
        prev = tl[-1]
        if (
            prev.get("event") == entry.get("event")
            and (prev.get("detail") or "") == (entry.get("detail") or "")
        ):
            return
    tl.append(entry)
    if len(tl) > _TIMELINE_MAX:
        del tl[: len(tl) - _TIMELINE_MAX]


def append_job_timeline(
    job_id: str,
    *,
    event: str,
    detail: str = "",
    at: str | None = None,
    time_s: str | None = None,
) -> None:
    """Persist one lifecycle event onto jobs.json timeline."""
    entry = _timeline_entry(
        event=event, detail=detail, at=at, time_s=time_s
    )
    with _lock:
        with locked_jobs_for_write() as data:
            job = next((j for j in data["jobs"] if j["id"] == job_id), None)
            if job is None:
                return
            _append_timeline_locked(job, entry)


def synthesize_job_timeline(job: dict) -> list[dict]:
    """Honest lifecycle reconstruction when no persisted timeline exists.

    Uses real timestamps from the job record only. Intermediate steps without
    stored times are labeled reconstructed and do not invent clocks.
    """
    events: list[dict] = []
    created = job.get("created_at") or ""
    updated = job.get("updated_at") or ""
    status = (job.get("status") or "").strip()
    detail = (job.get("status_detail") or "").strip()
    source = (job.get("source") or "").strip()
    resume = (job.get("resume_path") or "").strip()

    def add(at: str, event: str, det: str) -> None:
        if not event:
            return
        events.append(
            _timeline_entry(
                event=event,
                detail=det,
                at=at or None,
                time_s=_timeline_clock_from_iso(at) or "—",
                reconstructed=True,
            )
        )

    if created:
        if source == "manual":
            add(created, "added", "Added manually via dashboard.")
        else:
            src_bit = f" via {source}" if source else ""
            add(created, "discovered", f"Discovered{src_bit}.")

    # Resume on file without a dedicated timestamp — do not fake a clock.
    if resume and status in (
        "ready_for_review",
        "applied",
        "filling",
        "navigating",
        "tailoring",
        "stuck",
        "blocked_captcha",
    ):
        events.append(
            {
                "at": "",
                "time": "—",
                "event": "resume",
                "detail": (
                    "Resume on file (reconstructed — exact ready/fill "
                    "start time was not stored)."
                ),
                "reconstructed": True,
            }
        )

    # Terminal / current status at updated_at when it differs from discovered.
    if status and status not in ("discovered",) and updated:
        label = status
        det = detail
        if status == "ready_for_review":
            label = "ready_for_review"
            det = detail or "Ready for review (never submitted)."
        elif status == "applied":
            label = "applied"
            det = detail or "Marked as applied."
        elif not det:
            det = f"Status → {status}"
        if not (
            len(events) == 1
            and events[0].get("event") == status
            and (events[0].get("detail") or "") == det
        ):
            add(updated, label, det)
    elif detail and updated and status == "discovered":
        if events:
            events[0]["detail"] = detail
        else:
            add(updated, "discovered", detail)

    return events


def _job_is_holding_for_review(job: dict | None, *, job_id: str | None = None) -> bool:
    """True when headed hold is active (Ready or hold detail/activity).

    Hold stdout updates status_detail / activity event=hold while status often
    stays ``filling`` until an honest Ready report — the fill deadline must
    suspend in that window too (DASH-001).
    """
    if not isinstance(job, dict):
        return False
    if job.get("status") in FILL_ABORT_STATUSES:
        return False
    if job.get("status") == "ready_for_review":
        return True
    detail = (job.get("status_detail") or "").lower()
    if (
        "browser held open" in detail
        or "held open for review" in detail
        or "hold_review" in detail
        or detail.startswith("keeping browser open")
    ):
        return True
    jid = job_id or job.get("id")
    if jid:
        for ev in reversed(get_fill_activity(str(jid), tail=40) or []):
            if not isinstance(ev, dict):
                continue
            if (ev.get("event") or "") in ("hold", "hold_review"):
                return True
    return False


def _job_is_fill_paused(job: dict | None, *, job_id: str | None = None) -> bool:
    """True when in-page Pause is engaged — must NOT kill the fill CfT.

    Dashboard fill deadline previously only suspended on hold/Ready, so a long
    Pause + manual edit could hit DUMMY_FILL_PLAYWRIGHT_TIMEOUT_S and kill
    Chrome behind the human.
    """
    if not isinstance(job, dict):
        job = {}
    if job.get("status") in FILL_ABORT_STATUSES:
        return False
    detail = (job.get("status_detail") or "").lower()
    if (
        "fill paused" in detail
        or "paused between actions" in detail
        or "fill_pause" in detail
    ):
        return True
    jid = job_id or job.get("id")
    if not jid:
        return False
    for ev in reversed(get_fill_activity(str(jid), tail=40) or []):
        if not isinstance(ev, dict):
            continue
        event = (ev.get("event") or "").lower()
        det = (ev.get("detail") or "").lower()
        if event in ("fill_pause", "pause"):
            # Continue / resume lines clear pause suspension
            if "continu" in det or "resum" in det:
                return False
            return True
        if event == "notice" and "fill paused" in det:
            return True
        if event in ("hold", "hold_review", "run_end", "error"):
            break
    return False


def _job_fill_browser_must_stay_open(
    job: dict | None, *, job_id: str | None = None
) -> bool:
    """Hold/Ready OR Pause — suspend fill kill deadline; never auto-close CfT."""
    return _job_is_holding_for_review(job, job_id=job_id) or _job_is_fill_paused(
        job, job_id=job_id
    )


def _job_fill_hard_aborted(job_id: str) -> bool:
    """True on terminal abort statuses (cancel/delete/applied). Ignores stale fill_gen.

    Subprocess cooperative-abort uses this so a stale-gen race cannot kill
    PartyRock mid-gather after LaTeX is visible but before resume.tex is
    written. Cancel/Delete/Mark-applied still kill via explicit proc teardown
    and/or terminal status; handoff checkpoints use full ``_job_fill_aborted``.
    """
    with _lock:
        data = read_jobs()
        job = next((j for j in data.get("jobs") or [] if j.get("id") == job_id), None)
        if job is None:
            return True
        return job.get("status") in FILL_ABORT_STATUSES


def _job_fill_aborted(job_id: str) -> bool:
    """True if the job was cancelled/skipped/deleted/applied (or missing)."""
    if _fill_run_stale(job_id):
        return True
    return _job_fill_hard_aborted(job_id)


def _fill_abort_reason(job_id: str) -> str | None:
    """Human-readable abort cause for Live Activity / logs (None if still active)."""
    if _fill_run_stale(job_id):
        ctx = _fill_run_ctx.get()
        run_gen = ctx[1] if ctx and ctx[0] == job_id else "?"
        live_gen = _job_fill_gen(job_id)
        return f"fill_gen stale (run={run_gen}, live={live_gen})"
    with _lock:
        data = read_jobs()
        job = next((j for j in data.get("jobs") or [] if j.get("id") == job_id), None)
        if job is None:
            return "job missing from jobs.json"
        st = job.get("status")
        if st in FILL_ABORT_STATUSES:
            return f"status={st}"
    return None


def _pipeline_stop_if_aborted(job_id: str, stage: str) -> bool:
    """Log and return True when the pipeline must stop before the next handoff."""
    reason = _fill_abort_reason(job_id)
    if not reason:
        return False
    detail = (
        f"Pipeline stopped after {stage}: {reason}. "
        "Cancel and Retry if this was unexpected."
    )
    append_fill_activity(job_id, event="abort", detail=detail, persist=True)
    print(f"[fill] {job_id} handoff abort at {stage}: {reason}")
    # Stale gen: a newer Start owns the job. Exit silently — never demote the
    # newer run's in-progress status to stuck.
    return True


def _patch_job(job_id: str, **fields) -> None:
    """Update selected fields on a job and bump updated_at.

    Refuses to overwrite FILL_ABORT_STATUSES via status=… so Cancel / Delete /
    Mark-as-applied / Skip win over a still-running Start/fill daemon.
    Holds EX flock for the full read-mutate-write.
    """
    if _fill_run_stale(job_id):
        return
    close_pr = False
    with _lock:
        with locked_jobs_for_write() as data:
            job = next((j for j in data["jobs"] if j["id"] == job_id), None)
            if job is None:
                return
            old_status = job.get("status")
            if old_status in FILL_ABORT_STATUSES:
                if "status" in fields and fields.get("status") != old_status:
                    # Pipeline / fill-end must never undelete or un-cancel.
                    return
                if "status" not in fields and fields:
                    # Hold/pause stdout must not clobber applied/cancelled detail.
                    return
            job.update(fields)
            if "resume_path" in fields:
                if fields.get("resume_path"):
                    sync_job_resume_on_disk(job)
                else:
                    job["resume_on_disk"] = False
            _remember_runtime_job(job)
            job["updated_at"] = now_iso()
            new_status = job.get("status")
            # Leaving Ready re-arms the spoken announcement, so a genuinely new
            # ready_for_review event announces again (once) on the next run.
            if (
                "status" in fields
                and new_status != "ready_for_review"
                and job.get("ready_announced")
            ):
                job.pop("ready_announced", None)
            if (
                "status" in fields
                and new_status
                and new_status != old_status
            ):
                det = (
                    fields.get("status_detail")
                    if fields.get("status_detail") is not None
                    else job.get("status_detail")
                ) or f"Status → {new_status}"
                _append_timeline_locked(
                    job,
                    _timeline_entry(
                        event=str(new_status),
                        detail=str(det)[:500],
                        at=job["updated_at"],
                    ),
                )
            close_pr = new_status == "stuck"
    if close_pr:
        try:
            close_job_partyrock_tab(job_id, RESUMES_DIR / job_id)
        except Exception as e:
            print(f"warn: PartyRock tab close on stuck for {job_id}: {e}")


def clear_fill_activity(job_id: str) -> None:
    with _fill_activity_lock:
        _fill_activity[job_id] = []


def append_fill_activity(
    job_id: str,
    *,
    event: str,
    detail: str = "",
    time_s: str | None = None,
    persist: bool = False,
) -> None:
    """Append one human-readable fill event for the dashboard Live activity feed.

    When persist=True, also write to jobs.json ``timeline`` so Ready/Applied
    dossiers survive server restarts. Noisy fill-step lines stay memory-only.
    """
    entry = {
        "time": time_s or _activity_clock(),
        "event": (event or "fill")[:48],
        "detail": (detail or "")[:500],
        "at": now_iso(),
    }
    with _fill_activity_lock:
        buf = _fill_activity.setdefault(job_id, [])
        buf.append({k: entry[k] for k in ("time", "event", "detail")})
        if len(buf) > _FILL_ACTIVITY_MAX:
            del buf[: len(buf) - _FILL_ACTIVITY_MAX]
    if persist:
        append_job_timeline(
            job_id,
            event=entry["event"],
            detail=entry["detail"],
            at=entry["at"],
            time_s=entry["time"],
        )


def get_fill_activity(job_id: str, tail: int = 200) -> list[dict]:
    with _fill_activity_lock:
        buf = list(_fill_activity.get(job_id) or [])
    if tail > 0:
        return buf[-tail:]
    return buf


_FILL_STEP_LINE_RE = re.compile(
    r"^\[fill-step\s+(?P<n>\d+)\]\s*(?P<body>.*)$", re.IGNORECASE
)
_FILL_TAG_LINE_RE = re.compile(
    r"^\[(?P<tag>[a-zA-Z][a-zA-Z0-9_-]{0,31})\]\s*(?P<body>.*)$"
)
# Keep readable; skip pure JSON dumps that blow up the feed.
_SKIP_FILL_LINE_PREFIXES = (
    "{",
    "║",
    "╔",
    "╚",
)


def _classify_fill_stdout_line(line: str) -> tuple[str, str] | None:
    """Map a child stdout line → (event, detail) for the activity feed.

    Returns None to skip the line (noise / raw JSON).
    """
    raw = (line or "").rstrip("\n\r")
    s = raw.strip()
    if not s:
        return None
    # Banner / separator / huge JSON noise
    if s.startswith("--- prompt"):
        return None
    if s.startswith(_SKIP_FILL_LINE_PREFIXES) and not s.startswith("[fill-step"):
        return None
    if s.startswith("[") and not s.startswith("[fill-step") and not _FILL_TAG_LINE_RE.match(s):
        # JSON array dump, not a [tag] line
        if len(s) > 120:
            return None
    if len(s) > 400 and (s.startswith("{") or (s.startswith("[") and s[1:2] in '"{[')):
        return None

    m = _FILL_STEP_LINE_RE.match(s)
    if m:
        body = (m.group("body") or "").strip()
        # Prefer action token as event when present: "HH:MM:SS action | …"
        action = "step"
        detail = body
        parts = body.split(None, 2)
        if len(parts) >= 2 and re.match(r"^\d{2}:\d{2}:\d{2}$", parts[0]):
            # drop embedded clock; keep action + rest
            rest = body[len(parts[0]) :].strip()
            ap = rest.split(None, 1)
            if ap:
                action = ap[0][:40]
                detail = rest
        elif parts:
            action = parts[0][:40]
        return action or "fill-step", detail[:500] or f"step {m.group('n')}"

    if s.startswith("***"):
        body = s.strip("* ").strip()
        if "CAPTCHA" in body.upper() or "captcha" in body.lower():
            return "captcha", body[:500]
        if "FILL PAUSED" in body.upper() or "fill paused" in body.lower():
            return "fill_pause", body[:500]
        return "notice", body[:500]

    m2 = _FILL_TAG_LINE_RE.match(s)
    if m2:
        tag = (m2.group("tag") or "fill").lower()
        body = (m2.group("body") or "").strip()
        # Normalize common tags to short event keys the UI already styles
        alias = {
            "chromium": "browser",
            "browser": "browser",
            "captcha": "captcha",
            "hold": "hold",
            "flash": "flash",
            "identity": "identity",
            "cookie": "cookie",
            "entry": "entry",
            "wait": "wait",
            "fill-pause": "fill_pause",
            "fill_pause": "fill_pause",
        }.get(tag, tag)
        return alias, (body or s)[:500]

    # Untagged but useful progress lines
    low = s.lower()
    if "fill paused" in low or low.startswith("[fill-pause]"):
        return "fill_pause", s[:500]
    if "captcha" in low:
        return "captcha", s[:500]
    if "never submit" in low or "never_submit" in low:
        return "safety", s[:500]
    if s.startswith("LIVE FILL") or "fill-step" in low:
        return "fill", s[:500]
    # Skip very long untagged noise (stack traces mid-line etc. still pass if short)
    if len(s) > 280:
        return "log", s[:280] + "…"
    return "log", s[:500]


def _report_allows_ready(rep: dict) -> bool:
    """True only when report claims Ready and honesty preconditions pass.

    Hold alone must never promote — auth_wall / FAIL / incomplete block Ready.
    """
    if not isinstance(rep, dict):
        return False
    if not rep.get("ready_for_review"):
        return False
    try:
        from page_progress import can_claim_ready

        return bool(can_claim_ready(rep))
    except Exception:
        # Fail closed for clear blockers when page_progress import fails.
        if rep.get("verdict") == "FAIL":
            return False
        blocker = str(rep.get("blocker") or "").strip()
        if blocker in (
            "auth_wall",
            "page_incomplete",
            "validation_errors",
            "captcha",
            "akamai",
            "cloudflare",
            "email_verify",
            "self_id_incomplete",
            "multipage_incomplete",
            "ashby_spam_flagged",
        ):
            return False
        return bool(rep.get("ready_for_review"))


def ingest_fill_stdout_line(job_id: str, line: str) -> None:
    classified = _classify_fill_stdout_line(line)
    if not classified:
        return
    event, detail = classified
    append_fill_activity(job_id, event=event, detail=detail)
    # Hold-for-review: note browser held, but do NOT promote to Ready solely
    # because hold started — Ready requires an honest fill report.
    low = (detail or "").lower()
    if event in ("hold", "hold_review") or "hold_review" in low or (
        event == "hold" or low.startswith("keeping browser open")
    ):
        with _lock:
            with locked_jobs_for_write() as data:
                job = next((j for j in data.get("jobs") or [] if j.get("id") == job_id), None)
                if job is None:
                    return
                # UI-027 / DASH2-014: honor full FILL_ABORT_STATUSES (not a subset).
                if job.get("status") in FILL_ABORT_STATUSES:
                    return
                # Already Ready (from honest report) — keep; else only update detail.
                if job.get("status") == "ready_for_review":
                    job["status_detail"] = (
                        "Ready for review — browser held open (never submitted). "
                        "Mark as applied after you submit on the employer site, "
                        "or close the browser when done reviewing."
                    )
                    job["updated_at"] = now_iso()
                    return
                job["status_detail"] = (
                    "Browser held open for review (never submitted) — "
                    "waiting for honest Ready signal from fill report."
                )
                job["updated_at"] = now_iso()
        return
    # Pause engaged: surface in status_detail so kill-deadline suspends.
    if event == "fill_pause" and (
        "fill paused" in low or "paused between" in low or "paus" in low
    ):
        if "continu" in low or "resum" in low:
            return
        with _lock:
            with locked_jobs_for_write() as data:
                job = next((j for j in data.get("jobs") or [] if j.get("id") == job_id), None)
                if job is None or job.get("status") in FILL_ABORT_STATUSES:
                    return
                if job.get("status") == "ready_for_review":
                    return
                job["status_detail"] = (
                    "Fill paused — browser stays open until you Continue fill "
                    "or close the window (never auto-closes while paused)."
                )
                job["updated_at"] = now_iso()


def _classify_pipeline_stdout_line(line: str) -> tuple[str, str] | None:
    """Map tailor/compile/fit stdout → human milestones for Live activity."""
    raw = (line or "").rstrip("\n\r")
    s = raw.strip()
    if not s:
        return None
    # Drop leading [HH:MM:SS] from tailor_resume.py log()
    s_body = re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s*", "", s).strip()
    low = s_body.lower()
    if "partyrock mode=" in low or (low.startswith("opening") and "partyrock" in low):
        return "partyrock", s_body[:500]
    if "submitted jd" in low:
        return "partyrock", "Pasted job description into PartyRock"
    if "waiting for partyrock" in low:
        return "wait", "Waiting on resume from PartyRock…"
    if "wrote tailored resume" in low:
        return "partyrock", "Collected resume from PartyRock"
    # Poll spam — milestones already cover waiting; skip unless final write
    if low.startswith("poll "):
        return None
    if "note:" in low and "page" in low:
        return "pdf", s_body[:500]
    # Fall through to generic fill classifier for [fill-step] / tags
    return _classify_fill_stdout_line(s)


def ingest_pipeline_stdout_line(job_id: str, line: str) -> None:
    classified = _classify_pipeline_stdout_line(line)
    if not classified:
        return
    event, detail = classified
    append_fill_activity(job_id, event=event, detail=detail)


def pipeline_milestone(
    job_id: str,
    *,
    event: str,
    detail: str,
    status: str | None = None,
    status_detail: str | None = None,
) -> None:
    """Emit a human milestone into Live activity (+ durable timeline + optional patch).

    No-ops all job patches when cancelled/skipped/deleted/applied (DASH2-018)
    so a Cancel mid-PartyRock cannot be overwritten by compile/fit milestones
    (status_detail-only patches used to clobber aborted jobs).
    """
    if _job_fill_aborted(job_id):
        append_fill_activity(
            job_id,
            event="abort",
            detail=f"Skipped milestone ({event}): run already aborted.",
            persist=False,
        )
        return
    append_fill_activity(job_id, event=event, detail=detail, persist=True)
    patch: dict = {}
    if status is not None:
        patch["status"] = status
    if status_detail is not None:
        patch["status_detail"] = status_detail
    elif detail:
        patch["status_detail"] = detail
    if patch:
        _patch_job(job_id, **patch)


def get_job_activity(job: dict, tail: int = 200) -> list[dict]:
    """Dossier timeline: live fill while running, else persisted/synthesized lifecycle.

    Does **not** fall back to OpenClaw ``sessions tail`` — that mixed agent
    session.trace events into the job timeline and flickered with the empty
    status_detail synthesis on Applied jobs whenever the CLI returned
    intermittently empty results.
    """
    job_id = job.get("id") or ""
    status = (job.get("status") or "").strip()
    live = get_fill_activity(job_id, tail=tail) if job_id else []
    persisted = list(job.get("timeline") or []) if isinstance(job.get("timeline"), list) else []

    # Live stream while a run is in progress (or browser held at Ready).
    if live and status in IN_PROGRESS_STATUSES | {"ready_for_review"}:
        return live[-tail:] if tail > 0 else live

    if persisted:
        return persisted[-tail:] if tail > 0 else persisted

    synth = synthesize_job_timeline(job)
    if synth:
        return synth[-tail:] if tail > 0 else synth

    # Last resort: in-memory buffer (e.g. mid-run race before status flips).
    if live:
        return live[-tail:] if tail > 0 else live
    return []


def _run_fill_subprocess_streaming(
    cmd: list[str],
    *,
    job_id: str,
    session_key: str,
    log_path: Path,
    env: dict,
    timeout_s: int,
    preserve_activity: bool = False,
) -> tuple[int, bool]:
    """Run fast fill with line-buffered stdout teed to log + live activity.

    Returns (exit_code, timed_out).

    preserve_activity=True keeps prior Start/tailor milestones in the feed
    (default False clears so a standalone Fast fill starts clean).

    Once hold_review / ready_for_review / fill Pause begins, the fill deadline
    is suspended so an indefinite browser hold or human Pause is not killed.
    """
    if not preserve_activity:
        clear_fill_activity(job_id)
    # Short launch blurb — avoid dumping full -c scripts into the feed
    launch_bits = []
    for c in cmd:
        if c in ("-u", "-c") or (len(c) > 80 and "\n" in c):
            continue
        launch_bits.append(Path(c).name if "/" in c else c)
        if len(launch_bits) >= 4:
            break
    append_fill_activity(
        job_id,
        event="fill",
        detail=f"Launching {' '.join(launch_bits) or 'fast_fill'}…",
    )
    timed_out = False
    exit_code = -1
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            bufsize=1,
            start_new_session=True,  # own pgid → shutdown can kill Chrome-for-Testing tree
        )
        _running_procs[session_key] = proc

        def _reader() -> None:
            assert proc.stdout is not None
            try:
                for line in proc.stdout:
                    try:
                        log_file.write(line)
                        log_file.flush()
                    except Exception:
                        pass
                    try:
                        ingest_fill_stdout_line(job_id, line)
                    except Exception:
                        pass
            except Exception as e:
                append_fill_activity(
                    job_id, event="error", detail=f"stdout reader: {e}"[:200]
                )

        reader = threading.Thread(target=_reader, daemon=True, name=f"fill-log-{job_id}")
        reader.start()
        deadline = time.monotonic() + max(1, int(timeout_s))
        try:
            while True:
                if _job_fill_hard_aborted(job_id):
                    append_fill_activity(
                        job_id,
                        event="abort",
                        detail="Fill subprocess stopped (job cancelled/applied/deleted).",
                    )
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        exit_code = proc.wait(timeout=10)
                    except Exception:
                        exit_code = FILL_ABORT_EXIT
                    break
                remaining = deadline - time.monotonic()
                # Suspend kill-deadline once hold, Ready, OR Pause (indefinite review).
                with _lock:
                    data = read_jobs()
                    job = next(
                        (j for j in data.get("jobs") or [] if j.get("id") == job_id),
                        None,
                    )
                staying_open = _job_fill_browser_must_stay_open(job, job_id=job_id)
                if staying_open:
                    remaining = max(remaining, float(DUMMY_FILL_HOLD_GRACE_S))
                    deadline = time.monotonic() + remaining
                try:
                    exit_code = proc.wait(timeout=min(1.0, max(0.1, remaining)))
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline and not staying_open:
                        timed_out = True
                        append_fill_activity(
                            job_id,
                            event="error",
                            detail=f"Timed out after {timeout_s}s — killing (never submitted).",
                        )
                        proc.kill()
                        try:
                            exit_code = proc.wait(timeout=10)
                        except Exception:
                            exit_code = -1
                        break
        finally:
            _running_procs.pop(session_key, None)
            reader.join(timeout=5)
            # Drain any last bytes if reader exited early
            if proc.stdout and not proc.stdout.closed:
                try:
                    proc.stdout.close()
                except Exception:
                    pass

    append_fill_activity(
        job_id,
        event="run_end",
        detail=(
            f"Process exited {exit_code}"
            + (" (timed out)" if timed_out else "")
            + ". Never submitted."
        ),
    )
    return exit_code, timed_out


def _dummy_fill_flash_requested(payload: dict | None = None, query: dict | None = None) -> bool:
    """Flash leftovers for dashboard fills (dummy AND real).

    Default ON for both Test Mode and real-profile Start/Fast fill — leftovers
    (salary/clearance/essays) are what made dummy quality feel complete.
    Disable via JSON ``{"flash_leftovers": false}``, query ``?flash=0``, or env
    ``FASTFILL_FLASH_LEFTOVERS=0``. Never-submit still applies either way.
    """
    payload = payload or {}
    query = query or {}
    if "flash_leftovers" in payload:
        raw = payload.get("flash_leftovers")
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() not in ("0", "false", "no", "off")
    for key in ("flash", "flash_leftovers"):
        vals = query.get(key) or []
        if not vals:
            continue
        raw = str(vals[0]).strip().lower()
        if raw in ("0", "false", "no", "off"):
            return False
        if raw in ("1", "true", "yes", "on"):
            return True
    env = (os.environ.get("FASTFILL_FLASH_LEFTOVERS") or "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return True  # dashboard default ON (dummy + real)


def _dummy_fill_headed_requested(payload: dict | None = None, query: dict | None = None) -> bool:
    """Headed Chromium is optional — default headless for dashboard background runs.

    Enable via JSON body ``{"headed": true}``, query ``?headed=1``, or env
    ``FASTFILL_HEADED=1``.
    """
    payload = payload or {}
    query = query or {}
    if payload.get("headed") in (True, 1, "1", "true", "yes"):
        return True
    for key in ("headed", "headless"):
        vals = query.get(key) or []
        if not vals:
            continue
        raw = str(vals[0]).strip().lower()
        if key == "headed" and raw in ("1", "true", "yes", "on"):
            return True
        if key == "headless" and raw in ("0", "false", "no", "off"):
            return True
    env = (os.environ.get("FASTFILL_HEADED") or "").strip().lower()
    return env in ("1", "true", "yes", "on")


def _dummy_restore_status(status: str | None) -> str:
    """Status to restore after a dummy fill finishes (never leave stuck on filling)."""
    if status in (
        "discovered",
        "stuck",
        "blocked_captcha",
        "cancelled",
        "ready_for_review",
        "resume_ready",
    ):
        return status
    return "discovered"


def resolve_job_resume_file(job: dict | None) -> Path | None:
    """Return an on-disk resume PDF for this job, or None.

    Prefers ``job.resume_path`` only when the file exists, then
    ``resumes/<id>/resume.pdf``, then ``uploaded_resume.pdf``.
    Stale ``resume_path`` strings that point at missing files are ignored.
    """
    if not isinstance(job, dict):
        return None
    candidates: list[Path] = []
    rp = (job.get("resume_path") or "").strip()
    if rp:
        p = Path(rp)
        candidates.append(p if p.is_absolute() else ROOT / p)
    jid = (job.get("id") or "").strip()
    if jid:
        candidates.append(RESUMES_DIR / jid / "resume.pdf")
        candidates.append(RESUMES_DIR / jid / "uploaded_resume.pdf")
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        try:
            if cand.is_file() and cand.suffix.lower() in (".pdf", ".doc", ".docx"):
                return cand
        except OSError:
            continue
    return None


def resolve_job_resume_upload_file(job: dict | None) -> Path | None:
    """Prefer the conventionally named published PDF when filling an ATS."""
    if isinstance(job, dict):
        published = str(job.get("resume_by_company_path") or "").strip()
        if published:
            candidate = Path(published)
            if not candidate.is_absolute():
                candidate = ROOT / candidate
            try:
                if candidate.is_file() and candidate.suffix.lower() == ".pdf":
                    return candidate
            except OSError:
                pass
    return resolve_job_resume_file(job)


def _find_in_progress_job(
    data: dict, *, exclude_id: str | None = None
) -> dict | None:
    """First job in IN_PROGRESS_STATUSES, optionally excluding one id."""
    for job in data.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        jid = job.get("id")
        if exclude_id and jid == exclude_id:
            continue
        if job.get("status") in IN_PROGRESS_STATUSES:
            return job
    return None


# Statuses that block Start/Fast-fill when a headed fill hold is still live.
_HOLD_BLOCK_STATUSES = frozenset({"ready_for_review", "blocked_captcha"})


def _find_blocking_start_job(
    data: dict, *, exclude_id: str | None = None
) -> dict | None:
    """Return a job that must finish before *exclude_id* may Start.

    Concurrent dashboard fills are allowed — each job tracks its own
    session/process. Resource limits (headed Chrome cap) are enforced inside
    fast_fill / tailor, not here. Per-job guards in ``_handle_start`` still
    block double-Start on the same id.
    """
    _ = (data, exclude_id)  # kept for API stability + tests
    return None


def _mark_fill_thread_stuck(job_id: str, exc: BaseException, *, where: str) -> None:
    detail = (
        f"Fill thread crashed ({where}): {type(exc).__name__}: {exc}. "
        "Never submitted."
    )[:500]
    try:
        append_fill_activity(job_id, event="error", detail=detail, persist=True)
    except Exception:
        pass
    # Cancel / Delete / Applied / Skip must win over crash→stuck (DASH-005).
    if _job_fill_aborted(job_id):
        return
    try:
        _patch_job(job_id, status="stuck", status_detail=detail)
    except Exception as e:
        print(f"warn: could not mark stuck after {where} crash: {e}")


def _publish_resume_by_company(
    job: dict,
    pdf_path: Path | str,
    data: dict | None = None,
) -> Path | None:
    """Copy resume into resumes/by_company/ (Command Center Documents/Resumes).

    Best-effort: never fail the pipeline if publish fails. Mutates ``job``
    (file_id, resume_by_company_path). Pass ``data`` when holding a jobs
    lock so file_id allocation sees all existing ids.
    """
    src = Path(pdf_path)
    if not src.is_file() or src.suffix.lower() != ".pdf":
        return None
    try:
        existing = None
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            existing = {j["file_id"] for j in data["jobs"] if j.get("file_id")}
        dest = publish_resume_to_by_company(
            job,
            src,
            existing_file_ids=existing,
            root=ROOT,
        )
        tex_src = src.with_suffix(".tex")
        if not tex_src.is_file():
            jid = str(job.get("id") or "").strip()
            if jid:
                alt = RESUMES_DIR / jid / "resume.tex"
                if alt.is_file():
                    tex_src = alt
        _copy_tex_beside_pdf(dest, tex_path=tex_src)
        return dest
    except Exception as e:
        jid = (job.get("id") or "?")[:80]
        print(f"warn: by_company publish failed for job={jid}: {e}")
        return None


def _ensure_conventional_resume_pdf(job_id: str) -> Path | None:
    """Publish and return the job's actual conventionally named resume PDF."""
    with _lock:
        with locked_jobs_for_write() as data:
            job = next((j for j in data["jobs"] if j.get("id") == job_id), None)
            if job is None:
                return None
            source = resolve_job_resume_file(job)
            if source is None or source.suffix.lower() != ".pdf":
                return None
            published = _publish_resume_by_company(job, source, data)
            if published is None:
                return None
            job["updated_at"] = now_iso()
            return published


def _parse_multipart_file(body: bytes, content_type: str) -> tuple[str, bytes]:
    """Extract the first file part from a multipart/form-data body."""
    m = re.search(r"boundary=([^;\s]+)", content_type or "", re.I)
    if not m:
        raise ValueError("missing multipart boundary")
    boundary = m.group(1).strip().strip('"').encode("ascii", "ignore")
    if not boundary:
        raise ValueError("empty multipart boundary")
    for part in body.split(b"--" + boundary):
        if b"Content-Disposition" not in part:
            continue
        header_blob, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            header_blob, sep, data = part.partition(b"\n\n")
        if not sep or b"filename=" not in header_blob.lower():
            continue
        hm = re.search(br'filename="([^"]*)"', header_blob, re.I)
        if not hm:
            hm = re.search(br"filename=([^\r\n;]+)", header_blob, re.I)
        name = (hm.group(1).decode("utf-8", "replace").strip() if hm else "resume.pdf")
        name = Path(name).name or "resume.pdf"
        # Trim trailing boundary markers / CRLF
        if data.endswith(b"--\r\n"):
            data = data[:-4]
        elif data.endswith(b"--\n"):
            data = data[:-3]
        elif data.endswith(b"--"):
            data = data[:-2]
        data = data.rstrip(b"\r\n")
        return name, data
    raise ValueError("no file part in multipart body")


def _resume_latex_source(job_dir: Path) -> tuple[str, bool]:
    """Return a job's editable LaTeX or a clearly synthetic starter sample."""
    tex_path = job_dir / "resume.tex"
    if tex_path.is_file():
        try:
            source = tex_path.read_text(encoding="utf-8")
            if source.strip():
                return source, False
        except (OSError, UnicodeError):
            pass
    return RESUME_LATEX_SAMPLE, True


def _read_resume_tex_file(path: Path) -> str | None:
    """Return non-empty LaTeX from ``path``, or None."""
    try:
        if not path.is_file():
            return None
        if path.name.startswith(".resume-edit-"):
            return None
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    return source if source.strip() else None


def _rel_resume_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return path.name


def _copy_tex_beside_pdf(pdf_path: Path, *, tex_path: Path | None = None) -> Path | None:
    """Keep a matching .tex next to a saved resume PDF (job dir or by_company)."""
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        return None
    src = Path(tex_path) if tex_path is not None else pdf_path.with_suffix(".tex")
    if not src.is_file():
        return None
    dest = pdf_path.with_suffix(".tex")
    try:
        if dest.resolve() != src.resolve():
            shutil.copyfile(src, dest)
        return dest
    except OSError as e:
        print(f"warn: could not copy resume tex beside {pdf_path.name}: {e}")
        return None


def _job_resume_tex_candidates(job: dict, pdf: Path | None) -> list[Path]:
    """Job-specific compile inputs, never the dummy sample or scratch files."""
    out: list[Path] = []
    job_id = str(job.get("id") or "").strip()
    job_dir = RESUMES_DIR / job_id if job_id else None
    if job_dir is not None:
        out.append(job_dir / "resume.tex")
        out.append(job_dir / "tailored.tex")
    if pdf is not None:
        out.append(pdf.with_suffix(".tex"))
        out.append(pdf.parent / "resume.tex")
    if job_dir is not None:
        try:
            if job_dir.is_dir():
                extras = sorted(
                    p
                    for p in job_dir.glob("*.tex")
                    if not p.name.startswith(".resume-edit-")
                )
                out.extend(extras)
        except OSError:
            pass
    seen: set[str] = set()
    unique: list[Path] = []
    for path in out:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _resume_latex_for_job(job: dict) -> dict:
    """LaTeX that produced this job's saved PDF — never an empty success.

    Prefers ``resumes/<id>/resume.tex`` and other job-folder / PDF-sibling
    ``.tex`` files. Workspace ``resume.tex`` is a labeled last resort when a
    PDF exists. A PDF with no source is an explicit error, not the dummy
    sample.
    """
    pdf = resolve_job_resume_file(job)
    has_pdf = pdf is not None
    for path in _job_resume_tex_candidates(job, pdf):
        source = _read_resume_tex_file(path)
        if source is None:
            continue
        label = _rel_resume_label(path)
        return {
            "ok": True,
            "latex": source,
            "is_sample": False,
            "is_workspace_master": False,
            "missing_tex": False,
            "has_pdf": has_pdf,
            "path": label,
            "source_label": label,
        }
    master = ROOT / "resume.tex"
    if has_pdf:
        master_src = _read_resume_tex_file(master)
        if master_src is not None:
            label = _rel_resume_label(master)
            return {
                "ok": True,
                "latex": master_src,
                "is_sample": False,
                "is_workspace_master": True,
                "missing_tex": False,
                "has_pdf": True,
                "path": label,
                "source_label": label,
            }
        pdf_label = _rel_resume_label(pdf) if pdf is not None else "resume.pdf"
        return {
            "ok": False,
            "latex": "",
            "is_sample": False,
            "is_workspace_master": False,
            "missing_tex": True,
            "has_pdf": True,
            "path": None,
            "source_label": None,
            "error": (
                f"No LaTeX source found for the saved resume PDF ({pdf_label}). "
                "Expected resume.tex in the job folder, or a matching .tex next "
                "to the PDF."
            ),
        }
    job_id = str(job.get("id") or "").strip()
    source, is_sample = _resume_latex_source(RESUMES_DIR / job_id)
    return {
        "ok": True,
        "latex": source,
        "is_sample": is_sample,
        "is_workspace_master": False,
        "missing_tex": False,
        "has_pdf": False,
        "path": None if is_sample else _rel_resume_label(RESUMES_DIR / job_id / "resume.tex"),
        "source_label": None if is_sample else f"resumes/{job_id}/resume.tex",
    }


def _copy_kit_for_job(job: dict, *, test_mode: bool) -> dict:
    """Build Fast-copy kit. File I/O happens here — callers must not hold ``_lock``."""
    job_id = str(job.get("id") or "")
    tex = None
    if job_id:
        pdf = resolve_job_resume_file(job)
        for tex_path in _job_resume_tex_candidates(job, pdf):
            tex = _read_resume_tex_file(tex_path)
            if tex:
                break
    snap = {
        "id": job.get("id"),
        "company": job.get("company"),
        "title": job.get("title"),
        "file_id": job.get("file_id"),
        "applied_address": job.get("applied_address"),
        "location": job.get("location"),
        "resume_by_company_path": job.get("resume_by_company_path"),
    }
    if not str(snap.get("applied_address") or "").strip():
        snap["applied_address"] = resolve_applied_address_for_job(job) or ""
    profile_loader = None
    if not test_mode:
        def profile_loader():
            if PROFILE_FILE.is_file():
                return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
            return {}
    return build_copy_kit(
        snap,
        test_mode=bool(test_mode),
        tex=tex,
        profile_loader=profile_loader,
    )


def _resume_compile_error(label: str, output: str) -> dict:
    lines = (output or "").strip().splitlines()
    snippet = "\n".join(lines[-40:])[-6000:]
    detail = f"{label} failed."
    if snippet:
        detail += f"\n\n{snippet}"
    return {"ok": False, "error": detail}


def _compile_resume_latex(job_dir: Path, latex_source: str) -> dict:
    """Compile + two-page-fit in scratch files, then atomically publish.

    The current resume remains untouched unless both tectonic and the existing
    fit_resume_pages.py pass. No jobs lock is acquired here; callers update the
    job record separately after this potentially slow subprocess work.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    token = f".resume-edit-{uuid.uuid4().hex}"
    temp_tex = job_dir / f"{token}.tex"
    temp_pdf = temp_tex.with_suffix(".pdf")
    temp_tex.write_text(latex_source, encoding="utf-8")
    try:
        try:
            compile_proc = subprocess.run(
                [TECTONIC_BIN, temp_tex.name],
                capture_output=True,
                text=True,
                timeout=90,
                cwd=str(job_dir),
            )
        except subprocess.TimeoutExpired as exc:
            return _resume_compile_error("Tectonic compile timed out", str(exc))
        except OSError as exc:
            return _resume_compile_error("Tectonic could not start", str(exc))
        compile_output = (compile_proc.stdout or "") + (compile_proc.stderr or "")
        if compile_proc.returncode != 0 or not temp_pdf.is_file():
            return _resume_compile_error("Tectonic compile", compile_output)

        fit_env = os.environ.copy()
        fit_env["JOBHUNTER_TECTONIC_BIN"] = TECTONIC_BIN
        try:
            fit_proc = subprocess.run(
                [
                    PYTHON_BIN,
                    "-u",
                    str(ROOT / "scripts" / "fit_resume_pages.py"),
                    str(temp_tex),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(job_dir),
                env=fit_env,
            )
        except subprocess.TimeoutExpired as exc:
            return _resume_compile_error("Two-page fit timed out", str(exc))
        except OSError as exc:
            return _resume_compile_error("Two-page fit could not start", str(exc))
        fit_output = (fit_proc.stdout or "") + (fit_proc.stderr or "")
        # Match the Start/tailor pipeline: fit is best-effort. A nonzero exit
        # usually means "still >2 pages at the tightest layout" after the
        # script already left its best attempt on disk — still publish that.
        if not temp_pdf.is_file():
            return _resume_compile_error("Two-page fit", fit_output)
        warning = None
        if fit_proc.returncode != 0:
            lines = fit_output.strip().splitlines()
            warning = (
                "Compiled and saved, but the two-page fit did not fully succeed. "
                "Best-effort layout was kept."
            )
            if lines:
                warning += "\n\n" + "\n".join(lines[-20:])[-3000:]

        os.replace(temp_tex, job_dir / "resume.tex")
        os.replace(temp_pdf, job_dir / "resume.pdf")
        _copy_tex_beside_pdf(job_dir / "resume.pdf", tex_path=job_dir / "resume.tex")
        out = {
            "ok": True,
            "compile_log": compile_output[-2000:],
            "fit_log": fit_output[-2000:],
        }
        if warning:
            out["warning"] = warning
        return out
    finally:
        for artifact in job_dir.glob(f"{token}*"):
            try:
                artifact.unlink()
            except OSError:
                pass


def _parse_test_mode(payload: dict | None) -> bool:
    """Require explicit ``test_mode`` (UI-019 / DASH2-011 fail-closed).

    Dashboard always sends the flag. Raw API callers without it get
    ValueError → HTTP 400 instead of silently defaulting to dummy.
    """
    payload = payload or {}
    if "test_mode" not in payload:
        raise ValueError(
            "test_mode required (true = dummy identity, false = real profile)"
        )
    raw = payload.get("test_mode")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _parse_skip_partyrock(payload: dict | None) -> bool:
    """True when Start should bypass PartyRock / tailor_resume.

    Accepts ``skip_partyrock: true`` or ``partyrock: false``. Default False
    (PartyRock on). Only meaningful with Test Mode — real Start still needs
    a tailored resume.
    """
    payload = payload or {}
    if "skip_partyrock" in payload:
        raw = payload.get("skip_partyrock")
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        return str(raw).strip().lower() not in ("0", "false", "no", "off", "")
    if "partyrock" in payload:
        raw = payload.get("partyrock")
        if isinstance(raw, bool):
            return not raw
        if isinstance(raw, (int, float)):
            return not bool(raw)
        return str(raw).strip().lower() in ("0", "false", "no", "off")
    return False


def _parse_resume_only(payload: dict | None) -> bool:
    """True when Start should tailor/compile then stop (no form fill).

    Accepts ``resume_only: true`` or ``skip_fill: true``. Default False.
    ``resume_only`` takes precedence when both keys are present.
    """
    payload = payload or {}
    key = "resume_only" if "resume_only" in payload else (
        "skip_fill" if "skip_fill" in payload else None
    )
    if key is None:
        return False
    raw = payload.get(key)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "")


def _fill_mode_prefix(test_mode: bool) -> str:
    return "[DUMMY/TEST]" if test_mode else "[REAL]"


def _format_address_pick(pick: dict) -> str | None:
    """Format a complete address chosen for a fill; never synthesize gaps."""
    line1 = (pick.get("line1") or "").strip()
    city = (pick.get("city") or "").strip()
    state = (pick.get("state") or "").strip()
    zip_code = (pick.get("zip") or "").strip()
    if not all((line1, city, state, zip_code)):
        return None
    return f"{line1}, {city}, {state} {zip_code}"


def _find_resume_for_address(job: dict) -> Path | None:
    """Locate resume.tex or resume.pdf for address resolution (prefer .tex)."""
    dirs: list[Path] = []
    resume_path = job.get("resume_path")
    if resume_path:
        rp = Path(str(resume_path))
        dirs.append(ROOT / rp.parent if not rp.is_absolute() else rp.parent)
    job_id = job.get("id")
    if job_id:
        dirs.append(RESUMES_DIR / str(job_id))
    seen: set[Path] = set()
    for directory in dirs:
        if directory in seen:
            continue
        seen.add(directory)
        tex = directory / "resume.tex"
        pdf = directory / "resume.pdf"
        if tex.is_file():
            return tex
        if pdf.is_file():
            return pdf
    return None


def resolve_applied_address_for_job(job: dict) -> str | None:
    """Resolve synthetic mailing address from resume city → fixture bank.

    Same path as scripts/backfill_applied_addresses.py / pick_address.py.
    Never reads profile.json. Returns a formatted line or None.
    Deterministic apartment pick seeded by job id (or sorted-first).
    """
    existing = str(job.get("applied_address") or "").strip()
    if existing:
        return existing
    loc = str(job.get("location") or "").strip()
    if loc:
        try:
            from discovery_filters import is_clearly_non_us_location
        except Exception:
            is_clearly_non_us_location = None
        if is_clearly_non_us_location and is_clearly_non_us_location(loc):
            return None
    resume = _find_resume_for_address(job)
    if not resume:
        return None
    try:
        from address_resolver import resolve_address_for_resume
        from field_map import format_address_line
        from pick_address import address_rng_for_job

        pick = resolve_address_for_resume(
            resume,
            fallback_location=str(job.get("location") or ""),
            rng=address_rng_for_job(str(job.get("id") or "") or None),
        )
        return format_address_line(pick) or None
    except Exception as e:
        print(f"warn: applied_address resolve failed for {job.get('id')}: {e}")
        return None


def _ensure_fill_address(job_id: str, *, job_location: str = "") -> str | None:
    """Resolve once, persist applied_address, return formatted line for fill."""
    with _lock:
        data = read_jobs()
        job = next((j for j in data.get("jobs") or [] if j.get("id") == job_id), None)
        if job is None:
            return None
        existing = str(job.get("applied_address") or "").strip()
        if existing:
            return existing
        if job_location and not (job.get("location") or "").strip():
            job = dict(job)
            job["location"] = job_location
    resolved = resolve_applied_address_for_job(job)
    if resolved:
        _patch_job(job_id, applied_address=resolved)
        append_fill_activity(
            job_id,
            event="address",
            detail=f"Mailing address for fill: {resolved}",
        )
    return resolved


def _validated_applied_edit(payload: dict) -> dict:
    """Return only user-editable Applied fields, normalized for jobs.json."""
    limits = {
        "title": 500,
        "company": 500,
        "location": 500,
        "applied_address": 1000,
        "status_detail": 2000,
        "apply_url": 4000,
        "source": 200,
    }
    fields = {}
    for key, limit in limits.items():
        if key not in payload:
            continue
        value = str(payload.get(key) or "").strip()
        if len(value) > limit:
            raise ValueError(f"{key} is too long")
        if key == "apply_url" and value:
            parsed = urlparse(value)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("apply_url must be an http(s) URL")
        fields[key] = value
    if "applied_date" in payload:
        applied_date = str(payload.get("applied_date") or "").strip()
        if applied_date:
            try:
                datetime.strptime(applied_date, "%Y-%m-%d")
            except ValueError as e:
                raise ValueError("applied_date must be a valid YYYY-MM-DD date") from e
        fields["applied_at"] = applied_date
    return fields


def _configure_fastfill_child_env(
    env: dict,
    *,
    test_mode: bool,
    address_text: str | None = None,
) -> None:
    """Set child env for dashboard fast fill. test_mode=True → dummy-only.

    Real Start may pass ``address_text`` from the PartyRock pick_address step so
    prepare_real_run uses the same mailing address (no second random pick).
    """
    env.pop("FASTFILL_ALLOW_REAL", None)
    env.pop("FASTFILL_REAL_PROFILE", None)
    env.pop("FASTFILL_ADDRESS_TEXT", None)
    if test_mode:
        env["FASTFILL_REAL_PROFILE"] = "0"
        env["TEST_MODE"] = "1"
        if env.get("FASTFILL_REAL_PROFILE") != "0":
            raise RuntimeError("fast fill refuse: FASTFILL_REAL_PROFILE must be 0 in test mode")
        if env.get("TEST_MODE") != "1":
            raise RuntimeError("fast fill refuse: TEST_MODE must be 1 in test mode")
    else:
        env["FASTFILL_ALLOW_REAL"] = "1"
        env["FASTFILL_REAL_PROFILE"] = "1"
        env["TEST_MODE"] = "0"
        if not (
            env.get("FASTFILL_ALLOW_REAL") == "1"
            and env.get("FASTFILL_REAL_PROFILE") == "1"
            and env.get("TEST_MODE") == "0"
        ):
            raise RuntimeError("fast fill refuse: real-profile env incomplete")
        addr = (address_text or "").strip()
        if addr:
            env["FASTFILL_ADDRESS_TEXT"] = addr
    env.setdefault("FASTFILL_ACTION_SUPERVISOR", "1")
    env.setdefault("FASTFILL_STRICT_COMPLETION", "1")
    # Parallel headed fills: default cap 3 (one job on hold + others filling).
    env.setdefault("FASTFILL_MAX_HEADED_CHROME_MAINS", "3")
    if sys.platform == "darwin":
        env.setdefault("FASTFILL_NATIVE_HUD", "1")
        env.pop("FASTFILL_DOM_OVERLAY", None)


def _playwright_fastfill_argv(
    *,
    py: str,
    script: Path | str,
    apply_url: str,
    out_path: Path | str,
    test_mode: bool,
    job_id: str,
    headed: bool,
    flash_leftovers: bool,
    resume_path: str | Path | None = None,
) -> list[str]:
    """Build Playwright fast_fill argv for dashboard Start / Fast fill.

    Dummy vs real differ only on identity flags (``--test-mode`` vs
    ``--real-profile --job-id``). Engine flags (headed/captcha/hold, flash,
    refill) must stay identical so leftover quality does not regress in real.
    When ``resume_path`` is set, attach that PDF (job upload / tailored).

    FILL3-004 honesty matrix (dashboard headed + flash):
      --flash-leftovers + --hold-open + --refill-passes (Ashby 1, Workday 2)
      → inpage leftovers + same-session refill; Skyvern is deferred
        (``flash.skyvern_deferred``). ``flash.invoked`` means LLM ran, not
        that Skyvern ran. Do not treat invoked=false as Flash failure
        (FILL3-001). CLI raw default is Flash OFF / refill 0 (Skyvern
        eligible only when Flash ON without hold/refill).
    """
    cmd = [py, "-u", str(script), apply_url, "--out", str(out_path)]
    if test_mode:
        cmd.append("--test-mode")
    else:
        cmd.extend(["--real-profile", "--job-id", job_id])
    if resume_path:
        cmd.extend(["--resume-path", str(resume_path)])
    elif not test_mode:
        # Real mode without explicit path still uses --job-id resolution.
        pass
    if headed:
        # Visible browser + captcha pause + indefinite hold for review.
        # FILL3-004: pairing hold+refill with Flash is intentional → inpage-only.
        cmd.extend(["--headed", "--captcha-wait", "--hold-open"])
    else:
        cmd.append("--headless")
    if flash_leftovers:
        cmd.append("--flash-leftovers")
        try:
            sys.path.insert(0, str(ROOT / "scripts" / "fastfill"))
            from stealth import default_refill_passes_for_url

            refill_n = default_refill_passes_for_url(apply_url)
        except Exception:
            refill_n = 2
        cmd.extend(["--refill-passes", str(refill_n)])
    return cmd


def run_hybrid_fill_dummy(
    job_id: str,
    *,
    test_mode: bool = True,
    headed: bool = False,
    flash_leftovers: bool | None = None,
    restore_status: str | None = None,
    preserve_activity: bool = False,
    address_text: str | None = None,
    fill_run_gen: int | None = None,
) -> None:
    """Dashboard fast fill — dummy (default) or real profile when test_mode=False.

    Prefers scripts/fastfill/fast_fill.py (Playwright) when present;
    falls back to skyvern_runtime/scripts/hybrid_fill.py only if the Playwright
    script is missing. Never submits.
    Does not touch the real Start / run_tailor_then_fill agent path.

    ``flash_leftovers``: default True for both dummy and real (DeepSeek leftovers
    for salary/clearance/essays — same quality path as Test Mode). Pass False
    or FASTFILL_FLASH_LEFTOVERS=0 to disable.

    ``restore_status`` must be captured by the API handler *before* it claims
    status=filling — otherwise the thread would only see filling and wrongly
    fall back to discovered (dropping stuck / blocked_captcha / cancelled).
    """
    run_gen = fill_run_gen if fill_run_gen is not None else _job_fill_gen(job_id)
    ctx_token = _fill_run_ctx.set((job_id, run_gen))
    try:
        _run_hybrid_fill_dummy_body(
            job_id,
            test_mode=test_mode,
            headed=headed,
            flash_leftovers=flash_leftovers,
            restore_status=restore_status,
            preserve_activity=preserve_activity,
            address_text=address_text,
            fill_run_gen=run_gen,
        )
    except Exception as e:
        _mark_fill_thread_stuck(job_id, e, where="run_hybrid_fill_dummy")
    finally:
        _fill_run_ctx.reset(ctx_token)


def _run_hybrid_fill_dummy_body(
    job_id: str,
    *,
    test_mode: bool = True,
    headed: bool = False,
    flash_leftovers: bool | None = None,
    restore_status: str | None = None,
    preserve_activity: bool = False,
    address_text: str | None = None,
    fill_run_gen: int | None = None,
) -> None:
    if flash_leftovers is None:
        flash_leftovers = True
    conventional_resume = (
        None if test_mode else _ensure_conventional_resume_pdf(job_id)
    )
    with _lock:
        data = read_jobs()
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job is None:
            return
        session_key = job["session_key"]
        apply_url = (job.get("apply_url") or job.get("job_url") or "").strip()
        fill_job_location = (job.get("location") or "").strip()
        fill_job_title = (job.get("title") or "").strip()
        prev_status = job.get("status") or "discovered"
        resume_file = (
            resolve_job_resume_upload_file(job) if test_mode else conventional_resume
        )

    if restore_status is None:
        restore_status = _dummy_restore_status(prev_status)
    else:
        restore_status = _dummy_restore_status(restore_status)

    prefix = _fill_mode_prefix(test_mode)

    if _job_fill_aborted(job_id):
        return

    if not apply_url:
        _patch_job(
            job_id,
            status=restore_status,
            status_detail=f"{prefix} Fast fill aborted: no apply_url on this job.",
        )
        return

    if not test_mode:
        if resume_file is None or not Path(resume_file).is_file():
            _patch_job(
                job_id,
                status=restore_status,
                status_detail=(
                    f"{prefix} Fast fill aborted: no conventionally named resume "
                    "PDF could be published. Upload or tailor a PDF first."
                ),
            )
            return

    use_playwright = FASTFILL_SCRIPT.is_file()
    if use_playwright:
        mode = "headed" if headed else "headless"
        engine = f"Playwright fast_fill ({mode})"
        timeout_s = DUMMY_FILL_PLAYWRIGHT_TIMEOUT_S
    elif HYBRID_FILL_SCRIPT.is_file() and SKYVERN_PYTHON.is_file():
        engine = "hybrid_fill (Skyvern + DUMMY_PROFILE cheat sheet)"
        timeout_s = DUMMY_FILL_HYBRID_TIMEOUT_S
    else:
        _patch_job(
            job_id,
            status=restore_status,
            status_detail=(
                f"{prefix} Fast fill aborted: neither Playwright fast_fill.py "
                "nor hybrid_fill.py is available."
            ),
        )
        return

    py = str(SKYVERN_PYTHON if SKYVERN_PYTHON.is_file() else PYTHON_BIN)
    results_dir = ROOT / "skyvern_runtime" / "real_job_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / f"{'dummy' if test_mode else 'real'}-fill-{job_id}.json"
    ROOT.joinpath("logs").mkdir(exist_ok=True)
    log_path = ROOT / "logs" / f"{'dummy' if test_mode else 'real'}_fill_{job_id}.log"

    if use_playwright:
        # Flash leftovers ON by default for dummy AND real (salary/clearance/
        # essays). Never-submit still applies. Disable via flash_leftovers=False.
        # Test Mode: never attach job-scoped/tailored PDF (dummy fixture only).
        resume_arg = (
            None
            if test_mode
            else (str(resume_file) if resume_file is not None else None)
        )
        cmd = _playwright_fastfill_argv(
            py=py,
            script=FASTFILL_SCRIPT,
            apply_url=apply_url,
            out_path=out_path,
            test_mode=test_mode,
            job_id=job_id,
            headed=headed,
            flash_leftovers=flash_leftovers,
            resume_path=resume_arg,
        )
    else:
        cmd = [py, "-u", str(HYBRID_FILL_SCRIPT), apply_url, job_id if not test_mode else f"dummy-{job_id}"]
    flash_note = " Flash leftovers ON." if (use_playwright and flash_leftovers) else ""
    mode_label = "dummy resume + DUMMY_PROFILE" if test_mode else "real profile.json + resume PDF"
    if (not test_mode) and resume_file is not None:
        mode_label += f" (attach {resume_file.name})"
    _patch_job(
        job_id,
        status="filling",
        status_detail=(
            f"{prefix} Fast fill starting via {engine}. "
            f"Uses {mode_label}.{flash_note} NEVER submits."
        ),
    )

    env = os.environ.copy()
    _configure_fastfill_child_env(
        env, test_mode=test_mode, address_text=None if test_mode else address_text
    )
    env.pop("FASTFILL_JOB_LOCATION", None)
    env.pop("FASTFILL_JOB_TITLE", None)
    if fill_job_location:
        env["FASTFILL_JOB_LOCATION"] = fill_job_location
    if fill_job_title:
        env["FASTFILL_JOB_TITLE"] = fill_job_title

    cmd_joined = " ".join(cmd)
    if test_mode and ("credentials.json" in cmd_joined or "profile.json" in cmd_joined):
        raise RuntimeError("fast fill refuse: test-mode cmd must not reference profile/credentials")
    if "tailor_resume" in cmd_joined:
        raise RuntimeError("fast fill refuse: must not invoke tailor_resume")
    if _job_fill_aborted(job_id):
        return
    exit_code, timed_out = _run_fill_subprocess_streaming(
        cmd,
        job_id=job_id,
        session_key=session_key,
        log_path=log_path,
        env=env,
        timeout_s=timeout_s,
        preserve_activity=preserve_activity,
    )

    # Don't clobber Cancel / Skip / Delete / Applied issued while we were running.
    with _lock:
        data = read_jobs()
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job is None:
            return
        if job.get("status") in FILL_ABORT_STATUSES:
            return

    if timed_out:
        detail = (
            f"{prefix} Fast fill timed out after {timeout_s}s "
            f"via {engine}. Never submitted. Log: {log_path.name}"
        )
        append_fill_activity(job_id, event="error", detail=detail, persist=True)
        _patch_job(job_id, status=restore_status, status_detail=detail)
        return

    detail = _dummy_fill_result_detail(
        engine=engine,
        exit_code=exit_code,
        out_path=out_path,
        use_playwright=use_playwright,
        job_id=job_id,
        log_path=log_path,
        test_mode=test_mode,
    )
    # Safety contract failures must surface as stuck — never silently restore
    # to discovered as if the fill completed cleanly.
    headed_cap_blocked = False
    report_ready = False
    if use_playwright and out_path.is_file():
        try:
            rep = json.loads(out_path.read_text())
            headed_cap_blocked = str((rep or {}).get("blocker") or "") == "headed_cap"
            report_ready = _report_allows_ready(rep)
        except Exception:
            rep = None
    else:
        rep = None
    if detail.startswith(f"{prefix} SAFETY:") or headed_cap_blocked:
        final_status = "stuck"
        if headed_cap_blocked:
            cap_msg = ""
            try:
                cap_msg = str(
                    ((rep or {}).get("headed_cap") or {}).get("message") or ""
                )[:300]
            except Exception:
                cap_msg = ""
            detail = (
                f"{prefix} Headed fill refused (Chrome cap — another fill/hold "
                f"window is using the slot). {cap_msg} Wait for the other job, "
                f"or set FASTFILL_FORCE_HEADED=1. Never submitted."
            )[:500]
    else:
        # Prefer Ready only when report honestly allows it (not hold alone).
        if not report_ready and use_playwright and out_path.is_file() and rep is None:
            try:
                rep = json.loads(out_path.read_text())
                report_ready = _report_allows_ready(rep)
            except Exception:
                pass
        with _lock:
            data = read_jobs()
            cur = next((j for j in data["jobs"] if j["id"] == job_id), None)
            cur_status = (cur or {}).get("status")
        if cur_status == "ready_for_review" or report_ready:
            final_status = "ready_for_review"
        else:
            final_status = restore_status
    append_fill_activity(
        job_id,
        event="done" if exit_code == 0 and final_status != "stuck" else "error",
        detail=detail,
        persist=True,
    )
    _patch_job(job_id, status=final_status, status_detail=detail)


def _dummy_fill_result_detail(
    *,
    engine: str,
    exit_code: int,
    out_path: Path,
    use_playwright: bool,
    job_id: str,
    log_path: Path,
    test_mode: bool = True,
) -> str:
    """Build a status_detail line from Playwright/hybrid artifacts."""
    prefix = _fill_mode_prefix(test_mode)
    report = None
    if use_playwright and out_path.is_file():
        try:
            report = json.loads(out_path.read_text())
        except Exception:
            report = None
    elif not use_playwright:
        hybrid_candidates = [
            ROOT / "skyvern_runtime" / "real_job_results" / f"hybrid-{job_id}.json",
            ROOT / "skyvern_runtime" / "real_job_results" / f"hybrid-dummy-{job_id}.json",
        ]
        for hybrid_out in hybrid_candidates:
            if hybrid_out.is_file():
                try:
                    report = json.loads(hybrid_out.read_text())
                    break
                except Exception:
                    report = None

    # Both engines must declare never_submit; hybrid fallback is the same
    # dummy contract as Playwright (prepare_dummy_run + never FINAL).
    if report is not None and report.get("never_submit") is not True:
        return (
            f"{prefix} SAFETY: Fast fill report via {engine} missing "
            f"never_submit=True (got {report.get('never_submit')!r}). "
            f"Treat as failed. Log: {log_path.name}"
        )

    if use_playwright and report:
        unresolved = report.get("leftover_count", report.get("unresolved_count", "?"))
        mode_note = "Dummy" if report.get("dummy") else "Real"
        spam_note = ""
        if str(report.get("blocker") or "") == "ashby_spam_flagged" or report.get(
            "ashby_spam_flagged"
        ):
            spam_note = (
                " Ashby spam flag: close fill browser → Start again (fresh session), "
                "or submit from Chrome incognito with the apply URL."
            )
        elif report.get("ashby_spam_guidance"):
            spam_note = f" {report.get('ashby_spam_guidance')}"
        return (
            f"{prefix} Fast fill done via {engine}: "
            f"{report.get('filled_count', '?')} fields filled, "
            f"{unresolved} leftovers, "
            f"{report.get('elapsed_seconds', '?')}s, "
            f"coverage={report.get('coverage', '?')}. "
            f"Never submitted. {mode_note} email={report.get('identity_email', '?')} "
            f"(test_mode={report.get('test_mode', test_mode)}).{spam_note}"
        )
    if report and not use_playwright:
        status = report.get("status") or ("ok" if exit_code == 0 else "error")
        elapsed = report.get("elapsed_seconds")
        elapsed_s = f"{elapsed:.1f}s" if isinstance(elapsed, (int, float)) else "?"
        extra = ""
        if report.get("captcha_blocked"):
            extra = " CAPTCHA blocked."
        if report.get("submit_alarm"):
            extra += " SUBMIT_ALARM (run cancelled; never_submit held)."
        if report.get("error"):
            extra += f" error={report.get('error')!s}"[:120]
        email = report.get("identity_email") or report.get("email") or "?"
        return (
            f"{prefix} Fast fill finished via {engine}: status={status}, "
            f"elapsed={elapsed_s}.{extra} Never submitted. "
            f"email={email} (test_mode={report.get('test_mode', test_mode)})."
        )
    if exit_code == 0:
        return (
            f"{prefix} Fast fill exited 0 via {engine} (no JSON report). "
            f"Never submitted. Log: {log_path.name}"
        )
    return (
        f"{prefix} Fast fill failed via {engine} (exit={exit_code}). "
        f"Never submitted. See logs/{log_path.name}."
    )


def run_tailor_then_fill(
    job_id: str,
    test_mode: bool = True,
    skip_partyrock: bool = False,
    force_partyrock: bool = False,
    restore_status: str | None = None,
    fill_options: dict | None = None,
    fill_run_gen: int | None = None,
    resume_only: bool = False,
) -> None:
    """Resume tailoring is "paste text, wait for a web app, copy the
    result" - no judgment calls, so it runs as a plain script
    (scripts/tailor_resume.py, driving PartyRock directly over the
    browser's own CDP port - verified to share the same authenticated
    session, no separate login needed) instead of the agent
    snapshot-polling its way through a multi-minute wait. This also means
    the fill turn starts fresh with a small context instead of inheriting
    a giant pile of tailoring snapshots, mirroring the discovery split.

    Falls back to having the agent tailor manually via its own browser
    tool if the script fails for any reason (PartyRock UI change, a
    transient hiccup) - a single automation hiccup shouldn't strand the
    job, and the agent still knows the manual steps (see PLAYBOOK.md).

    test_mode=True (dashboard Test Mode ON) → PartyRock Testing app URL,
    then Playwright fast_fill with dummy (same as Fast fill button) —
    never the agent fill path (avoids openclaw/node PATH failures after
    PartyRock and keeps Test Mode never-submit + dummy-only).
    test_mode=True + skip_partyrock=True → no PartyRock / tailor_resume;
    go straight to headed fast_fill with dummy resume + DUMMY_PROFILE.
    test_mode=False → PartyRock Real app URL, then Playwright fast_fill
    with real profile + tailored resume (prepare_real_run). Never agent
    browser fill — that path hung on Ashby/Bumble analyzing the form and
    left status=navigating after SIGTERM. Subprocess timeout fails honestly.
    See partyrock.json.

    ``restore_status`` must be captured by the Start handler *before* it
    claims navigating/tailoring — otherwise Test Mode fill would always
    restore to discovered and drop stuck / blocked_captcha.
    """
    run_gen = fill_run_gen if fill_run_gen is not None else _job_fill_gen(job_id)
    # Bind fill_gen before claim-failure checks so stale runs never demote a
    # newer Start (``_job_fill_aborted`` / ``_patch_job`` need the ctx).
    claimed = False
    ctx_token = _bind_fill_run_ctx(job_id, run_gen)
    try:
        if not _claim_fill_job_for_run(job_id, run_gen):
            if _job_fill_aborted(job_id):
                print(
                    f"warn: tailor/fill claim skipped for {job_id} — "
                    "stale gen or terminal abort (not demoting)"
                )
                return
            detail = (
                "Fill pipeline could not start: another tailor/fill thread is already "
                "registered for this job. Cancel and Retry if nothing is running."
            )
            print(
                f"warn: tailor/fill already active for {job_id} — "
                "skipping duplicate thread"
            )
            append_fill_activity(job_id, event="error", detail=detail, persist=True)
            _patch_job(
                job_id,
                status="stuck",
                status_detail=detail,
                question=detail,
            )
            return
        claimed = True
        try:
            _run_tailor_then_fill_body(
                job_id,
                test_mode=test_mode,
                skip_partyrock=skip_partyrock,
                force_partyrock=force_partyrock,
                restore_status=restore_status,
                fill_options=fill_options,
                fill_run_gen=run_gen,
                resume_only=resume_only,
            )
        except Exception as e:
            _mark_fill_thread_stuck(job_id, e, where="run_tailor_then_fill")
    finally:
        _fill_run_ctx.reset(ctx_token)
        # Only the thread that owns the claim may release it — a losing Start
        # must not clear the active pipeline's slot.
        if claimed:
            _release_fill_job(job_id)


def _complete_resume_only(
    job_id: str,
    *,
    test_mode: bool,
    resume_label: str | None = None,
) -> None:
    """Park generate-only in IN PROGRESS after compile/publish. Never starts fill."""
    prefix = _fill_mode_prefix(test_mode)
    name_bit = f" ({resume_label})" if resume_label else ""
    pipeline_milestone(
        job_id,
        event="resume",
        detail=f"Resume generated{name_bit} — fill skipped.",
        status="resume_ready",
        status_detail=(
            f"{prefix} Resume ready{name_bit}. Fill when you want."
        ),
    )


def _fill_skipping_partyrock(
    job_id: str,
    *,
    test_mode: bool,
    existing_resume: Path | None,
    apply_url0: str,
    fill_restore: str,
    fill_options: dict | None = None,
    fill_run_gen: int | None = None,
    resume_only: bool = False,
) -> None:
    """PartyRock-bypass fast path shared by "resume already on disk" and the
    Test-Mode "skip PartyRock" toggle: publish any on-disk resume, then hand
    straight to ``run_hybrid_fill_dummy``.

    Extracted verbatim from ``_run_tailor_then_fill_body``; every path here
    used to ``return`` from that function, so the caller simply ``return``s
    after invoking this. Behavior is characterized in test_server_refactor.py.
    """
    clear_fill_activity(job_id)
    display_resume_name = None
    if existing_resume is not None:
        display_resume_name = existing_resume.name
        try:
            rel = str(existing_resume.relative_to(ROOT))
        except ValueError:
            rel = str(existing_resume)
        with _lock:
            with locked_jobs_for_write() as data:
                job = next((j for j in data["jobs"] if j["id"] == job_id), None)
                if job is not None:
                    job["resume_path"] = rel
                    sync_job_resume_on_disk(job)
                    job["updated_at"] = now_iso()
                    published = _publish_resume_by_company(job, existing_resume, data)
                    if published is not None:
                        display_resume_name = published.name
    if resume_only:
        _complete_resume_only(
            job_id, test_mode=test_mode, resume_label=display_resume_name
        )
        return
    if not apply_url0:
        pipeline_milestone(
            job_id,
            event="error",
            detail="Start aborted: no apply_url — cannot start fill.",
            status="stuck",
            status_detail=(
                f"{_fill_mode_prefix(test_mode)} No apply_url/job_url; fill skipped."
            ),
        )
        return
    if existing_resume is not None:
        if test_mode:
            detail = (
                f"Test Mode: using dummy fixture resume ({display_resume_name}) — "
                "PartyRock skipped."
            )
        else:
            detail = (
                f"Using on-disk resume ({display_resume_name}) — "
                "PartyRock / tailor skipped."
            )
        pipeline_milestone(
            job_id,
            event="resume",
            detail=detail,
            status="navigating",
            status_detail=detail,
        )
    else:
        pipeline_milestone(
            job_id,
            event="start",
            detail=(
                "Start (Test Mode): PartyRock bypassed — "
                "dummy resume + DUMMY_PROFILE fast_fill only."
            ),
            status="navigating",
            status_detail=(
                "[DUMMY/TEST] PartyRock off — skipping tailor; "
                f"opening apply URL via fast_fill (dummy, headed). {apply_url0[:160]}"
            ),
        )
    append_fill_activity(
        job_id,
        event="fill",
        detail=(
            f"{'On-disk/dummy resume; ' if existing_resume else 'PartyRock skipped. '}"
            f"Opening apply URL for fast_fill: {apply_url0[:120]}"
        ),
    )
    address_text = None
    if not test_mode:
        address_text = _ensure_fill_address(job_id)
    if _pipeline_stop_if_aborted(job_id, "fast_fill launch"):
        return
    run_hybrid_fill_dummy(
        job_id,
        test_mode=test_mode,
        headed=True,
        flash_leftovers=_dummy_fill_flash_requested(fill_options),
        restore_status=fill_restore,
        preserve_activity=True,
        address_text=address_text,
        fill_run_gen=fill_run_gen,
    )


def _run_tailor_then_fill_body(
    job_id: str,
    test_mode: bool = True,
    skip_partyrock: bool = False,
    force_partyrock: bool = False,
    restore_status: str | None = None,
    fill_options: dict | None = None,
    fill_run_gen: int | None = None,
    resume_only: bool = False,
) -> None:
    fill_restore = _dummy_restore_status(restore_status or "discovered")
    resume_only = bool(resume_only) or _parse_resume_only(fill_options)
    # Skip PartyRock when a resume is already on disk (upload or prior tailor),
    # or when Test Mode explicitly bypasses PartyRock — unless the dashboard
    # requested force_partyrock (Tailor + fill / regenerate / generate-only).
    with _lock:
        data = read_jobs()
        job0 = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job0 is None:
            return
        existing_resume = resolve_job_resume_file(job0)
        apply_url0 = (job0.get("apply_url") or job0.get("job_url") or "").strip()

    skip_for_resume = existing_resume is not None and not force_partyrock
    if skip_for_resume or (test_mode and skip_partyrock and not force_partyrock):
        _fill_skipping_partyrock(
            job_id,
            test_mode=test_mode,
            existing_resume=existing_resume,
            apply_url0=apply_url0,
            fill_restore=fill_restore,
            fill_options=fill_options,
            fill_run_gen=fill_run_gen,
            resume_only=resume_only,
        )
        return

    pr_url = partyrock_url(test_mode=test_mode)
    pr_mode = partyrock_mode_label(test_mode=test_mode)
    if _pipeline_stop_if_aborted(job_id, "PartyRock start"):
        return
    clear_fill_activity(job_id)
    after_bit = (
        "generate resume only (no fill)."
        if resume_only
        else (
            "then fast_fill (dummy)." if test_mode else "then fast_fill (real profile)."
        )
    )
    pipeline_milestone(
        job_id,
        event="start",
        detail=(
            f"Start ({'Test Mode' if test_mode else 'Real'}): PartyRock {pr_mode}, "
            f"{after_bit}"
        ),
        status="tailoring",
        status_detail=(
            f"Started. Opening PartyRock ({pr_mode}): {pr_url}"
            + (" — fill will not start." if resume_only else "")
        ),
    )
    with _lock:
        data = read_jobs()
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job is None:
            return
        session_key = job["session_key"]
        # jobs.json only holds a trimmed preview of the description at bulk-
        # discovery scale (see write_discovered_jobs.py) - the full text
        # PartyRock actually needs to tailor against lives in its own file.
        full_jd_file = RESUMES_DIR / job_id / "jd_full.txt"
        job_description = full_jd_file.read_text() if full_jd_file.exists() else (job.get("job_description") or "")
        job_title = (job.get("title") or "").strip()
        job_company = (job.get("company") or "").strip()
        job_location = (job.get("location") or "").strip()
        apply_url = job.get("apply_url") or job.get("job_url") or ""

    if not job_description.strip():
        # Deterministic extract once — do not open-end an agent fetch loop.
        if _pipeline_stop_if_aborted(job_id, "JD fetch (no description yet)"):
            return
        extract_url = (apply_url or "").strip()
        if extract_url:
            pipeline_milestone(
                job_id,
                event="jd",
                detail="No JD on file — running extract_job_posting once…",
                status_detail="No JD on file; extracting posting once…",
            )
            try:
                proc = subprocess.run(
                    [
                        PYTHON_BIN,
                        "-u",
                        str(ROOT / "scripts" / "extract_job_posting.py"),
                        extract_url,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    result = json.loads(proc.stdout)
                    description = (result.get("description") or "").strip()
                    if description:
                        job_dir = RESUMES_DIR / job_id
                        job_dir.mkdir(parents=True, exist_ok=True)
                        (job_dir / "jd_full.txt").write_text(description)
                        preview = description if len(description) <= 500 else (
                            description[: description.rfind(" ", 0, 500)]
                            + " … [full text in resumes/<id>/jd_full.txt]"
                        )
                        with _lock:
                            with locked_jobs_for_write() as data:
                                job_u = next(
                                    (j for j in data["jobs"] if j["id"] == job_id),
                                    None,
                                )
                                if job_u is not None:
                                    if result.get("company"):
                                        job_u["company"] = result["company"].strip()
                                        stamp_company_key(job_u)
                                        job_company = job_u["company"]
                                    if result.get("title"):
                                        job_u["title"] = result["title"].strip()
                                        job_title = job_u["title"]
                                    if result.get("location"):
                                        job_u["location"] = result["location"].strip()
                                        job_location = job_u["location"]
                                    job_u["job_description"] = preview
                                    job_u["updated_at"] = now_iso()
                        job_description = description
            except Exception as e:
                print(f"warn: extract_job_posting for {job_id} failed: {e}")
        if not job_description.strip():
            detail = (
                "No job description after extract_job_posting — cannot tailor. "
                "Paste a JD or fix the apply URL, then Retry."
            )
            pipeline_milestone(
                job_id,
                event="error",
                detail=detail,
                status="stuck",
                status_detail=detail,
            )
            try:
                close_job_partyrock_tab(job_id, RESUMES_DIR / job_id)
            except Exception as e:
                print(f"warn: PartyRock tab close on empty JD for {job_id}: {e}")
            return
        # Extract succeeded — refresh local job snapshot for later steps.
        with _lock:
            data = read_jobs()
            job = next((j for j in data["jobs"] if j["id"] == job_id), None)
            if job is None:
                return
            session_key = job["session_key"]
            job_title = (job.get("title") or job_title or "").strip()
            job_company = (job.get("company") or job_company or "").strip()
            job_location = (job.get("location") or job_location or "").strip()
            apply_url = job.get("apply_url") or job.get("job_url") or apply_url


    job_dir = RESUMES_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    jd_file = job_dir / "jd.txt"
    jd_file.write_text(job_description)
    resume_tex = job_dir / "resume.tex"
    resume_pdf = job_dir / "resume.pdf"
    playbook_already_sent = False

    if (
        not force_partyrock
        and resume_pdf.exists()
        and resume_tex.exists()
    ):
        # A resume was already produced for this job on some earlier
        # attempt (Start, then Cancel happened during/after filling, not
        # during tailoring) - Retry / Fill-with-resume shouldn't burn
        # another PartyRock generation for content that's already sitting
        # on disk. Tailor + fill sets force_partyrock and must re-run
        # PartyRock even when tex+pdf exist. (Upload-only PDF without tex
        # is handled earlier via resolve_job_resume_file.)
        pipeline_milestone(
            job_id,
            event="resume",
            detail="Reusing previously tailored resume (already on disk).",
            status="navigating",
            status_detail="Reusing previously tailored resume (already on disk). Navigating to apply URL.",
        )
        with _lock:
            with locked_jobs_for_write() as data:
                job = next((j for j in data["jobs"] if j["id"] == job_id), None)
                if job is not None:
                    job["resume_path"] = str(resume_pdf.relative_to(ROOT))
                    sync_job_resume_on_disk(job)
                    job["updated_at"] = now_iso()
    else:
        # Each run opens its own PartyRock CDP tab (partyrock_tabs.py); parallel
        # tailor across jobs is allowed. The tab closes after resume collection.
        pipeline_milestone(
            job_id,
            event="partyrock",
            detail=f"Opening PartyRock ({pr_mode}): {pr_url}",
            status="tailoring",
            status_detail=f"Opening PartyRock ({pr_mode}): {pr_url}",
        )
        if _pipeline_stop_if_aborted(job_id, "PartyRock browser open"):
            return
        try:
            # PR2-002: fail loud when CDP cannot start (not warn-only).
            _ensure_openclaw_managed_browser(required=True)
        except RuntimeError as e:
            # PR2-003: replace stale "Opening PartyRock…" with actionable stuck.
            if not _job_fill_aborted(job_id):
                _patch_job(
                    job_id,
                    status="stuck",
                    status_detail=(
                        f"PartyRock browser failed to start: {e}. "
                        "Fix: `./open_partyrock.sh` (Chrome for Testing + "
                        "OpenClaw CDP :18800), then Retry."
                    ),
                    question=(
                        "PartyRock CDP did not come up. Run `./open_partyrock.sh` "
                        "to re-auth/start CfT, then Retry. "
                        "Install CfT if needed: "
                        "python3 -m playwright install chromium"
                    ),
                )
            return
        # Re-tailor: drop any prior held tab for this job before opening a new one.
        try:
            close_job_partyrock_tab(job_id, job_dir)
        except Exception as e:
            print(f"warn: close prior PartyRock tab for {job_id}: {e}")
        if _pipeline_stop_if_aborted(job_id, "PartyRock tab prep"):
            return
        tailor_flag = "--test-mode" if test_mode else "--real"
        pipeline_milestone(
            job_id,
            event="partyrock",
            detail="Waiting on resume from PartyRock…",
            status_detail="Waiting on resume from PartyRock…",
        )
        tailor_exit, tailor_log = _run_subprocess_step(
            [
                PYTHON_BIN, "-u", str(TAILOR_SCRIPT),
                "--jd-file", str(jd_file),
                "--title", job_title,
                "--company", job_company,
                "--location", job_location,
                "--out", str(resume_tex),
                "--timeout", str(TAILOR_TIMEOUT_S - 100),
                "--job-id", job_id,
                tailor_flag,
            ],
            f"tailor_{job_id}.log", TAILOR_TIMEOUT_S, track_key=session_key,
            activity_job_id=job_id,
        )

        if tailor_exit != 0 or not resume_tex.exists():
            if tailor_exit == FILL_ABORT_EXIT:
                _pipeline_stop_if_aborted(job_id, "PartyRock tailor (user abort)")
                return
            # Used to also tell the agent to "continue the pipeline: fill the
            # application" in this same message - that handed the ENTIRE rest
            # of the job (compile, page-fit, address-pick, fill) to the agent
            # unsupervised, at full agent-token cost, with no server
            # checkpoint in between. Observed live on a real run: the agent
            # ended up re-deriving pick_address.py's whole job itself (cat-ing
            # all of addresses.json and writing an ad-hoc picker) because nothing
            # here ever got a chance to inject the pre-computed address for it.
            # Now the agent's job is narrowly just "produce resume.tex" - the
            # server checks for that file below and, if it's there, falls
            # through into the exact same compile/fit/address/fill pipeline the
            # happy path already uses, instead of leaving the agent to
            # improvise all of it.
            if _pipeline_stop_if_aborted(job_id, "PartyRock tailor failure"):
                return
            pipeline_milestone(
                job_id,
                event="partyrock",
                detail=(
                    f"Automated PartyRock tailor failed (exit {tailor_exit}). "
                    "Falling back to agent for resume.tex only."
                ),
                status_detail=(
                    f"PartyRock script failed (exit {tailor_exit}); "
                    "agent producing resume.tex manually."
                ),
            )
            if _pipeline_stop_if_aborted(job_id, "PartyRock agent fallback"):
                return
            run_agent_message(
                session_key,
                playbook_preamble() +
                f"Automated resume tailoring failed (scripts/tailor_resume.py "
                f"exited {tailor_exit}, see {tailor_log}). Follow PLAYBOOK.md's "
                "manual PartyRock steps instead: run `./open_partyrock.sh` "
                f"(OpenClaw Chrome-for-Testing, CDP :18800, shared login — "
                f"NOT a generic IDE/browser tool) for mode {pr_mode}, open "
                f"{pr_url}, paste the job description with leading "
                f"`Role Title: {job_title or 'Unknown'}`, "
                f"`Company: {job_company or 'Unknown'}`, and "
                f"`Location: {job_location or 'Unknown'}` lines, wait for it "
                "to finish, and "
                f"save the resulting LaTeX to {resume_tex}. Do NOT compile it "
                "yourself, do NOT pick a mailing address, do NOT proceed to "
                "filling the application, and do NOT log to the Excel tracker - "
                "just save that one file, then call update_job.py with "
                "--status-detail 'Manual tailoring done, handing back to "
                "pipeline.' and end your turn. The rest of the pipeline "
                "(compile, page-fit, address-pick, fill) resumes automatically "
                "once you do.",
                timeout_s=1800,
            )
            playbook_already_sent = True
            if not resume_tex.exists():
                # DASH2-004: agent fallback left no tex — force stuck so the
                # job does not sit in tailoring forever with no question.
                append_fill_activity(
                    job_id,
                    event="error",
                    detail="Manual PartyRock fallback did not produce resume.tex — stopping.",
                )
                if not _job_fill_aborted(job_id):
                    _patch_job(
                        job_id,
                        status="stuck",
                        status_detail=(
                            "Manual PartyRock fallback did not produce resume.tex."
                        ),
                        question=(
                            f"Agent PartyRock fallback finished without writing "
                            f"{resume_tex}. Check Live Activity / PartyRock, then "
                            "Retry or Skip."
                        ),
                    )
                return
            # else: fall through into the same compile/fit/address/fill steps
            # below, exactly as if scripts/tailor_resume.py had succeeded.
        else:
            append_fill_activity(
                job_id,
                event="partyrock",
                detail=f"resume.tex ready ({resume_tex.name}) — starting PDF compile.",
                persist=True,
            )
            pipeline_milestone(
                job_id,
                event="partyrock",
                detail="Collected resume from PartyRock",
                status_detail="Collected resume from PartyRock. Converting to PDF…",
            )

        # DASH2-018: Cancel / stale gen after PartyRock must not start compile/fit.
        if _pipeline_stop_if_aborted(job_id, "PartyRock gather (before PDF compile)"):
            return

        pipeline_milestone(
            job_id,
            event="pdf",
            detail="Converting resume to PDF (tectonic)…",
            status_detail="Converting resume to PDF…",
        )
        compile_exit, compile_log = _run_subprocess_step(
            [TECTONIC_BIN, str(resume_tex)],
            f"tectonic_{job_id}.log", 120, track_key=session_key,
            activity_job_id=job_id,
        )

        compile_ok = compile_exit == 0 and resume_pdf.exists()
        _persist_compiled_resume_after_tectonic(
            job_id,
            resume_pdf=resume_pdf,
            compile_ok=compile_ok,
            compile_exit=compile_exit,
            compile_log=compile_log,
            resume_only=resume_only,
        )

        if not compile_ok:
            if _pipeline_stop_if_aborted(job_id, "PDF compile failure"):
                return
            append_fill_activity(
                job_id,
                event="error",
                detail=f"PDF compile failed (exit {compile_exit}). See {compile_log.name}",
            )
            run_agent_message(
                session_key,
                (playbook_preamble() if not playbook_already_sent else "") +
                f"{resume_tex} was produced but tectonic failed to compile it "
                f"(see {compile_log}). Read the .tex file, fix the LaTeX error, and "
                f"recompile with tectonic so {resume_pdf} exists. "
                "Do NOT fill the application, do NOT pick an address, and do NOT "
                "set ready_for_review — only fix tex→pdf, then end your turn. "
                "The server resumes the pipeline after compile succeeds.",
                timeout_s=1800,
            )
            # Resume pipeline only if agent produced a PDF; else stay stuck.
            if resume_pdf.exists() and not _job_fill_aborted(job_id):
                _persist_compiled_resume_after_tectonic(
                    job_id,
                    resume_pdf=resume_pdf,
                    compile_ok=True,
                    compile_exit=0,
                    compile_log=compile_log,
                    resume_only=resume_only,
                )
                append_fill_activity(
                    job_id,
                    event="pdf",
                    detail="PDF compile recovered after agent tex fix — resuming pipeline.",
                )
            else:
                if not _job_fill_aborted(job_id):
                    _patch_job(
                        job_id,
                        status="stuck",
                        status_detail=(
                            f"Tectonic still failing after agent fix attempt "
                            f"(see {compile_log.name})."
                        ),
                        question=(
                            f"Could not compile {resume_tex}. Check the log, fix LaTeX, Retry."
                        ),
                    )
                return

        # Best-effort: shrink layout (margin/line-spacing only, never content -
        # see scripts/fit_resume_pages.py) if the resume ran past 2 pages. A
        # nonzero exit here just means it stayed over 2 pages at the tightest
        # tested layout - not worth stalling the pipeline over, so this is
        # logged, not treated as a hard failure.
        # DASH2-018: re-check abort after compile before page-fit / publish.
        if _pipeline_stop_if_aborted(job_id, "PDF compile (before page-fit)"):
            return
        pipeline_milestone(
            job_id,
            event="pdf",
            detail="Fitting resume within two pages…",
            status_detail="Fitting resume within two pages…",
        )
        fit_exit, fit_log = _run_subprocess_step(
            [PYTHON_BIN, "-u", str(ROOT / "scripts" / "fit_resume_pages.py"), str(resume_tex)],
            f"fit_pages_{job_id}.log", 90, track_key=session_key,
            activity_job_id=job_id,
        )
        if fit_exit != 0:
            append_fill_activity(
                job_id,
                event="pdf",
                detail=f"Page-fit best-effort exit={fit_exit} (continuing). See {fit_log.name}",
            )
            print(f"warn: fit_resume_pages.py exit={fit_exit} for {job_id} - see {fit_log}")
        else:
            append_fill_activity(
                job_id, event="pdf", detail="Resume PDF ready (≤2 pages or best effort).",
            )

    # Permanent user-facing copy for Command Center Documents/Resumes
    # (symlink → resumes/by_company/). After fit so the published PDF is final.
    published_name = None
    if resume_pdf.exists():
        with _lock:
            with locked_jobs_for_write() as data:
                job = next((j for j in data["jobs"] if j["id"] == job_id), None)
                if job is not None:
                    pub = _publish_resume_by_company(job, resume_pdf, data)
                    job["updated_at"] = now_iso()
                    if pub is not None:
                        published_name = pub.name
                    else:
                        published_name = conventional_resume_filename(job) or None
        if published_name is not None:
            append_fill_activity(
                job_id,
                event="resume",
                detail=f"Published resume → {published_name}",
            )

    if resume_only:
        if resume_pdf.exists():
            _complete_resume_only(
                job_id, test_mode=test_mode, resume_label=published_name
            )
        else:
            pipeline_milestone(
                job_id,
                event="error",
                detail="Generate resume only: no PDF produced.",
                status="stuck",
                status_detail=(
                    f"{_fill_mode_prefix(test_mode)} Generate resume only failed — "
                    "no PDF on disk."
                ),
            )
        return

    # The browser tool's file-upload only accepts paths under
    # ~/.openclaw/media/inbound - observed live, the agent tried uploading
    # resumes/<id>/resume.pdf directly, got "Invalid path: must stay within
    # inbound media directory", and burned a retry round-trip copying it
    # itself before every single fill turn. Doing that copy here instead
    # means the agent is never handed a path it can't actually use.
    INBOUND_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    if published_name:
        inbound_resume = INBOUND_MEDIA_DIR / published_name
        shutil.copyfile(resume_pdf, inbound_resume)
    _cleanup_old_inbound_resumes()

    # -----------------------------------------------------------------
    # After PartyRock + PDF: open apply_url via Playwright fast_fill.
    # Test Mode → prepare_dummy_run. Real → prepare_real_run (shared policy
    # + unique identity). Flash ON for both (same leftover quality as dummy).
    # Never agent browser fill here — that hung on Ashby/Bumble (analyze
    # forever / SIGTERM → status stuck at navigating).
    # Tailored PDF kept on disk; real fill uses it via --job-id.
    # -----------------------------------------------------------------
    apply_url_s = (apply_url or "").strip()
    prefix = _fill_mode_prefix(test_mode)
    if not apply_url_s:
        pipeline_milestone(
            job_id,
            event="error",
            detail="Tailor done but job has no apply_url — cannot start fill.",
            status="stuck",
            status_detail=(
                f"{prefix} Resume tailored, but no apply_url/job_url on this "
                "job — fill skipped."
            ),
        )
        return

    # Real mode (incl. skip-PartyRock): pick mailing address once and hand it
    # to fast_fill via FASTFILL_ADDRESS_TEXT — deterministic by job_id.
    address_text: str | None = None
    if not test_mode:
        address_text = _ensure_fill_address(job_id, job_location=job_location)
        if address_text is None:
            # Fall back to pick_address.py subprocess with --job-id seed.
            addr_exit, addr_log = _run_subprocess_step(
                [
                    PYTHON_BIN,
                    "-u",
                    str(ROOT / "scripts" / "pick_address.py"),
                    str(resume_tex if resume_tex.exists() else resume_pdf),
                    "--location",
                    job_location,
                    "--job-id",
                    job_id,
                ],
                f"pick_address_{job_id}.log", 15, track_key=session_key,
                activity_job_id=job_id,
            )
            if addr_exit == 0:
                try:
                    pick = json.loads(Path(addr_log).read_text(encoding="utf-8"))
                    address_text = _format_address_pick(pick)
                    if address_text:
                        _patch_job(job_id, applied_address=address_text)
                        append_fill_activity(
                            job_id,
                            event="address",
                            detail=f"Mailing address for fill: {address_text}",
                        )
                except Exception as e:
                    print(
                        f"warn: could not parse pick_address output for {job_id}: {e}"
                    )
            else:
                print(
                    f"warn: pick_address.py exit={addr_exit} for {job_id} - "
                    f"see {addr_log}; prepare_real_run will retry"
                )

    mode_bit = "dummy" if test_mode else "real profile"
    # Same Flash default as Fast fill button (ON unless env/payload disables).
    start_flash = _dummy_fill_flash_requested(fill_options)
    pipeline_milestone(
        job_id,
        event="fill",
        detail=f"Opening apply URL for fast_fill ({mode_bit}): {apply_url_s[:120]}",
        status="navigating",
        status_detail=(
            f"{prefix} Opening apply URL via Playwright fast_fill "
            f"(headed, {mode_bit}"
            f"{', flash ON' if start_flash else ''}). Never submits. "
            f"{apply_url_s[:160]}"
        ),
    )
    # headed=True so the form is visible for review. The separate PartyRock
    # target has already closed after resume collection.
    # Flash / captcha-wait / hold-open / refill identical for dummy and real.
    if _pipeline_stop_if_aborted(job_id, "fast_fill launch"):
        return
    run_hybrid_fill_dummy(
        job_id,
        test_mode=test_mode,
        headed=True,
        flash_leftovers=start_flash,
        restore_status=fill_restore,
        preserve_activity=True,
        address_text=address_text,
        fill_run_gen=fill_run_gen,
    )


def run_agent_message(session_key: str, message: str, timeout_s: int = 1200,
                       thinking: str = "medium") -> None:
    """Run an agent turn, tracking the process so it can be cancelled.

    Default thinking level is "medium", not the model/provider's own
    default (observed as "high" in request-shaping logs). Most turns here
    are routine field-fill/status-update decisions, not genuinely hard
    reasoning problems - "high" burns meaningfully more reasoning tokens
    per call for no real benefit on those, and at hundreds/thousands of
    jobs that difference adds up. Pass thinking="high" explicitly for a
    specific call if it's dealing with something that actually needs it."""
    turn_start = time.monotonic()
    # OpenClaw-free: the turn is a direct DeepSeek tool-loop run in-process by
    # agent_runner (no `openclaw agent` subprocess). It writes the same human
    # log here plus a structured events file for the reconcile loop / activity
    # feed. Graceful degradation: with no DEEPSEEK_API_KEY (or a loop that
    # can't proceed) it returns a non-zero exit and the block below surfaces
    # the job as `stuck` for a human — exactly the old "exit 127" behavior.
    ROOT.joinpath("logs").mkdir(exist_ok=True)
    log_name = session_key.rsplit(":", 1)[-1]
    log_path = ROOT / "logs" / f"agent_turn_{log_name}.log"
    # Truncate the human log at the start of each turn (Popen used mode "w").
    try:
        log_path.write_text("")
    except OSError:
        pass
    # Cross-process backstop for the double-start guarantee (in-process
    # tracking via agent_runner.active_turn_keys() is primary).
    with run_guard.session_lock(session_key):
        exit_code = agent_runner.run_turn(
            session_key, message,
            log_path=log_path, timeout_s=timeout_s, thinking=thinking,
        )
    _log_timing(f"agent_turn[{log_name}]", time.monotonic() - turn_start, f"exit={exit_code}")
    if exit_code != 0:
        # Surface silent CLI failures (e.g. missing node → 127) into any
        # job activity buffer that shares this session key's job id suffix.
        # Also fail honestly: never leave navigating/filling/tailoring forever
        # after SIGTERM/timeout (Bumble Ashby hung → exit 143, status stuck
        # at navigating until manual Cancel).
        try:
            hint = ""
            try:
                hint = (log_path.read_text(encoding="utf-8", errors="replace") or "")[:200]
            except Exception:
                pass
            job_id_guess = log_name.removeprefix("job-") if log_name.startswith("job-") else ""
            detail = (
                f"Agent turn exited {exit_code}"
                + (f": {hint.strip()}" if hint.strip() else "")
            )[:500]
            if job_id_guess:
                append_fill_activity(
                    job_id_guess,
                    event="error",
                    detail=detail,
                )
                with _lock:
                    with locked_jobs_for_write() as data:
                        job = next(
                            (j for j in data["jobs"] if j["id"] == job_id_guess), None
                        )
                        if job is not None and job.get("status") in IN_PROGRESS_STATUSES:
                            job["status"] = "stuck"
                            job["status_detail"] = (
                                f"Agent fill aborted (exit {exit_code}). "
                                "Never submitted. Retry Start or use Fast fill."
                            )[:500]
                            job["updated_at"] = now_iso()
        except Exception:
            pass


def abort_gateway_session(session_key: str) -> None:
    """Cancel a running agent turn for this session key.

    OpenClaw-free: turns run in-process, so we simply signal the runner's
    per-key cancel event (checked between tool steps), replacing the old
    OPENCLAW_DIRECT_ABORT trick of connecting a throwaway client. No-op if no
    turn is currently running on the key. (Name kept for call sites.)"""
    try:
        agent_runner.cancel_turn(session_key)
    except Exception as e:
        print(f"warn: abort_gateway_session failed: {e}")


_TAIL_LINE_RE = re.compile(
    r"^(?P<time>\d\d:\d\d:\d\d)\s+(?P<event>\S+)\s+\S+\s*(?P<detail>.*)$"
)


def _parse_openclaw_json(out: str):
    """Parse openclaw --json stdout; tolerate rare non-JSON prefixes on stdout."""
    text = (out or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise
        return json.loads(text[start:])


def _find_cron_job() -> dict | None:
    """The daily discovery schedule, as a cron-job-shaped dict.

    OpenClaw-free: backed by the in-process scheduler's local settings
    (``logs/cron_settings.json``) instead of ``openclaw cron list``. Always
    returns a job dict (the schedule always "exists" as a local setting), so
    the dashboard toggle/schedule controls keep working."""
    return scheduler_mod.settings_to_job_dict()


def _parse_cron_hm(expr: str | None) -> tuple[int, int]:
    """Return (minute, hour) from a 5-field cron expr. Default 0 9 * * *."""
    parts = str(expr or "").strip().split()
    if len(parts) < 2:
        return 0, 9
    try:
        minute = int(parts[0])
        hour = int(parts[1])
    except ValueError:
        return 0, 9
    if not (0 <= minute <= 59 and 0 <= hour <= 23):
        return 0, 9
    return minute, hour


def _cron_job_public(job: dict) -> dict:
    """Enrich cron job JSON with hour/minute for the dashboard UI."""
    out = dict(job)
    schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
    minute, hour = _parse_cron_hm(schedule.get("expr"))
    out["minute"] = minute
    out["hour"] = hour
    out["time"] = f"{hour:02d}:{minute:02d}"
    return out


def get_activity(session_key: str, tail: int = 60) -> list[dict]:
    """Structured lifecycle events for a session's agent turns.

    OpenClaw-free: reads the events the in-process ``agent_runner`` appends to
    ``logs/agent_events_<key>.jsonl`` (including a terminal ``session.ended``),
    replacing ``openclaw sessions tail``. The reconcile loop keys its
    auto-retry decision off the same ``session.ended`` event as before."""
    try:
        return agent_runner.read_events(session_key, tail=tail)
    except Exception as e:
        return [{"time": "", "event": "error", "detail": str(e)}]


IN_PROGRESS_STATUSES = {"navigating", "filling", "tailoring", "resuming"}
RECONCILE_INTERVAL_S = 20
STALE_AFTER_S = 90
NOTIFY_STATUSES = {"stuck", "blocked_captcha"}
NOTIFY_INTERVAL_S = 5
# Prune sweep cadence — cheap (regex over jobs.json) but not free, and new
# listings only arrive in discovery batches, so minutes is the right order.
AUTO_DELETE_SWEEP_INTERVAL_S = 300
_notified_fingerprints: dict[str, str] = {}


def _job_fingerprint(job: dict) -> str:
    return f"{job.get('status')}|{job.get('question') or ''}|{job.get('status_detail') or ''}"


def _send_desktop_notification(job: dict) -> None:
    """A stuck job otherwise only surfaces by having the dashboard tab open
    and looking at it - nothing pings you if you're away. `display dialog`
    (not `display notification`) is used deliberately: a notification
    banner auto-dismisses after a few seconds no matter what, that's a
    system-level behavior with no per-call override - a real modal dialog
    is the only osascript primitive that stays on screen until dismissed.

    For blocked_captcha specifically, the agent has correctly stopped
    rather than solving/bypassing the CAPTCHA (never do that) - the only
    legitimate way forward is a human finishing it by hand, so this offers
    an "Open Application" button that opens apply_url directly, landing
    exactly where the agent left off instead of making you dig the URL out
    of the dashboard yourself.

    Runs in its own thread since the dialog blocks until the user clicks a
    button (could be minutes) - it must never block notify_stuck_jobs_loop's
    5s poll, or a second job going stuck wouldn't be noticed until the
    first dialog is dismissed."""
    company = job.get("company") or "A job"
    is_captcha = job.get("status") == "blocked_captcha"
    if is_captcha:
        body = job.get("status_detail") or f"{job.get('title', '')} - blocked by CAPTCHA"
    else:
        body = job.get("question") or job.get("status_detail") or "Needs your input"
    body = body.strip().replace("\n", " ")[:300]
    title = f"Job Hunter needs you: {company}"
    apply_url = (job.get("apply_url") or job.get("job_url") or "").strip()

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    offer_open = is_captcha and apply_url
    if offer_open:
        script = (
            f'display dialog "{esc(body)}" with title "{esc(title)}" '
            'buttons {"Dismiss", "Open Application"} default button "Open Application" '
            'with icon caution'
        )
    else:
        script = (
            f'display dialog "{esc(body)}" with title "{esc(title)}" '
            'buttons {"OK"} default button "OK" with icon caution'
        )

    def _run():
        try:
            result = subprocess.run(
                ["osascript", "-e", "beep", "-e", script],
                capture_output=True, text=True,
            )
            if offer_open and "Open Application" in result.stdout:
                subprocess.run(["open", apply_url], capture_output=True)
        except Exception as e:
            print(f"warn: desktop notification failed: {e}")

    threading.Thread(target=_run, daemon=True).start()


def _send_answer_dialog(job: dict) -> None:
    """For a stuck job with an open question, an OK-only dialog still means
    walking to the dashboard to type the actual reply. `display dialog`'s
    `default answer` bakes a real text field into the same dialog - typing
    an answer and clicking Send calls submit_job_answer() directly, the
    exact same path the dashboard's own "Send answer" button uses, so the
    reply reaches the agent without ever opening the browser tab. Clicking
    Cancel (or closing the dialog) just leaves the job stuck, unchanged -
    the dashboard is still there as a fallback."""
    job_id = job["id"]
    company = job.get("company") or "A job"
    question = (job.get("question") or "").strip().replace("\n", " ")[:300]
    title = f"Job Hunter needs you: {company}"

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = (
        f'display dialog "{esc(question)}" with title "{esc(title)}" '
        'default answer "" buttons {"Cancel", "Send"} default button "Send" with icon caution'
    )

    def _run():
        try:
            result = subprocess.run(
                ["osascript", "-e", "beep", "-e", script],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                return  # Cancel clicked, or dialog dismissed - nothing to send
            m = re.search(r"text returned:(.*)$", result.stdout.strip())
            answer = m.group(1).strip() if m else ""
            if answer:
                submit_job_answer(job_id, answer)
        except Exception as e:
            print(f"warn: answer dialog failed: {e}")

    threading.Thread(target=_run, daemon=True).start()


def notify_stuck_jobs_loop() -> None:
    """The agent edits jobs.json directly via its own exec/write tools,
    bypassing every one of this server's API handlers - polling here is the
    only place that catches every path into a stuck state, not just the
    ones that happen to go through _handle_answer/_handle_start. Seeds
    "already notified" from whatever's already stuck at startup so a server
    restart doesn't re-fire notifications for old, already-seen blockers."""
    try:
        data = read_jobs()
        for job in data.get("jobs", []):
            if job.get("status") in NOTIFY_STATUSES:
                _notified_fingerprints[job["id"]] = _job_fingerprint(job)
    except Exception:
        pass
    while True:
        time.sleep(NOTIFY_INTERVAL_S)
        try:
            data = read_jobs()
            still_stuck_ids = set()
            for job in data.get("jobs", []):
                if job.get("status") not in NOTIFY_STATUSES:
                    continue
                still_stuck_ids.add(job["id"])
                fp = _job_fingerprint(job)
                if _notified_fingerprints.get(job["id"]) != fp:
                    _notified_fingerprints[job["id"]] = fp
                    if job.get("status") == "stuck" and job.get("question"):
                        _send_answer_dialog(job)
                    else:
                        _send_desktop_notification(job)
            # Forget jobs no longer stuck, so if one gets stuck again later
            # - even with the exact same question - it notifies again.
            for job_id in list(_notified_fingerprints):
                if job_id not in still_stuck_ids:
                    _notified_fingerprints.pop(job_id, None)
        except Exception as e:
            print(f"warn: notify_stuck_jobs_loop error: {e}")


_auto_retried_job_ids: set[str] = set()


def _force_stuck_orphaned_in_progress(*, ignore_age: bool = False) -> list[str]:
    """DASH2-002: IN_PROGRESS with no live tracked proc → stuck.

    After a dashboard crash/restart, local fast-fill jobs can remain
    ``filling``/``navigating``/``tailoring`` forever because reconcile used
    to require an OpenClaw ``session.ended`` event. Call with
    ``ignore_age=True`` at startup (no procs survived the restart); the
    reconcile loop uses age > ``STALE_AFTER_S``.
    """
    forced: list[str] = []
    with _lock:
        with locked_jobs_for_write() as data:
            for job in data.get("jobs") or []:
                if job.get("status") not in IN_PROGRESS_STATUSES:
                    continue
                if job.get("status") in FILL_ABORT_STATUSES:
                    continue
                session_key = job.get("session_key")
                proc = _running_procs.get(session_key) if session_key else None
                if proc is not None and proc.poll() is None:
                    continue
                if not ignore_age:
                    try:
                        updated_ts = datetime.fromisoformat(
                            str(job.get("updated_at") or "").replace("Z", "+00:00")
                        )
                    except Exception:
                        updated_ts = None
                    age_s = (now_dt() - updated_ts).total_seconds() if updated_ts else 9999
                    if age_s < STALE_AFTER_S:
                        continue
                jid = job.get("id") or "?"
                job["status"] = "stuck"
                job["status_detail"] = (
                    "No running fill/tailor process — in-progress status was orphaned "
                    "(server restart or crashed worker). Use Retry or Skip."
                )
                job["question"] = (
                    "This job was left in-progress with nothing running. "
                    "Check Live Activity, then Retry or Skip."
                )
                job["updated_at"] = now_iso()
                _append_timeline_locked(
                    job,
                    _timeline_entry(
                        event="stuck",
                        detail=job["status_detail"],
                        at=job["updated_at"],
                    ),
                )
                forced.append(str(jid))
    # CDP close is intentionally outside the jobs lock: a restart/dead worker
    # must not leave its tracked PartyRock page idle, and network I/O must not
    # block unrelated dashboard reads/writes.
    for jid in forced:
        try:
            close_job_partyrock_tab(jid, RESUMES_DIR / jid)
        except Exception as e:
            print(f"warn: PartyRock tab close on orphan reconcile for {jid}: {e}")
    return forced


def reconcile_loop():
    """Background thread: if a job's status claims it's in progress but its
    process isn't actually running anymore, surface that in the dashboard
    instead of showing a status that's gone stale forever. This catches
    error/aborted/timeout endings AND a plain successful completion that
    simply never updated jobs.json's own status - observed live: a turn
    can end cleanly (session.ended/success) without the agent making any
    further status-changing move, which left a job showing "resuming"
    long after the gateway itself considered the session done. From the
    dashboard's perspective a stale in-progress status with nothing
    actually running is equally broken either way.

    Before bothering a human, tries exactly one automatic retry - this
    covers the class of problem actually observed live (a transient
    DeepSeek API timeout that killed a turn 26 seconds in, with no error
    surfaced, on an otherwise-healthy job). Capped at one attempt per job
    id so a genuinely broken job doesn't retry forever silently; the
    second failure goes to a human exactly as before, noting a retry
    already happened.

    DASH2-002: local fast-fill orphans (no gateway ``session.ended``) are
    force-stuck once age exceeds ``STALE_AFTER_S`` with no live proc.
    """
    while True:
        time.sleep(RECONCILE_INTERVAL_S)
        try:
            # Phase 1 (locked, fast): find which jobs look stale enough to
            # need a get_activity() check - no subprocess calls in here.
            candidates = []
            with _lock:
                data = read_jobs()
                for job in data["jobs"]:
                    if job.get("status") not in IN_PROGRESS_STATUSES:
                        continue
                    session_key = job.get("session_key")
                    proc = _running_procs.get(session_key)
                    if proc and proc.poll() is None:
                        continue  # genuinely still running (tracked subprocess)
                    # Agent turns run in-process (no Popen); the runner's
                    # active-turn registry is authoritative for those.
                    if session_key and agent_runner.is_turn_active(session_key):
                        continue
                    try:
                        updated_ts = datetime.fromisoformat(job["updated_at"].replace("Z", "+00:00"))
                    except Exception:
                        updated_ts = None
                    age_s = (now_dt() - updated_ts).total_seconds() if updated_ts else 9999
                    if age_s < STALE_AFTER_S:
                        continue  # give it a moment before flagging
                    candidates.append((job["id"], session_key, job.get("status") or ""))

            if not candidates:
                continue

            # Phase 2 (unlocked, slow): the actual gateway subprocess calls.
            # This must never happen while _lock is held - it used to, and
            # every other dashboard request (any GET/POST needing the lock)
            # would queue up behind however long these calls took.
            to_retry = []  # (job_id, session_key, detail)
            to_stuck = []  # (job_id, detail)
            to_orphan_stuck = []  # (job_id, detail) — local fill, no session.ended
            for job_id, session_key, job_status in candidates:
                events = get_activity(session_key, tail=5) if session_key else []
                last = events[-1] if events else None
                # UI-018 / DASH2-010: only agent turns (tailoring/resuming) get
                # auto agent retry. Local fast_fill uses filling/navigating —
                # never spawn run_agent_message for those.
                agentish = job_status in ("tailoring", "resuming")
                if last and last.get("event") == "session.ended" and agentish:
                    detail = last.get("detail") or "session.ended"
                    if job_id not in _auto_retried_job_ids:
                        _auto_retried_job_ids.add(job_id)
                        to_retry.append((job_id, session_key, detail))
                    else:
                        to_stuck.append((job_id, detail))
                else:
                    # DASH2-002 / UI-018: no live proc + aged past STALE, or
                    # non-agent fill with session.ended noise. Force stuck —
                    # do not spawn an agent retry for a local fill.
                    to_orphan_stuck.append(
                        (
                            job_id,
                            "No running fill/tailor process; in-progress status "
                            "orphaned past stale window.",
                        )
                    )

            if not to_retry and not to_stuck and not to_orphan_stuck:
                continue

            # Phase 3 (locked, fast): apply whatever changed. Re-reads
            # jobs.json fresh in case something else wrote to it during
            # phase 2's unlocked window.
            with _lock:
                with locked_jobs_for_write() as data:
                    by_id = {j["id"]: j for j in data["jobs"]}
                    for job_id, session_key, detail in to_retry:
                        job = by_id.get(job_id)
                        if job is None:
                            continue
                        if job.get("status") not in IN_PROGRESS_STATUSES:
                            continue
                        job["status_detail"] = (
                            f"Previous run ended ({detail}) without finishing - "
                            "automatically retrying once before asking for help."
                        )
                        job["updated_at"] = now_iso()
                    for job_id, detail in to_stuck:
                        job = by_id.get(job_id)
                        if job is None:
                            continue
                        if job.get("status") not in IN_PROGRESS_STATUSES:
                            continue
                        job["status"] = "stuck"
                        job["status_detail"] = (
                            f"Session ended ({detail}) and nothing is currently running - an "
                            "automatic retry was already attempted once and also didn't finish."
                        )
                        job["question"] = (
                            f"The last two runs ended ({detail}) without reaching a stopping "
                            "point, including one automatic retry. Check Live Activity for "
                            "details. Reply here to give guidance, or use Start to retry "
                            "manually / Skip to give up on this one."
                        )
                        job["updated_at"] = now_iso()
                    for job_id, detail in to_orphan_stuck:
                        job = by_id.get(job_id)
                        if job is None:
                            continue
                        if job.get("status") not in IN_PROGRESS_STATUSES:
                            continue
                        job["status"] = "stuck"
                        job["status_detail"] = (
                            "No running fill/tailor process — in-progress status was orphaned "
                            "(server restart or crashed worker). Use Retry or Skip."
                        )
                        job["question"] = (
                            "This job was left in-progress with nothing running. "
                            "Check Live Activity, then Retry or Skip."
                        )
                        job["updated_at"] = now_iso()
                        _append_timeline_locked(
                            job,
                            _timeline_entry(
                                event="stuck",
                                detail=job["status_detail"],
                                at=job["updated_at"],
                            ),
                        )

            # Fire retry turns after releasing the lock - each spawns its
            # own thread anyway, no reason to hold the lock for it.
            for job_id, session_key, detail in to_retry:
                retry_message = (
                    "Your previous turn on this job ended without reaching a "
                    f"stopping point (session ended: {detail}). Check the job's "
                    "current real-world state (jobs.json status/status_detail, and "
                    "the actual page if a browser session is open) and continue from "
                    "there - don't restart from scratch if progress was already made. "
                    "If you're genuinely stuck, set status to stuck with a question "
                    "as usual."
                )
                threading.Thread(
                    target=run_agent_message, args=(session_key, retry_message),
                    daemon=True,
                ).start()
        except Exception as e:
            print(f"warn: reconcile_loop error: {e}")


def now_dt():
    return datetime.now(timezone.utc)


def _append_deleted_timeline(job: dict, *, detail: str, at: str | None = None) -> None:
    """Caller must already hold ``_lock`` (uses ``_append_timeline_locked``)."""
    _append_timeline_locked(
        job,
        _timeline_entry(
            event="deleted",
            detail=detail,
            at=at or job.get("updated_at") or now_iso(),
        ),
    )


def _mark_job_soft_deleted(
    job: dict,
    *,
    deleted_reason: str,
    status_detail: str,
    duplicate_of: str | None = None,
) -> None:
    """In-lock soft-delete fields (Deleted trash). Caller must hold ``_lock``."""
    now = now_iso()
    job["status"] = "deleted"
    job["deleted_at"] = now
    job["deleted_reason"] = deleted_reason
    job["status_detail"] = status_detail
    job["updated_at"] = now
    if duplicate_of:
        job["duplicate_of"] = duplicate_of
    _append_deleted_timeline(job, detail=status_detail, at=now)


_PROGRESS_CANCEL_ORIGINS = frozenset(
    {"tailoring", "navigating", "filling", "resuming", "resume_ready"}
)
_STUCK_CANCEL_ORIGINS = frozenset({"stuck", "blocked_captcha"})
_READY_CANCEL_ORIGINS = frozenset({"ready_for_review"})


def _clear_job_run_markers_locked(job: dict) -> None:
    """Drop live-run fields; keep resume_path. Caller must hold ``_lock``."""
    job["question"] = None
    job["pending_command"] = None
    job.pop("ready_announced", None)


def _park_job_after_cancel(job: dict, *, origin_status: str | None = None) -> None:
    """Abort the run but keep the job in its OmniDex queue bucket.

    In Progress → ``resume_ready`` (parked, not a live thread).
    Stuck / CAPTCHA → stay stuck / blocked_captcha.
    Ready → stay ``ready_for_review``.
    Never demotes to Open (``discovered``).

    Caller must already hold ``_lock``.
    """
    origin = (origin_status or job.get("status") or "").strip()
    resume_path = job.get("resume_path")
    now = now_iso()
    if origin in _STUCK_CANCEL_ORIGINS:
        job["status"] = origin
        if origin == "blocked_captcha":
            job["status_detail"] = "Run cancelled by user — still on CAPTCHA hold."
        else:
            job["status_detail"] = "Run cancelled by user — still stuck."
    elif origin in _READY_CANCEL_ORIGINS:
        job["status"] = "ready_for_review"
        job["status_detail"] = "Run cancelled by user — still Ready for review."
    elif origin in _PROGRESS_CANCEL_ORIGINS:
        job["status"] = "resume_ready"
        job["status_detail"] = "Cancelled by user — run stopped. Fill when you want."
    else:
        job["status_detail"] = "Cancelled by user — run stopped."
    job["updated_at"] = now
    _clear_job_run_markers_locked(job)
    if resume_path:
        job["resume_path"] = resume_path
    _append_timeline_locked(
        job,
        _timeline_entry(
            event="cancelled",
            detail=job["status_detail"],
            at=now,
        ),
    )


def _reset_job_to_open_after_cancel(job: dict) -> None:
    """Legacy cancelled → Open (migration only). Keep resume_path / disk resume.

    Caller must already hold ``_lock``. Live Cancel clicks use
    ``_park_job_after_cancel`` instead so In Progress / Stuck / Ready stay put.
    """
    resume_path = job.get("resume_path")
    now = now_iso()
    job["status"] = "discovered"
    job["status_detail"] = "Cancelled by user — returned to Open."
    job["updated_at"] = now
    _clear_job_run_markers_locked(job)
    if resume_path:
        job["resume_path"] = resume_path
    _append_timeline_locked(
        job,
        _timeline_entry(
            event="cancelled_reset",
            detail=job["status_detail"],
            at=now,
        ),
    )


def _find_duplicate_merge_pair(
    jobs: list, job: dict, preferred_id: str | None = None
) -> tuple[dict, dict] | None:
    """Return (winner, loser) for a duplicate skip/merge, or None if no partner."""
    try:
        from dedup_jobs import pick_winner, should_merge
    except Exception as e:
        print(f"warn: dedup_jobs import for merge failed: {e}")
        return None
    inactive = {"deleted", "applied"} | set(LEGACY_SKIP_STATUSES)
    by_id = {j.get("id"): j for j in jobs if j.get("id")}
    candidates: list[dict] = []
    if preferred_id and preferred_id in by_id and preferred_id != job.get("id"):
        other = by_id[preferred_id]
        if other.get("status") not in inactive:
            candidates.append(other)
    if not candidates:
        for other in jobs:
            if other is job or other.get("id") == job.get("id"):
                continue
            if other.get("status") in inactive:
                continue
            try:
                if should_merge(job, other):
                    candidates.append(other)
            except Exception:
                continue
    if not candidates:
        return None
    partner = candidates[0]
    for other in candidates[1:]:
        partner, _ = pick_winner(partner, other)
    return pick_winner(job, partner)


def migrate_triage_holding_pen_once() -> dict:
    """Map legacy Skipped holding pen → Deleted; cancelled → Open (retry).

    Choice (documented): skipped_* → deleted with reason preserved in
    deleted_reason / status_detail; cancelled → discovered so Fill can retry
    (resume_path kept). Idempotent.
    """
    counts = {"skipped_to_deleted": 0, "cancelled_to_open": 0}
    reason_map = {
        "skipped_manual": "skipped_manual",
        "skipped_duplicate": "duplicate",
        "skipped_contract": "contract",
        "skipped_easy_apply": "easy_apply",
    }
    to_block: list[dict] = []
    with _lock:
        with locked_jobs_for_write() as data:
            for job in data.get("jobs") or []:
                st = job.get("status")
                if st in LEGACY_SKIP_STATUSES:
                    detail = (job.get("status_detail") or "").strip() or (
                        f"Migrated from {st} (Skipped holding pen removed)."
                    )
                    _mark_job_soft_deleted(
                        job,
                        deleted_reason=reason_map.get(st, "skipped_manual"),
                        status_detail=detail,
                        duplicate_of=job.get("duplicate_of"),
                    )
                    counts["skipped_to_deleted"] += 1
                    if st != "skipped_duplicate":
                        to_block.append(dict(job))
                elif st == "cancelled":
                    _reset_job_to_open_after_cancel(job)
                    # Migration copy: clarify backlog reset (not a fresh Cancel click).
                    job["status_detail"] = (
                        "Migrated from cancelled — returned to Open for retry."
                    )
                    counts["cancelled_to_open"] += 1
    for snap in to_block:
        try:
            block_deleted_job(snap, keep_tombstone=True)
        except TypeError:
            try:
                block_deleted_job(snap)
            except Exception as e:
                print(f"warn: block on triage migrate {snap.get('id')}: {e}")
        except Exception as e:
            print(f"warn: block on triage migrate {snap.get('id')}: {e}")
    return counts


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_HEAD(self):
        """HEAD must succeed — some embedded browsers / proxies probe before GET."""
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        # Match GET /api/jobs so probes do not 404 while the list endpoint works.
        if parts == ["api", "jobs"]:
            with _lock:
                body, etag = _cached_jobs_list_response()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", etag)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        if not parts:
            path, ctype = STATIC_DIR / "index.html", "text/html"
        elif parts[0] == "app.js":
            path, ctype = STATIC_DIR / "app.js", "application/javascript"
        elif parts[0] == "job_sort.js":
            path, ctype = STATIC_DIR / "job_sort.js", "application/javascript"
        else:
            self.send_response(404)
            self.end_headers()
            return
        if not path.exists():
            self.send_response(404)
            self.end_headers()
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if ctype in ("text/html", "application/javascript"):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.end_headers()

    def _send_json(self, obj=None, status=200, *, headers=None, body_bytes=None):
        body = body_bytes if body_bytes is not None else json.dumps(obj).encode()
        self.send_response(status)
        if status != 304:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str, inline_filename: str | None = None):
        if not path.exists():
            self._send_json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # HTML/JS carry the ops UI (inline CSS in index.html). Chrome --app=
        # windows cache aggressively; no-cache keeps app-icon and browser tabs
        # on the same live stylesheet instead of a stale washed-out shell.
        if content_type in ("text/html", "application/javascript"):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        if inline_filename:
            # Without this, some browsers default to downloading a PDF
            # rather than opening it in the tab - explicit "inline" (not
            # "attachment") forces the view-in-tab behavior regardless of
            # the browser's own default.
            self.send_header("Content-Disposition", f'inline; filename="{inline_filename}"')
        self.end_headers()
        self.wfile.write(body)

    def _job(self, data, job_id):
        return next((j for j in data["jobs"] if j["id"] == job_id), None)

    @contextmanager
    def _locked_job(self, job_id, *, allow_purge: bool = False):
        """Hold ``_lock`` + EX flock while yielding ``(data, job)``.

        Full read-mutate-write under ``locked_jobs_for_write`` so discovery
        adds cannot be wiped by a stale snapshot. Callers mutate ``data`` in
        place; it is written on clean exit (do not call ``write_jobs``).
        """
        with _lock:
            with locked_jobs_for_write(allow_purge=allow_purge) as data:
                yield data, self._job(data, job_id)

    # POST /api/jobs/<id>/<action> → (handler method name, whether it takes the
    # JSON payload). Data-driven replacement for the hand-unrolled per-action
    # `len(parts)==4 and parts[3]=="…"` index checks. Matching + precedence are
    # identical: dispatch is gated on the same `len==4 and parts[0:2]==api/jobs`
    # guard, and any action not in this table falls through to the exact-path
    # routes below exactly as an unmatched `if` chain did. (The multipart
    # `resume` upload is intentionally absent — it is routed before the body is
    # read, above.)
    _JOB_ACTION_POST = {
        "answer": ("_handle_answer", True),
        "approve_command": ("_handle_approve_command", True),
        "cancel": ("_handle_cancel", False),
        "skip": ("_handle_skip", True),
        "restore": ("_handle_restore", False),
        "edit": ("_handle_edit_applied", True),
        "submitted": ("_handle_mark_submitted", False),
        "claim-ready-announcement": ("_handle_claim_ready_announcement", False),
        "resume-latex": ("_handle_resume_latex_save", True),
        "start": ("_handle_start", True),
        "resolve-apply": ("_handle_resolve_apply", True),
    }

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]

        if not parts:
            self._send_file(STATIC_DIR / "index.html", "text/html")
            return
        # Classic UI retired — keep bookmark redirects to Ops `/`.
        if parts[0] in ("classic", "classic.html", "classic.js"):
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        # Ops preview merged into `/` — redirect so bookmarks still work.
        if parts[0] in ("ops-preview", "ops-preview.html"):
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        if parts[0] == "app.js":
            self._send_file(STATIC_DIR / "app.js", "application/javascript")
            return
        if parts[0] == "job_sort.js":
            self._send_file(STATIC_DIR / "job_sort.js", "application/javascript")
            return
        if parts == ["api", "stats"]:
            with _lock:
                data = read_jobs()
            self._send_json(aggregate_stats(data.get("jobs", [])))
            return
        if len(parts) == 2 and parts[0] == "resume":
            with _lock:
                data = read_jobs()
            job = self._job(data, parts[1])
            if not job:
                self._send_json({"error": "not found"}, 404)
                return
            disk = _ensure_conventional_resume_pdf(job["id"])
            if disk is None:
                self._send_json({"error": "not found"}, 404)
                return
            filename = disk.name
            self._send_file(disk, "application/pdf", inline_filename=filename)
            return
        if parts == ["api", "jobs"]:
            with _lock:
                body, etag = _cached_jobs_list_response()
            # Omit JD bodies from the list poll — full cleaned text is
            # GET /api/jobs/<id>/description on expand.
            headers = {"ETag": etag}
            if self.headers.get("If-None-Match") == etag:
                self._send_json(status=304, headers=headers, body_bytes=b"")
            else:
                self._send_json(headers=headers, body_bytes=body)
            return
        if parts == ["api", "status"]:
            self._send_json(runtime_status())
            return
        if parts == ["api", "discover"]:
            self._send_json(discovery_status())
            return
        if parts == ["api", "discover", "settings"]:
            self._send_json(load_discovery_settings())
            return
        if parts == ["api", "prune", "settings"]:
            self._send_json(load_prune_settings())
            return
        if len(parts) == 4 and parts[0:2] == ["api", "jobs"] and parts[3] == "description":
            with _lock:
                data = read_jobs()
            job = self._job(data, parts[2])
            if not job:
                self._send_json({"error": "not found"}, 404)
                return
            raw, source = load_raw_job_description(job)
            cleaned = sanitize_job_description_for_display(raw)
            self._send_json({
                "id": job["id"],
                "job_description": cleaned,
                "source": source,
                "chars": len(cleaned),
            })
            return
        if len(parts) == 4 and parts[0:2] == ["api", "jobs"] and parts[3] == "copy-kit":
            qs = parse_qs(parsed.query)
            if "test_mode" not in qs:
                self._send_json(
                    {
                        "error": (
                            "test_mode required (true = dummy identity, "
                            "false = real profile)"
                        )
                    },
                    400,
                )
                return
            try:
                test_mode = _parse_test_mode({"test_mode": qs.get("test_mode", [""])[0]})
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
                return
            with _lock:
                data = read_jobs()
                job = self._job(data, parts[2])
                job = dict(job) if job else None
            if not job:
                self._send_json({"error": "not found"}, 404)
                return
            self._send_json(_copy_kit_for_job(job, test_mode=test_mode))
            return
        if len(parts) == 4 and parts[0:2] == ["api", "jobs"] and parts[3] == "resume-latex":
            with _lock:
                data = read_jobs()
            job = self._job(data, parts[2])
            if not job:
                self._send_json({"error": "not found"}, 404)
                return
            payload = _resume_latex_for_job(job)
            payload["id"] = job["id"]
            status = 409 if payload.get("missing_tex") else 200
            self._send_json(payload, status)
            return
        if len(parts) == 3 and parts[0:2] == ["api", "jobs"]:
            with _lock:
                data = read_jobs()
            job = self._job(data, parts[2])
            if not job:
                self._send_json({"error": "not found"}, 404)
                return
            self._send_json(slim_job_for_list(job))
            return
        if len(parts) == 4 and parts[0:2] == ["api", "jobs"] and parts[3] == "activity":
            with _lock:
                data = read_jobs()
            job = self._job(data, parts[2])
            if not job:
                self._send_json({"error": "not found"}, 404)
                return
            # Lifecycle timeline: live fill while running, else jobs.json
            # timeline / synthesized fields. Never OpenClaw session.tail here.
            self._send_json({"events": get_job_activity(job, tail=200)})
            return
        if parts == ["api", "profile"]:
            if PROFILE_FILE.exists():
                self._send_json(json.loads(PROFILE_FILE.read_text()))
            else:
                self._send_json({})
            return
        if parts == ["api", "cron"]:
            try:
                job = _find_cron_job()
                if not job:
                    self._send_json({"error": "not found"}, 404)
                else:
                    self._send_json(_cron_job_public(job))
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return
        if parts == ["api", "metrics", "timeline"]:
            self._send_json(_metrics_timeline_payload())
            return
        self._send_json({"error": "not found"}, 404)

    # --------------------------------------------------------------- POST
    def do_POST(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        length = int(self.headers.get("Content-Length", 0))
        # Multipart resume upload — do not JSON-parse the body.
        if len(parts) == 4 and parts[0:2] == ["api", "jobs"] and parts[3] == "resume":
            self._handle_resume_upload(parts[2], length)
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            # sendBeacon sometimes arrives as text/plain; tolerate empty body.
            if not raw.strip():
                payload = {}
            else:
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace"))
                except Exception:
                    self._send_json({"error": "bad json"}, 400)
                    return
        if not isinstance(payload, dict):
            payload = {}

        if parts == ["api", "heartbeat"]:
            body, code = record_ui_heartbeat(str(payload.get("client_id") or ""))
            self._send_json(body, code)
            return
        if parts == ["api", "shutdown"]:
            body, code = request_ui_shutdown(
                str(payload.get("client_id") or "") or None,
                force=bool(payload.get("force")),
            )
            self._send_json(body, code)
            return
        if parts == ["api", "restart"]:
            body, code = request_ui_restart(
                str(payload.get("client_id") or "") or None,
            )
            self._send_json(body, code)
            return
        if len(parts) == 4 and parts[0:2] == ["api", "jobs"]:
            route = self._JOB_ACTION_POST.get(parts[3])
            if route is not None:
                method_name, takes_payload = route
                handler = getattr(self, method_name)
                if takes_payload:
                    handler(parts[2], payload)
                else:
                    handler(parts[2])
                return
        if parts == ["api", "profile"]:
            self._handle_profile_update(payload)
            return
        if parts == ["api", "jobs", "add"]:
            self._handle_add_job(payload)
            return
        if parts == ["api", "jobs", "empty-deleted"]:
            self._handle_empty_deleted()
            return
        if parts == ["api", "prune"]:
            self._handle_prune(payload)
            return
        if parts == ["api", "prune", "settings"]:
            self._handle_prune_settings(payload)
            return
        if parts == ["api", "discover"]:
            self._handle_discover(payload)
            return
        if parts == ["api", "discover", "settings"]:
            self._handle_discover_settings(payload)
            return
        if parts == ["api", "discover", "abort"]:
            self._handle_discover_abort(payload)
            return
        if parts == ["api", "cron", "toggle"]:
            self._handle_cron_toggle(payload)
            return
        if parts == ["api", "cron", "schedule"]:
            self._handle_cron_schedule(payload)
            return
        self._send_json({"error": "not found"}, 404)

    # ------------------------------------------------------------- DELETE
    def do_DELETE(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) == 4 and parts[0:2] == ["api", "jobs"] and parts[3] == "resume":
            self._handle_resume_clear(parts[2])
            return
        if len(parts) == 3 and parts[0:2] == ["api", "jobs"]:
            job_id = parts[2]
            removed = None
            blocked = []
            session_key = None
            with _lock:
                with locked_jobs_for_write() as data:
                    for j in data["jobs"]:
                        if j.get("id") == job_id:
                            removed = j
                            break
                    if removed is None:
                        self._send_json({"error": "not found"}, 404)
                        return
                    session_key = removed.get("session_key")
                    # Soft-delete: keep row for Deleted trash view; block URLs now.
                    if removed.get("status") != "deleted":
                        _bump_job_fill_gen_locked(removed)
                        _mark_job_soft_deleted(
                            removed,
                            deleted_reason="user",
                            status_detail="Deleted by user from dashboard.",
                        )
            # Kill any in-flight fill/tailor so it cannot undelete at fill-end.
            proc = _running_procs.get(session_key) if session_key else None
            _kill_process_tree(proc)
            if session_key:
                abort_gateway_session(session_key)
            try:
                close_job_partyrock_tab(job_id, RESUMES_DIR / job_id)
            except Exception as e:
                print(f"warn: PartyRock tab close on delete for {job_id}: {e}")
            try:
                blocked = block_deleted_job(removed, keep_tombstone=True)
            except TypeError:
                blocked = block_deleted_job(removed)
            except Exception as e:
                print(f"warn: block_deleted_job failed: {e}")
            self._send_json({
                "ok": True,
                "deleted": True,
                "soft": True,
                "blocked_urls": blocked,
            })
            return
        self._send_json({"error": "not found"}, 404)

    # ----------------------------------------------------------- handlers
    def _handle_answer(self, job_id, payload):
        answer = payload.get("answer", "")
        if not submit_job_answer(job_id, answer):
            self._send_json({"error": "not found"}, 404)
            return
        self._send_json({"ok": True})

    def _handle_approve_command(self, job_id, payload):
        approve = bool(payload.get("approve"))
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            command = job.get("pending_command") or ""
            job.setdefault("qa_log", []).append(
                {
                    "question": f"[command approval] {command}",
                    "answer": "approved" if approve else "denied",
                    "ts": now_iso(),
                }
            )
            job["pending_command"] = None
            job["question"] = None
            job["status"] = "resuming"
            job["updated_at"] = now_iso()
            session_key = job["session_key"]

        def resume_after_command_decision():
            if approve and command:
                binary = command.split()[0]
                # OpenClaw-free: write the allowlist glob straight to the local
                # exec-approvals JSON (approvals_store), no `openclaw approvals`.
                try:
                    approvals_store.allowlist_add(f"{binary}*", "job-hunter",
                                                  path=EXEC_APPROVALS_FILE)
                except Exception as e:
                    print(f"warn: allowlist add failed: {e}")
                _ensure_job_hunter_ask_off()
                message = f"Approved. Run this exact command now: {command}"
            else:
                message = f"Denied: '{command}'. Do not run it. Find a different approach or ask a different question."
            run_agent_message(session_key, message)

        threading.Thread(target=resume_after_command_decision, daemon=True).start()
        self._send_json({"ok": True})

    def _handle_cancel(self, job_id):
        """Abort in-flight fill/tailor (or stuck/Ready/CAPTCHA) without leaving the queue.

        Stops subprocesses, wipes fill claim/profiles, and bumps fill_gen so stale
        pipeline threads cannot clobber the parked status. Keeps the job in
        In Progress (``resume_ready``), Stuck, or Ready — never demotes to Open.
        """
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            status = job.get("status")
            session_key = job.get("session_key")
            proc = _running_procs.get(session_key) if session_key else None
            proc_alive = proc is not None and proc.poll() is None
            # UI offers Cancel for in-progress, stuck/CAPTCHA, resume_ready, and Ready.
            cancellable = (
                status in IN_PROGRESS_STATUSES | NOTIFY_STATUSES
                | {"resume_ready", "ready_for_review"}
                or proc_alive
            )
            if not cancellable:
                self._send_json(
                    {
                        "error": f"job is not running (status={status})",
                        "status": status,
                    },
                    409,
                )
                return
            origin_status = status
            parked_gen = _bump_job_fill_gen_locked(job)
            _park_job_after_cancel(job, origin_status=origin_status)
            resume_kept = bool(job.get("resume_path"))
        proc = _running_procs.get(session_key) if session_key else None
        _kill_process_tree(proc)
        if session_key:
            abort_gateway_session(session_key)
        try:
            sys.path.insert(0, str(ROOT / "scripts" / "fastfill"))
            from browser_launch import wipe_fill_profiles_for_job

            wiped = wipe_fill_profiles_for_job(job_id)
            if wiped.get("removed"):
                print(f"[fill] wiped profiles on cancel for {job_id}: {wiped['removed']}")
        except Exception as e:
            print(f"warn: fill profile wipe on cancel for {job_id}: {e}")
        try:
            clear_fill_activity(job_id)
        except Exception as e:
            print(f"warn: clear_fill_activity on cancel for {job_id}: {e}")
        try:
            close_job_partyrock_tab(job_id, RESUMES_DIR / job_id)
        except Exception as e:
            print(f"warn: PartyRock tab close on cancel for {job_id}: {e}")
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            cur = job.get("status")
            # Re-park only if a stale pipeline flipped us back to a live status.
            # Never clobber applied / deleted / a newer Start (fill_gen moved).
            if (
                int(job.get("fill_gen") or 0) == parked_gen
                and cur not in ({"applied", "deleted"} | set(LEGACY_SKIP_STATUSES))
                and (cur in IN_PROGRESS_STATUSES or cur == "cancelled")
            ):
                _park_job_after_cancel(job, origin_status=origin_status)
            out_status = job.get("status")
            out_detail = job.get("status_detail")
            resume_kept = bool(job.get("resume_path")) or resume_kept
        self._send_json(
            {
                "ok": True,
                "status": out_status,
                "status_detail": out_detail,
                "resume_kept": resume_kept,
            }
        )

    def _handle_skip(self, job_id, payload=None):
        """Soft-delete a job (Deleted trash) — no Skipped holding pen.

        Optional payload.reason:
          duplicate → merge URLs into winner when possible, delete loser
          contract / easy_apply / not_us / too_senior / dead_link → deleted + reason
          omitted → deleted_reason=user ("Skipped by user")

        Refuses applied / already-deleted so a stale UI cannot clobber them.
        """
        payload = payload or {}
        reason = str(payload.get("reason") or "").strip().lower()
        preferred_dup = str(payload.get("duplicate_of") or "").strip() or None
        deleted_reason, detail = SKIP_REASON_TO_DELETED.get(
            reason, ("user", "Skipped by user from dashboard.")
        )
        unskippable = {"applied", "deleted"} | set(LEGACY_SKIP_STATUSES)
        block_urls = reason != "duplicate"
        merged_into = None
        deleted_id = job_id
        survivor_id = None
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            cur = job.get("status")
            if cur in unskippable:
                self._send_json(
                    {
                        "error": f"cannot skip job in terminal status (status={cur})",
                        "status": cur,
                    },
                    409,
                )
                return
            session_key = job.get("session_key")
            jobs_list = data.get("jobs") or []
            _bump_job_fill_gen_locked(job)

            if reason == "duplicate":
                try:
                    from dedup_jobs import fold_urls_into_winner
                except Exception as e:
                    print(f"warn: fold_urls_into_winner import failed: {e}")
                    fold_urls_into_winner = None  # type: ignore
                pair = _find_duplicate_merge_pair(jobs_list, job, preferred_dup)
                if pair and fold_urls_into_winner is not None:
                    winner, loser = pair
                    fold_urls_into_winner(winner, loser)
                    winner["updated_at"] = now_iso()
                    merge_detail = (
                        f"Duplicate of {winner.get('id')}: URLs merged onto winner; "
                        f"loser soft-deleted."
                    )
                    _bump_job_fill_gen_locked(loser)
                    _mark_job_soft_deleted(
                        loser,
                        deleted_reason="duplicate",
                        status_detail=merge_detail,
                        duplicate_of=winner.get("id"),
                    )
                    # Mirror dedup_jobs.mark_loser_merged for UI "duplicate of X".
                    if winner.get("id"):
                        loser["merged_from"] = winner.get("id")

                    merged_into = winner.get("id")
                    deleted_id = loser.get("id")
                    survivor_id = winner.get("id")
                    session_key = loser.get("session_key")
                    if winner.get("id") == job_id:
                        winner["status_detail"] = (
                            f"Merged duplicate {loser.get('id')} into this job "
                            f"(alternate apply URLs kept)."
                        )
                        detail = winner["status_detail"]
                    else:
                        detail = merge_detail
                    removed_snap = dict(loser)
                    block_urls = False
                else:
                    _mark_job_soft_deleted(
                        job,
                        deleted_reason="duplicate",
                        status_detail=detail,
                    )
                    removed_snap = dict(job)
                    block_urls = True
            else:
                _mark_job_soft_deleted(
                    job,
                    deleted_reason=deleted_reason,
                    status_detail=detail,
                )
                removed_snap = dict(job)

        if session_key:
            proc = _running_procs.get(session_key)
            _kill_process_tree(proc)
            abort_gateway_session(session_key)
        try:
            close_job_partyrock_tab(job_id, RESUMES_DIR / job_id)
        except Exception as e:
            print(f"warn: PartyRock tab close on skip for {job_id}: {e}")
        # Duplicate losers must not tombstone URLs that now live on the winner.
        blocked = []
        if block_urls:
            try:
                blocked = block_deleted_job(removed_snap, keep_tombstone=True)
            except TypeError:
                blocked = block_deleted_job(removed_snap)
            except Exception as e:
                print(f"warn: block_deleted_job on skip for {job_id}: {e}")
        job_removed = deleted_id == job_id
        self._send_json(
            {
                "ok": True,
                "status": "deleted" if job_removed else "discovered",
                "status_detail": detail,
                "deleted_reason": (
                    "duplicate" if reason == "duplicate" else deleted_reason
                ),
                "deleted_id": deleted_id,
                "merged_into": merged_into,
                "survivor_id": survivor_id,
                "blocked_urls": blocked,
            }
        )

    def _handle_restore(self, job_id):
        """Move a deleted (or legacy skipped/cancelled) job back to discovered."""
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            cur = job.get("status")
            restorable = {
                "skipped_manual",
                "skipped_duplicate",
                "skipped_contract",
                "skipped_easy_apply",
                "cancelled",
                "deleted",
            }
            if cur not in restorable:
                self._send_json(
                    {
                        "error": "only deleted (or legacy skipped/cancelled) jobs can be restored",
                        "status": cur,
                    },
                    409,
                )
                return
            was_deleted = cur == "deleted" or cur in LEGACY_SKIP_STATUSES
            job["status"] = "discovered"
            job["status_detail"] = "Restored to open from dashboard."
            job["updated_at"] = now_iso()
            if was_deleted or cur == "deleted":
                job.pop("deleted_at", None)
                job.pop("deleted_reason", None)
            job.pop("duplicate_of", None)
            _append_timeline_locked(
                job,
                _timeline_entry(
                    event="restored",
                    detail=job["status_detail"],
                    at=job["updated_at"],
                ),
            )
            job_snapshot = dict(job)
        if was_deleted:
            try:
                unblock_job(job_snapshot)
            except Exception as e:
                print(f"warn: unblock_job on restore for {job_id}: {e}")
        self._send_json({"ok": True, "status": "discovered"})

    def _handle_mark_submitted(self, job_id):
        """The agent never clicks Submit - it can't know when a real
        submission happens on the actual external site, so this is purely
        a manual action the user takes after they've actually done it."""
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            session_key = job.get("session_key")
            applied_at = now_iso()
            job["status"] = "applied"
            job["status_detail"] = "Marked as applied by user from dashboard."
            _bump_job_fill_gen_locked(job)
            job["applied_at"] = applied_at
            job["updated_at"] = applied_at
            # Phase 5: never leave Applied without a mailing address when the
            # resume city → fixture bank can resolve one (same as backfill).
            if not str(job.get("applied_address") or "").strip():
                resolved = resolve_applied_address_for_job(job)
                if resolved:
                    job["applied_address"] = resolved
            _append_timeline_locked(
                job,
                _timeline_entry(
                    event="applied",
                    detail=job["status_detail"],
                    at=job["updated_at"],
                ),
            )
            company, role = job.get("company"), job.get("title")
        # DASH2-006: kill in-flight fill/tailor like Cancel/Delete so Chromium
        # does not keep running after status is already applied.
        proc = _running_procs.get(session_key) if session_key else None
        _kill_process_tree(proc)
        if session_key:
            abort_gateway_session(session_key)
        _release_fill_job(job_id)
        try:
            clear_fill_activity(job_id)
        except Exception as e:
            print(f"warn: clear_fill_activity on mark-applied for {job_id}: {e}")
        try:
            sys.path.insert(0, str(ROOT / "scripts" / "fastfill"))
            from fill_pause import stop_native_hud

            stop_native_hud()
        except Exception as e:
            print(f"warn: stop native HUD on mark-applied for {job_id}: {e}")
        try:
            sys.path.insert(0, str(ROOT / "scripts" / "fastfill"))
            from browser_launch import wipe_fill_profiles_for_job

            wiped = wipe_fill_profiles_for_job(job_id)
            if wiped.get("removed"):
                print(
                    f"[fill] wiped profiles on mark-applied for {job_id}: {wiped['removed']}"
                )
        except Exception as e:
            print(f"warn: fill profile wipe on mark-applied for {job_id}: {e}")
        if company:
            try:
                cmd = [PYTHON_BIN, str(ROOT / "scripts" / "tracker.py"), "update-status",
                       "--company", company, "--status", "Submitted"]
                if role:
                    # Without --role, tracker.py's update-status matches
                    # every row for this company - real observed impact:
                    # JPMorganChase alone has 12 tracked roles, so marking
                    # ONE of them submitted would have silently flipped
                    # all 12 rows to "Submitted" in the Excel tracker.
                    cmd += ["--role", role]
                subprocess.run(cmd, capture_output=True, timeout=15)
            except Exception as e:
                print(f"warn: failed to update tracker status for {job_id}: {e}")
        # Idempotent cleanup for legacy/manual holds; active tabs for other jobs
        # are tracked separately and left untouched.
        try:
            pr_close = close_job_partyrock_tab(job_id, RESUMES_DIR / job_id)
            if pr_close.get("target_id"):
                print(
                    f"PartyRock tab for {job_id}: {pr_close.get('reason')} "
                    f"target={pr_close.get('target_id')}"
                )
        except Exception as e:
            print(f"warn: PartyRock tab close on mark-applied for {job_id}: {e}")
        self._send_json({"ok": True, "status": "applied"})

    def _handle_resolve_apply(self, job_id, payload=None):
        """Search for a company ATS apply URL when the job is still on LinkedIn.

        High confidence upgrades apply_url (LinkedIn kept on job_url/alts).
        Medium records a candidate without overwriting. Easy Apply / no ATS
        host / Workday-iCIMS stay as-is. Default write=True (user clicked);
        pass ``{"write": false}`` for dry-run.
        """
        payload = payload or {}
        write = True if "write" not in payload else bool(payload.get("write"))
        try:
            import resolve_apply_urls as rau
        except ImportError as e:
            self._send_json({"error": f"resolver unavailable: {e}"}, 500)
            return
        try:
            out = rau.resolve_job_id(str(job_id), write=write)
        except Exception as e:
            self._send_json({"error": str(e)[:300]}, 500)
            return
        if not isinstance(out, dict):
            self._send_json({"error": "resolver returned nothing"}, 500)
            return
        if not out.get("ok"):
            err = str(out.get("error") or "resolve failed")
            code = 404 if "no job found" in err.lower() else 400
            self._send_json(out, code)
            return
        self._send_json(out)

    def _handle_edit_applied(self, job_id, payload):
        try:
            fields = _validated_applied_edit(payload or {})
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        if not fields:
            self._send_json({"error": "no editable fields"}, 400)
            return
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            if job.get("status") != "applied":
                self._send_json({"error": "only applied jobs can be edited here"}, 409)
                return
            job.update(fields)
            job["updated_at"] = now_iso()
            _append_timeline_locked(
                job,
                _timeline_entry(
                    event="applied_edit",
                    detail="Applied job details edited by user.",
                    at=job["updated_at"],
                ),
            )
            response_job = slim_job_for_list(job)
        self._send_json({"ok": True, "job": response_job})

    def _handle_claim_ready_announcement(self, job_id):
        """Grant the spoken 'ready for review' announcement to one client only.

        Every open dashboard tab polls independently and each has its own
        in-page 'already spoken' set, so a single Ready event was announced
        once per client (observed with 11 connected clients). The flag lives
        on the job record, so exactly one client wins the claim regardless of
        how many tabs are open or whether a page was reloaded. ``_patch_job``
        clears it when the job leaves Ready, so a genuinely new Ready event
        is announced again — once.
        """
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            if job.get("status") != "ready_for_review":
                self._send_json({"speak": False, "reason": "not ready"})
                return
            if job.get("ready_announced"):
                self._send_json({"speak": False, "reason": "already announced"})
                return
            job["ready_announced"] = True
        self._send_json({"speak": True})

    def _handle_resume_upload(self, job_id: str, content_length: int) -> None:
        """Store a user-uploaded resume under resumes/<job_id>/ and set resume_path."""
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype.lower():
            self._send_json({"error": "expected multipart/form-data"}, 400)
            return
        if content_length <= 0 or content_length > 25 * 1024 * 1024:
            self._send_json({"error": "invalid or too-large upload"}, 400)
            return
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            if job.get("status") in IN_PROGRESS_STATUSES:
                self._send_json(
                    {
                        "error": (
                            "resume upload blocked while fill/tailor is in progress "
                            "(UI-007). Cancel the run first."
                        ),
                    },
                    409,
                )
                return
        try:
            body = self.rfile.read(content_length)
            filename, file_bytes = _parse_multipart_file(body, ctype)
        except Exception as e:
            self._send_json({"error": f"multipart parse failed: {e}"[:200]}, 400)
            return
        if not file_bytes:
            self._send_json({"error": "empty file"}, 400)
            return
        ext = Path(filename or "resume.pdf").suffix.lower()
        if ext not in (".pdf", ".doc", ".docx"):
            self._send_json(
                {"error": "only .pdf / .doc / .docx resumes are accepted"},
                400,
            )
            return
        # Prefer resume.pdf so Start/Retry / View Resume share one path.
        dest_name = "resume.pdf" if ext == ".pdf" else f"uploaded_resume{ext}"
        job_dir = RESUMES_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        dest = job_dir / dest_name
        dest.write_bytes(file_bytes)
        try:
            rel = str(dest.relative_to(ROOT))
        except ValueError:
            rel = str(dest)
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            if job.get("status") in IN_PROGRESS_STATUSES:
                self._send_json(
                    {
                        "error": (
                            "resume upload blocked while fill/tailor is in progress "
                            "(UI-007). Cancel the run first."
                        ),
                    },
                    409,
                )
                return
            job["resume_path"] = rel
            sync_job_resume_on_disk(job)
            job["status_detail"] = f"Resume uploaded ({dest_name})."
            job["updated_at"] = now_iso()
            published = None
            if dest.suffix.lower() == ".pdf":
                published = _publish_resume_by_company(job, dest, data)
        detail = f"Uploaded resume → {rel}"
        if published is not None:
            detail += f"; by_company → {published.name}"
        append_fill_activity(
            job_id,
            event="resume",
            detail=detail,
            persist=True,
        )
        payload = {
            "ok": True,
            "resume_path": rel,
            "filename": dest_name,
            "bytes": len(file_bytes),
        }
        if published is not None:
            payload["resume_by_company_path"] = job.get("resume_by_company_path")
        self._send_json(payload)

    def _handle_resume_latex_save(self, job_id: str, payload: dict) -> None:
        """Compile pasted/edited LaTeX, fit to two pages, and set job resume."""
        latex_source = payload.get("latex")
        if not isinstance(latex_source, str):
            self._send_json({"error": "latex must be a string"}, 400)
            return
        if not latex_source.strip():
            self._send_json({"error": "LaTeX source is empty"}, 400)
            return
        if len(latex_source) > RESUME_LATEX_MAX_CHARS:
            self._send_json({"error": "LaTeX source is too large (1 MB max)"}, 413)
            return
        if "\\begin{document}" not in latex_source or "\\end{document}" not in latex_source:
            self._send_json(
                {"error": "LaTeX must include \\begin{document} and \\end{document}"},
                400,
            )
            return
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            if job.get("status") in IN_PROGRESS_STATUSES:
                self._send_json(
                    {"error": "resume editing is blocked while fill/tailor is running. Cancel first."},
                    409,
                )
                return

        job_dir = RESUMES_DIR / job_id
        result = _compile_resume_latex(job_dir, latex_source)
        if not result.get("ok"):
            self._send_json({"error": result.get("error") or "resume compile failed"}, 422)
            return

        resume_pdf = job_dir / "resume.pdf"
        rel = str(resume_pdf.relative_to(ROOT))
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            if job.get("status") in IN_PROGRESS_STATUSES:
                self._send_json(
                    {
                        "error": (
                            "resume compiled, but the job started running before it could "
                            "be selected. Cancel the run, then save again."
                        )
                    },
                    409,
                )
                return
            job["resume_path"] = rel
            sync_job_resume_on_disk(job)
            job["status_detail"] = "LaTeX resume fitted, compiled, and saved."
            job["updated_at"] = now_iso()
            published = _publish_resume_by_company(job, resume_pdf, data)
            response_job = slim_job_for_list(job)
        append_fill_activity(
            job_id,
            event="resume",
            detail=f"LaTeX resume fitted and compiled → {rel}",
            persist=True,
        )
        payload = {
            "ok": True,
            "resume_path": rel,
            "resume_on_disk": True,
            "latex": (job_dir / "resume.tex").read_text(encoding="utf-8"),
            "resume_by_company_path": (
                response_job.get("resume_by_company_path") if published is not None else None
            ),
            "job": response_job,
        }
        if result.get("warning"):
            payload["warning"] = result["warning"]
        self._send_json(payload)

    def _handle_resume_clear(self, job_id: str) -> None:
        """Clear job.resume_path (and local resumes/<id> files) for dossier Clear."""
        cleared = []
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            if job.get("status") in IN_PROGRESS_STATUSES:
                self._send_json(
                    {
                        "error": (
                            "resume clear blocked while fill/tailor is in progress "
                            "(UI-007). Cancel the run first."
                        ),
                    },
                    409,
                )
                return
            job["resume_path"] = None
            job["resume_on_disk"] = False
            job.pop("resume_by_company_path", None)
            job["status_detail"] = "Resume cleared."
            job["updated_at"] = now_iso()
        job_dir = RESUMES_DIR / job_id
        for name in (
            "resume.tex",
            "resume.pdf",
            "uploaded_resume.pdf",
            "uploaded_resume.doc",
            "uploaded_resume.docx",
        ):
            p = job_dir / name
            try:
                if p.is_file():
                    p.unlink()
                    cleared.append(str(p.relative_to(ROOT)))
            except OSError as e:
                print(f"warn: resume clear unlink failed {p}: {e}")
        append_fill_activity(
            job_id,
            event="resume",
            detail="Cleared resume on file" + (f" ({', '.join(cleared)})" if cleared else ""),
        )
        self._send_json({"ok": True, "cleared": cleared})

    def _handle_add_job(self, payload):
        url = (payload.get("url") or "").strip()
        if not url or not url.lower().startswith(("http://", "https://")):
            self._send_json({"error": "a valid http(s) url is required"}, 400)
            return
        if is_url_blocked(url):
            self._send_json({
                "error": "this URL was deleted earlier and is blocked from re-adding",
            }, 409)
            return
        with _lock:
            with locked_jobs_for_write() as data:
                norm = normalize_url(url) or url
                for job in data["jobs"]:
                    for f in ("apply_url", "job_url"):
                        existing = job.get(f)
                        if not existing:
                            continue
                        if existing == url or (normalize_url(existing) or existing) == norm:
                            self._send_json({"error": "a job with this URL already exists", "id": job["id"]}, 409)
                            return
                slug_base = re.sub(r"[^a-z0-9]+", "-", urlparse(url).netloc.lower()).strip("-") or "manual"
                job_id = f"{slug_base}-{int(time.time())}"
                job = {
                    "id": job_id,
                    "company": "",
                    "title": "",
                    "location": "",
                    "source": "manual",
                    "date_posted": None,
                    "job_url": url,
                    "apply_url": url,
                    "job_description": "",
                    "status": "discovered",
                    "status_detail": "Added manually via dashboard - fetching job details...",
                    "question": None,
                    "pending_command": None,
                    "session_key": f"agent:job-hunter:job-{job_id}",
                    "resume_path": None,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                    "qa_log": [],
                    "timeline": [],
                }
                _append_timeline_locked(
                    job,
                    _timeline_entry(
                        event="added",
                        detail=job["status_detail"],
                        at=job["created_at"],
                    ),
                )
                data["jobs"].append(job)
        threading.Thread(target=_try_extract_manual_job_details, args=(job_id, url), daemon=True).start()
        self._send_json({"ok": True, "id": job_id})

    def _handle_profile_update(self, payload):
        if not isinstance(payload, dict):
            self._send_json({"error": "expected a JSON object"}, 400)
            return
        PROFILE_FILE.write_text(json.dumps(payload, indent=2))
        self._send_json({"ok": True})

    def _handle_discover(self, payload=None):
        # Only block a duplicate discovery run - a job actively applying
        # no longer needs to block this (jobs.json writes are properly
        # file-locked across processes now, see scripts/jobs_lock.py).
        payload = payload if isinstance(payload, dict) else {}
        parsed = _parse_enabled_sources(payload)
        enabled = set(DISCOVERY_SOURCE_IDS) if parsed is None else parsed
        # Persist any region toggles / Built In days included in the POST so a
        # discovery kicked off from the popover uses the just-picked regions.
        settings_patch = {
            k: payload[k]
            for k in ("builtin_days_since_updated", "discover_us", "discover_india")
            if k in payload
        }
        if settings_patch:
            try:
                save_discovery_settings(settings_patch)
            except ValueError as e:
                self._send_json(
                    {"error": str(e), "discovery": discovery_status()},
                    400,
                )
                return
        # Gate India-only sources: they never run unless the India region is on.
        regions = enabled_discovery_regions()
        if "india" not in regions:
            enabled = {sid for sid in enabled if sid not in INDIA_ONLY_SOURCE_IDS}
        if not enabled:
            self._send_json(
                {"error": "enable at least one discovery source", "discovery": discovery_status()},
                400,
            )
            return
        # Default: always continue from checkpoint. Explicit fresh=true
        # clears leftover progress and starts a new incremental pass.
        fresh = bool(payload.get("fresh") or payload.get("force_fresh"))
        if is_session_running(DISCOVERY_SESSION_KEY) or not _begin_discovery(enabled, fresh=fresh):
            self._send_json({"error": "discovery is already running", "discovery": discovery_status()}, 409)
            return
        threading.Thread(target=run_scout_scrape_then_dedup, daemon=True).start()
        self._send_json({
            "ok": True,
            "started": True,
            "resumed": bool(discovery_status().get("resumed")),
            "fresh": fresh,
            "discovery": discovery_status(),
        })

    def _handle_discover_settings(self, payload=None):
        try:
            settings = save_discovery_settings(
                payload if isinstance(payload, dict) else {}
            )
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        self._send_json({"ok": True, **settings})

    def _handle_discover_abort(self, payload=None):
        payload = payload if isinstance(payload, dict) else {}
        source_id = payload.get("source_id") or payload.get("source")
        if payload.get("all") or (isinstance(source_id, str) and source_id.strip().lower() == "all"):
            source_id = None
        elif source_id is not None:
            source_id = str(source_id).strip() or None
        body, code = request_discovery_abort(source_id)
        self._send_json(body, code)

    def _handle_prune(self, payload=None):
        payload = payload if isinstance(payload, dict) else {}
        try:
            reasons = _normalize_prune_reasons(payload.get("reasons"))
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        moved = _auto_delete_sweep_once(set(reasons))
        self._send_json({"ok": True, "moved": moved, "reasons": reasons})

    def _handle_prune_settings(self, payload=None):
        try:
            settings = save_prune_settings(payload if isinstance(payload, dict) else {})
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        self._send_json({"ok": True, **settings})

    def _handle_empty_deleted(self):
        """Hard-purge status=deleted jobs; keep URL tombstones for dedup.

        Soft-delete already wrote tombstones when each job was deleted.
        Re-calling block_deleted_job once per row rewrote blocked_urls.json
        thousands of times (~100s+), held _lock the whole time, and made the
        Empty Deleted request look like a no-op (UI never refreshed).
        Batch-ensure tombstones once outside the lock, then purge rows.
        """
        with _lock:
            data = read_jobs()
            deleted = [j for j in (data.get("jobs") or []) if j.get("status") == "deleted"]
        blocked_n = 0
        try:
            keys = block_deleted_jobs_batch(deleted, keep_tombstone=True)
            blocked_n = len(keys or [])
        except TypeError:
            for j in deleted:
                try:
                    blocked_n += len(block_deleted_job(j, keep_tombstone=True) or [])
                except TypeError:
                    blocked_n += len(block_deleted_job(j) or [])
                except Exception as e:
                    print(f"warn: empty-deleted block failed for {j.get('id')}: {e}")
        except Exception as e:
            print(f"warn: empty-deleted batch block failed: {e}")
        with _lock:
            with locked_jobs_for_write(allow_purge=True) as data:
                before = len(data.get("jobs") or [])
                data["jobs"] = [
                    j for j in (data.get("jobs") or []) if j.get("status") != "deleted"
                ]
                purged = before - len(data["jobs"])
        self._send_json({"ok": True, "purged": purged, "blocked_keys": blocked_n})

    def _handle_start(self, job_id, payload=None):
        # Unlocked read first - read_jobs() has its own internal file lock
        # for safe reads, no need for the broader in-process _lock here.
        payload = payload or {}
        try:
            test_mode = _parse_test_mode(payload)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        force_partyrock = False
        if "force_partyrock" in payload:
            raw = payload.get("force_partyrock")
            if isinstance(raw, bool):
                force_partyrock = raw
            elif isinstance(raw, (int, float)):
                force_partyrock = bool(raw)
            else:
                force_partyrock = str(raw).strip().lower() not in (
                    "0", "false", "no", "off", "",
                )
        resume_only = _parse_resume_only(payload)
        # Generate-resume-only always re-runs PartyRock even if a PDF exists,
        # then stops before fill.
        if resume_only:
            force_partyrock = True
        job = self._job(read_jobs(), job_id)
        if job is None:
            self._send_json({"error": "not found"}, 404)
            return
        # Skip PartyRock when resume already on disk (upload or prior tailor),
        # or when Test Mode toggle disables PartyRock — unless Tailor + fill
        # explicitly forces PartyRock regeneration.
        has_resume = resolve_job_resume_file(job) is not None
        if force_partyrock:
            skip_partyrock = False
        else:
            skip_partyrock = has_resume or (
                bool(test_mode) and _parse_skip_partyrock(payload)
            )
        pr_url = None if skip_partyrock else partyrock_url(test_mode=test_mode)
        pr_mode = (
            "bypassed"
            if skip_partyrock
            else partyrock_mode_label(test_mode=test_mode)
        )
        # Only block re-starting THIS job while it's already running - other
        # jobs and discovery no longer block Start at all. Uses the fast
        # local-only check (no gateway subprocess) so clicking Start →
        # tailoring isn't delayed by an ~15s `openclaw sessions list` round
        # trip. The in-lock status claim below (IN_PROGRESS_STATUSES) is the
        # authoritative double-Start guard, including for gateway agent turns.
        if _session_running_local(job["session_key"]):
            self._send_json({"error": "this job is already running"}, 409)
            return
        with self._locked_job(job_id) as (data, job):
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            # Status claim closes the race vs Fast fill (dummy): both paths
            # register _running_procs only after Popen, so a double-click
            # could otherwise start tailor + dummy fill on the same job.
            if job.get("status") in IN_PROGRESS_STATUSES:
                self._send_json({"error": "this job is already running"}, 409)
                return
            # UI-002: refuse second Start on same job while Ready/CAPTCHA hold
            # browser is still live.
            if (
                job.get("status") in _HOLD_BLOCK_STATUSES
                and _fill_hold_browser_active()
            ):
                self._send_json(
                    {
                        "error": (
                            "this job is still held for review/CAPTCHA — "
                            "Mark as applied or close the fill browser before "
                            "starting again"
                        ),
                    },
                    409,
                )
                return
            # Capture before claiming navigating/tailoring so Test Mode fill
            # can restore stuck / blocked_captcha instead of always discovered.
            prior_restore = _dummy_restore_status(job.get("status") or "discovered")
            if skip_partyrock:
                job["status"] = "navigating"
                sync_job_resume_on_disk(job)
                resume_on_disk = resolve_job_resume_file(job)
                if resume_on_disk is not None:
                    display_name = (
                        conventional_resume_filename(job) or "resume on disk"
                    )
                    job["status_detail"] = (
                        f"{_fill_mode_prefix(test_mode)} PartyRock bypassed "
                        f"(resume on disk: {display_name}) — fill only."
                    )
                elif test_mode:
                    job["status_detail"] = (
                        "[DUMMY/TEST] PartyRock bypassed (test mode) — "
                        "dummy fixture + PartyRock skipped; starting fast_fill."
                    )
                else:
                    job["status_detail"] = (
                        f"{_fill_mode_prefix(test_mode)} PartyRock skipped — "
                        "starting fast_fill."
                    )
            else:
                job["status"] = "tailoring"
                sync_job_resume_on_disk(job)
                if resume_only:
                    job["status_detail"] = (
                        f"Started by user from dashboard. Generate resume only via "
                        f"PartyRock ({pr_mode}): {pr_url} — fill will not start."
                    )
                else:
                    job["status_detail"] = (
                        f"Started by user from dashboard. Tailoring resume via PartyRock "
                        f"({pr_mode}): {pr_url}"
                    )
            fill_run_gen = _bump_job_fill_gen_locked(job)
            job["updated_at"] = now_iso()
            _append_timeline_locked(
                job,
                _timeline_entry(
                    event=job["status"],
                    detail=job["status_detail"],
                    at=job["updated_at"],
                ),
            )
        clear_fill_activity(job_id)
        if not skip_partyrock:
            # PartyRock will be used → warm the CDP browser now, concurrently,
            # so it's ready by the time tailor_resume.py connects instead of
            # cold-starting sequentially right before tailoring.
            _prewarm_openclaw_browser_async()
        if skip_partyrock:
            start_detail = (
                "Start queued (dummy fixture / PartyRock skipped)."
                if test_mode
                else (
                    "Start queued (on-disk resume — PartyRock skipped)."
                    if has_resume
                    else "Start queued (PartyRock skipped) — straight to fast_fill."
                )
            )
        elif resume_only:
            start_detail = (
                f"Start queued ({pr_mode}) — generate resume only (no fill)."
            )
        else:
            start_detail = (
                f"Start queued ({pr_mode}). "
                f"{'After PartyRock → fast_fill dummy.' if test_mode else 'After PartyRock → fast_fill real.'}"
            )
        append_fill_activity(job_id, event="start", detail=start_detail, persist=True)
        threading.Thread(
            target=run_tailor_then_fill,
            args=(job_id,),
            kwargs={
                "test_mode": test_mode,
                "skip_partyrock": skip_partyrock,
                "force_partyrock": force_partyrock,
                "restore_status": prior_restore,
                "fill_options": payload,
                "fill_run_gen": fill_run_gen,
                "resume_only": resume_only,
            },
            daemon=True,
        ).start()
        self._send_json({
            "ok": True,
            "test_mode": test_mode,
            "skip_partyrock": skip_partyrock,
            "force_partyrock": force_partyrock,
            "resume_on_disk": has_resume,
            "resume_only": resume_only,
            "partyrock": not skip_partyrock,
            "partyrock_mode": pr_mode,
            "partyrock_url": pr_url,
            "fill_after_tailor": (
                "none"
                if resume_only
                else (
                    "fast_fill_skip_tailor"
                    if skip_partyrock
                    else ("fast_fill_dummy" if test_mode else "fast_fill_real")
                )
            ),
        })

    def _handle_cron_toggle(self, payload):
        enable = bool(payload.get("enable"))
        try:
            # OpenClaw-free: persist to local scheduler settings; the in-process
            # DiscoveryScheduler re-reads them each tick, so this takes effect
            # without a restart.
            scheduler_mod.write_settings(enabled=enable)
            updated = _find_cron_job()
            self._send_json({"ok": True, "enabled": enable, "job": _cron_job_public(updated)})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_cron_schedule(self, payload):
        """Update job-hunter-daily cron wall-clock time (keeps daily * * *)."""
        payload = payload or {}
        hour = payload.get("hour")
        minute = payload.get("minute")
        time_raw = payload.get("time")
        if time_raw is not None and (hour is None or minute is None):
            try:
                parts = str(time_raw).strip().split(":")
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
            except (ValueError, IndexError):
                self._send_json({"error": "time must be HH:MM"}, 400)
                return
        try:
            hour = int(hour if hour is not None else 9)
            minute = int(minute if minute is not None else 0)
        except (TypeError, ValueError):
            self._send_json({"error": "hour/minute must be integers"}, 400)
            return
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            self._send_json({"error": "hour 0-23, minute 0-59"}, 400)
            return
        expr = f"{minute} {hour} * * *"
        try:
            # OpenClaw-free: persist the new wall-clock time to local scheduler
            # settings (keeps the daily cadence). The scheduler picks it up.
            scheduler_mod.write_settings(hour=hour, minute=minute)
            updated = _find_cron_job()
            self._send_json({
                "ok": True,
                "expr": expr,
                "hour": hour,
                "minute": minute,
                "time": f"{hour:02d}:{minute:02d}",
                "job": _cron_job_public(updated),
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)


def _backfill_company_key_loop() -> None:
    """One-shot: stamp missing/stale company_key from display company.

    Does not rewrite the raw company string. Uses a single jobs.json write
    lock (no nested locked_jobs_for_write).
    """
    try:
        changed = 0
        with _lock:
            with locked_jobs_for_write() as data:
                changed = backfill_company_keys(data)
        if changed:
            print(f"company_key backfill: changed={changed}")
    except Exception as e:
        print(f"warn: company_key backfill failed: {e}")


def _backfill_missing_jds_loop() -> None:
    """One-shot: fetch missing JDs for SmartRecruiters/Breezy/Rippling/Lever.

    Skips when logs/missing_jd_backfill_v1.done exists. Runs in a daemon
    thread so startup is not blocked; network work can take several minutes.
    """
    marker = ROOT / "logs" / "missing_jd_backfill_v1.done"
    if marker.exists():
        return
    script = ROOT / "scripts" / "backfill_missing_jds.py"
    if not script.is_file():
        return
    try:
        import subprocess

        py = ROOT / ".venv" / "bin" / "python"
        cmd = [str(py if py.is_file() else sys.executable), str(script)]
        print("missing JD backfill: starting background run…")
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60 * 45,
        )
        if proc.stdout:
            print(proc.stdout[-2000:])
        if proc.returncode != 0:
            print(
                f"warn: missing JD backfill exited {proc.returncode}: "
                f"{(proc.stderr or '')[-500:]}"
            )
        else:
            print("missing JD backfill: finished")
    except Exception as e:
        print(f"warn: missing JD backfill failed: {e}")


def _backfill_multi_opening_loop() -> None:
    """One-shot: mark multi_opening on jobs missing the flag (reads jd_full).

    Runs in a daemon thread so list sort works for existing jobs without
    blocking dashboard startup. Skips jobs that already have a boolean flag.
    """
    try:
        from multi_opening import backfill_multi_opening_flags
    except Exception as e:
        print(f"warn: multi_opening import failed: {e}")
        return
    try:
        changed = 0
        true_count = 0
        with _lock:
            with locked_jobs_for_write() as data:
                needs = any(
                    not isinstance(j.get("multi_opening"), bool)
                    for j in (data.get("jobs") or [])
                )
                if not needs:
                    return
                changed, true_count = backfill_multi_opening_flags(
                    data, only_missing=True
                )
        if changed:
            print(
                f"multi_opening backfill: changed={changed} "
                f"true={true_count}"
            )
    except Exception as e:
        print(f"warn: multi_opening backfill failed: {e}")


def _job_desc_for_backfill(job: dict) -> str:
    """Prefer resumes/<id>/jd_full.txt (canonical), then .md, then preview."""
    job_id = str(job.get("id") or "")
    for name in ("jd_full.txt", "jd_full.md"):
        jd_path = RESUMES_DIR / job_id / name
        if jd_path.is_file():
            try:
                full = jd_path.read_text(encoding="utf-8", errors="replace")
                if full.strip():
                    return full
            except OSError:
                pass
    return job.get("job_description") or ""


def _has_jd_full_for_backfill(job: dict) -> bool:
    job_id = str(job.get("id") or "")
    if not job_id:
        return False
    return any(
        (RESUMES_DIR / job_id / name).is_file()
        for name in ("jd_full.txt", "jd_full.md")
    )


# Bump when detection / full-JD backfill logic changes so undetermined
# stamps get one more pass. Marker lives under logs/.
_YOE_WM_BACKFILL_MARKER = ROOT / "logs" / "yoe_wm_full_jd_backfill_v2.done"
# Tier-2 display fallbacks (min_yoe_fallback / work_mode_fallback) — separate
# marker so the strict v2 pass is not re-run.
_YOE_WM_FALLBACK_MARKER = ROOT / "logs" / "yoe_wm_fallback_v3.done"
# Salary stamps (display only). Marker under logs/.
_SALARY_BACKFILL_MARKER = ROOT / "logs" / "salary_full_jd_backfill_v1.done"


def _backfill_salary_loop() -> None:
    """Stamp salary_min/max (+ fallbacks) from full JD. Display only — never prune."""
    try:
        from discovery_filters import extract_salary, extract_salary_fallback
    except Exception as e:
        print(f"warn: discovery_filters import failed (salary): {e}")
        return
    try:
        force = not _SALARY_BACKFILL_MARKER.exists()
        changed = 0
        strict_fixed = 0
        fb_fixed = 0
        with _lock:
            with locked_jobs_for_write() as data:
                jobs = data.get("jobs") or []
                needs = force or any(
                    "salary_min" not in j
                    or "salary_max" not in j
                    or "salary_min_fallback" not in j
                    or "salary_max_fallback" not in j
                    for j in jobs
                )
                if not needs:
                    return
                for job in jobs:
                    missing = (
                        "salary_min" not in job
                        or "salary_max" not in job
                        or "salary_min_fallback" not in job
                        or "salary_max_fallback" not in job
                    )
                    undetermined = job.get("salary_min") is None
                    refresh = force and undetermined and _has_jd_full_for_backfill(job)
                    if not missing and not refresh:
                        continue
                    title = job.get("title") or ""
                    desc = _job_desc_for_backfill(job)
                    sal = extract_salary(title=title, description=desc)
                    sal_fb = (
                        extract_salary_fallback(title=title, description=desc)
                        if sal is None
                        else None
                    )
                    new_min = (sal or {}).get("min")
                    new_max = (sal or {}).get("max")
                    new_fb_min = (sal_fb or {}).get("min")
                    new_fb_max = (sal_fb or {}).get("max")
                    prev = (
                        job.get("salary_min"),
                        job.get("salary_max"),
                        job.get("salary_min_fallback"),
                        job.get("salary_max_fallback"),
                    )
                    job["salary_min"] = new_min
                    job["salary_max"] = new_max
                    job["salary_min_fallback"] = new_fb_min
                    job["salary_max_fallback"] = new_fb_max
                    cur = (new_min, new_max, new_fb_min, new_fb_max)
                    if cur != prev or missing:
                        changed += 1
                        if new_min is not None and prev[0] != new_min:
                            strict_fixed += 1
                        if new_fb_min is not None and prev[2] != new_fb_min:
                            fb_fixed += 1
        if force:
            try:
                _SALARY_BACKFILL_MARKER.parent.mkdir(parents=True, exist_ok=True)
                _SALARY_BACKFILL_MARKER.write_text(
                    f"strict_fixed={strict_fixed} fb_fixed={fb_fixed} "
                    f"fields_touched={changed}\n",
                    encoding="utf-8",
                )
            except OSError as e:
                print(f"warn: could not write salary backfill marker: {e}")
        if changed:
            print(
                f"salary backfill: fields_set={changed} "
                f"strict_fixed={strict_fixed} fb_fixed={fb_fixed}"
            )
    except Exception as e:
        print(f"warn: salary backfill failed: {e}")


def _backfill_yoe_work_mode_loop() -> None:
    """Stamp min_yoe / work_mode (strict) and display fallbacks from full JD.

    v1 only filled missing keys and read jd_full.md (files are .txt), so many
    jobs kept unknown/null from the truncated jobs.json preview. v2 marker
    forces one recompute pass for undetermined fields when jd_full exists.
    Fallback marker stamps min_yoe_fallback / work_mode_fallback for UI ``~``
    labels without changing prune/excessive strict YOE.
    """
    try:
        from discovery_filters import (
            detect_work_mode,
            detect_work_mode_fallback,
            extract_min_required_yoe,
            extract_min_required_yoe_fallback,
        )
    except Exception as e:
        print(f"warn: discovery_filters import failed: {e}")
        return
    try:
        changed = 0
        mode_fixed = 0
        yoe_fixed = 0
        force_undetermined = not _YOE_WM_BACKFILL_MARKER.exists()
        force_fallback = not _YOE_WM_FALLBACK_MARKER.exists()
        fb_yoe_fixed = 0
        fb_mode_fixed = 0
        fb_changed = 0
        with _lock:
            with locked_jobs_for_write() as data:
                jobs = data.get("jobs") or []
                needs_strict = any(
                    "min_yoe" not in j
                    or "work_mode" not in j
                    or (
                        force_undetermined
                        and _has_jd_full_for_backfill(j)
                        and (
                            j.get("work_mode") in (None, "unknown")
                            or j.get("min_yoe") is None
                        )
                    )
                    for j in jobs
                )
                needs_fallback = force_fallback or any(
                    "min_yoe_fallback" not in j or "work_mode_fallback" not in j
                    for j in jobs
                )
                if not needs_strict and not force_undetermined and not needs_fallback:
                    return
                for job in jobs:
                    title = job.get("title") or ""
                    location = job.get("location") or ""
                    missing_yoe = "min_yoe" not in job
                    missing_mode = "work_mode" not in job
                    undetermined_mode = job.get("work_mode") in (None, "unknown")
                    undetermined_yoe = job.get("min_yoe") is None
                    refresh = force_undetermined and _has_jd_full_for_backfill(job)
                    want_strict = (
                        missing_yoe
                        or missing_mode
                        or (refresh and (undetermined_mode or undetermined_yoe))
                    )
                    missing_yoe_fb = "min_yoe_fallback" not in job
                    missing_mode_fb = "work_mode_fallback" not in job
                    want_fallback = force_fallback or missing_yoe_fb or missing_mode_fb
                    if not want_strict and not want_fallback:
                        continue
                    desc = _job_desc_for_backfill(job)
                    if want_strict:
                        if missing_mode or (refresh and undetermined_mode):
                            mode = detect_work_mode(
                                title=title, location=location, description=desc
                            )
                            prev = job.get("work_mode")
                            if missing_mode or mode != "unknown" or prev != mode:
                                if mode != prev:
                                    if mode != "unknown":
                                        mode_fixed += 1
                                    changed += 1
                                job["work_mode"] = mode
                        if missing_yoe or (refresh and undetermined_yoe):
                            yoe = extract_min_required_yoe(
                                title=title, description=desc
                            )
                            prev_y = job.get("min_yoe")
                            if missing_yoe or yoe is not None:
                                if yoe != prev_y:
                                    if yoe is not None:
                                        yoe_fixed += 1
                                    changed += 1
                                job["min_yoe"] = yoe
                    if want_fallback:
                        # Re-read after possible strict stamps in this same pass.
                        strict_yoe = job.get("min_yoe")
                        strict_mode = job.get("work_mode")
                        yoe_fb = None
                        if strict_yoe is None:
                            yoe_fb = extract_min_required_yoe_fallback(
                                title=title, description=desc
                            )
                        prev_yfb = job.get("min_yoe_fallback")
                        if missing_yoe_fb or force_fallback or yoe_fb != prev_yfb:
                            if yoe_fb != prev_yfb:
                                if yoe_fb is not None:
                                    fb_yoe_fixed += 1
                                fb_changed += 1
                            job["min_yoe_fallback"] = yoe_fb
                        mode_fb = None
                        if strict_mode in (None, "unknown"):
                            wm = detect_work_mode_fallback(
                                title=title, location=location, description=desc
                            )
                            if wm != "unknown":
                                mode_fb = wm
                        prev_mfb = job.get("work_mode_fallback")
                        if missing_mode_fb or force_fallback or mode_fb != prev_mfb:
                            if mode_fb != prev_mfb:
                                if mode_fb is not None:
                                    fb_mode_fixed += 1
                                fb_changed += 1
                            job["work_mode_fallback"] = mode_fb
        if force_undetermined:
            try:
                _YOE_WM_BACKFILL_MARKER.parent.mkdir(parents=True, exist_ok=True)
                _YOE_WM_BACKFILL_MARKER.write_text(
                    f"mode_fixed={mode_fixed} yoe_fixed={yoe_fixed} "
                    f"fields_touched={changed}\n",
                    encoding="utf-8",
                )
            except OSError as e:
                print(f"warn: could not write yoe/wm backfill marker: {e}")
        if force_fallback:
            try:
                _YOE_WM_FALLBACK_MARKER.parent.mkdir(parents=True, exist_ok=True)
                _YOE_WM_FALLBACK_MARKER.write_text(
                    f"mode_fb_fixed={fb_mode_fixed} yoe_fb_fixed={fb_yoe_fixed} "
                    f"fields_touched={fb_changed}\n",
                    encoding="utf-8",
                )
            except OSError as e:
                print(f"warn: could not write yoe/wm fallback marker: {e}")
        if changed:
            print(
                f"min_yoe/work_mode backfill: fields_set={changed} "
                f"mode_fixed={mode_fixed} yoe_fixed={yoe_fixed}"
            )
        if fb_changed:
            print(
                f"min_yoe_fallback/work_mode_fallback backfill: "
                f"fields_set={fb_changed} mode_fb_fixed={fb_mode_fixed} "
                f"yoe_fb_fixed={fb_yoe_fixed}"
            )
    except Exception as e:
        print(f"warn: min_yoe/work_mode backfill failed: {e}")


def _normalize_prune_reasons(raw) -> list[str]:
    if raw is None:
        return list(PRUNE_REASON_CODES)
    if not isinstance(raw, (list, tuple, set)):
        raise ValueError("reasons must be a list")
    requested = {str(reason).strip() for reason in raw}
    unknown = requested.difference(PRUNE_REASON_CODES)
    if unknown:
        raise ValueError(f"unknown prune reason: {sorted(unknown)[0]}")
    return [reason for reason in PRUNE_REASON_CODES if reason in requested]


def load_prune_settings() -> dict:
    defaults = {
        "interval_s": AUTO_DELETE_SWEEP_INTERVAL_S,
        "reasons": list(PRUNE_REASON_CODES),
    }
    with _prune_settings_lock:
        try:
            raw = json.loads(PRUNE_SETTINGS_FILE.read_text())
        except (OSError, json.JSONDecodeError, TypeError):
            return defaults
    try:
        interval_s = int(raw.get("interval_s", defaults["interval_s"]))
        if interval_s not in PRUNE_INTERVALS_S:
            interval_s = defaults["interval_s"]
        reasons = _normalize_prune_reasons(raw.get("reasons"))
    except (AttributeError, TypeError, ValueError):
        return defaults
    return {"interval_s": interval_s, "reasons": reasons}


def save_prune_settings(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    try:
        interval_s = int(payload.get("interval_s"))
    except (TypeError, ValueError):
        raise ValueError("interval_s must be one of the supported intervals")
    if interval_s not in PRUNE_INTERVALS_S:
        raise ValueError("interval_s must be one of 0, 300, 900, 3600, 86400")
    reasons = _normalize_prune_reasons(payload.get("reasons"))
    settings = {"interval_s": interval_s, "reasons": reasons}
    with _prune_settings_lock:
        PRUNE_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PRUNE_SETTINGS_FILE.with_suffix(PRUNE_SETTINGS_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(settings, indent=2) + "\n")
        tmp.replace(PRUNE_SETTINGS_FILE)
    _prune_schedule_wakeup.set()
    return settings


def _auto_delete_sweep_once(reasons: set[str] | None = None) -> int:
    """Move untouched discovered jobs that match prune rules into Deleted."""
    enabled_reasons = set(PRUNE_REASON_CODES if reasons is None else reasons)
    if not enabled_reasons:
        return 0
    try:
        from discovery_filters import auto_delete_reason
    except Exception as e:
        print(f"warn: auto_delete_reason import failed: {e}")
        return 0
    try:
        moved = 0
        to_block: list[dict] = []
        with _lock:
            with locked_jobs_for_write() as data:
                now_dt = datetime.now(timezone.utc)
                now = now_dt.isoformat()
                regions = enabled_discovery_regions()
                for job in data.get("jobs") or []:
                    if job.get("status") != "discovered":
                        continue
                    reason = auto_delete_reason(
                        title=job.get("title"),
                        location=job.get("location"),
                        company=job.get("company"),
                        description=_job_desc_for_backfill(job),
                        url=job.get("apply_url") or job.get("job_url"),
                        regions=regions,
                    )
                    if reason not in enabled_reasons:
                        reason = None
                    if not reason and "stale_listing" in enabled_reasons:
                        # Exact date_posted, or created_at when undated.
                        # date_posted_fallback (~) must never trigger prune.
                        posted = job.get("date_posted")
                        if not posted:
                            posted = job.get("created_at")
                        try:
                            posted_dt = datetime.fromisoformat(
                                str(posted or "").replace("Z", "+00:00")
                            )
                            if posted_dt.tzinfo is None:
                                posted_dt = posted_dt.replace(tzinfo=timezone.utc)
                            if (now_dt - posted_dt.astimezone(timezone.utc)).total_seconds() > (
                                STALE_LISTING_MAX_AGE_DAYS * 86400
                            ):
                                reason = "stale_listing"
                        except (TypeError, ValueError):
                            pass
                    if not reason:
                        continue
                    job["status"] = "deleted"
                    job["deleted_at"] = now
                    job["deleted_reason"] = reason
                    job["updated_at"] = now
                    moved += 1
                    # Snapshot for tombstones AFTER releasing _lock + jobs EX
                    # flock — block_deleted_job takes its own blocked_urls flock
                    # and must not run while we hold the jobs write lock.
                    to_block.append({
                        "id": job.get("id"),
                        "apply_url": job.get("apply_url"),
                        "job_url": job.get("job_url"),
                        "alternate_urls": list(job.get("alternate_urls") or []),
                    })
        for snap in to_block:
            try:
                block_deleted_job(snap, keep_tombstone=True)
            except TypeError:
                try:
                    block_deleted_job(snap)
                except Exception as e:
                    print(f"warn: block on auto-delete {snap.get('id')}: {e}")
            except Exception as e:
                print(f"warn: block on auto-delete {snap.get('id')}: {e}")
        if moved:
            print(f"auto-delete sweep: moved={moved}")
        return moved
    except Exception as e:
        print(f"warn: auto-delete sweep failed: {e}")
        return 0


def _backfill_auto_delete_loop() -> None:
    """Re-sweep prune rules on a timer, not just once at boot.

    Jobs keep arriving while the dashboard runs (discovery batches, manual
    add, JD/location enrichment that fills an empty location field after
    ingest), and a startup-only pass never sees any of them — non-US
    listings stayed visible for the whole life of a long-running server.
    """
    while True:
        _prune_schedule_wakeup.clear()
        settings = load_prune_settings()
        interval_s = int(settings.get("interval_s") or 0)
        if interval_s <= 0:
            _prune_schedule_wakeup.wait()
            continue
        changed = _prune_schedule_wakeup.wait(interval_s)
        if not changed:
            _run_scheduled_prune_once(settings)


def _run_scheduled_prune_once(settings: dict) -> int:
    """Run one timer-triggered sweep with the persisted rule selection."""
    return _auto_delete_sweep_once(set(settings.get("reasons") or []))


def _install_lifecycle_signal_handlers() -> None:
    """SIGTERM/SIGINT → same teardown as /api/shutdown (tracked child trees).

    launch_dashboard.sh may kill this process when the Desktop applet gets
    Cmd+Q; without handlers, start_new_session children (fills/discovery)
    would be orphaned.
    """

    def _on_signal(signum: int, _frame) -> None:
        try:
            name = signal.Signals(signum).name
        except (ValueError, AttributeError):
            name = str(signum)
        # Run teardown off the signal handler so killpg/wait are safer.
        threading.Thread(
            target=shutdown_dashboard_stack,
            args=(f"signal {name}",),
            daemon=True,
            name="dashboard-signal-shutdown",
        ).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError) as e:
            print(f"warn: could not install handler for {sig}: {e}")


def main():
    global _http_server
    # Do NOT start OpenClaw/PartyRock CDP or Chrome-for-Testing here —
    # only dashboard UI Chrome is opened by launch_dashboard.sh. PartyRock
    # CDP starts on demand via _ensure_openclaw_managed_browser() when a
    # Start/tailor path needs it; CfT is launched by Playwright fill.
    _install_lifecycle_signal_handlers()
    # DASH2-002: crash/restart leaves no _running_procs — force orphan stuck now.
    try:
        orphaned = _force_stuck_orphaned_in_progress(ignore_age=True)
        if orphaned:
            print(
                f"reconcile startup: force-stuck {len(orphaned)} orphaned "
                f"in-progress job(s): {', '.join(orphaned[:8])}"
                + ("…" if len(orphaned) > 8 else "")
            )
    except Exception as e:
        print(f"warn: startup orphan reconcile failed: {e}")
    try:
        triage = migrate_triage_holding_pen_once()
        if triage.get("skipped_to_deleted") or triage.get("cancelled_to_open"):
            print(
                "triage migrate: "
                f"{triage.get('skipped_to_deleted', 0)} skipped_* → deleted, "
                f"{triage.get('cancelled_to_open', 0)} cancelled → open"
            )
    except Exception as e:
        print(f"warn: triage holding-pen migration failed: {e}")
    threading.Thread(target=reconcile_loop, daemon=True).start()
    threading.Thread(target=notify_stuck_jobs_loop, daemon=True).start()
    # OpenClaw-free daily discovery scheduler (replaces `openclaw cron`). POSTs
    # /api/discover at the configured local time while the dashboard is up.
    try:
        _bind_host_env = (os.environ.get("JOBHUNTER_DASHBOARD_HOST") or "127.0.0.1").strip()
        _sched_host = "127.0.0.1" if _bind_host_env in ("", "0.0.0.0") else _bind_host_env
        try:
            _sched_port = int((os.environ.get("JOBHUNTER_DASHBOARD_PORT") or "8787").strip())
        except ValueError:
            _sched_port = 8787
        scheduler_mod.DiscoveryScheduler(host=_sched_host, port=_sched_port).start()
    except Exception as e:
        print(f"warn: could not start discovery scheduler: {e}")
    threading.Thread(target=_ui_watchdog_loop, daemon=True, name="ui-lifecycle-watchdog").start()
    threading.Thread(
        target=_backfill_company_key_loop,
        daemon=True,
        name="company-key-backfill",
    ).start()
    threading.Thread(
        target=_backfill_missing_jds_loop,
        daemon=True,
        name="missing-jd-backfill",
    ).start()
    threading.Thread(
        target=_backfill_multi_opening_loop,
        daemon=True,
        name="multi-opening-backfill",
    ).start()
    threading.Thread(
        target=_backfill_yoe_work_mode_loop,
        daemon=True,
        name="yoe-work-mode-backfill",
    ).start()
    threading.Thread(
        target=_backfill_salary_loop,
        daemon=True,
        name="salary-backfill",
    ).start()
    threading.Thread(
        target=_backfill_auto_delete_loop,
        daemon=True,
        name="auto-delete-backfill",
    ).start()
    # PID file for launch_dashboard.sh wait/cleanup (best-effort).
    # Companion files (also under logs/): dashboard_launcher.pid,
    # dashboard_chrome.pid — see launch_dashboard.sh header.
    try:
        pid_path = ROOT / "logs" / "dashboard_server.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()))
    except OSError as e:
        print(f"warn: could not write dashboard pid file: {e}")
    # Host/port default to loopback:8787 (unchanged macOS behavior). Containers
    # set JOBHUNTER_DASHBOARD_HOST=0.0.0.0 so the port is reachable from the host.
    _bind_host = (os.environ.get("JOBHUNTER_DASHBOARD_HOST") or "127.0.0.1").strip()
    try:
        _bind_port = int((os.environ.get("JOBHUNTER_DASHBOARD_PORT") or "8787").strip())
    except ValueError:
        _bind_port = 8787
    server = ThreadingHTTPServer((_bind_host, _bind_port), Handler)
    _http_server = server
    print(f"job-hunter dashboard: http://{_bind_host}:{_bind_port}")
    if ui_lifecycle_enabled():
        print(
            "UI lifecycle on: heartbeats track connected tabs; quit only via "
            "explicit POST /api/shutdown (header × / last window close / "
            "Cmd+Q) or POST /api/restart (Refresh). Idle heartbeat stall "
            "does not shut down the stack."
        )
    try:
        server.serve_forever()
    finally:
        # Belt-and-suspenders: kill any children still registered if shutdown
        # raced or HTTP stopped without going through shutdown_dashboard_stack.
        # CHR3-001/002: Refresh + hold must keep fill procs and fill CfT.
        preserve_fill = bool(_preserve_fill_cft_on_exit)
        if _restart_requested and not preserve_fill:
            try:
                preserve_fill = _fill_hold_browser_active()
            except Exception:
                preserve_fill = True
        try:
            _kill_all_tracked_child_procs(preserve_fill_procs=preserve_fill)
        except Exception as e:
            print(f"warn: final child cleanup: {e}")
        try:
            # Final quit path: always stop PartyRock CDP too (restart already
            # ran shutdown_dashboard_stack with stop_openclaw_browser=False).
            if not _restart_requested:
                _kill_jh_associated_browsers(
                    stop_openclaw_browser=True, preserve_fill_cft=False
                )
            else:
                _kill_jh_associated_browsers(
                    stop_openclaw_browser=False, preserve_fill_cft=preserve_fill
                )
        except Exception as e:
            print(f"warn: final JH browser cleanup: {e}")
        try:
            server.server_close()
        except Exception:
            pass
        try:
            (ROOT / "logs" / "dashboard_server.pid").unlink(missing_ok=True)
        except TypeError:
            # py<3.8 compat — not expected here, but keep cleanup best-effort
            p = ROOT / "logs" / "dashboard_server.pid"
            if p.exists():
                p.unlink()
        except OSError:
            pass
        print(f"dashboard stopped ({_shutdown_reason or 'serve_forever returned'})")


if __name__ == "__main__":
    main()
