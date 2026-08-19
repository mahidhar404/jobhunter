#!/usr/bin/env python3
"""Resolve aggregator (LinkedIn/Indeed/…) apply URLs to the company ATS page.

When a job's apply_url is LinkedIn (signin wall / Easy Apply hide the offsite
link), search the public web for the same posting and upgrade apply_url only
at high confidence (title + company alias + distinctive JD overlap). LinkedIn
is kept on job_url / source_url / alternate_urls (enrich_listing_urls).

Never: submit applications, solve CAPTCHA, scrape authenticated LinkedIn,
bypass Workday/iCIMS/Akamai, or use applicant PII.

Search backends (first that returns hits; fail soft):
  1. DuckDuckGo HTML (default, no key)
  2. Brave Search API if BRAVE_SEARCH_API_KEY is set
  3. Google CSE if GOOGLE_CSE_KEY + GOOGLE_CSE_CX are set
  4. JSearch if JSEARCH_API_KEY is set

Keys may also live in gitignored web_keys.json (Adzuna pattern). Never scrape
google.com HTML.

Usage:
  python3 scripts/resolve_apply_urls.py JOB_ID              # dry-run one
  python3 scripts/resolve_apply_urls.py --all               # dry-run LinkedIn/aggregator jobs
  python3 scripts/resolve_apply_urls.py --all --write       # persist high-confidence upgrades
  python3 scripts/resolve_apply_urls.py JOB_ID --write
  python3 scripts/resolve_apply_urls.py --all --limit 20 --delay 2.5

Dry-run is the default. --write records medium-confidence candidates without
overwriting apply_url. Easy Apply / no ATS host / Workday / iCIMS stay as-is.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
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
    is_known_ats_url,
    normalize_url,
)
from jd_fingerprint import description_text, normalize_jd_text  # noqa: E402
from jobs_lock import locked_jobs_for_read, locked_jobs_for_write  # noqa: E402
from text_normalize import normalize_company, normalize_title  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"
PROGRESS_FILE = LOGS_DIR / "resolve_apply_urls_progress.json"
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


def is_fetchable_ats_url(url) -> bool:
    """Known ATS host we can fetch without bypassing Akamai/CAPTCHA."""
    return bool(is_known_ats_url(url) and not is_unfetchable_ats(url))


def filter_candidate_urls(urls) -> list[str]:
    """Keep unique fetchable ATS apply URLs; drop aggregators and Workday/iCIMS."""
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


def build_search_queries(company: str, title: str) -> list[str]:
    """Company + title queries. Do not rely on guessing {company}.applytojob.com."""
    company = str(company or "").strip()
    title = str(title or "").strip()
    if not company or not title:
        return []
    return [
        f'"{title}" "{company}" apply',
        (
            f'"{title}" {company} '
            "(greenhouse OR lever OR ashby OR applytojob OR smartrecruiters "
            "OR workable OR bamboohr)"
        ),
        f"{title} {company} careers apply",
    ]


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


def company_matches_url(company: str, url: str) -> bool:
    host = _host(url)
    if not host:
        return False
    slug = host.split(".")[0]
    if companies_match(company, slug):
        return True
    try:
        path = unquote(urlparse(url).path or "")
    except ValueError:
        path = ""
    for part in [p for p in path.split("/") if p][:2]:
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
    if is_known_ats_url(apply):
        return False
    res = job.get("apply_url_resolution") if isinstance(job.get("apply_url_resolution"), dict) else {}
    if str(res.get("confidence") or "") == "high" and is_known_ats_url(res.get("url") or apply):
        return False
    primary = apply or job_url
    if not primary:
        return False
    return is_aggregator_url(primary) or is_aggregator_url(job_url)


def score_candidate(job: dict, url: str, page: dict | None) -> dict:
    page = page or {}
    title_ok = titles_match(job.get("title"), page.get("title") or "") or titles_match(
        job.get("title"), url
    )
    company_ok = companies_match(job.get("company") or "", page.get("company") or "") or (
        company_matches_url(job.get("company") or "", url)
    )
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
    elif not title_ok:
        conf = "low"
    elif overlap >= HIGH_OVERLAP:
        conf = "high"
    else:
        conf = "medium"

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
    """High → overwrite apply_url. Medium → record candidate only. Low → no-op."""
    conf = str((scored or {}).get("confidence") or "low")
    if conf == "low":
        return job
    url = str((scored or {}).get("url") or "").strip()
    if conf == "high" and url:
        merge_resolved_apply(job, url)
    job["apply_url_resolution"] = {
        "confidence": conf,
        "url": url or None,
        "score": (scored or {}).get("score"),
        "resolved_at": now_iso(),
    }
    return job


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


def load_search_keys() -> dict:
    """Env vars win; fall back to web_keys.json. Missing → empty."""
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

    return {
        "brave": pick("BRAVE_SEARCH_API_KEY", "brave_search_api_key"),
        "google_cse_key": pick("GOOGLE_CSE_KEY", "google_cse_key"),
        "google_cse_cx": pick("GOOGLE_CSE_CX", "google_cse_cx"),
        "jsearch": pick("JSEARCH_API_KEY", "jsearch_api_key"),
    }


def available_search_backends(*, include_ddg: bool = True) -> list[dict]:
    keys = load_search_keys()
    out: list[dict] = []
    if include_ddg:
        out.append({"name": "duckduckgo"})
    if keys.get("brave"):
        out.append({"name": "brave"})
    if keys.get("google_cse_key") and keys.get("google_cse_cx"):
        out.append({"name": "google_cse"})
    if keys.get("jsearch"):
        out.append({"name": "jsearch"})
    return out


def _http_get(url: str, headers: dict | None = None, timeout: int = 20) -> str:
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    req = Request(url, headers=hdrs)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def search_duckduckgo(query: str) -> list[str]:
    q = urlencode({"q": query})
    for base in (
        "https://html.duckduckgo.com/html/",
        "https://lite.duckduckgo.com/lite/",
    ):
        try:
            html = _http_get(base + "?" + q)
        except (URLError, HTTPError, TimeoutError, OSError, ValueError):
            continue
        urls = parse_ddg_html(html)
        if urls:
            return urls
    return []


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


def search_google_cse(query: str, api_key: str, cx: str) -> list[str]:
    url = "https://www.googleapis.com/customsearch/v1?" + urlencode(
        {"key": api_key, "cx": cx, "q": query, "num": 10}
    )
    try:
        data = json.loads(_http_get(url))
    except (URLError, HTTPError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return []
    urls: list[str] = []
    for item in (data.get("items") or []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get("link"):
            urls.append(item["link"])
    return urls


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
            if name == "duckduckgo":
                urls = search_duckduckgo(query)
            elif name == "brave" and keys.get("brave"):
                urls = search_brave(query, keys["brave"])
            elif name == "google_cse" and keys.get("google_cse_key") and keys.get("google_cse_cx"):
                urls = search_google_cse(query, keys["google_cse_key"], keys["google_cse_cx"])
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


def resolve_job(
    job: dict,
    *,
    search_fn: Callable[[str], list[str]] | None = None,
    fetch_fn: Callable[[str], dict | None] | None = None,
    write: bool = False,
    resumes_dir: Path | None = None,
    delay_s: float = 0.0,
) -> dict:
    """Search + score candidates. Mutates job only when write=True."""
    if is_easy_apply_job(job):
        return {"confidence": "low", "url": None, "reason": "easy_apply", "score": 0.0}
    if not needs_apply_resolution(job):
        return {"confidence": "low", "url": None, "reason": "not_needed", "score": 0.0}

    search_fn = search_fn or default_search
    fetch_fn = fetch_fn or default_fetch
    queries = build_search_queries(job.get("company") or "", job.get("title") or "")
    hits: list[str] = []
    seen_q: set[str] = set()
    for i, q in enumerate(queries):
        if q in seen_q:
            continue
        seen_q.add(q)
        if delay_s and i:
            time.sleep(delay_s)
        try:
            hits.extend(search_fn(q) or [])
        except Exception as e:
            log(f"search failed for {q!r}: {e}")
        if filter_candidate_urls(hits):
            break
    candidates = filter_candidate_urls(hits)
    if not candidates:
        return {"confidence": "low", "url": None, "reason": "no_ats_host", "score": 0.0}

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
        return {"confidence": "low", "url": None, "reason": "no_ats_host", "score": 0.0}
    if write:
        apply_scored_resolution(job, best)
        if best.get("confidence") == "high" and best.get("url"):
            try:
                seed_jazzhr_slug(best["url"])
            except Exception:
                pass
    if not best.get("reason"):
        if best.get("confidence") == "low":
            best["reason"] = "low_confidence"
        elif best.get("confidence") == "medium":
            best["reason"] = "medium_no_overwrite"
        else:
            best["reason"] = "upgraded"
    return best


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
    """Apply a scored result onto jobs.json under the write lock."""
    with locked_jobs_for_write() as data:
        job = next((j for j in data.get("jobs") or [] if j.get("id") == job_id), None)
        if job is None:
            return None
        apply_scored_resolution(job, scored)
        job["updated_at"] = now_iso()
        if scored.get("confidence") == "high" and scored.get("url"):
            try:
                seed_jazzhr_slug(scored["url"])
            except Exception:
                pass
        return dict(job)


def resolve_job_id(
    job_id: str,
    *,
    write: bool = False,
    search_fn=None,
    fetch_fn=None,
    delay_s: float = 0.0,
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
    )
    persisted = None
    if write and result.get("confidence") in ("high", "medium"):
        persisted = persist_job_resolution(job_id, result)
    return {
        "ok": True,
        "id": job_id,
        "confidence": result.get("confidence"),
        "url": result.get("url"),
        "reason": result.get("reason"),
        "score": result.get("score"),
        "apply_url": (persisted or snapshot).get("apply_url") if write else snapshot.get("apply_url"),
        "dry_run": not write,
    }


def resolve_all(
    *,
    write: bool = False,
    limit: int | None = None,
    delay_s: float = DEFAULT_DELAY_S,
    progress_path: Path | None = None,
    search_fn=None,
    fetch_fn=None,
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
            )
            conf = result.get("confidence") or "low"
            summary[conf] = summary.get(conf, 0) + 1
            if write and conf in ("high", "medium"):
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
    args = parser.parse_args(argv)

    if args.reset_progress and PROGRESS_FILE.is_file():
        PROGRESS_FILE.write_text(json.dumps({"done_ids": [], "updated_at": now_iso()}, indent=2))

    if not args.job_id and not args.all:
        parser.error("pass JOB_ID or --all")

    backends = [b["name"] for b in available_search_backends(include_ddg=True)]
    log(f"search backends: {', '.join(backends) or '(none — fail soft)'}")
    if args.write:
        log("WRITE mode: high-confidence apply_url upgrades will be persisted")
    else:
        log("dry-run (pass --write to persist)")

    if args.job_id:
        out = resolve_job_id(args.job_id, write=args.write, delay_s=args.delay)
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1

    summary = resolve_all(write=args.write, limit=args.limit, delay_s=args.delay)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
