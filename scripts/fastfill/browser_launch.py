"""Headed fill browser: regular Google Chrome + dedicated job-hunter profiles.

Uses Playwright ``channel=\"chrome\"`` (``/Applications/Google Chrome.app``) with
``job_hunter_fill_profiles/<job>_<run>`` — never the user's daily Chrome profile,
``dashboard_ui_profile``, or OpenClaw PartyRock CDP profile.

Headless fills may still use bundled Chromium when ``FASTFILL_HEADLESS_CHANNEL=0``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
FILL_PROFILES_ROOT = ROOT / "job_hunter_fill_profiles"
DEFAULT_CHROME_APP = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def _sanitize_profile_token(raw: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", (raw or "").strip())[:64]
    return s or "adhoc"


def resolve_fill_profile_dir(
    *,
    job_id: str | None = None,
    run_token: str | None = None,
) -> Path:
    """One profile dir per fill run (parallel-safe)."""
    token = _sanitize_profile_token(run_token or uuid.uuid4().hex[:12])
    job_bit = _sanitize_profile_token(job_id) if job_id else "adhoc"
    return FILL_PROFILES_ROOT / f"{job_bit}_{token}"


def resolve_fill_browser_channel(*, headless: bool) -> str | None:
    """Return Playwright channel name or None for bundled Chromium."""
    if not headless:
        force = (os.environ.get("FASTFILL_FILL_CHANNEL") or "").strip().lower()
        if force in ("0", "bundled", "chromium", "cft", "testing"):
            return None
        if sys.platform == "darwin":
            return "chrome"
        # Linux headed: prefer system chrome when installed
        if Path("/usr/bin/google-chrome").exists() or Path("/usr/bin/google-chrome-stable").exists():
            return "chrome"
        return None
    # Headless batch: bundled unless explicitly forced to chrome
    if (os.environ.get("FASTFILL_HEADLESS_CHANNEL") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "chrome",
    ):
        return "chrome"
    return None


def resolve_playwright_chromium_executable() -> str | None:
    """Legacy CfT path — only when ``FASTFILL_USE_CFT=1``."""
    if (os.environ.get("FASTFILL_USE_CFT") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return None
    import platform as _platform

    env = (os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or "").strip()
    if env and Path(env).exists():
        return env
    machine = _platform.machine().lower()
    prefer = ["arm64", "x64"] if machine in ("arm64", "aarch64") else ["x64", "arm64"]
    search_roots: list[Path] = []
    env_browsers = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if env_browsers:
        search_roots.append(Path(env_browsers).expanduser())
    default_browsers = Path.home() / "Library/Caches/ms-playwright"
    if default_browsers not in search_roots:
        search_roots.append(default_browsers)
    linux_default = Path.home() / ".cache/ms-playwright"
    if linux_default not in search_roots:
        search_roots.append(linux_default)
    candidates: list[tuple[str, Path]] = []
    for browsers in search_roots:
        if not browsers.is_dir():
            continue
        roots = sorted(browsers.glob("chromium-*"), reverse=True)
        for arch in prefer:
            for root in roots:
                cand = (
                    root
                    / f"chrome-mac-{arch}"
                    / "Google Chrome for Testing.app"
                    / "Contents"
                    / "MacOS"
                    / "Google Chrome for Testing"
                )
                if cand.is_file():
                    candidates.append((arch, cand))
    for arch in prefer:
        for a, cand in candidates:
            if a == arch:
                return str(cand)
    return None


def fill_chrome_exclude_markers() -> tuple[str, ...]:
    """Argv markers for Chrome processes that are NOT a headed form-fill."""
    return (
        f"--user-data-dir={ROOT / 'dashboard_ui_profile'}",
        f"--user-data-dir={ROOT / 'dashboard_chrome_profile'}",
        "--app=http://127.0.0.1:8787",
        f"--user-data-dir={Path.home() / '.openclaw' / 'browser' / 'openclaw' / 'user-data'}",
        "--remote-debugging-port=18800",
        "openclaw/user-data",
        f"--user-data-dir={ROOT / 'partyrock_chrome_profile'}",
    )


def fill_profile_marker() -> str:
    return f"--user-data-dir={FILL_PROFILES_ROOT}"


def _is_fill_chrome_main_line(line: str, *, headed_only: bool = False) -> bool:
    if "Helper" in line or "crashpad" in line:
        return False
    if headed_only and ("--headless" in line or "headless=new" in line):
        return False
    # Google Chrome (stable) or legacy CfT fill mains
    is_chrome = (
        "MacOS/Google Chrome" in line
        or "/Google Chrome.app/Contents/MacOS/Google Chrome" in line
        or "Google Chrome for Testing" in line
        or re.search(r"/chrome(?:\s|$)", line)
    )
    if not is_chrome:
        return False
    if any(m in line for m in fill_chrome_exclude_markers()):
        return False
    # Must be a job-hunter fill profile OR Playwright pipe (legacy CfT headed)
    if fill_profile_marker() in line or "job_hunter_fill_profile" in line:
        return True
    if "--remote-debugging-pipe" in line and "Google Chrome for Testing" in line:
        return True
    return False


def count_fill_chrome_mains(*, headed_only: bool = False) -> list[int]:
    """PIDs of fill Chrome mains (regular Chrome + job_hunter_fill_profiles)."""
    patterns = ("Google Chrome", "Google Chrome for Testing")
    pids: list[int] = []
    seen: set[int] = set()
    for pat in patterns:
        try:
            out = subprocess.check_output(
                ["pgrep", "-lf", pat],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            continue
        for line in out.splitlines():
            if not _is_fill_chrome_main_line(line, headed_only=headed_only):
                continue
            parts = line.strip().split(None, 1)
            if not parts:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if pid not in seen:
                seen.add(pid)
                pids.append(pid)
    return pids


def chromium_launch_hygiene_kwargs() -> dict[str, Any]:
    """Extra Playwright launch kwargs to reduce automation fingerprint."""
    return {
        "args": [
            "--disable-blink-features=AutomationControlled",
        ],
        "ignore_default_args": ["--enable-automation"],
    }


def build_persistent_context_kwargs(
    *,
    profile_dir: Path,
    headless: bool,
) -> dict[str, Any]:
    """Kwargs for ``chromium.launch_persistent_context``."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "slow_mo": 200 if not headless else 0,
        "locale": "en-US",
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    kwargs.update(chromium_launch_hygiene_kwargs())
    channel = resolve_fill_browser_channel(headless=headless)
    if channel:
        kwargs["channel"] = channel
    else:
        exe = resolve_playwright_chromium_executable()
        if exe:
            kwargs["executable_path"] = exe
    return kwargs


