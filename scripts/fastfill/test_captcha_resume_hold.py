#!/usr/bin/env python3
"""Unit tests: CAPTCHA pause resolve + resume success gate (no browser)."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from captcha_pause import (  # noqa: E402
    CAPTCHA_WAIT_MESSAGE,
    captcha_waiting_marker_active,
    clear_captcha_waiting_marker,
    handle_captcha_blocker,
    resolve_captcha_wait,
    wait_for_human_captcha,
    write_captcha_waiting_marker,
)
from cycle_orchestrate import (  # noqa: E402
    captcha_unresolved_should_skip_retries,
    evaluate_cycle_success,
)
from fast_fill import _finalize, resolve_refill_wait_enter  # noqa: E402
from resume_upload import (  # noqa: E402
    apply_resume_success_gate,
    report_has_verified_resume,
)


def test_captcha_wait_defaults():
    assert resolve_captcha_wait(headed=True, captcha_wait=None) is True
    assert resolve_captcha_wait(headed=False, captcha_wait=None) is False
    assert resolve_captcha_wait(headed=True, captcha_wait=False) is False
    assert resolve_captcha_wait(headed=False, captcha_wait=True) is True
    assert CAPTCHA_WAIT_MESSAGE.startswith("CAPTCHA detected")
    assert "click Continue" in CAPTCHA_WAIT_MESSAGE
    assert "press Enter here to continue" in CAPTCHA_WAIT_MESSAGE
    assert ".captcha_continue" in CAPTCHA_WAIT_MESSAGE or "captcha_continue" in CAPTCHA_WAIT_MESSAGE
    # Overlay Continue is the primary resume control (still Enter / sentinel OK)
    assert "hidden during CAPTCHA" not in CAPTCHA_WAIT_MESSAGE
    assert "not the Pause/Continue" not in CAPTCHA_WAIT_MESSAGE


def test_overlay_continue_resumes_when_challenge_gone():
    """CAPTCHA wait: overlay Continue (paused→False) resumes after challenge gone."""

    class _FakePage:
        url = "https://example.com/captcha"
        frames = []
        main_frame = None

    os.environ["FASTFILL_CAPTCHA_NO_FOCUS"] = "1"
    try:

        async def _run():
            state = {"paused": True, "captcha_gated": True, "visible": True}

            async def _read(page, assume_paused_on_error=None):
                return {
                    "paused": state["paused"],
                    "captcha_gated": state["captcha_gated"],
                    "installed": True,
                }

            async def _gate(page, active):
                state["captcha_gated"] = bool(active)
                if active:
                    state["paused"] = True
                return {"captcha_gated": bool(active), "paused": state["paused"]}

            async def _set_paused(page, paused, hold_mode=False):
                state["paused"] = bool(paused)
                return {"paused": state["paused"]}

            async def _click_soon():
                await asyncio.sleep(0.35)
                state["visible"] = False
                state["paused"] = False  # human clicked Continue

            async def _shows(_page=None):
                return state["visible"]

            with patch(
                "captcha_pause._stdin_is_interactive", return_value=False
            ), patch(
                "captcha_pause.page_shows_interactive_captcha",
                new_callable=AsyncMock,
                side_effect=_shows,
            ), patch(
                "fill_pause.set_fill_pause_captcha_gate",
                new_callable=AsyncMock,
                side_effect=_gate,
            ), patch(
                "fill_pause.read_fill_pause_state",
                new_callable=AsyncMock,
                side_effect=_read,
            ), patch(
                "fill_pause.set_fill_paused",
                new_callable=AsyncMock,
                side_effect=_set_paused,
            ), patch(
                "fill_pause.consume_fill_continue_sentinel",
                return_value=False,
            ):
                task = asyncio.create_task(_click_soon())
                result = await wait_for_human_captcha(
                    _FakePage(),
                    headed=True,
                    captcha_wait=True,
                    timeout_s=15,
                )
                await task
            return result

        result = asyncio.run(_run())
        assert result["waited"] is True
        assert result["continued"] is True
        assert result["via"] == "overlay_continue"
        assert result.get("solved_gone") is True
    finally:
        clear_captcha_waiting_marker()
        os.environ.pop("FASTFILL_CAPTCHA_NO_FOCUS", None)


def test_overlay_continue_keeps_waiting_while_challenge_visible():
    """First Continue while CAPTCHA visible must not clear as solved (warn + wait)."""

    class _FakePage:
        url = "https://example.com/captcha"
        frames = []
        main_frame = None

    os.environ["FASTFILL_CAPTCHA_NO_FOCUS"] = "1"
    try:

        async def _run():
            state = {"paused": True, "captcha_gated": True, "continueCount": 0}

            async def _read(page, assume_paused_on_error=None):
                return {
                    "paused": state["paused"],
                    "captcha_gated": state["captcha_gated"],
                    "installed": True,
                    "continueCount": state["continueCount"],
                }

            async def _gate(page, active):
                state["captcha_gated"] = bool(active)
                if active:
                    state["paused"] = True
                return {"captcha_gated": bool(active)}

            async def _set_paused(page, paused, hold_mode=False):
                state["paused"] = bool(paused)
                return {"paused": state["paused"]}

            async def _click_early():
                await asyncio.sleep(0.2)
                state["paused"] = False  # premature Continue (once)
                state["continueCount"] += 1

            with patch(
                "captcha_pause._stdin_is_interactive", return_value=False
            ), patch(
                "captcha_pause.page_shows_interactive_captcha",
                new_callable=AsyncMock,
                return_value=True,
            ), patch(
                "fill_pause.set_fill_pause_captcha_gate",
                new_callable=AsyncMock,
                side_effect=_gate,
            ), patch(
                "fill_pause.read_fill_pause_state",
                new_callable=AsyncMock,
                side_effect=_read,
            ), patch(
                "fill_pause.set_fill_paused",
                new_callable=AsyncMock,
                side_effect=_set_paused,
            ), patch(
                "fill_pause.consume_fill_continue_sentinel",
                return_value=False,
            ):
                task = asyncio.create_task(_click_early())
                result = await wait_for_human_captcha(
                    _FakePage(),
                    headed=True,
                    captcha_wait=True,
                    timeout_s=2.5,
                )
                await task
            return result, state

        result, state = asyncio.run(_run())
        assert result["waited"] is True
        assert result["continued"] is False
        assert result.get("timed_out") is True
        assert result.get("force_resume") is not True
        # Re-armed Continue after premature click
        assert state["paused"] is True or state["captcha_gated"] is False
    finally:
        clear_captcha_waiting_marker()
        os.environ.pop("FASTFILL_CAPTCHA_NO_FOCUS", None)


def test_second_continue_force_resumes_while_challenge_visible():
    """2nd Continue while sticky detector still True → force-resume (user intent)."""

    class _FakePage:
        url = "https://example.com/captcha"
        frames = []
        main_frame = None

    os.environ["FASTFILL_CAPTCHA_NO_FOCUS"] = "1"
    try:

        async def _run():
            state = {"paused": True, "captcha_gated": True, "continueCount": 0}

            async def _read(page, assume_paused_on_error=None):
                return {
                    "paused": state["paused"],
                    "captcha_gated": state["captcha_gated"],
                    "installed": True,
                    "continueCount": state["continueCount"],
                }

            async def _gate(page, active):
                state["captcha_gated"] = bool(active)
                if active:
                    state["paused"] = True
                return {
                    "captcha_gated": bool(active),
                    "paused": state["paused"],
                }

            async def _set_paused(page, paused, hold_mode=False):
                state["paused"] = bool(paused)
                return {"paused": state["paused"]}

            async def _double_click():
                await asyncio.sleep(0.25)
                state["paused"] = False
                state["continueCount"] += 1
                await asyncio.sleep(0.9)
                state["paused"] = False
                state["continueCount"] += 1

            with patch(
                "captcha_pause._stdin_is_interactive", return_value=False
            ), patch(
                "captcha_pause.page_shows_interactive_captcha",
                new_callable=AsyncMock,
                return_value=True,  # sticky forever
            ), patch(
                "fill_pause.set_fill_pause_captcha_gate",
                new_callable=AsyncMock,
                side_effect=_gate,
            ), patch(
                "fill_pause.read_fill_pause_state",
                new_callable=AsyncMock,
                side_effect=_read,
            ), patch(
                "fill_pause.set_fill_paused",
                new_callable=AsyncMock,
                side_effect=_set_paused,
            ), patch(
                "fill_pause.consume_fill_continue_sentinel",
                return_value=False,
            ):
                task = asyncio.create_task(_double_click())
                result = await wait_for_human_captcha(
                    _FakePage(),
                    headed=True,
                    captcha_wait=True,
                    timeout_s=15,
                )
                await task
            return result

        result = asyncio.run(_run())
        assert result["waited"] is True
        assert result["continued"] is True
        assert result.get("force_resume") is True
        assert result.get("solved_gone") is True
        assert "force" in str(result.get("via") or "")
        assert result.get("timed_out") is not True
    finally:
        clear_captcha_waiting_marker()
        os.environ.pop("FASTFILL_CAPTCHA_NO_FOCUS", None)


def test_sentinel_second_touch_force_resumes():
    """Second .captcha_continue while sticky → force-resume."""

    class _FakePage:
        url = "https://example.com/captcha"
        frames = []
        main_frame = None

    with tempfile.TemporaryDirectory() as td:
        sentinel = Path(td) / ".captcha_continue"
        prev = os.environ.get("FASTFILL_CAPTCHA_CONTINUE_FILE")
        os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = str(sentinel)
        os.environ["FASTFILL_CAPTCHA_NO_FOCUS"] = "1"
        try:

            async def _run():
                async def _touch_twice():
                    await asyncio.sleep(0.25)
                    sentinel.write_text("1")
                    await asyncio.sleep(1.0)
                    sentinel.write_text("2")

                with patch(
                    "captcha_pause._stdin_is_interactive", return_value=False
                ), patch(
                    "captcha_pause.page_shows_interactive_captcha",
                    new_callable=AsyncMock,
                    return_value=True,
                ):
                    task = asyncio.create_task(_touch_twice())
                    result = await wait_for_human_captcha(
                        _FakePage(),
                        headed=True,
                        captcha_wait=True,
                        timeout_s=15,
                    )
                    await task
                return result

            result = asyncio.run(_run())
            assert result["continued"] is True
            assert result.get("force_resume") is True
            assert "force" in str(result.get("via") or "")
        finally:
            clear_captcha_waiting_marker()
            os.environ.pop("FASTFILL_CAPTCHA_NO_FOCUS", None)
            if prev is None:
                os.environ.pop("FASTFILL_CAPTCHA_CONTINUE_FILE", None)
            else:
                os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = prev


def test_visible_captcha_challenge_ignores_checkbox_widget():
    """Detector must not treat dormant hCaptcha/reCAPTCHA checkbox as challenge."""
    import inspect

    from iframe_ctx import visible_captcha_challenge

    src = inspect.getsource(visible_captcha_challenge)
    # Evaluate body must require challenge/bframe — not bare checkbox widgets.
    assert "bframe" in src
    assert "getComputedStyle" in src or "visibleBox" in src
    # Catch-all checkbox selectors must not appear in the evaluate payload.
    eval_start = src.find('"""() =>')
    eval_body = src[eval_start:] if eval_start >= 0 else src
    assert ".h-captcha iframe" not in eval_body
    assert ".g-recaptcha iframe" not in eval_body
    assert "[data-hcaptcha-widget-id]" not in eval_body
    assert "challenge" in eval_body.lower()


