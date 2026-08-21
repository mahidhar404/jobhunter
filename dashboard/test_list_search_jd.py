"""JD token search endpoint — must not touch /api/jobs list cold path."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SERVER_PATH = HERE / "server.py"


def _load_server():
    spec = importlib.util.spec_from_file_location("jh_dashboard_search_srv", SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jh_dashboard_search_srv"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_search_jobs_jd_tokens_and_or_hits():
    srv = _load_server()
    with tempfile.TemporaryDirectory() as td:
        resumes = Path(td)
        (resumes / "a").mkdir()
        (resumes / "b").mkdir()
        (resumes / "a" / "jd_full.txt").write_text(
            "Visa sponsorship available. Free snacks.", encoding="utf-8"
        )
        (resumes / "b" / "jd_full.txt").write_text(
            "Flexible schedule and remote food stipend.", encoding="utf-8"
        )
        jobs = [
            {"id": "a", "job_description": ""},
            {"id": "b", "job_description": ""},
            {"id": "c", "job_description": "preview only sponsorship note"},
        ]
        with mock.patch.object(srv, "RESUMES_DIR", resumes):
            hits = srv.search_jobs_jd_tokens(
                ["sponsorship", "flexible", "food"],
                jobs=jobs,
                timeout_s=5.0,
                limit=100,
            )
    assert set(hits["sponsorship"]) == {"a", "c"}
    assert set(hits["flexible"]) == {"b"}
    assert set(hits["food"]) == {"b"}


def test_search_jobs_jd_respects_timeout_and_limit():
    srv = _load_server()
    with tempfile.TemporaryDirectory() as td:
        resumes = Path(td)
        jobs = []
        for i in range(20):
            jid = f"j{i}"
            (resumes / jid).mkdir()
            (resumes / jid / "jd_full.txt").write_text(f"token-{i} hello", encoding="utf-8")
            jobs.append({"id": jid})
        with mock.patch.object(srv, "RESUMES_DIR", resumes):
            hits = srv.search_jobs_jd_tokens(
                ["hello"],
                jobs=jobs,
                timeout_s=5.0,
                limit=5,
            )
    assert len(hits["hello"]) <= 5


def test_api_jobs_search_route_returns_token_hits():
    srv = _load_server()
    handler = srv.Handler.__new__(srv.Handler)
    handler.path = "/api/jobs/search?q=sponsorship,flexible"
    captured = {}

    def _send_json(obj=None, status=200, *, headers=None, body_bytes=None):
        captured["status"] = status
        captured["obj"] = obj

    handler._send_json = _send_json  # type: ignore[method-assign]
    with mock.patch.object(
        srv,
        "search_jobs_jd_tokens",
        return_value={"sponsorship": ["a"], "flexible": ["b"]},
    ) as search, mock.patch.object(
        srv, "read_jobs", return_value={"jobs": [{"id": "a"}, {"id": "b"}]}
    ), mock.patch.object(srv, "_cached_jobs_list_response", side_effect=AssertionError("no list")):
        handler.do_GET()
    search.assert_called_once()
    assert captured["status"] == 200
    assert captured["obj"]["hits"]["sponsorship"] == ["a"]
    assert captured["obj"]["hits"]["flexible"] == ["b"]


def test_api_jobs_list_unchanged_no_jd_scan_on_search_helper_name():
    """Cold list path must stay stamp-only; search is a separate helper."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    assert "def search_jobs_jd_tokens(" in src
    assert 'parts == ["api", "jobs", "search"]' in src
    # List handler still uses cache, not search_jobs_jd_tokens
    list_idx = src.index('parts == ["api", "jobs"]')
    search_idx = src.index('parts == ["api", "jobs", "search"]')
    assert search_idx != list_idx
    snippet = src[list_idx : list_idx + 400]
    assert "search_jobs_jd_tokens" not in snippet


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("OK test_list_search_jd")
