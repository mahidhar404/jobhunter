#!/usr/bin/env python3
"""Direct scraper for Built In (builtin.com) - a job board with no public
API and no JobSpy support (checked live: JobSpy's supported sites are
LinkedIn/Indeed/Glassdoor/Google/ZipRecruiter/Bayt/Naukri/BDJobs, no
Built In). Reverse-engineered live against real pages rather than any
official docs, since none exist:

- Search results (builtin.com/jobs/<filters>?search=<term>&page=<n>) are
  plain server-rendered HTML with real /job/<slug>/<id> links directly in
  the markup - no JS execution needed to discover them. Pagination is the
  &page=<n> query param that Built In's own pager links use; a /<n> path
  segment after the filter prefix re-serves page 1 (A/B verified live
  2026-08-05, see build_search_url).
- Each job page embeds a small JSON blob passed to a JS init call,
  `Builtin.jobPostInit({"job": {...}})`, containing companyName, title,
  howToApply (the REAL external apply URL - often the company's own ATS,
  e.g. a Workday/Greenhouse link), and isEasyApply (whether Built In
  hosts its own in-page application form instead of linking out).
- The full job description isn't in that JSON - it's the first
  `bg-midnight` section header's next sibling div ("The Role"), which in
  every page checked contains the complete posting text (What you'll do/
  What you'll bring etc. are subheadings within that same block, not
  separate top-level sections).
- Location isn't in the JSON either, but the page's own <meta
  name="description"> follows a completely consistent Built-In-authored
  template: "<Company> is hiring for a <Title> in <Location>. Find more
  details..." - reliable enough to regex out the location cleanly.

isEasyApply is skipped unconditionally, never just deprioritized - the
user's explicit instruction (matching the same policy already applied to
LinkedIn) is to never use a platform-hosted Easy Apply flow, only a real
external application route. A job with isEasyApply=true or a howToApply
that isn't a real http(s) URL (occasionally plain mail-in instructions)
is simply not usable and gets dropped here, not passed downstream.

Usage:
  python3 scrape_builtin.py [--out PATH] [--max-pages-per-term N]
                            [--skip-urls PATH]

Writes a JSON array of listings (same schema as scout.py/scrape_ats.py)
to --out (default: ../listings/<date>-builtin.json).

--skip-urls: JSON array of URL keys already in jobs.json / blocked /
partial listing — detail pages for those are not re-fetched. Existing
--out content is seeded so an interrupted run continues without
re-downloading pages already collected.
"""
import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from known_job_urls import (  # noqa: E402
    load_skip_urls_file,
    url_is_known,
)

