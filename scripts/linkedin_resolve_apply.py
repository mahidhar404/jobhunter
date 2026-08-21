#!/usr/bin/env python3
"""Capture offsite apply URLs from LinkedIn job pages.

Fast path: decrypt ``li_at`` (+ LinkedIn cookies) from
``linkedin_resolve_profile`` Chromium cookie DB, HTTP GET the job URL, parse
``companyApplyUrl`` / Apply hrefs / ``/safety/go/?url=`` (no Playwright tab).

Fallback: long-lived Chrome-for-Testing + CDP (``:18801``), background tab,
``domcontentloaded`` only — concurrency ≤2 (env ``LINKEDIN_RESOLVE_CONCURRENCY``,
max 3). Never one-shot ``launch_persistent_context`` relaunches.

Never: submit an application, click final Submit, solve CAPTCHA, automate
Easy Apply, or use daily Chrome / dashboard_ui / PartyRock / fill profiles.

Public search resolve (``resolve_apply_urls``) remains the fallback when this
path is unavailable or finds no offsite link.
"""
from __future__ import annotations

import html as html_lib
import os
import re
import sys
import threading
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from apply_urls import (  # noqa: E402
    is_aggregator_url,
    is_ats_or_company_apply,
    is_known_ats_url,
    normalize_url,
)
from linkedin_resolve_profile import (  # noqa: E402
    ensure_linkedin_resolve_browser,
    linkedin_resolve_cdp_ws_endpoint,
    linkedin_resolve_profile_dir,
    load_linkedin_cookies,
    login_required_message,
    profile_has_li_at,
    profile_in_use_message,
)

UNFETCHABLE_HOST_HINTS = (
    "myworkdayjobs.com",
    "myworkdaysite.com",
    "icims.com",
)

_CAPTCHA_HINTS = (
    "complete the captcha",
    "solve the captcha",
    "captcha required",
    "hcaptcha challenge",
    "recaptcha",
    "cf-challenge",
    "challenges.cloudflare",
    "verify you are human",
    "unusual activity from your network",
)

_SIGNIN_URL_HINTS = (
    "/login",
    "/checkpoint/",
    "/uas/login",
    "authwall",
)

DEFAULT_TIMEOUT_MS = 45_000
NAV_TIMEOUT_MS = 30_000
DEFAULT_RESOLVE_CONCURRENCY = 2
MAX_RESOLVE_CONCURRENCY = 3
DEFAULT_HTTP_CONCURRENCY = 36
MAX_HTTP_CONCURRENCY = 40
# HTTP GET timeout — keep snappy (batch showed ~8–12s is enough; 25s was slow).
DEFAULT_HTTP_TIMEOUT_S = 10.0
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)

SessionFn = Callable[..., dict]

# Process-wide semaphore for CDP tabs only (HTTP path does not acquire).
_cdp_tab_sema: threading.Semaphore | None = None
_cdp_tab_sema_n: int = 0
_cdp_tab_lock = threading.Lock()
_owned_cdp_tabs = 0


def clamp_resolve_concurrency(n: int | None) -> int:
    try:
        v = int(n) if n is not None else DEFAULT_RESOLVE_CONCURRENCY
    except (TypeError, ValueError):
        v = DEFAULT_RESOLVE_CONCURRENCY
    return max(1, min(MAX_RESOLVE_CONCURRENCY, v))


def resolve_concurrency_from_env() -> int:
    raw = (os.environ.get("LINKEDIN_RESOLVE_CONCURRENCY") or "").strip()
    if not raw:
        return DEFAULT_RESOLVE_CONCURRENCY
    try:
        return clamp_resolve_concurrency(int(raw))
    except ValueError:
        return DEFAULT_RESOLVE_CONCURRENCY


def clamp_http_concurrency(n: int | None) -> int:
    try:
        v = int(n) if n is not None else DEFAULT_HTTP_CONCURRENCY
    except (TypeError, ValueError):
        v = DEFAULT_HTTP_CONCURRENCY
    return max(1, min(MAX_HTTP_CONCURRENCY, v))


def http_concurrency_from_env() -> int:
    raw = (os.environ.get("LINKEDIN_HTTP_CONCURRENCY") or "").strip()
    if not raw:
        return DEFAULT_HTTP_CONCURRENCY
    try:
        return clamp_http_concurrency(int(raw))
    except ValueError:
        return DEFAULT_HTTP_CONCURRENCY


def linkedin_allow_cdp_from_env() -> bool:
    """CDP is opt-in only — batch showed it never uniquely succeeded over HTTP.

    Set ``LINKEDIN_ALLOW_CDP=1`` to re-enable Playwright/CDP fallback.
    """
    return (os.environ.get("LINKEDIN_ALLOW_CDP") or "").strip() == "1"


def _cdp_semaphore() -> threading.Semaphore:
    global _cdp_tab_sema, _cdp_tab_sema_n
    n = resolve_concurrency_from_env()
    with _cdp_tab_lock:
        if _cdp_tab_sema is None or _cdp_tab_sema_n != n:
            _cdp_tab_sema = threading.Semaphore(n)
            _cdp_tab_sema_n = n
        return _cdp_tab_sema


# Attrs that may carry an apply destination (LinkedIn often uses data-* without href).
_APPLY_URL_ATTRS = (
    "href",
    "data-url",
    "data-href",
    "data-destination-url",
    "data-apply-url",
    "data-external-url",
    "data-control-name",
    "data-tracking-control-name",
    "data-control-name-value",
)

_HTTP_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_JS_VOID_RE = re.compile(
    r"^\s*(?:javascript:|#|about:blank|void\s*\(.*\)\s*;?)\s*$",
    re.IGNORECASE,
)
# Embedded apply destinations in LinkedIn page JSON / code blocks.
_EMBEDDED_APPLY_URL_RE = re.compile(
    r"(?:companyApplyUrl|externalApplyUrl|company_apply_url|external_apply_url|"
    r"applyUrl|apply_url)\s*[\"']?\s*[:=]\s*[\"'](https?://[^\"']+)[\"']",
    re.IGNORECASE,
)


def is_linkedin_job_url(url: str | None) -> bool:
    s = (url or "").strip()
    if not s:
        return False
    try:
        host = (urlparse(s).hostname or "").lower()
    except ValueError:
        return False
    if host.startswith("www."):
        host = host[4:]
    if "linkedin.com" not in host:
        return False
    path = (urlparse(s).path or "").lower()
    return "/jobs/" in path or "/job/" in path