def bring_fill_chrome_to_front(*, loud: bool = False) -> bool:
    """Raise the headed fill Chrome window by PID (macOS System Events)."""
    if sys.platform != "darwin":
        return False
    if (os.environ.get("FASTFILL_CAPTCHA_NO_FOCUS") or "").strip() in (
        "1",
        "true",
        "yes",
    ):
        return False
    preferred: list[int] = []
    other: list[int] = []
    try:
        out = subprocess.check_output(
            ["pgrep", "-lf", "Google Chrome"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False
    marker = str(FILL_PROFILES_ROOT)
    for line in out.splitlines():
        if not _is_fill_chrome_main_line(line):
            continue
        if marker not in line and "--remote-debugging-pipe" not in line:
            continue
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if "--remote-debugging-pipe" in line or marker in line:
            preferred.append(pid)
        else:
            other.append(pid)
    for pid in preferred + other:
        try:
            r = subprocess.run(
                [
                    "osascript",
                    "-e",
                    "tell application \"System Events\" to set frontmost of "
                    f"first process whose unix id is {pid} to true",
                ],
                check=False,
                timeout=3,
                capture_output=True,
                text=True,
            )
            if r.returncode == 0:
                if loud:
                    print(f"[browser] focused fill Chrome pid={pid}", flush=True)
                return True
        except Exception:
            continue
    return False


def google_chrome_app_installed() -> bool:
    return DEFAULT_CHROME_APP.is_file()
