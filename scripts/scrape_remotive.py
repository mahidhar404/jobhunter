#!/usr/bin/env python3
"""Remotive public API scraper — https://remotive.com/api/remote-jobs (no auth).

Remotive requires attribution: link back to the job URL on Remotive and mention
Remotive as the source (see API legal notice on the endpoint).

Query params (official): category, company_name, search, limit (omit limit =
all matching). No date/max_days param — keep the full category dump. We fetch relevant categories (software-development, data, AI,
devops, …) with no limit, union them, then apply ``RELEVANT_KEYWORDS`` on
title so PM/design/marketing still drop.

Usage:
  python3 scrape_remotive.py [--out PATH]

Writes a JSON array of listings (same schema as scout.py / scrape_ats.py) to
--out (default: ../listings/<date>-remotive.json).
"""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from india_scrape_common import (
    ROOT,
    dedup_by_url,
    fetch_json,
    log,
    polite_sleep,
    write_listings,
)
from known_job_urls import filter_out_known_listings, load_skip_urls_file  # noqa: E402

from scrape_ats import RELEVANT_KEYWORDS, clean_html_content  # noqa: E402

SITE = "remotive"
API_URL = "https://remotive.com/api/remote-jobs"
ATTRIBUTION = (
    "Remotive API: link jobs to remotive.com and credit Remotive as the source."
)
REQUEST_DELAY_S = 0.35
# Live slugs from GET /api/remote-jobs/categories (not the older software-dev docs).
CATEGORIES = (
    "software-development",
    "data",
    "devops",
    "artificial-intelligence",
    "engineering",
    "information-technology",
    "research",
    "qa",
)


def is_relevant(title: str) -> bool:
    t = (title or "").lower()
    return any(kw in t for kw in RELEVANT_KEYWORDS)


def category_url(category: str) -> str:
    return f"{API_URL}?{urlencode({'category': category})}"


def query_urls() -> list[str]:
    """All jobs (no limit) plus relevant category feeds if the full dump is truncated."""
    return [API_URL, *[category_url(cat) for cat in CATEGORIES]]


def _parse_date(raw: str | None) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else None


def normalize_jobs(rows: list) -> list[dict]:
    out: list[dict] = []
    for job in rows:
        if not isinstance(job, dict) or not job.get("id"):
            continue
        title = job.get("title") or ""
        if not is_relevant(title):
            continue
        url = job.get("url")
        if not url:
            continue
        company = job.get("company_name") or ""
        description = clean_html_content(job.get("description") or "")
        out.append({
            "title": title,
            "company": company,
            "site": SITE,
            "job_url": url,
            "job_url_direct": url,
            "description": description,
            "date_posted": _parse_date(job.get("publication_date")),
            "job_type": (job.get("job_type") or "fulltime").replace("_", ""),
            "location": job.get("candidate_required_location"),
            "search_term": f"us:{SITE}",
        })
    return out


def scrape() -> list[dict]:
    log(f"  {ATTRIBUTION}")
    raw: list = []
    urls = query_urls()
    for i, url in enumerate(urls):
        data = fetch_json(url)
        if isinstance(data, dict):
            rows = data.get("jobs") or []
            if isinstance(rows, list):
                raw.extend(rows)
        if i < len(urls) - 1:
            polite_sleep(REQUEST_DELAY_S)
    jobs = dedup_by_url(normalize_jobs(raw))
    log(f"  got {len(jobs)} relevant results from {SITE}/api")
    return jobs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--skip-urls", default=None,
        help="JSON array of URL keys to drop (jobs.json / blocked / prior listing)",
    )
    args = parser.parse_args()

    out_path = (
        Path(args.out) if args.out
        else ROOT / "listings" / f"{date.today().isoformat()}-remotive.json"
    )

    skip_keys = load_skip_urls_file(Path(args.skip_urls) if args.skip_urls else None)
    if skip_keys:
        log(f"skip-urls: {len(skip_keys)} known key(s)")
    listings = scrape()
    listings, skipped = filter_out_known_listings(listings, skip_keys)
    if skipped:
        log(f"skipped {skipped} already-known URL(s)")
    write_listings(out_path, listings)
    log(f"wrote {len(listings)} listings -> {out_path}")


if __name__ == "__main__":
    main()
