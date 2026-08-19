#!/usr/bin/env python3
"""Rebuild jobs.json after accidental wipe or corruption.

Replays every listings/*-qualified*.json archive through write_discovered_jobs.py,
then adds minimal discovered stubs for resume dirs that have no jobs.json row yet
(jd_full.txt on disk is the usual signal). Backs up the current file first.

Usage:
  python3 scripts/recover_jobs_json.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
JOBS_FILE = ROOT / "jobs.json"
LISTINGS_DIR = ROOT / "listings"
RESUMES_DIR = ROOT / "resumes"
WRITE_SCRIPT = ROOT / "scripts" / "write_discovered_jobs.py"

sys.path.insert(0, str(ROOT / "scripts"))
from jobs_lock import backup_jobs_file, locked_jobs_for_write  # noqa: E402
from blocked_urls import load_blocked_id_set  # noqa: E402
from text_normalize import stamp_company_key  # noqa: E402


def _slug_title_from_id(job_id: str) -> tuple[str, str]:
    """Best-effort company/title guess from a resume dir name."""
    parts = [p for p in job_id.split("-") if p]
    if not parts:
        return "Unknown", "Unknown"
    if len(parts) >= 2 and parts[-1].isdigit():
        parts = parts[:-1]
    if len(parts) == 1:
        return parts[0].replace("-", " ").title(), "Role"
    company = " ".join(parts[:2]).replace("-", " ").title()
    title = " ".join(parts[2:]).replace("-", " ").title() or "Role"
    return company, title


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _add_resume_dir_stubs(dry_run: bool) -> int:
    with locked_jobs_for_write() as data:
        existing = {j.get("id") for j in data.get("jobs") or [] if j.get("id")}
        blocked_ids = load_blocked_id_set()
        added = 0
        if not RESUMES_DIR.is_dir():
            return 0
        for job_dir in sorted(RESUMES_DIR.iterdir()):
            if not job_dir.is_dir() or job_dir.name.startswith("."):
                continue
            job_id = job_dir.name
            if job_id in existing or job_id in blocked_ids:
                continue
            if not (job_dir / "jd_full.txt").is_file() and not (job_dir / "jd.txt").is_file():
                continue
            company, title = _slug_title_from_id(job_id)
            entry = {
                "id": job_id,
                "company": company,
                "title": title,
                "location": "",
                "source": "recovered",
                "status": "discovered",
                "status_detail": "Recovered from resumes/<id>/ on disk after jobs.json loss.",
                "needs_url": True,
                "question": None,
                "pending_command": None,
                "session_key": f"agent:job-hunter:job-{job_id}",
                "resume_path": None,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "qa_log": [],
            }
            stamp_company_key(entry)
            pdf = job_dir / "resume.pdf"
            if pdf.is_file():
                try:
                    entry["resume_path"] = str(pdf.relative_to(ROOT))
                except ValueError:
                    entry["resume_path"] = str(pdf)
            data["jobs"].append(entry)
            existing.add(job_id)
            added += 1
        if dry_run:
            raise SystemExit(f"dry-run: would add {added} resume-dir stubs")
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not write")
    args = parser.parse_args()

    qualified = sorted(LISTINGS_DIR.glob("*-qualified*.json"))
    if not qualified:
        print("error: no listings/*-qualified*.json files found", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        nonempty = [p for p in qualified if p.stat().st_size > 10]
        print(f"dry-run: would replay {len(nonempty)} qualified listing files")
        return

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if JOBS_FILE.exists():
        pre = JOBS_FILE.with_name(f"jobs.json.bak-pre-recovery-{ts}")
        shutil.copy2(JOBS_FILE, pre)
        print(f"backed up current jobs.json → {pre.name}")

    JOBS_FILE.write_text(json.dumps({"jobs": []}, indent=2), encoding="utf-8")
    print("reset jobs.json to empty list")

    total_added = 0
    for path in qualified:
        if path.stat().st_size <= 10:
            continue
        print(f"replaying {path.name} …", flush=True)
        proc = subprocess.run(
            [sys.executable, str(WRITE_SCRIPT), str(path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(f"warn: {path.name} failed ({proc.returncode}): {proc.stderr.strip()}", flush=True)
            continue
        m = re.search(r"added:\s*(\d+)", proc.stdout)
        if m:
            n = int(m.group(1))
            total_added += n
            print(f"  added {n}", flush=True)
        else:
            print(proc.stdout.strip() or "(no output)", flush=True)

    stubs = _add_resume_dir_stubs(dry_run=False)
    print(f"resume-dir stubs added: {stubs}")

    data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    print(f"recovery complete: {len(data.get('jobs') or [])} jobs in jobs.json")


if __name__ == "__main__":
    main()
