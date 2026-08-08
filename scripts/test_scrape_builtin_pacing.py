#!/usr/bin/env python3
"""Unit tests for Built In adaptive pacing + 429→Playwright fallback."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scrape_builtin as sb  # noqa: E402


def setup_function() -> None:
    sb._current_delay_s = sb.FETCH_DELAY_S
    sb._success_streak = 0
    sb._pw_fallback_hits = 0


def test_bump_delay_after_429() -> None:
    setup_function()
    before = sb._current_delay_s
    sb._bump_delay_after_429()
    assert sb._current_delay_s > before
    assert sb._current_delay_s <= sb.DELAY_CAP_S
    assert sb._success_streak == 0


def test_decay_after_success_streak() -> None:
    setup_function()
    sb._current_delay_s = 4.0
    for _ in range(sb.SUCCESS_STREAK_TO_DECAY):
        sb._note_fetch_success()
    assert sb._current_delay_s < 4.0
    assert sb._current_delay_s >= sb.FETCH_DELAY_S


def test_fetch_html_playwright_after_429() -> None:
    setup_function()
    good = "<html>" + ("job body " * 50) + "</html>"

    err = HTTPError("https://builtin.com/job/x", 429, "Too Many", hdrs=None, fp=None)

    with patch("scrape_builtin.urlopen", side_effect=err):
        with patch("scrape_builtin.time.sleep"):
            with patch(
                "pw_fetch_html.fetch_html_playwright", return_value=good
            ) as pw:
                html = sb.fetch_html("https://builtin.com/job/x")
    assert html == good
    assert sb._pw_fallback_hits == 1
    assert pw.call_count == 1


def test_fetch_html_no_pw_on_404() -> None:
    setup_function()
    err = HTTPError("https://builtin.com/job/x", 404, "Missing", hdrs=None, fp=None)
    with patch("scrape_builtin.urlopen", side_effect=err):
        with patch("pw_fetch_html.fetch_html_playwright") as pw:
            html = sb.fetch_html("https://builtin.com/job/gone")
    assert html is None
    pw.assert_not_called()


if __name__ == "__main__":
    test_bump_delay_after_429()
    test_decay_after_success_streak()
    test_fetch_html_playwright_after_429()
    test_fetch_html_no_pw_on_404()
    print("ok")