def _host(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def is_unfetchable_ats(url: str | None) -> bool:
    host = _host(url or "")
    return any(h in host for h in UNFETCHABLE_HOST_HINTS)


def is_acceptable_offsite_apply(url: str | None) -> bool:
    """Company/ATS https URL that is not LinkedIn/aggregator and not Workday/iCIMS."""
    s = (url or "").strip()
    if not s:
        return False
    if is_aggregator_url(s):
        return False
    if is_unfetchable_ats(s):
        return False
    if is_known_ats_url(s) or is_ats_or_company_apply(s):
        return True
    return False


def page_looks_like_captcha(text: str) -> bool:
    blob = (text or "").lower()
    return any(h in blob for h in _CAPTCHA_HINTS)


def page_looks_like_signin_wall(text: str, url: str) -> bool:
    u = (url or "").lower()
    if any(h in u for h in _SIGNIN_URL_HINTS) and "linkedin.com" in u:
        return True
    blob = (text or "").lower()
    if "sign in" in blob and "linkedin" in blob and ("join now" in blob or "email" in blob):
        return True
    if "authwall" in u or "authwall" in blob:
        return True
    return False


def classify_apply_control(label: str) -> str:
    t = re.sub(r"\s+", " ", (label or "").strip().lower())
    if not t:
        return "unknown"
    if "easy apply" in t:
        return "easy_apply"
    if any(
        p in t
        for p in (
            "company website",
            "company site",
            "external",
            "continue to application",
            "apply on",
            "apply off",
        )
    ):
        return "external"
    if t == "apply" or t.startswith("apply "):
        return "external_or_unknown"
    return "unknown"


def _clean_candidate_url(raw: str | None) -> str:
    s = html_lib.unescape((raw or "").strip())
    if not s:
        return ""
    # LinkedIn sometimes double-encodes destinations in query params.
    if "%2f" in s.lower() or "%3a" in s.lower():
        try:
            s2 = unquote(s)
            if s2.startswith("http"):
                s = s2
        except Exception:
            pass
    # Trim trailing punctuation from regex grabs.
    return s.rstrip(".,);]'\"")


def http_urls_in_text(blob: str | None) -> list[str]:
    """Extract http(s) URLs from an attribute / text blob (deduped, order kept)."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _HTTP_URL_RE.finditer(blob or ""):
        u = _clean_candidate_url(m.group(0))
        if not u:
            continue
        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out


def unwrap_linkedin_redirect_url(url: str | None) -> str | None:
    """If href is a LinkedIn redirect wrapper, return the embedded destination."""
    s = _clean_candidate_url(url)
    if not s:
        return None
    try:
        p = urlparse(s)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if "linkedin.com" not in host:
        return None
    qs = parse_qs(p.query or "")
    for key in (
        "url",
        "session_redirect",
        "redirectUrl",
        "redirect_url",
        "destURL",
        "destUrl",
        "destinationUrl",
    ):
        vals = qs.get(key) or []
        if not vals:
            continue
        dest = _clean_candidate_url(vals[0])
        if dest.startswith("http") and "linkedin.com" not in _host(dest):
            return dest
    # Path-style: /redir/redirect/... sometimes puts target in fragment rarely.
    for mined in http_urls_in_text(unquote(p.query or "")):
        if "linkedin.com" not in _host(mined) and mined.startswith("http"):
            return mined
    return None


def is_useless_apply_href(url: str | None) -> bool:
    """True for empty / JS void / LinkedIn-hosted / Easy Apply destinations.

    LinkedIn redirect wrappers that embed an external destination are *not*
    useless — callers should unwrap via ``unwrap_linkedin_redirect_url`` first.
    """
    s = _clean_candidate_url(url)
    if not s:
        return True
    if unwrap_linkedin_redirect_url(s):
        return False
    if _JS_VOID_RE.match(s) or s.lower().startswith("javascript:"):
        return True
    low = s.lower()
    if low.startswith("/") and "linkedin.com" not in low:
        # Relative LinkedIn path — still on LinkedIn once resolved.
        if "easy-apply" in low or "/apply" in low or "/jobs/" in low:
            return True
    try:
        host = (urlparse(s).hostname or "").lower()
    except ValueError:
        return True
    if host.startswith("www."):
        host = host[4:]
    if "linkedin.com" in host:
        return True
    return False


def _normalize_candidate_href(raw: str | None) -> str:
    """Clean + unwrap LinkedIn redirects into a concrete destination URL."""
    u = _clean_candidate_url(raw)
    if not u:
        return ""
    unwrapped = unwrap_linkedin_redirect_url(u)
    return unwrapped or u


def pick_offsite_apply_href(
    candidates: list[dict] | list[str] | None,
) -> str | None:
    """Return first acceptable company/ATS URL from apply-control candidates."""
    for item in candidates or []:
        if isinstance(item, dict):
            hrefs = [str(item.get("href") or "")]
            # Also mine any attrs blob the collector attached.
            extra = str(item.get("attrs_blob") or "")
            if extra:
                hrefs.extend(http_urls_in_text(extra))
        else:
            hrefs = [str(item)]
        for raw in hrefs:
            u = _normalize_candidate_href(raw)
            if not u or is_useless_apply_href(u):
                # is_useless is False when unwrapable; still try mined URLs.
                if not u:
                    continue
                # Fall through only for mining nested http URLs in the raw string.
                for mined in http_urls_in_text(raw):
                    m = _normalize_candidate_href(mined)
                    if not m or is_useless_apply_href(m) or is_unfetchable_ats(m):
                        continue
                    if is_acceptable_offsite_apply(m):
                        return normalize_url(m) or m
                continue
            if is_unfetchable_ats(u):
                continue
            if is_acceptable_offsite_apply(u):
                return normalize_url(u) or u
            for mined in http_urls_in_text(u):
                m = _normalize_candidate_href(mined)
                if not m or is_useless_apply_href(m) or is_unfetchable_ats(m):
                    continue
                if is_acceptable_offsite_apply(m):
                    return normalize_url(m) or m
    return None


def extract_embedded_apply_urls(html: str | None) -> list[str]:
    """Pull company/external apply URLs from LinkedIn page JSON / markup."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _EMBEDDED_APPLY_URL_RE.finditer(html or ""):
        u = _clean_candidate_url(m.group(1))
        if not u:
            continue
        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out


class _ApplyHrefHTMLParser(HTMLParser):
    """Collect apply-ish anchors/buttons and URL-bearing attributes from HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: list[dict] = []
        self._stack: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = { (k or "").lower(): (v or "") for k, v in attrs }
        tag_l = (tag or "").lower()
        href = ad.get("href") or ""
        data_blob = " ".join(
            ad.get(a, "") for a in _APPLY_URL_ATTRS if ad.get(a)
        )
        aria = ad.get("aria-label") or ""
        role = ad.get("role") or ""
        control = (
            ad.get("data-control-name")
            or ad.get("data-tracking-control-name")
            or ""
        ).lower()
        interesting = (
            tag_l in ("a", "button")
            or role == "button"
            or "apply" in control
            or bool(href)
        )
        if not interesting:
            return
        node = {
            "tag": tag_l,
            "href": href,
            "aria": aria,
            "data_blob": data_blob,
            "control": control,
            "text_parts": [],
            "depth": len(self._stack),
        }
        self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag_l = (tag or "").lower()
        # Pop matching open node (best-effort for malformed fixture HTML).
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i]["tag"] == tag_l:
                node = self._stack.pop(i)
                self._finish_node(node)
                return

    def handle_data(self, data: str) -> None:
        if not self._stack:
            return
        t = (data or "").strip()
        if t:
            self._stack[-1]["text_parts"].append(t)

    def _finish_node(self, node: dict) -> None:
        label = re.sub(
            r"\s+",
            " ",
            " ".join(node["text_parts"] + ([node["aria"]] if node["aria"] else [])),
        ).strip()[:120]
        low = label.lower()
        control = node["control"]
        looks_apply = (
            "apply" in low
            or "application" in low
            or "apply" in control
            or "company website" in low
            or "company site" in low
        )
        if not looks_apply:
            return
        hrefs: list[str] = []
        if node["href"]:
            hrefs.append(node["href"])
        hrefs.extend(http_urls_in_text(node["data_blob"]))
        if not hrefs and not node["data_blob"]:
            return
        # One candidate per finished node; prefer primary href then data blob URLs.
        primary = hrefs[0] if hrefs else ""
        self.candidates.append(
            {
                "label": label or "Apply",
                "href": primary,
                "attrs_blob": node["data_blob"],
                "source": "html",
            }
        )
        for extra in hrefs[1:]:
            self.candidates.append(
                {
                    "label": label or "Apply",
                    "href": extra,
                    "attrs_blob": "",
                    "source": "html_data",
                }
            )


def extract_apply_href_candidates_from_html(html: str | None) -> list[dict]:
    """Parse HTML fixtures / page markup for Apply control URL candidates."""
    blob = html or ""
    parser = _ApplyHrefHTMLParser()
    try:
        parser.feed(blob)
        parser.close()
    except Exception:
        pass
    out = list(parser.candidates)
    # Also fold embedded JSON apply URLs as synthetic candidates.
    for u in extract_embedded_apply_urls(blob):
        out.append(
            {
                "label": "embedded_apply_url",
                "href": u,
                "attrs_blob": "",
                "source": "embedded_json",
            }
        )
    # Dedupe by href+label
    seen: set[str] = set()
    deduped: list[dict] = []
    for c in out:
        key = f"{(c.get('href') or '').lower()}|{(c.get('label') or '').lower()}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def offsite_apply_url_from_snapshot(
    *,
    apply_href_candidates: list[dict] | list[str] | None = None,
    page_html: str | None = None,
) -> str | None:
    """Prefer an offsite apply URL from control hrefs / page markup (no click)."""
    picked = pick_offsite_apply_href(apply_href_candidates)
    if picked:
        return picked
    if page_html:
        return pick_offsite_apply_href(extract_apply_href_candidates_from_html(page_html))
    return None


def _http_page_text_snippet(html: str | None) -> str:
    """Short text for authwall/captcha checks — avoid script false positives."""
    blob = html or ""
    # Title + first chunk of visible-ish body text (strip scripts/styles).
    title_m = re.search(r"<title[^>]*>([^<]+)</title>", blob, re.I)
    title = (title_m.group(1) if title_m else "").strip()
    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", blob)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return f"{title}\n{cleaned[:2500]}"


def _apply_labels_from_html(html: str | None) -> list[str]:
    labels: list[str] = []
    for c in extract_apply_href_candidates_from_html(html):
        lab = str(c.get("label") or "").strip()
        if lab:
            labels.append(lab[:120])
    # Easy Apply buttons often lack href — mine plain text.
    for m in re.finditer(
        r"(?is)>(\s*Easy\s+Apply\s*)<",
        html or "",
    ):
        labels.append("Easy Apply")
    seen: set[str] = set()
    out: list[str] = []
    for lab in labels:
        key = lab.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(lab)
    return out


def decide_from_http_html(
    *,
    job_url: str,
    html: str,
    final_url: str | None = None,
) -> dict:
    """Decide from cookie-authenticated HTTP HTML (no browser)."""
    cur = (final_url or job_url or "").strip()
    text = _http_page_text_snippet(html)
    labels = _apply_labels_from_html(html)
    decision = decide_from_snapshot(
        job_url=job_url,
        current_url=cur,
        page_text=text,
        apply_labels=labels,
        profile_ready=True,
        apply_href_candidates=extract_apply_href_candidates_from_html(html),
        page_html=html,
    )
    # HTTP path never clicks — collapse click actions into terminal reasons.
    if decision.get("action") == "click_external_apply":
        if any(classify_apply_control(x) == "easy_apply" for x in labels):
            decision = {
                "confidence": "low",
                "url": None,
                "reason": "easy_apply",
                "message": "Easy Apply only (stays on LinkedIn) — not automating apply.",
                "captcha": False,
                "method": "linkedin_http",
                "score": 0.0,
            }
        else:
            decision = {
                "confidence": "low",
                "url": None,
                "reason": "no_external_apply",
                "message": "Apply control needs click — HTTP path found no offsite href.",
                "captcha": False,
                "method": "linkedin_http",
                "score": 0.0,
                "needs_browser": True,
            }
    decision.pop("action", None)
    decision.pop("click_label", None)
    decision["method"] = "linkedin_http"
    # Posted dates from the same HTML (exact beats approx; persist merges safely).
    try:
        from posted_date import extract_date_posted

        exact, approx = extract_date_posted(html)
        if exact:
            decision["date_posted"] = exact
            decision["date_posted_source"] = "linkedin_http"
        elif approx:
            decision["date_posted_fallback"] = approx
            decision["date_posted_source"] = "linkedin_http"
    except Exception:
        pass
    return decision


def http_fetch_linkedin_job(
    job_url: str,
    *,
    profile_dir: Path | None = None,
    cookies: dict[str, str] | None = None,
    session: Any = None,
    timeout_s: float = DEFAULT_HTTP_TIMEOUT_S,
) -> dict:
    """GET LinkedIn job HTML with profile cookies. Never logs cookie values.

    Pass a shared ``requests.Session`` (from ``build_linkedin_http_session``)
    for parallel batch resolve — cookies loaded once.
    """
    import requests

    profile = Path(profile_dir) if profile_dir is not None else linkedin_resolve_profile_dir()
    jar = cookies if cookies is not None else load_linkedin_cookies(profile)
    if not jar.get("li_at"):
        return {
            "ok": False,
            "html": "",
            "final_url": "",
            "status_code": 0,
            "error": "no_li_at_cookie",
            "authwall": True,
        }
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "csrf-token": str(jar.get("JSESSIONID") or "").strip('"'),
    }
    try:
        if session is not None:
            resp = session.get(
                job_url,
                headers=headers,
                timeout=timeout_s,
                allow_redirects=True,
            )
        else:
            resp = requests.get(
                job_url,
                headers=headers,
                cookies=jar,
                timeout=timeout_s,
                allow_redirects=True,
            )
    except requests.RequestException as e:
        return {
            "ok": False,
            "html": "",
            "final_url": "",
            "status_code": 0,
            "error": str(e)[:300],
            "authwall": False,
        }
    html = resp.text or ""
    final = str(resp.url or job_url)
    text = _http_page_text_snippet(html)
    authwall = page_looks_like_signin_wall(text, final)
    return {
        "ok": resp.status_code == 200 and not authwall,
        "html": html,
        "final_url": final,
        "status_code": int(resp.status_code),
        "authwall": authwall,
        "error": None if resp.status_code == 200 else f"http_{resp.status_code}",
    }


def build_linkedin_http_session(
    profile_dir: Path | None = None,
    *,
    cookies: dict[str, str] | None = None,
) -> tuple[Any, dict[str, str]]:
    """Build a shared ``requests.Session`` with LinkedIn cookies (values never logged)."""
    import requests

    profile = Path(profile_dir) if profile_dir is not None else linkedin_resolve_profile_dir()
    jar = dict(cookies) if cookies is not None else load_linkedin_cookies(profile)
    sess = requests.Session()
    sess.headers.update(
        {
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    if jar.get("JSESSIONID"):
        sess.headers["csrf-token"] = str(jar["JSESSIONID"]).strip('"')
    for name, value in jar.items():
        # Host-scoped loosely — LinkedIn accepts .linkedin.com cookies on www.
        sess.cookies.set(name, value, domain=".linkedin.com", path="/")
    return sess, jar


def resolve_via_http(
    job_url: str,
    *,
    profile_dir: Path | None = None,
    cookies: dict[str, str] | None = None,
    session: Any = None,
    timeout_s: float = DEFAULT_HTTP_TIMEOUT_S,
) -> dict:
    """HTTP-only resolve (no CDP). Returns decision + ``needs_cdp`` hint."""
    fetched = http_fetch_linkedin_job(
        job_url,
        profile_dir=profile_dir,
        cookies=cookies,
        session=session,
        timeout_s=timeout_s,
    )
    if fetched.get("authwall"):
        # Never spin on authwall — tell the user to login; CDP rarely helps.
        no_cookie = fetched.get("error") == "no_li_at_cookie"
        return {
            "confidence": "low",
            "url": None,
            "reason": "not_logged_in" if no_cookie else "authwall",
            "method": "linkedin_http",
            "score": 0.0,
            "needs_cdp": False if no_cookie else True,
            "message": login_required_message(),
            "captcha": False,
        }
    if not fetched.get("ok") or not fetched.get("html"):
        return {
            "confidence": "low",
            "url": None,
            "reason": "http_error",
            "method": "linkedin_http",
            "score": 0.0,
            "needs_cdp": True,
            "message": str(fetched.get("error") or "http_fetch_failed")[:300],
            "captcha": False,
        }
    decision = decide_from_http_html(
        job_url=job_url,
        html=str(fetched.get("html") or ""),
        final_url=str(fetched.get("final_url") or job_url),
    )
    needs_cdp = bool(decision.get("needs_browser"))
    if decision.get("confidence") == "high" and decision.get("url"):
        needs_cdp = False
    if decision.get("reason") in ("easy_apply", "blocked_captcha"):
        needs_cdp = False
    if decision.get("reason") == "no_external_apply" and not decision.get("needs_browser"):
        needs_cdp = False
    decision["needs_cdp"] = needs_cdp
    decision.pop("needs_browser", None)
    return decision


def resolve_linkedin_http_many(
    job_urls: list[tuple[str, str]],
    *,
    profile_dir: Path | None = None,
    concurrency: int | None = None,
    timeout_s: float = DEFAULT_HTTP_TIMEOUT_S,
) -> list[dict]:
    """Parallel HTTP resolve for ``[(job_id, linkedin_url), ...]``.

    Shared cookie Session. No Playwright. Concurrency from
    ``LINKEDIN_HTTP_CONCURRENCY`` (default 20, max 40).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    profile = Path(profile_dir) if profile_dir is not None else linkedin_resolve_profile_dir()
    workers = clamp_http_concurrency(
        concurrency if concurrency is not None else http_concurrency_from_env()
    )
    sess, cookies = build_linkedin_http_session(profile)
    if not cookies.get("li_at"):
        return [
            {
                "id": jid,
                "confidence": "low",
                "url": None,
                "reason": "not_logged_in",
                "method": "linkedin_http",
                "needs_cdp": False,
                "score": 0.0,
            }
            for jid, _u in job_urls
        ]

    out_by_id: dict[str, dict] = {}

    def _one(pair: tuple[str, str]) -> dict:
        jid, url = pair
        try:
            dec = resolve_via_http(
                url,
                profile_dir=profile,
                cookies=cookies,
                session=sess,
                timeout_s=timeout_s,
            )
        except Exception as e:
            dec = {
                "confidence": "low",
                "url": None,
                "reason": "exception",
                "message": str(e)[:300],
                "method": "linkedin_http",
                "needs_cdp": True,
                "score": 0.0,
            }
        dec = dict(dec)
        dec["id"] = jid
        return dec

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, pair): pair[0] for pair in job_urls}
        for fut in as_completed(futs):
            jid = futs[fut]
            try:
                out_by_id[jid] = fut.result()
            except Exception as e:
                out_by_id[jid] = {
                    "id": jid,
                    "confidence": "low",
                    "url": None,
                    "reason": "exception",
                    "message": str(e)[:300],
                    "method": "linkedin_http",
                    "needs_cdp": True,
                    "score": 0.0,
                }
    try:
        sess.close()
    except Exception:
        pass
    return [out_by_id[jid] for jid, _u in job_urls if jid in out_by_id]


