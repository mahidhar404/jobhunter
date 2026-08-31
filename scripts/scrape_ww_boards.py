#!/usr/bin/env python3
"""Worldwide board scraper — one process, many public JSON/RSS/HTML adapters.

Usage:
  python3 scrape_ww_boards.py --site himalayas [--out PATH]
  python3 scrape_ww_boards.py --site arbeitnow --out listings/today-arbeitnow.json

Polite HTTP only. Never solves CAPTCHA. Site ids must match discovery_sources.
"""
from __future__ import annotations

import argparse
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ww_scrape_common import (
    ROOT,
    clean_html,
    dedup_by_url,
    fetch_json,
    fetch_text,
    is_within_days,
    listing,
    log,
    normalize_posted,
    parse_rss_items,
    polite_sleep,
    write_listings,
)
from known_job_urls import filter_out_known_listings, load_skip_urls_file  # noqa: E402
import derive_company  # noqa: E402

# Recency window. Overridden per-run by --max-days (the dashboard passes the
# per-source "days" setting). Several boards keep postings live for weeks, so a
# too-tight window silently produced zero rows for them.
DEFAULT_MAX_DAYS = 21
_MAX_DAYS = DEFAULT_MAX_DAYS


def max_days() -> int:
    return _MAX_DAYS


def _from_rss(site: str, urls: list[str], *, location: str = "Remote", max_days: int | None = None) -> list[dict]:
    max_days = _MAX_DAYS if max_days is None else max_days
    out: list[dict] = []
    for url in urls:
        text = fetch_text(url, headers={"Accept": "application/rss+xml, application/xml, text/xml, */*"})
        polite_sleep(0.35)
        if not text:
            continue
        for item in parse_rss_items(text):
            pub = normalize_posted(item.get("pubDate"))
            if pub and not is_within_days(pub, max_days=max_days):
                continue
            title = item.get("title") or ""
            # "Company: Role" (We Work Remotely and other WP job feeds) —
            # checked first, otherwise the company ends up inside the title
            # and the row is dropped as no_company.
            company, cleaned = derive_company.from_weworkremotely(title)
            if company:
                title = cleaned
            else:
                # "Title at Company" or "Title (Company)"
                m = re.search(r"\bat\s+(.+)$", title, re.I)
                if m:
                    company = m.group(1).strip()[:80]
                else:
                    m2 = re.search(r"\(([^)]+)\)\s*$", title)
                    if m2:
                        company = m2.group(1).strip()[:80]
            row = listing(
                title=title,
                company=company,
                site=site,
                job_url=item.get("link") or "",
                description=item.get("description") or "",
                date_posted=pub,
                location=location,
                max_days=max_days,
            )
            if row:
                out.append(row)
    return out


def _parse_day_month(raw: str | None) -> str | None:
    """JustRemote sends `25 Aug` (no year). Assume the most recent such date."""
    if not raw:
        return None
    txt = str(raw).strip()
    for fmt in ("%d %b", "%d %B"):
        try:
            parsed = datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
        today = date.today()
        guess = parsed.replace(year=today.year)
        if guess > today:
            guess = guess.replace(year=today.year - 1)
        return guess.isoformat()
    return normalize_posted(txt)


def scrape_himalayas() -> list[dict]:
    """Himalayas public jobs API. Pages via ?offset= (?page= is ignored)."""
    out: list[dict] = []
    seen_first: str | None = None
    for offset in range(0, 1200, 20):
        data = fetch_json(f"https://himalayas.app/jobs/api?limit=20&offset={offset}")
        polite_sleep(0.4)
        if not data:
            break
        jobs = data if isinstance(data, list) else data.get("jobs") or data.get("data") or []
        if not jobs:
            break
        # Guard against an endpoint that silently ignores paging.
        first = str((jobs[0] or {}).get("guid") or (jobs[0] or {}).get("title") or "")
        if first and first == seen_first:
            break
        seen_first = first
        for job in jobs:
            if not isinstance(job, dict):
                continue
            title = job.get("title") or job.get("jobTitle") or ""
            co = job.get("company")
            company = co.get("name") if isinstance(co, dict) else str(
                co or job.get("companyName") or "")
            url = job.get("applicationLink") or job.get("url") or job.get("guid")
            if not url and job.get("companySlug"):
                url = f"https://himalayas.app/companies/{job['companySlug']}/jobs"
            if url and isinstance(url, str) and url.startswith("/"):
                url = urljoin("https://himalayas.app", url)
            loc = job.get("locationRestrictions") or job.get("location") or "Remote"
            if isinstance(loc, list):
                loc = ", ".join(str(x) for x in loc) or "Remote"
            loc = re.sub(r"[\[\]']", "", str(loc)) or "Remote"
            sal = None
            if job.get("minSalary") or job.get("maxSalary"):
                cur = job.get("currency") or "USD"
                sal = f"{cur} {job.get('minSalary') or ''}-{job.get('maxSalary') or ''}".strip()
            pub = normalize_posted(
                job.get("pubDate") or job.get("publishedAt") or job.get("createdAt"))
            row = listing(
                title=title,
                company=company,
                site="himalayas",
                job_url=str(url or ""),
                description=job.get("description") or job.get("excerpt") or "",
                date_posted=pub,
                location=str(loc),
                salary_hint=sal,
                max_days=_MAX_DAYS,
            )
            if row:
                out.append(row)
    return out


