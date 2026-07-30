"""Discovery via free, sanctioned JSON APIs instead of browser scraping.

Measured earlier this session: discovery (scrape_ats.py/scout.py, browser-driven)
consumes 71.5% of ALL compute time in this project - more than every other step
combined. Separately verified live, against real companies already in this
project's own backlog: Greenhouse, Lever, Ashby, Workday, and SmartRecruiters all
publish free, public, no-authentication JSON APIs for their job boards. Together
they cover 52.3% of the 3,472-job backlog (1,816 jobs).

For any posting on these five platforms, this replaces a browser-automation
scrape (tens of seconds, a real Chrome process, LLM cost if agent-driven) with a
single HTTP GET (milliseconds, zero browser, zero LLM, free).

Deliberately NOT attempted here, and why:
  * LinkedIn (24.1% of the backlog, the single largest platform) - its only
    accessible endpoint is an unofficial "guest" scrape target LinkedIn actively
    rate-limits, IP-bans, and has taken legal action over. Not the same category
    as a platform's OWN sanctioned public API - not adopted, same as declining
    the CAPTCHA-bypass tools researched earlier.
  * iCIMS - confirmed live, no public API exists (returns HTML, 404s the
    standard pattern).
  * Workable, Comeet, Zoho Recruit, ClearCompany, Teamtailor's real API - tested
    live, each either requires auth or has no simple guessable pattern. Not
    claimed as solved.

Each platform function returns NORMALIZED records - same shape regardless of
source - so a caller never needs to know which API answered.
"""

import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

# Checked live against real data (2026-07-30): 0 of 367 Greenhouse postings had
# a past deadline, 0 of 77 Ashby postings had isListed=False, 0 of 100
# SmartRecruiters postings were non-PUBLIC. These APIs mirror each company's
# live board directly - a closed role is REMOVED from the response, not kept
# with a dead flag - so this filter is a real defense-in-depth check against a
# genuine field these platforms expose, not a fix for an active problem seen
# today. Left in because "the API currently returns nothing dead" is a
# snapshot, not a guarantee.

def _normalize(company, title, location, url, description, platform, raw_id):
    return {
        "id": f"{platform}-{raw_id}", "company": company, "title": title,
        "location": location, "url": url, "description": description,
        "platform": platform,
    }


def fetch_greenhouse(company_slug: str, client: httpx.Client) -> list[dict]:
    """boards-api.greenhouse.io - confirmed live, e.g. rocketlab, scribdinc."""
    r = client.get(f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs",
                    params={"content": "true"}, timeout=15)
    if r.status_code != 200:
        return []
    out = []
    for j in r.json().get("jobs", []):
        deadline = j.get("application_deadline")
        if deadline and deadline < datetime.now(timezone.utc).isoformat():
            continue  # past its own stated deadline - dead
        out.append(_normalize(
            company_slug, j.get("title", ""), (j.get("location") or {}).get("name", ""),
            j.get("absolute_url", ""), re.sub(r"<[^>]+>", " ", j.get("content", ""))[:4000],
            "greenhouse", j.get("id"),
        ))
    return out


def fetch_lever(company_slug: str, client: httpx.Client) -> list[dict]:
    """api.lever.co - confirmed live, e.g. 3pillarglobal.

    No dead-posting filter here: checked live, a real posting's full key set
    has no status/state/active field (only `workplaceType`, which is
    onsite/remote/hybrid - a location signal, not a liveness one). Same as
    Greenhouse/Ashby/SmartRecruiters, this endpoint mirrors the live board
    directly - a closed role is removed from the response, not flagged - so
    there is genuinely nothing to filter on, not an oversight.
    """
    r = client.get(f"https://api.lever.co/v0/postings/{company_slug}",
                    params={"mode": "json"}, timeout=15)
    if r.status_code != 200:
        return []
    out = []
    for j in r.json():
        cats = j.get("categories", {}) or {}
        out.append(_normalize(
            company_slug, j.get("text", ""), cats.get("location", ""),
            j.get("hostedUrl", ""), (j.get("descriptionPlain") or "")[:4000],
            "lever", j.get("id"),
        ))
    return out


def fetch_ashby(company_slug: str, client: httpx.Client) -> list[dict]:
    """api.ashbyhq.com posting-api - confirmed live, e.g. mercor."""
    r = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}", timeout=15)
    if r.status_code != 200:
        return []
    out = []
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:  # explicit field this API exposes - honor it
            continue
        out.append(_normalize(
            company_slug, j.get("title", ""), j.get("location", ""),
            j.get("jobUrl") or j.get("applyUrl", ""), (j.get("descriptionPlain") or "")[:4000],
            "ashby", j.get("id"),
        ))
    return out


