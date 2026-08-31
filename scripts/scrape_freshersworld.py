#!/usr/bin/env python3
"""Freshersworld (India) job scraper — public HTML search (no API key / login).

Parses ``.job-container`` cards / job detail links from keyword search pages.
No CAPTCHA solving; challenged pages contribute zero rows.

Usage:
  python3 scrape_freshersworld.py [--out PATH] [--max-pages N]
"""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

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

SITE = "freshersworld"
BASE = "https://www.freshersworld.com"
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
    # Large pages with job markup are usable even if analytics mention captcha.
    if ".job-container" in html or 'class="seo_title"' in html or "seo_title" in html:
        return False
    return "access denied" in low


def _title_from_url(url: str) -> str:
    # …/jobs/data-engineer-ii-in-bengaluru-for-… → "data engineer ii in bengaluru…"
    m = re.search(r"/jobs/([^/?#]+)", url)
    if not m:
        return ""
    return m.group(1).replace("-", " ").strip()


def parse_html(html: str, *, search_term: str = "") -> list[dict]:
    if not html or _looks_challenged(html):
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen: set[str] = set()

    cards = soup.select(".job-container") or soup.select(".seo_title")
    if cards:
        for card in cards:
            link = card.select_one('a[href*="/jobs/"]') if hasattr(card, "select_one") else None
            if card.name == "a":
                link = card
            if not link:
                # seo_title may wrap the title; look for sibling/parent apply link
                parent = card.find_parent(["div", "li", "article"]) if card.name != "div" else card
                link = parent.select_one('a[href*="/jobs/"]') if parent else None
            if not link:
                continue
            href = link.get("href") or ""
            url = _abs_url(href.split("?")[0])
            if not url or "/jobs/" not in url or url in seen:
                continue
            title = ""
            seo = card.select_one(".seo_title") if hasattr(card, "select_one") else None
            if seo:
                title = seo.get_text(strip=True)
            if not title:
                title = (card.get_text(strip=True) if card.name != "a" else "") or ""
            if not title or title.lower() in ("view & apply", "view", "apply"):
                title = _title_from_url(url)
            company_el = card.select_one(".company-name, .company_name, .company") if hasattr(card, "select_one") else None
            company = company_el.get_text(strip=True) if company_el else ""
            loc_el = card.select_one(".job-location, .location, .loc") if hasattr(card, "select_one") else None
            location = loc_el.get_text(strip=True) if loc_el else "India"
            seen.add(url)
            out.append({
                "title": title,
                "company": company,
                "site": SITE,
                "job_url": url,
                "job_url_direct": url,
                "description": "",
                "date_posted": None,
                "job_type": "fulltime",
                "location": location or "India",
                "search_term": f"india:{SITE}:{search_term}" if search_term else f"india:{SITE}",
            })
        return out

    # Fallback: any job detail anchors on the page.
    for a in soup.select('a[href*="/jobs/"]'):
        href = a.get("href") or ""
        if "jobsearch" in href or href.rstrip("/").endswith("/jobs"):
            continue
        url = _abs_url(href.split("?")[0])
        if not url or url in seen:
            continue
        title = a.get_text(strip=True)
        if not title or title.lower() in ("view & apply", "view", "apply"):
            title = _title_from_url(url)
        if not title or len(title) < 4:
            continue
        seen.add(url)
        out.append({
            "title": title,
            "company": "",
            "site": SITE,
            "job_url": url,
            "job_url_direct": url,
            "description": "",
            "date_posted": None,
            "job_type": "fulltime",
            "location": "India",
            "search_term": f"india:{SITE}:{search_term}" if search_term else f"india:{SITE}",
        })
    return out


def scrape(*, max_pages: int) -> list[dict]:
    listings: list[dict] = []
    headers = {"User-Agent": BROWSER_UA, "Accept-Language": "en-IN,en;q=0.9"}
    for term in SEARCH_TERMS:
        slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-") + "-jobs"
        for page in range(1, max_pages + 1):
            if page == 1:
                url = f"{BASE}/jobs/jobsearch/{slug}"
            else:
                url = f"{BASE}/jobs/jobsearch/{slug}?page={page}"
            html = fetch_html(url, headers=headers)
            if html is None:
                break
            if _looks_challenged(html):
                log(f"warn: {SITE} challenge page for {term!r} p{page} — skipping", err=True)
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
    parser.add_argument("--skip-urls", default=None)
    args = parser.parse_args()

    out_path = (
        Path(args.out) if args.out
        else ROOT / "listings" / f"{date.today().isoformat()}-freshersworld.json"
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
