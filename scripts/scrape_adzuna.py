#!/usr/bin/env python3
"""Adzuna job scraper via the official Adzuna Jobs API (India `in` or US `us`).

Register a free app at https://developer.adzuna.com/ to get an APP_ID +
APP_KEY, then provide them via either:

  * env vars ADZUNA_APP_ID / ADZUNA_APP_KEY (take precedence), or
  * an ignored secrets file web_keys.json at the workspace root:
        {"adzuna_app_id": "...", "adzuna_app_key": "..."}

If neither is present the source SKIPS cleanly: it writes an empty listings
file, prints a UI-visible "disabled/skipped (no Adzuna API keys)" line, and
exits 0 (never crashes discovery). **US yield is 0 without keys** — this
script never invents or requires committed secrets.

Pagination: Adzuna caps ``results_per_page`` at 50. US defaults to 3 pages
per search term (150/term, ~1s delay between requests, 14 US terms ≈ 42
calls). India stays 1 page unless ``--max-pages`` is set. Hard cap is 5
pages so a CLI typo cannot hammer the API.

Usage:
  python3 scrape_adzuna.py [--country in|us] [--out PATH]
                           [--results-per-term N] [--max-pages N]

Writes a JSON array of listings (same schema as the other scrapers) to --out
(default: ../listings/<date>-adzuna-in.json or -adzuna-us.json).
"""
from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

from india_scrape_common import (
    ROOT,
    SEARCH_TERMS,
    dedup_by_url,
    fetch_json,
    load_web_keys,
    log,
    polite_sleep,
    write_listings,
)
from known_job_urls import filter_out_known_listings, load_skip_urls_file  # noqa: E402

SITE = "adzuna"
REQUEST_DELAY_S = 1.0
ADZUNA_MAX_RESULTS_PER_PAGE = 50
ADZUNA_MAX_PAGES_CAP = 5
US_DEFAULT_MAX_PAGES = 3
IN_DEFAULT_MAX_PAGES = 3

# US terms mirror scout.py's JobSpy queries; India uses india_scrape_common.
US_SEARCH_TERMS = [
    "machine learning engineer",
    "ai engineer",
    "data scientist",
    "data engineer",
    "mlops engineer",
    "applied scientist",
    "computer vision engineer",
    "nlp engineer",
    "research engineer",
    "generative ai engineer",
    "llm engineer",
    "analytics engineer",
    "ai research scientist",
    "ml platform engineer",
]

COUNTRY_CONFIG = {
    "in": {
        "api_base": "https://api.adzuna.com/v1/api/jobs/in/search",
        "out_suffix": "adzuna-in",
        "search_term_prefix": "india:adzuna",
        "log_label": "adzuna",
    },
    "us": {
        "api_base": "https://api.adzuna.com/v1/api/jobs/us/search",
        "out_suffix": "adzuna-us",
        "search_term_prefix": "us:adzuna",
        "log_label": "adzuna-us",
    },
}


def _resolve_keys() -> tuple[str | None, str | None]:
    """Env vars win; fall back to web_keys.json. Missing → (None, None)."""
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if app_id and app_key:
        return app_id, app_key
    keys = load_web_keys()
    app_id = app_id or keys.get("adzuna_app_id") or keys.get("ADZUNA_APP_ID")
    app_key = app_key or keys.get("adzuna_app_key") or keys.get("ADZUNA_APP_KEY")
    return (app_id or None), (app_key or None)


def normalize_results(
    data: dict | None, *, search_term: str = "", country: str = "in",
) -> list[dict]:
    """Map an Adzuna search-response dict to the shared listing shape."""
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for job in data.get("results") or []:
        if not isinstance(job, dict):
            continue
        url = job.get("redirect_url")
        if not url:
            continue
        company = ((job.get("company") or {}) if isinstance(job.get("company"), dict) else {}).get(
            "display_name"
        )
        location = ((job.get("location") or {}) if isinstance(job.get("location"), dict) else {}).get(
            "display_name"
        )
        created = job.get("created") or ""
        prefix = COUNTRY_CONFIG[country]["search_term_prefix"]
        out.append({
            "title": job.get("title") or "",
            "company": company or "",
            "site": SITE,
            "job_url": url,
            "job_url_direct": url,
            "description": job.get("description") or "",
            "date_posted": created[:10] if isinstance(created, str) and created else None,
            "job_type": "fulltime",
            "location": location,
            "search_term": f"{prefix}:{search_term}" if search_term else prefix,
        })
    return out


