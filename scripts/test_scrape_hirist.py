#!/usr/bin/env python3
"""Fixture tests for scrape_hirist JSON normalize (no network)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_hirist as sh  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_normalize_jobs_envelope(self):
        data = {
            "data": [
                {
                    "title": "Backend Developer",
                    "companyName": "Fintech Co",
                    "location": ["Gurugram", "Remote"],
                    "description": "Build APIs.",
                    "salary": "12-18 LPA",
                    "url": "/j/backend-developer-999",
                    "postedDate": "2026-08-02T00:00:00Z",
                },
                {
                    "jobTitle": "Data Engineer",
                    "company": {"name": "DataWorks"},
                    "city": "Pune",
                    "id": "555",
                    "slug": "data-engineer",
                },
            ]
        }
        jobs = sh.normalize_jobs(data, search_term="backend developer")
        self.assertEqual(len(jobs), 2)
        a = jobs[0]
        self.assertEqual(a["title"], "Backend Developer")
        self.assertEqual(a["company"], "Fintech Co")
        self.assertEqual(a["location"], "Gurugram, Remote")
        self.assertEqual(a["site"], "hirist")
        self.assertEqual(a["job_url"], "https://www.hirist.tech/j/backend-developer-999")
        self.assertIn("12-18 LPA", a["description"])  # salary appended for LPA parse
        self.assertEqual(a["date_posted"], "2026-08-02")
        # Second: company dict + slug/id url synthesis
        b = jobs[1]
        self.assertEqual(b["company"], "DataWorks")
        self.assertEqual(b["job_url"], "https://www.hirist.tech/j/data-engineer-555")

    def test_normalize_bad_input(self):
        self.assertEqual(sh.normalize_jobs(None), [])
        self.assertEqual(sh.normalize_jobs({}), [])
        self.assertEqual(sh.normalize_jobs({"data": []}), [])
        # record with no title / no url dropped
        self.assertEqual(sh.normalize_jobs({"jobs": [{"company": "X"}]}), [])


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