def decide_from_snapshot(
    *,
    job_url: str,
    current_url: str,
    page_text: str,
    apply_labels: list[str],
    profile_ready: bool,
    apply_href_candidates: list[dict] | list[str] | None = None,
    page_html: str | None = None,
) -> dict:
    """Pure decision from a page snapshot. May request a click via ``action``."""
    if not profile_ready:
        return {
            "confidence": "low",
            "url": None,
            "reason": "not_logged_in",
            "message": login_required_message(),
            "captcha": False,
            "method": "linkedin_session",
            "score": 0.0,
        }

    cur = (current_url or "").strip()
    text = page_text or ""

    if page_looks_like_captcha(text):
        return {
            "confidence": "low",
            "url": None,
            "reason": "blocked_captcha",
            "captcha": True,
            "message": "CAPTCHA / bot check on LinkedIn — stopped (never solve).",
            "method": "linkedin_session",
            "score": 0.0,
        }

    if page_looks_like_signin_wall(text, cur):
        return {
            "confidence": "low",
            "url": None,
            "reason": "not_logged_in",
            "message": login_required_message(),
            "captcha": False,
            "method": "linkedin_session",
            "score": 0.0,
        }

    if is_unfetchable_ats(cur):
        return {
            "confidence": "low",
            "url": None,
            "reason": "unfetchable_ats",
            "message": "Landed on Workday/iCIMS — left unresolved (Akamai policy).",
            "captcha": False,
            "method": "linkedin_session",
            "score": 0.0,
        }

    if is_acceptable_offsite_apply(cur):
        return {
            "confidence": "high",
            "url": normalize_url(cur) or cur,
            "reason": "linkedin_external_redirect",
            "captcha": False,
            "method": "linkedin_session",
            "score": 1.0,
        }

    # Prefer copying Apply href / data-* destination — no click needed.
    href_hit = offsite_apply_url_from_snapshot(
        apply_href_candidates=apply_href_candidates,
        page_html=page_html,
    )
    if href_hit:
        return {
            "confidence": "high",
            "url": href_hit,
            "reason": "linkedin_apply_href",
            "captcha": False,
            "method": "linkedin_session",
            "score": 1.0,
        }

    labels = [str(x) for x in (apply_labels or []) if str(x).strip()]
    kinds = [(lab, classify_apply_control(lab)) for lab in labels]
    easy = [lab for lab, k in kinds if k == "easy_apply"]
    external = [lab for lab, k in kinds if k == "external"]
    ambiguous = [lab for lab, k in kinds if k == "external_or_unknown"]

    if external:
        return {
            "action": "click_external_apply",
            "click_label": external[0],
            "confidence": "low",
            "url": None,
            "reason": "needs_click",
            "method": "linkedin_session",
            "score": 0.0,
        }

    if ambiguous and not easy:
        return {
            "action": "click_external_apply",
            "click_label": ambiguous[0],
            "confidence": "low",
            "url": None,
            "reason": "needs_click",
            "method": "linkedin_session",
            "score": 0.0,
        }

    if easy and not external and not ambiguous:
        return {
            "confidence": "low",
            "url": None,
            "reason": "easy_apply",
            "message": "Easy Apply only (stays on LinkedIn) — not automating apply.",
            "captcha": False,
            "method": "linkedin_session",
            "score": 0.0,
        }

    if easy and ambiguous:
        # Prefer trying the non-Easy control first.
        return {
            "action": "click_external_apply",
            "click_label": ambiguous[0],
            "confidence": "low",
            "url": None,
            "reason": "needs_click",
            "method": "linkedin_session",
            "score": 0.0,
        }

    return {
        "confidence": "low",
        "url": None,
        "reason": "no_external_apply",
        "message": "No offsite Apply control found on LinkedIn job page.",
        "captcha": False,
        "method": "linkedin_session",
        "score": 0.0,
    }


