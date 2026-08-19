#!/usr/bin/env python3
"""Backfill applied_address on in-progress resume-generated jobs.

Re-resolves from resume header city via the apartment bank (same path as
pick_address.py / backfill_applied_addresses.py). Overwrites stale generated
placeholders so fill prep uses real apartment-style addresses with
geographically valid ZIP codes.

Targets jobs parked in the In Progress pipeline (``resume_ready``, live fill
states, stuck/CAPTCHA, ready_for_review) that have a resume on disk.

Usage:
  python3 scripts/backfill_in_progress_applied_addresses.py [--dry-run] [--job-id ID]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESUMES_DIR = ROOT / "resumes"
FASTFILL_DIR = ROOT / "scripts" / "fastfill"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(FASTFILL_DIR))

from jobs_lock import locked_jobs_for_write  # noqa: E402
from address_resolver import resolve_address_for_resume  # noqa: E402
from field_map import format_address_line  # noqa: E402
from pick_address import address_rng_for_job  # noqa: E402

IN_PROGRESS_STATUSES = frozenset(
    {
        "resume_ready",
        "filling",
        "navigating",
        "tailoring",
        "resuming",
        "stuck",
        "blocked_captcha",
        "ready_for_review",
    }
)


def _find_resume(job: dict) -> Path | None:
    """Locate resume.tex or resume.pdf for a job (prefer .tex for city parsing)."""
    dirs: list[Path] = []
    resume_path = job.get("resume_path")
    if resume_path:
        rp = Path(resume_path)
        dirs.append(ROOT / rp.parent if not rp.is_absolute() else rp.parent)
    job_id = job.get("id")
    if job_id:
        dirs.append(RESUMES_DIR / job_id)
    seen: set[Path] = set()
    for directory in dirs:
        if directory in seen:
            continue
        seen.add(directory)
        tex = directory / "resume.tex"
        pdf = directory / "resume.pdf"
        if tex.is_file():
            return tex
        if pdf.is_file():
            return pdf
    return None


def _resolve_address(job: dict) -> str | None:
    resume = _find_resume(job)
    if not resume:
        return None
    jid = str(job.get("id") or "")
    try:
        pick = resolve_address_for_resume(
            resume,
            fallback_location=str(job.get("location") or ""),
            rng=address_rng_for_job(jid or None),
        )
    except Exception as exc:
        print(f"warn: address resolve failed for {jid}: {exc}")
        return None
    return format_address_line(pick) or None


def backfill(dry_run: bool = False, job_ids: set[str] | None = None) -> dict:
    stats = {
        "checked": 0,
        "unchanged": 0,
        "backfilled": 0,
        "skipped": 0,
    }
    pending: list[tuple[str, str, str]] = []

    with locked_jobs_for_write() as data:
        jobs = data.get("jobs") or []
        for job in jobs:
            status = (job.get("status") or "").strip()
            if status not in IN_PROGRESS_STATUSES:
                continue
            jid = job.get("id") or ""
            if job_ids and jid not in job_ids:
                continue
            if _find_resume(job) is None:
                continue
            stats["checked"] += 1
            existing = (job.get("applied_address") or "").strip()
            address = _resolve_address(job)
            if not address:
                stats["skipped"] += 1
                print(f"skip: {jid} — could not resolve address")
                continue
            if existing == address:
                stats["unchanged"] += 1
                continue
            pending.append((jid, existing, address))
            if not dry_run:
                job["applied_address"] = address
                job["updated_at"] = datetime.now(timezone.utc).isoformat()
                stats["backfilled"] += 1
                print(f"backfill: {jid} -> {address}")
            else:
                stats["backfilled"] += 1
                old_bit = existing or "(none)"
                print(f"would backfill: {jid} | {old_bit} -> {address}")

    if dry_run:
        stats["pending"] = pending
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--job-id", action="append", default=[], help="Only these job ids")
    args = parser.parse_args()
    ids = set(args.job_id) if args.job_id else None
    stats = backfill(dry_run=args.dry_run, job_ids=ids)
    label = "dry-run" if args.dry_run else "backfill"
    print(f"{label} complete:", stats)


if __name__ == "__main__":
    main()
