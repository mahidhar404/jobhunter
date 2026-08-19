#!/usr/bin/env python3
"""Deterministic dedup + qualify step for scraped job listings.

Merges one or more listings files (from scout.py and/or scrape_ats.py),
drops test/mock entries, indirect (staffing-agency) apply links, and
obviously irrelevant titles - then matches remaining listings by URL/posting
key, gated full-JD fingerprint, or a same-ATS-org exact-title repost. The same
role seen via two different boards (e.g. Indeed AND the company's own
Greenhouse page) collapses only with one of those identity signals. When a duplicate is found, the
direct-ATS-sourced copy (greenhouse/lever/ashby) wins over an aggregator
copy (indeed/linkedin) since it tends to have a cleaner apply_url and a
fuller description. Loser URLs are preserved on alternate_urls / source_url
(see apply_urls.merge_listing_pair) — never discarded.

This is intentionally a plain script, not something the agent re-derives
each run - the matching logic is fiddly to get right from scratch every
time and doesn't need judgment, just consistency.

Usage:
  python3 dedup_listings.py [listings_file ...] [--out PATH]

With no listings_file args, reads every listings/*.json for today's date.
Writes the qualified, deduped candidate array to --out (default:
../listings/<date>-qualified.json) and prints a one-line summary per
skip reason plus the final count.
"""
import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from apply_urls import (  # noqa: E402
    enrich_listing_urls,
    is_aggregator_url,
    merge_listing_pair,
    normalize_url,
)
from blocked_urls import block_keys_for_url, load_blocked_url_set  # noqa: E402
from discovery_filters import (  # noqa: E402
    enabled_regions_from_env,
    is_excluded_title,
    location_matches_regions,
    requires_excessive_experience,
    requires_security_clearance,
    requires_us_citizen_or_greencard,
)
from jd_fingerprint import item_jd_fingerprint  # noqa: E402
from dedup_jobs import exact_url_overlap, is_fresher  # noqa: E402
from posting_identity import ats_org_key, posting_key, same_ats_org  # noqa: E402
from text_normalize import normalize_company, normalize_title  # noqa: E402


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

ROOT = Path(__file__).parent.parent
JOBS_FILE = ROOT / "jobs.json"

STAFFING_DENY_HINTS = [
    "staffing", "recruiting agency", "talent acquisition partners",
    "randstad", "robert half", "adecco", "manpower", "kforce", "insight global",
]
TEST_HINTS = ["smart apply test", "internal test job", "test company"]
IRRELEVANT_TITLE_HINTS = [
    "drafter", "cad operator", "engineering technician", "business development manager",
    "customer support consultant", "workforce analytics", "solution architect",
    "infrastructure engineer - data center networking",
]
# Seniority + US-location + clearance/intel filters live in
# discovery_filters.py (shared with the dashboard hide rules for
# already-stored discovered jobs).
# Kept in sync with scrape_ats.py's own copy (same duplication pattern as
# normalize_company below) - scrape_ats.py only ever applied this to its
# own ATS-sourced listings, so scout.py's Indeed/LinkedIn results (the
# majority of volume) had zero title-relevance filtering at all, only
# ever passing through IRRELEVANT_TITLE_HINTS' blocklist of specific bad
# phrases. Observed live: generic "Software Engineer"/"Electrical
# Engineer"/"Product Manager"/sales roles were sailing straight through
# since none is an explicitly blocked phrase - a positive
# require-a-relevant-keyword check is what was actually missing. Scope is
# deliberately broad across the whole data/AI/ML circle - data analysis,
# data cleaning, and data engineering are all in-scope alongside ML/AI,
# not just the narrower "engineer"/"scientist" titles.
RELEVANT_KEYWORDS = [
    # Machine learning / AI - engineering & research
    "machine learning", "ml engineer", "mle", "ml ops", "mlops",
    "ml platform", "ml infrastructure", "ml research", "ai engineer",
    "ai infrastructure", "artificial intelligence", "ai researcher",
    "ai/ml", "applied scientist", "research scientist", "research engineer",
    "deep learning", "reinforcement learning", "computer vision",
    "nlp", "natural language processing", "llm", "generative ai", "genai",
    "prompt engineer", "conversational ai", "foundation model",
    "recommender system", "recommendation system", "ranking engineer",
    "search relevance", "speech recognition", "speech scientist",
    "ai safety", "responsible ai", "feature engineering",
    "perception engineer", "model training", "model deployment",
    "predictive analytics", "predictive model", "time series",
    "anomaly detection", "data annotation", "data labeling",

    # Data science / analysis
    "data scientist", "data science", "data analyst", "data analysis",
    "data analytics", "analytics engineer", "statistician",
    "business intelligence",

    # Data engineering / infrastructure
    "data engineer", "data engineering", "data platform",
    "data infrastructure", "data pipeline", "data architect",
    "data warehouse", "data lake", "data modeling", "database engineer",
    "etl", "elt", "dataops", "big data", "data cleaning", "data quality",
    "data wrangling",
]
ATS_SOURCE_RANK = {
    "greenhouse": 0, "lever": 0, "ashby": 0, "recruitee": 0, "personio": 0,
    "smartrecruiters": 0, "workable": 0, "rippling": 0, "breezy": 0, "bamboohr": 0,
    "indeed": 1, "linkedin": 1, "builtin": 1,
}
# Note: fuzzy-merge winner selection now uses apply_urls.merge_listing_pair
# (URL quality + site), not ATS_SOURCE_RANK alone. Kept for reference/compat.