def fetch_smartrecruiters(company_slug: str, client: httpx.Client) -> list[dict]:
    """api.smartrecruiters.com - confirmed live, e.g. AbbVie. Paginated;
    limit=100 per page, offset advances - most company boards fit in 1-3 pages."""
    out = []
    offset = 0
    for _ in range(20):  # hard cap: 2000 postings is far beyond any real company board
        r = client.get(f"https://api.smartrecruiters.com/v1/companies/{company_slug}/postings",
                        params={"limit": 100, "offset": offset}, timeout=15)
        if r.status_code != 200:
            break
        data = r.json()
        content = data.get("content", [])
        for j in content:
            if j.get("visibility") not in (None, "PUBLIC"):  # PRIVATE/internal - dead to us
                continue
            loc = j.get("location", {}) or {}
            out.append(_normalize(
                company_slug, j.get("name", ""),
                f"{loc.get('city','')}, {loc.get('region','')}".strip(", "),
                f"https://jobs.smartrecruiters.com/{company_slug}/{j.get('id','')}",
                "", "smartrecruiters", j.get("id"),
            ))
        if len(content) < 100 or offset + 100 >= data.get("totalFound", 0):
            break
        offset += 100
    return out


def fetch_workday(tenant_domain: str, site_slug: str, company_slug: str,
                  client: httpx.Client, url_path_prefix: str | None = None) -> list[dict]:
    """wday/cxs internal API - confirmed live, e.g. netflix.wd108.myworkdayjobs.com
    tenant='netflix', site_slug from the URL path segment right after the domain
    (e.g. "/Netflix/job/..." -> "Netflix"). Paginated via offset/limit.

    Real bug found live (2026-07-30): Workday's own API returns `externalPath`
    as just "/job/..." - it does NOT include the site slug. Every url this
    function built (`tenant_domain + externalPath`) was missing that segment
    and 404'd on the real site, even though the underlying fetch itself
    (job count, titles) was correct - caught only by actually navigating a
    constructed url via Skyvern, not by trusting a successful API response.
    url_path_prefix (from workday_tenant_from_url) is the exact path segment(s)
    - "Netflix", or "en-US/NVIDIAExternalCareerSite" for locale-prefixed
    tenants - that belong between the domain and externalPath; falls back to
    site_slug alone when not provided (e.g. a caller who only has a bare
    site_slug, not a full derived tenant tuple).

    No dead-posting filter here either: a real jobPostings entry's full key
    set is just bulletFields/externalPath/locationsText/postedOn/title - no
    status field at all. Same reasoning as fetch_lever - this endpoint IS the
    live board, so there is nothing dead left in it to filter."""
    prefix = url_path_prefix or site_slug
    url = f"https://{tenant_domain}/wday/cxs/{company_slug}/{site_slug}/jobs"
    out = []
    offset = 0
    for _ in range(50):  # Workday tenants can be large (Netflix: 637+)
        r = client.post(url, json={"appliedFacets": {}, "limit": 20, "offset": offset,
                                   "searchText": ""}, timeout=15)
        if r.status_code != 200:
            break
        data = r.json()
        postings = data.get("jobPostings", [])
        for j in postings:
            out.append(_normalize(
                company_slug, j.get("title", ""), j.get("locationsText", ""),
                f"https://{tenant_domain}/{prefix}{j.get('externalPath', '')}", "",
                "workday", j.get("bulletFields", [""])[0] or j.get("externalPath", ""),
            ))
        if len(postings) < 20 or offset + 20 >= data.get("total", 0):
            break
        offset += 20
    return out


_WORKDAY_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def workday_tenant_from_url(url: str) -> tuple[str, str, str, str] | None:
    """Derive (tenant_domain, company_slug, site_slug, url_path_prefix) from a
    real Workday apply_url already stored in jobs.json - no guessing needed,
    every field this API call requires is already encoded in the URL.
    company_slug is the subdomain prefix (netflix.wd108... -> netflix);
    site_slug is the path segment right after the domain (or after a locale
    segment, if present).

    Real bug found live: some tenants (Netflix) put the site slug FIRST
    ("/Netflix/job/..."), others (NVIDIA) put a locale code first
    ("/en-US/NVIDIAExternalCareerSite/job/..."). Blindly taking parts[0] as
    site_slug - the original implementation - would silently take "en-US" as
    NVIDIA's site slug, breaking both the wday/cxs API call (wrong endpoint)
    and any URL built from it. A locale code is always exactly xx-XX
    (ISO language-COUNTRY), which real Workday site slugs never look like -
    that shape is what distinguishes the two cases.
    """
    p = urlparse(url)
    if "myworkdayjobs" not in p.netloc and "myworkdaysite" not in p.netloc:
        return None
    company_slug = p.netloc.split(".")[0]
    parts = [seg for seg in p.path.split("/") if seg]
    if not parts:
        return None
    if _WORKDAY_LOCALE_RE.match(parts[0]) and len(parts) > 1:
        site_slug = parts[1]
        url_path_prefix = "/".join(parts[:2])
    else:
        site_slug = parts[0]
        url_path_prefix = parts[0]
    return p.netloc, company_slug, site_slug, url_path_prefix


