#!/usr/bin/env python3
"""Unit tests: discovery resume skips completed sources from checkpoint."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


def _reset(tmp_checkpoint: Path) -> None:
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
            "last_outcome": None,
            "last_summary": None,
            "last_jobs_added": None,
            "sources": [],
            "enabled_sources": list(srv.DISCOVERY_SOURCE_IDS),
            "can_abort": False,
            "abort_requested": False,
            "resumed": False,
            "resume_available": False,
            "run_id": None,
        })
        srv._discovery_procs_by_key.clear()
        srv._discovery_source_aborts.clear()
        srv._discovery_protect_proc = False
    srv._reset_checkpoint_meta()


def _write_incomplete_checkpoint(
    path: Path, *, completed: list[str], leftover: list[str],
) -> dict:
    today = srv._today_local_iso()
    sources = {}
    merged = []
    for sid in completed:
        listing = str(srv._source_listing_path(today, sid))
        sources[sid] = {
            "status": "completed",
            "count": 5,
            "detail": "5 listings",
            "enabled": True,
            "listing_path": listing,
            "merged": True,
        }
        merged.append(listing)
    for sid in leftover:
        listing = str(srv._source_listing_path(today, sid))
        sources[sid] = {
            "status": "stopped",
            "count": 1,
            "detail": "Interrupted",
            "enabled": True,
            "listing_path": listing,
            "merged": False,
        }
    enabled = completed + leftover
    payload = {
        "version": 1,
        "run_id": "test-run",
        "date": today,
        "started_at": "2026-08-03T12:00:00",
        "updated_at": "2026-08-03T12:30:00",
        "status": "incomplete",
        "enabled_sources": enabled,
        "sources": sources,
        "merged_paths": merged,
        "merges_ok": len(merged),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return payload


def test_begin_discovery_skips_completed_from_checkpoint() -> None:
    with tempfile.TemporaryDirectory() as td:
        ck = Path(td) / "discovery_checkpoint.json"
        _reset(ck)
        completed = ["indeed", "linkedin"]
        leftover = ["greenhouse", "builtin"]
        _write_incomplete_checkpoint(ck, completed=completed, leftover=leftover)

        enabled = set(completed + leftover)
        assert srv._checkpoint_has_leftover(srv._load_discovery_checkpoint(), enabled)
        assert srv._begin_discovery(enabled)

        status = srv.discovery_status()
        assert status["running"] is True
        assert status["resumed"] is True
        assert status["phase"] == "resuming"
        assert "Continuing" in (status.get("phase_label") or "")
        by_id = {s["id"]: s for s in status["sources"]}
        assert by_id["indeed"]["status"] == "completed"
        assert by_id["linkedin"]["status"] == "completed"
        assert by_id["greenhouse"]["status"] == "pending"
        assert "Continuing" in (by_id["greenhouse"].get("detail") or "")
        assert by_id["builtin"]["status"] == "pending"

        # Simulate runner skip set (same logic as run_scout_scrape_then_dedup).
        skip_ids = {
            s["id"] for s in status["sources"]
            if s.get("enabled") and s.get("status") == "completed"
        }
        assert skip_ids == {"indeed", "linkedin"}

        planned = []
        for sid in srv.SCOUT_SOURCE_IDS:
            if sid in enabled and sid not in skip_ids:
                planned.append(sid)
        for sid in srv.ATS_SOURCE_IDS:
            if sid in enabled and sid not in skip_ids:
                planned.append(sid)
        if "builtin" in enabled and "builtin" not in skip_ids:
            planned.append("builtin")
        assert "indeed" not in planned
        assert "linkedin" not in planned
        assert "greenhouse" in planned
        assert "builtin" in planned

        srv._mark_incomplete_sources_stopped()
        srv._finish_discovery(False, "test end")
        assert ck.exists(), "incomplete finish should keep checkpoint"
        assert srv.discovery_status()["resume_available"] is True


def test_hydrate_resume_banner_and_clear_on_full_finish() -> None:
    with tempfile.TemporaryDirectory() as td:
        ck = Path(td) / "discovery_checkpoint.json"
        _reset(ck)
        _write_incomplete_checkpoint(
            ck, completed=["indeed"], leftover=["linkedin"],
        )
        srv._hydrate_discovery_resume_banner()
        st = srv.discovery_status()
        assert st["resume_available"] is True
        assert st["running"] is False
        assert "Incomplete" in (st.get("error") or "")
        assert st.get("last_outcome") in ("interrupted", "partial", "failed")

        # Fresh complete run clears checkpoint.
        assert srv._begin_discovery({"indeed", "linkedin"})
        # Pretend both completed.
        with srv._discovery_lock:
            for src in srv._discovery_state["sources"]:
                if src.get("enabled"):
                    src["status"] = "completed"
                    src["detail"] = "done"
            srv._discovery_state["abort_requested"] = False
        srv._finish_discovery(True)
        assert not ck.exists()
        final = srv.discovery_status()
        assert final["resume_available"] is False
        assert final["last_outcome"] == "success"


def test_fresh_begin_clears_checkpoint() -> None:
    with tempfile.TemporaryDirectory() as td:
        ck = Path(td) / "discovery_checkpoint.json"
        _reset(ck)
        _write_incomplete_checkpoint(
            ck, completed=["indeed"], leftover=["linkedin"],
        )
        assert ck.exists()
        assert srv._begin_discovery({"indeed", "linkedin"}, fresh=True)
        st = srv.discovery_status()
        assert st["resumed"] is False
        by_id = {s["id"]: s for s in st["sources"]}
        assert by_id["indeed"]["status"] == "pending"
        assert by_id["linkedin"]["status"] == "pending"
        srv._finish_discovery(False, "test")


def test_last_run_outcome_partial_on_abort() -> None:
    with tempfile.TemporaryDirectory() as td:
        ck = Path(td) / "discovery_checkpoint.json"
        last = Path(td) / "discovery_last_run.json"
        prev_last = srv.DISCOVERY_LAST_RUN_FILE
        _reset(ck)
        srv.DISCOVERY_LAST_RUN_FILE = last
        try:
            assert srv._begin_discovery({"indeed", "linkedin"})
            with srv._discovery_lock:
                for src in srv._discovery_state["sources"]:
                    if src["id"] == "indeed":
                        src["status"] = "completed"
                        src["count"] = 3
                    elif src["id"] == "linkedin":
                        src["status"] = "stopped"
                srv._discovery_state["abort_requested"] = True
            srv._discovery_checkpoint_meta["jobs_added"] = 5
            srv._finish_discovery(True)
            st = srv.discovery_status()
            assert st["last_outcome"] in ("partial", "interrupted")
            assert st["resume_available"] is True
            assert "+5 jobs" in (st.get("last_summary") or "")
            data = json.loads(last.read_text())
            assert data["outcome"] == st["last_outcome"]
            assert data["jobs_added"] == 5
        finally:
            srv.DISCOVERY_LAST_RUN_FILE = prev_last


def test_runner_skips_completed_sources(monkeypatch_style: bool = True) -> None:
    """Integration-ish: run_scout_scrape_then_dedup only schedules leftover sources."""
    with tempfile.TemporaryDirectory() as td:
        ck = Path(td) / "discovery_checkpoint.json"
        _reset(ck)
        completed = ["indeed", "greenhouse"]
        leftover = ["linkedin"]
        _write_incomplete_checkpoint(ck, completed=completed, leftover=leftover)
        enabled = set(completed + leftover)
        assert srv._begin_discovery(enabled)

        ran_cmds: list[list[str]] = []

        def fake_subprocess_step(cmd, log_name, timeout_s, **kwargs):
            ran_cmds.append(list(cmd))
            # tracker.py list-companies
            if "tracker.py" in " ".join(cmd):
                out = Path(cmd[cmd.index("--out") + 1])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text("[]\n")
                return 0, Path(td) / log_name
            # scout / ats / builtin scrapes — write empty listing if --out present
            if "--out" in cmd:
                out = Path(cmd[cmd.index("--out") + 1])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text("[]\n")
            return 0, Path(td) / log_name

        with mock.patch.object(srv, "_run_subprocess_step", side_effect=fake_subprocess_step):
            with mock.patch.object(srv, "_incremental_merge_listing", return_value=False):
                with mock.patch.object(srv, "run_agent_message", return_value=None):
                    srv.run_scout_scrape_then_dedup()

        scrape_cmds = [
            c for c in ran_cmds
            if any(x in " ".join(c) for x in ("scout.py", "scrape_ats.py", "scrape_builtin.py"))
        ]
        joined = [" ".join(c) for c in scrape_cmds]
        assert any("linkedin" in j or "--sites linkedin" in j for j in joined), joined
        assert not any("--sites indeed" in j for j in joined), joined
        assert not any("scrape_ats.py" in j and "greenhouse" in j for j in joined), joined

        final = srv.discovery_status()
        assert final["running"] is False
        by_id = {s["id"]: s for s in final["sources"]}
        assert by_id["indeed"]["status"] == "completed"
        assert by_id["greenhouse"]["status"] == "completed"


if __name__ == "__main__":
    test_begin_discovery_skips_completed_from_checkpoint()
    test_hydrate_resume_banner_and_clear_on_full_finish()
    test_fresh_begin_clears_checkpoint()
    test_last_run_outcome_partial_on_abort()
    test_runner_skips_completed_sources()
    print("ok: discovery resume tests passed")
