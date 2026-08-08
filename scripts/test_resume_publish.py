#!/usr/bin/env python3
"""Unit tests for resumes/by_company publish helper.

Run:
  python3 scripts/test_resume_publish.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from resume_publish import (  # noqa: E402
    BY_COMPANY_DIR,
    FILE_ID_DIGITS,
    by_company_resume_path,
    ensure_file_id,
    publish_resume_to_by_company,
    sanitize_filename,
)


class TestSanitizeFilename(unittest.TestCase):
    def test_strips_illegal(self):
        self.assertEqual(sanitize_filename('Jerry.ai'), "Jerry.ai")
        self.assertEqual(sanitize_filename('Acme/Corp: Inc?'), "AcmeCorp Inc")
        self.assertEqual(sanitize_filename(""), "company")


class TestEnsureFileId(unittest.TestCase):
    def test_reuses_existing(self):
        job = {"id": "j1", "file_id": "04217"}
        self.assertEqual(ensure_file_id(job), "04217")

    def test_mints_unique_five_digit(self):
        job = {"id": "j2", "company": "Acme"}
        fid = ensure_file_id(job, existing_ids={"00000", "00001"})
        self.assertEqual(len(fid), FILE_ID_DIGITS)
        self.assertTrue(fid.isdigit())
        self.assertNotIn(fid, {"00000", "00001"})
        self.assertEqual(job["file_id"], fid)


class TestPublishResume(unittest.TestCase):
    def test_publish_naming_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            by_co = root / "resumes" / "by_company"
            src_dir = root / "resumes" / "jerry-ai-role"
            src_dir.mkdir(parents=True)
            src = src_dir / "resume.pdf"
            src.write_bytes(b"%PDF-1.4 first")

            job = {"id": "jerry-ai-role", "company": "Jerry.ai"}
            dest1 = publish_resume_to_by_company(
                job,
                src,
                by_company_dir=by_co,
                root=root,
                existing_file_ids=set(),
            )
            self.assertTrue(dest1.is_file())
            self.assertEqual(dest1.parent, by_co)
            self.assertRegex(dest1.name, r"^Jerry\.ai_resume_\d{5}\.pdf$")
            self.assertEqual(dest1.read_bytes(), b"%PDF-1.4 first")
            self.assertIn("file_id", job)
            self.assertEqual(job["resume_by_company_path"], str(dest1.relative_to(root)))

            # Re-publish same job: overwrite same file, no second copy.
            src.write_bytes(b"%PDF-1.4 second")
            dest2 = publish_resume_to_by_company(
                job,
                src,
                by_company_dir=by_co,
                root=root,
                existing_file_ids={job["file_id"]},
            )
            self.assertEqual(dest2, dest1)
            self.assertEqual(dest2.read_bytes(), b"%PDF-1.4 second")
            self.assertEqual(len(list(by_co.glob("*.pdf"))), 1)

    def test_two_jobs_same_company_distinct_ids(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            by_co = root / "resumes" / "by_company"
            pdf_a = root / "a.pdf"
            pdf_b = root / "b.pdf"
            pdf_a.write_bytes(b"%PDF-a")
            pdf_b.write_bytes(b"%PDF-b")

            job_a = {"id": "c1", "company": "Acme"}
            job_b = {"id": "c2", "company": "Acme"}
            d1 = publish_resume_to_by_company(
                job_a, pdf_a, by_company_dir=by_co, root=root, existing_file_ids=set()
            )
            d2 = publish_resume_to_by_company(
                job_b,
                pdf_b,
                by_company_dir=by_co,
                root=root,
                existing_file_ids={job_a["file_id"]},
            )
            self.assertNotEqual(d1.name, d2.name)
            self.assertNotEqual(job_a["file_id"], job_b["file_id"])
            self.assertEqual(len(list(by_co.glob("Acme_resume_*.pdf"))), 2)

    def test_rejects_non_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc = root / "x.docx"
            doc.write_bytes(b"not pdf")
            with self.assertRaises(ValueError):
                publish_resume_to_by_company(
                    {"id": "x", "company": "X"},
                    doc,
                    by_company_dir=root / "by_company",
                    root=root,
                )

    def test_by_company_resume_path_helper(self):
        p = by_company_resume_path("Jerry.ai", "00042", by_company_dir=Path("/tmp"))
        self.assertEqual(p.name, "Jerry.ai_resume_00042.pdf")


class TestSymlinkTarget(unittest.TestCase):
    def test_module_by_company_dir(self):
        self.assertEqual(BY_COMPANY_DIR.name, "by_company")
        self.assertEqual(BY_COMPANY_DIR.parent.name, "resumes")


if __name__ == "__main__":
    unittest.main()
