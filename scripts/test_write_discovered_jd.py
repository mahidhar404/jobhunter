#!/usr/bin/env python3
"""write_discovered_jobs must not persist empty JD when the apply URL can be fetched."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import write_discovered_jobs as wdj  # noqa: E402


class ResolveListingDescriptionTests(unittest.TestCase):
    def test_keeps_nonempty_listing_description(self):
        item = {
            "description": (
                "Responsibilities\nBuild pipelines with Spark.\n"
                "Requirements\nFive years of data engineering.\n"
                + ("More detail about the role, stack, and team. " * 40)
            ),
            "job_url": "https://acme.applytojob.com/apply/abc/Role",
        }
        with mock.patch.object(wdj, "extract_posting") as extract:
            text = wdj.resolve_listing_description(item)
        self.assertIn("Spark", text)
        extract.assert_not_called()

    def test_refetches_truncated_intro_only_listing(self):
        item = {
            "description": (
                "NextGen Federal Systems, LLC (NextGen) is seeking a Senior "
                "Statistician to work on our Operational Analysis Support "
                "contract in the 618th Air Operations Center at Scott AFB. "
                "Primary Work Location: Scott AFB IL"
            ),
            "job_url": "https://jobs.lever.co/nextgenfed/7387d176/apply",
            "apply_url": "https://jobs.lever.co/nextgenfed/7387d176/apply",
        }
        full = (
            item["description"]
            + "\n\nPosition Requirements\nExtensive experience in statistical analysis.\n"
            + ("Predictive analysis tools and reports. " * 40)
        )
        with mock.patch.object(
            wdj, "extract_posting", return_value={"description": full}
        ) as extract:
            text = wdj.resolve_listing_description(item)
        self.assertIn("Position Requirements", text)
        self.assertGreater(extract.call_count, 0)

    def test_looks_truncated_intro_without_requirements(self):
        intro = (
            "NextGen Federal Systems, LLC (NextGen) is seeking a Senior "
            "Statistician to work on our Operational Analysis Support "
            "contract in the 618th Air Operations Center at Scott AFB. "
            "Successful candidates will employ modern statistical tools."
        )
        self.assertTrue(wdj.looks_truncated_jd(intro))
        full = intro + "\n\nPosition Requirements\n" + ("Experience with SAS and R. " * 80)
        self.assertFalse(wdj.looks_truncated_jd(full))

    def test_looks_truncated_ignores_inline_mission_requirements(self):
        para = (
            "NextGen is seeking a highly motivated Application/Agentic AI Engineer "
            "to support integration and deployment of agentic AI capabilities using "
            "ReadiChat. Collaborate with operational users, engineers, and program "
            "stakeholders to translate mission requirements into scalable AI-enabled "
            "solutions that improve decision-making, automation, and user interaction."
        )
        intro = (para + " ") * 4  # ~1100 chars, complete sentence, no list heading
        self.assertGreater(len(intro), 900)
        self.assertTrue(wdj.looks_truncated_jd(intro))

    def test_enriches_empty_description_from_apply_url(self):
        item = {
            "description": "",
            "job_url": "https://acme.applytojob.com/apply/abc/Role",
            "apply_url": "https://acme.applytojob.com/apply/abc/Role",
        }
        with mock.patch.object(
            wdj,
            "extract_posting",
            return_value={"description": "Architect data models across SAP."},
        ) as extract:
            text = wdj.resolve_listing_description(item)
        self.assertIn("Architect data models", text)
        self.assertGreaterEqual(extract.call_count, 1)
        kwargs = extract.call_args_list[0]
        self.assertFalse(kwargs.kwargs.get("allow_playwright", True))

    def test_skips_workday_without_fetch(self):
        item = {
            "description": "",
            "job_url": "https://company.myworkdayjobs.com/en-US/job/1",
        }
        with mock.patch.object(wdj, "extract_posting") as extract:
            text = wdj.resolve_listing_description(item)
        self.assertEqual(text, "")
        extract.assert_not_called()


class FoldEmptyJdTests(unittest.TestCase):
    def test_fold_fills_empty_winner_jd(self):
        existing = {
            "id": "acme-role",
            "company": "Acme",
            "title": "Data Engineer",
            "job_description": "",
            "apply_url": "https://acme.applytojob.com/apply/abc/Role",
        }
        item = {
            "description": (
                "Responsibilities\nBuild pipelines with Spark.\nRequirements\n"
                + ("Warehouse modeling and ETL experience. " * 50)
            ),
            "job_url": "https://acme.applytojob.com/apply/abc/Role",
        }
        with mock.patch.object(wdj, "write_full_description"), mock.patch.object(
            wdj, "extract_posting"
        ) as extract:
            wdj.fold_discovered_urls(existing, item)
        self.assertIn("Spark", existing.get("job_description") or "")
        extract.assert_not_called()


class PostedDateAndDescriptionHardeningTests(unittest.TestCase):
    def setUp(self):
        self._jd_patch = mock.patch.object(wdj, "write_full_description")
        self._jd_patch.start()
        self.addCleanup(self._jd_patch.stop)
        self._extract_patch = mock.patch.object(wdj, "extract_posting", return_value={})
        self._extract_patch.start()
        self.addCleanup(self._extract_patch.stop)

    def test_nan_date_posted_not_invented_as_now(self):
        data = {"jobs": []}
        listing = {
            "company": "Acme",
            "title": "Data Scientist",
            "location": "Remote, US",
            "site": "linkedin",
            "job_url": "https://www.linkedin.com/jobs/view/1",
            "apply_url": "https://www.linkedin.com/jobs/view/1",
            "description": (
                "Responsibilities\nBuild models.\nRequirements\n"
                + ("Three years of ML experience. " * 40)
            ),
            "date_posted": "nan",
            "job_type": "fulltime",
        }
        wdj.ingest_qualified_listings(
            [listing],
            data,
            skip_companies=set(),
            blocked_urls=set(),
            blocked_ids=set(),
            to_block=[],
        )
        job = data["jobs"][0]
        self.assertIsNone(job.get("date_posted"))
        self.assertIsNone(job.get("date_posted_fallback"))
        self.assertTrue(job.get("created_at"))
        self.assertIn("unknown date", job.get("status_detail") or "")

    def test_nat_and_none_strings_cleaned(self):
        self.assertIsNone(wdj._clean_posted_value("NaT"))
        self.assertIsNone(wdj._clean_posted_value("None"))
        self.assertIsNone(wdj._clean_posted_value(float("nan")))
        self.assertEqual(wdj._clean_posted_value("2026-08-19"), "2026-08-19")

    def test_float_nan_description_does_not_crash(self):
        item = {
            "description": float("nan"),
            "job_url": "https://acme.applytojob.com/apply/abc/Role",
            "apply_url": "https://acme.applytojob.com/apply/abc/Role",
        }
        with mock.patch.object(
            wdj,
            "extract_posting",
            return_value={"description": "Responsibilities\nShip models.\n" + ("x " * 200)},
        ):
            text = wdj.resolve_listing_description(item)
        self.assertIn("Ship models", text)

    def test_contract_job_type_tombstoned(self):
        data = {"jobs": []}
        listing = {
            "company": "Acme",
            "title": "Data Scientist",
            "location": "Remote, US",
            "site": "linkedin",
            "job_url": "https://www.linkedin.com/jobs/view/contract-1",
            "apply_url": "https://www.linkedin.com/jobs/view/contract-1",
            "description": "Build models. 3+ years of experience required.",
            "date_posted": "2026-08-18",
            "job_type": "contract",
        }
        stats = wdj.ingest_qualified_listings(
            [listing],
            data,
            skip_companies=set(),
            blocked_urls=set(),
            blocked_ids=set(),
            to_block=[],
        )
        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["skipped_filtered"].get("contract"), 1)
        self.assertEqual(data["jobs"][0]["status"], "deleted")
        self.assertEqual(data["jobs"][0]["deleted_reason"], "contract")


if __name__ == "__main__":
    unittest.main()
