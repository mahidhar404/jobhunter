#!/usr/bin/env python3
"""Unit tests + fixture dry-run for apply URL preference / conservative dedup.

Run:
  python3 scripts/test_apply_urls.py
"""
from __future__ import annotations

import difflib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from apply_urls import (  # noqa: E402
    enrich_listing_urls,
    is_aggregator_url,
    is_ats_or_company_apply,
    is_known_ats_url,
    merge_listing_pair,
    normalize_url,
    prefer_apply_url,
    url_preference_rank,
    RANK_AGGREGATOR,
    RANK_KNOWN_ATS,
)
from dedup_jobs import fold_urls_into_winner  # noqa: E402
from dedup_listings import normalize_company, normalize_title  # noqa: E402


class TestNormalizeUrl(unittest.TestCase):
    def test_ashby_org_slug_casefold(self):
        a = "https://jobs.ashbyhq.com/Jerry.ai/71025a17-cf72-4152-9560-ec414571d2e2/application"
        b = "https://jobs.ashbyhq.com/jerry.ai/71025a17-cf72-4152-9560-ec414571d2e2/application"
        self.assertEqual(normalize_url(a), normalize_url(b))
        self.assertIn("/jerry.ai/", normalize_url(a))

    def test_path_casefold_generic(self):
        self.assertEqual(
            normalize_url("https://Careers.Example.com/Jobs/ABC"),
            normalize_url("https://careers.example.com/jobs/abc"),
        )


class TestFoldUrlsIntoWinner(unittest.TestCase):
    def test_does_not_reappend_existing_alts(self):
        winner = {
            "apply_url": "https://jobs.ashbyhq.com/jerry.ai/aaa/application",
            "job_url": "https://jobs.ashbyhq.com/jerry.ai/aaa",
            "alternate_urls": [
                "https://jobs.ashbyhq.com/Jerry.ai/bbb/application",
                "https://www.linkedin.com/jobs/view/1",
            ],
        }
        loser = {
            "apply_url": "https://jobs.ashbyhq.com/jerry.ai/bbb/application",
            "job_url": "https://jobs.ashbyhq.com/Jerry.ai/bbb",
            "alternate_urls": [
                "https://jobs.ashbyhq.com/jerry.ai/bbb",
                "https://jobs.ashbyhq.com/Jerry.ai/bbb/application",
            ],
        }
        before = list(winner["alternate_urls"])
        fold_urls_into_winner(winner, loser)
        # Existing alt still present; Jerry.ai/jerry.ai twins not duplicated
        ashby_alts = [
            u for u in winner["alternate_urls"]
            if "ashbyhq.com" in (u or "")
        ]
        norms = {normalize_url(u) for u in ashby_alts}
        self.assertEqual(len(ashby_alts), len(norms), msg=ashby_alts)
        # LinkedIn discovery preserved
        self.assertTrue(any("linkedin.com" in (u or "") for u in winner["alternate_urls"]))
        # Count of ashby alts should not grow from near-dupes already on winner
        self.assertLessEqual(len(ashby_alts), len(before) + 1)


class TestUrlClassify(unittest.TestCase):
    def test_aggregator(self):
        self.assertTrue(is_aggregator_url("https://www.linkedin.com/jobs/view/123"))
        self.assertTrue(is_aggregator_url("https://www.indeed.com/viewjob?jk=abc"))
        self.assertTrue(is_aggregator_url("https://www.glassdoor.com/job-listing/x"))
        self.assertFalse(is_aggregator_url("https://boards.greenhouse.io/acme/jobs/1"))

    def test_ats_or_company(self):
        self.assertTrue(is_known_ats_url("https://boards.greenhouse.io/acme/jobs/1"))
        self.assertTrue(is_ats_or_company_apply("https://jobs.lever.co/acme/uuid"))
        self.assertTrue(is_ats_or_company_apply("https://careers.example.com/jobs/1"))
        self.assertFalse(is_ats_or_company_apply("https://www.linkedin.com/jobs/view/1"))
        self.assertFalse(is_ats_or_company_apply(""))

    def test_ranks(self):
        self.assertEqual(url_preference_rank("https://jobs.ashbyhq.com/x/y"), RANK_KNOWN_ATS)
        self.assertEqual(url_preference_rank("https://linkedin.com/jobs/view/1"), RANK_AGGREGATOR)