BASE = "https://builtin.com"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
# A smaller, broad-coverage subset of scout.py's full 14-term list - Built
# In's own search already ranks by relevance per term, and dedup_listings.py
# downstream re-filters every listing by the full RELEVANT_KEYWORDS list
# anyway, so this only needs enough terms to surface the bulk of postings,
# not exhaustive phrase coverage.
#
# Tuned empirically 2026-08-05 (4 pages/term,
# country=USA, 7-day window, terms evaluated in listed order so each term's
# yield is measured *after* the ones above it). Kept = contributes relevant
# URLs no earlier term already found. Numbers below are relevant URLs that
# term added on top of every preceding term, and that jobs.json didn't have.
SEARCH_TERMS = [
    "machine learning engineer",     # +24
    "data scientist",                # +43
    "data engineer",                 # +31
    "ai engineer",                   # +20 (noisiest of the core five, still pays)
    "applied scientist",             # +3  (low volume, distinct role class)
    "data analyst",                  # +91 - by far the largest gap in the old list
    "research scientist",            # +24
    "analytics engineer",            # +20
    "mlops engineer",                # +10
    "computer vision engineer",      # +5
    "business intelligence engineer",# +4
    "generative ai engineer",        # +3
]
# Dropped after measurement, do not re-add without re-running the benchmark:
#   ai/ml engineer, software engineer machine learning - 39/13 URLs, 100%
#     already found by the core five (0 new).
#   machine learning operations engineer - 100% duplicate of "mlops engineer".
#   quantitative analyst - 5 URLs, none title-relevant (finance noise).
#   nlp engineer, natural language processing engineer - 0 results at all.
#   llm engineer, deep learning engineer - 1 new URL each; those roles are
#     titled "AI/ML Engineer" on Built In and already covered above.
DEFAULT_MAX_PAGES_PER_TERM = 15
# Built In's search has no date-sort - order is relevance-based and a real
# posting can rank many pages deep (verified live: an Intel "Data
# Scientist" req, real Workday apply link, ranked page 9/~207th for the
# plain "data scientist" term - past DEFAULT_MAX_PAGES_PER_TERM=2, so it
# was silently never fetched at all, not filtered out downstream). Rather
# than brute-forcing more pages against an unbounded relevance ranking,
# this narrows the search itself server-side via Built In's own filter
# UI (reverse-engineered from its jobs page's Alpine.js state + the
# criteria echoed back in the page's embedded JSON): a path-segment
# experience-level filter and a `daysSinceUpdated` query param. With both
# applied, the same Intel req moved from page 9 to page 2 of a
# ~230-result set (vs. an unbounded one before) - narrowing the pool
# rather than just paging deeper into it. DEFAULT_MAX_PAGES_PER_TERM is a
# hard safety ceiling; collect_job_urls() also early-stops a term after
# CONSECUTIVE_EMPTY_PAGES_STOP consecutive pages that yield no new job
# URLs (zero listings, or only URLs already seen this run).
# Built In "New Jobs" UI options only (postedDateFilterOptions filterValue):
# Past 24 hours=1, Past 3 days=3, Past week=7, Past month=30. No 14-day option.
SUPPORTED_DAYS_SINCE_UPDATED = (1, 3, 7, 30)
DEFAULT_DAYS_SINCE_UPDATED = 1
DAYS_SINCE_UPDATED = DEFAULT_DAYS_SINCE_UPDATED  # CLI/dashboard can override
# National board (builtin.com/jobs) is US-scoped; criteria.country=USA is the
# verified United States filter. allLocations=true clears country and pulls
# non-US — never set that. US Remote stays on the national board (do not force
# /jobs/remote/ which drops on-site US roles).
SEARCH_COUNTRY = "USA"
# Stop paging a search term after this many consecutive pages with no new URLs.
CONSECUTIVE_EMPTY_PAGES_STOP = 3
# experiencesFilterOptions aliases, excluding "internship" (not a fit for
# this project's roles) and "expert-leader" (Expert/Leader, 9+ years -
# explicitly excluded, matching the project's existing seniority-exclusion
# policy for Lead/Manager/VP titles elsewhere in the pipeline).
EXPERIENCE_LEVEL_ALIASES = ["entry-level", "junior", "mid-level", "senior"]
# Observed live: a first attempt at this used 4 concurrent workers for
# job-detail fetches and got 429'd on 280 of 369 requests within under a
# minute. Built In is a consumer-facing website with real bot-detection/
# rate-limiting, not a dedicated public API like Greenhouse/Lever/Ashby -
# unlike scrape_ats.py's fetch loop, this stays fully sequential with a
# real per-request delay, plus retry-with-backoff specifically for 429s
# (a transient "slow down", not "this page doesn't exist" - conflating
# the two would wrongly drop real jobs that just got rate-limited).
FETCH_DELAY_S = 1.2
SEARCH_PAGE_DELAY_S = 1.0
DELAY_CAP_S = 8.0
DELAY_GROWTH = 1.6
SUCCESS_STREAK_TO_DECAY = 5
DELAY_DECAY = 0.85
MAX_429_RETRIES = 3
RETRY_BACKOFF_S = (3, 8, 20)


def normalize_days_since_updated(value) -> int:
    """Allowlist Built In UI filter values only; raise ValueError otherwise."""
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"days_since_updated must be one of {SUPPORTED_DAYS_SINCE_UPDATED}"
        ) from None
    if days not in SUPPORTED_DAYS_SINCE_UPDATED:
        raise ValueError(
            f"days_since_updated must be one of {SUPPORTED_DAYS_SINCE_UPDATED}, got {days}"
        )
    return days


