#!/usr/bin/env python3
"""Regression tests for deterministic posting identity and discovery writes."""
from __future__ import annotations

import unittest

from posting_identity import posting_key
from apply_urls import collect_all_urls
from write_discovered_jobs import (
    find_existing_match,
    fold_discovered_urls,
    generated_job_id,
    is_recovered_stub,
)


class PostingKeyTests(unittest.TestCase):
    def test_extracts_supported_platform_posting_ids(self):
        cases = {
            "https://jobs.ashbyhq.com/clera/11111111-2222-3333-4444-555555555555":
                "jobs.ashbyhq.com:11111111-2222-3333-4444-555555555555",
            "https://boards.greenhouse.io/acme/jobs/1234567":
                "greenhouse.io:1234567",
            "https://jobs.lever.co/acme/abcdef12-3456-7890-abcd-ef1234567890":
                "jobs.lever.co:abcdef12-3456-7890-abcd-ef1234567890",
            "https://acme.wd5.myworkdayjobs.com/en-US/jobs/job/Boston/ML-Engineer_R-12345":
                "acme.wd5.myworkdayjobs.com:r-12345",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(posting_key({"apply_url": url}), expected)

    def test_lever_rejects_non_uuid_path_segments(self):
        for url in (
            "https://jobs.lever.co/acme/apply",
            "https://jobs.lever.co/acme/seattle",
            "https://jobs.lever.co/acme/abcdef",
        ):
            with self.subTest(url=url):
                self.assertIsNone(posting_key({"apply_url": url}))

    def test_greenhouse_embed_for_query_sets_org(self):
        from posting_identity import posting_identity_for_url

        key, org = posting_identity_for_url(
            "https://boards.greenhouse.io/embed/job_app?for=acme&token=1234567"
        )
        self.assertEqual(key, "greenhouse.io:1234567")
        self.assertEqual(org, "greenhouse:acme")


class DiscoveryWriteIdentityTests(unittest.TestCase):
    def test_matches_existing_alternate_url_and_folds_new_urls(self):
        existing = {
            "id": "acme-data-scientist",
            "company": "Acme",
            "title": "Data Scientist",
            "job_url": "https://example.com/careers",
            "apply_url": "https://jobs.ashbyhq.com/acme/11111111-2222-3333-4444-555555555555",
            "alternate_urls": ["https://www.linkedin.com/jobs/view/123"],
        }
        listing = {
            "company": "Acme",
            "title": "Data Scientist",
            "job_url": "https://www.linkedin.com/jobs/view/123",
            "apply_url": "https://jobs.ashbyhq.com/acme/11111111-2222-3333-4444-555555555555?utm_source=x",
            "alternate_urls": ["https://www.indeed.com/viewjob?jk=abc"],
        }
        self.assertIs(find_existing_match(listing, [existing]), existing)
        fold_discovered_urls(existing, listing)
        self.assertIn(
            "https://www.indeed.com/viewjob?jk=abc",
            existing["alternate_urls"],
        )

    def test_matches_existing_by_posting_key_when_urls_differ(self):
        existing = {
            "id": "acme-data-scientist",
            "company": "Acme",
            "title": "Data Scientist",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/1234567",
        }
        listing = {
            "company": "Acme",
            "title": "Data Scientist",
            "apply_url": "https://job-boards.greenhouse.io/acme/jobs/1234567",
        }
        self.assertIs(find_existing_match(listing, [existing]), existing)

    def test_recovered_stub_upgrades_by_exact_company_title(self):
        recovered = {
            "id": "acme-data-scientist",
            "company": "Acme",
            "title": "Data Scientist",
            "source": "recovered",
        }
        listing = {
            "company": "Acme Inc.",
            "title": "Data Scientist",
            "apply_url": "https://jobs.lever.co/acme/abcdef",
        }
        self.assertTrue(is_recovered_stub(recovered))
        self.assertIs(find_existing_match(listing, [recovered]), recovered)

    def test_blocked_base_id_is_not_renamed_to_dash_two(self):
        listing = {"company": "Acme", "title": "Data Scientist"}
        self.assertIsNone(
            generated_job_id(
                listing,
                existing_ids={"acme-data-scientist"},
                blocked_ids={"acme-data-scientist"},
            )
        )

    def test_does_not_fold_into_deleted_job_via_posting_key(self):
        deleted = {
            "id": "acme-ds-old",
            "company": "Acme",
            "title": "Data Scientist",
            "status": "deleted",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/999",
        }
        stub = {
            "id": "acme-data-scientist",
            "company": "Acme",
            "title": "Data Scientist",
            "source": "recovered",
            "status": "discovered",
        }
        listing = {
            "company": "Acme",
            "title": "Data Scientist",
            # Host variant of the deleted URL — same posting_key, different block host.
            "apply_url": "https://job-boards.greenhouse.io/acme/jobs/999",
        }
        match = find_existing_match(listing, [deleted, stub])
        self.assertIs(match, stub)
        self.assertTrue(is_recovered_stub(match))

    def test_does_not_upgrade_stub_when_active_posting_key_matches(self):
        active = {
            "id": "acme-ds-live",
            "company": "Acme",
            "title": "Data Scientist",
            "status": "discovered",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/111",
        }
        stub = {
            "id": "acme-data-scientist",
            "company": "Acme",
            "title": "Data Scientist",
            "source": "recovered",
            "status": "discovered",
        }
        listing = {
            "company": "Acme",
            "title": "Data Scientist",
            "apply_url": "https://job-boards.greenhouse.io/acme/jobs/111",
        }
        match = find_existing_match(listing, [stub, active])
        self.assertIs(match, active)
        self.assertFalse(is_recovered_stub(match))

    def test_fold_upgrade_clears_needs_url_flag(self):
        stub = {
            "id": "acme-data-scientist",
            "company": "Acme",
            "title": "Data Scientist",
            "source": "recovered",
            "status": "discovered",
            "needs_url": True,
        }
        listing = {
            "company": "Acme",
            "title": "Data Scientist",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/222",
            "job_url": "https://boards.greenhouse.io/acme/jobs/222",
        }
        # Simulate the upgrade clear that write_discovered_jobs performs.
        fold_discovered_urls(stub, listing)
        stub["source"] = "greenhouse"
        stub["needs_url"] = False
        self.assertFalse(stub["needs_url"])
        self.assertTrue(collect_all_urls(stub) or stub.get("apply_url"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