class TestPreferApplyUrl(unittest.TestCase):
    def test_prefer_ats_over_aggregator(self):
        li = "https://www.linkedin.com/jobs/view/999"
        gh = "https://boards.greenhouse.io/acme/jobs/42"
        self.assertEqual(prefer_apply_url(li, gh), gh)
        self.assertEqual(prefer_apply_url(gh, li), gh)

    def test_only_aggregator_keeps_it(self):
        li = "https://www.linkedin.com/jobs/view/999"
        self.assertEqual(prefer_apply_url(li), li)
        self.assertEqual(prefer_apply_url(None, "", li), li)

    def test_never_none_when_url_present(self):
        self.assertIsNotNone(prefer_apply_url("https://indeed.com/viewjob?jk=1"))
        self.assertIsNone(prefer_apply_url(None, "", "nan"))

    def test_never_overwrite_ats_with_aggregator_via_order(self):
        ats = "https://nvidia.wd5.myworkdayjobs.com/en-US/job/x"
        agg = "https://www.indeed.com/viewjob?jk=1"
        self.assertEqual(prefer_apply_url(ats, agg), ats)
        self.assertEqual(prefer_apply_url(agg, ats), ats)


class TestEnrich(unittest.TestCase):
    def test_jobspy_direct_upgrades_apply(self):
        item = {
            "job_url": "https://www.linkedin.com/jobs/view/1",
            "job_url_direct": "https://boards.greenhouse.io/acme/jobs/9",
            "description": "Apply today",
        }
        out = enrich_listing_urls(item)
        self.assertEqual(out["apply_url"], item["job_url_direct"])
        self.assertEqual(out["job_url"], item["job_url"])
        self.assertTrue(out["source_url"] == item["job_url"] or item["job_url"] in out["alternate_urls"])

    def test_description_ats_link(self):
        item = {
            "job_url": "https://www.indeed.com/viewjob?jk=abc",
            "description": "Apply at https://jobs.lever.co/acme/abcd-efgh please",
        }
        out = enrich_listing_urls(item)
        self.assertTrue(out["apply_url"].startswith("https://jobs.lever.co/"))

    def test_failure_keeps_aggregator(self):
        item = {"job_url": "https://www.linkedin.com/jobs/view/42"}
        out = enrich_listing_urls(item)
        self.assertEqual(out["apply_url"], item["job_url"])
        self.assertTrue(out["apply_url"])


class TestMergeFixture(unittest.TestCase):
    def test_linkedin_plus_greenhouse_same_role(self):
        li = {
            "title": "Machine Learning Engineer",
            "company": "Acme Corp",
            "site": "linkedin",
            "job_url": "https://www.linkedin.com/jobs/view/111",
            "description": "short",
        }
        gh = {
            "title": "Machine Learning Engineer",
            "company": "Acme Corporation",
            "site": "greenhouse",
            "job_url": "https://boards.greenhouse.io/acme/jobs/222",
            "job_url_direct": "https://boards.greenhouse.io/acme/jobs/222",
            "description": "full JD " * 40,
        }
        self.assertEqual(normalize_company(li["company"]), normalize_company(gh["company"]))
        self.assertGreaterEqual(
            difflib.SequenceMatcher(
                None, normalize_title(li["title"]), normalize_title(gh["title"])
            ).ratio(),
            0.85,
        )
        merged = merge_listing_pair(li, gh)
        self.assertTrue(is_known_ats_url(merged["apply_url"]) or "greenhouse" in merged["apply_url"])
        self.assertIn("greenhouse.io", merged["apply_url"])
        # LinkedIn discovery preserved somewhere
        preserved = {merged.get("job_url"), merged.get("source_url"), *(merged.get("alternate_urls") or [])}
        self.assertTrue(any("linkedin.com" in (u or "") for u in preserved))

    def test_different_company_not_auto_merged_by_helpers(self):
        # Helpers don't decide company equality — caller must. Just ensure
        # prefer still works independently.
        a = prefer_apply_url(
            "https://www.linkedin.com/jobs/view/1",
            "https://boards.greenhouse.io/other/jobs/2",
        )
        self.assertIn("greenhouse", a)


def _dry_run_print() -> None:
    li = {
        "title": "Data Scientist",
        "company": "Example Inc",
        "site": "linkedin",
        "job_url": "https://www.linkedin.com/jobs/view/555",
        "description": "x",
    }
    gh = {
        "title": "Data Scientist",
        "company": "Example",
        "site": "greenhouse",
        "job_url": "https://boards.greenhouse.io/example/jobs/777",
        "description": "long description here",
    }
    merged = merge_listing_pair(li, gh)
    print("dry-run merge LinkedIn+GH →")
    print(f"  apply_url: {merged.get('apply_url')}")
    print(f"  job_url:   {merged.get('job_url')}")
    print(f"  source_url:{merged.get('source_url')}")
    print(f"  alts:      {merged.get('alternate_urls')}")


if __name__ == "__main__":
    _dry_run_print()
    unittest.main(verbosity=2)
