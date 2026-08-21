#!/usr/bin/env python3
"""Discovery catalog / listing-path tests for new sources."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


def test_new_discovery_sources_in_catalog() -> None:
    ids = set(srv.DISCOVERY_SOURCE_IDS)
    for sid in (
        "teamtailor", "jazzhr", "pinpoint",
        "remoteok", "remotive", "jobicy", "rss_feeds", "adzuna_us",
    ):
        assert sid in ids, f"missing {sid} in DISCOVERY_SOURCE_DEFS"


def test_ats_sources_include_new_platforms() -> None:
    for sid in ("teamtailor", "jazzhr", "pinpoint"):
        assert sid in srv.ATS_SOURCE_IDS


def test_us_feed_sources_not_india_only() -> None:
    for sid in srv.US_FEED_SOURCE_IDS:
        assert sid not in srv.INDIA_ONLY_SOURCE_IDS


def test_listing_paths_for_new_sources() -> None:
    today = "2026-08-19"
    assert srv._source_listing_path(today, "adzuna_us").name == f"{today}-adzuna-us.json"
    assert srv._source_listing_path(today, "remoteok").name == f"{today}-remoteok.json"
    assert srv._source_listing_path(today, "remotive").name == f"{today}-remotive.json"
    assert srv._source_listing_path(today, "jobicy").name == f"{today}-jobicy.json"
    assert srv._source_listing_path(today, "rss_feeds").name == f"{today}-rss_feeds.json"
    assert srv._source_listing_path(today, "teamtailor").name == f"{today}-ats-teamtailor.json"
    assert srv._source_listing_path(today, "jazzhr").name == f"{today}-ats-jazzhr.json"
    assert srv._source_listing_path(today, "pinpoint").name == f"{today}-ats-pinpoint.json"


def test_pre_ats_includes_feeds_not_ats() -> None:
    pre = set(srv.PRE_ATS_SOURCE_IDS)
    ats = set(srv.ATS_SOURCE_IDS)
    for sid in ("remoteok", "remotive", "jobicy", "rss_feeds", "adzuna_us"):
        assert sid in pre and sid not in ats
    assert "greenhouse" not in pre and "greenhouse" in ats


def test_us_feed_scripts_exist() -> None:
    for sid, path in srv.US_FEED_SOURCE_SCRIPTS.items():
        assert path.is_file(), f"missing scraper for {sid}: {path}"


def test_js_catalog_matches_server_defs() -> None:
    js = (ROOT / "dashboard" / "static" / "app.js").read_text()
    start = js.index("const DISCOVERY_SOURCE_CATALOG = [")
    block = js[start: js.index("];", start) + 2]
    js_ids = []
    for line in block.splitlines():
        if 'id: "' not in line:
            continue
        js_ids.append(line.split('id: "', 1)[1].split('"', 1)[0])
    assert js_ids == list(srv.DISCOVERY_SOURCE_IDS)


def test_us_feed_log_lines_map_to_source_ids() -> None:
    samples = {
        "remoteok": "  got 4 relevant results from remoteok/api",
        "remotive": "  got 3 relevant results from remotive/api",
        "jobicy": "  got 2 relevant results from jobicy/api",
        "rss_feeds": "  got 7 relevant results from rss-feeds/all",
        "adzuna_us": "  got 5 results from adzuna-us/data engineer p1",
    }
    srv._discovery_state["sources"] = srv._empty_discovery_sources(set(samples))
    for sid, line in samples.items():
        srv._parse_discovery_log_line(line, "us_feed")
        row = next(s for s in srv._discovery_state["sources"] if s["id"] == sid)
        assert row["count"] > 0, f"{sid} count not updated from {line!r}"
        assert row["status"] == "collecting"


if __name__ == "__main__":
    test_new_discovery_sources_in_catalog()
    test_ats_sources_include_new_platforms()
    test_us_feed_sources_not_india_only()
    test_listing_paths_for_new_sources()
    test_pre_ats_includes_feeds_not_ats()
    test_us_feed_scripts_exist()
    test_js_catalog_matches_server_defs()
    test_us_feed_log_lines_map_to_source_ids()
    print("ok: discovery source defs tests passed")
