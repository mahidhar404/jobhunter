"""Discovery source catalog + script path maps (shared by dashboard server).

Kept out of server.py so catalog/id tests and scrapers stay import-light.
Does not invent secrets — Adzuna key presence is a boolean probe only.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Listing sources discovery actually scrapes (JobSpy sites + ATS boards + Built In).
DISCOVERY_SOURCE_DEFS: list[tuple[str, str]] = [
    ("indeed", "Indeed"),
    ("linkedin", "LinkedIn"),
    ("greenhouse", "Greenhouse"),
    ("lever", "Lever"),
    ("ashby", "Ashby"),
    ("recruitee", "Recruitee"),
    ("personio", "Personio"),
    ("smartrecruiters", "SmartRecruiters"),
    ("workable", "Workable"),
    ("rippling", "Rippling"),
    ("breezy", "Breezy"),
    ("bamboohr", "BambooHR"),
    ("teamtailor", "Teamtailor"),
    ("jazzhr", "JazzHR"),
    ("pinpoint", "Pinpoint"),
    ("builtin", "Built In"),
    ("remoteok", "RemoteOK"),
    ("remotive", "Remotive"),
    ("jobicy", "Jobicy"),
    ("rss_feeds", "RSS feeds"),
    ("adzuna_us", "Adzuna (US)"),
    # India-only sources — only run when the India region is enabled.
    ("internshala", "Internshala"),
    ("hirist", "Hirist"),
    ("cutshort", "Cutshort"),
    ("adzuna", "Adzuna (IN)"),
]

SCOUT_SOURCE_IDS = ("indeed", "linkedin")
ATS_SOURCE_IDS = (
    "greenhouse", "lever", "ashby", "recruitee", "personio",
    "smartrecruiters", "workable", "rippling", "breezy", "bamboohr",
    "teamtailor", "jazzhr", "pinpoint",
)
US_FEED_SOURCE_IDS = ("remoteok", "remotive", "jobicy", "rss_feeds", "adzuna_us")
INDIA_ONLY_SOURCE_IDS = ("internshala", "hirist", "cutshort", "adzuna")
RECENCY_SOURCE_IDS = ("indeed", "linkedin", "builtin", "adzuna_us", "adzuna")
PRE_ATS_SOURCE_IDS = (
    *SCOUT_SOURCE_IDS,
    "builtin",
    *US_FEED_SOURCE_IDS,
    *INDIA_ONLY_SOURCE_IDS,
)

INDIA_SOURCE_SCRIPTS = {
    "internshala": ROOT / "scripts" / "scrape_internshala.py",
    "hirist": ROOT / "scripts" / "scrape_hirist.py",
    "cutshort": ROOT / "scripts" / "scrape_cutshort.py",
    "adzuna": ROOT / "scripts" / "scrape_adzuna.py",
}
US_FEED_SOURCE_SCRIPTS = {
    "remoteok": ROOT / "scripts" / "scrape_remoteok.py",
    "remotive": ROOT / "scripts" / "scrape_remotive.py",
    "jobicy": ROOT / "scripts" / "scrape_jobicy.py",
    "rss_feeds": ROOT / "scripts" / "scrape_rss_feeds.py",
    "adzuna_us": ROOT / "scripts" / "scrape_adzuna.py",
}

DISCOVERY_SOURCE_IDS = tuple(sid for sid, _ in DISCOVERY_SOURCE_DEFS)
US_FEED_LOG_LABEL_TO_ID = {sid.replace("_", "-"): sid for sid in US_FEED_SOURCE_IDS}

ADZUNA_MISSING_KEYS_DETAIL = (
    "Missing Adzuna API keys (set ADZUNA_APP_ID/ADZUNA_APP_KEY or web_keys.json)"
)


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
    return {"adzuna_us": dict(row), "adzuna": dict(row)}
