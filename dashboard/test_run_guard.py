#!/usr/bin/env python3
"""Tests for the local flock double-start guard."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import run_guard  # noqa: E402


def test_lock_reports_running_only_while_held():
    with tempfile.TemporaryDirectory() as td:
        with mock.patch.object(run_guard, "LOCK_DIR", Path(td)):
            key = "agent:job-hunter:job-123"
            assert run_guard.is_locked(key) is False
            with run_guard.session_lock(key) as got:
                assert got is True
                # While held (by us), is_locked reports running.
                assert run_guard.is_locked(key) is True
            # Released after the context exits.
            assert run_guard.is_locked(key) is False


def test_distinct_keys_independent():
    with tempfile.TemporaryDirectory() as td:
        with mock.patch.object(run_guard, "LOCK_DIR", Path(td)):
            with run_guard.session_lock("agent:job-hunter:discovery"):
                assert run_guard.is_locked("agent:job-hunter:discovery") is True
                assert run_guard.is_locked("agent:job-hunter:job-999") is False


def test_lock_never_raises_on_weird_key():
    with tempfile.TemporaryDirectory() as td:
        with mock.patch.object(run_guard, "LOCK_DIR", Path(td)):
            key = "weird/../key with spaces:and*chars"
            with run_guard.session_lock(key) as got:
                assert got is True
                assert run_guard.is_locked(key) is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("all run_guard tests passed")
