#!/usr/bin/env python3
"""Adzuna India (`in`) job scraper via the official Adzuna Jobs API.

Adzuna is the only India-capable source here with a self-serve, documented,
ToS-friendly API. Register a free app at https://developer.adzuna.com/ to get
an APP_ID + APP_KEY, then provide them via either:

  * env vars ADZUNA_APP_ID / ADZUNA_APP_KEY (take precedence), or
  * an ignored secrets file web_keys.json at the workspace root:
        {"adzuna_app_id": "...", "adzuna_app_key": "..."}

If neither is present the source SKIPS cleanly: it writes an empty listings
file, prints a UI-visible "disabled/skipped (no Adzuna API keys)" line, and
exits 0 (never crashes discovery). Attribution: results link back to Adzuna
via redirect_url; display "Jobs by Adzuna" if surfaced publicly.

Usage:
  python3 scrape_adzuna.py [--out PATH] [--results-per-term N] [--max-pages N]

Writes a JSON array of listings (same schema as the other scrapers) to --out
(default: ../listings/<date>-adzuna-in.json).
"""
from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

from india_scrape_common import (
    ROOT,
    SEARCH_TERMS,
    dedup_by_url,
    fetch_json,
    load_web_keys,
    log,
    polite_sleep,
    write_listings,
)

SITE = "adzuna"
API_BASE = "https://api.adzuna.com/v1/api/jobs/in/search"
REQUEST_DELAY_S = 1.0


def _resolve_keys() -> tuple[str | None, str | None]:
    """Env vars win; fall back to web_keys.json. Missing → (None, None)."""
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if app_id and app_key:
        return app_id, app_key
    keys = load_web_keys()
    app_id = app_id or keys.get("adzuna_app_id") or keys.get("ADZUNA_APP_ID")
    app_key = app_key or keys.get("adzuna_app_key") or keys.get("ADZUNA_APP_KEY")
    return (app_id or None), (app_key or None)


def normalize_results(data: dict | None, *, search_term: str = "") -> list[dict]:
    """Map an Adzuna search-response dict to the shared listing shape."""
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for job in data.get("results") or []:
        if not isinstance(job, dict):
            continue
        url = job.get("redirect_url")
        if not url:
            continue
        company = ((job.get("company") or {}) if isinstance(job.get("company"), dict) else {}).get(
            "display_name"
        )
        location = ((job.get("location") or {}) if isinstance(job.get("location"), dict) else {}).get(
            "display_name"
        )
        created = job.get("created") or ""
        out.append({
            "title": job.get("title") or "",
            "company": company or "",
            "site": SITE,
            "job_url": url,
            "job_url_direct": url,
            "description": job.get("description") or "",
            "date_posted": created[:10] if isinstance(created, str) and created else None,
            "job_type": "fulltime",
            "location": location,
            "search_term": f"india:{SITE}:{search_term}" if search_term else f"india:{SITE}",
        })
    return out


def scrape(app_id: str, app_key: str, *, results_per_term: int, max_pages: int) -> list[dict]:
    listings: list[dict] = []
    for term in SEARCH_TERMS:
        for page in range(1, max_pages + 1):
            params = (
                f"app_id={app_id}&app_key={app_key}"
                f"&results_per_page={results_per_term}"
                f"&what={term.replace(' ', '%20')}"
                f"&content-type=application/json"
            )
            data = fetch_json(f"{API_BASE}/{page}?{params}")
            rows = normalize_results(data, search_term=term)
            if not rows:
                break
            listings.extend(rows)
            log(f"  got {len(rows)} results from {SITE}/{term} p{page}")
            polite_sleep(REQUEST_DELAY_S)
    return dedup_by_url(listings)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--results-per-term", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=1)
    args = parser.parse_args()

    out_path = (
        Path(args.out) if args.out
        else ROOT / "listings" / f"{date.today().isoformat()}-adzuna-in.json"
    )

    app_id, app_key = _resolve_keys()
    if not app_id or not app_key:
        # Clean skip — write empty file so the merge step finds nothing, and
        # print a UI-visible status the dashboard log surfaces.
        write_listings(out_path, [])
        log(f"disabled/skipped ({SITE}): no Adzuna API keys "
            f"(set ADZUNA_APP_ID/ADZUNA_APP_KEY or web_keys.json)")
        log(f"wrote 0 listings -> {out_path}")
        return

    listings = scrape(
        app_id, app_key,
        results_per_term=max(1, min(args.results_per_term, 50)),
        max_pages=max(1, args.max_pages),
    )
    write_listings(out_path, listings)
    log(f"wrote {len(listings)} listings -> {out_path}")


if __name__ == "__main__":
    main()
