"""Headed fill browser: regular Google Chrome + dedicated job-hunter profiles.

Uses Playwright ``channel=\"chrome\"`` (``/Applications/Google Chrome.app``) with
``job_hunter_fill_profiles/<job>_<run>`` — never the user's daily Chrome profile,
``dashboard_ui_profile``, or OpenClaw PartyRock CDP profile.

Headless fills may still use bundled Chromium when ``FASTFILL_HEADLESS_CHANNEL=0``.
"""

from __future__ import annotations

import os
import re
import shutil
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
        f"--user-data-dir={ROOT / 'linkedin_resolve_profile'}",
        "linkedin_resolve_profile",
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


def find_fill_chrome_pid_for_profile(profile_dir: Path | str | None) -> int | None:
    """Best-effort PID of the headed fill Chrome for a profile directory."""
    if not profile_dir:
        return None
    marker = f"--user-data-dir={Path(profile_dir).resolve()}"
    try:
        out = subprocess.check_output(
            ["pgrep", "-lf", "Google Chrome"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    for line in out.splitlines():
        if marker not in line:
            continue
        if not _is_fill_chrome_main_line(line, headed_only=True):
            continue
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        try:
            return int(parts[0])
        except ValueError:
            continue
    return None


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


def _chrome_binary_for_version() -> Path | None:
    if DEFAULT_CHROME_APP.is_file():
        return DEFAULT_CHROME_APP
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        try:
            out = subprocess.check_output(["which", name], text=True, stderr=subprocess.DEVNULL)
            p = Path(out.strip())
            if p.is_file():
                return p
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            continue
    return None


def detect_chrome_version() -> str:
    """Best-effort installed Chrome/Chromium version (major.minor.build.patch)."""
    binary = _chrome_binary_for_version()
    if binary is None:
        return "131.0.0.0"
    try:
        out = subprocess.check_output(
            [str(binary), "--version"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=8,
        )
        m = re.search(r"([\d]+\.[\d]+\.[\d]+\.[\d]+)", out)
        if m:
            return m.group(1)
        m = re.search(r"([\d]+\.[\d]+)", out)
        if m:
            return f"{m.group(1)}.0.0"
    except (subprocess.CalledProcessError, FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return "131.0.0.0"


def build_chrome_user_agent(version: str | None = None) -> str:
    """User-Agent string matching installed Chrome on this host."""
    ver = (version or detect_chrome_version()).strip() or "131.0.0.0"
    if sys.platform == "darwin":
        platform_token = "Macintosh; Intel Mac OS X 10_15_7"
    elif sys.platform.startswith("linux"):
        platform_token = "X11; Linux x86_64"
    else:
        platform_token = "Windows NT 10.0; Win64; x64"
    return (
        f"Mozilla/5.0 ({platform_token}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{ver} Safari/537.36"
    )


def system_timezone_id() -> str:
    """IANA timezone for Playwright ``timezone_id`` (minimal deps)."""
    if sys.platform == "darwin":
        try:
            link = os.readlink("/etc/localtime")
            if "zoneinfo/" in link:
                return link.split("zoneinfo/", 1)[1]
            if link.startswith("/var/db/timezone/zoneinfo/"):
                return link.split("/var/db/timezone/zoneinfo/", 1)[1]
        except OSError:
            pass
        try:
            out = subprocess.check_output(
                ["systemsetup", "-gettimezone"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            m = re.search(r":\s*([A-Za-z0-9_+/\-]+)\s*$", out.strip())
            if m:
                return m.group(1)
        except (subprocess.CalledProcessError, FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass
    else:
        tz_file = Path("/etc/timezone")
        if tz_file.is_file():
            try:
                tz = tz_file.read_text(encoding="utf-8").strip()
                if tz:
                    return tz
            except OSError:
                pass
        try:
            link = os.readlink("/etc/localtime")
            if "zoneinfo/" in link:
                return link.split("zoneinfo/", 1)[1]
        except OSError:
            pass
    try:
        from datetime import datetime

        key = getattr(datetime.now().astimezone().tzinfo, "key", None)
        if key:
            return str(key)
    except Exception:
        pass
    return "America/New_York"


def resolve_viewport() -> dict[str, int]:
    """Common headed viewport — override via FASTFILL_VIEWPORT=WxH."""
    raw = (os.environ.get("FASTFILL_VIEWPORT") or "1440x900").strip().lower()
    m = re.match(r"^(\d{3,5})x(\d{3,5})$", raw)
    if m:
        return {"width": int(m.group(1)), "height": int(m.group(2))}
    return {"width": 1440, "height": 900}


def resolve_wipe_profile_on_teardown() -> bool:
    raw = (os.environ.get("FASTFILL_WIPE_PROFILE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def wipe_fill_profile_dir(profile_dir: Path | str | None) -> dict[str, Any]:
    """Delete a per-run fill profile directory (best-effort)."""
    out: dict[str, Any] = {"path": str(profile_dir or ""), "wiped": False}
    if not profile_dir:
        return out
    path = Path(profile_dir)
    root = FILL_PROFILES_ROOT.resolve()
    try:
        resolved = path.resolve()
    except OSError:
        return out
    if not str(resolved).startswith(str(root)):
        out["reason"] = "outside_fill_profiles_root"
        return out
    if not resolved.exists():
        out["reason"] = "missing"
        return out
    try:
        shutil.rmtree(resolved)
        out["wiped"] = True
    except OSError as e:
        out["reason"] = str(e)[:120]
    return out


def wipe_fill_profiles_for_job(job_id: str | None) -> dict[str, Any]:
    """Remove all fill profiles for a job id (dashboard cancel / mark-applied)."""
    out: dict[str, Any] = {"job_id": job_id or "", "removed": []}
    if not job_id:
        return out
    prefix = f"{_sanitize_profile_token(job_id)}_"
    if not FILL_PROFILES_ROOT.is_dir():
        return out
    for child in FILL_PROFILES_ROOT.iterdir():
        if child.is_dir() and child.name.startswith(prefix):
            res = wipe_fill_profile_dir(child)
            if res.get("wiped"):
                out["removed"].append(child.name)
    return out


def resolve_browser_user_agent(
    *,
    channel: str | None,
    executable_path: str | None = None,
) -> str | None:
    """User-Agent matching the browser Playwright will launch.

    System Chrome (``channel=\"chrome\"`` or CfT executable) → detected Chrome UA.
    Bundled Playwright Chromium → ``None`` (let Playwright default match binary).
    """
    if channel == "chrome" or executable_path:
        return build_chrome_user_agent(detect_chrome_version())
    return None


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_scripts_path() -> None:
    scripts = str(_scripts_dir())
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def headed_fill_window_outer(screen_metrics: Any = None) -> Any:
    """Outer right-two-thirds rect, or None when screen metrics are unavailable."""
    _ensure_scripts_path()
    from window_geometry import work_window_plan

    return work_window_plan(role="fill", metrics=screen_metrics)


def build_persistent_context_kwargs(
    *,
    profile_dir: Path,
    headless: bool,
    screen_metrics: Any = None,
) -> dict[str, Any]:
    """Kwargs for ``chromium.launch_persistent_context``."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    chrome_ver = detect_chrome_version()
    channel = resolve_fill_browser_channel(headless=headless)
    executable_path: str | None = None
    kwargs: dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "slow_mo": 200 if not headless else 0,
        "locale": "en-US",
        "viewport": resolve_viewport(),
        "timezone_id": system_timezone_id(),
        "chrome_version_detected": chrome_ver,
    }
    ua = resolve_browser_user_agent(channel=channel, executable_path=None)
    if ua:
        kwargs["user_agent"] = ua
    kwargs.update(chromium_launch_hygiene_kwargs())
    if channel:
        kwargs["channel"] = channel
    else:
        exe = resolve_playwright_chromium_executable()
        if exe:
            kwargs["executable_path"] = exe
            ua_exe = resolve_browser_user_agent(channel=None, executable_path=exe)
            if ua_exe:
                kwargs["user_agent"] = ua_exe
    if not headless:
        try:
            outer = headed_fill_window_outer(screen_metrics)
        except Exception:
            outer = None
        if outer is not None:
            _ensure_scripts_path()
            from window_geometry import chromium_window_args, playwright_viewport

            args = list(kwargs.get("args") or [])
            args.extend(chromium_window_args(outer))
            kwargs["args"] = args
            if not (os.environ.get("FASTFILL_VIEWPORT") or "").strip():
                kwargs["viewport"] = playwright_viewport(outer)
            kwargs["_jh_window_outer"] = outer
    return kwargs


async def place_headed_fill_window(page: Any, *, outer: Any = None) -> dict[str, Any] | None:
    """CDP-place the headed fill window on the right two-thirds (best-effort)."""
    if page is None:
        return None
    _ensure_scripts_path()
    from window_geometry import Rect, place_playwright_window

    plan = outer
    if isinstance(plan, dict):
        plan = Rect(
            x=int(plan["x"]),
            y=int(plan["y"]),
            width=int(plan["width"]),
            height=int(plan["height"]),
        )
    try:
        return await place_playwright_window(page, outer=plan)
    except Exception:
        return None


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