def scrape_arbeitnow() -> list[dict]:
    out: list[dict] = []
    for page in range(1, 16):
        data = fetch_json(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
        polite_sleep(0.4)
        if not isinstance(data, dict):
            break
        jobs = data.get("data") or []
        if not jobs:
            break
        for job in jobs:
            tags = " ".join(job.get("tags") or [])
            pub = normalize_posted(job.get("created_at"))
            row = listing(
                title=job.get("title") or "",
                company=job.get("company_name") or "",
                site="arbeitnow",
                job_url=job.get("url") or "",
                description=(job.get("description") or "") + f"\n{tags}",
                date_posted=pub,
                location=job.get("location") or ("Remote" if job.get("remote") else "Remote"),
                max_days=_MAX_DAYS,
            )
            if row:
                out.append(row)
    return out


def scrape_landing_jobs() -> list[dict]:
    """Landing.jobs public API. Rows carry `url`, `locations`, `published_at`."""
    out: list[dict] = []
    # The API holds ~58 jobs total; offset 100+ returns nothing.
    for offset in (0, 50, 100):
        data = fetch_json(
            f"https://landing.jobs/api/v1/jobs?limit=50&offset={offset}")
        polite_sleep(0.3)
        jobs = []
        if isinstance(data, dict):
            jobs = data.get("jobs") or data.get("data") or data.get("offers") or []
        elif isinstance(data, list):
            jobs = data
        if not jobs:
            break
        for job in jobs:
            if not isinstance(job, dict):
                continue
            company = job.get("company")
            if isinstance(company, dict):
                company = company.get("name") or ""
            url = job.get("url") or job.get("apply_url") or ""
            if url and str(url).startswith("/"):
                url = urljoin("https://landing.jobs", str(url))
            if not company:
                # The API omits the employer, but the job URL is
                # /at/<company>/<job-slug>.
                company = derive_company.from_landing_jobs(str(url))
            locs = job.get("locations")
            if isinstance(locs, list):
                city = ", ".join(
                    (loc.get("city") or loc.get("name") or "") if isinstance(loc, dict)
                    else str(loc) for loc in locs
                ).strip(", ")
            else:
                city = str(job.get("city") or job.get("location") or "")
            if not city:
                city = "Remote" if str(job.get("remote")).lower() == "true" else "Europe"
            desc = " ".join(str(job.get(k) or "") for k in
                            ("role_description", "main_requirements", "nice_to_have"))
            sal = None
            if job.get("gross_salary_low") or job.get("gross_salary_high"):
                cur = job.get("currency_code") or "EUR"
                sal = (f"{cur} {job.get('gross_salary_low') or ''}"
                       f"-{job.get('gross_salary_high') or ''}").strip()
            # published_at can be a year old while the ad is still open; prefer
            # updated_at so long-lived European ads are not date-pruned away.
            pub = normalize_posted(job.get("updated_at") or job.get("published_at")
                                   or job.get("created_at"))
            row = listing(
                title=job.get("title") or "",
                company=str(company or ""),
                site="landing_jobs",
                job_url=str(url),
                description=desc,
                date_posted=pub,
                location=city,
                salary_hint=sal,
                max_days=_MAX_DAYS,
            )
            if row:
                out.append(row)
    # RSS / Atom fallback if the API was empty
    if not out:
        out.extend(_from_rss("landing_jobs", ["https://landing.jobs/feed"],
                             location="Europe"))
    return out


def scrape_jsremotely() -> list[dict]:
    out: list[dict] = []
    # HTML scraping from jsremotely / javascript.jobs
    for url in ("https://jsremotely.com", "https://javascript.jobs/jobs"):
        html = fetch_text(url)
        polite_sleep(0.4)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/job/" not in href:
                continue
            full_url = urljoin(url, href)
            title = a.get_text(strip=True)
            if not title or len(title) < 5 or title.lower() in ("apply", "view", "details"):
                continue
            company = ""
            parent = a.find_parent(["div", "article", "li", "section"])
            if parent:
                co_el = parent.find(["span", "h3", "h4", "p"])
                if co_el and co_el.get_text(strip=True) != title:
                    company = co_el.get_text(strip=True)
            row = listing(
                title=title,
                company=company,
                site="jsremotely",
                job_url=full_url,
                location="Remote",
                max_days=_MAX_DAYS,
            )
            if row:
                out.append(row)
        if out:
            break
    # RSS fallback
    if not out:
        out.extend(_from_rss("jsremotely", ["https://jsremotely.com/feed", "https://javascript.jobs/feed"]))
    return out


def scrape_working_nomads() -> list[dict]:
    out: list[dict] = []
    data = fetch_json("https://www.workingnomads.com/api/exposed_jobs/")
    polite_sleep(0.3)
    jobs = data if isinstance(data, list) else []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        pub = normalize_posted(job.get("pub_date") or job.get("published_at"))
        row = listing(
            title=job.get("title") or "",
            company=job.get("company_name") or job.get("company") or "",
            site="working_nomads",
            job_url=job.get("url") or "",
            description=(job.get("description") or "") + " " + str(job.get("tags") or ""),
            date_posted=pub,
            location=job.get("location") or "Remote",
            max_days=_MAX_DAYS,
        )
        if row:
            out.append(row)
    if not out:
        out.extend(_from_rss("working_nomads", ["https://www.workingnomads.com/jobs/feed/"]))
    return out


def scrape_europeremotely() -> list[dict]:
    """europeremotely.com is a parked domain as of 2026-08-25.

    Every path returns HTTP 403 from `server: Parking/1.0` — there is no site
    left to scrape. Kept as a stub so the id stays resolvable; the catalog
    marks it `dead` so discovery no longer schedules it.
    """
    log("europeremotely: domain parked (403 Parking/1.0) — nothing to scrape", err=True)
    return []


def scrape_relocate_me() -> list[dict]:
    """relocate.me — /search 301s to /international-jobs (paged card grid)."""
    out: list[dict] = []
    for page in range(1, 16):
        url = "https://relocate.me/international-jobs"
        if page > 1:
            url = f"{url}?page={page}"
        html = fetch_text(url)
        polite_sleep(0.4)
        if not html:
            break
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".jobs-list__job")
        if not cards:
            break
        for card in cards:
            a = card.find("a", href=True)
            if not a:
                continue
            title_el = card.select_one(".job__title")
            title = " ".join(
                (title_el or a).get_text(" ", strip=True).split())
            # ".job__company" repeats: first is the country, second the company.
            metas = [m.get_text(" ", strip=True) for m in card.select(".job__company")]
            country = metas[0] if metas else "Relocation"
            company = metas[1] if len(metas) > 1 else ""
            desc_el = card.select_one(".job__preview")
            row = listing(
                title=title,
                company=company,
                site="relocate_me",
                job_url=urljoin("https://relocate.me", a["href"]),
                description=desc_el.get_text(" ", strip=True) if desc_el else "",
                location=f"{country} (relocation)",
                max_days=_MAX_DAYS,
            )
            if row:
                out.append(row)
    return out


