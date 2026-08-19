#!/usr/bin/env python3
"""Shared jobs.json file locking, used by every process that reads or
writes it - the dashboard server, get_job.py, update_job.py, and
write_discovered_jobs.py.

Why this exists: update_job.py and write_discovered_jobs.py each do a
plain read-json / mutate / write-json round trip as a separate OS
process, with zero coordination between them. Two of those overlapping
(a job's agent turn calling update_job.py while a discovery run's
write_discovered_jobs.py is also writing) is a real read-modify-write
race - whichever finishes last wins and silently discards the other's
change. The dashboard used to sidestep this by only ever allowing one
job (or discovery) to run at a time, but that also blocked the very
per-job concurrency the pipeline is designed for. A real file lock
(fcntl.flock, OS-level, released automatically even if a process
crashes) lets every writer safely queue on the same file instead of
needing to serialize the whole pipeline to avoid this.

Uses a sibling `.lock` file rather than locking jobs.json itself, so
locking never interferes with a plain, un-locked `read_text()` of the
data file elsewhere (e.g. a quick manual look) - anything that mutates
the file should go through here.
"""
import fcntl
import json
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

JOBS_FILE = Path(__file__).parent.parent / "jobs.json"
LOCK_FILE = JOBS_FILE.with_suffix(".json.lock")
_AUTO_BACKUP_KEEP = 5
# Refuse snapshot writes that empty or halve a non-trivial on-disk list
# unless the caller opts into an intentional purge (empty-deleted).
_COLLAPSE_MIN_ON_DISK = 10
_COLLAPSE_RATIO = 0.5


class JobsWriteRefused(RuntimeError):
    """Raised when a write would empty or collapse jobs.json unsafely."""


def backup_jobs_file() -> None:
    """Snapshot jobs.json before a destructive write (best-effort)."""
    if not JOBS_FILE.is_file():
        return
    try:
        if JOBS_FILE.stat().st_size <= 0:
            return
    except OSError:
        return
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    bak = JOBS_FILE.with_name(f"jobs.json.bak-auto-{ts}")
    try:
        shutil.copy2(JOBS_FILE, bak)
    except OSError:
        return
    backups = sorted(
        JOBS_FILE.parent.glob("jobs.json.bak-auto-*"),
        key=lambda p: p.stat().st_mtime if p.is_file() else 0,
        reverse=True,
    )
    for old in backups[_AUTO_BACKUP_KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass


def jobs_list_count(data: dict | None) -> int:
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return len(jobs) if isinstance(jobs, list) else 0


def refuse_jobs_collapse(
    on_disk_n: int,
    new_n: int,
    *,
    allow_purge: bool = False,
) -> None:
    """Raise JobsWriteRefused when new_n empties or collapses on-disk jobs.

    Corrupt reads that yield [] must not be silently written back over a
    populated jobs.json. Intentional bulk purges (empty-deleted) pass
    allow_purge=True.
    """
    if allow_purge:
        return
    if on_disk_n <= 0:
        return
    if new_n <= 0:
        raise JobsWriteRefused(
            f"refusing to write empty jobs list over {on_disk_n} on-disk job(s); "
            "pass allow_purge=True for an intentional purge"
        )
    if (
        on_disk_n >= _COLLAPSE_MIN_ON_DISK
        and new_n < on_disk_n * _COLLAPSE_RATIO
    ):
        raise JobsWriteRefused(
            f"refusing to collapse jobs {on_disk_n} → {new_n}; "
            "pass allow_purge=True for an intentional purge"
        )


def _read_jobs_unlocked() -> dict:
    if not JOBS_FILE.exists():
        return {"jobs": []}
    try:
        data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise JobsWriteRefused(
            f"refusing to write over unreadable jobs.json ({e})"
        ) from e
    if not isinstance(data, dict):
        raise JobsWriteRefused("refusing to write over non-object jobs.json")
    if not isinstance(data.get("jobs"), list):
        data["jobs"] = []
    return data


@contextmanager
def locked_jobs_for_write(*, allow_purge: bool = False):
    """Exclusive lock for a read-modify-write. Blocks until any other
    reader or writer is done. Yields the parsed {"jobs": [...]} dict -
    mutate it in place; it's written back automatically on a clean exit
    (not written back if the block raises, so a half-done mutation never
    gets persisted).

    Collapse guard: refuses to persist an empty or dramatically smaller
    job list versus the count read at lock acquire, unless allow_purge.
    """
    LOCK_FILE.touch(exist_ok=True)
    with open(LOCK_FILE, "r+") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            data = _read_jobs_unlocked()
            on_disk_n = jobs_list_count(data)
            before = json.dumps(data, sort_keys=True, separators=(",", ":"))
            yield data
            after = json.dumps(data, sort_keys=True, separators=(",", ":"))
            if after == before:
                return
            refuse_jobs_collapse(
                on_disk_n, jobs_list_count(data), allow_purge=allow_purge
            )
            backup_jobs_file()
            data["revision"] = int(data.get("revision") or 0) + 1
            JOBS_FILE.write_text(
                json.dumps(data, separators=(",", ":")), encoding="utf-8"
            )
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


@contextmanager
def locked_jobs_for_read():
    """Shared lock for a read-only pass - multiple readers can hold this
    at once, but it still blocks until any in-progress writer is done, so
    a reader can never observe a half-written file."""
    LOCK_FILE.touch(exist_ok=True)
    with open(LOCK_FILE, "r+") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_SH)
        try:
            if not JOBS_FILE.exists():
                yield {"jobs": []}
            else:
                yield json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)
