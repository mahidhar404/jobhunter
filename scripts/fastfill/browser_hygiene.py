"""Browser session hygiene for ATS anti-spam (Ashby).

Playwright fills use a fresh ``BrowserContext`` per run (no persistent
user-data-dir), but Ashby can still flag a session during automation and
persist the flag in cookies / web storage until the context is discarded.
Incognito works because it starts with a clean profile.

This module:
  - clears apply-host cookies + storage at Ashby fill start (retry hygiene)
  - strips the fill overlay / obvious automation markers before manual submit
  - detects Ashby's "possible spam" banner for dashboard blockers
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

ASHBY_SPAM_NEEDLES = (
    "flagged as possible spam",
    "possible spam",
    "couldn't submit your application",
    "could not submit your application",
    "we couldn't submit your application",
)

ASHBY_APPLY_HOST_SUFFIXES = (
    "ashbyhq.com",
    "ashby.com",
)

# Guidance surfaced in reports / dashboard status_detail.
ASHBY_SPAM_USER_GUIDANCE = (
    "Ashby flagged this browser session as possible spam. "
    "Close the fill window, click Start again (fresh session), or submit from "
    "a normal Chrome incognito window with the same apply URL. "
    "Reloading the page in the same window usually does not clear the flag."
)


def hosts_for_apply_url(url: str) -> list[str]:
    """Hostnames whose cookies/storage should be cleared for *url*."""
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
    except Exception:
        return []
    if not host:
        return []
    hosts = [host]
    if host.startswith("www."):
        hosts.append(host[4:])
    for suffix in ASHBY_APPLY_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            if "jobs.ashbyhq.com" not in hosts:
                hosts.append("jobs.ashbyhq.com")
            if "app.ashbyhq.com" not in hosts:
                hosts.append("app.ashbyhq.com")
            break
    # Stable order, dedupe.
    seen: set[str] = set()
    out: list[str] = []
    for h in hosts:
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out


def is_ashby_url(url: str) -> bool:
    low = (url or "").lower()
    return any(s in low for s in ("ashbyhq.com", ".ashby.com/"))


def detect_ashby_spam_text(text: str) -> bool:
    blob = (text or "").lower()
    return any(n in blob for n in ASHBY_SPAM_NEEDLES)


async def detect_ashby_spam(page) -> bool:
    """True when Ashby spam / submit-block banner is visible in page text."""
    if page is None:
        return False
    try:
        body = await page.evaluate(
            "() => (document.body && document.body.innerText || '').slice(0, 8000)"
        )
        title = await page.title()
        return detect_ashby_spam_text(f"{title}\n{body}")
    except Exception:
        return False


async def clear_apply_site_storage(
    page,
    *,
    url: str | None = None,
    context=None,
) -> dict[str, Any]:
    """Clear cookies + local/session storage for apply host(s).

    Safe on a fresh context (no-op cookies) and on retry when Ashby persisted
    a spam flag in the same browser session.
    """
    out: dict[str, Any] = {"hosts": [], "cookies_cleared": 0, "storage_cleared": False}
    page_url = url or getattr(page, "url", "") or ""
    hosts = hosts_for_apply_url(page_url)
    out["hosts"] = hosts
    if not hosts:
        return out

    ctx = context
    if ctx is None and page is not None:
        try:
            ctx = page.context
        except Exception:
            ctx = None

    if ctx is not None:
        try:
            cookies = await ctx.cookies()
            keep = [c for c in cookies if (c.get("domain") or "").lstrip(".").lower() not in hosts and not any(
                (c.get("domain") or "").lstrip(".").lower().endswith(h) for h in hosts
            )]
            removed = len(cookies) - len(keep)
            await ctx.clear_cookies()
            if keep:
                await ctx.add_cookies(keep)
            out["cookies_cleared"] = max(0, removed)
        except Exception as e:
            out["cookie_error"] = str(e)[:120]

    if page is not None:
        try:
            await page.evaluate(
                """() => {
                  try { localStorage.clear(); } catch (_) {}
                  try { sessionStorage.clear(); } catch (_) {}
                  return true;
                }"""
            )
            out["storage_cleared"] = True
        except Exception as e:
            out["storage_error"] = str(e)[:120]

    return out


async def prepare_browser_for_human_submit(page) -> dict[str, Any]:
    """Remove fill overlay and soften obvious automation markers before manual submit."""
    out: dict[str, Any] = {"overlay_detached": False}
    if page is None:
        return out
    try:
        from fill_pause import detach_fill_pause_overlay

        out["overlay_detached"] = bool(await detach_fill_pause_overlay(page))
    except Exception as e:
        out["overlay_error"] = str(e)[:120]

    try:
        out["automation_scrub"] = await page.evaluate(
            """() => {
              const touched = [];
              try {
                if (navigator.webdriver) {
                  try {
                    Object.defineProperty(navigator, 'webdriver', {
                      get: () => undefined,
                      configurable: true,
                    });
                    touched.push('webdriver');
                  } catch (_) {}
                }
              } catch (_) {}
              for (const k of Object.keys(window)) {
                if (/^__pw/i.test(k) || /^__playwright/i.test(k)) {
                  try { delete window[k]; touched.push(k); } catch (_) {}
                }
              }
              return touched;
            }"""
        )
    except Exception as e:
        out["scrub_error"] = str(e)[:120]

    return out


async def note_ashby_spam_blocker(page, report: dict | None) -> bool:
    """If spam banner is present, set ``report['blocker']`` and guidance."""
    if not await detect_ashby_spam(page):
        return False
    if report is not None:
        report["blocker"] = "ashby_spam_flagged"
        report["ashby_spam_flagged"] = True
        report["ashby_spam_guidance"] = ASHBY_SPAM_USER_GUIDANCE
        report["ready_for_review"] = False
        report.pop("hold_incomplete", None)
    return True


def chromium_launch_hygiene_kwargs() -> dict[str, Any]:
    """Extra Playwright ``chromium.launch`` kwargs to reduce automation fingerprint."""
    return {
        "args": [
            "--disable-blink-features=AutomationControlled",
        ],
        "ignore_default_args": ["--enable-automation"],
    }
