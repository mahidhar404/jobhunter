#!/usr/bin/env python3
"""Scrape full-time AI/ML/DS/DE/SWE job postings via JobSpy.

Usage: python3 scout.py [--out PATH] [--regions india,worldwide]

Writes a JSON array of listings (deduped by job_url) to --out
(default: ../listings/<date>.json), each with title/company/site/job_url/
description/date_posted/job_type/location.

Lane-aware: India and Worldwide (legacy ``us`` → worldwide). India runs
JobSpy with India metros; Worldwide prefers Remote so US onsite/hybrid
are less common (still pruned downstream). Regions come from --regions or
JOBHUNTER_DISCOVERY_REGIONS.
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
from known_job_urls import load_skip_urls_file, url_is_known  # noqa: E402

# JobSpy query params per enabled lane. Legacy "us" normalizes to worldwide.
REGION_QUERY = {
    "worldwide": {"location": "Remote", "country_indeed": "USA"},
    "us": {"location": "Remote", "country_indeed": "USA"},  # legacy alias
    "india": {"location": "India", "country_indeed": "india"},
}

# Rotate metros for India JobSpy passes (Indeed responds better to cities).
INDIA_LOCATION_ROTATION = (
    "Bengaluru",
    "Hyderabad",
    "Mumbai",
    "Pune",
    "Chennai",
    "Delhi",
    "Noida",
    "Gurgaon",
    "Remote",
    "India",
)


def log(msg: str, *, err: bool = False) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr if err else sys.stdout, flush=True)

SEARCH_TERMS = [
    "machine learning engineer",
    "ai engineer",
    "data scientist",
    "data engineer",
    "data analyst",
    "software engineer",
    "software developer",
    "backend engineer",
    "full stack engineer",
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
    "python developer",
]

# Scraped one site at a time (not a single combined site_name=[...] call) -
# a combined call was observed to hang indefinitely on Indeed while LinkedIn
# scraping ran fine. Each call also gets its own hard timeout so one stuck
# term/site is skipped instead of hanging the whole run.
SITES = ["indeed", "linkedin"]  # zip_recruiter/glassdoor dropped: reliably 403/400 without proxies
PER_CALL_TIMEOUT_S = 90


def _cell_str(value) -> str | None:
    """Coerce a JobSpy/pandas cell to str, mapping NaN/NaT/nullish → None."""
    if value is None:
        return None
    try:
        # pandas NA / NaT / float nan — avoid str(nan) == "nan" in listings JSON.
        import pandas as pd

        if pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value).strip()
    if not text or text.lower() in ("nan", "nat", "none", "null"):
        return None
    return text


def scrape_one(
    site: str,
    term: str,
    results_wanted: int,
    *,
    region: str = "worldwide",
    hours_old: int | None = None,
    location_override: str | None = None,
):
    q = REGION_QUERY.get(region, REGION_QUERY["worldwide"])
    location = location_override or q["location"]
    kwargs = dict(
        site_name=[site],
        search_term=term,
        location=location,
        results_wanted=results_wanted,
        job_type="fulltime",
        country_indeed=q["country_indeed"],
        linkedin_fetch_description=(site == "linkedin"),
    )
    if hours_old:
        kwargs["hours_old"] = int(hours_old)
    return scrape_jobs(**kwargs)


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
        help="Comma-separated lanes to query (india,worldwide). Default: "
             "JOBHUNTER_DISCOVERY_REGIONS env.",
    )
    parser.add_argument(
        "--hours-old", type=int, default=None,
        help="JobSpy recency window in hours (adaptive Discover: days * 24).",
    )
    parser.add_argument(
        "--skip-urls", default=None,
        help="JSON array of URL keys already in jobs.json / blocked / prior listings. "
             "JobSpy still queries aggregators (no per-URL skip inside JobSpy); "
             "known URLs are dropped before write.",
    )
    args = parser.parse_args()

    sites = list(args.sites) if args.sites else list(SITES)
    if not sites:
        raise SystemExit("no --sites selected")

    regions = normalize_regions(args.regions) if args.regions else enabled_regions_from_env()
    if not regions:
        regions = ("india", "worldwide")

    out_path = Path(args.out) if args.out else Path(__file__).parent.parent / "listings" / f"{date.today().isoformat()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    skip_keys = load_skip_urls_file(Path(args.skip_urls) if args.skip_urls else None)
    if skip_keys:
        log(f"skip-urls: {len(skip_keys)} known key(s) (filter after JobSpy; cannot skip JobSpy network)")

    run_start = time.monotonic()
    if args.hours_old:
        log(f"regions: {', '.join(regions)}; hours_old={args.hours_old}")
    else:
        log(f"regions: {', '.join(regions)}")
    seen_urls: set[str] = set()
    all_listings: list[dict] = []
    skipped_known = 0
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
                loc_override = None
                if region == "india":
                    # Rotate metros so Indeed India isn't stuck on a dead query shape.
                    loc_override = INDIA_LOCATION_ROTATION[
                        SEARCH_TERMS.index(term) % len(INDIA_LOCATION_ROTATION)
                    ]
                for site in sites:
                    label = f"{site}: {term} [{region}"
                    if loc_override:
                        label += f"/{loc_override}"
                    log(f"scraping {label}]...")
                futures = {
                    site: pool.submit(
                        scrape_one, site, term, args.results_per_term,
                        region=region, hours_old=args.hours_old,
                        location_override=loc_override,
                    )
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
                        url = _cell_str(row.get("job_url"))
                        if not url or url in seen_urls:
                            continue
                        direct = _cell_str(row.get("job_url_direct"))
                        if url_is_known(url, skip_keys) or url_is_known(direct, skip_keys):
                            skipped_known += 1
                            continue
                        seen_urls.add(url)
                        all_listings.append({
                            "title": _cell_str(row.get("title")),
                            "company": _cell_str(row.get("company")),
                            "site": _cell_str(row.get("site")) or site,
                            "job_url": url,
                            "job_url_direct": direct,
                            "description": _cell_str(row.get("description")) or "",
                            "date_posted": _cell_str(row.get("date_posted")),
                            "job_type": _cell_str(row.get("job_type")),
                            "location": _cell_str(row.get("location")),
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

    if skipped_known:
        log(f"skipped {skipped_known} already-known URL(s)")
    log(f"done: {len(all_listings)} listings -> {out_path} (total {time.monotonic() - run_start:.1f}s)")


if __name__ == "__main__":
    main()
