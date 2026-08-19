#!/usr/bin/env python3
"""Resolve Chrome for Testing / Chromium — never daily Google Chrome.app.

CHR2-001: OpenClaw auto-detects /Applications/Google Chrome.app for the
PartyRock CDP browser. Launching that binary with a custom --user-data-dir
registers as com.google.Chrome and hijacks Dock/Spotlight "Google Chrome".
Chrome for Testing (com.google.chrome.for.testing) keeps daily Chrome free.

Used by open_partyrock.sh and dashboard/server.py::_ensure_openclaw_managed_browser.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

OPENCLAW_USER_DATA = Path.home() / ".openclaw" / "browser" / "openclaw" / "user-data"
OPENCLAW_CDP_PORT = 18800
# Resolve openclaw: explicit env override → PATH → macOS Homebrew default.
OPENCLAW_BIN_DEFAULT = (
    (os.environ.get("JOBHUNTER_OPENCLAW_BIN") or "").strip()
    or shutil.which("openclaw")
    or "/opt/homebrew/bin/openclaw"
)

_DAILY_CHROME_MARKERS = (
    "/Applications/Google Chrome.app/",
    "/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def is_daily_google_chrome(path: str | Path | None) -> bool:
    if not path:
        return False
    text = str(path)
    return any(m in text for m in _DAILY_CHROME_MARKERS) and "Chrome for Testing" not in text


def resolve_chrome_for_testing() -> Path | None:
    """Newest Playwright CfT binary, or Chromium — never Google Chrome.app."""
    env = (os.environ.get("JOB_HUNTER_PARTYROCK_BROWSER") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file() and os.access(p, os.X_OK) and not is_daily_google_chrome(p):
            return p

    roots: list[Path] = []
    pw = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if pw:
        roots.append(Path(pw).expanduser())
    roots.extend(
        [
            Path.home() / "Library" / "Caches" / "ms-playwright",
            Path.home() / ".cache" / "ms-playwright",
        ]
    )
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for arch in ("arm64", "x64"):
            pattern = (
                f"chromium-*/chrome-mac-{arch}/"
                "Google Chrome for Testing.app/Contents/MacOS/"
                "Google Chrome for Testing"
            )
            found.extend(root.glob(pattern))
    found = [p for p in found if p.is_file() and os.access(p, os.X_OK)]
    if found:
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return found[0]

    for cand in (
        Path(
            "/Applications/Google Chrome for Testing.app/Contents/MacOS/"
            "Google Chrome for Testing"
        ),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand
    return None


def cdp_port_open(port: int = OPENCLAW_CDP_PORT, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def resolve_openclaw_bin() -> str | None:
    env = (os.environ.get("OPENCLAW_BIN") or "").strip()
    if env and Path(env).is_file() and os.access(env, os.X_OK):
        return env
    if Path(OPENCLAW_BIN_DEFAULT).is_file() and os.access(OPENCLAW_BIN_DEFAULT, os.X_OK):
        return OPENCLAW_BIN_DEFAULT
    which = subprocess.run(
        ["bash", "-lc", "command -v openclaw"],
        capture_output=True,
        text=True,
    )
    cand = (which.stdout or "").strip()
    if cand and Path(cand).is_file():
        return cand
    return None


def _openclaw_config_get(openclaw_bin: str, key: str) -> str | None:
    try:
        proc = subprocess.run(
            [openclaw_bin, "config", "get", key],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        return None
    # CLI may print warnings on stderr; stdout is the value (sometimes quoted).
    line = out.splitlines()[-1].strip().strip('"').strip("'")
    if line.lower() in ("null", "undefined", "(unset)", ""):
        return None
    if "config path not found" in out.lower():
        return None
    return line


def ensure_openclaw_executable_is_cft(
    *,
    openclaw_bin: str | None = None,
    cft: Path | None = None,
) -> Path | None:
    """Point OpenClaw ``browser.executablePath`` at CfT when unset or daily Chrome.

    Returns the CfT path on success, or None if CfT is unavailable.
    """
    cft = cft or resolve_chrome_for_testing()
    if cft is None:
        return None
    oc = openclaw_bin or resolve_openclaw_bin()
    if not oc:
        return cft
    current = _openclaw_config_get(oc, "browser.executablePath")
    if current and not is_daily_google_chrome(current) and Path(current).exists():
        # Already a non-daily binary (CfT / Chromium / Brave, etc.).
        if "Chrome for Testing" in current or "Chromium" in current:
            return Path(current) if Path(current).exists() else cft
        # Keep explicit non-Chrome overrides, but still prefer CfT for PartyRock
        # when the configured path is missing.
        if Path(current).is_file():
            return Path(current)
    try:
        subprocess.run(
            [oc, "config", "set", "browser.executablePath", str(cft)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"warn: could not set openclaw browser.executablePath: {e}", file=sys.stderr)
    return cft


def _clear_stale_singleton(profile: Path) -> None:
    if not profile.is_dir():
        return
    try:
        out = subprocess.check_output(
            ["/usr/bin/pgrep", "-f", f"--user-data-dir={profile}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        if out.strip():
            return
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            (profile / name).unlink(missing_ok=True)
        except OSError:
            pass


def launch_cft_with_openclaw_profile(
    cft: Path,
    *,
    user_data: Path = OPENCLAW_USER_DATA,
    cdp_port: int = OPENCLAW_CDP_PORT,
    url: str | None = None,
) -> None:
    """Start CfT against the OpenClaw user-data dir + CDP port (CHR2-001)."""
    user_data.mkdir(parents=True, exist_ok=True)
    _clear_stale_singleton(user_data)
    cmd = [
        str(cft),
        f"--user-data-dir={user_data}",
        f"--remote-debugging-port={cdp_port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
    ]
    if url:
        try:
            from window_geometry import chromium_window_args, work_window_plan

            outer = work_window_plan(role="fill")
            if outer is not None:
                cmd.extend(chromium_window_args(outer))
        except Exception:
            pass
        cmd.append(url)
    else:
        # Tailor/resume path: CDP up without raising CfT (PartyRock tabs via
        # Target.createTarget background=true in partyrock_tabs.py). Window
        # bounds are applied when the first tab is created/shown.
        cmd.append("--no-startup-window")
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def ensure_partyrock_browser_direct(
    *,
    url: str | None = None,
    wait_s: float = 25.0,
) -> dict:
    """OpenClaw-free PartyRock CDP: launch Chrome-for-Testing directly.

    Same mechanism OpenClaw's ``browser start`` only ever wrapped — the CfT
    binary on the persistent ``~/.openclaw/browser/openclaw/user-data`` dir at
    CDP :18800. Login/cookies persist in that dir across launches exactly as
    before (one-time human login preserved). Never touches the ``openclaw``
    binary, so it works with OpenClaw completely absent.
    """
    cft = resolve_chrome_for_testing()
    result: dict = {
        "ok": False,
        "cft": str(cft) if cft else None,
        "cdp_port": OPENCLAW_CDP_PORT,
        "started": False,
        "via": None,
        "error": None,
    }
    if cft is None:
        result["error"] = (
            "Chrome for Testing / Chromium not found. "
            "Install with: python3 -m playwright install chromium"
        )
        return result

    if cdp_port_open(OPENCLAW_CDP_PORT):
        result["via"] = "already_running"
        result["started"] = True
    else:
        launch_cft_with_openclaw_profile(cft, url=url)
        result["via"] = "cft_direct"
        deadline = time.monotonic() + wait_s
        started = False
        while time.monotonic() < deadline:
            if cdp_port_open(OPENCLAW_CDP_PORT):
                started = True
                break
            time.sleep(0.25)
        result["started"] = started

    if not cdp_port_open(OPENCLAW_CDP_PORT):
        result["error"] = f"CDP :{OPENCLAW_CDP_PORT} did not come up"
        return result

    if url and result["via"] != "cft_direct":
        # Only need to open the URL explicitly when we didn't just launch with
        # it. Use the CDP HTTP /json/new endpoint (no openclaw browser open).
        opened = False
        try:
            import urllib.parse
            import urllib.request

            quoted = urllib.parse.quote(url, safe="")
            for method in ("PUT", "GET"):
                try:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{OPENCLAW_CDP_PORT}/json/new?{quoted}",
                        method=method,
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        if 200 <= resp.status < 300:
                            opened = True
                            break
                except Exception as e:
                    result["open_error"] = str(e)[:200]
        except Exception as e:
            result["open_error"] = str(e)[:200]
        result["opened_url"] = opened

    result["ok"] = True
    return result


def ensure_openclaw_partyrock_browser(
    *,
    openclaw_bin: str | None = None,
    url: str | None = None,
    wait_s: float = 25.0,
) -> dict:
    """Ensure PartyRock CDP browser is up on CfT (not Google Chrome.app).

    1. Resolve CfT and pin ``browser.executablePath`` when needed.
    2. If CDP :18800 is already Google Chrome.app, stop it (Dock-hijack class).
    3. If CDP is down, prefer ``openclaw browser start`` (now CfT),
       else launch CfT directly with the OpenClaw profile.
    4. Optionally open *url* via ``openclaw browser open`` or CDP /json/new.
    """
    oc = openclaw_bin or resolve_openclaw_bin()
    cft = ensure_openclaw_executable_is_cft(openclaw_bin=oc)
    result: dict = {
        "ok": False,
        "cft": str(cft) if cft else None,
        "cdp_port": OPENCLAW_CDP_PORT,
        "started": False,
        "via": None,
        "error": None,
    }
    if cft is None:
        result["error"] = (
            "Chrome for Testing / Chromium not found. "
            "Install with: python3 -m playwright install chromium"
        )
        return result

    def _cdp_is_daily_chrome() -> str | None:
        try:
            out = subprocess.check_output(
                [
                    "/usr/bin/pgrep",
                    "-lf",
                    f"--remote-debugging-port={OPENCLAW_CDP_PORT}",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return None
        for line in out.splitlines():
            if is_daily_google_chrome(line) and "Chrome for Testing" not in line:
                return line[:300]
        return None

    # If a prior OpenClaw start left daily Chrome on :18800, tear it down and
    # relaunch on CfT (CHR2-001 regression of CHR-006).
    hijack = _cdp_is_daily_chrome()
    if hijack:
        result["replaced_daily_chrome"] = True
        if oc:
            try:
                subprocess.run(
                    [oc, "browser", "stop"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        # Kill any leftover mains still holding the OpenClaw profile / CDP port.
        for pat in (
            f"--user-data-dir={OPENCLAW_USER_DATA}",
            f"--remote-debugging-port={OPENCLAW_CDP_PORT}",
        ):
            try:
                out = subprocess.check_output(
                    ["/usr/bin/pgrep", "-f", pat],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
            except (subprocess.CalledProcessError, FileNotFoundError, OSError):
                continue
            for pid_s in out.split():
                try:
                    os.kill(int(pid_s), 15)
                except (OSError, ValueError):
                    pass
        time.sleep(0.8)

    if not cdp_port_open(OPENCLAW_CDP_PORT):
        started = False
        if oc:
            try:
                proc = subprocess.run(
                    [oc, "browser", "start"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                # Give CDP a moment even on non-zero (CLI may warn but start).
                deadline = time.monotonic() + min(wait_s, 15.0)
                while time.monotonic() < deadline:
                    if cdp_port_open(OPENCLAW_CDP_PORT):
                        started = True
                        result["via"] = "openclaw"
                        break
                    time.sleep(0.25)
                if not started and proc.returncode != 0:
                    result["openclaw_start_rc"] = proc.returncode
            except (OSError, subprocess.TimeoutExpired) as e:
                result["openclaw_start_error"] = str(e)[:200]

        # If openclaw still brought up daily Chrome, or CDP still down → CfT direct.
        still_hijack = _cdp_is_daily_chrome()
        if still_hijack or not cdp_port_open(OPENCLAW_CDP_PORT):
            if still_hijack:
                for pat in (
                    f"--user-data-dir={OPENCLAW_USER_DATA}",
                    f"--remote-debugging-port={OPENCLAW_CDP_PORT}",
                ):
                    try:
                        out = subprocess.check_output(
                            ["/usr/bin/pgrep", "-f", pat],
                            text=True,
                            stderr=subprocess.DEVNULL,
                        )
                    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
                        continue
                    for pid_s in out.split():
                        try:
                            os.kill(int(pid_s), 15)
                        except (OSError, ValueError):
                            pass
                time.sleep(0.6)
            launch_cft_with_openclaw_profile(cft, url=url)
            result["via"] = "cft_direct"
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline:
                if cdp_port_open(OPENCLAW_CDP_PORT) and not _cdp_is_daily_chrome():
                    started = True
                    break
                time.sleep(0.25)
        result["started"] = started
    else:
        result["via"] = "already_running"
        result["started"] = True

    if not cdp_port_open(OPENCLAW_CDP_PORT):
        result["error"] = f"CDP :{OPENCLAW_CDP_PORT} did not come up"
        return result

    hijack = _cdp_is_daily_chrome()
    if hijack:
        result["error"] = (
            "PartyRock CDP is still Google Chrome.app — refusing Dock hijack. "
            "Stop it and re-run so Chrome for Testing can take over."
        )
        result["hijack_line"] = hijack
        return result

    if url:
        opened = False
        if oc:
            try:
                proc = subprocess.run(
                    [oc, "browser", "open", url],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                opened = proc.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                opened = False
        if not opened:
            # CDP HTTP fallback (same as partyrock_tabs.create_tab) — PR2-004:
            # try PUT then GET for /json/new Chromium variance.
            try:
                import urllib.parse
                import urllib.request

                quoted = urllib.parse.quote(url, safe="")
                for method in ("PUT", "GET"):
                    try:
                        req = urllib.request.Request(
                            f"http://127.0.0.1:{OPENCLAW_CDP_PORT}/json/new?{quoted}",
                            method=method,
                        )
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            if 200 <= resp.status < 300:
                                opened = True
                                break
                    except Exception as e:
                        result["open_error"] = str(e)[:200]
            except Exception as e:
                result["open_error"] = str(e)[:200]
        result["opened_url"] = opened

    result["ok"] = True
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chrome for Testing / OpenClaw PartyRock helpers")
    parser.add_argument("--resolve", action="store_true", help="Print CfT binary path")
    parser.add_argument(
        "--ensure-openclaw",
        action="store_true",
        help="Pin openclaw browser.executablePath to CfT and print path",
    )
    parser.add_argument(
        "--ensure-partyrock",
        action="store_true",
        help="Ensure PartyRock CDP is up on CfT (CHR2-001)",
    )
    parser.add_argument("--open-url", default=None, help="URL to open after ensure-partyrock")
    parser.add_argument("--json", action="store_true", help="JSON result for --ensure-partyrock")
    args = parser.parse_args(argv)

    if args.resolve:
        cft = resolve_chrome_for_testing()
        if not cft:
            print("error: Chrome for Testing not found", file=sys.stderr)
            return 1
        print(cft)
        return 0

    if args.ensure_openclaw:
        cft = ensure_openclaw_executable_is_cft()
        if not cft:
            print("error: Chrome for Testing not found", file=sys.stderr)
            return 1
        print(cft)
        return 0

    if args.ensure_partyrock:
        result = ensure_openclaw_partyrock_browser(url=args.open_url)
        if args.json:
            import json

            print(json.dumps(result))
        else:
            if result.get("ok"):
                print(
                    f"PartyRock CDP ok via={result.get('via')} "
                    f"cft={result.get('cft')}"
                )
            else:
                print(f"error: {result.get('error')}", file=sys.stderr)
        return 0 if result.get("ok") else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
