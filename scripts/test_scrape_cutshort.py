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
        from datetime import date
        today_iso = date.today().isoformat()
        data = {
            "jobs": [
                {
                    "title": "Full Stack Engineer",
                    "company": {"name": "Startup X"},
                    "location": "Bengaluru",
                    "summary": "Own the product.",
                    "ctc": "20 LPA",
                    "slug": "full-stack-engineer-startup-x",
                    "created_at": today_iso,
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
        self.assertEqual(a["date_posted"], today_iso)
        b = jobs[1]
        self.assertEqual(b["company"], "Startup Y")
        self.assertEqual(b["location"], "Remote, India")
        self.assertEqual(b["job_url"], "https://cutshort.io/job/abc123")

    def test_normalize_bad_input(self):
        self.assertEqual(sc.normalize_jobs(None), [])
        self.assertEqual(sc.normalize_jobs({}), [])
        self.assertEqual(sc.normalize_jobs({"jobs": [{"company": "X"}]}), [])

    def test_parse_html_job_links(self):
        html = """
        <a href="https://cutshort.io/job/Sr-Backend-Egnyte-koBBDTQ1">sr backend</a>
        <a href="/job/Data-Scientist-Acme-abcdef12">Apply now</a>
        <a href="/jobs?page=2">2</a>
        """
        rows = sc.parse_html(html, search_term="backend")
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["job_url"].startswith("https://cutshort.io/job/"))
        self.assertEqual(rows[0]["site"], "cutshort")


class SitemapAndCategoryTests(unittest.TestCase):
    """The keyword paths built from SEARCH_TERMS (/jobs/machine-learning) render
    an empty shell, so scrape_html used to break after the bare /jobs page and
    yield ~9 rows. Real category slugs end in `-jobs`, and the public jobs
    sitemap carries the whole board."""

    def test_category_paths_use_real_slugs(self):
        import re

        self.assertIn("/jobs", sc.CATEGORY_PATHS)
        extras = [p for p in sc.CATEGORY_PATHS if p != "/jobs"]
        self.assertTrue(extras)
        # Real slugs always carry the "-jobs" token (backend-developer-jobs,
        # startup-jobs-in-pune). The old generated ones never did.
        for path in extras:
            self.assertIn("-jobs", path, f"{path} is not a real category slug")
        generated = {
            f"/jobs/{re.sub(r'[^a-z0-9]+', '-', t.lower()).strip('-')}"
            for t in sc.SEARCH_TERMS
        }
        self.assertFalse(
            generated & set(sc.CATEGORY_PATHS),
            "SEARCH_TERMS-derived paths render an empty shell on Cutshort")

    def test_scrape_job_sitemap_parses_locs(self):
        from unittest import mock
        import io

        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url>
            <loc>https://cutshort.io/job/Senior-Data-Engineer-Sprinto-Rqw1mekJ</loc>
            <lastmod>2026-08-20T10:00:00+00:00</lastmod>
          </url>
          <url><loc>https://cutshort.io/companies/acme</loc></url>
        </urlset>"""

        class FakeResp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=FakeResp(xml)):
            rows = sc.scrape_job_sitemap(max_urls=10)
        self.assertEqual(len(rows), 1, "non-/job/ locs must be skipped")
        row = rows[0]
        self.assertEqual(row["site"], "cutshort")
        self.assertEqual(row["date_posted"], "2026-08-20")
        self.assertEqual(
            row["job_url"],
            "https://cutshort.io/job/Senior-Data-Engineer-Sprinto-Rqw1mekJ")
        # The opaque trailing id is not part of the title.
        self.assertNotIn("Rqw1mekJ", row["title"])
        self.assertIn("Senior Data Engineer", row["title"])

    def test_title_from_slug_separates_company_from_role(self):
        """"Sprinto" is the employer, not part of the role name.

        It used to be left inside the title with an empty company, so the row
        was dropped by dedup's no_company filter — 974 scraped, 0 jobs.
        """
        title, company = sc._title_from_slug("Senior-Full-stack-Engineer-Sprinto")
        self.assertEqual(company, "Sprinto")
        self.assertEqual(title, "Senior Full stack Engineer")
        self.assertNotIn("Sprinto", title)

    def test_title_from_slug_drops_the_opaque_id(self):
        title, company = sc._title_from_slug("Sr-Backend-Engineer-Egnyte-koBBDTQ1")
        self.assertEqual(company, "Egnyte")
        self.assertNotIn("koBBDTQ1", title)

    def test_role_only_slug_yields_no_company(self):
        """Never invent an employer — a wrong one misdirects a tailored resume."""
        title, company = sc._title_from_slug("Software-Engineer-XyZ123ab")
        self.assertEqual(company, "")
        self.assertEqual(title, "Software Engineer")


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