def _collect_apply_labels_from_page(page: Any) -> list[str]:
    labels: list[str] = []
    try:
        buttons = page.get_by_role("button").all()
        for btn in buttons[:40]:
            try:
                t = (btn.inner_text(timeout=500) or "").strip()
            except Exception:
                continue
            if not t:
                continue
            low = t.lower()
            if "apply" in low or "application" in low:
                labels.append(re.sub(r"\s+", " ", t)[:120])
    except Exception:
        pass
    try:
        links = page.get_by_role("link").all()
        for link in links[:40]:
            try:
                t = (link.inner_text(timeout=500) or "").strip()
            except Exception:
                continue
            if not t:
                continue
            low = t.lower()
            if "apply" in low:
                labels.append(re.sub(r"\s+", " ", t)[:120])
    except Exception:
        pass
    # Dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for lab in labels:
        key = lab.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(lab)
    return out


_COLLECT_APPLY_HREFS_JS = """() => {
  const out = [];
  const seen = new Set();
  const push = (label, href, source, attrsBlob) => {
    const h = (href || '').trim();
    const lab = (label || '').trim().slice(0, 120);
    const key = (h + '|' + lab + '|' + source).toLowerCase();
    if (!h && !(attrsBlob || '').trim()) return;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({
      label: lab || 'Apply',
      href: h,
      source: source || 'dom',
      attrs_blob: (attrsBlob || '').slice(0, 500),
    });
  };
  const attrs = [
    'href', 'data-url', 'data-href', 'data-destination-url',
    'data-apply-url', 'data-external-url',
    'data-control-name', 'data-tracking-control-name',
  ];
  const looksApply = (el) => {
    const t = (
      (el.innerText || el.textContent || '') + ' ' +
      (el.getAttribute('aria-label') || '') + ' ' +
      (el.getAttribute('data-control-name') || '') + ' ' +
      (el.getAttribute('data-tracking-control-name') || '')
    ).toLowerCase();
    return t.includes('apply') || t.includes('application') ||
      t.includes('company website') || t.includes('company site');
  };
  const nodes = document.querySelectorAll(
    'a, button, [role="button"], [data-control-name*="apply" i], [data-tracking-control-name*="apply" i]'
  );
  for (const el of nodes) {
    if (!looksApply(el)) continue;
    const label = (
      (el.innerText || el.textContent || '').trim() ||
      (el.getAttribute('aria-label') || '')
    ).slice(0, 120);
    const blobParts = [];
    for (const a of attrs) {
      const v = el.getAttribute(a);
      if (!v) continue;
      blobParts.push(v);
      if (a === 'href' || /^https?:\\/\\//i.test(v) || v.includes('://')) {
        push(label, v, a, '');
      }
    }
    const nested = el.querySelector && el.querySelector('a[href]');
    if (nested) push(label, nested.getAttribute('href') || '', 'nested_a', '');
    const parentA = el.closest && el.closest('a[href]');
    if (parentA && parentA !== el) {
      push(label, parentA.getAttribute('href') || '', 'parent_a', '');
    }
    if (blobParts.length) {
      push(label, '', 'data_attrs', blobParts.join(' '));
    }
    if (out.length >= 60) break;
  }
  for (const a of document.querySelectorAll('a[href]')) {
    const t = ((a.innerText || a.textContent || '') + ' ' +
      (a.getAttribute('aria-label') || '')).toLowerCase();
    if (
      t.includes('company website') ||
      t.includes('company site') ||
      t.includes('apply on') ||
      t.includes('continue to application')
    ) {
      push(
        (a.innerText || a.textContent || '').trim().slice(0, 120),
        a.getAttribute('href') || '',
        'nearby_apply_link',
        ''
      );
    }
    if (out.length >= 80) break;
  }
  return out;
}"""


