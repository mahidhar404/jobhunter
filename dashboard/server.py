#!/usr/bin/env python3
"""Local-only command-center dashboard for the job-hunter agent.

No external deps (stdlib http.server). Reads/writes jobs.json as the
source of truth. Answering a stuck job resumes that job's own agent
session via `openclaw agent --agent job-hunter --session-key <key> --message <answer>`.
"""
from __future__ import annotations

import fcntl
import json
import re
import shlex
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent.parent
JOBS_FILE = ROOT / "jobs.json"
# Same lock file scripts/jobs_lock.py uses - update_job.py and
# write_discovered_jobs.py run as separate OS processes with no visibility
# into this server's in-memory _lock, so a real file lock is what actually
# keeps a status update here from racing a discovery run's bulk write (or
# vice versa). _lock still serializes this process's own threads; this
# additionally guards against every other process that touches the file.
JOBS_LOCK_FILE = JOBS_FILE.with_suffix(".json.lock")
PROFILE_FILE = ROOT / "profile.json"
STATIC_DIR = Path(__file__).parent / "static"
OPENCLAW_BIN = "/opt/homebrew/bin/openclaw"
PYTHON_BIN = str(ROOT / ".venv" / "bin" / "python3")
SCOUT_SCRIPT = ROOT / "scripts" / "scout.py"
LISTINGS_DIR = ROOT / "listings"
EXEC_APPROVALS_FILE = Path.home() / ".openclaw" / "exec-approvals.json"
CRON_JOB_NAME = "job-hunter-daily"
DISCOVERY_SESSION_KEY = "agent:job-hunter:discovery"
SCOUT_TIMEOUT_S = 1500  # raised alongside SEARCH_TERMS growing from 6 to 14 terms
TAILOR_SCRIPT = ROOT / "scripts" / "tailor_resume.py"
RESUMES_DIR = ROOT / "resumes"
TAILOR_TIMEOUT_S = 700
INBOUND_MEDIA_DIR = Path.home() / ".openclaw" / "media" / "inbound"
INBOUND_RESUME_MAX_AGE_S = 7 * 24 * 3600
ATS_NOTES_DIR = ROOT / "ats_notes"
PLAYBOOK_FILE = ROOT / "PLAYBOOK.md"


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
# PartyRock is one shared logged-in app instance (one managed browser
# session) - two jobs tailoring at the same moment would fight over the
# same "job description" input / generated output. Everything else in the
# pipeline (navigating/filling the real application, waiting for review)
# doesn't touch PartyRock at all and can run fully in parallel across
# jobs - this lock only ever wraps the narrow window where a job is
# actually using PartyRock, so clicking Start on job B while job A is
# mid-tailor queues B for PartyRock specifically, not for the whole job.
_partyrock_lock = threading.Lock()


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
    """The actual work happens on the gateway server, which can keep running
    a turn even after the local CLI client that started it has exited or
    disconnected. Local process tracking alone is not authoritative - ask
    the gateway which sessions are genuinely still 'running'."""
    try:
        out = subprocess.run(
            [OPENCLAW_BIN, "sessions", "list", "--agent", "job-hunter",
             "--active", "60", "--json"],
            capture_output=True, text=True, timeout=15,
        ).stdout
        data = json.loads(out) if out.strip() else {}
        return {s["key"] for s in data.get("sessions", []) if s.get("status") == "running"}
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
    local_keys = {k for k, p in _running_procs.items() if p.poll() is None}
    gateway_keys = gateway_running_session_keys()
    return session_key in (local_keys | gateway_keys)


def active_job() -> dict | None:
    """Return the currently-running job, if any - used only for display
    (e.g. showing what's in progress), not to block starting anything
    else. See is_session_running() for the actual per-session check."""
    local_keys = {k for k, p in _running_procs.items() if p.poll() is None}
    gateway_keys = gateway_running_session_keys()
    running_keys = local_keys | gateway_keys
    if not running_keys:
        return None
    data = read_jobs()
    for job in data["jobs"]:
        if job.get("session_key") in running_keys:
            return job
    if DISCOVERY_SESSION_KEY in running_keys:
        return {"id": None, "company": "(discovery run)", "title": ""}
    return None


def read_jobs() -> dict:
    if not JOBS_FILE.exists():
        return {"jobs": []}
    JOBS_LOCK_FILE.touch(exist_ok=True)
    with open(JOBS_LOCK_FILE, "r+") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_SH)
        try:
            return json.loads(JOBS_FILE.read_text())
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def write_jobs(data: dict) -> None:
    JOBS_LOCK_FILE.touch(exist_ok=True)
    with open(JOBS_LOCK_FILE, "r+") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            JOBS_FILE.write_text(json.dumps(data, indent=2))
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def submit_job_answer(job_id: str, answer: str) -> bool:
    """Shared by the dashboard's own Send-answer button and the desktop
    answer dialog (see _send_answer_dialog) - both need to log the
    question/answer pair, flip status back to resuming, and kick off the
    agent turn with the answer as its new message. Returns False if the
    job doesn't exist."""
    with _lock:
        data = read_jobs()
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job is None:
            return False
        job.setdefault("qa_log", []).append(
            {"question": job.get("question"), "answer": answer, "ts": now_iso()}
        )
        job["question"] = None
        job["status"] = "resuming"
        job["updated_at"] = now_iso()
        write_jobs(data)
        session_key = job["session_key"]
    threading.Thread(target=run_agent_message, args=(session_key, answer), daemon=True).start()
    return True


