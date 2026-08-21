"""Regression tests for DASH-001…010 / 018 and DASH2-001…004 / 006 / 013.

No browser — exercises server helpers and static UI source contracts.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SERVER_PATH = HERE / "server.py"
APP_JS = HERE / "static" / "app.js"
INDEX_HTML = HERE / "static" / "index.html"


def _load_server():
    spec = importlib.util.spec_from_file_location("jh_dashboard_bugs_srv", SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jh_dashboard_bugs_srv"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_hold_active_suspends_without_ready_status():
    """DASH-001: hold detail / activity suspends deadline, not only Ready."""
    srv = _load_server()
    job = {
        "id": "j1",
        "status": "filling",
        "status_detail": "Browser held open for review (never submitted) — waiting.",
    }
    assert srv._job_is_holding_for_review(job, job_id="j1") is True
    ready = {"id": "j2", "status": "ready_for_review", "status_detail": "ok"}
    assert srv._job_is_holding_for_review(ready) is True
    filling = {"id": "j3", "status": "filling", "status_detail": "Filling fields…"}
    assert srv._job_is_holding_for_review(filling) is False


def test_hold_active_via_activity_event():
    srv = _load_server()
    job = {"id": "hold-ev", "status": "filling", "status_detail": "still filling"}
    srv.clear_fill_activity("hold-ev")
    srv.append_fill_activity("hold-ev", event="hold", detail="Keeping browser open…")
    assert srv._job_is_holding_for_review(job, job_id="hold-ev") is True


def test_fill_pause_suspends_kill_deadline():
    """Pause must suspend dashboard fill timeout (same grace as hold/Ready)."""
    srv = _load_server()
    job = {
        "id": "pause-j",
        "status": "filling",
        "status_detail": "Fill paused — browser stays open until you Continue fill.",
    }
    assert srv._job_is_fill_paused(job, job_id="pause-j") is True
    assert srv._job_fill_browser_must_stay_open(job, job_id="pause-j") is True
    filling = {"id": "ok", "status": "filling", "status_detail": "Filling fields…"}
    assert srv._job_is_fill_paused(filling, job_id="ok") is False
    srv.clear_fill_activity("pause-act")
    job2 = {"id": "pause-act", "status": "filling", "status_detail": "Filling…"}
    srv.append_fill_activity(
        "pause-act",
        event="fill_pause",
        detail="*** FILL PAUSED (between actions) — edit the form…",
    )
    assert srv._job_is_fill_paused(job2, job_id="pause-act") is True
    srv.append_fill_activity(
        "pause-act",
        event="fill_pause",
        detail="[fill-pause] Continue — resuming fill…",
    )
    assert srv._job_is_fill_paused(job2, job_id="pause-act") is False


def test_classify_fill_paused_stdout():
    srv = _load_server()
    ev, det = srv._classify_fill_stdout_line(
        "*** FILL PAUSED (between actions) — edit the form in Chrome ***"
    )
    assert ev == "fill_pause"
    assert "PAUSED" in det.upper()
    ev2, _ = srv._classify_fill_stdout_line(
        "[fill-pause] Continue — resuming fill (will skip fields already filled)…"
    )
    assert ev2 == "fill_pause"

def test_patch_job_refuses_clobber_abort_statuses():
    """DASH-002/003/004: _patch_job must not undelete / un-cancel / un-apply."""
    srv = _load_server()
    for abort in ("cancelled", "deleted", "applied", "skipped_manual"):
        jobs = {
            "jobs": [
                {
                    "id": "x",
                    "status": abort,
                    "status_detail": "terminal",
                    "session_key": "agent:job-hunter:job-x",
                }
            ]
        }

        @contextmanager
        def _fake_locked(*, allow_purge=False):
            yield jobs

        with mock.patch.object(srv, "locked_jobs_for_write", _fake_locked):
            srv._patch_job("x", status="ready_for_review", status_detail="should not apply")
            assert jobs["jobs"][0]["status"] == abort


def test_patch_job_allows_normal_progress():
    srv = _load_server()
    jobs = {
        "jobs": [
            {
                "id": "x",
                "status": "filling",
                "status_detail": "go",
                "session_key": "agent:job-hunter:job-x",
            }
        ]
    }

    @contextmanager
    def _fake_locked(*, allow_purge=False):
        yield jobs

    with mock.patch.object(srv, "locked_jobs_for_write", _fake_locked):
        srv._patch_job("x", status="ready_for_review", status_detail="Ready")
        assert jobs["jobs"][0]["status"] == "ready_for_review"


def test_mark_fill_thread_stuck_skips_abort_statuses():
    """DASH-005: crash handler must not overwrite cancelled/deleted/applied."""
    srv = _load_server()
    for abort in ("cancelled", "deleted", "applied", "skipped_duplicate"):
        with mock.patch.object(srv, "_job_fill_aborted", return_value=True), mock.patch.object(
            srv, "_patch_job"
        ) as pj, mock.patch.object(srv, "append_fill_activity"):
            srv._mark_fill_thread_stuck("j", RuntimeError("boom"), where="test")
            pj.assert_not_called()


def test_mark_fill_thread_stuck_patches_when_running():
    srv = _load_server()
    with mock.patch.object(srv, "_job_fill_aborted", return_value=False), mock.patch.object(
        srv, "_patch_job"
    ) as pj, mock.patch.object(srv, "append_fill_activity"):
        srv._mark_fill_thread_stuck("j", RuntimeError("boom"), where="test")
        pj.assert_called_once()
        assert pj.call_args.kwargs["status"] == "stuck"


def test_pipeline_milestone_aborts_when_cancelled():
    """DASH-002: milestone must not re-set tailoring after Cancel."""
    srv = _load_server()
    with mock.patch.object(srv, "_job_fill_aborted", return_value=True), mock.patch.object(
        srv, "_patch_job"
    ) as pj, mock.patch.object(srv, "append_fill_activity") as af:
        srv.pipeline_milestone(
            "j",
            event="partyrock",
            detail="Opening PartyRock",
            status="tailoring",
            status_detail="Opening…",
        )
        pj.assert_not_called()
        assert af.called


def test_fill_abort_statuses_cover_hunt_set():
    srv = _load_server()
    needed = {
        "cancelled",
        "deleted",
        "applied",
        "skipped_manual",
        "skipped_duplicate",
        "skipped_contract",
        "skipped_easy_apply",
    }
    assert needed <= set(srv.FILL_ABORT_STATUSES)


def test_app_js_cancel_for_progress_stuck_ready():
    """Cancel/Stop is offered for In Progress, Stuck, and Ready — not Open/Applied."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "canCancel" in src
    cancel_line = [ln for ln in src.splitlines() if "const canCancel" in ln]
    assert cancel_line, "canCancel assignment missing"
    joined = " ".join(cancel_line)
    assert "progress" in joined and "stuck" in joined and "ready" in joined
    assert "Cancel is unavailable on Ready" not in src