PLATFORM_DETECT = [
    (re.compile(r"job-boards(\.\w+)?\.greenhouse\.io/([^/]+)"), "greenhouse"),
    (re.compile(r"boards\.greenhouse\.io/([^/]+)"), "greenhouse"),
    (re.compile(r"jobs\.lever\.co/([^/]+)"), "lever"),
    (re.compile(r"jobs\.ashbyhq\.com/([^/]+)"), "ashby"),
    (re.compile(r"jobs\.smartrecruiters\.com/([^/]+)"), "smartrecruiters"),
]


def detect_platform(url: str) -> tuple[str, str] | None:
    """(platform, company_slug) from an existing posting URL, or None if it's
    not one of the five API-eligible platforms. Workday is handled separately
    (workday_tenant_from_url) since it needs three parts, not one slug."""
    for pattern, platform in PLATFORM_DETECT:
        m = pattern.search(url)
        if m:
            return platform, m.group(m.lastindex)
    if workday_tenant_from_url(url):
        return "workday", None
    return None


def fetch_company_postings(url: str, client: httpx.Client) -> list[dict]:
    """Single entry point: given ANY existing posting URL for a company, fetch
    that company's FULL current board via the matching free API. One seed URL
    -> every open role at that employer, at the cost of one HTTP call (plus
    pagination for large boards) instead of one browser scrape per posting."""
    detected = detect_platform(url)
    if not detected:
        return []
    platform, slug = detected
    if platform == "greenhouse":
        return fetch_greenhouse(slug, client)
    if platform == "lever":
        return fetch_lever(slug, client)
    if platform == "ashby":
        return fetch_ashby(slug, client)
    if platform == "smartrecruiters":
        return fetch_smartrecruiters(slug, client)
    if platform == "workday":
        wd = workday_tenant_from_url(url)
        if wd:
            # Named, not positional (*wd) - workday_tenant_from_url returns
            # (domain, company, site, url_path_prefix) but fetch_workday's
            # parameter order is (domain, SITE, COMPANY, ...). A positional
            # unpack silently swapped them, building wday/cxs/{site}/{company}/jobs
            # instead of wday/cxs/{company}/{site}/jobs - a 404 that returned 0
            # results without ever raising, caught only by testing through the
            # actual dispatch path rather than trusting the function in isolation.
            tenant_domain, company_slug, site_slug, url_path_prefix = wd
            return fetch_workday(tenant_domain=tenant_domain, site_slug=site_slug,
                                 company_slug=company_slug, client=client,
                                 url_path_prefix=url_path_prefix)
    return []


if __name__ == "__main__":
    import sys
    test_urls = [
        "https://job-boards.greenhouse.io/rocketlab/jobs/7777678003",
        "https://jobs.lever.co/3pillarglobal/d2ded0cc-eb2c-4185-9347-62d5c9f402bd",
        "https://jobs.ashbyhq.com/mercor/7cee578f-799c-46ad-8951-cb0b724d619a/application",
        "https://jobs.smartrecruiters.com/AbbVie/3743990014302945-senior-research-statistician-hybrid-",
        "https://netflix.wd108.myworkdayjobs.com/Netflix/job/Los-Gatos/Machine-Learning-Engineer-5---Ads-Platform-Engineering_JR33083",
    ]
    urls = sys.argv[1:] or test_urls
    with httpx.Client() as client:
        for url in urls:
            t0 = time.time()
            jobs = fetch_company_postings(url, client)
            dt = time.time() - t0
            det = detect_platform(url)
            print(f"{(det[0] if det else '???'):16s} {len(jobs):4d} postings  {dt:.2f}s  {url[:70]}")
            if jobs:
                print(f"   e.g. {jobs[0]['title']!r} @ {jobs[0]['location']!r}")
