#!/usr/bin/env python3
"""Direct scraper for Built In (builtin.com) - a job board with no public
API and no JobSpy support (checked live: JobSpy's supported sites are
LinkedIn/Indeed/Glassdoor/Google/ZipRecruiter/Bayt/Naukri/BDJobs, no
Built In). Reverse-engineered live against real pages rather than any
official docs, since none exist:

- Search results (builtin.com/jobs?search=<term>&page=<n>) are plain
  server-rendered HTML with real /job/<slug>/<id> links directly in the
  markup - no JS execution needed to discover them.
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

Writes a JSON array of listings (same schema as scout.py/scrape_ats.py)
to --out (default: ../listings/<date>-builtin.json).
"""
import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

BASE = "https://builtin.com"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
# A smaller, broad-coverage subset of scout.py's full 14-term list - Built
# In's own search already ranks by relevance per term, and dedup_listings.py
# downstream re-filters every listing by the full RELEVANT_KEYWORDS list
# anyway, so this only needs enough terms to surface the bulk of postings,
# not exhaustive phrase coverage.
SEARCH_TERMS = [
    "machine learning engineer", "data scientist", "data engineer",
    "ai engineer", "applied scientist",
]
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
# rather than just paging deeper into it. 15 pages is a safety margin
# above the largest observed filtered result count (10 pages for "data
# scientist"); collect_job_urls() already stops early via its
# empty-results break once a term's filtered results run out.
DAYS_SINCE_UPDATED = 7  # postedDateFilterOptions alias "week" (id 3, filterValue 7) - "past week" per explicit ask
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
MAX_429_RETRIES = 3
RETRY_BACKOFF_S = (3, 8, 20)


def log(msg: str, *, err: bool = False) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr if err else sys.stdout, flush=True)


def fetch_html(url: str) -> str | None:
    """Returns None for a genuine failure (404, malformed response, out of
    retries on repeated 429s) - callers treat that as "skip this URL",
    not as a crash."""
    for attempt in range(MAX_429_RETRIES + 1):
        req = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            if exc.code == 429 and attempt < MAX_429_RETRIES:
                wait_s = RETRY_BACKOFF_S[attempt]
                log(f"warn: 429 for {url}, retrying in {wait_s}s (attempt {attempt + 1}/{MAX_429_RETRIES})", err=True)
                time.sleep(wait_s)
                continue
            log(f"warn: fetch failed for {url}: {exc}", err=True)
            return None
        except URLError as exc:
            log(f"warn: fetch failed for {url}: {exc}", err=True)
            return None
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
        "date_posted": None,  # not reliably available anywhere on the page - left for dedup/write step to handle as unknown
        "job_type": "fulltime",
        "location": location,
        "search_term": search_term,
    }


def collect_job_urls(max_pages_per_term: int) -> dict[str, str]:
    """Returns {job_url: search_term_that_found_it} - first term to find a
    URL wins the attribution, matching scout.py's dedup-by-url approach."""
    urls: dict[str, str] = {}
    filter_prefix = "/".join(EXPERIENCE_LEVEL_ALIASES)
    for term in SEARCH_TERMS:
        for page in range(1, max_pages_per_term + 1):
            q = quote(term)
            search_url = (
                f"{BASE}/jobs/{filter_prefix}?search={q}&daysSinceUpdated={DAYS_SINCE_UPDATED}"
                + (f"&page={page}" if page > 1 else "")
            )
            html = fetch_html(search_url)
            if not html:
                break
            found = extract_job_urls(html)
            if not found:
                break  # ran out of results for this term
            new = 0
            for path in found:
                full = BASE + path
                if full not in urls:
                    urls[full] = term
                    new += 1
            log(f"  {term!r} page {page}: {len(found)} listed, {new} new")
            if page < max_pages_per_term:
                time.sleep(SEARCH_PAGE_DELAY_S)
    return urls


def main() -> None:
    run_start = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--max-pages-per-term", type=int, default=DEFAULT_MAX_PAGES_PER_TERM)
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else Path(__file__).parent.parent / "listings" / f"{date.today().isoformat()}-builtin.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    log("collecting job URLs from search results...")
    url_to_term = collect_job_urls(args.max_pages_per_term)
    log(f"found {len(url_to_term)} unique job URLs across {len(SEARCH_TERMS)} search terms")

    listings = []
    skipped_unusable = 0  # Easy Apply, or no real apply link - a genuine "don't want this one"
    fetch_failed = 0  # 404/malformed/exhausted 429 retries - not the same thing, don't conflate in the log
    total = len(url_to_term)
    for i, (url, term) in enumerate(url_to_term.items(), start=1):
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
        if i % 20 == 0 or i == total:
            log(f"  processed {i}/{total} ({len(listings)} usable so far)")
            # Written periodically, not just at the end - if this run gets
            # killed by the caller's own timeout, whatever's completed so
            # far survives on disk instead of the whole run vanishing.
            out_path.write_text(json.dumps(listings, indent=2, default=str))
        if i < total:
            time.sleep(FETCH_DELAY_S)

    out_path.write_text(json.dumps(listings, indent=2, default=str))
    log(f"skipped {skipped_unusable} (Easy Apply, no usable apply link, or fetch failure)")
    log(f"wrote {len(listings)} listings -> {out_path} (total {time.monotonic() - run_start:.1f}s)")


if __name__ == "__main__":
    main()
