"""jd_incomplete on the slim jobs-list payload — stamp-only, no jd_full I/O.

Dummy JDs only — no applicant PII.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SERVER_PATH = HERE / "server.py"

TRUNCATED_INTRO = (
    "NextGen Federal Systems, LLC (NextGen) is seeking a Senior "
    "Statistician to work on our Operational Analysis Support "
    "contract in the 618th Air Operations Center at Scott AFB. "
    "Successful candidates will employ modern statistical tools."
)
COMPLETE_JD = (
    TRUNCATED_INTRO
    + "\n\nPosition Requirements\n"
    + ("Experience with SAS and R. " * 80)
)


def _load_server():
    spec = importlib.util.spec_from_file_location("jh_dashboard_jd_incomplete", SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jh_dashboard_jd_incomplete"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_empty_jd_is_incomplete():
    srv = _load_server()
    with tempfile.TemporaryDirectory() as td:
        srv.RESUMES_DIR = Path(td)
        with mock.patch.object(
            srv, "load_raw_job_description", side_effect=AssertionError("jd read")
        ):
            slim = srv.slim_job_for_list({"id": "empty-jd", "job_description": ""})
    assert slim["jd_incomplete"] is True
    assert "job_description" not in slim


def test_stamped_incomplete_without_jd_read():
    srv = _load_server()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        srv.RESUMES_DIR = root
        job_dir = root / "trunc-jd"
        job_dir.mkdir()
        (job_dir / "jd_full.txt").write_text(TRUNCATED_INTRO, encoding="utf-8")
        with mock.patch.object(
            srv, "load_raw_job_description", side_effect=AssertionError("jd read")
        ):
            slim = srv.slim_job_for_list(
                {
                    "id": "trunc-jd",
                    "job_description": TRUNCATED_INTRO,
                    "jd_incomplete": True,
                    "work_mode": "unknown",
                    "clearance": False,
                    "us_person": False,
                }
            )
    assert slim["jd_incomplete"] is True


def test_stamped_complete_short_preview_skips_jd_full():
    srv = _load_server()
    preview = srv._trim_job_description_preview(COMPLETE_JD)
    assert len(preview) < 600
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        srv.RESUMES_DIR = root
        job_dir = root / "full-jd"
        job_dir.mkdir()
        (job_dir / "jd_full.txt").write_text(COMPLETE_JD, encoding="utf-8")
        with mock.patch.object(
            srv, "load_raw_job_description", side_effect=AssertionError("jd read")
        ):
            slim = srv.slim_job_for_list(
                {
                    "id": "full-jd",
                    "job_description": preview,
                    "jd_incomplete": False,
                    "work_mode": "remote",
                    "clearance": False,
                    "us_person": False,
                }
            )
    assert slim["has_description"] is True
    assert slim["jd_incomplete"] is False
    assert "job_description" not in slim


def test_stamp_jd_incomplete_reads_outside_write_lock():
    """jd_full I/O must not run while holding locked_jobs_for_write (EX)."""
    srv = _load_server()
    events: list[str] = []

    class _FakeCtx:
        def __enter__(self):
            events.append("ex_enter")
            return {"revision": 1, "jobs": [{"id": "need-stamp", "job_description": "x"}]}

        def __exit__(self, *args):
            events.append("ex_exit")
            return False

    def _load(job):
        events.append("jd_read")
        return COMPLETE_JD, "jd_full"

    with mock.patch.object(srv, "read_jobs", return_value={
        "revision": 1,
        "jobs": [{"id": "need-stamp", "job_description": "x"}],
    }), mock.patch.object(
        srv, "locked_jobs_for_write", side_effect=lambda **kw: _FakeCtx()
    ), mock.patch.object(
        srv, "load_raw_job_description", side_effect=_load
    ), mock.patch.object(srv, "_invalidate_jobs_list_cache"):
        n = srv._stamp_jd_incomplete_into_jobs()
    assert n == 1
    assert events[0] == "jd_read"
    assert events.index("jd_read") < events.index("ex_enter")


def test_index_html_jh_port_meta_uses_bound_port():
    srv = _load_server()
    srv._dashboard_bound_port = 8799
    handler = srv.Handler.__new__(srv.Handler)
    captured: dict = {}

    class _W:
        def write(self, b):
            captured["body"] = b

    handler.wfile = _W()  # type: ignore[attr-defined]
    handler.send_response = lambda *a, **k: None  # type: ignore[method-assign]
    handler.send_header = lambda *a, **k: None  # type: ignore[method-assign]
    handler.end_headers = lambda: None  # type: ignore[method-assign]
    handler._send_file(HERE / "static" / "index.html", "text/html")
    body = captured["body"]
    assert b'name="jh-port" content="8799"' in body
    assert b'name="jh-port" content="8787"' not in body


if __name__ == "__main__":
    test_empty_jd_is_incomplete()
    test_stamped_incomplete_without_jd_read()
    test_stamped_complete_short_preview_skips_jd_full()
    test_stamp_jd_incomplete_reads_outside_write_lock()
    test_index_html_jh_port_meta_uses_bound_port()
    print("OK test_jd_incomplete")
