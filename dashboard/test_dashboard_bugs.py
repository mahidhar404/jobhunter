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
CLASSIC_JS = HERE / "static" / "classic.js"


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
    """DASH-008: Cancel only when runInProgress (not Ready bucket)."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "runInProgress || bucket === \"ready\"" not in src
    assert "(runInProgress)" in src or "${(runInProgress)" in src


def test_app_js_hybrid_fill_409_mentions_another_job():
    """DASH-018: ops 409 fallback matches classic wording."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "another job is already running" in src


def test_app_js_start_fill_mode_no_false_skip_without_pdf():
    """DASH-010: real Fill-with-resume without PDF must not always claim skip."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "jobHasDiskResume(job) || testModeEnabled" in src
    # Old always-skip line must be gone
    assert "const skipPartyrock = normalized === \"with-resume\" || normalized === \"retry\";" not in src


def test_classic_js_escapes_question_and_cancel_gate():
    """DASH-007 / DASH-009: escapeHtml question; Cancel only in-progress."""
    src = CLASSIC_JS.read_text(encoding="utf-8")
    assert "escapeHtml(job.question" in src
    assert "runInProgress" in src
    # Old ungated Cancel inside !TERMINAL block should not remain as sole gate
    assert "if (runInProgress)" in src


def test_classic_js_uses_js_string_escape_on_detail_actions():
    """DASH-007: onclick job ids go through jsStringEscape."""
    src = CLASSIC_JS.read_text(encoding="utf-8")
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


def test_partyrock_lock_aborts_when_cancelled():
    """DASH2-001: lock wait polls abort and raises without holding the lock."""
    srv = _load_server()
    # Ensure lock is free, then hold it so acquire must wait.
    if srv._partyrock_lock.locked():
        try:
            srv._partyrock_lock.release()
        except RuntimeError:
            pass
    assert srv._partyrock_lock.acquire(blocking=False)
    jobs = {
        "jobs": [
            {
                "id": "lock-abort",
                "status": "tailoring",
                "status_detail": "Waiting for another job…",
                "session_key": "agent:job-hunter:job-lock-abort",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
    }
    calls = {"n": 0}

    def aborted(_job_id):
        calls["n"] += 1
        # First checks while waiting see running; then Cancel flips it.
        return calls["n"] >= 3

    with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
        srv, "write_jobs"
    ), mock.patch.object(srv, "_job_fill_aborted", side_effect=aborted), mock.patch.dict(
        "os.environ", {"PARTYROCK_LOCK_POLL_S": "0.05"}, clear=False
    ):
        try:
            raised = None
            try:
                srv._acquire_partyrock_lock("lock-abort", "agent:job-hunter:job-lock-abort")
            except Exception as e:
                raised = e
            assert isinstance(raised, srv.PartyRockLockAborted), raised
            assert calls["n"] >= 3
            # Must not leave the waiter holding the lock.
            assert srv._partyrock_lock.locked()
        finally:
            srv._partyrock_lock.release()


def test_partyrock_lock_acquire_source_polls_abort():
    """DASH2-001: acquire implementation must poll _job_fill_aborted."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    chunk = src.split("def _acquire_partyrock_lock", 1)[1].split("def _activity_clock", 1)[0]
    assert "_job_fill_aborted" in chunk
    assert "PartyRockLockAborted" in chunk


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


def test_classic_js_shows_restore_for_deleted():
    """DASH2-003: classic detail offers Restore; restoreJob posts /restore."""
    src = CLASSIC_JS.read_text(encoding="utf-8")
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


def test_classic_routes_redirect_to_ops():
    """UI-033: /classic, classic.html, classic.js HTTP-redirect to Ops /."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    assert 'parts[0] in ("classic", "classic.html", "classic.js")' in src
    chunk = src.split('("classic", "classic.html", "classic.js")', 1)[1][:400]
    assert "send_response(302)" in chunk
    assert 'send_header("Location", "/")' in chunk
    assert '_send_file(STATIC_DIR / "classic.html"' not in src
    classic_html = (HERE / "static" / "classic.html").read_text(encoding="utf-8")
    classic_js = CLASSIC_JS.read_text(encoding="utf-8")
    assert "FROZEN" in classic_html[:200]
    assert "FROZEN" in classic_js[:200]
    app = APP_JS.read_text(encoding="utf-8")
    assert "async function skipJob(" in app
    assert "surfaceDeletedJob" in app
    assert 'data-queue="skipped"' not in (HERE / "static" / "index.html").read_text(encoding="utf-8")


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
    test_app_js_hybrid_fill_409_mentions_another_job()
    test_app_js_start_fill_mode_no_false_skip_without_pdf()
    test_classic_js_escapes_question_and_cancel_gate()
    test_classic_js_uses_js_string_escape_on_detail_actions()
    test_restore_handler_accepts_deleted_in_source()
    test_delete_kills_running_proc_in_source()
    test_partyrock_lock_aborts_when_cancelled()
    test_partyrock_lock_acquire_source_polls_abort()
    test_force_stuck_orphaned_in_progress_ignores_age_on_startup()
    test_force_stuck_orphaned_respects_stale_age()
    test_force_stuck_orphaned_when_past_stale()
    test_mark_submitted_kills_proc_in_source()
    test_classic_js_shows_restore_for_deleted()
    test_ready_hold_copy_no_cancel()
    test_ingest_hold_honors_full_abort_set()
    test_pipeline_milestone_skips_detail_when_aborted()
    test_app_js_date_older_excludes_unknown()
    test_agent_fallback_missing_tex_forces_stuck_in_source()
    test_classic_routes_redirect_to_ops()
    print("OK test_dashboard_bugs")