def _collect_apply_href_candidates_from_page(page: Any) -> list[dict]:
    """Read Apply control href / data-* URLs from the live DOM (no click)."""
    try:
        raw = page.evaluate(_COLLECT_APPLY_HREFS_JS)
    except Exception:
        raw = None
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "label": str(item.get("label") or "Apply")[:120],
                "href": str(item.get("href") or ""),
                "source": str(item.get("source") or "dom"),
                "attrs_blob": str(item.get("attrs_blob") or "")[:500],
            }
        )
    return out


def _page_html(page: Any) -> str:
    try:
        return (page.content() or "")[:200_000]
    except Exception:
        return ""


def _page_text(page: Any) -> str:
    try:
        return (page.inner_text("body", timeout=3000) or "")[:8000]
    except Exception:
        try:
            return (page.content() or "")[:8000]
        except Exception:
            return ""


def _click_label(page: Any, label: str) -> bool:
    """Click an Apply-like control. Never clicks Submit."""
    low = (label or "").lower()
    if "submit" in low and "apply" not in low:
        return False
    # Prefer exact / contains role match
    for role in ("button", "link"):
        try:
            loc = page.get_by_role(role, name=re.compile(re.escape(label), re.I))
            if loc.count() > 0:
                loc.first.click(timeout=5000)
                return True
        except Exception:
            continue
    try:
        loc = page.locator(
            f"button:has-text({label!r}), a:has-text({label!r})"
        )
        if loc.count() > 0:
            loc.first.click(timeout=5000)
            return True
    except Exception:
        pass
    return False


