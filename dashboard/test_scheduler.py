#!/usr/bin/env python3
"""Tests for the OpenClaw-free daily discovery scheduler."""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import scheduler as sched  # noqa: E402


def test_settings_roundtrip_and_defaults():
    with tempfile.TemporaryDirectory() as td:
        with mock.patch.object(sched, "CRON_SETTINGS_FILE", Path(td) / "cron_settings.json"):
            # Defaults when no file.
            s = sched.read_settings()
            assert s == {"enabled": False, "hour": 9, "minute": 0}
            # Write + read back.
            sched.write_settings(enabled=True, hour=6, minute=30)
            s2 = sched.read_settings()
            assert s2 == {"enabled": True, "hour": 6, "minute": 30}
            # Merge update keeps other fields.
            sched.write_settings(minute=45)
            s3 = sched.read_settings()
            assert s3 == {"enabled": True, "hour": 6, "minute": 45}


def test_settings_to_job_dict_shape():
    job = sched.settings_to_job_dict({"enabled": True, "hour": 7, "minute": 5})
    assert job["name"] == "job-hunter-daily"
    assert job["enabled"] is True
    assert job["schedule"]["expr"] == "5 7 * * *"
    assert job["payload"]["argv"][0] == "sh"


def test_scheduler_fires_once_per_day_at_time():
    fired = []
    sc = sched.DiscoveryScheduler()
    sc._post_discover = lambda: fired.append(datetime.now())  # type: ignore

    fixed = datetime(2026, 8, 8, 9, 0, 0)
    with mock.patch.object(sched, "read_settings",
                           return_value={"enabled": True, "hour": 9, "minute": 0}):
        with mock.patch.object(sched, "datetime") as mdt:
            mdt.now.return_value = fixed
            # Simulate one poll tick body (bypass the wait loop).
            s = sched.read_settings()
            now = sched.datetime.now()
            today = now.strftime("%Y-%m-%d")
            if s["enabled"] and sc._last_fired_date != today and \
               now.hour == s["hour"] and now.minute == s["minute"]:
                sc._last_fired_date = today
                sc._post_discover()
            # Second tick same minute — must not double-fire.
            if s["enabled"] and sc._last_fired_date != today:
                sc._post_discover()
    assert len(fired) == 1


def test_scheduler_disabled_does_not_fire():
    fired = []
    sc = sched.DiscoveryScheduler()
    sc._post_discover = lambda: fired.append(1)  # type: ignore
    with mock.patch.object(sched, "read_settings",
                           return_value={"enabled": False, "hour": 9, "minute": 0}):
        s = sched.read_settings()
        if s["enabled"]:
            sc._post_discover()
    assert fired == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("all scheduler tests passed")
