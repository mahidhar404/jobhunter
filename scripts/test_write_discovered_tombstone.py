#!/usr/bin/env python3
"""Prune matches at write time must land in Deleted, not be skipped."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import write_discovered_jobs as wdj  # noqa: E402


def _listing(**overrides):
    item = {
        "company": "Acme",
        "title": "Data Scientist",
        "location": "Remote, US",
        "site": "greenhouse",
        "job_url": "https://boards.greenhouse.io/acme/jobs/1",
        "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
        "description": "Build models. 3+ years of experience required.",
        "date_posted": "2026-08-18",
    }
    item.update(overrides)
    return item


class TombstoneOnWriteTests(unittest.TestCase):
    def setUp(self):
        self._jd_patch = mock.patch.object(wdj, "write_full_description")
        self._jd_patch.start()
        self.addCleanup(self._jd_patch.stop)
        self._extract_patch = mock.patch.object(wdj, "extract_posting", return_value={})
        self._extract_patch.start()
        self.addCleanup(self._extract_patch.stop)

    def test_citizen_only_listing_is_written_deleted(self):
        data = {"jobs": []}
        listing = _listing(
            description="US citizens only. Build production ML systems."
        )
        stats = wdj.ingest_qualified_listings(
            [listing],
            data,
            skip_companies=set(),
            blocked_urls=set(),
            blocked_ids=set(),
            to_block=[],
        )
        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["skipped_filtered"].get("citizenship_or_greencard"), 1)
        job = data["jobs"][0]
        self.assertEqual(job["status"], "deleted")
        self.assertEqual(job["deleted_reason"], "citizenship_or_greencard")
        self.assertTrue(job.get("deleted_at"))
        self.assertIn("US citizens only", job.get("job_description") or "")

    def test_does_not_mint_second_deleted_copy_for_same_url(self):
        data = {"jobs": []}
        listing = _listing(
            description="Must hold a Secret clearance for this role."
        )
        first = wdj.ingest_qualified_listings(
            [listing],
            data,
            skip_companies=set(),
            blocked_urls=set(),
            blocked_ids=set(),
            to_block=[],
        )
        second = wdj.ingest_qualified_listings(
            [listing],
            data,
            skip_companies=set(),
            blocked_urls=set(),
            blocked_ids=set(),
            to_block=[],
        )
        self.assertEqual(first["added"], 1)
        self.assertEqual(second["skipped_existing"], 1)
        self.assertEqual(len(data["jobs"]), 1)
        self.assertEqual(data["jobs"][0]["deleted_reason"], "clearance_or_intel")

    def test_anduril_us_person_ts_written_deleted_with_tags(self):
        data = {"jobs": []}
        listing = _listing(
            company="Anduril",
            title="Software Engineer - ML Infrastructure",
            location="Costa Mesa, CA, US",
            description=(
                "U.S. Person status is required as this position needs to access "
                "export controlled data. Eligibility to obtain/maintain a US Top Secret "
                "clearance is also desirable."
            ),
        )
        stats = wdj.ingest_qualified_listings(
            [listing],
            data,
            skip_companies=set(),
            blocked_urls=set(),
            blocked_ids=set(),
            to_block=[],
        )
        self.assertEqual(stats["added"], 1)
        job = data["jobs"][0]
        self.assertEqual(job["status"], "deleted")
        self.assertIn(
            job["deleted_reason"],
            ("clearance_or_intel", "citizenship_or_greencard"),
        )
        self.assertTrue(job.get("clearance"))
        self.assertTrue(job.get("us_person"))

    def test_keep_listing_still_discovered(self):
        data = {"jobs": []}
        listing = _listing()
        stats = wdj.ingest_qualified_listings(
            [listing],
            data,
            skip_companies=set(),
            blocked_urls=set(),
            blocked_ids=set(),
            to_block=[],
        )
        self.assertEqual(stats["added"], 1)
        self.assertEqual(data["jobs"][0]["status"], "discovered")
        self.assertNotIn("deleted_reason", data["jobs"][0])

    def test_queues_url_block_for_prune_tombstone(self):
        data = {"jobs": []}
        to_block: list = []
        listing = _listing(title="Engineering Manager")
        wdj.ingest_qualified_listings(
            [listing],
            data,
            skip_companies=set(),
            blocked_urls=set(),
            blocked_ids=set(),
            to_block=to_block,
        )
        self.assertEqual(data["jobs"][0]["deleted_reason"], "management_track")
        self.assertEqual(len(to_block), 1)
        self.assertEqual(to_block[0]["id"], data["jobs"][0]["id"])


class StaffingAutoDeleteTests(unittest.TestCase):
    def test_staffing_company_tombstones(self):
        from discovery_filters import auto_delete_reason

        self.assertEqual(
            auto_delete_reason(
                title="Data Scientist",
                location="Remote, US",
                company="Insight Global",
                description="Contract data science role.",
            ),
            "staffing",
        )


if __name__ == "__main__":
    unittest.main()
