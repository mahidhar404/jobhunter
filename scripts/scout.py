#!/usr/bin/env python3
"""Scrape US full-time AI/ML/DS/DE job postings via JobSpy.

Usage: python3 scout.py [--out PATH]

Writes a JSON array of listings (deduped by job_url) to --out
(default: ../listings/<date>.json), each with title/company/site/job_url/
description/date_posted/job_type/location.
"""
import argparse
import gc
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import date, datetime
from pathlib import Path

from jobspy import scrape_jobs


def log(msg: str, *, err: bool = False) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr if err else sys.stdout, flush=True)

SEARCH_TERMS = [
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

# Scraped one site at a time (not a single combined site_name=[...] call) -
# a combined call was observed to hang indefinitely on Indeed while LinkedIn
# scraping ran fine. Each call also gets its own hard timeout so one stuck
# term/site is skipped instead of hanging the whole run.
SITES = ["indeed", "linkedin"]  # zip_recruiter/glassdoor dropped: reliably 403/400 without proxies
PER_CALL_TIMEOUT_S = 90


def scrape_one(site: str, term: str, results_wanted: int):
    return scrape_jobs(
        site_name=[site],
        search_term=term,
        location="United States",
        results_wanted=results_wanted,
        job_type="fulltime",
        country_indeed="USA",
        linkedin_fetch_description=(site == "linkedin"),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--results-per-term", type=int, default=40)
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else Path(__file__).parent.parent / "listings" / f"{date.today().isoformat()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    run_start = time.monotonic()
    seen_urls: set[str] = set()
    all_listings: list[dict] = []
    # max_workers=2 matches len(SITES): both sites for one term are
    # submitted before either is waited on, so indeed+linkedin actually
    # run concurrently (previously max_workers=1 combined with waiting on
    # each future immediately meant only one call was ever in flight at a
    # time regardless of pool size - bumping the worker count alone
    # wouldn't have changed anything). Terms themselves stay sequential -
    # a bigger jump in concurrency risks tripping site-side rate limits
    # this hasn't been tested against.
    with ThreadPoolExecutor(max_workers=len(SITES)) as pool:
        for term in SEARCH_TERMS:
            for site in SITES:
                log(f"scraping {site}: {term}...")
            call_start = time.monotonic()
            futures = {site: pool.submit(scrape_one, site, term, args.results_per_term) for site in SITES}
            for site, future in futures.items():
                call_site_start = time.monotonic()
                try:
                    df = future.result(timeout=PER_CALL_TIMEOUT_S)
                except FutureTimeoutError:
                    log(f"warn: {site}/{term} timed out after {PER_CALL_TIMEOUT_S}s, skipping", err=True)
                    continue
                except Exception as exc:  # one site/term failing shouldn't kill the whole run
                    log(f"warn: scrape failed for {site}/{term!r}: {exc}", err=True)
                    continue

                added = 0
                for _, row in df.iterrows():
                    url = row.get("job_url")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    all_listings.append({
                        "title": row.get("title"),
                        "company": row.get("company"),
                        "site": row.get("site"),
                        "job_url": url,
                        "job_url_direct": row.get("job_url_direct"),
                        "description": row.get("description"),
                        "date_posted": str(row.get("date_posted")) if row.get("date_posted") is not None else None,
                        "job_type": row.get("job_type"),
                        "location": row.get("location"),
                        "search_term": term,
                    })
                    added += 1
                site_elapsed = time.monotonic() - call_site_start
                log(f"  got {added} new results from {site}/{term} in {site_elapsed:.1f}s")

            # Flush to disk after each term so partial results survive a crash
            out_path.write_text(json.dumps(all_listings, indent=2, default=str))
            log(f"  flushed {len(all_listings)} total listings -> {out_path.name}")

            # Hint to reclaim DataFrames and scraper memory before next term
            gc.collect()

    log(f"done: {len(all_listings)} listings -> {out_path} (total {time.monotonic() - run_start:.1f}s)")


if __name__ == "__main__":
    main()