def test_escape_safe_while_captcha():
    """FILL3-019: Escape must not fire while CAPTCHA is on-screen."""
    from captcha_pause import escape_safe_while_captcha

    assert escape_safe_while_captcha(True) is False
    assert escape_safe_while_captcha(False) is True


def test_press_escape_unless_captcha_skips_during_challenge():
    """FILL3-019: Workday/fiber/GH Escape paths share CAPTCHA-safe press."""
    from captcha_pause import press_escape_unless_captcha

    class _KB:
        def __init__(self):
            self.pressed = []

        async def press(self, key):
            self.pressed.append(key)

    class _Page:
        def __init__(self):
            self.keyboard = _KB()

    async def _run():
        page = _Page()
        with patch(
            "captcha_pause.page_shows_interactive_captcha",
            new=AsyncMock(return_value=True),
        ):
            assert await press_escape_unless_captcha(page) is False
        assert page.keyboard.pressed == []

        with patch(
            "captcha_pause.page_shows_interactive_captcha",
            new=AsyncMock(return_value=False),
        ):
            assert await press_escape_unless_captcha(page) is True
        assert page.keyboard.pressed == ["Escape"]

        # Probe failure → fail closed (no Escape)
        page2 = _Page()
        with patch(
            "captcha_pause.page_shows_interactive_captcha",
            new=AsyncMock(side_effect=RuntimeError("cdp dead")),
        ):
            assert await press_escape_unless_captcha(page2) is False
        assert page2.keyboard.pressed == []

    asyncio.run(_run())