def build_search_url(
    term: str,
    page: int = 1,
    *,
    days_since_updated: int = DEFAULT_DAYS_SINCE_UPDATED,
    experience_aliases: list[str] | None = None,
    country: str = SEARCH_COUNTRY,
) -> str:
    """Build a Built In jobs search URL (experience path + query filters).

    Pagination uses ``?page=N`` - the form Built In's own pager links use.
    Re-verified live 2026-08-05 with an A/B of both forms (with and without
    ``country``/experience filters): ``?page=2`` returned 25 URLs none of
    which were on page 1, while the ``/2`` path segment returned page 1's
    markup again (0-1 new URLs). A path segment silently caps every term at
    one page, so do not switch back without re-running that A/B.
    """
    days = normalize_days_since_updated(days_since_updated)
    aliases = experience_aliases if experience_aliases is not None else EXPERIENCE_LEVEL_ALIASES
    filter_prefix = "/".join(aliases)
    q = quote(term)
    url = (
        f"{BASE}/jobs/{filter_prefix}?search={q}"
        f"&daysSinceUpdated={days}&country={quote(country)}"
    )
    if page > 1:
        url += f"&page={page}"
    return url

# Adaptive pacing for one scrape run (search + detail share the same throttle).
_current_delay_s = FETCH_DELAY_S
_success_streak = 0
_pw_fallback_hits = 0


def log(msg: str, *, err: bool = False) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr if err else sys.stdout, flush=True)


def _bump_delay_after_429() -> None:
    global _current_delay_s, _success_streak
    _success_streak = 0
    _current_delay_s = min(DELAY_CAP_S, max(_current_delay_s, FETCH_DELAY_S) * DELAY_GROWTH)
    log(f"pacing: delay raised to {_current_delay_s:.1f}s after 429")


def _note_fetch_success() -> None:
    global _current_delay_s, _success_streak
    _success_streak += 1
    if _success_streak >= SUCCESS_STREAK_TO_DECAY and _current_delay_s > FETCH_DELAY_S:
        _current_delay_s = max(FETCH_DELAY_S, _current_delay_s * DELAY_DECAY)
        _success_streak = 0
        log(f"pacing: delay cooled to {_current_delay_s:.1f}s")


def adaptive_sleep(*, search: bool = False) -> None:
    """Sleep using the adaptive delay (search uses a slightly lighter floor)."""
    base = SEARCH_PAGE_DELAY_S if search else FETCH_DELAY_S
    delay = max(base, _current_delay_s) if not search else max(SEARCH_PAGE_DELAY_S, _current_delay_s * 0.85)
    time.sleep(delay)


def _fetch_html_http(url: str) -> tuple[str | None, bool]:
    """HTTP-only fetch. Returns (html_or_None, hit_429).

    hit_429 is True when we exhausted retries specifically due to 429s
    (Playwright fallback is appropriate). Other failures leave hit_429 False.
    """
    saw_429 = False
    for attempt in range(MAX_429_RETRIES + 1):
        req = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8", errors="replace")
                _note_fetch_success()
                return html, False
        except HTTPError as exc:
            if exc.code == 429:
                saw_429 = True
                _bump_delay_after_429()
                if attempt < MAX_429_RETRIES:
                    wait_s = RETRY_BACKOFF_S[attempt]
                    log(
                        f"warn: 429 for {url}, retrying in {wait_s}s "
                        f"(attempt {attempt + 1}/{MAX_429_RETRIES})",
                        err=True,
                    )
                    time.sleep(wait_s)
                    continue
                log(f"warn: HTTP 429 exhausted for {url}", err=True)
                return None, True
            log(f"warn: fetch failed for {url}: {exc}", err=True)
            return None, False
        except URLError as exc:
            log(f"warn: fetch failed for {url}: {exc}", err=True)
            return None, False
    return None, saw_429


