#!/usr/bin/env python3
"""Unit tests for fill/PartyRock right-two-thirds window geometry.

Fake screen metrics only — no live GUI required (CI-safe).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from window_geometry import (  # noqa: E402
    DOCK_FALLBACK_PX,
    INSET_PX,
    MENUBAR_FALLBACK_PX,
    TITLEBAR_PX,
    Rect,
    ScreenMetrics,
    _guard_usable_frame,
    chrome_cdp_bounds,
    chrome_cdp_fullscreen_bounds,
    chromium_dashboard_launch_flags,
    chromium_window_args,
    macos_fullscreen_applescript,
    left_third_outer,
    maximized_outer,
    pick_screen,
    playwright_viewport,
    right_two_thirds_outer,
    system_events_size,
    work_window_plan,
)


def _mbp14() -> ScreenMetrics:
    """Typical 14\" MacBook: 1512×982 logical, 25px menu bar, 70px Dock bottom."""
    return ScreenMetrics(
        screen_x=0,
        screen_y=0,
        screen_width=1512,
        screen_height=982,
        visible_x=0,
        visible_y=25,
        visible_width=1512,
        visible_height=982 - 25 - 70,
        scale=2.0,
        is_primary=True,
    )


def _dock_left() -> ScreenMetrics:
    """External display, Dock on the left, menu bar on top."""
    return ScreenMetrics(
        screen_x=0,
        screen_y=0,
        screen_width=1920,
        screen_height=1080,
        visible_x=80,
        visible_y=25,
        visible_width=1920 - 80,
        visible_height=1080 - 25,
        scale=1.0,
        is_primary=True,
    )


def _tiny() -> ScreenMetrics:
    return ScreenMetrics(
        screen_x=0,
        screen_y=0,
        screen_width=800,
        screen_height=600,
        visible_x=0,
        visible_y=22,
        visible_width=800,
        visible_height=600 - 22 - 40,
        scale=1.0,
        is_primary=True,
    )


def _assert_inside_inset(outer: Rect, screen: ScreenMetrics, *, inset: int = INSET_PX) -> None:
    left = screen.visible_x + inset
    top = screen.visible_y + inset
    right = screen.visible_x + screen.visible_width - inset
    bottom = screen.visible_y + screen.visible_height - inset
    assert outer.x >= left, (outer, left)
    assert outer.y >= top, (outer, top)
    assert outer.x + outer.width <= right, (outer, right)
    assert outer.y + outer.height <= bottom, (outer, bottom)


def test_right_two_thirds_mbp_menu_bar_and_dock() -> None:
    screen = _mbp14()
    outer = right_two_thirds_outer(screen)
    _assert_inside_inset(outer, screen)
    inner_w = screen.visible_width - 2 * INSET_PX
    assert outer.width == round(inner_w * 2 / 3)
    # Right-aligned inside the inset usable frame.
    inset_right = screen.visible_x + screen.visible_width - INSET_PX
    assert outer.x + outer.width == inset_right
    assert outer.y == screen.visible_y + INSET_PX
    assert outer.height == screen.visible_height - 2 * INSET_PX
    # Never y=0 under the menu bar; never raw full-screen.
    assert outer.y > 0
    assert outer.y >= screen.visible_y
    assert outer.width < screen.screen_width
    assert outer.height < screen.screen_height
    # Titlebar + traffic lights sit in the outer rect, below the menu bar.
    assert outer.y >= 25


def test_right_two_thirds_dock_on_left() -> None:
    screen = _dock_left()
    outer = right_two_thirds_outer(screen)
    _assert_inside_inset(outer, screen)
    assert outer.x >= screen.visible_x + INSET_PX
    assert outer.x > 80  # not behind the left Dock
    assert outer.y >= 25 + INSET_PX


def test_right_two_thirds_small_screen() -> None:
    screen = _tiny()
    outer = right_two_thirds_outer(screen)
    _assert_inside_inset(outer, screen)
    assert outer.width >= 1
    assert outer.height >= 1
    assert outer.x + outer.width <= screen.visible_x + screen.visible_width
    assert outer.y + outer.height <= screen.visible_y + screen.visible_height


