#!/usr/bin/env python3
"""Per-source Discover recency (1–10 days) persistence and scraper wiring."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


def _isolated_settings():
    td = tempfile.TemporaryDirectory()
    settings_file = Path(td.name) / "discovery_settings.json"
    last_run = Path(td.name) / "discovery_last_run.json"
    return td, mock.patch.object(srv, "DISCOVERY_SETTINGS_FILE", settings_file), mock.patch.object(
        srv, "DISCOVERY_LAST_RUN_FILE", last_run
    )


def test_recency_capable_sources_are_date_filtered() -> None:
    assert set(srv.RECENCY_SOURCE_IDS) == {
        "indeed", "linkedin", "builtin", "adzuna_us", "adzuna",
    }
    for sid in srv.ATS_SOURCE_IDS:
        assert sid not in srv.RECENCY_SOURCE_IDS
    for sid in ("remoteok", "remotive", "jobicy", "rss_feeds",
                "internshala", "hirist", "cutshort"):
        assert sid not in srv.RECENCY_SOURCE_IDS


def test_normalize_source_days_accepts_1_to_10() -> None:
    for n in range(1, 11):
        assert srv.normalize_source_days(n) == n
        assert srv.normalize_source_days(str(n)) == n
    assert srv.normalize_source_days(None) == 7


def test_normalize_source_days_rejects_0_and_11() -> None:
    for bad in (0, 11, 14, 30, -1, "0", "11"):
        try:
            srv.normalize_source_days(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError as e:
            assert "1" in str(e) and "10" in str(e)


def test_source_days_round_trip() -> None:
    td, p1, p2 = _isolated_settings()
    with td, p1, p2:
        saved = srv.save_discovery_settings({
            "source_days": {"indeed": 3, "linkedin": 10, "builtin": 2},
        })
        reloaded = srv.load_discovery_settings()
    assert saved["source_days"]["indeed"] == 3
    assert reloaded["source_days"] == saved["source_days"]
    assert reloaded["builtin_days_since_updated"] == 2
    assert "greenhouse" not in reloaded["source_days"]


def test_source_days_merge_does_not_wipe_other_pins() -> None:
    td, p1, p2 = _isolated_settings()
    with td, p1, p2:
        srv.save_discovery_settings({"source_days": {"indeed": 4, "adzuna_us": 8}})
        saved = srv.save_discovery_settings({"source_days": {"linkedin": 1}})
    assert saved["source_days"]["indeed"] == 4
    assert saved["source_days"]["linkedin"] == 1
    assert saved["source_days"]["adzuna_us"] == 8


def test_source_days_invalid_on_save() -> None:
    td, p1, p2 = _isolated_settings()
    with td, p1, p2:
        try:
            srv.save_discovery_settings({"source_days": {"indeed": 0}})
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
        try:
            srv.save_discovery_settings({"source_days": {"indeed": 11}})
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


def test_builtin_days_alias_pins_source_days() -> None:
    td, p1, p2 = _isolated_settings()
    with td, p1, p2:
        saved = srv.save_discovery_settings({"builtin_days_since_updated": 9})
    assert saved["source_days"]["builtin"] == 9
    assert saved["builtin_days_since_updated"] == 9


def test_normalize_builtin_days_is_1_to_10() -> None:
    assert srv.normalize_builtin_days_since_updated(5) == 5
    assert srv.normalize_builtin_days_since_updated("10") == 10
    try:
        srv.normalize_builtin_days_since_updated(30)
        raise AssertionError("expected ValueError for 30")
    except ValueError:
        pass


def test_pinned_source_days_win_over_adaptive() -> None:
    now = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)
    last = (now - timedelta(days=20)).isoformat()
    td, p1, p2 = _isolated_settings()
    with td, p1, p2:
        srv.save_discovery_settings({
            "last_successful_discover_at": last,
            "source_days": {"indeed": 2, "builtin": 4},
        })
        rec = srv.resolve_discovery_recency(now=now)
    assert rec["days"] == 10  # adaptive N+1 capped at 10
    assert rec["source_days"]["indeed"] == 2
    assert rec["source_days"]["builtin"] == 4
    assert rec["source_days"]["linkedin"] == 10  # unpinned → adaptive
    assert rec["source_days"]["adzuna_us"] == 10
    assert rec["hours_old"] == 10 * 24
    assert rec["source_hours"]["indeed"] == 48


def test_unpinned_uses_adaptive_clamped_1_to_10() -> None:
    now = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)
    last = (now - timedelta(hours=2)).isoformat()
    td, p1, p2 = _isolated_settings()
    with td, p1, p2:
        srv.save_discovery_settings({"last_successful_discover_at": last})
        rec = srv.resolve_discovery_recency(now=now)
    assert rec["days"] == 7
    assert rec["source_days"]["indeed"] == 7
    assert rec["builtin_days"] == 7
    assert rec["source_days"] == {
        sid: 7 for sid in srv.RECENCY_SOURCE_IDS
    }


def test_scout_cmd_uses_source_hours() -> None:
    listing = Path("/tmp/fake-scout.json")
    cmd = srv._scout_scrape_cmd(listing, "linkedin", hours_old=96)
    assert "--hours-old" in cmd
    assert "96" in cmd


def test_builtin_cmd_accepts_any_1_to_10() -> None:
    listing = Path("/tmp/fake-builtin.json")
    cmd = srv._builtin_scrape_cmd(listing, skip_urls_file=None, days_since_updated=5)
    assert "--days-since-updated" in cmd
    assert "5" in cmd


def test_adzuna_cmd_includes_max_days() -> None:
    listing = Path("/tmp/fake-adzuna.json")
    cmd = srv._adzuna_scrape_cmd(listing, country="us", max_days=3)
    assert "--max-days" in cmd
    assert "3" in cmd
    assert "--country" in cmd
    assert "us" in cmd
    cmd_in = srv._adzuna_scrape_cmd(listing, country="in", max_days=9)
    assert "--max-days" in cmd_in
    assert "9" in cmd_in


def test_discovery_status_exposes_source_days_and_catalog_recency() -> None:
    td, p1, p2 = _isolated_settings()
    with td, p1, p2:
        srv.save_discovery_settings({"source_days": {"adzuna": 6}})
        status = srv.discovery_status()
    assert status["source_days"]["adzuna"] == 6
    assert "indeed" not in status["source_days"]
    by_id = {row["id"]: row for row in status["source_catalog"]}
    assert by_id["indeed"]["recency"] is True
    assert by_id["greenhouse"]["recency"] is False
    assert by_id["remoteok"]["recency"] is False
    assert by_id["builtin"]["recency"] is True


def test_ui_per_source_days_select() -> None:
    app_js = (ROOT / "dashboard" / "static" / "app.js").read_text()
    assert "saveSourceDaysSetting" in app_js
    assert "src-days" in app_js
    assert "recency: true" in app_js
    html = (ROOT / "dashboard" / "static" / "index.html").read_text()
    assert "src-days" in html
    # 1–10 options, not the old 30-day Built In snap
    assert "full board" in app_js.lower()


def test_load_clamps_legacy_thirty_builtin_days() -> None:
    td, p1, p2 = _isolated_settings()
    with td, p1, p2:
        srv.DISCOVERY_SETTINGS_FILE.write_text(json.dumps({
            "builtin_days_since_updated": 30,
            "discover_us": True,
            "discover_india": False,
        }))
        loaded = srv.load_discovery_settings()
    assert loaded["builtin_days_since_updated"] == 10
    assert loaded["source_days"].get("builtin") == 10
    assert loaded.get("discover_worldwide") is True
    assert "discover_us" not in loaded or loaded.get("discover_worldwide") is True


if __name__ == "__main__":
    test_recency_capable_sources_are_date_filtered()
    test_normalize_source_days_accepts_1_to_10()
    test_normalize_source_days_rejects_0_and_11()
    test_source_days_round_trip()
    test_source_days_merge_does_not_wipe_other_pins()
    test_source_days_invalid_on_save()
    test_builtin_days_alias_pins_source_days()
    test_normalize_builtin_days_is_1_to_10()
    test_pinned_source_days_win_over_adaptive()
    test_unpinned_uses_adaptive_clamped_1_to_10()
    test_scout_cmd_uses_source_hours()
    test_builtin_cmd_accepts_any_1_to_10()
    test_adzuna_cmd_includes_max_days()
    test_discovery_status_exposes_source_days_and_catalog_recency()
    test_ui_per_source_days_select()
    test_load_clamps_legacy_thirty_builtin_days()
    print("ok: discovery source days tests passed")
