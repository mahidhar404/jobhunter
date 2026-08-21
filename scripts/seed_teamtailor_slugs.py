#!/usr/bin/env python3
"""One-off: seed ats_companies.json slugs from blocked_urls, listings, and jobs.json.

Extracts Teamtailor hostnames, JazzHR applytojob slugs, and Pinpoint tenant slugs
from apply_url / job_url fields and appends any new entries to the registry.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_FILE = ROOT / "ats_companies.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape_ats as sa  # noqa: E402


def _listing_paths() -> list[Path]:
    paths: list[Path] = []
    jobs = ROOT / "jobs.json"
    if jobs.is_file():
        paths.append(jobs)
    blocked = ROOT / "blocked_urls.json"
    if blocked.is_file():
        paths.append(blocked)
    listings_dir = ROOT / "listings"
    if listings_dir.is_dir():
        paths.extend(sorted(listings_dir.glob("*.json")))
    return paths


def main() -> None:
    registry = sa.load_registry()
    # Verified slugs from repo history / blocked_urls when not already present.
    verified = {
        "teamtailor": [
            "spokeo.na.teamtailor.com",
            "flightstory.teamtailor.com",
        ],
        "jazzhr": [
            "brightvisiontechnologies",
            "emedlabsllc",
        ],
        "pinpoint": [
            "cardfactory",
        ],
    }
    for ats, slugs in verified.items():
        for slug in slugs:
            if slug not in registry.get(ats, []):
                registry[ats].append(slug)

    paths = _listing_paths()
    added = sa.extract_slugs(paths, registry)
    sa.save_registry(registry)

    print(f"seeded from {len(paths)} file(s); extract_slugs added {added} slug(s)")
    for ats in ("teamtailor", "jazzhr", "pinpoint"):
        slugs = registry.get(ats, [])
        print(f"  {ats}: {len(slugs)} slug(s) — {slugs[:8]}{'…' if len(slugs) > 8 else ''}")


if __name__ == "__main__":
    main()
