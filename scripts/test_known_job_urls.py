#!/usr/bin/env python3
"""Unit tests for known_job_urls skip-key helpers."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from known_job_urls import (  # noqa: E402
    load_known_url_keys,
    load_skip_urls_file,
    url_is_known,
    write_skip_urls_file,
)


class KnownJobUrlsTests(unittest.TestCase):
    def test_url_is_known_normalizes(self) -> None:
        known = {"https://boards.greenhouse.io/acme/jobs/1"}
        self.assertTrue(
            url_is_known("https://boards.greenhouse.io/acme/jobs/1?utm_source=x", known)
        )
        self.assertFalse(url_is_known("https://boards.greenhouse.io/acme/jobs/2", known))

    def test_load_from_jobs_and_listings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            jobs = td_path / "jobs.json"
            jobs.write_text(json.dumps({
                "jobs": [{
                    "id": "a",
                    "job_url": "https://jobs.lever.co/acme/abc",
                    "apply_url": "https://jobs.lever.co/acme/abc/apply",
                    "alternate_urls": ["https://www.indeed.com/viewjob?jk=1"],
                }]
            }))
            listing = td_path / "listing.json"
            listing.write_text(json.dumps([{
                "job_url": "https://builtin.com/job/foo/123",
                "title": "ML Engineer",
            }]))
            keys = load_known_url_keys(
                jobs_path=jobs,
                extra_listing_paths=[listing],
                include_blocked=False,
            )
            self.assertTrue(url_is_known("https://jobs.lever.co/acme/abc", keys))
            self.assertTrue(url_is_known("https://builtin.com/job/foo/123", keys))
            out = td_path / "skip.json"
            write_skip_urls_file(out, keys)
            loaded = load_skip_urls_file(out)
            self.assertEqual(keys, loaded)


if __name__ == "__main__":
    unittest.main()
