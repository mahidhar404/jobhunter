#!/usr/bin/env python3
"""Durable archive of every scraped listing (SQLite).

Why this exists: `listings/*.json` are per-source, per-day scratch files that
get overwritten on the next run, and `jobs.json` only holds rows that survived
the relevance / lane / prune filters. Anything a scraper found but the pipeline
dropped — or anything scraped while a run was interrupted — used to be
unrecoverable. This store keeps the raw row for every listing ever scraped,
keyed by normalized URL, so a filter change or an aborted run can never lose
data again.

It is an archive, not the pipeline: jobs.json stays the source of truth for
the job list. Nothing here filters, prunes, or de-duplicates by heuristic —
the only key is the normalized URL.

Usage:
  python3 listings_db.py ingest listings/2026-08-25-himalayas.json [...]
  python3 listings_db.py backfill            # every listings/*.json on disk
  python3 listings_db.py stats
  python3 listings_db.py export --site nodesk --out rows.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))

DB_PATH = ROOT / "listings.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    url_key       TEXT PRIMARY KEY,
    job_url       TEXT NOT NULL,
    title         TEXT,
    company       TEXT,
    site          TEXT,
    location      TEXT,
    lane          TEXT,
    date_posted   TEXT,
    search_term   TEXT,
    description   TEXT,
    raw           TEXT NOT NULL,
    source_file   TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listings_site ON listings(site);
CREATE INDEX IF NOT EXISTS idx_listings_lane ON listings(lane);
CREATE INDEX IF NOT EXISTS idx_listings_first_seen ON listings(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_listings_company ON listings(company);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")      # readers never block the writer
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def _url_key(url: str) -> str:
    """Normalized key. Falls back to the raw URL if apply_urls is unavailable."""
    try:
        from apply_urls import normalize_url
        return (normalize_url(url) or url or "").strip().lower()
    except Exception:
        return (url or "").strip().lower()


def _lane_for(row: dict) -> str | None:
    try:
        from discovery_filters import lane_for_job
        return lane_for_job(
            row.get("location"),
            title=row.get("title"),
            description=row.get("description") or "",
        )
    except Exception:
        return None


def ingest_rows(conn: sqlite3.Connection, rows: list[dict], *,
                source_file: str | None = None) -> tuple[int, int]:
    """Insert/refresh rows. Returns (new, updated)."""
    now = _now()
    new = updated = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        url = (row.get("job_url") or row.get("job_url_direct") or "").strip()
        if not url:
            continue
        key = _url_key(url)
        if not key:
            continue
        cur = conn.execute("SELECT 1 FROM listings WHERE url_key = ?", (key,))
        exists = cur.fetchone() is not None
        payload = (
            key, url, row.get("title"), row.get("company"), row.get("site"),
            row.get("location"), _lane_for(row), row.get("date_posted"),
            row.get("search_term"), row.get("description"),
            json.dumps(row, ensure_ascii=False), source_file, now, now,
        )
        if exists:
            # Keep first_seen_at; refresh everything else.
            conn.execute(
                """UPDATE listings SET job_url=?, title=?, company=?, site=?,
                       location=?, lane=?, date_posted=?, search_term=?,
                       description=?, raw=?, source_file=?, last_seen_at=?
                   WHERE url_key=?""",
                (url, row.get("title"), row.get("company"), row.get("site"),
                 row.get("location"), _lane_for(row), row.get("date_posted"),
                 row.get("search_term"), row.get("description"),
                 json.dumps(row, ensure_ascii=False), source_file, now, key),
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO listings (url_key, job_url, title, company, site,
                       location, lane, date_posted, search_term, description,
                       raw, source_file, first_seen_at, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload)
            new += 1
    conn.commit()
    return new, updated


def ingest_file(conn: sqlite3.Connection, path: Path) -> tuple[int, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return (0, 0)
    if not isinstance(data, list):
        return (0, 0)
    rows = [r for r in data if isinstance(r, dict)]
    return ingest_rows(conn, rows, source_file=path.name)


def backfill(conn: sqlite3.Connection, listings_dir: Path | None = None) -> dict:
    """Ingest every raw listing file on disk (skips *-qualified-* duplicates)."""
    base = listings_dir or (ROOT / "listings")
    total_new = total_upd = files = 0
    for path in sorted(base.glob("*.json")):
        if "qualified" in path.name:
            continue
        n, u = ingest_file(conn, path)
        if n or u:
            files += 1
        total_new += n
        total_upd += u
    return {"files": files, "new": total_new, "updated": total_upd}


def rows_missing_from_jobs(conn: sqlite3.Connection, *,
                           jobs_path: Path | None = None) -> list[dict]:
    """Archived listings that never reached jobs.json (any status).

    A row lands here when it was scraped but the merge never ran for it — an
    aborted pass, a listing file overwritten by the next run, a crash between
    scrape and dedup. Rows the pipeline *evaluated* and pruned are excluded:
    those are in jobs.json (status deleted) or carry a blocked-URL tombstone.
    """
    from apply_urls import normalize_url
    from blocked_urls import block_keys_for_url, load_blocked_url_set

    path = jobs_path or (ROOT / "jobs.json")
    known: set[str] = set()
    try:
        jobs = json.loads(path.read_text(encoding="utf-8")).get("jobs") or []
    except (OSError, json.JSONDecodeError):
        jobs = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        for field in ("job_url", "apply_url", "source_url"):
            url = job.get(field)
            if not url:
                continue
            known |= {k for k in block_keys_for_url(url) if k}
            n = normalize_url(url) or url
            if n:
                known.add(n)
    try:
        known |= load_blocked_url_set()
    except Exception:
        pass

    out: list[dict] = []
    for url, raw in conn.execute("SELECT job_url, raw FROM listings"):
        keys = {k for k in block_keys_for_url(url) if k}
        n = normalize_url(url) or url
        if n:
            keys.add(n)
        if keys & known:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    by_site = conn.execute(
        "SELECT site, COUNT(*) FROM listings GROUP BY site ORDER BY 2 DESC"
    ).fetchall()
    by_lane = conn.execute(
        "SELECT lane, COUNT(*) FROM listings GROUP BY lane ORDER BY 2 DESC"
    ).fetchall()
    return {"total": total, "by_site": by_site, "by_lane": by_lane}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_ing = sub.add_parser("ingest")
    p_ing.add_argument("files", nargs="+")
    sub.add_parser("backfill")
    sub.add_parser("stats")
    p_rec = sub.add_parser(
        "reconcile",
        help="write archived listings that never reached jobs.json to --out")
    p_rec.add_argument("--out", required=True)
    p_rec.add_argument("--jobs", default=None)
    p_exp = sub.add_parser("export")
    p_exp.add_argument("--site")
    p_exp.add_argument("--lane")
    p_exp.add_argument("--out", required=True)
    for p in (p_ing, p_exp, p_rec):
        p.add_argument("--db", default=None)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = connect(Path(args.db) if getattr(args, "db", None) else None)
    if args.cmd == "ingest":
        n = u = 0
        for f in args.files:
            a, b = ingest_file(conn, Path(f))
            n += a
            u += b
        print(f"listings.db: +{n} new, {u} refreshed")
    elif args.cmd == "backfill":
        res = backfill(conn)
        print(f"listings.db backfill: {res['files']} file(s), "
              f"+{res['new']} new, {res['updated']} refreshed")
    elif args.cmd == "stats":
        s = stats(conn)
        print(f"listings.db: {s['total']} listing(s)")
        print("  by lane:", dict(s["by_lane"]))
        print("  by site:")
        for site, n in s["by_site"]:
            print(f"    {str(site):20s} {n}")
    elif args.cmd == "reconcile":
        rows = rows_missing_from_jobs(
            conn, jobs_path=Path(args.jobs) if args.jobs else None)
        Path(args.out).write_text(
            json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"reconcile: {len(rows)} archived listing(s) not in jobs.json "
              f"-> {args.out}")
    elif args.cmd == "export":
        q = "SELECT raw FROM listings WHERE 1=1"
        params: list = []
        if args.site:
            q += " AND site = ?"
            params.append(args.site)
        if args.lane:
            q += " AND lane = ?"
            params.append(args.lane)
        rows = [json.loads(r[0]) for r in conn.execute(q, params)]
        Path(args.out).write_text(
            json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"exported {len(rows)} listing(s) -> {args.out}")
    conn.close()


if __name__ == "__main__":
    main()
