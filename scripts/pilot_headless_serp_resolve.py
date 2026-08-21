#!/usr/bin/env python3
"""Bounded pilot: SERP resolve for unresolved LinkedIn jobs.

Uses an ephemeral Playwright Chromium/Chrome context (NOT the LinkedIn CfT
profile). Default headless: DuckDuckGo → Bing → Brave (no Google). Pass
``--headed`` for visible Chrome (Google primary by default).

Rule: on first CAPTCHA/challenge for an engine, mark that engine **dead** and
skip it for the rest of the run (never solve CAPTCHA). Cookie-consent Accept
is OK. Never submits. Restores only high-confidence ATS/company job URLs.

Usage:
  skyvern_runtime/venv/bin/python scripts/pilot_headless_serp_resolve.py
  skyvern_runtime/venv/bin/python scripts/pilot_headless_serp_resolve.py --dry-run
  skyvern_runtime/venv/bin/python scripts/pilot_headless_serp_resolve.py \\
      --engines duckduckgo,bing,brave --limit 25
  skyvern_runtime/venv/bin/python scripts/pilot_headless_serp_resolve.py \\
      --headed --engines google --limit 10
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from resolve_apply_urls import (  # noqa: E402
    build_search_queries,
    default_fetch,
    filter_candidate_urls,
    is_acceptable_resolve_target,
    locked_jobs_for_read,
    locked_jobs_for_write,
    merge_resolved_apply,
    now_iso,
    parse_ddg_html,
    prefer_company_relevant_urls,
    restore_unresolved_deleted_job,
    score_candidate,
    set_apply_resolve_fields,
    stamp_unresolved_apply_url_tag,
)

# Reuse Bing HTML link extraction by importing the module-level regexes via
# calling the same parsing logic on Playwright-fetched HTML.
import resolve_apply_urls as rau  # noqa: E402

try:
    from pw_fetch_html import looks_like_challenge_page, resolve_chromium_executable
except ImportError:
    looks_like_challenge_page = None  # type: ignore
    resolve_chromium_executable = lambda: None  # noqa: E731

OUT_DIR = ROOT / "logs"
OUT_JSON = OUT_DIR / "pilot_headless_serp_resolve.json"
OUT_MD = OUT_DIR / "pilot_headless_serp_resolve.md"
OUT_JSON_HEADED = OUT_DIR / "pilot_headed_google_serp_resolve.json"
OUT_MD_HEADED = OUT_DIR / "pilot_headed_google_serp_resolve.md"
OUT_JSON_ALT = OUT_DIR / "pilot_alt_serp_resolve.json"
OUT_MD_ALT = OUT_DIR / "pilot_alt_serp_resolve.md"

# Default headless path: non-Google only (Google CAPTCHA thrash in prior pilots).
ENGINES_DEFAULT = ("duckduckgo", "bing", "brave", "startpage", "mojeek")
ENGINES_HEADED_DEFAULT = ("google",)
ALLOWED_ENGINES = frozenset(
    {"google", "bing", "duckduckgo", "brave", "startpage", "mojeek"}
)

# Jobs already restored (or false-positive reverted) in prior pilots —
# prefer fresh unresolved-deleted rows when available.
_PRIOR_PILOT_IDS = frozenset(
    {
        "envision-data-scientists-w2",
        "smartfox-machine-learning-engineer",
        "place-data-scientist",
        "google-business-data-scientist-youtube-marketing",
        "logical-intelligence-ai-engineer-in-ml-data",
        "fanuc-america-corporation-research-engineer",
        "microsoft-ai-applied-scientist-ii",
        "precision-technologies-data-scientist",
        "prime-video-amazon-mgm-studios-applied-scientist-ii-prime-video",
        "suffolk-construction-site-ai-engineer-las-vegas-nv",
        "amazon-applied-scientist-fauna-2",
    }
)

_CAPTCHA_RE = re.compile(
    r"(unusual\s+traffic|are\s+you\s+a\s+robot|captcha|recaptcha|"
    r"verify\s+you\s+are\s+human|g-recaptcha|cf-challenge|"
    r"attention\s+required|access\s+denied)",
    re.I,
)
_CONSENT_RE = re.compile(
    r"(consent\.google|before\s+you\s+continue|we\s+use\s+cookies|"
    r"i\s+agree|accept\s+all|privacy\s+reminder|동의|Einwilligung)",
    re.I,
)
_HTTP_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pick_pilot_jobs(limit: int = 10, *, prefer_fresh: bool = True) -> list[dict]:
    with locked_jobs_for_read() as data:
        jobs = list(data.get("jobs") or [])
    li: list[dict] = []
    for j in jobs:
        if str(j.get("status") or "").strip().lower() != "deleted":
            continue
        if str(j.get("deleted_reason") or "").strip().lower() != "unresolved_apply_url":
            continue
        if not (j.get("company") and j.get("title")):
            continue
        blob = " ".join(
            str(j.get(k) or "") for k in ("source", "apply_url", "job_url")
        ).lower()
        if "linkedin" not in blob and (j.get("source") or "").lower() != "linkedin":
            continue
        li.append(j)

    def _ts(j: dict) -> float:
        s = str(j.get("deleted_at") or "")
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return 0.0

    li.sort(key=_ts, reverse=True)
    # Prefer jobs not already tried/restored in the headless pilot.
    if prefer_fresh:
        fresh = [j for j in li if str(j.get("id") or "") not in _PRIOR_PILOT_IDS]
        prior = [j for j in li if str(j.get("id") or "") in _PRIOR_PILOT_IDS]
        li = fresh + prior

    seen_co: set[str] = set()
    out: list[dict] = []
    for j in li:
        co = str(j.get("company") or "").strip().lower()
        if not co or co in seen_co:
            continue
        if "jack" in co and "jill" in co:
            continue
        seen_co.add(co)
        out.append(dict(j))
        if len(out) >= limit:
            break
    return out


def try_accept_cookie_consent(page) -> bool:
    """Click a simple Accept/I agree cookie button. Never touch CAPTCHA widgets."""
    # If this is already a /sorry/ challenge, do not click anything.
    url_l = (page.url or "").lower()
    if "/sorry/" in url_l or "recaptcha" in url_l:
        return False
    selectors = [
        'button:has-text("Accept all")',
        'button:has-text("Accept All")',
        'button:has-text("Accept all cookies")',
        'button:has-text("I agree")',
        'button:has-text("I Agree")',
        'button:has-text("Accept")',
        'button:has-text("Agree")',
        'button:has-text("Got it")',
        '#L2AGLb',  # Google consent Accept all
        'button[aria-label="Accept all"]',
        'button[aria-label="Accept"]',
        'form[action*="consent"] button',
        '#onetrust-accept-btn-handler',
        'button#accept',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            # Skip if element looks like a captcha submit.
            txt = (loc.inner_text(timeout=500) or "").lower()
            if any(x in txt for x in ("captcha", "verify", "robot", "challenge")):
                continue
            loc.click(timeout=1500)
            page.wait_for_timeout(600)
            return True
        except Exception:
            continue
    return False


def detect_block(html: str | None, final_url: str = "") -> str | None:
    """Return 'captcha' | 'consent' | 'empty' | None if page looks usable."""
    if not html or len(html) < 80:
        return "empty"
    head = html[:12000]
    url_l = (final_url or "").lower()
    if "consent.google" in url_l or "/sorry/" in url_l:
        return "captcha" if "/sorry/" in url_l else "consent"
    if _CAPTCHA_RE.search(head) or (looks_like_challenge_page and looks_like_challenge_page(html)):
        # Challenge helper is broad; prefer captcha when keywords match.
        if _CAPTCHA_RE.search(head) or "/sorry/" in url_l:
            return "captcha"
        if _CONSENT_RE.search(head):
            return "consent"
        return "captcha"
    if _CONSENT_RE.search(head) and (
        "google" in url_l or "consent" in head[:2000].lower()
    ):
        # Soft consent interstitial without results.
        if "google.com/search" not in url_l and len(html) < 50000:
            return "consent"
    return None


def parse_google_html(html: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        u = unquote(str(raw or "").strip())
        if not u.startswith("http"):
            return
        host = (urlparse(u).hostname or "").lower()
        if not host or any(
            x in host
            for x in (
                "google.",
                "gstatic.",
                "youtube.com",
                "schema.org",
                "w3.org",
            )
        ):
            return
        if "/search" in u and "google" in host:
            return
        key = u.split("#")[0].lower()
        if key in seen:
            return
        seen.add(key)
        found.append(u)

    # /url?q= unwrap
    for m in re.finditer(r"/url\?q=([^&\"']+)", html or ""):
        _add(m.group(1))
    for m in re.finditer(r"[?&]url=(https?[^&\"']+)", html or ""):
        _add(m.group(1))
    for m in re.finditer(
        r'<a[^>]+href="(https?://[^"]+)"[^>]*>', html or "", re.I
    ):
        _add(m.group(1))
    return found


def parse_bing_html_local(html: str) -> list[str]:
    """Mirror resolve_apply_urls.search_bing_html parsing without HTTP fetch."""
    html = (html or "").replace("&amp;", "&")
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        u = str(raw or "").strip()
        if not u:
            return
        host = (urlparse(u).hostname or "").lower()
        if not host or "bing.com" in host or "microsoft.com" in host:
            return
        key = u.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(u)

    for m in re.finditer(r"[?&]u=(a1[^&\"'\s]+)", html):
        payload = m.group(1)[2:]
        pad = "=" * ((4 - len(payload) % 4) % 4)
        try:
            import base64

            dec = base64.urlsafe_b64decode(payload + pad).decode("utf-8", "replace")
        except Exception:
            continue
        if dec.startswith("http"):
            _add(dec)
    for m in re.finditer(r"<cite[^>]*>(.*?)</cite>", html, re.I | re.S):
        cite = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        cite = cite.replace(" › ", "/").replace("»", "/").strip()
        if cite.startswith("http"):
            _add(cite)
        elif "." in cite and " " not in cite:
            _add("https://" + cite.lstrip("/"))
    for m in _HTTP_RE.finditer(html):
        u = m.group(0).rstrip(".,;:!?)")
        if rau.is_known_ats_url(u) or rau.looks_like_job_apply_url(u):
            _add(u)
    return found


def engine_search_url(engine: str, query: str) -> str:
    q = urlencode({"q": query})
    if engine == "google":
        return f"https://www.google.com/search?{q}&hl=en&num=10"
    if engine == "bing":
        return f"https://www.bing.com/search?{q}&count=10"
    if engine == "duckduckgo":
        return f"https://html.duckduckgo.com/html/?{q}"
    if engine == "brave":
        return f"https://search.brave.com/search?{q}"
    if engine == "startpage":
        return f"https://www.startpage.com/sp/search?{q}"
    if engine == "mojeek":
        return f"https://www.mojeek.com/search?{q}"
    raise ValueError(engine)


def _parse_generic_result_links(html: str, skip_hosts: tuple[str, ...]) -> list[str]:
    """Pull http(s) anchors; drop engine self-links; keep ATS/job-shaped."""
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        u = unquote(str(raw or "").strip())
        if not u.startswith("http"):
            return
        host = (urlparse(u).hostname or "").lower()
        if not host or any(h in host for h in skip_hosts):
            return
        key = u.split("#")[0].lower()
        if key in seen:
            return
        seen.add(key)
        found.append(u)

    for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"', html or "", re.I):
        _add(m.group(1))
    for m in _HTTP_RE.finditer(html or ""):
        u = m.group(0).rstrip(".,;:!?)")
        if rau.is_known_ats_url(u) or rau.looks_like_job_apply_url(u):
            _add(u)
    return found


def parse_brave_html_local(html: str) -> list[str]:
    """Mirror resolve_apply_urls.search_brave_html on Playwright HTML."""
    html = (html or "").replace("&amp;", "&")
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        u = str(raw or "").strip()
        if not u.startswith("http"):
            return
        host = (urlparse(u).hostname or "").lower()
        if not host or "brave.com" in host or "search.brave" in host:
            return
        key = u.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(u)

    for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"', html, re.I):
        u = m.group(1).strip()
        if (
            rau.is_known_ats_url(u)
            or rau.looks_like_job_apply_url(u)
            or rau.is_ats_or_company_apply(u)
        ):
            _add(u)
    if not found:
        for m in _HTTP_RE.finditer(html):
            u = m.group(0).rstrip(".,;:!?)")
            if rau.is_known_ats_url(u) or rau.looks_like_job_apply_url(u):
                _add(u)
                if len(found) >= 20:
                    break
    return found


def parse_engine_html(engine: str, html: str) -> list[str]:
    if engine == "google":
        return parse_google_html(html)
    if engine == "bing":
        return parse_bing_html_local(html)
    if engine == "duckduckgo":
        return parse_ddg_html(html)
    if engine == "brave":
        return parse_brave_html_local(html)
    if engine == "startpage":
        return _parse_generic_result_links(
            html, ("startpage.com", "startmail.com", "ixquick.com")
        )
    if engine == "mojeek":
        return _parse_generic_result_links(html, ("mojeek.com",))
    return []


def score_hits(job: dict, urls: list[str]) -> dict | None:
    company = str(job.get("company") or "")
    candidates = prefer_company_relevant_urls(filter_candidate_urls(urls), company)
    if not candidates:
        return None
    best = None
    for url in candidates[:8]:
        try:
            page = default_fetch(url)
        except Exception:
            page = None
        scored = score_candidate(job, url, page)
        scored["reason"] = scored.get("reason") or "public_search"
        scored["method"] = "serp_pilot"
        if best is None:
            best = scored
        else:
            rank = {"high": 0, "medium": 1, "low": 2}
            if rank.get(scored.get("confidence"), 9) < rank.get(best.get("confidence"), 9):
                best = scored
            elif scored.get("confidence") == best.get("confidence") and float(
                scored.get("score") or 0
            ) > float(best.get("score") or 0):
                best = scored
        if best and best.get("confidence") == "high":
            break
    return best


def restore_high(
    job_id: str, url: str, *, dry_run: bool, resolve_method: str = "serp_pilot"
) -> bool:
    if dry_run:
        return True
    if not is_acceptable_resolve_target(url):
        return False
    unblocked = None
    with locked_jobs_for_write() as data:
        live = next((j for j in data.get("jobs") or [] if j.get("id") == job_id), None)
        if live is None:
            return False
        ok = restore_unresolved_deleted_job(
            live,
            apply_url=url,
            resolve_reason="public_search",
            resolve_method=resolve_method,
        )
        if not ok:
            if is_acceptable_resolve_target(url):
                merge_resolved_apply(live, url)
                set_apply_resolve_fields(
                    live,
                    {
                        "confidence": "high",
                        "url": url,
                        "reason": "public_search",
                        "method": resolve_method,
                        "score": 1.0,
                    },
                )
                stamp_unresolved_apply_url_tag(live, on=False)
                live["status"] = "discovered"
                live.pop("deleted_reason", None)
                live.pop("deleted_at", None)
                live["updated_at"] = now_iso()
            else:
                return False
        live["apply_resolve_search_attempted"] = True
        live["updated_at"] = now_iso()
        unblocked = dict(live)
    if unblocked is not None:
        try:
            from blocked_urls import unblock_job

            unblock_job(unblocked)
        except Exception:
            pass
        try:
            # Nudge dashboard list cache if server module is importable.
            import importlib

            srv = importlib.import_module("dashboard.server")
            if hasattr(srv, "_invalidate_jobs_list_cache"):
                srv._invalidate_jobs_list_cache()
        except Exception:
            pass
    return True


def run_pilot(
    *,
    limit: int = 10,
    dry_run: bool = False,
    max_seconds: float = 900.0,
    headed: bool = False,
    engines: tuple[str, ...] | None = None,
) -> dict:
    jobs = pick_pilot_jobs(limit=limit, prefer_fresh=True)
    started = time.monotonic()
    results: list[dict] = []
    engines = tuple(engines) if engines else (
        ENGINES_HEADED_DEFAULT if headed else ENGINES_DEFAULT
    )
    resolve_method = "headed_serp" if headed else "headless_serp"
    no_google = "google" not in engines
    if headed:
        out_json, out_md = OUT_JSON_HEADED, OUT_MD_HEADED
    elif no_google:
        out_json, out_md = OUT_JSON_ALT, OUT_MD_ALT
    else:
        out_json, out_md = OUT_JSON, OUT_MD

    # Per-engine counters; retired on first CAPTCHA for that engine.
    engine_stats: dict[str, dict] = {
        e: {
            "attempted": 0,
            "restored": 0,
            "captcha_at_job": None,
            "retired": False,
            "consent": 0,
            "empty": 0,
            "no_ats": 0,
            "low_conf": 0,
            "errors": 0,
        }
        for e in engines
    }
    dead_engines: set[str] = set()

    from playwright.sync_api import sync_playwright

    launch_kwargs: dict = {
        "headless": not headed,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    }
    # Headed: prefer system Chrome (more human-like). Never LinkedIn CfT profile.
    # Headless: keep bundled Chromium / optional exe from pw_fetch_html.
    if headed:
        from pathlib import Path as _P

        if _P("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").exists():
            launch_kwargs["channel"] = "chrome"
        else:
            exe = (
                resolve_chromium_executable()
                if callable(resolve_chromium_executable)
                else None
            )
            if exe:
                launch_kwargs["executable_path"] = exe
    else:
        exe = (
            resolve_chromium_executable()
            if callable(resolve_chromium_executable)
            else None
        )
        if exe:
            launch_kwargs["executable_path"] = exe

    print(
        f"pilot jobs={len(jobs)} dry_run={dry_run} headed={headed} "
        f"engines={engines} launch={launch_kwargs.get('channel') or launch_kwargs.get('executable_path') or 'default'}",
        flush=True,
    )

    def _new_context(browser):
        # Ephemeral — never LinkedIn CfT / fill profiles.
        # Headed Chrome: omit spoofed UA (version mismatch looks botty).
        kwargs: dict = {
            "locale": "en-US",
            "viewport": {"width": 1280, "height": 900},
        }
        if not headed:
            kwargs["user_agent"] = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        ctx = browser.new_context(**kwargs)
        pg = ctx.new_page()
        pg.set_default_timeout(25000)
        return ctx, pg

    def _retire_engine(engine: str, job_idx: int, reason: str = "captcha") -> None:
        st = engine_stats[engine]
        if st["retired"]:
            return
        st["retired"] = True
        st["captcha_at_job"] = job_idx
        dead_engines.add(engine)
        print(
            f"  !! engine {engine} RETIRED after {reason} at job#{job_idx} "
            f"(skip for rest of run)",
            flush=True,
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        context = page = None
        try:
            context, page = _new_context(browser)

            for idx, job in enumerate(jobs, 1):
                if time.monotonic() - started > max_seconds:
                    print("wall-clock cap reached; stopping", flush=True)
                    break

                live_engines = [e for e in engines if e not in dead_engines]
                if not live_engines:
                    print(
                        f"all engines dead after CAPTCHA; stopping at job#{idx}",
                        flush=True,
                    )
                    break

                # Fresh context per job when headed (CAPTCHA/close isolation).
                if headed and idx > 1:
                    try:
                        context.close()
                    except Exception:
                        pass
                    try:
                        if not browser.is_connected():
                            browser = p.chromium.launch(**launch_kwargs)
                    except Exception:
                        browser = p.chromium.launch(**launch_kwargs)
                    context, page = _new_context(browser)

                jid = str(job.get("id") or "")
                company = str(job.get("company") or "")
                title = str(job.get("title") or "")
                queries = build_search_queries(company, title, job.get("location"))
                # Prefer company+title (+ apply/careers) phrasing.
                query = f"{company} {title} apply careers"
                if queries:
                    # Still use company-first when available; append apply/careers.
                    base = queries[0]
                    if "apply" not in base.lower() and "career" not in base.lower():
                        query = f"{base} apply careers"
                    else:
                        query = base
                row = {
                    "id": jid,
                    "company": company,
                    "title": title,
                    "query": query,
                    "engine": None,
                    "result": "fail",
                    "fail_reason": None,
                    "url": None,
                    "captcha": False,
                    "engine_attempts": [],
                }
                print(f"\n[{idx}/{len(jobs)}] {company} / {title[:50]}", flush=True)
                print(f"  query: {query}", flush=True)
                print(f"  live engines: {live_engines}", flush=True)

                restored = False
                for engine in live_engines:
                    if time.monotonic() - started > max_seconds:
                        break
                    if engine in dead_engines:
                        continue
                    engine_stats[engine]["attempted"] += 1
                    attempt = {"engine": engine, "status": "pending"}
                    url = engine_search_url(engine, query)
                    try:
                        # Recover if prior CAPTCHA/close killed the browser.
                        try:
                            _ = page.url
                        except Exception:
                            try:
                                if browser is None or not browser.is_connected():
                                    browser = p.chromium.launch(**launch_kwargs)
                            except Exception:
                                browser = p.chromium.launch(**launch_kwargs)
                            context, page = _new_context(browser)
                            print("  (relaunched browser/context)", flush=True)

                        page.goto(url, wait_until="domcontentloaded", timeout=25000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=6000)
                        except Exception:
                            pass
                        page.wait_for_timeout(900 if headed else 800)
                        # Simple cookie consent Accept is OK (not CAPTCHA).
                        if try_accept_cookie_consent(page):
                            attempt["consent_clicked"] = True
                            engine_stats[engine]["consent"] += 1
                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=8000)
                            except Exception:
                                pass
                            page.wait_for_timeout(700)
                        html = page.content()
                        final_url = page.url or url
                    except Exception as e:
                        attempt["status"] = "error"
                        attempt["error"] = str(e)[:160]
                        row["engine_attempts"].append(attempt)
                        engine_stats[engine]["errors"] += 1
                        print(f"  {engine}: error {e}", flush=True)
                        # Force relaunch next attempt/job
                        try:
                            context.close()
                        except Exception:
                            pass
                        try:
                            if browser and browser.is_connected():
                                pass
                            else:
                                browser = p.chromium.launch(**launch_kwargs)
                        except Exception:
                            try:
                                browser = p.chromium.launch(**launch_kwargs)
                            except Exception as e2:
                                print(f"  relaunch failed: {e2}", flush=True)
                        try:
                            context, page = _new_context(browser)
                        except Exception:
                            pass
                        continue

                    block = detect_block(html, final_url)
                    if block == "consent" and headed:
                        # One more consent attempt after detect, then re-check.
                        if try_accept_cookie_consent(page):
                            attempt["consent_clicked"] = True
                            engine_stats[engine]["consent"] += 1
                            page.wait_for_timeout(800)
                            html = page.content()
                            final_url = page.url or url
                            block = detect_block(html, final_url)
                    if block:
                        attempt["status"] = block
                        attempt["final_url"] = final_url[:200]
                        row["engine_attempts"].append(attempt)
                        if block == "captcha":
                            row["captcha"] = True
                            _retire_engine(engine, idx, "captcha")
                        elif block == "empty":
                            engine_stats[engine]["empty"] += 1
                        print(f"  {engine}: {block}", flush=True)
                        # After CAPTCHA/consent: new ephemeral context (don't
                        # reuse poisoned session). Never solve CAPTCHA.
                        if block in ("captcha", "consent"):
                            try:
                                context.close()
                            except Exception:
                                pass
                            try:
                                if not browser.is_connected():
                                    browser = p.chromium.launch(**launch_kwargs)
                                context, page = _new_context(browser)
                            except Exception as e:
                                print(f"  context reset failed: {e}", flush=True)
                        continue

                    links = parse_engine_html(engine, html)
                    ats = filter_candidate_urls(links)
                    attempt["raw_links"] = len(links)
                    attempt["ats_links"] = len(ats)
                    if not links:
                        attempt["status"] = "empty"
                        row["engine_attempts"].append(attempt)
                        engine_stats[engine]["empty"] += 1
                        print(f"  {engine}: empty", flush=True)
                        continue
                    if not ats:
                        attempt["status"] = "no_ats"
                        attempt["sample"] = links[:3]
                        row["engine_attempts"].append(attempt)
                        engine_stats[engine]["no_ats"] += 1
                        print(f"  {engine}: no_ats ({len(links)} links)", flush=True)
                        continue

                    scored = score_hits(job, links)
                    if not scored or not scored.get("url"):
                        attempt["status"] = "no_ats"
                        row["engine_attempts"].append(attempt)
                        engine_stats[engine]["no_ats"] += 1
                        print(f"  {engine}: no_ats after score", flush=True)
                        continue
                    conf = str(scored.get("confidence") or "low")
                    attempt["confidence"] = conf
                    attempt["candidate"] = scored.get("url")
                    if conf != "high":
                        attempt["status"] = "low_conf" if conf == "low" else "medium_conf"
                        row["engine_attempts"].append(attempt)
                        engine_stats[engine]["low_conf"] += 1
                        print(
                            f"  {engine}: {attempt['status']} → {scored.get('url')}",
                            flush=True,
                        )
                        # Keep searching other engines for high.
                        continue

                    # High confidence — restore.
                    attempt["status"] = "high"
                    row["engine_attempts"].append(attempt)
                    ok = restore_high(
                        jid,
                        str(scored["url"]),
                        dry_run=dry_run,
                        resolve_method=resolve_method,
                    )
                    if ok:
                        row["engine"] = engine
                        row["result"] = "restored" if not dry_run else "would_restore"
                        row["url"] = scored["url"]
                        row["fail_reason"] = None
                        restored = True
                        engine_stats[engine]["restored"] += 1
                        print(
                            f"  {engine}: HIGH → {scored['url']} "
                            f"({'dry-run' if dry_run else 'restored'})",
                            flush=True,
                        )
                        break
                    attempt["status"] = "restore_failed"
                    print(f"  {engine}: restore failed", flush=True)

                if not restored:
                    reasons = [
                        a.get("status") for a in row["engine_attempts"] if a.get("status")
                    ]
                    fail = "empty"
                    for pref in (
                        "captcha",
                        "consent",
                        "no_ats",
                        "low_conf",
                        "medium_conf",
                        "error",
                        "empty",
                    ):
                        if pref in reasons:
                            fail = pref
                            break
                    if not reasons:
                        fail = "all_engines_dead" if not live_engines else "empty"
                    if fail == "medium_conf":
                        fail = "low_conf"
                    row["fail_reason"] = fail
                    row["result"] = "fail"
                    row["engine"] = ",".join(
                        a["engine"] for a in row["engine_attempts"]
                    ) or None
                    print(f"  FAIL: {fail}", flush=True)

                results.append(row)
                # polite gap between jobs (slightly longer when headed/Google)
                time.sleep(2.5 if headed else 1.2)

            try:
                context.close()
            except Exception:
                pass
        finally:
            try:
                browser.close()
            except Exception:
                pass

    restored_n = sum(
        1 for r in results if r.get("result") in ("restored", "would_restore")
    )
    captcha_n = sum(1 for r in results if r.get("captcha"))
    engines_ok: dict[str, int] = {}
    for r in results:
        if r.get("result") in ("restored", "would_restore") and r.get("engine"):
            eng = str(r["engine"]).split(",")[0]
            engines_ok[eng] = engines_ok.get(eng, 0) + 1

    summary = {
        "started_at": _iso(),
        "dry_run": dry_run,
        "headed": headed,
        "engines": list(engines),
        "engine_stats": engine_stats,
        "dead_engines": sorted(dead_engines),
        "considered": len(results),
        "restored": restored_n,
        "captcha_jobs": captcha_n,
        "engines_that_restored": engines_ok,
        "elapsed_s": round(time.monotonic() - started, 1),
        "results": results,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2))
    _write_md(summary, out_md=out_md, headed=headed)
    summary["_out_json"] = str(out_json)
    summary["_out_md"] = str(out_md)
    return summary


def _write_md(
    summary: dict, *, out_md: Path | None = None, headed: bool = False
) -> None:
    engines = summary.get("engines") or []
    no_google = "google" not in engines
    if headed:
        mode = "Headed Google"
    elif no_google:
        mode = "Alt-SERP (no Google)"
    else:
        mode = "Headless"
    lines = [
        f"# {mode} SERP resolve pilot",
        "",
        f"- Restored: **{summary['restored']}/{summary['considered']}**",
        f"- CAPTCHA jobs: {summary.get('captcha_jobs', 0)}",
        f"- Dry-run: {summary['dry_run']}",
        f"- Headed: {summary.get('headed', headed)}",
        f"- Engines: {summary.get('engines')}",
        f"- Dead engines: {summary.get('dead_engines') or []}",
        f"- Elapsed: {summary['elapsed_s']}s",
        f"- Engines that restored: {summary.get('engines_that_restored') or {}}",
        "",
        "## Per engine",
        "",
        "| engine | attempted | restored | captcha@job# | retired? |",
        "|---|---:|---:|---:|---|",
    ]
    for eng, st in (summary.get("engine_stats") or {}).items():
        cap = st.get("captcha_at_job")
        cap_s = str(cap) if cap is not None else "—"
        lines.append(
            f"| {eng} | {st.get('attempted', 0)} | {st.get('restored', 0)} | "
            f"{cap_s} | {'yes' if st.get('retired') else 'no'} |"
        )
    lines.extend(
        [
            "",
            "## Per job",
            "",
            "| id / company / title | captcha? | engine | result |",
            "|---|---|---|---|",
        ]
    )
    for r in summary.get("results") or []:
        label = f"`{r.get('id')}` / {r.get('company')} / {(r.get('title') or '')[:40]}"
        eng = r.get("engine") or "—"
        cap = "yes" if r.get("captcha") else "no"
        if r.get("result") in ("restored", "would_restore"):
            res = f"{r.get('result')}: `{r.get('url')}`"
        else:
            res = f"fail: {r.get('fail_reason')}"
        lines.append(f"| {label} | {cap} | {eng} | {res} |")
    lines.append("")
    path = out_md or OUT_MD
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=1200.0)
    ap.add_argument(
        "--headed",
        action="store_true",
        help="Visible Chrome (headless=False); Google primary by default",
    )
    ap.add_argument(
        "--engines",
        default="",
        help=(
            "Comma list: duckduckgo,bing,brave,startpage,mojeek,google "
            "(default: non-Google headless; google if --headed)"
        ),
    )
    args = ap.parse_args()
    engines = None
    if args.engines.strip():
        engines = tuple(
            e.strip().lower()
            for e in args.engines.split(",")
            if e.strip().lower() in ALLOWED_ENGINES
        )
    summary = run_pilot(
        limit=args.limit,
        dry_run=args.dry_run,
        max_seconds=args.max_seconds,
        headed=args.headed,
        engines=engines,
    )
    print("\n=== SUMMARY ===", flush=True)
    print(
        f"{summary['restored']}/{summary['considered']} restored; "
        f"captcha_jobs={summary.get('captcha_jobs')}; "
        f"dead={summary.get('dead_engines')}; "
        f"engines={summary.get('engines_that_restored')}; "
        f"elapsed={summary['elapsed_s']}s",
        flush=True,
    )
    for eng, st in (summary.get("engine_stats") or {}).items():
        print(
            f"  {eng}: attempted={st['attempted']} restored={st['restored']} "
            f"captcha@={st['captcha_at_job']} retired={st['retired']}",
            flush=True,
        )
    restored_rows = [
        r
        for r in summary.get("results") or []
        if r.get("result") in ("restored", "would_restore")
    ]
    if restored_rows:
        print("Restored:", flush=True)
        for r in restored_rows:
            print(f"  {r.get('id')}: {r.get('url')}", flush=True)
    print(f"wrote {summary.get('_out_json')}", flush=True)
    print(f"wrote {summary.get('_out_md')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
