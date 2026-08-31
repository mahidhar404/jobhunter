#!/usr/bin/env python3
"""No board may sit greyed out as "Disabled" in the UI.

Boards used to be catalogued `catalog` / `needs_account` / `blocked_captcha`,
which meant Discover never scheduled them and the UI rendered them off. That
hid *why* a board produced nothing, and made a stale classification permanent:
wellfound was marked `blocked_captcha` while its /jobs page was in fact plain
server-rendered HTML. Every board now runs each pass and reports a real
terminal status.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "scripts"))

import discovery_sources as ds  # noqa: E402


class AllBoardsRunnableTests(unittest.TestCase):
    def test_every_catalogued_board_is_runnable(self):
        not_runnable = [
            sid for sid in ds.DISCOVERY_SOURCE_IDS
            if not ds.source_is_runnable(sid)
        ]
        self.assertEqual(
            not_runnable, [],
            "these boards would render as Disabled in the UI")

    def test_every_board_is_scheduled_in_a_lane(self):
        scheduled = (set(ds.SCOUT_SOURCE_IDS)
                     | set(ds.INDIA_ONLY_SOURCE_IDS)
                     | set(ds.WORLDWIDE_FEED_SOURCE_IDS))
        missing = sorted(set(ds.DISCOVERY_SOURCE_IDS) - scheduled)
        self.assertEqual(missing, [], "unscheduled boards never run")

    def test_every_scheduled_board_has_a_script_that_exists(self):
        for sid in ds.INDIA_ONLY_SOURCE_IDS:
            self.assertIn(sid, ds.INDIA_SOURCE_SCRIPTS, f"{sid} has no script")
            self.assertTrue(ds.INDIA_SOURCE_SCRIPTS[sid].is_file(),
                            f"{sid}: {ds.INDIA_SOURCE_SCRIPTS[sid]} missing")
        for sid in ds.WORLDWIDE_FEED_SOURCE_IDS:
            self.assertIn(sid, ds.WORLDWIDE_FEED_SOURCE_SCRIPTS, f"{sid} has no script")
            self.assertTrue(ds.WORLDWIDE_FEED_SOURCE_SCRIPTS[sid].is_file(),
                            f"{sid}: script missing")

    def test_probe_boards_all_have_a_documented_reason(self):
        import scrape_probe_board as probe
        probe_ids = [
            sid for sid, meta in ds.DISCOVERY_SOURCE_META.items()
            if meta.get("scrape_status") in ("probe", "dead")
        ]
        for sid in probe_ids:
            self.assertIn(sid, probe.PROBES, f"{sid} has no probe definition")
            self.assertTrue(probe.PROBES[sid].get("reason"),
                            f"{sid} must explain why it yields nothing")

    def test_wellfound_is_no_longer_marked_captcha_blocked(self):
        self.assertEqual(
            ds.DISCOVERY_SOURCE_META["wellfound"]["scrape_status"], "active")
        self.assertIn("wellfound", ds.WORLDWIDE_FEED_SOURCE_IDS)

    def test_angellist_india_runs_in_the_india_lane(self):
        self.assertIn("angellist_india", ds.INDIA_ONLY_SOURCE_IDS)
        self.assertEqual(
            ds.INDIA_SOURCE_SCRIPTS["angellist_india"].name,
            "scrape_wellfound.py")


class ScrapeCmdWiringTests(unittest.TestCase):
    def test_site_and_india_flags_are_passed(self):
        src = (ROOT / "dashboard" / "server.py").read_text()
        self.assertIn('_SITE_ARG_SCRIPTS', src)
        self.assertIn('"scrape_probe_board.py"', src)
        self.assertIn('cmd.append("--india")', src)

    def test_raw_scrapes_are_archived_before_filtering(self):
        src = (ROOT / "dashboard" / "server.py").read_text()
        self.assertIn("_archive_listing_file(listing_path)", src)
        archive_at = src.index("_archive_listing_file(listing_path)")
        dedup_at = src.index("dedup_listings.py")
        self.assertLess(archive_at, dedup_at,
                        "archive must run before dedup can drop rows")


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
