#!/usr/bin/env python3
"""Undo deletions caused by a lane toggle rather than by the job itself.

The auto-delete sweep used to evaluate jobs against the lanes *currently*
switched on in the UI. Switching India off therefore deleted every India job
already discovered and — via block_deleted_job — tombstoned its URL, so
re-discovery would skip it permanently. The lane switches are a discovery
scope ("which boards to scrape"), never a retention policy.

This restores jobs whose only disqualification was the toggle: re-evaluated
against every valid lane, they qualify again. Jobs that fail for a real
reason (management track, US onsite/hybrid, excessive YOE, clearance…) stay
deleted. Their URL tombstones are lifted so discovery can see them again.

Usage:
  python3 restore_lane_swept_jobs.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from discovery_filters import auto_delete_reason  # noqa: E402
from jobs_lock import locked_jobs_for_write  # noqa: E402

VALID_LANES = ("india", "worldwide")
SWEEP_REASON = "non_us_location"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def restore(*, dry_run: bool = False) -> dict:
    restored: list[dict] = []
    kept = 0
    with locked_jobs_for_write() as data:
        for job in data.get("jobs") or []:
            if not isinstance(job, dict):
                continue
            if job.get("status") != "deleted":
                continue
            if job.get("deleted_reason") != SWEEP_REASON:
                continue
            desc = job.get("job_description") or ""
            reason = auto_delete_reason(
                title=job.get("title"),
                location=job.get("location"),
                company=job.get("company"),
                description=desc,
                url=job.get("apply_url") or job.get("job_url"),
                regions=VALID_LANES,
            )
            if reason:
                kept += 1          # fails for a real reason — leave deleted
                continue
            restored.append({
                "id": job.get("id"),
                "apply_url": job.get("apply_url"),
                "job_url": job.get("job_url"),
                "alternate_urls": list(job.get("alternate_urls") or []),
            })
            if dry_run:
                continue
            job["status"] = "discovered"
            job["status_detail"] = (
                "Restored — was removed only because its lane was toggled off."
            )
            job["updated_at"] = _now()
            for field in ("deleted_at", "deleted_reason"):
                job.pop(field, None)
        if dry_run:
            raise SystemExit(
                f"dry-run: would restore {len(restored)}, keep {kept} deleted")

    # Lift the URL tombstones so discovery stops skipping these postings.
    unblocked = 0
    from blocked_urls import unblock_job
    for snap in restored:
        try:
            res = unblock_job(snap)
            if res.get("urls") or res.get("ids") or res.get("tombstones_removed"):
                unblocked += 1
        except Exception as e:  # noqa: BLE001
            print(f"warn: unblock {snap.get('id')}: {e}")
    return {"restored": len(restored), "kept_deleted": kept,
            "unblocked": unblocked}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    res = restore(dry_run=args.dry_run)
    print(f"restored {res['restored']} job(s) swept by a lane toggle; "
          f"{res['kept_deleted']} stayed deleted for a real reason; "
          f"{res['unblocked']} URL tombstone(s) lifted")


if __name__ == "__main__":
    main()
