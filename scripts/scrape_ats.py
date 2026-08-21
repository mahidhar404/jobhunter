#!/usr/bin/env python3
"""Direct-to-ATS job scraper: Greenhouse, Lever, Ashby, Recruitee, Personio,
SmartRecruiters, Workable, Rippling, Breezy HR, BambooHR.

These platforms expose plain public APIs / JSON feeds per-company (no login,
no browser) - much faster and more reliable than scraping aggregators, and it
catches roles that never get syndicated to Indeed/LinkedIn at all. The
catch: there's no cross-company search - each fetch needs a company slug.

Deliberately excluded (and why):
  * Workday / iCIMS - Akamai bot-protection; never bypass CAPTCHA (PLAYBOOK).
  * Jobvite, Gem, Dover, Comeet - no reliable unauthenticated board JSON
    (Comeet needs a token; Gem/Dover are HTML SPAs; Jobvite HTML).
  * Teamtailor - supported via per-site /jobs.json JSON feeds.
  * ZipRecruiter / Glassdoor - aggregators, not employer boards; paid or
    anti-bot; out of scope same as LinkedIn guest scrape.
  * Taleo / SuccessFactors / Avature - no public cross-company board API.

This script keeps a persisted registry of known company slugs
(ats_companies.json at the workspace root) and self-expands it: point it at
one or more listings files (from scout.py or a prior run) and it will
extract any known-platform slugs it finds in apply_url/job_url and add
them to the registry, then fetch every company's *entire* board -
catching sibling roles the aggregator scrape never surfaced.

Usage:
  python3 scrape_ats.py [--out PATH] [--seed-from PATH ...] [--skip-urls PATH]

Writes a JSON array of listings (same schema as scout.py's output, plus
"source" already set to the ATS name) to --out (default:
../listings/<date>-ats.json).

--skip-urls skips known jobs (jobs.json / blocked / prior listing) so
detail-page fetches and duplicate rows are not repeated. Existing --out
content is seeded on resume.
"""
import argparse
import html
import json
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from urllib.error import URLError, HTTPError

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from known_job_urls import load_skip_urls_file, url_is_known  # noqa: E402

REGISTRY_FILE = ROOT / "ats_companies.json"
# Populated in main() from --skip-urls / jobs already on disk.
_SKIP_URL_KEYS: set[str] = set()
# Incremented when scrapers skip a known URL (esp. before detail fetches).
_SKIPPED_KNOWN: int = 0
_SKIP_LOCK = threading.Lock()


def _is_known_job_url(url: str | None) -> bool:
    return bool(url) and url_is_known(url, _SKIP_URL_KEYS)


def _skip_if_known(url: str | None) -> bool:
    """True when URL is known; increments skip counter (call before detail GET)."""
    global _SKIPPED_KNOWN
    if _is_known_job_url(url):
        with _SKIP_LOCK:
            _SKIPPED_KNOWN += 1
        return True
    return False

# Observed live: raising --max-guesses to 300 made nearly every Personio
# probe in a batch come back 429 (rate-limited) - a small delay between
# just Personio's own probes (the other platforms haven't shown this)
# trades a little time for actually getting real answers instead of
# transient failures.
PERSONIO_PROBE_DELAY_S = 0.5
# Only applied to fetching already-known-good slugs (the main scrape
# loop), never to guess_new_slugs' speculative probing - that one keeps
# its own sequential pacing (see PERSONIO_PROBE_DELAY_S) specifically to
# avoid rate-limiting on guesses that don't even correspond to a real
# company yet. A known-good slug's board is a company that's already
# confirmed to exist, so this is a much gentler load than probing.
FETCH_WORKERS = 10
INCREMENTAL_SAVE_EVERY = 20


def log(msg: str, *, err: bool = False) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr if err else sys.stdout, flush=True)

RELEVANT_KEYWORDS = [
    # Machine learning / AI - engineering & research
    "machine learning", "ml engineer", "mle", "ml ops", "mlops",
    "ml platform", "ml infrastructure", "ml research", "ai engineer",
    "ai infrastructure", "artificial intelligence", "ai researcher",
    "ai/ml", "applied scientist", "research scientist", "research engineer",
    "deep learning", "reinforcement learning", "computer vision",
    "nlp", "natural language processing", "llm", "generative ai", "genai",
    "prompt engineer", "conversational ai", "foundation model",
    "recommender system", "recommendation system", "ranking engineer",
    "search relevance", "speech recognition", "speech scientist",
    "ai safety", "responsible ai", "feature engineering",
    "perception engineer", "model training", "model deployment",
    "predictive analytics", "predictive model", "time series",
    "anomaly detection", "data annotation", "data labeling",

    # Data science / analysis
    "data scientist", "data science", "data analyst", "data analysis",
    "data analytics", "analytics engineer", "statistician",
    "business intelligence",

    # Data engineering / infrastructure
    "data engineer", "data engineering", "data platform",
    "data infrastructure", "data pipeline", "data architect",
    "data warehouse", "data lake", "data modeling", "database engineer",
    "etl", "elt", "dataops", "big data", "data cleaning", "data quality",
    "data wrangling",
]

SLUG_PATTERNS = {
    "greenhouse": re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([^/]+)/"),
    "lever": re.compile(r"jobs\.lever\.co/([^/]+)/"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([^/]+)/"),
    "recruitee": re.compile(r"([a-z0-9-]+)\.recruitee\.com/"),
    "personio": re.compile(r"([a-z0-9-]+)\.jobs\.personio\.(?:com|de)/"),
    # Company boards only — apply.workable.com/j/{shortcode} is a job deep
    # link with no account slug (negative lookahead skips the bare "j" path).
    "workable": re.compile(r"apply\.workable\.com/(?!j(?:/|$))([a-z0-9-]+)(?:/|$)"),
    "smartrecruiters": re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)/"),
    "rippling": re.compile(r"ats\.rippling\.com/([^/?#]+)(?:/|$)"),
    "breezy": re.compile(r"([a-z0-9-]+)\.breezy\.hr(?:/|$)"),
    "bamboohr": re.compile(r"([a-z0-9-]+)\.bamboohr\.com/"),
    # Full hostname stored as slug (e.g. spokeo.na.teamtailor.com).
    "teamtailor": re.compile(r"([a-z0-9-]+(?:\.[a-z0-9-]+)*\.teamtailor\.com)/"),
    "jazzhr": re.compile(r"([a-z0-9-]+)\.applytojob\.com/"),
    "pinpoint": re.compile(r"([a-z0-9-]+)\.pinpointhq\.com/"),
}


