#!/usr/bin/env python3
"""Fill in missing company names on archived listings, then re-export them.

`dedup_listings` drops every row whose company is blank (`no_company`), so
sources that never captured one — Hirist's and Cutshort's sitemaps, NoDesk,
Landing.jobs, We Work Remotely — contributed thousands of scraped rows and
zero jobs. The company is usually already present in the URL slug or the
title, so most rows are recoverable with no extra HTTP (see derive_company).

Rows whose company is still unknown *and* whose title is a target role can
optionally be resolved with a bounded detail-page fetch (--fetch).

Usage:
  python3 backfill_listing_companies.py --dry-run
  python3 backfill_listing_companies.py
  python3 backfill_listing_companies.py --fetch --fetch-limit 120
  python3 backfill_listing_companies.py --export listings/recovered.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))

import listings_db  # noqa: E402
from derive_company import derive  # noqa: E402

PLACEHOLDERS = {"", "unknown", "cutshort listing", "n/a", "none"}


def _is_missing(company: str | None) -> bool:
    return (company or "").strip().lower() in PLACEHOLDERS


def _company_from_page(url: str) -> str:
    """Best-effort company from a job detail page (JSON-LD, then meta)."""
    from ww_scrape_common import fetch_text

    html = fetch_text(url)
    if not html:
        return ""
    # 1. schema.org JobPosting carries hiringOrganization.name
    for blob in re.findall(
            r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            org = node.get("hiringOrganization")
            if isinstance(org, dict) and org.get("name"):
                return str(org["name"]).strip()[:80]
    # 2. og:site_name is wrong (it is the board), but many boards expose the
    #    employer in a meta tag or a company link.
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
    if m and " at " in m.group(1):
        return m.group(1).rsplit(" at ", 1)[-1].strip()[:80]
    return ""


def run(*, dry_run: bool, fetch: bool, fetch_limit: int,
        export: Path | None) -> dict:
    conn = listings_db.connect()
    rows = conn.execute(
        "SELECT url_key, job_url, title, company, site, raw FROM listings"
    ).fetchall()

    parsed = fetched = 0
    still_missing = 0
    updates: list[tuple[str, str, str]] = []   # (url_key, company, title)
    to_fetch: list[tuple[str, str, str]] = []  # (url_key, url, title)

    import dedup_listings as dl
    for url_key, url, title, company, site, _raw in rows:
        if not _is_missing(company):
            continue
        new_company, new_title = derive(site, url=url, title=title)
        if new_company:
            parsed += 1
            updates.append((url_key, new_company, new_title or title or ""))
            continue
        # Only worth a page fetch if the title would survive the role filter.
        if fetch and dl.is_relevant(title or ""):
            to_fetch.append((url_key, url, title or ""))
        else:
            still_missing += 1

    if fetch and to_fetch:
        from ww_scrape_common import polite_sleep
        for url_key, url, title in to_fetch[:max(0, fetch_limit)]:
            company = _company_from_page(url)
            polite_sleep(0.6)
            if company:
                fetched += 1
                updates.append((url_key, company, title))
            else:
                still_missing += 1
        still_missing += max(0, len(to_fetch) - max(0, fetch_limit))

    if not dry_run:
        for url_key, company, title in updates:
            row = conn.execute(
                "SELECT raw FROM listings WHERE url_key = ?", (url_key,)
            ).fetchone()
            if not row:
                continue
            try:
                raw = json.loads(row[0])
            except json.JSONDecodeError:
                raw = {}
            raw["company"] = company
            if title:
                raw["title"] = title
            conn.execute(
                "UPDATE listings SET company = ?, title = ?, raw = ? "
                "WHERE url_key = ?",
                (company, title or raw.get("title"),
                 json.dumps(raw, ensure_ascii=False), url_key))
        conn.commit()

    exported = 0
    if export is not None and not dry_run:
        keys = {u for u, _c, _t in updates}
        out = []
        for url_key, _url, _t, _c, _s, raw in conn.execute(
                "SELECT url_key, job_url, title, company, site, raw FROM listings"):
            if url_key in keys:
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
        export.parent.mkdir(parents=True, exist_ok=True)
        export.write_text(json.dumps(out, indent=2, ensure_ascii=False),
                          encoding="utf-8")
        exported = len(out)

    conn.close()
    return {"parsed": parsed, "fetched": fetched,
            "still_missing": still_missing, "exported": exported}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fetch", action="store_true",
                    help="resolve leftovers via a detail-page fetch")
    ap.add_argument("--fetch-limit", type=int, default=200)
    ap.add_argument("--export", default=None,
                    help="write the repaired rows to a listing JSON file")
    args = ap.parse_args()
    res = run(dry_run=args.dry_run, fetch=args.fetch,
              fetch_limit=args.fetch_limit,
              export=Path(args.export) if args.export else None)
    print(f"company backfill: parsed={res['parsed']} fetched={res['fetched']} "
          f"still-missing={res['still_missing']} exported={res['exported']}"
          + (" (dry-run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
