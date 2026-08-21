#!/usr/bin/env python3
"""Cutshort (India) curated-startup job scraper — prefers JSON over HTML.

Cutshort lists curated startup roles. We hit its public jobs JSON at a polite
rate. No login, no CAPTCHA, low volume (see PLAYBOOK).

The live JSON endpoint/shape may change; ``normalize_jobs`` accepts the common
envelope keys and the fetch never raises. If the shape drifts the source
yields zero rows rather than crashing discovery.

Usage:
  python3 scrape_cutshort.py [--out PATH] [--max-pages N]

Writes a JSON array of listings (shared schema) to --out
(default: ../listings/<date>-cutshort.json).
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from india_scrape_common import (
    ROOT,
    SEARCH_TERMS,
    dedup_by_url,
    fetch_json,
    log,
    polite_sleep,
    write_listings,
)
from known_job_urls import filter_out_known_listings, load_skip_urls_file  # noqa: E402

SITE = "cutshort"
BASE = "https://cutshort.io"
SEARCH_API = f"{BASE}/api/v1/jobs/search"
REQUEST_DELAY_S = 1.5


def _records(data) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("data", "jobs", "results", "hits", "docs"):
        val = data.get(key)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)]
        if isinstance(val, dict):
            inner = val.get("jobs") or val.get("results") or val.get("hits")
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
    return []


def _job_url(job: dict) -> str:
    for key in ("url", "job_url", "public_url", "seo_url", "link", "permalink"):
        val = job.get(key)
        if val:
            return val if str(val).startswith("http") else f"{BASE}{val}"
    slug = job.get("slug") or job.get("seo_slug")
    jid = job.get("id") or job.get("_id") or job.get("job_id")
    if slug:
        return f"{BASE}/job/{slug}"
    if jid:
        return f"{BASE}/job/{jid}"
    return ""


def normalize_jobs(data, *, search_term: str = "") -> list[dict]:
    """Map a Cutshort jobs response to shared-shape listings."""
    out: list[dict] = []
    for job in _records(data):
        url = _job_url(job)
        if not url:
            continue
        title = job.get("title") or job.get("role") or job.get("position") or ""
        company = (
            job.get("company") or job.get("companyName")
            or job.get("company_name") or job.get("organization") or ""
        )
        if isinstance(company, dict):
            company = company.get("name") or company.get("display_name") or ""
        location = job.get("location") or job.get("city") or job.get("locations") or ""
        if isinstance(location, list):
            location = ", ".join(str(x) for x in location if x)
        desc = (
            job.get("description") or job.get("jobDescription")
            or job.get("summary") or ""
        )
        sal = job.get("salary") or job.get("ctc") or job.get("compensation")
        if sal:
            desc = f"{desc}\nSalary: {sal}".strip()
        posted = (
            job.get("postedDate") or job.get("date_posted")
            or job.get("createdAt") or job.get("created_at") or ""
        )
        if not title:
            continue
        out.append({
            "title": title,
            "company": company or "",
            "site": SITE,
            "job_url": url,
            "job_url_direct": url,
            "description": desc or "",
            "date_posted": str(posted)[:10] if posted else None,
            "job_type": "fulltime",
            "location": location or "India",
            "search_term": f"india:{SITE}:{search_term}" if search_term else f"india:{SITE}",
        })
    return out


def scrape(*, max_pages: int) -> list[dict]:
    listings: list[dict] = []
    for term in SEARCH_TERMS:
        for page in range(1, max_pages + 1):
            q = term.replace(" ", "%20")
            url = f"{SEARCH_API}?q={q}&page={page}&country=india"
            data = fetch_json(url)
            rows = normalize_jobs(data, search_term=term)
            if not rows:
                break
            listings.extend(rows)
            log(f"  got {len(rows)} results from {SITE}/{term} p{page}")
            polite_sleep(REQUEST_DELAY_S)
    return dedup_by_url(listings)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument(
        "--skip-urls", default=None,
        help="JSON array of URL keys to drop (jobs.json / blocked / prior listing)",
    )
    args = parser.parse_args()

    out_path = (
        Path(args.out) if args.out
        else ROOT / "listings" / f"{date.today().isoformat()}-cutshort.json"
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
