#!/usr/bin/env python3
"""Unit tests for browser_hygiene (Ashby spam / session hygiene)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from browser_hygiene import (  # noqa: E402
    ASHBY_SPAM_USER_GUIDANCE,
    clear_apply_site_storage,
    detect_ashby_spam_text,
    hosts_for_apply_url,
    is_ashby_url,
)


def test_detect_ashby_spam_text():
    assert detect_ashby_spam_text(
        "We couldn't submit your application. Your application submission was flagged as possible spam."
    )
    assert not detect_ashby_spam_text("Apply for Analytics Engineer")


def test_hosts_for_apply_url_ashby():
    hosts = hosts_for_apply_url("https://jobs.ashbyhq.com/onepay/abc-123/application")
    assert "jobs.ashbyhq.com" in hosts
    assert any("onepay" in h or "ashbyhq" in h for h in hosts)


def test_is_ashby_url():
    assert is_ashby_url("https://jobs.ashbyhq.com/acme/uuid/application")
    assert not is_ashby_url("https://boards.greenhouse.io/acme/jobs/1")


async def _test_detect_ashby_spam_async():
    from browser_hygiene import detect_ashby_spam

    page = AsyncMock()
    page.evaluate = AsyncMock(
        return_value="flagged as possible spam on submit"
    )
    page.title = AsyncMock(return_value="Application")
    assert await detect_ashby_spam(page) is True


def test_detect_ashby_spam_page():
    import asyncio

    asyncio.run(_test_detect_ashby_spam_async())


def test_guidance_nonempty():
    assert "incognito" in ASHBY_SPAM_USER_GUIDANCE.lower()
    assert "fresh" in ASHBY_SPAM_USER_GUIDANCE.lower()


async def _test_clear_apply_site_storage_async():
    from unittest.mock import AsyncMock, MagicMock

    page = AsyncMock()
    page.url = "https://jobs.ashbyhq.com/acme/uuid/application"
    page.evaluate = AsyncMock(return_value=True)
    ctx = MagicMock()
    ctx.cookies = AsyncMock(
        return_value=[
            {"domain": "jobs.ashbyhq.com", "name": "a"},
            {"domain": "example.com", "name": "b"},
        ]
    )
    ctx.clear_cookies = AsyncMock()
    ctx.add_cookies = AsyncMock()
    page.context = ctx
    out = await clear_apply_site_storage(page, url=page.url, context=ctx)
    assert "jobs.ashbyhq.com" in out["hosts"]
    assert out["cookies_cleared"] >= 1
    assert out["storage_cleared"] is True
    ctx.clear_cookies.assert_awaited()


def test_clear_apply_site_storage():
    import asyncio

    asyncio.run(_test_clear_apply_site_storage_async())


if __name__ == "__main__":
    test_detect_ashby_spam_text()
    test_hosts_for_apply_url_ashby()
    test_is_ashby_url()
    test_detect_ashby_spam_page()
    test_guidance_nonempty()
    test_clear_apply_site_storage()
    print("test_browser_hygiene: OK")