def is_relevant(title) -> bool:
    t = str(title or "").lower()
    return any(kw in t for kw in RELEVANT_KEYWORDS)


def looks_like_staffing(company: str, description: str) -> bool:
    # Only the COMPANY NAME is a reliable signal here. Job descriptions from
    # real direct employers routinely include boilerplate like "we do not
    # accept submissions from staffing agencies" - matching against the
    # description text flags the employer for the exact opposite of what
    # the phrase means, so it's deliberately excluded.
    return any(h in str(company or "").lower() for h in STAFFING_DENY_HINTS)


def deduplicate_candidates(candidates: list[dict]) -> tuple[list[dict], int]:
    """Apply URL/posting → gated full-JD → exact-title repost identity order."""
    merged_count = 0

    def merge_phase(items: list[dict], reason_for_pair) -> list[dict]:
        nonlocal merged_count
        kept: list[dict] = []
        for item in items:
            match_idx = None
            reason = None
            for i, existing in enumerate(kept):
                reason = reason_for_pair(item, existing)
                if reason:
                    match_idx = i
                    break
            if match_idx is None:
                kept.append(item)
                continue
            existing = kept[match_idx]
            if reason == "repost" and is_fresher(item, existing):
                kept[match_idx] = merge_listing_pair(item, existing)
            else:
                kept[match_idx] = merge_listing_pair(existing, item)
            merged_count += 1
        return kept

    def url_or_posting_reason(a: dict, b: dict) -> str | None:
        if exact_url_overlap(a, b):
            return "url"
        a_key, b_key = posting_key(a), posting_key(b)
        return "posting_key" if a_key and a_key == b_key else None

    survivors = merge_phase(candidates, url_or_posting_reason)

    fp_groups: dict[str, list[dict]] = {}
    without_fp: list[dict] = []
    for item in survivors:
        fp = item_jd_fingerprint(item)
        if fp:
            fp_groups.setdefault(fp, []).append(item)
        else:
            without_fp.append(item)
    survivors = without_fp
    for items in fp_groups.values():
        def fingerprint_reason(a: dict, b: dict) -> str | None:
            same_company = (
                normalize_company(a.get("company"))
                and normalize_company(a.get("company"))
                == normalize_company(b.get("company"))
            )
            return "jd_fingerprint" if same_company or same_ats_org(a, b) else None

        survivors.extend(merge_phase(items, fingerprint_reason))

    repost_groups: dict[tuple[str, str], list[dict]] = {}
    without_repost_key: list[dict] = []
    for item in survivors:
        key = (ats_org_key(item) or "", normalize_title(item.get("title")))
        if all(key):
            repost_groups.setdefault(key, []).append(item)
        else:
            without_repost_key.append(item)
    survivors = without_repost_key
    for items in repost_groups.values():
        def repost_reason(a: dict, b: dict) -> str | None:
            a_key, b_key = posting_key(a), posting_key(b)
            if (
                a_key
                and b_key
                and a_key != b_key
                and (is_fresher(a, b) or is_fresher(b, a))
            ):
                return "repost"
            return None

        survivors.extend(merge_phase(items, repost_reason))
    return survivors, merged_count


