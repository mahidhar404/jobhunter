#!/usr/bin/env python3
"""Cutshort (India) curated-startup job scraper — HTML first, JSON optional.

Public JSON ``/api/v1/jobs/search`` requires an API key (HTTP 401). When keys
are absent we scrape public ``/jobs`` HTML pages instead (no login). No
CAPTCHA solving (see PLAYBOOK).

Usage:
  python3 scrape_cutshort.py [--out PATH] [--max-pages N]
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from india_scrape_common import (
    ROOT,
    SEARCH_TERMS,
    dedup_by_url,
    fetch_html,
    fetch_json,
    is_within_days,
    load_web_keys,
    log,
    polite_sleep,
    write_listings,
)
from known_job_urls import filter_out_known_listings, load_skip_urls_file  # noqa: E402
import derive_company  # noqa: E402

SITE = "cutshort"
BASE = "https://cutshort.io"
SEARCH_API = f"{BASE}/api/v1/jobs/search"
JOBS_SITEMAP = (
    "https://cutshort-data.s3.amazonaws.com/cloudfront/public/jobs-sitemap.xml"
)
REQUEST_DELAY_S = 1.5
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
JOB_PATH_RE = re.compile(r"/job/([^/?#]+)", re.I)


def _records(data) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("data", "jobs", "results", "hits", "docs"):
        val = data.get(key)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)]
        if isinstance(val, dict):
            inner = val.get("jobs") or val.get("results") or val.get("hits")
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
    return []


def _job_url(job: dict) -> str:
    for key in ("url", "job_url", "public_url", "seo_url", "link", "permalink"):
        val = job.get(key)
        if val:
            return val if str(val).startswith("http") else f"{BASE}{val}"
    slug = job.get("slug") or job.get("seo_slug")
    jid = job.get("id") or job.get("_id") or job.get("job_id")
    if slug:
        return f"{BASE}/job/{slug}"
    if jid:
        return f"{BASE}/job/{jid}"
    return ""


def normalize_jobs(data, *, search_term: str = "") -> list[dict]:
    """Map a Cutshort jobs response to shared-shape listings."""
    out: list[dict] = []
    for job in _records(data):
        url = _job_url(job)
        if not url:
            continue
        title = job.get("title") or job.get("role") or job.get("position") or ""
        company = (
            job.get("company") or job.get("companyName")
            or job.get("company_name") or job.get("organization") or ""
        )
        if isinstance(company, dict):
            company = company.get("name") or company.get("display_name") or ""
        location = job.get("location") or job.get("city") or job.get("locations") or ""
        if isinstance(location, list):
            location = ", ".join(str(x) for x in location if x)
        desc = (
            job.get("description") or job.get("jobDescription")
            or job.get("summary") or ""
        )
        sal = job.get("salary") or job.get("ctc") or job.get("compensation")
        if sal:
            desc = f"{desc}\nSalary: {sal}".strip()
        posted_raw = (
            job.get("postedDate") or job.get("date_posted")
            or job.get("createdAt") or job.get("created_at") or ""
        )
        posted = str(posted_raw)[:10] if posted_raw else None
        if posted and not is_within_days(posted, max_days=10):
            continue
        if not title:
            continue
        out.append({
            "title": title,
            "company": company or "",
            "site": SITE,
            "job_url": url,
            "job_url_direct": url,
            "description": desc or "",
            "date_posted": posted,
            "job_type": "fulltime",
            "location": location or "India",
            "search_term": f"india:{SITE}:{search_term}" if search_term else f"india:{SITE}",
        })
    return out


def _title_from_slug(slug: str) -> tuple[str, str]:
    """Return (title, company_guess) from Cutshort slug tokens.

    Slugs are ``<Role-Words>-[<City>…]-<Company>-<id>``. Returning an empty
    company here meant every sitemap row was dropped by dedup's no_company
    filter — 974 scraped rows, 0 jobs.
    """
    company, role = derive_company.company_from_role_slug(slug)
    if company and role:
        return role, company
    parts = [p for p in slug.split("-") if p]
    # Trailing token is an opaque id (mixed case + digits), not part of the title.
    if parts and re.fullmatch(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]{6,12}", parts[-1]):
        parts = parts[:-1]
    return " ".join(parts).strip(), ""


def parse_html(html: str, *, search_term: str = "") -> list[dict]:
    """Parse Cutshort /jobs HTML for /job/<slug> detail links."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen: set[str] = set()
    for a in soup.select('a[href*="/job/"]'):
        href = a.get("href") or ""
        m = JOB_PATH_RE.search(href)
        if not m:
            continue
        slug = m.group(1)
        if slug.lower() in ("jobs", "startup-jobs"):
            continue
        url = href if href.startswith("http") else f"{BASE}/job/{slug}"
        url = url.split("?")[0]
        if url in seen:
            continue
        title = (a.get_text(strip=True) or "").strip()
        company = ""
        if not title or title.lower() in ("apply now", "apply", "view"):
            title, company = _title_from_slug(slug)
        # Prefer a nearby company label if present.
        parent = a.find_parent(["div", "li", "article", "section"])
        if parent:
            for sel in (".company", ".company-name", "[class*='company']"):
                el = parent.select_one(sel)
                if el and el.get_text(strip=True):
                    company = el.get_text(strip=True)
                    break
        if not title or len(title) < 3:
            continue
        if not company:
            # Recover it from the slug rather than inventing a placeholder —
            # "Cutshort listing" passed the no_company filter but made every
            # row look like it came from the same employer.
            _t, company = _title_from_slug(slug)
        seen.add(url)
        out.append({
            "title": title,
            "company": company,
            "site": SITE,
            "job_url": url,
            "job_url_direct": url,
            "description": "",
            "date_posted": None,
            "job_type": "fulltime",
            "location": "India",
            "search_term": f"india:{SITE}:{search_term}" if search_term else f"india:{SITE}",
        })
    return out