def test_retina_uses_logical_points_not_backing_pixels() -> None:
    logical = ScreenMetrics(
        screen_x=0,
        screen_y=0,
        screen_width=1512,
        screen_height=982,
        visible_x=0,
        visible_y=25,
        visible_width=1512,
        visible_height=887,
        scale=1.0,
        is_primary=True,
    )
    retina = ScreenMetrics(**{**logical.__dict__, "scale": 2.0})
    a = right_two_thirds_outer(logical)
    b = right_two_thirds_outer(retina)
    assert (a.x, a.y, a.width, a.height) == (b.x, b.y, b.width, b.height)
    assert a.width < 1512
    assert a.width < logical.screen_width * retina.scale


def test_dashboard_maximized_fills_inset_box() -> None:
    """Fallback geometry if fullscreen cannot be applied — still not fill 2/3."""
    screen = _mbp14()
    outer = work_window_plan(role="dashboard", metrics=screen)
    assert outer is not None
    expected = maximized_outer(screen)
    assert outer == expected
    _assert_inside_inset(outer, screen)
    inset_w = screen.visible_width - 2 * INSET_PX
    inset_h = screen.visible_height - 2 * INSET_PX
    assert outer.width == inset_w
    assert outer.height == inset_h
    assert outer.x == screen.visible_x + INSET_PX
    assert outer.y == screen.visible_y + INSET_PX
    fill = work_window_plan(role="fill", metrics=screen)
    assert fill is not None
    assert fill.width < outer.width
    assert fill.x > outer.x


def test_chromium_dashboard_launch_flags_fullscreen_then_maximized() -> None:
    flags = chromium_dashboard_launch_flags()
    assert flags == ["--start-fullscreen", "--start-maximized"]
    assert "--start-kiosk" not in flags


def test_chrome_cdp_fullscreen_state() -> None:
    bounds = chrome_cdp_fullscreen_bounds()
    assert bounds == {"windowState": "fullscreen"}
    fill_cdp = chrome_cdp_bounds(right_two_thirds_outer(_mbp14()))
    assert fill_cdp["windowState"] == "normal"


def test_macos_fullscreen_script_does_not_toggle_if_already() -> None:
    script = macos_fullscreen_applescript(4242)
    assert "unix id is 4242" in script
    assert 'attribute "AXFullScreen"' in script
    assert 'if fs is true then return "already"' in script
    assert "control down, command down" in script
    assert "set size of window" not in script
    fill_place = (
        ROOT / "scripts" / "window_geometry.py"
    ).read_text(encoding="utf-8")
    # Fill apply path still sizes windows; dashboard apply uses fullscreen helper.
    assert "def apply_system_events_bounds" in fill_place
    assert "enter_macos_fullscreen" in fill_place


def test_left_third_does_not_overlap_right_two_thirds() -> None:
    screen = _mbp14()
    right = right_two_thirds_outer(screen)
    left = left_third_outer(screen)
    _assert_inside_inset(left, screen)
    _assert_inside_inset(right, screen)
    assert left.x + left.width <= right.x
    assert left.y == right.y
    assert left.height == right.height
    # Together they fill the inset usable width.
    inset_w = screen.visible_width - 2 * INSET_PX
    assert left.width + right.width == inset_w


def test_chrome_cdp_bounds_subtract_titlebar() -> None:
    """CDP width/height are content size; left/top are outer origin.

    Passing outer height as CDP height makes Chromium grow by ~28px and clip
    under the Dock. Fake metrics prove we compensate; no live Chrome needed.
    """
    screen = _mbp14()
    outer = right_two_thirds_outer(screen)
    cdp = chrome_cdp_bounds(outer)
    assert cdp["windowState"] == "normal"
    assert cdp["left"] == outer.x
    assert cdp["top"] == outer.y
    assert cdp["width"] == outer.width
    assert cdp["height"] == outer.height - TITLEBAR_PX
    assert cdp["height"] >= 1
    # Content + titlebar reconstructs the outer rect (still inside usable).
    reconstructed_bottom = cdp["top"] + TITLEBAR_PX + cdp["height"]
    assert reconstructed_bottom == outer.y + outer.height
    usable_bottom = screen.visible_y + screen.visible_height - INSET_PX
    assert reconstructed_bottom <= usable_bottom
    # Must not send y=0 / full screen.
    assert cdp["top"] != 0
    assert cdp["width"] != screen.screen_width
    assert cdp["height"] != screen.screen_height


