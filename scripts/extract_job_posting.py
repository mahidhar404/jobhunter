#!/usr/bin/env python3
"""Extract company/title/location/description from a job posting URL,
programmatically, no agent/LLM involved - backs the dashboard's
"paste an apply link" manual-add feature.

Three tiers, tried in order, each one only reached if the previous one
doesn't apply or doesn't have an answer:

1. Known-unreachable platforms - detected up front and skipped
   immediately, not attempted: Workday/iCIMS are Akamai-protected (see
   PLAYBOOK.md Hard Rules - this project never tries to bypass that), and
   LinkedIn requires the authenticated browser session this script
   doesn't have (verified live: an unauthenticated fetch gets a stripped
   page with no job data at all). These fall straight through to the
   existing agent-browser fallback (see server.py's manually-added-job
   branch) exactly as before this script existed.
2. Known ATS platforms (Greenhouse/Lever/Ashby/Recruitee/Personio/
   SmartRecruiters/Rippling/Breezy) - each has a public API or board
   page already used by scrape_ats.py for bulk board scraping. This
   calls the same APIs for just the one job in the URL, verified live
   against real postings. Deliberately does NOT reuse scrape_ats.py's
   scrape_*() wrapper functions directly - those filter by
   RELEVANT_KEYWORDS, which would wrongly hide a manually-pasted job
   whose title happens not to match that list (the user already made the
   relevance call by pasting it).
3. Generic fallback for everything else - schema.org JobPosting ld+json
   first (verified: not present on Greenhouse/LinkedIn/this Panasonic
   career page in live testing, but a real, common pattern many direct
   company career sites use for Google for Jobs indexing), then
   trafilatura's general-purpose content extraction with a minimum
   length floor (verified live: a JS-rendered career page returns only
   ~180 chars of nav-menu junk when fetched with a plain HTTP request -
   below the floor, this is correctly treated as "nothing useful found"
   rather than accepted as a real but tiny description).
4. Headless Playwright render - only if HTTP HTML was missing or too
   thin. Re-runs schema.org + trafilatura on the rendered DOM. Never
   used for Workday/iCIMS/LinkedIn (tier 1). Never solves CAPTCHA.

Usage:
  python3 extract_job_posting.py URL
    Prints {"company", "title", "location", "description", "apply_url"}
    as JSON to stdout on success. Exits 1 with nothing on stdout if
    nothing usable could be extracted (including tier-1 skips) - the
    caller should fall back to the agent's own browser tool, exactly the
    behavior that already existed before this script did.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent))
from scrape_ats import (  # noqa: E402
    fetch_json,
    fetch_xml,
    TransientFetchError,
    clean_html_content,
    lever_compose_description,
    smartrecruiters_description_from_detail,
    rippling_description_from_detail,
    description_from_jobposting_ldjson,
)
from apply_urls import (  # noqa: E402
    extract_ats_urls_from_text,
    is_aggregator_url,
    prefer_apply_url,
)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
MIN_DESCRIPTION_CHARS = 200  # below this, treat a generic-fallback extraction as noise, not a real JD

UNREACHABLE_PATTERNS = {
    "workday (Akamai-protected)": re.compile(r"myworkdayjobs\.com|myworkdaysite\.com"),
    "icims (Akamai-protected)": re.compile(r"icims\.com"),
    "linkedin (needs authenticated session)": re.compile(r"linkedin\.com"),
}

GREENHOUSE_RE = re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([^/]+)/jobs/(\d+)")
LEVER_RE = re.compile(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]+)")
ASHBY_RE = re.compile(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]+)")
RECRUITEE_RE = re.compile(r"([a-z0-9-]+)\.recruitee\.com/o/([^/?]+)")
PERSONIO_RE = re.compile(r"([a-z0-9-]+)\.jobs\.personio\.(?:com|de)")
SMARTRECRUITERS_RE = re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)/([^/?#]+)")
RIPPLING_RE = re.compile(r"ats\.rippling\.com/([^/?#]+)/jobs/([^/?#]+)")
BREEZY_RE = re.compile(r"([a-z0-9-]+)\.breezy\.hr/p/([^/?#]+)")


def fetch_html(url: str) -> str | None:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (URLError, HTTPError):
        return None


def try_greenhouse(url: str) -> dict | None:
    m = GREENHOUSE_RE.search(url)
    if not m:
        return None
    slug, job_id = m.groups()
    try:
        data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}?content=true")
    except TransientFetchError:
        return None
    if not data:
        return None
    return {
        "company": data.get("company_name") or slug,
        "title": data.get("title") or "",
        "location": (data.get("location") or {}).get("name"),
        "description": clean_html_content(data.get("content") or ""),
    }


def try_lever(url: str) -> dict | None:
    m = LEVER_RE.search(url)
    if not m:
        return None
    slug, posting_id = m.groups()
    try:
        data = fetch_json(f"https://api.lever.co/v0/postings/{slug}/{posting_id}?mode=json")
    except TransientFetchError:
        return None
    if not data or not isinstance(data, dict):
        return None
    cat = data.get("categories") or {}
    return {
        "company": slug,
        "title": data.get("text") or "",
        "location": cat.get("location"),
        "description": lever_compose_description(data),
    }


def try_ashby(url: str) -> dict | None:
    m = ASHBY_RE.search(url)
    if not m:
        return None
    slug, job_id = m.groups()
    try:
        data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    except TransientFetchError:
        return None
    if not data:
        return None
    for job in data.get("jobs", []):
        if job_id in (job.get("jobUrl") or "") or job_id in (job.get("applyUrl") or ""):
            return {
                "company": slug,
                "title": job.get("title") or "",
                "location": job.get("location"),
                "description": job.get("descriptionPlain") or "",
            }
    return None


def try_recruitee(url: str) -> dict | None:
    m = RECRUITEE_RE.search(url)
    if not m:
        return None
    slug, offer_slug = m.groups()
    try:
        data = fetch_json(f"https://{slug}.recruitee.com/api/offers/")
    except TransientFetchError:
        return None
    if not data:
        return None
    for job in data.get("offers", []):
        careers_url = job.get("careers_url") or ""
        if offer_slug in careers_url:
            return {
                "company": job.get("company_name") or slug,
                "title": job.get("title") or "",
                "location": job.get("location"),
                "description": job.get("description") or "",
            }
    return None


def try_personio(url: str) -> dict | None:
    m = PERSONIO_RE.search(url)
    if not m:
        return None
    slug = m.group(1)
    try:
        root = fetch_xml(f"https://{slug}.jobs.personio.de/xml?language=en")
    except TransientFetchError:
        return None
    if root is None:
        return None
    # Personio has no per-job URL (see scrape_builtin.py/scrape_ats.py's
    # own notes on this) - match by position id fragment if present in
    # the pasted URL, otherwise this can't disambiguate and bails out.
    frag_match = re.search(r"#(\d+)", url)
    for pos in root.findall("position"):
        pos_id = pos.findtext("id") or ""
        if frag_match and frag_match.group(1) != pos_id:
            continue
        descriptions = []
        for jd in pos.findall("jobDescriptions/jobDescription"):
            name = jd.findtext("name") or ""
            value = jd.findtext("value") or ""
            descriptions.append(f"{name}\n{value}" if name else value)
        return {
            "company": pos.findtext("subcompany") or slug,
            "title": pos.findtext("name") or "",
            "location": pos.findtext("office"),
            "description": "\n\n".join(d for d in descriptions if d),
        }
    return None


def try_smartrecruiters(url: str) -> dict | None:
    m = SMARTRECRUITERS_RE.search(url)
    if not m:
        return None
    slug, job_id = m.groups()
    try:
        data = fetch_json(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}"
        )
    except TransientFetchError:
        return None
    if not data or not isinstance(data, dict):
        return None
    loc = data.get("location") or {}
    location = ", ".join(
        p for p in (loc.get("city"), loc.get("region"), loc.get("country")) if p
    ) or None
    company = data.get("company") or {}
    company_name = company.get("name") if isinstance(company, dict) else None
    return {
        "company": company_name or slug,
        "title": data.get("name") or "",
        "location": location,
        "description": smartrecruiters_description_from_detail(data),
    }


def try_rippling(url: str) -> dict | None:
    m = RIPPLING_RE.search(url)
    if not m:
        return None
    slug, uuid = m.groups()
    try:
        data = fetch_json(f"https://ats.rippling.com/api/v1/board/{slug}/jobs/{uuid}")
    except TransientFetchError:
        return None
    if not data or not isinstance(data, dict):
        return None
    locs = data.get("workLocations") or []
    location = None
    if isinstance(locs, list) and locs:
        first = locs[0]
        if isinstance(first, dict):
            location = first.get("label") or first.get("name")
    return {
        "company": data.get("companyName") or slug,
        "title": data.get("name") or "",
        "location": location,
        "description": rippling_description_from_detail(data),
    }


def try_breezy(url: str) -> dict | None:
    m = BREEZY_RE.search(url)
    if not m:
        return None
    slug, _fid = m.groups()
    html = fetch_html(url)
    if not html:
        return None
    description = description_from_jobposting_ldjson(html)
    if not description:
        return None
    # Prefer ld+json metadata when present
    title = None
    company = slug
    location = None
    for block in re.findall(
        r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S | re.I
    ):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                title = item.get("title") or title
                org = item.get("hiringOrganization") or {}
                if isinstance(org, dict) and org.get("name"):
                    company = org["name"]
                loc = item.get("jobLocation") or {}
                address = loc.get("address") if isinstance(loc, dict) else None
                if isinstance(address, dict):
                    location = ", ".join(
                        v for v in (address.get("addressLocality"), address.get("addressRegion")) if v
                    ) or location
    return {
        "company": company,
        "title": title or "",
        "location": location,
        "description": description,
    }


KNOWN_ATS_TRIERS = [
    try_greenhouse,
    try_lever,
    try_ashby,
    try_recruitee,
    try_personio,
    try_smartrecruiters,
    try_rippling,
    try_breezy,
]


def try_schema_org_jsonld(html: str) -> dict | None:
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                org = item.get("hiringOrganization") or {}
                loc = item.get("jobLocation") or {}
                address = loc.get("address") if isinstance(loc, dict) else None
                location = None
                if isinstance(address, dict):
                    location = ", ".join(
                        v for v in (address.get("addressLocality"), address.get("addressRegion")) if v
                    ) or None
                return {
                    "company": org.get("name") if isinstance(org, dict) else None,
                    "title": item.get("title"),
                    "location": location,
                    "description": clean_html_content(item.get("description") or ""),
                }
    return None


def try_generic_fallback(url: str, html: str) -> dict | None:
    import trafilatura

    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not text or len(text) < MIN_DESCRIPTION_CHARS:
        return None  # almost certainly nav/header junk from a JS-rendered page, not a real JD

    title = None
    m = re.search(r"<title>([^<]*)</title>", html)
    if m:
        title = m.group(1).strip()

    return {
        "company": None,  # not confidently extractable without a known pattern - left for the agent/user to fill in
        "title": title,
        "location": None,
        "description": text,
    }


def _attach_apply_url(result: dict, page_url: str) -> dict:
    """Prefer an ATS/company link found in the JD over an aggregator page URL.

    Never clears apply_url — if nothing better is found, keep page_url.
    """
    desc = result.get("description") or ""
    ats_hits = extract_ats_urls_from_text(desc)
    best = prefer_apply_url(*(ats_hits + [result.get("apply_url"), page_url]))
    result["apply_url"] = best or page_url
    if is_aggregator_url(page_url) and best and not is_aggregator_url(best):
        result["source_url"] = page_url
    return result


def _parse_html_tiers(url: str, html: str) -> dict | None:
    """schema.org then trafilatura on already-fetched HTML."""
    html_ats = extract_ats_urls_from_text(html)
    result = try_schema_org_jsonld(html)
    if result and result.get("description") and len(result["description"]) >= MIN_DESCRIPTION_CHARS:
        if html_ats:
            result["apply_url"] = prefer_apply_url(*(html_ats + [url]))
        return _attach_apply_url(result, url)

    result = try_generic_fallback(url, html)
    if result:
        if html_ats:
            result["apply_url"] = prefer_apply_url(*html_ats, url)
        return _attach_apply_url(result, url)
    return None


def extract(url: str, *, allow_playwright: bool = True) -> dict | None:
    for platform, pattern in UNREACHABLE_PATTERNS.items():
        if pattern.search(url):
            print(f"skipping: {platform} can't be fetched programmatically", file=sys.stderr)
            return None

    for trier in KNOWN_ATS_TRIERS:
        result = trier(url)
        if result and result.get("description"):
            return _attach_apply_url(result, url)

    html = fetch_html(url)
    if html:
        parsed = _parse_html_tiers(url, html)
        if parsed:
            return parsed

    # Tier 4: headless Chromium for JS-rendered / thin HTTP pages.
    # Never used for unreachable hosts (already returned above).
    if not allow_playwright:
        return None
    try:
        from pw_fetch_html import fetch_html_playwright
    except ImportError:
        print("pw_fetch_html unavailable", file=sys.stderr)
        return None
    print(f"playwright extract fallback: {url}", file=sys.stderr)
    pw_html = fetch_html_playwright(url)
    if not pw_html:
        return None
    return _parse_html_tiers(url, pw_html)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    args = parser.parse_args()

    result = extract(args.url)
    if result is None:
        print("could not extract job details programmatically", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