def _api_key() -> str | None:
    key = (os.environ.get("CUTSHORT_API_KEY") or "").strip()
    if key:
        return key
    keys = load_web_keys()
    raw = keys.get("cutshort_api_key") or keys.get("CUTSHORT_API_KEY")
    return str(raw).strip() if raw else None


def scrape_api(*, max_pages: int, api_key: str) -> list[dict]:
    listings: list[dict] = []
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    for term in SEARCH_TERMS:
        for page in range(1, max_pages + 1):
            q = quote_plus(term)
            url = f"{SEARCH_API}?q={q}&page={page}&country=india"
            data = fetch_json(url, headers=headers)
            rows = normalize_jobs(data, search_term=term)
            if not rows:
                break
            listings.extend(rows)
            log(f"  got {len(rows)} results from {SITE}-api/{term} p{page}")
            polite_sleep(REQUEST_DELAY_S)
    return dedup_by_url(listings)


# Cutshort's category pages are /jobs/<slug>-jobs — slugs built from
# SEARCH_TERMS (e.g. /jobs/machine-learning) render an empty shell, which made
# every keyword path return 0 and break the loop after the bare /jobs page.
CATEGORY_PATHS = (
    "/jobs",
    "/jobs/backend-developer-jobs",
    "/jobs/frontend-developer-jobs",
    "/jobs/datascience-jobs",
    "/jobs/devops-jobs",
    "/jobs/product-based-company-jobs",
    "/jobs/startup-jobs",
    "/jobs/startup-jobs-in-bangalore-bengaluru",
    "/jobs/startup-jobs-in-hyderabad",
    "/jobs/startup-jobs-in-pune",
    "/jobs/startup-jobs-in-delhi-ncr-gurgaon-noida",
)


