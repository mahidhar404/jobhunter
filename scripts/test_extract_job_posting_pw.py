#!/usr/bin/env python3
"""Tests for extract_job_posting Playwright tier (mocked)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_job_posting as ejp  # noqa: E402


def test_playwright_tier_after_thin_http() -> None:
    thin = "<html><body><nav>Home Careers</nav></body></html>"
    rich = (
        "<html><head><title>ML Engineer at Acme</title></head><body>"
        + ("<p>We are hiring an ML engineer with deep learning experience. " * 30)
        + "</p></body></html>"
    )
    with patch.object(ejp, "fetch_html", return_value=thin):
        with patch.object(ejp, "KNOWN_ATS_TRIERS", []):
            with patch(
                "pw_fetch_html.fetch_html_playwright", return_value=rich
            ) as pw:
                result = ejp.extract(
                    "https://careers.example.com/jobs/ml-1",
                    allow_playwright=True,
                )
    assert result is not None
    assert result.get("description")
    assert len(result["description"]) >= ejp.MIN_DESCRIPTION_CHARS
    pw.assert_called_once()


def test_unreachable_skips_playwright() -> None:
    with patch("pw_fetch_html.fetch_html_playwright") as pw:
        result = ejp.extract("https://company.myworkdayjobs.com/en-US/job/1")
    assert result is None
    pw.assert_not_called()


def test_allow_playwright_false() -> None:
    with patch.object(ejp, "fetch_html", return_value=None):
        with patch.object(ejp, "KNOWN_ATS_TRIERS", []):
            with patch("pw_fetch_html.fetch_html_playwright") as pw:
                result = ejp.extract(
                    "https://careers.example.com/jobs/x",
                    allow_playwright=False,
                )
    assert result is None
    pw.assert_not_called()


if __name__ == "__main__":
    test_playwright_tier_after_thin_http()
    test_unreachable_skips_playwright()
    test_allow_playwright_false()
    print("ok")
