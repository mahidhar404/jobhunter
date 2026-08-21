#!/usr/bin/env python3
"""Discovery source module + Adzuna health surface."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import discovery_sources as ds  # noqa: E402
import server as srv  # noqa: E402


def test_discovery_sources_module_matches_server() -> None:
    assert list(ds.DISCOVERY_SOURCE_IDS) == list(srv.DISCOVERY_SOURCE_IDS)
    assert ds.ATS_SOURCE_IDS == srv.ATS_SOURCE_IDS
    assert ds.US_FEED_SOURCE_IDS == srv.US_FEED_SOURCE_IDS


def test_adzuna_keys_absent_surfaces_in_discovery_status() -> None:
    with mock.patch.object(ds, "adzuna_api_keys_present", return_value=False), mock.patch.object(
        srv, "adzuna_api_keys_present", return_value=False
    ), mock.patch.object(srv, "adzuna_source_health", return_value=ds.adzuna_source_health()):
        # Force health helper too
        with mock.patch.object(
            srv, "adzuna_source_health",
            return_value={
                "adzuna_us": {
                    "keys_configured": False,
                    "fail_reason": ds.ADZUNA_MISSING_KEYS_DETAIL,
                },
                "adzuna": {
                    "keys_configured": False,
                    "fail_reason": ds.ADZUNA_MISSING_KEYS_DETAIL,
                },
            },
        ):
            status = srv.discovery_status()
    assert status["adzuna_keys_configured"] is False
    assert "Missing Adzuna API keys" in (status.get("adzuna_keys_detail") or "")
    assert status["source_health"]["adzuna_us"]["keys_configured"] is False


def test_finalize_adzuna_without_keys_fails_loud() -> None:
    srv._discovery_state["sources"] = srv._empty_discovery_sources({"adzuna_us"})
    with mock.patch.object(srv, "adzuna_api_keys_present", return_value=False):
        srv._finalize_discovery_source(
            "adzuna_us", 0, ROOT / "listings" / "no-such-adzuna.json", aborted=False
        )
    row = next(s for s in srv._discovery_state["sources"] if s["id"] == "adzuna_us")
    assert row["status"] == "failed"
    assert "Missing Adzuna API keys" in (row.get("detail") or "")


def test_parse_adzuna_skip_log_line() -> None:
    srv._discovery_state["sources"] = srv._empty_discovery_sources({"adzuna_us"})
    srv._parse_discovery_log_line(
        "disabled/skipped (adzuna-us): no Adzuna API keys (set ADZUNA_APP_ID…)",
        "us_feed",
    )
    row = next(s for s in srv._discovery_state["sources"] if s["id"] == "adzuna_us")
    assert row["status"] == "failed"
    assert "Missing Adzuna API keys" in (row.get("detail") or "")


if __name__ == "__main__":
    test_discovery_sources_module_matches_server()
    test_adzuna_keys_absent_surfaces_in_discovery_status()
    test_finalize_adzuna_without_keys_fails_loud()
    test_parse_adzuna_skip_log_line()
    print("OK test_discovery_source_health")
