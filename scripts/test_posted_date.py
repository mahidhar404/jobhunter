#!/usr/bin/env python3
"""Tests for shared posted_date extraction + merge rules.

No network, no jobs.json, dummy HTML only.
  python3 scripts/test_posted_date.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from posted_date import apply_posted_dates, extract_date_posted  # noqa: E402


class TestExtractDatePosted(unittest.TestCase):
    def test_ldjson_exact(self):
        html = (
            '<script type="application/ld+json">'
            '{"@type":"JobPosting","datePosted":"2026-08-04T09:00:00Z"}'
            "</script>"
        )
        exact, approx = extract_date_posted(html)
        self.assertEqual(exact, "2026-08-04")
        self.assertIsNone(approx)

    def test_relative_approx(self):
        exact, approx = extract_date_posted("<span>Posted 2 Days Ago</span>")
        self.assertIsNone(exact)
        self.assertIsNotNone(approx)
        self.assertRegex(approx or "", r"^\d{4}-\d{2}-\d{2}$")

    def test_exact_preferred_when_both(self):
        html = '{"datePosted":"2026-08-04"}<span>Posted 2 Days Ago</span>'
        exact, approx = extract_date_posted(html)
        self.assertEqual(exact, "2026-08-04")
        # approx still computed by extractor; callers drop it when exact present
        self.assertIsNotNone(approx)

    def test_empty(self):
        self.assertEqual(extract_date_posted("<html>nothing</html>"), (None, None))


class TestApplyPostedDates(unittest.TestCase):
    def test_exact_fills_and_clears_fallback(self):
        job = {"date_posted_fallback": "2026-08-01"}
        self.assertTrue(apply_posted_dates(job, "2026-08-10", None, source="t"))
        self.assertEqual(job["date_posted"], "2026-08-10")
        self.assertIsNone(job["date_posted_fallback"])
        self.assertEqual(job["date_posted_source"], "t")

    def test_exact_does_not_overwrite_existing_exact(self):
        job = {"date_posted": "2026-08-01"}
        self.assertFalse(apply_posted_dates(job, "2026-08-10", None))
        self.assertEqual(job["date_posted"], "2026-08-01")

    def test_approx_never_overwrites_exact(self):
        job = {"date_posted": "2026-08-01"}
        self.assertFalse(apply_posted_dates(job, None, "2026-08-10"))
        self.assertEqual(job["date_posted"], "2026-08-01")
        self.assertNotIn("date_posted_fallback", job)

    def test_approx_fills_when_undated(self):
        job: dict = {}
        self.assertTrue(apply_posted_dates(job, None, "2026-08-10", source="li"))
        self.assertEqual(job["date_posted_fallback"], "2026-08-10")
        self.assertEqual(job["date_posted_source"], "li")

    def test_approx_does_not_overwrite_fallback(self):
        job = {"date_posted_fallback": "2026-08-01"}
        self.assertFalse(apply_posted_dates(job, None, "2026-08-10"))
        self.assertEqual(job["date_posted_fallback"], "2026-08-01")


if __name__ == "__main__":
    unittest.main()
