"""List chips: prefer jobs.json stamps; GET /api/jobs never reads jd_full.

Dummy JD / fixture pay only — no applicant PII.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SERVER_PATH = HERE / "server.py"

INTRO = (
    "Dummy ML Engineer at Fixture Co. We build models and ship them. "
    "This preview is intentionally long so pay/YOE/mode/visa sit only in "
    "jd_full.txt. " * 20
)
FULL_JD = (
    INTRO
    + "\n\nCompensation: $166,000 per year.\n"
    + "3+ years of experience required.\n"
    + "This is a fully remote role.\n"
    + "U.S. Person status is required as this position needs to access "
    "export controlled data.\n"
)


def _load_server():
    spec = importlib.util.spec_from_file_location("jh_dashboard_list_tags", SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jh_dashboard_list_tags"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_slim_list_never_enriches_from_jd_full():
    """Stamp-only contract: legacy missing keys must not open jd_full on list."""
    srv = _load_server()
    preview = srv._trim_job_description_preview(FULL_JD)
    assert "$166" not in preview
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        srv.RESUMES_DIR = root
        job_dir = root / "pay-hidden"
        job_dir.mkdir()
        (job_dir / "jd_full.txt").write_text(FULL_JD, encoding="utf-8")
        job = {
            "id": "pay-hidden",
            "title": "Dummy ML Engineer",
            "company": "Fixture Co",
            "location": "Remote",
            "job_description": preview,
            "salary_min": None,
            "salary_max": None,
            # Intentionally omit work_mode / clearance (true legacy).
        }
        with mock.patch.object(
            srv, "load_raw_job_description", side_effect=AssertionError("jd read")
        ):
            slim = srv.slim_job_for_list(job)
    assert "job_description" not in slim
    assert slim.get("salary_min") is None
    assert "work_mode" not in slim or slim.get("work_mode") is None


def test_slim_uses_stamped_chips_without_reading_jd_full():
    """Hot path: stamped jobs.json chips must not open jd_full.txt."""
    srv = _load_server()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        srv.RESUMES_DIR = root
        job_dir = root / "stamped-hot"
        job_dir.mkdir()
        (job_dir / "jd_full.txt").write_text(FULL_JD, encoding="utf-8")
        job = {
            "id": "stamped-hot",
            "title": "Dummy ML Engineer",
            "company": "Fixture Co",
            "location": "Remote",
            "job_description": INTRO,
            "salary_min": 120000,
            "salary_max": 150000,
            "min_yoe": 2,
            "work_mode": "hybrid",
            "clearance": False,
            "us_person": False,
            "jd_incomplete": False,
        }
        with mock.patch.object(
            srv, "load_raw_job_description", side_effect=AssertionError("jd read")
        ):
            slim = srv.slim_job_for_list(job)
    assert slim["salary_min"] == 120000
    assert slim["work_mode"] == "hybrid"
    assert slim["jd_incomplete"] is False


def test_slim_does_not_reparse_when_unknown_stamps_exist():
    """Stamped unknown/null chips must not open jd_full on the list hot path."""
    srv = _load_server()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        srv.RESUMES_DIR = root
        job_dir = root / "jack-jill-unknown-keys"
        job_dir.mkdir()
        (job_dir / "jd_full.txt").write_text(FULL_JD, encoding="utf-8")
        with mock.patch.object(
            srv, "load_raw_job_description", side_effect=AssertionError("jd read")
        ):
            slim = srv.slim_job_for_list(
                {
                    "id": "jack-jill-unknown-keys",
                    "title": "Dummy ML Engineer",
                    "company": "Jack & Jill",
                    "location": "Seattle, WA",
                    "job_description": srv._trim_job_description_preview(FULL_JD),
                    "work_mode": "unknown",
                    "work_mode_fallback": None,
                    "salary_min": None,
                    "salary_max": None,
                    "salary_min_fallback": None,
                    "salary_max_fallback": None,
                    "min_yoe": None,
                    "min_yoe_fallback": None,
                    "clearance": False,
                    "us_person": False,
                    "jd_incomplete": False,
                }
            )
    assert slim["work_mode"] == "unknown"
    assert slim["salary_min"] is None
    assert slim["min_yoe"] is None
    assert slim["jd_incomplete"] is False


def test_slim_list_tags_need_jd_parse_always_false():
    srv = _load_server()
    assert srv._list_tags_need_jd_parse({}) is False
    assert srv._list_tags_need_jd_parse({"title": "x"}) is False


def test_persist_jd_invalidates_jobs_list_cache():
    srv = _load_server()
    srv._jobs_list_cache["body_bytes"] = b'{"jobs":[]}'
    srv._jobs_list_cache["etag"] = '"stale"'
    srv._jobs_list_cache["mtime"] = 1
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        srv.RESUMES_DIR = root
        job = {"id": "cache-bust", "title": "Dummy"}
        srv.persist_job_description(job, "Dummy JD with Remote work.\n")
    assert srv._jobs_list_cache.get("body_bytes") is None
    assert job.get("work_mode") == "remote"
    assert isinstance(job.get("jd_incomplete"), bool)


def test_slim_keeps_existing_strict_salary_stamp():
    srv = _load_server()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        srv.RESUMES_DIR = root
        job_dir = root / "stamped"
        job_dir.mkdir()
        (job_dir / "jd_full.txt").write_text(FULL_JD, encoding="utf-8")
        slim = srv.slim_job_for_list(
            {
                "id": "stamped",
                "title": "Dummy ML Engineer",
                "job_description": INTRO,
                "salary_min": 120000,
                "salary_max": 150000,
                "work_mode": "remote",
                "clearance": False,
                "us_person": True,
                "jd_incomplete": False,
            }
        )
    assert slim["salary_min"] == 120000
    assert slim["salary_max"] == 150000


if __name__ == "__main__":
    test_slim_list_never_enriches_from_jd_full()
    test_slim_uses_stamped_chips_without_reading_jd_full()
    test_slim_does_not_reparse_when_unknown_stamps_exist()
    test_slim_list_tags_need_jd_parse_always_false()
    test_persist_jd_invalidates_jobs_list_cache()
    test_slim_keeps_existing_strict_salary_stamp()
    print("OK test_list_tag_stamps")
