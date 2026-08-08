#!/usr/bin/env python3
"""Unit tests for pw_fetch_html helpers (no live browser required)."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pw_fetch_html as mod  # noqa: E402


def test_challenge_detection() -> None:
    assert mod.looks_like_challenge_page(None)
    assert mod.looks_like_challenge_page("short")
    assert mod.looks_like_challenge_page(
        "<html><title>Just a moment...</title><body>cf-browser-verification</body></html>"
    )
    assert not mod.looks_like_challenge_page(
        "<html><head><title>Software Engineer</title></head>"
        + ("<body><p>Role details here.</p>" * 20)
        + "</body></html>"
    )


def _run_with_fake_browser(html: str) -> str | None:
    fake_page = MagicMock()
    fake_page.content.return_value = html
    fake_context = MagicMock()
    fake_context.new_page.return_value = fake_page
    fake_browser = MagicMock()
    fake_browser.new_context.return_value = fake_context
    fake_chromium = MagicMock()
    fake_chromium.launch.return_value = fake_browser
    fake_p = MagicMock()
    fake_p.chromium = fake_chromium

    class _SP:
        def __enter__(self):
            return fake_p

        def __exit__(self, *args):
            return False

    fake_mod = types.ModuleType("playwright.sync_api")
    fake_mod.sync_playwright = lambda: _SP()  # type: ignore[attr-defined]
    parent = types.ModuleType("playwright")
    with patch.object(mod, "resolve_chromium_executable", return_value="/fake/chrome"):
        with patch.dict(
            "sys.modules",
            {"playwright": parent, "playwright.sync_api": fake_mod},
        ):
            return mod.fetch_html_playwright("https://example.com/job"), fake_browser


def test_fetch_html_playwright_returns_content() -> None:
    good = (
        "<html><head><title>Job</title></head>"
        + ("<body><div>description " * 40)
        + "</div></body></html>"
    )
    html, browser = _run_with_fake_browser(good)
    assert html == good
    browser.close.assert_called()


def test_fetch_rejects_challenge() -> None:
    bad = (
        "<html><title>Just a moment...</title>"
        "<body>cf-browser-verification xxxxx</body></html>"
    )
    html, _browser = _run_with_fake_browser(bad)
    assert html is None


if __name__ == "__main__":
    test_challenge_detection()
    test_fetch_html_playwright_returns_content()
    test_fetch_rejects_challenge()
    print("ok")
