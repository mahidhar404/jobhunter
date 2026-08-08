#!/usr/bin/env python3
"""One-shot (re-runnable) backfill for jobs missing job descriptions.

Walks jobs.json for entries with empty ``job_description`` and no
``resumes/<id>/jd_full.txt``, fetches JD text via source-specific public
APIs (SmartRecruiters / Rippling / Lever / Breezy) or
``extract_job_posting.extract`` as HTML fallback, then writes:

  * resumes/<id>/jd_full.txt
  * jobs.json job_description preview (≤500 chars, same trim as
    write_discovered_jobs.py)
  * recomputed min_yoe / min_yoe_fallback / work_mode / work_mode_fallback

Safety: HTTP/API first, then ``extract_job_posting`` (which may use
headless Chromium for JS-thin pages). Never submits, never CAPTCHA,
never uses applicant PII. Throttles between requests.

Usage:
  .venv/bin/python scripts/backfill_missing_jds.py [--dry-run] [--limit N]
  .venv/bin/python scripts/backfill_missing_jds.py --stamp-marker

Marker: logs/missing_jd_backfill_v1.done (skip auto-run once stamped).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS_FILE = ROOT / "jobs.json"
RESUMES_DIR = ROOT / "resumes"
LOGS_DIR = ROOT / "logs"
MARKER = LOGS_DIR / "missing_jd_backfill_v1.done"
DELAY_S = 0.35
DELAY_AFTER_EXTRACT_S = 1.5  # extract may launch headless Chromium
MIN_USEFUL_CHARS = 80

sys.path.insert(0, str(ROOT / "scripts"))
from jobs_lock import locked_jobs_for_write  # noqa: E402
from write_discovered_jobs import trim_description, write_full_description  # noqa: E402
from discovery_filters import (  # noqa: E402
    detect_work_mode,
    detect_work_mode_fallback,
    extract_min_required_yoe,
    extract_min_required_yoe_fallback,
    extract_salary,
    extract_salary_fallback,
)
from scrape_ats import (  # noqa: E402
    TransientFetchError,
    fetch_json,
    fetch_html,
    lever_compose_description,
    smartrecruiters_description_from_detail,
    rippling_description_from_detail,
    description_from_jobposting_ldjson,
)
from extract_job_posting import extract as extract_posting  # noqa: E402

SMARTRECRUITERS_RE = re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)/([^/?#]+)")
RIPPLING_RE = re.compile(r"ats\.rippling\.com/([^/?#]+)/jobs/([^/?#]+)")
LEVER_RE = re.compile(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]+)")
BREEZY_RE = re.compile(r"([a-z0-9-]+)\.breezy\.hr/p/([^/?#]+)")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def has_jd_full(job_id: str) -> bool:
    for name in ("jd_full.txt", "jd_full.md"):
        path = RESUMES_DIR / job_id / name
        if path.is_file() and path.stat().st_size > 0:
            try:
                if path.read_text(encoding="utf-8", errors="replace").strip():
                    return True
            except OSError:
                pass
    return False


def needs_jd(job: dict) -> bool:
    """True when preview is empty and no jd_full on disk."""
    if has_jd_full(job["id"]):
        return False
    return not (job.get("job_description") or "").strip()


def candidate_urls(job: dict) -> list[str]:
    urls: list[str] = []
    for key in ("apply_url", "job_url", "source_url"):
        u = job.get(key)
        if u and u not in urls:
            urls.append(u)
    for u in job.get("alternate_urls") or []:
        if u and u not in urls:
            urls.append(u)
    return urls


def fetch_via_source_api(url: str) -> str:
    """Prefer source-specific APIs; return empty string on miss."""
    m = SMARTRECRUITERS_RE.search(url or "")
    if m:
        slug, job_id = m.groups()
        try:
            detail = fetch_json(
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}"
            )
        except TransientFetchError:
            return ""
        if isinstance(detail, dict):
            return smartrecruiters_description_from_detail(detail)
        return ""

    m = RIPPLING_RE.search(url or "")
    if m:
        slug, uuid = m.groups()
        try:
            detail = fetch_json(
                f"https://ats.rippling.com/api/v1/board/{slug}/jobs/{uuid}"
            )
        except TransientFetchError:
            return ""
        if isinstance(detail, dict):
            return rippling_description_from_detail(detail)
        return ""

    m = LEVER_RE.search(url or "")
    if m:
        slug, posting_id = m.groups()
        try:
            detail = fetch_json(
                f"https://api.lever.co/v0/postings/{slug}/{posting_id}?mode=json"
            )
        except TransientFetchError:
            return ""
        if isinstance(detail, dict):
            return lever_compose_description(detail)
        return ""

    m = BREEZY_RE.search(url or "")
    if m:
        html = fetch_html(url)
        if html:
            return description_from_jobposting_ldjson(html)
        return ""

    return ""


def fetch_description_for_job(job: dict) -> tuple[str, str]:
    """Return (description, method) where method is api|extract|none."""
    for url in candidate_urls(job):
        text = fetch_via_source_api(url)
        if text and len(text.strip()) >= MIN_USEFUL_CHARS:
            return text.strip(), "api"
    for url in candidate_urls(job):
        # Skip known-unreachable without calling extract (LinkedIn etc.)
        try:
            result = extract_posting(url)
        except Exception as exc:  # noqa: BLE001 — backfill must keep going
            log(f"  extract error for {url}: {exc}")
            result = None
        if result and (result.get("description") or "").strip():
            text = result["description"].strip()
            if len(text) >= MIN_USEFUL_CHARS:
                return text, "extract"
    return "", "none"


def apply_jd_fields(job: dict, full_text: str) -> None:
    title = job.get("title") or ""
    location = job.get("location") or ""
    job["job_description"] = trim_description(full_text)
    work_mode = detect_work_mode(
        title=title, location=location, description=full_text
    )
    wm_fb = detect_work_mode_fallback(
        title=title, location=location, description=full_text
    )
    job["min_yoe"] = extract_min_required_yoe(title=title, description=full_text)
    job["min_yoe_fallback"] = extract_min_required_yoe_fallback(
        title=title, description=full_text
    )
    job["work_mode"] = work_mode
    job["work_mode_fallback"] = (
        wm_fb if work_mode == "unknown" and wm_fb != "unknown" else None
    )
    sal = extract_salary(title=title, description=full_text)
    sal_fb = extract_salary_fallback(title=title, description=full_text)
    job["salary_min"] = (sal or {}).get("min")
    job["salary_max"] = (sal or {}).get("max")
    job["salary_min_fallback"] = (sal_fb or {}).get("min")
    job["salary_max_fallback"] = (sal_fb or {}).get("max")
    job["updated_at"] = datetime.now(timezone.utc).isoformat()


def stamp_marker() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(
        json.dumps({
            "done_at": datetime.now(timezone.utc).isoformat(),
            "note": "missing JD backfill completed or skipped",
        }, indent=2)
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max jobs to attempt (0=all)")
    parser.add_argument(
        "--stamp-marker",
        action="store_true",
        help="Only write the done marker (used after a successful full run)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if marker exists",
    )
    args = parser.parse_args()

    if args.stamp_marker:
        stamp_marker()
        log(f"wrote {MARKER}")
        return 0

    if MARKER.exists() and not args.force and not args.dry_run:
        log(f"marker exists ({MARKER.name}); pass --force to re-run")
        return 0

    data = json.loads(JOBS_FILE.read_text()) if JOBS_FILE.exists() else {"jobs": []}
    before_by_source: Counter[str] = Counter()
    targets: list[dict] = []
    for job in data.get("jobs") or []:
        if needs_jd(job):
            src = job.get("source") or "unknown"
            before_by_source[src] += 1
            # Snapshot fields we need for fetch; mutate later under lock.
            targets.append({
                "id": job["id"],
                "source": src,
                "title": job.get("title") or "",
                "location": job.get("location") or "",
                "apply_url": job.get("apply_url"),
                "job_url": job.get("job_url"),
                "source_url": job.get("source_url"),
                "alternate_urls": list(job.get("alternate_urls") or []),
            })
    if args.limit and args.limit > 0:
        targets = targets[: args.limit]

    log(
        f"missing JD: {sum(before_by_source.values())} total "
        f"(attempting {len(targets)}); by source={dict(before_by_source)}"
    )

    filled = 0
    failed = 0
    filled_by_source: Counter[str] = Counter()
    failed_by_source: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    # job_id -> full text
    fetched: dict[str, str] = {}

    for i, job in enumerate(targets, 1):
        job_id = job["id"]
        src = job["source"]
        log(f"[{i}/{len(targets)}] {src} {job_id}")
        if args.dry_run:
            continue
        text, method = fetch_description_for_job(job)
        time.sleep(DELAY_AFTER_EXTRACT_S if method == "extract" else DELAY_S)
        if not text:
            failed += 1
            failed_by_source[src] += 1
            method_counts["none"] += 1
            log("  FAIL (no description)")
            continue
        write_full_description(job_id, text)
        fetched[job_id] = text
        filled += 1
        filled_by_source[src] += 1
        method_counts[method] += 1
        log(f"  OK via {method} ({len(text)} chars)")

    if fetched and not args.dry_run:
        with locked_jobs_for_write() as locked:
            by_id = {j["id"]: j for j in locked.get("jobs") or []}
            for job_id, text in fetched.items():
                job = by_id.get(job_id)
                if not job:
                    continue
                apply_jd_fields(job, text)

    remaining_by_source: Counter[str] = Counter()
    data = json.loads(JOBS_FILE.read_text()) if JOBS_FILE.exists() else {"jobs": []}
    for job in data.get("jobs") or []:
        if needs_jd(job):
            remaining_by_source[job.get("source") or "unknown"] += 1

    summary = {
        "before_total": sum(before_by_source.values()),
        "before_by_source": dict(before_by_source),
        "attempted": len(targets),
        "filled": filled if not args.dry_run else 0,
        "failed": failed if not args.dry_run else 0,
        "filled_by_source": dict(filled_by_source),
        "failed_by_source": dict(failed_by_source),
        "methods": dict(method_counts),
        "remaining_total": sum(remaining_by_source.values()),
        "remaining_by_source": dict(remaining_by_source),
        "dry_run": args.dry_run,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOGS_DIR / "missing_jd_backfill_report.json"
    report_path.write_text(json.dumps(summary, indent=2) + "\n")
    log(f"report: {report_path}")
    log(
        f"filled={summary['filled']} failed={summary['failed']} "
        f"remaining={summary['remaining_total']} "
        f"by_source_remaining={summary['remaining_by_source']}"
    )

    if not args.dry_run and args.limit == 0:
        stamp_marker()
        log(f"marker: {MARKER}")

    return 0 if summary["failed"] == 0 or summary["filled"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