def load_registry() -> dict:
    """Handles both a missing file and an existing file that predates a
    newly-added platform (e.g. recruitee/personio added after
    ats_companies.json already existed with only greenhouse/lever/ashby
    keys) - either way, every key in SLUG_PATTERNS ends up present."""
    reg = json.loads(REGISTRY_FILE.read_text()) if REGISTRY_FILE.exists() else {}
    for ats in SLUG_PATTERNS:
        reg.setdefault(ats, [])
    reg.setdefault("tried_and_failed", {})
    for ats in SLUG_PATTERNS:
        reg["tried_and_failed"].setdefault(ats, [])
    return reg


def save_registry(reg: dict) -> None:
    REGISTRY_FILE.write_text(json.dumps(reg, indent=2, sort_keys=True))


def extract_slugs(listings_paths: list[Path], registry: dict) -> int:
    added = 0
    for path in listings_paths:
        if not path.exists():
            continue
        try:
            listings = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        for item in listings:
            if not isinstance(item, dict):
                continue  # this dir should only ever hold job-listing arrays, but be defensive
            for field in ("apply_url", "job_url", "job_url_direct"):
                url = item.get(field) or ""
                for ats, pattern in SLUG_PATTERNS.items():
                    m = pattern.search(url)
                    if m:
                        slug = m.group(1)
                        if slug not in registry[ats]:
                            registry[ats].append(slug)
                            added += 1
    return added


def is_relevant(title: str) -> bool:
    t = (title or "").lower()
    return any(kw in t for kw in RELEVANT_KEYWORDS)


def clean_html_content(raw: str) -> str:
    """Greenhouse's own API returns the job description as HTML-entity-
    escaped markup (verified live: the JSON string literally contains
    '&lt;p&gt;...&lt;/p&gt;' as characters, not real '<p>' tags) - a plain
    HTML parser sees escaped entities as inert text, not markup, so it
    never recognizes them as tags to strip unless they're decoded to real
    '<'/'>' characters first. Everything downstream (PartyRock tailoring,
    the dashboard's job-description panel) expects readable text, not
    HTML soup or literal escape sequences."""
    if not raw:
        return raw
    return BeautifulSoup(html.unescape(raw), "html.parser").get_text(separator="\n", strip=True)


def fetch_html(url: str) -> str | None:
    """Plain HTML GET for boards that only expose JD text in the page
    (Breezy ld+json). Returns None on hard miss / transport failure."""
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; job-hunter-agent/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    })
    try:
        with urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        if exc.code in (404, 403):
            return None
        log(f"warn: transient HTML fetch failure for {url}: {exc}", err=True)
        return None
    except URLError as exc:
        log(f"warn: transient HTML fetch failure for {url}: {exc}", err=True)
        return None


def _iter_jobposting_nodes(data):
    """Yield schema.org JobPosting dicts from ld+json (@graph, lists, typed lists)."""
    if isinstance(data, list):
        for item in data:
            yield from _iter_jobposting_nodes(item)
        return
    if not isinstance(data, dict):
        return
    types = data.get("@type")
    type_names = types if isinstance(types, list) else [types]
    if "JobPosting" in type_names:
        yield data
    graph = data.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            yield from _iter_jobposting_nodes(item)


def description_from_jobposting_ldjson(html_text: str) -> str:
    """Pull JobPosting.description from schema.org ld+json blocks."""
    if not html_text:
        return ""
    for block in re.findall(
        r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
        html_text,
        re.S | re.I,
    ):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for item in _iter_jobposting_nodes(data):
            text = clean_html_content(item.get("description") or "")
            if text.strip():
                return text
    return ""


def description_from_job_html(html_text: str) -> str:
    """Prefer the longer of JobPosting ld+json vs JazzHR #job-description HTML.

    Many applytojob.com pages only emit Organization schema, not JobPosting
    (verified live: Zealogics). Others emit a short JobPosting description
    while the full posting (responsibilities/requirements) is in the SSR
    ``#job-description`` body — taking ld+json first dropped those lists.
    """
    from_json = (description_from_jobposting_ldjson(html_text) or "").strip()
    soup = BeautifulSoup(html_text or "", "html.parser")
    node = soup.find(id="job-description")
    if node is None:
        node = soup.select_one(".job-details .job-description")
    from_html = clean_html_content(str(node)) if node is not None else ""
    from_html = from_html.strip()
    if len(from_html) > len(from_json) + 40:
        return from_html
    return from_json or from_html


def _lever_plain_intro(job: dict) -> str:
    plain = (job.get("descriptionPlain") or "").strip()
    if plain:
        return plain
    html_desc = job.get("description") or ""
    if isinstance(html_desc, str) and html_desc.strip():
        return clean_html_content(html_desc)
    parts: list[str] = []
    for key in ("openingPlain", "descriptionBodyPlain"):
        val = (job.get(key) or "").strip()
        if not val:
            raw = job.get(key.replace("Plain", "")) or ""
            if isinstance(raw, str) and raw.strip():
                val = clean_html_content(raw)
        if val:
            parts.append(val)
    return "\n\n".join(parts).strip()


def _lever_lists_and_additional(job: dict) -> list[str]:
    parts: list[str] = []
    for item in job.get("lists") or []:
        if not isinstance(item, dict):
            continue
        title = (item.get("text") or "").strip()
        content = clean_html_content(item.get("content") or "")
        if title and content:
            parts.append(f"{title}\n{content}")
        elif content:
            parts.append(content)
        elif title:
            parts.append(title)
    additional = (job.get("additionalPlain") or "").strip()
    if not additional:
        additional = clean_html_content(job.get("additional") or "")
    if additional:
        parts.append(additional)
    return parts


