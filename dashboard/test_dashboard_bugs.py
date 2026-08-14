"""Regression tests for DASH-001…010 / 018 and DASH2-001…004 / 006 / 013.

No browser — exercises server helpers and static UI source contracts.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SERVER_PATH = HERE / "server.py"
APP_JS = HERE / "static" / "app.js"


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
        with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
            srv, "write_jobs"
        ) as wj:
            srv._patch_job("x", status="ready_for_review", status_detail="should not apply")
            wj.assert_not_called()
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
    with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
        srv, "write_jobs"
    ) as wj:
        srv._patch_job("x", status="ready_for_review", status_detail="Ready")
        wj.assert_called_once()
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


def test_app_js_cancel_not_for_ready():
    """DASH-008: Cancel when runInProgress or stuck (not Ready bucket)."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "runInProgress || bucket === \"ready\"" not in src
    assert "canCancel" in src
    assert "STUCK_STATUSES.has(job.status)" in src


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


def test_app_js_escapes_question_and_cancel_gate():
    """DASH-007 / DASH-009: escapeHtml question; Cancel for in-progress + stuck."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "escapeHtml(job.question" in src
    assert "canCancel" in src
    assert "STUCK_STATUSES.has(job.status)" in src
    # Cancel must not show for every non-terminal job (e.g. open/applied).
    assert "${canCancel" in src or "(canCancel)" in src


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
    assert 'setQueue("progress")' in src


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
    with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
        srv, "write_jobs"
    ) as wj:
        forced = srv._force_stuck_orphaned_in_progress(ignore_age=True)
        assert forced == ["orphan-fill"]
        assert jobs["jobs"][0]["status"] == "stuck"
        wj.assert_called_once()


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
    with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
        srv, "write_jobs"
    ) as wj:
        forced = srv._force_stuck_orphaned_in_progress(ignore_age=False)
        assert forced == []
        assert jobs["jobs"][0]["status"] == "navigating"
        wj.assert_not_called()


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
    with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
        srv, "write_jobs"
    ) as wj:
        forced = srv._force_stuck_orphaned_in_progress(ignore_age=False)
        assert forced == ["old-fill"]
        assert jobs["jobs"][0]["status"] == "stuck"
        wj.assert_called_once()


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


def test_app_js_date_older_excludes_unknown():
    """UI-040: Posted 'older' must not include unknown dates."""
    src = APP_JS.read_text(encoding="utf-8")
    chunk = src.split("function jobMatchesDateFilter", 1)[1].split(
        "function jobMatchesSalaryFilter", 1
    )[0]
    assert 'dateFilter === "older"' in chunk
    # Within the older branch, unknown must return false (not true).
    older = chunk.split('dateFilter === "older"', 1)[1].split("ageDays", 1)[0]
    assert "if (unknown) return true" not in older
    assert "if (unknown) return false" in older


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


def test_app_js_surfaces_applied_after_mark_submitted():
    """DASH2-006 UI: mark applied optimistically moves job to Applied queue."""
    app = APP_JS.read_text(encoding="utf-8")
    assert "surfaceAppliedJob" in app
    assert "applyMarkedAppliedLocally" in app
    chunk = app.split("async function markSubmitted", 1)[1].split(
        "async function uploadJobResume", 1
    )[0]
    assert "if (!ok)" in chunk
    assert "applyMarkedAppliedLocally(jobId)" in chunk
    assert "surfaceAppliedJob(jobId)" in chunk
    poll_chunk = app.split("async function poll()", 1)[1].split(
        "// ------------------------------------------------------------ Utility pane", 1
    )[0]
    assert "_pollSeq" in poll_chunk
    assert "seq !== _pollSeq" in poll_chunk


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
    handler = srv.Handler.__new__(srv.Handler)
    with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
        srv, "write_jobs"
    ), mock.patch.object(srv, "_kill_process_tree"), mock.patch.object(
        srv, "abort_gateway_session"
    ), mock.patch.object(srv, "clear_fill_activity"), mock.patch.object(
        srv, "close_job_partyrock_tab", return_value={}
    ), mock.patch.object(handler, "_send_json"):
        handler._handle_cancel("run-cancel")
    assert jobs["jobs"][0]["fill_gen"] == 4
    assert jobs["jobs"][0]["status"] == "discovered"


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
        with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
            srv, "write_jobs"
        ) as wj:
            srv._patch_job("stale-run", status="navigating", status_detail="should not apply")
            wj.assert_not_called()
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
            srv, "append_fill_activity"
        ) as af:
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
    assert "_claim_fill_job" in chunk
    assert "status=\"stuck\"" in chunk or "status='stuck'" in chunk


def test_claim_fill_job_failure_marks_stuck():
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
        with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
            srv, "write_jobs"
        ), mock.patch.object(srv, "_patch_job") as pj, mock.patch.object(
            srv, "append_fill_activity"
        ), mock.patch.object(srv, "_run_tailor_then_fill_body") as body:
            srv.run_tailor_then_fill("dup-claim", fill_run_gen=1)
            body.assert_not_called()
            pj.assert_called_once()
            assert pj.call_args.kwargs.get("status") == "stuck"
    finally:
        srv._active_fill_jobs.discard("dup-claim")


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
    test_app_js_cancel_not_for_ready()
    test_max_headed_chrome_mains_default_three()
    test_app_js_concurrent_fill_no_global_busy_gate()
    test_app_js_start_fill_mode_no_false_skip_without_pdf()
    test_app_js_escapes_question_and_cancel_gate()
    test_app_js_uses_js_string_escape_on_detail_actions()
    test_restore_handler_accepts_deleted_in_source()
    test_delete_kills_running_proc_in_source()
    test_handle_cancel_allows_stuck_in_source()
    test_partyrock_parallel_no_global_lock()
    test_claim_fill_job_blocks_duplicate_thread()
    test_bind_fill_run_ctx_uses_explicit_gen()
    test_app_js_optimistic_fill_start()
    test_app_js_mark_applied_while_in_progress()
    test_force_stuck_orphaned_in_progress_ignores_age_on_startup()
    test_force_stuck_orphaned_respects_stale_age()
    test_force_stuck_orphaned_when_past_stale()
    test_mark_submitted_kills_proc_in_source()
    test_app_js_shows_restore_for_deleted()
    test_ready_hold_copy_no_cancel()
    test_ingest_hold_honors_full_abort_set()
    test_pipeline_milestone_skips_detail_when_aborted()
    test_app_js_date_older_excludes_unknown()
    test_agent_fallback_missing_tex_forces_stuck_in_source()
    test_app_js_surfaces_deleted_and_skip_without_classic()
    test_app_js_surfaces_applied_after_mark_submitted()
    test_cancel_bumps_fill_gen()
    test_stale_fill_gen_blocks_pipeline_patch()
    test_subprocess_cooperative_abort_ignores_stale_gen()
    test_pipeline_stop_if_aborted_logs_stale_gen()
    test_subprocess_step_uses_hard_abort_in_source()
    test_partyrock_handoff_has_abort_checkpoints_in_source()
    test_run_tailor_then_fill_claim_failure_in_source()
    test_claim_fill_job_failure_marks_stuck()
    test_session_running_local_includes_agent_turn()
    print("OK test_dashboard_bugs")