def test_workday_escape_helper_uses_captcha_gate():
    """FILL3-019: WD dismiss helper delegates to press_escape_unless_captcha."""
    import inspect

    from exp_workday_selectors import _escape_unless_captcha

    src = inspect.getsource(_escape_unless_captcha)
    assert "press_escape_unless_captcha" in src
    # No raw Escape outside the shared helper.
    wd = Path(__file__).resolve().parent / "exp_workday_selectors.py"
    text = wd.read_text(encoding="utf-8")
    assert 'keyboard.press("Escape")' not in text
    vs = Path(__file__).resolve().parent / "verified_select.py"
    assert 'keyboard.press("Escape")' not in vs.read_text(encoding="utf-8")
    gh = Path(__file__).resolve().parent / "gh_select.py"
    assert 'keyboard.press("Escape")' not in gh.read_text(encoding="utf-8")


def test_no_skipped_no_tty_helper_exists():
    """Headed no-TTY must wait via poll/sentinel — never abort without waiting."""
    import inspect

    from captcha_pause import wait_for_human_captcha as wfn

    src = inspect.getsource(wfn)
    assert "skipped_no_tty" not in src
    assert "consume_captcha_continue_sentinel" in src
    assert "_stdin_is_interactive" in src
    from captcha_pause import captcha_continue_sentinel_path

    p = captcha_continue_sentinel_path()
    assert p.name == ".captcha_continue" or "captcha" in p.name.lower()


