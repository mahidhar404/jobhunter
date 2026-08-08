#!/usr/bin/env python3
"""Hirist (India) niche-tech job scraper — prefers the site's JSON/XHR feed.

Hirist is a tech-focused India board (LPA + skills in each posting). We hit
its public search JSON at a polite rate rather than parsing HTML. No login,
no CAPTCHA, low volume (see PLAYBOOK).

The live JSON endpoint/shape may change; ``normalize_jobs`` accepts the
common envelope keys (``data`` / ``jobs`` / ``results``) and the fetch never
raises. If the shape drifts the source yields zero rows rather than crashing
discovery. LPA figures flow through as description text and are surfaced (not
pruned) by discovery_filters.extract_inr_salary downstream.

Usage:
  python3 scrape_hirist.py [--out PATH] [--max-pages N]

Writes a JSON array of listings (shared schema) to --out
(default: ../listings/<date>-hirist.json).
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

SITE = "hirist"
BASE = "https://www.hirist.tech"
# Public search JSON. Query params are appended per term/page in scrape().
SEARCH_API = f"{BASE}/api/v1/job/search"
REQUEST_DELAY_S = 1.5


def _records(data) -> list[dict]:
    """Pull the job list out of whatever envelope Hirist returns."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("data", "jobs", "results", "docs", "hits"):
        val = data.get(key)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)]
        if isinstance(val, dict):
            inner = val.get("jobs") or val.get("results") or val.get("docs")
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
    return []


def _job_url(job: dict) -> str:
    for key in ("url", "job_url", "jobUrl", "detail_url", "seo_url", "link"):
        val = job.get(key)
        if val:
            return val if str(val).startswith("http") else f"{BASE}{val}"
    jid = job.get("id") or job.get("job_id") or job.get("_id")
    slug = job.get("slug") or job.get("seo_slug")
    if jid and slug:
        return f"{BASE}/j/{slug}-{jid}"
    if jid:
        return f"{BASE}/j/{jid}"
    return ""


def normalize_jobs(data, *, search_term: str = "") -> list[dict]:
    """Map a Hirist search response to shared-shape listings."""
    out: list[dict] = []
    for job in _records(data):
        url = _job_url(job)
        if not url:
            continue
        title = (
            job.get("title") or job.get("jobTitle") or job.get("designation") or ""
        )
        company = (
            job.get("company") or job.get("companyName") or job.get("company_name") or ""
        )
        if isinstance(company, dict):
            company = company.get("name") or company.get("display_name") or ""
        location = job.get("location") or job.get("city") or job.get("locations") or ""
        if isinstance(location, list):
            location = ", ".join(str(x) for x in location if x)
        desc = (
            job.get("description") or job.get("jobDescription")
            or job.get("job_description") or job.get("summary") or ""
        )
        # Append salary/LPA hints into the description so extract_inr_salary
        # can surface them (display only — never pruned).
        sal = job.get("salary") or job.get("ctc") or job.get("compensation")
        if sal:
            desc = f"{desc}\nSalary: {sal}".strip()
        posted = (
            job.get("postedDate") or job.get("date_posted")
            or job.get("createdAt") or job.get("posted_on") or ""
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
            url = f"{SEARCH_API}?q={q}&page={page}&location=india"
            data = fetch_json(url, headers={"Accept": "application/json"})
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
    args = parser.parse_args()

    out_path = (
        Path(args.out) if args.out
        else ROOT / "listings" / f"{date.today().isoformat()}-hirist.json"
    )
    listings = scrape(max_pages=max(1, args.max_pages))
    write_listings(out_path, listings)
    log(f"wrote {len(listings)} listings -> {out_path}")


if __name__ == "__main__":
    main()
