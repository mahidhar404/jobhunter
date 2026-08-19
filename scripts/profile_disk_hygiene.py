#!/usr/bin/env python3
"""Best-effort disk hygiene for local Chrome profiles and fill run dirs.

Safe to run while the dashboard UI is closed. UI cache trim is skipped when
the dedicated dashboard Chrome main process is already running.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "fastfill"))

from browser_launch import FILL_PROFILES_ROOT  # noqa: E402

UI_PROFILE = ROOT / "dashboard_ui_profile"
LEGACY_UI_PROFILE = ROOT / "dashboard_chrome_profile"
STAMP_FILE = ROOT / "logs" / ".profile_disk_hygiene_stamp"

# Under Chromium's Default/ profile — safe to delete; Chrome rebuilds on launch.
_CACHE_REL = (
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "GrShaderCache",
    "ShaderCache",
    "Service Worker/CacheStorage",
    "Service Worker/ScriptCache",
)

# Profile-root blobs Chromium can re-download (on-device models, hints).
_PROFILE_ROOT_BLOAT = (
    "OptGuideOnDeviceModel",
    "OptimizationHints",
    "BrowserMetrics-spare.pma",
)

_DEFAULT_MIN_HOURS = 12
_DEFAULT_CACHE_BYTES = 200 * 1024 * 1024  # trim when cache alone exceeds 200 MiB
_FILL_MAX_AGE_DAYS = 14
_FILL_KEEP_NEWEST = 25


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    try:
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    pass
    except OSError:
        return 0
    return total


def chrome_cache_bytes(profile_root: Path) -> int:
    default = profile_root / "Default"
    if not default.is_dir():
        total = 0
    else:
        total = sum(_dir_size(default / rel) for rel in _CACHE_REL)
    for rel in _PROFILE_ROOT_BLOAT:
        total += _dir_size(profile_root / rel)
    return total


def _should_trim(*, cache_bytes: int, min_hours: float, force: bool) -> bool:
    if force:
        return True
    if cache_bytes >= _DEFAULT_CACHE_BYTES:
        return True
    if not STAMP_FILE.is_file():
        return True
    try:
        age_h = (time.time() - STAMP_FILE.stat().st_mtime) / 3600
    except OSError:
        return True
    return age_h >= min_hours


def trim_chrome_profile_cache(
    profile_root: Path,
    *,
    min_hours: float = _DEFAULT_MIN_HOURS,
    force: bool = False,
) -> dict:
    """Remove rebuildable cache dirs under a Chromium user-data-dir."""
    out = {"profile": str(profile_root), "removed_bytes": 0, "removed": []}
    if not profile_root.is_dir():
        out["reason"] = "missing"
        return out
    cache_bytes = chrome_cache_bytes(profile_root)
    out["cache_bytes_before"] = cache_bytes
    if not _should_trim(cache_bytes=cache_bytes, min_hours=min_hours, force=force):
        out["skipped"] = "recent_trim"
        return out
    default = profile_root / "Default"
    for rel in _CACHE_REL:
        target = default / rel
        if not target.exists():
            continue
        before = _dir_size(target)
        try:
            shutil.rmtree(target)
            out["removed"].append(rel)
            out["removed_bytes"] += before
        except OSError:
            pass
    for rel in _PROFILE_ROOT_BLOAT:
        target = profile_root / rel
        if not target.exists():
            continue
        before = _dir_size(target)
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            out["removed"].append(rel)
            out["removed_bytes"] += before
        except OSError:
            pass
    if out["removed"]:
        try:
            STAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
            STAMP_FILE.write_text(str(int(time.time())), encoding="utf-8")
        except OSError:
            pass
    return out


def prune_stale_fill_profiles(
    *,
    max_age_days: int = _FILL_MAX_AGE_DAYS,
    keep_newest: int = _FILL_KEEP_NEWEST,
) -> dict:
    """Drop old per-run fill profile dirs; keep the newest N regardless of age."""
    out: dict = {"removed": [], "kept": 0}
    if not FILL_PROFILES_ROOT.is_dir():
        return out
    dirs = [p for p in FILL_PROFILES_ROOT.iterdir() if p.is_dir()]
    if not dirs:
        return out
    dirs.sort(key=lambda p: p.stat().st_mtime if p.is_dir() else 0, reverse=True)
    cutoff = time.time() - max_age_days * 86400
    for i, path in enumerate(dirs):
        if i < keep_newest:
            out["kept"] += 1
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            out["kept"] += 1
            continue
        try:
            shutil.rmtree(path)
            out["removed"].append(path.name)
        except OSError:
            pass
    return out


def _dashboard_ui_running() -> bool:
    """True when dedicated dashboard UI Chrome appears to be running."""
    try:
        import subprocess

        proc = subprocess.run(
            ["/bin/ps", "-ax", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    needle = str(UI_PROFILE)
    legacy = str(LEGACY_UI_PROFILE)
    for line in proc.stdout.splitlines():
        if "Google Chrome for Testing" not in line and "Chromium" not in line:
            continue
        if needle in line or legacy in line:
            if "--type=" not in line:
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trim-ui-cache", action="store_true")
    parser.add_argument("--prune-fill-profiles", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--min-hours", type=float, default=_DEFAULT_MIN_HOURS)
    args = parser.parse_args()
    if not args.trim_ui_cache and not args.prune_fill_profiles:
        parser.print_help()
        return 1
    if args.trim_ui_cache:
        if _dashboard_ui_running():
            print("skip: dashboard UI Chrome is running")
        else:
            for profile in (UI_PROFILE, LEGACY_UI_PROFILE):
                res = trim_chrome_profile_cache(
                    profile, min_hours=args.min_hours, force=args.force
                )
                if res.get("removed"):
                    mb = res.get("removed_bytes", 0) / (1024 * 1024)
                    print(f"trimmed {profile.name}: {mb:.1f} MiB ({len(res['removed'])} dirs)")
                elif res.get("skipped"):
                    print(f"skip {profile.name}: {res['skipped']}")
    if args.prune_fill_profiles:
        res = prune_stale_fill_profiles()
        if res.get("removed"):
            print(f"pruned fill profiles: {len(res['removed'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