def workable_compose_description(detail: dict) -> str:
    """Workable v2 job payload: intro HTML plus requirements/benefits."""
    if not isinstance(detail, dict):
        return ""
    parts: list[str] = []
    intro = clean_html_content(detail.get("description") or "")
    if intro:
        parts.append(intro)
    for key, heading in (
        ("requirements", "Requirements"),
        ("benefits", "Benefits"),
    ):
        text = clean_html_content(detail.get(key) or "")
        if not text:
            continue
        if heading.lower() not in text[:80].lower():
            parts.append(f"{heading}\n{text}")
        else:
            parts.append(text)
    return "\n\n".join(p for p in parts if p).strip()


def pinpoint_compose_description(item: dict) -> str:
    """Pinpoint postings.json: description plus responsibilities/skills/benefits."""
    if not isinstance(item, dict):
        return ""
    parts: list[str] = []
    intro = clean_html_content(item.get("description") or "")
    if intro:
        parts.append(intro)
    for body_key, header_key, fallback in (
        ("key_responsibilities", "key_responsibilities_header", "Key responsibilities"),
        (
            "skills_knowledge_expertise",
            "skills_knowledge_expertise_header",
            "Skills, knowledge and expertise",
        ),
        ("benefits", "benefits_header", "Benefits"),
    ):
        text = clean_html_content(item.get(body_key) or "")
        if not text:
            continue
        heading = (item.get(header_key) or fallback).strip()
        parts.append(f"{heading}\n{text}" if heading else text)
    return "\n\n".join(p for p in parts if p).strip()


def lever_compose_description(job: dict) -> str:
    """Full Lever posting: intro + lists[] + additionalPlain.

    The hosted page renders descriptionPlain as the intro and ``lists`` as
    responsibilities/requirements. Returning descriptionPlain alone (when
    non-empty) dropped those HTML lists — verified live: nextgenfed Senior
    Statistician stored only the company blurb + location.
    """
    parts: list[str] = []
    intro = _lever_plain_intro(job)
    if intro:
        parts.append(intro)
    parts.extend(_lever_lists_and_additional(job))
    return "\n\n".join(p for p in parts if p).strip()


def smartrecruiters_description_from_detail(detail: dict) -> str:
    """Detail GET jobAd.sections — list endpoint has no description."""
    sections = ((detail.get("jobAd") or {}).get("sections") or {})
    if not isinstance(sections, dict):
        return ""
    preferred = (
        "jobDescription",
        "qualifications",
        "companyDescription",
        "additionalInformation",
    )
    parts: list[str] = []
    seen: set[str] = set()
    for key in preferred:
        block = sections.get(key)
        if not isinstance(block, dict):
            continue
        seen.add(key)
        title = (block.get("title") or "").strip()
        text = clean_html_content(block.get("text") or "")
        if not text:
            continue
        parts.append(f"{title}\n{text}".strip() if title else text)
    for key, block in sections.items():
        if key in seen or not isinstance(block, dict):
            continue
        title = (block.get("title") or "").strip()
        text = clean_html_content(block.get("text") or "")
        if not text:
            continue
        parts.append(f"{title}\n{text}".strip() if title else text)
    return "\n\n".join(parts).strip()


def rippling_description_from_detail(detail: dict) -> str:
    """Rippling detail `description` is often a dict of HTML sections
    (company/role/...), not a plain string."""
    desc = detail.get("description")
    if isinstance(desc, str):
        return clean_html_content(desc)
    if isinstance(desc, dict):
        preferred = ("company", "role", "responsibilities", "requirements", "benefits")
        parts: list[str] = []
        seen: set[str] = set()
        for key in preferred:
            raw = desc.get(key)
            if isinstance(raw, str) and raw.strip():
                seen.add(key)
                parts.append(clean_html_content(raw))
        for key, raw in desc.items():
            if key in seen or not isinstance(raw, str) or not raw.strip():
                continue
            parts.append(clean_html_content(raw))
        return "\n\n".join(parts).strip()
    return ""


def slug_candidates(company: str) -> list[str]:
    base = re.sub(r"\b(inc|llc|corp|corporation|ltd|co|company)\b\.?", "", company.lower())
    base = re.sub(r"[^a-z0-9\s-]", "", base).strip()
    no_space = re.sub(r"\s+", "", base)
    hyphenated = re.sub(r"\s+", "-", base)
    candidates = [no_space]
    if hyphenated != no_space:
        candidates.append(hyphenated)
    return [c for c in candidates if c]


def guess_new_slugs(
    listings_paths: list[Path],
    registry: dict,
    max_probes: int,
    *,
    deadline_monotonic: float | None = None,
) -> tuple[int, int]:
    """Known slugs so far only cover companies whose Indeed/LinkedIn listing
    happened to link straight to a Greenhouse/Lever/Ashby URL. Most
    companies on these ATSs never show that in an aggregator listing at
    all - guessing a slug from the company name and probing it directly
    catches those too. Failed guesses are cached permanently
    (tried_and_failed) so the same non-match is never re-probed.

    ``deadline_monotonic`` (``time.monotonic()``) stops probing so guesses
    cannot steal the known-slug fetch budget when this runs *after* fetch.
    """
    known = {ats: set(registry[ats]) for ats in SLUG_PATTERNS}
    tried = {ats: set(registry["tried_and_failed"][ats]) for ats in SLUG_PATTERNS}

    companies = set()
    for path in listings_paths:
        if not path.exists():
            continue
        try:
            listings = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        for item in listings:
            if not isinstance(item, dict):
                continue
            c = item.get("company")
            if c and str(c).lower() != "nan":
                companies.add(str(c))

    added, probed, rate_limited = 0, 0, 0
    for company in companies:
        if probed >= max_probes:
            break
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            log("guess budget exhausted — stopping speculative probes")
            break
        for ats in SLUG_PATTERNS:
            if probed >= max_probes:
                break
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                break
            for slug in slug_candidates(company):
                if slug in known[ats] or slug in tried[ats]:
                    continue
                if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                    break
                probed += 1
                if ats == "personio":
                    time.sleep(PERSONIO_PROBE_DELAY_S)
                try:
                    found = probe_slug(ats, slug)
                except TransientFetchError:
                    # Rate-limited/server error, not a genuine "doesn't
                    # exist" - skip without caching either way, so it gets
                    # a fair retry on a future run instead of being
                    # permanently blacklisted for a transient hiccup.
                    rate_limited += 1
                    continue
                if found:
                    registry[ats].append(slug)
                    known[ats].add(slug)
                    added += 1
                    break  # found the right variant for this company/ATS, stop trying others
                else:
                    registry["tried_and_failed"][ats].append(slug)
                    tried[ats].add(slug)
    if rate_limited:
        log(f"skipped {rate_limited} probe(s) due to rate-limit/server errors (not cached, will retry later)")
    return added, probed