def scrape_germanstartups() -> list[dict]:
    """jobs.germanstartups.com refuses the TLS handshake as of 2026-08-25.

    Both curl and urllib get `tlsv1 alert internal error` on every path, so no
    HTTP client can reach it. Stub kept; the catalog marks it `dead`.
    """
    log("germanstartups: origin refuses TLS handshake — nothing to scrape", err=True)
    return []


def scrape_justremote() -> list[dict]:
    """JustRemote — the site is a client-rendered SPA with no jobs in the HTML.

    Its own public API (found in the shipped JS bundle) returns the full board.
    """
    out: list[dict] = []
    rows = fetch_json("https://justremote-api.herokuapp.com/api/v1/jobs")
    polite_sleep(0.3)
    if isinstance(rows, dict):
        rows = rows.get("jobs") or rows.get("data") or rows.get("results") or []
    for job in rows or []:
        if not isinstance(job, dict):
            continue
        if str(job.get("is_active")).lower() == "false":
            continue
        href = str(job.get("href") or "")
        if not href:
            continue
        loc = str(job.get("location_restrictions") or "").strip()
        loc = re.sub(r"[\[\]']", "", loc) or str(job.get("remote_type") or "Remote")
        row = listing(
            title=job.get("title") or "",
            company=job.get("company_name") or "",
            site="justremote",
            job_url=urljoin("https://justremote.co/", href.lstrip("/")),
            description=str(job.get("category") or ""),
            date_posted=_parse_day_month(job.get("date")),
            location=loc,
            max_days=_MAX_DAYS,
        )
        if row:
            out.append(row)
    # HTML fallback (job cards render client-side, so this rarely fires).
    if not out:
        for cat in ("remote-developer-jobs", "remote-tech-jobs", "remote-data-jobs"):
            html = fetch_text(f"https://justremote.co/{cat}")
            polite_sleep(0.4)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=re.compile(r"/remote-jobs/[^/]+$")):
                title = a.get_text(strip=True)
                if not title or len(title) < 5:
                    continue
                row = listing(
                    title=title, company="", site="justremote",
                    job_url=urljoin("https://justremote.co", a["href"]),
                    location="Remote", max_days=_MAX_DAYS,
                )
                if row:
                    out.append(row)
    return out


