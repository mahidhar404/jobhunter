#!/usr/bin/env python3
"""Resolve aggregator (LinkedIn/Indeed/…) apply URLs to the company ATS page.

When a job's apply_url is LinkedIn (signin wall / Easy Apply hide the offsite
link):
  1. Opt-in authenticated path: if ``linkedin_resolve_profile`` has a login,
     open the LinkedIn job URL in Chrome-for-Testing with that profile, follow
     offsite Apply redirect, and capture company/ATS ``apply_url`` (never Easy
     Apply submit, never CAPTCHA solve). See ``linkedin_resolve_apply.py``.
  2. Else (or on miss): search the public web for the same posting and upgrade
     apply_url only at high confidence (title + company alias + distinctive JD
     overlap, or company-host job URL). LinkedIn is kept on job_url /
     source_url / alternate_urls.

Discovery / prune order for unresolved aggregators:
  LinkedIn HTTP href (if LinkedIn) → public company+title search → only then
  stamp failed/no_external and Unresolved URL prune. Company careers pages
  (e.g. coinbase.com/careers/positions/…?gh_jid=) count as resolved targets.

Public search still never scrapes authenticated LinkedIn for discovery.
Authenticated LinkedIn is only for apply-URL redirect capture via the dedicated
profile.

Never: submit applications, solve CAPTCHA, bypass Workday/iCIMS/Akamai, or use
applicant PII.

Resolve path for public company+title recovery (fail soft):
  0. Direct ATS board APIs (Ashby/Greenhouse/Lever/…) using ats_companies.json
     slugs + company-name slug guesses — finds jobs.ashbyhq.com the way a
     Google ``Title Company`` search would, without depending on HTML SERPs
  1. Google CSE if GOOGLE_CSE_KEY / GOOGLE_CSE_KEYS + GOOGLE_CSE_CX are set
     (multiple keys rotate on 403/429/quota)
  2. Brave Search HTML / Bing HTML / DuckDuckGo HTML (no key)
  3. Brave Search API if BRAVE_SEARCH_API_KEY is set
  4. JSearch if JSEARCH_API_KEY is set

Keys may also live in gitignored web_keys.json (Adzuna pattern). Never scrape
google.com HTML.

Usage:
  python3 scripts/resolve_apply_urls.py JOB_ID              # dry-run one
  python3 scripts/resolve_apply_urls.py --all               # dry-run LinkedIn/aggregator jobs
  python3 scripts/resolve_apply_urls.py --all --write       # persist high-confidence upgrades
  python3 scripts/resolve_apply_urls.py JOB_ID --write
  python3 scripts/resolve_apply_urls.py --all --limit 20 --delay 2.5
  python3 scripts/resolve_apply_urls.py --reresolve-deleted --write --limit 0
  # limit 0 = keep draining checkpointed chunks (40/cycle) until backlog empty;
  # or pass --limit 100 for a single bounded batch.

Dry-run is the default. --write records medium-confidence candidates without
overwriting apply_url. Easy Apply / CAPTCHA / profile lock skip public search;
other LinkedIn misses (http_error, no_external, …) always search before prune.
Dashboard backlog auto-retries pruned Unresolved URL rows via public search.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))

from apply_urls import (  # noqa: E402
    enrich_listing_urls,
    is_aggregator_url,
    is_ats_or_company_apply,
    is_known_ats_url,
    normalize_url,
)
from jd_fingerprint import description_text, normalize_jd_text  # noqa: E402
from jobs_lock import locked_jobs_for_read, locked_jobs_for_write  # noqa: E402
from text_normalize import normalize_company, normalize_title  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"
PROGRESS_FILE = LOGS_DIR / "resolve_apply_urls_progress.json"
RERESOLVE_PROGRESS_FILE = LOGS_DIR / "reresolve_unresolved_deleted_progress.json"
REGISTRY_FILE = ROOT / "ats_companies.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Phrase-overlap gate for high confidence. LinkedIn vs ATS text almost never
# hash-equal (jd_fingerprint); this is overlap of distinctive shingles.
HIGH_OVERLAP = 0.20
DEFAULT_DELAY_S = 2.5
CONF_RANK = {"high": 0, "medium": 1, "low": 2}
APPLY_RESOLVE_MSG_MAX = 200

# Machine reason → compact status stamped on the job.
_REASON_STATUS = {
    "easy_apply": "easy_apply",
    "no_external_apply": "no_external",
    "not_needed": "skipped",
    "medium_no_overwrite": "skipped",
    "linkedin_apply_href": "ok",
    "linkedin_external_redirect": "ok",
    "upgraded": "ok",
    "ats_board_api": "ok",
    "public_search": "ok",
}

# Terminal outcomes — discovery auto-resolve skips these unless apply_url is
# still LinkedIn (ok stamped but never upgraded).
TERMINAL_APPLY_RESOLVE_STATUSES = frozenset({"ok", "easy_apply", "no_external"})

# Default HTTP batch size for post-discover LinkedIn resolve (clamped 1–40).
DISCOVERY_RESOLVE_HTTP_CONCURRENCY = 36

# Company careers / job-posting path hints (Greenhouse embeds, /positions/N, …).
_JOB_APPLY_PATH_RE = re.compile(
    r"/(?:jobs?|job-detail|positions?|openings?|careers|opportunit(?:y|ies)|"
    r"requisitions?|apply)(?:/|$)",
    re.I,
)
_GH_JID_RE = re.compile(r"(?:^|&)gh_jid=\d+", re.I)
_NUMERIC_JOB_ID_RE = re.compile(r"/\d{5,}(?:/|$)")
# Ashby / Lever-style UUID job ids in the path.
_ATS_UUID_RE = re.compile(
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:/|$)",
    re.I,
)

# Public board JSON endpoints used when HTML SERPs miss ATS hosts.
_ATS_BOARD_API = {
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "smartrecruiters": (
        "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
    ),
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{slug}",
}
_ATS_BOARD_PROBE_ORDER = (
    "ashby",
    "greenhouse",
    "lever",
    "smartrecruiters",
    "workable",
)
_ATS_BOARD_CACHE: dict[tuple[str, str], list[dict]] = {}
_ATS_BOARD_CACHE_LOCK = threading.Lock()
_ATS_BOARD_MAX_PROBES = 12

# LinkedIn HTTP/session outcomes that are terminal — do not fall back to search.
_LINKEDIN_TERMINAL_NO_SEARCH = frozenset(
    {"easy_apply", "blocked_captcha", "profile_in_use"}
)
# May soft-delete without a public-search attempt (search cannot help).
_PRUNE_OK_WITHOUT_SEARCH_REASONS = frozenset(
    {"easy_apply", "blocked_captcha", "profile_in_use"}
)
# Reasons that imply public company+title search already ran (legacy + current).
_SEARCH_ATTEMPTED_REASONS = frozenset(
    {"no_ats_host", "public_search", "unfetchable_ats"}
)

# Cap for re-resolving pruned unresolved_apply_url rows via public search.
# 0 / None = process the full remaining backlog (checkpointed).
RERESOLVE_DELETED_DEFAULT_LIMIT = 0
RERESOLVE_DELETED_DEFAULT_WORKERS = 2
RERESOLVE_DELETED_CHUNK = 25
_AGGREGATOR_RERESOLVE_PRIORITY = (
    "weworkremotely",
    "rss",
    "indeed",
    "remoteok",
    "remotive",
    "jobicy",
    "builtin",
    "authenticjobs",
)

_DEFAULT_RESOLVE_MESSAGES = {
    "not_logged_in": "Open LinkedIn resolve browser first: ./open_linkedin_resolve.sh",
    "authwall": "Open LinkedIn resolve browser first: ./open_linkedin_resolve.sh",
    "blocked_captcha": "CAPTCHA / bot check on LinkedIn — stopped (never solve).",
    "easy_apply": "Easy Apply only (stays on LinkedIn) — not automating apply.",
    "no_external_apply": "No offsite Apply redirect found on LinkedIn.",
    "no_ats_host": "Search did not find a company/ATS apply URL.",
    "unfetchable_ats": "Landed on Workday/iCIMS — left unresolved.",
    "profile_in_use": "LinkedIn resolve profile is locked — close login window first.",
    "browser_error": "LinkedIn resolve browser error.",
    "http_error": "LinkedIn HTTP fetch failed.",
}

UNFETCHABLE_HOST_HINTS = (
    "myworkdayjobs.com",
    "myworkdaysite.com",
    "icims.com",
)

_STOPWORDS = frozenset(
    """
    a an the and or of to for in on with our you will your we are is be at as
    by from this that those these it its they their them was were been being
    have has had do does did not no nor but if then than so such into over
    also can may must should would could about after before more most other
    into per via using including include includes including job role team
    work working experience years year plus ability able strong join us
    company posted applicants apply application sign login linkedin easy
    description requirements responsibilities what youll you'll you'll
    """.split()
)

_HTTP_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_UDDG_RE = re.compile(r"[?&]uddg=([^&\"'#]+)")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _host(url: str) -> str:
    try:
        host = (urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def is_unfetchable_ats(url) -> bool:
    host = _host(url)
    if not host:
        return False
    return any(h in host for h in UNFETCHABLE_HOST_HINTS)


def looks_like_job_apply_url(url) -> bool:
    """True for known ATS hosts or company career URLs that look like a job post."""
    s = str(url or "").strip()
    if not s:
        return False
    if is_known_ats_url(s):
        return True
    if not is_ats_or_company_apply(s):
        return False
    try:
        p = urlparse(s)
    except ValueError:
        return False
    path = p.path or ""
    query = p.query or ""
    if _JOB_APPLY_PATH_RE.search(path):
        return True
    if _GH_JID_RE.search(query):
        return True
    # Bare /12345/ on a random company host is too loose (matches /questions/514625/).
    # Only accept numeric ids when the path also has a job-ish segment.
    if _NUMERIC_JOB_ID_RE.search(path) and re.search(
        r"/(?:job|jobs|position|positions|opening|openings|career|careers|"
        r"opportunit|requisition|apply|role|roles)(?:/|$)",
        path,
        re.I,
    ):
        return True
    return False


def is_acceptable_resolve_target(url) -> bool:
    """ATS or company job URL usable as apply_url (not aggregator / Workday / iCIMS)."""
    s = str(url or "").strip()
    if not s or is_aggregator_url(s) or is_unfetchable_ats(s):
        return False
    if is_known_ats_url(s):
        return True
    return bool(is_ats_or_company_apply(s) and looks_like_job_apply_url(s))


def is_resolved_apply_url(url) -> bool:
    """True when apply_url is already a company/ATS destination (not aggregator)."""
    s = str(url or "").strip()
    if not s or is_aggregator_url(s):
        return False
    return bool(is_ats_or_company_apply(s))


def is_fetchable_ats_url(url) -> bool:
    """ATS / company job URL we can try to fetch without bypassing Akamai/CAPTCHA."""
    return is_acceptable_resolve_target(url)


def filter_candidate_urls(urls) -> list[str]:
    """Keep unique fetchable ATS/company job URLs; drop aggregators and Workday/iCIMS."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls or []:
        u = str(raw or "").strip()
        if not u or not is_fetchable_ats_url(u):
            continue
        key = normalize_url(u) or u.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out


def prefer_company_relevant_urls(urls: list[str], company: str) -> list[str]:
    """When company is known, prefer URLs whose host/path mention the employer.

    Stops Bing ``Applied …`` false-positives (applied.com) from crowding out
    real ATS hits when both appear in a SERP mix.
    """
    company = str(company or "").strip()
    if not company or not urls:
        return list(urls or [])
    relevant: list[str] = []
    other: list[str] = []
    for u in urls:
        if company_matches_url(company, u) or companies_match(
            company, _host(u).split(".")[0] if _host(u) else ""
        ):
            relevant.append(u)
        else:
            other.append(u)
    return relevant + other if relevant else list(urls)