def test_refill_wait_enter_defaults_off():
    """Humans must not babysit School/salary — auto-refill is the default."""
    assert resolve_refill_wait_enter(None) is False
    assert resolve_refill_wait_enter(False) is False
    assert resolve_refill_wait_enter(True) is True


def test_captcha_unresolved_skips_retries():
    """Orchestrator must not burn BLOCKED×3 on unresolved CAPTCHA."""
    assert captcha_unresolved_should_skip_retries(
        {
            "blocker": "captcha",
            "decision": {"verdict": "BLOCKED", "reasons": ["blocker:captcha"]},
        }
    )
    assert captcha_unresolved_should_skip_retries(
        {
            "blocker": "cloudflare",
            "captcha_wait": {"timed_out": True, "via": "timeout"},
        }
    )
    assert not captcha_unresolved_should_skip_retries(
        {
            "captcha_human_solved": True,
            "blocker": None,
            "decision": {"verdict": "FAIL_BLANK", "reasons": ["vision_not_complete"]},
        }
    )
    assert not captcha_unresolved_should_skip_retries(
        {
            "blocker": None,
            "decision": {"verdict": "FAIL_BLANK", "reasons": ["empty_fields:2"]},
        }
    )


def test_waiting_marker_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        sentinel = Path(td) / ".captcha_continue"
        prev = os.environ.get("FASTFILL_CAPTCHA_CONTINUE_FILE")
        os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = str(sentinel)
        try:
            payload = write_captcha_waiting_marker(
                blocker="captcha",
                timeout_s=60,
                sentinel=sentinel,
                has_tty=False,
                page_url="https://example.com/apply",
            )
            assert payload["status"] == "waiting"
            json_p = Path(td) / ".captcha_waiting.json"
            md_p = Path(td) / "CAPTCHA_WAITING.md"
            assert json_p.is_file()
            assert md_p.is_file()
            assert "touch" in md_p.read_text()
            assert captcha_waiting_marker_active() is True
            clear_captcha_waiting_marker()
            assert not json_p.exists()
            assert not md_p.exists()
            assert captcha_waiting_marker_active() is False
        finally:
            if prev is None:
                os.environ.pop("FASTFILL_CAPTCHA_CONTINUE_FILE", None)
            else:
                os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = prev


def test_captcha_marker_ttl_clears_stale():
    """CHR2-005: dead-writer / past-timeout markers must not forever hold."""
    import json
    import time

    with tempfile.TemporaryDirectory() as td:
        sentinel = Path(td) / ".captcha_continue"
        prev = os.environ.get("FASTFILL_CAPTCHA_CONTINUE_FILE")
        os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = str(sentinel)
        try:
            write_captcha_waiting_marker(
                blocker="captcha",
                timeout_s=1,
                sentinel=sentinel,
                has_tty=False,
                page_url="https://example.com/apply",
            )
            json_p = Path(td) / ".captcha_waiting.json"
            payload = json.loads(json_p.read_text())
            # Dead writer PID + age past dead-pid grace → stale.
            payload["pid"] = 999_999_999
            payload["ts"] = time.time() - 60
            json_p.write_text(json.dumps(payload))
            assert captcha_waiting_marker_active() is False
            assert not json_p.exists()

            write_captcha_waiting_marker(
                blocker="captcha",
                timeout_s=1,
                sentinel=sentinel,
                has_tty=False,
            )
            payload = json.loads(json_p.read_text())
            # Past timeout_s + grace even with live-looking pid → stale.
            payload["ts"] = time.time() - 1000
            payload["timeout_s"] = 1
            payload["pid"] = os.getpid()
            json_p.write_text(json.dumps(payload))
            assert captcha_waiting_marker_active() is False
            assert not json_p.exists()
        finally:
            clear_captcha_waiting_marker()
            if prev is None:
                os.environ.pop("FASTFILL_CAPTCHA_CONTINUE_FILE", None)
            else:
                os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = prev


