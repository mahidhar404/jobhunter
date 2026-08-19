#!/usr/bin/env python3
"""Stamp persisted resume_on_disk flags on every jobs.json record."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))

import server as dashboard_server  # noqa: E402
from jobs_lock import locked_jobs_for_write  # noqa: E402


def main() -> int:
    changed = 0
    present = 0
    with locked_jobs_for_write() as data:
        for job in data.get("jobs") or []:
            on_disk = dashboard_server.resolve_job_resume_file(job) is not None
            if job.get("resume_on_disk") is not on_disk:
                changed += 1
            job["resume_on_disk"] = on_disk
            present += int(on_disk)
        total = len(data.get("jobs") or [])
    print(
        f"Stamped resume_on_disk on {total} jobs "
        f"({present} present, {changed} changed)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
