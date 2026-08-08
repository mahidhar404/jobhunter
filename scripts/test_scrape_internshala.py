#!/usr/bin/env python3
"""Fixture tests for scrape_internshala HTML parse (no network)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_internshala as si  # noqa: E402

SAMPLE_HTML = """
<div class="container">
  <div class="individual_internship" data-href="/job/detail/data-scientist-at-acme-123">
    <h3 class="job-internship-name">Data Scientist</h3>
    <p class="company-name">Acme Analytics</p>
    <div class="locations"><a href="#">Bengaluru</a></div>
    <a class="job-title-href" href="/job/detail/data-scientist-at-acme-123">view</a>
  </div>
  <div class="individual_internship" data-href="/job/detail/ml-engineer-at-beta-456">
    <div class="job-internship-name">Machine Learning Engineer</div>
    <div class="company-name">Beta Labs</div>
    <div class="locations"><a href="#">Remote</a></div>
  </div>
  <div class="individual_internship">
    <!-- no title / no url: dropped -->
    <p class="company-name">Ghost Co</p>
  </div>
</div>
"""


class ParseTests(unittest.TestCase):
    def test_parse_html(self):
        jobs = si.parse_html(SAMPLE_HTML, search_term="data science")
        self.assertEqual(len(jobs), 2)
        a = jobs[0]
        self.assertEqual(a["title"], "Data Scientist")
        self.assertEqual(a["company"], "Acme Analytics")
        self.assertEqual(a["location"], "Bengaluru")
        self.assertEqual(a["site"], "internshala")
        self.assertEqual(
            a["job_url"],
            "https://internshala.com/job/detail/data-scientist-at-acme-123",
        )
        self.assertTrue(a["search_term"].startswith("india:internshala"))
        # Card with title but href only from data-href still resolves.
        self.assertEqual(jobs[1]["title"], "Machine Learning Engineer")
        self.assertTrue(jobs[1]["job_url"].endswith("ml-engineer-at-beta-456"))

    def test_parse_empty(self):
        self.assertEqual(si.parse_html("", search_term="x"), [])
        self.assertEqual(si.parse_html("<div></div>"), [])


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
