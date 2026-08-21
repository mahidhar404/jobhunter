#!/usr/bin/env python3
"""Classify and prefer job Application URLs (ATS/company over aggregators).

Additive / enrich semantics only:
- Prefer Greenhouse/Lever/Ashby/Workday/iCIMS/etc. (and non-aggregator https)
  over LinkedIn/Indeed/Glassdoor/etc.
- Never return None when at least one input URL was present.
- Never overwrite a good ATS/company URL with an aggregator URL.
- On failed resolution, keep the aggregator URL so the job stays visible.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

# Host substrings — matched against netloc (lowercased, www. stripped).
AGGREGATOR_HOST_HINTS = (
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "simplyhired.com",
    "monster.com",
    "dice.com",
    "careerbuilder.com",
    "builtin.com",
    "wellfound.com",
    "angel.co",
    "jooble.org",
    "snagajob.com",
    "talent.com",
    "remoteok.com",
    "remotive.com",
    "jobicy.com",
    "adzuna.com",
    "adzuna.in",
    "weworkremotely.com",
    "authenticjobs.com",
    "jobspresso.co",
    "workingnomads.com",
    "workingnomads.co",
    # Job-mirror / scrape sites that mint path-token false positives in SERP score.
    "tryjeremy.com",
    "theladders.com",
    "opentalent.in",
    "opentalent.com",
    "himalayas.app",
    "shine.com",
    "jobright.ai",
    "tealhq.com",
    "jobleads.com",
)

# Known ATS / hosted apply platforms (prefer these over bare company career pages).
ATS_HOST_HINTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "myworkdaysite.com",
    "icims.com",
    "smartrecruiters.com",
    "workable.com",
    "bamboohr.com",
    "recruitee.com",
    "personio.com",
    "personio.de",
    "jobvite.com",
    "taleo.net",
    "successfactors.com",
    "dayforcehcm.com",
    "ultipro.com",
    "ukg.net",
    "oraclecloud.com",
    "rippling.com",
    "teamtailor.com",
    "pinpointhq.com",
    "applytojob.com",
    "breezy.hr",
    "jobscore.com",
    "gem.com",
    "dover.io",
    "phenom.com",
    "greenhouse.com",
    "jobs.lever.co",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
)

# Rank: lower = better Application link.
RANK_KNOWN_ATS = 0
RANK_COMPANY = 1
RANK_AGGREGATOR = 2
RANK_OTHER = 3
RANK_EMPTY = 99

_HTTP_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def _clean_url(url) -> str:
    if url is None:
        return ""
    s = str(url).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    return s


def normalize_url(url) -> str:
    """Light normalize for equality / dedup: scheme+host+path, no fragment, strip trailing slash."""
    s = _clean_url(url)
    if not s:
        return ""
    try:
        p = urlparse(s)
    except ValueError:
        return s.rstrip("/").lower()
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    # Casefold path so Ashby org-slug twins (Jerry.ai vs jerry.ai) and similar
    # ATS path variants collapse for equality / dedup.
    path = (p.path or "").rstrip("/").lower()
    # Drop common tracking query noise for equality, but keep meaningful job ids
    # when path alone is insufficient (e.g. indeed ?jk=). Keep full query for
    # those aggregators; strip utm_* elsewhere.
    query = p.query or ""
    if host.endswith("indeed.com") or "linkedin.com" in host:
        norm_query = query
    else:
        parts = []
        for part in query.split("&"):
            if not part:
                continue
            key = part.split("=", 1)[0].lower()
            if key.startswith("utm_") or key in ("fbclid", "gclid", "ref", "source"):
                continue
            parts.append(part)
        norm_query = "&".join(parts)
    scheme = (p.scheme or "https").lower()
    netloc = host
    return urlunparse((scheme, netloc, path, "", norm_query, ""))


def _host(url: str) -> str:
    s = _clean_url(url)
    if not s:
        return ""
    try:
        host = (urlparse(s).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def is_aggregator_url(url) -> bool:
    host = _host(url)
    if not host:
        return False
    return any(h in host for h in AGGREGATOR_HOST_HINTS)


def is_known_ats_url(url) -> bool:
    host = _host(url)
    if not host:
        return False
    return any(h in host for h in ATS_HOST_HINTS)


def is_ats_or_company_apply(url) -> bool:
    """True for known ATS hosts or non-aggregator https career/apply pages."""
    s = _clean_url(url)
    if not s:
        return False
    try:
        p = urlparse(s)
    except ValueError:
        return False
    if p.scheme not in ("http", "https"):
        return False
    if is_aggregator_url(s):
        return False
    if is_known_ats_url(s):
        return True
    # Non-aggregator https → treat as company career / apply.
    return bool(p.netloc)


def url_preference_rank(url) -> int:
    s = _clean_url(url)
    if not s:
        return RANK_EMPTY
    if is_known_ats_url(s):
        return RANK_KNOWN_ATS
    if is_ats_or_company_apply(s):
        return RANK_COMPANY
    if is_aggregator_url(s):
        return RANK_AGGREGATOR
    return RANK_OTHER


def prefer_apply_url(*urls) -> str | None:
    """Pick best Application URL. Never None if any input had a non-empty URL.

    Never prefers an aggregator over an ATS/company URL already in the list.
    """
    cleaned = [_clean_url(u) for u in urls]
    cleaned = [u for u in cleaned if u]
    if not cleaned:
        return None
    # Stable: among equal rank, keep first occurrence (caller order = priority).
    return min(cleaned, key=lambda u: (url_preference_rank(u), cleaned.index(u)))


def merge_apply_url(existing, new) -> str | None:
    """Upgrade-only merge: never replace ATS/company with aggregator."""
    return prefer_apply_url(existing, new)


def extract_ats_urls_from_text(text: str) -> list[str]:
    """Pull https URLs from free text that look like known ATS apply links."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for m in _HTTP_URL_RE.finditer(str(text)):
        raw = m.group(0).rstrip(".,;:!?)")
        if not is_known_ats_url(raw):
            continue
        key = normalize_url(raw)
        if key in seen:
            continue
        seen.add(key)
        found.append(raw)
    return found