def _close_resolve_tabs(context: Any, pages_before: list | None) -> None:
    """Close tabs opened during one resolve; never ``browser.close()`` / CDP kill.

    ``pages_before`` is the context.pages snapshot taken *before* opening the
    job tab. Any page not in that set (owned job tab + click-spawned offsite
    tabs) is closed. Pre-existing feed / login / blank tabs are left alone.
    """
    if context is None:
        return
    before = list(pages_before or [])
    try:
        current = list(context.pages)
    except Exception:
        return
    for p in current:
        if p in before:
            continue
        try:
            p.close()
        except Exception:
            pass


def _pages_in_browser(browser: Any) -> list:
    pages: list = []
    try:
        for ctx in browser.contexts or []:
            pages.extend(list(ctx.pages))
    except Exception:
        pass
    return pages


def _is_keep_tab_url(url: str | None) -> bool:
    low = (url or "").lower()
    if not low or low.startswith(("about:", "chrome:", "chrome-error:")):
        return True
    if "linkedin.com" not in low:
        return False
    return any(
        x in low
        for x in (
            "/feed",
            "/login",
            "/checkpoint/",
            "/uas/login",
            "authwall",
            "/mynetwork",
            "/messaging",
            "/notifications",
        )
    ) or low.rstrip("/") in (
        "https://www.linkedin.com",
        "https://linkedin.com",
        "http://www.linkedin.com",
        "http://linkedin.com",
    )


def _linkedin_job_orphan_pages(pages: list) -> list:
    orphans: list = []
    for p in pages:
        try:
            url = getattr(p, "url", "") or ""
        except Exception:
            continue
        if _is_keep_tab_url(url):
            continue
        low = url.lower()
        if "/jobs/view/" in low or "/jobs/collections/" in low or "/job/" in low:
            orphans.append(p)
    return orphans


def _close_orphan_linkedin_job_tabs(context: Any, *, keep_at_most: int = 1) -> int:
    """Close leftover LinkedIn job tabs; keep feed/home. Returns closed count."""
    if context is None:
        return 0
    try:
        pages = list(context.pages)
    except Exception:
        return 0
    orphans = _linkedin_job_orphan_pages(pages)
    # Keep newest keep_at_most orphans (usually 0 before open).
    to_close = orphans[:-keep_at_most] if keep_at_most > 0 else orphans
    closed = 0
    for p in to_close:
        try:
            p.close()
            closed += 1
        except Exception:
            pass
    return closed


def _open_pages_count(browser: Any) -> int:
    return len(_pages_in_browser(browser))


def _open_job_page_background(
    browser: Any,
    context: Any,
    job_url: str,
    *,
    timeout_ms: int = NAV_TIMEOUT_MS,
) -> Any:
    """Open *job_url* in a background CDP tab (no OS focus steal).

    Uses ``Target.createTarget`` with ``background: true`` (PartyRock pattern).
    Falls back to ``context.new_page()`` + goto if CDP create fails — still never
    activates CfT via AppleScript / raise-window helpers.
    """
    import time

    pages_before = _pages_in_browser(browser)
    try:
        session = browser.new_browser_cdp_session()
        result = session.send(
            "Target.createTarget",
            {"url": job_url, "background": True},
        )
        target_id = str((result or {}).get("targetId") or "").strip()
        if not target_id:
            raise RuntimeError("Target.createTarget returned no targetId")
        deadline = time.monotonic() + max(2.0, min(timeout_ms / 1000.0, 30.0))
        while time.monotonic() < deadline:
            for pg in _pages_in_browser(browser):
                if pg not in pages_before:
                    return pg
            time.sleep(0.05)
        raise RuntimeError("background target did not appear in Playwright")
    except Exception:
        # Last resort: may briefly raise CfT — still no Activate/osascript.
        page = context.new_page()
        page.goto(job_url, wait_until="domcontentloaded", timeout=timeout_ms)
        return page


