#!/usr/bin/env python3
"""Unit + optional live probes for scrape_ats new platforms.

Default: fixture/mocked HTTP only (no network).
  python3 scripts/test_scrape_ats_platforms.py

Optional live (one public board fetch per new platform):
  python3 scripts/test_scrape_ats_platforms.py --live
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_ats as sa  # noqa: E402


class SlugPatternTests(unittest.TestCase):
    def test_workable_skips_job_shortlinks(self):
        pat = sa.SLUG_PATTERNS["workable"]
        self.assertIsNone(pat.search("https://apply.workable.com/j/5FDA6B7FB2"))
        m = pat.search("https://apply.workable.com/tiger-analytics/j/39D0D71D6D")
        self.assertEqual(m.group(1), "tiger-analytics")

    def test_extract_new_platform_slugs(self):
        listings = [
            {"apply_url": "https://jobs.smartrecruiters.com/AbbVie/123-role"},
            {"job_url": "https://apply.workable.com/node/j/B897D2F1F1"},
            {"apply_url": "https://ats.rippling.com/tensorlake/jobs/abc"},
            {"apply_url": "https://cruisebound.breezy.hr/p/40a2b0b63666-data-scientist"},
            {"apply_url": "https://peerislands.bamboohr.com/careers/51"},
            {"apply_url": "https://spokeo.na.teamtailor.com/jobs/609343-senior-data-engineer"},
            {"apply_url": "https://emedlabsllc.applytojob.com/apply/FI24qAupbj/Analytics-Engineer"},
            {"job_url": "https://cardfactory.pinpointhq.com/en/postings/f1084665-head-of-dei"},
        ]
        path = Path(self.id().replace(".", "_") + "_listings.json")
        # Write under /tmp via NamedTemporaryFile pattern using workspace tmp
        tmp = ROOT / "listings" / "_test_scrape_ats_seed.json"
        tmp.write_text(json.dumps(listings))
        try:
            reg = {ats: [] for ats in sa.SLUG_PATTERNS}
            reg["tried_and_failed"] = {ats: [] for ats in sa.SLUG_PATTERNS}
            added = sa.extract_slugs([tmp], reg)
            self.assertGreaterEqual(added, 5)
            self.assertIn("AbbVie", reg["smartrecruiters"])
            self.assertIn("node", reg["workable"])
            self.assertIn("tensorlake", reg["rippling"])
            self.assertIn("cruisebound", reg["breezy"])
            self.assertIn("peerislands", reg["bamboohr"])
            self.assertIn("spokeo.na.teamtailor.com", reg["teamtailor"])
            self.assertIn("emedlabsllc", reg["jazzhr"])
            self.assertIn("cardfactory", reg["pinpoint"])
        finally:
            if tmp.exists():
                tmp.unlink()


class FixtureScrapeTests(unittest.TestCase):
    def test_smartrecruiters_fixture(self):
        listing = {
            "offset": 0,
            "limit": 100,
            "totalFound": 1,
            "content": [{
                "id": "sr1",
                "name": "Senior Machine Learning Engineer",
                "visibility": "PUBLIC",
                "location": {"city": "Chicago", "region": "IL", "country": "United States"},
                "releasedDate": "2026-07-01T00:00:00.000Z",
            }],
        }
        detail = {
            "id": "sr1",
            "name": "Senior Machine Learning Engineer",
            "company": {"name": "AbbVie"},
            "postingUrl": "https://jobs.smartrecruiters.com/AbbVie/sr1",
            "jobAd": {
                "sections": {
                    "jobDescription": {
                        "title": "Job Description",
                        "text": "<p>Build ML models at scale</p>",
                    },
                    "qualifications": {
                        "title": "Qualifications",
                        "text": "<p>5+ years experience</p>",
                    },
                }
            },
        }

        def fake_fetch(url, *args, **kwargs):
            if url.rstrip("/").endswith("/postings") or "offset=" in url:
                return listing
            if url.endswith("/postings/sr1"):
                return detail
            return None

        with mock.patch.object(sa, "fetch_json", side_effect=fake_fetch):
            jobs = sa.scrape_smartrecruiters("AbbVie")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["site"], "smartrecruiters")
        self.assertIn("smartrecruiters.com/AbbVie/sr1", jobs[0]["job_url"])
        self.assertIn("Build ML models", jobs[0]["description"])
        self.assertIn("5+ years", jobs[0]["description"])

    def test_workable_fixture(self):
        widget = {
            "name": "Node.Digital",
            "jobs": [{
                "title": "AI/ML Engineer",
                "shortcode": "B897D2F1F1",
                "city": "Arlington",
                "state": "VA",
                "country": "United States",
                "application_url": "https://apply.workable.com/j/B897D2F1F1",
                "published_on": "2026-07-15",
            }],
        }
        detail = {
            "description": "<p>Build models for production.</p>",
            "requirements": "<p>Requirements</p><p>Five years of Python and SQL.</p>",
            "benefits": "<p>Health insurance.</p>",
        }

        def fake_fetch(url, *args, **kwargs):
            if "widget" in url:
                return widget
            if "B897D2F1F1" in url:
                return detail
            return None

        with mock.patch.object(sa, "fetch_json", side_effect=fake_fetch):
            jobs = sa.scrape_workable("node")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Node.Digital")
        self.assertIn("Build models", jobs[0]["description"])
        self.assertIn("Requirements", jobs[0]["description"])
        self.assertIn("Five years of Python", jobs[0]["description"])
        self.assertIn("Requirements", jobs[0]["description"])
        self.assertIn("Five years of Python", jobs[0]["description"])

    def test_workable_skips_detail_fetch_for_known_url(self):
        widget = {
            "name": "Node.Digital",
            "jobs": [{
                "title": "AI/ML Engineer",
                "shortcode": "B897D2F1F1",
                "city": "Arlington",
                "state": "VA",
                "country": "United States",
                "application_url": "https://apply.workable.com/j/B897D2F1F1",
                "published_on": "2026-07-15",
            }],
        }
        from blocked_urls import block_keys_for_url
        calls: list[str] = []

        def fake_fetch(url, *args, **kwargs):
            calls.append(url)
            if "widget" in url:
                return widget
            raise AssertionError(f"unexpected detail fetch: {url}")

        sa._SKIP_URL_KEYS = set(block_keys_for_url(
            "https://apply.workable.com/j/B897D2F1F1"
        ))
        sa._SKIPPED_KNOWN = 0
        try:
            with mock.patch.object(sa, "fetch_json", side_effect=fake_fetch):
                jobs = sa.scrape_workable("node")
            self.assertEqual(jobs, [])
            self.assertEqual(sa._SKIPPED_KNOWN, 1)
            self.assertTrue(all("widget" in u for u in calls))
            self.assertFalse(any("/api/v2/" in u for u in calls))
        finally:
            sa._SKIP_URL_KEYS = set()
            sa._SKIPPED_KNOWN = 0

    def test_smartrecruiters_skips_detail_fetch_for_known_url(self):
        listing = {
            "offset": 0,
            "limit": 100,
            "totalFound": 1,
            "content": [{
                "id": "sr1",
                "name": "Senior Machine Learning Engineer",
                "visibility": "PUBLIC",
                "location": {"city": "Chicago", "region": "IL", "country": "United States"},
                "releasedDate": "2026-07-01T00:00:00.000Z",
                "postingUrl": "https://jobs.smartrecruiters.com/AbbVie/sr1",
            }],
        }
        from blocked_urls import block_keys_for_url
        calls: list[str] = []

        def fake_fetch(url, *args, **kwargs):
            calls.append(url)
            if "offset=" in url or url.rstrip("/").endswith("/postings"):
                return listing
            raise AssertionError(f"unexpected detail fetch: {url}")

        sa._SKIP_URL_KEYS = set(block_keys_for_url(
            "https://jobs.smartrecruiters.com/AbbVie/sr1"
        ))
        sa._SKIPPED_KNOWN = 0
        try:
            with mock.patch.object(sa, "fetch_json", side_effect=fake_fetch):
                jobs = sa.scrape_smartrecruiters("AbbVie")
            self.assertEqual(jobs, [])
            self.assertEqual(sa._SKIPPED_KNOWN, 1)
            self.assertFalse(any(u.endswith("/postings/sr1") for u in calls))
        finally:
            sa._SKIP_URL_KEYS = set()
            sa._SKIPPED_KNOWN = 0

    def test_rippling_fixture(self):
        payload = [{
            "uuid": "u1",
            "name": "Data Engineer",
            "url": "https://ats.rippling.com/tensorlake/jobs/u1",
            "workLocation": {"label": "San Francisco, CA"},
        }]
        detail = {
            "uuid": "u1",
            "name": "Data Engineer",
            "companyName": "Tensorlake",
            "url": "https://ats.rippling.com/tensorlake/jobs/u1",
            "description": {
                "company": "<p>About Tensorlake</p>",
                "role": "<p>Build data pipelines</p>",
            },
        }

        def fake_fetch(url, *args, **kwargs):
            if url.endswith("/jobs"):
                return payload
            if url.endswith("/jobs/u1"):
                return detail
            return None

        with mock.patch.object(sa, "fetch_json", side_effect=fake_fetch):
            jobs = sa.scrape_rippling("tensorlake")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["site"], "rippling")
        self.assertIn("Build data pipelines", jobs[0]["description"])

    def test_breezy_fixture(self):
        payload = [{
            "id": "x1",
            "friendly_id": "x1-data-scientist",
            "name": "Data Scientist",
            "url": "https://cruisebound.breezy.hr/p/x1-data-scientist",
            "published_date": "2026-07-20",
            "location": {"name": "East Coast, US"},
            "company": {"name": "Cruisebound"},
        }]
        html = '''
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Data Scientist",
         "description":"<p>Analyze cruise booking data</p>",
         "hiringOrganization":{"name":"Cruisebound"}}
        </script>
        '''
        with mock.patch.object(sa, "fetch_json", return_value=payload), \
             mock.patch.object(sa, "fetch_html", return_value=html):
            jobs = sa.scrape_breezy("cruisebound")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Cruisebound")
        self.assertIn("Analyze cruise booking data", jobs[0]["description"])

    def test_lever_compose_when_description_plain_empty(self):
        payload = [{
            "id": "abc",
            "text": "Machine Learning Engineer",
            "hostedUrl": "https://jobs.lever.co/zoox/abc",
            "applyUrl": "https://jobs.lever.co/zoox/abc/apply",
            "description": "",
            "descriptionPlain": "",
            "categories": {"location": "Foster City"},
            "lists": [{
                "text": "Responsibilities",
                "content": "<li>Build sensor sims</li>",
            }],
            "additionalPlain": "Benefits include equity.",
            "additional": "",
            "openingPlain": "",
        }]
        with mock.patch.object(sa, "fetch_json", return_value=payload):
            jobs = sa.scrape_lever("zoox")
        self.assertEqual(len(jobs), 1)
        self.assertIn("Build sensor sims", jobs[0]["description"])
        self.assertIn("Benefits include equity", jobs[0]["description"])

    def test_lever_compose_includes_lists_when_plain_present(self):
        """descriptionPlain is the intro; lists hold requirements (nextgenfed)."""
        payload = [{
            "id": "7387d176-8443-4da8-a98f-b4192ae966fe",
            "text": "Senior Statistician",
            "hostedUrl": "https://jobs.lever.co/nextgenfed/7387d176",
            "applyUrl": "https://jobs.lever.co/nextgenfed/7387d176/apply",
            "description": "<div>NextGen Federal Systems seeks a Senior Statistician at Scott AFB.</div>",
            "descriptionPlain": (
                "NextGen Federal Systems, LLC (NextGen) is seeking a Senior "
                "Statistician to work on our Operational Analysis Support "
                "contract in the 618th Air Operations Center at Scott AFB."
            ),
            "categories": {"location": "Scott AFB IL"},
            "lists": [
                {
                    "text": "Position Requirements",
                    "content": (
                        "<li>Extensive experience in statistical analysis</li>"
                        "<li>Experience in the development of predictive analysis tools</li>"
                    ),
                },
                {
                    "text": "Desired Skills",
                    "content": "<li>Advanced skills in Excel, JMP, MATLAB, R, SAS</li>",
                },
            ],
            "additionalPlain": "NextGen is an Equal Opportunity Employer.",
            "additional": "",
            "openingPlain": "",
        }]
        with mock.patch.object(sa, "fetch_json", return_value=payload):
            jobs = sa.scrape_lever("nextgenfed")
        self.assertEqual(len(jobs), 1)
        desc = jobs[0]["description"]
        self.assertIn("Senior Statistician", desc)
        self.assertIn("Position Requirements", desc)
        self.assertIn("predictive analysis tools", desc)
        self.assertIn("Desired Skills", desc)
        self.assertIn("Equal Opportunity Employer", desc)

    def test_bamboohr_fixture(self):
        listing = {
            "meta": {"totalCount": 1},
            "result": [{
                "id": "15",
                "jobOpeningName": "Data Analyst",
                "location": {"city": "Austin", "state": "TX"},
            }],
        }
        detail = {
            "result": {
                "jobOpening": {
                    "jobOpeningShareUrl": "https://acme.bamboohr.com/careers/15",
                    "description": "<p>Analyze data</p>",
                    "datePosted": "2026-06-01",
                }
            }
        }

        def fake_fetch(url, *args, **kwargs):
            if url.endswith("/list"):
                return listing
            if url.endswith("/detail"):
                return detail
            return None

        with mock.patch.object(sa, "fetch_json", side_effect=fake_fetch):
            jobs = sa.scrape_bamboohr("acme")
        self.assertEqual(len(jobs), 1)
        self.assertIn("Analyze data", jobs[0]["description"])

    def test_teamtailor_fixture(self):
        feed = {
            "title": "Spokeo",
            "items": [{
                "id": "j1",
                "title": "Senior Data Engineer",
                "url": "https://spokeo.na.teamtailor.com/jobs/609343-senior-data-engineer",
                "date_published": "2026-08-04T09:43:48-07:00",
                "content_html": "<p>Build data pipelines</p>",
            }],
        }
        with mock.patch.object(sa, "fetch_json", return_value=feed):
            jobs = sa.scrape_teamtailor("spokeo.na.teamtailor.com")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["site"], "teamtailor")
        self.assertEqual(jobs[0]["company"], "Spokeo")
        self.assertIn("Build data pipelines", jobs[0]["description"])

    def test_jazzhr_fixture(self):
        board_html = '''
        <html><head>
        <meta property="og:description" content="Explore open job opportunities at Bright Vision." />
        </head><body>
        <a href="https://acme.applytojob.com/apply/pIuCcNu1fu/AI-Data-Engineer">
            AI Data Engineer
        </a>
        </body></html>
        '''
        job_html = '''
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"AI Data Engineer","datePosted":"2026-07-01",
         "description":"<p>Build pipelines</p>"}
        </script>
        '''
        with mock.patch.object(sa, "fetch_html", side_effect=[board_html, job_html]):
            jobs = sa.scrape_jazzhr("acme")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["site"], "jazzhr")
        self.assertIn("Build pipelines", jobs[0]["description"])

    def test_jazzhr_html_description_when_no_jobposting_ldjson(self):
        """Zealogics-style boards: Organization ld+json only; JD is #job-description."""
        board_html = '''
        <html><head>
        <meta property="og:description" content="Explore open job opportunities at Zealogics." />
        </head><body>
        <a href="https://acme.applytojob.com/apply/AgViDHIEyQ/Senior-Data-Engineer">
            Senior Data Engineer
        </a>
        </body></html>
        '''
        job_html = '''
        <script type="application/ld+json">
        {"@type":"Organization","name":"Zealogics.com"}
        </script>
        <div class="col description" id="job-description">
            <b>Position Overview</b><br>Architect data models across SAP Core and PostgreSQL.
        </div>
        '''
        with mock.patch.object(sa, "fetch_html", side_effect=[board_html, job_html]):
            jobs = sa.scrape_jazzhr("acme")
        self.assertEqual(len(jobs), 1)
        self.assertIn("Architect data models", jobs[0]["description"])
        self.assertNotIn("No job description", jobs[0]["description"])

    def test_jazzhr_prefers_html_body_over_short_jobposting(self):
        board_html = '''
        <html><body>
        <a href="https://acme.applytojob.com/apply/AgViDHIEyQ/Senior-Data-Engineer">
            Senior Data Engineer
        </a>
        </body></html>
        '''
        job_html = '''
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Senior Data Engineer",
         "description":"<p>Acme is hiring in Austin.</p>"}
        </script>
        <div id="job-description">
            <p>Acme is hiring in Austin.</p>
            <h2>Responsibilities</h2>
            <p>Architect data models across SAP Core and PostgreSQL.</p>
            <h2>Requirements</h2>
            <p>''' + ("Experience with ETL pipelines and warehouse design. " * 20) + '''</p>
        </div>
        '''
        with mock.patch.object(sa, "fetch_html", side_effect=[board_html, job_html]):
            jobs = sa.scrape_jazzhr("acme")
        self.assertEqual(len(jobs), 1)
        self.assertIn("Responsibilities", jobs[0]["description"])
        self.assertIn("Architect data models", jobs[0]["description"])

    def test_pinpoint_fixture(self):
        payload = {
            "data": [{
                "title": "Senior Data Engineer",
                "url": "https://cardfactory.pinpointhq.com/en/postings/abc",
                "description": "<p>Build data platforms</p>",
                "employment_type": "full_time",
                "location": {"name": "Remote"},
                "deadline_at": "2026-08-01T00:00:00Z",
            }],
        }
        with mock.patch.object(sa, "fetch_json", return_value=payload):
            jobs = sa.scrape_pinpoint("cardfactory")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["site"], "pinpoint")
        self.assertIn("Build data platforms", jobs[0]["description"])

    def test_probe_smartrecruiters_rejects_empty_unknown(self):
        with mock.patch.object(
            sa, "fetch_json",
            return_value={"offset": 0, "limit": 1, "totalFound": 0, "content": []},
        ):
            self.assertFalse(sa.probe_slug("smartrecruiters", "fake-company"))