def test_sentinel_continue_no_tty():
    """No-TTY headed wait continues when sentinel appears AND challenge is gone."""

    class _FakePage:
        url = "https://example.com/captcha"
        frames = []
        main_frame = None

    with tempfile.TemporaryDirectory() as td:
        sentinel = Path(td) / ".captcha_continue"
        prev = os.environ.get("FASTFILL_CAPTCHA_CONTINUE_FILE")
        os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = str(sentinel)
        os.environ["FASTFILL_CAPTCHA_NO_FOCUS"] = "1"
        try:

            async def _run():
                still_visible = {"v": True}

                async def _touch_soon():
                    await asyncio.sleep(0.3)
                    still_visible["v"] = False  # human solved before sentinel
                    sentinel.write_text("")

                async def _shows(_page=None):
                    return still_visible["v"]

                with patch(
                    "captcha_pause._stdin_is_interactive", return_value=False
                ), patch(
                    "captcha_pause.page_shows_interactive_captcha",
                    new_callable=AsyncMock,
                    side_effect=_shows,
                ):
                    task = asyncio.create_task(_touch_soon())
                    result = await wait_for_human_captcha(
                        _FakePage(),
                        headed=True,
                        captcha_wait=True,
                        timeout_s=15,
                    )
                    await task
                return result

            result = asyncio.run(_run())
            assert result["waited"] is True
            assert result["continued"] is True
            assert result["via"] in ("sentinel", "gone")
            assert result.get("solved_gone") is True
            assert result.get("timed_out") is False
            assert "skipped_no_tty" not in str(result)
        finally:
            clear_captcha_waiting_marker()
            os.environ.pop("FASTFILL_CAPTCHA_NO_FOCUS", None)
            if prev is None:
                os.environ.pop("FASTFILL_CAPTCHA_CONTINUE_FILE", None)
            else:
                os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = prev


def test_sentinel_keeps_waiting_while_challenge_visible():
    """FILL-008: sentinel while CAPTCHA still visible must not clear as solved."""

    class _FakePage:
        url = "https://example.com/captcha"
        frames = []
        main_frame = None

    with tempfile.TemporaryDirectory() as td:
        sentinel = Path(td) / ".captcha_continue"
        prev = os.environ.get("FASTFILL_CAPTCHA_CONTINUE_FILE")
        os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = str(sentinel)
        os.environ["FASTFILL_CAPTCHA_NO_FOCUS"] = "1"
        try:

            async def _run():
                async def _touch_early():
                    await asyncio.sleep(0.2)
                    sentinel.write_text("")

                with patch(
                    "captcha_pause._stdin_is_interactive", return_value=False
                ), patch(
                    "captcha_pause.page_shows_interactive_captcha",
                    new_callable=AsyncMock,
                    return_value=True,  # always still visible
                ):
                    task = asyncio.create_task(_touch_early())
                    result = await wait_for_human_captcha(
                        _FakePage(),
                        headed=True,
                        captcha_wait=True,
                        timeout_s=2.5,
                    )
                    await task
                return result

            result = asyncio.run(_run())
            assert result["waited"] is True
            assert result["continued"] is False
            assert result.get("timed_out") is True
            assert result.get("solved_gone") is False
        finally:
            clear_captcha_waiting_marker()
            os.environ.pop("FASTFILL_CAPTCHA_NO_FOCUS", None)
            if prev is None:
                os.environ.pop("FASTFILL_CAPTCHA_CONTINUE_FILE", None)
            else:
                os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = prev

