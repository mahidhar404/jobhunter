#!/usr/bin/env python3
"""One-shot (re-runnable) backfill of ``date_posted`` for Built In jobs.

Why this exists: ``scrape_builtin.parse_job_page`` hardcoded
``date_posted = None`` until the schema.org "datePosted" extraction landed,
so every Built In row ingested before that fix has a null posted date.
Discovery skips URLs it has already seen (``known_job_urls.py``), so those
rows are never re-fetched and would stay blank forever - the dashboard shows
them with an unknown posted date and the Posted sort parks them at the very
bottom regardless of how fresh they actually are.

What it does: for each Built In job with no posted date, re-fetch the detail
page through ``scrape_builtin.fetch_html`` (adaptive pacing, 429 backoff, one
headless Playwright fallback - never a CAPTCHA solve), extract the date, and
write it back. Exact "datePosted" goes to ``date_posted``; a relative
"Posted N Days Ago" string, when that's all the page has, goes to
``date_posted_fallback`` and renders with the "~" approximate marker.

Safety: writes only the two posted-date fields (plus the progress marker).
Never touches status, never resurrects deleted/tombstoned jobs, never writes
a field when no real date was found. Each job is committed under the shared
jobs.json lock as it completes, so interrupting mid-run keeps everything
already fetched and a re-run picks up exactly where it stopped.

Usage:
  .venv/bin/python scripts/backfill_builtin_posted_dates.py --dry-run --limit 20
  .venv/bin/python scripts/backfill_builtin_posted_dates.py --limit 20
  .venv/bin/python scripts/backfill_builtin_posted_dates.py          # full run
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS_FILE = ROOT / "jobs.json"
LOGS_DIR = ROOT / "logs"
MARKER = LOGS_DIR / "builtin_posted_date_backfill_v1.done"

sys.path.insert(0, str(ROOT / "scripts"))
from jobs_lock import locked_jobs_for_write, locked_jobs_for_read  # noqa: E402
import scrape_builtin  # noqa: E402

# A job already moved to deleted / tombstoned must not be touched at all -
# backfilling it would be a pointless fetch and risks looking like a revival.
SKIP_STATUSES = {"deleted", "tombstoned"}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def builtin_detail_url(job: dict) -> str | None:
    """The builtin.com/job/... detail URL for this job, if it has one."""
    candidates = [job.get("job_url"), job.get("source_url")]
    candidates.extend(job.get("alternate_urls") or [])
    for url in candidates:
        if url and "builtin.com/job/" in url:
            return url
    return None


def needs_posted_date(job: dict) -> bool:
    if job.get("status") in SKIP_STATUSES:
        return False
    if job.get("date_posted") or job.get("date_posted_fallback"):
        return False
    return builtin_detail_url(job) is not None


def collect_targets() -> tuple[list[dict], int]:
    """(targets, total_missing) - snapshots only what the fetch loop needs."""
    with locked_jobs_for_read() as data:
        jobs = data.get("jobs") or []
    targets = [
        {"id": j["id"], "url": builtin_detail_url(j), "company": j.get("company") or ""}
        for j in jobs
        if needs_posted_date(j)
    ]
    return targets, len(targets)


def commit(job_id: str, exact: str | None, approx: str | None) -> bool:
    """Write the posted date for one job. Returns False if it raced away."""
    with locked_jobs_for_write() as data:
        for job in data.get("jobs") or []:
            if job.get("id") != job_id:
                continue
            # Re-check under the lock: another writer may have filled this in,
            # or moved the job to a status we must not touch.
            if job.get("status") in SKIP_STATUSES:
                return False
            if job.get("date_posted") or job.get("date_posted_fallback"):
                return False
            if exact:
                job["date_posted"] = exact
            elif approx:
                job["date_posted_fallback"] = approx
            else:
                return False
            return True
    return False


def remaining_count() -> int:
    with locked_jobs_for_read() as data:
        return sum(1 for j in (data.get("jobs") or []) if needs_posted_date(j))


def stamp_marker(filled: int, remaining: int) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(
        json.dumps(
            {
                "done_at": datetime.now(timezone.utc).isoformat(),
                "filled": filled,
                "remaining": remaining,
            },
            indent=2,
        )
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report what would be written, without touching jobs.json",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Max jobs to attempt this run (0=all)"
    )
    parser.add_argument(
        "--id",
        action="append",
        dest="ids",
        metavar="JOB_ID",
        help="Only backfill these job ids (repeatable); useful for spot checks",
    )
    parser.add_argument(
        "--force", action="store_true", help="Run even if the done marker exists"
    )
    args = parser.parse_args()

    if MARKER.exists() and not args.force and not args.dry_run:
        log(f"marker exists ({MARKER.name}); pass --force to re-run")
        return 0

    targets, total = collect_targets()
    if args.ids:
        wanted = set(args.ids)
        targets = [t for t in targets if t["id"] in wanted]
        missing = wanted - {t["id"] for t in targets}
        if missing:
            log(f"warn: not backfillable (already dated, deleted, or not Built In): {sorted(missing)}")
    if args.limit and args.limit > 0:
        targets = targets[: args.limit]
    log(
        f"Built In jobs missing a posted date: {total} "
        f"(attempting {len(targets)}{' - dry run' if args.dry_run else ''})"
    )
    if not targets:
        return 0

    outcomes: Counter[str] = Counter()
    for i, target in enumerate(targets, 1):
        if i > 1:
            scrape_builtin.adaptive_sleep()
        html = scrape_builtin.fetch_html(target["url"])
        if not html:
            outcomes["fetch_failed"] += 1
            log(f"[{i}/{len(targets)}] {target['id']}: fetch failed")
            continue
        exact, approx = scrape_builtin.extract_date_posted(html)
        if not exact and not approx:
            outcomes["no_date_on_page"] += 1
            log(f"[{i}/{len(targets)}] {target['id']}: no date on page")
            continue
        kind = "exact" if exact else "approx"
        value = exact or approx
        if args.dry_run:
            outcomes[f"would_fill_{kind}"] += 1
            log(f"[{i}/{len(targets)}] {target['id']}: would set {kind} {value}")
            continue
        if commit(target["id"], exact, approx):
            outcomes[f"filled_{kind}"] += 1
            log(f"[{i}/{len(targets)}] {target['id']}: {kind} {value}")
        else:
            outcomes["skipped_changed"] += 1
            log(f"[{i}/{len(targets)}] {target['id']}: skipped (changed under us)")

    remaining = remaining_count()
    log(f"done: {dict(outcomes)}")
    log(f"Built In jobs still missing a posted date: {remaining}")
    if not args.dry_run and remaining == 0:
        stamp_marker(sum(v for k, v in outcomes.items() if k.startswith("filled_")), remaining)
        log(f"wrote {MARKER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