class TransientFetchError(Exception):
    """Rate-limited, server error, or timeout - NOT the same as a genuine
    404 'this slug doesn't exist'. Observed live: raising max-guesses to
    300 made guess_new_slugs hit Personio's rate limit on nearly every
    probe in a batch - if that gets treated the same as a 404, a perfectly
    valid company slug gets permanently blacklisted in tried_and_failed
    just because we asked too fast, never to be probed again."""


def fetch_json(
    url: str,
    method: str = "GET",
    body: bytes | None = None,
    *,
    not_found_codes: tuple[int, ...] = (404,),
    require_json_content_type: bool = False,
    headers: dict | None = None,
) -> dict | list | None:
    hdrs = {
        "User-Agent": "Mozilla/5.0 (compatible; job-hunter-agent/1.0)",
        "Accept": "application/json",
    }
    if headers:
        hdrs.update(headers)
    req = Request(url, data=body, method=method, headers=hdrs)
    try:
        with urlopen(req, timeout=20) as resp:
            raw = resp.read()
            if require_json_content_type:
                ct = (resp.headers.get("Content-Type") or "").lower()
                # BambooHR returns HTML 200 (marketing homepage) for unknown
                # tenants instead of 404 - treat non-JSON as "slug missing".
                if "json" not in ct:
                    return None
            return json.loads(raw)
    except HTTPError as exc:
        if exc.code in not_found_codes:
            return None
        log(f"warn: transient fetch failure for {url}: {exc}", err=True)
        raise TransientFetchError(str(exc)) from exc
    except json.JSONDecodeError:
        # Non-JSON body with a 200 (BambooHR unknown tenant, etc.)
        if require_json_content_type:
            return None
        log(f"warn: transient fetch failure for {url}: non-JSON body", err=True)
        raise TransientFetchError(f"non-JSON body from {url}")
    except URLError as exc:
        log(f"warn: transient fetch failure for {url}: {exc}", err=True)
        raise TransientFetchError(str(exc)) from exc


def fetch_xml(url: str) -> ET.Element | None:
    """Personio's feed is XML, not JSON - everything else here is JSON, so
    this is kept separate rather than overloading fetch_json."""
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; job-hunter-agent/1.0)",
        "Accept": "application/xml",
    })
    try:
        with urlopen(req, timeout=20) as resp:
            return ET.fromstring(resp.read())
    except HTTPError as exc:
        if exc.code == 404:
            return None
        log(f"warn: transient fetch failure for {url}: {exc}", err=True)
        raise TransientFetchError(str(exc)) from exc
    except (URLError, ET.ParseError) as exc:
        log(f"warn: transient fetch failure for {url}: {exc}", err=True)
        raise TransientFetchError(str(exc)) from exc


PROBE_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "recruitee": "https://{slug}.recruitee.com/api/offers/",
    "personio": "https://{slug}.jobs.personio.de/xml?language=en",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{slug}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1",
    "rippling": "https://ats.rippling.com/api/v1/board/{slug}/jobs",
    "breezy": "https://{slug}.breezy.hr/json",
    "bamboohr": "https://{slug}.bamboohr.com/careers/list",
    "teamtailor": "https://{slug}/jobs.json",
    "jazzhr": "https://{slug}.applytojob.com/apply",
    "pinpoint": "https://{slug}.pinpointhq.com/postings.json",
}


