#!/usr/bin/env python3
"""Unit tests for browser_launch (Chrome channel + fill profiles)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from browser_launch import (  # noqa: E402
    FILL_PROFILES_ROOT,
    build_persistent_context_kwargs,
    fill_chrome_exclude_markers,
    fill_profile_marker,
    resolve_fill_browser_channel,
    resolve_fill_profile_dir,
    resolve_playwright_chromium_executable,
)


def test_resolve_fill_profile_dir_unique():
    a = resolve_fill_profile_dir(job_id="job-1", run_token="aaa")
    b = resolve_fill_profile_dir(job_id="job-1", run_token="bbb")
    assert a != b
    assert "job-1" in str(a)
    assert str(a).startswith(str(FILL_PROFILES_ROOT))


def test_headed_channel_is_chrome_on_darwin():
    prev = os.environ.pop("FASTFILL_FILL_CHANNEL", None)
    try:
        if sys.platform == "darwin":
            assert resolve_fill_browser_channel(headless=False) == "chrome"
        assert resolve_fill_browser_channel(headless=True) is None
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_FILL_CHANNEL", None)
        else:
            os.environ["FASTFILL_FILL_CHANNEL"] = prev


def test_cft_executable_only_when_opt_in():
    prev = os.environ.get("FASTFILL_USE_CFT")
    os.environ.pop("FASTFILL_USE_CFT", None)
    try:
        assert resolve_playwright_chromium_executable() is None
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_USE_CFT", None)
        else:
            os.environ["FASTFILL_USE_CFT"] = prev


def test_persistent_context_kwargs_include_profile():
    d = resolve_fill_profile_dir(job_id="x", run_token="t1")
    kw = build_persistent_context_kwargs(profile_dir=d, headless=True)
    assert kw["user_data_dir"] == str(d)
    assert "ignore_default_args" in kw


def test_fill_profile_marker():
    assert "job_hunter_fill_profiles" in fill_profile_marker()


def test_exclude_markers_protect_dashboard_and_partyrock():
    markers = fill_chrome_exclude_markers()
    blob = " ".join(markers)
    assert "dashboard_ui_profile" in blob
    assert "openclaw/user-data" in blob


def test_fast_fill_source_uses_persistent_context():
    src = (HERE / "fast_fill.py").read_text(encoding="utf-8")
    assert "launch_persistent_context" in src
    assert "build_persistent_context_kwargs" in src
    assert 'channel="chrome"' not in src  # channel set via browser_launch kwargs


def test_fill_pause_default_no_dom_overlay():
    src = (HERE / "fill_pause.py").read_text(encoding="utf-8")
    assert "use_dom_overlay" in src
    assert "use_native_hud" in src
    assert "fill_pause_hud.py" in src or "start_native_hud" in src


if __name__ == "__main__":
    test_resolve_fill_profile_dir_unique()
    test_headed_channel_is_chrome_on_darwin()
    test_cft_executable_only_when_opt_in()
    test_persistent_context_kwargs_include_profile()
    test_fill_profile_marker()
    test_exclude_markers_protect_dashboard_and_partyrock()
    test_fast_fill_source_uses_persistent_context()
    test_fill_pause_default_no_dom_overlay()
    print("test_browser_launch: OK")
