"""Stamp-only jobs list helpers for GET /api/jobs.

LAW: never open resumes/<id>/jd_full.txt and never re-parse salary / YOE /
work_mode / clearance from JD text on this path. Persist + background
backfill stamp jobs.json; detail and /api/jobs/search may read jd_full.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any


_jobs_list_cache_lock = threading.Lock()
_jobs_list_cache: dict[str, Any] = {
    "mtime": None,
    "body_bytes": None,
    "etag": None,
    "fill_hold": None,
}


def invalidate_jobs_list_cache(cache: dict | None = None, lock: threading.Lock | None = None) -> None:
    target = cache if cache is not None else _jobs_list_cache
    with lock or _jobs_list_cache_lock:
        target.update(
            {"mtime": None, "body_bytes": None, "etag": None, "fill_hold": None}
        )


def slim_job_for_list(
    job: dict,
    *,
    resolve_resume_file: Callable[[dict], Path | None] | None = None,
    conventional_resume_filename: Callable[[dict], str] | None = None,
    root: Path | None = None,
) -> dict:
    """List payloads omit JD bodies — stamps from jobs.json only."""
    out = {
        k: v
        for k, v in job.items()
        if k not in ("job_description", "timeline")
    }
    has_desc = bool((job.get("job_description") or "").strip())
    out["has_description"] = has_desc
    stamped_incomplete = job.get("jd_incomplete")
    if isinstance(stamped_incomplete, bool):
        out["jd_incomplete"] = stamped_incomplete
    else:
        # No stamp yet: empty preview → incomplete; otherwise assume complete
        # until persist/backfill stamps (never open jd_full here).
        out["jd_incomplete"] = not has_desc

    if job.get("resume_on_disk") and resolve_resume_file is not None:
        disk = resolve_resume_file(job)
        resume_on_disk = disk is not None
    else:
        disk = None
        resume_on_disk = False
    out["resume_on_disk"] = resume_on_disk
    out["resume_display_name"] = (
        conventional_resume_filename(job)
        if resume_on_disk and conventional_resume_filename is not None
        else None
    )
    if not resume_on_disk:
        out["resume_path"] = None
    elif disk is not None and root is not None:
        try:
            out["resume_path"] = str(disk.relative_to(root))
        except ValueError:
            out["resume_path"] = str(disk)
    elif job.get("resume_path"):
        out["resume_path"] = job.get("resume_path")
    return out


def jobs_list_response(
    data: dict,
    *,
    fill_hold: bool,
    remember_runtime: Callable[[dict], None] | None = None,
    slim: Callable[[dict], dict] | None = None,
) -> dict:
    jobs = data.get("jobs") or []
    slim_fn = slim or (lambda j: slim_job_for_list(j))
    out_jobs = []
    for job in jobs:
        if remember_runtime is not None:
            remember_runtime(job)
        out_jobs.append(slim_fn(job))
    return {
        "jobs": out_jobs,
        "fill_hold_active": bool(fill_hold),
    }


def cached_jobs_list_response(
    *,
    jobs_file: Path,
    read_jobs: Callable[[], dict],
    fill_hold_active: Callable[[], bool],
    build_response: Callable[[dict, bool], dict],
    cache: dict | None = None,
    lock: threading.Lock | None = None,
) -> tuple[bytes, str]:
    """Build or reuse the slim /api/jobs body. Never takes a global jobs lock.

    Cache key is the mtime observed *before* ``read_jobs``. If the file
    changes during the read, we retry once so we never store a stale body
    under a newer mtime (TOCTOU).
    """
    target = cache if cache is not None else _jobs_list_cache
    cache_lock = lock or _jobs_list_cache_lock
    fill_hold = fill_hold_active()
    last_data: dict | None = None
    last_mtime = -1

    for _attempt in range(2):
        try:
            mtime = jobs_file.stat().st_mtime_ns
        except OSError:
            mtime = -1
        with cache_lock:
            cached_body = target.get("body_bytes")
            if (
                cached_body is not None
                and target.get("mtime") == mtime
                and target.get("fill_hold") == fill_hold
            ):
                return cached_body, str(target["etag"])

        data = read_jobs()
        last_data = data
        last_mtime = mtime
        try:
            mtime_after = jobs_file.stat().st_mtime_ns
        except OSError:
            mtime_after = -1
        if mtime_after != mtime:
            # Writer landed mid-read — retry with a fresh snapshot.
            continue

        revision = int(data.get("revision") or 0)
        body = json.dumps(
            build_response(data, fill_hold), separators=(",", ":")
        ).encode()
        etag = f'"{mtime:x}-{revision:x}-{1 if fill_hold else 0}"'
        with cache_lock:
            cached_body = target.get("body_bytes")
            if (
                cached_body is not None
                and target.get("mtime") == mtime
                and target.get("fill_hold") == fill_hold
            ):
                return cached_body, str(target["etag"])
            target.update(
                {"mtime": mtime, "body_bytes": body, "etag": etag, "fill_hold": fill_hold}
            )
        return body, etag

    # Unstable mtime after retry: return a correct body for this request
    # without poisoning the cache under a mismatched key.
    data = last_data if last_data is not None else read_jobs()
    revision = int(data.get("revision") or 0)
    body = json.dumps(
        build_response(data, fill_hold), separators=(",", ":")
    ).encode()
    etag = f'"{last_mtime:x}-{revision:x}-{1 if fill_hold else 0}"'
    return body, etag
