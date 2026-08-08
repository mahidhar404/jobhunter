#!/usr/bin/env python3
"""Fixture tests for scrape_adzuna (no network)."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_adzuna as sa  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_normalize_results(self):
        data = {
            "results": [
                {
                    "title": "Machine Learning Engineer",
                    "company": {"display_name": "Acme India"},
                    "location": {"display_name": "Bengaluru, Karnataka"},
                    "created": "2026-08-01T10:00:00Z",
                    "redirect_url": "https://www.adzuna.in/details/123",
                    "description": "Build ML systems in India.",
                },
                {"title": "No URL", "company": {}, "location": {}},  # dropped: no url
            ]
        }
        jobs = sa.normalize_results(data, search_term="machine learning")
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["site"], "adzuna")
        self.assertEqual(j["company"], "Acme India")
        self.assertEqual(j["location"], "Bengaluru, Karnataka")
        self.assertEqual(j["date_posted"], "2026-08-01")
        self.assertEqual(j["job_url"], "https://www.adzuna.in/details/123")
        self.assertTrue(j["search_term"].startswith("india:adzuna"))

    def test_normalize_bad_input(self):
        self.assertEqual(sa.normalize_results(None), [])
        self.assertEqual(sa.normalize_results({}), [])
        self.assertEqual(sa.normalize_results({"results": []}), [])


class SkipWithoutKeysTests(unittest.TestCase):
    def test_main_skips_cleanly_without_keys(self):
        out = ROOT / "listings" / "_test_adzuna_skip.json"
        argv = ["scrape_adzuna.py", "--out", str(out)]
        env = {k: v for k, v in os.environ.items()
               if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")}
        try:
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(sa, "load_web_keys", return_value={}), \
                 mock.patch.object(sa, "fetch_json") as fj:
                sa.main()
                fj.assert_not_called()  # never hit the network without keys
            self.assertTrue(out.exists())
            self.assertEqual(json.loads(out.read_text()), [])
        finally:
            if out.exists():
                out.unlink()

    def test_resolve_keys_prefers_env(self):
        with mock.patch.dict(os.environ,
                             {"ADZUNA_APP_ID": "eid", "ADZUNA_APP_KEY": "ekey"}), \
             mock.patch.object(sa, "load_web_keys",
                               return_value={"adzuna_app_id": "fid"}):
            self.assertEqual(sa._resolve_keys(), ("eid", "ekey"))
        with mock.patch.dict(os.environ,
                             {k: v for k, v in os.environ.items()
                              if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")},
                             clear=True), \
             mock.patch.object(sa, "load_web_keys",
                               return_value={"adzuna_app_id": "fid",
                                             "adzuna_app_key": "fkey"}):
            self.assertEqual(sa._resolve_keys(), ("fid", "fkey"))


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
