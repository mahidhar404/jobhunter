#!/usr/bin/env python3
"""Fetch one job's record from jobs.json without reading the whole file.

jobs.json has grown to 800+ entries (2MB+) and keeps growing with every
discovery run - an agent turn reading the whole file to find its own one
record costs an enormous and ever-increasing number of tokens for
information that's a few hundred bytes. This is the same
"deterministic-script-instead-of-raw-file-access" pattern already used
for the Excel tracker (scripts/tracker.py) and session diagnostics
(scripts/session_timing_report.py), applied to jobs.json itself.

Usage:
  python3 get_job.py JOB_ID
    Prints that job's JSON object (pretty-printed) to stdout.
    Exits 1 with an error on stderr if not found.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from jobs_lock import locked_jobs_for_read


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: get_job.py JOB_ID", file=sys.stderr)
        sys.exit(1)
    job_id = sys.argv[1]
    with locked_jobs_for_read() as data:
        for job in data["jobs"]:
            if job["id"] == job_id:
                print(json.dumps(job, indent=2))
                return
    print(f"no job found with id {job_id!r}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