def test_max_headed_chrome_mains_default_three():
    """Concurrent dashboard fills default to 3 headed Chrome slots."""
    import importlib.util
    from pathlib import Path

    ff_path = Path(__file__).resolve().parent.parent / "scripts" / "fastfill" / "fast_fill.py"
    spec = importlib.util.spec_from_file_location("ff_headed_cap", ff_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._max_headed_chrome_mains() >= 3
    assert callable(mod.refuse_headed_if_chrome_busy)


def test_app_js_concurrent_fill_no_global_busy_gate():
    """Fill must not be globally disabled while another job is in progress."""
    app_js = (Path(__file__).resolve().parent / "static" / "app.js").read_text(encoding="utf-8")
    assert "anyOtherJobInProgress" not in app_js
    assert "one fill at a time" not in app_js
    assert "_fillFaceBusyJobs" in app_js


def test_app_js_start_fill_mode_no_false_skip_without_pdf():
    """DASH-010: real Fill-with-resume without PDF must not always claim skip."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "jobHasDiskResume(job) || testModeEnabled" in src
    # Old always-skip line must be gone
    assert "const skipPartyrock = normalized === \"with-resume\" || normalized === \"retry\";" not in src


def test_resume_face_previews_and_menu_has_no_duplicate_preview_or_copy_path():
    """Resume face owns preview; its secondary menu only contains file actions."""
    src = APP_JS.read_text(encoding="utf-8")
    popover = src.split("function renderResumePopover", 1)[1].split(
        "function renderDossier", 1
    )[0]
    resume_face = src.split("function executeResumeFace", 1)[1].split(
        "function toggleTreatResumeOnFile", 1
    )[0]

    assert 'id="resume-menu-btn"' in src
    assert "toggleResumeMenu(event)" in src
    assert "previewJobResume(jobId);" in resume_face
    assert "toggleResumePanel(jobId);" not in resume_face
    assert 'id="resume-preview-btn"' not in popover
    assert 'id="resume-docs-btn"' not in popover
    assert "Copy path" not in popover
    assert ">Preview</button>" not in popover
    assert "\n        Upload\n" in popover
    assert ">Edit LaTeX</button>" in popover
    assert ">Clear</button>" in popover


def test_dashboard_uses_conventional_resume_display_name_everywhere():
    """Dashboard labels and preview use server-provided published filename."""
    src = APP_JS.read_text(encoding="utf-8")
    display_name = src.split("function resumeDisplayName", 1)[1].split(
        "function defaultFillMode", 1
    )[0]
    applied = src.split("const resumeName =", 1)[1].split(
        "const editRow =", 1
    )[0]

    assert "job.resume_display_name" in display_name
    assert "job.resume_by_company_path" in display_name
    assert "job.resume_path" not in display_name
    assert "resumeDisplayName(job)" in applied
    assert "${escapeHtml(resumeName)}</a>" in applied
    assert "resumeDisplayName(job)" in src.split("function renderResumePanel", 1)[1]


def test_app_js_cancel_stays_on_current_tab():
    """Cancel must not jump the dossier to Open after abort."""
    src = APP_JS.read_text(encoding="utf-8")
    fn = src.split("async function cancelJob(jobId)", 1)[1].split(
        "\nasync function skipJob", 1
    )[0]
    assert "surfaceOpenJob" not in fn
    assert "await poll()" in fn


def test_app_js_escapes_question_and_cancel_gate():
    """DASH-007 / DASH-009: escapeHtml question; Cancel for in-progress + stuck."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "escapeHtml(job.question" in src
    assert "canCancel" in src
    assert "${canCancel" in src or "(canCancel)" in src
    # Cancel must not show for every non-terminal job (e.g. open/applied).
    assert "bucket === \"open\"" not in src.split("const canCancel", 1)[1].split("\n", 1)[0]


def test_app_js_uses_js_string_escape_on_detail_actions():
    """DASH-007: onclick job ids go through jsStringEscape (Ops)."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "jsStringEscape(job.id)" in src
    assert "onclick=\"cancelJob('${job.id}')\"" not in src
    assert "onclick=\"startJob('${job.id}')\"" not in src


def test_restore_handler_accepts_deleted_in_source():
    """DASH-006: API restores deleted (UI Restore is no longer a lie)."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    assert '"deleted"' in src.split("def _handle_restore", 1)[1].split("def _handle_mark_submitted", 1)[0]
    assert "unblock_job" in src
    assert "only deleted (or legacy skipped/cancelled) jobs can be restored" in src


def test_delete_kills_running_proc_in_source():
    """DASH-003: soft-delete kills tracked proc + aborts gateway session."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    # Soft-delete branch must kill before responding
    delete_chunk = src.split("def do_DELETE", 1)[1].split("def _handle_answer", 1)[0]
    assert "_kill_process_tree" in delete_chunk
    assert "abort_gateway_session" in delete_chunk


def test_handle_cancel_allows_stuck_in_source():
    """Stuck/CAPTCHA jobs can cancel even without a live proc."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    chunk = src.split("def _handle_cancel", 1)[1].split("def _handle_skip", 1)[0]
    assert "NOTIFY_STATUSES" in chunk
    assert "IN_PROGRESS_STATUSES | NOTIFY_STATUSES" in chunk


def test_partyrock_parallel_no_global_lock():
    """Parallel PartyRock: no global tailor lock serializes jobs."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    assert "_partyrock_lock" not in src
    assert "_acquire_partyrock_lock" not in src
    assert "PartyRockLockAborted" not in src
    assert "Waiting for another job to finish using PartyRock" not in src
    chunk = src.split("def _run_tailor_then_fill_body", 1)[1].split(
        "def run_agent_message", 1
    )[0]
    assert "--job-id" in chunk
    assert "close_job_partyrock_tab" in chunk


def test_claim_fill_job_blocks_duplicate_thread():
    """Only one tailor/fill pipeline thread per job_id at a time."""
    srv = _load_server()
    srv._active_fill_jobs.clear()
    assert srv._claim_fill_job("dup-job") is True
    assert srv._claim_fill_job("dup-job") is False
    assert srv._claim_fill_job("other-job") is True
    srv._release_fill_job("dup-job")
    assert srv._claim_fill_job("dup-job") is True
    srv._active_fill_jobs.clear()


def test_fill_run_stale_accepts_locked_fill_gen():
    """Post-tectonic path holds _lock; stale check must not re-enter it."""
    srv = _load_server()
    tok = srv._fill_run_ctx.set(("lock-job", 3))
    try:
        assert srv._fill_run_stale("lock-job", fill_gen=3) is False
        assert srv._fill_run_stale("lock-job", fill_gen=4) is True
        # While holding _lock, fill_gen=… must return without calling _job_fill_gen.
        with srv._lock:
            with mock.patch.object(
                srv, "_job_fill_gen", side_effect=AssertionError("must not re-lock")
            ):
                assert srv._fill_run_stale("lock-job", fill_gen=3) is False
    finally:
        srv._fill_run_ctx.reset(tok)


def test_post_tectonic_stale_check_passes_fill_gen_in_source():
    """Regression: bare _fill_run_stale(job_id) under _lock deadlocks the server."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    helper = src.split("def _persist_compiled_resume_after_tectonic", 1)[1].split(
        "def _claim_fill_job", 1
    )[0]
    assert "fill_gen=int(job.get(\"fill_gen\") or 0)" in helper
    # Call sites only (docstring may mention the bare form as a warning).
    call_lines = [
        ln for ln in helper.splitlines()
        if "_fill_run_stale(" in ln and ln.lstrip().startswith(("if ", "and ", "return "))
    ]
    assert call_lines, "expected an _fill_run_stale call in helper"
    assert all("fill_gen=" in ln for ln in call_lines), call_lines
    assert "_persist_compiled_resume_after_tectonic(" in src.split(
        "Converting resume to PDF (tectonic)", 1
    )[1].split("if not compile_ok", 1)[0]


def test_post_tectonic_handoff_completes_under_real_locks():
    """End-to-end: tectonic handoff with real _lock + EX flock must not deadlock.

    The production hang was: with _lock + locked_jobs_for_write, then bare
    _fill_run_stale(job_id) → _job_fill_gen → with _lock again.
    """
    import tempfile

    srv = _load_server()
    job_id = "deadlock-tectonic-1"
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        jobs_file = td_path / "jobs.json"
        lock_file = td_path / "jobs.json.lock"
        resumes = td_path / "resumes" / job_id
        resumes.mkdir(parents=True)
        resume_pdf = resumes / "resume.pdf"
        # Minimal PDF header so resolve/sync treats it as a real file.
        resume_pdf.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
        jobs = {
            "revision": 1,
            "jobs": [
                {
                    "id": job_id,
                    "status": "tailoring",
                    "status_detail": "Converting resume to PDF…",
                    "fill_gen": 2,
                    "title": "Dummy ML Role",
                    "company": "Dummy Co",
                    "location": "Remote",
                    "apply_url": "https://example.test/apply/dummy",
                    "session_key": f"agent:job-hunter:job-{job_id}",
                    "job_description": "Dummy JD for lock regression — not real PII.",
                }
            ],
        }
        jobs_file.write_text(json.dumps(jobs), encoding="utf-8")
        lock_file.touch()

        done = threading.Event()
        err: list[BaseException] = []

        def _run() -> None:
            try:
                with (
                    mock.patch.object(srv, "JOBS_FILE", jobs_file),
                    mock.patch.object(srv, "JOBS_LOCK_FILE", lock_file),
                    mock.patch.object(srv, "ROOT", td_path),
                    mock.patch.object(srv._jobs_lock_mod, "JOBS_FILE", jobs_file),
                    mock.patch.object(srv._jobs_lock_mod, "LOCK_FILE", lock_file),
                ):
                    tok = srv._fill_run_ctx.set((job_id, 2))
                    try:
                        srv._persist_compiled_resume_after_tectonic(
                            job_id,
                            resume_pdf=resume_pdf,
                            compile_ok=True,
                            compile_exit=0,
                            compile_log=td_path / "tectonic_dummy.log",
                        )
                    finally:
                        srv._fill_run_ctx.reset(tok)
            except BaseException as e:
                err.append(e)
            finally:
                done.set()

        t = threading.Thread(target=_run, name="post-tectonic-handoff", daemon=True)
        t.start()
        if not done.wait(5.0):
            raise AssertionError(
                "post-tectonic handoff deadlocked (did not finish within 5s) — "
                "likely bare _fill_run_stale under _lock"
            )
        t.join(timeout=1.0)
        if err:
            raise err[0]

        saved = json.loads(jobs_file.read_text(encoding="utf-8"))
        job = next(j for j in saved["jobs"] if j["id"] == job_id)
        assert job["status"] == "navigating", job
        assert job.get("resume_on_disk") is True, job
        assert job.get("resume_path"), job
        assert "Preparing fill" in (job.get("status_detail") or "")


def test_post_tectonic_pipeline_advances_past_tailoring():
    """Stubbed PartyRock/tectonic/fill: status leaves tailoring after compile."""
    import tempfile

    srv = _load_server()
    job_id = "pipe-tectonic-2"
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        jobs_file = td_path / "jobs.json"
        lock_file = td_path / "jobs.json.lock"
        resumes = td_path / "resumes" / job_id
        resumes.mkdir(parents=True)
        (resumes / "jd_full.txt").write_text(
            "Dummy job description for pipeline lock test.\n", encoding="utf-8"
        )
        jobs = {
            "revision": 1,
            "jobs": [
                {
                    "id": job_id,
                    "status": "tailoring",
                    "status_detail": "Waiting on resume from PartyRock…",
                    "fill_gen": 1,
                    "title": "Dummy Data Scientist",
                    "company": "Fixture Corp",
                    "location": "Austin, TX",
                    "apply_url": "https://boards.example.test/jobs/dummy",
                    "session_key": f"agent:job-hunter:job-{job_id}",
                    "job_description": "Short dummy JD.",
                }
            ],
        }
        jobs_file.write_text(json.dumps(jobs), encoding="utf-8")
        lock_file.touch()
        subprocess_commands: list[list[str]] = []

        def _fake_subprocess(cmd, log_name, timeout_s, **kwargs):
            subprocess_commands.append([str(c) for c in cmd])
            log_path = td_path / "logs"
            log_path.mkdir(exist_ok=True)
            out = log_path / log_name
            out.write_text("ok\n", encoding="utf-8")
            cmd0 = " ".join(str(c) for c in cmd)
            job_dir = resumes
            if "tailor_resume" in cmd0 or "TAILOR" in cmd0.upper():
                # Dummy LaTeX only — never real applicant content.
                (job_dir / "resume.tex").write_text(
                    "\\documentclass{article}\\begin{document}Dummy\\end{document}\n",
                    encoding="utf-8",
                )
                return 0, out
            if "tectonic" in cmd0:
                (job_dir / "resume.pdf").write_bytes(
                    b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ntrailer<<>>\n%%EOF\n"
                )
                return 0, out
            if "fit_resume_pages" in cmd0:
                return 0, out
            return 0, out

        fill_calls: list[tuple] = []

        def _fake_fill(*args, **kwargs):
            fill_calls.append((args, kwargs))

        done = threading.Event()
        err: list[BaseException] = []

        def _run() -> None:
            try:
                with (
                    mock.patch.object(srv, "JOBS_FILE", jobs_file),
                    mock.patch.object(srv, "JOBS_LOCK_FILE", lock_file),
                    mock.patch.object(srv, "ROOT", td_path),
                    mock.patch.object(srv, "RESUMES_DIR", td_path / "resumes"),
                    mock.patch.object(srv, "INBOUND_MEDIA_DIR", td_path / "inbound"),
                    mock.patch.object(srv._jobs_lock_mod, "JOBS_FILE", jobs_file),
                    mock.patch.object(srv._jobs_lock_mod, "LOCK_FILE", lock_file),
                    mock.patch.object(srv, "_run_subprocess_step", _fake_subprocess),
                    mock.patch.object(
                        srv, "_ensure_openclaw_managed_browser", return_value={}
                    ),
                    mock.patch.object(srv, "close_job_partyrock_tab", return_value=None),
                    mock.patch.object(
                        srv, "partyrock_url", return_value="https://partyrock.example.test/"
                    ),
                    mock.patch.object(srv, "partyrock_mode_label", return_value="Testing"),
                    mock.patch.object(srv, "run_hybrid_fill_dummy", _fake_fill),
                    mock.patch.object(srv, "_publish_resume_by_company", return_value=None),
                    mock.patch.object(srv, "_cleanup_old_inbound_resumes"),
                    mock.patch.object(srv, "run_agent_message"),
                ):
                    tok = srv._bind_fill_run_ctx(job_id, 1)
                    try:
                        srv._run_tailor_then_fill_body(
                            job_id,
                            test_mode=True,
                            skip_partyrock=False,
                            force_partyrock=True,
                            restore_status="discovered",
                            fill_run_gen=1,
                        )
                    finally:
                        srv._fill_run_ctx.reset(tok)
            except BaseException as e:
                err.append(e)
            finally:
                done.set()

        t = threading.Thread(target=_run, name="tailor-fill-pipe", daemon=True)
        t.start()
        if not done.wait(15.0):
            raise AssertionError(
                "tailor→fill pipeline deadlocked or hung (>15s) after compile handoff"
            )
        t.join(timeout=1.0)
        if err:
            raise err[0]

        saved = json.loads(jobs_file.read_text(encoding="utf-8"))
        job = next(j for j in saved["jobs"] if j["id"] == job_id)
        assert job["status"] != "tailoring", job
        assert job.get("resume_path"), job
        assert job.get("resume_on_disk") is True, job
        assert fill_calls, "expected run_hybrid_fill_dummy after successful compile"
        assert job["status"] in ("navigating", "filling", "ready_for_review", "stuck") or fill_calls
        tailor_cmd = next(cmd for cmd in subprocess_commands if "tailor_resume.py" in " ".join(cmd))
        assert "--keep-open" not in tailor_cmd


def test_browser_cold_start_sweeps_restored_idle_partyrock_tabs():
    srv = _load_server()
    started = {"ok": True, "via": "cft_direct", "started": True}
    with mock.patch.object(
        srv, "ensure_partyrock_browser_direct", return_value=started
    ), mock.patch.object(
        srv,
        "close_idle_partyrock_tabs",
        return_value={"closed": ["OLD"], "failed": [], "protected": []},
    ) as sweep:
        result = srv._ensure_openclaw_managed_browser(required=True)

    assert result == started
    assert sweep.called


def test_browser_already_running_sweeps_idle_partyrock_tabs():
    """Leftover PartyRock tabs must be swept when CfT is already up (not cold-start only)."""
    srv = _load_server()
    started = {"ok": True, "via": "already_running", "started": True}
    with mock.patch.object(
        srv, "ensure_partyrock_browser_direct", return_value=started
    ), mock.patch.object(
        srv,
        "close_idle_partyrock_tabs",
        return_value={"closed": ["OLD"], "failed": [], "protected": []},
    ) as sweep:
        result = srv._ensure_openclaw_managed_browser(required=True)

    assert result == started
    assert sweep.called
    kwargs = sweep.call_args.kwargs
    assert kwargs.get("resumes_dir") == srv.RESUMES_DIR


def test_bare_fill_run_stale_under_lock_would_deadlock():
    """Watchdog proof: re-entering _lock via bare _fill_run_stale hangs.

    Uses a private Lock so a stuck daemon cannot poison the module ``_lock``.
    ContextVar must be set on the worker thread (ContextVars are not shared).
    """
    srv = _load_server()
    probe = threading.Lock()
    hung = threading.Event()
    finished = threading.Event()

    def _boom() -> None:
        tok = srv._fill_run_ctx.set(("reentry-job", 1))
        try:
            with probe:
                hung.set()
                try:
                    srv._fill_run_stale("reentry-job")  # no fill_gen= → re-enters probe
                finally:
                    finished.set()
        finally:
            srv._fill_run_ctx.reset(tok)

    with mock.patch.object(srv, "_lock", probe):
        t = threading.Thread(target=_boom, daemon=True)
        t.start()
        assert hung.wait(1.0), "worker never acquired probe lock"
        assert not finished.wait(0.4), (
            "bare _fill_run_stale under _lock did not hang (unexpected)"
        )


def test_bind_fill_run_ctx_uses_explicit_gen():
    """Start must pass bumped fill_gen so the new thread is not stale."""
    srv = _load_server()
    jobs = {
        "jobs": [
            {
                "id": "job-x",
                "status": "tailoring",
                "fill_gen": 7,
                "session_key": "agent:job-hunter:job-x",
            }
        ]
    }
    tok = srv._bind_fill_run_ctx("job-x", 7)
    try:
        with mock.patch.object(srv, "read_jobs", return_value=jobs):
            assert srv._fill_run_stale("job-x") is False
            jobs["jobs"][0]["fill_gen"] = 8
            assert srv._fill_run_stale("job-x") is True
    finally:
        srv._fill_run_ctx.reset(tok)


def test_app_js_optimistic_fill_start():
    """Fill should flip to in-progress locally before /start returns."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "applyFillStartLocally" in src
    assert "applyFillStartLocally(jobId" in src
    fn = src.split("function applyFillStartLocally(", 1)[1].split(
        "\nasync function startJob", 1
    )[0]
    assert 'job.status = skipPartyrock ? "navigating" : "tailoring"' in fn
    assert "render()" in fn
    # Stay on Open (or current tab); status change alone drops it from Open list.
    assert 'setQueue("progress")' not in fn


def test_app_js_start_switches_to_progress_tab():
    """Start must NOT switch tabs — job leaves Open via status, dossier stays."""
    src = APP_JS.read_text(encoding="utf-8")
    start_fn = src.split("async function startJob(jobId", 1)[1].split(
        "\nfunction toggleTestMode(", 1
    )[0]
    assert 'setQueue("progress")' not in start_fn
    apply_fn = src.split("function applyFillStartLocally(", 1)[1].split(
        "\nasync function startJob", 1
    )[0]
    assert 'setQueue("progress")' not in apply_fn
    assert "Pin selected job when Start moved it to progress" not in src
    vis = src.split("function visibleJobs()", 1)[1].split(
        "\nfunction groupPriorityStatus(", 1
    )[0]
    # Generating job must leave the Open list (no selectedId pin).
    assert "selectedId" not in vis
    dossier = src.split("function renderDossier()", 1)[1].split(
        "\nfunction bindDossierPopoverHandlers", 1
    )[0]
    # Same selected job still renders after it leaves the current queue list.
    assert "jobs.find(j => j.id === selectedId)" in dossier
    assert "visibleJobs()" not in dossier


def test_app_js_company_apply_count_badge():
    """Sidebar company rows show red applied-count tag via normalized companyKey."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "companyApplyCountBadgeHtml" in src
    assert "rebuildCompanyAppliedCounts" in src
    assert "normalizeCompanyName" in src
    assert 'j.status !== "applied"' in src
    badge_fn = src.split("function companyApplyCountBadgeHtml", 1)[1].split("\nfunction ", 1)[0]
    assert 'class="tag applied-count"' in badge_fn
    assert "${n}x" in badge_fn
    row = src.split("function renderJobRow(", 1)[1].split("\nfunction ", 1)[0]
    assert 'class="co"' in row
    assert "companyApplyCountBadgeHtml(job)" in row
    assert 'class="tag clearance"' in row
    assert 'class="tag us-person"' in row
    sib = src.split("function toggleCompanySiblings(", 1)[1].split("\nfunction ", 1)[0]
    assert "normalizeCompanyName(company)" in sib
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert ".tag.applied-count" in html
    assert ".company-apply-count" not in html
    proc = subprocess.run(
        ["node", str(HERE / "test_company_key.js")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "OK test_company_key.js" in proc.stdout


def test_app_js_mark_applied_while_in_progress():
    """Mark as applied visible during fill/tailor/stuck — not gated on !runInProgress."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "canMarkApplied" in src
    assert "bucket !== \"applied\" && !runInProgress" not in src
    assert "PROGRESS_STATUSES.has(status) || STUCK_STATUSES.has(status)" in src
    assert "cancels any running fill/tailor" in src


def test_force_stuck_orphaned_in_progress_ignores_age_on_startup():
    """DASH2-002: startup reconcile force-sticks orphans with no live proc."""
    srv = _load_server()
    srv._running_procs.clear()
    stale = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    jobs = {
        "jobs": [
            {
                "id": "orphan-fill",
                "status": "filling",
                "status_detail": "Filling…",
                "session_key": "agent:job-hunter:job-orphan-fill",
                "updated_at": stale,
            }
        ]
    }

    @contextmanager
    def _fake_locked(*, allow_purge=False):
        yield jobs

    with mock.patch.object(
        srv, "locked_jobs_for_write", _fake_locked
    ), mock.patch.object(
        srv, "close_job_partyrock_tab", return_value={"closed": True}
    ) as close_tab:
        forced = srv._force_stuck_orphaned_in_progress(ignore_age=True)
        assert forced == ["orphan-fill"]
        assert jobs["jobs"][0]["status"] == "stuck"
        close_tab.assert_called_once_with(
            "orphan-fill", srv.RESUMES_DIR / "orphan-fill"
        )


def test_force_stuck_orphaned_respects_stale_age():
    """DASH2-002: reconcile path waits STALE_AFTER_S before force-stuck."""
    srv = _load_server()
    srv._running_procs.clear()
    fresh = datetime.now(timezone.utc).isoformat()
    jobs = {
        "jobs": [
            {
                "id": "fresh-fill",
                "status": "navigating",
                "status_detail": "Opening…",
                "session_key": "agent:job-hunter:job-fresh-fill",
                "updated_at": fresh,
            }
        ]
    }

    @contextmanager
    def _fake_locked(*, allow_purge=False):
        yield jobs

    with mock.patch.object(srv, "locked_jobs_for_write", _fake_locked):
        forced = srv._force_stuck_orphaned_in_progress(ignore_age=False)
        assert forced == []
        assert jobs["jobs"][0]["status"] == "navigating"


def test_force_stuck_orphaned_when_past_stale():
    srv = _load_server()
    srv._running_procs.clear()
    old = (
        datetime.now(timezone.utc) - timedelta(seconds=srv.STALE_AFTER_S + 5)
    ).isoformat()
    jobs = {
        "jobs": [
            {
                "id": "old-fill",
                "status": "filling",
                "status_detail": "Filling…",
                "session_key": "agent:job-hunter:job-old-fill",
                "updated_at": old,
            }
        ]
    }

    @contextmanager
    def _fake_locked(*, allow_purge=False):
        yield jobs

    with mock.patch.object(srv, "locked_jobs_for_write", _fake_locked):
        forced = srv._force_stuck_orphaned_in_progress(ignore_age=False)
        assert forced == ["old-fill"]
        assert jobs["jobs"][0]["status"] == "stuck"


def test_mark_submitted_kills_proc_in_source():
    """DASH2-006: mark-as-applied must kill fill proc like Cancel."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    chunk = src.split("def _handle_mark_submitted", 1)[1].split(
        "def _handle_edit_applied", 1
    )[0]
    assert "_kill_process_tree" in chunk
    assert "abort_gateway_session" in chunk


def test_app_js_shows_restore_for_deleted():
    """DASH2-003: Ops detail offers Restore; restoreJob posts /restore."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "restoreJob(" in src
    assert 'job.status === "deleted"' in src
    assert "/restore" in src
    assert ">Restore</button>" in src or "Restore</button>" in src


def test_ready_hold_copy_no_cancel():
    """DASH2-013: Ready hold detail must not promise Cancel."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    assert "Close the browser or Cancel when done" not in src
    assert "Mark as applied after you submit on the employer site" in src
    assert "close the browser when done reviewing" in src


def test_ingest_hold_honors_full_abort_set():
    """UI-027 / DASH2-014: hold ingest uses FILL_ABORT_STATUSES, not a subset."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    chunk = src.split("def ingest_fill_stdout_line", 1)[1].split(
        "def _classify_pipeline_stdout_line", 1
    )[0]
    assert "FILL_ABORT_STATUSES" in chunk
    assert 'in ("cancelled", "skipped_manual", "applied", "deleted")' not in chunk


def test_pipeline_milestone_skips_detail_when_aborted():
    """DASH2-018: aborted jobs skip status_detail-only milestone patches too."""
    srv = _load_server()
    with mock.patch.object(srv, "_job_fill_aborted", return_value=True), mock.patch.object(
        srv, "_patch_job"
    ) as pj, mock.patch.object(srv, "append_fill_activity") as af:
        srv.pipeline_milestone(
            "j",
            event="pdf",
            detail="Fitting…",
            status_detail="Fitting resume…",
        )
        pj.assert_not_called()
        assert af.called


def test_app_js_date_older_includes_unknown():
    """Posted 'older' keeps missing/~ dates; only exact young dates drop out."""
    src = APP_JS.read_text(encoding="utf-8")
    chunk = src.split("function jobMatchesDateFilter", 1)[1].split(
        "function jobMatchesSalaryFilter", 1
    )[0]
    assert 'dateFilter === "older"' in chunk
    assert "listFilterUnsurePasses" in chunk
    assert "jobPostedDisplay" in chunk


def test_agent_fallback_missing_tex_forces_stuck_in_source():
    """DASH2-004: missing resume.tex after agent fallback → stuck, not silent return."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    chunk = src.split("Manual PartyRock fallback did not produce resume.tex", 1)[1][
        :1200
    ]
    assert 'status="stuck"' in chunk
    assert "_patch_job" in chunk
    assert "don't force a status" not in chunk


def test_app_js_surfaces_deleted_and_skip_without_classic():
    """UI-033 successor: Ops (/) owns skip/deleted surfacing; no skipped queue tab."""
    app = APP_JS.read_text(encoding="utf-8")
    assert "async function skipJob(" in app
    assert "surfaceDeletedJob" in app
    assert 'data-queue="skipped"' not in (HERE / "static" / "index.html").read_text(encoding="utf-8")


def test_classic_routes_redirect_to_ops():
    """UI-033: /classic, classic.html, classic.js, ops-preview* HTTP-redirect to Ops /."""
    src = (HERE / "server.py").read_text(encoding="utf-8")
    assert 'parts[0] in ("classic", "classic.html", "classic.js")' in src
    classic_chunk = src.split('("classic", "classic.html", "classic.js")', 1)[1][:400]
    assert "send_response(302)" in classic_chunk
    assert 'send_header("Location", "/")' in classic_chunk
    assert 'parts[0] in ("ops-preview", "ops-preview.html")' in src
    ops_chunk = src.split('("ops-preview", "ops-preview.html")', 1)[1][:400]
    assert "send_response(302)" in ops_chunk
    assert 'send_header("Location", "/")' in ops_chunk
    assert '_send_file(STATIC_DIR / "classic.html"' not in src
    assert not (HERE / "static" / "classic.html").exists()
    assert not (HERE / "static" / "classic.js").exists()
    assert not (HERE / "static" / "ops-preview.html").exists()


def test_app_js_surfaces_applied_after_mark_submitted():
    """Mark applied updates status locally; view stays on the current tab."""
    app = APP_JS.read_text(encoding="utf-8")
    assert "surfaceAppliedJob" in app
    assert "applyMarkedAppliedLocally" in app
    chunk = app.split("async function markSubmitted", 1)[1].split(
        "async function uploadJobResume", 1
    )[0]
    assert "if (!ok)" in chunk
    assert "applyMarkedAppliedLocally(jobId)" in chunk
    assert "surfaceAppliedJob(jobId)" in chunk
    assert "setQueue" not in chunk
    surface = app.split("function surfaceAppliedJob", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "setQueue" not in surface
    poll_chunk = app.split("async function poll()", 1)[1].split(
        "// ------------------------------------------------------------ Utility pane", 1
    )[0]
    assert "_pollSeq" in poll_chunk
    assert "seq !== _pollSeq" in poll_chunk
    assert "setQueue" not in poll_chunk
    assert "maybeAutoSelectQueue" not in poll_chunk


def test_launch_script_reloads_focused_ui_on_reuse():
    """Blank CfT --app= tabs after server downtime must reload on focus."""
    src = (HERE / "launch_dashboard.sh").read_text(encoding="utf-8")
    assert 'grep -q \'class="ops-shell"\'' in src
    assert "reload_dashboard_ui_window" in src
    open_chunk = src.split("open_dashboard_ui() {", 1)[1].split("\n}\n", 1)[0]
    assert "focus_dashboard_ui" in open_chunk
    assert "reload_dashboard_ui_window" in open_chunk


def test_launch_script_post_reboot_port_and_python():
    """Post-reboot: scan ports, persist choice, prefer repo venv python."""
    src = (HERE / "launch_dashboard.sh").read_text(encoding="utf-8")
    assert "resolve_dashboard_port" in src
    assert "port_is_bindable" in src
    assert "SO_REUSEADDR" in src
    assert "wait_for_preferred_port_ready" in src
    assert '[[ "${RESTARTING}" -eq 1 ]]' in src
    assert "resolve_dashboard_python" in src
    assert "remember_dashboard_port" in src
    assert "restore_dashboard_port_from_file" in src
    assert 'open -a "Google Chrome"' in src
    assert ".venv/bin/python3" in src
    assert "skyvern_runtime/venv/bin/python3" in src
    assert "JOBHUNTER_DASHBOARD_PORT" in src


def test_read_jobs_recovers_corrupt_json(tmp_path):
    """Corrupt jobs.json must not crash dashboard reads."""
    srv = _load_server()
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text("{not json", encoding="utf-8")
    backup = tmp_path / "jobs.json.bak-recovery"
    backup.write_text(
        json.dumps({"jobs": [{"id": "ok", "status": "discovered"}]}),
        encoding="utf-8",
    )
    with mock.patch.object(srv, "JOBS_FILE", jobs_file), mock.patch.object(
        srv, "JOBS_LOCK_FILE", tmp_path / "jobs.json.lock"
    ):
        data = srv.read_jobs()
    assert data["jobs"][0]["id"] == "ok"
    assert jobs_file.read_text(encoding="utf-8").startswith("{")
    assert not jobs_file.read_text(encoding="utf-8").startswith("{not")


def test_read_jobs_empty_when_corrupt_and_no_backup(tmp_path):
    srv = _load_server()
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text("{broken", encoding="utf-8")
    with mock.patch.object(srv, "JOBS_FILE", jobs_file), mock.patch.object(
        srv, "JOBS_LOCK_FILE", tmp_path / "jobs.json.lock"
    ):
        data = srv.read_jobs()
    assert data == {"jobs": []}


def test_app_js_connection_error_banner():
    """Poll failures surface a visible reconnect banner (not a silent blank shell)."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "showConnectionBanner" in src
    assert "connection-error-bar" in src
    assert 'addEventListener("online"' in src


def test_app_js_timeline_starts_collapsed_and_auto_collapses():
    """Timeline boots closed; expands are temporary (10s timer + focus-loss)."""
    src = APP_JS.read_text(encoding="utf-8")
    html = (HERE / "static" / "index.html").read_text(encoding="utf-8")
    assert "TL_AUTO_COLLAPSE_MS = 10000" in src
    assert "scheduleTimelineAutoCollapse" in src
    assert "eventInsideTimeline" in src
    assert "timelineHasUserAttention" in src
    assert "armTimelineAutoCollapseOnInteraction" in src
    assert 'addEventListener("scroll", armTimelineAutoCollapseOnInteraction' in src
    assert 'addEventListener("wheel", armTimelineAutoCollapseOnInteraction' in src
    assert "let timelineCollapsed = true" in src
    assert 'class="ops-body tl-collapsed"' in html
    assert 'id="timeline-pane"' in html and "collapsed" in html
    assert 'title="Expand timeline"' in html


def test_app_js_start_failure_invalidates_etag_not_just_json():
    """Failed Start must clear ETag too — else 304 keeps optimistic in-progress UI."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "function invalidateJobsListCache()" in src
    assert "lastJobsEtag = null" in src
    start_fn = src.split("async function startJob(jobId", 1)[1].split(
        "\nfunction toggleTestMode(", 1
    )[0]
    assert "invalidateJobsListCache()" in start_fn
    assert start_fn.count("invalidateJobsListCache()") >= 2
    poll_fn = src.split("async function poll()", 1)[1].split("\nfunction openUtil(", 1)[0]
    assert "if (lastJobsJSON == null)" in poll_fn
    assert 'await fetch("/api/jobs")' in poll_fn


def test_app_js_optimistic_fill_restores_fast_poll():
    """Local fill start must bump idle→active poll cadence immediately."""
    src = APP_JS.read_text(encoding="utf-8")
    fn = src.split("function applyFillStartLocally(", 1)[1].split(
        "\nasync function startJob", 1
    )[0]
    assert "syncPollTimers()" in fn


def test_temp_applied_count_override_is_null():
    """TEMP_APPLIED_COUNT_OVERRIDE must stay null outside deliberate UI experiments."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "const TEMP_APPLIED_COUNT_OVERRIDE = null;" in src


def test_kpi_counts_use_per_family_filter_state():
    """Each mission KPI uses its queue family's saved filters, not the active tab's."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "function jobMatchesListFilters(" in src
    assert "function filterFamilyForBucket(" in src
    assert "function filterStateForFamily(" in src
    assert "function jobMatchesListFiltersForFamily(" in src
    count_fn = src.split("function countBucket(bucket", 1)[1].split(
        "\nfunction renderStats", 1
    )[0]
    assert "jobMatchesListFiltersForFamily" in count_fn
    vis = src.split("function visibleJobs()", 1)[1].split(
        "\nfunction groupPriorityStatus(", 1
    )[0]
    assert "jobMatchesListFilters" in vis


def test_list_filters_persist_per_family_in_localstorage():
    """List filters are a per-family localStorage map; tab switches swap them."""
    src = APP_JS.read_text(encoding="utf-8")
    assert 'const FILTER_STATE_KEY = "opsFilterState"' in src
    assert "FILTER_FAMILY_KEYS" in src
    assert '"pipeline"' in src
    assert "filterFamilyForQueue" in src
    save_fn = src.split("function saveFilterState()", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "filterStateByFamily" in save_fn
    assert "persistFilterMap" in save_fn
    assert "sessionStorage" not in save_fn
    setq = src.split("function setQueue(next)", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "swapQueueFilterState" in setq


def _setqueue_call_lines(src: str) -> list[str]:
    return [ln.strip() for ln in src.splitlines() if "setQueue(" in ln]


def test_app_js_setqueue_only_from_user_tab_clicks():
    """KPI / Deleted trash clicks may setQueue; Start/poll/stuck/skip/restore must not."""
    src = APP_JS.read_text(encoding="utf-8")
    lines = _setqueue_call_lines(src)
    assert lines == [
        "function setQueue(next) {",
        'setQueue(queue === "deleted" ? "open" : "deleted");',
        "if (q) setQueue(q);",
    ]
    bind = src.split("function bindOpsChrome()", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "if (q) setQueue(q);" in bind
    assert 'e.target.closest(".mstat")' in bind

    for name, splitter in (
        ("applyFillStartLocally", "\nasync function startJob"),
        ("surfaceDeletedJob", "\nfunction surfaceOpenJob"),
        ("surfaceOpenJob", "\nfunction surfaceAppliedJob"),
        ("surfaceAppliedJob", "\nfunction applyMarkedAppliedLocally"),
        ("restoreJob", "\nasync function markSubmitted"),
        ("skipJob", "\nasync function restoreJob"),
        ("deleteJob", "\nasync function emptyDeleted"),
        ("cancelJob", "\nasync function skipJob"),
        ("poll", "// ------------------------------------------------------------ Utility pane"),
    ):
        prefix = "async function " if name in (
            "restoreJob", "skipJob", "deleteJob", "cancelJob", "poll",
        ) else "function "
        if name == "applyFillStartLocally":
            chunk = src.split("function applyFillStartLocally(", 1)[1].split(
                splitter, 1
            )[0]
        elif name == "poll":
            chunk = src.split("async function poll()", 1)[1].split(splitter, 1)[0]
        else:
            chunk = src.split(f"{prefix}{name}", 1)[1].split(splitter, 1)[0]
        assert "setQueue" not in chunk, f"{name} must not auto-switch queue tabs"

    assert 'setQueue("stuck")' not in src
    assert 'setQueue("progress")' not in src
    assert 'setQueue("ready")' not in src
    assert 'setQueue("applied")' not in src


def test_header_tooltip_css_covers_nested_buttons_without_click_capture():
    """Wrap-nested header buttons need descendant tooltip selectors; ::after no hits."""
    html = (HERE / "static" / "index.html").read_text(encoding="utf-8")
    assert ".header-actions [data-tooltip]" in html
    assert ".header-actions > [data-tooltip]" not in html
    tip_block = html.split(".header-actions [data-tooltip]::after", 1)[1].split(
        ".header-actions [data-tooltip]:hover::after", 1
    )[0]
    assert "pointer-events: none" in tip_block


def test_app_js_job_sort_fallbacks():
    """A failed job_sort.js load must not brick list render."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "typeof compareByPosted !== \"function\"" in src
    assert "job_sort.js missing" in src


def test_deleted_toolbar_filters_and_empty_deleted_side_by_side():
    """Deleted queue: Filters and Empty deleted share the toolbar 50/50."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    src = APP_JS.read_text(encoding="utf-8")
    assert 'class="list-filters-toolbar"' in html
    assert re.search(
        r'<div class="list-filters-toolbar">\s*<div class="list-filters"',
        html,
    )
    assert re.search(
        r'id="list-filters">[\s\S]*id="filters-toggle"[\s\S]*</div>\s*'
        r'<button type="button" class="empty-deleted-btn" id="empty-deleted-btn"',
        html,
    )
    assert "Empty deleted" in html
    assert html.count('id="empty-deleted-btn"') == 1
    assert "flex: 1 1 50%" in html
    empty_fn = src.split("async function emptyDeleted()", 1)[1].split(
        "\nasync function ", 1
    )[0]
    assert 'confirm(`Permanently remove ${n} deleted job(s)?' in empty_fn
    assert '"/api/jobs/empty-deleted"' in empty_fn
    assert 'classList.toggle("visible", queue === "deleted")' in src


def test_description_api_prefers_jd_full_over_preview():
    """Evidence JD must be the full on-disk posting, not the 500-char preview."""
    srv = _load_server()
    job_id = "nextgenfed-senior-statistician"
    preview = (
        "NextGen Federal Systems, LLC (NextGen) is seeking a Senior "
        "Statistician … [full text in resumes/<id>/jd_full.txt]"
    )
    full = (
        "NextGen Federal Systems, LLC (NextGen) is seeking a Senior Statistician "
        "at Scott AFB.\n\nPosition Requirements\nExtensive experience in "
        "statistical analysis, including quantitative and qualitative methods.\n"
        "Desired Skills\nAdvanced skills in Excel, JMP, MATLAB, R, SAS.\n"
        "NextGen is an Equal Opportunity Employer.\n"
    )
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        resumes = td_path / "resumes" / job_id
        resumes.mkdir(parents=True)
        (resumes / "jd_full.txt").write_text(full, encoding="utf-8")
        job = {
            "id": job_id,
            "title": "Senior Statistician",
            "company": "nextgenfed",
            "job_description": preview,
        }
        with mock.patch.object(srv, "RESUMES_DIR", td_path / "resumes"):
            raw, source = srv.load_raw_job_description(job)
            cleaned = srv.sanitize_job_description_for_display(raw)
        assert source == "jd_full.txt"
        assert "Position Requirements" in cleaned
        assert "Equal Opportunity Employer" in cleaned
        assert "full text in resumes" not in cleaned
        assert len(cleaned) > len(preview)


def test_header_branding_omnidex_without_insights():
    """Logo is OmniDex; Insights toggle must not sit under the KPI strip."""
    html = (HERE / "static" / "index.html").read_text(encoding="utf-8")
    assert "<title>OmniDex</title>" in html
    assert '<div class="mark">OmniDex</div>' in html
    assert "Orbitron" in html
    assert 'id="insights"' not in html
    assert "<summary>Insights</summary>" not in html
    assert 'id="mission-stats"' in html
    assert 'id="stat-stuck"' in html
    # Refresh / Quit live under the OmniDex logo hover menu, not header-actions.
    assert 'id="brand-wrap"' in html
    assert 'id="brand-popover"' in html
    brand_idx = html.index('id="brand-wrap"')
    pop_idx = html.index('id="brand-popover"')
    refresh_idx = html.index('id="refresh-btn"')
    quit_idx = html.index('id="quit-btn"')
    header_actions_idx = html.index('id="header-actions"')
    assert brand_idx < pop_idx < refresh_idx < quit_idx < header_actions_idx
    assert "restartDashboard()" in html[refresh_idx : refresh_idx + 200]
    assert "quitDashboard()" in html[quit_idx : quit_idx + 200]
    assert "Refresh dashboard" in html[pop_idx:header_actions_idx]
    assert "Quit dashboard" in html[pop_idx:header_actions_idx]
    src = APP_JS.read_text(encoding="utf-8")
    assert "function pollStats" not in src
    assert "function renderInsights" not in src
    assert "insightsStats" not in src
    assert "setBrandPopoverOpen" in src
    assert '"brand-wrap"' in src


def test_app_js_activity_dot_active_vs_ready():
    """List squares + banner pulse: live work orange, parked/waiting green."""
    src = APP_JS.read_text(encoding="utf-8")
    html = (HERE / "static" / "index.html").read_text(encoding="utf-8")
    assert "function jobActivityDot(" in src
    fn = src.split("function jobActivityDot(", 1)[1].split("\nfunction ", 1)[0]
    assert "ACTIVE_PROGRESS_STATUSES.has" in fn
    assert 'return "active"' in fn
    assert 'return "ready"' in fn
    assert "resume_ready" in fn
    assert "HOLD_BUSY_STATUSES" in fn

    row = src.split("function renderJobRow(", 1)[1].split("\nfunction ", 1)[0]
    assert "jobActivityDot(job)" in row
    assert "activity-" in row
    assert "queueBucket(job.status)" in row

    bar = src.split("function updateStatusBar(", 1)[1].split("\nasync function ", 1)[0]
    assert "jobActivityDot(" in bar
    assert "activity-ready" in bar or "activity-${" in bar

    group_block = src.split("html = groupEntries.map", 1)[1].split("list.innerHTML", 1)[0]
    assert "jobActivityDot(" in group_block

    assert ".status-rail.activity-active" in html
    assert ".status-rail.activity-ready" in html
    assert ".mstat.progress .n { color: var(--orange); }" in html
    assert "#status-bar.activity-ready .pulse" in html

    active_line = re.search(
        r"const ACTIVE_PROGRESS_STATUSES = new Set\([^;]+;", src
    )
    hold_line = re.search(r"const HOLD_BUSY_STATUSES = new Set\([^;]+;", src)
    helper = re.search(
        r"function jobActivityDot\(job\) \{.*?\n\}", src, flags=re.S
    )
    assert active_line and hold_line and helper, "jobActivityDot wiring missing"
    snippet = (
        active_line.group(0)
        + "\n"
        + hold_line.group(0)
        + "\n"
        + helper.group(0)
        + "\n"
        + "const jobs = "
        + json.dumps([
            {"status": "tailoring"},
            {"status": "navigating"},
            {"status": "filling"},
            {"status": "resuming"},
            {"status": "resume_ready"},
            {"status": "ready_for_review"},
            {"status": "blocked_captcha"},
            {"status": "discovered"},
            {"status": "applied"},
            {"status": "stuck"},
        ])
        + ";\n"
        + "process.stdout.write(JSON.stringify(jobs.map(j => jobActivityDot(j))));\n"
    )
    proc = subprocess.run(
        ["node", "-e", snippet],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got == [
        "active",
        "active",
        "active",
        "active",
        "ready",
        "ready",
        "ready",
        "",
        "",
        "",
    ]


def test_add_job_hover_does_not_steal_search_focus():
    """Add-URL mouseenter must not yank focus from #search while typing."""
    src = APP_JS.read_text(encoding="utf-8")
    assert 'getElementById("add-job-wrap")' in src
    # Guard: skip autofocus when another control (e.g. Search) is active.
    assert "wrap.contains(active)" in src
    assert 'getElementById("search")' in src
    assert "add-job-url" in src


def test_index_html_embedded_browser_boot():
    """Zero-height embedded browsers must still paint; scripts must surface load errors."""
    html = (HERE / "static" / "index.html").read_text(encoding="utf-8")
    assert "min-height: 480px" in html
    assert "min-height: max(100vh, 480px)" in html
    assert 'id="ops-shell"' in html and "min-height:480px" in html
    assert 'id="list-boot-msg"' in html
    assert "__jhBootFailed" in html
    assert 'onerror="window.__jhBootFailed' in html


def test_app_js_boot_render_without_auto_queue():
    """First paint before poll completes; never auto-jump queue tabs."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "list-boot-msg" in src
    assert "maybeAutoSelectQueue" not in src
    assert "markDashboardPainted" in src
    boot = src.split("try {", 1)[1].split("} catch (bootErr)", 1)[0]
    assert "render();" in boot
    assert "markDashboardPainted();" in boot
    assert boot.index("render();") < boot.index("markDashboardPainted();")
    assert "poll();" in boot


def test_cancel_bumps_fill_gen():
    """Cancel must invalidate in-flight fill/tailor threads (fill_gen bump)."""
    srv = _load_server()
    jobs = {
        "jobs": [
            {
                "id": "run-cancel",
                "status": "filling",
                "session_key": "agent:job-hunter:job-run-cancel",
                "fill_gen": 3,
                "status_detail": "Filling…",
                "timeline": [],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
    }

    @contextmanager
    def _fake_locked(*, allow_purge=False):
        yield jobs

    handler = srv.Handler.__new__(srv.Handler)
    with mock.patch.object(srv, "locked_jobs_for_write", _fake_locked), mock.patch.object(
        srv, "_kill_process_tree"
    ), mock.patch.object(srv, "abort_gateway_session"), mock.patch.object(
        srv, "clear_fill_activity"
    ), mock.patch.object(
        srv, "close_job_partyrock_tab", return_value={}
    ), mock.patch.object(handler, "_send_json"):
        handler._handle_cancel("run-cancel")
    assert jobs["jobs"][0]["fill_gen"] == 4
    assert jobs["jobs"][0]["status"] != "discovered"
    assert jobs["jobs"][0]["status"] == "resume_ready"


def test_stale_fill_gen_blocks_pipeline_patch():
    """After Cancel→Open, stale pipeline threads must not clobber discovered."""
    srv = _load_server()
    jobs = {
        "jobs": [
            {
                "id": "stale-run",
                "status": "discovered",
                "session_key": "agent:job-hunter:job-stale-run",
                "fill_gen": 2,
                "status_detail": "Cancelled by user — returned to Open.",
                "timeline": [],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
    }
    tok = srv._fill_run_ctx.set(("stale-run", 1))
    try:
        @contextmanager
        def _fake_locked(*, allow_purge=False):
            raise AssertionError("stale patch must not enter locked write")

        with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
            srv, "locked_jobs_for_write", _fake_locked
        ):
            srv._patch_job("stale-run", status="navigating", status_detail="should not apply")
        assert jobs["jobs"][0]["status"] == "discovered"
        assert srv._job_fill_aborted("stale-run") is True
    finally:
        srv._fill_run_ctx.reset(tok)


def test_session_running_local_includes_agent_turn():
    """Start guard must see in-process agent_runner turns (no Popen)."""
    srv = _load_server()
    key = "agent:job-hunter:job-agentish"
    with mock.patch.object(srv, "_running_procs", {}), mock.patch.object(
        srv.agent_runner, "is_turn_active", return_value=True
    ):
        assert srv._session_running_local(key) is True
    with mock.patch.object(srv, "_running_procs", {}), mock.patch.object(
        srv.agent_runner, "is_turn_active", return_value=False
    ):
        assert srv._session_running_local(key) is False


def test_subprocess_cooperative_abort_ignores_stale_gen():
    """PartyRock tailor must not be killed mid-gather solely for stale fill_gen."""
    srv = _load_server()
    jobs = {
        "jobs": [
            {
                "id": "pr-job",
                "status": "tailoring",
                "fill_gen": 9,
                "session_key": "agent:job-hunter:job-pr-job",
            }
        ]
    }
    tok = srv._fill_run_ctx.set(("pr-job", 8))
    try:
        with mock.patch.object(srv, "read_jobs", return_value=jobs):
            assert srv._job_fill_aborted("pr-job") is True
            assert srv._job_fill_hard_aborted("pr-job") is False
    finally:
        srv._fill_run_ctx.reset(tok)


def test_pipeline_stop_if_aborted_logs_stale_gen():
    srv = _load_server()
    jobs = {
        "jobs": [
            {
                "id": "stale-handoff",
                "status": "tailoring",
                "fill_gen": 3,
                "session_key": "agent:job-hunter:job-stale-handoff",
            }
        ]
    }
    tok = srv._fill_run_ctx.set(("stale-handoff", 2))
    try:
        with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
            srv, "write_jobs"
        ), mock.patch.object(srv, "append_fill_activity") as af:
            stopped = srv._pipeline_stop_if_aborted("stale-handoff", "PartyRock gather")
            assert stopped is True
            af.assert_called_once()
            detail = af.call_args.kwargs.get("detail") or af.call_args[1].get("detail")
            assert "fill_gen stale" in detail
            assert "PartyRock gather" in detail
    finally:
        srv._fill_run_ctx.reset(tok)


def test_subprocess_step_uses_hard_abort_in_source():
    """Regression: stale fill_gen must not kill PartyRock mid-gather (ac3ab89)."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    chunk = src.split("def _run_subprocess_step", 1)[1].split("\ndef ", 1)[0]
    assert "_job_fill_hard_aborted(activity_job_id)" in chunk
    assert "_job_fill_aborted(activity_job_id)" not in chunk


def test_partyrock_handoff_has_abort_checkpoints_in_source():
    """Compile/fill handoffs must log when aborted (no silent stall)."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    chunk = src.split("def _run_tailor_then_fill_body", 1)[1].split(
        "def run_agent_message", 1
    )[0]
    assert "_pipeline_stop_if_aborted" in chunk
    assert "PartyRock gather (before PDF compile)" in chunk
    assert "fast_fill launch" in chunk
    assert "resume.tex ready" in chunk


def test_run_tailor_then_fill_claim_failure_in_source():
    src = SERVER_PATH.read_text(encoding="utf-8")
    chunk = src.split("def run_tailor_then_fill", 1)[1].split(
        "def _fill_skipping_partyrock", 1
    )[0]
    assert "_claim_fill_job_for_run" in chunk
    assert "_bind_fill_run_ctx" in chunk
    assert "status=\"stuck\"" in chunk or "status='stuck'" in chunk
    assert "stale gen or terminal abort" in chunk
    assert "if claimed:" in chunk
    assert "_release_fill_job(job_id)" in chunk


def test_claim_fill_job_failure_marks_stuck():
    """Same-gen claim timeout still surfaces stuck (slot never freed)."""
    srv = _load_server()
    srv._active_fill_jobs.add("dup-claim")
    jobs = {
        "jobs": [
            {
                "id": "dup-claim",
                "status": "tailoring",
                "fill_gen": 1,
                "session_key": "agent:job-hunter:job-dup-claim",
            }
        ]
    }
    try:
        with mock.patch.object(srv, "_FILL_CLAIM_WAIT_S", 0), mock.patch.object(
            srv, "read_jobs", return_value=jobs
        ), mock.patch.object(srv, "write_jobs"), mock.patch.object(
            srv, "_patch_job"
        ) as pj, mock.patch.object(srv, "append_fill_activity"), mock.patch.object(
            srv, "_run_tailor_then_fill_body"
        ) as body:
            srv.run_tailor_then_fill("dup-claim", fill_run_gen=1)
            body.assert_not_called()
            pj.assert_called_once()
            assert pj.call_args.kwargs.get("status") == "stuck"
    finally:
        srv._active_fill_jobs.discard("dup-claim")


def test_claim_fill_job_failure_stale_does_not_demote():
    """Losing/stale Start must not flip the live run to stuck on claim miss."""
    srv = _load_server()
    srv._active_fill_jobs.add("stale-claim")
    jobs = {
        "jobs": [
            {
                "id": "stale-claim",
                "status": "navigating",
                "fill_gen": 5,
                "session_key": "agent:job-hunter:job-stale-claim",
            }
        ]
    }
    try:
        with mock.patch.object(srv, "_FILL_CLAIM_WAIT_S", 0), mock.patch.object(
            srv, "read_jobs", return_value=jobs
        ), mock.patch.object(srv, "write_jobs"), mock.patch.object(
            srv, "_patch_job"
        ) as pj, mock.patch.object(srv, "append_fill_activity") as af, mock.patch.object(
            srv, "_run_tailor_then_fill_body"
        ) as body:
            srv.run_tailor_then_fill("stale-claim", fill_run_gen=4)
            body.assert_not_called()
            pj.assert_not_called()
            af.assert_not_called()
            assert jobs["jobs"][0]["status"] == "navigating"
            # Losing thread must not release the active pipeline's claim.
            assert "stale-claim" in srv._active_fill_jobs
    finally:
        srv._active_fill_jobs.discard("stale-claim")


def test_claim_failure_does_not_release_peer_claim():
    """Claim-timeout stuck path must leave another thread's claim intact."""
    srv = _load_server()
    srv._active_fill_jobs.add("peer-claim")
    jobs = {
        "jobs": [
            {
                "id": "peer-claim",
                "status": "tailoring",
                "fill_gen": 1,
                "session_key": "agent:job-hunter:job-peer-claim",
            }
        ]
    }
    try:
        with mock.patch.object(srv, "_FILL_CLAIM_WAIT_S", 0), mock.patch.object(
            srv, "read_jobs", return_value=jobs
        ), mock.patch.object(srv, "write_jobs"), mock.patch.object(
            srv, "_patch_job"
        ), mock.patch.object(srv, "append_fill_activity"), mock.patch.object(
            srv, "_run_tailor_then_fill_body"
        ):
            srv.run_tailor_then_fill("peer-claim", fill_run_gen=1)
        assert "peer-claim" in srv._active_fill_jobs
    finally:
        srv._active_fill_jobs.discard("peer-claim")


def test_claim_fill_job_for_run_waits_then_claims():
    """New Start should claim after a prior thread releases (Cancel→Retry race)."""
    srv = _load_server()
    srv._active_fill_jobs.add("wait-claim")
    released = {"done": False}

    def _release_soon():
        time.sleep(0.08)
        srv._release_fill_job("wait-claim")
        released["done"] = True

    t = threading.Thread(target=_release_soon, daemon=True)
    t.start()
    try:
        with mock.patch.object(srv, "_FILL_CLAIM_WAIT_S", 2.0), mock.patch.object(
            srv, "_job_fill_gen", return_value=2
        ):
            assert srv._claim_fill_job_for_run("wait-claim", 2) is True
        assert released["done"] is True
        assert "wait-claim" in srv._active_fill_jobs
    finally:
        t.join(timeout=2)
        srv._active_fill_jobs.discard("wait-claim")


def test_hold_detection_ignores_applied_status():
    """Mark-applied jobs must not keep fill deadline suspended via hold activity."""
    srv = _load_server()
    job = {
        "id": "hold-applied",
        "status": "applied",
        "status_detail": "Browser held open for review (never submitted)",
    }
    srv.clear_fill_activity("hold-applied")
    srv.append_fill_activity("hold-applied", event="hold", detail="Keeping browser open…")
    assert srv._job_is_holding_for_review(job, job_id="hold-applied") is False
    assert srv._job_is_fill_paused(job, job_id="hold-applied") is False


def test_patch_job_blocks_detail_on_terminal_status():
    srv = _load_server()
    jobs = {
        "jobs": [
            {
                "id": "applied-x",
                "status": "applied",
                "status_detail": "Marked as applied by user from dashboard.",
                "session_key": "agent:job-hunter:job-applied-x",
            }
        ]
    }

    @contextmanager
    def _fake_locked(*, allow_purge=False):
        yield jobs

    with mock.patch.object(srv, "locked_jobs_for_write", _fake_locked):
        srv._patch_job("applied-x", status_detail="Browser held open for review")
    assert jobs["jobs"][0]["status_detail"] == "Marked as applied by user from dashboard."


def test_pipeline_stale_gen_handoff_marks_stuck():
    """Stale fill_gen must exit silently — never demote a newer run to stuck."""
    srv = _load_server()
    jobs = {
        "jobs": [
            {
                "id": "stale-nav",
                "status": "navigating",
                "fill_gen": 3,
                "session_key": "agent:job-hunter:job-stale-nav",
            }
        ]
    }
    tok = srv._bind_fill_run_ctx("stale-nav", 2)
    try:
        with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
            srv, "write_jobs"
        ) as wj:

            @contextmanager
            def _fake_locked(*, allow_purge=False):
                yield jobs

            with mock.patch.object(srv, "locked_jobs_for_write", _fake_locked):
                stopped = srv._pipeline_stop_if_aborted("stale-nav", "fast_fill launch")
            assert stopped is True
            assert jobs["jobs"][0]["status"] == "navigating"
            wj.assert_not_called()
    finally:
        srv._fill_run_ctx.reset(tok)


def test_empty_jd_uses_extract_not_open_agent_in_source():
    src = SERVER_PATH.read_text(encoding="utf-8")
    assert "extract_job_posting.py" in src
    assert "No job description after extract_job_posting" in src
    assert "agent fetching apply page then continuing pipeline" not in src
    assert "Do NOT fill the application" in src


def test_tectonic_fail_agent_does_not_fill_in_source():
    src = SERVER_PATH.read_text(encoding="utf-8")
    chunk = src.split("if not compile_ok:", 1)[1].split(
        "Fitting resume within two pages", 1
    )[0]
    assert "Do NOT fill the application" in chunk
    assert "set ready_for_review" in chunk
    assert "continue the pipeline (fill the application" not in chunk
    assert "resume_pdf.exists()" in chunk
    assert "_persist_compiled_resume_after_tectonic(" in chunk


def test_skip_partyrock_messaging_avoids_uploaded_resume_in_source():
    src = SERVER_PATH.read_text(encoding="utf-8")
    assert "using uploaded resume" not in src.lower()
    assert "dummy fixture" in src
    assert "PartyRock skipped" in src


def test_mark_submitted_releases_fill_and_clears_hold_in_source():
    src = SERVER_PATH.read_text(encoding="utf-8")
    chunk = src.split("def _handle_mark_submitted", 1)[1].split(
        "def _handle_edit_applied", 1
    )[0]
    assert "_release_fill_job" in chunk
    assert "clear_fill_activity" in chunk
    assert "stop_native_hud" in chunk


def test_fill_streaming_aborts_on_terminal_status_in_source():
    src = SERVER_PATH.read_text(encoding="utf-8")
    chunk = src.split("def _run_fill_subprocess_streaming", 1)[1].split(
        "def _dummy_fill_flash_requested", 1
    )[0]
    assert "_job_fill_hard_aborted" in chunk


def test_hybrid_fill_passes_fill_run_gen_in_source():
    src = SERVER_PATH.read_text(encoding="utf-8")
    chunk = src.split("def _run_tailor_then_fill_body", 1)[1].split(
        "def run_agent_message", 1
    )[0]
    assert "fill_run_gen=fill_run_gen" in chunk


if __name__ == "__main__":
    test_hold_active_suspends_without_ready_status()
    test_hold_active_via_activity_event()
    test_fill_pause_suspends_kill_deadline()
    test_classify_fill_paused_stdout()
    test_patch_job_refuses_clobber_abort_statuses()
    test_patch_job_allows_normal_progress()
    test_mark_fill_thread_stuck_skips_abort_statuses()
    test_mark_fill_thread_stuck_patches_when_running()
    test_pipeline_milestone_aborts_when_cancelled()
    test_fill_abort_statuses_cover_hunt_set()
    test_app_js_cancel_for_progress_stuck_ready()
    test_max_headed_chrome_mains_default_three()
    test_app_js_concurrent_fill_no_global_busy_gate()
    test_app_js_start_fill_mode_no_false_skip_without_pdf()
    test_app_js_escapes_question_and_cancel_gate()
    test_app_js_cancel_stays_on_current_tab()
    test_app_js_uses_js_string_escape_on_detail_actions()
    test_restore_handler_accepts_deleted_in_source()
    test_delete_kills_running_proc_in_source()
    test_handle_cancel_allows_stuck_in_source()
    test_partyrock_parallel_no_global_lock()
    test_claim_fill_job_blocks_duplicate_thread()
    test_fill_run_stale_accepts_locked_fill_gen()
    test_post_tectonic_stale_check_passes_fill_gen_in_source()
    test_post_tectonic_handoff_completes_under_real_locks()
    test_post_tectonic_pipeline_advances_past_tailoring()
    test_bare_fill_run_stale_under_lock_would_deadlock()
    test_bind_fill_run_ctx_uses_explicit_gen()
    test_app_js_optimistic_fill_start()
    test_app_js_start_switches_to_progress_tab()
    test_app_js_company_apply_count_badge()
    test_app_js_mark_applied_while_in_progress()
    test_force_stuck_orphaned_in_progress_ignores_age_on_startup()
    test_force_stuck_orphaned_respects_stale_age()
    test_force_stuck_orphaned_when_past_stale()
    test_mark_submitted_kills_proc_in_source()
    test_app_js_shows_restore_for_deleted()
    test_ready_hold_copy_no_cancel()
    test_ingest_hold_honors_full_abort_set()
    test_pipeline_milestone_skips_detail_when_aborted()
    test_app_js_date_older_includes_unknown()
    test_agent_fallback_missing_tex_forces_stuck_in_source()
    test_app_js_surfaces_deleted_and_skip_without_classic()
    test_classic_routes_redirect_to_ops()
    test_app_js_surfaces_applied_after_mark_submitted()
    test_cancel_bumps_fill_gen()
    test_stale_fill_gen_blocks_pipeline_patch()
    test_subprocess_cooperative_abort_ignores_stale_gen()
    test_pipeline_stop_if_aborted_logs_stale_gen()
    test_subprocess_step_uses_hard_abort_in_source()
    test_partyrock_handoff_has_abort_checkpoints_in_source()
    test_run_tailor_then_fill_claim_failure_in_source()
    test_claim_fill_job_failure_marks_stuck()
    test_claim_fill_job_failure_stale_does_not_demote()
    test_claim_failure_does_not_release_peer_claim()
    test_claim_fill_job_for_run_waits_then_claims()
    test_session_running_local_includes_agent_turn()
    test_hold_detection_ignores_applied_status()
    test_patch_job_blocks_detail_on_terminal_status()
    test_pipeline_stale_gen_handoff_marks_stuck()
    test_empty_jd_uses_extract_not_open_agent_in_source()
    test_tectonic_fail_agent_does_not_fill_in_source()
    test_skip_partyrock_messaging_avoids_uploaded_resume_in_source()
    test_mark_submitted_releases_fill_and_clears_hold_in_source()
    test_fill_streaming_aborts_on_terminal_status_in_source()
    test_hybrid_fill_passes_fill_run_gen_in_source()
    test_app_js_timeline_starts_collapsed_and_auto_collapses()
    test_app_js_start_failure_invalidates_etag_not_just_json()
    test_app_js_optimistic_fill_restores_fast_poll()
    test_temp_applied_count_override_is_null()
    test_kpi_counts_use_per_family_filter_state()
    test_list_filters_persist_per_family_in_localstorage()
    test_app_js_setqueue_only_from_user_tab_clicks()
    test_app_js_boot_render_without_auto_queue()
    test_header_tooltip_css_covers_nested_buttons_without_click_capture()
    test_deleted_toolbar_filters_and_empty_deleted_side_by_side()
    test_description_api_prefers_jd_full_over_preview()
    test_header_branding_omnidex_without_insights()
    test_app_js_activity_dot_active_vs_ready()
    test_add_job_hover_does_not_steal_search_focus()
    print("OK test_dashboard_bugs")