def test_handle_blocker_clears_on_continue():
    class _FakePage:
        url = "https://example.com"
        frames = []
        main_frame = None

    with tempfile.TemporaryDirectory() as td:
        sentinel = Path(td) / ".captcha_continue"
        prev = os.environ.get("FASTFILL_CAPTCHA_CONTINUE_FILE")
        os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = str(sentinel)
        os.environ["FASTFILL_CAPTCHA_NO_FOCUS"] = "1"
        report = {
            "blocker": "captcha",
            "leftovers": [{"reason": "blocker:captcha", "label": "page_blocked"}],
        }
        try:

            async def _run():
                with patch(
                    "captcha_pause._stdin_is_interactive", return_value=False
                ), patch(
                    "captcha_pause.page_shows_interactive_captcha",
                    new_callable=AsyncMock,
                    side_effect=[True, True, False],
                ):
                    return await handle_captcha_blocker(
                        _FakePage(),
                        report,
                        "captcha",
                        headed=True,
                        captcha_wait=True,
                        timeout_s=15,
                    )

            outcome = asyncio.run(_run())
            assert outcome == "continued"
            assert report.get("captcha_human_solved") is True
            assert report.get("blocker") is None or "blocker" not in report
            assert not any(
                str(u.get("reason", "")).startswith("blocker:captcha")
                for u in (report.get("leftovers") or [])
                if isinstance(u, dict)
            )
        finally:
            clear_captcha_waiting_marker()
            os.environ.pop("FASTFILL_CAPTCHA_NO_FOCUS", None)
            if prev is None:
                os.environ.pop("FASTFILL_CAPTCHA_CONTINUE_FILE", None)
            else:
                os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = prev


def test_resume_gate_fails_success_when_missing():
    report = {
        "verdict": "SUCCESS",
        "filled": [],
        "leftovers": [],
        "resume_upload": {"field_present": True, "attempted": True, "verified": False},
        "entry_prepass": {"final_clicks": 0},
        "never_submit": True,
        "submit_clicked": False,
    }
    out = apply_resume_success_gate(report)
    assert out["verdict"] == "FAIL"
    assert out["resume_gate"] == "missing_or_unverified"
    assert out["resume_verified"] is False
    assert any(u.get("reason") == "resume_missing" for u in out["leftovers"])


def test_resume_gate_ok_when_verified_row():
    report = {
        "verdict": "SUCCESS",
        "filled": [
            {
                "type": "RESUME_UPLOAD",
                "mode": "file",
                "ok": True,
                "verified": True,
                "value": "dummy_resume_de.pdf",
                "readback": "dummy_resume_de.pdf",
            }
        ],
        "leftovers": [],
        "resume_upload": {"field_present": True, "verified": True},
        "entry_prepass": {"final_clicks": 0},
        "never_submit": True,
        "submit_clicked": False,
    }
    assert report_has_verified_resume(report) is True
    out = apply_resume_success_gate(report)
    assert out["resume_gate"] == "verified"
    assert out["verdict"] == "SUCCESS"


def test_finalize_applies_resume_gate():
    report = {
        "filled": [],
        "leftovers": [],
        "extracted_count": 0,
        "verdict": "SUCCESS",
        "resume_upload": {"field_present": True, "attempted": True},
        "entry_prepass": {"final_clicks": 0},
    }
    out = _finalize(report)
    assert out["verdict"] == "FAIL"
    assert out["resume_gate"] == "missing_or_unverified"


def test_cycle_success_requires_resume_when_present():
    decision = evaluate_cycle_success(
        {
            "never_submit": True,
            "submit_clicked": False,
            "identity_email": "randommail6969+abc123def456@gmail.com",
            "resume_field_present": True,
            "resume_verified": False,
            "leftovers": [],
        },
        {
            "complete": True,
            "empty_fields": [],
            "confidence": "high",
            "source": "dom",
        },
    )
    assert decision["success"] is False
    assert "resume_missing" in decision["reasons"]


def test_cycle_success_ok_without_resume_field():
    decision = evaluate_cycle_success(
        {
            "never_submit": True,
            "submit_clicked": False,
            "identity_email": "randommail6969+abc123def456@gmail.com",
            "resume_field_present": False,
            "resume_verified": False,
            "leftovers": [],
        },
        {
            "complete": True,
            "empty_fields": [],
            "confidence": "high",
            "source": "dom",
        },
    )
    assert decision["success"] is True


