#!/usr/bin/env python3
"""Dedicated Chrome-for-Testing + CDP browser for LinkedIn apply-URL resolve.

Same *mechanism* as PartyRock (``./open_partyrock.sh`` / CfT + long-lived
user-data-dir + CDP attach) — **separate** profile so LinkedIn cookies never
mix with PartyRock/OpenClaw.

Profile (gitignored): ``<job-hunter>/linkedin_resolve_profile``
CDP: ``http://127.0.0.1:18801`` (override ``JOB_HUNTER_LINKEDIN_RESOLVE_CDP_PORT``)
Override profile: ``JOB_HUNTER_LINKEDIN_RESOLVE_PROFILE=/abs/path``

Never: daily Google Chrome.app, dashboard_ui_profile, PartyRock/OpenClaw
profile (``~/.openclaw/browser/openclaw/user-data`` / ``:18800``), wipeable
fill profiles, CAPTCHA solve, Easy Apply submit, or applicant PII.

Usage:
  python3 scripts/linkedin_resolve_profile.py --path
  python3 scripts/linkedin_resolve_profile.py --status
  python3 scripts/linkedin_resolve_profile.py --profile-ok
  python3 scripts/linkedin_resolve_profile.py --login
  ./open_linkedin_resolve.sh
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = ROOT / "linkedin_resolve_profile"
LOGIN_URL = "https://www.linkedin.com/login"
DEFAULT_CDP_PORT = 18801
# Brief pause after clean quit so Chromium can flush Cookies to disk (legacy).
COOKIE_FLUSH_WAIT_S = 3.0

sys.path.insert(0, str(Path(__file__).resolve().parent))


def linkedin_resolve_profile_dir() -> Path:
    env = (os.environ.get("JOB_HUNTER_LINKEDIN_RESOLVE_PROFILE") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_PROFILE.resolve()


def linkedin_resolve_cdp_port() -> int:
    raw = (os.environ.get("JOB_HUNTER_LINKEDIN_RESOLVE_CDP_PORT") or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return DEFAULT_CDP_PORT


def linkedin_resolve_cdp_http() -> str:
    return f"http://127.0.0.1:{linkedin_resolve_cdp_port()}"


def linkedin_resolve_cdp_ws_endpoint() -> str:
    """Playwright ``connect_over_cdp`` accepts HTTP CDP base URL."""
    return linkedin_resolve_cdp_http()


def login_required_message(profile: Path | None = None) -> str:
    path = profile or linkedin_resolve_profile_dir()
    return (
        "Open LinkedIn resolve browser first (PartyRock-style CDP): "
        f"./open_linkedin_resolve.sh  or  "
        f"python3 scripts/linkedin_resolve_profile.py --login "
        f"(profile: {path}, CDP :{linkedin_resolve_cdp_port()})"
    )


def profile_in_use_message(profile: Path | None = None) -> str:
    """Legacy message — used when a non-CDP Chromium still holds the profile."""
    path = profile or linkedin_resolve_profile_dir()
    return (
        "A LinkedIn Chromium is using this profile without CDP. "
        f"Quit that window (red X / Cmd+Q), then re-run ./open_linkedin_resolve.sh "
        f"(profile: {path}, CDP :{linkedin_resolve_cdp_port()})."
    )


def profile_looks_initialized(profile: Path | None = None) -> bool:
    """True when Chromium has written a Default profile (cookies may exist)."""
    root = Path(profile) if profile is not None else linkedin_resolve_profile_dir()
    prefs = root / "Default" / "Preferences"
    cookies = root / "Default" / "Cookies"
    network_cookies = root / "Default" / "Network" / "Cookies"
    return prefs.is_file() or cookies.is_file() or network_cookies.is_file()


def _pgrep_user_data_dir(profile: Path) -> list[int]:
    """PIDs whose argv includes this ``--user-data-dir``.

    macOS ``pgrep -f --user-data-dir=...`` treats the pattern as a *flag*
    (illegal option ``--``). Always pass ``pgrep -f -- <pattern>``.
    """
    root = Path(profile).resolve()
    pattern = f"--user-data-dir={root}"
    try:
        out = subprocess.check_output(
            ["/usr/bin/pgrep", "-f", "--", pattern],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line.split(None, 1)[0]))
        except ValueError:
            continue
    return pids


def profile_has_live_browser(profile: Path | None = None) -> bool:
    """True when a Chromium/CfT process holds this user-data-dir."""
    root = Path(profile) if profile is not None else linkedin_resolve_profile_dir()
    return bool(_pgrep_user_data_dir(root))


def cdp_port_open(port: int | None = None, host: str = "127.0.0.1") -> bool:
    import socket

    p = linkedin_resolve_cdp_port() if port is None else int(port)
    try:
        with socket.create_connection((host, p), timeout=0.4):
            return True
    except OSError:
        return False


def wait_for_profile_unlock(
    profile: Path | None = None,
    *,
    timeout_s: float = 8.0,
    poll_s: float = 0.5,
) -> bool:
    """Wait until no live browser holds the profile. Never kills that window.

    Returns True if unlocked (or never locked); False if still in use after timeout.
    Kept for legacy callers; PartyRock-style resolve prefers CDP attach instead.
    """
    root = Path(profile) if profile is not None else linkedin_resolve_profile_dir()
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        if not profile_has_live_browser(root):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.05, float(poll_s)))


def _cookie_db_paths(profile: Path) -> tuple[Path, ...]:
    root = Path(profile)
    return (
        root / "Default" / "Network" / "Cookies",
        root / "Default" / "Cookies",
    )


def profile_has_li_at(profile: Path | None = None) -> bool:
    """True if LinkedIn ``li_at`` session cookie exists (never returns the value)."""
    import sqlite3

    root = Path(profile) if profile is not None else linkedin_resolve_profile_dir()
    for db in _cookie_db_paths(root):
        if not db.is_file():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "SELECT 1 FROM cookies WHERE name = ? LIMIT 1",
                    ("li_at",),
                ).fetchone()
            finally:
                con.close()
            if row:
                return True
        except (sqlite3.Error, OSError):
            continue
    return False


def profile_is_logged_in(profile: Path | None = None) -> bool:
    """Boolean login check for resolve readiness (never prints cookie values)."""
    return profile_has_li_at(profile)


def _chromium_safe_storage_password() -> bytes | None:
    """Keychain password for Chromium/CfT cookie decryption (never logged)."""
    for service in (
        "Chrome for Testing Safe Storage",
        "Chromium Safe Storage",
        "Chrome Safe Storage",
    ):
        try:
            out = subprocess.check_output(
                ["/usr/bin/security", "find-generic-password", "-w", "-s", service],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            continue
        pw = (out or "").strip()
        if pw:
            return pw.encode("utf-8")
    return None


def _decrypt_chromium_cookie_value(encrypted_value: bytes, *, key: bytes) -> str | None:
    """Decrypt Chromium v10 cookie blob → plaintext (never logs the value)."""
    import binascii
    import hashlib
    import tempfile

    if not encrypted_value or len(encrypted_value) < 4:
        return None
    if encrypted_value[:3] != b"v10":
        return None
    aes_key = hashlib.pbkdf2_hmac("sha1", key, b"saltysalt", 1003, dklen=16)
    key_hex = binascii.hexlify(aes_key).decode("ascii")
    iv_hex = binascii.hexlify(b" " * 16).decode("ascii")
    payload = encrypted_value[3:]
    path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(payload)
            path = fh.name
        raw = subprocess.check_output(
            [
                "/usr/bin/openssl",
                "enc",
                "-aes-128-cbc",
                "-d",
                "-nopad",
                "-K",
                key_hex,
                "-iv",
                iv_hex,
                "-in",
                path,
            ],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
    if not raw:
        return None
    pad = raw[-1]
    if 1 <= pad <= 16 and raw.endswith(bytes([pad]) * pad):
        raw = raw[:-pad]
    # Chromium domain-bound cookies: 32-byte hash prefix then value.
    if len(raw) > 32:
        try:
            tail = raw[32:].decode("utf-8")
            if tail and all(32 <= ord(c) < 127 for c in tail):
                return tail
        except UnicodeDecodeError:
            pass
    try:
        plain = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if plain and all(32 <= ord(c) < 127 for c in plain):
        return plain
    return None


def load_linkedin_cookies(profile: Path | None = None) -> dict[str, str]:
    """Load LinkedIn cookies from the Chromium profile DB for HTTP resolve.

    Returns ``{name: value}`` for ``*.linkedin.com`` hosts. Never prints or
    logs cookie values. Empty dict when the DB is missing / undecryptable.
    """
    import sqlite3

    root = Path(profile) if profile is not None else linkedin_resolve_profile_dir()
    key = _chromium_safe_storage_password()
    if not key:
        return {}
    out: dict[str, str] = {}
    for db in _cookie_db_paths(root):
        if not db.is_file():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            rows = con.execute(
                "SELECT name, value, encrypted_value FROM cookies "
                "WHERE host_key LIKE '%linkedin%'"
            ).fetchall()
        except sqlite3.Error:
            con.close()
            continue
        try:
            for name, value, encrypted in rows:
                n = str(name or "").strip()
                if not n:
                    continue
                plain = str(value or "").strip()
                if not plain and encrypted:
                    plain = (_decrypt_chromium_cookie_value(encrypted, key=key) or "").strip()
                if plain:
                    out[n] = plain
        finally:
            con.close()
        if out.get("li_at"):
            break
    return out


def _clear_stale_singleton(profile: Path) -> None:
    """Remove leftover Singleton* only when no live process holds the profile.

    Never clears while a login/resolve window is open (avoids mid-cookie-flush wipe).
    """
    if not profile.is_dir():
        return
    if profile_has_live_browser(profile):
        return
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            (profile / name).unlink(missing_ok=True)
        except OSError:
            pass


def login_browser_cmd(
    cft: Path,
    profile: Path,
    *,
    url: str | None = LOGIN_URL,
    cdp_port: int | None = None,
) -> list[str]:
    """CfT argv for LinkedIn — headed, persistent profile, CDP (PartyRock-style)."""
    port = linkedin_resolve_cdp_port() if cdp_port is None else int(cdp_port)
    cmd = [
        str(cft),
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-infobars",
    ]
    if url:
        cmd.append(url)
    else:
        cmd.append("--no-startup-window")
    return cmd


def open_url_via_cdp(
    url: str,
    *,
    cdp_http: str | None = None,
    background: bool = True,
) -> bool:
    """Open *url* in a new tab via CDP.

    Default ``background=True`` uses ``Target.createTarget`` so resolve/ensure
    does not raise Chrome for Testing over the dashboard (PartyRock pattern).
    Interactive ``--login`` passes ``background=False`` (may focus once).
    Never uses osascript / Activate.
    """
    base = (cdp_http or linkedin_resolve_cdp_http()).rstrip("/")
    if background:
        try:
            # Reuse PartyRock CDP helper (no window placement).
            from partyrock_tabs import create_tab

            info = create_tab(url, cdp_http=base, background=True, place=False)
            if info and info.get("id"):
                return True
        except Exception:
            pass
    quoted = urllib.parse.quote(url, safe="")
    for method in ("PUT", "GET"):
        try:
            req = urllib.request.Request(
                f"{base}/json/new?{quoted}",
                method=method,
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
    return False


def launch_cft_linkedin_resolve(
    cft: Path,
    *,
    profile: Path | None = None,
    cdp_port: int | None = None,
    url: str | None = None,
) -> None:
    """Start CfT against the LinkedIn resolve profile + CDP (leave running).

    Resolve/ensure with ``url=None`` uses ``--no-startup-window`` (no focus steal).
    Interactive login may pass a URL (Chromium may raise once — intentional).
    Never osascript-activates the CfT app.
    """
    root = Path(profile) if profile is not None else linkedin_resolve_profile_dir()
    port = linkedin_resolve_cdp_port() if cdp_port is None else int(cdp_port)
    root.mkdir(parents=True, exist_ok=True)
    _clear_stale_singleton(root)
    cmd = login_browser_cmd(cft, root, url=url, cdp_port=port)
    assert "--headless" not in cmd and "--incognito" not in cmd
    assert any(a.startswith("--user-data-dir=") for a in cmd)
    assert any(a.startswith("--remote-debugging-port=") for a in cmd)
    # No `open -a` / AppleScript activate — Popen only.
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def ensure_linkedin_resolve_browser(
    *,
    url: str | None = None,
    wait_s: float = 25.0,
    profile: Path | None = None,
    steal_focus: bool = False,
) -> dict:
    """Ensure LinkedIn resolve CfT+CDP is up (mirror PartyRock ``ensure_*_direct``).

    Leaves the browser running. Reuses an existing CDP instance when possible.
    Never touches PartyRock's ``:18800`` / OpenClaw user-data.
    Never osascript-activates CfT. Opening an extra URL uses a background CDP
    tab unless ``steal_focus=True`` (interactive ``--login`` only).
    """
    from chrome_for_testing import is_daily_google_chrome, resolve_chrome_for_testing

    root = Path(profile) if profile is not None else linkedin_resolve_profile_dir()
    port = linkedin_resolve_cdp_port()
    cft = resolve_chrome_for_testing()
    result: dict = {
        "ok": False,
        "profile": str(root),
        "cft": str(cft) if cft else None,
        "cdp_port": port,
        "cdp_http": f"http://127.0.0.1:{port}",
        "started": False,
        "via": None,
        "error": None,
        "li_at": False,
        "steal_focus": bool(steal_focus),
    }
    if cft is None:
        result["error"] = (
            "Chrome for Testing / Chromium not found. "
            "Install with: python3 -m playwright install chromium"
        )
        return result
    if is_daily_google_chrome(cft):
        result["error"] = "Refusing daily Google Chrome.app — use Chrome for Testing"
        return result

    # Non-CDP Chromium holding the profile blocks attach (old --login without CDP).
    if profile_has_live_browser(root) and not cdp_port_open(port):
        result["error"] = profile_in_use_message(root)
        result["already_open_no_cdp"] = True
        return result

    if cdp_port_open(port):
        result["via"] = "already_running"
        result["started"] = True
    else:
        # Resolve path: url=None → --no-startup-window (no raise). Login may pass url.
        launch_cft_linkedin_resolve(cft, profile=root, cdp_port=port, url=url)
        result["via"] = "cft_direct"
        deadline = time.monotonic() + wait_s
        started = False
        while time.monotonic() < deadline:
            if cdp_port_open(port):
                started = True
                break
            time.sleep(0.25)
        result["started"] = started

    if not cdp_port_open(port):
        result["error"] = f"LinkedIn resolve CDP :{port} did not come up"
        return result

    if url and result["via"] == "already_running":
        result["opened_url"] = open_url_via_cdp(
            url,
            cdp_http=f"http://127.0.0.1:{port}",
            background=not steal_focus,
        )

    result["ok"] = True
    result["li_at"] = profile_has_li_at(root)
    return result


def launch_login_browser(
    *,
    url: str = LOGIN_URL,
    wait: bool = False,
    flush_wait_s: float = COOKIE_FLUSH_WAIT_S,
) -> dict:
    """Open LinkedIn login in the long-lived CfT+CDP browser (PartyRock-style).

    Default leaves the browser running (``wait=False``). ``wait=True`` is a
    legacy path that blocks until the process exits (cookie flush) — prefer
    leaving CDP up so Resolve can attach without relaunch.
    """
    if not wait:
        out = ensure_linkedin_resolve_browser(url=url, steal_focus=True)
        # Normalize keys for callers that expect launch_login_browser shape.
        out.setdefault("waited", False)
        out.setdefault("url", url)
        if out.get("already_open_no_cdp"):
            out["already_open"] = True
        return out

    # Legacy: one-shot headed window that exits when user quits (no CDP attach).
    from chrome_for_testing import is_daily_google_chrome, resolve_chrome_for_testing

    cft = resolve_chrome_for_testing()
    result: dict = {
        "ok": False,
        "profile": str(linkedin_resolve_profile_dir()),
        "cft": str(cft) if cft else None,
        "url": url,
        "waited": True,
        "li_at": False,
        "error": None,
        "cdp_port": linkedin_resolve_cdp_port(),
    }
    if cft is None:
        result["error"] = (
            "Chrome for Testing / Chromium not found. "
            "Install with: python3 -m playwright install chromium"
        )
        return result
    if is_daily_google_chrome(cft):
        result["error"] = "Refusing daily Google Chrome.app — use Chrome for Testing"
        return result

    profile = linkedin_resolve_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    if profile_has_live_browser(profile):
        result["error"] = profile_in_use_message(profile)
        result["already_open"] = True
        return result

    _clear_stale_singleton(profile)
    # Legacy wait path still enables CDP so a later Resolve can attach if the
    # user leaves the window open instead of quitting.
    cmd = login_browser_cmd(cft, profile, url=url)
    assert "--headless" not in cmd and "--incognito" not in cmd

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        result["error"] = str(e)[:300]
        return result

    result["pid"] = proc.pid
    try:
        proc.wait()
    except KeyboardInterrupt:
        result["error"] = (
            "Interrupted while waiting — leave the LinkedIn CDP browser open "
            "(PartyRock-style) or quit with red X / Cmd+Q so cookies flush."
        )
        return result

    if flush_wait_s > 0:
        time.sleep(float(flush_wait_s))
    result["ok"] = True
    result["li_at"] = profile_has_li_at(profile)
    return result


def status_dict() -> dict:
    profile = linkedin_resolve_profile_dir()
    ready = profile_looks_initialized(profile)
    has_li_at = profile_has_li_at(profile) if ready else False
    in_use = profile_has_live_browser(profile)
    cdp_up = cdp_port_open()
    return {
        "profile": str(profile),
        "exists": profile.is_dir(),
        "initialized": ready,
        "li_at": has_li_at,
        "logged_in": has_li_at,
        "in_use": in_use,
        "cdp_port": linkedin_resolve_cdp_port(),
        "cdp_up": cdp_up,
        "login_hint": (
            login_required_message(profile)
            if not ready or not has_li_at
            else (
                None
                if cdp_up
                else (
                    profile_in_use_message(profile)
                    if in_use and not cdp_up
                    else login_required_message(profile)
                )
            )
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--path", action="store_true", help="Print profile directory path")
    g.add_argument("--status", action="store_true", help="JSON status (initialized?)")
    g.add_argument(
        "--profile-ok",
        action="store_true",
        help="Print yes/no whether logged_in (li_at present); never print cookie values",
    )
    g.add_argument(
        "--login",
        action="store_true",
        help="Ensure CfT+CDP LinkedIn browser is up and open login (leave running)",
    )
    g.add_argument(
        "--ensure",
        action="store_true",
        help="Ensure LinkedIn resolve CDP is up (no URL); JSON with --json",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="With --login: legacy block until Chrome quits (prefer leave CDP open)",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Deprecated no-op (default already leaves browser running)",
    )
    parser.add_argument("--json", action="store_true", help="JSON for --login/--ensure")
    parser.add_argument(
        "--open-url",
        default=None,
        help="With --ensure/--login: URL to open (default login for --login)",
    )
    args = parser.parse_args(argv)

    if args.path:
        print(linkedin_resolve_profile_dir())
        return 0
    if args.status:
        print(json.dumps(status_dict(), indent=2))
        return 0
    if args.profile_ok:
        # Boolean only — never cookie values.
        print("yes" if profile_is_logged_in() else "no")
        return 0 if profile_is_logged_in() else 1

    if args.ensure:
        url = args.open_url
        out = ensure_linkedin_resolve_browser(url=url)
        if args.json:
            print(json.dumps(out, indent=2))
        elif out.get("ok"):
            print(
                f"LinkedIn resolve CDP ok via={out.get('via')} "
                f"port={out.get('cdp_port')} cft={out.get('cft')}"
            )
        else:
            print(f"error: {out.get('error')}", file=sys.stderr)
        return 0 if out.get("ok") else 1

    # --login (PartyRock-style: leave browser running)
    profile = linkedin_resolve_profile_dir()
    port = linkedin_resolve_cdp_port()
    print(f"LinkedIn resolve profile: {profile}")
    print(f"CDP: http://127.0.0.1:{port}  (same mechanism as PartyRock :18800)")
    print("Sign into LinkedIn in that window; leave it open for Resolve ATS.")
    print("Never use daily Chrome, dashboard UI, PartyRock, or fill profiles.")

    url = args.open_url or LOGIN_URL
    if args.wait:
        out = launch_login_browser(url=url, wait=True)
    else:
        out = ensure_linkedin_resolve_browser(url=url, steal_focus=True)

    if args.json:
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1

    if out.get("already_open_no_cdp") or out.get("already_open"):
        print(f"error: {out.get('error')}", file=sys.stderr)
        return 2
    if not out.get("ok"):
        print(f"error: {out.get('error')}", file=sys.stderr)
        return 1

    print(f"LinkedIn CDP ok via={out.get('via')} cft={out.get('cft')}")
    if out.get("waited"):
        print(
            "Window closed. logged_in="
            + ("yes" if out.get("li_at") else "no")
            + " (check: python3 scripts/linkedin_resolve_profile.py --profile-ok)"
        )
        return 0 if out.get("li_at") else 1
    print(
        "Browser left running. After feed/home loads, confirm with:\n"
        "  python3 scripts/linkedin_resolve_profile.py --profile-ok\n"
        "Then use Resolve ATS (attaches via CDP — do not quit the window)."
    )
    if out.get("li_at"):
        print("logged_in=yes (li_at present)")
    else:
        print("logged_in=no yet — finish signing in in the open window.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