def main() -> None:
    run_start = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument("listings_files", nargs="*")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    if args.listings_files:
        paths = [Path(p) for p in args.listings_files]
    else:
        today = date.today().isoformat()
        paths = sorted((ROOT / "listings").glob(f"{today}*.json"))

    all_listings = []
    for p in paths:
        if p.exists():
            all_listings.extend(json.loads(p.read_text()))

    existing_urls = set()
    if JOBS_FILE.exists():
        for job in json.loads(JOBS_FILE.read_text()).get("jobs", []):
            for f in ("job_url", "apply_url", "source_url"):
                if job.get(f):
                    existing_urls.add(normalize_url(job[f]) or job[f])
            for u in job.get("alternate_urls") or []:
                if u:
                    existing_urls.add(normalize_url(u) or u)
    # User-deleted apply links must never re-enter the qualified set.
    blocked_urls = load_blocked_url_set()

    # Enabled discovery regions (US default; India opt-in) — set by the
    # dashboard via JOBHUNTER_DISCOVERY_REGIONS before spawning this step.
    regions = enabled_regions_from_env()
    log(f"discovery regions: {', '.join(regions)}")

    skipped = {"test": 0, "existing_url": 0, "blocked_url": 0, "staffing": 0,
               "irrelevant_title": 0, "not_relevant": 0, "no_company": 0,
               "management_track": 0, "non_us_location": 0,
               "clearance_or_intel": 0, "excessive_yoe": 0,
               "citizenship_or_greencard": 0}
    candidates = []
    for item in all_listings:
        if not isinstance(item, dict):
            continue  # this dir should only ever hold job-listing arrays, but be defensive
        company = item.get("company")
        title = item.get("title") or ""
        description = item.get("description") or ""
        location = item.get("location") or ""
        # Enrich aggregator listings with job_url_direct / ATS links in description
        # before URL-already-seen checks — never drop the listing if enrichment fails.
        enriched_fields = enrich_listing_urls(item)
        item = dict(item)
        item["apply_url"] = enriched_fields["apply_url"] or item.get("apply_url")
        if enriched_fields.get("source_url"):
            item["source_url"] = enriched_fields["source_url"]
        if enriched_fields.get("alternate_urls"):
            item["alternate_urls"] = enriched_fields["alternate_urls"]
        if enriched_fields.get("job_url") and not item.get("job_url"):
            item["job_url"] = enriched_fields["job_url"]
        # Prefer direct ATS as job_url_direct when we found one
        if enriched_fields["apply_url"] and item.get("job_url_direct") != enriched_fields["apply_url"]:
            if enriched_fields["apply_url"] and not is_aggregator_url(enriched_fields["apply_url"]):
                item["job_url_direct"] = enriched_fields["apply_url"]

        url = item.get("job_url") or ""
        direct_url = item.get("job_url_direct") or ""
        apply_url = item.get("apply_url") or ""

        if not company or str(company).lower() == "nan":
            skipped["no_company"] += 1
            continue
        text_blob = f"{title} {description}".lower()
        if any(h in text_blob for h in TEST_HINTS) or any(h in str(company).lower() for h in TEST_HINTS):
            skipped["test"] += 1
            continue
        url_keys = {normalize_url(u) or u for u in (url, direct_url, apply_url) if u}
        blocked_keys = set()
        for u in (url, direct_url, apply_url):
            if u:
                blocked_keys.update(block_keys_for_url(u))
        if blocked_keys & blocked_urls:
            skipped["blocked_url"] += 1
            continue
        if url_keys & existing_urls:
            skipped["existing_url"] += 1
            continue
        if looks_like_staffing(company, description):
            skipped["staffing"] += 1
            continue
        norm_title = normalize_title(title)
        if any(h in norm_title for h in IRRELEVANT_TITLE_HINTS):
            skipped["irrelevant_title"] += 1
            continue
        if is_excluded_title(title):
            skipped["management_track"] += 1
            continue
        if not location_matches_regions(location, regions):
            skipped["non_us_location"] += 1
            continue
        if requires_security_clearance(
            title=title,
            company=company,
            location=location,
            description=description,
            url=apply_url or direct_url or url,
        ):
            skipped["clearance_or_intel"] += 1
            continue
        if requires_excessive_experience(title=title, description=description):
            skipped["excessive_yoe"] += 1
            continue
        if requires_us_citizen_or_greencard(title=title, description=description):
            skipped["citizenship_or_greencard"] += 1
            continue
        if not is_relevant(title):
            skipped["not_relevant"] += 1
            continue

        candidates.append(item)

    qualified, merged_count = deduplicate_candidates(candidates)

    out_path = Path(args.out) if args.out else ROOT / "listings" / f"{date.today().isoformat()}-qualified.json"
    out_path.write_text(json.dumps(qualified, indent=2, default=str))

    log(f"input: {len(all_listings)} listings across {len(paths)} file(s)")
    for reason, count in skipped.items():
        if count:
            log(f"  skipped ({reason}): {count}")
    log(f"  merged as duplicates: {merged_count}")
    log(f"qualified: {len(qualified)} -> {out_path} (total {time.monotonic() - run_start:.1f}s)")


if __name__ == "__main__":
    main()
