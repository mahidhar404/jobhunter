#!/usr/bin/env python3
"""OpenClaw-free daily discovery scheduler.

Replaces the ``openclaw cron`` registration whose only job was, verbatim:

    sh -lc "curl -s -X POST http://127.0.0.1:8787/api/discover"

fired on a daily cron expr (``0 9 * * *`` by default). OpenClaw added nothing
here beyond being a cron daemon. This module is a tiny in-process scheduler
that does the exact same thing — POST ``/api/discover`` to the dashboard's own
port at the configured wall-clock time — while the dashboard is running (which
is when discovery can succeed anyway; the old cron logged 7 consecutive errors
precisely because it fired at 09:00 while the dashboard was down).

Settings persist in ``logs/cron_settings.json`` instead of the OpenClaw cron
store. ``settings_to_job_dict`` returns a job-shaped dict compatible with the
existing dashboard ``/api/cron`` UI (``id``/``name``/``enabled``/``schedule``),
so the frontend and ``_cron_job_public`` keep working unchanged.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRON_SETTINGS_FILE = ROOT / "logs" / "cron_settings.json"
CRON_JOB_NAME = "job-hunter-daily"
CRON_JOB_ID = "job-hunter-daily-local"

DEFAULT_HOUR = 9
DEFAULT_MINUTE = 0


def read_settings() -> dict:
    """Load persisted schedule settings, filling defaults."""
    hour, minute, enabled = DEFAULT_HOUR, DEFAULT_MINUTE, False
    try:
        data = json.loads(CRON_SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            enabled = bool(data.get("enabled", False))
            h = data.get("hour")
            m = data.get("minute")
            if isinstance(h, int) and 0 <= h <= 23:
                hour = h
            if isinstance(m, int) and 0 <= m <= 59:
                minute = m
    except (OSError, ValueError):
        pass
    return {"enabled": enabled, "hour": hour, "minute": minute}


def write_settings(*, enabled: bool | None = None, hour: int | None = None,
                   minute: int | None = None) -> dict:
    """Merge-update and persist schedule settings; returns the new settings."""
    cur = read_settings()
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    if hour is not None:
        cur["hour"] = int(hour)
    if minute is not None:
        cur["minute"] = int(minute)
    CRON_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CRON_SETTINGS_FILE.write_text(json.dumps(cur, indent=2) + "\n", encoding="utf-8")
    return cur


def settings_to_job_dict(settings: dict | None = None) -> dict:
    """Shape settings like the OpenClaw cron job the dashboard UI expects."""
    s = settings or read_settings()
    hour = int(s.get("hour", DEFAULT_HOUR))
    minute = int(s.get("minute", DEFAULT_MINUTE))
    return {
        "id": CRON_JOB_ID,
        "name": CRON_JOB_NAME,
        "enabled": bool(s.get("enabled", False)),
        "schedule": {"kind": "cron", "expr": f"{minute} {hour} * * *"},
        "payload": {
            "kind": "command",
            "argv": ["sh", "-lc", "curl -s -X POST http://127.0.0.1:8787/api/discover"],
        },
    }


class DiscoveryScheduler:
    """Background thread that POSTs /api/discover once/day at the set time.

    Fires when ``enabled`` and the current local time matches HH:MM, at most
    once per calendar day. Re-reads settings each tick so toggles/reschedules
    from the dashboard take effect without a restart.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8787,
                 poll_interval_s: float = 20.0):
        self.host = host
        self.port = port
        self.poll_interval_s = poll_interval_s
        self._stop = threading.Event()
        self._last_fired_date: str | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="discovery-scheduler"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _post_discover(self) -> None:
        url = f"http://{self.host}:{self.port}/api/discover"
        try:
            req = urllib.request.Request(url, data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15).read()
            print(f"scheduler: fired daily discovery POST {url}")
        except Exception as e:
            print(f"warn: scheduler discovery POST failed: {e}")

    def _run(self) -> None:
        while not self._stop.wait(self.poll_interval_s):
            try:
                s = read_settings()
                if not s.get("enabled"):
                    continue
                now = datetime.now()
                today = now.strftime("%Y-%m-%d")
                if self._last_fired_date == today:
                    continue
                if now.hour == int(s["hour"]) and now.minute == int(s["minute"]):
                    self._last_fired_date = today
                    self._post_discover()
            except Exception as e:
                print(f"warn: scheduler loop error: {e}")
