#!/usr/bin/env python3
"""Fixture tests for scrape_jobicy (no network)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_jobicy as sj  # noqa: E402


class JobicyTests(unittest.TestCase):
    def test_normalize_relevant_jobs(self):
        rows = [
            {
                "id": 1,
                "jobTitle": "Data Engineer",
                "companyName": "Acme",
                "url": "https://jobicy.com/jobs/1-data-engineer",
                "jobDescription": "<p>Build pipelines</p>",
                "pubDate": "2026-08-18",
                "jobType": ["Full-Time"],
                "jobGeo": "USA",
            },
            {
                "id": 2,
                "jobTitle": "Office Manager",
                "url": "https://jobicy.com/jobs/2-office",
            },
        ]
        jobs = sj.normalize_jobs(rows)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["site"], "jobicy")

    def test_scrape_fixture(self):
        payload = {
            "friendlyNotice": "Credit Jobicy with a link.",
            "jobs": [{
                "id": 9,
                "jobTitle": "Data Scientist",
                "companyName": "Lab",
                "url": "https://jobicy.com/jobs/9-data-scientist",
                "jobDescription": "<p>Analyze</p>",
                "pubDate": "2026-08-01",
            }],
        }
        with mock.patch.object(sj, "fetch_json", return_value=payload), \
             mock.patch.object(sj, "polite_sleep"):
            jobs = sj.scrape()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Data Scientist")

    def test_query_urls_use_count_100_and_industries(self):
        urls = sj.query_urls()
        self.assertTrue(all("count=100" in u for u in urls))
        joined = " ".join(urls)
        for industry in ("data-science", "engineering", "admin"):
            self.assertIn(industry, joined)
        self.assertGreaterEqual(len(urls), 4)

    def test_scrape_unions_industry_feeds_and_dedups(self):
        payload_a = {
            "jobs": [{
                "id": 1,
                "jobTitle": "Data Engineer",
                "url": "https://jobicy.com/jobs/1",
                "companyName": "A",
            }],
        }
        payload_b = {
            "jobs": [{
                "id": 1,
                "jobTitle": "Data Engineer",
                "url": "https://jobicy.com/jobs/1",
                "companyName": "A",
            }, {
                "id": 2,
                "jobTitle": "ML Engineer",
                "url": "https://jobicy.com/jobs/2",
                "companyName": "B",
            }],
        }
        payloads = [payload_a] + [payload_b] * 10
        with mock.patch.object(sj, "fetch_json", side_effect=payloads), \
             mock.patch.object(sj, "polite_sleep"):
            jobs = sj.scrape()
        titles = {j["title"] for j in jobs}
        self.assertEqual(titles, {"Data Engineer", "ML Engineer"})


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
