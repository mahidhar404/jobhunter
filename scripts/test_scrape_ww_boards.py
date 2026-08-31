#!/usr/bin/env python3
"""Unit tests for scrape_ww_boards adapters (fixtures, no network)."""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_ww_boards as ww
import ww_scrape_common as wc


class WwScrapeTests(unittest.TestCase):
    def test_is_within_days_filtering(self):
        today = date.today().isoformat()
        self.assertTrue(wc.is_within_days(today, max_days=10))
        self.assertFalse(wc.is_within_days("2020-01-01", max_days=10))
        self.assertTrue(wc.is_within_days(None, max_days=10))

    def test_parse_rss_items_fallback(self):
        malformed_xml = """
        <rss><channel>
          <item>
            <title>Software Engineer & Developer</title>
            <link>https://example.com/job/1</link>
            <description>Unescaped <br> & mismatched tags
            <pubDate>2026-08-25</pubDate>
          </item>
        </channel></rss>
        """
        items = wc.parse_rss_items(malformed_xml)
        self.assertEqual(len(items), 1)
        self.assertIn("Software Engineer", items[0]["title"])
        self.assertEqual(items[0]["link"], "https://example.com/job/1")

    def test_scrape_yc_jobs_from_yc_board(self):
        """Primary path is YC's own board; HN Algolia is only the fallback."""
        html = """
        <div>
          <div class="card">
            SuperAI | (W25) | AI for everything | ( 3 days ago) |
            <a href="/companies/superai/jobs/abc123-machine-learning-engineer">
              Machine Learning Engineer</a>
            Full-time | Engineering | $180K - $220K
          </div>
          <div class="card">
            Acme | (S24) | Boxes | ( 1 day ago) |
            <a href="/companies/acme/jobs/def456-sales-representative">
              Sales Representative</a>
          </div>
        </div>
        """
        with mock.patch.object(ww, "fetch_text", return_value=html), \
             mock.patch.object(ww, "polite_sleep"):
            jobs = ww.scrape_yc_jobs()
        # Sales is dropped by the title relevance filter; the ML role survives
        # once per role page, deduped by the caller.
        self.assertTrue(jobs)
        self.assertTrue(all(j["site"] == "yc_jobs" for j in jobs))
        job = jobs[0]
        self.assertEqual(job["title"], "Machine Learning Engineer")
        self.assertEqual(job["company"], "SuperAI")
        self.assertEqual(
            job["job_url"],
            "https://www.ycombinator.com/companies/superai/jobs/abc123-machine-learning-engineer",
        )
        self.assertNotIn("sales", " ".join(j["title"].lower() for j in jobs))

    def test_scrape_yc_jobs_falls_back_to_hn(self):
        today_iso = date.today().isoformat()
        payload = {
            "hits": [{
                "objectID": "12345",
                "title": "SuperAI (YC W25) Is Hiring Senior Machine Learning Engineers",
                "url": "https://jobs.ashbyhq.com/superai/1",
                "created_at": f"{today_iso}T10:00:00.000Z",
                "story_text": "<p>Build foundation models</p>",
            }]
        }
        with mock.patch.object(ww, "fetch_text", return_value=None), \
             mock.patch.object(ww, "fetch_json", side_effect=[payload, None]), \
             mock.patch.object(ww, "polite_sleep"):
            jobs = ww.scrape_yc_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "SuperAI")
        self.assertEqual(jobs[0]["job_url"], "https://jobs.ashbyhq.com/superai/1")
        self.assertEqual(jobs[0]["date_posted"], today_iso)

    def test_scrape_justremote_uses_json_api(self):
        """The site is a SPA — jobs only come from its own JSON API."""
        payload = [
            {
                "title": "Senior Full Stack Software Engineer",
                "company_name": "Dave",
                "href": "remote-developer-jobs/senior-full-stack-software-engineer-dave",
                "date": "25 Aug",
                "category": "developer",
                "is_active": "True",
                "location_restrictions": "['United States']",
            },
            {
                "title": "Senior Software Engineer",
                "company_name": "Ghost",
                "href": "remote-developer-jobs/gone",
                "is_active": "False",
            },
        ]
        with mock.patch.object(ww, "fetch_json", return_value=payload), \
             mock.patch.object(ww, "fetch_text", return_value=None), \
             mock.patch.object(ww, "polite_sleep"):
            jobs = ww.scrape_justremote()
        self.assertEqual(len(jobs), 1, "inactive rows must be dropped")
        self.assertEqual(jobs[0]["company"], "Dave")
        self.assertEqual(
            jobs[0]["job_url"],
            "https://justremote.co/remote-developer-jobs/"
            "senior-full-stack-software-engineer-dave",
        )
        self.assertEqual(jobs[0]["location"], "United States")

    def test_scrape_dynamitejobs_matches_remote_job_urls(self):
        """Job links are /company/<co>/remote-job/<slug>, not /job/<slug>."""
        html = """
        <a href="/company/oura/remote-job/senior-mlops-engineer">Senior MLOps Engineer</a>
        <a href="/company/holafly/remote-job/data-engineer">Data Engineer</a>
        <a href="/category/remote-design-jobs">Design</a>
        """
        with mock.patch.object(ww, "fetch_text", return_value=html), \
             mock.patch.object(ww, "polite_sleep"):
            jobs = ww.scrape_dynamitejobs()
        urls = {j["job_url"] for j in jobs}
        self.assertIn(
            "https://dynamitejobs.com/company/oura/remote-job/senior-mlops-engineer", urls)
        self.assertTrue(all("/category/" not in u for u in urls))
        self.assertEqual({j["company"] for j in jobs}, {"Oura", "Holafly"})

    def test_scrape_relocate_me_parses_card_grid(self):
        html = """
        <div class="jobs-list">
          <div class="jobs-list__job">
            <div class="job__company">Japan</div>
            <div class="job__company">PayPay</div>
            <a href="/japan/tokyo/paypay/backend-engineer-10205">
              <div class="job__title">Backend Engineer in Tokyo</div></a>
            <div class="job__preview">PayPay is looking for a Backend Engineer</div>
          </div>
        </div>
        """
        with mock.patch.object(ww, "fetch_text", side_effect=[html, None]), \
             mock.patch.object(ww, "polite_sleep"):
            jobs = ww.scrape_relocate_me()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "PayPay")
        self.assertEqual(jobs[0]["location"], "Japan (relocation)")
        self.assertEqual(
            jobs[0]["job_url"],
            "https://relocate.me/japan/tokyo/paypay/backend-engineer-10205")

    def test_dead_boards_are_not_dispatchable(self):
        """Parked / TLS-dead origins must never be scheduled."""
        self.assertNotIn("europeremotely", ww.SCRAPERS)
        self.assertNotIn("germanstartups", ww.SCRAPERS)
        self.assertEqual(ww.scrape_europeremotely(), [])
        self.assertEqual(ww.scrape_germanstartups(), [])

    def test_scrape_himalayas_fixture(self):
        today_iso = date.today().isoformat()
        payload = {
            "jobs": [
                {
                    "title": "Backend Software Engineer",
                    "company": {"name": "Himalayas Tech"},
                    "applicationLink": "https://himalayas.app/jobs/backend",
                    "location": ["Remote"],
                    "minSalary": 100000,
                    "maxSalary": 150000,
                    "pubDate": f"{today_iso}T00:00:00Z",
                    "description": "<p>Python/FastAPI</p>",
                }
            ]
        }
        with mock.patch.object(ww, "fetch_json", side_effect=[payload, None, None, None, None, None]), \
             mock.patch.object(ww, "polite_sleep"):
            jobs = ww.scrape_himalayas()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["site"], "himalayas")
        self.assertEqual(jobs[0]["company"], "Himalayas Tech")
        self.assertEqual(jobs[0]["date_posted"], today_iso)