def probe_slug(ats: str, slug: str) -> bool:
    """Lightweight existence check - a real board, whether or not it
    currently has any AI/ML-relevant openings (a company with zero
    matching jobs right now is still a valid slug worth keeping for next
    time, so this deliberately doesn't filter by title relevance).

    Exceptions where an empty board is indistinguishable from a missing
    slug (SmartRecruiters returns 200 + empty content for unknowns) -
    require at least one posting so guess_new_slugs doesn't pollute the
    registry. URL extraction still adds those slugs without probing.
    """
    url = PROBE_URLS[ats].format(slug=slug)
    if ats == "personio":
        root = fetch_xml(url)
        return root is not None and root.tag == "workzag-jobs"
    if ats == "jazzhr":
        html_text = fetch_html(url)
        return bool(html_text and re.search(r"/apply/[A-Za-z0-9]+/", html_text))
    if ats == "breezy":
        # Unknown Breezy tenants return Akamai/CDN 403 HTML, not 404.
        data = fetch_json(url, not_found_codes=(404, 403))
    elif ats == "bamboohr":
        data = fetch_json(url, require_json_content_type=True)
    elif ats == "pinpoint":
        data = fetch_json(
            url,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
    else:
        data = fetch_json(url)
    if data is None:
        return False
    if ats == "lever":
        return isinstance(data, list)
    if ats == "rippling":
        return isinstance(data, list)
    if ats == "breezy":
        return isinstance(data, list)
    if ats == "recruitee":
        return isinstance(data.get("offers"), list) if isinstance(data, dict) else False
    if ats == "workable":
        return isinstance(data.get("jobs"), list) if isinstance(data, dict) else False
    if ats == "smartrecruiters":
        # Invalid company identifiers also return 200 + empty content —
        # only treat as real when the board actually has postings.
        if not isinstance(data, dict):
            return False
        return int(data.get("totalFound") or 0) > 0 or bool(data.get("content"))
    if ats == "bamboohr":
        return isinstance(data.get("result"), list) if isinstance(data, dict) else False
    if ats == "teamtailor":
        return isinstance(data.get("items"), list) if isinstance(data, dict) else False
    if ats == "pinpoint":
        return isinstance(data.get("data"), list) if isinstance(data, dict) else False
    return isinstance(data.get("jobs"), list) if isinstance(data, dict) else False


def scrape_greenhouse(slug: str) -> list[dict]:
    data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    if not data:
        return []
    out = []
    for job in data.get("jobs", []):
        title = job.get("title", "")
        if not is_relevant(title):
            continue
        job_url = job.get("absolute_url")
        if _skip_if_known(job_url):
            continue
        out.append({
            "title": title,
            "company": slug,
            "site": "greenhouse",
            "job_url": job_url,
            "job_url_direct": job_url,
            "description": clean_html_content(job.get("content")),
            "date_posted": (job.get("updated_at") or "")[:10] or None,
            "job_type": "fulltime",
            "location": (job.get("location") or {}).get("name"),
            "search_term": "ats:greenhouse",
        })
    return out


def scrape_lever(slug: str) -> list[dict]:
    data = fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not data:
        return []
    out = []
    for job in data:
        title = job.get("text", "")
        if not is_relevant(title):
            continue
        job_url = job.get("hostedUrl")
        apply_url = job.get("applyUrl")
        if _skip_if_known(job_url or apply_url):
            continue
        cat = job.get("categories") or {}
        out.append({
            "title": title,
            "company": slug,
            "site": "lever",
            "job_url": job_url,
            "job_url_direct": apply_url or job_url,
            "description": lever_compose_description(job),
            "date_posted": None,
            "job_type": "fulltime",
            "location": cat.get("location"),
            "search_term": "ats:lever",
        })
    return out


def scrape_ashby(slug: str) -> list[dict]:
    data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if not data:
        return []
    out = []
    for job in data.get("jobs", []):
        title = job.get("title", "")
        if not is_relevant(title):
            continue
        job_url = job.get("jobUrl") or job.get("applyUrl")
        if _skip_if_known(job_url):
            continue
        out.append({
            "title": title,
            "company": slug,
            "site": "ashby",
            "job_url": job_url,
            "job_url_direct": job.get("applyUrl") or job.get("jobUrl"),
            "description": job.get("descriptionPlain"),
            "date_posted": (job.get("publishedAt") or "")[:10] or None,
            "job_type": "fulltime",
            "location": job.get("location"),
            "search_term": "ats:ashby",
        })
    return out


def scrape_recruitee(slug: str) -> list[dict]:
    data = fetch_json(f"https://{slug}.recruitee.com/api/offers/")
    if not data:
        return []
    out = []
    for job in data.get("offers", []):
        title = job.get("title", "")
        if not is_relevant(title):
            continue
        job_url = job.get("careers_url")
        if _skip_if_known(job_url):
            continue
        out.append({
            "title": title,
            "company": job.get("company_name") or slug,
            "site": "recruitee",
            "job_url": job_url,
            "job_url_direct": job.get("careers_apply_url") or job_url,
            "description": job.get("description"),
            "date_posted": (job.get("published_at") or "")[:10] or None,
            "job_type": "fulltime",
            "location": job.get("location"),
            "search_term": "ats:recruitee",
        })
    return out


def scrape_personio(slug: str) -> list[dict]:
    root = fetch_xml(f"https://{slug}.jobs.personio.de/xml?language=en")
    if root is None:
        return []
    out = []
    # Personio's XML feed has no per-job URL field at all (verified live -
    # checked every tag on a real posting) - the careers page root is the
    # only confirmed-working link. A guessed /job/<id>-style deep link
    # risks landing on a 404, which is worse than a page that just needs
    # one extra look to find the specific role. The position id is
    # appended as a URL fragment purely so each job gets a distinct
    # job_url - required for this script's own and write_discovered_jobs.
    # py's dedup-by-url logic to treat same-company postings as separate
    # jobs instead of collapsing them into one.
    careers_url = f"https://{slug}.jobs.personio.de/?language=en"
    for pos in root.findall("position"):
        title = pos.findtext("name") or ""
        if not is_relevant(title):
            continue
        descriptions = []
        for jd in pos.findall("jobDescriptions/jobDescription"):
            name = jd.findtext("name") or ""
            value = jd.findtext("value") or ""
            descriptions.append(f"{name}\n{value}" if name else value)
        pos_id = pos.findtext("id") or ""
        job_url = f"{careers_url}#{pos_id}" if pos_id else careers_url
        if _skip_if_known(job_url):
            continue
        out.append({
            "title": title,
            "company": pos.findtext("subcompany") or slug,
            "site": "personio",
            "job_url": job_url,
            "job_url_direct": job_url,
            "description": "\n\n".join(d for d in descriptions if d),
            "date_posted": (pos.findtext("createdAt") or "")[:10] or None,
            "job_type": "fulltime",
            "location": pos.findtext("office"),
            "search_term": "ats:personio",
        })
    return out


def scrape_smartrecruiters(slug: str) -> list[dict]:
    """api.smartrecruiters.com public postings API - no auth. Paginated.

    List payload has titles/locations but no JD body; for title-relevant
    roles only, pull sections from the per-posting detail endpoint (same
    pattern as Workable/BambooHR).
    """
    out = []
    offset = 0
    for _ in range(30):  # hard cap: 3000 postings
        data = fetch_json(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
            f"?limit=100&offset={offset}"
        )
        if not data or not isinstance(data, dict):
            break
        content = data.get("content") or []
        for job in content:
            if job.get("visibility") not in (None, "PUBLIC"):
                continue
            title = job.get("name") or ""
            if not is_relevant(title):
                continue
            loc = job.get("location") or {}
            location = ", ".join(
                p for p in (loc.get("city"), loc.get("region"), loc.get("country")) if p
            )
            job_id = job.get("id") or ""
            job_url = (
                job.get("postingUrl")
                or f"https://jobs.smartrecruiters.com/{slug}/{job_id}"
            )
            if _skip_if_known(job_url):
                continue
            description = ""
            company_name = slug
            if job_id:
                try:
                    detail = fetch_json(
                        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}"
                    )
                except TransientFetchError:
                    detail = None
                if isinstance(detail, dict):
                    description = smartrecruiters_description_from_detail(detail)
                    company = detail.get("company") or {}
                    if isinstance(company, dict) and company.get("name"):
                        company_name = company["name"]
                    job_url = detail.get("postingUrl") or job_url
            out.append({
                "title": title,
                "company": company_name,
                "site": "smartrecruiters",
                "job_url": job_url,
                "job_url_direct": job_url,
                "description": description,
                "date_posted": (job.get("releasedDate") or "")[:10] or None,
                "job_type": "fulltime",
                "location": location or None,
                "search_term": "ats:smartrecruiters",
            })
        if len(content) < 100 or offset + 100 >= int(data.get("totalFound") or 0):
            break
        offset += 100
    return out