def build_search_queries(
    company: str,
    title: str,
    location: str | None = None,
) -> list[str]:
    """Company + title queries. Prefer Google-style company-first phrasing.

    Title-first queries make Bing latch onto common words like ``Applied``
    (applied.com) and miss the employer. Do not rely on guessing
    ``{company}.applytojob.com`` alone.
    """
    company = str(company or "").strip()
    title = str(title or "").strip()
    if not company or not title:
        return []
    out = [
        # Google page-1 style: company first, then title (no filler words).
        f"{company} {title}",
        f'"{company}" "{title}"',
        f'"{title}" "{company}" apply',
        (
            f'"{title}" {company} '
            "(greenhouse OR lever OR ashby OR ashbyhq OR applytojob OR "
            "smartrecruiters OR workable OR bamboohr OR careers)"
        ),
        f"site:jobs.ashbyhq.com {company} {title}",
        f"site:boards.greenhouse.io {company} {title}",
        f"site:jobs.lever.co {company} {title}",
        f"{title} {company} careers apply",
    ]
    loc = str(location or "").strip()
    if loc and loc.lower() not in ("remote", "remote, us", "united states", "us"):
        out.append(f'"{company}" "{title}" {loc} apply')
    # Dedupe while preserving order.
    seen: set[str] = set()
    uniq: list[str] = []
    for q in out:
        if q in seen:
            continue
        seen.add(q)
        uniq.append(q)
    return uniq


_ATS_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def company_ats_slug_candidates(company: str) -> list[str]:
    """Slug guesses for Ashby/Greenhouse/Lever board hosts.

    Only URL-safe path segments (no spaces/commas) — raw multi-word company
    names must never be interpolated into board API URLs.
    """
    raw = str(company or "").strip()
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _add(s: str) -> None:
        s = str(s or "").strip()
        if not s or not _ATS_SLUG_RE.fullmatch(s):
            return
        key = s.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(s)

    # Prefer compact / hyphenated forms; skip raw "Foo Bar Inc." with spaces.
    if _ATS_SLUG_RE.fullmatch(raw):
        _add(raw)
    if _ATS_SLUG_RE.fullmatch(raw.lower()):
        _add(raw.lower())
    compact = normalize_company(raw)
    if compact:
        _add(compact)
    words = re.findall(r"[A-Za-z0-9]+", raw)
    if words:
        _add("".join(words).lower())
        _add("-".join(w.lower() for w in words))
        if len(words) > 1:
            _add(words[0].lower())
    return out


