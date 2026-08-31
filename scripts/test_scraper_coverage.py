#!/usr/bin/env python3
"""Coverage settings the 2026-08-26 optimization pass established.

Each number here was measured, not guessed — these tests exist so a later edit
that quietly narrows coverage fails loudly instead of silently halving the
scrape.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_ww_boards as ww  # noqa: E402
import scrape_jobicy as jb  # noqa: E402
import scrape_remoteok as ro  # noqa: E402
import scrape_wellfound as wf  # noqa: E402


class RecencyWindowTests(unittest.TestCase):
    """A hardcoded 10-day window discarded most of what these boards return."""

    def test_every_feed_scraper_exposes_max_days(self):
        for mod in (ww, jb, ro):
            self.assertTrue(hasattr(mod, "DEFAULT_MAX_DAYS"), mod.__name__)
            self.assertGreaterEqual(mod.DEFAULT_MAX_DAYS, 21, mod.__name__)

    def test_ww_boards_max_days_is_runtime_settable(self):
        self.assertTrue(hasattr(ww, "max_days"))
        original = ww._MAX_DAYS
        try:
            ww._MAX_DAYS = 45
            self.assertEqual(ww.max_days(), 45)
        finally:
            ww._MAX_DAYS = original


class QueryBreadthTests(unittest.TestCase):
    """These APIs have no paging — breadth comes from unioning query axes."""

    def test_jobicy_queries_industry_geo_and_tag(self):
        urls = jb.query_urls()
        self.assertGreaterEqual(len(urls), 15, "jobicy lost query axes")
        joined = " ".join(urls)
        for axis in ("industry=", "geo=", "tag="):
            self.assertIn(axis, joined, f"jobicy no longer queries {axis}")
        self.assertTrue(all(f"count={jb.COUNT}" in u for u in urls))

    def test_remoteok_queries_enough_tags(self):
        # The bare /api is RemoteOK's general feed (bell captain, sandwich
        # artist); tech roles only surface under tags.
        self.assertGreaterEqual(len(ro.TAGS), 18, "remoteok lost tag coverage")
        urls = ro.query_urls()
        self.assertEqual(len(urls), len(ro.TAGS) + 1)

    def test_wellfound_covers_roles_and_india_locations(self):
        self.assertGreaterEqual(len(wf.WORLDWIDE_PATHS), 18)
        self.assertGreaterEqual(len(wf.INDIA_PATHS), 12)
        self.assertIn("/jobs", wf.WORLDWIDE_PATHS)


class WastedRequestTests(unittest.TestCase):
    """Coverage that was measured to add nothing was deliberately removed."""

    def test_yc_does_not_fan_out_over_roles(self):
        """YC filters roles client-side — every role page returns the same set
        (engineering vs data-science shared 40 of 41 links)."""
        src = (ROOT / "scripts" / "scrape_ww_boards.py").read_text()
        block = src[src.index("def scrape_yc_jobs"):]
        block = block[:block.index("SCRAPERS = {")]
        self.assertIn('roles = ("engineering", "data-science")', block)

    def test_dynamitejobs_only_queries_categories_that_carry_jobs(self):
        src = (ROOT / "scripts" / "scrape_ww_boards.py").read_text()
        block = src[src.index("def scrape_dynamitejobs"):]
        block = block[:block.index("def scrape_weworkremotely")]
        # Check the queried tuple, not the whole body — the rejected slugs are
        # named in the comment that explains why they are rejected.
        cats = block[block.index("cats = ("):]
        cats = cats[:cats.index(")")]
        for dead in ("remote-devops-jobs", "remote-engineering-jobs", "remote-it-jobs"):
            self.assertNotIn(dead, cats, f"{dead} returns a shell with no postings")
        self.assertIn("remote-development-jobs", cats)

    def test_landing_jobs_stops_at_the_end_of_the_api(self):
        src = (ROOT / "scripts" / "scrape_ww_boards.py").read_text()
        block = src[src.index("def scrape_landing_jobs"):]
        block = block[:block.index("def scrape_jsremotely")]
        self.assertIn("for offset in (0, 50, 100):", block)


class DeepPaginationTests(unittest.TestCase):
    def test_himalayas_pages_past_the_first_few_hundred(self):
        src = (ROOT / "scripts" / "scrape_ww_boards.py").read_text()
        self.assertIn("range(0, 1200, 20)", src, "himalayas pagination narrowed")

    def test_arbeitnow_pages_deeper_than_five(self):
        src = (ROOT / "scripts" / "scrape_ww_boards.py").read_text()
        block = src[src.index("def scrape_arbeitnow"):]
        block = block[:block.index("def scrape_landing_jobs")]
        self.assertIn("range(1, 16)", block)


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
