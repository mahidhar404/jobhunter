#!/usr/bin/env python3
"""Discovery triggers post-merge LinkedIn HTTP apply-URL resolve (mocked)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


def _reset_discovery(tmp_checkpoint: Path | None = None) -> None:
    if tmp_checkpoint is not None:
        srv.DISCOVERY_CHECKPOINT_FILE = tmp_checkpoint
        if tmp_checkpoint.exists():
            tmp_checkpoint.unlink()
    with srv._discovery_lock:
        srv._discovery_state.update({
            "running": False,
            "phase": None,
            "phase_label": None,
            "started_at": None,
            "finished_at": None,
            "last_finished_at": None,
            "ok": None,
            "error": None,
            "sources": [],
            "can_abort": False,
            "abort_requested": False,
            "resumed": False,
            "resume_available": False,
            "run_id": None,
            "resolve_done": None,
            "resolve_total": None,
        })
        srv._discovery_current_procs.clear()
        srv._discovery_protect_proc = False
    srv._reset_checkpoint_meta()


def test_discovery_phase_labels_include_resolving() -> None:
    assert "resolving" in srv.DISCOVERY_PHASE_LABELS
    assert "Resolving apply links" in srv.DISCOVERY_PHASE_LABELS["resolving"]


def test_discovery_phase_labels_include_tagging() -> None:
    assert "tagging" in srv.DISCOVERY_PHASE_LABELS
    assert "Tagging" in srv.DISCOVERY_PHASE_LABELS["tagging"]


def test_apply_resolve_backlog_skips_while_discovery_running() -> None:
    """Backlog loop must not call resolve when Discover is active."""
    calls: list[dict] = []

    def fake_resolve(**kwargs):
        calls.append(kwargs)
        return {"considered": 0, "upgraded": []}

    with mock.patch.object(srv, "_wait_jobs_list_boot"), \
         mock.patch.object(srv, "is_session_running", return_value=True), \
         mock.patch.object(srv, "APPLY_RESOLVE_BACKLOG_INTERVAL_S", 0), \
         mock.patch(
             "resolve_apply_urls.resolve_discovery_apply_urls",
             side_effect=fake_resolve,
         ), \
         mock.patch.object(srv.time, "sleep", side_effect=[None, StopIteration]):
        try:
            srv._apply_resolve_backlog_loop()
        except StopIteration:
            pass
    assert calls == []


def test_apply_resolve_backlog_passes_limit_no_since() -> None:
    captured: list[dict] = []
    reresolve_calls: list[dict] = []

    def fake_resolve(**kwargs):
        captured.append(kwargs)
        return {"considered": 2, "upgraded": [{"id": "x"}], "high": 1}

    def fake_reresolve(**kwargs):
        reresolve_calls.append(kwargs)
        return {
            "considered": 3,
            "restored": 1,
            "still_unresolved": 2,
            "errors": [],
            "restored_by": {"sibling": 1},
        }

    sleeps = [None, StopIteration]

    def _sleep(_s):
        nxt = sleeps.pop(0)
        if nxt is StopIteration:
            raise StopIteration
        return None

    with mock.patch.object(srv, "_wait_jobs_list_boot"), \
         mock.patch.object(srv, "is_session_running", return_value=False), \
         mock.patch.object(srv, "APPLY_RESOLVE_BACKLOG_INTERVAL_S", 0), \
         mock.patch.object(srv, "APPLY_RESOLVE_BACKLOG_LIMIT", 8), \
         mock.patch.object(srv, "APPLY_RESOLVE_BACKLOG_CONCURRENCY", 3), \
         mock.patch.object(srv, "APPLY_RESOLVE_RERESOLVE_LIMIT", 40), \
         mock.patch.object(srv, "APPLY_RESOLVE_RERESOLVE_WORKERS", 4), \
         mock.patch.object(srv, "APPLY_RESOLVE_RELIABLE_LIMIT", 80), \
         mock.patch.object(srv, "APPLY_RESOLVE_RELIABLE_WORKERS", 6), \
         mock.patch(
             "resolve_apply_urls.resolve_discovery_apply_urls",
             side_effect=fake_resolve,
         ), \
         mock.patch(
             "resolve_apply_urls.reresolve_unresolved_deleted",
             side_effect=fake_reresolve,
         ), \
         mock.patch.object(srv, "_invalidate_jobs_list_cache"), \
         mock.patch.object(srv.time, "sleep", side_effect=_sleep):
        try:
            srv._apply_resolve_backlog_loop()
        except StopIteration:
            pass
    assert len(captured) == 1
    assert captured[0].get("since_iso") is None
    assert captured[0].get("limit") == 8
    assert captured[0].get("concurrency") == 3
    assert captured[0].get("write") is True
    # Reliable-only first, then checkpointed full search.
    assert len(reresolve_calls) == 2
    assert reresolve_calls[0].get("reliable_only") is True
    assert reresolve_calls[0].get("limit") == 80
    assert reresolve_calls[0].get("workers") == 6
    assert reresolve_calls[0].get("write") is True
    assert reresolve_calls[1].get("reliable_only") is False
    assert reresolve_calls[1].get("include_linkedin") is True
    assert reresolve_calls[1].get("limit") == 40
    assert reresolve_calls[1].get("write") is True
    assert reresolve_calls[1].get("workers") == 4


def test_set_discovery_resolve_progress_updates_label() -> None:
    with tempfile.TemporaryDirectory() as td:
        ck = Path(td) / "discovery_checkpoint.json"
        _reset_discovery(ck)
        assert srv._begin_discovery()
        srv._set_discovery_resolve_progress(3, 10)
        status = srv.discovery_status()
        assert status["phase"] == "resolving"
        assert status["phase_label"] == "Resolving apply links… 3/10"
        assert status["resolve_done"] == 3
        assert status["resolve_total"] == 10
        srv._finish_discovery(True)


def test_run_discovery_apply_resolve_scopes_to_started_at() -> None:
    with tempfile.TemporaryDirectory() as td:
        ck = Path(td) / "discovery_checkpoint.json"
        _reset_discovery(ck)
        assert srv._begin_discovery()
        with srv._discovery_lock:
            started = srv._discovery_state["started_at"]

        captured: dict = {}

        def fake_resolve(**kwargs):
            captured.update(kwargs)
            if kwargs.get("progress_cb"):
                kwargs["progress_cb"](1, 2)
                kwargs["progress_cb"](2, 2)
            return {
                "considered": 2,
                "linkedin": 2,
                "other": 0,
                "high": 1,
                "upgraded": [{"id": "x", "url": "https://boards.greenhouse.io/a/jobs/1"}],
                "aborted": False,
            }

        import resolve_apply_urls as rau

        with mock.patch.object(rau, "resolve_discovery_apply_urls", side_effect=fake_resolve):
            summary = srv._run_discovery_apply_resolve()

        assert summary is not None
        assert captured.get("write") is True
        assert captured.get("concurrency") == 20
        assert captured.get("since_iso") == started
        assert callable(captured.get("progress_cb"))
        assert callable(captured.get("abort_cb"))
        st = srv.discovery_status()
        assert st["phase"] == "resolving"
        assert "2/2" in (st.get("phase_label") or "")
        srv._finish_discovery(True)


def test_run_scout_calls_apply_resolve_after_dedup() -> None:
    """run_scout_scrape_then_dedup invokes resolve after merge when not aborted."""
    with tempfile.TemporaryDirectory() as td:
        ck = Path(td) / "discovery_checkpoint.json"
        _reset_discovery(ck)
        calls: list[str] = []

        def fake_resolve():
            calls.append("resolve")
            return {"considered": 1}

        # Minimal stub: skip real scrapes by enabling nothing and short-circuiting.
        with mock.patch.object(srv, "_discovery_enabled_set", return_value=set()), \
             mock.patch.object(srv, "resolve_discovery_recency", return_value={
                 "days": 7,
                 "source_days": {s: 7 for s in srv.DISCOVERY_SOURCE_IDS},
                 "source_hours": {s: 168 for s in srv.SCOUT_SOURCE_IDS},
                 "last_successful_discover_at": None,
                 "jobs_gap_days": None,
             }), \
             mock.patch.object(srv, "enabled_discovery_regions", return_value=["us"]), \
             mock.patch.object(srv, "_run_subprocess_step", return_value=(0, Path("/dev/null"))), \
             mock.patch.object(srv, "_run_discovery_apply_resolve", side_effect=fake_resolve), \
             mock.patch.object(srv, "_finish_discovery") as finish, \
             mock.patch.object(srv, "_listing_file_nonempty", return_value=False), \
             mock.patch.object(srv, "_flush_discovery_checkpoint"), \
             mock.patch("known_job_urls.load_known_url_keys", return_value=set()), \
             mock.patch("known_job_urls.write_skip_urls_file"):
            assert srv._begin_discovery(set())
            # Tracker step needs skip file to exist — create it via side effect
            skip = ROOT / "logs" / "tracked-companies-skip.json"
            skip.parent.mkdir(parents=True, exist_ok=True)
            skip.write_text("[]")

            def _step(cmd, log_name, *_a, **_k):
                if "tracker" in str(log_name) or "list-companies" in " ".join(str(c) for c in cmd):
                    return 0, Path("/dev/null")
                return 0, Path("/dev/null")

            with mock.patch.object(srv, "_run_subprocess_step", side_effect=_step):
                srv.run_scout_scrape_then_dedup()

        assert calls == ["resolve"]
        finish.assert_called()
        assert finish.call_args[0][0] is True


if __name__ == "__main__":
    test_discovery_phase_labels_include_resolving()
    test_discovery_phase_labels_include_tagging()
    test_set_discovery_resolve_progress_updates_label()
    test_run_discovery_apply_resolve_scopes_to_started_at()
    test_run_scout_calls_apply_resolve_after_dedup()
    test_apply_resolve_backlog_skips_while_discovery_running()
    test_apply_resolve_backlog_passes_limit_no_since()
    print("OK test_discovery_apply_resolve")
