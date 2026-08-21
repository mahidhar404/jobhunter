#!/usr/bin/env python3
"""Fixture tests for scrape_remotive (no network)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_remotive as sr  # noqa: E402


class RemotiveTests(unittest.TestCase):
    def test_normalize_relevant_jobs(self):
        rows = [
            {
                "id": 1,
                "title": "Machine Learning Engineer",
                "company_name": "Acme",
                "url": "https://remotive.com/remote-jobs/ml-1",
                "description": "<p>Build models</p>",
                "publication_date": "2026-08-18T08:00:07",
                "candidate_required_location": "USA",
                "job_type": "full_time",
            },
            {
                "id": 2,
                "title": "Office Manager",
                "url": "https://remotive.com/remote-jobs/office-2",
            },
        ]
        jobs = sr.normalize_jobs(rows)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["site"], "remotive")
        self.assertIn("Build models", jobs[0]["description"])

    def test_scrape_fixture(self):
        payload = {
            "jobs": [{
                "id": 9,
                "title": "Data Scientist",
                "company_name": "Lab",
                "url": "https://remotive.com/remote-jobs/data-9",
                "description": "<p>Analyze</p>",
                "publication_date": "2026-08-01",
            }],
        }
        with mock.patch.object(sr, "fetch_json", return_value=payload), \
             mock.patch.object(sr, "polite_sleep"):
            jobs = sr.scrape()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Data Scientist")

    def test_query_urls_use_live_category_slugs(self):
        urls = sr.query_urls()
        joined = " ".join(urls)
        self.assertIn(sr.API_URL, urls)
        self.assertIn("category=software-development", joined)
        self.assertIn("category=data", joined)
        self.assertIn("category=artificial-intelligence", joined)
        self.assertNotIn("limit=", joined)

    def test_scrape_unions_categories_keeps_title_filter(self):
        def fake_fetch(url: str):
            if "software-development" in url:
                return {"jobs": [{
                    "id": 1,
                    "title": "Data Engineer",
                    "url": "https://remotive.com/j/1",
                    "company_name": "A",
                }, {
                    "id": 3,
                    "title": "Office Manager",
                    "url": "https://remotive.com/j/3",
                }]}
            if "category=data" in url:
                return {"jobs": [{
                    "id": 1,
                    "title": "Data Engineer",
                    "url": "https://remotive.com/j/1",
                    "company_name": "A",
                }, {
                    "id": 2,
                    "title": "Machine Learning Engineer",
                    "url": "https://remotive.com/j/2",
                    "company_name": "B",
                }]}
            return {"jobs": []}

        with mock.patch.object(sr, "fetch_json", side_effect=fake_fetch), \
             mock.patch.object(sr, "polite_sleep"):
            jobs = sr.scrape()
        titles = {j["title"] for j in jobs}
        self.assertEqual(titles, {"Data Engineer", "Machine Learning Engineer"})


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
