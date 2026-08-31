#!/usr/bin/env python3
""""0 listings" must distinguish "nothing new" from "board broken".

Every scraper filters its results against skip-urls (every URL already in
jobs.json, any status, plus blocked-URL tombstones). On a second pass over the
same boards that legitimately removes 100% of what was found — Himalayas found
50 and dropped 50, NoDesk found 102 and dropped 102 — and the source row then
read "0 listings", identical to a scraper that crashed. The user reasonably
concluded every worldwide board had stopped working.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


class SkippedKnownParsingTests(unittest.TestCase):
    def _log(self, text: str) -> Path:
        d = Path(self.tmp.name) / "x.log"
        d.write_text(text, encoding="utf-8")
        return d

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ww_board_skip_line(self):
        p = self._log("[20:43] scraping worldwide board: himalayas\n"
                      "[20:43] skip-urls: dropped 50 known\n"
                      "[20:43] wrote 0 listings -> x.json\n")
        self.assertEqual(srv._skipped_known_from_log(p), 50)

    def test_api_scraper_skip_line(self):
        p = self._log("[20:43] skip-urls: 3233 known key(s)\n"
                      "[20:43]   got 18 relevant results from remoteok/api\n"
                      "[20:43] skipped 18 already-known URL(s)\n")
        self.assertEqual(srv._skipped_known_from_log(p), 18,
                         "the 3233 known-keys line is the filter size, not a skip count")

    def test_multiple_skip_lines_accumulate(self):
        p = self._log("skip-urls: dropped 10 known\nskipped 5 already-known URL(s)\n")
        self.assertEqual(srv._skipped_known_from_log(p), 15)

    def test_a_genuinely_empty_board_reports_zero(self):
        p = self._log("[20:43] probing board: turing\n"
                      "[20:43] no public listings for turing: needs an account\n"
                      "[20:43] wrote 0 listings -> x.json\n")
        self.assertEqual(srv._skipped_known_from_log(p), 0)

    def test_missing_log_is_not_fatal(self):
        self.assertEqual(
            srv._skipped_known_from_log(Path(self.tmp.name) / "nope.log"), 0)


class FinalizeWiringTests(unittest.TestCase):
    def test_zero_with_skips_reports_already_in_your_list(self):
        src = (ROOT / "dashboard" / "server.py").read_text()
        self.assertIn("No new roles —", src)
        self.assertIn("_skipped_known_from_log(", src)

    def test_zero_detail_is_only_used_when_the_board_found_nothing_new(self):
        src = (ROOT / "dashboard" / "server.py").read_text()
        block = src[src.index("def _finalize_discovery_source"):]
        block = block[:block.index("def _run_discovery_apply_resolve")]
        self.assertIn("if not counts.get(source_id):", block,
                      "boards that produced rows must keep their real count")


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