def listing_url_candidates(item: dict) -> list[str]:
    """Ordered candidate Application URLs from a scraped listing dict."""
    if not isinstance(item, dict):
        return []
    out: list[str] = []
    for key in ("apply_url", "job_url_direct", "application_url"):
        u = _clean_url(item.get(key))
        if u:
            out.append(u)
    for u in extract_ats_urls_from_text(item.get("description") or ""):
        out.append(u)
    for key in ("job_url", "url", "source_url", "discovery_url"):
        u = _clean_url(item.get(key))
        if u:
            out.append(u)
    for u in item.get("alternate_urls") or []:
        cu = _clean_url(u)
        if cu:
            out.append(cu)
    return out


def enrich_listing_urls(item: dict) -> dict:
    """Return apply_url / job_url / source_url / alternate_urls without dropping links.

    On success (found company/ATS): apply_url upgraded; original aggregator kept
    as job_url and/or source_url.
    On failure: apply_url stays aggregator (or whatever we had) — job still has a link.
    """
    item = dict(item or {})
    discovery = (
        _clean_url(item.get("job_url"))
        or _clean_url(item.get("source_url"))
        or _clean_url(item.get("discovery_url"))
        or ""
    )
    candidates = listing_url_candidates(item)
    apply = prefer_apply_url(*candidates)
    if not apply and discovery:
        apply = discovery
    if not apply:
        return {
            "apply_url": "",
            "job_url": discovery,
            "source_url": _clean_url(item.get("source_url")) or "",
            "alternate_urls": list(item.get("alternate_urls") or []),
        }

    alts: list[str] = []
    seen = {normalize_url(apply)}
    for u in candidates:
        key = normalize_url(u)
        if not key or key in seen:
            continue
        seen.add(key)
        alts.append(u)

    source_url = _clean_url(item.get("source_url")) or ""
    job_url = discovery or apply
    # If we upgraded apply away from an aggregator discovery URL, remember it.
    if discovery and normalize_url(discovery) != normalize_url(apply):
        if is_aggregator_url(discovery):
            source_url = source_url or discovery
        if discovery not in alts and normalize_url(discovery) not in seen:
            alts.append(discovery)

    # Never leave apply empty when we had any URL.
    if not apply:
        apply = discovery or (candidates[0] if candidates else "")
    return {
        "apply_url": apply,
        "job_url": job_url,
        "source_url": source_url,
        "alternate_urls": alts,
    }


def site_source_rank(site) -> int:
    """Rank discovery site labels; unknown → aggregator-ish."""
    s = str(site or "").lower().strip()
    if s in (
        "greenhouse", "lever", "ashby", "recruitee", "personio",
        "smartrecruiters", "workable", "rippling", "breezy", "bamboohr",
        "teamtailor", "jazzhr", "pinpoint",
    ):
        return RANK_KNOWN_ATS
    if s in (
        "indeed", "linkedin", "builtin", "glassdoor", "ziprecruiter",
        "remoteok", "remotive", "jobicy", "rss_feeds", "adzuna", "adzuna_us",
    ):
        return RANK_AGGREGATOR
    return RANK_OTHER


def listing_preference_rank(item: dict) -> tuple:
    """Lower tuple wins when fuzzy-merging listings."""
    enriched = enrich_listing_urls(item)
    apply = enriched["apply_url"]
    url_rank = url_preference_rank(apply)
    site_rank = site_source_rank(item.get("site"))
    # Prefer better of site label vs actual URL quality.
    best = min(url_rank, site_rank)
    desc_len = -len(item.get("description") or "")
    return (best, url_rank, desc_len)


