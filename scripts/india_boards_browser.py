#!/usr/bin/env python3
"""Shared headed Chrome profile for India boards that block headless/API bots.

Naukri and Hirist refuse plain HTTP / headless Playwright (Akamai / login walls).
A long-lived headed Chromium profile under ``india_boards_chrome_profile/``
works for Naukri HTML and can carry Hirist login cookies after the user signs
in once via ``./open_india_boards.sh``.

Never solves CAPTCHAs — if a challenge wall appears, scrapers log and skip.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDIA_BOARDS_PROFILE = ROOT / "india_boards_chrome_profile"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def launch_india_boards_context(*, headless: bool = False):
    """Launch persistent Chromium for India board scrapes.

    Prefers system Chrome (``channel=chrome``) — Naukri often Access-Denies
    Playwright's bundled Chromium. Falls back to bundled Chromium.
    """
    from playwright.sync_api import sync_playwright

    INDIA_BOARDS_PROFILE.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    kwargs = dict(
        user_data_dir=str(INDIA_BOARDS_PROFILE),
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
        user_agent=BROWSER_UA,
        locale="en-IN",
        viewport={"width": 1280, "height": 900},
    )
    try:
        ctx = pw.chromium.launch_persistent_context(channel="chrome", **kwargs)
    except Exception:
        ctx = pw.chromium.launch_persistent_context(**kwargs)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
    except Exception:
        pass
    return pw, ctx, page