def _ensure_job_hunter_ask_off() -> None:
    """`openclaw approvals allowlist add` rewrites the agent block and drops
    any fields it doesn't know about (like `ask`). Restore ask=off after
    every allowlist add so job-hunter never goes back to hanging on
    unattended approval prompts."""
    try:
        data = json.loads(EXEC_APPROVALS_FILE.read_text())
        agent = data.setdefault("agents", {}).setdefault("job-hunter", {})
        if agent.get("ask") != "off":
            agent["ask"] = "off"
            agent.setdefault("security", "allowlist")
            EXEC_APPROVALS_FILE.write_text(json.dumps(data, indent=2))
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


def _run_subprocess_step(cmd: list[str], log_name: str, timeout_s: int,
                          track_key: str = DISCOVERY_SESSION_KEY) -> tuple[int, Path]:
    """Run one pipeline step as a plain subprocess with real logging (not
    /dev/null - piping output away makes it impossible to check progress
    mid-run). Tracked under track_key for is_session_running()'s
    double-start check and for the dashboard's "what's currently running"
    display - callers working on a specific job should pass that job's
    own session_key, not the default, so it's attributed to the right job
    instead of reporting it as a stray discovery run."""
    ROOT.joinpath("logs").mkdir(exist_ok=True)
    log_path = ROOT / "logs" / log_name
    log_file = open(log_path, "w")
    step_start = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log_file, stderr=subprocess.STDOUT)
    _running_procs[track_key] = proc
    try:
        exit_code = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        exit_code = -1
    finally:
        _running_procs.pop(track_key, None)
        log_file.close()
    _log_timing(log_name.removesuffix(".log"), time.monotonic() - step_start, f"exit={exit_code}")
    return exit_code, log_path


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
    steps actually fails and needs a human-judgment fix."""
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    scout_exit, scout_log = _run_subprocess_step(
        [PYTHON_BIN, "-u", str(SCOUT_SCRIPT)], "scout.log", SCOUT_TIMEOUT_S)
    scout_file = LISTINGS_DIR / f"{today}.json"
    if scout_exit != 0 or not scout_file.exists():
        run_agent_message(
            DISCOVERY_SESSION_KEY,
            f"scripts/scout.py exited with code {scout_exit} and {scout_file} "
            f"was not produced (see {scout_log}). Check for a bug in scout.py "
            "itself and fix it, then note the issue - do not retry scraping "
            "inside this turn.",
            timeout_s=600,
        )
        return

    ats_exit, ats_log = _run_subprocess_step(
        [PYTHON_BIN, "-u", str(ROOT / "scripts" / "scrape_ats.py")], "scrape_ats.log", 300)
    ats_file = LISTINGS_DIR / f"{today}-ats.json"

    # Built In has no public API and no JobSpy support - direct HTML
    # scraping instead (see scrape_builtin.py's own docstring for exactly
    # what was reverse-engineered live to make this work). Best-effort
    # like the ATS scrape above: a failure here shouldn't block the rest
    # of discovery, just contribute zero listings for this run.
    # 180s was the initial guess before a real timed run existed - a real
    # live run (5 terms x 2 pages, 247 candidate job pages) took 614.3s.
    # scrape_builtin.py now applies Built In's own date/experience filters
    # and pages deeper per term (see its own notes: an unfiltered, shallow
    # search silently missed a real Intel posting ranked page 9) - a real
    # live count of the new filtered search collected 1097 unique candidate
    # URLs (~4.4x more than before), so the sequential-with-delay fetch
    # phase (see scrape_builtin.py's own notes on why it can't parallelize
    # this without triggering 429s) scales proportionally to roughly
    # 45 real minutes. 5400s gives real headroom above that estimate.
    builtin_exit, builtin_log = _run_subprocess_step(
        [PYTHON_BIN, "-u", str(ROOT / "scripts" / "scrape_builtin.py")], "scrape_builtin.log", 5400)
    builtin_file = LISTINGS_DIR / f"{today}-builtin.json"

    dedup_cmd = [PYTHON_BIN, "-u", str(ROOT / "scripts" / "dedup_listings.py"), str(scout_file)]
    if ats_exit == 0 and ats_file.exists():
        dedup_cmd.append(str(ats_file))
    if builtin_exit == 0 and builtin_file.exists():
        dedup_cmd.append(str(builtin_file))
    dedup_exit, dedup_log = _run_subprocess_step(dedup_cmd, "dedup_listings.log", 120)
    qualified_file = LISTINGS_DIR / f"{today}-qualified.json"

    if dedup_exit != 0 or not qualified_file.exists():
        run_agent_message(
            DISCOVERY_SESSION_KEY,
            f"scripts/dedup_listings.py exited with code {dedup_exit} and "
            f"{qualified_file} was not produced (see {dedup_log}). Check for "
            "a bug in dedup_listings.py and fix it - do not re-implement "
            "dedup/filter logic ad-hoc, fix the script.",
            timeout_s=600,
        )
        return

    # Deliberately NOT under listings/ - scrape_ats.py's own default
    # --seed-from globs every *.json in that directory expecting job-
    # listing arrays; a prior day's leftover skip-file (a flat array of
    # company name strings, a completely different shape) sitting there
    # crashed it with "'str' object has no attribute 'get'" the very next
    # time discovery ran. logs/ is already the right place for ephemeral,
    # single-run working files like this.
    skip_file = ROOT / "logs" / "tracked-companies-skip.json"
    tracker_exit, tracker_log = _run_subprocess_step(
        [PYTHON_BIN, "-u", str(ROOT / "scripts" / "tracker.py"), "list-companies", "--out", str(skip_file)],
        "tracker_list.log", 30,
    )
    if tracker_exit != 0 or not skip_file.exists():
        run_agent_message(
            DISCOVERY_SESSION_KEY,
            f"scripts/tracker.py list-companies exited with code {tracker_exit} "
            f"(see {tracker_log}). Check for a bug in tracker.py and fix it.",
            timeout_s=600,
        )
        return

    write_cmd = [PYTHON_BIN, "-u", str(ROOT / "scripts" / "write_discovered_jobs.py"),
                 str(qualified_file), "--skip-companies", str(skip_file)]
    write_exit, write_log = _run_subprocess_step(write_cmd, "write_discovered_jobs.log", 60)
    if write_exit != 0:
        run_agent_message(
            DISCOVERY_SESSION_KEY,
            f"scripts/write_discovered_jobs.py exited with code {write_exit} "
            f"(see {write_log}). Check for a bug in the script and fix it - "
            "do not hand-write jobs.json entries yourself.",
            timeout_s=600,
        )


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
    for f in INBOUND_MEDIA_DIR.glob("*-resume.pdf"):
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
        data = read_jobs()
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job is None:
            return
        if result.get("company"):
            job["company"] = result["company"].strip()
        if result.get("title"):
            job["title"] = result["title"].strip()
        if result.get("location"):
            job["location"] = result["location"].strip()
        job["job_description"] = preview
        job["status_detail"] = "Added manually via dashboard - details fetched automatically."
        job["updated_at"] = now_iso()
        write_jobs(data)


def _acquire_partyrock_lock(job_id: str, session_key: str) -> None:
    """Blocks until PartyRock is free. If it's already taken, marks the
    job's status_detail first so the dashboard shows why it's waiting
    instead of looking stalled - cleared again once the lock is ours."""
    if _partyrock_lock.acquire(blocking=False):
        return
    with _lock:
        data = read_jobs()
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job is not None:
            job["status_detail"] = "Waiting for another job to finish using PartyRock..."
            job["updated_at"] = now_iso()
            write_jobs(data)
    _partyrock_lock.acquire()


def run_tailor_then_fill(job_id: str) -> None:
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
    job, and the agent still knows the manual steps (see PLAYBOOK.md)."""
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
        apply_url = job.get("apply_url") or job.get("job_url") or ""
        # Dry-run/test identity override (see api/jobs/<id>/dry_run) - must
        # be baked into the FIRST fill-turn message, not sent as a
        # follow-up correction after Start: observed live, the agent can
        # reach real account creation within ~60s of the turn starting,
        # faster than a human can react to send a correction in time.
        dry_run_note = job.get("dry_run_identity")

    if not job_description.strip():
        # Manually-added jobs start with no description fetched yet - the
        # agent has to get that (and everything else) from the real apply
        # page first, so automated tailoring can't run yet this turn.
        # This is one big unsupervised turn covering both tailoring and
        # filling (unlike the split happy path below), so the PartyRock
        # lock here is held coarser than ideal - through the fill step
        # too, not just the tailor step. Acceptable since manually-added
        # jobs are the rare path; still correct (never lets two jobs touch
        # PartyRock at once), just not maximally concurrent.
        _acquire_partyrock_lock(job_id, session_key)
        try:
            run_agent_message(
                session_key,
                playbook_preamble() +
                "Follow PLAYBOOK.md above for this job. Here is its current full "
                f"record (do NOT read jobs.json yourself - it has 800+ entries "
                f"and reading the whole file wastes a huge number of tokens for "
                f"one record; use scripts/get_job.py {job_id} if you ever need "
                f"it again, and scripts/update_job.py {job_id} [--field value ...] "
                f"to write changes, never a direct read/write of the file):"
                f"\n\n{json.dumps(job, indent=2)}\n\n"
                "It has no job_description yet - fetch the real posting details "
                "from apply_url first (then save them with update_job.py's "
                "--company/--title/--location/--job-description flags), then "
                "continue the full pipeline (tailor resume, fill the "
                "application) and stop at ready_for_review. Never submit.",
                timeout_s=1800,
            )
        finally:
            _partyrock_lock.release()
        return

    job_dir = RESUMES_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    jd_file = job_dir / "jd.txt"
    jd_file.write_text(job_description)
    resume_tex = job_dir / "resume.tex"
    resume_pdf = job_dir / "resume.pdf"
    playbook_already_sent = False

    if resume_pdf.exists() and resume_tex.exists():
        # A resume was already produced for this job on some earlier
        # attempt (Start, then Cancel happened during/after filling, not
        # during tailoring) - Retry shouldn't burn another PartyRock
        # generation for content that's already sitting on disk. The mere
        # presence of these two files is the check: a genuinely fresh job
        # never has them yet, so this naturally falls through to the
        # normal tailor-from-scratch path below with no extra flag needed.
        with _lock:
            data = read_jobs()
            job = next((j for j in data["jobs"] if j["id"] == job_id), None)
            if job is not None:
                job["resume_path"] = str(resume_pdf.relative_to(ROOT))
                job["status"] = "navigating"
                job["status_detail"] = "Reusing previously tailored resume (already on disk). Navigating to apply URL."
                job["updated_at"] = now_iso()
                write_jobs(data)
    else:
        # PartyRock is one shared logged-in app instance - only one job may be
        # actively tailoring against it at a time (see _partyrock_lock). This
        # is the ONLY section of the pipeline that touches it: released right
        # below, before the compile step, so a second job queued here starts
        # its own tailoring the moment this one's done with PartyRock, while
        # this job goes on to compile/fit/address-pick/fill concurrently -
        # those steps never need the lock at all.
        _acquire_partyrock_lock(job_id, session_key)
        try:
            tailor_exit, tailor_log = _run_subprocess_step(
                [PYTHON_BIN, "-u", str(TAILOR_SCRIPT), "--jd-file", str(jd_file), "--out", str(resume_tex),
                 "--timeout", str(TAILOR_TIMEOUT_S - 100)],
                f"tailor_{job_id}.log", TAILOR_TIMEOUT_S, track_key=session_key,
            )

            if tailor_exit != 0 or not resume_tex.exists():
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
                run_agent_message(
                    session_key,
                    playbook_preamble() +
                    f"Automated resume tailoring failed (scripts/tailor_resume.py "
                    f"exited {tailor_exit}, see {tailor_log}). Follow PLAYBOOK.md's "
                    "manual PartyRock steps instead: open the app via your browser "
                    "tool, paste the job description, wait for it to finish, and "
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
                    # The agent didn't manage to produce it - it should have left
                    # jobs.json in whatever state explains why (stuck with a
                    # question, an error it already reported) via update_job.py.
                    # Nothing further to do here; don't force a status.
                    return
                # else: fall through into the same compile/fit/address/fill steps
                # below, exactly as if scripts/tailor_resume.py had succeeded.
        finally:
            _partyrock_lock.release()

        compile_exit, compile_log = _run_subprocess_step(
            ["/opt/homebrew/bin/tectonic", str(resume_tex)], f"tectonic_{job_id}.log", 120, track_key=session_key)

        with _lock:
            data = read_jobs()
            job = next((j for j in data["jobs"] if j["id"] == job_id), None)
            if job is not None:
                if compile_exit == 0 and resume_pdf.exists():
                    job["resume_path"] = str(resume_pdf.relative_to(ROOT))
                    job["status"] = "navigating"
                    job["status_detail"] = "Resume tailored and compiled. Navigating to apply URL."
                else:
                    job["status"] = "stuck"
                    job["question"] = (
                        f"{resume_tex} was produced but tectonic failed to "
                        f"compile it (exit {compile_exit}, see {compile_log}). The LaTeX likely has "
                        "a real syntax error - can you check it, or should I have the agent fix it?"
                    )
                job["updated_at"] = now_iso()
                write_jobs(data)

        if compile_exit != 0 or not resume_pdf.exists():
            run_agent_message(
                session_key,
                (playbook_preamble() if not playbook_already_sent else "") +
                f"{resume_tex} was produced but tectonic failed to compile it "
                f"(see {compile_log}). Read the .tex file, fix the LaTeX error, recompile with "
                f"tectonic, then continue the pipeline (fill the application, stop at "
                "ready_for_review, never submit).",
                timeout_s=1800,
            )
            return

        # Best-effort: shrink layout (margin/line-spacing only, never content -
        # see scripts/fit_resume_pages.py) if the resume ran past 2 pages. A
        # nonzero exit here just means it stayed over 2 pages at the tightest
        # tested layout - not worth stalling the pipeline over, so this is
        # logged, not treated as a hard failure.
        fit_exit, fit_log = _run_subprocess_step(
            [PYTHON_BIN, "-u", str(ROOT / "scripts" / "fit_resume_pages.py"), str(resume_tex)],
            f"fit_pages_{job_id}.log", 90, track_key=session_key)
        if fit_exit != 0:
            print(f"warn: fit_resume_pages.py exit={fit_exit} for {job_id} - see {fit_log}")

    # The browser tool's file-upload only accepts paths under
    # ~/.openclaw/media/inbound - observed live, the agent tried uploading
    # resumes/<id>/resume.pdf directly, got "Invalid path: must stay within
    # inbound media directory", and burned a retry round-trip copying it
    # itself before every single fill turn. Doing that copy here instead
    # means the agent is never handed a path it can't actually use.
    INBOUND_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    inbound_resume = INBOUND_MEDIA_DIR / f"{job_id}-resume.pdf"
    shutil.copyfile(resume_pdf, inbound_resume)
    _cleanup_old_inbound_resumes()

    # Mailing-address selection (find the city PartyRock put in the resume
    # header, match its metro in addresses.json, pick a nearby placeholder
    # entry at random) is pure mechanical lookup, not a judgment call - see
    # scripts/pick_address.py. Pre-computing it here means the agent never
    # reads the whole addresses.json pool itself. Falls back to the manual
    # PLAYBOOK.md instructions (still in place) if this can't find a city
    # in the header or a matching entry - a script hiccup here shouldn't
    # strand the job.
    address_json = None
    addr_exit, addr_log = _run_subprocess_step(
        [PYTHON_BIN, "-u", str(ROOT / "scripts" / "pick_address.py"), str(resume_tex)],
        f"pick_address_{job_id}.log", 15, track_key=session_key)
    if addr_exit == 0:
        try:
            address_json = json.loads(addr_log.read_text())
        except Exception as e:
            print(f"warn: could not parse pick_address.py output for {job_id}: {e}")
    else:
        print(f"warn: pick_address.py exit={addr_exit} for {job_id} - see {addr_log}, falling back to manual address selection")

    fill_message = (
        (playbook_preamble() if not playbook_already_sent else "") +
        f"Here is this job's current full record (do NOT read jobs.json yourself - it has "
        f"800+ entries and reading the whole file wastes a huge number of tokens for one "
        f"record; use scripts/get_job.py {job_id} if you ever need it again, and "
        f"scripts/update_job.py {job_id} [--status S] [--status-detail D] [--question Q] "
        f"[--clear-question] [--pending-command C] [--clear-pending-command] to write "
        f"changes during this turn, never a direct read/write of the file):"
        f"\n\n{json.dumps(job, indent=2)}\n\n"
        f"The resume is already tailored and compiled - upload it from {inbound_resume} "
        "(do NOT redo tailoring or run tailor_resume.py again; do not use any other path for "
        "the upload, it will be rejected)."
    )
    if address_json:
        fill_message += (
            "\n\nFor the mailing address fields, use exactly this pre-picked placeholder "
            "(already matched to the city on the resume, per PLAYBOOK.md's mailing-address "
            f"rule) - do not look anything up yourself: {json.dumps(address_json)}"
        )
    fill_message += (
        " Follow PLAYBOOK.md's Fill the application step: "
        "navigate to apply_url, upload that resume, fill the form efficiently, and stop at "
        "ready_for_review. Ask via question/pending_command and end your turn whenever you're "
        "unsure - never submit."
    )
    ats_notes_match = ats_notes_for_url(apply_url)
    if ats_notes_match:
        notes_path, notes_content = ats_notes_match
        fill_message += (
            f"\n\nThis apply_url is on a known ATS platform - here are notes from past "
            f"runs on this same platform (field selectors, known quirks, known blockers), "
            f"from {notes_path}. Treat these as a strong first guess to verify with one "
            "snapshot, not a guarantee - fall back to normal exploration if something "
            f"doesn't match. If you learn something new and reliably repeatable, append it "
            f"to {notes_path} directly (via exec) so future jobs on this platform benefit "
            f"too:\n\n{notes_content}"
        )
    if dry_run_note:
        fill_message = (
            f"DRY RUN - TEST PIPELINE ONLY, NOT A REAL APPLICATION. {dry_run_note} Do not use "
            "profile.json's real identity for any field or account creation - use only the "
            "synthetic identity given above. Do not log this to the Excel tracker "
            "(application_tracker.xlsx) - skip that step entirely. "
        ) + fill_message
    run_agent_message(session_key, fill_message, timeout_s=1800)


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
    # stdout/stderr used to go to DEVNULL - when a turn died silently (a
    # transient provider timeout, an unhandled CLI error) there was no way
    # to tell why short of re-running it and hoping to catch it again.
    # Logging here means the answer is just sitting in the file already.
    ROOT.joinpath("logs").mkdir(exist_ok=True)
    log_name = session_key.rsplit(":", 1)[-1]
    log_path = ROOT / "logs" / f"agent_turn_{log_name}.log"
    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(
            [
                OPENCLAW_BIN, "agent",
                "--agent", "job-hunter",
                "--session-key", session_key,
                "--message", message,
                "--timeout", str(timeout_s),
                "--thinking", thinking,
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        _running_procs[session_key] = proc
        exit_code = proc.wait()
    _running_procs.pop(session_key, None)
    _log_timing(f"agent_turn[{log_name}]", time.monotonic() - turn_start, f"exit={exit_code}")


def abort_gateway_session(session_key: str) -> None:
    """The real work runs on the gateway server and can outlive the local CLI
    client that started it. Connecting a fresh short-lived client to the same
    session-key causes the gateway to abort whatever was previously running
    on it (observed behavior: OPENCLAW_DIRECT_ABORT). Fire this in the
    background and don't wait - we only need the abort side effect."""
    def _fire():
        subprocess.run(
            [
                OPENCLAW_BIN, "agent",
                "--agent", "job-hunter",
                "--session-key", session_key,
                "--message", "STOP. Cancelled by user. Do not continue this turn.",
                "--timeout", "15",
            ],
            capture_output=True,
        )
    threading.Thread(target=_fire, daemon=True).start()


_TAIL_LINE_RE = re.compile(
    r"^(?P<time>\d\d:\d\d:\d\d)\s+(?P<event>\S+)\s+\S+\s*(?P<detail>.*)$"
)


def _find_cron_job() -> dict | None:
    out = subprocess.run(
        [OPENCLAW_BIN, "cron", "list", "--all", "--json"],
        capture_output=True, text=True, timeout=15,
    ).stdout
    parsed = json.loads(out) if out.strip() else {}
    jobs_list = parsed.get("jobs", []) if isinstance(parsed, dict) else parsed
    return next((j for j in jobs_list if j.get("name") == CRON_JOB_NAME), None)


def get_activity(session_key: str, tail: int = 60) -> list[dict]:
    try:
        out = subprocess.run(
            [
                OPENCLAW_BIN, "sessions", "tail",
                "--agent", "job-hunter",
                "--session-key", session_key,
                "--tail", str(tail),
            ],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception as e:
        return [{"time": "", "event": "error", "detail": str(e)}]
    events = []
    for line in out.splitlines():
        m = _TAIL_LINE_RE.match(line.strip())
        if m:
            events.append(m.groupdict())
    return events


IN_PROGRESS_STATUSES = {"navigating", "filling", "tailoring", "resuming"}
RECONCILE_INTERVAL_S = 20
STALE_AFTER_S = 90
NOTIFY_STATUSES = {"stuck", "blocked_captcha"}
NOTIFY_INTERVAL_S = 5
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
    already happened."""
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
                        continue  # genuinely still running
                    try:
                        updated_ts = datetime.fromisoformat(job["updated_at"].replace("Z", "+00:00"))
                    except Exception:
                        updated_ts = None
                    age_s = (now_dt() - updated_ts).total_seconds() if updated_ts else 9999
                    if age_s < STALE_AFTER_S:
                        continue  # give it a moment before flagging
                    candidates.append((job["id"], session_key))

            if not candidates:
                continue

            # Phase 2 (unlocked, slow): the actual gateway subprocess calls.
            # This must never happen while _lock is held - it used to, and
            # every other dashboard request (any GET/POST needing the lock)
            # would queue up behind however long these calls took.
            to_retry = []  # (job_id, session_key, detail)
            to_stuck = []  # (job_id, detail)
            for job_id, session_key in candidates:
                events = get_activity(session_key, tail=5)
                last = events[-1] if events else None
                if not (last and last["event"] == "session.ended"):
                    continue
                detail = last["detail"]
                if job_id not in _auto_retried_job_ids:
                    _auto_retried_job_ids.add(job_id)
                    to_retry.append((job_id, session_key, detail))
                else:
                    to_stuck.append((job_id, detail))

            if not to_retry and not to_stuck:
                continue

            # Phase 3 (locked, fast): apply whatever changed. Re-reads
            # jobs.json fresh in case something else wrote to it during
            # phase 2's unlocked window.
            with _lock:
                data = read_jobs()
                by_id = {j["id"]: j for j in data["jobs"]}
                for job_id, session_key, detail in to_retry:
                    job = by_id.get(job_id)
                    if job is None:
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
                write_jobs(data)

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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str, inline_filename: str | None = None):
        if not path.exists():
            self._send_json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
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

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]

        if not parts:
            self._send_file(STATIC_DIR / "index.html", "text/html")
            return
        if parts[0] == "app.js":
            self._send_file(STATIC_DIR / "app.js", "application/javascript")
            return
        if len(parts) == 2 and parts[0] == "resume":
            with _lock:
                data = read_jobs()
            job = self._job(data, parts[1])
            if not job or not job.get("resume_path"):
                self._send_json({"error": "not found"}, 404)
                return
            filename = f"{job.get('company') or job['id']}_resume.pdf"
            self._send_file(ROOT / job["resume_path"], "application/pdf", inline_filename=filename)
            return
        if parts == ["api", "jobs"]:
            with _lock:
                self._send_json(read_jobs())
            return
        if len(parts) == 3 and parts[0:2] == ["api", "jobs"]:
            with _lock:
                data = read_jobs()
            job = self._job(data, parts[2])
            self._send_json(job if job else {"error": "not found"}, 200 if job else 404)
            return
        if len(parts) == 4 and parts[0:2] == ["api", "jobs"] and parts[3] == "activity":
            with _lock:
                data = read_jobs()
            job = self._job(data, parts[2])
            if not job:
                self._send_json({"error": "not found"}, 404)
                return
            self._send_json({"events": get_activity(job["session_key"])})
            return
        if parts == ["api", "profile"]:
            if PROFILE_FILE.exists():
                self._send_json(json.loads(PROFILE_FILE.read_text()))
            else:
                self._send_json({})
            return
        if parts == ["api", "allowlist"]:
            try:
                d = json.loads(EXEC_APPROVALS_FILE.read_text())
                self._send_json(d.get("agents", {}).get("job-hunter", {}))
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return
        if parts == ["api", "cron"]:
            try:
                job = _find_cron_job()
                self._send_json(job or {"error": "not found"}, 200 if job else 404)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return
        self._send_json({"error": "not found"}, 404)

    # --------------------------------------------------------------- POST
    def do_POST(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json({"error": "bad json"}, 400)
            return

        if len(parts) == 4 and parts[0:2] == ["api", "jobs"] and parts[3] == "answer":
            self._handle_answer(parts[2], payload)
            return
        if len(parts) == 4 and parts[0:2] == ["api", "jobs"] and parts[3] == "approve_command":
            self._handle_approve_command(parts[2], payload)
            return
        if len(parts) == 4 and parts[0:2] == ["api", "jobs"] and parts[3] == "cancel":
            self._handle_cancel(parts[2])
            return
        if len(parts) == 4 and parts[0:2] == ["api", "jobs"] and parts[3] == "skip":
            self._handle_skip(parts[2])
            return
        if len(parts) == 4 and parts[0:2] == ["api", "jobs"] and parts[3] == "submitted":
            self._handle_mark_submitted(parts[2])
            return
        if parts == ["api", "profile"]:
            self._handle_profile_update(payload)
            return
        if parts == ["api", "jobs", "add"]:
            self._handle_add_job(payload)
            return
        if parts == ["api", "discover"]:
            self._handle_discover()
            return
        if parts == ["api", "cron", "toggle"]:
            self._handle_cron_toggle(payload)
            return
        if len(parts) == 4 and parts[0:2] == ["api", "jobs"] and parts[3] == "start":
            self._handle_start(parts[2])
            return
        if parts == ["api", "cli"]:
            self._handle_cli(payload)
            return
        self._send_json({"error": "not found"}, 404)

    # ------------------------------------------------------------- DELETE
    def do_DELETE(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) == 3 and parts[0:2] == ["api", "jobs"]:
            with _lock:
                data = read_jobs()
                before = len(data["jobs"])
                data["jobs"] = [j for j in data["jobs"] if j["id"] != parts[2]]
                write_jobs(data)
            self._send_json({"ok": True, "deleted": before != len(data["jobs"])})
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
        with _lock:
            data = read_jobs()
            job = self._job(data, job_id)
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
            write_jobs(data)
            session_key = job["session_key"]

        def resume_after_command_decision():
            if approve and command:
                binary = command.split()[0]
                subprocess.run(
                    [OPENCLAW_BIN, "approvals", "allowlist", "add",
                     "--agent", "job-hunter", f"{binary}*"],
                    capture_output=True,
                )
                _ensure_job_hunter_ask_off()
                message = f"Approved. Run this exact command now: {command}"
            else:
                message = f"Denied: '{command}'. Do not run it. Find a different approach or ask a different question."
            run_agent_message(session_key, message)

        threading.Thread(target=resume_after_command_decision, daemon=True).start()
        self._send_json({"ok": True})

    def _handle_cancel(self, job_id):
        with _lock:
            data = read_jobs()
            job = self._job(data, job_id)
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            session_key = job["session_key"]
            job["status"] = "cancelled"
            job["status_detail"] = "Cancelled by user from dashboard."
            job["updated_at"] = now_iso()
            write_jobs(data)
        proc = _running_procs.get(session_key)
        if proc and proc.poll() is None:
            proc.terminate()
        abort_gateway_session(session_key)
        self._send_json({"ok": True})

    def _handle_skip(self, job_id):
        with _lock:
            data = read_jobs()
            job = self._job(data, job_id)
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            session_key = job["session_key"]
            job["status"] = "skipped_manual"
            job["status_detail"] = "Skipped by user from dashboard."
            job["updated_at"] = now_iso()
            write_jobs(data)
        proc = _running_procs.get(session_key)
        if proc and proc.poll() is None:
            proc.terminate()
        abort_gateway_session(session_key)
        self._send_json({"ok": True})

    def _handle_mark_submitted(self, job_id):
        """The agent never clicks Submit - it can't know when a real
        submission happens on the actual external site, so this is purely
        a manual action the user takes after they've actually done it."""
        with _lock:
            data = read_jobs()
            job = self._job(data, job_id)
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            job["status"] = "applied"
            job["status_detail"] = "Marked submitted by user from dashboard."
            job["updated_at"] = now_iso()
            write_jobs(data)
            company, role = job.get("company"), job.get("title")
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
        self._send_json({"ok": True})

    def _handle_add_job(self, payload):
        url = (payload.get("url") or "").strip()
        if not url or not url.lower().startswith(("http://", "https://")):
            self._send_json({"error": "a valid http(s) url is required"}, 400)
            return
        with _lock:
            data = read_jobs()
            for job in data["jobs"]:
                if job.get("apply_url") == url or job.get("job_url") == url:
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
            }
            data["jobs"].append(job)
            write_jobs(data)
        threading.Thread(target=_try_extract_manual_job_details, args=(job_id, url), daemon=True).start()
        self._send_json({"ok": True, "id": job_id})

    def _handle_profile_update(self, payload):
        if not isinstance(payload, dict):
            self._send_json({"error": "expected a JSON object"}, 400)
            return
        PROFILE_FILE.write_text(json.dumps(payload, indent=2))
        self._send_json({"ok": True})

    def _handle_discover(self):
        # Only block a duplicate discovery run - a job actively applying
        # no longer needs to block this (jobs.json writes are properly
        # file-locked across processes now, see scripts/jobs_lock.py).
        if is_session_running(DISCOVERY_SESSION_KEY):
            self._send_json({"error": "discovery is already running"}, 409)
            return
        threading.Thread(target=run_scout_scrape_then_dedup, daemon=True).start()
        self._send_json({"ok": True, "started": True})

    def _handle_start(self, job_id):
        # Unlocked read first - read_jobs() has its own internal file lock
        # for safe reads, no need for the broader in-process _lock here.
        job = self._job(read_jobs(), job_id)
        if job is None:
            self._send_json({"error": "not found"}, 404)
            return
        # Only block re-starting THIS job while it's already running - other
        # jobs and discovery no longer block Start at all. Checked OUTSIDE
        # _lock deliberately: is_session_running() can shell out to the
        # gateway (subprocess, up to ~15s) - doing that while holding _lock
        # would freeze every other dashboard request (any GET/POST that
        # needs the lock) for that whole window, not just this one.
        if is_session_running(job["session_key"]):
            self._send_json({"error": "this job is already running"}, 409)
            return
        with _lock:
            data = read_jobs()
            job = self._job(data, job_id)
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            job["status"] = "tailoring"
            job["status_detail"] = "Started by user from dashboard. Tailoring resume."
            job["updated_at"] = now_iso()
            write_jobs(data)
        threading.Thread(target=run_tailor_then_fill, args=(job_id,), daemon=True).start()
        self._send_json({"ok": True})

    def _handle_cli(self, payload):
        args_str = (payload.get("args") or "").strip()
        confirmed = bool(payload.get("confirmed"))
        if not args_str:
            self._send_json({"error": "no command given"}, 400)
            return
        if is_risky(args_str) and not confirmed:
            self._send_json({"requires_confirm": True})
            return
        try:
            args = shlex.split(args_str)
        except ValueError as e:
            self._send_json({"error": f"could not parse command: {e}"}, 400)
            return
        try:
            proc = subprocess.Popen(
                [OPENCLAW_BIN, *args],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            deadline = time.time() + 45
            try:
                out, _ = proc.communicate(timeout=max(0.1, deadline - time.time()))
                timed_out = False
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()
                timed_out = True
            if "allowlist" in args and "add" in args and "job-hunter" in args:
                _ensure_job_hunter_ask_off()
            self._send_json({
                "ok": True,
                "output": out,
                "exit_code": proc.returncode,
                "timed_out": timed_out,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_cron_toggle(self, payload):
        enable = bool(payload.get("enable"))
        try:
            job = _find_cron_job()
            if not job:
                self._send_json({"error": "cron job not found"}, 404)
                return
            subprocess.run(
                [OPENCLAW_BIN, "cron", "enable" if enable else "disable", job["id"]],
                capture_output=True,
            )
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)


def main():
    # No-op if already running - but the browser tool silently hangs every
    # navigation if it's ever not running, so make sure on every start.
    subprocess.run([OPENCLAW_BIN, "browser", "start"], capture_output=True)
    threading.Thread(target=reconcile_loop, daemon=True).start()
    threading.Thread(target=notify_stuck_jobs_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", 8787), Handler)
    print("job-hunter dashboard: http://127.0.0.1:8787")
    server.serve_forever()


if __name__ == "__main__":
    main()
