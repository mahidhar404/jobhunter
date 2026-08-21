#!/usr/bin/env python3
"""Tests: Built In days setting persistence + discovery command wiring."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


def test_discovery_settings_default_builtin_days_is_seven() -> None:
    with tempfile.TemporaryDirectory() as td:
        settings_file = Path(td) / "discovery_settings.json"
        with mock.patch.object(srv, "DISCOVERY_SETTINGS_FILE", settings_file):
            defaults = srv.load_discovery_settings()
    assert defaults["builtin_days_since_updated"] == 7


def test_discovery_settings_round_trip_allowlisted_days() -> None:
    with tempfile.TemporaryDirectory() as td:
        settings_file = Path(td) / "discovery_settings.json"
        with mock.patch.object(srv, "DISCOVERY_SETTINGS_FILE", settings_file):
            saved = srv.save_discovery_settings({"builtin_days_since_updated": 7})
            reloaded = srv.load_discovery_settings()
    assert saved == reloaded
    assert reloaded["builtin_days_since_updated"] == 7


def test_discovery_settings_reject_unsupported_days() -> None:
    with tempfile.TemporaryDirectory() as td:
        settings_file = Path(td) / "discovery_settings.json"
        with mock.patch.object(srv, "DISCOVERY_SETTINGS_FILE", settings_file):
            try:
                srv.save_discovery_settings({"builtin_days_since_updated": 14})
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "builtin_days_since_updated" in str(e)


def test_normalize_builtin_days_allowlist() -> None:
    assert srv.normalize_builtin_days_since_updated(1) == 1
    assert srv.normalize_builtin_days_since_updated("10") == 10
    assert srv.normalize_builtin_days_since_updated(None) == 7
    try:
        srv.normalize_builtin_days_since_updated(14)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    try:
        srv.normalize_builtin_days_since_updated(30)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_builtin_scrape_cmd_includes_days_flag() -> None:
    listing = Path("/tmp/fake-builtin.json")
    cmd = srv._builtin_scrape_cmd(listing, skip_urls_file=None, days_since_updated=3)
    assert str(ROOT / "scripts" / "scrape_builtin.py") in " ".join(cmd) or any(
        "scrape_builtin.py" in c for c in cmd
    )
    assert "--days-since-updated" in cmd
    assert "3" in cmd
    # days flag only; other sources unaffected by this helper
    assert "--sites" not in cmd


def test_discovery_status_exposes_builtin_days() -> None:
    with tempfile.TemporaryDirectory() as td:
        settings_file = Path(td) / "discovery_settings.json"
        with mock.patch.object(srv, "DISCOVERY_SETTINGS_FILE", settings_file):
            srv.save_discovery_settings({"builtin_days_since_updated": 10})
            status = srv.discovery_status()
    assert status.get("builtin_days_since_updated") == 10


def test_ui_discover_popover_has_per_source_days_control() -> None:
    """Wiring smoke: popover markup/JS must expose 1–10 lookback per source."""
    app_js = (ROOT / "dashboard" / "static" / "app.js").read_text()
    assert "saveSourceDaysSetting" in app_js
    assert "src-days" in app_js
    for v in range(1, 11):
        assert f">{v}d<" in app_js or f"${{d}}d" in app_js


if __name__ == "__main__":
    test_discovery_settings_default_builtin_days_is_seven()
    test_discovery_settings_round_trip_allowlisted_days()
    test_discovery_settings_reject_unsupported_days()
    test_normalize_builtin_days_allowlist()
    test_builtin_scrape_cmd_includes_days_flag()
    test_discovery_status_exposes_builtin_days()
    test_ui_discover_popover_has_per_source_days_control()
    print("ok: discovery builtin settings tests passed")
