#!/usr/bin/env python3
"""GET /api/jobs must not hang while a discovery merge holds the jobs lock.

Regression: the list body is cached under jobs.json's mtime. A merge bumps
that mtime on every write *and* holds LOCK_EX for minutes (it fetches a JD per
job), so every poll missed the cache and blocked in read_jobs() — the job list
spun for the whole merge, exactly when you most want to watch it fill up.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from jobs_list import cached_jobs_list_response  # noqa: E402


def _payload(n: int) -> dict:
    return {"jobs": [{"id": f"j{i}", "title": f"Job {i}"} for i in range(n)],
            "revision": n}


class NonBlockingListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.jobs_file = Path(self.tmp.name) / "jobs.json"
        self.jobs_file.write_text(json.dumps(_payload(2)))
        self.cache: dict = {"mtime": None, "body_bytes": None,
                            "etag": None, "fill_hold": None}
        self.lock = threading.Lock()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _call(self, read_jobs, read_nb):
        return cached_jobs_list_response(
            jobs_file=self.jobs_file,
            read_jobs=read_jobs,
            read_jobs_nonblocking=read_nb,
            fill_hold_active=lambda: False,
            build_response=lambda data, hold: data,
            cache=self.cache,
            lock=self.lock,
        )

    def test_serves_cached_body_when_writer_holds_the_lock(self):
        # 1. Warm the cache while nothing holds the lock.
        body, etag = self._call(lambda: _payload(2), lambda: _payload(2))
        self.assertIn(b"Job 1", body)

        # 2. A merge lands rows (mtime changes -> cache key misses) and holds
        #    the lock, so the non-blocking read returns None.
        self.jobs_file.write_text(json.dumps(_payload(3)))
        blocking_calls = []

        def _blocking():
            blocking_calls.append(1)
            raise AssertionError("must not block on the jobs lock")

        body2, etag2 = self._call(_blocking, lambda: None)
        self.assertEqual(blocking_calls, [], "blocking read must not be reached")
        self.assertEqual(body2, body, "should serve the previous body")
        self.assertEqual(etag2, etag)

    def test_cold_cache_still_falls_back_to_blocking_read(self):
        """With nothing cached there is nothing to serve — correctness wins."""
        used = []

        def _blocking():
            used.append(1)
            return _payload(1)

        body, _etag = self._call(_blocking, lambda: None)
        self.assertEqual(used, [1])
        self.assertIn(b"Job 0", body)

    def test_normal_path_still_refreshes_after_the_writer_finishes(self):
        body, etag = self._call(lambda: _payload(2), lambda: _payload(2))
        self.jobs_file.write_text(json.dumps(_payload(5)))
        body2, etag2 = self._call(lambda: _payload(5), lambda: _payload(5))
        self.assertNotEqual(etag, etag2)
        self.assertIn(b"Job 4", body2)


class ServerWiringTests(unittest.TestCase):
    def test_read_jobs_nonblocking_is_wired_into_the_list_endpoint(self):
        src = (ROOT / "dashboard" / "server.py").read_text()
        self.assertIn("def read_jobs_nonblocking()", src)
        self.assertIn("read_jobs_nonblocking=read_jobs_nonblocking", src)
        self.assertIn("fcntl.LOCK_SH | fcntl.LOCK_NB", src)


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
