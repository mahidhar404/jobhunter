#!/usr/bin/env python3
"""Hirist (India) scraper — public keyword API + job sitemap (no login).

Authenticated ``/job/jobfeed`` needs login. Public
``https://gladiator.hirist.tech/job/keyword/`` returns listings without auth.
We also pull job URLs from ``new_sitemap-j-*.xml.gz`` when present.

Usage:
  python3 scrape_hirist.py [--out PATH] [--max-pages N]
"""
from __future__ import annotations

import argparse
import gzip
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

from india_scrape_common import (
    ROOT,
    SEARCH_TERMS,
    dedup_by_url,
    fetch_json,
    fetch_html,
    is_within_days,
    log,
    polite_sleep,
    write_listings,
)
from known_job_urls import filter_out_known_listings, load_skip_urls_file  # noqa: E402
import derive_company  # noqa: E402

SITE = "hirist"
BASE = "https://www.hirist.tech"
KEYWORD_API = "https://gladiator.hirist.tech/job/keyword/"
SITEMAP_INDEX = f"{BASE}/new_sitemap_index.xml"
REQUEST_DELAY_S = 1.0
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
API_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Origin": BASE,
    "Referer": f"{BASE}/",
    "version": "2",
}


def _records(data) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("data", "jobs", "results", "jobfeed", "docs", "hits", "list"):
        val = data.get(key)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)]
        if isinstance(val, dict):
            inner = val.get("jobs") or val.get("results") or val.get("data")
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
    return []


def _job_url(job: dict) -> str:
    for key in ("url", "job_url", "jobUrl", "seoUrl", "seo_url", "link", "seoURL"):
        val = job.get(key)
        if val:
            return val if str(val).startswith("http") else f"{BASE}{val}"
    jid = job.get("id") or job.get("jobId") or job.get("job_id") or job.get("_id")
    slug = job.get("slug") or job.get("seoSlug") or job.get("seo_slug") or job.get("seoKey")
    if jid and slug:
        return f"{BASE}/j/{slug}-{jid}"
    if jid:
        return f"{BASE}/j/{jid}"
    return ""


