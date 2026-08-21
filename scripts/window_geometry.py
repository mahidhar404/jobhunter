"""Shared fill/PartyRock window placement: right two-thirds of the usable screen.

Usable frame = screen minus menu bar and Dock (never raw ``[0,0,W,H]`` or ``y=0``
under the menu bar). The outer window — titlebar (~28px), traffic lights, and
shadows — is inset a few pixels inside that frame, then sized to ~2/3 of the
inset width and right-aligned at full inset height.

Dashboard launches in true fullscreen (Chromium ``--start-fullscreen`` plus
macOS AXFullScreen). Maximized-to-usable-frame is the fallback if fullscreen
cannot be applied. Fill/PartyRock still use the right ~2/3 on their own
Chrome windows (separate processes).

Coordinate space is logical points (top-left origin), not retina backing pixels.

Chrome CDP / ``--window-size`` treat width×height as the *content* rect (excluding
the titlebar) while left/top is the *outer* origin. Passing outer height as CDP
height makes Chromium grow ~28px and clip under the Dock. ``chrome_cdp_bounds``
subtracts ``TITLEBAR_PX``. System Events ``size`` is outer (no subtract).
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, replace
from typing import Any, Callable

INSET_PX = 8
TITLEBAR_PX = 28
WIDTH_FRACTION = 2 / 3
# When NSScreen.visibleFrame matches the full display (no menu bar / Dock in
# the probe — common in agent/CI sessions), still keep chrome on-screen.
MENUBAR_FALLBACK_PX = 25
DOCK_FALLBACK_PX = 64

CdpCall = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class ScreenMetrics:
    screen_x: int
    screen_y: int
    screen_width: int
    screen_height: int
    visible_x: int
    visible_y: int
    visible_width: int
    visible_height: int
    scale: float = 1.0
    is_primary: bool = False
    is_main: bool = False


def _inset_box(screen: ScreenMetrics, inset: int = INSET_PX) -> Rect:
    max_ix = max(0, (screen.visible_width - 1) // 2)
    max_iy = max(0, (screen.visible_height - 1) // 2)
    ix = min(max(0, inset), max_ix)
    iy = min(max(0, inset), max_iy)
    return Rect(
        x=screen.visible_x + ix,
        y=screen.visible_y + iy,
        width=max(1, screen.visible_width - 2 * ix),
        height=max(1, screen.visible_height - 2 * iy),
    )


def right_two_thirds_outer(screen: ScreenMetrics, *, inset: int = INSET_PX) -> Rect:
    """Outer window: ~2/3 of inset usable width, right-aligned, full inset height."""
    box = _inset_box(screen, inset)
    win_w = max(1, int(round(box.width * WIDTH_FRACTION)))
    if win_w > box.width:
        win_w = box.width
    return Rect(
        x=box.x + box.width - win_w,
        y=box.y,
        width=win_w,
        height=box.height,
    )


def left_third_outer(screen: ScreenMetrics, *, inset: int = INSET_PX) -> Rect:
    """Outer window: remainder of the inset usable frame (legacy left ~1/3 layout)."""
    box = _inset_box(screen, inset)
    right = right_two_thirds_outer(screen, inset=inset)
    return Rect(
        x=box.x,
        y=box.y,
        width=max(1, right.x - box.x),
        height=box.height,
    )


def maximized_outer(screen: ScreenMetrics, *, inset: int = INSET_PX) -> Rect:
    """Outer window: full inset usable frame (dashboard fallback if fullscreen fails)."""
    return _inset_box(screen, inset)


def chromium_dashboard_launch_flags() -> list[str]:
    """Chromium argv for ops dashboard: true fullscreen, maximized as fallback."""
    return ["--start-fullscreen", "--start-maximized"]


def chrome_cdp_fullscreen_bounds() -> dict[str, Any]:
    """CDP ``Browser.setWindowBounds`` payload for OS fullscreen (not kiosk)."""
    return {"windowState": "fullscreen"}


def macos_fullscreen_applescript(pid: int) -> str:
    """System Events: enter macOS Space fullscreen (green-button), not maximize.

    Reads AXFullScreen first so Cmd+Ctrl+F is not toggled if already fullscreen.
    """
    return (
        f'tell application "System Events"\n'
        f"  tell (first process whose unix id is {int(pid)})\n"
        f"    if (count of windows) is 0 then return \"no_window\"\n"
        f"    set frontmost to true\n"
        f"    delay 0.12\n"
        f"    set w to window 1\n"
        f"    try\n"
        f"      set fs to value of attribute \"AXFullScreen\" of w\n"
        f"      if fs is true then return \"already\"\n"
        f"      set value of attribute \"AXFullScreen\" of w to true\n"
        f"      return \"ax\"\n"
        f"    end try\n"
        f"  end tell\n"
        f"  keystroke \"f\" using {{control down, command down}}\n"
        f"  return \"key\"\n"
        f"end tell"
    )


def enter_macos_fullscreen(pid: int) -> bool:
    """Best-effort true fullscreen for window 1 of *pid* (macOS only)."""
    if sys.platform != "darwin" or pid <= 0:
        return False
    script = macos_fullscreen_applescript(pid)
    try:
        proc = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=6,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    token = (proc.stdout or "").strip().splitlines()[-1].strip().lower() if (proc.stdout or "").strip() else ""
    return token in {"ax", "already", "key", "true"}


def _content_height(outer: Rect, *, titlebar_px: int = TITLEBAR_PX) -> int:
    return max(1, outer.height - max(0, titlebar_px))


def chrome_cdp_bounds(outer: Rect, *, titlebar_px: int = TITLEBAR_PX) -> dict[str, Any]:
    """CDP ``Browser.setWindowBounds`` payload (content size, outer origin)."""
    return {
        "left": outer.x,
        "top": outer.y,
        "width": outer.width,
        "height": _content_height(outer, titlebar_px=titlebar_px),
        "windowState": "normal",
    }


def chromium_window_args(outer: Rect, *, titlebar_px: int = TITLEBAR_PX) -> list[str]:
    """``--window-position`` / ``--window-size`` (size is content, not outer)."""
    h = _content_height(outer, titlebar_px=titlebar_px)
    return [
        f"--window-position={outer.x},{outer.y}",
        f"--window-size={outer.width},{h}",
    ]


def playwright_viewport(outer: Rect, *, titlebar_px: int = TITLEBAR_PX) -> dict[str, int]:
    return {"width": outer.width, "height": _content_height(outer, titlebar_px=titlebar_px)}


def system_events_size(outer: Rect) -> tuple[int, int]:
    """System Events window size includes the titlebar — use outer dims."""
    return (outer.width, outer.height)


def pick_screen(
    screens: list[ScreenMetrics],
    *,
    anchor: Rect | None = None,
) -> ScreenMetrics:
    if not screens:
        raise ValueError("pick_screen requires at least one screen")
    if anchor is not None:
        cx = anchor.x + max(anchor.width, 1) // 2
        cy = anchor.y + max(anchor.height, 1) // 2
        for s in screens:
            if (
                s.screen_x <= cx < s.screen_x + s.screen_width
                and s.screen_y <= cy < s.screen_y + s.screen_height
            ):
                return s
    for s in screens:
        if s.is_main:
            return s
    for s in screens:
        if s.is_primary:
            return s
    return screens[0]


_JXA_SCREENS = r"""
ObjC.import('AppKit');
function qrect(nsRect, primaryHeight) {
  var x = nsRect.origin.x;
  var y = nsRect.origin.y;
  var w = nsRect.size.width;
  var h = nsRect.size.height;
  return {
    x: Math.round(x),
    y: Math.round(primaryHeight - (y + h)),
    width: Math.round(w),
    height: Math.round(h)
  };
}
var screens = $.NSScreen.screens;
if (!screens || screens.count === 0) {
  JSON.stringify([]);
} else {
  var primary = screens.objectAtIndex(0);
  var main = $.NSScreen.mainScreen;
  var pH = primary.frame.size.height;
  var out = [];
  for (var i = 0; i < screens.count; i++) {
    var s = screens.objectAtIndex(i);
    var f = qrect(s.frame, pH);
    var v = qrect(s.visibleFrame, pH);
    out.push({
      screen_x: f.x,
      screen_y: f.y,
      screen_width: f.width,
      screen_height: f.height,
      visible_x: v.x,
      visible_y: v.y,
      visible_width: v.width,
      visible_height: v.height,
      scale: Number(s.backingScaleFactor),
      is_primary: i === 0,
      is_main: !!main && s.isEqual(main)
    });
  }
  JSON.stringify(out);
}
"""


def probe_screens() -> list[ScreenMetrics]:
    """Live NSScreen metrics via JXA. Empty on non-Darwin / failure (no GUI needed in tests)."""
    if sys.platform != "darwin":
        return []
    try:
        proc = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript"],
            input=_JXA_SCREENS,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    raw = (proc.stdout or "").strip()
    if proc.returncode != 0 or not raw:
        return []
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out: list[ScreenMetrics] = []
    if not isinstance(rows, list):
        return []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            out.append(
                ScreenMetrics(
                    screen_x=int(row["screen_x"]),
                    screen_y=int(row["screen_y"]),
                    screen_width=int(row["screen_width"]),
                    screen_height=int(row["screen_height"]),
                    visible_x=int(row["visible_x"]),
                    visible_y=int(row["visible_y"]),
                    visible_width=int(row["visible_width"]),
                    visible_height=int(row["visible_height"]),
                    scale=float(row.get("scale") or 1.0),
                    is_primary=bool(row.get("is_primary")),
                    is_main=bool(row.get("is_main")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def process_window_rect(pid: int) -> Rect | None:
    """Outer bounds of window 1 for *pid* via System Events (best-effort)."""
    if sys.platform != "darwin" or pid <= 0:
        return None
    script = (
        f'tell application "System Events"\n'
        f"  tell (first process whose unix id is {int(pid)})\n"
        f"    if (count of windows) is 0 then return \"\"\n"
        f"    set p to position of window 1\n"
        f"    set s to size of window 1\n"
        f"    return (item 1 of p as text) & \",\" & (item 2 of p as text)"
        f" & \",\" & (item 1 of s as text) & \",\" & (item 2 of s as text)\n"
        f"  end tell\n"
        f"end tell"
    )
    try:
        proc = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = (proc.stdout or "").strip()
    if proc.returncode != 0 or not raw:
        return None
    parts = raw.split(",")
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = (int(float(p.strip())) for p in parts)
    except ValueError:
        return None
    if w < 1 or h < 1:
        return None
    return Rect(x=x, y=y, width=w, height=h)


def resolve_screen(
    *,
    metrics: ScreenMetrics | None = None,
    screens: list[ScreenMetrics] | None = None,
    anchor: Rect | None = None,
    pid: int | None = None,
) -> ScreenMetrics | None:
    if metrics is not None:
        return metrics
    found = list(screens) if screens is not None else probe_screens()
    if not found:
        return None
    use_anchor = anchor
    if use_anchor is None and pid:
        use_anchor = process_window_rect(int(pid))
    return pick_screen(found, anchor=use_anchor)


def _guard_usable_frame(screen: ScreenMetrics) -> ScreenMetrics:
    """Never treat y=0 / full-height as usable on macOS (menu bar + Dock)."""
    if sys.platform != "darwin":
        return screen
    vx, vy, vw, vh = (
        screen.visible_x,
        screen.visible_y,
        screen.visible_width,
        screen.visible_height,
    )
    if vy <= screen.screen_y:
        vy = screen.screen_y + MENUBAR_FALLBACK_PX
        vh = max(1, screen.visible_y + screen.visible_height - vy)
    bottom = vy + vh
    screen_bottom = screen.screen_y + screen.screen_height
    if bottom >= screen_bottom:
        side_dock = vx > screen.screen_x or (vx + vw) < (
            screen.screen_x + screen.screen_width
        )
        if not side_dock:
            vh = max(1, vh - DOCK_FALLBACK_PX)
    if (
        vx == screen.visible_x
        and vy == screen.visible_y
        and vw == screen.visible_width
        and vh == screen.visible_height
    ):
        return screen
    return replace(
        screen,
        visible_x=vx,
        visible_y=vy,
        visible_width=vw,
        visible_height=vh,
    )


def work_window_plan(
    *,
    role: str = "fill",
    metrics: ScreenMetrics | None = None,
    screens: list[ScreenMetrics] | None = None,
    anchor: Rect | None = None,
    pid: int | None = None,
) -> Rect | None:
    """Return outer bounds for fill/PartyRock (``fill``) or dashboard fallback.

    Dashboard *placement* prefers ``enter_macos_fullscreen``; this rect is the
    maximized usable-frame fallback only (fill still right ~2/3).
    """
    screen = resolve_screen(metrics=metrics, screens=screens, anchor=anchor, pid=pid)
    if screen is None:
        return None
    screen = _guard_usable_frame(screen)
    if role == "dashboard":
        return maximized_outer(screen)
    return right_two_thirds_outer(screen)


def place_cdp_window(
    cdp_call: CdpCall,
    *,
    outer: Rect,
    target_id: str | None = None,
) -> dict[str, Any]:
    """Apply outer rect via CDP. *cdp_call(method, params) -> result dict*."""
    get_params: dict[str, Any] = {}
    if target_id:
        get_params["targetId"] = target_id
    info = cdp_call("Browser.getWindowForTarget", get_params) or {}
    window_id = info.get("windowId")
    if window_id is None:
        raise RuntimeError("Browser.getWindowForTarget missing windowId")
    bounds = chrome_cdp_bounds(outer)
    cdp_call(
        "Browser.setWindowBounds",
        {"windowId": window_id, "bounds": bounds},
    )
    return {"windowId": window_id, "bounds": bounds, "outer": outer}


def apply_system_events_bounds(pid: int, outer: Rect) -> bool:
    """Place window 1 of *pid* using outer System Events size (macOS)."""
    if sys.platform != "darwin" or pid <= 0:
        return False
    w, h = system_events_size(outer)
    script = (
        f'tell application "System Events"\n'
        f"  tell (first process whose unix id is {int(pid)})\n"
        f"    if (count of windows) is 0 then return false\n"
        f"    set position of window 1 to {{{int(outer.x)}, {int(outer.y)}}}\n"
        f"    set size of window 1 to {{{int(w)}, {int(h)}}}\n"
        f"    return true\n"
        f"  end tell\n"
        f"end tell"
    )
    try:
        proc = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and "true" in (proc.stdout or "").lower()


async def place_playwright_window(page: Any, *, outer: Rect | None = None) -> dict[str, Any] | None:
    """Headed Playwright: CDP-set the fill window to the right two-thirds plan."""
    plan = outer or work_window_plan(role="fill")
    if plan is None or page is None:
        return None
    cdp = await page.context.new_cdp_session(page)
    info = await cdp.send("Browser.getWindowForTarget")
    window_id = (info or {}).get("windowId")
    if window_id is None:
        return None
    bounds = chrome_cdp_bounds(plan)
    await cdp.send(
        "Browser.setWindowBounds",
        {"windowId": window_id, "bounds": bounds},
    )
    return {"windowId": window_id, "bounds": bounds, "outer": plan}


def _rect_json(rect: Rect) -> dict[str, int]:
    return {"x": rect.x, "y": rect.y, "width": rect.width, "height": rect.height}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Place fill/PartyRock windows; dashboard uses macOS fullscreen"
    )
    parser.add_argument("--role", choices=("fill", "partyrock", "dashboard"), default="fill")
    parser.add_argument("--apply-pid", type=int, default=0, help="System Events place window 1")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    role = "dashboard" if args.role == "dashboard" else "fill"
    pid = int(args.apply_pid or 0) or None
    outer = work_window_plan(role=role, pid=pid)
    if outer is None:
        if args.json:
            print(json.dumps({"ok": False, "error": "no_screen_metrics"}))
        return 1
    applied = False
    fullscreen = False
    if pid:
        if role == "dashboard":
            fullscreen = enter_macos_fullscreen(pid)
            applied = fullscreen
            if not applied:
                applied = apply_system_events_bounds(pid, outer)
        else:
            applied = apply_system_events_bounds(pid, outer)
    payload = {
        "ok": True,
        "role": role,
        "outer": _rect_json(outer),
        "cdp": chrome_cdp_bounds(outer) if role != "dashboard" else chrome_cdp_fullscreen_bounds(),
        "applied": applied,
        "fullscreen": fullscreen,
        "pid": pid,
    }
    if args.json or not pid:
        print(json.dumps(payload))
    return 0 if (not pid or applied) else 2


if __name__ == "__main__":
    raise SystemExit(main())
