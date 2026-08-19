"""Human-like fill timing + stealth mode resolution for fastfill."""

from __future__ import annotations

import asyncio
import os
import random
from typing import Any

from browser_hygiene import is_ashby_url


def resolve_stealth_enabled(
    *,
    headed: bool,
    headless: bool,
    platform: str = "",
    url: str = "",
    stealth: bool | None = None,
) -> bool:
    """Whether to use human-like typing and inter-field jitter.

    Priority:
      1. Explicit ``stealth`` arg
      2. ``FASTFILL_STEALTH`` env (1/0)
      3. Ashby URLs / platform → ON (even headless)
      4. Headed → ON, headless → OFF
    """
    if stealth is not None:
        return bool(stealth)
    raw = (os.environ.get("FASTFILL_STEALTH") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    plat = (platform or "").lower()
    if plat == "ashby" or is_ashby_url(url):
        return True
    if headed and not headless:
        return True
    return False


def stealth_typing_delay_ms() -> int:
    """Per-keystroke delay for ``locator.type`` (30–80ms)."""
    return random.randint(30, 80)


def stealth_action_jitter_ms() -> int:
    """Pause between field actions when stealth is on (100–400ms)."""
    return random.randint(100, 400)


async def stealth_field_pause(page, report: dict | None) -> None:
    """Small random delay before touching the next field. Aborts if Pause is on."""
    if not report or not report.get("stealth_enabled"):
        return
    ms = stealth_action_jitter_ms()
    if ms <= 0 or page is None:
        return
    remaining = ms / 1000.0
    while remaining > 0:
        try:
            from fill_pause import abort_if_paused

            abort_if_paused()
        except ImportError:
            pass
        slice_s = min(0.05, remaining)
        try:
            await page.wait_for_timeout(int(slice_s * 1000))
        except Exception:
            await asyncio.sleep(slice_s)
        remaining -= slice_s


async def stealth_fill_visible_text(
    loc,
    value: str,
    *,
    page=None,
    field_type: str = "",
    automation_id: str = "",
    selector: str = "",
) -> dict[str, Any]:
    """Type into a visible text control with human delays; fiber fallback when needed."""
    from verified_select import fill_text_fiber_then_read, is_stubborn_text_field

    stubborn = is_stubborn_text_field(
        automation_id=automation_id,
        field_type=field_type,
        selector=selector,
    )
    if stubborn:
        return await fill_text_fiber_then_read(loc, str(value), stubborn=True, page=page)

    visible = True
    try:
        visible = bool(await loc.is_visible(timeout=1500))
    except Exception:
        visible = False
    if not visible:
        return await fill_text_fiber_then_read(loc, str(value), stubborn=False, page=page)

    delay = stealth_typing_delay_ms()
    try:
        from fill_pause import FillPausedAbort, abort_if_paused, run_cancellable
    except ImportError:
        FillPausedAbort = Exception  # type: ignore[misc,assignment]
        abort_if_paused = None  # type: ignore[assignment]
        run_cancellable = None  # type: ignore[assignment]

    try:
        if abort_if_paused:
            abort_if_paused()
        click = loc.click(timeout=3000)
        await (run_cancellable(click) if run_cancellable else click)
    except FillPausedAbort:
        raise
    except Exception:
        pass
    try:
        if abort_if_paused:
            abort_if_paused()
        await loc.fill("", timeout=3000)
    except FillPausedAbort:
        raise
    except Exception:
        try:
            await loc.clear(timeout=3000)
        except Exception:
            pass
    type_coro = loc.type(str(value)[:2000], delay=delay)
    if run_cancellable is not None:
        await run_cancellable(type_coro)
    else:
        await type_coro
    return {"algorithm": "stealth_type", "stealth_delay_ms": delay}


def default_refill_passes_for_url(url: str) -> int:
    """Dashboard / visible-fill defaults: Ashby 1, Workday 2, else 2."""
    low = (url or "").lower()
    if "ashbyhq.com" in low or ".ashby.com/" in low:
        return 1
    if "myworkdayjobs.com" in low or "workday.com" in low:
        return 2
    return 2
