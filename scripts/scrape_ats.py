#!/usr/bin/env python3
"""Direct-to-ATS job scraper: Greenhouse, Lever, Ashby, Recruitee, Personio.

These platforms expose plain public APIs per-company (no login, no
browser) - much faster and more reliable than scraping aggregators, and it
catches roles that never get syndicated to Indeed/LinkedIn at all. The
catch: there's no cross-company search - each fetch needs a company slug.
(Workday and iCIMS deliberately excluded - Akamai-protected, out of
scope. SmartRecruiters, Workable, Jobvite, Breezy HR, Taleo,
SuccessFactors, and Avature were checked and excluded too - each either
requires a per-employer API key or has no reliable public, unauthenticated
access across companies.)

This script keeps a persisted registry of known company slugs
(ats_companies.json at the workspace root) and self-expands it: point it at
one or more listings files (from scout.py or a prior run) and it will
extract any known-platform slugs it finds in apply_url/job_url and add
them to the registry, then fetch every company's *entire* board -
catching sibling roles the aggregator scrape never surfaced.

Usage:
  python3 scrape_ats.py [--out PATH] [--seed-from PATH ...]

Writes a JSON array of listings (same schema as scout.py's output, plus
"source" already set to the ATS name) to --out (default:
../listings/<date>-ats.json).
"""
import argparse
import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from urllib.error import URLError, HTTPError

ROOT = Path(__file__).parent.parent
REGISTRY_FILE = ROOT / "ats_companies.json"
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


def slug_candidates(company: str) -> list[str]:
    base = re.sub(r"\b(inc|llc|corp|corporation|ltd|co|company)\b\.?", "", company.lower())
    base = re.sub(r"[^a-z0-9\s-]", "", base).strip()
    no_space = re.sub(r"\s+", "", base)
    hyphenated = re.sub(r"\s+", "-", base)
    candidates = [no_space]
    if hyphenated != no_space:
        candidates.append(hyphenated)
    return [c for c in candidates if c]


def guess_new_slugs(listings_paths: list[Path], registry: dict, max_probes: int) -> tuple[int, int]:
    """Known slugs so far only cover companies whose Indeed/LinkedIn listing
    happened to link straight to a Greenhouse/Lever/Ashby URL. Most
    companies on these ATSs never show that in an aggregator listing at
    all - guessing a slug from the company name and probing it directly
    catches those too. Failed guesses are cached permanently
    (tried_and_failed) so the same non-match is never re-probed."""
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
        for ats in SLUG_PATTERNS:
            if probed >= max_probes:
                break
            for slug in slug_candidates(company):
                if slug in known[ats] or slug in tried[ats]:
                    continue
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


def fetch_json(url: str, method: str = "GET", body: bytes | None = None) -> dict | list | None:
    req = Request(url, data=body, method=method, headers={
        "User-Agent": "Mozilla/5.0 (compatible; job-hunter-agent/1.0)",
        "Accept": "application/json",
    })
    try:
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except HTTPError as exc:
        if exc.code == 404:
            return None
        log(f"warn: transient fetch failure for {url}: {exc}", err=True)
        raise TransientFetchError(str(exc)) from exc
    except (URLError, json.JSONDecodeError) as exc:
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
}