def scrape_html(*, max_pages: int) -> list[dict]:
    listings: list[dict] = []
    headers = {"User-Agent": BROWSER_UA, "Accept-Language": "en-IN,en;q=0.9"}
    for path in CATEGORY_PATHS:
        for page in range(1, max_pages + 1):
            url = f"{BASE}{path}" if page == 1 else f"{BASE}{path}?page={page}"
            html = fetch_html(url, headers=headers)
            rows = parse_html(html or "", search_term=path.strip("/"))
            if not rows:
                break
            listings.extend(rows)
            log(f"  got {len(rows)} results from {SITE}-html{path} p{page}")
            polite_sleep(REQUEST_DELAY_S)
    return dedup_by_url(listings)


def scrape_job_sitemap(*, max_urls: int = 800) -> list[dict]:
    """Pull /job/<slug> URLs from Cutshort's public jobs sitemap.

    The category HTML only exposes ~10 cards per page; the sitemap carries the
    whole board (tens of thousands of URLs), same trick as Hirist.
    """
    import urllib.request
    from xml.etree import ElementTree as ET

    out: list[dict] = []
    seen: set[str] = set()
    try:
        req = urllib.request.Request(
            JOBS_SITEMAP, headers={"User-Agent": BROWSER_UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001
        log(f"warn: {SITE} sitemap fetch failed: {exc}", err=True)
        return []
    if raw[:2] == b"\x1f\x8b":
        import gzip
        try:
            raw = gzip.decompress(raw)
        except OSError as exc:
            log(f"warn: {SITE} sitemap gunzip failed: {exc}", err=True)
            return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        log(f"warn: {SITE} sitemap parse failed: {exc}", err=True)
        return []
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    for url_el in root.iter(f"{ns}url"):
        loc_el = url_el.find(f"{ns}loc")
        loc = (loc_el.text or "").strip() if loc_el is not None else ""
        if not loc or "/job/" not in loc or loc in seen:
            continue
        seen.add(loc)
        slug = loc.rstrip("/").split("/")[-1]
        title, company = _title_from_slug(slug)
        if not title:
            continue
        lastmod_el = url_el.find(f"{ns}lastmod")
        lastmod = (lastmod_el.text or "").strip()[:10] if lastmod_el is not None else ""
        out.append({
            "title": title,
            "company": company or "Cutshort listing",
            "site": SITE,
            "job_url": loc,
            "job_url_direct": loc,
            "description": "",
            "date_posted": lastmod or None,
            "job_type": "fulltime",
            "location": "India",
            "search_term": f"india:{SITE}:sitemap",
        })
        if len(out) >= max_urls:
            break
    return out


def scrape(*, max_pages: int) -> list[dict]:
    api_key = _api_key()
    if api_key:
        log(f"{SITE}: using API key from env/web_keys.json")
        rows = scrape_api(max_pages=max_pages, api_key=api_key)
        if rows:
            return rows
        log(f"warn: {SITE} API returned 0 — falling back to public HTML", err=True)
    else:
        log(f"{SITE}: no API key — scraping public HTML (set CUTSHORT_API_KEY to use JSON)")
    listings = scrape_html(max_pages=max_pages)
    sitemap_rows = scrape_job_sitemap(max_urls=800)
    if sitemap_rows:
        log(f"  got {len(sitemap_rows)} results from {SITE}-sitemap")
        listings.extend(sitemap_rows)
    return dedup_by_url(listings)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument(
        "--skip-urls", default=None,
        help="JSON array of URL keys to drop (jobs.json / blocked / prior listing)",
    )
    args = parser.parse_args()

    out_path = (
        Path(args.out) if args.out
        else ROOT / "listings" / f"{date.today().isoformat()}-cutshort.json"
    )
    skip_keys = load_skip_urls_file(Path(args.skip_urls) if args.skip_urls else None)
    if skip_keys:
        log(f"skip-urls: {len(skip_keys)} known key(s)")
    listings = scrape(max_pages=max(1, args.max_pages))
    listings, skipped = filter_out_known_listings(listings, skip_keys)
    if skipped:
        log(f"skipped {skipped} already-known URL(s)")
    write_listings(out_path, listings)
    log(f"wrote {len(listings)} listings -> {out_path}")


if __name__ == "__main__":
    main()
