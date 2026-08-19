#!/usr/bin/env python3
"""Unit tests for LinkedIn/aggregator → company ATS apply-URL resolution.

No live search or network. Search/fetch are injected fakes.

Run:
  python3 scripts/test_resolve_apply_urls.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import resolve_apply_urls as rau  # noqa: E402
from apply_urls import is_aggregator_url, is_known_ats_url  # noqa: E402
from jd_fingerprint import jd_fingerprint, normalize_jd_text  # noqa: E402


LINKEDIN_URL = "https://www.linkedin.com/jobs/view/4452248501"
JAZZHR_URL = "https://emedlabsllc.applytojob.com/apply/FI24qAupbj/Analytics-Engineer"
WRONG_ATS_URL = "https://boards.greenhouse.io/acmecorp/jobs/999001"

# Distinctive in-hand JD (LinkedIn-style chrome + unique phrases).
EMED_LINKEDIN_JD = """
Analytics Engineer — eMed (LinkedIn)
Posted 2 weeks ago · 100+ applicants · Sign in to apply

We're hiring an Analytics Engineer to own our dbt models and Looker explores
for the at-home lab diagnostics platform. You will partner with clinicians to
ship trustworthy metrics for COVID testing volume and care-kit fulfillment.

Requirements: 3+ years of SQL, dbt, Looker, and Snowflake. Experience with
healthcare data privacy and HIPAA-aware warehouse design is a plus.

This listing is syndicated on LinkedIn; the real application is offsite.
""" + (" Additional context about analytics engineering at a diagnostics lab. " * 8)

# ATS/JazzHR page text — same posting, different chrome. Must NOT hash-equal.
EMED_ATS_JD = """
eMed Labs LLC
Analytics Engineer

Join eMed Labs to own dbt models and Looker explores for the at-home lab
diagnostics platform. Partner with clinicians to ship trustworthy metrics
for COVID testing volume and care-kit fulfillment.

What you'll do
- Model clinical and fulfillment data in Snowflake
- Maintain Looker explores used by ops and clinicians