def probe_slug(ats: str, slug: str) -> bool:
    """Lightweight existence check - a real board, whether or not it
    currently has any AI/ML-relevant openings (a company with zero
    matching jobs right now is still a valid slug worth keeping for next
    time, so this deliberately doesn't filter by title relevance)."""
    url = PROBE_URLS[ats].format(slug=slug)
    if ats == "personio":
        root = fetch_xml(url)
        return root is not None and root.tag == "workzag-jobs"
    data = fetch_json(url)
    if data is None:
        return False
    if ats == "lever":
        return isinstance(data, list)
    if ats == "recruitee":
        return isinstance(data.get("offers"), list) if isinstance(data, dict) else False
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
        out.append({
            "title": title,
            "company": slug,
            "site": "greenhouse",
            "job_url": job.get("absolute_url"),
            "job_url_direct": job.get("absolute_url"),
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
        cat = job.get("categories") or {}
        out.append({
            "title": title,
            "company": slug,
            "site": "lever",
            "job_url": job.get("hostedUrl"),
            "job_url_direct": job.get("applyUrl") or job.get("hostedUrl"),
            "description": job.get("descriptionPlain") or job.get("description"),
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
        out.append({
            "title": title,
            "company": slug,
            "site": "ashby",
            "job_url": job.get("jobUrl") or job.get("applyUrl"),
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
        out.append({
            "title": title,
            "company": job.get("company_name") or slug,
            "site": "recruitee",
            "job_url": job.get("careers_url"),
            "job_url_direct": job.get("careers_apply_url") or job.get("careers_url"),
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


SCRAPERS = {
    "greenhouse": scrape_greenhouse, "lever": scrape_lever, "ashby": scrape_ashby,
    "recruitee": scrape_recruitee, "personio": scrape_personio,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--seed-from", nargs="*", default=None,
                         help="Listings files to scan for new company slugs (default: all listings/*.json)")
    parser.add_argument("--max-guesses", type=int, default=300,
                         help="Cap on speculative slug probes per run (bounds network cost). "
                              "Raised from 60 - registries were tiny (single digits per "
                              "platform) relative to how many distinct companies show up in "
                              "daily scraping, and each probe is one cheap, safe public-API "
                              "existence check, not something bot-detection-sensitive.")
    parser.add_argument("--no-guess", action="store_true",
                         help="Skip proactive slug-guessing, only use slugs found in listing URLs")
    args = parser.parse_args()

    run_start = time.monotonic()
    out_path = Path(args.out) if args.out else Path(__file__).parent.parent / "listings" / f"{date.today().isoformat()}-ats.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    registry = load_registry()
    seed_paths = [Path(p) for p in args.seed_from] if args.seed_from else sorted((ROOT / "listings").glob("*.json"))
    added = extract_slugs(seed_paths, registry)
    if added:
        log(f"discovered {added} new company slug(s) from listing URLs in {len(seed_paths)} file(s)")

    if not args.no_guess:
        guessed, probed = guess_new_slugs(seed_paths, registry, args.max_guesses)
        if probed:
            log(f"probed {probed} speculative slug(s), {guessed} were real company boards")

    if added or not args.no_guess:
        save_registry(registry)

    # Fetching every known slug sequentially doesn't scale - a real recent
    # run already took 213.5s against the 300s timeout server.py gives
    # this script, with only 72 known slugs total. The registry only
    # grows over time (that's its whole point), so this was headed
    # straight for "run times out, whole result is lost" (nothing was
    # ever written until the very end). Each fetch is an independent,
    # I/O-bound HTTP call - ideal for a thread pool. Also now wrapped in
    # its own try/except: previously a single flaky company board
    # (transient 500/timeout) would raise out of the plain sequential
    # loop and crash the whole run, losing every result gathered so far.
    tasks = [(ats, slug) for ats in SLUG_PATTERNS for slug in registry[ats]]
    seen: dict[str, dict] = {}

    def write_partial() -> None:
        out_path.write_text(json.dumps(list(seen.values()), indent=2, default=str))

    completed = 0
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
                key = f"{url}|{j.get('title')}"
                if url and key not in seen:
                    seen[key] = j
            completed += 1
            log(f"  got {len(jobs)} relevant results from {ats}/{slug} ({completed}/{len(tasks)} done)")
            # Written periodically, not just at the end - if this run gets
            # killed by the caller's own timeout, whatever's completed so
            # far survives on disk instead of the whole run vanishing.
            if completed % INCREMENTAL_SAVE_EVERY == 0:
                write_partial()

    write_partial()
    log(f"wrote {len(seen)} listings -> {out_path} (total {time.monotonic() - run_start:.1f}s)")


if __name__ == "__main__":
    main()
