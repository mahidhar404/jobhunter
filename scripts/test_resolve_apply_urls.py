#!/usr/bin/env python3
"""Unit tests for LinkedIn/aggregator → company ATS apply-URL resolution.

No live search or network. Search/fetch are injected fakes.

Run:
  python3 scripts/test_resolve_apply_urls.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import contextlib

import resolve_apply_urls as rau  # noqa: E402
from apply_urls import is_aggregator_url, is_known_ats_url  # noqa: E402
from jd_fingerprint import jd_fingerprint, normalize_jd_text  # noqa: E402

# Public-search unit tests stay offline — never launch LinkedIn CfT profile.
rau.try_linkedin_session_resolve = lambda *a, **k: None  # type: ignore[assignment]


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
        # Google-style company-first query comes first.
        self.assertTrue(qs[0].lower().startswith("emed"))
        self.assertTrue(any("site:jobs.ashbyhq.com" in q.lower() for q in qs))
        # Must not be *only* a guessed {company}.applytojob.com host.
        slug_only = all("applytojob.com" in q.lower() and "analytics" not in q.lower() for q in qs)
        self.assertFalse(slug_only)
        self.assertFalse(any(q.lower().strip() == "emed.applytojob.com" for q in qs))

    def test_empty_company_or_title_yields_no_queries(self):
        self.assertEqual(rau.build_search_queries("", "Analytics Engineer"), [])
        self.assertEqual(rau.build_search_queries("eMed", ""), [])

    def test_mintlify_queries_are_company_first(self):
        qs = rau.build_search_queries("Mintlify", "Applied AI Engineer")
        self.assertEqual(qs[0], "Mintlify Applied AI Engineer")
        blob = " ".join(qs).lower()
        self.assertIn("ashbyhq", blob)


class TestAtsBoardSearch(unittest.TestCase):
    MINTLIFY_ASHBY = (
        "https://jobs.ashbyhq.com/Mintlify/ec55d98f-6e94-4ffb-9a55-4adad39297c3"
    )

    def test_company_slug_candidates_include_compact_forms(self):
        slugs = rau.company_ats_slug_candidates("Mintlify")
        self.assertIn("Mintlify", slugs)
        self.assertIn("mintlify", [s.lower() for s in slugs])

    def test_search_ats_boards_matches_title_from_mocked_postings(self):
        def fake_fetch(ats, slug):
            if ats == "ashby" and slug.lower() == "mintlify":
                return [
                    {
                        "title": "Applied AI Engineer",
                        "url": self.MINTLIFY_ASHBY,
                        "company": "Mintlify",
                    },
                    {
                        "title": "Backend Engineer",
                        "url": "https://jobs.ashbyhq.com/Mintlify/other",
                        "company": "Mintlify",
                    },
                ]
            return []

        with patch.object(rau, "fetch_ats_board_postings", side_effect=fake_fetch), \
             patch.object(
                 rau,
                 "registry_slugs_matching_company",
                 return_value=[("ashby", "Mintlify")],
             ):
            urls = rau.search_ats_boards("Mintlify", "Applied AI Engineer")
        self.assertEqual(urls, [self.MINTLIFY_ASHBY])

    def test_resolve_job_board_api_upgrades_mintlify_without_html_search(self):
        job = {
            "id": "mintlify-applied-ai-engineer-2",
            "company": "Mintlify",
            "title": "Applied AI Engineer",
            "apply_url": LINKEDIN_URL,
            "job_url": LINKEDIN_URL,
            "status": "discovered",
            "job_description": "Applied AI Engineer at Mintlify building docs AI.",
        }
        search_calls: list[str] = []

        def fake_search(q):
            search_calls.append(q)
            return ["https://www.applied.com/"]

        def fake_board(company, title):
            self.assertEqual(company, "Mintlify")
            self.assertEqual(title, "Applied AI Engineer")
            return [self.MINTLIFY_ASHBY]

        def fake_fetch(url):
            self.assertIn("ashbyhq.com", url)
            return {
                "title": "Applied AI Engineer",
                "company": "Mintlify",
                "description": "Applied AI Engineer at Mintlify building docs AI.",
            }

        with tempfile.TemporaryDirectory() as td:
            result = rau.resolve_job(
                job,
                search_fn=fake_search,
                fetch_fn=fake_fetch,
                board_search_fn=fake_board,
                write=True,
                resumes_dir=Path(td),
                linkedin_session=False,
            )
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["url"], self.MINTLIFY_ASHBY)
        self.assertEqual(result.get("reason"), "ats_board_api")
        self.assertEqual(job["apply_url"], self.MINTLIFY_ASHBY)
        # Board hit short-circuits HTML SERP backends.
        self.assertEqual(search_calls, [])

    def test_resolve_job_board_api_promotes_low_when_company_host_misses(self):
        """Workable/etc. board hits stay high even if company_host gate fails."""
        workable = "https://apply.workable.com/innovaccer-analytics/j/643DCD9804/"
        job = {
            "id": "innovaccer-ai-eng",
            "company": "Innovaccer",
            "title": "Artificial Intelligence Engineer",
            "apply_url": LINKEDIN_URL,
            "job_url": LINKEDIN_URL,
            "status": "discovered",
        }

        def fake_board(company, title):
            return [workable]

        def fake_fetch(url):
            # Unrelated page company + host miss → score_candidate would be low.
            return {
                "title": "Artificial Intelligence Engineer",
                "company": "Unrelated Staffing LLC",
                "description": "",
            }

        with tempfile.TemporaryDirectory() as td, \
             patch.object(rau, "company_matches_url", return_value=False):
            result = rau.resolve_job(
                job,
                search_fn=lambda _q: [],
                fetch_fn=fake_fetch,
                board_search_fn=fake_board,
                write=False,
                resumes_dir=Path(td),
                linkedin_session=False,
            )
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["url"], workable)
        self.assertEqual(result.get("reason"), "ats_board_api")

    def test_mock_google_style_serp_with_ashby_is_high_confidence(self):
        """HTML/search backends returning ashbyhq.com → high-confidence upgrade."""
        job = {
            "id": "mintlify-li",
            "company": "Mintlify",
            "title": "Applied AI Engineer",
            "apply_url": LINKEDIN_URL,
            "job_url": LINKEDIN_URL,
            "status": "discovered",
            "job_description": "Ship Applied AI features for Mintlify docs.",
        }

        def fake_search(_q):
            return [
                "https://www.applied.com/",
                "https://www.linkedin.com/jobs/view/1",
                self.MINTLIFY_ASHBY,
                "https://www.mintlify.com/careers",
            ]

        def fake_fetch(url):
            if "ashbyhq.com" not in url:
                return {"title": "Home", "company": "Other", "description": "nope"}
            return {
                "title": "Applied AI Engineer",
                "company": "Mintlify",
                "description": "Ship Applied AI features for Mintlify docs.",
            }

        with tempfile.TemporaryDirectory() as td:
            result = rau.resolve_job(
                job,
                search_fn=fake_search,
                fetch_fn=fake_fetch,
                board_search_fn=lambda *_: [],
                write=True,
                resumes_dir=Path(td),
                linkedin_session=False,
            )
        self.assertEqual(result["confidence"], "high")
        self.assertIn("ashbyhq.com", result["url"])
        self.assertEqual(job["apply_url"], self.MINTLIFY_ASHBY)

    def test_ashby_uuid_counts_as_specific_job_id(self):
        self.assertTrue(rau._specific_job_id_in_url(self.MINTLIFY_ASHBY))
        self.assertTrue(
            rau._specific_job_id_in_url(
                "https://jobs.lever.co/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            )
        )


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
        self.assertTrue(all(is_known_ats_url(u) or rau.is_acceptable_resolve_target(u) for u in kept))

    def test_keeps_company_careers_job_url(self):
        coinbase = (
            "https://www.coinbase.com/careers/positions/8024880?gh_jid=8024880"
        )
        kept = rau.filter_candidate_urls([LINKEDIN_URL, coinbase, "https://coinbase.com/"])
        self.assertIn(coinbase, kept)
        self.assertFalse(any(u.rstrip("/").endswith("coinbase.com") for u in kept))

    def test_drops_workingnomads_aggregator(self):
        wn = "https://www.workingnomads.com/jobs/senior-ai-engineer-lemonio-1734670"
        kept = rau.filter_candidate_urls([wn, JAZZHR_URL])
        self.assertEqual(kept, [JAZZHR_URL])
        self.assertFalse(rau.is_acceptable_resolve_target(wn))

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

    def test_careers_subdomain_still_matches_company(self):
        """careers.airbnb.com / jobs.greystar.com — not just the first label."""
        self.assertTrue(
            rau.company_matches_url(
                "Airbnb",
                "https://careers.airbnb.com/positions/8130355?gh_jid=8130355",
            )
        )
        self.assertTrue(
            rau.company_matches_url(
                "Greystar",
                "https://jobs.greystar.com/job/-/-/35302/99087128096",
            )
        )
        self.assertTrue(
            rau.company_matches_url(
                "Microsoft",
                "https://apply.careers.microsoft.com/careers/job/1970393556949556",
            )
        )

    def test_title_matches_slug_and_near_variant(self):
        self.assertTrue(rau.titles_match("Analytics Engineer", "Analytics Engineer"))
        self.assertTrue(rau.titles_match("Analytics Engineer", JAZZHR_URL))
        self.assertTrue(
            rau.titles_match("Analytics Engineer", "Analytics Engineer, Data Platform")
        )
        self.assertFalse(rau.titles_match("Analytics Engineer", "Staff Backend Engineer"))


class TestAtsSlugCandidates(unittest.TestCase):
    def test_multi_word_company_yields_url_safe_slugs_only(self):
        slugs = rau.company_ats_slug_candidates("Paradigm Operations LP-AL")
        self.assertTrue(slugs)
        self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", s) for s in slugs))
        self.assertFalse(any(" " in s for s in slugs))
        self.assertIn("paradigm", [s.lower() for s in slugs])


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

    def test_title_and_ats_host_match_is_high_even_with_weak_jd(self):
        # Company slug in ATS host + title match → high (JD fetch often blocked).
        result = rau.score_candidate(
            job=_emed_job(),
            url=JAZZHR_URL,
            page={
                "title": "Analytics Engineer",
                "company": "eMed Labs LLC",
                "description": GENERIC_ATS_JD,
            },
        )
        self.assertEqual(result["confidence"], "high")

    def test_title_match_without_company_host_stays_medium_on_weak_jd(self):
        # Page company matches but URL host/path does not → still need JD overlap.
        result = rau.score_candidate(
            job=_emed_job(),
            url="https://boards.greenhouse.io/unrelatedboard/jobs/12345",
            page={
                "title": "Analytics Engineer",
                "company": "eMed Labs LLC",
                "description": GENERIC_ATS_JD,
            },
        )
        self.assertEqual(result["confidence"], "medium")
        self.assertTrue(result["company_match"])
        self.assertFalse(rau.company_matches_url("eMed", result["url"]))

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
                board_search_fn=lambda *_: [],
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
                board_search_fn=lambda *_: [],
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

        result = rau.resolve_job(job, search_fn=fake_search, fetch_fn=lambda u: None,
                board_search_fn=lambda *_: [], write=True)
        self.assertEqual(result["confidence"], "low")
        self.assertEqual(job["apply_url"], LINKEDIN_URL)
        self.assertIn(result.get("reason"), ("easy_apply", "not_needed", "skipped"))

    def test_linkedin_only_search_hits_do_not_upgrade(self):
        job = _emed_job()

        def fake_search(_q):
            return [LINKEDIN_URL, "https://www.linkedin.com/jobs/view/other"]

        result = rau.resolve_job(job, search_fn=fake_search, fetch_fn=lambda u: None,
                board_search_fn=lambda *_: [], write=True)
        self.assertEqual(result["confidence"], "low")
        self.assertEqual(job["apply_url"], LINKEDIN_URL)

    def test_no_search_backend_fails_soft(self):
        job = _emed_job()
        result = rau.resolve_job(
            job, search_fn=lambda q: [], fetch_fn=lambda u: None,
                board_search_fn=lambda *_: [], write=True
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
                board_search_fn=lambda *_: [],
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
        self.assertIn("bing", names)
        self.assertIn("brave_html", names)
        self.assertNotIn("brave", names)
        self.assertNotIn("google_cse", names)


class TestOptionalJazzhrSeed(unittest.TestCase):
    def test_extracts_applytojob_slug(self):
        self.assertEqual(rau.jazzhr_slug_from_url(JAZZHR_URL), "emedlabsllc")
        self.assertIsNone(rau.jazzhr_slug_from_url(LINKEDIN_URL))


class TestApplyResolvePersistence(unittest.TestCase):
    def test_classify_status_for_common_reasons(self):
        self.assertEqual(
            rau.classify_apply_resolve_status(
                {"confidence": "high", "url": JAZZHR_URL, "reason": "linkedin_apply_href"}
            ),
            "ok",
        )
        self.assertEqual(
            rau.classify_apply_resolve_status({"confidence": "low", "reason": "easy_apply"}),
            "easy_apply",
        )
        self.assertEqual(
            rau.classify_apply_resolve_status(
                {"confidence": "low", "reason": "no_external_apply"}
            ),
            "no_external",
        )
        self.assertEqual(
            rau.classify_apply_resolve_status(
                {"confidence": "low", "reason": "not_logged_in"}
            ),
            "failed",
        )
        self.assertEqual(
            rau.classify_apply_resolve_status(
                {"confidence": "medium", "url": JAZZHR_URL, "reason": "medium_no_overwrite"}
            ),
            "skipped",
        )

    def test_set_apply_resolve_fields_success_clears_message(self):
        job = _emed_job(
            apply_resolve_status="failed",
            apply_resolve_reason="no_ats_host",
            apply_resolve_message="Search did not find a known ATS apply URL.",
        )
        changed = rau.set_apply_resolve_fields(
            job,
            {
                "confidence": "high",
                "url": JAZZHR_URL,
                "reason": "linkedin_apply_href",
                "method": "linkedin_http",
            },
        )
        self.assertTrue(changed)
        self.assertEqual(job["apply_resolve_status"], "ok")
        self.assertEqual(job["apply_resolve_reason"], "linkedin_apply_href")
        self.assertTrue(job.get("apply_resolve_at"))
        self.assertNotIn("apply_resolve_message", job)

    def test_set_apply_resolve_fields_idempotent(self):
        job = _emed_job()
        rau.set_apply_resolve_fields(
            job, {"confidence": "low", "reason": "no_external_apply"}
        )
        at = job["apply_resolve_at"]
        changed = rau.set_apply_resolve_fields(
            job, {"confidence": "low", "reason": "no_external_apply"}
        )
        self.assertFalse(changed)
        self.assertEqual(job["apply_resolve_at"], at)

    def test_sanitize_strips_cookie_like_messages(self):
        msg = rau.sanitize_apply_resolve_message("li_at=SECRETCOOKIE; path=/")
        self.assertIn("open_linkedin_resolve.sh", msg)
        self.assertNotIn("SECRETCOOKIE", msg)

    def test_resolve_job_write_stamps_failure_fields(self):
        job = _emed_job()
        with patch.object(rau, "try_linkedin_session_resolve", return_value=None):
            result = rau.resolve_job(
                job,
                search_fn=lambda q: [],
                board_search_fn=lambda *_: [],
                fetch_fn=lambda u: None,
                write=True,
                linkedin_session=True,
            )
        self.assertEqual(result.get("reason"), "no_ats_host")
        self.assertEqual(job["apply_resolve_status"], "failed")
        self.assertEqual(job["apply_resolve_reason"], "no_ats_host")
        self.assertTrue(job.get("apply_resolve_at"))

    def test_should_auto_resolve_skips_ats_and_terminal(self):
        ats = {
            "id": "a1",
            "status": "discovered",
            "apply_url": JAZZHR_URL,
        }
        self.assertFalse(rau.should_auto_resolve_job(ats))
        terminal = {
            "id": "a2",
            "status": "discovered",
            "apply_url": "https://www.indeed.com/viewjob?jk=1",
            "apply_resolve_status": "no_external",
        }
        self.assertFalse(rau.should_auto_resolve_job(terminal))
        ok_upgraded = {
            "id": "a3",
            "status": "discovered",
            "apply_url": JAZZHR_URL,
            "apply_resolve_status": "ok",
        }
        self.assertFalse(rau.should_auto_resolve_job(ok_upgraded))

    def test_should_auto_resolve_linkedin_unresolved(self):
        job = {
            "id": "li-new",
            "status": "discovered",
            "apply_url": LINKEDIN_URL,
            "job_url": LINKEDIN_URL,
        }
        self.assertTrue(rau.should_auto_resolve_job(job))
        # Terminal easy_apply still on LinkedIn → skip
        job["apply_resolve_status"] = "easy_apply"
        self.assertFalse(rau.should_auto_resolve_job(job))
        # ok but still LinkedIn → re-resolve
        job["apply_resolve_status"] = "ok"
        self.assertTrue(rau.should_auto_resolve_job(job))

    def test_resolve_discovery_apply_urls_http_many_mocked(self):
        greenhouse = "https://boards.greenhouse.io/acme/jobs/1"
        job = {
            "id": "li-disc-1",
            "status": "discovered",
            "company": "Acme",
            "title": "ML Engineer",
            "apply_url": LINKEDIN_URL,
            "job_url": LINKEDIN_URL,
            "created_at": "2026-08-20T12:00:00+00:00",
            "updated_at": "2026-08-20T12:00:00+00:00",
        }
        progress: list[tuple[int, int]] = []

        def fake_http_many(pairs, concurrency=20, **_kw):
            self.assertEqual(len(pairs), 1)
            self.assertEqual(pairs[0][0], "li-disc-1")
            self.assertGreaterEqual(concurrency, 1)
            return [{
                "id": "li-disc-1",
                "confidence": "high",
                "url": greenhouse,
                "reason": "linkedin_apply_href",
                "method": "linkedin_http",
                "score": 1.0,
            }]

        with patch.object(rau, "locked_jobs_for_read") as lr, \
             patch.object(rau, "persist_job_resolution") as persist:
            @contextlib.contextmanager
            def _read():
                yield {"jobs": [job]}
            lr.return_value = _read()
            persist.return_value = {**job, "apply_url": greenhouse}
            summary = rau.resolve_discovery_apply_urls(
                since_iso="2026-08-20T11:00:00+00:00",
                write=True,
                concurrency=20,
                http_many_fn=fake_http_many,
                progress_cb=lambda d, t: progress.append((d, t)),
            )
        self.assertEqual(summary["considered"], 1)
        self.assertEqual(summary["linkedin"], 1)
        self.assertEqual(summary["high"], 1)
        self.assertEqual(summary["upgraded"][0]["url"], greenhouse)
        persist.assert_called_once()
        self.assertIn((0, 1), progress)
        self.assertIn((1, 1), progress)

    def test_resolve_discovery_skips_already_resolved(self):
        job = {
            "id": "li-done",
            "status": "discovered",
            "apply_url": JAZZHR_URL,
            "apply_resolve_status": "ok",
            "created_at": "2026-08-20T12:00:00+00:00",
            "updated_at": "2026-08-20T12:00:00+00:00",
        }

        def boom_http(*_a, **_k):
            raise AssertionError("should not call http_many for ATS jobs")

        with patch.object(rau, "locked_jobs_for_read") as lr:
            @contextlib.contextmanager
            def _read():
                yield {"jobs": [job]}
            lr.return_value = _read()
            summary = rau.resolve_discovery_apply_urls(
                since_iso="2026-08-20T11:00:00+00:00",
                write=True,
                http_many_fn=boom_http,
            )
        self.assertEqual(summary["considered"], 0)

    def test_select_jobs_for_discovery_resolve_respects_limit(self):
        older = {
            "id": "old",
            "status": "discovered",
            "apply_url": LINKEDIN_URL,
            "created_at": "2026-08-01T00:00:00+00:00",
        }
        newer = {
            "id": "new",
            "status": "discovered",
            "apply_url": LINKEDIN_URL,
            "created_at": "2026-08-20T00:00:00+00:00",
        }
        selected = rau.select_jobs_for_discovery_resolve(
            [newer, older], since_iso=None, limit=1,
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["id"], "old")

    def test_prune_unresolved_failed_and_no_external_and_easy_apply(self):
        failed = _emed_job(
            id="u-fail",
            status="discovered",
            apply_resolve_status="failed",
            apply_resolve_reason="no_ats_host",
            apply_resolve_message="Search did not find a known ATS apply URL.",
            apply_resolve_search_attempted=True,
        )
        no_ext = _emed_job(
            id="u-noext",
            status="discovered",
            apply_resolve_status="no_external",
            apply_resolve_reason="no_external_apply",
            apply_resolve_search_attempted=True,
        )
        http_err = _emed_job(
            id="u-http",
            status="discovered",
            apply_resolve_status="failed",
            apply_resolve_reason="http_error",
            apply_resolve_message="http_429",
        )
        easy = _emed_job(
            id="u-easy",
            status="discovered",
            apply_resolve_status="easy_apply",
            apply_resolve_reason="easy_apply",
        )
        applied = _emed_job(
            id="u-applied",
            status="applied",
            apply_resolve_status="failed",
        )
        ok_ats = _emed_job(
            id="u-ok",
            status="discovered",
            apply_url=JAZZHR_URL,
            apply_resolve_status="failed",
        )
        self.assertTrue(rau.should_prune_unresolved_apply_url(failed))
        self.assertTrue(rau.should_prune_unresolved_apply_url(no_ext))
        # http_error without public search must NOT prune.
        self.assertFalse(rau.should_prune_unresolved_apply_url(http_err))
        self.assertTrue(rau.should_prune_unresolved_apply_url(easy))
        self.assertFalse(rau.should_prune_unresolved_apply_url(applied))
        self.assertFalse(rau.should_prune_unresolved_apply_url(ok_ats))

        self.assertTrue(rau.tombstone_unresolved_apply_url(failed))
        self.assertEqual(failed["status"], "deleted")
        self.assertEqual(failed["deleted_reason"], "unresolved_apply_url")
        self.assertTrue(failed.get("unresolved_apply_url"))
        self.assertIn("unresolved apply url", failed.get("status_detail", "").lower())
        self.assertNotIn("li_at=", failed.get("status_detail", ""))

        # Idempotent: already deleted → no re-prune
        self.assertFalse(rau.should_prune_unresolved_apply_url(failed))

    def test_persist_prunes_unresolved_and_tags(self):
        job = _emed_job(id="persist-unresolved", status="discovered")
        scored = {
            "confidence": "low",
            "url": None,
            "reason": "no_ats_host",
            "message": "Search did not find a known ATS apply URL.",
            "score": 0.0,
            "search_attempted": True,
        }

        @contextlib.contextmanager
        def _write():
            yield {"jobs": [job]}

        with patch.object(rau, "locked_jobs_for_write", return_value=_write()), \
             patch.object(rau, "_tombstone_url_block") as block:
            out = rau.persist_job_resolution("persist-unresolved", scored)
        self.assertIsNotNone(out)
        self.assertEqual(job["status"], "deleted")
        self.assertEqual(job["deleted_reason"], "unresolved_apply_url")
        self.assertTrue(job.get("unresolved_apply_url"))
        self.assertEqual(job["apply_resolve_status"], "failed")
        block.assert_called_once()

    def test_persist_success_clears_unresolved_tag(self):
        job = _emed_job(
            id="persist-ok",
            status="discovered",
            unresolved_apply_url=True,
            apply_resolve_status="failed",
        )
        scored = {
            "confidence": "high",
            "url": JAZZHR_URL,
            "reason": "linkedin_apply_href",
            "method": "linkedin_http",
            "score": 1.0,
        }

        @contextlib.contextmanager
        def _write():
            yield {"jobs": [job]}

        with patch.object(rau, "locked_jobs_for_write", return_value=_write()), \
             patch.object(rau, "_tombstone_url_block") as block:
            rau.persist_job_resolution("persist-ok", scored)
        self.assertEqual(job["status"], "discovered")
        self.assertNotIn("unresolved_apply_url", job)
        self.assertEqual(job["apply_url"], JAZZHR_URL)
        block.assert_not_called()



class TestSearchBeforePrune(unittest.TestCase):
    COINBASE_URL = (
        "https://www.coinbase.com/careers/positions/8024880?gh_jid=8024880"
    )
    WWR_URL = (
        "https://weworkremotely.com/remote-jobs/coinbase-analytics-engineer-gfco-analytics"
    )

    def test_linkedin_http_miss_attempts_public_search_before_fail(self):
        job = _emed_job()
        session = {
            "confidence": "low",
            "url": None,
            "reason": "no_external_apply",
            "method": "linkedin_http",
            "score": 0.0,
        }
        search_calls: list[str] = []

        def fake_search(q):
            search_calls.append(q)
            return [JAZZHR_URL]

        def fake_fetch(url):
            return {
                "title": "Analytics Engineer",
                "company": "eMed Labs LLC",
                "description": EMED_ATS_JD,
            }

        with tempfile.TemporaryDirectory() as td, \
             patch.object(rau, "try_linkedin_session_resolve", return_value=session):
            result = rau.resolve_job(
                job,
                search_fn=fake_search,
                board_search_fn=lambda *_: [],
                fetch_fn=fake_fetch,
                write=True,
                resumes_dir=Path(td),
                linkedin_session=True,
            )
        self.assertTrue(search_calls)
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(job["apply_url"], JAZZHR_URL)
        self.assertEqual(job["apply_resolve_status"], "ok")

    def test_mock_company_careers_search_hit_prevents_prune(self):
        job = {
            "id": "coinbase-gfco",
            "company": "Coinbase",
            "title": "Analytics Engineer, GFCO Analytics",
            "apply_url": self.WWR_URL,
            "job_url": self.WWR_URL,
            "status": "discovered",
            "job_description": "Analytics Engineer GFCO at Coinbase.",
        }

        def fake_search(_q):
            return [self.COINBASE_URL, self.WWR_URL]

        def fake_fetch(url):
            if "coinbase.com" in url:
                return {
                    "title": "Analytics Engineer, GFCO Analytics",
                    "company": "Coinbase",
                    "description": "",
                }
            return None

        result = rau.resolve_job(
            job,
            search_fn=fake_search,
                board_search_fn=lambda *_: [],
            fetch_fn=fake_fetch,
            write=False,
            linkedin_session=False,
        )
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["url"], self.COINBASE_URL)

        @contextlib.contextmanager
        def _write():
            yield {"jobs": [job]}

        with patch.object(rau, "locked_jobs_for_write", return_value=_write()), \
             patch.object(rau, "_tombstone_url_block") as block:
            rau.persist_job_resolution("coinbase-gfco", result)
        self.assertEqual(job["status"], "discovered")
        self.assertNotEqual(job.get("deleted_reason"), "unresolved_apply_url")
        self.assertNotIn("unresolved_apply_url", job)
        self.assertEqual(job["apply_url"], self.COINBASE_URL)
        self.assertEqual(job["apply_resolve_status"], "ok")
        block.assert_not_called()

    def test_discovery_linkedin_miss_searches_before_persist_fail(self):
        job = {
            "id": "li-miss-1",
            "status": "discovered",
            "company": "Acme",
            "title": "ML Engineer",
            "apply_url": LINKEDIN_URL,
            "job_url": LINKEDIN_URL,
            "created_at": "2026-08-20T12:00:00+00:00",
            "updated_at": "2026-08-20T12:00:00+00:00",
        }
        greenhouse = "https://boards.greenhouse.io/acme/jobs/1"
        search_called = {"n": 0}

        def fake_http_many(pairs, concurrency=20, **_kw):
            return [{
                "id": "li-miss-1",
                "confidence": "low",
                "url": None,
                "reason": "no_external_apply",
                "method": "linkedin_http",
                "score": 0.0,
            }]

        def fake_resolve(job_arg, **kw):
            search_called["n"] += 1
            self.assertFalse(kw.get("linkedin_session", True))
            return {
                "confidence": "high",
                "url": greenhouse,
                "reason": "public_search",
                "score": 1.0,
            }

        persisted: list = []

        def fake_persist(jid, scored):
            persisted.append((jid, scored))
            return {**job, "apply_url": scored.get("url")}

        with patch.object(rau, "locked_jobs_for_read") as lr, \
             patch.object(rau, "persist_job_resolution", side_effect=fake_persist):
            @contextlib.contextmanager
            def _read():
                yield {"jobs": [job]}
            lr.return_value = _read()
            summary = rau.resolve_discovery_apply_urls(
                since_iso="2026-08-20T11:00:00+00:00",
                write=True,
                concurrency=20,
                http_many_fn=fake_http_many,
                resolve_job_fn=fake_resolve,
            )
        self.assertEqual(search_called["n"], 1)
        self.assertEqual(summary["high"], 1)
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0][1].get("confidence"), "high")
        self.assertEqual(persisted[0][1].get("url"), greenhouse)

    def test_restore_unresolved_deleted_job(self):
        job = {
            "id": "coinbase-analytics-engineer-gfco-analytics",
            "company": "Coinbase",
            "title": "Analytics Engineer, GFCO Analytics",
            "status": "deleted",
            "deleted_reason": "unresolved_apply_url",
            "unresolved_apply_url": True,
            "apply_url": self.WWR_URL,
            "job_url": self.WWR_URL,
            "apply_resolve_status": "failed",
        }
        ok = rau.restore_unresolved_deleted_job(job, apply_url=self.COINBASE_URL)
        self.assertTrue(ok)
        self.assertEqual(job["status"], "discovered")
        self.assertNotIn("deleted_reason", job)
        self.assertNotIn("unresolved_apply_url", job)
        self.assertEqual(job["apply_url"], self.COINBASE_URL)
        self.assertEqual(job["apply_resolve_status"], "ok")

    def test_find_sibling_resolved_apply_url(self):
        deleted = {
            "id": "airbnb-ml-1",
            "company": "Airbnb",
            "title": "Senior Machine Learning Engineer, Trust",
            "status": "deleted",
            "deleted_reason": "unresolved_apply_url",
            "apply_url": LINKEDIN_URL,
        }
        sibling = {
            "id": "airbnb-ml-2",
            "company": "Airbnb",
            "title": "Senior Machine Learning Engineer, Trust",
            "status": "discovered",
            "apply_url": "https://careers.airbnb.com/positions/8130355?gh_jid=8130355",
        }
        other = {
            "id": "airbnb-other",
            "company": "Airbnb",
            "title": "Staff Engineer",
            "status": "discovered",
            "apply_url": "https://careers.airbnb.com/positions/1?gh_jid=1",
        }
        url = rau.find_sibling_resolved_apply_url(deleted, [deleted, sibling, other])
        self.assertEqual(url, sibling["apply_url"])
        self.assertIsNone(rau.find_sibling_resolved_apply_url(deleted, [deleted, other]))

    def test_linkedin_http_error_falls_through_to_search_and_upgrades(self):
        """http_error is not terminal — public search must run before prune."""
        platform_url = "https://www.coinbase.com/careers/positions/7736521"
        job = {
            "id": "coinbase-platform-ae",
            "company": "Coinbase",
            "title": "Senior Analytics Engineer (Platform - Financial Analytics)",
            "apply_url": LINKEDIN_URL,
            "job_url": LINKEDIN_URL,
            "status": "discovered",
            "job_description": (
                "Senior Analytics Engineer Platform Financial Analytics Coinbase"
            ),
        }
        session = {
            "confidence": "low",
            "url": None,
            "reason": "http_error",
            "message": "http_429",
            "method": "linkedin_http",
            "score": 0.0,
        }
        search_calls: list[str] = []

        def fake_search(q):
            search_calls.append(q)
            return [platform_url, LINKEDIN_URL]

        def fake_fetch(url):
            if "coinbase.com" in url:
                return {
                    "title": (
                        "Senior Analytics Engineer (Platform - Financial Analytics)"
                    ),
                    "company": "Coinbase",
                    "description": "",
                }
            return None

        with tempfile.TemporaryDirectory() as td, \
             patch.object(rau, "try_linkedin_session_resolve", return_value=session):
            result = rau.resolve_job(
                job,
                search_fn=fake_search,
                board_search_fn=lambda *_: [],
                fetch_fn=fake_fetch,
                write=True,
                resumes_dir=Path(td),
                linkedin_session=True,
            )
        self.assertTrue(search_calls)
        self.assertTrue(result.get("search_attempted"))
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["url"], platform_url)
        self.assertEqual(job["apply_url"], platform_url)
        self.assertEqual(job["apply_resolve_status"], "ok")
        self.assertTrue(job.get("apply_resolve_search_attempted"))
        self.assertNotEqual(job.get("status"), "deleted")

        @contextlib.contextmanager
        def _write():
            yield {"jobs": [job]}

        with patch.object(rau, "locked_jobs_for_write", return_value=_write()), \
             patch.object(rau, "_tombstone_url_block") as block:
            rau.persist_job_resolution("coinbase-platform-ae", result)
        self.assertEqual(job["status"], "discovered")
        self.assertNotIn("unresolved_apply_url", job)
        block.assert_not_called()

    def test_discovery_http_error_searches_before_persist(self):
        job = {
            "id": "li-http-err",
            "status": "discovered",
            "company": "Coinbase",
            "title": "Senior Analytics Engineer (Platform - Financial Analytics)",
            "apply_url": LINKEDIN_URL,
            "job_url": LINKEDIN_URL,
            "created_at": "2026-08-20T12:00:00+00:00",
            "updated_at": "2026-08-20T12:00:00+00:00",
        }
        careers = "https://www.coinbase.com/careers/positions/7736521"
        search_called = {"n": 0}

        def fake_http_many(pairs, concurrency=20, **_kw):
            return [{
                "id": "li-http-err",
                "confidence": "low",
                "url": None,
                "reason": "http_error",
                "message": "http_429",
                "method": "linkedin_http",
                "score": 0.0,
            }]

        def fake_resolve(job_arg, **kw):
            search_called["n"] += 1
            self.assertFalse(kw.get("linkedin_session", True))
            return {
                "confidence": "high",
                "url": careers,
                "reason": "public_search",
                "score": 1.0,
                "search_attempted": True,
            }

        persisted: list = []

        def fake_persist(jid, scored):
            persisted.append((jid, scored))
            return {**job, "apply_url": scored.get("url")}

        with patch.object(rau, "locked_jobs_for_read") as lr, \
             patch.object(rau, "persist_job_resolution", side_effect=fake_persist):
            @contextlib.contextmanager
            def _read():
                yield {"jobs": [job]}
            lr.return_value = _read()
            summary = rau.resolve_discovery_apply_urls(
                since_iso="2026-08-20T11:00:00+00:00",
                write=True,
                concurrency=20,
                http_many_fn=fake_http_many,
                resolve_job_fn=fake_resolve,
            )
        self.assertEqual(search_called["n"], 1)
        self.assertEqual(summary["high"], 1)
        self.assertEqual(persisted[0][1].get("url"), careers)

    def test_select_reresolve_includes_linkedin_by_default(self):
        li = {
            "id": "li-1",
            "status": "deleted",
            "deleted_reason": "unresolved_apply_url",
            "company": "Coinbase",
            "title": "Senior Analytics Engineer (Platform - Financial Analytics)",
            "source": "linkedin",
            "apply_url": LINKEDIN_URL,
            "apply_resolve_reason": "http_error",
            "deleted_at": "2026-08-21T01:00:00+00:00",
        }
        wwr = {
            "id": "wwr-1",
            "status": "deleted",
            "deleted_reason": "unresolved_apply_url",
            "company": "Acme",
            "title": "ML Engineer",
            "source": "weworkremotely",
            "apply_url": "https://weworkremotely.com/remote-jobs/acme-ml",
            "deleted_at": "2026-08-20T01:00:00+00:00",
        }
        picked = rau.select_unresolved_deleted_for_reresolve([li, wwr], limit=10)
        ids = {j["id"] for j in picked}
        self.assertIn("li-1", ids)
        self.assertIn("wwr-1", ids)
        self.assertEqual(picked[0]["id"], "li-1")


class TestGoogleCseMultiKey(unittest.TestCase):
    """Multi-key CSE fallbacks — dummy keys only; never real secrets."""

    def test_load_search_keys_merges_single_and_list(self):
        file_keys = {
            "GOOGLE_CSE_CX": "cx-fixture",
            "GOOGLE_CSE_KEY": "file-single",
            "GOOGLE_CSE_KEYS": ["file-a", "file-single", "file-b"],
        }
        controlled = {
            "GOOGLE_CSE_KEY": "env-single",
            "GOOGLE_CSE_KEYS": json.dumps(["env-a", "env-single"]),
            "GOOGLE_CSE_CX": "cx-env",
        }
        with patch.dict(os.environ, controlled, clear=False):
            stashed = {
                k: os.environ.pop(k)
                for k in list(os.environ)
                if k.startswith("GOOGLE_CSE_") and k not in controlled
            }
            try:
                with patch(
                    "india_scrape_common.load_web_keys",
                    return_value=file_keys,
                ):
                    keys = rau.load_search_keys()
            finally:
                os.environ.update(stashed)
        self.assertEqual(keys["google_cse_cx"], "cx-env")
        self.assertEqual(
            keys["google_cse_keys"],
            ["env-single", "env-a", "file-single", "file-a", "file-b"],
        )
        self.assertEqual(keys["google_cse_key"], "env-single")

    def test_available_search_backends_puts_cse_first_when_configured(self):
        with patch.object(
            rau,
            "load_search_keys",
            return_value={
                "google_cse_key": "k1",
                "google_cse_keys": ["k1", "k2"],
                "google_cse_cx": "cx",
            },
        ):
            names = [b["name"] for b in rau.available_search_backends()]
        self.assertEqual(names[0], "google_cse")
        self.assertIn("brave_html", names)

    def test_search_google_cse_falls_back_after_429(self):
        from io import BytesIO
        from urllib.error import HTTPError

        calls: list[str] = []

        def fake_http_get(url, headers=None, timeout=20):
            calls.append(url)
            # Do not assert key strings into logs — only branch on call order.
            if len(calls) == 1:
                raise HTTPError(
                    url, 429, "Too Many Requests", hdrs=None, fp=BytesIO(b"")
                )
            return json.dumps(
                {
                    "items": [
                        {
                            "link": (
                                "https://jobs.ashbyhq.com/Mintlify/"
                                "ec55d98f-6e94-4ffb-9a55-4adad39297c3"
                            )
                        }
                    ]
                }
            )

        with patch.object(rau, "_http_get", side_effect=fake_http_get):
            urls = rau.search_google_cse(
                "Mintlify Applied AI Engineer",
                ["dummy-key-1", "dummy-key-2"],
                "cx-fixture",
            )
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("ashbyhq.com/Mintlify" in u for u in urls))
        # URL query embeds keys — ensure we never print them in assertions beyond
        # confirming both attempts happened.
        self.assertIn("key=dummy-key-1", calls[0])
        self.assertIn("key=dummy-key-2", calls[1])

    def test_default_search_uses_cse_key_list(self):
        seen: dict = {}

        def fake_cse(query, api_key, cx):
            seen["api_key"] = api_key
            seen["cx"] = cx
            return ["https://jobs.ashbyhq.com/Mintlify/abc"]

        with patch.object(
            rau,
            "load_search_keys",
            return_value={
                "google_cse_keys": ["k1", "k2"],
                "google_cse_key": "k1",
                "google_cse_cx": "cx",
            },
        ), patch.object(rau, "search_google_cse", side_effect=fake_cse), patch.object(
            rau, "search_brave_html", return_value=[]
        ), patch.object(rau, "search_bing_html", return_value=[]), patch.object(
            rau, "search_duckduckgo", return_value=[]
        ):
            urls = rau.default_search("Mintlify Applied AI Engineer")
        self.assertEqual(seen["api_key"], ["k1", "k2"])
        self.assertEqual(seen["cx"], "cx")
        self.assertTrue(any("ashbyhq.com" in u for u in urls))

    def test_default_search_falls_through_when_cse_empty(self):
        """CSE returning [] must not block Brave/Bing backends."""
        calls: list[str] = []

        def fake_cse(query, api_key, cx):
            calls.append("cse")
            return []

        def fake_brave(query):
            calls.append("brave_html")
            return [
                "https://jobs.ashbyhq.com/Mintlify/"
                "ec55d98f-6e94-4ffb-9a55-4adad39297c3"
            ]

        with patch.object(
            rau,
            "load_search_keys",
            return_value={
                "google_cse_keys": ["k1"],
                "google_cse_key": "k1",
                "google_cse_cx": "cx",
            },
        ), patch.object(rau, "search_google_cse", side_effect=fake_cse), patch.object(
            rau, "search_brave_html", side_effect=fake_brave
        ), patch.object(rau, "search_bing_html", return_value=[]), patch.object(
            rau, "search_duckduckgo", return_value=[]
        ):
            rau._CSE_QUOTA_EXHAUSTED = False
            urls = rau.default_search("Mintlify Applied AI Engineer")
        self.assertEqual(calls, ["cse", "brave_html"])
        self.assertTrue(any("ashbyhq.com" in u for u in urls))

    def test_available_search_backends_omits_cse_when_exhausted(self):
        with patch.object(
            rau,
            "load_search_keys",
            return_value={
                "google_cse_keys": ["k1"],
                "google_cse_key": "k1",
                "google_cse_cx": "cx",
            },
        ):
            rau._CSE_QUOTA_EXHAUSTED = True
            try:
                names = [b["name"] for b in rau.available_search_backends()]
            finally:
                rau._CSE_QUOTA_EXHAUSTED = False
        self.assertNotIn("google_cse", names)
        self.assertIn("brave_html", names)

    def test_default_search_skips_cse_http_when_exhausted(self):
        cse_calls: list[str] = []

        def fake_cse(*_a, **_k):
            cse_calls.append("hit")
            return []

        def fake_brave(_q):
            return ["https://jobs.ashbyhq.com/Mintlify/abc"]

        with patch.object(
            rau,
            "load_search_keys",
            return_value={
                "google_cse_keys": ["k1"],
                "google_cse_key": "k1",
                "google_cse_cx": "cx",
            },
        ), patch.object(rau, "search_google_cse", side_effect=fake_cse), patch.object(
            rau, "search_brave_html", side_effect=fake_brave
        ), patch.object(rau, "search_bing_html", return_value=[]), patch.object(
            rau, "search_duckduckgo", return_value=[]
        ):
            rau._CSE_QUOTA_EXHAUSTED = True
            try:
                urls = rau.default_search("Mintlify Applied AI Engineer")
            finally:
                rau._CSE_QUOTA_EXHAUSTED = False
        self.assertEqual(cse_calls, [])
        self.assertTrue(any("ashbyhq.com" in u for u in urls))

    def test_reresolve_reliable_only_skips_search_and_ignores_checkpoint(self):
        board_url = (
            "https://jobs.ashbyhq.com/Mintlify/"
            "ec55d98f-6e94-4ffb-9a55-4adad39297c3"
        )
        deleted = {
            "id": "mintlify-applied-ai-engineer-reli",
            "company": "Mintlify",
            "title": "Applied AI Engineer",
            "status": "deleted",
            "deleted_reason": "unresolved_apply_url",
            "apply_url": LINKEDIN_URL,
            "job_url": LINKEDIN_URL,
            "deleted_at": "2026-08-20T12:00:00+00:00",
        }

        def boom_search(_q):
            raise AssertionError("reliable_only must not call public search")

        def fake_board(company, title):
            self.assertEqual(company, "Mintlify")
            return [board_url]

        def fake_fetch(url):
            return {
                "title": "Applied AI Engineer",
                "company": "Mintlify",
                "description": "Applied AI Engineer at Mintlify",
            }

        with tempfile.TemporaryDirectory() as td:
            progress = Path(td) / "prog.json"
            progress.write_text(
                json.dumps({"done_ids": [deleted["id"]], "updated_at": "x"})
            )
            with patch.object(rau, "locked_jobs_for_read") as lr, \
                 patch.object(rau, "search_ats_boards", side_effect=fake_board), \
                 patch.object(rau, "default_fetch", side_effect=fake_fetch):
                @contextlib.contextmanager
                def _read():
                    yield {"jobs": [deleted]}
                lr.return_value = _read()
                summary = rau.reresolve_unresolved_deleted(
                    limit=10,
                    write=False,
                    search_fn=boom_search,
                    fetch_fn=fake_fetch,
                    workers=1,
                    progress_path=progress,
                    reliable_only=True,
                )
        self.assertEqual(summary["considered"], 1)
        self.assertEqual(summary["restored"], 1)
        self.assertEqual(summary["restored_by"]["ats_board_api"], 1)
        self.assertTrue(summary.get("reliable_only"))

    def test_resolve_discovery_uses_sibling_before_search(self):
        sibling_url = "https://careers.airbnb.com/positions/8130355?gh_jid=8130355"
        need = {
            "id": "airbnb-ml-agg",
            "status": "discovered",
            "company": "Airbnb",
            "title": "Senior Machine Learning Engineer, Trust",
            "apply_url": "https://www.indeed.com/viewjob?jk=1",
            "job_url": "https://www.indeed.com/viewjob?jk=1",
            "created_at": "2026-08-20T12:00:00+00:00",
            "updated_at": "2026-08-20T12:00:00+00:00",
        }
        sibling = {
            "id": "airbnb-ml-ok",
            "status": "discovered",
            "company": "Airbnb",
            "title": "Senior Machine Learning Engineer, Trust",
            "apply_url": sibling_url,
            "apply_resolve_status": "ok",
        }

        def boom_resolve(*_a, **_k):
            raise AssertionError("sibling hit must skip resolve_job")

        with patch.object(rau, "locked_jobs_for_read") as lr, \
             patch.object(rau, "persist_job_resolution") as persist:
            @contextlib.contextmanager
            def _read():
                yield {"jobs": [need, sibling]}
            lr.return_value = _read()
            persist.return_value = {**need, "apply_url": sibling_url}
            summary = rau.resolve_discovery_apply_urls(
                since_iso="2026-08-20T11:00:00+00:00",
                write=True,
                resolve_job_fn=boom_resolve,
                http_many_fn=lambda *_a, **_k: [],
            )
        self.assertEqual(summary["high"], 1)
        self.assertEqual(summary["upgraded"][0]["url"], sibling_url)
        args, _kw = persist.call_args
        self.assertEqual(args[1].get("reason"), "sibling_resolved_apply_url")


if __name__ == "__main__":
    unittest.main()