def collect_all_urls(item: dict) -> list[str]:
    """Every URL worth preserving from a listing or job record."""
    urls: list[str] = []
    seen: set[str] = set()
    for u in listing_url_candidates(item):
        key = normalize_url(u)
        if key and key not in seen:
            seen.add(key)
            urls.append(u)
    return urls


def _source_label(item: dict) -> str:
    for key in ("site", "source"):
        s = str(item.get(key) or "").strip()
        if s and s.lower() not in ("nan", "none", "null"):
            return s
    return ""


def merge_source_names(*items: dict) -> list[str]:
    """Ordered unique discovery source labels across merged listings/jobs."""
    names: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for n in item.get("source_names") or []:
            label = str(n or "").strip()
            if not label:
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(label)
        label = _source_label(item)
        if label:
            key = label.lower()
            if key not in seen:
                seen.add(key)
                names.append(label)
    return names


def merge_sources_entries(*items: dict) -> list[dict]:
    """Structured per-source apply/job URLs for UI secondary links."""
    entries: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for src in item.get("sources") or []:
            if not isinstance(src, dict):
                continue
            name = str(src.get("name") or src.get("id") or "").strip()
            apply_u = _clean_url(src.get("apply_url"))
            job_u = _clean_url(src.get("job_url"))
            key = (name.lower(), normalize_url(apply_u or job_u))
            if not name and not apply_u and not job_u:
                continue
            if key in seen:
                continue
            seen.add(key)
            entry = {"name": name or "source"}
            if apply_u:
                entry["apply_url"] = apply_u
            if job_u:
                entry["job_url"] = job_u
            entries.append(entry)
        name = _source_label(item)
        enriched = enrich_listing_urls(item) if (item.get("job_url") or item.get("apply_url") or item.get("site")) else {}
        apply_u = enriched.get("apply_url") or _clean_url(item.get("apply_url"))
        job_u = enriched.get("job_url") or _clean_url(item.get("job_url"))
        if not name and not apply_u and not job_u:
            continue
        key = ((name or "source").lower(), normalize_url(apply_u or job_u))
        if key in seen:
            continue
        seen.add(key)
        entry = {"name": name or "source"}
        if apply_u:
            entry["apply_url"] = apply_u
        if job_u:
            entry["job_url"] = job_u
        entries.append(entry)
    return entries


def merge_listing_pair(a: dict, b: dict) -> dict:
    """Keep the better listing; preserve loser's URLs in alternate_urls / source_url."""
    winner, loser = (a, b) if listing_preference_rank(a) <= listing_preference_rank(b) else (b, a)
    out = dict(winner)
    enriched_w = enrich_listing_urls(winner)
    enriched_l = enrich_listing_urls(loser)
    apply = prefer_apply_url(enriched_w["apply_url"], enriched_l["apply_url"])
    alts: list[str] = []
    seen = {normalize_url(apply)} if apply else set()
    for u in (
        collect_all_urls(winner)
        + collect_all_urls(loser)
        + (enriched_w.get("alternate_urls") or [])
        + (enriched_l.get("alternate_urls") or [])
    ):
        key = normalize_url(u)
        if not key or key in seen:
            continue
        seen.add(key)
        alts.append(u)
    out["apply_url"] = apply or enriched_w["apply_url"] or enriched_l["apply_url"]
    # job_url: keep aggregator discovery when either side had one, else winner's.
    if is_aggregator_url(enriched_w["job_url"]):
        job_url = enriched_w["job_url"]
    elif is_aggregator_url(enriched_l["job_url"]):
        job_url = enriched_l["job_url"]
    else:
        job_url = enriched_w["job_url"] or enriched_l["job_url"]
    if not job_url:
        job_url = out["apply_url"]
    out["job_url"] = job_url
    direct = prefer_apply_url(
        winner.get("job_url_direct"),
        loser.get("job_url_direct"),
        apply,
    )
    if direct and not is_aggregator_url(direct):
        out["job_url_direct"] = direct
    source = (
        enriched_w.get("source_url")
        or enriched_l.get("source_url")
        or (
            job_url
            if is_aggregator_url(job_url)
            and normalize_url(job_url) != normalize_url(apply or "")
            else ""
        )
    )
    if source:
        out["source_url"] = source
    out["alternate_urls"] = alts
    if not out.get("apply_url"):
        out["apply_url"] = prefer_apply_url(*collect_all_urls(out)) or job_url
    names = merge_source_names(winner, loser)
    if names:
        out["source_names"] = names
        # Primary site label stays the winner's; chips use source_names.
        if not out.get("site") and names:
            out["site"] = names[0]
    sources = merge_sources_entries(winner, loser)
    if sources:
        out["sources"] = sources
    return out
