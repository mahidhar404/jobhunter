#!/usr/bin/env python3
"""Deterministic dedup + qualify step for scraped job listings.

Merges one or more listings files (from scout.py and/or scrape_ats.py),
drops test/mock entries, indirect (staffing-agency) apply links, and
obviously irrelevant titles - then fuzzy-matches remaining listings by
(normalized company, normalized title) so the same role seen via two
different boards (e.g. Indeed AND the company's own
Greenhouse page) collapses into one entry. When a duplicate is found, the
direct-ATS-sourced copy (greenhouse/lever/ashby) wins over an aggregator
copy (indeed/linkedin) since it tends to have a cleaner apply_url and a
fuller description.

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
import difflib
import json
import re
import time
from datetime import date, datetime
from pathlib import Path


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
# The user wants hands-on IC roles, not management-track ones - a title
# containing any of these is a people-management or executive position
# regardless of the technical domain.
SENIORITY_EXCLUDE_HINTS = ["lead", "manager", "vice president"]
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
ATS_SOURCE_RANK = {"greenhouse": 0, "lever": 0, "ashby": 0, "indeed": 1, "linkedin": 1}


def is_relevant(title) -> bool:
    t = str(title or "").lower()
    return any(kw in t for kw in RELEVANT_KEYWORDS)


def normalize_company(name) -> str:
    name = str(name or "").lower()
    name = re.sub(r"\b(inc|llc|corp|corporation|ltd|co|company|group|technologies|technology)\b\.?", "", name)
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name


def normalize_title(title) -> str:
    title = str(title or "").lower()
    title = re.sub(r"[^a-z0-9 ]+", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def looks_like_staffing(company: str, description: str) -> bool:
    # Only the COMPANY NAME is a reliable signal here. Job descriptions from
    # real direct employers routinely include boilerplate like "we do not
    # accept submissions from staffing agencies" - matching against the
    # description text flags the employer for the exact opposite of what
    # the phrase means, so it's deliberately excluded.
    return any(h in str(company or "").lower() for h in STAFFING_DENY_HINTS)


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
            for f in ("job_url", "apply_url"):
                if job.get(f):
                    existing_urls.add(job[f])

    skipped = {"test": 0, "existing_url": 0, "staffing": 0, "irrelevant_title": 0,
               "not_relevant": 0, "no_company": 0, "management_track": 0}
    candidates = []
    for item in all_listings:
        if not isinstance(item, dict):
            continue  # this dir should only ever hold job-listing arrays, but be defensive
        company = item.get("company")
        title = item.get("title") or ""
        url = item.get("job_url") or ""
        direct_url = item.get("job_url_direct") or ""
        description = item.get("description") or ""

        if not company or str(company).lower() == "nan":
            skipped["no_company"] += 1
            continue
        text_blob = f"{title} {description}".lower()
        if any(h in text_blob for h in TEST_HINTS) or any(h in str(company).lower() for h in TEST_HINTS):
            skipped["test"] += 1
            continue
        if url in existing_urls or direct_url in existing_urls:
            skipped["existing_url"] += 1
            continue
        norm_company = normalize_company(company)
        if looks_like_staffing(company, description):
            skipped["staffing"] += 1
            continue
        norm_title = normalize_title(title)
        if any(h in norm_title for h in IRRELEVANT_TITLE_HINTS):
            skipped["irrelevant_title"] += 1
            continue
        if any(h in norm_title for h in SENIORITY_EXCLUDE_HINTS):
            skipped["management_track"] += 1
            continue
        if not is_relevant(title):
            skipped["not_relevant"] += 1
            continue

        candidates.append(item)

    # Fuzzy-merge duplicates: same normalized company + highly similar title.
    clusters: dict[str, list[dict]] = {}
    for item in candidates:
        key = normalize_company(item.get("company"))
        clusters.setdefault(key, []).append(item)

    qualified = []
    merged_count = 0
    for company_key, items in clusters.items():
        kept: list[dict] = []
        for item in items:
            title_norm = normalize_title(item.get("title"))
            match_idx = None
            for i, k in enumerate(kept):
                if difflib.SequenceMatcher(None, title_norm, normalize_title(k.get("title"))).ratio() >= 0.85:
                    match_idx = i
                    break
            if match_idx is None:
                kept.append(item)
            else:
                existing = kept[match_idx]
                existing_rank = ATS_SOURCE_RANK.get(existing.get("site", ""), 1)
                new_rank = ATS_SOURCE_RANK.get(item.get("site", ""), 1)
                if new_rank < existing_rank or (new_rank == existing_rank and len(item.get("description") or "") > len(existing.get("description") or "")):
                    kept[match_idx] = item
                merged_count += 1
        qualified.extend(kept)

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