def test_chromium_window_args_use_content_size() -> None:
    screen = _mbp14()
    outer = right_two_thirds_outer(screen)
    args = chromium_window_args(outer)
    assert f"--window-position={outer.x},{outer.y}" in args
    content_h = outer.height - TITLEBAR_PX
    assert f"--window-size={outer.width},{content_h}" in args


def test_playwright_viewport_matches_content() -> None:
    screen = _mbp14()
    outer = right_two_thirds_outer(screen)
    vp = playwright_viewport(outer)
    assert vp == {"width": outer.width, "height": outer.height - TITLEBAR_PX}


def test_system_events_size_is_outer() -> None:
    """System Events position/size include the titlebar — do not subtract."""
    outer = Rect(x=100, y=40, width=900, height=800)
    w, h = system_events_size(outer)
    assert (w, h) == (900, 800)


def test_pick_screen_prefers_dashboard_display() -> None:
    primary = _mbp14()
    other = ScreenMetrics(
        screen_x=1512,
        screen_y=0,
        screen_width=1920,
        screen_height=1080,
        visible_x=1512,
        visible_y=25,
        visible_width=1920,
        visible_height=1055,
        scale=1.0,
        is_primary=False,
    )
    dash = Rect(x=1600, y=100, width=400, height=700)
    picked = pick_screen([primary, other], anchor=dash)
    assert picked.screen_x == 1512
    fallback = pick_screen([primary, other], anchor=None)
    assert fallback.is_primary is True


def test_pick_screen_primary_when_anchor_missing() -> None:
    extra = ScreenMetrics(
        screen_x=2000,
        screen_y=0,
        screen_width=800,
        screen_height=600,
        visible_x=2000,
        visible_y=25,
        visible_width=800,
        visible_height=575,
        scale=1.0,
        is_primary=False,
    )
    picked = pick_screen([extra, _mbp14()], anchor=None)
    assert picked.is_primary is True


def test_guard_usable_frame_rejects_fullscreen_y0() -> None:
    raw = ScreenMetrics(
        screen_x=0,
        screen_y=0,
        screen_width=1440,
        screen_height=900,
        visible_x=0,
        visible_y=0,
        visible_width=1440,
        visible_height=900,
        scale=2.0,
        is_primary=True,
    )
    guarded = _guard_usable_frame(raw)
    if sys.platform == "darwin":
        assert guarded.visible_y >= MENUBAR_FALLBACK_PX
        assert guarded.visible_y + guarded.visible_height <= raw.screen_height - DOCK_FALLBACK_PX
        outer = work_window_plan(role="fill", metrics=raw)
        assert outer is not None
        assert outer.y >= MENUBAR_FALLBACK_PX
        assert outer.y != 0
        assert outer.y + outer.height <= 900 - INSET_PX
    else:
        assert guarded.visible_y == 0


def test_place_cdp_window_sends_compensated_bounds() -> None:
    from window_geometry import place_cdp_window

    calls: list[tuple[str, dict]] = []

    def fake_cdp(method: str, params: dict | None = None) -> dict:
        calls.append((method, params or {}))
        if method == "Browser.getWindowForTarget":
            return {"windowId": 7, "bounds": {"left": 0, "top": 0, "width": 800, "height": 600}}
        return {}

    outer = right_two_thirds_outer(_mbp14())
    place_cdp_window(fake_cdp, outer=outer, target_id="TID")
    assert calls[0][0] == "Browser.getWindowForTarget"
    assert calls[0][1].get("targetId") == "TID"
    assert calls[1][0] == "Browser.setWindowBounds"
    bounds = calls[1][1]["bounds"]
    assert calls[1][1]["windowId"] == 7
    assert bounds == chrome_cdp_bounds(outer)


def test_wiring_partyrock_places_on_create_and_open() -> None:
    src = (ROOT / "scripts" / "partyrock_tabs.py").read_text(encoding="utf-8")
    assert "place_partyrock_window" in src
    assert "from window_geometry import" in src or "import window_geometry" in src


