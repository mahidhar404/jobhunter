#!/usr/bin/env python3
"""Local double-start guard via ``fcntl.flock`` lock files.

Replaces the OpenClaw ``sessions list`` round-trip that
``gateway_running_session_keys`` used to make. That call only ever existed to
catch a turn still alive on the gateway after the CLI client that started it
had exited — an OpenClaw-specific concern. Now that the dashboard owns the
runner (in-process agent turns + tracked subprocesses), local state is
authoritative.

This adds a cross-process belt-and-suspenders on top of the in-process
tracking: an advisory ``flock`` per session key (same pattern as
``scripts/jobs_lock.py``). If a second dashboard process (or a stray worker)
holds a session's lock, ``is_locked`` reports it as running, preserving the
"no overlapping discovery/fill runs" guarantee across processes too. The lock
releases automatically if the holder crashes (OS-level flock semantics).
"""
from __future__ import annotations

import fcntl
import re
import threading
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_DIR = ROOT / "logs" / "run_locks"

# Track fds we hold in THIS process so is_locked() doesn't misreport our own
# held lock as "free" (flock is per-open-file-description; a second attempt to
# LOCK_EX|LOCK_NB the same path from the same process can still succeed).
_own_lock = threading.Lock()
_own_keys: set[str] = set()


def _lock_path(session_key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_key) or "session"
    return LOCK_DIR / f"{safe}.lock"


def is_locked(session_key: str) -> bool:
    """True if this session key is currently guarded (by us or another proc)."""
    with _own_lock:
        if session_key in _own_keys:
            return True
    path = _lock_path(session_key)
    if not path.exists():
        return False
    try:
        with open(path, "r+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True  # someone else holds it
            fcntl.flock(fh, fcntl.LOCK_UN)
            return False
    except OSError:
        return False


@contextmanager
def session_lock(session_key: str):
    """Hold an advisory exclusive lock for the duration of a run.

    Non-fatal if the lock can't be taken (yields anyway) — the in-process
    registry remains the primary guard; this is a cross-process backstop.
    """
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = _lock_path(session_key)
    fh = None
    got = False
    try:
        fh = open(path, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            got = True
            with _own_lock:
                _own_keys.add(session_key)
        except OSError:
            got = False
        yield got
    finally:
        with _own_lock:
            _own_keys.discard(session_key)
        if fh is not None:
            try:
                if got:
                    fcntl.flock(fh, fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()
