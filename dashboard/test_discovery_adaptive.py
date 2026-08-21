#!/usr/bin/env python3
"""Adaptive recency + ATS timeout wiring on the dashboard."""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


def test_ats_timeout_raised() -> None:
    assert srv.ATS_SOURCE_TIMEOUT_S >= 1200
    assert srv.ATS_GUESS_BUDGET_S < srv.ATS_SOURCE_TIMEOUT_S
    assert srv.ATS_MAX_GUESSES <= 80


def test_ats_cmd_guess_after_fetch_budget() -> None:
    listing = Path("/tmp/fake-ats.json")
    cmd = srv._ats_scrape_cmd(listing, "greenhouse", skip_urls_file=None)
    joined = " ".join(cmd)
    assert "scrape_ats.py" in joined
    assert "--guess-budget-s" in cmd
    assert str(srv.ATS_GUESS_BUDGET_S) in cmd
    assert "--max-guesses" in cmd
    # Guessing is not the first phase; timeout lives in the dashboard wrapper.
    assert "--no-guess" not in cmd


def test_scout_cmd_hours_old() -> None:
    listing = Path("/tmp/fake-scout.json")
    cmd = srv._scout_scrape_cmd(listing, "indeed", hours_old=168)
    assert "--hours-old" in cmd
    assert "168" in cmd


def test_resolve_recency_floor_when_success_today() -> None:
    now = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)
    last = (now - timedelta(hours=2)).isoformat()
    with tempfile.TemporaryDirectory() as td:
        settings_file = Path(td) / "discovery_settings.json"
        last_run = Path(td) / "discovery_last_run.json"
        with mock.patch.object(srv, "DISCOVERY_SETTINGS_FILE", settings_file), \
             mock.patch.object(srv, "DISCOVERY_LAST_RUN_FILE", last_run):
            srv.save_discovery_settings({
                "last_successful_discover_at": last,
                "builtin_days_since_updated": 7,
            })
            rec = srv.resolve_discovery_recency(now=now)
    assert rec["days"] == 7
    assert rec["hours_old"] == 7 * 24
    assert rec["builtin_days"] == 7


def test_resolve_recency_widens_after_gap() -> None:
    now = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)
    last = (now - timedelta(days=20)).isoformat()
    with tempfile.TemporaryDirectory() as td:
        settings_file = Path(td) / "discovery_settings.json"
        last_run = Path(td) / "discovery_last_run.json"
        with mock.patch.object(srv, "DISCOVERY_SETTINGS_FILE", settings_file), \
             mock.patch.object(srv, "DISCOVERY_LAST_RUN_FILE", last_run):
            srv.save_discovery_settings({
                "last_successful_discover_at": last,
            })
            rec = srv.resolve_discovery_recency(now=now)
    assert rec["days"] == 10
    assert rec["builtin_days"] == 10  # unpinned → adaptive cap 10


if __name__ == "__main__":
    test_ats_timeout_raised()
    test_ats_cmd_guess_after_fetch_budget()
    test_scout_cmd_hours_old()
    test_resolve_recency_floor_when_success_today()
    test_resolve_recency_widens_after_gap()
    print("ok: discovery adaptive wiring tests passed")
