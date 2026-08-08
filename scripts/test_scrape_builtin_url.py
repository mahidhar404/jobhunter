#!/usr/bin/env python3
"""Failing-first tests: Built In search URL construction + days allowlist."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_builtin as sb  # noqa: E402


class BuildSearchUrlTests(unittest.TestCase):
    def test_includes_us_country_and_days_and_experience_path(self):
        url = sb.build_search_url(
            "machine learning engineer",
            page=1,
            days_since_updated=1,
        )
        parsed = urlparse(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "builtin.com")
        self.assertTrue(
            parsed.path.startswith("/jobs/entry-level/junior/mid-level/senior"),
            parsed.path,
        )
        qs = parse_qs(parsed.query)
        self.assertEqual(qs.get("search"), ["machine learning engineer"])
        self.assertEqual(qs.get("daysSinceUpdated"), ["1"])
        self.assertEqual(qs.get("country"), ["USA"])
        self.assertNotIn("allLocations", qs)
        self.assertNotIn("page", qs)

    def test_page_two_uses_query_param_not_path_segment(self):
        # Live A/B 2026-08-05: ?page=2 advances, /2 re-serves page 1.
        url = sb.build_search_url("ai engineer", page=2, days_since_updated=7)
        parsed = urlparse(url)
        self.assertFalse(parsed.path.rstrip("/").endswith("/2"), parsed.path)
        qs = parse_qs(parsed.query)
        self.assertEqual(qs.get("page"), ["2"])
        self.assertEqual(qs.get("daysSinceUpdated"), ["7"])
        self.assertEqual(qs.get("country"), ["USA"])

    def test_does_not_use_remote_only_path_by_default(self):
        url = sb.build_search_url("data scientist", page=1, days_since_updated=1)
        self.assertNotIn("/jobs/remote/", urlparse(url).path)


class DaysSinceUpdatedValidationTests(unittest.TestCase):
    def test_supported_values_are_ui_options(self):
        self.assertEqual(set(sb.SUPPORTED_DAYS_SINCE_UPDATED), {1, 3, 7, 30})

    def test_normalize_accepts_supported(self):
        for v in (1, 3, 7, 30, "1", "30"):
            self.assertEqual(sb.normalize_days_since_updated(v), int(v))

    def test_normalize_rejects_unsupported(self):
        for v in (14, 2, 0, -1, 90, "14", "week", None, ""):
            with self.assertRaises(ValueError):
                sb.normalize_days_since_updated(v)

    def test_default_is_one_day(self):
        self.assertEqual(sb.DEFAULT_DAYS_SINCE_UPDATED, 1)
        self.assertEqual(sb.DAYS_SINCE_UPDATED, 1)


class CollectJobUrlsUsesBuilderTests(unittest.TestCase):
    def test_collect_passes_days_and_country_into_fetch_url(self):
        seen: list[str] = []

        def fake_fetch(url: str) -> str | None:
            seen.append(url)
            return '<html><body><a href="/job/a/1">x</a></body></html>'

        with (
            mock.patch.object(sb, "SEARCH_TERMS", ["data scientist"]),
            mock.patch.object(sb, "fetch_html", side_effect=fake_fetch),
            mock.patch.object(sb, "SEARCH_PAGE_DELAY_S", 0),
            mock.patch.object(sb, "log"),
        ):
            urls = sb.collect_job_urls(max_pages_per_term=1, days_since_updated=3)

        self.assertEqual(len(seen), 1)
        qs = parse_qs(urlparse(seen[0]).query)
        self.assertEqual(qs.get("daysSinceUpdated"), ["3"])
        self.assertEqual(qs.get("country"), ["USA"])
        self.assertEqual(set(urls), {f"{sb.BASE}/job/a/1"})


if __name__ == "__main__":
    unittest.main()
