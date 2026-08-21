#!/usr/bin/env python3
"""Tests for scripts/link_liveness.py — dead/404 prune vs soft failures."""
from __future__ import annotations

import contextlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import link_liveness as ll  # noqa: E402


LEVER_404_HTML = """
<html><body>
  <h1>Sorry, we couldn't find anything here</h1>
  <p>This posting might have closed. (404 error)</p>
</body></html>
"""

LEVER_LIVE_HTML = """
<html><body>
  <h1>Data Engineer</h1>
  <p>AnaVation is hiring a Data Engineer in Hanover, MD.</p>
  <a href="/apply">Apply</a>
</body></html>
"""

GREENHOUSE_CLOSED_HTML = """
<html><body>
  <h2>This job posting is no longer available</h2>
</body></html>
"""

ASHBY_CLOSED_HTML = """
<html><body>
  <h1>This job is no longer available</h1>
  <p>The job you're looking for has been closed or removed.</p>
</body></html>
"""

ASHBY_404_HTML = """
<html><body>
  <title>Job posting not found</title>
  <p>Job posting not found</p>
</body></html>
"""


def _ats_job(**kwargs):
    job = {
        "id": "anavationllc-data-engineer",
        "status": "discovered",
        "company": "AnaVation",
        "title": "Data Engineer",
        "apply_url": (
            "https://jobs.lever.co/anavationllc/"
            "a3b73ff5-1556-4963-aef6-39881a5cda0e/apply"
        ),
        "job_url": (
            "https://jobs.lever.co/anavationllc/"
            "a3b73ff5-1556-4963-aef6-39881a5cda0e"
        ),
        "source": "lever",
    }
    job.update(kwargs)
    return job


class ClassifyTests(unittest.TestCase):
    def test_greenhouse_host_404_labels_closed_greenhouse(self):
        r = ll.classify_http_response(
            url="https://boards.greenhouse.io/acme/jobs/1",
            status=404,
            body="<html>not found</html>",
        )
        self.assertEqual(r.verdict, "dead")
        self.assertEqual(r.deleted_reason, "closed/greenhouse")
        self.assertEqual(r.label, "closed/greenhouse")

    def test_company_careers_404_is_dead_404(self):
        r = ll.classify_http_response(
            url="https://careers.example.com/jobs/12345",
            status=404,
            body="<html>not found</html>",
        )
        self.assertEqual(r.verdict, "dead")
        self.assertEqual(r.deleted_reason, "dead/404")
        self.assertEqual(r.label, "dead/404")

    def test_lever_host_404_labels_closed_lever_even_without_body(self):
        r = ll.classify_http_response(
            url="https://jobs.lever.co/anavationllc/a3b73ff5-1556-4963-aef6-39881a5cda0e",
            status=404,
            body="<title>Not found – 404 error</title>",  # message may be past body cap
        )
        self.assertEqual(r.verdict, "dead")
        self.assertEqual(r.deleted_reason, "closed/lever")
        self.assertEqual(r.label, "closed/lever")

    def test_lever_404_html_uses_closed_lever_label(self):
        r = ll.classify_http_response(
            url="https://jobs.lever.co/anavationllc/a3b73ff5-1556-4963-aef6-39881a5cda0e",
            status=404,
            body=LEVER_404_HTML,
        )
        self.assertEqual(r.verdict, "dead")
        self.assertEqual(r.deleted_reason, "closed/lever")
        self.assertEqual(r.label, "closed/lever")
        self.assertIn("lever", (r.detail or "").lower())

    def test_http_410_is_dead_410_for_company(self):
        r = ll.classify_http_response(
            url="https://careers.example.com/jobs/1",
            status=410,
            body="",
        )
        self.assertEqual(r.verdict, "dead")
        self.assertEqual(r.deleted_reason, "dead/410")

    def test_ashby_host_404_labels_closed_ashby(self):
        r = ll.classify_http_response(
            url="https://jobs.ashbyhq.com/acme/abc-123",
            status=404,
            body=ASHBY_404_HTML,
        )
        self.assertEqual(r.verdict, "dead")
        self.assertEqual(r.deleted_reason, "closed/ashby")

    def test_200_live_keeps(self):
        r = ll.classify_http_response(
            url="https://jobs.lever.co/acme/abc",
            status=200,
            body=LEVER_LIVE_HTML,
        )
        self.assertEqual(r.verdict, "alive")
        self.assertIsNone(r.deleted_reason)

    def test_200_closed_greenhouse_html(self):
        r = ll.classify_http_response(
            url="https://boards.greenhouse.io/acme/jobs/1",
            status=200,
            body=GREENHOUSE_CLOSED_HTML,
        )
        self.assertEqual(r.verdict, "dead")
        self.assertEqual(r.deleted_reason, "closed/greenhouse")

    def test_200_closed_ashby_html(self):
        r = ll.classify_http_response(
            url="https://jobs.ashbyhq.com/acme/uuid-here",
            status=200,
            body=ASHBY_CLOSED_HTML,
        )
        self.assertEqual(r.verdict, "dead")
        self.assertEqual(r.deleted_reason, "closed/ashby")

    def test_smartrecruiters_404_labels_closed(self):
        r = ll.classify_http_response(
            url="https://jobs.smartrecruiters.com/Acme/111",
            status=404,
            body="",
        )
        self.assertEqual(r.verdict, "dead")
        self.assertEqual(r.deleted_reason, "closed/smartrecruiters")

    def test_jazzhr_404_labels_closed(self):
        r = ll.classify_http_response(
            url="https://acme.applytojob.com/apply/abc",
            status=404,
            body="",
        )
        self.assertEqual(r.verdict, "dead")
        self.assertEqual(r.deleted_reason, "closed/jazzhr")

    def test_timeout_soft_no_prune(self):
        r = ll.classify_http_response(
            url="https://jobs.lever.co/acme/abc",
            status=None,
            error="timeout",
        )
        self.assertEqual(r.verdict, "soft_fail")
        self.assertEqual(r.signal, "timeout")
        self.assertIsNone(r.deleted_reason)

    def test_403_and_429_soft(self):
        for status in (403, 429):
            r = ll.classify_http_response(
                url="https://jobs.lever.co/acme/abc",
                status=status,
                body="forbidden",
            )
            self.assertEqual(r.verdict, "soft_fail", status)
            self.assertIsNone(r.deleted_reason)


