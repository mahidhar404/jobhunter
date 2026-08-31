#!/usr/bin/env python3
"""Stamp lane on existing jobs; prune US onsite/hybrid from Open.

Usage:
  python3 scripts/backfill_lanes.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from discovery_filters import auto_delete_reason, lane_for_job  # noqa: E402
from jobs_lock import locked_jobs_for_write  # noqa: E402

JOBS_FILE = ROOT / "jobs.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        raw = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        jobs = raw.get("jobs") or []
        lanes = {"india": 0, "worldwide": 0, "unknown": 0}
        prune = 0
        for job in jobs:
            if not isinstance(job, dict):
                continue
            lane = lane_for_job(
                job.get("location"),
                work_mode=job.get("work_mode"),
                title=job.get("title"),
                description=job.get("job_description"),
            )
            lanes[lane] = lanes.get(lane, 0) + 1
            if job.get("status") == "discovered" and auto_delete_reason(
                title=job.get("title"),
                location=job.get("location"),
                description=job.get("job_description"),
                work_mode=job.get("work_mode"),
            ) == "us_onsite_or_hybrid":
                prune += 1
        print(json.dumps({"dry_run": True, "lanes": lanes, "would_prune_us": prune}, indent=2))
        return

    stats = {"touched": 0, "pruned_us": 0, "lanes": {"india": 0, "worldwide": 0, "unknown": 0}}
    with locked_jobs_for_write() as data:
        jobs = data.get("jobs") or []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            location = job.get("location")
            work_mode = job.get("work_mode")
            title = job.get("title")
            description = job.get("job_description")
            lane = lane_for_job(
                location,
                work_mode=work_mode,
                title=title,
                description=description,
            )
            if job.get("lane") != lane or job.get("region") != lane:
                job["lane"] = lane
                job["region"] = lane
                stats["touched"] += 1
            stats["lanes"][lane] = stats["lanes"].get(lane, 0) + 1

            if job.get("status") == "discovered" and auto_delete_reason(
                title=title,
                location=location,
                company=job.get("company"),
                description=description,
                url=job.get("apply_url") or job.get("job_url"),
                job_type=job.get("job_type"),
                work_mode=work_mode,
            ) == "us_onsite_or_hybrid":
                job["status"] = "deleted"
                job["deleted_reason"] = "us_onsite_or_hybrid"
                job["status_detail"] = "US onsite/hybrid — not eligible"
                stats["pruned_us"] += 1
        data["jobs"] = jobs
    print(json.dumps({"ok": True, **stats}, indent=2))


if __name__ == "__main__":
    main()