def test_cycle_success_rejects_heuristic_and_missing_source():
    """W03 P0: heuristic / missing source / demoted_false_verified never SUCCESS."""
    base_report = {
        "never_submit": True,
        "submit_clicked": False,
        "identity_email": "randommail6969+abc123def456@gmail.com",
        "resume_field_present": False,
        "leftovers": [],
    }
    d1 = evaluate_cycle_success(
        base_report,
        {
            "complete": True,
            "empty_fields": [],
            "confidence": "high",
            "source": "heuristic_report",
        },
    )
    assert d1["success"] is False
    assert "vision_source_heuristic_not_honest" in d1["reasons"]

    d2 = evaluate_cycle_success(
        base_report,
        {"complete": True, "empty_fields": [], "confidence": "high"},
    )
    assert d2["success"] is False
    assert "vision_source_missing" in d2["reasons"]

    d3 = evaluate_cycle_success(
        {**base_report, "demoted_false_verified": [{"type": "LINKEDIN"}]},
        {
            "complete": True,
            "empty_fields": [],
            "confidence": "high",
            "source": "cursor_agent2_pixels",
        },
    )
    assert d3["success"] is False
    assert any(r.startswith("demoted_false_verified") for r in d3["reasons"])


def test_cycle_success_ignores_optional_demographic_empties():
    """Live DOM may list optional race blank; must not block SUCCESS."""
    decision = evaluate_cycle_success(
        {
            "never_submit": True,
            "submit_clicked": False,
            "identity_email": "randommail6969+abc123def456@gmail.com",
            "resume_field_present": False,
            "leftovers": [],
        },
        {
            "complete": True,
            "empty_fields": [
                {
                    "label": "Please identify your race",
                    "optional_demographic": True,
                    "required": False,
                }
            ],
            "confidence": "high",
            "source": "dom",
        },
    )
    assert decision["success"] is True


def test_cycle_success_rejects_leftovers_despite_dom_complete():
    """Navigate-away false COMPLETE must not SUCCESS when leftovers remain."""
    decision = evaluate_cycle_success(
        {
            "never_submit": True,
            "submit_clicked": False,
            "identity_email": "randommail6969+abc123def456@gmail.com",
            "resume_field_present": False,
            "leftovers": [
                {
                    "label": "email",
                    "reason": "live_required_empty:empty_required_input",
                }
            ],
            "unfillable_after_2": True,
            "unfillable_count": 1,
        },
        {
            "complete": True,
            "empty_fields": [],
            "confidence": "high",
            "source": "dom",
        },
    )
    assert decision["success"] is False
    assert any(r.startswith("leftovers_remain") for r in decision["reasons"])
    assert "unfillable_after_2" in decision["reasons"]


def test_hold_open_is_indefinite():
    from fast_fill import (
        HOLD_INDEFINITE,
        HOLD_OPEN_SECONDS,
        _resolve_hold_seconds,
        hold_is_active,
    )

    assert HOLD_OPEN_SECONDS == HOLD_INDEFINITE
    assert _resolve_hold_seconds(hold_seconds=HOLD_INDEFINITE, headed=True) == HOLD_INDEFINITE
    assert hold_is_active(HOLD_INDEFINITE) is True
    assert hold_is_active(90) is True
    assert hold_is_active(0) is False
    # Explicit short hold still capped for variety unless ALLOW_LONG_HOLD
    assert _resolve_hold_seconds(hold_seconds=90, headed=True) == 90
    assert _resolve_hold_seconds(hold_seconds=None, headed=False) == 0


def test_hold_for_review_continue_via_overlay():
    """Hold arms Continue; overlay unpause returns continued=True (resume fill)."""
    from fast_fill import HOLD_INDEFINITE, _hold_for_review

    class _Browser:
        def is_connected(self):
            return True

    async def _run():
        paused = {"v": True}
        report = {
            "verdict": "FAIL",
            "hold_incomplete": True,
            "fill_pause_enabled": True,
            "leftovers": [],
            "required_empty_after_fill": ["x"],
            "footer_primary_kind": "ADVANCE",
            "footer_primary_label": "Next",
        }

        async def _read(page, assume_paused_on_error=None):
            return {
                "paused": paused["v"],
                "captcha_gated": False,
                "installed": True,
            }

        async def _enter(page, report=None, incomplete=False):
            paused["v"] = True
            return {"paused": True, "holdMode": True}

        async def _continue_soon():
            await asyncio.sleep(0.4)
            paused["v"] = False

        task = asyncio.create_task(_continue_soon())
        with patch(
            "fast_fill.enter_hold_continue_mode",
            new_callable=AsyncMock,
            side_effect=_enter,
        ), patch(
            "fast_fill.read_fill_pause_state",
            new_callable=AsyncMock,
            side_effect=_read,
        ), patch(
            "fast_fill.consume_fill_continue_sentinel", return_value=False
        ), patch(
            "fast_fill.set_fill_paused", new_callable=AsyncMock
        ):
            out = await _hold_for_review(
                seconds=HOLD_INDEFINITE,
                report=report,
                browser=_Browser(),
                page=object(),
            )
        await task
        return out, report

    out, report = asyncio.run(_run())
    assert out.get("continued") is True
    assert out.get("via") == "overlay_continue"
    assert report.get("hold_continued") is True


