"""Phase 1 regression tests for the dashboard jobs-list data path."""

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
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _load_server():
    spec = importlib.util.spec_from_file_location("jh_dashboard_perf_srv", SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jh_dashboard_perf_srv"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_slim_job_uses_persisted_resume_flag_without_resolve():
    srv = _load_server()
    job = {
        "id": "flagged",
        "resume_path": "resumes/flagged/resume.pdf",
        "resume_on_disk": False,
    }
    with mock.patch.object(
        srv, "resolve_job_resume_file", side_effect=AssertionError("legacy resolver called")
    ):
        slim = srv.slim_job_for_list(job)
    assert slim["resume_on_disk"] is False
    assert slim["resume_path"] is None


def test_write_jobs_bumps_revision_and_writes_compact_json():
    srv = _load_server()
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        jobs_file = tmp_path / "jobs.json"
        lock_file = tmp_path / "jobs.json.lock"
        jobs_file.write_text('{"revision":4,"jobs":[]}', encoding="utf-8")
        data = {"revision": 4, "jobs": [{"id": "one", "status": "discovered"}]}
        with mock.patch.object(srv, "JOBS_FILE", jobs_file), mock.patch.object(
            srv, "JOBS_LOCK_FILE", lock_file
        ), mock.patch.object(srv, "backup_jobs_file"):
            srv.write_jobs(data)
        raw = jobs_file.read_text(encoding="utf-8")
        assert data["revision"] == 5
        assert json.loads(raw)["revision"] == 5
        assert raw == json.dumps(data, separators=(",", ":"))


def test_runtime_status_does_not_read_jobs():
    srv = _load_server()
    key = "agent:job-hunter:job-runtime-only"
    srv._runtime_job_snapshots[key] = {
        "id": "runtime-only",
        "company": "Example",
        "title": "Engineer",
        "status": "filling",
    }
    with mock.patch.object(srv, "read_jobs", side_effect=AssertionError("hot-path read")), \
        mock.patch.object(srv, "_running_procs", {}), \
        mock.patch.object(srv.agent_runner, "active_turn_keys", return_value={key}), \
        mock.patch.object(
            srv, "_discovery_status_in_memory", return_value={"running": False}
        ), \
        mock.patch.object(srv, "ui_lifecycle_status", return_value={}):
        status = srv.runtime_status()
    assert status["running_job_ids"] == ["runtime-only"]
    assert status["running_jobs"][0]["company"] == "Example"


def test_jobs_list_cache_reuses_serialized_body():
    srv = _load_server()
    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        jobs_file.write_text('{"revision":7,"jobs":[]}', encoding="utf-8")
        data = {"revision": 7, "jobs": []}
        srv._invalidate_jobs_list_cache()
        with mock.patch.object(srv, "JOBS_FILE", jobs_file), mock.patch.object(
            srv, "read_jobs", return_value=data
        ) as read, mock.patch.object(
            srv, "_fill_hold_browser_active", return_value=False
        ):
            first = srv._cached_jobs_list_response()
            second = srv._cached_jobs_list_response()
    assert first == second
    assert first[1].endswith('-7-0"')
    read.assert_called_once()


def test_jobs_list_cache_rejects_stale_body_under_newer_mtime():
    """TOCTOU: never cache an old revision under a post-write mtime."""
    import jobs_list as jl

    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        jobs_file.write_text(
            json.dumps({"revision": 1, "jobs": [{"id": "a"}]}), encoding="utf-8"
        )
        mtimes = [jobs_file.stat().st_mtime_ns]
        reads = {"n": 0}

        def _read():
            reads["n"] += 1
            if reads["n"] == 1:
                # Simulate a writer landing after the pre-read mtime check.
                time.sleep(0.02)
                jobs_file.write_text(
                    json.dumps({"revision": 2, "jobs": [{"id": "b"}]}),
                    encoding="utf-8",
                )
                mtimes.append(jobs_file.stat().st_mtime_ns)
                return {"revision": 1, "jobs": [{"id": "a"}]}
            return json.loads(jobs_file.read_text(encoding="utf-8"))

        cache: dict = {
            "mtime": None,
            "body_bytes": None,
            "etag": None,
            "fill_hold": None,
        }
        body1, _etag1 = jl.cached_jobs_list_response(
            jobs_file=jobs_file,
            read_jobs=_read,
            fill_hold_active=lambda: False,
            build_response=lambda data, fh: {
                "jobs": data.get("jobs") or [],
                "fill_hold_active": fh,
            },
            cache=cache,
        )
        # First attempt saw mtime change mid-read and retried.
        assert reads["n"] >= 2
        payload1 = json.loads(body1.decode())
        assert payload1["jobs"][0]["id"] == "b"

        body2, _etag2 = jl.cached_jobs_list_response(
            jobs_file=jobs_file,
            read_jobs=_read,
            fill_hold_active=lambda: False,
            build_response=lambda data, fh: {
                "jobs": data.get("jobs") or [],
                "fill_hold_active": fh,
            },
            cache=cache,
        )
        payload2 = json.loads(body2.decode())
        assert payload2["jobs"][0]["id"] == "b"
        assert cache.get("mtime") == jobs_file.stat().st_mtime_ns


def test_jobs_list_etag_includes_fill_hold():
    srv = _load_server()
    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        jobs_file.write_text('{"revision":3,"jobs":[]}', encoding="utf-8")
        data = {"revision": 3, "jobs": []}
        srv._invalidate_jobs_list_cache()
        with mock.patch.object(srv, "JOBS_FILE", jobs_file), mock.patch.object(
            srv, "read_jobs", return_value=data
        ), mock.patch.object(srv, "_fill_hold_browser_active", return_value=True):
            _body, etag = srv._cached_jobs_list_response()
    assert etag.endswith('-3-1"')


def test_write_jobs_refuses_empty_collapse(tmp_path=None):
    srv = _load_server()
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        jobs_file = tmp_path / "jobs.json"
        lock_file = tmp_path / "jobs.json.lock"
        jobs_file.write_text(
            json.dumps({"revision": 1, "jobs": [{"id": f"j{i}"} for i in range(20)]}),
            encoding="utf-8",
        )
        with mock.patch.object(srv, "JOBS_FILE", jobs_file), mock.patch.object(
            srv, "JOBS_LOCK_FILE", lock_file
        ), mock.patch.object(srv, "backup_jobs_file"):
            try:
                srv.write_jobs({"revision": 1, "jobs": []})
            except srv.JobsWriteRefused:
                pass
            else:
                raise AssertionError("expected JobsWriteRefused for empty collapse")
            # Intentional purge still allowed.
            srv.write_jobs({"revision": 1, "jobs": []}, allow_purge=True)
        assert json.loads(jobs_file.read_text())["jobs"] == []


def test_app_js_start_failure_forces_poll_resync():
    source = (Path(__file__).resolve().parent / "static" / "app.js").read_text()
    assert "function invalidateJobsListCache()" in source
    assert "lastJobsJSON = null" in source
    assert "lastJobsEtag = null" in source
    # Failed Start / empty-deleted must invalidate before poll (avoid sticky 304).
    assert "invalidateJobsListCache(); // force poll to drop optimistic status" in source
    # 304 with a forced refresh must refetch a body, not keep optimistic local jobs.
    assert "if (lastJobsJSON == null)" in source
    assert 'res = await fetch("/api/jobs")' in source


def test_jobs_get_returns_304_for_matching_etag():
    srv = _load_server()
    handler = srv.Handler.__new__(srv.Handler)
    handler.path = "/api/jobs"
    handler.headers = {"If-None-Match": '"abc-2"'}
    with mock.patch.object(
        srv, "_cached_jobs_list_response", return_value=(b'{"jobs":[]}', '"abc-2"')
    ), mock.patch.object(handler, "_send_json") as send:
        handler.do_GET()
    send.assert_called_once_with(
        status=304, headers={"ETag": '"abc-2"'}, body_bytes=b""
    )


def test_jobs_head_matches_get_without_body():
    """HEAD /api/jobs must not 404 — probes expect the same resource as GET."""
    srv = _load_server()
    handler = srv.Handler.__new__(srv.Handler)
    handler.path = "/api/jobs"
    body = b'{"jobs":[],"fill_hold_active":false}'
    etag = '"deadbeef-9-0"'
    sent = {}

    def _capture_response(code):
        sent["code"] = code

    def _capture_header(name, value):
        sent.setdefault("headers", {})[name] = value

    def _end():
        sent["ended"] = True

    handler.send_response = _capture_response  # type: ignore[method-assign]
    handler.send_header = _capture_header  # type: ignore[method-assign]
    handler.end_headers = _end  # type: ignore[method-assign]
    with mock.patch.object(
        srv, "_cached_jobs_list_response", return_value=(body, etag)
    ):
        handler.do_HEAD()
    assert sent.get("code") == 200
    assert sent.get("ended") is True
    assert sent["headers"]["Content-Type"] == "application/json"
    assert sent["headers"]["Content-Length"] == str(len(body))
    assert sent["headers"]["ETag"] == etag
    assert "wfile" not in sent  # HEAD must not write a body


def test_stats_route_exists_alongside_jobs_etag():
    srv = _load_server()
    src = SERVER_PATH.read_text(encoding="utf-8")
    assert 'parts == ["api", "stats"]' in src
    assert 'parts == ["api", "jobs"]' in src
    assert 'If-None-Match' in src
    assert 'aggregate_stats' in src
    # Classic redirects must remain.
    assert 'parts[0] in ("classic", "classic.html", "classic.js")' in src
    assert "send_response(302)" in src


def test_jobs_list_stamp_only_never_reads_jd_full():
    """Contract: GET /api/jobs / slim_job_for_list must not touch jd_full."""
    srv = _load_server()
    src = SERVER_PATH.read_text(encoding="utf-8")
    # Hot-path slim + cache must delegate to jobs_list (single source of truth).
    assert "return _slim_job_for_list_impl(" in src
    assert "_cached_jobs_list_response_impl(" in src
    assert "_invalidate_jobs_list_cache_impl(" in src
    assert "load_raw_job_description" not in (
        src[src.index("def slim_job_for_list") : src.index("def sync_job_resume_on_disk")]
    )
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        jobs_file = root / "jobs.json"
        resumes = root / "resumes"
        job_dir = resumes / "legacy-row"
        job_dir.mkdir(parents=True)
        (job_dir / "jd_full.txt").write_text(
            "Compensation: $200,000. Remote. 5+ years.\n", encoding="utf-8"
        )
        data = {
            "revision": 1,
            "jobs": [
                {
                    "id": "legacy-row",
                    "title": "Dummy",
                    "job_description": "short preview",
                    # Missing stamp keys — must still skip jd_full.
                }
            ],
        }
        jobs_file.write_text(json.dumps(data), encoding="utf-8")
        srv._invalidate_jobs_list_cache()
        with mock.patch.object(srv, "JOBS_FILE", jobs_file), mock.patch.object(
            srv, "RESUMES_DIR", resumes
        ), mock.patch.object(srv, "read_jobs", return_value=data), mock.patch.object(
            srv, "_fill_hold_browser_active", return_value=False
        ), mock.patch.object(
            srv, "load_raw_job_description", side_effect=AssertionError("jd_full")
        ):
            body, etag = srv._cached_jobs_list_response()
    payload = json.loads(body.decode())
    assert len(payload["jobs"]) == 1
    assert payload["jobs"][0].get("salary_min") is None
    assert etag.startswith('"')


def test_jobs_list_contract_documented_in_source():
    src = SERVER_PATH.read_text(encoding="utf-8")
    assert "stamp-only" in src.lower() or "Stamp-only" in src
    assert "never opens resumes/<id>/jd_full.txt" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("OK test_jobs_list_perf")