def scrape_workable(slug: str) -> list[dict]:
    """apply.workable.com public widget JSON - no auth.

    List payload has titles/locations but not full HTML descriptions; for
    title-relevant roles only, pull description from the v2 job endpoint
    (one extra GET per match - boards after title filter are small).
    """
    data = fetch_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    if not data or not isinstance(data, dict):
        return []
    company_name = data.get("name") or slug
    out = []
    for job in data.get("jobs") or []:
        title = job.get("title") or ""
        if not is_relevant(title):
            continue
        shortcode = job.get("shortcode") or ""
        job_url = (
            job.get("application_url")
            or job.get("url")
            or job.get("shortlink")
            or (f"https://apply.workable.com/j/{shortcode}" if shortcode else None)
        )
        location = ", ".join(
            p for p in (job.get("city"), job.get("state"), job.get("country")) if p
        )
        if _skip_if_known(job_url):
            continue
        description = ""
        if shortcode:
            detail = fetch_json(
                f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}"
            )
            if isinstance(detail, dict):
                description = workable_compose_description(detail)
        out.append({
            "title": title,
            "company": company_name,
            "site": "workable",
            "job_url": job_url,
            "job_url_direct": job_url,
            "description": description,
            "date_posted": (job.get("published_on") or job.get("created_at") or "")[:10] or None,
            "job_type": "fulltime",
            "location": location or None,
            "search_term": "ats:workable",
        })
    return out


def scrape_rippling(slug: str) -> list[dict]:
    """ats.rippling.com public board JSON - no auth.

    List payload has titles/locations but not descriptions; detail GET
    ``/api/v1/board/{slug}/jobs/{uuid}`` includes the JD body.
    """
    data = fetch_json(f"https://ats.rippling.com/api/v1/board/{slug}/jobs")
    if not isinstance(data, list):
        return []
    out = []
    for job in data:
        title = job.get("name") or ""
        if not is_relevant(title):
            continue
        uuid = job.get("uuid") or ""
        job_url = job.get("url") or (
            f"https://ats.rippling.com/{slug}/jobs/{uuid}" if uuid else None
        )
        loc = job.get("workLocation") or {}
        if _skip_if_known(job_url):
            continue
        description = ""
        company_name = slug
        date_posted = None
        if uuid:
            try:
                detail = fetch_json(
                    f"https://ats.rippling.com/api/v1/board/{slug}/jobs/{uuid}"
                )
            except TransientFetchError:
                detail = None
            if isinstance(detail, dict):
                description = rippling_description_from_detail(detail)
                company_name = detail.get("companyName") or company_name
                job_url = detail.get("url") or job_url
                created = detail.get("createdOn") or ""
                if isinstance(created, str) and created:
                    date_posted = created[:10]
                elif isinstance(created, (int, float)) and created:
                    # ms epoch in some payloads
                    try:
                        date_posted = datetime.utcfromtimestamp(created / 1000).strftime("%Y-%m-%d")
                    except (OverflowError, OSError, ValueError):
                        date_posted = None
        out.append({
            "title": title,
            "company": company_name,
            "site": "rippling",
            "job_url": job_url,
            "job_url_direct": job_url,
            "description": description,
            "date_posted": date_posted,
            "job_type": "fulltime",
            "location": loc.get("label") if isinstance(loc, dict) else None,
            "search_term": "ats:rippling",
        })
    return out


def scrape_breezy(slug: str) -> list[dict]:
    """{slug}.breezy.hr/json public feed - no auth.

    List JSON has no JD; the public posting page embeds schema.org
    JobPosting ld+json with the full description (verified live).
    """
    data = fetch_json(
        f"https://{slug}.breezy.hr/json",
        not_found_codes=(404, 403),
    )
    if not isinstance(data, list):
        return []
    out = []
    for job in data:
        title = job.get("name") or ""
        if not is_relevant(title):
            continue
        company = job.get("company") or {}
        company_name = (
            company.get("name") if isinstance(company, dict) else None
        ) or slug
        loc = job.get("location") or {}
        location = loc.get("name") if isinstance(loc, dict) else None
        job_url = job.get("url") or (
            f"https://{slug}.breezy.hr/p/{job.get('friendly_id') or job.get('id', '')}"
        )
        if _skip_if_known(job_url):
            continue
        description = ""
        if job_url:
            html_text = fetch_html(job_url)
            if html_text:
                description = description_from_jobposting_ldjson(html_text)
        out.append({
            "title": title,
            "company": company_name,
            "site": "breezy",
            "job_url": job_url,
            "job_url_direct": job_url,
            "description": description,
            "date_posted": (job.get("published_date") or "")[:10] or None,
            "job_type": "fulltime",
            "location": location,
            "search_term": "ats:breezy",
        })
    return out


