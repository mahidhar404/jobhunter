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
    job_dir = RESUMES_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "jd_full.txt").write_text(full_text)


def normalize_company(name) -> str:
    name = str(name or "").lower()
    name = re.sub(r"\b(inc|llc|corp|corporation|ltd|co|company|group|technologies|technology)\b\.?", "", name)
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name


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

    added, skipped_tracked, skipped_existing = 0, 0, 0
    with locked_jobs_for_write() as data:
        existing_ids = {j["id"] for j in data["jobs"]}
        existing_urls = set()
        for j in data["jobs"]:
            for f in ("job_url", "apply_url"):
                if j.get(f):
                    existing_urls.add(j[f])

        for item in listings:
            company = item.get("company") or ""
            norm = normalize_company(company)
            url = item.get("job_url") or ""
            direct_url = item.get("job_url_direct") or ""

            if norm in skip_companies:
                skipped_tracked += 1
                continue
            if url in existing_urls or direct_url in existing_urls:
                skipped_existing += 1
                continue

            job_id = f"{slugify(company)}-{slugify(item.get('title'))}"
            base_id = job_id
            n = 1
            while job_id in existing_ids:
                n += 1
                job_id = f"{base_id}-{n}"
            existing_ids.add(job_id)

            apply_url = item.get("apply_url") or item.get("job_url_direct") or item.get("job_url") or ""
            date_posted = item.get("date_posted")
            if date_posted in ("nan", "None", ""):
                date_posted = None

            full_description = item.get("description") or ""
            if full_description:
                write_full_description(job_id, full_description)

            data["jobs"].append({
                "id": job_id,
                "company": company,
                "title": item.get("title") or "",
                "location": item.get("location") or "",
                "source": item.get("site"),
                "date_posted": date_posted,
                "job_url": url,
                "apply_url": apply_url,
                "job_description": trim_description(full_description),
                "status": "discovered",
                "status_detail": f"New listing from {item.get('site')}, posted {date_posted or 'unknown date'}.",
                "question": None,
                "pending_command": None,
                "session_key": f"agent:job-hunter:job-{job_id}",
                "resume_path": None,
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "qa_log": [],
            })
            existing_urls.add(url)
            if direct_url:
                existing_urls.add(direct_url)
            added += 1

    log(f"added: {added}")
    if skipped_tracked:
        log(f"skipped (already tracked): {skipped_tracked}")
    if skipped_existing:
        log(f"skipped (already in jobs.json): {skipped_existing}")
    log(f"done (total {time.monotonic() - run_start:.2f}s)")


if __name__ == "__main__":
    main()