def playwright_session_fn(
    job_url: str,
    profile_dir: Path,
    *,
    click_label: str | None = None,
    headless: bool = False,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict:
    """Open LinkedIn job via CDP attach to the long-lived resolve browser.

    PartyRock pattern: ``ensure_linkedin_resolve_browser`` then
    ``connect_over_cdp`` — never one-shot Playwright persistent relaunch.
    Always headed. ``headless=True`` is ignored. Never close the CDP browser
    process (disconnect only; close per-job tabs in ``finally``).
    Opens the job tab in the **background** (no dashboard focus steal).
    Hard-capped by ``LINKEDIN_RESOLVE_CONCURRENCY`` (default 2, max 3).
    """
    # LinkedIn session resolve must never run headless.
    headless = False
    global _owned_cdp_tabs

    profile_dir = Path(profile_dir)
    # Resolve path: never steal_focus (dashboard stays frontmost).
    ensured = ensure_linkedin_resolve_browser(profile=profile_dir, steal_focus=False)
    if not ensured.get("ok"):
        reason = "profile_in_use" if ensured.get("already_open_no_cdp") else "browser_error"
        return {
            "current_url": "",
            "page_text": "",
            "apply_labels": [],
            "error": str(ensured.get("error") or "LinkedIn CDP failed"),
            "reason": reason,
        }

    from playwright.sync_api import sync_playwright

    conc = resolve_concurrency_from_env()
    sem = _cdp_semaphore()
    acquired = sem.acquire(blocking=True, timeout=120)
    if not acquired:
        return {
            "current_url": "",
            "page_text": "",
            "apply_labels": [],
            "error": "CDP tab semaphore timeout",
            "reason": "browser_error",
        }

    cdp_url = linkedin_resolve_cdp_ws_endpoint()
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            sem.release()
            return {
                "current_url": "",
                "page_text": "",
                "apply_labels": [],
                "error": f"CDP connect failed: {e}"[:300],
                "reason": "browser_error",
            }
        context = None
        pages_before: list = []
        owned_page = None
        page = None
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            # Before opening: close orphan LinkedIn job tabs (keep feed/home).
            _close_orphan_linkedin_job_tabs(context, keep_at_most=1)
            open_n = _open_pages_count(browser)
            if open_n > conc + 2:
                _close_orphan_linkedin_job_tabs(context, keep_at_most=0)
                open_n = _open_pages_count(browser)
                if open_n > conc + 2:
                    return {
                        "current_url": "",
                        "page_text": "",
                        "apply_labels": [],
                        "error": (
                            f"Too many open pages ({open_n} > concurrency+2="
                            f"{conc + 2}); refused to open another resolve tab"
                        ),
                        "reason": "browser_error",
                    }
            # Snapshot pre-existing tabs so finally only closes what we opened
            # (job tab + any click-spawned offsite tabs). Never Browser.close on CDP.
            pages_before = list(context.pages)
            with _cdp_tab_lock:
                _owned_cdp_tabs += 1
            owned_page = _open_job_page_background(
                browser, context, job_url, timeout_ms=NAV_TIMEOUT_MS
            )
            page = owned_page
            # Keep OmniDex / dashboard focused — do not raise the CfT window.
            page.set_default_timeout(min(timeout_ms, NAV_TIMEOUT_MS))
            # Fast path fallback: domcontentloaded only (skip full network idle).
            try:
                page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass

            if click_label:
                labels = _collect_apply_labels_from_page(page)
                if not _click_label(page, click_label):
                    for lab in labels:
                        if classify_apply_control(lab) in (
                            "external",
                            "external_or_unknown",
                        ):
                            if _click_label(page, lab):
                                break
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                # Prefer a newly spawned tab for the offsite URL, but keep
                # owned_page tracked for cleanup (do not orphan the job tab).
                try:
                    spawned = [
                        p for p in context.pages
                        if p not in pages_before and p is not owned_page
                    ]
                    if spawned:
                        page = spawned[-1]
                except Exception:
                    pass
                for _ in range(15):
                    cur = page.url or ""
                    if is_acceptable_offsite_apply(cur) or is_unfetchable_ats(cur):
                        break
                    if not is_linkedin_job_url(cur) and "linkedin.com" not in _host(cur):
                        break
                    page.wait_for_timeout(400)

            cur = page.url or ""
            text = _page_text(page)
            labels = _collect_apply_labels_from_page(page)
            hrefs: list[dict] = []
            html_blob = ""
            # Href scrape is most useful before a click (still on LinkedIn).
            if not click_label and "linkedin.com" in _host(cur):
                hrefs = _collect_apply_href_candidates_from_page(page)
                html_blob = _page_html(page)
            return {
                "current_url": cur,
                "page_text": text,
                "apply_labels": labels,
                "apply_href_candidates": hrefs,
                "page_html": html_blob,
            }
        finally:
            # Close every tab this resolve opened (owned job tab + click-spawned
            # tabs). Leave pre-existing feed/login/blank tabs and the CDP browser.
            try:
                _close_resolve_tabs(context, pages_before)
            finally:
                with _cdp_tab_lock:
                    _owned_cdp_tabs = max(0, _owned_cdp_tabs - 1)
                sem.release()


def resolve_linkedin_apply_url(
    job_url: str,
    *,
    profile_dir: Path | None = None,
    profile_ready: bool | None = None,
    session_fn: SessionFn | None = None,
    page: Any = None,
    headless: bool = False,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    prefer_http: bool = True,
    allow_cdp: bool | None = None,
    http_session: Any = None,
    http_cookies: dict[str, str] | None = None,
) -> dict:
    """Resolve LinkedIn job → offsite ATS/company apply URL.

    Fast path: cookie DB + HTTP GET + HTML/JSON parse (no tab). CDP/Playwright
    only when ``allow_cdp`` is true (default: ``LINKEDIN_ALLOW_CDP=1`` only)
    and HTTP authwall / needs_cdp / transport failure. ``session_fn``
    injectable for tests (still runs when injected even if CDP is off).
    """
    # LinkedIn session resolve must never run headless (sign-in wall / cookie wipe).
    headless = False
    if allow_cdp is None:
        allow_cdp = linkedin_allow_cdp_from_env()

    profile = Path(profile_dir) if profile_dir is not None else linkedin_resolve_profile_dir()
    # Require li_at — initialized-but-logged-out still hits authwall.
    ready = bool(profile_ready) if profile_ready is not None else profile_has_li_at(profile)

    if not is_linkedin_job_url(job_url):
        return {
            "confidence": "low",
            "url": None,
            "reason": "not_linkedin",
            "method": "linkedin_session",
            "score": 0.0,
        }

    if not ready:
        return decide_from_snapshot(
            job_url=job_url,
            current_url="",
            page_text="",
            apply_labels=[],
            profile_ready=False,
        )

    # --- Cookie HTTP fast path (no Playwright / no ensure CDP) ---
    if prefer_http and page is None:
        http_decision = resolve_via_http(
            job_url,
            profile_dir=profile,
            cookies=http_cookies,
            session=http_session,
        )
        # Tests patch http_fetch_linkedin_job; resolve_via_http uses it.
        if http_decision.get("confidence") == "high" and http_decision.get("url"):
            http_decision.pop("needs_cdp", None)
            return http_decision
        reason = str(http_decision.get("reason") or "")
        if reason in ("easy_apply", "blocked_captcha"):
            http_decision.pop("needs_cdp", None)
            return http_decision
        if reason == "no_external_apply" and not http_decision.get("needs_cdp"):
            http_decision.pop("needs_cdp", None)
            return http_decision
        if reason == "not_logged_in" and http_decision.get("message") == "no_li_at_cookie":
            if session_fn is None:
                http_decision.pop("needs_cdp", None)
                return decide_from_snapshot(
                    job_url=job_url,
                    current_url="",
                    page_text="",
                    apply_labels=[],
                    profile_ready=False,
                )
            # Injected session_fn (tests) — fall through despite empty cookie DB.
        # Terminal without browser: Easy Apply / clear no_external already returned.
        # authwall / needs_cdp / http_error → CDP only when explicitly allowed.
        if not allow_cdp and session_fn is None:
            http_decision.pop("needs_cdp", None)
            if reason in ("authwall", "not_logged_in"):
                http_decision["reason"] = "not_logged_in"
                http_decision["message"] = login_required_message()
            return http_decision
        if not allow_cdp and session_fn is not None:
            # Injected session_fn still runs (unit tests).
            pass
        elif not http_decision.get("needs_cdp") and reason not in (
            "authwall",
            "http_error",
            "not_logged_in",
        ):
            http_decision.pop("needs_cdp", None)
            return http_decision
        # else fall through to CDP / session_fn

    def _run(click_label: str | None = None) -> dict:
        if session_fn is not None:
            try:
                return session_fn(job_url, profile, click_label=click_label)
            except TypeError:
                # Older test stubs without click_label kw
                if click_label is None:
                    return session_fn(job_url, profile)
                return session_fn(job_url, profile, click_label=click_label)
        if page is not None:
            # Legacy/simple page object: read snapshot; click when asked
            if click_label:
                _click_label(page, click_label)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
            else:
                try:
                    page.goto(job_url, wait_until="domcontentloaded")
                except Exception:
                    pass
            return {
                "current_url": getattr(page, "url", "") or "",
                "page_text": _page_text(page),
                "apply_labels": _collect_apply_labels_from_page(page),
                "apply_href_candidates": (
                    _collect_apply_href_candidates_from_page(page)
                    if not click_label
                    else []
                ),
                "page_html": _page_html(page) if not click_label else "",
            }
        if not allow_cdp:
            return {
                "current_url": "",
                "page_text": "",
                "apply_labels": [],
                "error": "CDP fallback disabled (http-only)",
                "reason": "browser_error",
            }
        return playwright_session_fn(
            job_url,
            profile,
            click_label=click_label,
            headless=False,
            timeout_ms=timeout_ms,
        )

    snap = _run(None)
    if snap.get("error") and not snap.get("current_url"):
        reason = str(snap.get("reason") or "browser_error")
        if reason not in ("profile_in_use", "browser_error"):
            reason = "browser_error"
        err_l = str(snap.get("error") or "").lower()
        if (
            "close login window first" in err_l
            or "without cdp" in err_l
            or "open_linkedin_resolve" in err_l
        ):
            reason = "profile_in_use"
        return {
            "confidence": "low",
            "url": None,
            "reason": reason,
            "message": str(snap.get("error")),
            "method": "linkedin_session",
            "score": 0.0,
        }

    decision = decide_from_snapshot(
        job_url=job_url,
        current_url=str(snap.get("current_url") or ""),
        page_text=str(snap.get("page_text") or ""),
        apply_labels=list(snap.get("apply_labels") or []),
        profile_ready=True,
        apply_href_candidates=list(snap.get("apply_href_candidates") or []),
        page_html=str(snap.get("page_html") or "") or None,
    )
    if decision.get("action") == "click_external_apply":
        label = str(decision.get("click_label") or "")
        snap2 = _run(label)
        decision = decide_from_snapshot(
            job_url=job_url,
            current_url=str(snap2.get("current_url") or ""),
            page_text=str(snap2.get("page_text") or ""),
            apply_labels=list(snap2.get("apply_labels") or []),
            profile_ready=True,
            apply_href_candidates=list(snap2.get("apply_href_candidates") or []),
            page_html=str(snap2.get("page_html") or "") or None,
        )
        # If still on LinkedIn after click and Easy Apply visible → easy_apply
        if decision.get("action") == "click_external_apply" or (
            decision.get("reason") == "no_external_apply"
            and is_linkedin_job_url(str(snap2.get("current_url") or ""))
        ):
            labels = list(snap2.get("apply_labels") or snap.get("apply_labels") or [])
            if any(classify_apply_control(x) == "easy_apply" for x in labels):
                return {
                    "confidence": "low",
                    "url": None,
                    "reason": "easy_apply",
                    "message": "Easy Apply only (stays on LinkedIn) — not automating apply.",
                    "captcha": False,
                    "method": "linkedin_session",
                    "score": 0.0,
                }
            return {
                "confidence": "low",
                "url": None,
                "reason": "no_external_apply",
                "message": "Apply click did not leave LinkedIn.",
                "method": "linkedin_session",
                "score": 0.0,
            }

    decision.pop("action", None)
    decision.pop("click_label", None)
    return decision


def job_linkedin_url(job: dict) -> str | None:
    for key in ("apply_url", "job_url", "source_url"):
        u = str((job or {}).get(key) or "").strip()
        if is_linkedin_job_url(u):
            return u
    for u in (job or {}).get("alternate_urls") or []:
        if is_linkedin_job_url(str(u)):
            return str(u).strip()
    return None
