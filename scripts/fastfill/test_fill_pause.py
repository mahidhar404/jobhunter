#!/usr/bin/env python3
"""Unit tests: fill_pause resolve + sentinels + mock page pause/resume (no browser)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fill_pause import (  # noqa: E402
    ACTIVITY_GLOBAL,
    CAPTCHA_GATE_GLOBAL,
    OVERLAY_ID,
    SYM_PAUSE,
    SYM_PLAY,
    FillPausedAbort,
    _NATIVE_STATE,
    abort_if_paused,
    append_fill_log,
    consume_fill_continue_sentinel,
    drain_pause_before_close,
    fill_pause_continue_sentinel_path,
    fill_pause_control_path,
    fill_pause_force_sentinel_path,
    force_pause_sentinel_present,
    format_fill_activity_compact,
    format_fill_activity_text,
    inject_fill_pause_overlay,
    is_fill_paused_now,
    may_auto_close_fill_browser,
    note_fill_activity,
    request_fill_pause,
    reset_native_pause_state,
    resolve_fill_pause,
    run_cancellable,
    sanitize_fill_log_line,
    set_fill_pause_captcha_gate,
    set_fill_paused,
    should_keep_fill_browser_open,
    use_dom_overlay,
    use_native_hud,
    wait_while_paused,
)


def test_resolve_fill_pause_defaults():
    prev = os.environ.pop("FASTFILL_FILL_PAUSE", None)
    try:
        assert resolve_fill_pause(headed=True, fill_pause=None) is True
        assert resolve_fill_pause(headed=False, fill_pause=None) is False
        assert resolve_fill_pause(headed=True, fill_pause=False) is False
        assert resolve_fill_pause(headed=False, fill_pause=True) is True
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_FILL_PAUSE", None)
        else:
            os.environ["FASTFILL_FILL_PAUSE"] = prev


def test_resolve_fill_pause_env():
    prev = os.environ.get("FASTFILL_FILL_PAUSE")
    try:
        os.environ["FASTFILL_FILL_PAUSE"] = "0"
        assert resolve_fill_pause(headed=True, fill_pause=None) is False
        os.environ["FASTFILL_FILL_PAUSE"] = "1"
        assert resolve_fill_pause(headed=False, fill_pause=None) is True
        # Explicit flag wins over env
        assert resolve_fill_pause(headed=True, fill_pause=False) is False
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_FILL_PAUSE", None)
        else:
            os.environ["FASTFILL_FILL_PAUSE"] = prev


def test_sentinel_paths_default():
    prev_c = os.environ.pop("FASTFILL_FILL_CONTINUE_FILE", None)
    prev_p = os.environ.pop("FASTFILL_FILL_PAUSE_FILE", None)
    try:
        c = fill_pause_continue_sentinel_path()
        p = fill_pause_force_sentinel_path()
        assert c.name == ".fill_continue"
        assert p.name == ".fill_paused"
        assert "real_job_results" in str(c)
    finally:
        if prev_c is None:
            os.environ.pop("FASTFILL_FILL_CONTINUE_FILE", None)
        else:
            os.environ["FASTFILL_FILL_CONTINUE_FILE"] = prev_c
        if prev_p is None:
            os.environ.pop("FASTFILL_FILL_PAUSE_FILE", None)
        else:
            os.environ["FASTFILL_FILL_PAUSE_FILE"] = prev_p


def test_consume_continue_sentinel():
    with tempfile.TemporaryDirectory() as td:
        sentinel = Path(td) / ".fill_continue"
        prev = os.environ.get("FASTFILL_FILL_CONTINUE_FILE")
        os.environ["FASTFILL_FILL_CONTINUE_FILE"] = str(sentinel)
        try:
            assert consume_fill_continue_sentinel() is False
            sentinel.write_text("")
            assert consume_fill_continue_sentinel() is True
            assert not sentinel.exists()
            assert consume_fill_continue_sentinel() is False
        finally:
            if prev is None:
                os.environ.pop("FASTFILL_FILL_CONTINUE_FILE", None)
            else:
                os.environ["FASTFILL_FILL_CONTINUE_FILE"] = prev


def test_force_pause_sentinel_present():
    with tempfile.TemporaryDirectory() as td:
        sentinel = Path(td) / ".fill_paused"
        prev = os.environ.get("FASTFILL_FILL_PAUSE_FILE")
        os.environ["FASTFILL_FILL_PAUSE_FILE"] = str(sentinel)
        try:
            assert force_pause_sentinel_present() is False
            sentinel.write_text("")
            assert force_pause_sentinel_present() is True
        finally:
            if prev is None:
                os.environ.pop("FASTFILL_FILL_PAUSE_FILE", None)
            else:
                os.environ["FASTFILL_FILL_PAUSE_FILE"] = prev


class _pause_files:
    """Isolate pause IPC so tests cannot hang on a leftover `.fill_paused`."""

    def __init__(self, td: str):
        self.td = Path(td)
        self.prev = {}

    def __enter__(self):
        keys = (
            "FASTFILL_FILL_PAUSE_STATE",
            "FASTFILL_FILL_PAUSE_FILE",
            "FASTFILL_FILL_CONTINUE_FILE",
            "FASTFILL_NATIVE_HUD",
        )
        for key in keys:
            self.prev[key] = os.environ.get(key)
        os.environ["FASTFILL_FILL_PAUSE_STATE"] = str(self.td / ".fill_pause_state.json")
        os.environ["FASTFILL_FILL_PAUSE_FILE"] = str(self.td / ".fill_paused")
        os.environ["FASTFILL_FILL_CONTINUE_FILE"] = str(self.td / ".fill_continue")
        os.environ["FASTFILL_NATIVE_HUD"] = "0"
        reset_native_pause_state()
        return self

    def __exit__(self, *exc):
        for key, val in self.prev.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        return False


class _FakePage:
    """Minimal page mock: evaluate stores paused state + overlay install."""

    def __init__(self, *, fail_read: bool = False):
        self._state = {
            "paused": False,
            "holdMode": False,
            "pauseCount": 0,
            "continueCount": 0,
            "installed": False,
            "captcha_gated": False,
            "activity": None,
        }
        self.evaluate_calls = 0
        self.fail_read = fail_read

    async def evaluate(self, js, *args):
        self.evaluate_calls += 1
        s = str(js)
        # Push activity (window.__jhFillActivity)
        if "__jhFillActivity" in s and "Object.assign" in s:
            payload = args[0] if args else {}
            self._state["activity"] = payload if isinstance(payload, dict) else {}
            return {"ok": True, "text": (self._state["activity"] or {}).get("text") or ""}
        # Install overlay (FILL3-017 throttle target)
        if "__jhFillPauseObserver" in s or "data-jh-fill-pause" in s:
            self._state["installed"] = True
            return {
                "ok": True,
                "paused": self._state["paused"],
                "holdMode": self._state["holdMode"],
                "pauseCount": self._state["pauseCount"],
                "continueCount": self._state["continueCount"],
                "captcha_gated": self._state["captcha_gated"],
            }
        # CAPTCHA gate setter — shows Continue (visible), marks gated
        if "data-jh-captcha-gated" in s and (
            "holdMode" in s or "CAPTCHA — solve" in s or "window[CGATE] = !!want" in s
            or "window[CGATE]=!!want" in s.replace(" ", "")
        ):
            # Prefer dedicated gate script (sets captcha_gated + paused)
            if "pauseCount" in s or "holdMode" in s:
                want = bool(args[0]) if args else False
                self._state["captcha_gated"] = want
                if want:
                    if not self._state["paused"]:
                        self._state["paused"] = True
                        self._state["pauseCount"] += 1
                    self._state["holdMode"] = True
                else:
                    self._state["holdMode"] = False
                return {
                    "captcha_gated": want,
                    "overlay_present": True,
                    "paused": self._state["paused"],
                    "holdMode": self._state["holdMode"],
                }
        if "data-jh-captcha-gated" in s and "pauseCount" not in s:
            want = bool(args[0]) if args else False
            self._state["captcha_gated"] = want
            return {
                "captcha_gated": want,
                "overlay_present": True,
            }
        # Read state
        if "paused: !!(window" in s or '"installed": true' in s or "installed: true" in s:
            if self.fail_read:
                raise RuntimeError("simulated evaluate failure")
            return {
                "paused": self._state["paused"],
                "installed": True,
                "holdMode": self._state["holdMode"],
                "pauseCount": self._state["pauseCount"],
                "continueCount": self._state["continueCount"],
                "captcha_gated": self._state["captcha_gated"],
            }
        # set_fill_paused — dict payload {paused, hold_mode} or legacy bool
        if args and (
            (isinstance(args[0], bool) and "c.paused" in s)
            or (isinstance(args[0], dict) and "paused" in args[0])
        ):
            if isinstance(args[0], dict):
                want = bool(args[0].get("paused"))
                hold_mode = bool(args[0].get("hold_mode"))
            else:
                want = bool(args[0])
                hold_mode = False
            was = self._state["paused"]
            self._state["paused"] = want
            if want:
                self._state["holdMode"] = hold_mode or self._state["holdMode"]
            else:
                self._state["holdMode"] = False
            if want and not was:
                self._state["pauseCount"] += 1
            if (not want) and was:
                self._state["continueCount"] += 1
            return {
                "paused": self._state["paused"],
                "holdMode": self._state["holdMode"],
                "pauseCount": self._state["pauseCount"],
                "continueCount": self._state["continueCount"],
                "captcha_gated": self._state["captcha_gated"],
            }
        # Default: install overlay return
        self._state["installed"] = True
        return {
            "ok": True,
            "paused": self._state["paused"],
            "holdMode": self._state["holdMode"],
            "pauseCount": self._state["pauseCount"],
            "continueCount": self._state["continueCount"],
            "captcha_gated": self._state["captcha_gated"],
        }


def test_wait_while_paused_disabled():
    async def _run():
        page = _FakePage()
        report = {"fill_pause_enabled": False}
        out = await wait_while_paused(page, report, poll_s=0.05)
        assert out["enabled"] is False
        assert out["via"] == "disabled"
        assert out["waited"] is False

    asyncio.run(_run())


def test_wait_while_paused_not_paused():
    async def _run():
        with tempfile.TemporaryDirectory() as td:
            with _pause_files(td):
                page = _FakePage()
                report = {"fill_pause_enabled": True}
                out = await wait_while_paused(page, report, poll_s=0.05)
                assert out["enabled"] is True
                assert out["via"] == "not_paused"
                assert out["waited"] is False

    asyncio.run(_run())


def test_wait_while_paused_resume_via_sentinel():
    with tempfile.TemporaryDirectory() as td:
        with _pause_files(td):
            cont = Path(td) / ".fill_continue"

            async def _run():
                page = _FakePage()
                await set_fill_paused(page, True)
                report = {"fill_pause_enabled": True}

                async def _touch_soon():
                    await asyncio.sleep(0.35)
                    cont.write_text("")

                task = asyncio.create_task(_touch_soon())
                out = await wait_while_paused(page, report, poll_s=0.1)
                await task
                return out, report, page

            out, report, page = asyncio.run(_run())
            assert out["waited"] is True
            assert out["resumed"] is True
            assert out["via"] == "sentinel"
            assert (report.get("fill_pause") or {}).get("resume_rescan") is True
            assert _NATIVE_STATE.get("paused") is False


def test_native_hud_default_on_darwin():
    prev_dom = os.environ.pop("FASTFILL_DOM_OVERLAY", None)
    prev_hud = os.environ.pop("FASTFILL_NATIVE_HUD", None)
    try:
        os.environ.pop("FASTFILL_DOM_OVERLAY", None)
        assert use_dom_overlay() is False
        if sys.platform == "darwin":
            assert use_native_hud() is True
    finally:
        if prev_dom is None:
            os.environ.pop("FASTFILL_DOM_OVERLAY", None)
        else:
            os.environ["FASTFILL_DOM_OVERLAY"] = prev_dom
        if prev_hud is None:
            os.environ.pop("FASTFILL_NATIVE_HUD", None)
        else:
            os.environ["FASTFILL_NATIVE_HUD"] = prev_hud


def test_resolve_hud_python_prefers_tkinter_capable_interpreter():
    from fill_pause import resolve_hud_python

    py = resolve_hud_python()
    assert py
    proc = subprocess.run(
        [py, "-c", "import tkinter"],
        capture_output=True,
        timeout=5,
    )
    assert proc.returncode == 0, (py, proc.stderr)


def test_start_native_hud_uses_resolve_hud_python_in_source():
    src = (HERE / "fill_pause.py").read_text(encoding="utf-8")
    assert "resolve_hud_python" in src
    chunk = src.split("def start_native_hud", 1)[1].split("def stop_native_hud", 1)[0]
    assert "resolve_hud_python()" in chunk


def test_set_fill_paused_native_state():
    async def _run():
        with tempfile.TemporaryDirectory() as td:
            with _pause_files(td):
                prev_dom = os.environ.get("FASTFILL_DOM_OVERLAY")
                os.environ["FASTFILL_DOM_OVERLAY"] = "0"
                try:
                    page = _FakePage()
                    st = await set_fill_paused(page, True)
                    assert st.get("paused") is True
                    assert _NATIVE_STATE.get("paused") is True
                    st2 = await set_fill_paused(page, False)
                    assert st2.get("paused") is False
                finally:
                    if prev_dom is None:
                        os.environ.pop("FASTFILL_DOM_OVERLAY", None)
                    else:
                        os.environ["FASTFILL_DOM_OVERLAY"] = prev_dom

    asyncio.run(_run())


def test_wait_while_paused_resume_via_native_continue():
    async def _run():
        with tempfile.TemporaryDirectory() as td:
            with _pause_files(td):
                prev_dom = os.environ.get("FASTFILL_DOM_OVERLAY")
                os.environ["FASTFILL_DOM_OVERLAY"] = "0"
                try:
                    page = _FakePage()
                    await set_fill_paused(page, True)
                    report = {"fill_pause_enabled": True}

                    async def _unpause_soon():
                        await asyncio.sleep(0.3)
                        await set_fill_paused(page, False)

                    task = asyncio.create_task(_unpause_soon())
                    out = await wait_while_paused(page, report, poll_s=0.1)
                    await task
                    return out, report
                finally:
                    if prev_dom is None:
                        os.environ.pop("FASTFILL_DOM_OVERLAY", None)
                    else:
                        os.environ["FASTFILL_DOM_OVERLAY"] = prev_dom

    out, report = asyncio.run(_run())
    assert out["waited"] is True
    assert out["resumed"] is True
    assert out["via"] in ("overlay_continue", "native_hud")
    assert (report.get("fill_pause") or {}).get("resume_rescan") is True


def test_overlay_id_stable():
    assert OVERLAY_ID == "jh-fill-pause-overlay"


def test_dom_overlay_opt_in_only():
    prev = os.environ.get("FASTFILL_DOM_OVERLAY")
    try:
        os.environ["FASTFILL_DOM_OVERLAY"] = "1"
        assert use_dom_overlay() is True
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_DOM_OVERLAY", None)
        else:
            os.environ["FASTFILL_DOM_OVERLAY"] = prev


def test_pause_captcha_gate_shows_continue_and_skips_wait():
    """CAPTCHA gate → wait_while_paused yields; native HUD shows Continue."""

    async def _run():
        with tempfile.TemporaryDirectory() as td:
            with _pause_files(td):
                page = _FakePage()
                await set_fill_paused(page, True)
                report = {"fill_pause_enabled": True}
                gate = await set_fill_pause_captcha_gate(page, True)
                assert gate.get("captcha_gated") is True
                assert _NATIVE_STATE.get("captcha_gated") is True
                assert _NATIVE_STATE.get("paused") is True
                assert _NATIVE_STATE.get("hold_mode") is True
                out = await wait_while_paused(page, report, poll_s=0.05)
                assert out["via"] == "captcha_gated"
                await set_fill_pause_captcha_gate(page, False)
                assert _NATIVE_STATE.get("captcha_gated") is False
                await set_fill_paused(page, True)

                async def _unpause_soon():
                    await asyncio.sleep(0.25)
                    await set_fill_paused(page, False)

                task = asyncio.create_task(_unpause_soon())
                out2 = await wait_while_paused(page, report, poll_s=0.1)
                await task
                assert out2["via"] in ("overlay_continue", "native_hud")
                assert out2.get("resumed") is True or out2.get("waited") is True

    asyncio.run(_run())


def test_captcha_gate_css_keeps_overlay_visible():
    """Overlay must stay clickable during CAPTCHA (DOM mode only)."""
    prev = os.environ.get("FASTFILL_DOM_OVERLAY")
    os.environ["FASTFILL_DOM_OVERLAY"] = "1"
    try:
        from fill_pause import _OVERLAY_CSS, _INSTALL_OVERLAY_JS, _SET_CAPTCHA_GATE_JS

        assert "visibility: hidden" not in _OVERLAY_CSS
        gated_block = _OVERLAY_CSS.split("jh-captcha-gated")[1].split("}")[0]
        assert "pointer-events: none" not in gated_block
        assert "opacity: 1" in gated_block
        assert "opacity: 0" not in gated_block.replace("opacity: 1", "")
        assert SYM_PLAY in _INSTALL_OVERLAY_JS
        assert "CAPTCHA — solve" in _SET_CAPTCHA_GATE_JS
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_DOM_OVERLAY", None)
        else:
            os.environ["FASTFILL_DOM_OVERLAY"] = prev


def test_enter_hold_continue_mode():
    async def _run():
        from fill_pause import enter_hold_continue_mode

        page = _FakePage()
        report: dict = {"fill_pause_enabled": True}
        out = await enter_hold_continue_mode(page, report, incomplete=True)
        assert out.get("paused") is True
        assert out.get("holdMode") is True
        assert _NATIVE_STATE.get("paused") is True
        assert _NATIVE_STATE.get("hold_mode") is True
        assert (report.get("fill_pause") or {}).get("hold_continue_mode") is True
        assert (report.get("fill_pause") or {}).get("hold_incomplete_ui") is True

    asyncio.run(_run())


def test_hold_and_captcha_button_labels_in_overlay_js():
    """Control shows ❚❚ / ▶; aria-label keeps Pause fill / Continue / Continue fill."""
    from fill_pause import _INSTALL_OVERLAY_JS, _SET_CAPTCHA_GATE_JS

    assert SYM_PAUSE in _INSTALL_OVERLAY_JS
    assert SYM_PLAY in _INSTALL_OVERLAY_JS
    assert "data-jh-symbol" in _INSTALL_OVERLAY_JS
    assert "data-jh-mode" in _INSTALL_OVERLAY_JS
    assert "jh-status" in _INSTALL_OVERLAY_JS
    assert "compactStatus" in _INSTALL_OVERLAY_JS
    # Accessibility: full words remain on aria-label / title (not as button textContent).
    assert "aria-label" in _INSTALL_OVERLAY_JS
    assert "'Pause fill'" in _INSTALL_OVERLAY_JS or '"Pause fill"' in _INSTALL_OVERLAY_JS
    assert "'Continue'" in _INSTALL_OVERLAY_JS or '"Continue"' in _INSTALL_OVERLAY_JS
    assert "'Continue fill'" in _INSTALL_OVERLAY_JS or '"Continue fill"' in _INSTALL_OVERLAY_JS
    assert "holdMode" in _INSTALL_OVERLAY_JS
    assert "CAPTCHA — solve" in _SET_CAPTCHA_GATE_JS
    # Must not assign word labels via textContent (symbols + status spans instead).
    assert "btn.textContent = 'Pause fill'" not in _INSTALL_OVERLAY_JS
    assert "btn.textContent = 'Continue'" not in _INSTALL_OVERLAY_JS
    assert "btn.textContent = 'Continue fill'" not in _INSTALL_OVERLAY_JS


def test_inject_overlay_throttled():
    """FILL3-017: rapid reinject returns throttled without extra CDP (DOM mode)."""

    async def _run():
        prev = os.environ.get("FASTFILL_DOM_OVERLAY")
        os.environ["FASTFILL_DOM_OVERLAY"] = "1"
        try:
            page = _FakePage()
            r1 = await inject_fill_pause_overlay(page, force=True)
            assert r1.get("ok") is True
            calls_after_first = page.evaluate_calls
            r2 = await inject_fill_pause_overlay(page, force=False, throttle_s=60.0)
            assert r2.get("throttled") is True
            assert page.evaluate_calls == calls_after_first
            r3 = await inject_fill_pause_overlay(page, force=True)
            assert r3.get("throttled") is not True
            assert page.evaluate_calls > calls_after_first
        finally:
            if prev is None:
                os.environ.pop("FASTFILL_DOM_OVERLAY", None)
            else:
                os.environ["FASTFILL_DOM_OVERLAY"] = prev

    asyncio.run(_run())


def test_pause_ux_instant_stop():
    """Overlay promises instant pause, includes Pause + log, top-right CSS."""
    from fill_pause import _INSTALL_OVERLAY_JS, _OVERLAY_CSS

    js = _INSTALL_OVERLAY_JS.lower()
    css = _OVERLAY_CSS.lower()
    assert "pause fill" in js
    assert "jh-log" in js
    assert "Fill activity log" in _INSTALL_OVERLAY_JS
    assert "position: fixed" in css
    assert "top: 12px" in css
    assert "right: 12px" in css
    assert "between actions" not in js
    assert "not mid-widget" not in js
    assert "not mid-field" not in js
    assert "immediately" in js


def test_should_keep_fill_browser_open_decision():
    """Hold-open / Pause must never auto-close; timed hold may after hold ends."""
    assert should_keep_fill_browser_open(paused=True, hold_seconds=0) is True
    assert should_keep_fill_browser_open(paused=True, hold_seconds=-1) is True
    assert should_keep_fill_browser_open(paused=False, hold_seconds=-1) is True
    assert should_keep_fill_browser_open(paused=False, hold_seconds=90) is False
    assert should_keep_fill_browser_open(paused=False, hold_seconds=0) is False
    assert may_auto_close_fill_browser(paused=False, hold_seconds=0) is True
    assert may_auto_close_fill_browser(paused=True, hold_seconds=0) is False
    assert may_auto_close_fill_browser(paused=False, hold_seconds=-1) is False


def test_wait_while_paused_fail_closed_on_evaluate_error():
    """CDP blip mid-pause must not look like Continue (DOM overlay mode)."""

    async def _run():
        with tempfile.TemporaryDirectory() as td:
            with _pause_files(td):
                prev = os.environ.get("FASTFILL_DOM_OVERLAY")
                os.environ["FASTFILL_DOM_OVERLAY"] = "1"
                try:
                    page = _FakePage(fail_read=False)
                    await set_fill_paused(page, True)
                    report = {"fill_pause_enabled": True}

                    async def _blip_then_continue():
                        await asyncio.sleep(0.2)
                        page.fail_read = True
                        await asyncio.sleep(0.35)
                        page.fail_read = False
                        await set_fill_paused(page, False)

                    task = asyncio.create_task(_blip_then_continue())
                    out = await wait_while_paused(page, report, poll_s=0.1)
                    await task
                    assert out["waited"] is True
                    assert out["resumed"] is True
                    assert out["via"] in ("overlay_continue", "native_hud")
                finally:
                    if prev is None:
                        os.environ.pop("FASTFILL_DOM_OVERLAY", None)
                    else:
                        os.environ["FASTFILL_DOM_OVERLAY"] = prev

    asyncio.run(_run())

def test_drain_pause_before_close_waits():
    async def _run():
        with tempfile.TemporaryDirectory() as td:
            with _pause_files(td):
                page = _FakePage()
                await set_fill_paused(page, True)
                report = {"fill_pause_enabled": True}

                async def _continue():
                    await asyncio.sleep(0.25)
                    await set_fill_paused(page, False)

                task = asyncio.create_task(_continue())
                out = await drain_pause_before_close(page, report)
                await task
                assert out.get("drained") is True
                assert (out.get("paused_wait") or {}).get("waited") is True

    asyncio.run(_run())


def test_note_fill_activity_and_hover_globals():
    act = note_fill_activity(
        layer="flash", action="flash leftover", label="Cover letter", detail="essay"
    )
    assert act["layer"] == "flash"
    assert "Layer 2" in act["layer_label"]
    text = format_fill_activity_text(act)
    assert "Layer 2" in text
    assert "flash leftover" in text
    assert "Cover letter" in text
    compact = format_fill_activity_compact(act)
    assert compact.startswith("L2 ·")
    assert "Cover letter" in compact
    fill_act = note_fill_activity(layer="1", action="fill", label="Email")
    assert format_fill_activity_compact(fill_act) == "L1 · filling Email"
    hold_act = note_fill_activity(
        layer="hold", action="holding incomplete — not ready", detail="incomplete"
    )
    assert format_fill_activity_compact(hold_act) == "hold · incomplete"
    cap_act = note_fill_activity(layer="captcha", action="waiting human solve")
    assert format_fill_activity_compact(cap_act) == "CAPTCHA"
    assert ACTIVITY_GLOBAL == "__jhFillActivity"
    assert SYM_PAUSE == "❚❚"
    assert SYM_PLAY == "▶"
    from fill_pause import _INSTALL_OVERLAY_JS

    assert ACTIVITY_GLOBAL in _INSTALL_OVERLAY_JS
    assert "jh-activity-tip" in _INSTALL_OVERLAY_JS
    assert "jh-status" in _INSTALL_OVERLAY_JS
    assert "mouseenter" in _INSTALL_OVERLAY_JS


def test_push_activity_to_page():
    async def _run():
        from fill_pause import push_fill_activity

        page = _FakePage()
        note_fill_activity(layer="1", action="fill", label="Email")
        out = await push_fill_activity(page)
        assert out.get("ok") is True
        if use_dom_overlay():
            assert page._state.get("activity")
        assert "Email" in str(out.get("text") or "")
        assert out.get("compact") == "L1 · filling Email"

    asyncio.run(_run())


def test_native_state_atomic_write_in_source():
    src = (HERE / "fill_pause.py").read_text(encoding="utf-8")
    assert "_flock_json_update" in src
    assert "fill_pause_control_path" in src
    hud = (HERE / "fill_pause_hud.py").read_text(encoding="utf-8")
    assert ".with_suffix(\".tmp\")" in hud or "_flock_json_update" in hud
    assert "hud_chrome_bounds" in hud
    assert "#0a0a0a" in hud
    assert "jh-log" in src or "Fill activity log" in src


def test_hud_pin_chrome_env_default_on():
    from fill_pause import use_hud_pin_chrome

    prev = os.environ.pop("FASTFILL_HUD_PIN_CHROME", None)
    prev_dom = os.environ.pop("FASTFILL_DOM_OVERLAY", None)
    try:
        os.environ.pop("FASTFILL_DOM_OVERLAY", None)
        if sys.platform == "darwin":
            assert use_hud_pin_chrome() is True
        os.environ["FASTFILL_HUD_PIN_CHROME"] = "0"
        assert use_hud_pin_chrome() is False
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_HUD_PIN_CHROME", None)
        else:
            os.environ["FASTFILL_HUD_PIN_CHROME"] = prev
        if prev_dom is None:
            os.environ.pop("FASTFILL_DOM_OVERLAY", None)
        else:
            os.environ["FASTFILL_DOM_OVERLAY"] = prev_dom


def test_note_fill_chrome_for_hud_persists_pid():
    from fill_pause import fill_pause_state_path, note_fill_chrome_for_hud, reset_native_pause_state

    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / ".fill_pause_state.json"
        prev = os.environ.get("FASTFILL_FILL_PAUSE_STATE")
        os.environ["FASTFILL_FILL_PAUSE_STATE"] = str(state)
        try:
            reset_native_pause_state()
            out = note_fill_chrome_for_hud(pid=4242, job_id="job-abc")
            assert out["pid"] == 4242
            data = json.loads(state.read_text(encoding="utf-8"))
            assert data["fill_chrome_pid"] == 4242
            assert data["job_id"] == "job-abc"
        finally:
            if prev is None:
                os.environ.pop("FASTFILL_FILL_PAUSE_STATE", None)
            else:
                os.environ["FASTFILL_FILL_PAUSE_STATE"] = prev


def test_hud_chrome_offset_math():
    from hud_chrome_bounds import (
        BoundsCache,
        compute_hud_xy,
        default_hud_margins,
        margins_from_hud_xy,
    )

    chrome = {"x": 100, "y": 50, "width": 1200, "height": 800}
    mr, mt = default_hud_margins()
    hud_w, hud_h = 300, 120
    x, y = compute_hud_xy(
        chrome,
        hud_width=hud_w,
        hud_height=hud_h,
        margin_right=mr,
        margin_top=mt,
    )
    assert x == chrome["x"] + chrome["width"] - hud_w - mr
    assert y == chrome["y"] + mt
    back_mr, back_mt = margins_from_hud_xy(
        chrome, hud_x=x, hud_y=y, hud_width=hud_w, hud_height=hud_h
    )
    assert (back_mr, back_mt) == (mr, mt)

    moved = {"x": 200, "y": 80, "width": 1200, "height": 800}
    x2, y2 = compute_hud_xy(
        moved,
        hud_width=hud_w,
        hud_height=hud_h,
        margin_right=back_mr,
        margin_top=back_mt,
    )
    assert x2 == moved["x"] + moved["width"] - hud_w - back_mr
    assert y2 == moved["y"] + back_mt


def test_bounds_cache_skips_unchanged():
    from hud_chrome_bounds import BoundsCache

    cache = BoundsCache()
    chrome = {"x": 10, "y": 20, "width": 800, "height": 600}
    assert cache.should_reposition(
        chrome, hud_width=300, hud_height=100, margin_right=12, margin_top=12
    )
    assert not cache.should_reposition(
        chrome, hud_width=300, hud_height=100, margin_right=12, margin_top=12
    )
    chrome2 = dict(chrome, x=11)
    assert cache.should_reposition(
        chrome2, hud_width=300, hud_height=100, margin_right=12, margin_top=12
    )


def test_stealth_resolve_defaults():
    from stealth import (
        default_refill_passes_for_url,
        resolve_stealth_enabled,
        stealth_action_jitter_ms,
        stealth_typing_delay_ms,
    )

    prev = os.environ.pop("FASTFILL_STEALTH", None)
    try:
        assert resolve_stealth_enabled(headed=True, headless=False) is True
        assert resolve_stealth_enabled(headed=False, headless=True) is False
        assert resolve_stealth_enabled(
            headed=False,
            headless=True,
            url="https://jobs.ashbyhq.com/acme/uuid/application",
        )
        assert default_refill_passes_for_url(
            "https://jobs.ashbyhq.com/acme/uuid/application"
        ) == 1
        assert default_refill_passes_for_url(
            "https://foo.myworkdayjobs.com/en-US/careers/job/123"
        ) == 2
        lo, hi = stealth_typing_delay_ms(), stealth_action_jitter_ms()
        assert 30 <= lo <= 80
        assert 100 <= hi <= 400
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_STEALTH", None)
        else:
            os.environ["FASTFILL_STEALTH"] = prev


def test_pause_flag_aborts_mid_loop():
    """Cooperative flag aborts a tight fill loop without finishing remaining steps."""

    async def _run():
        prev_state = os.environ.get("FASTFILL_FILL_PAUSE_STATE")
        prev_dom = os.environ.get("FASTFILL_DOM_OVERLAY")
        with tempfile.TemporaryDirectory() as td:
            os.environ["FASTFILL_FILL_PAUSE_STATE"] = str(Path(td) / ".fill_pause_state.json")
            os.environ["FASTFILL_DOM_OVERLAY"] = "0"
            try:
                reset_native_pause_state()
                n = {"i": 0}

                async def loop():
                    for i in range(40):
                        abort_if_paused()
                        n["i"] = i
                        await asyncio.sleep(0.02)

                async def pause_soon():
                    await asyncio.sleep(0.08)
                    request_fill_pause(True, via="test")

                loop_task = asyncio.create_task(loop())
                pause_task = asyncio.create_task(pause_soon())
                with _raises_paused():
                    await loop_task
                await pause_task
                assert is_fill_paused_now() is True
                assert n["i"] < 39
            finally:
                request_fill_pause(False, via="test")
                if prev_state is None:
                    os.environ.pop("FASTFILL_FILL_PAUSE_STATE", None)
                else:
                    os.environ["FASTFILL_FILL_PAUSE_STATE"] = prev_state
                if prev_dom is None:
                    os.environ.pop("FASTFILL_DOM_OVERLAY", None)
                else:
                    os.environ["FASTFILL_DOM_OVERLAY"] = prev_dom

    asyncio.run(_run())


class _raises_paused:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is FillPausedAbort:
            return True
        raise AssertionError("loop should have aborted with FillPausedAbort")


def test_run_cancellable_aborts_in_flight():
    async def _run():
        prev_state = os.environ.get("FASTFILL_FILL_PAUSE_STATE")
        with tempfile.TemporaryDirectory() as td:
            os.environ["FASTFILL_FILL_PAUSE_STATE"] = str(Path(td) / ".fill_pause_state.json")
            try:
                reset_native_pause_state()

                async def sleepy():
                    await asyncio.sleep(2.0)
                    return "done"

                async def pause_soon():
                    await asyncio.sleep(0.05)
                    request_fill_pause(True, via="test")

                pause_task = asyncio.create_task(pause_soon())
                t0 = time.monotonic()
                try:
                    await run_cancellable(sleepy(), poll_s=0.04)
                    raise AssertionError("expected FillPausedAbort")
                except FillPausedAbort:
                    pass
                await pause_task
                elapsed = time.monotonic() - t0
                assert elapsed < 1.0
            finally:
                request_fill_pause(False, via="test")
                if prev_state is None:
                    os.environ.pop("FASTFILL_FILL_PAUSE_STATE", None)
                else:
                    os.environ["FASTFILL_FILL_PAUSE_STATE"] = prev_state

    asyncio.run(_run())


def test_activity_persist_does_not_clobber_hud_pause():
    """Pause control file must survive activity/status writes (the old ignore-pause bug)."""
    prev_state = os.environ.get("FASTFILL_FILL_PAUSE_STATE")
    prev_dom = os.environ.get("FASTFILL_DOM_OVERLAY")
    with tempfile.TemporaryDirectory() as td:
        os.environ["FASTFILL_FILL_PAUSE_STATE"] = str(Path(td) / ".fill_pause_state.json")
        os.environ["FASTFILL_DOM_OVERLAY"] = "0"
        try:
            reset_native_pause_state()
            assert is_fill_paused_now() is False
            request_fill_pause(True, via="hud")
            note_fill_activity(layer="1", action="fill", label="Email")
            assert is_fill_paused_now() is True
            ctrl = json.loads(fill_pause_control_path().read_text(encoding="utf-8"))
            assert ctrl.get("paused") is True
        finally:
            request_fill_pause(False, via="test")
            if prev_state is None:
                os.environ.pop("FASTFILL_FILL_PAUSE_STATE", None)
            else:
                os.environ["FASTFILL_FILL_PAUSE_STATE"] = prev_state
            if prev_dom is None:
                os.environ.pop("FASTFILL_DOM_OVERLAY", None)
            else:
                os.environ["FASTFILL_DOM_OVERLAY"] = prev_dom


def test_sanitize_fill_log_line_redacts_pii():
    out = sanitize_fill_log_line(
        "filling jane@example.com phone 405-555-0100 password=hunter2"
    )
    assert "jane@example.com" not in out
    assert "405-555-0100" not in out
    assert "hunter2" not in out
    assert "{{EMAIL}}" in out
    assert "{{PHONE}}" in out
    assert "{{SECRET}}" in out
    line = append_fill_log("filling Email email=jane@example.com", kind="fill", persist=False)
    assert "jane@example.com" not in line
    assert "{{EMAIL}}" in line


def test_ensure_fill_pause_ready_does_not_reset_pause():
    src = (HERE / "fill_pause.py").read_text(encoding="utf-8")
    chunk = src.split("async def ensure_fill_pause_ready", 1)[1].split(
        "async def detach_fill_pause_overlay", 1
    )[0]
    assert "reset_native_pause_state()" not in chunk


def test_default_hud_margins_below_titlebar():
    from hud_chrome_bounds import default_hud_margins

    right, top = default_hud_margins()
    assert 8 <= right <= 12
    assert top >= 28


def test_cdp_pause_binding_in_overlay_js():
    from fill_pause import PAUSE_BINDING, _INSTALL_OVERLAY_JS

    assert PAUSE_BINDING == "__jhFillPauseSet"
    assert PAUSE_BINDING in _INSTALL_OVERLAY_JS
    src = (HERE / "fill_pause.py").read_text(encoding="utf-8")
    assert "expose_binding" in src


def main() -> int:
    test_resolve_fill_pause_defaults()
    test_resolve_fill_pause_env()
    test_stealth_resolve_defaults()
    test_native_state_atomic_write_in_source()
    test_hud_pin_chrome_env_default_on()
    test_note_fill_chrome_for_hud_persists_pid()
    test_hud_chrome_offset_math()
    test_bounds_cache_skips_unchanged()
    test_default_hud_margins_below_titlebar()
    test_sentinel_paths_default()
    test_consume_continue_sentinel()
    test_force_pause_sentinel_present()
    test_wait_while_paused_disabled()
    test_wait_while_paused_not_paused()
    test_wait_while_paused_resume_via_sentinel()
    test_wait_while_paused_resume_via_native_continue()
    test_set_fill_paused_native_state()
    test_native_hud_default_on_darwin()
    test_resolve_hud_python_prefers_tkinter_capable_interpreter()
    test_start_native_hud_uses_resolve_hud_python_in_source()
    test_dom_overlay_opt_in_only()
    test_overlay_id_stable()
    test_pause_captcha_gate_shows_continue_and_skips_wait()
    test_captcha_gate_css_keeps_overlay_visible()
    test_enter_hold_continue_mode()
    test_hold_and_captcha_button_labels_in_overlay_js()
    test_inject_overlay_throttled()
    test_pause_ux_instant_stop()
    test_should_keep_fill_browser_open_decision()
    test_wait_while_paused_fail_closed_on_evaluate_error()
    test_drain_pause_before_close_waits()
    test_note_fill_activity_and_hover_globals()
    test_push_activity_to_page()
    test_pause_flag_aborts_mid_loop()
    test_run_cancellable_aborts_in_flight()
    test_activity_persist_does_not_clobber_hud_pause()
    test_sanitize_fill_log_line_redacts_pii()
    test_ensure_fill_pause_ready_does_not_reset_pause()
    test_cdp_pause_binding_in_overlay_js()
    print("test_fill_pause: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())