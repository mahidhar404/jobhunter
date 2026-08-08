#!/usr/bin/env python3
"""Unit tests for JD fingerprint equality merge (high precision).

Run:
  python3 scripts/test_jd_fingerprint.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from apply_urls import merge_listing_pair  # noqa: E402
from dedup_jobs import should_merge  # noqa: E402
from jd_fingerprint import (  # noqa: E402
    MIN_JD_CHARS,
    jd_fingerprint,
    normalize_jd_text,
    same_jd_fingerprint,
)


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

    def test_should_merge_on_identical_jd_fingerprint(self):
        jd = _long_jd("merge-me")
        a = {
            "id": "a",
            "company": "Acme",
            "title": "ML Engineer",
            "job_description": jd,
            "apply_url": "https://www.linkedin.com/jobs/view/111",
            "source": "linkedin",
            "job_url": "https://www.linkedin.com/jobs/view/111",
        }
        b = {
            "id": "b",
            "company": "Other Co",  # company can differ; JD identity is enough
            "title": "Staff Banana Peeler",
            "job_description": jd,
            "apply_url": "https://boards.greenhouse.io/acme/jobs/222",
            "source": "greenhouse",
            "job_url": "https://boards.greenhouse.io/acme/jobs/222",
        }
        self.assertTrue(should_merge(a, b))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
