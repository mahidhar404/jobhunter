#!/usr/bin/env python3
"""Backfill skip policy: recovered stubs, Workday/iCIMS, LinkedIn Easy Apply."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import backfill_missing_jds as bf  # noqa: E402


class NeedsJdTests(unittest.TestCase):
    def test_recovered_without_url_is_skip_not_target(self):
        job = {"id": "stub-1", "source": "recovered", "job_description": ""}
        self.assertEqual(bf.skip_reason(job), "recovered-no-url")

    def test_workday_skip(self):
        job = {
            "id": "wd-1",
            "source": "linkedin",
            "job_description": "",
            "apply_url": "https://acme.myworkdayjobs.com/en-US/job/1",
        }
        self.assertEqual(bf.skip_reason(job), "workday")

    def test_icims_skip(self):
        job = {
            "id": "ic-1",
            "job_description": "",
            "apply_url": "https://acme.icims.com/jobs/123",
        }
        self.assertEqual(bf.skip_reason(job), "icims")

    def test_linkedin_easy_apply_skip(self):
        job = {
            "id": "li-1",
            "job_description": "",
            "apply_url": "https://www.linkedin.com/jobs/view/123",
        }
        self.assertEqual(bf.skip_reason(job), "linkedin")

    def test_jazzhr_fetch_uses_html_not_only_ldjson(self):
        job = {
            "id": "zea-1",
            "source": "jazzhr",
            "job_description": "",
            "apply_url": "https://acme.applytojob.com/apply/abc/Role",
        }
        with mock.patch.object(bf, "fetch_via_source_api", return_value=""):
            with mock.patch.object(
                bf,
                "extract_posting",
                return_value={"description": "Architect data models across SAP. " * 8},
            ):
                text, method = bf.fetch_description_for_job(job)
        self.assertGreater(len(text), 80)
        self.assertEqual(method, "extract")

    def test_needs_jd_when_stored_text_is_truncated_intro(self):
        job = {
            "id": "nextgenfed-senior-statistician",
            "source": "lever",
            "job_description": (
                "NextGen Federal Systems, LLC (NextGen) is seeking a Senior "
                "Statistician to work on our Operational Analysis Support "
                "contract in the 618th Air Operations Center at Scott AFB. "
                "Primary Work Location: Scott AFB IL"
            ),
            "apply_url": "https://jobs.lever.co/nextgenfed/7387d176/apply",
        }
        with mock.patch.object(bf, "stored_jd_text", return_value=job["job_description"]):
            self.assertTrue(bf.needs_jd(job))

    def test_is_upgrade_requires_longer_full_posting(self):
        old = "NextGen blurb at Scott AFB. " * 15
        new = old + "\n\nPosition Requirements\n" + ("SAS and R experience. " * 40)
        self.assertTrue(bf.is_upgrade(old, new))
        self.assertFalse(bf.is_upgrade(old, old))

    def test_workable_source_api_includes_requirements(self):
        job = {
            "id": "techop-1",
            "source": "workable",
            "job_description": "We are seeking a data analyst. " * 8,
            "apply_url": "https://apply.workable.com/j/C636EB0415/apply",
        }
        listing = {
            "url": "https://techop-solutions-international.workable.com/jobs/6017186",
        }
        detail = {
            "description": "<p>We are seeking a data analyst for CBP.</p>",
            "requirements": "<h2>Requirements</h2><p>" + ("SQL and process mapping. " * 40) + "</p>",
            "benefits": "",
        }

        def fake_json(url, *args, **kwargs):
            if "www.workable.com/api/jobs/" in url:
                return listing
            if "api/v2/accounts/" in url:
                return detail
            return None

        with mock.patch.object(bf, "fetch_json", side_effect=fake_json):
            text, method = bf.fetch_description_for_job(job)
        self.assertEqual(method, "api")
        self.assertIn("SQL and process mapping", text)
        self.assertFalse(bf.looks_truncated_jd(text))

    def test_workable_ignores_apply_subdomain_as_slug(self):
        url = "https://apply.workable.com/j/C636EB0415/apply"
        self.assertIsNone(bf.WORKABLE_HOST_RE.search(url))
        listing_url = "https://techop-solutions-international.workable.com/jobs/6017186"
        self.assertEqual(
            bf.WORKABLE_HOST_RE.search(listing_url).group(1),
            "techop-solutions-international",
        )

    def test_lever_closed_posting_is_skip_not_fail(self):
        job = {
            "id": "zoox-1",
            "source": "lever",
            "job_description": "Zoox mapping intro. " * 20,
            "apply_url": "https://jobs.lever.co/zoox/fd97675c-3483-42c6-826f-12fbb96ccafc/apply",
            "job_url": "https://jobs.lever.co/zoox/fd97675c-3483-42c6-826f-12fbb96ccafc",
        }
        with mock.patch.object(bf, "fetch_json", return_value=None):
            with mock.patch.object(bf, "http_status", return_value=404):
                text, method = bf.fetch_description_for_job(job)
        self.assertEqual(text, "")
        self.assertEqual(method, "skip:closed")

    def test_fetch_urls_prefer_posting_over_apply(self):
        apply = "https://jobs.lever.co/nextgenfed/335cfd8f-003b-4051-ba96-bce990df5e80/apply"
        posting = "https://jobs.lever.co/nextgenfed/335cfd8f-003b-4051-ba96-bce990df5e80"
        job = {"apply_url": apply, "job_url": posting}
        urls = bf.jd_fetch_urls_for_job(job)
        self.assertEqual(urls[0], posting)
        self.assertIn(apply, urls)

    def test_greenhouse_source_api_from_apply_url(self):
        job = {
            "id": "gh-1",
            "source": "greenhouse",
            "job_description": "",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/12345/apply",
        }
        detail = {
            "company_name": "Acme",
            "title": "Data Engineer",
            "location": {"name": "Remote"},
            "content": "<h2>Requirements</h2><p>" + ("Spark and Python. " * 40) + "</p>",
        }
        with mock.patch("extract_job_posting.fetch_json", return_value=detail):
            with mock.patch.object(bf, "extract_posting") as extract:
                text, method = bf.fetch_description_for_job(job)
        self.assertEqual(method, "api")
        self.assertIn("Spark and Python", text)
        extract.assert_not_called()

    def test_extract_is_called_with_posting_url_not_only_apply(self):
        apply = "https://jobs.lever.co/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/apply"
        posting = "https://jobs.lever.co/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        job = {
            "id": "acme-ml",
            "source": "builtin",
            "job_description": "",
            "apply_url": apply,
        }
        seen: list[str] = []

        def fake_extract(url, *, allow_playwright=True):
            seen.append((url, allow_playwright))
            if url == posting and not allow_playwright:
                return {"description": "Requirements\n" + ("Ship models. " * 40)}
            return None

        with mock.patch.object(bf, "fetch_via_source_api", return_value=""):
            with mock.patch.object(bf, "http_status", return_value=200):
                with mock.patch.object(bf, "extract_posting", side_effect=fake_extract):
                    text, method = bf.fetch_description_for_job(job)
        self.assertEqual(method, "extract")
        self.assertIn((posting, False), seen)
        self.assertGreater(len(text), 80)

    def test_preview_needs_disk_sync_ignores_normal_clip(self):
        disk = "Position Requirements\n" + ("Build models with Python and SQL. " * 80)
        clip = disk[:500] + " … [full text in resumes/<id>/jd_full.txt]"
        self.assertFalse(bf.preview_needs_disk_sync(clip, disk))
        intro = "NextGen is seeking a Senior Statistician at Scott AFB. " * 8
        self.assertTrue(bf.preview_needs_disk_sync(intro, disk))

    def test_apply_jd_fields_restamps_and_prunes_clearance(self):
        job = {
            "id": "clear-1",
            "status": "discovered",
            "company": "Acme",
            "title": "Data Engineer",
            "location": "Remote, US",
            "apply_url": "https://jobs.lever.co/acme/abc",
            "job_description": "Short intro only.",
        }
        full = (
            "We are hiring a Data Engineer.\n"
            "Requirements: Active Top Secret clearance required. "
            "Build pipelines in Python and SQL. " * 5
        )
        reason = bf.apply_jd_fields(job, full)
        self.assertEqual(reason, "clearance_or_intel")
        self.assertTrue(job["clearance"])
        self.assertEqual(job["status"], "deleted")
        self.assertEqual(job["deleted_reason"], "clearance_or_intel")
        self.assertIn("Pruned after JD backfill", job["status_detail"])

    def test_apply_jd_fields_stamps_without_pruning_clean_jd(self):
        job = {
            "id": "ok-1",
            "status": "discovered",
            "company": "Acme",
            "title": "ML Engineer",
            "location": "Remote, US",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
            "job_description": "",
        }
        full = (
            "Remote ML Engineer role.\n"
            "Requirements: 3+ years experience with Python and PyTorch. "
            "Build and ship models. " * 8
        )
        reason = bf.apply_jd_fields(job, full)
        self.assertIsNone(reason)
        self.assertEqual(job["status"], "discovered")
        self.assertEqual(job["work_mode"], "remote")
        self.assertEqual(job["min_yoe"], 3)
        self.assertFalse(job["clearance"])
        self.assertFalse(job["us_person"])

    def test_maybe_prune_skips_non_discovered(self):
        job = {
            "id": "inprog",
            "status": "in_progress",
            "title": "Data Scientist",
            "location": "Remote, US",
        }
        full = "Requires US citizenship and active TS/SCI clearance. " * 5
        self.assertIsNone(bf.maybe_prune_discovered_job(job, full))
        self.assertEqual(job["status"], "in_progress")


if __name__ == "__main__":
    unittest.main()
