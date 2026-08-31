#!/usr/bin/env python3
"""listings.db is the archive that makes filtering and aborts non-destructive."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import listings_db  # noqa: E402


def _row(url: str, **kw) -> dict:
    base = {
        "title": "Data Engineer", "company": "Acme", "site": "himalayas",
        "job_url": url, "job_url_direct": url, "description": "",
        "date_posted": None, "job_type": "fulltime", "location": "Remote",
        "search_term": "ww:himalayas",
    }
    base.update(kw)
    return base


class ListingsDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "listings.db"
        self.conn = listings_db.connect(self.db)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_ingest_is_idempotent_by_url(self):
        rows = [_row("https://example.com/job/1"), _row("https://example.com/job/2")]
        new, updated = listings_db.ingest_rows(self.conn, rows)
        self.assertEqual((new, updated), (2, 0))
        new2, updated2 = listings_db.ingest_rows(self.conn, rows)
        self.assertEqual((new2, updated2), (0, 2), "re-ingest must refresh, not duplicate")
        self.assertEqual(listings_db.stats(self.conn)["total"], 2)

    def test_first_seen_is_preserved_across_reingest(self):
        listings_db.ingest_rows(self.conn, [_row("https://example.com/job/1")])
        first = self.conn.execute(
            "SELECT first_seen_at FROM listings").fetchone()[0]
        listings_db.ingest_rows(
            self.conn, [_row("https://example.com/job/1", title="Renamed")])
        after, title = self.conn.execute(
            "SELECT first_seen_at, title FROM listings").fetchone()
        self.assertEqual(after, first, "first_seen_at must not move")
        self.assertEqual(title, "Renamed", "other fields must refresh")

    def test_rows_without_a_url_are_skipped_not_fatal(self):
        new, _ = listings_db.ingest_rows(
            self.conn, [_row(""), {"title": "no url"}, _row("https://x.test/j/9")])
        self.assertEqual(new, 1)

    def test_lane_is_stamped_for_filtering(self):
        listings_db.ingest_rows(self.conn, [
            _row("https://x.test/j/in", location="Bengaluru, India"),
            _row("https://x.test/j/ww", location="Berlin"),
        ])
        lanes = dict(listings_db.stats(self.conn)["by_lane"])
        self.assertEqual(lanes.get("india"), 1)
        self.assertEqual(lanes.get("worldwide"), 1)

    def test_backfill_reads_raw_files_and_skips_qualified(self):
        d = Path(self.tmp.name) / "listings"
        d.mkdir()
        (d / "2026-08-25-himalayas.json").write_text(
            json.dumps([_row("https://x.test/j/a")]))
        (d / "2026-08-25-qualified-himalayas.json").write_text(
            json.dumps([_row("https://x.test/j/b")]))
        res = listings_db.backfill(self.conn, d)
        self.assertEqual(res["new"], 1, "qualified files are post-filter duplicates")

    def test_corrupt_listing_file_does_not_raise(self):
        d = Path(self.tmp.name) / "bad"
        d.mkdir()
        (d / "x.json").write_text("{not json")
        # The tuple-shaped file the ww scraper used to emit: [[rows], count]
        (d / "y.json").write_text(json.dumps([[_row("https://x.test/j/c")], 3]))
        res = listings_db.backfill(self.conn, d)
        self.assertEqual(res["new"], 0)


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
