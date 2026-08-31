#!/usr/bin/env python3
"""Fixture tests for scrape_shine HTML parse (no network)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_shine as ss  # noqa: E402


SAMPLE = """
<html><body>
<a href="https://www.shine.com/jobs/data-scientist/acme-labs/19485342">Data scientist</a>
<a href="/jobs/ml-engineer/beta-corp/111">ML Engineer</a>
<a href="https://www.shine.com/job-search/data-scientist-jobs">Search</a>
</body></html>
"""


class ParseTests(unittest.TestCase):
    def test_parse_structured_job_anchors(self):
        rows = ss.parse_html(SAMPLE, search_term="data scientist")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["site"], "shine")
        self.assertIn("shine.com/jobs/", rows[0]["job_url"])
        self.assertTrue(rows[0]["search_term"].startswith("india:shine"))
        self.assertEqual(rows[0]["company"], "acme labs")
        self.assertEqual(rows[1]["company"], "beta corp")

    def test_challenge_page_yields_empty(self):
        self.assertEqual(ss.parse_html("<html>captcha required</html>"), [])


if __name__ == "__main__":
    unittest.main()
