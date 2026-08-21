#!/usr/bin/env python3
"""Backfill ``date_posted`` / ``date_posted_fallback`` for undated LinkedIn jobs.

HTTP-parallel (cookies from ``linkedin_resolve_profile``) — same path as apply
resolve, no CDP. Does not hang the machine: bounded concurrency, ``--limit``,
and per-job commits so a stop mid-run keeps progress.

Exact ld+json ``datePosted`` beats relative Posted/Reposted approx. Never
overwrites an existing exact date with a weaker signal.

Usage:
  .venv/bin/python scripts/backfill_linkedin_posted_dates.py --dry-run --limit 20
  .venv/bin/python scripts/backfill_linkedin_posted_dates.py --limit 50
  .venv/bin/python scripts/backfill_linkedin_posted_dates.py
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS_FILE = ROOT / "jobs.json"
LOGS_DIR = ROOT / "logs"
MARKER = LOGS_DIR / "linkedin_posted_date_backfill_v1.done"

sys.path.insert(0, str(ROOT / "scripts"))
from jobs_lock import locked_jobs_for_read, locked_jobs_for_write  # noqa: E402
from posted_date import apply_posted_dates, extract_date_posted  # noqa: E402

SKIP_STATUSES = {"deleted", "tombstoned"}
DEFAULT_WORKERS = 12
MAX_WORKERS = 24


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def linkedin_job_url(job: dict) -> str | None:
    try:
        from linkedin_resolve_apply import job_linkedin_url

        return job_linkedin_url(job)
    except ImportError:
        for key in ("apply_url", "job_url", "source_url"):
            u = str(job.get(key) or "").strip()
            if "linkedin.com" in u and "/jobs/" in u:
                return u
        return None


def needs_posted_date(job: dict) -> bool:
    if job.get("status") in SKIP_STATUSES:
        return False
    if job.get("date_posted") or job.get("date_posted_fallback"):
        return False
    return linkedin_job_url(job) is not None


def collect_targets() -> tuple[list[dict], int]:
    with locked_jobs_for_read() as data:
        jobs = data.get("jobs") or []
    targets = [
        {
            "id": j["id"],
            "url": linkedin_job_url(j),
            "company": j.get("company") or "",
        }
        for j in jobs
        if needs_posted_date(j)
    ]
    return targets, len(targets)


def commit(job_id: str, exact: str | None, approx: str | None) -> bool:
    with locked_jobs_for_write() as data:
        for job in data.get("jobs") or []:
            if job.get("id") != job_id:
                continue
            if job.get("status") in SKIP_STATUSES:
                return False
            return apply_posted_dates(
                job, exact, approx, source="linkedin_http_backfill"
            )
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


def _fetch_one(session, cookies: dict, url: str) -> tuple[str | None, str | None, str | None]:
    """Return (exact, approx, error)."""
    from linkedin_resolve_apply import http_fetch_linkedin_job

    fetched = http_fetch_linkedin_job(url, cookies=cookies, session=session)
    if fetched.get("authwall") or fetched.get("error") == "no_li_at_cookie":
        return None, None, "authwall"
    if not fetched.get("ok") or not fetched.get("html"):
        return None, None, str(fetched.get("error") or "http_error")
    exact, approx = extract_date_posted(str(fetched.get("html") or ""))
    return exact, approx, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max jobs this run (0=all)")
    parser.add_argument(
        "--id",
        action="append",
        dest="ids",
        metavar="JOB_ID",
        help="Only these job ids (repeatable)",
    )
    parser.add_argument("--force", action="store_true", help="Ignore done marker")
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"HTTP concurrency (default {DEFAULT_WORKERS}, max {MAX_WORKERS})",
    )
    args = parser.parse_args()

    if MARKER.exists() and not args.force and not args.dry_run:
        log(f"marker exists ({MARKER.name}); pass --force to re-run")
        return 0

    try:
        from linkedin_resolve_apply import (
            build_linkedin_http_session,
            clamp_http_concurrency,
        )
        from linkedin_resolve_profile import profile_has_li_at
    except ImportError as e:
        log(f"linkedin resolve unavailable: {e}")
        return 1

    if not profile_has_li_at():
        log("no li_at cookie — run ./open_linkedin_resolve.sh and log in first")
        return 1

    targets, total = collect_targets()
    if args.ids:
        wanted = set(args.ids)
        targets = [t for t in targets if t["id"] in wanted]
        missing = wanted - {t["id"] for t in targets}
        if missing:
            log(
                f"warn: not backfillable (already dated, deleted, or not LinkedIn): "
                f"{sorted(missing)}"
            )
    if args.limit and args.limit > 0:
        targets = targets[: args.limit]
    workers = clamp_http_concurrency(args.workers)
    workers = max(1, min(MAX_WORKERS, workers))

    log(
        f"LinkedIn jobs missing a posted date: {total} "
        f"(attempting {len(targets)}, workers={workers}"
        f"{' — dry run' if args.dry_run else ''})"
    )
    if not targets:
        return 0

    sess, cookies = build_linkedin_http_session()
    if not cookies.get("li_at"):
        log("no li_at after session build")
        return 1

    outcomes: Counter[str] = Counter()

    def work(target: dict) -> tuple[str, str | None, str | None, str | None]:
        exact, approx, err = _fetch_one(sess, cookies, target["url"])
        return target["id"], exact, approx, err

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, t): t for t in targets}
        for i, fut in enumerate(as_completed(futures), 1):
            jid, exact, approx, err = fut.result()
            if err == "authwall":
                outcomes["authwall"] += 1
                log(f"[{i}/{len(targets)}] {jid}: authwall / login required")
                continue
            if err:
                outcomes["fetch_failed"] += 1
                log(f"[{i}/{len(targets)}] {jid}: fetch failed ({err})")
                continue
            if not exact and not approx:
                outcomes["no_date_on_page"] += 1
                log(f"[{i}/{len(targets)}] {jid}: no date on page")
                continue
            kind = "exact" if exact else "approx"
            value = exact or approx
            if args.dry_run:
                outcomes[f"would_fill_{kind}"] += 1
                log(f"[{i}/{len(targets)}] {jid}: would set {kind} {value}")
                continue
            if commit(jid, exact, approx):
                outcomes[f"filled_{kind}"] += 1
                log(f"[{i}/{len(targets)}] {jid}: {kind} {value}")
            else:
                outcomes["skipped_changed"] += 1
                log(f"[{i}/{len(targets)}] {jid}: skipped (changed under us)")

    remaining = remaining_count()
    log(f"done: {dict(outcomes)}")
    log(f"LinkedIn jobs still missing a posted date: {remaining}")
    if not args.dry_run and remaining == 0:
        stamp_marker(
            sum(v for k, v in outcomes.items() if k.startswith("filled_")),
            remaining,
        )
        log(f"wrote {MARKER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
