#!/usr/bin/env python3
"""Fixture tests for scrape_remoteok (no network)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_remoteok as sr  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_normalize_relevant_jobs(self):
        rows = [
            {"id": "meta"},  # API metadata row — skipped
            {
                "id": "1",
                "position": "Machine Learning Engineer",
                "company": "Acme",
                "url": "https://remoteOK.com/remote-jobs/remote-ml-acme-1",
                "description": "<p>Build models</p>",
                "date": "2026-08-18T08:00:07+00:00",
                "location": "Remote",
            },
            {
                "id": "2",
                "position": "Office Manager",
                "url": "https://remoteOK.com/remote-jobs/office-2",
            },
        ]
        jobs = sr.normalize_jobs(rows)
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["site"], "remoteok")
        self.assertEqual(j["company"], "Acme")
        self.assertEqual(j["date_posted"], "2026-08-18")
        self.assertIn("Build models", j["description"])
        self.assertTrue(j["search_term"].startswith("ww:remoteok"))

    def test_scrape_fixture(self):
        from datetime import date
        today_iso = date.today().isoformat()
        payload = [
            {"legal": "terms"},
            {
                "id": "9",
                "position": "Data Scientist",
                "company": "Lab",
                "url": "https://remoteOK.com/remote-jobs/data-9",
                "description": "<p>Analyze</p>",
                "date": today_iso,
            },
        ]
        with mock.patch.object(sr, "fetch_json", return_value=payload), \
             mock.patch.object(sr, "polite_sleep"):
            jobs = sr.scrape()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Data Scientist")

    def test_query_urls_include_tag_feeds(self):
        urls = sr.query_urls()
        self.assertIn(sr.API_URL, urls)
        joined = " ".join(urls)
        for tag in ("ai", "python", "data", "ml"):
            self.assertIn(f"tag={tag}", joined)

    def test_scrape_unions_tags_and_dedups(self):
        def fake_fetch(url: str):
            if url.endswith("/api") and "tag=" not in url:
                return [{"legal": "terms"}]
            if "tag=ai" in url:
                return [
                    {"legal": "terms"},
                    {
                        "id": "1",
                        "position": "Machine Learning Engineer",
                        "company": "A",
                        "url": "https://remoteOK.com/remote-jobs/1",
                    },
                ]
            if "tag=python" in url:
                return [
                    {"legal": "terms"},
                    {
                        "id": "1",
                        "position": "Machine Learning Engineer",
                        "company": "A",
                        "url": "https://remoteOK.com/remote-jobs/1",
                    },
                    {
                        "id": "2",
                        "position": "Data Engineer",
                        "company": "B",
                        "url": "https://remoteOK.com/remote-jobs/2",
                    },
                    {
                        "id": "3",
                        "position": "Office Manager",
                        "url": "https://remoteOK.com/remote-jobs/3",
                    },
                ]
            return [{"legal": "terms"}]

        with mock.patch.object(sr, "fetch_json", side_effect=fake_fetch), \
             mock.patch.object(sr, "polite_sleep"):
            jobs = sr.scrape()
        titles = {j["title"] for j in jobs}
        self.assertEqual(titles, {"Machine Learning Engineer", "Data Engineer"})


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