def scrape_dynamitejobs() -> list[dict]:
    """Dynamite Jobs — the landing page only links categories.

    Job links live on the category pages and look like
    /company/<slug>/remote-job/<slug> (the old /job/ pattern never matched).
    """
    out: list[dict] = []
    # Only these category slugs actually carry job links; the plausible-looking
    # remote-devops-jobs / remote-engineering-jobs / remote-it-jobs return a
    # 200 shell with zero postings.
    cats = (
        "remote-development-jobs",
        "remote-data-analyst-jobs",
        "remote-product-jobs",
    )
    job_href = re.compile(r"/company/[^/]+/remote-job/")
    for cat in cats:
        html = fetch_text(f"https://dynamitejobs.com/category/{cat}")
        polite_sleep(0.4)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=job_href):
            href = a["href"]
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 5:
                # Card text lives in siblings; fall back to the URL slug.
                title = href.rsplit("/", 1)[-1].replace("-", " ").title()
            company = ""
            m = re.search(r"/company/([^/]+)/", href)
            if m:
                company = m.group(1).replace("-", " ").title()
            row = listing(
                title=title,
                company=company,
                site="dynamitejobs",
                job_url=urljoin("https://dynamitejobs.com", href),
                location="Remote",
                max_days=_MAX_DAYS,
            )
            if row:
                out.append(row)
    return out


def scrape_weworkremotely() -> list[dict]:
    return _from_rss(
        "weworkremotely",
        [
            "https://weworkremotely.com/categories/remote-programming-jobs.rss",
            "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
            "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
            "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
            "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
            "https://weworkremotely.com/categories/remote-data-jobs.rss",
            "https://weworkremotely.com/remote-jobs.rss",
            "https://weworkremotely.com/categories/all-other-remote-jobs.rss",
        ],
    )


def scrape_jobspresso() -> list[dict]:
    """Jobspresso is WP Job Manager: /feed/ is the *blog* (0 items).

    The job feed is ?feed=job_feed, which also accepts search_keywords.
    """
    base = "https://jobspresso.co/?feed=job_feed&posts_per_page=100&search_keywords="
    return _from_rss(
        "jobspresso",
        [
            "https://jobspresso.co/?feed=job_feed&posts_per_page=100",
            base + "software+engineer",
            base + "data",
            base + "machine+learning",
            base + "python",
        ],
    )


