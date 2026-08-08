"""Shared helpers for the India Wave-A scrapers (Internshala / Hirist /
Cutshort / Adzuna).

These scrapers only READ public listing pages / official APIs at personal,
low volume — polite rate limits, a plain descriptive User-Agent, and no
login / CAPTCHA solving (see PLAYBOOK). They normalize to the same listing
shape every other scraper writes so dedup_listings.py and
write_discovered_jobs.py can consume them unchanged:

    title, company, site, job_url, job_url_direct, description,
    date_posted, job_type, location, search_term

The region gate in discovery_filters.py keeps their India roles only when
the India region is enabled; these scrapers deliberately do NOT re-implement
the region filter (single source of truth).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent

# Plain, honest UA — we are a personal agent, not pretending to be a browser
# farm. Kept identical to the ATS scraper's UA for consistency.
USER_AGENT = "Mozilla/5.0 (compatible; job-hunter-agent/1.0)"

# Software / data categories only (keeps volume + relevance sane). dedup's
# RELEVANT_KEYWORDS still filter titles downstream; these just scope queries.
SEARCH_TERMS = [
    "machine learning",
    "data scientist",
    "data engineer",
    "data analyst",
    "ai engineer",
    "software engineer",
    "backend developer",
    "python developer",
]


def log(msg: str, *, err: bool = False) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr if err else sys.stdout, flush=True)


def polite_sleep(seconds: float) -> None:
    """Small delay between requests to stay well under any rate limit."""
    if seconds > 0:
        time.sleep(seconds)


def fetch_json(url: str, *, headers: dict | None = None, timeout: int = 20,
               method: str = "GET", body: bytes | None = None):
    """GET/POST JSON; returns parsed data or None on any failure (no raise)."""
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = Request(url, data=body, method=method, headers=hdrs)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        log(f"warn: fetch_json failed for {url}: {exc}", err=True)
        return None


def fetch_html(url: str, *, headers: dict | None = None, timeout: int = 20) -> str | None:
    """Plain HTML GET; returns text or None on any failure (no raise)."""
    hdrs = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        log(f"warn: fetch_html failed for {url}: {exc}", err=True)
        return None


def write_listings(out_path: Path, listings: list[dict]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(listings, indent=2, default=str))


def dedup_by_url(listings: list[dict]) -> list[dict]:
    """Drop rows sharing a job_url (first wins)."""
    seen: set[str] = set()
    out: list[dict] = []
    for item in listings:
        url = item.get("job_url") or ""
        key = url or f"{item.get('company')}|{item.get('title')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def load_web_keys() -> dict:
    """Load optional API keys from an ignored secrets file (web_keys.json).

    The file is git-ignored and holds provider keys (e.g. Adzuna). Missing
    file / bad JSON → empty dict so callers can skip cleanly. Env vars take
    precedence over this file (handled by each caller).
    """
    path = ROOT / "web_keys.json"
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