def load_ats_registry(registry_path: Path | None = None) -> dict:
    path = Path(registry_path) if registry_path is not None else REGISTRY_FILE
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def registry_slugs_matching_company(
    company: str,
    registry: dict | None = None,
) -> list[tuple[str, str]]:
    """``(ats, slug)`` pairs from ``ats_companies.json`` that match ``company``."""
    reg = registry if isinstance(registry, dict) else load_ats_registry()
    company = str(company or "").strip()
    if not company:
        return []
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ats in _ATS_BOARD_PROBE_ORDER:
        slugs = reg.get(ats)
        if not isinstance(slugs, list):
            continue
        for slug in slugs:
            s = str(slug or "").strip()
            if not s:
                continue
            if not (
                companies_match(company, s)
                or companies_match(company, s.replace("-", " "))
            ):
                continue
            key = (ats, s.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append((ats, s))
    return out


def _http_get_json(url: str, *, timeout: int = 15) -> dict | list | None:
    try:
        raw = _http_get(url, headers={"Accept": "application/json"}, timeout=timeout)
        data = json.loads(raw)
    except (URLError, HTTPError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, (dict, list)) else None


def fetch_ats_board_postings(ats: str, slug: str) -> list[dict]:
    """Fetch public board JSON → ``[{title, url, company}]``. Cached per process."""
    ats_key = str(ats or "").strip().lower()
    slug = str(slug or "").strip()
    if not ats_key or not slug or ats_key not in _ATS_BOARD_API:
        return []
    cache_key = (ats_key, slug.lower())
    with _ATS_BOARD_CACHE_LOCK:
        if cache_key in _ATS_BOARD_CACHE:
            return list(_ATS_BOARD_CACHE[cache_key])

    postings: list[dict] = []
    url = _ATS_BOARD_API[ats_key].format(slug=slug)
    data = _http_get_json(url)
    if data is None:
        with _ATS_BOARD_CACHE_LOCK:
            _ATS_BOARD_CACHE[cache_key] = []
        return []

    if ats_key == "ashby" and isinstance(data, dict):
        for job in data.get("jobs") or []:
            if not isinstance(job, dict):
                continue
            job_url = str(job.get("jobUrl") or job.get("applyUrl") or "").strip()
            title = str(job.get("title") or "").strip()
            if job_url and title:
                postings.append({"title": title, "url": job_url, "company": slug})
    elif ats_key == "greenhouse" and isinstance(data, dict):
        for job in data.get("jobs") or []:
            if not isinstance(job, dict):
                continue
            job_url = str(job.get("absolute_url") or "").strip()
            title = str(job.get("title") or "").strip()
            if job_url and title:
                postings.append({"title": title, "url": job_url, "company": slug})
    elif ats_key == "lever" and isinstance(data, list):
        for job in data:
            if not isinstance(job, dict):
                continue
            job_url = str(job.get("hostedUrl") or job.get("applyUrl") or "").strip()
            title = str(job.get("text") or "").strip()
            if job_url and title:
                postings.append({"title": title, "url": job_url, "company": slug})
    elif ats_key == "smartrecruiters" and isinstance(data, dict):
        for job in data.get("content") or []:
            if not isinstance(job, dict):
                continue
            title = str(job.get("name") or "").strip()
            ref = str(job.get("refNumber") or job.get("id") or "").strip()
            company_slug = str(
                ((job.get("company") or {}) if isinstance(job.get("company"), dict) else {}).get(
                    "identifier"
                )
                or slug
            ).strip()
            job_url = str(job.get("applyUrl") or "").strip()
            if not job_url and ref:
                job_url = (
                    f"https://jobs.smartrecruiters.com/{company_slug}/{ref}"
                )
            if job_url and title:
                postings.append({"title": title, "url": job_url, "company": company_slug})
    elif ats_key == "workable" and isinstance(data, dict):
        for job in data.get("jobs") or []:
            if not isinstance(job, dict):
                continue
            title = str(job.get("title") or "").strip()
            shortcode = str(job.get("shortcode") or "").strip()
            job_url = str(job.get("url") or "").strip()
            # Prefer slug-prefixed apply URLs so company_matches_url / scoring
            # can see the employer (API often returns /j/{code} without slug).
            if shortcode:
                slug_url = f"https://apply.workable.com/{slug}/j/{shortcode}/"
                if (not job_url) or re.search(
                    r"apply\.workable\.com/j/[A-Za-z0-9]+/?$", job_url, re.I
                ):
                    job_url = slug_url
            if job_url and title:
                postings.append({"title": title, "url": job_url, "company": slug})

    with _ATS_BOARD_CACHE_LOCK:
        _ATS_BOARD_CACHE[cache_key] = list(postings)
    return list(postings)


def search_ats_boards(
    company: str,
    title: str,
    *,
    registry_path: Path | None = None,
    max_probes: int = _ATS_BOARD_MAX_PROBES,
) -> list[str]:
    """Return ATS job URLs by probing known boards (registry + slug guesses).

    This is the free path that recovers ``jobs.ashbyhq.com/…`` when Brave/Bing
    HTML SERPs are empty, rate-limited, or return irrelevant hosts.
    """
    company = str(company or "").strip()
    title = str(title or "").strip()
    if not company or not title:
        return []

    reg = load_ats_registry(registry_path)
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add_pair(ats: str, slug: str) -> None:
        ats_k = str(ats or "").strip().lower()
        s = str(slug or "").strip()
        if not ats_k or not s or ats_k not in _ATS_BOARD_API:
            return
        key = (ats_k, s.lower())
        if key in seen:
            return
        seen.add(key)
        pairs.append((ats_k, s))

    for ats, slug in registry_slugs_matching_company(company, reg):
        _add_pair(ats, slug)

    # Slug guesses only for the big three (cheap, high hit-rate).
    for slug in company_ats_slug_candidates(company):
        for ats in ("ashby", "greenhouse", "lever"):
            _add_pair(ats, slug)

    matched: list[str] = []
    matched_seen: set[str] = set()
    probes = 0
    limit = max(1, int(max_probes or _ATS_BOARD_MAX_PROBES))
    for ats, slug in pairs:
        if probes >= limit:
            break
        probes += 1
        try:
            postings = fetch_ats_board_postings(ats, slug)
        except Exception as e:
            log(f"ats board {ats}/{slug} failed: {e}")
            continue
        board_hits = 0
        for post in postings:
            if not titles_match(title, post.get("title") or ""):
                continue
            u = str(post.get("url") or "").strip()
            if not u or not is_fetchable_ats_url(u):
                continue
            key = normalize_url(u) or u.lower()
            if key in matched_seen:
                continue
            matched_seen.add(key)
            matched.append(u)
            board_hits += 1
        if board_hits:
            # One matching board is enough — avoid extra probes.
            break
    return matched



def companies_match(a: str, b: str) -> bool:
    na = normalize_company(a)
    nb = normalize_company(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if min(len(na), len(nb)) >= 4 and (na in nb or nb in na):
        return True
    return False


_HOST_LABEL_SKIP = frozenset(
    {
        "www",
        "careers",
        "jobs",
        "job",
        "apply",
        "boards",
        "board",
        "my",
        "app",
        "apps",
        "go",
        "get",
        "hire",
        "hiring",
        "talent",
        "recruiting",
        "recruit",
        "com",
        "org",
        "net",
        "io",
        "co",
        "us",
        "uk",
        "ai",
    }
)


def company_matches_url(company: str, url: str) -> bool:
    """True when the employer appears in the URL host or early path segments.

    Checks every host label (not just the subdomain) so
    ``careers.airbnb.com`` / ``apply.careers.microsoft.com`` / ``jobs.greystar.com``
    still match — Ashby UUID paths already match via the company slug segment.
    """
    host = _host(url)
    if not host:
        return False
    for label in host.split("."):
        lab = str(label or "").strip().lower()
        if not lab or lab in _HOST_LABEL_SKIP:
            continue
        if companies_match(company, lab):
            return True
    try:
        path = unquote(urlparse(url).path or "")
    except ValueError:
        path = ""
    for part in [p for p in path.split("/") if p][:3]:
        if companies_match(company, part.replace("-", " ")):
            return True
    return False


def titles_match(title, other) -> bool:
    nt = normalize_title(title)
    if not nt:
        return False
    other_s = str(other or "").strip()
    if re.match(r"https?://", other_s, re.I) or "://" in other_s:
        try:
            path = unquote(urlparse(other_s).path or "")
        except ValueError:
            path = other_s
        slug = path.rstrip("/").split("/")[-1] if path else ""
        other_n = normalize_title(slug.replace("-", " ").replace("_", " "))
    else:
        other_n = normalize_title(other_s)
    if not other_n:
        return False
    if nt == other_n:
        return True
    a, b = set(nt.split()), set(other_n.split())
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return shorter <= longer


def _content_words(text: str) -> list[str]:
    return [
        w
        for w in normalize_jd_text(text).split()
        if w not in _STOPWORDS and len(w) > 2
    ]


def distinctive_phrases(text: str, n: int = 4) -> set[str]:
    words = _content_words(text)
    if len(words) < n:
        return set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def distinctive_tokens(text: str) -> set[str]:
    return {w for w in _content_words(text) if len(w) > 3}


def jd_overlap_score(in_hand: str, candidate: str) -> float:
    """Distinctive 4-gram overlap plus token overlap. Not an exact hash."""
    pa = distinctive_phrases(in_hand)
    pb = distinctive_phrases(candidate)
    if pa and pb:
        phrase = len(pa & pb) / min(len(pa), len(pb))
    else:
        phrase = 0.0
    ta = distinctive_tokens(in_hand)
    tb = distinctive_tokens(candidate)
    if ta and tb:
        token = len(ta & tb) / min(len(ta), len(tb))
    else:
        token = 0.0
    return 0.7 * phrase + 0.3 * token


def is_easy_apply_job(job: dict) -> bool:
    if not isinstance(job, dict):
        return False
    if job.get("easy_apply") is True:
        return True
    if str(job.get("deleted_reason") or "").strip().lower() == "easy_apply":
        return True
    status = str(job.get("status") or "").strip().lower()
    if status in ("skipped_easy_apply",):
        return True
    kind = str(job.get("apply_kind") or "").strip().lower().replace("-", "_")
    if kind == "easy_apply":
        return True
    detail = str(job.get("status_detail") or "").lower()
    if "easy apply" in detail and status in ("deleted", "skipped_easy_apply"):
        return True
    return False


def needs_apply_resolution(job: dict) -> bool:
    """True when apply/job URL is still an aggregator and not Easy Apply / already ATS."""
    if not isinstance(job, dict):
        return False
    if is_easy_apply_job(job):
        return False
    apply = str(job.get("apply_url") or "").strip()
    job_url = str(job.get("job_url") or "").strip()
    if is_resolved_apply_url(apply):
        return False
    res = job.get("apply_url_resolution") if isinstance(job.get("apply_url_resolution"), dict) else {}
    if str(res.get("confidence") or "") == "high" and is_resolved_apply_url(
        res.get("url") or apply
    ):
        return False
    primary = apply or job_url
    if not primary:
        return False
    return is_aggregator_url(primary) or is_aggregator_url(job_url)


def apply_url_still_linkedin(job: dict) -> bool:
    """True when ``apply_url`` itself is still a LinkedIn job page."""
    try:
        from linkedin_resolve_apply import is_linkedin_job_url
    except ImportError:
        apply = str((job or {}).get("apply_url") or "").strip().lower()
        return "linkedin.com" in apply and "/jobs/" in apply
    return bool(is_linkedin_job_url(str((job or {}).get("apply_url") or "").strip()))


def should_auto_resolve_job(job: dict) -> bool:
    """Whether Discover's post-merge HTTP resolve should touch this job.

    Skip when apply_url is already a known ATS/company careers page, or when
    ``apply_resolve_status`` is terminal (ok / easy_apply / no_external)
    **unless** apply_url is still LinkedIn (stamped ok but never upgraded).
    """
    if not isinstance(job, dict):
        return False
    if is_easy_apply_job(job):
        return False
    status = str(job.get("status") or "").strip().lower()
    if status in ("deleted", "merged", "applied", "blocked_captcha"):
        return False
    apply = str(job.get("apply_url") or "").strip()
    if is_resolved_apply_url(apply):
        return False
    resolve_status = str(job.get("apply_resolve_status") or "").strip().lower()
    still_li = apply_url_still_linkedin(job)
    if resolve_status in TERMINAL_APPLY_RESOLVE_STATUSES and not still_li:
        return False
    if still_li:
        # Re-resolve ok+still-linkedin; skip easy_apply / no_external terminals
        # that correctly remain on LinkedIn.
        if resolve_status in ("easy_apply", "no_external"):
            return False
        return True
    return needs_apply_resolution(job)


def _iso_ts(value: str | None) -> str:
    return str(value or "").strip()


def job_touched_since(job: dict, since_iso: str | None) -> bool:
    """True when job was created/updated at or after ``since_iso`` (lexicographic ISO)."""
    if not since_iso:
        return True
    since = _iso_ts(since_iso)
    if not since:
        return True
    created = _iso_ts(job.get("created_at"))
    updated = _iso_ts(job.get("updated_at"))
    stamp = max(created, updated)
    return bool(stamp and stamp >= since)


def select_jobs_for_discovery_resolve(
    jobs: list,
    *,
    since_iso: str | None = None,
    job_ids: set[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Jobs Discover should HTTP-resolve after merge (this-run filter optional).

    ``limit`` caps how many jobs are returned (oldest-first by created_at)
    so backlog / continuous resolve stays rate-limited.
    """
    id_filter = {str(x) for x in (job_ids or ()) if x}
    out: list[dict] = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        jid = str(job.get("id") or "")
        if id_filter and jid not in id_filter:
            continue
        if since_iso and not job_touched_since(job, since_iso):
            continue
        if should_auto_resolve_job(job):
            out.append(job)
    if limit is not None and limit > 0 and len(out) > limit:
        out.sort(key=lambda j: str(j.get("created_at") or j.get("updated_at") or ""))
        out = out[:limit]
    return out


def _specific_job_id_in_url(url: str) -> bool:
    try:
        p = urlparse(str(url or ""))
    except ValueError:
        return False
    path = p.path or ""
    return bool(
        _GH_JID_RE.search(p.query or "")
        or _NUMERIC_JOB_ID_RE.search(path)
        or _ATS_UUID_RE.search(path)
    )


def score_candidate(job: dict, url: str, page: dict | None) -> dict:
    page = page or {}
    title_ok = titles_match(job.get("title"), page.get("title") or "") or titles_match(
        job.get("title"), url
    )
    company_ok = companies_match(job.get("company") or "", page.get("company") or "") or (
        company_matches_url(job.get("company") or "", url)
    )
    company_host_ok = company_matches_url(job.get("company") or "", url)
    in_hand = (
        (job.get("job_description") or "")
        or (job.get("description") or "")
    )
    overlap = 0.0
    if in_hand and page.get("description"):
        overlap = jd_overlap_score(in_hand, page.get("description") or "")

    if not is_fetchable_ats_url(url):
        conf = "low"
    elif not company_ok:
        conf = "low"
    elif title_ok and overlap >= HIGH_OVERLAP:
        conf = "high"
    elif title_ok and company_host_ok and looks_like_job_apply_url(url):
        # Company careers / Greenhouse-backed pages often block JD fetch; title +
        # company host match on a job-shaped URL is enough for high confidence.
        conf = "high"
    elif title_ok:
        conf = "medium"
    elif (
        company_host_ok
        and looks_like_job_apply_url(url)
        and _specific_job_id_in_url(url)
        and not str(page.get("title") or "").strip()
    ):
        # Public-search hit on a company job-id URL when the page title was not
        # fetchable (SPA / bot wall) — still upgrade rather than prune.
        conf = "high"
    else:
        conf = "low"

    return {
        "confidence": conf,
        "url": url,
        "title_match": bool(title_ok),
        "company_match": bool(company_ok),
        "score": overlap,
    }


def merge_resolved_apply(job: dict, ats_url: str) -> dict:
    """Upgrade apply_url to ATS; keep aggregator on job_url/source_url/alts."""
    original_apply = str(job.get("apply_url") or "").strip()
    original_job = str(job.get("job_url") or original_apply).strip()
    alts = [u for u in (job.get("alternate_urls") or []) if u]
    if original_apply and original_apply not in alts:
        alts.append(original_apply)
    item = dict(job)
    item["apply_url"] = ats_url
    item["job_url"] = original_job or original_apply
    if is_aggregator_url(original_apply) and not str(item.get("source_url") or "").strip():
        item["source_url"] = original_apply
    item["alternate_urls"] = alts
    enriched = enrich_listing_urls(item)
    job["apply_url"] = enriched.get("apply_url") or ats_url
    job["job_url"] = enriched.get("job_url") or original_job
    if enriched.get("source_url"):
        job["source_url"] = enriched["source_url"]
    job["alternate_urls"] = enriched.get("alternate_urls") or alts
    return job


def apply_scored_resolution(job: dict, scored: dict) -> dict:
    """High → overwrite apply_url. Medium → record candidate only. Low → no-op on URL."""
    conf = str((scored or {}).get("confidence") or "low")
    if conf == "low":
        return job
    url = str((scored or {}).get("url") or "").strip()
    if conf == "high" and url:
        merge_resolved_apply(job, url)
        job.pop("apply_url_manual", None)
    new_res = {
        "confidence": conf,
        "url": url or None,
        "score": (scored or {}).get("score"),
    }
    old = job.get("apply_url_resolution") if isinstance(job.get("apply_url_resolution"), dict) else {}
    if (
        old.get("confidence") == new_res["confidence"]
        and old.get("url") == new_res["url"]
        and old.get("score") == new_res["score"]
    ):
        # Idempotent: keep existing resolved_at
        return job
    new_res["resolved_at"] = now_iso()
    job["apply_url_resolution"] = new_res
    return job


def classify_apply_resolve_status(result: dict | None) -> str:
    """Map a resolve result to ``apply_resolve_status``."""
    result = result or {}
    conf = str(result.get("confidence") or "low")
    reason = str(result.get("reason") or "").strip()
    if conf == "high" and result.get("url"):
        return "ok"
    mapped = _REASON_STATUS.get(reason)
    if mapped:
        return mapped
    if conf == "medium":
        return "skipped"
    return "failed"


def sanitize_apply_resolve_message(msg: str | None) -> str | None:
    """Short human message — never cookies/secrets/PII."""
    if msg is None:
        return None
    s = str(msg).strip()
    if not s:
        return None
    low = s.lower()
    # Strip anything that looks like a cookie dump.
    if "li_at=" in low or "jsessionid=" in low or "cookie:" in low:
        return _DEFAULT_RESOLVE_MESSAGES["not_logged_in"]
    if len(s) > APPLY_RESOLVE_MSG_MAX:
        s = s[: APPLY_RESOLVE_MSG_MAX - 1] + "…"
    return s


def success_apply_resolve_reason(result: dict | None) -> str:
    result = result or {}
    reason = str(result.get("reason") or "").strip()
    if reason and reason not in ("upgraded",):
        return reason[:80]
    method = str(result.get("method") or "").strip()
    if method == "linkedin_http":
        return "linkedin_apply_href"
    if method == "linkedin_session":
        return "linkedin_external_redirect"
    if method:
        return method[:80]
    return "upgraded"


def compact_apply_resolve_fields(result: dict | None) -> dict:
    """Build compact job fields for a resolve outcome (no secrets)."""
    result = result or {}
    status = classify_apply_resolve_status(result)
    if status == "ok":
        reason = success_apply_resolve_reason(result)
        message = None
    else:
        reason = str(result.get("reason") or "failed")[:80] or "failed"
        message = sanitize_apply_resolve_message(result.get("message"))
        if not message:
            message = _DEFAULT_RESOLVE_MESSAGES.get(reason)
    out = {
        "apply_resolve_status": status,
        "apply_resolve_reason": reason,
        "apply_resolve_at": now_iso(),
    }
    if message:
        out["apply_resolve_message"] = message
    return out


def apply_resolve_fields_unchanged(job: dict, fields: dict) -> bool:
    for key in ("apply_resolve_status", "apply_resolve_reason", "apply_resolve_message"):
        if (job.get(key) or None) != (fields.get(key) or None):
            return False
    return True


def set_apply_resolve_fields(job: dict, result: dict | None) -> bool:
    """Stamp resolve outcome on ``job``. Returns True if fields changed.

    Idempotent: same status/reason/message → no mutation (avoids thrashing
    jobs.json via timestamp-only updates).
    """
    fields = compact_apply_resolve_fields(result)
    changed = False
    if not apply_resolve_fields_unchanged(job, fields):
        job["apply_resolve_status"] = fields["apply_resolve_status"]
        job["apply_resolve_reason"] = fields["apply_resolve_reason"]
        job["apply_resolve_at"] = fields["apply_resolve_at"]
        if fields.get("apply_resolve_message"):
            job["apply_resolve_message"] = fields["apply_resolve_message"]
        else:
            job.pop("apply_resolve_message", None)
        changed = True
    if public_search_was_attempted(job, result) and not job.get(
        "apply_resolve_search_attempted"
    ):
        job["apply_resolve_search_attempted"] = True
        changed = True
    return changed


# Soft-delete discovered Open jobs when apply resolve leaves a non-ATS
# LinkedIn/aggregator URL (failed / no_external / easy_apply). Stamps
# ``unresolved_apply_url`` for the OmniDex "Unresolved URL" chip.
UNRESOLVED_APPLY_URL_REASON = "unresolved_apply_url"
# Backward-compatible alias (older prune-settings / docs).
APPLY_RESOLVE_FAILED_REASON = UNRESOLVED_APPLY_URL_REASON
UNRESOLVED_APPLY_RESOLVE_STATUSES = frozenset({"failed", "no_external", "easy_apply"})


def apply_url_still_unresolved_aggregator(job: dict) -> bool:
    """True when apply_url is still LinkedIn/aggregator (not ATS/company careers)."""
    if not isinstance(job, dict):
        return False
    apply = str(job.get("apply_url") or "").strip()
    job_url = str(job.get("job_url") or "").strip()
    if is_resolved_apply_url(apply):
        return False
    primary = apply or job_url
    if not primary:
        return False
    return is_aggregator_url(primary) or is_aggregator_url(job_url)


def public_search_was_attempted(job: dict | None = None, result: dict | None = None) -> bool:
    """True when company+title public search already ran for this job/result."""
    if isinstance(result, dict) and result.get("search_attempted"):
        return True
    if not isinstance(job, dict):
        return False
    if job.get("apply_resolve_search_attempted"):
        return True
    reason = str(job.get("apply_resolve_reason") or "").strip().lower()
    return reason in _SEARCH_ATTEMPTED_REASONS


def should_prune_unresolved_apply_url(job: dict) -> bool:
    """True when an Open job should be tombstoned for unresolved apply URL.

    Never prune LinkedIn/aggregator misses (http_error, no_external, failed, …)
    until public company+title search was attempted — except Easy Apply /
    CAPTCHA / profile lock, where search is intentionally skipped.
    """
    if not isinstance(job, dict):
        return False
    if str(job.get("status") or "").strip().lower() != "discovered":
        return False
    if not apply_url_still_unresolved_aggregator(job):
        return False
    resolve_status = str(job.get("apply_resolve_status") or "").strip().lower()
    reason = str(job.get("apply_resolve_reason") or "").strip().lower()
    if resolve_status == "easy_apply" or reason in _PRUNE_OK_WITHOUT_SEARCH_REASONS:
        return True
    if resolve_status in UNRESOLVED_APPLY_RESOLVE_STATUSES:
        return public_search_was_attempted(job)
    # Legacy Easy Apply flag without a resolve stamp, still on LinkedIn.
    if is_easy_apply_job(job) and apply_url_still_linkedin(job):
        return True
    return False


# Aliases — older call sites / scheduled prune.
should_prune_apply_resolve_failed = should_prune_unresolved_apply_url


def stamp_unresolved_apply_url_tag(job: dict, *, on: bool = True) -> None:
    """Stamp/clear the list+detail ``Unresolved URL`` chip field."""
    if on:
        job["unresolved_apply_url"] = True
    else:
        job.pop("unresolved_apply_url", None)


def tombstone_unresolved_apply_url(job: dict) -> bool:
    """Soft-delete a discovered job with unresolved apply URL. Mutates in place.

    Sets ``status=deleted``, ``deleted_reason=unresolved_apply_url``,
    ``unresolved_apply_url=True`` (chip), and ``status_detail`` from resolve
    reason/message (sanitized, no secrets). Returns True when mutated.
    Caller must URL-tombstone after releasing the jobs write lock.
    """
    if not should_prune_unresolved_apply_url(job):
        return False
    now = now_iso()
    resolve_status = str(job.get("apply_resolve_status") or "").strip().lower()
    reason = str(job.get("apply_resolve_reason") or resolve_status or "unresolved").strip()
    reason = reason or "unresolved"
    message = sanitize_apply_resolve_message(job.get("apply_resolve_message"))
    detail = f"Pruned: unresolved apply URL ({reason})."
    if message:
        detail = f"{detail} {message}"
    if len(detail) > 500:
        detail = detail[:499] + "…"
    job["status"] = "deleted"
    job["deleted_reason"] = UNRESOLVED_APPLY_URL_REASON
    job["deleted_at"] = now
    job["updated_at"] = now
    job["status_detail"] = detail
    stamp_unresolved_apply_url_tag(job, on=True)
    return True


tombstone_apply_resolve_failed = tombstone_unresolved_apply_url


def _block_snap_for_job(job: dict) -> dict:
    return {
        "id": job.get("id"),
        "company": job.get("company"),
        "title": job.get("title"),
        "apply_url": job.get("apply_url"),
        "job_url": job.get("job_url"),
        "alternate_urls": list(job.get("alternate_urls") or []),
    }


def _tombstone_url_block(snap: dict | None) -> None:
    if not snap:
        return
    try:
        from blocked_urls import block_deleted_job

        block_deleted_job(snap, keep_tombstone=True)
    except TypeError:
        try:
            from blocked_urls import block_deleted_job

            block_deleted_job(snap)
        except Exception:
            pass
    except Exception:
        pass


def sweep_unresolved_apply_urls(*, write: bool = True) -> dict:
    """One-shot: tombstone discovered jobs with unresolved LinkedIn/aggregator URL.

    Covers ``apply_resolve_status`` in failed / no_external / easy_apply (and
    legacy Easy Apply flags still on LinkedIn). Only ``status=discovered``.
    """
    to_block: list[dict] = []
    moved = 0
    if write:
        with locked_jobs_for_write() as data:
            for job in data.get("jobs") or []:
                if not isinstance(job, dict):
                    continue
                if tombstone_unresolved_apply_url(job):
                    moved += 1
                    to_block.append(_block_snap_for_job(job))
        for snap in to_block:
            _tombstone_url_block(snap)
    else:
        with locked_jobs_for_read() as data:
            for job in data.get("jobs") or []:
                if isinstance(job, dict) and should_prune_unresolved_apply_url(job):
                    moved += 1
    return {"moved": moved, "dry_run": not write}


sweep_apply_resolve_failed = sweep_unresolved_apply_urls


def clear_unresolved_deleted_fields(job: dict) -> None:
    """Clear soft-delete / Unresolved URL fields; set status back to discovered."""
    job["status"] = "discovered"
    job.pop("deleted_reason", None)
    job.pop("deleted_at", None)
    job.pop("status_detail", None)
    stamp_unresolved_apply_url_tag(job, on=False)
    job["updated_at"] = now_iso()


def _company_title_key(company: str, title: str) -> tuple[str, str]:
    return (normalize_company(company), normalize_title(title))


def find_sibling_resolved_apply_url(
    job: dict,
    jobs: list | None,
) -> str | None:
    """Return another job's company/ATS apply_url for the same company+title.

    Fast restore path when a duplicate/sibling row already has a good URL —
    no public search required.
    """
    if not isinstance(job, dict) or not jobs:
        return None
    key = _company_title_key(job.get("company") or "", job.get("title") or "")
    if not key[0] or not key[1]:
        return None
    jid = str(job.get("id") or "")
    for other in jobs:
        if not isinstance(other, dict):
            continue
        oid = str(other.get("id") or "")
        if oid and oid == jid:
            continue
        if _company_title_key(other.get("company") or "", other.get("title") or "") != key:
            continue
        url = str(other.get("apply_url") or "").strip()
        if url and is_acceptable_resolve_target(url):
            return url
    return None


def _restore_method_bucket(reason: str | None, method: str | None = None) -> str:
    """Map resolve reason/method to sibling | existing | ats_board_api | public_search."""
    r = str(reason or "").strip().lower()
    m = str(method or "").strip().lower()
    if r == "sibling_resolved_apply_url" or m == "sibling":
        return "sibling"
    if r == "already_resolved_apply_url" or m == "existing":
        return "existing"
    if r == "ats_board_api" or m == "ats_board_api":
        return "ats_board_api"
    return "public_search"


def try_existing_or_sibling_apply_url(
    job: dict,
    jobs: list | None,
) -> dict | None:
    """Return a high-confidence result from existing apply_url or a sibling row.

    Used by Discover post-resolve and deleted re-resolve before board/search.
    """
    if not isinstance(job, dict):
        return None
    existing = str(job.get("apply_url") or "").strip()
    if existing and (
        is_acceptable_resolve_target(existing) or is_resolved_apply_url(existing)
    ):
        return {
            "confidence": "high",
            "url": existing,
            "reason": "already_resolved_apply_url",
            "method": "existing",
            "score": 1.0,
            "search_attempted": True,
        }
    sibling_url = find_sibling_resolved_apply_url(job, jobs)
    if sibling_url:
        return {
            "confidence": "high",
            "url": sibling_url,
            "reason": "sibling_resolved_apply_url",
            "method": "sibling",
            "score": 1.0,
            "search_attempted": True,
        }
    return None


def restore_unresolved_deleted_job(
    job: dict,
    *,
    apply_url: str | None = None,
    resolve_reason: str = "public_search",
    resolve_method: str | None = None,
) -> bool:
    """Undelete a job tombstoned for unresolved_apply_url. Optionally set apply_url.

    Mutates ``job`` in place. Caller should ``unblock_job`` outside the jobs lock
    when write=True. Returns True when the job was restored.
    """
    if not isinstance(job, dict):
        return False
    status = str(job.get("status") or "").strip().lower()
    reason = str(job.get("deleted_reason") or "").strip().lower()
    if status != "deleted" or reason != UNRESOLVED_APPLY_URL_REASON:
        return False
    clear_unresolved_deleted_fields(job)
    url = str(apply_url or "").strip()
    if url and is_acceptable_resolve_target(url):
        merge_resolved_apply(job, url)
        method = str(resolve_method or "").strip() or (
            "sibling"
            if resolve_reason == "sibling_resolved_apply_url"
            else "existing"
            if resolve_reason == "already_resolved_apply_url"
            else "ats_board_api"
            if resolve_reason == "ats_board_api"
            else "public_search"
        )
        set_apply_resolve_fields(
            job,
            {
                "confidence": "high",
                "url": url,
                "reason": resolve_reason,
                "method": method,
                "score": 1.0,
            },
        )
        stamp_unresolved_apply_url_tag(job, on=False)
    return True


def _aggregator_reresolve_rank(job: dict) -> tuple:
    """Lower = higher priority for bounded re-resolve batches."""
    company = str(job.get("company") or "").strip().lower()
    reason = str(job.get("apply_resolve_reason") or "").strip().lower()
    blob = " ".join(
        str(job.get(k) or "")
        for k in ("source", "apply_url", "job_url", "source_url")
    ).lower()
    # Coinbase / recent http_error first (user-visible false prunes).
    coinbase = 0 if "coinbase" in company else 1
    http_err = 0 if reason == "http_error" else 1
    agg_rank = 50
    for i, hint in enumerate(_AGGREGATOR_RERESOLVE_PRIORITY):
        if hint in blob:
            agg_rank = i
            break
    else:
        if "linkedin.com" in blob or (job.get("source") or "").lower() == "linkedin":
            agg_rank = 99
    deleted_at = str(job.get("deleted_at") or job.get("updated_at") or "")
    # Recent first within bucket (ISO lexicographic invert via negation of string).
    return (coinbase, http_err, agg_rank, "" if not deleted_at else deleted_at, str(job.get("id") or ""))


def select_unresolved_deleted_for_reresolve(
    jobs: list,
    *,
    limit: int | None = RERESOLVE_DELETED_DEFAULT_LIMIT,
    include_linkedin: bool = True,
    skip_ids: set[str] | None = None,
) -> list[dict]:
    """Pick pruned unresolved_apply_url jobs for a cheap public-search retry.

    LinkedIn-sourced rows are included by default (http_error backlog). Pass
    ``include_linkedin=False`` only to prefer non-LinkedIn aggregators.
    """
    skip = {str(x) for x in (skip_ids or set()) if x}
    out: list[dict] = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        jid = str(job.get("id") or "")
        if jid and jid in skip:
            continue
        if str(job.get("status") or "").strip().lower() != "deleted":
            continue
        if str(job.get("deleted_reason") or "").strip().lower() != UNRESOLVED_APPLY_URL_REASON:
            continue
        if not (job.get("company") and job.get("title")):
            continue
        if not include_linkedin:
            blob = " ".join(
                str(job.get(k) or "")
                for k in ("source", "apply_url", "job_url")
            ).lower()
            if "linkedin.com" in blob or (job.get("source") or "").lower() == "linkedin":
                continue
        out.append(job)

    def _sort_key(j: dict) -> tuple:
        r = _aggregator_reresolve_rank(j)
        deleted_iso = r[3]
        try:
            ts = datetime.fromisoformat(deleted_iso.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            ts = 0.0
        # Newest deleted_at first within the same priority bucket.
        return (r[0], r[1], r[2], -ts, r[4])

    out.sort(key=_sort_key)
    if limit is not None and int(limit) > 0:
        out = out[: int(limit)]
    return out


def reresolve_unresolved_deleted(
    *,
    limit: int | None = RERESOLVE_DELETED_DEFAULT_LIMIT,
    write: bool = True,
    include_linkedin: bool = True,
    search_fn=None,
    fetch_fn=None,
    job_ids: set[str] | None = None,
    workers: int = RERESOLVE_DELETED_DEFAULT_WORKERS,
    progress_path: Path | str | None = None,
    reset_progress: bool = False,
    reliable_only: bool = False,
    extra_skip_ids: set[str] | None = None,
) -> dict:
    """Re-resolve pruned Unresolved URL jobs (HTTP, no Chrome).

    Order per job: existing apply_url → sibling company+title → ATS board API
    → optional public search (skipped when ``reliable_only``).

    On high-confidence hit: restore to discovered, upgrade apply_url, unblock.
    ``limit`` 0/None = full remaining backlog. Checkpointed via
    ``logs/reresolve_unresolved_deleted_progress.json`` so long runs resume.
    LinkedIn included by default. Parallel workers for search (default 4).

    ``reliable_only``: sibling + board API only (no CSE/HTML SERP). Ignores the
    search checkpoint for selection so registry/sibling growth can still restore
    previously attempted rows; non-restores are *not* marked done (search backlog
    stays eligible). Restores are checkpointed so full search skips them.
    ``extra_skip_ids``: session-local skips (reliable-only multi-chunk drains).
    """
    progress_path = Path(progress_path) if progress_path else RERESOLVE_PROGRESS_FILE
    if reset_progress and progress_path.is_file():
        try:
            progress_path.unlink()
        except OSError:
            pass
    done_ids = _load_progress(progress_path)
    with locked_jobs_for_read() as data:
        jobs = list(data.get("jobs") or [])
    # Process in chunks so checkpoints land frequently on large backlogs.
    chunk = int(limit) if (limit is not None and int(limit) > 0) else RERESOLVE_DELETED_CHUNK
    # Reliable-only re-probes unresolved rows (ignore search checkpoint) but
    # honors extra_skip_ids so multi-chunk drains advance.
    skip_ids: set[str] | None
    if job_ids:
        skip_ids = None
    elif reliable_only:
        skip_ids = {str(x) for x in (extra_skip_ids or set()) if x}
    else:
        skip_ids = set(done_ids)
        if extra_skip_ids:
            skip_ids |= {str(x) for x in extra_skip_ids if x}
    selected = select_unresolved_deleted_for_reresolve(
        jobs,
        limit=chunk if not job_ids else None,
        include_linkedin=include_linkedin,
        skip_ids=skip_ids,
    )
    if job_ids:
        want = {str(x) for x in job_ids if x}
        selected = [
            j for j in select_unresolved_deleted_for_reresolve(
                jobs, limit=None, include_linkedin=include_linkedin, skip_ids=None,
            )
            if str(j.get("id") or "") in want
        ]
        if limit and int(limit) > 0 and len(selected) > int(limit):
            selected = selected[: int(limit)]

    summary: dict = {
        "considered": len(selected),
        "restored": 0,
        "still_unresolved": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "upgraded": [],
        "errors": [],
        "dry_run": not write,
        "include_linkedin": include_linkedin,
        "workers": max(1, int(workers or 1)),
        "checkpoint": str(progress_path),
        "skipped_done": 0 if reliable_only else len(done_ids),
        "reliable_only": bool(reliable_only),
        "restored_by": {
            "sibling": 0,
            "existing": 0,
            "ats_board_api": 0,
            "public_search": 0,
        },
        "considered_ids": [
            str(j.get("id") or "") for j in selected if j.get("id")
        ],
    }
    workers_n = max(1, int(workers or 1))
    # Board/sibling only — never call CSE/HTML/Brave/JSearch.
    effective_search = (lambda _q: []) if reliable_only else search_fn

    def _resolve_one(job: dict) -> tuple[str, dict | None, str | None]:
        jid = str(job.get("id") or "")
        cheap = try_existing_or_sibling_apply_url(job, jobs)
        if cheap:
            return jid, cheap, None
        snap = dict(job)
        snap["status"] = "discovered"
        snap.pop("deleted_reason", None)
        try:
            result = resolve_job(
                snap,
                search_fn=effective_search,
                fetch_fn=fetch_fn,
                write=False,
                linkedin_session=False,
            )
            return jid, result, None
        except Exception as e:
            return jid, None, str(e)[:200]

    results: list[tuple[str, dict | None, str | None]] = []
    if workers_n <= 1 or len(selected) <= 1:
        for job in selected:
            results.append(_resolve_one(job))
    else:
        with ThreadPoolExecutor(max_workers=workers_n) as pool:
            futs = {pool.submit(_resolve_one, job): job for job in selected}
            for fut in as_completed(futs):
                results.append(fut.result())

    # Preserve selection order for logging/upgrades
    by_id = {jid: (res, err) for jid, res, err in results}
    progress_dirty = False
    for job in selected:
        jid = str(job.get("id") or "")
        result, err = by_id.get(jid, (None, "missing"))
        # Full search marks attempts done; reliable-only only checkpoints restores.
        if not reliable_only:
            done_ids.add(jid)
            progress_dirty = True
        if err or result is None:
            summary["errors"].append({"id": jid, "error": err or "missing"})
            summary["still_unresolved"] += 1
            continue
        conf = str(result.get("confidence") or "low")
        summary[conf] = summary.get(conf, 0) + 1
        if conf != "high" or not result.get("url"):
            summary["still_unresolved"] += 1
            # Stamp search_attempted on deleted row so prune gate / UI know
            # we tried — without restoring. Skip on reliable-only (board miss
            # is not a public-search attempt).
            if write and not reliable_only:
                try:
                    with locked_jobs_for_write() as data:
                        live = next(
                            (j for j in data.get("jobs") or [] if j.get("id") == jid),
                            None,
                        )
                        if live is not None and public_search_was_attempted(
                            live, result
                        ):
                            live["apply_resolve_search_attempted"] = True
                            live["apply_resolve_reason"] = str(
                                result.get("reason") or live.get("apply_resolve_reason") or ""
                            )[:80]
                            live["apply_resolve_at"] = now_iso()
                            live["updated_at"] = now_iso()
                except Exception as e:
                    summary["errors"].append({"id": jid, "error": str(e)[:200]})
            continue
        url = str(result.get("url") or "").strip()
        bucket = _restore_method_bucket(
            result.get("reason"), result.get("method")
        )
        if not write:
            summary["restored"] += 1
            summary["restored_by"][bucket] = int(
                summary["restored_by"].get(bucket) or 0
            ) + 1
            summary["upgraded"].append({"id": jid, "url": url, "method": bucket})
            if reliable_only:
                done_ids.add(jid)
                progress_dirty = True
            continue
        unblocked = None
        with locked_jobs_for_write() as data:
            live = next(
                (j for j in data.get("jobs") or [] if j.get("id") == jid),
                None,
            )
            if live is None:
                summary["still_unresolved"] += 1
                continue
            if not restore_unresolved_deleted_job(
                live,
                apply_url=url,
                resolve_reason=str(result.get("reason") or "public_search"),
                resolve_method=str(result.get("method") or "") or None,
            ):
                # Already restored / wrong reason — still try apply upgrade.
                if is_acceptable_resolve_target(url):
                    merge_resolved_apply(live, url)
                    set_apply_resolve_fields(live, result)
                    stamp_unresolved_apply_url_tag(live, on=False)
                    live["status"] = "discovered"
                    live.pop("deleted_reason", None)
                    live.pop("deleted_at", None)
                    live["updated_at"] = now_iso()
                else:
                    summary["still_unresolved"] += 1
                    continue
            live["apply_resolve_search_attempted"] = True
            unblocked = dict(live)
        if unblocked is not None:
            try:
                from blocked_urls import unblock_job

                unblock_job(unblocked)
            except Exception:
                pass
            summary["restored"] += 1
            summary["restored_by"][bucket] = int(
                summary["restored_by"].get(bucket) or 0
            ) + 1
            summary["upgraded"].append({"id": jid, "url": url, "method": bucket})
            log(f"restored {jid} → {url} ({bucket})")
            if reliable_only:
                done_ids.add(jid)
                progress_dirty = True
        else:
            summary["still_unresolved"] += 1

    if progress_dirty or not reliable_only:
        try:
            _save_progress(
                progress_path,
                done_ids,
                extra={
                    "restored_total": int(summary.get("restored") or 0),
                    "last_batch_considered": len(selected),
                    "last_batch_restored": int(summary.get("restored") or 0),
                    "last_reliable_only": bool(reliable_only),
                },
            )
        except OSError as e:
            summary["errors"].append({"id": "*", "error": f"checkpoint: {e}"[:200]})
    return summary


def jazzhr_slug_from_url(url: str) -> str | None:
    host = _host(url)
    if host.endswith(".applytojob.com"):
        slug = host[: -len(".applytojob.com")]
        return slug or None
    return None


def seed_jazzhr_slug(url: str, registry_path: Path | None = None) -> bool:
    """Optional: remember *.applytojob.com slugs so sibling roles can skip search."""
    slug = jazzhr_slug_from_url(url)
    if not slug:
        return False
    path = Path(registry_path) if registry_path is not None else REGISTRY_FILE
    try:
        reg = json.loads(path.read_text()) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(reg, dict):
        return False
    slugs = reg.setdefault("jazzhr", [])
    if not isinstance(slugs, list):
        slugs = []
        reg["jazzhr"] = slugs
    if slug in slugs:
        return False
    slugs.append(slug)
    try:
        path.write_text(json.dumps(reg, indent=2, sort_keys=True))
    except OSError:
        return False
    return True


def parse_ddg_html(html: str) -> list[str]:
    """Extract result URLs from DuckDuckGo HTML/lite (uddg= unwrap)."""
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        u = unquote(str(raw or "").strip())
        if not u:
            return
        if u.startswith("//"):
            u = "https:" + u
        if not u.startswith("http"):
            return
        host = _host(u)
        if not host or "duckduckgo.com" in host:
            # Maybe a redirect wrapper still carrying uddg=
            qs = parse_qs(urlparse(u).query)
            inner = (qs.get("uddg") or [""])[0]
            if inner:
                _add(inner)
            return
        if "google.com" in host and "/search" in u:
            return
        key = normalize_url(u) or u
        if key in seen:
            return
        seen.add(key)
        found.append(u)

    for m in _UDDG_RE.finditer(html or ""):
        _add(m.group(1))
    for m in _HTTP_RE.finditer(html or ""):
        _add(m.group(0).rstrip(".,;:!?)"))
    return found


def _parse_cse_key_list(raw) -> list[str]:
    """Normalize a GOOGLE_CSE_KEYS value (list or JSON-array string) to strings."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x or "").strip()]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                parsed = json.loads(s)
            except json.JSONDecodeError:
                return []
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x or "").strip()]
            return []
        return [s]
    return []


def _unique_nonempty(*groups: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            s = str(item or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def load_search_keys() -> dict:
    """Env vars win / merge first; fall back to web_keys.json. Missing → empty.

    Expected ``web_keys.json`` CSE shape (placeholders only — never commit secrets)::

        {
          "GOOGLE_CSE_CX": "...",
          "GOOGLE_CSE_KEYS": ["key1", "key2", "key3", "key4"]
        }

    Single ``GOOGLE_CSE_KEY`` / ``google_cse_key`` still works and is merged
    uniquely into the fallback list (env then file). Shared CX via
    ``GOOGLE_CSE_CX`` / ``google_cse_cx``.
    """
    file_keys: dict = {}
    try:
        from india_scrape_common import load_web_keys

        loaded = load_web_keys()
        if isinstance(loaded, dict):
            file_keys = loaded
    except Exception:
        path = ROOT / "web_keys.json"
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict):
                file_keys = loaded
        except (OSError, json.JSONDecodeError, TypeError):
            file_keys = {}

    def pick(*names: str) -> str | None:
        for n in names:
            v = os.environ.get(n) or file_keys.get(n)
            if v:
                return str(v)
        return None

    cse_keys = _unique_nonempty(
        _parse_cse_key_list(os.environ.get("GOOGLE_CSE_KEY")),
        _parse_cse_key_list(os.environ.get("GOOGLE_CSE_KEYS")),
        _parse_cse_key_list(
            file_keys.get("GOOGLE_CSE_KEY") or file_keys.get("google_cse_key")
        ),
        _parse_cse_key_list(
            file_keys.get("GOOGLE_CSE_KEYS") or file_keys.get("google_cse_keys")
        ),
    )

    return {
        "brave": pick("BRAVE_SEARCH_API_KEY", "brave_search_api_key"),
        "google_cse_key": cse_keys[0] if cse_keys else None,
        "google_cse_keys": cse_keys,
        "google_cse_cx": pick("GOOGLE_CSE_CX", "google_cse_cx"),
        "jsearch": pick("JSEARCH_API_KEY", "jsearch_api_key"),
    }


# Process-level CSE quota flag. Set when every configured key returns 403/429/
# quota; available_search_backends then omits google_cse for the rest of the
# process lifetime so we never thrash exhausted keys.
_CSE_QUOTA_EXHAUSTED = False


def available_search_backends(*, include_ddg: bool = True) -> list[dict]:
    keys = load_search_keys()
    out: list[dict] = []
    cse_keys = keys.get("google_cse_keys") or (
        [keys["google_cse_key"]] if keys.get("google_cse_key") else []
    )
    # Prefer CSE early when key(s)+cx are configured (API more reliable than HTML).
    # Once this process marks CSE quota/429 exhausted, omit it entirely so we
    # never burn another request until the interpreter restarts.
    if cse_keys and keys.get("google_cse_cx") and not _CSE_QUOTA_EXHAUSTED:
        out.append({"name": "google_cse"})
    # HTML engines need no API keys. DDG often times out — Brave/Bing first.
    out.append({"name": "brave_html"})
    out.append({"name": "bing"})
    if include_ddg:
        out.append({"name": "duckduckgo"})
    if keys.get("brave"):
        out.append({"name": "brave"})
    if keys.get("jsearch"):
        out.append({"name": "jsearch"})
    return out


# Simple cross-thread throttle for HTML search backends (avoid 429).
_SEARCH_LOCK = threading.Lock()
_LAST_HTML_SEARCH_AT = 0.0
_HTML_SEARCH_MIN_INTERVAL_S = 0.75


def _throttle_html_search() -> None:
    global _LAST_HTML_SEARCH_AT
    with _SEARCH_LOCK:
        now = time.monotonic()
        wait = _HTML_SEARCH_MIN_INTERVAL_S - (now - _LAST_HTML_SEARCH_AT)
        if wait > 0:
            time.sleep(wait)
        _LAST_HTML_SEARCH_AT = time.monotonic()


def _http_get(url: str, headers: dict | None = None, timeout: int = 20) -> str:
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    req = Request(url, headers=hdrs)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def search_duckduckgo(query: str) -> list[str]:
    _throttle_html_search()
    q = urlencode({"q": query})
    for base in (
        "https://html.duckduckgo.com/html/",
        "https://lite.duckduckgo.com/lite/",
    ):
        try:
            html = _http_get(base + "?" + q, timeout=12)
        except (URLError, HTTPError, TimeoutError, OSError, ValueError):
            continue
        urls = parse_ddg_html(html)
        if urls:
            return urls
    return []


def search_bing_html(query: str) -> list[str]:
    """Parse Bing web search HTML for result URLs (no API key)."""
    _throttle_html_search()
    url = "https://www.bing.com/search?" + urlencode({"q": query, "count": "10"})
    try:
        html = _http_get(url, timeout=15)
    except (URLError, HTTPError, TimeoutError, OSError, ValueError):
        return []
    html = (html or "").replace("&amp;", "&")
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        u = str(raw or "").strip()
        if not u:
            return
        host = _host(u)
        if not host or "bing.com" in host or "microsoft.com" in host:
            return
        key = normalize_url(u) or u.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(u)

    # Unwrap bing.com/ck redirect ``u=a1…`` base64 payloads.
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
        elif dec.startswith("/"):
            continue
    # Cite display URLs (often the real host/path with › separators).
    for m in re.finditer(r"<cite[^>]*>(.*?)</cite>", html, re.I | re.S):
        cite = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        cite = cite.replace(" › ", "/").replace("»", "/").strip()
        if cite.startswith("http"):
            _add(cite)
        elif "." in cite and " " not in cite:
            _add("https://" + cite.lstrip("/"))
    for m in _HTTP_RE.finditer(html):
        u = m.group(0).rstrip(".,;:!?)")
        if is_known_ats_url(u) or looks_like_job_apply_url(u):
            _add(u)
    return found


def search_brave_html(query: str) -> list[str]:
    """Parse Brave Search HTML for result URLs (no API key)."""
    _throttle_html_search()
    url = "https://search.brave.com/search?" + urlencode({"q": query})
    try:
        html = _http_get(
            url,
            headers={"Accept": "text/html"},
            timeout=15,
        )
    except HTTPError as e:
        if getattr(e, "code", None) == 429:
            time.sleep(2.0)
            try:
                html = _http_get(
                    url,
                    headers={"Accept": "text/html"},
                    timeout=15,
                )
            except (URLError, HTTPError, TimeoutError, OSError, ValueError):
                return []
        else:
            return []
    except (URLError, TimeoutError, OSError, ValueError):
        return []
    html = (html or "").replace("&amp;", "&")
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        u = str(raw or "").strip()
        if not u.startswith("http"):
            return
        host = _host(u)
        if not host or "brave.com" in host or "search.brave" in host:
            return
        key = normalize_url(u) or u.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(u)

    # Prefer result anchors; Brave uses hashed svelte class names.
    for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"', html, re.I):
        u = m.group(1).strip()
        if is_known_ats_url(u) or looks_like_job_apply_url(u) or is_ats_or_company_apply(u):
            _add(u)
    if not found:
        for m in _HTTP_RE.finditer(html):
            u = m.group(0).rstrip(".,;:!?)")
            if is_known_ats_url(u) or looks_like_job_apply_url(u):
                _add(u)
                if len(found) >= 20:
                    break
    return found


def search_brave(query: str, api_key: str) -> list[str]:
    url = "https://api.search.brave.com/res/v1/web/search?" + urlencode(
        {"q": query, "count": 10}
    )
    try:
        raw = _http_get(
            url,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
        )
        data = json.loads(raw)
    except (URLError, HTTPError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return []
    urls: list[str] = []
    web = (data.get("web") or {}) if isinstance(data, dict) else {}
    for item in web.get("results") or []:
        if isinstance(item, dict) and item.get("url"):
            urls.append(item["url"])
    return urls


def _is_cse_quota_or_rate_error(exc: BaseException) -> bool:
    """True for CSE 403/429 or quota/rate-limit bodies (no secrets logged)."""
    if not isinstance(exc, HTTPError):
        return False
    code = getattr(exc, "code", None)
    if code in (403, 429):
        return True
    body = ""
    try:
        fp = getattr(exc, "fp", None)
        if fp is not None:
            raw = fp.read() if hasattr(fp, "read") else b""
            if isinstance(raw, bytes):
                body = raw.decode("utf-8", errors="replace")
            else:
                body = str(raw or "")
    except Exception:
        body = ""
    blob = body.lower()
    return any(
        marker in blob
        for marker in (
            "dailylimitexceeded",
            "userratelimitexceeded",
            "ratelimitexceeded",
            "quotaexceeded",
            "quota exceeded",
            "rate limit",
        )
    )


def search_google_cse(
    query: str,
    api_key: str | list[str],
    cx: str,
) -> list[str]:
    """Query Google Programmable Search. ``api_key`` may be one key or a list.

    On 403/429/quota for a key, try the next key; stop on the first successful
    HTTP response (including empty result sets). Never logs key material.
    When every key is rate-limited, marks CSE exhausted for this process so
    ``default_search`` falls through to Brave/Bing/… instead of thrashing.
    """
    global _CSE_QUOTA_EXHAUSTED
    if _CSE_QUOTA_EXHAUSTED:
        return []

    if isinstance(api_key, str):
        keys = _unique_nonempty([api_key])
    else:
        keys = _unique_nonempty(list(api_key or []))
    cx = str(cx or "").strip()
    if not keys or not cx:
        return []

    rate_limited = 0
    for key in keys:
        url = "https://www.googleapis.com/customsearch/v1?" + urlencode(
            {"key": key, "cx": cx, "q": query, "num": 10}
        )
        try:
            data = json.loads(_http_get(url))
        except HTTPError as e:
            try:
                if _is_cse_quota_or_rate_error(e):
                    rate_limited += 1
                    continue
                return []
            finally:
                close = getattr(e, "close", None)
                if callable(close):
                    close()
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            return []
        urls: list[str] = []
        for item in (data.get("items") or []) if isinstance(data, dict) else []:
            if isinstance(item, dict) and item.get("link"):
                urls.append(item["link"])
        return urls
    if rate_limited and rate_limited >= len(keys):
        _CSE_QUOTA_EXHAUSTED = True
        log(
            f"google_cse: all {rate_limited} key(s) rate-limited/quota — "
            "falling through to other backends"
        )
    return []


def search_jsearch(query: str, api_key: str) -> list[str]:
    url = "https://jsearch.p.rapidapi.com/search?" + urlencode(
        {"query": query, "page": "1", "num_pages": "1"}
    )
    try:
        data = json.loads(
            _http_get(
                url,
                headers={
                    "X-RapidAPI-Key": api_key,
                    "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
                },
            )
        )
    except (URLError, HTTPError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return []
    urls: list[str] = []
    for item in (data.get("data") or []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        for key in ("job_apply_link", "apply_link", "job_url"):
            u = item.get(key)
            if u:
                urls.append(u)
    return urls


def default_search(query: str) -> list[str]:
    """Try configured backends in order; empty list if all fail (soft).

    Aggregator-only hits (LinkedIn, etc.) do not stop later backends — we keep
    going until a known ATS URL appears or every backend has been tried.
    """
    keys = load_search_keys()
    collected: list[str] = []
    for backend in available_search_backends(include_ddg=True):
        name = backend.get("name")
        urls: list[str] = []
        try:
            if name == "duckduckgo" and collected:
                # DDG often times out; skip when Brave/Bing already returned hits.
                continue
            if name == "bing":
                urls = search_bing_html(query)
            elif name == "brave_html":
                urls = search_brave_html(query)
            elif name == "duckduckgo":
                urls = search_duckduckgo(query)
            elif name == "brave" and keys.get("brave"):
                urls = search_brave(query, keys["brave"])
            elif name == "google_cse" and keys.get("google_cse_cx"):
                if _CSE_QUOTA_EXHAUSTED:
                    continue
                cse_keys = keys.get("google_cse_keys") or (
                    [keys["google_cse_key"]] if keys.get("google_cse_key") else []
                )
                if cse_keys:
                    urls = search_google_cse(query, cse_keys, keys["google_cse_cx"])
            elif name == "jsearch" and keys.get("jsearch"):
                urls = search_jsearch(query, keys["jsearch"])
        except Exception as e:
            log(f"search backend {name} failed: {e}")
            urls = []
        if urls:
            collected.extend(urls)
            if filter_candidate_urls(collected):
                return collected
    return collected


def default_fetch(url: str) -> dict | None:
    """Fetch a candidate ATS page. Never Workday/iCIMS/LinkedIn; no Playwright."""
    if not is_fetchable_ats_url(url):
        return None
    try:
        from extract_job_posting import extract
    except ImportError:
        return None
    try:
        result = extract(url, allow_playwright=False)
    except Exception:
        return None
    if not isinstance(result, dict):
        return None
    return {
        "title": result.get("title") or "",
        "company": result.get("company") or "",
        "description": result.get("description") or "",
    }


def _better(a: dict | None, b: dict) -> dict:
    if a is None:
        return b
    ra, rb = CONF_RANK.get(a.get("confidence"), 9), CONF_RANK.get(b.get("confidence"), 9)
    if rb < ra:
        return b
    if rb == ra and float(b.get("score") or 0) > float(a.get("score") or 0):
        return b
    return a


def try_linkedin_session_resolve(
    job: dict,
    *,
    headless: bool = False,
) -> dict | None:
    """Opt-in LinkedIn profile path for offsite Apply redirect capture.

    Always headed (visible CfT) — ``headless`` is ignored / coerced False.
    HTTP-first; CDP only when ``LINKEDIN_ALLOW_CDP=1``. Returns a result dict
    when the job has a LinkedIn URL; None if not LinkedIn (caller should use
    public search only). Never submits / never CAPTCHA.
    """
    try:
        from linkedin_resolve_apply import (
            job_linkedin_url,
            linkedin_allow_cdp_from_env,
            resolve_linkedin_apply_url,
        )
    except ImportError as e:
        log(f"linkedin session resolve unavailable: {e}")
        return None
    li_url = job_linkedin_url(job)
    if not li_url:
        return None
    try:
        # Force headed: headless Playwright hits LinkedIn authwall and can wipe li_at.
        return resolve_linkedin_apply_url(
            li_url,
            headless=False,
            allow_cdp=linkedin_allow_cdp_from_env(),
        )
    except Exception as e:
        log(f"linkedin session resolve failed: {e}")
        return {
            "confidence": "low",
            "url": None,
            "reason": "browser_error",
            "message": str(e)[:300],
            "method": "linkedin_session",
            "score": 0.0,
        }


def resolve_job(
    job: dict,
    *,
    search_fn: Callable[[str], list[str]] | None = None,
    fetch_fn: Callable[[str], dict | None] | None = None,
    board_search_fn: Callable[[str, str], list[str]] | None = None,
    write: bool = False,
    resumes_dir: Path | None = None,
    delay_s: float = 0.0,
    linkedin_session: bool = True,
) -> dict:
    """Search + score candidates. Mutates job only when write=True."""

    def _done(result: dict) -> dict:
        if write:
            conf = str(result.get("confidence") or "low")
            if conf in ("high", "medium"):
                apply_scored_resolution(job, result)
                if conf == "high" and result.get("url"):
                    try:
                        seed_jazzhr_slug(result["url"])
                    except Exception:
                        pass
            set_apply_resolve_fields(job, result)
        return result

    if is_easy_apply_job(job):
        return _done({"confidence": "low", "url": None, "reason": "easy_apply", "score": 0.0})
    if not needs_apply_resolution(job):
        return _done({"confidence": "low", "url": None, "reason": "not_needed", "score": 0.0})

    session_result: dict | None = None
    if linkedin_session:
        session_result = try_linkedin_session_resolve(job)
        if session_result:
            conf = str(session_result.get("confidence") or "low")
            reason = str(session_result.get("reason") or "")
            # Terminal: high upgrade, Easy Apply, CAPTCHA, profile lock
            if conf == "high" and session_result.get("url"):
                session_result.setdefault("reason", "linkedin_external_redirect")
                return _done(session_result)
            if reason in _LINKEDIN_TERMINAL_NO_SEARCH:
                return _done(session_result)
            # LinkedIn HTTP/session miss (http_error, no_external, failed, …)
            # → fall through to public company+title search before prune.

    search_fn = search_fn or default_search
    fetch_fn = fetch_fn or default_fetch
    board_search_fn = board_search_fn or search_ats_boards
    loc = (
        job.get("location")
        or job.get("job_location")
        or job.get("city")
        or ""
    )
    company = str(job.get("company") or "").strip()
    title = str(job.get("title") or "").strip()
    queries = build_search_queries(
        company,
        title,
        location=str(loc) if loc else None,
    )
    hits: list[str] = []
    seen_q: set[str] = set()
    search_attempted = False

    # Prefer direct ATS board APIs before HTML SERPs — Brave/Bing often 429 or
    # return irrelevant hosts while Ashby/GH/Lever board JSON has the job.
    board_hit_set: set[str] = set()
    if company and title:
        try:
            board_hits = board_search_fn(company, title) or []
        except Exception as e:
            log(f"ats board search failed: {e}")
            board_hits = []
        if board_hits:
            hits.extend(board_hits)
            search_attempted = True
            for u in board_hits:
                key = normalize_url(u) or u.lower()
                board_hit_set.add(key)

    for i, q in enumerate(queries):
        if q in seen_q:
            continue
        seen_q.add(q)
        # Skip slow HTML search when board API already produced ATS candidates.
        if filter_candidate_urls(hits):
            break
        search_attempted = True
        if delay_s and i:
            time.sleep(delay_s)
        try:
            hits.extend(search_fn(q) or [])
        except Exception as e:
            log(f"search failed for {q!r}: {e}")
        if filter_candidate_urls(hits):
            break
    # Empty query list still counts as attempted when we reached this path
    # (company+title missing → cannot search; stamp so we don't spin forever).
    if not queries and not hits:
        search_attempted = True

    def _with_search(result: dict) -> dict:
        if search_attempted:
            result = dict(result)
            result["search_attempted"] = True
        return result

    candidates = prefer_company_relevant_urls(filter_candidate_urls(hits), company)
    if not candidates:
        # Prefer LinkedIn session's not_logged_in / no_external message over bare
        # no_ats_host when we already tried the profile path — but only after
        # search was attempted (so prune gate sees search_attempted).
        if session_result and session_result.get("reason") in (
            "not_logged_in",
            "no_external_apply",
            "unfetchable_ats",
            "browser_error",
            "http_error",
            "failed",
            "authwall",
        ):
            return _done(_with_search(session_result))
        return _done(
            _with_search(
                {"confidence": "low", "url": None, "reason": "no_ats_host", "score": 0.0}
            )
        )

    in_hand = description_text(job, resumes_dir=resumes_dir) or (
        job.get("job_description") or job.get("description") or ""
    )
    scoring_job = dict(job)
    if in_hand:
        scoring_job["job_description"] = in_hand

    best: dict | None = None
    for url in candidates:
        try:
            page = fetch_fn(url)
        except Exception:
            page = None
        scored = score_candidate(scoring_job, url, page)
        best = _better(best, scored)
        if best and best.get("confidence") == "high":
            break

    if best is None:
        return _done(
            _with_search(
                {"confidence": "low", "url": None, "reason": "no_ats_host", "score": 0.0}
            )
        )
    if not best.get("reason"):
        best_url_key = normalize_url(str(best.get("url") or "")) or str(
            best.get("url") or ""
        ).lower()
        if best_url_key and best_url_key in board_hit_set:
            best["reason"] = "ats_board_api"
            best["method"] = "ats_board_api"
            # Board APIs already title-filtered the posting against a company
            # registry/slug probe. Promote low/medium → high when the URL is
            # job-shaped — Workable/SmartRecruiters/Ashby hosts often miss the
            # stricter company_host gate without a fetchable JD overlap.
            if (
                best.get("confidence") in ("low", "medium")
                and looks_like_job_apply_url(str(best.get("url") or ""))
            ):
                best = dict(best)
                best["confidence"] = "high"
        elif best.get("confidence") == "low":
            best["reason"] = "low_confidence"
        elif best.get("confidence") == "medium":
            best["reason"] = "medium_no_overwrite"
        else:
            best["reason"] = "upgraded"
    return _done(_with_search(best))


def _load_progress(progress_path: Path | None) -> set[str]:
    if not progress_path:
        return set()
    try:
        data = json.loads(Path(progress_path).read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return set()
    ids = data.get("done_ids") if isinstance(data, dict) else None
    if not isinstance(ids, list):
        return set()
    return {str(x) for x in ids if x}


def _save_progress(progress_path: Path, done_ids: set[str], extra: dict | None = None) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"done_ids": sorted(done_ids), "updated_at": now_iso()}
    if extra:
        payload.update(extra)
    progress_path.write_text(json.dumps(payload, indent=2))


def select_jobs_for_resolution(
    jobs: list,
    progress_path: Path | str | None = None,
) -> list[dict]:
    done = _load_progress(Path(progress_path) if progress_path else None)
    out: list[dict] = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        jid = str(job.get("id") or "")
        if jid and jid in done:
            continue
        if needs_apply_resolution(job):
            out.append(job)
    return out


def persist_job_resolution(job_id: str, scored: dict) -> dict | None:
    """Apply a scored result (+ resolve status fields) onto jobs.json under lock.

    Always stamps ``apply_resolve_*`` (success or failure). High/medium still
    update apply_url / apply_url_resolution. Idempotent: unchanged meta skips
    write (jobs_lock compares before/after).

    When the result carries ``date_posted`` / ``date_posted_fallback`` (LinkedIn
    HTTP HTML parse), merge them with exact-beats-approx rules.

    After an unresolved stamp (failed / no_external / easy_apply) on a
    discovered job still on LinkedIn/aggregator, soft-deletes
    (``deleted_reason=unresolved_apply_url``), stamps the Unresolved URL chip,
    and URL-tombstones outside the jobs write lock — same pattern as discovery
    prune. Successful ATS upgrades clear the chip.
    """
    pruned_snap: dict | None = None
    with locked_jobs_for_write() as data:
        job = next((j for j in data.get("jobs") or [] if j.get("id") == job_id), None)
        if job is None:
            return None
        before_url = job.get("apply_url")
        before_res = job.get("apply_url_resolution")
        before_status = job.get("status")
        before_tag = bool(job.get("unresolved_apply_url"))
        before_posted = (
            job.get("date_posted"),
            job.get("date_posted_fallback"),
            job.get("date_posted_source"),
        )
        apply_scored_resolution(job, scored)
        meta_changed = set_apply_resolve_fields(job, scored)
        posted_changed = False
        exact = (scored or {}).get("date_posted")
        approx = (scored or {}).get("date_posted_fallback")
        if exact or approx:
            try:
                from posted_date import apply_posted_dates

                posted_changed = apply_posted_dates(
                    job,
                    exact if exact else None,
                    approx if approx else None,
                    source=str((scored or {}).get("date_posted_source") or "linkedin_http"),
                )
            except Exception:
                posted_changed = False
        url_changed = job.get("apply_url") != before_url
        res_changed = job.get("apply_url_resolution") != before_res
        after_posted = (
            job.get("date_posted"),
            job.get("date_posted_fallback"),
            job.get("date_posted_source"),
        )
        if posted_changed or after_posted != before_posted:
            posted_changed = True
        # Clear chip when apply_url upgraded to known ATS / company careers.
        if is_resolved_apply_url(str(job.get("apply_url") or "").strip()):
            stamp_unresolved_apply_url_tag(job, on=False)
        pruned = tombstone_unresolved_apply_url(job)
        if pruned:
            pruned_snap = _block_snap_for_job(job)
        status_changed = job.get("status") != before_status
        tag_changed = bool(job.get("unresolved_apply_url")) != before_tag
        if (
            meta_changed
            or url_changed
            or res_changed
            or posted_changed
            or status_changed
            or tag_changed
        ):
            job["updated_at"] = now_iso()
        if scored.get("confidence") == "high" and scored.get("url"):
            try:
                seed_jazzhr_slug(scored["url"])
            except Exception:
                pass
        out = dict(job)
    if pruned_snap:
        _tombstone_url_block(pruned_snap)
    return out


def resolve_job_id(
    job_id: str,
    *,
    write: bool = False,
    search_fn=None,
    fetch_fn=None,
    delay_s: float = 0.0,
    linkedin_session: bool = True,
) -> dict:
    with locked_jobs_for_read() as data:
        job = next((j for j in data.get("jobs") or [] if j.get("id") == job_id), None)
        if job is None:
            return {"ok": False, "error": f"no job found with id {job_id!r}"}
        snapshot = dict(job)
    result = resolve_job(
        snapshot,
        search_fn=search_fn,
        fetch_fn=fetch_fn,
        write=write,
        delay_s=delay_s,
        linkedin_session=linkedin_session,
    )
    persisted = None
    if write:
        # Persist success *and* failure/skip so the dashboard can show reason.
        persisted = persist_job_resolution(job_id, result)
    src = persisted or snapshot
    if persisted:
        fields = {
            "apply_resolve_status": src.get("apply_resolve_status"),
            "apply_resolve_reason": src.get("apply_resolve_reason"),
            "apply_resolve_at": src.get("apply_resolve_at"),
            "apply_resolve_message": src.get("apply_resolve_message"),
        }
    else:
        fields = compact_apply_resolve_fields(result)
    return {
        "ok": True,
        "id": job_id,
        "confidence": result.get("confidence"),
        "url": result.get("url"),
        "reason": result.get("reason") or fields.get("apply_resolve_reason"),
        "score": result.get("score"),
        "message": result.get("message") or fields.get("apply_resolve_message"),
        "method": result.get("method"),
        "captcha": result.get("captcha"),
        "apply_url": src.get("apply_url") if write else snapshot.get("apply_url"),
        "apply_resolve_status": fields.get("apply_resolve_status"),
        "apply_resolve_reason": fields.get("apply_resolve_reason"),
        "apply_resolve_at": fields.get("apply_resolve_at"),
        "apply_resolve_message": fields.get("apply_resolve_message"),
        "dry_run": not write,
    }


def resolve_discovery_apply_urls(
    *,
    since_iso: str | None = None,
    job_ids: set[str] | None = None,
    limit: int | None = None,
    write: bool = True,
    concurrency: int | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    abort_cb: Callable[[], bool] | None = None,
    http_many_fn: Callable[..., list[dict]] | None = None,
    resolve_job_fn: Callable[..., dict] | None = None,
) -> dict:
    """Post-discover apply-URL resolve: LinkedIn HTTP batch, then other aggregators.

    HTTP-only for LinkedIn (``resolve_linkedin_http_many``). No CDP unless the
    caller later uses Resolve ATS with ``LINKEDIN_ALLOW_CDP=1``. Other
    aggregators use public search only (``linkedin_session=False``).

    Stamps ``apply_resolve_*`` via ``persist_job_resolution`` when write=True.
    ``progress_cb(done, total)`` fires as each job finishes. ``abort_cb`` may
    stop between batches. ``limit`` caps backlog/continuous batches.
    """
    workers = int(concurrency) if concurrency is not None else DISCOVERY_RESOLVE_HTTP_CONCURRENCY
    try:
        from linkedin_resolve_apply import (
            job_linkedin_url,
            resolve_linkedin_http_many,
            clamp_http_concurrency,
        )
        workers = clamp_http_concurrency(workers)
    except ImportError:
        job_linkedin_url = None  # type: ignore[assignment]
        resolve_linkedin_http_many = None  # type: ignore[assignment]

    with locked_jobs_for_read() as data:
        jobs = list(data.get("jobs") or [])
    # Full jobs list for sibling lookups (not just the resolve batch).
    all_jobs = jobs
    selected = select_jobs_for_discovery_resolve(
        jobs, since_iso=since_iso, job_ids=job_ids, limit=limit,
    )
    summary: dict = {
        "considered": len(selected),
        "linkedin": 0,
        "other": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "upgraded": [],
        "errors": [],
        "aborted": False,
        "dry_run": not write,
    }
    if not selected:
        if progress_cb:
            progress_cb(0, 0)
        return summary

    linkedin_pairs: list[tuple[str, str]] = []
    other_jobs: list[dict] = []
    for job in selected:
        jid = str(job.get("id") or "")
        li_url = None
        if job_linkedin_url is not None:
            try:
                li_url = job_linkedin_url(job)
            except Exception:
                li_url = None
        if not li_url and apply_url_still_linkedin(job):
            li_url = str(job.get("apply_url") or "").strip() or None
        if li_url and jid:
            linkedin_pairs.append((jid, li_url))
        elif jid:
            other_jobs.append(job)

    summary["linkedin"] = len(linkedin_pairs)
    summary["other"] = len(other_jobs)
    total = len(linkedin_pairs) + len(other_jobs)
    done = 0
    if progress_cb:
        progress_cb(done, total)

    def _bump(result: dict | None, jid: str) -> None:
        nonlocal done
        conf = str((result or {}).get("confidence") or "low")
        summary[conf] = summary.get(conf, 0) + 1
        if conf == "high" and (result or {}).get("url"):
            summary["upgraded"].append({"id": jid, "url": result.get("url")})
        done += 1
        if progress_cb:
            progress_cb(done, total)

    # LinkedIn HTTP hits that did not upgrade — fall back to public search before
    # stamping failed/no_external (Unresolved URL prune).
    linkedin_search_fallback: list[dict] = []
    jobs_by_id = {
        str(j.get("id") or ""): j
        for j in selected
        if isinstance(j, dict) and j.get("id")
    }

    http_many = http_many_fn or resolve_linkedin_http_many
    if linkedin_pairs and http_many is not None:
        if abort_cb and abort_cb():
            summary["aborted"] = True
            return summary
        try:
            results = http_many(linkedin_pairs, concurrency=workers)
        except Exception as e:
            summary["errors"].append({"id": "*", "error": f"http_many: {e}"[:200]})
            results = []
            for jid, _u in linkedin_pairs:
                results.append({
                    "id": jid,
                    "confidence": "low",
                    "url": None,
                    "reason": "browser_error",
                    "message": str(e)[:300],
                    "method": "linkedin_http",
                    "score": 0.0,
                })
        by_id = {str(r.get("id") or ""): r for r in (results or []) if isinstance(r, dict)}
        for jid, _u in linkedin_pairs:
            if abort_cb and abort_cb():
                summary["aborted"] = True
                break
            result = by_id.get(jid) or {
                "confidence": "low",
                "url": None,
                "reason": "failed",
                "method": "linkedin_http",
                "score": 0.0,
            }
            conf = str(result.get("confidence") or "low")
            reason = str(result.get("reason") or "")
            if conf == "high" and result.get("url"):
                try:
                    if write:
                        persist_job_resolution(jid, result)
                except Exception as e:
                    summary["errors"].append({"id": jid, "error": str(e)[:200]})
                _bump(result, jid)
                continue
            if reason in _LINKEDIN_TERMINAL_NO_SEARCH:
                try:
                    if write:
                        persist_job_resolution(jid, result)
                except Exception as e:
                    summary["errors"].append({"id": jid, "error": str(e)[:200]})
                _bump(result, jid)
                continue
            # Miss → public company+title search before fail/prune.
            job_snap = jobs_by_id.get(jid)
            if job_snap:
                linkedin_search_fallback.append(dict(job_snap))
            else:
                try:
                    if write:
                        persist_job_resolution(jid, result)
                except Exception as e:
                    summary["errors"].append({"id": jid, "error": str(e)[:200]})
                _bump(result, jid)
    elif linkedin_pairs and http_many is None:
        for jid, _u in linkedin_pairs:
            job_snap = jobs_by_id.get(jid)
            if job_snap:
                linkedin_search_fallback.append(dict(job_snap))
            else:
                summary["errors"].append({"id": jid, "error": "linkedin_resolve_apply unavailable"})
                _bump({"confidence": "low", "reason": "browser_error"}, jid)

    if summary.get("aborted"):
        return summary

    resolve_one = resolve_job_fn or resolve_job
    search_jobs = list(linkedin_search_fallback) + list(other_jobs)
    for job in search_jobs:
        if abort_cb and abort_cb():
            summary["aborted"] = True
            break
        jid = str(job.get("id") or "")
        try:
            # Prefer existing ATS URL / same company+title sibling before board+search.
            result = try_existing_or_sibling_apply_url(job, all_jobs)
            if result is None:
                result = resolve_one(
                    dict(job),
                    write=False,
                    linkedin_session=False,
                )
            if write:
                persist_job_resolution(jid, result)
        except Exception as e:
            result = {
                "confidence": "low",
                "url": None,
                "reason": "failed",
                "message": str(e)[:300],
                "score": 0.0,
            }
            summary["errors"].append({"id": jid, "error": str(e)[:200]})
            if write:
                try:
                    persist_job_resolution(jid, result)
                except Exception:
                    pass
        _bump(result, jid)
    return summary


def resolve_all(
    *,
    write: bool = False,
    limit: int | None = None,
    delay_s: float = DEFAULT_DELAY_S,
    progress_path: Path | None = None,
    search_fn=None,
    fetch_fn=None,
    linkedin_session: bool = True,
) -> dict:
    progress_path = progress_path or PROGRESS_FILE
    with locked_jobs_for_read() as data:
        jobs = list(data.get("jobs") or [])
    selected = select_jobs_for_resolution(jobs, progress_path=progress_path)
    if limit is not None:
        selected = selected[: max(0, int(limit))]
    done = _load_progress(progress_path)
    summary = {
        "considered": len(selected),
        "high": 0,
        "medium": 0,
        "low": 0,
        "upgraded": [],
        "errors": [],
        "dry_run": not write,
    }
    for i, job in enumerate(selected):
        jid = str(job.get("id") or "")
        log(
            f"{i + 1}/{len(selected)} {jid} {job.get('company')} / {job.get('title')}"
        )
        try:
            result = resolve_job(
                dict(job),
                search_fn=search_fn,
                fetch_fn=fetch_fn,
                write=False,
                delay_s=0.0,
                linkedin_session=linkedin_session,
            )
            conf = result.get("confidence") or "low"
            summary[conf] = summary.get(conf, 0) + 1
            if write:
                persist_job_resolution(jid, result)
            if conf == "high" and result.get("url"):
                summary["upgraded"].append({"id": jid, "url": result["url"]})
                log(f"  HIGH → {result['url']}")
            elif conf == "medium":
                log(f"  MEDIUM (not overwriting) {result.get('url')}")
            else:
                log(f"  LOW ({result.get('reason')})")
        except Exception as e:
            summary["errors"].append({"id": jid, "error": str(e)[:200]})
            log(f"  error: {e}")
        if write and jid:
            done.add(jid)
            _save_progress(
                progress_path,
                done,
                extra={"high": summary["high"], "medium": summary["medium"], "low": summary["low"]},
            )
        if delay_s and i + 1 < len(selected):
            time.sleep(delay_s)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("job_id", nargs="?", help="Resolve a single job id")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Walk LinkedIn/aggregator open jobs (skip already-ATS / Easy Apply)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist high-confidence apply_url upgrades (default is dry-run)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max jobs for --all")
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_S,
        help=f"Seconds between jobs (default {DEFAULT_DELAY_S})",
    )
    parser.add_argument(
        "--reset-progress",
        action="store_true",
        help="Ignore logs/resolve_apply_urls_progress.json and start fresh",
    )
    parser.add_argument(
        "--no-linkedin-session",
        action="store_true",
        help="Skip authenticated LinkedIn profile redirect capture (public search only)",
    )
    parser.add_argument(
        "--reresolve-deleted",
        action="store_true",
        help="Public-search retry for pruned unresolved_apply_url jobs (checkpointed)",
    )
    parser.add_argument(
        "--include-linkedin",
        action="store_true",
        default=True,
        help="With --reresolve-deleted, include LinkedIn-sourced rows (default: on)",
    )
    parser.add_argument(
        "--no-linkedin",
        action="store_true",
        help="With --reresolve-deleted, skip LinkedIn-sourced rows",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=RERESOLVE_DELETED_DEFAULT_WORKERS,
        help=f"Parallel public-search workers for --reresolve-deleted (default {RERESOLVE_DELETED_DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--reliable-only",
        action="store_true",
        help=(
            "With --reresolve-deleted: sibling + ATS board API only "
            "(skip CSE/HTML search; ignore search checkpoint for selection)"
        ),
    )
    args = parser.parse_args(argv)

    if args.reset_progress:
        for path in (PROGRESS_FILE, RERESOLVE_PROGRESS_FILE):
            if path.is_file():
                path.write_text(json.dumps({"done_ids": [], "updated_at": now_iso()}, indent=2))

    if not args.job_id and not args.all and not args.reresolve_deleted:
        parser.error("pass JOB_ID, --all, or --reresolve-deleted")

    backends = [b["name"] for b in available_search_backends(include_ddg=True)]
    log(f"search backends: {', '.join(backends) or '(none — fail soft)'}")
    if args.write:
        log("WRITE mode: high-confidence apply_url upgrades will be persisted")
    else:
        log("dry-run (pass --write to persist)")
    use_li = not args.no_linkedin_session

    if args.reresolve_deleted:
        include_li = not args.no_linkedin
        lim = args.limit if args.limit is not None else RERESOLVE_DELETED_DEFAULT_LIMIT
        # limit 0 / None → drain checkpointed chunks until no candidates remain.
        drain = lim is None or int(lim) <= 0
        totals = {
            "considered": 0,
            "restored": 0,
            "still_unresolved": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "upgraded": [],
            "errors": [],
            "batches": 0,
            "dry_run": not args.write,
            "include_linkedin": include_li,
            "workers": args.workers,
            "reliable_only": bool(args.reliable_only),
            "restored_by": {
                "sibling": 0,
                "existing": 0,
                "ats_board_api": 0,
                "public_search": 0,
            },
        }
        session_skip: set[str] = set()
        while True:
            summary = reresolve_unresolved_deleted(
                limit=0 if drain else lim,
                write=args.write,
                include_linkedin=include_li,
                job_ids={args.job_id} if args.job_id else None,
                workers=args.workers,
                reset_progress=False,
                reliable_only=bool(args.reliable_only),
                extra_skip_ids=session_skip if args.reliable_only else None,
            )
            totals["batches"] += 1
            for k in ("considered", "restored", "still_unresolved", "high", "medium", "low"):
                totals[k] = int(totals.get(k) or 0) + int(summary.get(k) or 0)
            totals["upgraded"].extend(summary.get("upgraded") or [])
            totals["errors"].extend(summary.get("errors") or [])
            for bk, bv in (summary.get("restored_by") or {}).items():
                totals["restored_by"][bk] = int(
                    totals["restored_by"].get(bk) or 0
                ) + int(bv or 0)
            totals["checkpoint"] = summary.get("checkpoint")
            for cid in summary.get("considered_ids") or []:
                if cid:
                    session_skip.add(str(cid))
            log(
                f"reresolve batch {totals['batches']}: "
                f"considered={summary.get('considered')} "
                f"restored={summary.get('restored')} "
                f"still={summary.get('still_unresolved')} "
                f"by={summary.get('restored_by')}"
            )
            if args.job_id or not drain:
                break
            if int(summary.get("considered") or 0) <= 0:
                break
            # Reliable-only over the full unresolved set: stop when a full
            # chunk yields zero restores (gains flattened) after at least one
            # non-empty pass — but keep chunking via session_skip until empty.
            if args.reliable_only and int(summary.get("considered") or 0) <= 0:
                break
        print(json.dumps(totals, indent=2))
        return 0

    if args.job_id:
        out = resolve_job_id(
            args.job_id,
            write=args.write,
            delay_s=args.delay,
            linkedin_session=use_li,
        )
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1

    summary = resolve_all(
        write=args.write,
        limit=args.limit,
        delay_s=args.delay,
        linkedin_session=use_li,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