def scrape_bamboohr(slug: str) -> list[dict]:
    """{slug}.bamboohr.com/careers/list (+ /detail for descriptions)."""
    data = fetch_json(
        f"https://{slug}.bamboohr.com/careers/list",
        require_json_content_type=True,
    )
    if not data or not isinstance(data, dict):
        return []
    out = []
    for job in data.get("result") or []:
        title = job.get("jobOpeningName") or ""
        if not is_relevant(title):
            continue
        jid = job.get("id")
        job_url = f"https://{slug}.bamboohr.com/careers/{jid}" if jid else None
        if _skip_if_known(job_url):
            continue
        loc = job.get("location") or {}
        location = None
        if isinstance(loc, dict):
            location = ", ".join(
                p for p in (loc.get("city"), loc.get("state")) if p
            ) or None
        description = ""
        date_posted = None
        if jid:
            detail = fetch_json(
                f"https://{slug}.bamboohr.com/careers/{jid}/detail",
                require_json_content_type=True,
            )
            if isinstance(detail, dict):
                opening = (detail.get("result") or {}).get("jobOpening") or {}
                description = clean_html_content(opening.get("description") or "")
                date_posted = (opening.get("datePosted") or "")[:10] or None
                job_url = opening.get("jobOpeningShareUrl") or job_url
        out.append({
            "title": title,
            "company": slug,
            "site": "bamboohr",
            "job_url": job_url,
            "job_url_direct": job_url,
            "description": description,
            "date_posted": date_posted,
            "job_type": "fulltime",
            "location": location,
            "search_term": "ats:bamboohr",
        })
    return out


_JAZZHR_APPLY_RE = re.compile(r"/apply/([A-Za-z0-9]+)/([^/?#]+)")


def _jazzhr_company_name(html_text: str, slug: str) -> str:
    m = re.search(
        r'<meta property="og:description" content="Explore open job opportunities at ([^".]+)',
        html_text or "",
    )
    if m:
        return m.group(1).strip()
    m = re.search(r"<title>([^<]+) - Career Page</title>", html_text or "", re.I)
    if m:
        return m.group(1).strip()
    return slug