def normalize_jobs(data, *, search_term: str = "") -> list[dict]:
    out: list[dict] = []
    for job in _records(data):
        url = _job_url(job)
        title = (
            job.get("title") or job.get("jobTitle") or job.get("designation") or ""
        )
        if not title and not url:
            continue
        company = (
            job.get("company") or job.get("companyName") or job.get("company_name") or ""
        )
        if isinstance(company, dict):
            company = company.get("name") or company.get("display_name") or ""
        location = job.get("location") or job.get("city") or job.get("locations") or ""
        if isinstance(location, list):
            location = ", ".join(
                (x.get("name") if isinstance(x, dict) else str(x)) for x in location if x
            )
        elif isinstance(location, dict):
            location = location.get("name") or location.get("city") or ""
        desc = (
            job.get("description") or job.get("jobDescription")
            or job.get("job_description") or job.get("summary") or ""
        )
        sal = job.get("salary") or job.get("ctc") or job.get("compensation")
        if sal:
            desc = f"{desc}\nSalary: {sal}".strip()
        posted_raw = (
            job.get("postedDate") or job.get("date_posted")
            or job.get("createdAt") or job.get("posted_on") or ""
        )
        posted = str(posted_raw)[:10] if posted_raw else None
        if posted and not is_within_days(posted, max_days=10):
            continue
        if not title:
            # Sitemap-only rows may only have a URL.
            title = re.sub(r"[-_]+", " ", Path(url).name).strip() or "Hirist role"
        if not url:
            continue
        if not company:
            # Hirist's API often leaves company blank but embeds it in the
            # title as "Company - Role - Detail". Strip it from the title too,
            # otherwise every listing reads like a different job at a glance.
            company, cleaned = derive_company.from_hirist_title(title)
            if not company:
                company, cleaned = derive_company.company_from_corporate_suffix(title)
            if company and cleaned:
                title = cleaned
        out.append({
            "title": title,
            "company": company,
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


def scrape_keyword_api(*, max_pages: int, page_size: int = 40) -> list[dict]:
    listings: list[dict] = []
    for term in SEARCH_TERMS:
        for page in range(0, max_pages):
            params = {
                "page": page,
                "size": page_size,
                "keyword": term,
            }
            # fetch_json doesn't take params dict easily — build URL
            q = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
            url = f"{KEYWORD_API}?{q}"
            data = fetch_json(url, headers=API_HEADERS)
            rows = normalize_jobs(data, search_term=term)
            if not rows:
                break
            listings.extend(rows)
            log(f"  got {len(rows)} results from {SITE}-keyword/{term} p{page}")
            # stop if API says no more
            if isinstance(data, dict) and data.get("hasMore") is False:
                break
            polite_sleep(REQUEST_DELAY_S)
    return dedup_by_url(listings)


def _parse_sitemap_locs(xml_bytes: bytes) -> list[str]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    locs: list[str] = []
    for el in root.iter():
        if el.tag.endswith("loc") and el.text:
            locs.append(el.text.strip())
    return locs


def scrape_job_sitemap(*, max_urls: int = 500) -> list[dict]:
    """Pull /j/ URLs from Hirist job sitemaps (gzipped)."""
    idx = fetch_html(SITEMAP_INDEX, headers={"User-Agent": BROWSER_UA, "Accept": "*/*"})
    if not idx:
        return []
    child_maps = [
        loc for loc in _parse_sitemap_locs(idx.encode("utf-8"))
        if "sitemap-j" in loc or "/j-" in loc
    ]
    if not child_maps:
        log(f"warn: {SITE} sitemap index has no job sitemaps", err=True)
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for sm_url in child_maps:
        log(f"fetching {SITE} sitemap {sm_url}")
        try:
            import urllib.request
            req = urllib.request.Request(
                sm_url, headers={"User-Agent": BROWSER_UA, "Accept": "*/*"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
        except Exception as exc:  # noqa: BLE001
            log(f"warn: sitemap fetch failed: {exc}", err=True)
            continue
        if sm_url.endswith(".gz") or raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except OSError as exc:
                log(f"warn: gzip decompress failed: {exc}", err=True)
                continue
        for loc in _parse_sitemap_locs(raw):
            if "/j/" not in loc or loc in seen:
                continue
            seen.add(loc)
            # URL shape: /j/slug-id
            slug = loc.rstrip("/").split("/")[-1]
            title = re.sub(r"-\d+$", "", slug).replace("-", " ").strip() or "Hirist role"
            title = title.title()
            # Hirist titles are usually "Company - Role - Detail". Without the
            # company these rows all died on dedup's no_company filter, which
            # is why the sitemap contributed 1000 rows and 0 jobs.
            company, cleaned = derive_company.from_hirist_title(title)
            if not company:
                # The sitemap flattens "Company - Role" into one slug, so the
                # separator is gone. Fall back to a corporate suffix in the
                # leading words ("Vunet Systems Golang Developer").
                company, cleaned = derive_company.company_from_corporate_suffix(title)
            if cleaned:
                title = cleaned
            out.append({
                "title": title,
                "company": company,
                "site": SITE,
                "job_url": loc,
                "job_url_direct": loc,
                "description": "",
                "date_posted": None,
                "job_type": "fulltime",
                "location": "India",
                "search_term": f"india:{SITE}:sitemap",
            })
            if len(out) >= max_urls:
                return out
        polite_sleep(REQUEST_DELAY_S)
    return out


def scrape(*, max_pages: int) -> list[dict]:
    listings = scrape_keyword_api(max_pages=max(1, max_pages))
    # Supplement with sitemap job URLs (broad coverage, titles from slug).
    sitemap_rows = scrape_job_sitemap(max_urls=800)
    if sitemap_rows:
        log(f"  got {len(sitemap_rows)} results from {SITE}-sitemap")
        listings.extend(sitemap_rows)
    if not listings:
        log(
            f"disabled/skipped ({SITE}): keyword API + sitemap returned 0",
            err=True,
        )
    return dedup_by_url(listings)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--skip-urls", default=None)
    args = parser.parse_args()

    out_path = (
        Path(args.out) if args.out
        else ROOT / "listings" / f"{date.today().isoformat()}-hirist.json"
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
