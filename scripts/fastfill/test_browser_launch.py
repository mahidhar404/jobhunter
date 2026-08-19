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
    build_chrome_user_agent,
    build_persistent_context_kwargs,
    detect_chrome_version,
    fill_chrome_exclude_markers,
    fill_profile_marker,
    resolve_browser_user_agent,
    resolve_fill_browser_channel,
    resolve_fill_profile_dir,
    resolve_playwright_chromium_executable,
    resolve_viewport,
    resolve_wipe_profile_on_teardown,
    system_timezone_id,
    wipe_fill_profile_dir,
    wipe_fill_profiles_for_job,
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
    assert "user_agent" not in kw  # bundled Chromium — Playwright default UA
    assert kw["viewport"]["width"] >= 1024
    assert kw["timezone_id"]
    assert kw.get("chrome_version_detected")


def test_persistent_context_headed_has_chrome_user_agent():
    d = resolve_fill_profile_dir(job_id="x", run_token="t2")
    kw = build_persistent_context_kwargs(profile_dir=d, headless=False)
    if sys.platform == "darwin":
        assert "Chrome/" in kw.get("user_agent", "")
        assert "Chrome/120.0.0.0" not in kw.get("user_agent", "")


def test_resolve_browser_user_agent_bundled_vs_chrome():
    assert resolve_browser_user_agent(channel=None, executable_path=None) is None
    ua = resolve_browser_user_agent(channel="chrome")
    assert ua and "Chrome/" in ua


def test_chrome_user_agent_matches_detected_version():
    ver = detect_chrome_version()
    ua = build_chrome_user_agent(ver)
    assert ver.split(".")[0] in ua
    assert "Safari/537.36" in ua


def test_system_timezone_nonempty():
    assert "/" in system_timezone_id() or system_timezone_id()


def test_headed_kwargs_place_right_two_thirds():
    from window_geometry import ScreenMetrics, chromium_window_args, right_two_thirds_outer

    metrics = ScreenMetrics(
        screen_x=0,
        screen_y=0,
        screen_width=1512,
        screen_height=982,
        visible_x=0,
        visible_y=25,
        visible_width=1512,
        visible_height=887,
        scale=2.0,
        is_primary=True,
    )
    d = resolve_fill_profile_dir(job_id="geom", run_token="t3")
    prev = os.environ.pop("FASTFILL_VIEWPORT", None)
    try:
        kw = build_persistent_context_kwargs(
            profile_dir=d, headless=False, screen_metrics=metrics
        )
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_VIEWPORT", None)
        else:
            os.environ["FASTFILL_VIEWPORT"] = prev
    outer = right_two_thirds_outer(metrics)
    blob = " ".join(kw.get("args") or [])
    for flag in chromium_window_args(outer):
        assert flag in blob
    assert kw["_jh_window_outer"] == outer
    assert kw["viewport"]["width"] == outer.width
    assert kw["headless"] is False


def test_resolve_viewport_default():
    vp = resolve_viewport()
    assert vp["width"] == 1440
    assert vp["height"] == 900


def test_wipe_fill_profile_dir_only_under_root(tmp_path, monkeypatch):
    import browser_launch as bl

    monkeypatch.setattr(bl, "FILL_PROFILES_ROOT", tmp_path)
    prof = tmp_path / "job1_abc"
    prof.mkdir()
    (prof / "Default").mkdir()
    res = wipe_fill_profile_dir(prof)
    assert res["wiped"] is True
    assert not prof.exists()
    outside = tmp_path.parent / "outside_profile"
    outside.mkdir(exist_ok=True)
    bad = wipe_fill_profile_dir(outside)
    assert bad.get("wiped") is False


def test_wipe_profiles_for_job_prefix(tmp_path, monkeypatch):
    import browser_launch as bl

    monkeypatch.setattr(bl, "FILL_PROFILES_ROOT", tmp_path)
    (tmp_path / "acme_aaa").mkdir()
    (tmp_path / "acme_bbb").mkdir()
    (tmp_path / "other_ccc").mkdir()
    out = wipe_fill_profiles_for_job("acme")
    assert set(out["removed"]) == {"acme_aaa", "acme_bbb"}


