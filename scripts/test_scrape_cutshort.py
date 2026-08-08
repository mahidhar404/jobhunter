#!/usr/bin/env python3
"""Fixture tests for scrape_cutshort JSON normalize (no network)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_cutshort as sc  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_normalize_jobs_envelope(self):
        data = {
            "jobs": [
                {
                    "title": "Full Stack Engineer",
                    "company": {"name": "Startup X"},
                    "location": "Bengaluru",
                    "summary": "Own the product.",
                    "ctc": "20 LPA",
                    "slug": "full-stack-engineer-startup-x",
                    "created_at": "2026-07-30",
                },
                {
                    "role": "Data Scientist",
                    "company_name": "Startup Y",
                    "locations": ["Remote", "India"],
                    "id": "abc123",
                },
            ]
        }
        jobs = sc.normalize_jobs(data, search_term="software engineer")
        self.assertEqual(len(jobs), 2)
        a = jobs[0]
        self.assertEqual(a["title"], "Full Stack Engineer")
        self.assertEqual(a["company"], "Startup X")
        self.assertEqual(a["site"], "cutshort")
        self.assertEqual(a["job_url"], "https://cutshort.io/job/full-stack-engineer-startup-x")
        self.assertIn("20 LPA", a["description"])
        self.assertEqual(a["date_posted"], "2026-07-30")
        b = jobs[1]
        self.assertEqual(b["company"], "Startup Y")
        self.assertEqual(b["location"], "Remote, India")
        self.assertEqual(b["job_url"], "https://cutshort.io/job/abc123")

    def test_normalize_bad_input(self):
        self.assertEqual(sc.normalize_jobs(None), [])
        self.assertEqual(sc.normalize_jobs({}), [])
        self.assertEqual(sc.normalize_jobs({"jobs": [{"company": "X"}]}), [])


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
