#!/usr/bin/env python3
"""Boards with no public listing endpoint — probe, report, never sit "off".

These used to be catalogued as ``catalog`` / ``needs_account`` /
``blocked_captcha``, which meant Discover skipped them entirely and the UI
greyed them out as "Disabled". That hides *why* a board yields nothing and
makes a dead origin look identical to one that simply had no new roles.

So every board runs on every pass. This adapter actually attempts each
board's public endpoints, writes whatever it finds (usually nothing), and
exits 0 with a specific, checkable reason. A board that starts serving public
listings again will begin returning rows here without any code change; one
that needs a login stays honest about it.

No CAPTCHA is ever solved and no login is ever used.

Usage:
  python3 scrape_probe_board.py --site turing [--out PATH]
"""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ww_scrape_common import (
    ROOT,
    dedup_by_url,
    fetch_text,
    listing,
    log,
    polite_sleep,
    write_listings,
)
from known_job_urls import filter_out_known_listings, load_skip_urls_file  # noqa: E402

DEFAULT_MAX_DAYS = 21

# site -> (urls to try, job-link regex, location label, why it usually yields 0)
PROBES: dict[str, dict] = {
    "otta": {
        "urls": ("https://www.welcometothejungle.com/en/jobs",
                 "https://otta.com/jobs"),
        "href": r"/companies/[^/]+/jobs/",
        "location": "Remote / Europe",
        "reason": ("Otta redirects to welcometothejungle.com, which answers "
                   "non-browser clients with an empty HTTP 202 and whose "
                   "search API is an Algolia proxy that 404s publicly"),
    },
    "turing": {
        "urls": ("https://www.turing.com/jobs",),
        "href": r"/jobs/[a-z0-9-]{6,}",
        "location": "Remote",
        "reason": "Turing lists roles only behind a developer account",
    },
    "hired": {
        "urls": ("https://hired.com/jobs",),
        "href": r"/jobs/[a-z0-9-]{6,}",
        "location": "Remote",
        "reason": "Hired is a candidate marketplace — roles need an account",
    },
    "jooble": {
        "urls": ("https://jooble.org/api",),
        "href": r"/desc/[0-9]+",
        "location": "Remote",
        "reason": "Jooble's API requires a partner key (403 without one)",
    },
    "producthunt_jobs": {
        "urls": ("https://www.producthunt.com/jobs",),
        "href": r"/jobs/[a-z0-9-]{4,}",
        "location": "Remote",
        "reason": "Product Hunt returns 403 to non-browser clients (Cloudflare)",
    },
    "remotetechjobs": {
        "urls": ("https://remotetechjobs.com/", "https://remotetechjobs.com/jobs"),
        "href": r"/(job|jobs)/[a-z0-9-]{4,}",
        "location": "Remote",
        "reason": ("remotetechjobs.com is no longer a job board — the domain now "
                   "serves a generic WordPress site (about/services/projects)"),
    },
    "outsourcely": {
        "urls": ("https://www.outsourcely.com/remote-jobs",),
        "href": r"/remote-[a-z-]+-jobs/[a-z0-9-]+",
        "location": "Remote",
        "reason": "outsourcely.com does not resolve/connect",
    },
    "hubstaff_talent": {
        "urls": ("https://talent.hubstaff.com/search/jobs",),
        "href": r"/jobs/[0-9]+",
        "location": "Remote",
        "reason": ("Hubstaff Talent redirects to a freelancer-profile search — "
                   "it lists people, not job postings"),
    },
    "pangian": {
        "urls": ("https://pangian.com/job-travel-remote/",),
        "href": r"/job/[a-z0-9-]+",
        "location": "Remote",
        "reason": "pangian.com now redirects to an empty GitHub Pages placeholder",
    },
    "topaijobs": {
        "urls": ("https://topai.jobs/",),
        "href": r"/jobs?/[a-z0-9-]+",
        "location": "Remote",
        "reason": "topai.jobs does not resolve/connect",
    },
    "crossover": {
        "urls": ("https://www.crossover.com/jobs",),
        "href": r"/job[s]?/[a-z0-9-]{4,}",
        "location": "Remote",
        "reason": "Crossover's job board is a client-rendered app with no public API",
    },
    "jobbatical": {
        "urls": ("https://www.jobbatical.com/jobs-and-careers",),
        "href": r"/jobs?/[a-z0-9-]{4,}",
        "location": "Relocation",
        "reason": "Jobbatical moved its board behind app.jobbatical.com (login)",
    },
    "angelhub": {
        "urls": ("https://angelhub.io/jobs", "https://angelhub.io"),
        "href": r"/jobs?/[a-z0-9-]+",
        "location": "Remote",
        "reason": "angelhub.io does not resolve/connect",
    },
    "europeremotely": {
        "urls": ("https://europeremotely.com/",),
        "href": r"/job/[a-z0-9-]+",
        "location": "Europe (Remote)",
        "reason": "europeremotely.com is a parked domain (403 from Parking/1.0)",
    },
    "germanstartups": {
        "urls": ("https://jobs.germanstartups.com/jobs",),
        "href": r"/jobs?/[0-9a-z-]+",
        "location": "Germany",
        "reason": "jobs.germanstartups.com refuses the TLS handshake",
    },
}


def probe(site: str, *, max_days: int) -> tuple[list[dict], list[str]]:
    spec = PROBES[site]
    out: list[dict] = []
    notes: list[str] = []
    href_re = re.compile(spec["href"], re.I)
    for url in spec["urls"]:
        html = fetch_text(url)
        polite_sleep(0.5)
        if not html:
            notes.append(f"{url}: unreachable")
            continue
        low = html[:4000].lower()
        if "just a moment" in low or "cf-challenge" in low:
            notes.append(f"{url}: bot-challenge wall (never CAPTCHA-solved)")
            continue
        soup = BeautifulSoup(html, "html.parser")
        hits = 0
        for a in soup.find_all("a", href=href_re):
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 5:
                continue
            row = listing(
                title=title,
                company="",
                site=site,
                job_url=urljoin(url, a["href"]),
                location=spec.get("location") or "Remote",
                max_days=max_days,
            )
            if row:
                out.append(row)
                hits += 1
        notes.append(f"{url}: {hits} listing(s)")
    return dedup_by_url(out), notes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, choices=sorted(PROBES))
    ap.add_argument("--out", default=None)
    ap.add_argument("--skip-urls", default=None)
    ap.add_argument("--max-days", type=int, default=DEFAULT_MAX_DAYS)
    args = ap.parse_args()

    site = args.site
    out_path = Path(args.out) if args.out else (
        ROOT / "listings" / f"{date.today().isoformat()}-{site}.json")
    log(f"probing board: {site}")
    rows, notes = probe(site, max_days=max(1, args.max_days))
    for note in notes:
        log(f"  {note}")
    if not rows:
        # Exit 0 on purpose: "ran, found nothing, here is why" is a completed
        # source, not a failure. A red row would be indistinguishable from a
        # crash and would hide a board that is simply quiet today.
        log(f"no public listings for {site}: {PROBES[site]['reason']}")
    skip = load_skip_urls_file(Path(args.skip_urls) if args.skip_urls else None)
    if skip:
        rows, skipped = filter_out_known_listings(rows, skip)
        log(f"skip-urls: dropped {skipped} known")
    write_listings(out_path, rows)


if __name__ == "__main__":
    main()
