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
from contextlib import contextmanager
from pathlib import Path

JOBS_FILE = Path(__file__).parent.parent / "jobs.json"
LOCK_FILE = JOBS_FILE.with_suffix(".json.lock")


@contextmanager
def locked_jobs_for_write():
    """Exclusive lock for a read-modify-write. Blocks until any other
    reader or writer is done. Yields the parsed {"jobs": [...]} dict -
    mutate it in place; it's written back automatically on a clean exit
    (not written back if the block raises, so a half-done mutation never
    gets persisted)."""
    LOCK_FILE.touch(exist_ok=True)
    with open(LOCK_FILE, "r+") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            data = json.loads(JOBS_FILE.read_text()) if JOBS_FILE.exists() else {"jobs": []}
            yield data
            JOBS_FILE.write_text(json.dumps(data, indent=2))
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
            yield json.loads(JOBS_FILE.read_text()) if JOBS_FILE.exists() else {"jobs": []}
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)
