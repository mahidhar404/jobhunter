#!/usr/bin/env python3
"""Internshala (India) job scraper — software / data categories.

Internshala is the easiest India HTML source (early-career + tech roles). We
fetch its public keyword job-search pages at a polite rate and parse the
server-rendered job cards. No login, no CAPTCHA, low volume (see PLAYBOOK).

Live DOM selectors on internshala.com can change; the parse is deliberately
defensive (multiple selector fallbacks) and the fetch never raises. If the
markup drifts the source yields zero rows rather than crashing discovery —
re-check selectors against a saved page if counts drop to zero unexpectedly.

Usage:
  python3 scrape_internshala.py [--out PATH] [--max-pages N]

Writes a JSON array of listings (shared schema) to --out
(default: ../listings/<date>-internshala.json).
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from india_scrape_common import (
    ROOT,
    dedup_by_url,
    fetch_html,
    log,
    polite_sleep,
    write_listings,
)

SITE = "internshala"
BASE = "https://internshala.com"
# Category keyword slugs Internshala understands in /jobs/keywords-<slug>/.
CATEGORY_SLUGS = [
    "data-science",
    "machine-learning",
    "data-engineering",
    "data-analytics",
    "software-development",
    "python-development",
    "backend-development",
    "artificial-intelligence",
]
REQUEST_DELAY_S = 1.5


def _first_text(node, selectors: list[str]) -> str:
    for sel in selectors:
        el = node.select_one(sel)
        if el and el.get_text(strip=True):
            return el.get_text(strip=True)
    return ""


def _abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    return f"{BASE}{href}" if href.startswith("/") else f"{BASE}/{href}"


def parse_html(html: str, *, search_term: str = "") -> list[dict]:
    """Parse an Internshala job-search page into shared-shape listings.

    Cards live in ``div.individual_internship``; title/company/location use a
    few selector fallbacks so a minor class rename doesn't zero the source.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    cards = soup.select("div.individual_internship") or soup.select("[data-href*='/job/detail']")
    for card in cards:
        title = _first_text(card, [
            ".job-internship-name", ".job-title-href", "h3.job-internship-name",
            ".profile", "h3",
        ])
        company = _first_text(card, [
            ".company-name", "p.company-name", ".company_name", ".company h4",
        ])
        location = _first_text(card, [
            ".locations a", ".location_link", ".locations span", ".row-1-item .location_link",
        ])
        # Detail link: an anchor to /job/detail/... or the card's data-href.
        href = ""
        link = card.select_one("a.job-title-href") or card.select_one("a[href*='/job/detail']")
        if link and link.get("href"):
            href = link["href"]
        if not href:
            href = card.get("data-href") or ""
        url = _abs_url(href)
        if not (title and url):
            continue
        out.append({
            "title": title,
            "company": company or "",
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


def scrape(*, max_pages: int) -> list[dict]:
    listings: list[dict] = []
    for slug in CATEGORY_SLUGS:
        for page in range(1, max_pages + 1):
            url = f"{BASE}/jobs/keywords-{slug}/page-{page}/" if page > 1 \
                else f"{BASE}/jobs/keywords-{slug}/"
            html = fetch_html(url)
            rows = parse_html(html or "", search_term=slug.replace("-", " "))
            if not rows:
                break
            listings.extend(rows)
            log(f"  got {len(rows)} results from {SITE}/{slug} p{page}")
            polite_sleep(REQUEST_DELAY_S)
    return dedup_by_url(listings)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--max-pages", type=int, default=2)
    args = parser.parse_args()

    out_path = (
        Path(args.out) if args.out
        else ROOT / "listings" / f"{date.today().isoformat()}-internshala.json"
    )
    listings = scrape(max_pages=max(1, args.max_pages))
    write_listings(out_path, listings)
    log(f"wrote {len(listings)} listings -> {out_path}")


if __name__ == "__main__":
    main()
