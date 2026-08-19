#!/usr/bin/env python3
"""Tests for profile_disk_hygiene.py."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import profile_disk_hygiene as pdh  # noqa: E402
from profile_disk_hygiene import prune_stale_fill_profiles, trim_chrome_profile_cache  # noqa: E402


def test_trim_removes_cache_dirs() -> None:
    with tempfile.TemporaryDirectory() as td:
        profile = Path(td) / "ui_profile"
        cache = profile / "Default" / "Cache"
        cache.mkdir(parents=True)
        (cache / "data.bin").write_bytes(b"x" * 1024)
        res = trim_chrome_profile_cache(profile, force=True)
        assert res["removed"]
        assert not cache.exists()


def test_prune_keeps_newest_fill_profiles() -> None:
    with tempfile.TemporaryDirectory() as td:
        fill_root = Path(td) / "fills"
        fill_root.mkdir()
        old = fill_root / "job-old_run1"
        new = fill_root / "job-new_run2"
        old.mkdir()
        new.mkdir()
        old_t = time.time() - 30 * 86400
        (old / "x").write_text("1")
        (new / "x").write_text("1")
        os.utime(old, (old_t, old_t))
        orig = pdh.FILL_PROFILES_ROOT
        try:
            pdh.FILL_PROFILES_ROOT = fill_root
            res = prune_stale_fill_profiles(max_age_days=14, keep_newest=1)
            assert "job-old_run1" in res["removed"]
            assert new.exists()
        finally:
            pdh.FILL_PROFILES_ROOT = orig


if __name__ == "__main__":
    test_trim_removes_cache_dirs()
    test_prune_keeps_newest_fill_profiles()
    print("OK test_profile_disk_hygiene")
