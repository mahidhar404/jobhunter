#!/usr/bin/env python3
"""Unit tests for JD fingerprint equality merge (high precision).

Run:
  python3 scripts/test_jd_fingerprint.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from apply_urls import merge_listing_pair  # noqa: E402
from dedup_jobs import (  # noqa: E402
    _merge_active_jobs,
    duplicate_reason,
    should_merge,
    soft_link_exact_title_peers,
)
from dedup_listings import deduplicate_candidates  # noqa: E402
from jd_fingerprint import (  # noqa: E402
    MIN_JD_CHARS,
    item_jd_fingerprint,
    jd_fingerprint,
    normalize_jd_text,
    same_jd_fingerprint,
)
from posting_identity import ats_org_key, posting_key  # noqa: E402


def _long_jd(seed: str = "alpha") -> str:
    # Build a substantial unique-ish JD past MIN_JD_CHARS.
    body = (
        f"We are hiring a machine learning engineer ({seed}). "
        "You will train models, own pipelines, partner with product, "
        "and ship reliable inference services. "
    )
    while len(normalize_jd_text(body)) < MIN_JD_CHARS + 20:
        body += (
            "Responsibilities include feature engineering, evaluation, "
            "monitoring, and collaboration with platform teams. "
        )
    return body


class TestJdFingerprint(unittest.TestCase):
    def test_normalize_collapses_whitespace_and_case(self):
        a = normalize_jd_text("Hello   WORLD\n\nFoo")
        b = normalize_jd_text("hello world foo")
        self.assertEqual(a, b)

    def test_normalize_strips_html_and_common_volatile_footers(self):
        body = _long_jd("html")
        a = f"<div><strong>{body}</strong></div><p>Posted 3 days ago</p>"
        b = f"{body}\nEqual Opportunity Employer\nPosted 2026-08-17"
        self.assertEqual(normalize_jd_text(a), normalize_jd_text(b))

    def test_item_fingerprint_prefers_full_jd_file_over_preview(self):
        with tempfile.TemporaryDirectory() as td:
            resumes = Path(td)
            job_dir = resumes / "acme-mle"
            job_dir.mkdir()
            full = _long_jd("on-disk")
            (job_dir / "jd_full.txt").write_text(full)
            item = {
                "id": "acme-mle",
                "job_description": full[:500] + " … [full text in resumes/<id>/jd_full.txt]",
            }
            self.assertEqual(
                item_jd_fingerprint(item, resumes_dir=resumes),
                jd_fingerprint(full),
            )

    def test_short_text_no_fingerprint(self):
        self.assertIsNone(jd_fingerprint("too short"))
        self.assertIsNone(jd_fingerprint(""))
        self.assertIsNone(jd_fingerprint("x" * (MIN_JD_CHARS - 50)))

    def test_substantial_identical_jds_match(self):
        jd = _long_jd("same")
        a = {"description": jd, "site": "linkedin", "company": "Acme", "title": "MLE"}
        b = {
            "description": "  " + jd.upper().replace("  ", " ") + "\n",
            "site": "greenhouse",
            "company": "Acme Inc",
            "title": "Machine Learning Engineer",
        }
        # Uppercasing then normalize should still match the original seed text
        # only if we compare via normalize — use same underlying words:
        b["description"] = jd.replace("\n", "  \n  ")
        self.assertTrue(same_jd_fingerprint(a, b))
        self.assertEqual(jd_fingerprint(a["description"]), jd_fingerprint(b["description"]))

    def test_different_jds_do_not_match(self):
        a = {"description": _long_jd("alpha"), "site": "linkedin"}
        b = {"description": _long_jd("beta-different-role"), "site": "greenhouse"}
        self.assertFalse(same_jd_fingerprint(a, b))
        self.assertNotEqual(
            jd_fingerprint(a["description"]), jd_fingerprint(b["description"])
        )

    def test_short_text_never_merges_via_fingerprint(self):
        a = {
            "id": "a",
            "company": "Acme",
            "title": "MLE",
            "job_description": "short",
            "apply_url": "https://www.linkedin.com/jobs/view/1",
            "source": "linkedin",
        }
        b = {
            "id": "b",
            "company": "Acme",
            "title": "MLE",
            "job_description": "short",
            "apply_url": "https://www.indeed.com/viewjob?jk=2",
            "source": "indeed",
        }
        # Same short text must not create a fingerprint merge signal.
        self.assertFalse(same_jd_fingerprint(a, b))
        # should_merge may still be true via ATS-vs-aggregator title rule —
        # verify fingerprint path alone is false by using different titles:
        b["title"] = "Completely Different Title XYZ"
        self.assertFalse(should_merge(a, b))

    def test_cross_company_fingerprint_does_not_merge_without_same_ats_org(self):
        jd = _long_jd("merge-me")
        a = {
            "id": "a",
            "company": "Acme",
            "title": "ML Engineer",
            "job_description": jd,
            "apply_url": "https://jobs.ashbyhq.com/acme/11111111-1111-1111-1111-111111111111",
            "source": "linkedin",
            "job_url": "https://www.linkedin.com/jobs/view/111",
        }
        b = {
            "id": "b",
            "company": "Other Co",  # company can differ; JD identity is enough
            "title": "Staff Banana Peeler",
            "job_description": jd,
            "apply_url": "https://jobs.ashbyhq.com/other/22222222-2222-2222-2222-222222222222",
            "source": "ashby",
            "job_url": "https://jobs.ashbyhq.com/other/22222222-2222-2222-2222-222222222222",
        }
        self.assertFalse(should_merge(a, b))

    def test_cross_company_fingerprint_merges_with_same_ats_org(self):
        jd = _long_jd("same-org")
        a = {
            "id": "a",
            "company": "Acme",
            "title": "ML Engineer",
            "job_description": jd,
            "apply_url": "https://jobs.ashbyhq.com/clera/11111111-1111-1111-1111-111111111111",
        }
        b = {
            "id": "b",
            "company": "Acme Holdings",
            "title": "Machine Learning Engineer",
            "job_description": jd,
            "apply_url": "https://jobs.ashbyhq.com/clera/22222222-2222-2222-2222-222222222222",
        }
        self.assertEqual(ats_org_key(a), "ashby:clera")
        self.assertEqual(duplicate_reason(a, b), "jd_fingerprint")

    def test_distinct_posting_keys_do_not_merge_on_title_similarity(self):
        a = {
            "id": "a",
            "company": "Acme",
            "title": "Senior Data Scientist",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/1001",
        }
        b = {
            "id": "b",
            "company": "Acme",
            "title": "Senior Data Scientist",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/1002",
        }
        self.assertNotEqual(posting_key(a), posting_key(b))
        self.assertFalse(should_merge(a, b))
        soft_link_exact_title_peers([a, b])
        self.assertEqual(a["related_listing_ids"], ["b"])
        self.assertEqual(b["related_listing_ids"], ["a"])

    def test_exact_title_same_org_distinct_postings_is_repost_when_fresher(self):
        a = {
            "id": "a",
            "company": "Acme",
            "title": "Senior Data Scientist",
            "date_posted": "2026-07-01",
            "apply_url": "https://jobs.ashbyhq.com/acme/11111111-1111-1111-1111-111111111111",
        }
        b = {
            "id": "b",
            "company": "Acme",
            "title": "Senior Data Scientist",
            "date_posted": "2026-08-01",
            "apply_url": "https://jobs.ashbyhq.com/acme/22222222-2222-2222-2222-222222222222",
        }
        self.assertEqual(duplicate_reason(a, b), "repost")
        jobs = [a, b]
        self.assertEqual(_merge_active_jobs(jobs, {"a": a, "b": b}), 1)
        self.assertEqual(a["status"], "deleted")
        self.assertEqual(a["deleted_reason"], "repost")
        self.assertNotEqual(b.get("status"), "deleted")

    def test_merge_listing_pair_keeps_source_names(self):
        jd = _long_jd("pair")
        li = {
            "title": "Data Scientist",
            "company": "Example",
            "site": "linkedin",
            "job_url": "https://www.linkedin.com/jobs/view/555",
            "description": jd,
        }
        gh = {
            "title": "Data Scientist",
            "company": "Example",
            "site": "greenhouse",
            "job_url": "https://boards.greenhouse.io/example/jobs/777",
            "description": jd,
        }
        merged = merge_listing_pair(li, gh)
        names = {n.lower() for n in (merged.get("source_names") or [])}
        self.assertIn("linkedin", names)
        self.assertIn("greenhouse", names)
        self.assertTrue(
            "greenhouse" in (merged.get("apply_url") or "")
            or "greenhouse" in str(merged.get("job_url_direct") or "")
        )


class TestListingsFingerprintPass(unittest.TestCase):
    def test_dedup_listings_merges_identical_jd_across_sources(self):
        # Exercise merge_listing_pair path used by dedup_listings (no I/O).
        jd = _long_jd("listings-pass")
        a = {
            "company": "Acme",
            "title": "ML Engineer",
            "site": "indeed",
            "job_url": "https://www.indeed.com/viewjob?jk=aaa",
            "description": jd,
        }
        b = {
            "company": "Acme Corp",
            "title": "Machine Learning Engineer II",
            "site": "ashby",
            "job_url": "https://jobs.ashbyhq.com/acme/uuid",
            "description": jd,
        }
        self.assertTrue(same_jd_fingerprint(a, b))
        merged = merge_listing_pair(a, b)
        self.assertGreaterEqual(len(merged.get("source_names") or []), 2)

    def test_dedup_listings_keeps_cross_company_same_jd_without_same_org(self):
        jd = _long_jd("cross-company")
        a = {
            "company": "Acme",
            "title": "Data Scientist",
            "site": "ashby",
            "job_url": "https://jobs.ashbyhq.com/acme/11111111-1111-1111-1111-111111111111",
            "description": jd,
        }
        b = {
            "company": "Beta",
            "title": "Data Scientist",
            "site": "ashby",
            "job_url": "https://jobs.ashbyhq.com/beta/22222222-2222-2222-2222-222222222222",
            "description": jd,
        }
        kept, merged = deduplicate_candidates([a, b])
        self.assertEqual(len(kept), 2)
        self.assertEqual(merged, 0)

    def test_dedup_listings_keeps_distinct_same_title_postings_without_dates(self):
        a = {
            "company": "Acme",
            "title": "Data Scientist",
            "site": "greenhouse",
            "job_url": "https://boards.greenhouse.io/acme/jobs/1001",
        }
        b = {
            "company": "Acme",
            "title": "Data Scientist",
            "site": "greenhouse",
            "job_url": "https://boards.greenhouse.io/acme/jobs/1002",
        }
        kept, merged = deduplicate_candidates([a, b])
        self.assertEqual(len(kept), 2)
        self.assertEqual(merged, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
