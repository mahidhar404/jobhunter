#!/usr/bin/env python3
"""Shine (India) job scraper — public HTML search pages (no API key / login).

Parses structured ``/jobs/<slug>/<company>/<id>`` anchors from Shine search
pages. No CAPTCHA solving; if a page looks challenged, that page contributes
zero rows and the run continues (see PLAYBOOK).

Usage:
  python3 scrape_shine.py [--out PATH] [--max-pages N]
"""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from india_scrape_common import (
    ROOT,
    SEARCH_TERMS,
    dedup_by_url,
    fetch_html,
    is_within_days,
    log,
    polite_sleep,
    write_listings,
)
from known_job_urls import filter_out_known_listings, load_skip_urls_file  # noqa: E402

SITE = "shine"
BASE = "https://www.shine.com"
JOB_HREF_RE = re.compile(r"/jobs/([^/]+)/([^/]+)/(\d+)", re.I)
REQUEST_DELAY_S = 1.0
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    return f"{BASE}{href}" if href.startswith("/") else f"{BASE}/{href}"


def _looks_challenged(html: str) -> bool:
    """True only for hard blocks — ignore incidental 'captcha' strings in JS."""
    if not html:
        return True
    if len(html) < 2000:
        low = html.lower()
        return any(
            token in low
            for token in ("captcha", "access denied", "akamai", "px-captcha", "bot detection")
        )
    low = html.lower()
    if "access denied" in low or "px-captcha" in low:
        return True
    if "/jobs/" in html and "shine.com" in html:
        return False
    return False


def parse_html(html: str, *, search_term: str = "") -> list[dict]:
    """Parse Shine search HTML into shared-shape listings."""
    if not html or _looks_challenged(html):
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen: set[str] = set()
    for a in soup.select('a[href*="/jobs/"]'):
        href = a.get("href") or ""
        m = JOB_HREF_RE.search(href)
        if not m:
            continue
        url = _abs_url(href.split("?")[0])
        if not url or url in seen:
            continue
        title = (a.get_text(strip=True) or "").strip()
        location = "India"
        salary = ""
        parent = a.find_parent(["div", "li", "article", "section"])
        if parent:
            if not title or title.lower() in ("view", "apply", "view & apply"):
                heading = parent.find(["h2", "h3", "h4"])
                if heading and heading.get_text(strip=True):
                    title = heading.get_text(strip=True)
            loc_el = parent.select_one("[class*='Loc'], [class*='location'], .jobCard_jobLoc__v_pKV")
            if loc_el and loc_el.get_text(strip=True):
                location = loc_el.get_text(strip=True)
            sal_el = parent.select_one("[class*='Sal'], [class*='salary']")
            if sal_el and sal_el.get_text(strip=True):
                salary = sal_el.get_text(strip=True)
        if not title or len(title) < 3:
            title = m.group(1).replace("-", " ").strip()
        company = m.group(2).replace("-", " ").strip()
        if company.lower() in ("premium", "jobs"):
            company = ""
        seen.add(url)
        desc = f"Salary: {salary}" if salary else ""
        out.append({
            "title": title,
            "company": company,
            "site": SITE,
            "job_url": url,
            "job_url_direct": url,
            "description": desc,
            "date_posted": None,
            "job_type": "fulltime",
            "location": location,
            "search_term": f"india:{SITE}:{search_term}" if search_term else f"india:{SITE}",
        })
    return out


def scrape(*, max_pages: int = 3) -> list[dict]:
    listings: list[dict] = []
    headers = {"User-Agent": BROWSER_UA, "Accept-Language": "en-IN,en;q=0.9"}
    for term in SEARCH_TERMS:
        path_slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-") + "-jobs"
        for page in range(1, max_pages + 1):
            if page == 1:
                url = f"{BASE}/job-search/{path_slug}"
            else:
                url = f"{BASE}/job-search/{path_slug}?page={page}"
            html = fetch_html(url, headers=headers)
            if html is None:
                break
            if _looks_challenged(html):
                log(f"warn: {SITE} challenge page for {term!r} p{page} — skipping page", err=True)
                break
            rows = parse_html(html, search_term=term)
            if not rows:
                break
            listings.extend(rows)
            log(f"  got {len(rows)} results from {SITE}/{term} p{page}")
            polite_sleep(REQUEST_DELAY_S)
    return dedup_by_url(listings)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument(
        "--skip-urls", default=None,
        help="JSON array of URL keys to drop (jobs.json / blocked / prior listing)",
    )
    args = parser.parse_args()

    out_path = (
        Path(args.out) if args.out
        else ROOT / "listings" / f"{date.today().isoformat()}-shine.json"
    )
    skip_keys = load_skip_urls_file(Path(args.skip_urls) if args.skip_urls else None)
    if skip_keys:
        log(f"skip-urls: {len(skip_keys)} known key(s)")
    listings = scrape(max_pages=max(1, args.max_pages))
    listings, skipped = filter_out_known_listings(listings, skip_keys)
    if skipped:
        log(f"skipped {skipped} already-known URL(s)")
    write_listings(out_path, listings)
    log(f"wrote {len(listings)} listings -> {out_path}")


if __name__ == "__main__":
    main()