Requirements
SQL, dbt, Looker, Snowflake. Healthcare data privacy familiarity welcome.
""" + (" JazzHR hosted application form follows this description. " * 8)

GENERIC_ATS_JD = """
Analytics Engineer
Acme Corp is hiring an Analytics Engineer to join our data platform team.
You will write SQL, build dashboards, and work with stakeholders.
Requirements: SQL, Python, cloud warehouses, communication skills.
""" + (" Generic analytics engineer posting filler text. " * 12)


def _emed_job(**extra) -> dict:
    job = {
        "id": "fixture-emed-analytics-engineer",
        "company": "eMed",
        "title": "Analytics Engineer",
        "apply_url": LINKEDIN_URL,
        "job_url": LINKEDIN_URL,
        "source_url": LINKEDIN_URL,
        "job_description": EMED_LINKEDIN_JD,
        "status": "discovered",
    }
    job.update(extra)
    return job


class TestQueryBuilder(unittest.TestCase):
    def test_queries_use_company_and_title_not_just_applytojob_slug(self):
        qs = rau.build_search_queries("eMed", "Analytics Engineer")
        self.assertTrue(qs)
        blob = " ".join(qs).lower()
        self.assertIn("emed", blob)
        self.assertIn("analytics engineer", blob)
        # Must not be *only* a guessed {company}.applytojob.com host.
        slug_only = all("applytojob.com" in q.lower() and "analytics" not in q.lower() for q in qs)
        self.assertFalse(slug_only)
        self.assertFalse(any(q.lower().strip() == "emed.applytojob.com" for q in qs))

    def test_empty_company_or_title_yields_no_queries(self):
        self.assertEqual(rau.build_search_queries("", "Analytics Engineer"), [])
        self.assertEqual(rau.build_search_queries("eMed", ""), [])


class TestCandidateUrlFilter(unittest.TestCase):
    def test_keeps_known_ats_drops_linkedin(self):
        urls = [
            LINKEDIN_URL,
            JAZZHR_URL,
            "https://boards.greenhouse.io/acme/jobs/1",
            "https://jobs.lever.co/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "https://jobs.ashbyhq.com/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "https://www.indeed.com/viewjob?jk=abc",
        ]
        kept = rau.filter_candidate_urls(urls)
        self.assertIn(JAZZHR_URL, kept)
        self.assertTrue(any("greenhouse.io" in u for u in kept))
        self.assertTrue(any("lever.co" in u for u in kept))
        self.assertTrue(any("ashbyhq.com" in u for u in kept))
        self.assertFalse(any(is_aggregator_url(u) for u in kept))
        self.assertTrue(all(is_known_ats_url(u) for u in kept))

    def test_drops_workday_and_icims_unfetchable(self):
        urls = [
            "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/x",
            "https://acme.icims.com/jobs/123/job",
            JAZZHR_URL,
        ]
        kept = rau.filter_candidate_urls(urls)
        self.assertEqual(kept, [JAZZHR_URL])
        self.assertFalse(rau.is_fetchable_ats_url(urls[0]))
        self.assertFalse(rau.is_fetchable_ats_url(urls[1]))

    def test_dedupes_normalized_urls(self):
        a = JAZZHR_URL
        b = JAZZHR_URL + "?utm_source=linkedin"
        kept = rau.filter_candidate_urls([a, b])
        self.assertEqual(len(kept), 1)


class TestCompanyTitleMatch(unittest.TestCase):
    def test_emed_aliases_emedlabsllc_slug(self):
        self.assertTrue(rau.companies_match("eMed", "eMed Labs LLC"))
        self.assertTrue(rau.company_matches_url("eMed", JAZZHR_URL))
        self.assertTrue(rau.companies_match("eMed", "emedlabsllc"))

    def test_wrong_company_rejected(self):
        self.assertFalse(rau.companies_match("eMed", "Acme Corp"))
        self.assertFalse(rau.company_matches_url("eMed", WRONG_ATS_URL))

    def test_title_matches_slug_and_near_variant(self):
        self.assertTrue(rau.titles_match("Analytics Engineer", "Analytics Engineer"))
        self.assertTrue(rau.titles_match("Analytics Engineer", JAZZHR_URL))
        self.assertTrue(
            rau.titles_match("Analytics Engineer", "Analytics Engineer, Data Platform")
        )
        self.assertFalse(rau.titles_match("Analytics Engineer", "Staff Backend Engineer"))


class TestJdOverlap(unittest.TestCase):
    def test_linkedin_vs_ats_overlap_high_despite_unequal_hash(self):
        # Exact fingerprint must NOT be the accept gate.
        self.assertNotEqual(jd_fingerprint(EMED_LINKEDIN_JD), jd_fingerprint(EMED_ATS_JD))
        self.assertNotEqual(normalize_jd_text(EMED_LINKEDIN_JD), normalize_jd_text(EMED_ATS_JD))
        score = rau.jd_overlap_score(EMED_LINKEDIN_JD, EMED_ATS_JD)
        self.assertGreaterEqual(score, rau.HIGH_OVERLAP)

    def test_unrelated_jd_is_low_overlap(self):
        score = rau.jd_overlap_score(EMED_LINKEDIN_JD, GENERIC_ATS_JD)
        self.assertLess(score, rau.HIGH_OVERLAP)


class TestScoreCandidate(unittest.TestCase):
    def test_gold_emed_jazzhr_is_high(self):
        result = rau.score_candidate(
            job=_emed_job(),
            url=JAZZHR_URL,
            page={
                "title": "Analytics Engineer",
                "company": "eMed Labs LLC",
                "description": EMED_ATS_JD,
            },
        )
        self.assertEqual(result["confidence"], "high")
        self.assertTrue(result["title_match"])
        self.assertTrue(result["company_match"])

    def test_wrong_company_same_title_is_low(self):
        result = rau.score_candidate(
            job=_emed_job(),
            url=WRONG_ATS_URL,
            page={
                "title": "Analytics Engineer",
                "company": "Acme Corp",
                "description": GENERIC_ATS_JD,
            },
        )
        self.assertEqual(result["confidence"], "low")
        self.assertFalse(result["company_match"])

    def test_title_and_ats_but_weak_jd_is_medium_not_high(self):
        result = rau.score_candidate(
            job=_emed_job(),
            url=JAZZHR_URL,
            page={
                "title": "Analytics Engineer",
                "company": "eMed Labs LLC",
                "description": GENERIC_ATS_JD,
            },
        )
        self.assertEqual(result["confidence"], "medium")

    def test_linkedin_url_is_low(self):
        result = rau.score_candidate(
            job=_emed_job(),
            url=LINKEDIN_URL,
            page={"title": "Analytics Engineer", "company": "eMed", "description": EMED_ATS_JD},
        )
        self.assertEqual(result["confidence"], "low")


class TestNeedsResolution(unittest.TestCase):
    def test_linkedin_open_job_needs_resolution(self):
        self.assertTrue(rau.needs_apply_resolution(_emed_job()))

    def test_already_ats_apply_url_skipped(self):
        job = _emed_job(apply_url=JAZZHR_URL, job_url=LINKEDIN_URL)
        self.assertFalse(rau.needs_apply_resolution(job))

    def test_easy_apply_tagged_skipped(self):
        job = _emed_job(deleted_reason="easy_apply", status="deleted")
        self.assertFalse(rau.needs_apply_resolution(job))
        job2 = _emed_job(status="skipped_easy_apply")
        self.assertFalse(rau.needs_apply_resolution(job2))
        job3 = _emed_job(easy_apply=True)
        self.assertFalse(rau.needs_apply_resolution(job3))

    def test_already_high_resolution_skipped(self):
        job = _emed_job(
            apply_url=JAZZHR_URL,
            apply_url_resolution={"confidence": "high", "url": JAZZHR_URL},
        )
        self.assertFalse(rau.needs_apply_resolution(job))


class TestMergeResolvedApply(unittest.TestCase):
    def test_high_confidence_upgrades_apply_keeps_linkedin(self):
        job = _emed_job()
        out = rau.merge_resolved_apply(job, JAZZHR_URL)
        self.assertEqual(out["apply_url"], JAZZHR_URL)
        self.assertTrue(is_aggregator_url(out["job_url"]) or is_aggregator_url(out.get("source_url")))
        kept = " ".join(
            [out.get("job_url") or "", out.get("source_url") or ""]
            + list(out.get("alternate_urls") or [])
        )
        self.assertIn("linkedin.com", kept)
        self.assertTrue(is_known_ats_url(out["apply_url"]))

    def test_medium_does_not_overwrite_apply_url(self):
        job = _emed_job()
        scored = {
            "confidence": "medium",
            "url": JAZZHR_URL,
            "score": 0.12,
        }
        out = rau.apply_scored_resolution(job, scored)
        self.assertEqual(out["apply_url"], LINKEDIN_URL)
        self.assertEqual(out["apply_url_resolution"]["confidence"], "medium")
        self.assertEqual(out["apply_url_resolution"]["url"], JAZZHR_URL)

    def test_low_leaves_job_as_is(self):
        job = _emed_job()
        before = json.dumps(job, sort_keys=True)
        out = rau.apply_scored_resolution(job, {"confidence": "low", "url": JAZZHR_URL})
        self.assertEqual(out["apply_url"], LINKEDIN_URL)
        self.assertEqual(json.dumps(out, sort_keys=True), before)


class TestResolveJobEndToEndMocked(unittest.TestCase):
    def test_gold_path_upgrades_with_mocked_search_fetch(self):
        job = _emed_job()
        hits = [LINKEDIN_URL, JAZZHR_URL, WRONG_ATS_URL]

        def fake_search(_q):
            return list(hits)

        def fake_fetch(url):
            if url == JAZZHR_URL:
                return {
                    "title": "Analytics Engineer",
                    "company": "eMed Labs LLC",
                    "description": EMED_ATS_JD,
                }
            if url == WRONG_ATS_URL:
                return {
                    "title": "Analytics Engineer",
                    "company": "Acme Corp",
                    "description": GENERIC_ATS_JD,
                }
            return None

        with tempfile.TemporaryDirectory() as td:
            result = rau.resolve_job(
                job,
                search_fn=fake_search,
                fetch_fn=fake_fetch,
                write=False,
                resumes_dir=Path(td),
            )
            self.assertEqual(result["confidence"], "high")
            self.assertEqual(result["url"], JAZZHR_URL)
            self.assertEqual(job["apply_url"], LINKEDIN_URL)  # dry-run does not mutate

            result_w = rau.resolve_job(
                job,
                search_fn=fake_search,
                fetch_fn=fake_fetch,
                write=True,
                resumes_dir=Path(td),
            )
        self.assertEqual(result_w["confidence"], "high")
        self.assertEqual(job["apply_url"], JAZZHR_URL)
        self.assertIn("linkedin.com", (job.get("job_url") or "") + (job.get("source_url") or ""))

    def test_easy_apply_linkedin_only_does_not_upgrade(self):
        job = _emed_job(easy_apply=True)

        def fake_search(_q):
            return [LINKEDIN_URL]

        result = rau.resolve_job(job, search_fn=fake_search, fetch_fn=lambda u: None, write=True)
        self.assertEqual(result["confidence"], "low")
        self.assertEqual(job["apply_url"], LINKEDIN_URL)
        self.assertIn(result.get("reason"), ("easy_apply", "not_needed", "skipped"))

    def test_linkedin_only_search_hits_do_not_upgrade(self):
        job = _emed_job()

        def fake_search(_q):
            return [LINKEDIN_URL, "https://www.linkedin.com/jobs/view/other"]

        result = rau.resolve_job(job, search_fn=fake_search, fetch_fn=lambda u: None, write=True)
        self.assertEqual(result["confidence"], "low")
        self.assertEqual(job["apply_url"], LINKEDIN_URL)

    def test_no_search_backend_fails_soft(self):
        job = _emed_job()
        result = rau.resolve_job(
            job, search_fn=lambda q: [], fetch_fn=lambda u: None, write=True
        )
        self.assertEqual(result["confidence"], "low")
        self.assertEqual(job["apply_url"], LINKEDIN_URL)

    def test_stops_searching_once_ats_hit_exists(self):
        job = _emed_job()
        calls = []

        def fake_search(q):
            calls.append(q)
            return [JAZZHR_URL]

        def fake_fetch(url):
            return {
                "title": "Analytics Engineer",
                "company": "eMed Labs LLC",
                "description": EMED_ATS_JD,
            }

        with tempfile.TemporaryDirectory() as td:
            rau.resolve_job(
                job,
                search_fn=fake_search,
                fetch_fn=fake_fetch,
                write=False,
                resumes_dir=Path(td),
            )
        self.assertEqual(len(calls), 1)


class TestSelectJobsAndResume(unittest.TestCase):
    def test_selects_linkedin_aggregator_skips_ats_and_easy_apply(self):
        jobs = [
            _emed_job(id="li-open"),
            _emed_job(id="already-ats", apply_url=JAZZHR_URL),
            _emed_job(id="easy", easy_apply=True),
            {
                "id": "greenhouse",
                "company": "Acme",
                "title": "MLE",
                "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
                "status": "discovered",
            },
        ]
        selected = rau.select_jobs_for_resolution(jobs)
        ids = [j["id"] for j in selected]
        self.assertEqual(ids, ["li-open"])

    def test_progress_file_skips_done_ids(self):
        jobs = [_emed_job(id="a"), _emed_job(id="b")]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "progress.json"
            path.write_text(json.dumps({"done_ids": ["a"]}))
            selected = rau.select_jobs_for_resolution(jobs, progress_path=path)
            self.assertEqual([j["id"] for j in selected], ["b"])


class TestSearchHtmlParse(unittest.TestCase):
    def test_ddg_uddg_unwrap(self):
        html = (
            '<a rel="nofollow" class="result__a" '
            'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Femedlabsllc.applytojob.com'
            '%2Fapply%2FFI24qAupbj%2FAnalytics-Engineer&rut=abc">Analytics</a>'
        )
        urls = rau.parse_ddg_html(html)
        self.assertTrue(any("emedlabsllc.applytojob.com" in u for u in urls))
        self.assertFalse(any("google.com/search" in u for u in urls))

    def test_search_backends_empty_when_no_keys(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch.object(rau, "load_search_keys", return_value={}):
                backends = rau.available_search_backends(include_ddg=True)
        names = [b["name"] for b in backends]
        self.assertIn("duckduckgo", names)
        self.assertNotIn("brave", names)
        self.assertNotIn("google_cse", names)


class TestOptionalJazzhrSeed(unittest.TestCase):
    def test_extracts_applytojob_slug(self):
        self.assertEqual(rau.jazzhr_slug_from_url(JAZZHR_URL), "emedlabsllc")
        self.assertIsNone(rau.jazzhr_slug_from_url(LINKEDIN_URL))


if __name__ == "__main__":
    unittest.main()
