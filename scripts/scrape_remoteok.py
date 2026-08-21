#!/usr/bin/env python3
"""RemoteOK public API scraper — https://remoteok.com/api (no auth).

RemoteOK exposes a JSON feed of remote jobs. Attribution per their API terms:
link back to the job URL on RemoteOK and mention RemoteOK as the source.

The untagged ``/api`` feed is recent-only. Tagged feeds (``?tag=ai`` etc.)
surface more of that tag; we union a small set of data/ML/software tags,
dedup by URL, then apply ``RELEVANT_KEYWORDS`` so designer/PM titles drop.
No recency query param — do not client-filter by date (would drop unseen older rows).

Usage:
  python3 scrape_remoteok.py [--out PATH]

Writes a JSON array of listings (same schema as scout.py / scrape_ats.py) to
--out (default: ../listings/<date>-remoteok.json).
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

# Reuse the ATS scraper's keyword list — dedup_listings applies the same filter
# downstream, but filtering here keeps volume sane on a broad remote feed.
from scrape_ats import RELEVANT_KEYWORDS, clean_html_content  # noqa: E402

SITE = "remoteok"
API_URL = "https://remoteok.com/api"
REQUEST_DELAY_S = 0.35
# Documented: ?tag=dev or ?tags=dev,python. One tag per request, then union.
TAGS = ("ai", "python", "data", "devops", "ml", "datascience")


def is_relevant(title: str) -> bool:
    t = (title or "").lower()
    return any(kw in t for kw in RELEVANT_KEYWORDS)


def tag_url(tag: str) -> str:
    return f"{API_URL}?{urlencode({'tag': tag})}"


def query_urls() -> list[str]:
    """Untagged latest feed plus tagged feeds (dedup later)."""
    return [API_URL, *[tag_url(t) for t in TAGS]]


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
        title = job.get("position") or ""
        if not is_relevant(title):
            continue
        url = job.get("url") or job.get("apply_url")
        if not url:
            continue
        company = job.get("company") or ""
        description = clean_html_content(job.get("description") or "")
        out.append({
            "title": title,
            "company": company,
            "site": SITE,
            "job_url": url,
            "job_url_direct": url,
            "description": description,
            "date_posted": _parse_date(job.get("date")),
            "job_type": "fulltime",
            "location": job.get("location"),
            "search_term": f"us:{SITE}",
        })
    return out


def scrape() -> list[dict]:
    raw: list = []
    urls = query_urls()
    for i, url in enumerate(urls):
        data = fetch_json(url)
        if isinstance(data, list):
            raw.extend(data)
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
        else ROOT / "listings" / f"{date.today().isoformat()}-remoteok.json"
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