class SelectionTests(unittest.TestCase):
    def test_company_careers_job_url_is_checkable(self):
        job = {
            "id": "acme-ml",
            "status": "discovered",
            "apply_url": "https://careers.acme.com/jobs/12345",
            "job_url": "https://careers.acme.com/jobs/12345",
        }
        self.assertTrue(ll.job_has_checkable_ats_url(job))
        self.assertEqual(
            ll.primary_listing_url(job),
            "https://careers.acme.com/jobs/12345",
        )

    def test_linkedin_only_not_checkable(self):
        job = _ats_job(
            apply_url="https://www.linkedin.com/jobs/view/123",
            job_url="https://www.linkedin.com/jobs/view/123",
            source="linkedin",
        )
        self.assertFalse(ll.job_has_checkable_ats_url(job))
        self.assertEqual(ll.primary_listing_url(job), "")

    def test_prefers_ats_when_apply_still_linkedin(self):
        job = _ats_job(
            apply_url="https://www.linkedin.com/jobs/view/999",
            job_url=(
                "https://boards.greenhouse.io/acme/jobs/555"
            ),
        )
        self.assertTrue(ll.job_has_checkable_ats_url(job))
        self.assertIn("greenhouse.io", ll.primary_listing_url(job))


class TombstoneTests(unittest.TestCase):
    def test_tombstone_sets_concrete_reason_and_chip(self):
        job = _ats_job()
        result = ll.classify_http_response(
            url=job["job_url"],
            status=404,
            body=LEVER_404_HTML,
        )
        self.assertTrue(ll.should_prune_closed_posting(job, result))
        self.assertTrue(ll.tombstone_closed_posting(job, result))
        self.assertEqual(job["status"], "deleted")
        self.assertEqual(job["deleted_reason"], "closed/lever")
        self.assertTrue(job.get("closed_posting"))
        self.assertEqual(job.get("closed_posting_label"), "closed/lever")
        self.assertIn("closed/lever", job.get("status_detail", "").lower())
        # Idempotent once deleted
        self.assertFalse(ll.should_prune_closed_posting(job, result))

    def test_timeout_does_not_tombstone(self):
        job = _ats_job()
        result = ll.LivenessResult(
            url=job["job_url"],
            verdict="soft_fail",
            signal="timeout",
            detail="timeout",
        )
        self.assertFalse(ll.should_prune_closed_posting(job, result))
        self.assertFalse(ll.tombstone_closed_posting(job, result))
        self.assertEqual(job["status"], "discovered")

    def test_alive_does_not_tombstone(self):
        job = _ats_job()
        result = ll.LivenessResult(
            url=job["job_url"],
            verdict="alive",
            http_status=200,
            signal="ok",
        )
        self.assertFalse(ll.tombstone_closed_posting(job, result))
        self.assertEqual(job["status"], "discovered")

    def test_aggregator_url_not_checkable(self):
        job = _ats_job(
            apply_url="https://www.linkedin.com/jobs/view/123",
            job_url="https://www.linkedin.com/jobs/view/123",
            source="linkedin",
        )
        self.assertFalse(ll.job_has_checkable_ats_url(job))
        result = ll.check_job_liveness(job)
        self.assertEqual(result.verdict, "skip")


