#!/usr/bin/env python3
"""One-time backfill: park pre-resume_ready generate-only jobs on resume_ready.

Before ``_complete_resume_only`` landed, generate-only runs ended on
``discovered`` with a resume on disk and "Fill not started" detail. This
script moves those jobs into the IN PROGRESS bucket (``resume_ready``).
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))

import server as dashboard_server  # noqa: E402
from jobs_lock import locked_jobs_for_write  # noqa: E402

# Active pipeline / terminal — never touch.
SKIP_STATUSES = frozenset(
    {
        "applied",
        "deleted",
        "ready_for_review",
        "stuck",
        "blocked_captcha",
        "tailoring",
        "navigating",
        "filling",
        "resuming",
        "resume_ready",
    }
)

# Wrong bucket after generate-only completed.
WRONG_STATUSES = frozenset({"discovered", "open"})

_RESUME_NAME_RE = re.compile(r"\(([^)]+\.pdf)\)", re.IGNORECASE)


def _resume_label(job: dict) -> str | None:
    by_co = str(job.get("resume_by_company_path") or "").strip()
    if by_co:
        return Path(by_co).name
    detail = str(job.get("status_detail") or "")
    m = _RESUME_NAME_RE.search(detail)
    if m:
        return m.group(1)
    rp = str(job.get("resume_path") or "").strip()
    if rp:
        return Path(rp).name
    disk = dashboard_server.resolve_job_resume_file(job)
    return disk.name if disk else None


def _looks_generate_only(job: dict) -> bool:
    detail = (job.get("status_detail") or "").lower()
    if "fill not started" in detail or "fill will not start" in detail:
        return True
    events = job.get("timeline") or []
    if not isinstance(events, list):
        return False
    text = " ".join(
        f"{e.get('event', '')} {e.get('detail', '')}" for e in events
    ).lower()
    if "fill skipped" in text:
        return True
    if "fill not started" in text:
        return True
    return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    backfilled: list[dict] = []
    with locked_jobs_for_write() as data:
        for job in data.get("jobs") or []:
            status = (job.get("status") or "").strip()
            if status in SKIP_STATUSES or status not in WRONG_STATUSES:
                continue
            if dashboard_server.resolve_job_resume_file(job) is None:
                continue
            if not _looks_generate_only(job):
                continue

            label = _resume_label(job)
            name_bit = f" ({label})" if label else ""
            new_detail = f"[REAL] Resume ready{name_bit}. Fill when you want."
            job["status"] = "resume_ready"
            job["status_detail"] = new_detail
            job["updated_at"] = _now_iso()
            dashboard_server.sync_job_resume_on_disk(job)

            timeline = job.get("timeline")
            if not isinstance(timeline, list):
                timeline = []
                job["timeline"] = timeline
            timeline.append(
                {
                    "ts": _now_iso(),
                    "event": "resume_ready",
                    "detail": f"Backfilled to resume_ready{name_bit} — fill when you want.",
                }
            )

            backfilled.append(
                {
                    "id": job.get("id"),
                    "company": job.get("company"),
                    "resume_label": label,
                }
            )

    print(f"Backfilled {len(backfilled)} job(s) to resume_ready:")
    for row in backfilled:
        print(f"  - {row['id']} ({row['company']}) — {row['resume_label'] or 'resume.pdf'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
