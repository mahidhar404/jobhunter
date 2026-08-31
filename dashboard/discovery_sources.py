"""Discovery source catalog + script path maps (shared by dashboard server).

Lanes: india | worldwide | shared (ATS/scout run when either lane is on).
Does not invent secrets — Adzuna key presence is a boolean probe only.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# Every catalogued board is runnable. Boards without a public listing
# endpoint run through scrape_probe_board.py, which attempts their public
# URLs and reports a specific reason instead of the board sitting greyed
# out as "Disabled" — a board that starts serving listings again then
# picks up with no code change. "probe" = no public endpoint today;
# "dead" = origin verified unreachable (parked domain / refused TLS).
RUNNABLE_STATUSES = frozenset({"active", "rss", "api", "probe", "dead"})

# (id, label, lane, url, scrape_status)
# lane: india | worldwide | shared
# ATS board discovery (greenhouse/lever/ashby/…) was removed 2026-08-25 per
# user request — US-centric, no longer wanted. Apply-side ATS support
# (resolve_apply_urls, form fill, ats_notes/) is unaffected.
_SOURCE_ROWS: list[tuple[str, str, str, str, str]] = [
    # Shared scrapers (JobSpy) — location gate assigns lane after scrape
    ("indeed", "Indeed", "shared", "https://www.indeed.com", "active"),
    ("linkedin", "LinkedIn", "shared", "https://www.linkedin.com/jobs", "active"),
    # India boards
    ("internshala", "Internshala", "india", "https://internshala.com", "active"),
    ("hirist", "Hirist", "india", "https://www.hirist.tech", "active"),
    ("cutshort", "Cutshort", "india", "https://cutshort.io", "active"),
    ("shine", "Shine", "india", "https://www.shine.com", "active"),
    ("freshersworld", "Freshersworld", "india", "https://www.freshersworld.com", "active"),
    ("naukri", "Naukri", "india", "https://www.naukri.com", "active"),
    ("adzuna", "Adzuna (IN)", "india", "https://www.adzuna.in", "api"),
    ("angellist_india", "AngelList India", "india", "https://wellfound.com", "active"),
    # Worldwide / International remote boards
    ("remoteok", "RemoteOK", "worldwide", "https://remoteok.com", "active"),
    ("remotive", "Remotive", "worldwide", "https://remotive.com", "active"),
    ("jobicy", "Jobicy", "worldwide", "https://jobicy.com", "active"),
    ("rss_feeds", "RSS feeds (bundle)", "worldwide", "", "rss"),
    ("himalayas", "Himalayas", "worldwide", "https://himalayas.app", "active"),
    ("weworkremotely", "We Work Remotely", "worldwide", "https://weworkremotely.com", "rss"),
    ("jobspresso", "Jobspresso", "worldwide", "https://jobspresso.co", "rss"),
    ("authentic_jobs", "Authentic Jobs", "worldwide", "https://authenticjobs.com", "rss"),
    ("nodesk", "NoDesk", "worldwide", "https://nodesk.co", "rss"),
    ("landing_jobs", "Landing.jobs", "worldwide", "https://landing.jobs", "active"),
    ("jsremotely", "JS Remotely", "worldwide", "https://jsremotely.com", "active"),
    ("working_nomads", "Working Nomads", "worldwide", "https://www.workingnomads.com", "active"),
    # 2026-08-25: domain is parked (every path 403s from `server: Parking/1.0`).
    ("europeremotely", "EuropeRemotely", "worldwide", "https://europeremotely.com", "dead"),
    ("arbeitnow", "Arbeitnow", "worldwide", "https://www.arbeitnow.com", "active"),
    ("relocate_me", "relocate.me", "worldwide", "https://relocate.me", "active"),
    # 2026-08-25: origin refuses the TLS handshake (tlsv1 alert internal error).
    ("germanstartups", "German Startups Jobs", "worldwide", "https://jobs.germanstartups.com", "dead"),
    ("justremote", "JustRemote", "worldwide", "https://justremote.co", "active"),
    ("dynamitejobs", "Dynamite Jobs", "worldwide", "https://dynamitejobs.com", "active"),
    # 2026-08-25: NOT captcha-blocked — /jobs and /role pages are server-rendered.
    ("wellfound", "Wellfound", "worldwide", "https://wellfound.com", "active"),
    ("otta", "Otta", "worldwide", "https://otta.com", "probe"),
    ("yc_jobs", "Y Combinator Jobs", "worldwide", "https://www.ycombinator.com/jobs", "active"),
    ("turing", "Turing", "worldwide", "https://www.turing.com", "probe"),
    ("angelhub", "AngelHub", "worldwide", "https://angelhub.io", "probe"),
    ("producthunt_jobs", "Product Hunt Jobs", "worldwide", "https://www.producthunt.com/jobs", "probe"),
    ("remotetechjobs", "RemoteTechJobs", "worldwide", "https://remotetechjobs.com", "probe"),
    ("outsourcely", "Outsourcely", "worldwide", "https://www.outsourcely.com", "probe"),
    ("hubstaff_talent", "Hubstaff Talent", "worldwide", "https://talent.hubstaff.com", "probe"),
    ("workew", "Workew", "worldwide", "https://workew.com", "rss"),
    ("pangian", "Pangian", "worldwide", "https://pangian.com", "probe"),
    ("hired", "Hired", "worldwide", "https://hired.com", "probe"),
    ("themuse", "The Muse", "worldwide", "https://www.themuse.com", "api"),
    ("jooble", "Jooble", "worldwide", "https://jooble.org", "probe"),
    ("topaijobs", "TopAIJobs", "worldwide", "https://topai.jobs", "probe"),
    ("crossover", "Crossover", "worldwide", "https://www.crossover.com", "probe"),
    ("jobbatical", "Jobbatical", "worldwide", "https://jobbatical.com", "probe"),
]

DISCOVERY_SOURCE_DEFS: list[tuple[str, str]] = [
    (sid, label) for sid, label, _lane, _url, _st in _SOURCE_ROWS
]

DISCOVERY_SOURCE_META: dict[str, dict[str, Any]] = {
    sid: {
        "id": sid,
        "label": label,
        "lane": lane,
        "url": url,
        "scrape_status": status,
    }
    for sid, label, lane, url, status in _SOURCE_ROWS
}

DISCOVERY_SOURCE_IDS = tuple(sid for sid, *_ in _SOURCE_ROWS)

SCOUT_SOURCE_IDS = ("indeed", "linkedin")

INDIA_ONLY_SOURCE_IDS = (
    "internshala", "hirist", "cutshort", "shine", "freshersworld", "naukri",
    "adzuna", "angellist_india",
)

# Runnable worldwide feeds (have scripts). Catalog-only boards are UI-only.
WORLDWIDE_FEED_SOURCE_IDS = (
    "remoteok", "remotive", "jobicy", "rss_feeds",
    "himalayas", "weworkremotely", "jobspresso", "authentic_jobs", "nodesk",
    "landing_jobs", "jsremotely", "working_nomads",
    "arbeitnow", "relocate_me", "justremote", "dynamitejobs",
    "themuse", "workew", "yc_jobs", "wellfound",
    # Probe boards: run every pass, report why they yield nothing.
    "otta", "turing", "angelhub", "producthunt_jobs", "remotetechjobs",
    "outsourcely", "hubstaff_talent", "pangian", "hired", "jooble",
    "topaijobs", "crossover", "jobbatical", "europeremotely",
    "germanstartups",
)

US_FEED_SOURCE_IDS = WORLDWIDE_FEED_SOURCE_IDS

RECENCY_SOURCE_IDS = ("indeed", "linkedin", "adzuna")
PRIMARY_SOURCE_IDS = (
    *SCOUT_SOURCE_IDS,
    *WORLDWIDE_FEED_SOURCE_IDS,
    *INDIA_ONLY_SOURCE_IDS,
)

INDIA_SOURCE_SCRIPTS = {
    "internshala": ROOT / "scripts" / "scrape_internshala.py",
    "hirist": ROOT / "scripts" / "scrape_hirist.py",
    "cutshort": ROOT / "scripts" / "scrape_cutshort.py",
    "shine": ROOT / "scripts" / "scrape_shine.py",
    "freshersworld": ROOT / "scripts" / "scrape_freshersworld.py",
    "naukri": ROOT / "scripts" / "scrape_naukri.py",
    "adzuna": ROOT / "scripts" / "scrape_adzuna.py",
    "angellist_india": ROOT / "scripts" / "scrape_wellfound.py",
}

WORLDWIDE_FEED_SOURCE_SCRIPTS = {
    "remoteok": ROOT / "scripts" / "scrape_remoteok.py",
    "remotive": ROOT / "scripts" / "scrape_remotive.py",
    "jobicy": ROOT / "scripts" / "scrape_jobicy.py",
    "rss_feeds": ROOT / "scripts" / "scrape_rss_feeds.py",
    "himalayas": ROOT / "scripts" / "scrape_ww_boards.py",
    "weworkremotely": ROOT / "scripts" / "scrape_ww_boards.py",
    "jobspresso": ROOT / "scripts" / "scrape_ww_boards.py",
    "authentic_jobs": ROOT / "scripts" / "scrape_ww_boards.py",
    "nodesk": ROOT / "scripts" / "scrape_ww_boards.py",
    "landing_jobs": ROOT / "scripts" / "scrape_ww_boards.py",
    "jsremotely": ROOT / "scripts" / "scrape_ww_boards.py",
    "working_nomads": ROOT / "scripts" / "scrape_ww_boards.py",
    "arbeitnow": ROOT / "scripts" / "scrape_ww_boards.py",
    "relocate_me": ROOT / "scripts" / "scrape_ww_boards.py",
    "justremote": ROOT / "scripts" / "scrape_ww_boards.py",
    "dynamitejobs": ROOT / "scripts" / "scrape_ww_boards.py",
    "themuse": ROOT / "scripts" / "scrape_ww_boards.py",
    "workew": ROOT / "scripts" / "scrape_ww_boards.py",
    "yc_jobs": ROOT / "scripts" / "scrape_ww_boards.py",
    "wellfound": ROOT / "scripts" / "scrape_wellfound.py",
    **{
        sid: ROOT / "scripts" / "scrape_probe_board.py"
        for sid in (
            "otta", "turing", "angelhub", "producthunt_jobs",
            "remotetechjobs", "outsourcely", "hubstaff_talent", "pangian",
            "hired", "jooble", "topaijobs", "crossover", "jobbatical",
            "europeremotely", "germanstartups",
        )
    },
}

US_FEED_SOURCE_SCRIPTS = WORLDWIDE_FEED_SOURCE_SCRIPTS

US_FEED_LOG_LABEL_TO_ID = {
    sid.replace("_", "-"): sid for sid in WORLDWIDE_FEED_SOURCE_IDS
}

ADZUNA_MISSING_KEYS_DETAIL = (
    "Missing Adzuna API keys (set ADZUNA_APP_ID/ADZUNA_APP_KEY or web_keys.json)"
)


def source_is_runnable(source_id: str) -> bool:
    meta = DISCOVERY_SOURCE_META.get(source_id)
    if not meta:
        return False
    return meta.get("scrape_status") in RUNNABLE_STATUSES


def catalog_payload() -> list[dict[str, Any]]:
    return [dict(meta) for meta in DISCOVERY_SOURCE_META.values()]


def adzuna_api_keys_present(*, web_keys_path: Path | None = None) -> bool:
    """True when Adzuna can run. Never returns secret values."""
    import json

    if os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"):
        return True
    path = web_keys_path or (ROOT / "web_keys.json")
    try:
        if not path.is_file():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    app_id = data.get("adzuna_app_id") or data.get("ADZUNA_APP_ID")
    app_key = data.get("adzuna_app_key") or data.get("ADZUNA_APP_KEY")
    return bool(app_id and app_key)


def adzuna_source_health() -> dict[str, dict]:
    """Per-Adzuna-source health for discovery status (no secret values)."""
    ok = adzuna_api_keys_present()
    row = {
        "keys_configured": ok,
        "fail_reason": None if ok else ADZUNA_MISSING_KEYS_DETAIL,
    }
    return {"adzuna": dict(row)}