class SweepTests(unittest.TestCase):
    def test_sweep_prunes_dead_keeps_soft(self):
        dead_job = _ats_job(id="dead-1", date_posted="2026-08-01T00:00:00+00:00")
        soft_job = _ats_job(
            id="soft-1",
            date_posted="2026-08-02T00:00:00+00:00",
            apply_url="https://jobs.lever.co/other/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb/apply",
            job_url="https://jobs.lever.co/other/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        )
        live_job = _ats_job(
            id="live-1",
            date_posted="2026-08-03T00:00:00+00:00",
            apply_url="https://jobs.lever.co/live/cccccccc-cccc-cccc-cccc-cccccccccccc/apply",
            job_url="https://jobs.lever.co/live/cccccccc-cccc-cccc-cccc-cccccccccccc",
        )
        gh_dead = {
            "id": "gh-dead",
            "status": "discovered",
            "date_posted": "2026-07-01T00:00:00+00:00",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/99",
            "job_url": "https://boards.greenhouse.io/acme/jobs/99",
        }
        jobs = [dead_job, soft_job, live_job, gh_dead]

        def fake_fetch(url, *, timeout_s=12.0):
            if "other/" in url:
                return ll.LivenessResult(
                    url=url, verdict="soft_fail", signal="timeout", detail="timeout"
                )
            if "live/" in url:
                return ll.LivenessResult(
                    url=url, verdict="alive", http_status=200, signal="ok"
                )
            if "greenhouse.io" in url:
                return ll.classify_http_response(
                    url=url, status=404, body="<html>gone</html>"
                )
            return ll.classify_http_response(
                url=url, status=404, body=LEVER_404_HTML
            )

        @contextlib.contextmanager
        def _read_cm():
            yield {"jobs": jobs}

        @contextlib.contextmanager
        def _write_cm():
            yield {"jobs": jobs}

        with patch.object(ll, "locked_jobs_for_read", return_value=_read_cm()), \
             patch.object(ll, "locked_jobs_for_write", return_value=_write_cm()), \
             patch.object(ll, "_tombstone_url_block") as block:
            summary = ll.sweep_closed_postings(
                write=True,
                limit=10,
                concurrency=2,
                skip_recently_checked_s=None,
                fetch=fake_fetch,
            )

        self.assertEqual(summary["pruned"], 2)
        self.assertEqual(summary["dead"], 2)
        self.assertEqual(summary["soft_fail"], 1)
        self.assertEqual(summary["alive"], 1)
        self.assertEqual(dead_job["status"], "deleted")
        self.assertEqual(dead_job["deleted_reason"], "closed/lever")
        self.assertEqual(gh_dead["status"], "deleted")
        self.assertEqual(gh_dead["deleted_reason"], "closed/greenhouse")
        self.assertEqual(soft_job["status"], "discovered")
        self.assertEqual(live_job["status"], "discovered")
        self.assertTrue(soft_job.get("link_liveness_at"))
        self.assertEqual(block.call_count, 2)
        self.assertIn("closed/lever", summary["pruned_by_reason"])
        self.assertIn("closed/greenhouse", summary["pruned_by_reason"])


if __name__ == "__main__":
    unittest.main()
