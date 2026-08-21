#!/usr/bin/env python3
"""JD detail must be cheap: file-first, no jobs.json parse on jd_full hit.

Dummy JD text only — never applicant PII. Does not submit applications.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server as srv  # noqa: E402

APP_JS = HERE / "static" / "app.js"


def test_fetch_job_description_file_first_skips_read_jobs():
    """Hot path: jd_full.txt must not parse jobs.json / take the slow lock path."""
    job_id = "dummy-instant-jd"
    full = "Dummy JD for instant-load test.\n\nRequirements: Python.\n"
    with tempfile.TemporaryDirectory() as td:
        resumes = Path(td) / "resumes"
        job_dir = resumes / job_id
        job_dir.mkdir(parents=True)
        (job_dir / "jd_full.txt").write_text(full, encoding="utf-8")

        def _boom_read_jobs():
            raise AssertionError("read_jobs must not run when jd_full exists")

        with (
            mock.patch.object(srv, "RESUMES_DIR", resumes),
            mock.patch.object(srv, "read_jobs", side_effect=_boom_read_jobs),
        ):
            raw, source = srv.fetch_job_description_for_api(job_id)
        assert source == "jd_full.txt"
        assert raw == full


def test_fetch_job_description_falls_back_to_preview():
    job_id = "dummy-preview-only"
    preview = "Short dummy preview only — not a full JD."
    with tempfile.TemporaryDirectory() as td:
        resumes = Path(td) / "resumes"
        resumes.mkdir(parents=True)
        data = {
            "jobs": [
                {
                    "id": job_id,
                    "title": "Dummy Role",
                    "job_description": preview,
                }
            ]
        }
        with (
            mock.patch.object(srv, "RESUMES_DIR", resumes),
            mock.patch.object(srv, "read_jobs", return_value=data),
            mock.patch.object(srv, "_lock", mock.MagicMock()),
        ):
            raw, source = srv.fetch_job_description_for_api(job_id)
        assert source == "jobs.json"
        assert raw == preview


def test_fetch_job_description_unknown_id_is_none():
    with tempfile.TemporaryDirectory() as td:
        resumes = Path(td) / "resumes"
        resumes.mkdir(parents=True)
        with (
            mock.patch.object(srv, "RESUMES_DIR", resumes),
            mock.patch.object(srv, "read_jobs", return_value={"jobs": []}),
            mock.patch.object(srv, "_lock", mock.MagicMock()),
        ):
            raw, source = srv.fetch_job_description_for_api("missing-job")
        assert raw is None
        assert source == "none"


def test_app_js_has_jd_prefetch_and_optimistic_cache():
    src = APP_JS.read_text(encoding="utf-8")
    assert "function scheduleJdCacheWarm(" in src
    assert "function warmJdCacheForVisible(" in src
    assert "function prefetchJdNeighbors(" in src
    assert "jdInflight" in src
    assert "loadJobDescription(id, { background: true })" in src
    # Must not blank the dossier with the long loading copy when text exists.
    assert "Loading job description…" not in src
    assert 'class="evidence jd-loading"' in src
    # List paint must schedule idle warm — never put jd_full on /api/jobs.
    assert "scheduleJdCacheWarm()" in src


if __name__ == "__main__":
    test_fetch_job_description_file_first_skips_read_jobs()
    test_fetch_job_description_falls_back_to_preview()
    test_fetch_job_description_unknown_id_is_none()
    test_app_js_has_jd_prefetch_and_optimistic_cache()
    print("ok")
