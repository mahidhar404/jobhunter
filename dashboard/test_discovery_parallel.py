#!/usr/bin/env python3
"""Smoke test: parallel discovery scrapes + abort kills all process groups."""
from __future__ import annotations

import concurrent.futures
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


FAKE_SCRAPE = [
    sys.executable,
    "-u",
    "-c",
    (
        "import sys, time\n"
        "print('got 3 new results from indeed/fake', flush=True)\n"
        "print('got 2 relevant results from greenhouse/fake (1/1 done)', flush=True)\n"
        "print('processed 1/10 (4 usable so far)', flush=True)\n"
        "time.sleep(60)\n"
    ),
]


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
        })
        srv._discovery_current_procs.clear()
        srv._discovery_protect_proc = False
    srv._reset_checkpoint_meta()


def test_parallel_procs_update_sources_and_abort_kills_all() -> None:
    with tempfile.TemporaryDirectory() as td:
        ck = Path(td) / "discovery_checkpoint.json"
        _reset_discovery(ck)
        assert srv._begin_discovery()
        srv._set_discovery_phase("scraping")
        srv._update_discovery_sources(srv.SCOUT_SOURCE_IDS, status="collecting", detail="Starting…")
        srv._update_discovery_sources(srv.ATS_SOURCE_IDS, status="collecting", detail="Starting…")
        srv._update_discovery_sources(("builtin",), status="collecting", detail="Starting…")

        results: dict[str, tuple[int, Path]] = {}

        def run_one(name: str, mode: str, log_name: str) -> tuple[str, int, Path]:
            code, log = srv._run_subprocess_step(
                FAKE_SCRAPE,
                log_name,
                90,
                track_key=f"{srv.DISCOVERY_SESSION_KEY}:{name}",
                allow_abort=True,
                log_parse_mode=mode,
            )
            return name, code, log

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futs = [
                pool.submit(run_one, "scout", "scout", "test_parallel_scout.log"),
                pool.submit(run_one, "ats", "ats", "test_parallel_ats.log"),
                pool.submit(run_one, "builtin", "builtin", "test_parallel_builtin.log"),
            ]

            # Wait until all three scrapes are registered and log tails have
            # bumped per-source counts (poll interval inside _run_subprocess_step
            # is ~0.4s).
            deadline = time.monotonic() + 15
            by_id: dict = {}
            while time.monotonic() < deadline:
                with srv._discovery_lock:
                    n = len(srv._discovery_current_procs)
                status = srv.discovery_status()
                by_id = {s["id"]: s for s in status["sources"]}
                if (
                    n >= 3
                    and int(by_id.get("indeed", {}).get("count") or 0) >= 3
                    and int(by_id.get("greenhouse", {}).get("count") or 0) >= 2
                    and int(by_id.get("builtin", {}).get("count") or 0) >= 4
                ):
                    break
                time.sleep(0.05)
            else:
                with srv._discovery_lock:
                    n = len(srv._discovery_current_procs)
                raise AssertionError(
                    f"parallel scrape state not ready: procs={n} "
                    f"indeed={by_id.get('indeed')} greenhouse={by_id.get('greenhouse')} "
                    f"builtin={by_id.get('builtin')}"
                )

            assert by_id["indeed"]["status"] == "collecting"
            assert by_id["greenhouse"]["status"] == "collecting"
            assert by_id["builtin"]["status"] == "collecting"
            collecting = sum(
                1 for s in srv.discovery_status()["sources"] if s["status"] == "collecting"
            )
            assert collecting >= 3, f"expected concurrent collecting sources, got {collecting}"

            body, code = srv.request_discovery_abort()
            assert code == 200, body
            assert body.get("aborting") is True

            for fut in concurrent.futures.as_completed(futs):
                name, exit_code, log = fut.result()
                results[name] = (exit_code, log)

        assert set(results) == {"scout", "ats", "builtin"}
        for name, (exit_code, _) in results.items():
            assert exit_code == srv.DISCOVERY_ABORT_EXIT, f"{name} exit={exit_code}"

        with srv._discovery_lock:
            leftover = list(srv._discovery_current_procs)
        assert leftover == [], f"abort left procs registered: {leftover}"

        srv._mark_incomplete_sources_stopped()
        srv._finish_discovery(False, "Aborted by user")
        final = srv.discovery_status()
        assert final["running"] is False
        unfinished = [
            s["id"] for s in final["sources"]
            if s["status"] in ("pending", "collecting")
        ]
        assert unfinished == [], f"sources still unfinished: {unfinished}"
        _reset_discovery(ck)


def test_abort_while_not_running_is_409() -> None:
    with tempfile.TemporaryDirectory() as td:
        ck = Path(td) / "discovery_checkpoint.json"
        _reset_discovery(ck)
        body, code = srv.request_discovery_abort()
        assert code == 409
        assert "not running" in (body.get("error") or "")


if __name__ == "__main__":
    test_abort_while_not_running_is_409()
    test_parallel_procs_update_sources_and_abort_kills_all()
    print("ok: parallel discovery abort smoke passed")