def test_wipe_profile_default_on():
    prev = os.environ.pop("FASTFILL_WIPE_PROFILE", None)
    try:
        assert resolve_wipe_profile_on_teardown() is True
        os.environ["FASTFILL_WIPE_PROFILE"] = "0"
        assert resolve_wipe_profile_on_teardown() is False
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_WIPE_PROFILE", None)
        else:
            os.environ["FASTFILL_WIPE_PROFILE"] = prev


def test_fill_profile_marker():
    assert "job_hunter_fill_profiles" in fill_profile_marker()


def test_exclude_markers_protect_dashboard_and_partyrock():
    markers = fill_chrome_exclude_markers()
    blob = " ".join(markers)
    assert "dashboard_ui_profile" in blob
    assert "openclaw/user-data" in blob


def test_fast_fill_source_wipes_profile_on_early_abort():
    src = (HERE / "fast_fill.py").read_text(encoding="utf-8")
    assert "def _wipe_fill_profile_dir" in src
    assert "_wipe_fill_profile_dir(profile_dir, report)" in src
    assert "headed_cap REFUSED" in src
    assert "launch FAILED (fail-fast" in src


def test_fast_fill_source_uses_persistent_context():
    src = (HERE / "fast_fill.py").read_text(encoding="utf-8")
    assert "launch_persistent_context" in src
    assert "build_persistent_context_kwargs" in src
    assert 'channel="chrome"' not in src  # channel set via browser_launch kwargs


def test_close_fill_context_idempotent():
    import asyncio
    import tempfile
    from unittest.mock import AsyncMock, MagicMock, patch

    import fast_fill as ff

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        prof = root / "job1_test"
        prof.mkdir()
        (prof / "Default").mkdir()
        ctx = MagicMock()
        ctx._jh_fill_teardown = False
        close_calls = 0

        async def _close():
            nonlocal close_calls
            close_calls += 1

        ctx.close = AsyncMock(side_effect=_close)
        report: dict = {}

        async def _run():
            with patch("browser_launch.FILL_PROFILES_ROOT", root):
                await ff._close_fill_context(ctx, profile_dir=prof, report=report)
                await ff._close_fill_context(ctx, profile_dir=prof, report=report)

        asyncio.run(_run())
        assert close_calls == 1
        assert report.get("fill_profile_wiped", {}).get("wiped") is True


def test_ashby_storage_clear_before_entry_prepass_in_source():
    src = (HERE / "fast_fill.py").read_text(encoding="utf-8")
    early = src.find('report["ashby_storage_cleared"] = cleared')
    entry = src.find("prepass = await entry_prepass(page")
    assert early > 0 and entry > 0
    assert early < entry


def test_stealth_readback_retry_in_source():
    src = (HERE / "fast_fill.py").read_text(encoding="utf-8")
    assert "stealth_readback_retry" in src


def test_find_fill_chrome_pid_for_profile_source():
    src = (HERE / "browser_launch.py").read_text(encoding="utf-8")
    assert "def find_fill_chrome_pid_for_profile" in src


def test_fast_fill_notes_chrome_pid_for_hud():
    src = (HERE / "fast_fill.py").read_text(encoding="utf-8")
    assert "note_fill_chrome_for_hud" in src


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
    test_persistent_context_headed_has_chrome_user_agent()
    test_resolve_browser_user_agent_bundled_vs_chrome()
    test_chrome_user_agent_matches_detected_version()
    test_system_timezone_nonempty()
    test_resolve_viewport_default()
    test_headed_kwargs_place_right_two_thirds()
    test_wipe_profile_default_on()
    test_fill_profile_marker()
    test_exclude_markers_protect_dashboard_and_partyrock()
    test_fast_fill_source_uses_persistent_context()
    test_fast_fill_source_wipes_profile_on_early_abort()
    test_close_fill_context_idempotent()
    test_ashby_storage_clear_before_entry_prepass_in_source()
    test_stealth_readback_retry_in_source()
    test_find_fill_chrome_pid_for_profile_source()
    test_fast_fill_notes_chrome_pid_for_hud()
    test_fill_pause_default_no_dom_overlay()
    print("test_browser_launch: OK")
