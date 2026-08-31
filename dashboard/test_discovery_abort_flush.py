#!/usr/bin/env python3
"""Stopping discovery must never throw away already-scraped listings.

Regression: an abort called `_kill_all_discovery_procs()`, which killed every
registered subprocess — including the dedup / write-into-jobs.json steps that
were explicitly marked `protect_from_abort=True`. The protection was a single
global bool that any of the ~28 parallel scrapes reset to False as it launched,
so it almost never held. Result: thousands of rows sat in listings/*.json and
never reached jobs.json, and the run reported "Aborted by user" with 0 added.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


class _FakeProc:
    def __init__(self, name: str) -> None:
        self.name = name
        self.killed = False


class AbortProtectsMergeStepsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._procs = dict(srv._discovery_procs_by_key)
        self._protected = set(srv._discovery_protected_keys)
        srv._discovery_procs_by_key.clear()
        srv._discovery_protected_keys.clear()

    def tearDown(self) -> None:
        srv._discovery_procs_by_key.clear()
        srv._discovery_procs_by_key.update(self._procs)
        srv._discovery_protected_keys.clear()
        srv._discovery_protected_keys.update(self._protected)

    def test_kill_all_spares_protected_merge_steps(self):
        scrape_a = _FakeProc("scrape:hirist")
        scrape_b = _FakeProc("scrape:naukri")
        write = _FakeProc("write:hirist")
        srv._discovery_procs_by_key.update({
            "discovery:src:hirist": scrape_a,
            "discovery:src:naukri": scrape_b,
            "discovery:write:hirist": write,
        })
        srv._discovery_protected_keys.add("discovery:write:hirist")

        killed: list[str] = []
        with mock.patch.object(srv, "_kill_process_tree",
                               side_effect=lambda p: killed.append(p.name)):
            srv._kill_all_discovery_procs()

        self.assertIn("scrape:hirist", killed)
        self.assertIn("scrape:naukri", killed)
        self.assertNotIn(
            "write:hirist", killed,
            "the jobs.json write must survive Stop — killing it strands "
            "every scraped listing on disk")

    def test_parallel_scrape_launch_cannot_unprotect_a_merge(self):
        """The old single global bool was reset by each scrape that launched."""
        write = _FakeProc("write:cutshort")
        srv._discovery_procs_by_key["discovery:write:cutshort"] = write
        srv._discovery_protected_keys.add("discovery:write:cutshort")

        # A scrape launching concurrently registers itself unprotected.
        scrape = _FakeProc("scrape:shine")
        srv._discovery_procs_by_key["discovery:src:shine"] = scrape
        srv._discovery_protected_keys.discard("discovery:src:shine")

        killed: list[str] = []
        with mock.patch.object(srv, "_kill_process_tree",
                               side_effect=lambda p: killed.append(p.name)):
            srv._kill_all_discovery_procs()

        self.assertEqual(killed, ["scrape:shine"])


class AbortOutcomeTests(unittest.TestCase):
    def test_abort_message_no_longer_claims_a_discarded_run(self):
        """A stop with nothing scraped says so; a stop with merges is a success."""
        src = (ROOT / "dashboard" / "server.py").read_text()
        self.assertIn("Stopped — no listings had been scraped yet", src)
        # The success branch must still be taken when anything merged.
        self.assertIn("if merges_ok > 0:", src)

    def test_final_flush_loop_is_guarded_per_source(self):
        src = (ROOT / "dashboard" / "server.py").read_text()
        self.assertIn("discovery final flush:", src)
        self.assertIn("warn: final flush failed for", src)


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
