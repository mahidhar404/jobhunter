#!/usr/bin/env python3
"""Update one job's fields in jobs.json without reading/writing the whole
file from an agent turn.

jobs.json has grown to 800+ entries (2MB+) - a naive "read the whole
file, edit my one record, write the whole file back" pattern from inside
an agent turn would mean holding that entire file in context just to
change a status field, and that cost only grows with every discovery run.
This script does the read-modify-write as a plain subprocess (free,
deterministic, no LLM tokens involved) - the agent only ever passes a
handful of CLI args.

Usage:
  python3 update_job.py JOB_ID [--status S] [--status-detail D] \
      [--question Q] [--clear-question] [--pending-command C] \
      [--clear-pending-command] [--resume-path P] [--date-posted D] \
      [--company C] [--title T] [--location L] [--job-description D]

Only the fields you pass get changed; everything else stays as-is.
Always sets updated_at to now. This is for status/progress updates during
a turn (including filling in company/title/location/job_description for
a manually-added job) - it does NOT touch qa_log, which is the
dashboard's own answer-flow bookkeeping, not something a turn should
write directly.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from jobs_lock import locked_jobs_for_write
from multi_opening import apply_multi_opening_flag
from resume_publish import publish_resume_to_by_company

ROOT = Path(__file__).resolve().parent.parent


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--status", default=None)
    parser.add_argument("--status-detail", default=None)
    parser.add_argument("--question", default=None)
    parser.add_argument("--clear-question", action="store_true")
    parser.add_argument("--pending-command", default=None)
    parser.add_argument("--clear-pending-command", action="store_true")
    parser.add_argument("--resume-path", default=None)
    parser.add_argument("--date-posted", default=None)
    parser.add_argument("--company", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--location", default=None)
    parser.add_argument("--job-description", default=None)
    args = parser.parse_args()

    with locked_jobs_for_write() as data:
        job = next((j for j in data["jobs"] if j["id"] == args.job_id), None)
        if job is None:
            print(f"no job found with id {args.job_id!r}", file=sys.stderr)
            sys.exit(1)

        if args.status is not None:
            job["status"] = args.status
        if args.status_detail is not None:
            job["status_detail"] = args.status_detail
        if args.question is not None:
            job["question"] = args.question
        if args.clear_question:
            job["question"] = None
        if args.pending_command is not None:
            job["pending_command"] = args.pending_command
        if args.clear_pending_command:
            job["pending_command"] = None
        if args.resume_path is not None:
            job["resume_path"] = args.resume_path
            pdf = Path(args.resume_path)
            if not pdf.is_absolute():
                pdf = ROOT / pdf
            if pdf.is_file() and pdf.suffix.lower() == ".pdf":
                existing_ids = {j["file_id"] for j in data["jobs"] if j.get("file_id")}
                try:
                    publish_resume_to_by_company(
                        job,
                        pdf,
                        existing_file_ids=existing_ids,
                        root=ROOT,
                    )
                except Exception as e:
                    print(f"warn: by_company publish failed: {e}", file=sys.stderr)
        if args.date_posted is not None:
            job["date_posted"] = args.date_posted
        if args.company is not None:
            job["company"] = args.company
        if args.title is not None:
            job["title"] = args.title
        if args.location is not None:
            job["location"] = args.location
        if args.job_description is not None:
            job["job_description"] = args.job_description
        # Recompute when title or JD text may have changed (also picks up
        # resumes/<id>/jd_full.txt if present).
        if (
            args.title is not None
            or args.job_description is not None
            or "multi_opening" not in job
        ):
            apply_multi_opening_flag(job)
        job["updated_at"] = now_iso()
        status_for_print = job.get("status")

    print(f"updated {args.job_id}: status={status_for_print!r}")


if __name__ == "__main__":
    main()
