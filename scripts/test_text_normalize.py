#!/usr/bin/env python3
"""Company identity: suffix-normalize, persist company_key, never merge jobs.

Dummy employer names only — no applicant PII.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from text_normalize import (  # noqa: E402
    backfill_company_keys,
    normalize_company,
    stamp_company_key,
)
from write_discovered_jobs import generated_job_id, find_existing_match  # noqa: E402


class TestNormalizeCompany(unittest.TestCase):
    def test_bright_vision_suffix_is_same_employer(self):
        self.assertEqual(
            normalize_company("Bright Vision"),
            normalize_company("Bright Vision Technologies"),
        )
        self.assertEqual(normalize_company("Bright Vision"), "brightvision")
        self.assertEqual(
            normalize_company("Bright Vision Technologies"),
            "brightvision",
        )

    def test_legal_suffixes_and_punctuation(self):
        self.assertEqual(normalize_company("Acme Inc."), normalize_company("Acme"))
        self.assertEqual(normalize_company("Acme, LLC"), normalize_company("ACME"))
        self.assertEqual(
            normalize_company("  Bright   Vision  Technologies, Inc.  "),
            normalize_company("Bright Vision"),
        )

    def test_does_not_fuzzy_merge_apple_hospital(self):
        self.assertNotEqual(
            normalize_company("Apple"),
            normalize_company("Apple Hospital"),
        )


class TestStampCompanyKey(unittest.TestCase):
    def test_stamps_key_and_keeps_display_company(self):
        job = {
            "id": "bright-vision-data-engineer",
            "company": "Bright Vision Technologies",
            "title": "Data Engineer",
        }
        self.assertTrue(stamp_company_key(job))
        self.assertEqual(job["company"], "Bright Vision Technologies")
        self.assertEqual(job["company_key"], "brightvision")
        self.assertFalse(stamp_company_key(job))

    def test_rewrites_stale_key_without_touching_company(self):
        job = {
            "company": "Bright Vision Technologies",
            "company_key": "brightvisiontechnologies",
        }
        self.assertTrue(stamp_company_key(job))
        self.assertEqual(job["company"], "Bright Vision Technologies")
        self.assertEqual(job["company_key"], "brightvision")

    def test_skips_blank_company(self):
        job = {"company": "", "title": "Role"}
        self.assertFalse(stamp_company_key(job))
        self.assertNotIn("company_key", job)


class TestBackfillCompanyKeys(unittest.TestCase):
    def test_idempotent_one_pass(self):
        jobs = [
            {"id": "a", "company": "Bright Vision", "title": "DE"},
            {
                "id": "b",
                "company": "Bright Vision Technologies",
                "title": "ML Engineer",
                "company_key": "stale",
            },
            {"id": "c", "company": "Acme Inc.", "company_key": "acme"},
            {"id": "d", "company": ""},
        ]
        data = {"jobs": jobs}
        changed = backfill_company_keys(data)
        self.assertEqual(changed, 2)
        self.assertEqual(jobs[0]["company"], "Bright Vision")
        self.assertEqual(jobs[0]["company_key"], "brightvision")
        self.assertEqual(jobs[1]["company"], "Bright Vision Technologies")
        self.assertEqual(jobs[1]["company_key"], "brightvision")
        self.assertEqual(jobs[2]["company_key"], "acme")
        self.assertNotIn("company_key", jobs[3])
        self.assertEqual(backfill_company_keys(data), 0)

    def test_script_uses_single_jobs_lock_and_is_idempotent(self):
        import backfill_company_key as mod

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs_file = root / "jobs.json"
            lock_file = root / "jobs.json.lock"
            payload = {
                "revision": 1,
                "jobs": [
                    {
                        "id": "bv-de",
                        "company": "Bright Vision",
                        "title": "Data Engineer",
                    },
                    {
                        "id": "bv-ml",
                        "company": "Bright Vision Technologies",
                        "title": "ML Engineer",
                    },
                ],
            }
            jobs_file.write_text(json.dumps(payload), encoding="utf-8")
            with (
                mock.patch.object(mod, "JOBS_FILE", jobs_file),
                mock.patch.object(mod.jobs_lock, "JOBS_FILE", jobs_file),
                mock.patch.object(mod.jobs_lock, "LOCK_FILE", lock_file),
            ):
                self.assertEqual(mod.main(), 0)
                self.assertEqual(mod.main(), 0)
            data = json.loads(jobs_file.read_text(encoding="utf-8"))
            keys = {j["id"]: j for j in data["jobs"]}
            self.assertEqual(keys["bv-de"]["company"], "Bright Vision")
            self.assertEqual(keys["bv-ml"]["company"], "Bright Vision Technologies")
            self.assertEqual(keys["bv-de"]["company_key"], "brightvision")
            self.assertEqual(keys["bv-ml"]["company_key"], "brightvision")
            self.assertNotEqual(keys["bv-de"]["id"], keys["bv-ml"]["id"])


class TestDoesNotMergeJobRows(unittest.TestCase):
    def test_different_titles_keep_distinct_ids(self):
        a = {
            "company": "Bright Vision",
            "title": "Data Engineer",
            "apply_url": "https://example.test/jobs/de",
        }
        b = {
            "company": "Bright Vision Technologies",
            "title": "ML Engineer",
            "apply_url": "https://example.test/jobs/ml",
        }
        existing = [
            {
                "id": "bright-vision-data-engineer",
                "company": a["company"],
                "title": a["title"],
                "apply_url": a["apply_url"],
                "status": "discovered",
            }
        ]
        self.assertIsNone(find_existing_match(b, existing))
        id_a = generated_job_id(a, existing_ids=set(), blocked_ids=set())
        id_b = generated_job_id(b, existing_ids={id_a}, blocked_ids=set())
        self.assertNotEqual(id_a, id_b)
        self.assertEqual(id_a, "bright-vision-data-engineer")
        self.assertEqual(id_b, "bright-vision-technologies-ml-engineer")


class TestWritersStampCompanyKey(unittest.TestCase):
    def test_discovery_and_recovery_call_stamp(self):
        write_src = Path(__file__).with_name("write_discovered_jobs.py").read_text(
            encoding="utf-8"
        )
        recover_src = Path(__file__).with_name("recover_jobs_json.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("stamp_company_key(entry)", write_src)
        self.assertIn("stamp_company_key(existing_match)", write_src)
        self.assertIn("stamp_company_key(entry)", recover_src)


if __name__ == "__main__":
    unittest.main()
