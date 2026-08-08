#!/usr/bin/env python3
"""Tests for batch Empty Deleted tombstone writes."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import blocked_urls as bu


class BlockDeletedJobsBatchTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        root = Path(self._td.name)
        self._prev_file = bu.BLOCKED_URLS_FILE
        self._prev_lock = bu.LOCK_FILE
        bu.BLOCKED_URLS_FILE = root / "blocked_urls.json"
        bu.LOCK_FILE = root / "blocked_urls.json.lock"
        bu.BLOCKED_URLS_FILE.write_text(json.dumps(bu._empty()) + "\n")

    def tearDown(self):
        bu.BLOCKED_URLS_FILE = self._prev_file
        bu.LOCK_FILE = self._prev_lock
        self._td.cleanup()

    def test_batch_writes_once_and_keeps_tombstones(self):
        jobs = [
            {
                "id": "acme-eng",
                "company": "Acme",
                "title": "Engineer",
                "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
            },
            {
                "id": "beta-ml",
                "company": "Beta",
                "title": "ML Eng",
                "job_url": "https://jobs.lever.co/beta/abc",
            },
        ]
        keys = bu.block_deleted_jobs_batch(jobs, keep_tombstone=True)
        self.assertTrue(keys)
        data = json.loads(bu.BLOCKED_URLS_FILE.read_text())
        self.assertIn("acme-eng", data["ids"])
        self.assertIn("beta-ml", data["ids"])
        self.assertEqual(len(data["tombstones"]), 2)
        # Idempotent second batch
        keys2 = bu.block_deleted_jobs_batch(jobs, keep_tombstone=True)
        self.assertTrue(keys2)
        data2 = json.loads(bu.BLOCKED_URLS_FILE.read_text())
        self.assertEqual(len(data2["tombstones"]), 2)
        self.assertEqual(sorted(data2["ids"]), sorted(data["ids"]))

    def test_single_wrapper_matches_batch(self):
        job = {
            "id": "solo",
            "company": "Solo",
            "title": "Dev",
            "apply_url": "https://example.com/jobs/solo",
        }
        keys = bu.block_deleted_job(job, keep_tombstone=True)
        self.assertTrue(keys)
        data = json.loads(bu.BLOCKED_URLS_FILE.read_text())
        self.assertEqual(data["ids"], ["solo"])
        self.assertEqual(len(data["tombstones"]), 1)


if __name__ == "__main__":
    unittest.main()
