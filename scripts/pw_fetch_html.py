#!/usr/bin/env python3
"""Headless Chromium HTML fetch for JS-rendered / rate-limited pages.

Used as a fallback after plain HTTP fails (Built In 429s, thin JS career
pages). Never solves CAPTCHA; never touches Workday/iCIMS bypass.

Usage:
  from pw_fetch_html import fetch_html_playwright
  html = fetch_html_playwright(url)
"""
from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path

# Challenge / bot interstitial heuristics — return None rather than junk HTML.
_CHALLENGE_RE = re.compile(
    r"(cf-browser-verification|challenge-platform|attention\s+required"
    r"|access\s+denied|captcha|akamai|just\s+a\s+moment\.{0,3}\s*</title>)",
    re.I,
)


def resolve_chromium_executable() -> str | None:
    """Prefer an existing Chromium / Chrome-for-Testing binary."""
    env = (os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or "").strip()
    if env and Path(env).exists():
        return env

    machine = platform.machine().lower()
    prefer = ["arm64", "x64"] if machine in ("arm64", "aarch64") else ["x64", "arm64"]

    search_roots: list[Path] = []
    env_browsers = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if env_browsers:
        search_roots.append(Path(env_browsers).expanduser())
    for default in (
        Path.home() / "Library/Caches/ms-playwright",
        Path.home() / ".cache/ms-playwright",
    ):
        if default not in search_roots:
            search_roots.append(default)

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
                for linux_name in (f"chrome-linux-{arch}", "chrome-linux"):
                    linux_cand = root / linux_name / "chrome"
                    if linux_cand.is_file():
                        candidates.append(
                            (arch if "arm" in linux_name else "x64", linux_cand)
                        )

    for arch in prefer:
        for a, cand in candidates:
            if a == arch:
                return str(cand)

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if exe and Path(exe).exists():
                if machine in ("arm64", "aarch64") and "chrome-mac-x64" in str(exe):
                    return None
                return exe
    except Exception:
        pass
    return None


def looks_like_challenge_page(html: str | None) -> bool:
    if not html or len(html) < 50:
        return True
    head = html[:8000]
    return bool(_CHALLENGE_RE.search(head))


def fetch_html_playwright(
    url: str,
    *,
    timeout_ms: int = 25000,
    log=None,
) -> str | None:
    """Return rendered page HTML, or None on failure / challenge page.

    Launches one headless Chromium context per call and always closes it.
    """
    _log = log or (lambda msg: print(f"pw_fetch: {msg}", file=sys.stderr, flush=True))
    if not url or not str(url).startswith(("http://", "https://")):
        return None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _log("playwright not installed")
        return None

    exe = resolve_chromium_executable()
    try:
        with sync_playwright() as p:
            launch_kwargs: dict = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if exe:
                launch_kwargs["executable_path"] = exe
            browser = p.chromium.launch(**launch_kwargs)
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 900},
                )
                try:
                    page = context.new_page()
                    page.set_default_timeout(timeout_ms)
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    try:
                        page.wait_for_load_state(
                            "networkidle", timeout=min(8000, timeout_ms)
                        )
                    except Exception:
                        pass  # networkidle often never settles; DOM is enough
                    html = page.content()
                finally:
                    context.close()
            finally:
                browser.close()

            if looks_like_challenge_page(html):
                _log(f"challenge/interstitial detected for {url}")
                return None
            _log(f"ok {url} ({len(html)} chars)")
            return html
    except Exception as exc:
        _log(f"failed for {url}: {exc}")
        return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: pw_fetch_html.py URL", file=sys.stderr)
        sys.exit(2)
    out = fetch_html_playwright(sys.argv[1])
    if out is None:
        sys.exit(1)
    sys.stdout.write(out)
