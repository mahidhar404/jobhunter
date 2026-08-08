#!/usr/bin/env python3
"""Unit tests: fill_pause resolve + sentinels + mock page pause/resume (no browser)."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fill_pause import (  # noqa: E402
    ACTIVITY_GLOBAL,
    CAPTCHA_GATE_GLOBAL,
    OVERLAY_ID,
    consume_fill_continue_sentinel,
    drain_pause_before_close,
    fill_pause_continue_sentinel_path,
    fill_pause_force_sentinel_path,
    force_pause_sentinel_present,
    format_fill_activity_text,
    inject_fill_pause_overlay,
    may_auto_close_fill_browser,
    note_fill_activity,
    resolve_fill_pause,
    set_fill_pause_captcha_gate,
    set_fill_paused,
    should_keep_fill_browser_open,
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


class _FakePage:
    """Minimal page mock: evaluate stores paused state + overlay install."""

    def __init__(self, *, fail_read: bool = False):
        self._state = {
            "paused": False,
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
                "pauseCount": self._state["pauseCount"],
                "continueCount": self._state["continueCount"],
                "captcha_gated": self._state["captcha_gated"],
            }
        # CAPTCHA gate setter only (not install / set_fill_paused)
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
                "pauseCount": self._state["pauseCount"],
                "continueCount": self._state["continueCount"],
                "captcha_gated": self._state["captcha_gated"],
            }
        # set_fill_paused (takes bool arg, mutates pauseCount)
        if args and isinstance(args[0], bool) and "c.paused" in s:
            want = bool(args[0])
            was = self._state["paused"]
            self._state["paused"] = want
            if want and not was:
                self._state["pauseCount"] += 1
            if (not want) and was:
                self._state["continueCount"] += 1
            return {
                "paused": self._state["paused"],
                "pauseCount": self._state["pauseCount"],
                "continueCount": self._state["continueCount"],
                "captcha_gated": self._state["captcha_gated"],
            }
        # Default: install overlay return
        self._state["installed"] = True
        return {
            "ok": True,
            "paused": self._state["paused"],
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
        page = _FakePage()
        report = {"fill_pause_enabled": True}
        out = await wait_while_paused(page, report, poll_s=0.05)
        assert out["enabled"] is True
        assert out["via"] == "not_paused"
        assert out["waited"] is False

    asyncio.run(_run())


def test_wait_while_paused_resume_via_sentinel():
    with tempfile.TemporaryDirectory() as td:
        cont = Path(td) / ".fill_continue"
        prev_c = os.environ.get("FASTFILL_FILL_CONTINUE_FILE")
        prev_p = os.environ.get("FASTFILL_FILL_PAUSE_FILE")
        os.environ["FASTFILL_FILL_CONTINUE_FILE"] = str(cont)
        os.environ["FASTFILL_FILL_PAUSE_FILE"] = str(Path(td) / ".fill_paused")
        try:

            async def _run():
                page = _FakePage()
                page._state["paused"] = True
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
            assert page._state["paused"] is False
        finally:
            if prev_c is None:
                os.environ.pop("FASTFILL_FILL_CONTINUE_FILE", None)
            else:
                os.environ["FASTFILL_FILL_CONTINUE_FILE"] = prev_c
            if prev_p is None:
                os.environ.pop("FASTFILL_FILL_PAUSE_FILE", None)
            else:
                os.environ["FASTFILL_FILL_PAUSE_FILE"] = prev_p


def test_wait_while_paused_resume_via_overlay_continue():
    async def _run():
        page = _FakePage()
        page._state["paused"] = True
        report = {"fill_pause_enabled": True}

        async def _unpause_soon():
            await asyncio.sleep(0.3)
            page._state["paused"] = False
            page._state["continueCount"] += 1

        task = asyncio.create_task(_unpause_soon())
        out = await wait_while_paused(page, report, poll_s=0.1)
        await task
        return out, report

    out, report = asyncio.run(_run())
    assert out["waited"] is True
    assert out["resumed"] is True
    assert out["via"] == "overlay_continue"
    assert (report.get("fill_pause") or {}).get("resume_rescan") is True


def test_set_fill_paused_updates_state():
    async def _run():
        page = _FakePage()
        st = await set_fill_paused(page, True)
        assert st.get("paused") is True
        st2 = await set_fill_paused(page, False)
        assert st2.get("paused") is False

    asyncio.run(_run())


def test_overlay_id_stable():
    assert OVERLAY_ID == "jh-fill-pause-overlay"


def test_pause_captcha_gate_hides_overlay_and_skips_wait():
    """FILL3-002 / FILL2-S03: CAPTCHA gate → wait_while_paused exits; overlay gated."""

    async def _run():
        page = _FakePage()
        page._state["paused"] = True  # would block without gate
        report = {"fill_pause_enabled": True}
        gate = await set_fill_pause_captcha_gate(page, True)
        assert gate.get("captcha_gated") is True
        assert page._state["captcha_gated"] is True
        out = await wait_while_paused(page, report, poll_s=0.05)
        assert out["via"] == "captcha_gated"
        assert out["waited"] is False
        # Ungate restores normal pause wait path
        await set_fill_pause_captcha_gate(page, False)
        assert page._state["captcha_gated"] is False

        async def _unpause_soon():
            await asyncio.sleep(0.25)
            page._state["paused"] = False

        task = asyncio.create_task(_unpause_soon())
        out2 = await wait_while_paused(page, report, poll_s=0.1)
        await task
        assert out2["via"] == "overlay_continue"
        assert out2["waited"] is True

    asyncio.run(_run())


def test_inject_overlay_throttled():
    """FILL3-017: rapid reinject returns throttled without extra CDP."""

    async def _run():
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

    asyncio.run(_run())


def test_pause_ux_says_between_actions():
    """FILL3-009: overlay copy must not promise near-immediate mid-widget stop."""
    from fill_pause import _INSTALL_OVERLAY_JS

    assert "between" in _INSTALL_OVERLAY_JS.lower()
    assert "near-immediate" not in _INSTALL_OVERLAY_JS.lower()
    assert "mid-widget" in _INSTALL_OVERLAY_JS.lower() or "mid-field" in _INSTALL_OVERLAY_JS.lower()


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
    """CDP blip mid-pause must not look like Continue (would close Chrome)."""

    async def _run():
        page = _FakePage(fail_read=False)
        page._state["paused"] = True
        report = {"fill_pause_enabled": True}

        async def _blip_then_continue():
            # Let pause engage (successful read → last_known=paused)
            await asyncio.sleep(0.2)
            page.fail_read = True
            await asyncio.sleep(0.35)
            page.fail_read = False
            page._state["paused"] = False

        task = asyncio.create_task(_blip_then_continue())
        out = await wait_while_paused(page, report, poll_s=0.1)
        await task
        assert out["waited"] is True
        assert out["resumed"] is True
        assert out["via"] == "overlay_continue"

    asyncio.run(_run())

def test_drain_pause_before_close_waits():
    async def _run():
        page = _FakePage()
        page._state["paused"] = True
        report = {"fill_pause_enabled": True}

        async def _continue():
            await asyncio.sleep(0.25)
            page._state["paused"] = False

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
    assert ACTIVITY_GLOBAL == "__jhFillActivity"
    from fill_pause import _INSTALL_OVERLAY_JS

    assert ACTIVITY_GLOBAL in _INSTALL_OVERLAY_JS
    assert "jh-activity-tip" in _INSTALL_OVERLAY_JS
    assert "mouseenter" in _INSTALL_OVERLAY_JS


def test_push_activity_to_page():
    async def _run():
        from fill_pause import push_fill_activity

        page = _FakePage()
        note_fill_activity(layer="1", action="fill", label="Email")
        out = await push_fill_activity(page)
        assert out.get("ok") is True
        assert page._state.get("activity")
        assert "Email" in str(page._state["activity"].get("text") or "")

    asyncio.run(_run())


def main() -> int:
    test_resolve_fill_pause_defaults()
    test_resolve_fill_pause_env()
    test_sentinel_paths_default()
    test_consume_continue_sentinel()
    test_force_pause_sentinel_present()
    test_wait_while_paused_disabled()
    test_wait_while_paused_not_paused()
    test_wait_while_paused_resume_via_sentinel()
    test_wait_while_paused_resume_via_overlay_continue()
    test_set_fill_paused_updates_state()
    test_overlay_id_stable()
    test_pause_captcha_gate_hides_overlay_and_skips_wait()
    test_inject_overlay_throttled()
    test_pause_ux_says_between_actions()
    test_should_keep_fill_browser_open_decision()
    test_wait_while_paused_fail_closed_on_evaluate_error()
    test_drain_pause_before_close_waits()
    test_note_fill_activity_and_hover_globals()
    test_push_activity_to_page()
    print("test_fill_pause: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())