def fetch_html(url: str) -> str | None:
    """HTTP first; after exhausted 429s, one headless Playwright attempt.

    Returns None for a genuine failure (404, malformed, PW miss) - callers
    treat that as "skip this URL", not as a crash.
    """
    global _pw_fallback_hits
    html, hit_429 = _fetch_html_http(url)
    if html is not None:
        return html
    if not hit_429:
        return None
    try:
        from pw_fetch_html import fetch_html_playwright
    except ImportError:
        log("warn: pw_fetch_html unavailable; cannot Playwright-fallback", err=True)
        return None
    log(f"pw fallback after 429: {url}")
    pw_html = fetch_html_playwright(url, log=lambda m: log(f"pw_fetch: {m}", err=True))
    if pw_html:
        _pw_fallback_hits += 1
        _note_fetch_success()
        log(f"pw fallback ok for {url}")
        return pw_html
    log(f"warn: pw fallback failed for {url}", err=True)
    return None


def extract_job_urls(search_html: str) -> list[str]:
    return sorted(set(re.findall(r'href="(/job/[^"?]+)"', search_html)))


def unwrap_tracking_redirect(url: str) -> str:
    """Some listings' howToApply is wrapped in an ad-tech click-tracking
    redirect rather than a direct company link - observed live on real
    Optum/UnitedHealth postings: ad.doubleclick.net/ddm/clk/...;...;k?
    <real_url>. Following the tracker redirect would still work (it's a
    genuine HTTP redirect), but landing directly on the real destination
    is more reliable than depending on that hop succeeding."""
    if "doubleclick.net" in url and "?" in url:
        tail = url.split("?", 1)[1]
        if tail.startswith("http://") or tail.startswith("https://"):
            return tail
    return url


_JOB_INIT_RE = re.compile(r"Builtin\.jobPostInit\(")
_META_DESC_RE = re.compile(r'<meta name="description" content="([^"]+)"')
_LOCATION_RE = re.compile(r"^.+? is hiring for an? .+? in (.+?)\.\s*Find more details")
# The posted date IS on every job page - in the embedded schema.org JobPosting
# ld+json as "datePosted":"YYYY-MM-DD" (verified live: present on 18/18 sampled
# postings). It's absent from the jobPostInit JSON, which earlier led to the
# wrong assumption that no date was available; the ld+json block has it.
_DATE_POSTED_RE = re.compile(r'"datePosted"\s*:\s*"([^"]+)"')
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
# Card/header chrome carries only a relative string ("Posted 2 Days Ago",
# "Posted Yesterday", "Reposted 3 Hours Ago"). Day-granular at best, and it
# drifts as the page ages, so it never overwrites a real "datePosted" - it
# lands in date_posted_fallback and renders with the "~" approximate marker.
_RELATIVE_POSTED_RE = re.compile(
    r"(?:Re)?[Pp]osted\s+(?:(\d+)\+?\s*(minute|hour|day|week|month)s?\s*ago"
    r"|(today|yesterday))",
    re.IGNORECASE,
)
_RELATIVE_UNIT_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30}


def extract_date_posted(html: str) -> tuple[str | None, str | None]:
    """(exact, approximate) posted dates as YYYY-MM-DD, either may be None.

    ``exact`` comes from the embedded schema.org JobPosting "datePosted".
    ``approximate`` is derived from a relative "Posted N Days Ago" string and
    is only meaningful when ``exact`` is None.
    """
    exact = None
    md = _DATE_POSTED_RE.search(html)
    if md:
        raw = md.group(1).strip()
        # Normalize an ISO datetime ("2026-08-04T..." / "2026-08-04") to a
        # plain YYYY-MM-DD; leave any other format untouched for the write step.
        exact = raw[:10] if _ISO_DATE_RE.match(raw) else raw

    approx = None
    mr = _RELATIVE_POSTED_RE.search(html)
    if mr:
        if mr.group(3):
            days = 0 if mr.group(3).lower() == "today" else 1
        else:
            days = int(mr.group(1)) * _RELATIVE_UNIT_DAYS[mr.group(2).lower()]
        approx = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    return exact, approx


