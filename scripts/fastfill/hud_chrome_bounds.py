"""Lightweight macOS Chrome window bounds for the native fill HUD.

Uses Quartz ``CGWindowListCopyWindowInfo`` via ctypes (no per-tick osascript).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
from typing import Any

# CGWindowList option flags
_KCG_WINDOW_LIST_ON_SCREEN = 1
_KCG_WINDOW_LIST_EXCLUDE_DESKTOP = 1 << 4
_KCF_STRING_ENCODING_UTF8 = 0x08000100
_KCF_NUMBER_INT_TYPE = 9
_KCF_NUMBER_DOUBLE_TYPE = 13

_CORE_FOUNDATION: ctypes.CDLL | None = None
_APP_SERVICES: ctypes.CDLL | None = None


def _core_foundation() -> ctypes.CDLL:
    global _CORE_FOUNDATION
    if _CORE_FOUNDATION is None:
        path = ctypes.util.find_library("CoreFoundation")
        if not path:
            path = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        _CORE_FOUNDATION = ctypes.CDLL(path)
    return _CORE_FOUNDATION


def _app_services() -> ctypes.CDLL:
    global _APP_SERVICES
    if _APP_SERVICES is None:
        path = ctypes.util.find_library("ApplicationServices")
        if not path:
            path = (
                "/System/Library/Frameworks/ApplicationServices.framework/"
                "ApplicationServices"
            )
        _APP_SERVICES = ctypes.CDLL(path)
    return _APP_SERVICES


def _cfstring(key: str) -> Any:
    cf = _core_foundation()
    fn = cf.CFStringCreateWithCString
    fn.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    fn.restype = ctypes.c_void_p
    return fn(None, key.encode("utf-8"), _KCF_STRING_ENCODING_UTF8)


def _cf_release(ref: Any) -> None:
    if not ref:
        return
    cf = _core_foundation()
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease(ref)


def _cf_dict_get(d: Any, key: str) -> Any:
    cf = _core_foundation()
    fn = cf.CFDictionaryGetValue
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    fn.restype = ctypes.c_void_p
    k = _cfstring(key)
    try:
        return fn(d, k)
    finally:
        _cf_release(k)


def _cf_number_int(ref: Any) -> int | None:
    if not ref:
        return None
    cf = _core_foundation()
    fn = cf.CFNumberGetValue
    fn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    fn.restype = ctypes.c_bool
    out = ctypes.c_int()
    if fn(ref, _KCF_NUMBER_INT_TYPE, ctypes.byref(out)):
        return int(out.value)
    return None


def _cf_number_double(ref: Any) -> float | None:
    if not ref:
        return None
    cf = _core_foundation()
    fn = cf.CFNumberGetValue
    fn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    fn.restype = ctypes.c_bool
    out = ctypes.c_double()
    if fn(ref, _KCF_NUMBER_DOUBLE_TYPE, ctypes.byref(out)):
        return float(out.value)
    return None


def _cf_dict_numbers(d: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in ("X", "Y", "Width", "Height"):
        val = _cf_dict_get(d, key)
        num = _cf_number_double(val)
        if num is not None:
            out[key] = num
    return out


def pin_chrome_enabled() -> bool:
    """Default ON for headed fills; ``FASTFILL_HUD_PIN_CHROME=0`` → screen corner."""
    raw = (os.environ.get("FASTFILL_HUD_PIN_CHROME") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def chrome_window_bounds(pid: int) -> dict[str, int] | None:
    """Return ``{x, y, width, height}`` for the largest on-screen window of ``pid``."""
    if sys.platform != "darwin" or pid <= 0:
        return None
    try:
        app = _app_services()
        fn = app.CGWindowListCopyWindowInfo
        fn.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        fn.restype = ctypes.c_void_p
        opts = _KCG_WINDOW_LIST_ON_SCREEN | _KCG_WINDOW_LIST_EXCLUDE_DESKTOP
        window_list = fn(opts, 0)
        if not window_list:
            return None
        cf = _core_foundation()
        count_fn = cf.CFArrayGetCount
        count_fn.argtypes = [ctypes.c_void_p]
        count_fn.restype = ctypes.c_long
        at_fn = cf.CFArrayGetValueAtIndex
        at_fn.argtypes = [ctypes.c_void_p, ctypes.c_long]
        at_fn.restype = ctypes.c_void_p
        best: dict[str, int] | None = None
        best_area = 0
        try:
            n = int(count_fn(window_list))
            for i in range(n):
                info = at_fn(window_list, i)
                if not info:
                    continue
                owner = _cf_number_int(_cf_dict_get(info, "kCGWindowOwnerPID"))
                if owner != int(pid):
                    continue
                layer = _cf_number_int(_cf_dict_get(info, "kCGWindowLayer"))
                if layer is not None and layer != 0:
                    continue
                bounds_ref = _cf_dict_get(info, "kCGWindowBounds")
                if not bounds_ref:
                    continue
                nums = _cf_dict_numbers(bounds_ref)
                w = int(round(nums.get("Width", 0)))
                h = int(round(nums.get("Height", 0)))
                if w < 200 or h < 120:
                    continue
                area = w * h
                if area > best_area:
                    best_area = area
                    best = {
                        "x": int(round(nums.get("X", 0))),
                        "y": int(round(nums.get("Y", 0))),
                        "width": w,
                        "height": h,
                    }
        finally:
            _cf_release(window_list)
        return best
    except Exception:
        return None


def default_hud_margins() -> tuple[int, int]:
    """Default top-right inset inside Chrome (margin_right, margin_top)."""
    return (12, 12)


def compute_hud_xy(
    chrome: dict[str, int],
    *,
    hud_width: int,
    hud_height: int,
    margin_right: int,
    margin_top: int,
) -> tuple[int, int]:
    """Place HUD top-left using margins from Chrome top-right."""
    x = chrome["x"] + chrome["width"] - hud_width - margin_right
    y = chrome["y"] + margin_top
    return (max(chrome["x"], x), max(chrome["y"], y))


def margins_from_hud_xy(
    chrome: dict[str, int],
    *,
    hud_x: int,
    hud_y: int,
    hud_width: int,
    hud_height: int,
) -> tuple[int, int]:
    """Derive top-right margins from absolute HUD position."""
    margin_right = chrome["x"] + chrome["width"] - (hud_x + hud_width)
    margin_top = hud_y - chrome["y"]
    return (max(0, int(margin_right)), max(0, int(margin_top)))


class BoundsCache:
    """Skip reposition when Chrome bounds and HUD margins are unchanged."""

    __slots__ = ("_last_bounds", "_last_margins", "_last_size")

    def __init__(self) -> None:
        self._last_bounds: tuple[int, int, int, int] | None = None
        self._last_margins: tuple[int, int] | None = None
        self._last_size: tuple[int, int] | None = None

    def should_reposition(
        self,
        chrome: dict[str, int],
        *,
        hud_width: int,
        hud_height: int,
        margin_right: int,
        margin_top: int,
    ) -> bool:
        bounds_key = (
            chrome["x"],
            chrome["y"],
            chrome["width"],
            chrome["height"],
        )
        margins_key = (margin_right, margin_top)
        size_key = (hud_width, hud_height)
        if (
            self._last_bounds == bounds_key
            and self._last_margins == margins_key
            and self._last_size == size_key
        ):
            return False
        self._last_bounds = bounds_key
        self._last_margins = margins_key
        self._last_size = size_key
        return True

    def clear(self) -> None:
        self._last_bounds = None
        self._last_margins = None
        self._last_size = None