def scrape_authentic_jobs() -> list[dict]:
    base = "https://authenticjobs.com/?feed=job_feed&posts_per_page=100&search_keywords="
    return _from_rss(
        "authentic_jobs",
        [
            "https://authenticjobs.com/?feed=job_feed&posts_per_page=100",
            base + "software+engineer",
            base + "data+scientist",
            base + "data+engineer",
            base + "machine+learning",
            base + "ai+engineer",
            base + "python",
        ],
    )


def scrape_nodesk() -> list[dict]:
    """NoDesk — /feed/ and /remote-jobs/feed.xml both 404; scrape the index.

    Job pages are /remote-jobs/<company>-<title>/, one segment deep, sharing
    the prefix with category pages (/remote-jobs/ai/). Category links are
    dropped by the title relevance filter, so match loosely.
    """
    out: list[dict] = []
    paths = (
        "/remote-jobs/",
        "/remote-jobs/engineering/",
        "/remote-jobs/data/",
        "/remote-jobs/ai/",
        "/remote-jobs/python/",
        "/remote-jobs/software-development/",
        "/remote-jobs/devops/",
        "/remote-jobs/backend/",
        "/remote-jobs/full-stack/",
        "/remote-jobs/machine-learning/",
        "/remote-jobs/cloud/",
        "/remote-jobs/javascript/",
    )
    job_href = re.compile(r"^/remote-jobs/[^/]+/?$")
    for path in paths:
        html = fetch_text(f"https://nodesk.co{path}")
        polite_sleep(0.4)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=job_href):
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 5 or title.lower() in ("view job", "apply"):
                continue
            # Slugs are "<company>-<title>"; the visible text is the title.
            url = urljoin("https://nodesk.co", a["href"])
            # Slugs are "<company>-<role-words>"; without the company every
            # NoDesk row died on dedup's no_company filter.
            company = derive_company.from_slug_minus_title(
                url, title, company_first=True)
            row = listing(
                title=title,
                company=company,
                site="nodesk",
                job_url=url,
                location="Remote",
                max_days=_MAX_DAYS,
            )
            if row:
                out.append(row)
    return out


def scrape_themuse() -> list[dict]:
    out: list[dict] = []
    categories = (
        "Software Engineering", "Data Science", "Data and Analytics",
        "IT", "Engineering", "Product Management", "Science and Engineering",
    )
    for category in categories:
        for page in range(1, 9):
            data = fetch_json(
                "https://www.themuse.com/api/public/jobs"
                f"?category={category.replace(' ', '%20')}"
                "&location=Flexible%20/%20Remote"
                f"&descending=true&page={page}"
            )
            polite_sleep(0.4)
            if not isinstance(data, dict):
                break
            jobs = data.get("results") or []
            if not jobs:
                break
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                co = job.get("company")
                company = co.get("name") if isinstance(co, dict) else str(co or "")
                refs = job.get("refs") or {}
                url = refs.get("landing_page") if isinstance(refs, dict) else ""
                locs = job.get("locations") or []
                location = ", ".join(
                    str(loc.get("name") or "") for loc in locs if isinstance(loc, dict)
                ) or "Remote"
                pub = normalize_posted(job.get("publication_date"))
                row = listing(
                    title=job.get("name") or "",
                    company=str(company or ""),
                    site="themuse",
                    job_url=str(url or ""),
                    description=clean_html(job.get("contents") or ""),
                    date_posted=pub,
                    location=location,
                    max_days=_MAX_DAYS,
                )
                if row:
                    out.append(row)
    return out


def scrape_workew() -> list[dict]:
    """Workew is WP Job Manager — the plain /feed/ is their blog.

    ?feed=job_feed defaults to the newest 10, which are usually non-tech;
    posts_per_page widens it and search_keywords targets the tech roles.
    """
    base = "https://workew.com/?feed=job_feed&posts_per_page=100&search_keywords="
    return _from_rss(
        "workew",
        [
            "https://workew.com/?feed=job_feed&posts_per_page=100",
            base + "developer",
            base + "engineer",
            base + "data",
            base + "python",
            base + "machine+learning",
        ],
    )


