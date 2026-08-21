#!/usr/bin/env python3
"""RSS bundle scraper — We Work Remotely, Authentic Jobs, Jobspresso (optional).

Sign-in-free public RSS feeds only. Maps items to the standard listing schema.
Feeds have no days-back query param — keep every item in the current XML.
WWR: programming + devops + back-end (not sales/marketing/design). Authentic
Jobs: split keyword queries (ML / data / mlops) so WP search is not one giant
AND. Jobspresso: site feed, still title-filtered.

Usage:
  python3 scrape_rss_feeds.py [--out PATH]

Writes a JSON array of listings to --out (default: ../listings/<date>-rss_feeds.json).
"""
from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

from india_scrape_common import ROOT, dedup_by_url, log, write_listings
from known_job_urls import filter_out_known_listings, load_skip_urls_file  # noqa: E402

from scrape_ats import RELEVANT_KEYWORDS, clean_html_content  # noqa: E402

SITE = "rss_feeds"
AJ_NS = "https://authenticjobs.com"
AJ_BASE = (
    "https://authenticjobs.com/?feed=job_feed&search_location=remote"
    "&search_keywords="
)

# (parser_id, url, log_label)
FEEDS: list[tuple[str, str, str]] = [
    (
        "weworkremotely",
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "weworkremotely-programming",
    ),
    (
        "weworkremotely",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "weworkremotely-devops",
    ),
    (
        "weworkremotely",
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "weworkremotely-backend",
    ),
    (
        "authenticjobs",
        AJ_BASE + "machine+learning",
        "authenticjobs-ml",
    ),
    (
        "authenticjobs",
        AJ_BASE + "data+scientist",
        "authenticjobs-data-scientist",
    ),
    (
        "authenticjobs",
        AJ_BASE + "data+engineer",
        "authenticjobs-data-engineer",
    ),
    (
        "authenticjobs",
        AJ_BASE + "mlops",
        "authenticjobs-mlops",
    ),
    (
        "authenticjobs",
        AJ_BASE + "analytics+engineer",
        "authenticjobs-analytics",
    ),
    (
        "jobspresso",
        "https://jobspresso.co/feed/",
        "jobspresso",
    ),
]


def is_relevant(title: str) -> bool:
    t = (title or "").lower()
    return any(kw in t for kw in RELEVANT_KEYWORDS)


def _parse_date(raw: str | None) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else None


def fetch_rss(url: str) -> ET.Element | None:
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; job-hunter-agent/1.0)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    try:
        with urlopen(req, timeout=25) as resp:
            return ET.fromstring(resp.read())
    except Exception as exc:
        log(f"warn: RSS fetch failed for {url}: {exc}", err=True)
        return None


def _item_text(item: ET.Element, tag: str) -> str:
    el = item.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""


def _aj_field(item: ET.Element, local: str) -> str:
    el = item.find(f"{{{AJ_NS}}}{local}")
    return (el.text or "").strip() if el is not None and el.text else ""


def _content_encoded(item: ET.Element) -> str:
    for tag in ("{http://purl.org/rss/1.0/modules/content/}encoded", "description"):
        el = item.find(tag)
        if el is not None and (el.text or "").strip():
            return el.text or ""
    return _item_text(item, "description")


def _split_company_title(raw_title: str) -> tuple[str, str]:
    title = (raw_title or "").strip()
    if ":" in title:
        company, role = title.split(":", 1)
        return company.strip(), role.strip()
    return "", title


def parse_weworkremotely(root: ET.Element) -> list[dict]:
    out: list[dict] = []
    for item in root.findall(".//item"):
        raw_title = _item_text(item, "title")
        company, title = _split_company_title(raw_title)
        if not title:
            continue
        if not is_relevant(title):
            continue
        job_url = _item_text(item, "link")
        if not job_url:
            continue
        region = _item_text(item, "region")
        description = clean_html_content(_content_encoded(item))
        out.append({
            "title": title,
            "company": company,
            "site": SITE,
            "job_url": job_url,
            "job_url_direct": job_url,
            "description": description,
            "date_posted": _parse_date(_item_text(item, "pubDate")),
            "job_type": "fulltime",
            "location": region or None,
            "search_term": "us:rss:weworkremotely",
        })
    return out


def parse_authenticjobs(root: ET.Element) -> list[dict]:
    out: list[dict] = []
    for item in root.findall(".//item"):
        title = _item_text(item, "title")
        if not title or not is_relevant(title):
            continue
        job_url = _item_text(item, "link")
        if not job_url:
            continue
        company = _aj_field(item, "company")
        location = _aj_field(item, "location")
        description = clean_html_content(_content_encoded(item))
        out.append({
            "title": title,
            "company": company,
            "site": SITE,
            "job_url": job_url,
            "job_url_direct": job_url,
            "description": description,
            "date_posted": _parse_date(_item_text(item, "pubDate")),
            "job_type": (_aj_field(item, "job_type") or "fulltime").lower(),
            "location": location or None,
            "search_term": "us:rss:authenticjobs",
        })
    return out


def parse_jobspresso(root: ET.Element) -> list[dict]:
    out: list[dict] = []
    for item in root.findall(".//item"):
        raw_title = _item_text(item, "title")
        company, title = _split_company_title(raw_title)
        if not title:
            title = raw_title
        if not is_relevant(title):
            continue
        job_url = _item_text(item, "link")
        if not job_url:
            continue
        description = clean_html_content(_content_encoded(item))
        out.append({
            "title": title,
            "company": company,
            "site": SITE,
            "job_url": job_url,
            "job_url_direct": job_url,
            "description": description,
            "date_posted": _parse_date(_item_text(item, "pubDate")),
            "job_type": "fulltime",
            "location": None,
            "search_term": "us:rss:jobspresso",
        })
    return out


PARSERS = {
    "weworkremotely": parse_weworkremotely,
    "authenticjobs": parse_authenticjobs,
    "jobspresso": parse_jobspresso,
}


def scrape() -> list[dict]:
    all_jobs: list[dict] = []
    for feed_id, url, label in FEEDS:
        root = fetch_rss(url)
        if root is None:
            continue
        parser = PARSERS.get(feed_id)
        if not parser:
            continue
        jobs = parser(root)
        log(f"  got {len(jobs)} relevant results from rss:{label}")
        all_jobs.extend(jobs)
    all_jobs = dedup_by_url(all_jobs)
    log(f"  got {len(all_jobs)} relevant results from rss-feeds/all")
    return all_jobs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--skip-urls", default=None,
        help="JSON array of URL keys to drop (jobs.json / blocked / prior listing)",
    )
    args = parser.parse_args()

    out_path = (
        Path(args.out) if args.out
        else ROOT / "listings" / f"{date.today().isoformat()}-rss_feeds.json"
    )

    skip_keys = load_skip_urls_file(Path(args.skip_urls) if args.skip_urls else None)
    if skip_keys:
        log(f"skip-urls: {len(skip_keys)} known key(s)")
    listings = scrape()
    listings, skipped = filter_out_known_listings(listings, skip_keys)
    if skipped:
        log(f"skipped {skipped} already-known URL(s)")
    write_listings(out_path, listings)
    log(f"wrote {len(listings)} listings -> {out_path}")


if __name__ == "__main__":
    main()