def resolve_max_pages(country: str, max_pages_arg: int | None) -> int:
    """US defaults to 3 pages/term; India 1. Hard-cap 5. None = use default."""
    default = US_DEFAULT_MAX_PAGES if country == "us" else IN_DEFAULT_MAX_PAGES
    raw = default if max_pages_arg is None else max_pages_arg
    return max(1, min(int(raw), ADZUNA_MAX_PAGES_CAP))


def scrape(
    app_id: str,
    app_key: str,
    *,
    country: str,
    results_per_term: int,
    max_pages: int,
    max_days: int | None = None,
) -> list[dict]:
    cfg = COUNTRY_CONFIG[country]
    api_base = cfg["api_base"]
    log_label = cfg["log_label"]
    terms = SEARCH_TERMS if country == "in" else US_SEARCH_TERMS
    listings: list[dict] = []
    for term in terms:
        for page in range(1, max_pages + 1):
            params = (
                f"app_id={app_id}&app_key={app_key}"
                f"&results_per_page={results_per_term}"
                f"&what={term.replace(' ', '%20')}"
                f"&content-type=application/json"
            )
            if max_days:
                params += f"&max_days={int(max_days)}"
            data = fetch_json(f"{api_base}/{page}?{params}")
            rows = normalize_results(data, search_term=term, country=country)
            if not rows:
                break
            listings.extend(rows)
            log(f"  got {len(rows)} results from {log_label}/{term} p{page}")
            polite_sleep(REQUEST_DELAY_S)
    return dedup_by_url(listings)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--country", choices=sorted(COUNTRY_CONFIG), default="in",
        help="Adzuna country API (in=India, us=United States).",
    )
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--results-per-term", type=int, default=ADZUNA_MAX_RESULTS_PER_PAGE,
        help="Adzuna results_per_page (API max 50).",
    )
    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="Pages per search term (US default 3, India default 1, cap 5).",
    )
    parser.add_argument(
        "--max-days", type=int, default=None,
        help="Adzuna max_days recency filter (adaptive Discover window).",
    )
    parser.add_argument(
        "--skip-urls", default=None,
        help="JSON array of URL keys to drop (jobs.json / blocked / prior listing)",
    )
    args = parser.parse_args()

    country = args.country
    cfg = COUNTRY_CONFIG[country]
    out_path = (
        Path(args.out) if args.out
        else ROOT / "listings" / f"{date.today().isoformat()}-{cfg['out_suffix']}.json"
    )

    app_id, app_key = _resolve_keys()
    if not app_id or not app_key:
        # Clean skip — write empty file so the merge step finds nothing, and
        # print a UI-visible status the dashboard log surfaces.
        write_listings(out_path, [])
        log(f"disabled/skipped ({cfg['log_label']}): no Adzuna API keys "
            f"(set ADZUNA_APP_ID/ADZUNA_APP_KEY or web_keys.json)")
        log(f"wrote 0 listings -> {out_path}")
        return

    skip_keys = load_skip_urls_file(Path(args.skip_urls) if args.skip_urls else None)
    if skip_keys:
        log(f"skip-urls: {len(skip_keys)} known key(s)")
    listings = scrape(
        app_id, app_key,
        country=country,
        results_per_term=max(1, min(args.results_per_term, ADZUNA_MAX_RESULTS_PER_PAGE)),
        max_pages=resolve_max_pages(country, args.max_pages),
        max_days=args.max_days if args.max_days and args.max_days > 0 else None,
    )
    listings, skipped = filter_out_known_listings(listings, skip_keys)
    if skipped:
        log(f"skipped {skipped} already-known URL(s)")
    write_listings(out_path, listings)
    log(f"wrote {len(listings)} listings -> {out_path}")


if __name__ == "__main__":
    main()