def scrape_yc_jobs() -> list[dict]:
    """Y Combinator's own jobs board (was: HN Algolia, which yields ~2 rows).

    /jobs/role/<role> is server-rendered: each card carries company, "N days
    ago", title and salary.
    """
    out: list[dict] = []
    # YC's /jobs/role/<role> pages filter client-side — every role returns the
    # same server-rendered set (engineering vs data-science share 40 of 41
    # links), so extra roles are pure request waste. Two pages is the ceiling.
    roles = ("engineering", "data-science")
    job_href = re.compile(r"^/companies/[^/]+/jobs/")
    for role in roles:
        html = fetch_text(f"https://www.ycombinator.com/jobs/role/{role}")
        polite_sleep(0.5)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=job_href):
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 4:
                continue
            company = ""
            posted = None
            root = a.find_parent(["div", "li", "section"])
            if root:
                text = root.get_text(" | ", strip=True)
                m = re.match(r"\s*([^|]+?)\s*\|\s*\([WSXF]\d{2}\)", text)
                if m:
                    company = m.group(1).strip()[:80]
                d = re.search(r"\(\s*(\d+)\s*(day|days|hour|hours|month|months)\s*ago", text)
                if d:
                    n = int(d.group(1))
                    unit = d.group(2)
                    days = 0 if unit.startswith("hour") else (n * 30 if unit.startswith("month") else n)
                    posted = (date.today() - timedelta(days=days)).isoformat()
            row = listing(
                title=title,
                company=company or "YC Startup",
                site="yc_jobs",
                job_url=urljoin("https://www.ycombinator.com", a["href"]),
                description=root.get_text(" ", strip=True)[:600] if root else "",
                date_posted=posted,
                location="Remote / US",
                max_days=_MAX_DAYS,
            )
            if row:
                out.append(row)
    # Fallback: HN "who is hiring" job posts.
    if not out:
        for page in range(0, 2):
            data = fetch_json(
                "https://hn.algolia.com/api/v1/search_by_date"
                f"?tags=job&hitsPerPage=50&page={page}")
            polite_sleep(0.3)
            if not isinstance(data, dict):
                break
            for hit in data.get("hits") or []:
                if not isinstance(hit, dict):
                    continue
                title = hit.get("title") or ""
                m = re.match(r"^([^(\n]+?)(?:\s*\([^)]+\))?\s+(?:is hiring|hiring)", title, re.I)
                row = listing(
                    title=title,
                    company=(m.group(1).strip() if m else "YC Startup"),
                    site="yc_jobs",
                    job_url=hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                    description=hit.get("story_text") or "",
                    date_posted=normalize_posted(hit.get("created_at")),
                    location="Remote",
                    max_days=_MAX_DAYS,
                )
                if row:
                    out.append(row)
    return out


# europeremotely / germanstartups are intentionally absent: their origins are
# dead (parked domain / refused TLS). Their stubs stay above for reference.
SCRAPERS = {
    "himalayas": scrape_himalayas,
    "arbeitnow": scrape_arbeitnow,
    "landing_jobs": scrape_landing_jobs,
    "jsremotely": scrape_jsremotely,
    "working_nomads": scrape_working_nomads,
    "relocate_me": scrape_relocate_me,
    "justremote": scrape_justremote,
    "dynamitejobs": scrape_dynamitejobs,
    "weworkremotely": scrape_weworkremotely,
    "jobspresso": scrape_jobspresso,
    "authentic_jobs": scrape_authentic_jobs,
    "nodesk": scrape_nodesk,
    "themuse": scrape_themuse,
    "workew": scrape_workew,
    "yc_jobs": scrape_yc_jobs,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True, choices=sorted(SCRAPERS.keys()))
    parser.add_argument("--out", default=None)
    parser.add_argument("--skip-urls", default=None)
    parser.add_argument(
        "--max-days", type=int, default=DEFAULT_MAX_DAYS,
        help=f"Recency window in days (default {DEFAULT_MAX_DAYS})")
    args = parser.parse_args()

    global _MAX_DAYS
    _MAX_DAYS = max(1, int(args.max_days))
    site = args.site
    out_path = Path(args.out) if args.out else (
        ROOT / "listings" / f"{date.today().isoformat()}-{site}.json"
    )
    log(f"scraping worldwide board: {site}")
    rows = SCRAPERS[site]()
    rows = dedup_by_url(rows)
    skip = load_skip_urls_file(Path(args.skip_urls) if args.skip_urls else None)
    if skip:
        rows, skipped = filter_out_known_listings(rows, skip)
        log(f"skip-urls: dropped {skipped} known")
    write_listings(out_path, rows)


if __name__ == "__main__":
    main()
