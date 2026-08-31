#!/usr/bin/env python3
"""Naukri (India) scraper via headed Chrome DOM — no CAPTCHA solve.

Plain HTTP / JobSpy / headless Chromium hit Akamai or ``recaptcha required``.
Headed Chrome with a persistent profile (`india_boards_chrome_profile`) can
load public search HTML. We paginate keyword+city search URLs and parse
``a[href*="job-listings"]`` cards.

Usage:
  python3 scrape_naukri.py [--out PATH] [--max-pages N] [--headed/--headless]
"""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

from india_boards_browser import launch_india_boards_context
from india_scrape_common import (
    ROOT,
    SEARCH_TERMS,
    dedup_by_url,
    is_within_days,
    log,
    polite_sleep,
    write_listings,
)
from known_job_urls import filter_out_known_listings, load_skip_urls_file  # noqa: E402

SITE = "naukri"
REQUEST_DELAY_S = 1.2
CITIES = (
    "bangalore",
    "hyderabad",
    "mumbai",
    "pune",
    "delhi",
)


def _slug(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")


def _search_url(term: str, city: str, page: int) -> str:
    base = f"https://www.naukri.com/{_slug(term)}-jobs-in-{city}"
    if page <= 1:
        return base
    return f"{base}-{page}"


def _looks_denied(title: str, html: str) -> bool:
    t = (title or "").lower()
    if "access denied" in t:
        return True
    if len(html or "") < 2000 and "access denied" in (html or "").lower():
        return True
    return False


def _extract_jobs(page, *, search_term: str) -> list[dict]:
    rows = page.evaluate(
        """() => {
      const out = [];
      const seen = new Set();
      for (const a of document.querySelectorAll('a[href*="job-listings"]')) {
        const href = (a.href || '').split('?')[0];
        if (!href || seen.has(href)) continue;
        seen.add(href);
        const root = a.closest('article, .srp-jobtuple-wrapper, .cust-job-tuple, .row') || a.parentElement;
        let title = (a.innerText || '').trim();
        if (!title && root) {
          const t = root.querySelector('a.title, .title, .jobTupleHeader a');
          if (t) title = (t.innerText || '').trim();
        }
        let company = '';
        let location = '';
        let salary = '';
        let posted = '';
        if (root) {
          const c = root.querySelector('.comp-name, .companyInfo a, a.comp-name, .company-name');
          if (c) company = (c.innerText || '').trim();
          const loc = root.querySelector('.locWdth, .location, span.loc, .loc-wrap');
          if (loc) location = (loc.innerText || '').trim();
          const sal = root.querySelector('.sal-wrap, .salary, .sal');
          if (sal) salary = (sal.innerText || '').trim();
          const postEl = root.querySelector('.job-post-day, .day, .time-wrap');
          if (postEl) posted = (postEl.innerText || '').trim();
        }
        if (title && href) {
          out.push({ title: title.slice(0, 240), company, location, salary, posted, url: href });
        }
      }
      return out;
    }"""
    )
    out: list[dict] = []
    for row in rows or []:
        url = row.get("url") or ""
        title = row.get("title") or ""
        if not url or not title:
            continue
        sal = row.get("salary") or ""
        desc = f"Salary: {sal}" if sal else ""
        out.append({
            "title": title,
            "company": row.get("company") or "",
            "site": SITE,
            "job_url": url,
            "job_url_direct": url,
            "description": desc,
            "date_posted": None,
            "job_type": "fulltime",
            "location": (row.get("location") or "India").replace("\n", ", "),
            "search_term": f"india:{SITE}:{search_term}",
        })
    return out


def _browser_is_gone(exc: Exception) -> bool:
    """True when Playwright says the page/context/browser is closed."""
    msg = str(exc).lower()
    return (
        "has been closed" in msg
        or "target closed" in msg
        or "browser closed" in msg
        or "connection closed" in msg
    )


def scrape(*, max_pages: int, headless: bool) -> list[dict]:
    listings: list[dict] = []
    pw = ctx = page = None
    relaunched = False
    try:
        pw, ctx, page = launch_india_boards_context(headless=headless)
        for term in SEARCH_TERMS:
            for city in CITIES:
                empty_pages = 0
                for page_no in range(1, max_pages + 1):
                    url = _search_url(term, city, page_no)
                    log(f"scraping {SITE}: {term} / {city} p{page_no}")
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(3500)
                    except Exception as exc:  # noqa: BLE001
                        log(f"warn: {SITE} navigation failed: {exc}", err=True)
                        # A closed target means Chrome itself died (crash, or the
                        # user quit the window). Every later goto would fail the
                        # same way, so try one relaunch and give up if that fails
                        # rather than logging the same error for every remaining
                        # term x city.
                        if _browser_is_gone(exc):
                            try:
                                if ctx is not None:
                                    ctx.close()
                            except Exception:
                                pass
                            try:
                                if pw is not None:
                                    pw.stop()
                            except Exception:
                                pass
                            if relaunched:
                                log(f"disabled/skipped ({SITE}): browser died twice — "
                                    "keeping what we have", err=True)
                                return dedup_by_url(listings)
                            relaunched = True
                            log(f"{SITE}: browser died — relaunching once")
                            try:
                                pw, ctx, page = launch_india_boards_context(
                                    headless=headless)
                            except Exception as relaunch_exc:  # noqa: BLE001
                                log(f"disabled/skipped ({SITE}): relaunch failed: "
                                    f"{relaunch_exc}", err=True)
                                return dedup_by_url(listings)
                            continue
                        break
                    title = page.title()
                    html = page.content()
                    if _looks_denied(title, html):
                        log(
                            f"disabled/skipped ({SITE}): Access Denied / bot wall "
                            f"on {url} — open headed Chrome once or retry later "
                            "(never CAPTCHA-solve)",
                            err=True,
                        )
                        return dedup_by_url(listings)
                    if "captcha" in (html or "").lower() and "job-listings" not in html:
                        log(
                            f"disabled/skipped ({SITE}): CAPTCHA wall on {url} — "
                            "solve it manually in india_boards_chrome_profile if needed",
                            err=True,
                        )
                        return dedup_by_url(listings)
                    rows = _extract_jobs(page, search_term=f"{term}@{city}")
                    if not rows:
                        empty_pages += 1
                        if empty_pages >= 1:
                            break
                        continue
                    empty_pages = 0
                    listings.extend(rows)
                    log(f"  got {len(rows)} results from {SITE}/{term}/{city} p{page_no}")
                    polite_sleep(REQUEST_DELAY_S)
    finally:
        try:
            if ctx is not None:
                ctx.close()
        except Exception:
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass
    return dedup_by_url(listings)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--max-pages", type=int, default=3,
                        help="Pages per keyword×city (default 3)")
    parser.add_argument("--headless", action="store_true",
                        help="Try headless (often Access Denied on Naukri)")
    parser.add_argument("--headed", action="store_true", default=True)
    parser.add_argument("--skip-urls", default=None)
    args = parser.parse_args()

    out_path = (
        Path(args.out) if args.out
        else ROOT / "listings" / f"{date.today().isoformat()}-naukri.json"
    )
    skip_keys = load_skip_urls_file(Path(args.skip_urls) if args.skip_urls else None)
    if skip_keys:
        log(f"skip-urls: {len(skip_keys)} known key(s)")
    headless = bool(args.headless)
    listings = scrape(max_pages=max(1, args.max_pages), headless=headless)
    listings, skipped = filter_out_known_listings(listings, skip_keys)
    if skipped:
        log(f"skipped {skipped} already-known URL(s)")
    write_listings(out_path, listings)
    log(f"wrote {len(listings)} listings -> {out_path}")


if __name__ == "__main__":
    main()
