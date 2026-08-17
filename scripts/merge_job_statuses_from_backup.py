#!/usr/bin/env python3
"""Overlay job statuses from backup jobs.json into the live file.

Keeps every job in the current jobs.json (e.g. full discovery set) and
restores workflow statuses (applied, stuck, ready_for_review, deleted)
from one or more backup snapshots matched by job id or normalized URL.

Usage:
  python3 scripts/merge_job_statuses_from_backup.py BACKUP [BACKUP...] [--dry-run]
  python3 scripts/merge_job_statuses_from_backup.py --from-zip ~/Desktop/Command\\ Center/job-hunter.zip
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from jobs_lock import locked_jobs_for_write  # noqa: E402

RESTORE_STATUSES = frozenset({"applied", "stuck", "ready_for_review", "deleted"})
APPLIED_FIELDS = (
    "status",
    "status_detail",
    "applied_at",
    "applied_address",
    "timeline",
    "updated_at",
    "resume_path",
    "resume_by_company_path",
    "file_id",
    "qa_log",
)


def _normalize_url(url: str | None) -> str:
    if not url:
        return ""
    raw = str(url).strip()
    if not raw:
        return ""
    parsed = urlparse(raw.lower())
    host = (parsed.hostname or "").removeprefix("www.")
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, host, path, "", "", ""))


def _job_urls(job: dict) -> set[str]:
    urls: set[str] = set()
    for key in ("apply_url", "job_url", "source_url", "url"):
        norm = _normalize_url(job.get(key))
        if norm:
            urls.add(norm)
    for alt in job.get("alternate_urls") or []:
        norm = _normalize_url(alt)
        if norm:
            urls.add(norm)
    for src in job.get("sources") or []:
        if isinstance(src, dict):
            for key in ("apply_url", "job_url", "url"):
                norm = _normalize_url(src.get(key))
                if norm:
                    urls.add(norm)
    return urls


def _load_jobs(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return data
    return list(data.get("jobs") or [])


def _load_backup(path: Path) -> list[dict]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in ("job-hunter/jobs.json", "jobs.json"):
                try:
                    with zf.open(name) as fh:
                        data = json.load(fh)
                    return data if isinstance(data, list) else list(data.get("jobs") or [])
                except KeyError:
                    continue
        raise SystemExit(f"no jobs.json found inside zip: {path}")
    return _load_jobs(path)


def _index_backups(backups: list[list[dict]]) -> dict[str, dict]:
    """Index restorable jobs by id only (URL matching caused false positives)."""
    by_id: dict[str, dict] = {}
    for jobs in backups:
        for job in jobs:
            status = job.get("status")
            if status not in RESTORE_STATUSES:
                continue
            jid = job.get("id")
            if jid and jid not in by_id:
                by_id[jid] = job
    return by_id


def _find_backup(job: dict, by_id: dict[str, dict]) -> dict | None:
    jid = job.get("id")
    if jid and jid in by_id:
        return by_id[jid]
    return None


def _overlay(job: dict, backup: dict) -> bool:
    status = backup.get("status")
    if status not in RESTORE_STATUSES:
        return False
    changed = False
    if job.get("status") != status:
        job["status"] = status
        changed = True
    if status == "applied":
        fields = APPLIED_FIELDS
    else:
        fields = ("status", "status_detail", "updated_at", "timeline", "qa_log")
    for key in fields:
        if key not in backup:
            continue
        val = backup.get(key)
        if job.get(key) != val:
            job[key] = val
            changed = True
    return changed


def merge_backups(backup_paths: list[Path], dry_run: bool = False) -> dict:
    backups = [_load_backup(path) for path in backup_paths]
    by_id = _index_backups(backups)

    stats = Counter()
    unmatched: list[str] = []

    if dry_run:
        current = _load_jobs(ROOT / "jobs.json")
        for job in current:
            backup = _find_backup(job, by_id)
            if not backup:
                continue
            status = backup.get("status")
            stats[f"would_restore_{status}"] += 1
            if job.get("status") != status:
                stats["would_change_status"] += 1
        for jid, backup in by_id.items():
            if not any(j.get("id") == jid for j in current):
                unmatched.append(jid)
                stats[f"backup_only_{backup.get('status')}"] += 1
        return dict(stats)

    with locked_jobs_for_write() as data:
        jobs = data.get("jobs") or []
        for job in jobs:
            backup = _find_backup(job, by_id)
            if not backup:
                continue
            if _overlay(job, backup):
                stats[f"restored_{backup.get('status')}"] += 1
        data["jobs"] = jobs

    current_ids = {j.get("id") for j in _load_jobs(ROOT / "jobs.json")}
    for jid, backup in by_id.items():
        if jid not in current_ids:
            unmatched.append(jid)
            stats[f"backup_only_{backup.get('status')}"] += 1

    stats["unmatched_backup_ids"] = len(unmatched)
    if unmatched:
        stats["unmatched_sample"] = unmatched[:10]
    return dict(stats)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backups", nargs="*", type=Path, help="Backup jobs.json or zip paths")
    parser.add_argument("--from-zip", type=Path, help="Shortcut for Command Center zip")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    paths: list[Path] = list(args.backups)
    if args.from_zip:
        paths.append(args.from_zip)
    if not paths:
        parser.error("provide at least one backup path or --from-zip")

    stats = merge_backups(paths, dry_run=args.dry_run)
    label = "dry-run" if args.dry_run else "merge"
    print(f"{label} complete:", json.dumps(stats, indent=2))

    if not args.dry_run:
        current = _load_jobs(ROOT / "jobs.json")
        counts = Counter(j.get("status") for j in current)
        print("current status counts:", dict(counts))


if __name__ == "__main__":
    main()