def test_hold_for_review_indefinite_exits_on_disconnect():
    from fast_fill import HOLD_INDEFINITE, _hold_for_review

    class _Gone:
        def is_connected(self):
            return False

    async def _run():
        # Clean report + live vision → can_claim_ready True → Ready ok
        report = {
            "verdict": "SUCCESS",
            "leftovers": [],
            "required_empty_after_fill": [],
            "required_empty_before_advance": [],
            "vision_judge_live": {
                "complete": True,
                "verdict": "COMPLETE",
                "empty_fields": [],
                "never_submit": True,
            },
        }
        await _hold_for_review(seconds=HOLD_INDEFINITE, report=report, browser=_Gone())
        assert report.get("ready_for_review") is True
        assert report.get("hold_indefinite") is True

    async def _no_vision_no_ready():
        report = {
            "verdict": "SUCCESS",
            "leftovers": [],
            "required_empty_after_fill": [],
            "required_empty_before_advance": [],
        }
        await _hold_for_review(seconds=HOLD_INDEFINITE, report=report, browser=_Gone())
        assert report.get("hold_indefinite") is True
        assert report.get("ready_for_review") is not True

    async def _auth_wall_no_ready():
        report = {
            "verdict": "FAIL",
            "blocker": "auth_wall",
            "leftovers": [],
            "required_empty_after_fill": [],
            "required_empty_before_advance": [],
            "vision_judge_live": {
                "complete": True,
                "verdict": "COMPLETE",
                "empty_fields": [],
                "never_submit": True,
            },
        }
        await _hold_for_review(seconds=HOLD_INDEFINITE, report=report, browser=_Gone())
        assert report.get("hold_indefinite") is True
        assert report.get("ready_for_review") is not True

    asyncio.run(_run())
    asyncio.run(_no_vision_no_ready())
    asyncio.run(_auth_wall_no_ready())


def test_resume_pdf_allows_job_scoped_in_real_mode(tmp_path):
    """Real-profile mode must accept resumes/<job>/resume.pdf (Jerry/Ashby fix)."""
    import os

    from field_map import RESUME_UPLOAD
    from resume_upload import resume_pdf_from_values

    pdf = tmp_path / "resumes" / "jerry-ai-senior-data-scientist-2" / "resume.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")
    prev = {
        k: os.environ.get(k)
        for k in ("FASTFILL_ALLOW_REAL", "FASTFILL_REAL_PROFILE", "TEST_MODE")
    }
    try:
        os.environ["FASTFILL_ALLOW_REAL"] = "1"
        os.environ["FASTFILL_REAL_PROFILE"] = "1"
        os.environ["TEST_MODE"] = "0"
        out = resume_pdf_from_values({RESUME_UPLOAD: str(pdf)})
        assert out == pdf
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    import tempfile

    test_captcha_wait_defaults()
    test_escape_safe_while_captcha()
    test_press_escape_unless_captcha_skips_during_challenge()
    test_workday_escape_helper_uses_captcha_gate()
    test_no_skipped_no_tty_helper_exists()
    test_refill_wait_enter_defaults_off()
    test_captcha_unresolved_skips_retries()
    test_waiting_marker_roundtrip()
    test_captcha_marker_ttl_clears_stale()
    test_sentinel_continue_no_tty()
    test_sentinel_keeps_waiting_while_challenge_visible()
    test_overlay_continue_resumes_when_challenge_gone()
    test_overlay_continue_keeps_waiting_while_challenge_visible()
    test_second_continue_force_resumes_while_challenge_visible()
    test_sentinel_second_touch_force_resumes()
    test_visible_captcha_challenge_ignores_checkbox_widget()
    test_handle_blocker_clears_on_continue()
    test_resume_gate_fails_success_when_missing()
    test_resume_gate_ok_when_verified_row()
    test_finalize_applies_resume_gate()
    test_cycle_success_requires_resume_when_present()
    test_cycle_success_ok_without_resume_field()
    test_cycle_success_rejects_heuristic_and_missing_source()
    test_hold_open_is_indefinite()
    test_hold_for_review_continue_via_overlay()
    test_hold_for_review_indefinite_exits_on_disconnect()
    with tempfile.TemporaryDirectory() as td:
        test_resume_pdf_allows_job_scoped_in_real_mode(Path(td))
    print("test_captcha_resume_hold: OK")