def test_wiring_browser_launch_headed_uses_helper() -> None:
    src = (ROOT / "scripts" / "fastfill" / "browser_launch.py").read_text(encoding="utf-8")
    assert "window_geometry" in src
    assert "right_two_thirds" in src or "work_window_plan" in src or "headed_window_plan" in src


def test_wiring_fast_fill_places_after_launch() -> None:
    src = (ROOT / "scripts" / "fastfill" / "fast_fill.py").read_text(encoding="utf-8")
    assert "place_playwright_window" in src or "place_headed_fill_window" in src


def test_enter_macos_fullscreen_accepts_ax_token() -> None:
    from unittest.mock import patch

    from window_geometry import enter_macos_fullscreen

    class _Proc:
        returncode = 0
        stdout = "ax\n"
        stderr = ""

    with patch("window_geometry.sys.platform", "darwin"):
        with patch("window_geometry.subprocess.run", return_value=_Proc()) as run:
            assert enter_macos_fullscreen(99) is True
            argv = run.call_args[0][0]
            assert argv[:2] == ["/usr/bin/osascript", "-e"]
            assert 'attribute "AXFullScreen"' in argv[2]


def test_fill_wiring_avoids_dashboard_fullscreen() -> None:
    launch = (ROOT / "scripts" / "fastfill" / "browser_launch.py").read_text(encoding="utf-8")
    assert "start-fullscreen" not in launch
    assert 'role="fill"' in launch or "role='fill'" in launch
    cft = (ROOT / "scripts" / "chrome_for_testing.py").read_text(encoding="utf-8")
    assert "start-fullscreen" not in cft
    party = (ROOT / "scripts" / "partyrock_tabs.py").read_text(encoding="utf-8")
    assert 'role="fill"' in party or "role='fill'" in party


def test_wiring_launch_dashboard_fullscreen() -> None:
    src = (ROOT / "dashboard" / "launch_dashboard.sh").read_text(encoding="utf-8")
    assert "window_geometry" in src
    assert "--role dashboard" in src
    assert "--start-fullscreen" in src
    assert "--start-maximized" in src
    open_chunk = src.split("open_dashboard_ui() {", 1)[1].split("\n}\n", 1)[0]
    assert "--start-fullscreen" in open_chunk
    assert "--start-maximized" in open_chunk
    assert "--kiosk" not in open_chunk


def test_wiring_chrome_for_testing_window_args_when_url() -> None:
    src = (ROOT / "scripts" / "chrome_for_testing.py").read_text(encoding="utf-8")
    assert "window_geometry" in src or "chromium_window_args" in src


if __name__ == "__main__":
    test_right_two_thirds_mbp_menu_bar_and_dock()
    test_right_two_thirds_dock_on_left()
    test_right_two_thirds_small_screen()
    test_retina_uses_logical_points_not_backing_pixels()
    test_dashboard_maximized_fills_inset_box()
    test_chromium_dashboard_launch_flags_fullscreen_then_maximized()
    test_chrome_cdp_fullscreen_state()
    test_macos_fullscreen_script_does_not_toggle_if_already()
    test_left_third_does_not_overlap_right_two_thirds()
    test_chrome_cdp_bounds_subtract_titlebar()
    test_chromium_window_args_use_content_size()
    test_playwright_viewport_matches_content()
    test_system_events_size_is_outer()
    test_pick_screen_prefers_dashboard_display()
    test_pick_screen_primary_when_anchor_missing()
    test_guard_usable_frame_rejects_fullscreen_y0()
    test_place_cdp_window_sends_compensated_bounds()
    test_wiring_partyrock_places_on_create_and_open()
    test_wiring_browser_launch_headed_uses_helper()
    test_wiring_fast_fill_places_after_launch()
    test_enter_macos_fullscreen_accepts_ax_token()
    test_fill_wiring_avoids_dashboard_fullscreen()
    test_wiring_launch_dashboard_fullscreen()
    test_wiring_chrome_for_testing_window_args_when_url()
    print("OK test_window_geometry")
