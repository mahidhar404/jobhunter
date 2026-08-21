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
        )
        no_ext = _emed_job(
            id="u-noext",
            status="discovered",
            apply_resolve_status="no_external",
            apply_resolve_reason="no_external_apply",
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


if __name__ == "__main__":
    unittest.main()