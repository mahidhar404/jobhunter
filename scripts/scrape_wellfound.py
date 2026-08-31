#!/usr/bin/env python3
"""Wellfound (ex-AngelList Talent) scraper — server-rendered Apollo state.

Wellfound was catalogued as ``blocked_captcha``. That is stale: the public
``/role/...`` and ``/location/...`` job pages are server-rendered Next.js and
ship the whole result set inside ``__NEXT_DATA__``'s Apollo cache, so a plain
polite GET returns real listings. No CAPTCHA is solved and no login is used —
if a challenge page ever comes back we log and skip.

Also serves ``angellist_india`` (same site, India locations).

Usage:
  python3 scrape_wellfound.py [--out PATH] [--india] [--max-days N]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from ww_scrape_common import (
    ROOT,
    dedup_by_url,
    fetch_text,
    listing,
    log,
    polite_sleep,
    write_listings,
)
from known_job_urls import filter_out_known_listings, load_skip_urls_file  # noqa: E402

BASE = "https://wellfound.com"
DEFAULT_MAX_DAYS = 21

# Public role/location listing pages (server-rendered).
WORLDWIDE_PATHS = (
    "/jobs",
    "/role/software-engineer",
    "/role/data-scientist",
    "/role/data-engineer",
    "/role/machine-learning-engineer",
    "/role/backend-engineer",
    "/role/full-stack-engineer",
    "/role/frontend-engineer",
    "/role/data-analyst",
    "/role/devops-engineer",
    "/role/python-developer",
    "/role/java-developer",
    "/role/mobile-engineer",
    "/role/qa-engineer",
    # Remote-scoped variants surface a different slice than the bare role page.
    "/role/r/software-engineer",
    "/role/r/data-scientist",
    "/role/r/backend-engineer",
    "/role/r/data-engineer",
    "/role/r/machine-learning-engineer",
    "/role/r/full-stack-engineer",
)
INDIA_PATHS = (
    "/location/india",
    "/role/l/software-engineer/india",
    "/role/l/software-engineer/bangalore",
    "/role/l/software-engineer/mumbai",
    "/role/l/software-engineer/delhi",
    "/role/l/software-engineer/hyderabad",
    "/role/l/software-engineer/pune",
    "/role/l/data-scientist/india",
    "/role/l/data-scientist/bangalore",
    "/role/l/backend-engineer/india",
    "/role/l/backend-engineer/bangalore",
    "/role/l/data-engineer/india",
    "/role/l/full-stack-engineer/india",
    "/role/l/machine-learning-engineer/india",
)


def _lit(value, default):
    """Apollo stringifies python-ish literals ("['San Francisco']", "False")."""
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


def _apollo_data(html: str) -> dict:
    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return {}
    try:
        blob = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    try:
        return blob["props"]["pageProps"]["apolloState"]["data"]
    except (KeyError, TypeError):
        return {}


def _company_index(data: dict) -> dict[str, str]:
    """Map JobListingSearchResult key -> company name.

    Search-result jobs carry no startup field; the Startup(Result) node points
    at its own jobs through ``highlightedJobListings``, so invert that.
    """
    out: dict[str, str] = {}
    for key, node in data.items():
        if not isinstance(node, dict):
            continue
        if not (key.startswith("StartupResult:") or key.startswith("Startup:")):
            continue
        name = node.get("name")
        if not name:
            continue
        refs = _lit(node.get("highlightedJobListings"), []) or []
        if isinstance(refs, (list, tuple)):
            for ref in refs:
                if isinstance(ref, dict) and ref.get("__ref"):
                    out[ref["__ref"]] = name
    return out


def _looks_challenged(html: str) -> bool:
    low = (html or "")[:4000].lower()
    return ("just a moment" in low or "cf-challenge" in low
            or "captcha" in low and "__next_data__" not in low)


def scrape(*, india: bool, max_days: int) -> list[dict]:
    site = "angellist_india" if india else "wellfound"
    out: list[dict] = []
    for path in (INDIA_PATHS if india else WORLDWIDE_PATHS):
        html = fetch_text(f"{BASE}{path}")
        polite_sleep(1.0)
        if not html:
            continue
        if _looks_challenged(html):
            log(f"disabled/skipped ({site}): challenge wall on {path} — "
                "never CAPTCHA-solved", err=True)
            continue
        data = _apollo_data(html)
        if not data:
            continue
        # /jobs uses JobListing + Startup; /role/* and /location/* use
        # JobListingSearchResult + StartupResult (company reached by walking
        # the startup's highlightedJobListings refs, not a field on the job).
        company_by_job = _company_index(data)
        for key, node in data.items():
            if not isinstance(node, dict):
                continue
            if not (key.startswith("JobListing:")
                    or key.startswith("JobListingSearchResult:")):
                continue
            jid, slug = node.get("id"), node.get("slug")
            if not jid:
                continue
            url = f"{BASE}/jobs/{jid}-{slug}" if slug else f"{BASE}/jobs/{jid}"
            company = ""
            ref = (node.get("startup") or {})
            if isinstance(ref, dict) and ref.get("__ref"):
                startup = data.get(ref["__ref"]) or {}
                company = startup.get("name") or ""
            if not company:
                company = company_by_job.get(key, "")
            locs = _lit(node.get("locationNames"), []) or _lit(
                node.get("acceptedRemoteLocationNames"), [])
            if isinstance(locs, (list, tuple)):
                location = ", ".join(str(x) for x in locs)
            else:
                location = str(locs or "")
            if not location:
                location = "Remote" if _lit(node.get("remote"), False) else (
                    "India" if india else "Remote")
            posted = None
            live = node.get("liveStartAt")
            if live:
                try:
                    posted = datetime.fromtimestamp(
                        int(live), tz=timezone.utc).date().isoformat()
                except (ValueError, OSError, OverflowError):
                    posted = None
            row = listing(
                title=node.get("title") or node.get("primaryRoleTitle") or "",
                company=company,
                site=site,
                job_url=url,
                description=node.get("description") or "",
                date_posted=posted,
                location=location,
                salary_hint=node.get("compensation") or None,
                max_days=max_days,
            )
            if row:
                out.append(row)
    return dedup_by_url(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--india", action="store_true",
                    help="Scrape the AngelList India lane instead of worldwide")
    ap.add_argument("--skip-urls", default=None)
    ap.add_argument("--max-days", type=int, default=DEFAULT_MAX_DAYS)
    args = ap.parse_args()

    site = "angellist_india" if args.india else "wellfound"
    out_path = Path(args.out) if args.out else (
        ROOT / "listings" / f"{date.today().isoformat()}-{site}.json")
    log(f"scraping {site}")
    rows = scrape(india=args.india, max_days=max(1, args.max_days))
    skip = load_skip_urls_file(Path(args.skip_urls) if args.skip_urls else None)
    if skip:
        rows, skipped = filter_out_known_listings(rows, skip)
        log(f"skip-urls: dropped {skipped} known")
    write_listings(out_path, rows)


if __name__ == "__main__":
    main()
