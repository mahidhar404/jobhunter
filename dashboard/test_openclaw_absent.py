#!/usr/bin/env python3
"""End-to-end-ish checks that the dashboard runs with `openclaw` absent.

Covers the server wiring of the OpenClaw-free replacements:
  - run_agent_message with no DEEPSEEK_API_KEY degrades a job to `stuck`
    (never crashes) — the core safety fallback.
  - the cron endpoints operate on local scheduler settings.
  - abort_gateway_session / gateway_running_session_keys are openclaw-free.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402
import agent_runner as ar  # noqa: E402
import scheduler as sched  # noqa: E402


def test_run_agent_message_no_key_marks_job_stuck(tmp_jobs=None):
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        jobs_file = tmp / "jobs.json"
        jobs_file.write_text(json.dumps({"jobs": [{
            "id": "1", "session_key": "agent:job-hunter:job-1",
            "status": "tailoring", "updated_at": srv.now_iso(),
        }]}))
        lock_file = jobs_file.with_suffix(".json.lock")
        with mock.patch.object(srv, "JOBS_FILE", jobs_file), \
             mock.patch.object(srv, "JOBS_LOCK_FILE", lock_file), \
             mock.patch.object(ar, "ROOT", tmp), \
             mock.patch.object(ar, "LOGS_DIR", tmp / "logs"), \
             mock.patch.dict(ar.os.environ, {}, clear=True):
            # No key configured → runner returns EXIT_NO_KEY → server marks stuck.
            srv.run_agent_message("agent:job-hunter:job-1", "continue", timeout_s=30)
            data = json.loads(jobs_file.read_text())
            job = data["jobs"][0]
            assert job["status"] == "stuck", job
            assert "Never submitted" in (job.get("status_detail") or "")


def test_cron_endpoints_use_local_settings():
    with tempfile.TemporaryDirectory() as td:
        with mock.patch.object(sched, "CRON_SETTINGS_FILE", Path(td) / "cron.json"):
            job = srv._find_cron_job()
            assert job["name"] == "job-hunter-daily"
            assert job["enabled"] is False
            sched.write_settings(enabled=True, hour=8, minute=15)
            pub = srv._cron_job_public(srv._find_cron_job())
            assert pub["enabled"] is True
            assert pub["hour"] == 8 and pub["minute"] == 15
            assert pub["time"] == "08:15"


def test_gateway_keys_and_abort_are_local():
    # No turns active → empty; abort is a safe no-op.
    assert srv.gateway_running_session_keys() == ar.active_turn_keys()
    srv.abort_gateway_session("agent:job-hunter:job-none")  # must not raise


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("all openclaw-absent tests passed")