def scrape_jazzhr(slug: str) -> list[dict]:
    """JazzHR / applytojob.com SSR board at {slug}.applytojob.com/apply."""
    board_url = f"https://{slug}.applytojob.com/apply"
    html_text = fetch_html(board_url)
    if not html_text:
        return []
    company = _jazzhr_company_name(html_text, slug)
    soup = BeautifulSoup(html_text, "html.parser")
    seen_ids: set[str] = set()
    stubs: list[tuple[str, str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        m = _JAZZHR_APPLY_RE.search(href)
        if not m:
            continue
        job_id, title_slug = m.group(1), m.group(2)
        if job_id in seen_ids:
            continue
        title = anchor.get_text(" ", strip=True)
        if not title:
            title = title_slug.replace("-", " ")
        seen_ids.add(job_id)
        if href.startswith("http"):
            job_url = href.split("?")[0]
        else:
            job_url = f"https://{slug}.applytojob.com/apply/{job_id}/{title_slug}"
        stubs.append((title, job_url, job_id))

    out: list[dict] = []
    for title, job_url, _job_id in stubs:
        if not is_relevant(title):
            continue
        if _skip_if_known(job_url):
            continue
        description = ""
        date_posted = None
        page = fetch_html(job_url)
        if page:
            description = description_from_job_html(page)
            for block in re.findall(
                r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
                page,
                re.S | re.I,
            ):
                try:
                    data = json.loads(block.strip())
                except json.JSONDecodeError:
                    continue
                candidates = data if isinstance(data, list) else [data]
                for item in candidates:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        raw_date = item.get("datePosted") or ""
                        if isinstance(raw_date, str) and raw_date:
                            date_posted = raw_date[:10]
                        break
                if date_posted:
                    break
        out.append({
            "title": title,
            "company": company,
            "site": "jazzhr",
            "job_url": job_url,
            "job_url_direct": job_url,
            "description": description,
            "date_posted": date_posted,
            "job_type": "fulltime",
            "location": None,
            "search_term": "ats:jazzhr",
        })
    return out


def scrape_pinpoint(slug: str) -> list[dict]:
    data = fetch_json(
        f"https://{slug}.pinpointhq.com/postings.json",
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    if not isinstance(data, dict):
        return []
    company = slug
    out: list[dict] = []
    for item in data.get("data") or []:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or ""
        if not is_relevant(title):
            continue
        job_url = item.get("url")
        if not job_url:
            continue
        if _skip_if_known(job_url):
            continue
        location = None
        loc = item.get("location")
        if isinstance(loc, dict):
            location = loc.get("name") or None
        description = pinpoint_compose_description(item)
        deadline = item.get("deadline_at") or ""
        date_posted = deadline[:10] if isinstance(deadline, str) and deadline else None
        out.append({
            "title": title,
            "company": company,
            "site": "pinpoint",
            "job_url": job_url,
            "job_url_direct": job_url,
            "description": description,
            "date_posted": date_posted,
            "job_type": (item.get("employment_type") or "fulltime").replace("_", ""),
            "location": location,
            "search_term": "ats:pinpoint",
        })
    return out


def scrape_teamtailor(slug: str) -> list[dict]:
    """slug is the full careers hostname, e.g. spokeo.na.teamtailor.com."""
    data = fetch_json(f"https://{slug}/jobs.json")
    if not isinstance(data, dict):
        return []
    company = data.get("title") or slug.split(".")[0]
    out = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or ""
        if not is_relevant(title):
            continue
        job_url = item.get("url")
        if not job_url:
            continue
        if _skip_if_known(job_url):
            continue
        published = item.get("date_published") or ""
        out.append({
            "title": title,
            "company": company,
            "site": "teamtailor",
            "job_url": job_url,
            "job_url_direct": job_url,
            "description": clean_html_content(item.get("content_html") or ""),
            "date_posted": published[:10] if isinstance(published, str) and published else None,
            "job_type": "fulltime",
            "location": None,
            "search_term": "ats:teamtailor",
        })
    return out


# Fetch known boards first; speculative slug probes run afterward with a
# time cap so --max-guesses cannot starve Greenhouse/Ashby fetches.
SCRAPE_PHASE_ORDER = ("extract", "fetch_known", "guess", "fetch_guessed")
DEFAULT_MAX_GUESSES = 80
DEFAULT_GUESS_BUDGET_S = 180


def _seed_seen_from_out(out_path: Path) -> dict[str, dict]:
    seen: dict[str, dict] = {}
    if not (out_path.exists() and out_path.stat().st_size > 0):
        return seen
    try:
        prior = json.loads(out_path.read_text())
    except (json.JSONDecodeError, OSError):
        return seen
    if not isinstance(prior, list):
        return seen
    for j in prior:
        if not isinstance(j, dict):
            continue
        url = j.get("job_url")
        key = f"{url}|{j.get('title')}"
        if url and key not in seen:
            seen[key] = j
    if seen:
        log(f"seeded {len(seen)} listing(s) from existing {out_path.name}")
    return seen


def fetch_board_listings(
    tasks: list[tuple[str, str]],
    seen: dict[str, dict],
    out_path: Path,
) -> None:
    """Fetch known (ats, slug) boards into ``seen``."""
    if not tasks:
        return
    completed = 0

    def write_partial() -> None:
        out_path.write_text(json.dumps(list(seen.values()), indent=2, default=str))

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        future_to_task = {pool.submit(SCRAPERS[ats], slug): (ats, slug) for ats, slug in tasks}
        for future in as_completed(future_to_task):
            ats, slug = future_to_task[future]
            try:
                jobs = future.result()
            except Exception as exc:
                log(f"warn: fetch failed for {ats}/{slug}: {exc}", err=True)
                continue
            for j in jobs:
                # Keyed by url+title, not url alone - Personio has no
                # per-job URL, so every job at one company shares the same
                # careers-page link, and url-only dedup would collapse
                # them into a single entry.
                url = j.get("job_url")
                # Scrapers already skip known URLs before detail fetches;
                # keep a safety net here without re-counting.
                if _is_known_job_url(url):
                    continue
                key = f"{url}|{j.get('title')}"
                if url and key not in seen:
                    seen[key] = j
            completed += 1
            log(f"  got {len(jobs)} relevant results from {ats}/{slug} ({completed}/{len(tasks)} done)")
            if completed % INCREMENTAL_SAVE_EVERY == 0:
                write_partial()
    out_path.write_text(json.dumps(list(seen.values()), indent=2, default=str))


SCRAPERS = {
    "greenhouse": scrape_greenhouse,
    "lever": scrape_lever,
    "ashby": scrape_ashby,
    "recruitee": scrape_recruitee,
    "personio": scrape_personio,
    "smartrecruiters": scrape_smartrecruiters,
    "workable": scrape_workable,
    "rippling": scrape_rippling,
    "breezy": scrape_breezy,
    "bamboohr": scrape_bamboohr,
    "teamtailor": scrape_teamtailor,
    "jazzhr": scrape_jazzhr,
    "pinpoint": scrape_pinpoint,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--seed-from", nargs="*", default=None,
                         help="Listings files to scan for new company slugs (default: all listings/*.json)")
    parser.add_argument("--max-guesses", type=int, default=DEFAULT_MAX_GUESSES,
                         help="Cap on speculative slug probes after known boards are fetched "
                              f"(default {DEFAULT_MAX_GUESSES}; 0 is the same as --no-guess).")
    parser.add_argument("--guess-budget-s", type=int, default=DEFAULT_GUESS_BUDGET_S,
                         help="Seconds allowed for speculative probes AFTER known-slug fetch "
                              f"(default {DEFAULT_GUESS_BUDGET_S}).")
    parser.add_argument("--no-guess", action="store_true",
                         help="Skip proactive slug-guessing, only use slugs found in listing URLs")
    parser.add_argument(
        "--platforms", nargs="+", choices=sorted(SLUG_PATTERNS), default=None,
        help="Subset of ATS platforms to scrape (default: all).",
    )
    parser.add_argument(
        "--skip-urls", default=None,
        help="JSON array of URL keys to skip (jobs.json / blocked / prior listing)",
    )
    args = parser.parse_args()

    platforms = list(args.platforms) if args.platforms else list(SLUG_PATTERNS)
    if not platforms:
        raise SystemExit("no --platforms selected")

    run_start = time.monotonic()
    out_path = Path(args.out) if args.out else Path(__file__).parent.parent / "listings" / f"{date.today().isoformat()}-ats.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    registry = load_registry()
    seed_paths = [Path(p) for p in args.seed_from] if args.seed_from else sorted((ROOT / "listings").glob("*.json"))
    # Phase: extract — listing URLs only; never Workday.
    added = extract_slugs(seed_paths, registry)
    if added:
        log(f"discovered {added} new company slug(s) from listing URLs in {len(seed_paths)} file(s)")
        save_registry(registry)

    global _SKIP_URL_KEYS, _SKIPPED_KNOWN
    _SKIP_URL_KEYS = load_skip_urls_file(Path(args.skip_urls) if args.skip_urls else None)
    _SKIPPED_KNOWN = 0
    if _SKIP_URL_KEYS:
        log(f"skip-urls: {len(_SKIP_URL_KEYS)} known key(s)")

    seen = _seed_seen_from_out(out_path)
    known_before = {ats: set(registry.get(ats) or []) for ats in platforms}
    tasks = [(ats, slug) for ats in platforms for slug in registry.get(ats, [])]
    # Phase: fetch_known — spend the timeout on boards we already have.
    fetch_board_listings(tasks, seen, out_path)

    do_guess = (not args.no_guess) and args.max_guesses > 0 and args.guess_budget_s > 0
    guessed = probed = 0
    if do_guess:
        # Phase: guess — leftover time only; does not run before fetch.
        deadline = time.monotonic() + max(1, int(args.guess_budget_s))
        guessed, probed = guess_new_slugs(
            seed_paths, registry, args.max_guesses, deadline_monotonic=deadline,
        )
        if probed:
            log(f"probed {probed} speculative slug(s), {guessed} were real company boards")
        if guessed:
            save_registry(registry)
            extra = [
                (ats, slug)
                for ats in platforms
                for slug in registry.get(ats, [])
                if slug not in known_before.get(ats, set())
            ]
            if extra:
                log(f"fetching {len(extra)} newly guessed board(s)")
                fetch_board_listings(extra, seen, out_path)

    if _SKIPPED_KNOWN:
        log(f"skipped {_SKIPPED_KNOWN} already-known URL(s)")
    out_path.write_text(json.dumps(list(seen.values()), indent=2, default=str))
    log(f"wrote {len(seen)} listings -> {out_path} (total {time.monotonic() - run_start:.1f}s)")


if __name__ == "__main__":
    main()
