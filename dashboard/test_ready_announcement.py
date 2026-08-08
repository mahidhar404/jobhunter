#!/usr/bin/env python3
"""Tests for one-shot spoken 'ready for review' announcement claiming.

Every open dashboard tab polls independently and keeps its own in-page
"already spoken" set, so a single Ready event used to be announced once per
connected client. The claim endpoint grants it to exactly one caller.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


class _FakeHandler:
    """Drives the real handler method without standing up an HTTP server."""

    _job = srv.Handler._job
    _handle_claim_ready_announcement = srv.Handler._handle_claim_ready_announcement

    def __init__(self):
        self.responses = []

    def _send_json(self, payload, status=200):
        self.responses.append((status, payload))

    @property
    def last(self):
        return self.responses[-1]


def _claim(jobs_file, job_id):
    handler = _FakeHandler()
    with (
        mock.patch.object(srv, "JOBS_FILE", jobs_file),
        mock.patch.object(srv, "JOBS_LOCK_FILE", jobs_file.with_suffix(".json.lock")),
    ):
        handler._handle_claim_ready_announcement(job_id)
    return handler.last


def _write(jobs_file, jobs):
    jobs_file.write_text(json.dumps({"jobs": jobs}))


def test_only_first_claim_speaks() -> None:
    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        _write(jobs_file, [{
            "id": "j1",
            "status": "ready_for_review",
            "company": "Acme",
            "title": "Data Scientist",
        }])

        status, first = _claim(jobs_file, "j1")
        assert status == 200
        assert first["speak"] is True, first

        # Simulates the other connected dashboard clients polling the same event.
        for _ in range(10):
            _, again = _claim(jobs_file, "j1")
            assert again["speak"] is False, again

        saved = json.loads(jobs_file.read_text())["jobs"][0]
        assert saved["ready_announced"] is True


def test_claim_refused_when_not_ready() -> None:
    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        _write(jobs_file, [{"id": "j1", "status": "filling"}])

        _, resp = _claim(jobs_file, "j1")
        assert resp["speak"] is False
        assert resp["reason"] == "not ready"


def test_unknown_job_is_404() -> None:
    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        _write(jobs_file, [])

        status, resp = _claim(jobs_file, "nope")
        assert status == 404
        assert "error" in resp


def test_leaving_ready_rearms_announcement() -> None:
    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        _write(jobs_file, [{
            "id": "j1",
            "status": "ready_for_review",
            "company": "Acme",
            "title": "Data Scientist",
        }])

        assert _claim(jobs_file, "j1")[1]["speak"] is True

        with (
            mock.patch.object(srv, "JOBS_FILE", jobs_file),
            mock.patch.object(srv, "JOBS_LOCK_FILE", jobs_file.with_suffix(".json.lock")),
        ):
            # A new run moves the job out of Ready, clearing the flag.
            srv._patch_job("j1", status="filling", status_detail="Refilling.")
            assert json.loads(jobs_file.read_text())["jobs"][0].get(
                "ready_announced"
            ) is None
            # Back to Ready → announced again, exactly once.
            srv._patch_job("j1", status="ready_for_review", status_detail="Ready.")

        _, resp = _claim(jobs_file, "j1")
        assert resp["speak"] is True, resp
        _, resp2 = _claim(jobs_file, "j1")
        assert resp2["speak"] is False, resp2


if __name__ == "__main__":
    test_only_first_claim_speaks()
    test_claim_refused_when_not_ready()
    test_unknown_job_is_404()
    test_leaving_ready_rearms_announcement()
    print("ready announcement tests: OK")
