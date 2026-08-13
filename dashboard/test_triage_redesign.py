#!/usr/bin/env python3
"""Triage redesign: Cancel→Open, Skip→Deleted, duplicate merge (no Skipped pen)."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _load_server():
    path = HERE / "server.py"
    spec = importlib.util.spec_from_file_location("jh_dashboard_server", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestDedupMergeDeletesLoser(unittest.TestCase):
    def test_mark_loser_merged_soft_deletes(self):
        from dedup_jobs import fold_urls_into_winner, mark_loser_merged, pick_winner

        ats = {
            "id": "ats-job",
            "company": "Acme",
            "title": "Engineer",
            "status": "discovered",
            "apply_url": "https://jobs.ashbyhq.com/acme/abc/application",
            "job_url": "https://jobs.ashbyhq.com/acme/abc",
            "alternate_urls": [],
        }
        li = {
            "id": "li-job",
            "company": "Acme",
            "title": "Engineer",
            "status": "discovered",
            "apply_url": "https://www.linkedin.com/jobs/view/999",
            "job_url": "https://www.linkedin.com/jobs/view/999",
            "alternate_urls": [],
        }
        winner, loser = pick_winner(ats, li)
        self.assertEqual(winner["id"], "ats-job")
        fold_urls_into_winner(winner, loser)
        mark_loser_merged(loser, winner, why="test")
        self.assertEqual(loser["status"], "deleted")
        self.assertEqual(loser["deleted_reason"], "duplicate")
        self.assertEqual(loser["duplicate_of"], "ats-job")
        self.assertTrue(
            any("linkedin.com" in (u or "") for u in (winner.get("alternate_urls") or []))
            or "linkedin.com" in (winner.get("source_url") or "")
            or any("linkedin.com" in (u or "") for u in [winner.get("job_url"), winner.get("apply_url")])
        )
        # Both links preserved on survivor
        norms = " ".join(
            str(x)
            for x in [
                winner.get("apply_url"),
                winner.get("job_url"),
                winner.get("source_url"),
                *(winner.get("alternate_urls") or []),
            ]
        )
        self.assertIn("ashbyhq.com", norms)
        self.assertIn("linkedin.com", norms)

    def test_merge_active_jobs_deletes_not_skipped(self):
        from dedup_jobs import _merge_active_jobs

        jobs = [
            {
                "id": "w",
                "company": "Acme Inc",
                "title": "Software Engineer",
                "status": "discovered",
                "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
                "job_url": "https://boards.greenhouse.io/acme/jobs/1",
                "job_description": "x" * 400,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": "l",
                "company": "Acme",
                "title": "Software Engineer",
                "status": "discovered",
                "apply_url": "https://www.linkedin.com/jobs/view/1",
                "job_url": "https://www.linkedin.com/jobs/view/1",
                "job_description": "y" * 50,
                "created_at": "2026-01-02T00:00:00+00:00",
            },
        ]
        by_id = {j["id"]: j for j in jobs}
        n = _merge_active_jobs(jobs, by_id, dry_run=False)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(by_id["l"]["status"], "deleted")
        self.assertEqual(by_id["l"]["deleted_reason"], "duplicate")
        self.assertNotEqual(by_id["l"]["status"], "skipped_duplicate")
        self.assertEqual(by_id["w"]["status"], "discovered")


class TestSanDiskClassFreshnessMerge(unittest.TestCase):
    """Older winner + newer SmartRecruiters re-post must keep Open-visible freshness."""

    def test_merge_promotes_fresher_date_and_ats_url(self):
        from dedup_jobs import (
            _merge_active_jobs,
            fold_urls_into_winner,
            mark_loser_merged,
            pick_winner,
            posted_signal,
        )
        from apply_urls import normalize_url

        older = {
            "id": "sandisk-bi-old",
            "company": "Sandisk",
            "title": "Business Intelligence (BI) Analyst – Finance",
            "status": "discovered",
            "date_posted": "2026-05-01",
            "apply_url": "https://jobs.smartrecruiters.com/Sandisk/744000124195146",
            "job_url": "https://jobs.smartrecruiters.com/Sandisk/744000124195146",
            "alternate_urls": [],
            "job_description": "Build finance BI dashboards. " * 40,
            "created_at": "2026-08-02T12:00:00+00:00",
        }
        newer = {
            "id": "sandisk-bi-new",
            "company": "Sandisk",
            "title": "Business Intelligence (BI) Analyst – Finance",
            "status": "discovered",
            "date_posted": "2026-08-04",
            "apply_url": (
                "https://jobs.smartrecruiters.com/Sandisk/"
                "744000141410579-business-intelligence-bi-analyst-finance"
            ),
            "job_url": (
                "https://jobs.smartrecruiters.com/Sandisk/"
                "744000141410579-business-intelligence-bi-analyst-finance"
            ),
            "alternate_urls": [],
            "job_description": "Build finance BI dashboards. " * 40,
            "created_at": "2026-08-04T10:00:00+00:00",
        }
        winner, loser = pick_winner(older, newer)
        # Older/longer-history record still wins identity; freshness is folded on.
        self.assertEqual(winner["id"], "sandisk-bi-old")
        self.assertEqual(loser["id"], "sandisk-bi-new")
        fold_urls_into_winner(winner, loser)
        mark_loser_merged(loser, winner, why="identical job description fingerprint")

        self.assertEqual(loser["status"], "deleted")
        self.assertEqual(loser["deleted_reason"], "duplicate")
        self.assertEqual(loser["duplicate_of"], "sandisk-bi-old")
        self.assertEqual(loser["merged_from"], "sandisk-bi-old")

        # Winner visible freshness ≥ loser's posted date (stale filter uses date_posted).
        self.assertEqual(winner["date_posted"], "2026-08-04")
        self.assertGreaterEqual(
            posted_signal(winner).ts,  # type: ignore[union-attr]
            posted_signal({"date_posted": "2026-08-04"}).ts,  # type: ignore[union-attr]
        )

        # Fresher equal-quality ATS URL is primary; old ATS kept in pool.
        self.assertIn("744000141410579", winner.get("apply_url") or "")
        blob = " ".join(
            str(x)
            for x in [
                winner.get("apply_url"),
                winner.get("job_url"),
                winner.get("source_url"),
                *(winner.get("alternate_urls") or []),
            ]
        )
        self.assertIn("744000141410579", blob)
        self.assertIn("744000124195146", blob)
        # Norms distinct — both retained
        norms = {
            normalize_url(u)
            for u in [
                winner.get("apply_url"),
                winner.get("job_url"),
                *(winner.get("alternate_urls") or []),
            ]
            if u
        }
        self.assertTrue(any("744000141410579" in (n or "") for n in norms))
        self.assertTrue(any("744000124195146" in (n or "") for n in norms))

    def test_merge_active_jobs_sandisk_pair(self):
        from dedup_jobs import _merge_active_jobs

        jobs = [
            {
                "id": "w",
                "company": "Sandisk",
                "title": "Business Intelligence Analyst Finance",
                "status": "discovered",
                "date_posted": "2026-05-01",
                "apply_url": "https://jobs.smartrecruiters.com/Sandisk/744000124195146",
                "job_url": "https://jobs.smartrecruiters.com/Sandisk/744000124195146",
                "job_description": "x" * 400,
                "created_at": "2026-08-02T00:00:00+00:00",
            },
            {
                "id": "l",
                "company": "Sandisk",
                "title": "Business Intelligence Analyst Finance",
                "status": "discovered",
                "date_posted": "2026-08-04",
                "apply_url": (
                    "https://jobs.smartrecruiters.com/Sandisk/"
                    "744000141410579-business-intelligence-bi-analyst-finance"
                ),
                "job_url": (
                    "https://jobs.smartrecruiters.com/Sandisk/"
                    "744000141410579-business-intelligence-bi-analyst-finance"
                ),
                "job_description": "x" * 400,
                "created_at": "2026-08-04T00:00:00+00:00",
            },
        ]
        by_id = {j["id"]: j for j in jobs}
        n = _merge_active_jobs(jobs, by_id, dry_run=False)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(by_id["l"]["status"], "deleted")
        self.assertEqual(by_id["l"]["duplicate_of"], "w")
        self.assertEqual(by_id["w"]["date_posted"], "2026-08-04")
        self.assertIn("744000141410579", by_id["w"].get("apply_url") or "")

    def test_ats_still_beats_linkedin_even_if_linkedin_fresher(self):
        from dedup_jobs import fold_urls_into_winner, pick_winner

        ats = {
            "id": "ats",
            "company": "Acme",
            "title": "Engineer",
            "status": "discovered",
            "date_posted": "2026-05-01",
            "apply_url": "https://jobs.lever.co/acme/abc",
            "job_url": "https://jobs.lever.co/acme/abc",
            "alternate_urls": [],
            "job_description": "y" * 100,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        li = {
            "id": "li",
            "company": "Acme",
            "title": "Engineer",
            "status": "discovered",
            "date_posted": "2026-08-05",
            "apply_url": "https://www.linkedin.com/jobs/view/999",
            "job_url": "https://www.linkedin.com/jobs/view/999",
            "alternate_urls": [],
            "job_description": "y" * 50,
            "created_at": "2026-08-05T00:00:00+00:00",
        }
        winner, loser = pick_winner(ats, li)
        self.assertEqual(winner["id"], "ats")
        fold_urls_into_winner(winner, loser)
        self.assertIn("lever.co", winner.get("apply_url") or "")
        self.assertNotIn("linkedin.com", (winner.get("apply_url") or "").lower())
        # Freshness still promoted from LinkedIn re-discovery
        self.assertEqual(winner["date_posted"], "2026-08-05")


class TestCancelResetAndSkipDelete(unittest.TestCase):
    def setUp(self):
        self.srv = _load_server()

    def test_reset_keeps_resume_path(self):
        job = {
            "id": "c1",
            "status": "filling",
            "resume_path": "/tmp/dummy_resume.pdf",
            "question": "What?",
            "pending_command": "ls",
            "ready_announced": True,
            "timeline": [],
        }
        self.srv._reset_job_to_open_after_cancel(job)
        self.assertEqual(job["status"], "discovered")
        self.assertEqual(job["resume_path"], "/tmp/dummy_resume.pdf")
        self.assertIsNone(job["question"])
        self.assertIsNone(job["pending_command"])
        self.assertNotIn("ready_announced", job)
        self.assertIn("returned to Open", job["status_detail"])

    def test_handle_cancel_resets_to_discovered(self):
        srv = self.srv
        jobs = {
            "jobs": [
                {
                    "id": "run1",
                    "status": "filling",
                    "session_key": "agent:job-hunter:job-run1",
                    "resume_path": str(ROOT / "scripts/fastfill/fixtures/dummy_resume_de.pdf"),
                    "status_detail": "Filling…",
                    "timeline": [],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ]
        }
        handler = srv.Handler.__new__(srv.Handler)
        writes = []

        def write_jobs(data):
            writes.append(data)

        with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
            srv, "write_jobs", side_effect=write_jobs
        ), mock.patch.object(srv, "_kill_process_tree"), mock.patch.object(
            srv, "abort_gateway_session"
        ), mock.patch.object(srv, "clear_fill_activity"), mock.patch.object(
            srv, "close_job_partyrock_tab", return_value={}
        ), mock.patch.object(handler, "_send_json") as send:
            handler._handle_cancel("run1")
        self.assertEqual(jobs["jobs"][0]["status"], "discovered")
        self.assertTrue(jobs["jobs"][0].get("resume_path"))
        payload = send.call_args[0][0]
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("status"), "discovered")
        self.assertTrue(payload.get("resume_kept"))

    def test_handle_cancel_stuck_resets_to_discovered(self):
        srv = self.srv
        jobs = {
            "jobs": [
                {
                    "id": "stuck1",
                    "status": "stuck",
                    "session_key": "agent:job-hunter:job-stuck1",
                    "resume_path": str(ROOT / "scripts/fastfill/fixtures/dummy_resume_de.pdf"),
                    "status_detail": "Agent needs help",
                    "question": "Which option should I pick?",
                    "timeline": [],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ]
        }
        handler = srv.Handler.__new__(srv.Handler)
        writes = []

        def write_jobs(data):
            writes.append(data)

        with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
            srv, "write_jobs", side_effect=write_jobs
        ), mock.patch.object(srv, "_kill_process_tree"), mock.patch.object(
            srv, "abort_gateway_session"
        ), mock.patch.object(srv, "clear_fill_activity"), mock.patch.object(
            srv, "close_job_partyrock_tab", return_value={}
        ), mock.patch.object(handler, "_send_json") as send:
            handler._handle_cancel("stuck1")
        job = jobs["jobs"][0]
        self.assertEqual(job["status"], "discovered")
        self.assertTrue(job.get("resume_path"))
        self.assertIsNone(job.get("question"))
        payload = send.call_args[0][0]
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("status"), "discovered")

    def test_handle_skip_soft_deletes(self):
        srv = self.srv
        jobs = {
            "jobs": [
                {
                    "id": "s1",
                    "status": "discovered",
                    "title": "Role",
                    "company": "Co",
                    "timeline": [],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ]
        }
        handler = srv.Handler.__new__(srv.Handler)
        with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
            srv, "write_jobs"
        ), mock.patch.object(srv, "block_deleted_job", return_value=[]), mock.patch.object(
            handler, "_send_json"
        ) as send:
            handler._handle_skip("s1", {})
        self.assertEqual(jobs["jobs"][0]["status"], "deleted")
        self.assertEqual(jobs["jobs"][0]["deleted_reason"], "user")
        self.assertIn("Skipped by user", jobs["jobs"][0]["status_detail"])
        payload = send.call_args[0][0]
        self.assertEqual(payload.get("status"), "deleted")

    def test_handle_skip_contract_reason(self):
        srv = self.srv
        jobs = {
            "jobs": [
                {
                    "id": "c2c",
                    "status": "discovered",
                    "timeline": [],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ]
        }
        handler = srv.Handler.__new__(srv.Handler)
        with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
            srv, "write_jobs"
        ), mock.patch.object(srv, "block_deleted_job", return_value=[]), mock.patch.object(
            handler, "_send_json"
        ) as send:
            handler._handle_skip("c2c", {"reason": "contract"})
        self.assertEqual(jobs["jobs"][0]["deleted_reason"], "contract")
        self.assertIn("contract", (jobs["jobs"][0]["status_detail"] or "").lower())

    def test_handle_skip_duplicate_merges_urls(self):
        srv = self.srv
        jobs = {
            "jobs": [
                {
                    "id": "dup-ats",
                    "company": "Acme",
                    "title": "Backend Engineer",
                    "status": "discovered",
                    "apply_url": "https://jobs.lever.co/acme/abc",
                    "job_url": "https://jobs.lever.co/acme/abc",
                    "alternate_urls": [],
                    "job_description": "Build APIs " * 40,
                    "timeline": [],
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "id": "dup-li",
                    "company": "Acme",
                    "title": "Backend Engineer",
                    "status": "discovered",
                    "apply_url": "https://www.linkedin.com/jobs/view/42",
                    "job_url": "https://www.linkedin.com/jobs/view/42",
                    "alternate_urls": [],
                    "job_description": "Build APIs " * 40,
                    "timeline": [],
                    "created_at": "2026-01-02T00:00:00+00:00",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            ]
        }
        handler = srv.Handler.__new__(srv.Handler)
        with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
            srv, "write_jobs"
        ), mock.patch.object(handler, "_send_json") as send:
            handler._handle_skip("dup-li", {"reason": "duplicate"})
        by_id = {j["id"]: j for j in jobs["jobs"]}
        self.assertEqual(by_id["dup-li"]["status"], "deleted")
        self.assertEqual(by_id["dup-li"]["deleted_reason"], "duplicate")
        self.assertEqual(by_id["dup-ats"]["status"], "discovered")
        blob = " ".join(
            str(x)
            for x in [
                by_id["dup-ats"].get("apply_url"),
                by_id["dup-ats"].get("source_url"),
                *(by_id["dup-ats"].get("alternate_urls") or []),
            ]
        )
        self.assertIn("lever.co", blob)
        self.assertIn("linkedin.com", blob)
        payload = send.call_args[0][0]
        self.assertEqual(payload.get("merged_into"), "dup-ats")

    def test_migrate_skipped_to_deleted_cancelled_to_open(self):
        srv = self.srv
        jobs = {
            "jobs": [
                {
                    "id": "m1",
                    "status": "skipped_contract",
                    "status_detail": "Skipped: contract/C2C.",
                    "timeline": [],
                },
                {
                    "id": "m2",
                    "status": "cancelled",
                    "status_detail": "Cancelled by user.",
                    "resume_path": "/tmp/r.pdf",
                    "timeline": [],
                },
                {
                    "id": "m3",
                    "status": "skipped_duplicate",
                    "status_detail": "Duplicate of x",
                    "duplicate_of": "x",
                    "timeline": [],
                },
            ]
        }
        with mock.patch.object(srv, "read_jobs", return_value=jobs), mock.patch.object(
            srv, "write_jobs"
        ), mock.patch.object(srv, "block_deleted_job", return_value=[]):
            counts = srv.migrate_triage_holding_pen_once()
        self.assertEqual(counts["skipped_to_deleted"], 2)
        self.assertEqual(counts["cancelled_to_open"], 1)
        by_id = {j["id"]: j for j in jobs["jobs"]}
        self.assertEqual(by_id["m1"]["status"], "deleted")
        self.assertEqual(by_id["m1"]["deleted_reason"], "contract")
        self.assertEqual(by_id["m2"]["status"], "discovered")
        self.assertEqual(by_id["m2"]["resume_path"], "/tmp/r.pdf")
        self.assertEqual(by_id["m3"]["status"], "deleted")
        self.assertEqual(by_id["m3"]["deleted_reason"], "duplicate")


class TestOpsUiNoSkippedChip(unittest.TestCase):
    def test_skipped_chip_removed(self):
        html = (HERE / "static" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('data-queue="skipped"', html)
        self.assertNotIn("stat-skipped", html)
        app = (HERE / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('setQueue("skipped")', app)
        self.assertIn("surfaceDeletedJob", app)
        self.assertIn("surfaceOpenJob", app)
        self.assertIn("LEGACY_SKIPPED_STATUSES", app)
        # Pipeline filter options removed
        self.assertNotIn('<option value="cancelled">Cancelled</option>', html)
        self.assertNotIn('<option value="skipped">Skipped</option>', html)

    def test_restore_error_message_updated(self):
        src = (HERE / "server.py").read_text(encoding="utf-8")
        self.assertIn("only deleted (or legacy skipped/cancelled) jobs can be restored", src)


if __name__ == "__main__":
    unittest.main()
