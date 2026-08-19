#!/usr/bin/env python3
"""Unit tests for scripts/chrome_for_testing.py (CHR2-001)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from chrome_for_testing import (  # noqa: E402
    is_daily_google_chrome,
    resolve_chrome_for_testing,
)


def test_is_daily_google_chrome():
    assert is_daily_google_chrome(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    assert not is_daily_google_chrome(
        "/Users/x/Library/Caches/ms-playwright/chromium-1/chrome-mac-arm64/"
        "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    )
    assert not is_daily_google_chrome("/Applications/Chromium.app/Contents/MacOS/Chromium")
    assert not is_daily_google_chrome(None)


def test_launch_cft_without_url_uses_no_startup_window() -> None:
    src = (ROOT / "scripts" / "chrome_for_testing.py").read_text()
    block = src.split("def launch_cft_with_openclaw_profile(", 1)[1].split(
        "\ndef ensure_partyrock_browser_direct", 1
    )[0]
    assert "--no-startup-window" in block
    assert "if url:" in block


def test_resolve_cft_finds_playwright_cache():
    cft = resolve_chrome_for_testing()
    assert cft is not None, "expected Playwright Chrome for Testing on this machine"
    assert cft.is_file()
    assert "Chrome for Testing" in str(cft) or "Chromium" in str(cft)
    assert not is_daily_google_chrome(cft)


if __name__ == "__main__":
    test_is_daily_google_chrome()
    test_launch_cft_without_url_uses_no_startup_window()
    test_resolve_cft_finds_playwright_cache()
    print("OK test_chrome_for_testing")
