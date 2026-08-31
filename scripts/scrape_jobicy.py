#!/usr/bin/env python3
"""Jobicy public API scraper — https://jobicy.com/api/v2/remote-jobs (no auth).

Jobicy requires attribution: credit Jobicy with a direct link to the source job
URL (see friendlyNotice on the API response).

The public API returns at most ``count=100`` jobs per request and has **no
pagination** and **no recency/date filter**. Default (no count) is a tiny first page, so we always pass
count=100 and union relevant industry slugs (data-science, engineering, …)
so ML/data titles are not drowned by the latest mixed remotes. Title filter
(``RELEVANT_KEYWORDS``) still drops PM/design/marketing roles.

Usage:
  python3 scrape_jobicy.py [--out PATH]

Writes a JSON array of listings (same schema as scout.py / scrape_ats.py) to
--out (default: ../listings/<date>-jobicy.json).
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
    is_within_days,
    log,
    polite_sleep,
    write_listings,
)
from known_job_urls import filter_out_known_listings, load_skip_urls_file  # noqa: E402

from extract_job_posting import RELEVANT_KEYWORDS, clean_html_content  # noqa: E402

SITE = "jobicy"
# Recency window. These boards keep ads live for weeks, so the old
# hardcoded 10 days silently discarded most of what they returned
# (RemoteOK: 38 relevant roles found, 1 inside 10 days).
DEFAULT_MAX_DAYS = 21
_MAX_DAYS = DEFAULT_MAX_DAYS

API_URL = "https://jobicy.com/api/v2/remote-jobs"
# API max per request; there is no page= / offset=.
COUNT = 100
REQUEST_DELAY_S = 0.35
# Taxonomy slugs from GET ?get=industries (2026-08). Skip marketing/sales/design.
INDUSTRIES = (
    "data-science",
    "engineering",
    "admin",  # DevOps & Infrastructure
    "cybersecurity",
    "qa-testing",
)


def is_relevant(title: str) -> bool:
    t = (title or "").lower()
    return any(kw in t for kw in RELEVANT_KEYWORDS)


# The API has no paging, so breadth comes from querying independent axes and
# unioning the results. industry / geo / tag each return up to COUNT rows.
GEOS = ("usa", "canada", "europe", "uk", "germany", "india", "anywhere")
TAGS = ("python", "java", "javascript", "react", "aws", "sql", "machine-learning")


def api_query_url(*, industry: str | None = None, geo: str | None = None,
                  tag: str | None = None) -> str:
    params = {"count": str(COUNT)}
    if industry:
        params["industry"] = industry
    if geo:
        params["geo"] = geo
    if tag:
        params["tag"] = tag
    return f"{API_URL}?{urlencode(params)}"


def query_urls() -> list[str]:
    """Latest 100 plus industry / geo / tag feeds, unioned by URL."""
    return [
        api_query_url(),
        *[api_query_url(industry=i) for i in INDUSTRIES],
        *[api_query_url(geo=g) for g in GEOS],
        *[api_query_url(tag=t) for t in TAGS],
    ]


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
        title = job.get("jobTitle") or ""
        if not is_relevant(title):
            continue
        url = job.get("url")
        if not url:
            continue
        posted = _parse_date(job.get("pubDate"))
        if posted and not is_within_days(posted, max_days=_MAX_DAYS):
            continue
        company = job.get("companyName") or ""
        description = clean_html_content(
            job.get("jobDescription") or job.get("jobExcerpt") or ""
        )
        job_type_raw = job.get("jobType")
        if isinstance(job_type_raw, list):
            job_type = (job_type_raw[0] or "fulltime").lower().replace("-", "")
        else:
            job_type = str(job_type_raw or "fulltime").lower().replace("-", "")
        out.append({
            "title": title,
            "company": company,
            "site": SITE,
            "job_url": url,
            "job_url_direct": url,
            "description": description,
            "date_posted": posted,
            "job_type": job_type,
            "location": job.get("jobGeo"),
            "search_term": f"ww:{SITE}",
        })
    return out


def _rows_from_payload(data, *, log_notice: bool = False) -> list:
    if not isinstance(data, dict):
        return []
    if log_notice:
        notice = data.get("friendlyNotice")
        if isinstance(notice, str) and notice.strip():
            log(f"  Jobicy API notice: {notice.strip()}")
    rows = data.get("jobs") or []
    return rows if isinstance(rows, list) else []


def scrape() -> list[dict]:
    raw: list = []
    urls = query_urls()
    for i, url in enumerate(urls):
        data = fetch_json(url)
        raw.extend(_rows_from_payload(data, log_notice=(i == 0)))
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
    parser.add_argument(
        "--max-days", type=int, default=DEFAULT_MAX_DAYS,
        help=f"Recency window in days (default {DEFAULT_MAX_DAYS})")
    args = parser.parse_args()

    global _MAX_DAYS
    _MAX_DAYS = max(1, int(args.max_days))

    out_path = (
        Path(args.out) if args.out
        else ROOT / "listings" / f"{date.today().isoformat()}-jobicy.json"
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
