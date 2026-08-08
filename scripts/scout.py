#!/usr/bin/env python3
"""Scrape full-time AI/ML/DS/DE job postings via JobSpy.

Usage: python3 scout.py [--out PATH] [--regions us,india]

Writes a JSON array of listings (deduped by job_url) to --out
(default: ../listings/<date>.json), each with title/company/site/job_url/
description/date_posted/job_type/location.

Region-aware: US discovery is the default; India is opt-in. When India is
enabled it runs an extra JobSpy pass with location="India" /
country_indeed="india". Both passes accumulate into the SAME --out file
in one process (written once per term), so US and India results never
clobber each other and the dashboard's per-source listing path / status
counting stays unchanged. Regions come from --regions or, when omitted, the
JOBHUNTER_DISCOVERY_REGIONS env var the dashboard sets (US-only default).
The discovery region gate (discovery_filters) still keeps/drops by location
downstream — this only controls which geographies JobSpy is queried for.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from discovery_filters import enabled_regions_from_env, normalize_regions  # noqa: E402

# JobSpy query params per enabled region. Keep in sync with
# discovery_filters region ids. LinkedIn India is brittle/low-priority
# (guest scrape often blocked); the Easy-Apply skip is JobSpy's default
# behavior and is preserved (we never automate LinkedIn apply).
REGION_QUERY = {
    "us": {"location": "United States", "country_indeed": "USA"},
    "india": {"location": "India", "country_indeed": "india"},
}


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


def scrape_one(site: str, term: str, results_wanted: int, *, region: str = "us"):
    q = REGION_QUERY.get(region, REGION_QUERY["us"])
    return scrape_jobs(
        site_name=[site],
        search_term=term,
        location=q["location"],
        results_wanted=results_wanted,
        job_type="fulltime",
        country_indeed=q["country_indeed"],
        linkedin_fetch_description=(site == "linkedin"),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--results-per-term", type=int, default=40)
    parser.add_argument(
        "--sites", nargs="+", choices=SITES, default=None,
        help="Subset of JobSpy sites to scrape (default: all).",
    )
    parser.add_argument(
        "--regions", default=None,
        help="Comma-separated regions to query (us,india). Default: "
             "JOBHUNTER_DISCOVERY_REGIONS env / US-only.",
    )
    args = parser.parse_args()

    sites = list(args.sites) if args.sites else list(SITES)
    if not sites:
        raise SystemExit("no --sites selected")

    regions = normalize_regions(args.regions) if args.regions else enabled_regions_from_env()
    if not regions:
        regions = ("us",)

    out_path = Path(args.out) if args.out else Path(__file__).parent.parent / "listings" / f"{date.today().isoformat()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    run_start = time.monotonic()
    log(f"regions: {', '.join(regions)}")
    seen_urls: set[str] = set()
    all_listings: list[dict] = []
    # max_workers matches len(sites): both sites for one term are
    # submitted before either is waited on, so indeed+linkedin actually
    # run concurrently (previously max_workers=1 combined with waiting on
    # each future immediately meant only one call was ever in flight at a
    # time regardless of pool size - bumping the worker count alone
    # wouldn't have changed anything). Terms themselves stay sequential -
    # a bigger jump in concurrency risks tripping site-side rate limits
    # this hasn't been tested against.
    with ThreadPoolExecutor(max_workers=max(1, len(sites))) as pool:
        for term in SEARCH_TERMS:
            # US then India (when enabled): separate JobSpy passes, one shared
            # in-memory list, one file write per term → no US/IN clobber.
            for region in regions:
                for site in sites:
                    log(f"scraping {site}: {term} [{region}]...")
                futures = {
                    site: pool.submit(scrape_one, site, term, args.results_per_term, region=region)
                    for site in sites
                }
                for site, future in futures.items():
                    call_site_start = time.monotonic()
                    try:
                        df = future.result(timeout=PER_CALL_TIMEOUT_S)
                    except FutureTimeoutError:
                        log(f"warn: {site}/{term} [{region}] timed out after {PER_CALL_TIMEOUT_S}s, skipping", err=True)
                        continue
                    except Exception as exc:  # one site/term failing shouldn't kill the whole run
                        log(f"warn: scrape failed for {site}/{term!r} [{region}]: {exc}", err=True)
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
                    log(f"  got {added} new results from {site}/{term} [{region}] in {site_elapsed:.1f}s")

            # Flush to disk after each term so partial results survive a crash
            out_path.write_text(json.dumps(all_listings, indent=2, default=str))
            log(f"  flushed {len(all_listings)} total listings -> {out_path.name}")

            # Hint to reclaim DataFrames and scraper memory before next term
            gc.collect()

    log(f"done: {len(all_listings)} listings -> {out_path} (total {time.monotonic() - run_start:.1f}s)")


if __name__ == "__main__":
    main()