def _extract_job_init_json(html: str) -> dict | None:
    m = _JOB_INIT_RE.search(html)
    if not m:
        return None
    start = m.end()
    depth = 0
    end = None
    for i in range(start, len(html)):
        c = html[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return None
    try:
        return json.loads(html[start:end])
    except json.JSONDecodeError:
        return None


def parse_job_page(url: str, search_term: str) -> dict | None:
    html = fetch_html(url)
    if not html:
        return None

    data = _extract_job_init_json(html)
    if not data:
        return None
    job = data.get("job") or {}

    if job.get("isEasyApply"):
        return None  # never use Built In's own hosted apply form

    how_to_apply = (job.get("howToApply") or "").strip()
    if not how_to_apply.startswith(("http://", "https://")):
        return None  # not a real clickable link (e.g. plain mail-in instructions)
    apply_url = unwrap_tracking_redirect(how_to_apply)

    company = job.get("companyName") or ""
    title = job.get("title") or ""
    if not company or not title:
        return None

    location = None
    m = _META_DESC_RE.search(html)
    if m:
        m2 = _LOCATION_RE.match(m.group(1))
        if m2:
            location = m2.group(1)

    date_posted, date_posted_approx = extract_date_posted(html)

    soup = BeautifulSoup(html, "html.parser")
    role_header = soup.find("div", class_="bg-midnight")
    description = role_header.find_next_sibling("div").get_text(separator="\n", strip=True) \
        if role_header and role_header.find_next_sibling("div") else ""
    if not description:
        return None

    return {
        "title": title,
        "company": company,
        "site": "builtin",
        "job_url": url,
        "job_url_direct": apply_url,
        "description": description,
        "date_posted": date_posted,  # from schema.org ld+json "datePosted" (YYYY-MM-DD); None only if absent
        "date_posted_fallback": date_posted_approx if not date_posted else None,
        "job_type": "fulltime",
        "location": location,
        "search_term": search_term,
    }


def collect_job_urls(
    max_pages_per_term: int,
    *,
    days_since_updated: int = DEFAULT_DAYS_SINCE_UPDATED,
    terms: list[str] | None = None,
) -> dict[str, str]:
    """Returns {job_url: search_term_that_found_it} - first term to find a
    URL wins the attribution, matching scout.py's dedup-by-url approach.

    Pages up to max_pages_per_term (hard ceiling). For each term, stops early
    after CONSECUTIVE_EMPTY_PAGES_STOP consecutive pages that add no new job
    URLs (empty page or only duplicates already seen this run). Fetch failure
    still aborts that term immediately."""
    days = normalize_days_since_updated(days_since_updated)
    search_terms = list(terms) if terms is not None else list(SEARCH_TERMS)
    urls: dict[str, str] = {}
    for term in search_terms:
        consecutive_empty = 0
        for page in range(1, max_pages_per_term + 1):
            search_url = build_search_url(term, page, days_since_updated=days)
            html = fetch_html(search_url)
            if not html:
                break  # fetch failure - abort this term
            found = extract_job_urls(html)
            new = 0
            for path in found:
                full = BASE + path
                if full not in urls:
                    urls[full] = term
                    new += 1
            log(f"  {term!r} page {page}: {len(found)} listed, {new} new")
            if new == 0:
                consecutive_empty += 1
                if consecutive_empty >= CONSECUTIVE_EMPTY_PAGES_STOP:
                    log(
                        f"  {term!r}: early-stop after {consecutive_empty} consecutive "
                        f"pages with no new URLs"
                    )
                    break
            else:
                consecutive_empty = 0
            if page < max_pages_per_term:
                adaptive_sleep(search=True)
    return urls


def _load_existing_listings(out_path: Path) -> list[dict]:
    if not out_path.exists() or out_path.stat().st_size == 0:
        return []
    try:
        rows = json.loads(out_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def main() -> None:
    run_start = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--max-pages-per-term", type=int, default=DEFAULT_MAX_PAGES_PER_TERM)
    parser.add_argument(
        "--days-since-updated",
        type=int,
        default=DEFAULT_DAYS_SINCE_UPDATED,
        choices=list(SUPPORTED_DAYS_SINCE_UPDATED),
        help="Built In New Jobs filter: 1 / 3 / 7 / 30 (default: 1 = past 24 hours)",
    )
    parser.add_argument(
        "--skip-urls", default=None,
        help="JSON array of URL keys to skip (jobs.json / blocked / prior listing)",
    )
    args = parser.parse_args()
    days_since_updated = normalize_days_since_updated(args.days_since_updated)

    out_path = Path(args.out) if args.out else Path(__file__).parent.parent / "listings" / f"{date.today().isoformat()}-builtin.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    skip_keys = load_skip_urls_file(Path(args.skip_urls) if args.skip_urls else None)
    listings = _load_existing_listings(out_path)
    already_have: set[str] = set()
    for row in listings:
        for f in ("job_url", "job_url_direct", "apply_url"):
            u = row.get(f)
            if u:
                already_have.add(u)
    if listings:
        log(f"seeded {len(listings)} listing(s) from existing {out_path.name}")
    if skip_keys:
        log(f"skip-urls: {len(skip_keys)} known key(s)")

    log(
        f"collecting job URLs from search results "
        f"(daysSinceUpdated={days_since_updated}, country={SEARCH_COUNTRY})..."
    )
    url_to_term = collect_job_urls(
        args.max_pages_per_term, days_since_updated=days_since_updated
    )
    log(f"found {len(url_to_term)} unique job URLs across {len(SEARCH_TERMS)} search terms")

    skipped_unusable = 0  # Easy Apply, or no real apply link - a genuine "don't want this one"
    fetch_failed = 0  # 404/malformed/exhausted 429 retries - not the same thing, don't conflate in the log
    # Only fetch detail pages we don't already have.
    to_fetch = {
        url: term for url, term in url_to_term.items()
        if url not in already_have and not url_is_known(url, skip_keys)
    }
    skipped_known = len(url_to_term) - len(to_fetch)
    if skipped_known:
        log(f"skipping {skipped_known} URL(s) already known / previously collected")
    total = len(to_fetch)
    for i, (url, term) in enumerate(to_fetch.items(), start=1):
        try:
            job = parse_job_page(url, term)
        except Exception as exc:
            log(f"warn: parse failed for {url}: {exc}", err=True)
            fetch_failed += 1
            continue
        if job:
            listings.append(job)
        elif job is None:
            # parse_job_page returns None both when the page just wasn't
            # usable (Easy Apply/no link) and when the fetch itself failed
            # after retries - fetch_html already logged the failure case,
            # so anything reaching here without a prior fetch-failure log
            # line is the "unusable" case. Simplest reliable signal: check
            # whether the page was fetchable at all, one more time isn't
            # worth it - just bucket both under skipped_unusable, the
            # per-URL warn lines above already distinguish real fetch
            # failures for anyone reading the log.
            skipped_unusable += 1
        if total and (i % 20 == 0 or i == total):
            log(f"  processed {i}/{total} ({len(listings)} usable so far)")
            # Written periodically, not just at the end - if this run gets
            # killed by the caller's own timeout, whatever's completed so
            # far survives on disk instead of the whole run vanishing.
            out_path.write_text(json.dumps(listings, indent=2, default=str))
        if i < total:
            adaptive_sleep(search=False)

    out_path.write_text(json.dumps(listings, indent=2, default=str))
    log(f"skipped {skipped_unusable} (Easy Apply, no usable apply link, or fetch failure)")
    if skipped_known:
        log(f"skipped {skipped_known} (already known / collected)")
    if _pw_fallback_hits:
        log(f"playwright fallback succeeded {_pw_fallback_hits} time(s)")
    log(f"wrote {len(listings)} listings -> {out_path} (total {time.monotonic() - run_start:.1f}s)")


if __name__ == "__main__":
    main()