class LiveProbeTests(unittest.TestCase):
    """Opt-in live HTTP — one public board per new platform."""

    LIVE_CASES = [
        ("smartrecruiters", "AbbVie", "scrape_smartrecruiters"),
        ("workable", "node", "scrape_workable"),
        ("rippling", "tensorlake", "scrape_rippling"),
        ("breezy", "cruisebound", "scrape_breezy"),
        ("bamboohr", "peerislands", "scrape_bamboohr"),
    ]

    def test_live_boards(self):
        if not getattr(self, "run_live", False):
            self.skipTest("pass --live to hit public boards")
        for ats, slug, fn_name in self.LIVE_CASES:
            with self.subTest(ats=ats, slug=slug):
                self.assertTrue(sa.probe_slug(ats, slug), f"probe failed for {ats}/{slug}")
                jobs = getattr(sa, fn_name)(slug)
                # Boards may have zero *relevant* titles right now; probe already
                # confirmed the endpoint works. Prefer seeing at least a list.
                self.assertIsInstance(jobs, list)


class OrchestrationTests(unittest.TestCase):
    def test_fetch_known_before_guess(self):
        self.assertEqual(
            sa.SCRAPE_PHASE_ORDER,
            ("extract", "fetch_known", "guess", "fetch_guessed"),
        )
        src = Path(sa.__file__).read_text()
        fetch_i = src.index("fetch_board_listings(tasks")
        guess_i = src.index("guessed, probed = guess_new_slugs(")
        self.assertLess(fetch_i, guess_i)

    def test_guess_deadline_stops_probes(self):
        tmp = ROOT / "listings" / "_test_guess_deadline.json"
        tmp.write_text(json.dumps([{"company": "DefinitelyNotARealAtsCoXYZ"}]))
        try:
            reg = {ats: [] for ats in sa.SLUG_PATTERNS}
            reg["tried_and_failed"] = {ats: [] for ats in sa.SLUG_PATTERNS}
            added, probed = sa.guess_new_slugs(
                [tmp], reg, 300, deadline_monotonic=time.monotonic() - 1,
            )
            self.assertEqual(probed, 0)
            self.assertEqual(added, 0)
        finally:
            if tmp.exists():
                tmp.unlink()


if __name__ == "__main__":
    live = "--live" in sys.argv
    if live:
        sys.argv = [a for a in sys.argv if a != "--live"]
    LiveProbeTests.run_live = live
    assert "smartrecruiters" in sa.SCRAPERS
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
