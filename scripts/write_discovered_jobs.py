#!/usr/bin/env python3
"""Deterministically turn qualified listings into jobs.json entries.

Writing hundreds of complete, correctly-shaped JSON objects by hand in an
LLM turn is unreliable at this volume (observed: the agent wrote 321 new
entries missing the source/date_posted fields entirely, even though it had
clearly seen both values - it just didn't carry them into the structured
fields every time). The field mapping here is pure mechanical
transformation, so a script does it instead - every entry gets every
field, every time.

The full list of companies already tracked comes from the local Excel
tracker (application_tracker.xlsx, via scripts/tracker.py list-companies)
- that's a separate, equally mechanical step run just before this one and
passed here as --skip-companies.

Usage:
  python3 write_discovered_jobs.py QUALIFIED_FILE [--skip-companies PATH]

Appends one new jobs.json entry per qualifying listing (skipping any
company in --skip-companies or already present in jobs.json by URL) and
prints how many were added vs skipped.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
JOBS_FILE = ROOT / "jobs.json"
RESUMES_DIR = ROOT / "resumes"

sys.path.insert(0, str(Path(__file__).parent))
from jobs_lock import locked_jobs_for_write
from apply_urls import enrich_listing_urls, normalize_url  # noqa: E402
from text_normalize import normalize_company  # noqa: E402
from blocked_urls import block_keys_for_url, load_blocked_url_set  # noqa: E402
from multi_opening import detect_multi_opening  # noqa: E402
from discovery_filters import (  # noqa: E402
    auto_delete_reason,
    detect_work_mode,
    detect_work_mode_fallback,
    extract_inr_salary,
    extract_min_required_yoe,
    extract_min_required_yoe_fallback,
    extract_salary,
    extract_salary_fallback,
    region_for_location,
)
DESCRIPTION_PREVIEW_CHARS = 500


def trim_description(full_text: str) -> str:
    """Full JDs can run 1,500-3,000+ words and get pulled into context at
    multiple points if stored whole in jobs.json, for every one of
    potentially thousands of jobs. The full text is still needed for
    tailoring (see write_full_description), just not for every read of
    jobs.json - so only a short preview lives here."""
    if len(full_text) <= DESCRIPTION_PREVIEW_CHARS:
        return full_text
    cut = full_text.rfind(" ", 0, DESCRIPTION_PREVIEW_CHARS)
    if cut == -1:
        cut = DESCRIPTION_PREVIEW_CHARS
    return full_text[:cut] + " … [full text in resumes/<id>/jd_full.txt]"


def write_full_description(job_id: str, full_text: str) -> None:
    if not isinstance(full_text, str):
        full_text = "" if full_text is None else str(full_text)
    job_dir = RESUMES_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "jd_full.txt").write_text(full_text)


def slugify(text) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return text or "job"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    run_start = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument("qualified_file")
    parser.add_argument("--skip-companies", default=None,
                         help="JSON file: array of company names already tracked (scripts/tracker.py list-companies)")
    args = parser.parse_args()

    listings = json.loads(Path(args.qualified_file).read_text())

    skip_companies = set()
    if args.skip_companies and Path(args.skip_companies).exists():
        raw = json.loads(Path(args.skip_companies).read_text())
        skip_companies = {normalize_company(c) for c in raw}

    added, skipped_tracked, skipped_existing, skipped_blocked = 0, 0, 0, 0
    skipped_filtered: dict[str, int] = {}
    blocked_urls = load_blocked_url_set()
    with locked_jobs_for_write() as data:
        existing_ids = {j["id"] for j in data["jobs"]}
        existing_urls = set()
        for j in data["jobs"]:
            for f in ("job_url", "apply_url", "source_url"):
                if j.get(f):
                    existing_urls.add(normalize_url(j[f]) or j[f])
            for u in j.get("alternate_urls") or []:
                if u:
                    existing_urls.add(normalize_url(u) or u)

        for item in listings:
            company = item.get("company") or ""
            norm = normalize_company(company)
            enriched = enrich_listing_urls(item)
            url = enriched.get("job_url") or item.get("job_url") or ""
            direct_url = item.get("job_url_direct") or ""
            apply_url = enriched.get("apply_url") or ""

            if norm in skip_companies:
                skipped_tracked += 1
                continue
            url_keys = {normalize_url(u) or u for u in (url, direct_url, apply_url) if u}
            blocked_keys = set()
            for u in (url, direct_url, apply_url):
                if u:
                    blocked_keys.update(block_keys_for_url(u))
            if blocked_keys & blocked_urls:
                skipped_blocked += 1
                continue
            if url_keys & existing_urls:
                skipped_existing += 1
                continue

            job_id = f"{slugify(company)}-{slugify(item.get('title'))}"
            base_id = job_id
            n = 1
            while job_id in existing_ids:
                n += 1
                job_id = f"{base_id}-{n}"
            existing_ids.add(job_id)

            # Prefer company/ATS apply; keep aggregator as job_url / source_url.
            # Never leave apply_url empty when the listing had any URL.
            if not apply_url:
                apply_url = url or direct_url
            date_posted = item.get("date_posted")
            if date_posted in ("nan", "None", ""):
                date_posted = None
            # Approximate posted date derived from a relative "Posted N Days
            # Ago" string; only meaningful when there's no exact date.
            date_posted_fallback = item.get("date_posted_fallback")
            if date_posted or date_posted_fallback in ("nan", "None", ""):
                date_posted_fallback = None

            full_description = item.get("description") or ""
            title = item.get("title") or ""
            location = item.get("location") or ""
            # dedup_listings.py already applies these rules at qualify time,
            # but this script is also fed by hand-built listing files and by
            # per-ATS runs that skip that step, so re-check here rather than
            # trusting the caller.
            prune_reason = auto_delete_reason(
                title=title,
                location=location,
                company=company,
                description=full_description,
                url=apply_url or direct_url or url,
            )
            if prune_reason:
                skipped_filtered[prune_reason] = skipped_filtered.get(prune_reason, 0) + 1
                continue

            if full_description:
                write_full_description(job_id, full_description)

            work_mode = detect_work_mode(
                title=title, location=location, description=full_description
            )
            wm_fb = detect_work_mode_fallback(
                title=title, location=location, description=full_description
            )
            sal = extract_salary(title=title, description=full_description)
            sal_fb = extract_salary_fallback(
                title=title, description=full_description
            )
            # India LPA/lakh pay is display-only (never pruned); stamp when found.
            inr = extract_inr_salary(title=title, description=full_description)
            entry = {
                "id": job_id,
                "company": company,
                "title": title,
                "location": location,
                "source": item.get("site"),
                "date_posted": date_posted,
                "date_posted_fallback": date_posted_fallback,
                "job_url": url or apply_url,
                "apply_url": apply_url,
                "job_description": trim_description(full_description),
                "multi_opening": detect_multi_opening(
                    title=title, description=full_description
                ),
                "min_yoe": extract_min_required_yoe(
                    title=title, description=full_description
                ),
                "min_yoe_fallback": extract_min_required_yoe_fallback(
                    title=title, description=full_description
                ),
                "work_mode": work_mode,
                "work_mode_fallback": wm_fb if work_mode == "unknown" and wm_fb != "unknown" else None,
                "region": region_for_location(location),
                "salary_min": (sal or {}).get("min"),
                "salary_max": (sal or {}).get("max"),
                "salary_min_fallback": (sal_fb or {}).get("min"),
                "salary_max_fallback": (sal_fb or {}).get("max"),
                "salary_inr_display": (inr or {}).get("display"),
                "salary_inr_min_lpa": (inr or {}).get("min_lpa"),
                "salary_inr_max_lpa": (inr or {}).get("max_lpa"),
                "status": "discovered",
                "status_detail": f"New listing from {item.get('site')}, posted {date_posted or 'unknown date'}.",
                "question": None,
                "pending_command": None,
                "session_key": f"agent:job-hunter:job-{job_id}",
                "resume_path": None,
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "qa_log": [],
            }
            if enriched.get("source_url"):
                entry["source_url"] = enriched["source_url"]
            if enriched.get("alternate_urls"):
                entry["alternate_urls"] = enriched["alternate_urls"]
            source_names = item.get("source_names")
            if isinstance(source_names, list) and source_names:
                entry["source_names"] = [str(s) for s in source_names if s]
            elif item.get("site"):
                entry["source_names"] = [str(item.get("site"))]
            if isinstance(item.get("sources"), list) and item.get("sources"):
                entry["sources"] = item["sources"]
            if entry.get("source_names") and len(entry["source_names"]) > 1:
                entry["status_detail"] = (
                    f"New listing from {', '.join(entry['source_names'])}, "
                    f"posted {date_posted or 'unknown date'}."
                )
            data["jobs"].append(entry)
            for u in (url, direct_url, apply_url, enriched.get("source_url")):
                if u:
                    existing_urls.add(normalize_url(u) or u)
            added += 1

    log(f"added: {added}")
    if skipped_tracked:
        log(f"skipped (already tracked): {skipped_tracked}")
    if skipped_existing:
        log(f"skipped (already in jobs.json): {skipped_existing}")
    if skipped_blocked:
        log(f"skipped (user-deleted / blocked URL): {skipped_blocked}")
    for reason in sorted(skipped_filtered):
        log(f"skipped ({reason}): {skipped_filtered[reason]}")
    log(f"done (total {time.monotonic() - run_start:.2f}s)")


if __name__ == "__main__":
    main()