class WwMainWritesListingsTests(unittest.TestCase):
    """main() once wrote filter_out_known_listings' (rows, count) TUPLE to disk.

    Every worldwide board therefore produced `[[...rows...], 3]` — a file that
    parses as JSON but has 2 elements, so downstream dedup saw no listings and
    zero worldwide jobs ever reached jobs.json.
    """

    def test_main_writes_flat_row_list_after_skip_filter(self):
        import json
        import tempfile

        rows = [{
            "title": "Data Engineer", "company": "Acme", "site": "himalayas",
            "job_url": "https://example.com/job/1",
            "job_url_direct": "https://example.com/job/1",
            "description": "", "date_posted": None, "job_type": "fulltime",
            "location": "Remote", "search_term": "ww:himalayas",
        }]
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.json"
            skip = Path(td) / "skip.json"
            skip.write_text(json.dumps(["https://example.com/job/other"]))
            argv = [
                "scrape_ww_boards.py", "--site", "himalayas",
                "--out", str(out), "--skip-urls", str(skip),
            ]
            with mock.patch.dict(ww.SCRAPERS, {"himalayas": lambda: list(rows)}), \
                 mock.patch.object(sys, "argv", argv):
                ww.main()
            written = json.loads(out.read_text())
        self.assertIsInstance(written, list)
        self.assertEqual(len(written), 1)
        self.assertTrue(
            all(isinstance(r, dict) for r in written),
            f"expected flat dict rows, got {[type(r).__name__ for r in written]}")
        self.assertEqual(written[0]["job_url"], "https://example.com/job/1")

    def test_max_days_cli_overrides_default_window(self):
        import tempfile

        captured = {}

        def fake_scraper():
            captured["max_days"] = ww.max_days()
            return []

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.json"
            argv = ["scrape_ww_boards.py", "--site", "himalayas",
                    "--out", str(out), "--max-days", "45"]
            with mock.patch.dict(ww.SCRAPERS, {"himalayas": fake_scraper}), \
                 mock.patch.object(sys, "argv", argv):
                ww.main()
        self.assertEqual(captured["max_days"], 45)


if __name__ == "__main__":
    unittest.main()
