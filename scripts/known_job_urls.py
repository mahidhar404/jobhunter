#!/usr/bin/env python3
"""Known job URL keys for discovery scrapers — skip re-fetch / re-write.

Collects normalized URL keys (and ``posting:<key>`` tokens) from jobs.json
(all statuses), blocked_urls tombstones, and optional listing JSON files
already on disk. Scrapers use these to avoid detail-page fetches and
duplicate listing rows for postings we already have.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from apply_urls import normalize_url  # noqa: E402
from blocked_urls import block_keys_for_url, load_blocked_url_set  # noqa: E402

JOBS_FILE = ROOT / "jobs.json"


def add_url_keys(keys: set[str], url: str | None) -> None:
    if not url:
        return
    for k in block_keys_for_url(url):
        if k:
            keys.add(k)
    n = normalize_url(url) or str(url).strip()
    if n:
        keys.add(n)


def _add_posting_key_token(keys: set[str], posting: str | None) -> None:
    pk = str(posting or "").strip().lower()
    if not pk:
        return
    if pk.startswith("posting:"):
        keys.add(pk)
    else:
        keys.add(f"posting:{pk}")


def load_jobs_url_keys(jobs_path: Path | None = None) -> set[str]:
    path = jobs_path or JOBS_FILE
    keys: set[str] = set()
    if not path.exists():
        return keys
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return keys
    for job in data.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        for f in ("job_url", "apply_url", "source_url"):
            add_url_keys(keys, job.get(f))
        for u in job.get("alternate_urls") or []:
            add_url_keys(keys, u)
        _add_posting_key_token(keys, job.get("posting_key"))
    return keys


def load_listing_url_keys(*listing_paths: Path) -> set[str]:
    keys: set[str] = set()
    for path in listing_paths:
        if not path or not path.exists():
            continue
        try:
            rows = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            for f in ("job_url", "job_url_direct", "apply_url", "source_url"):
                add_url_keys(keys, item.get(f))
            for u in item.get("alternate_urls") or []:
                add_url_keys(keys, u)
    return keys


def load_known_url_keys(
    *,
    jobs_path: Path | None = None,
    extra_listing_paths: list[Path] | None = None,
    include_blocked: bool = True,
) -> set[str]:
    keys = load_jobs_url_keys(jobs_path)
    if include_blocked:
        keys |= load_blocked_url_set()
    if extra_listing_paths:
        keys |= load_listing_url_keys(*extra_listing_paths)
    return keys


def url_is_known(url: str | None, known: set[str]) -> bool:
    if not url or not known:
        return False
    for k in block_keys_for_url(url):
        if k in known:
            return True
    n = normalize_url(url) or str(url).strip()
    return bool(n and n in known)


def listing_is_known(item: dict | None, known: set[str]) -> bool:
    """True when any URL / posting_key on the listing is already known."""
    if not item or not known or not isinstance(item, dict):
        return False
    for f in ("job_url", "job_url_direct", "apply_url", "source_url"):
        if url_is_known(item.get(f), known):
            return True
    for u in item.get("alternate_urls") or []:
        if url_is_known(u, known):
            return True
    pk = str(item.get("posting_key") or "").strip().lower()
    if pk and (pk in known or f"posting:{pk}" in known):
        return True
    return False


def filter_out_known_listings(
    listings: list[dict], known: set[str],
) -> tuple[list[dict], int]:
    """Drop rows already in the known set. Returns (kept, skipped_count)."""
    if not known:
        return list(listings or []), 0
    kept: list[dict] = []
    skipped = 0
    for item in listings or []:
        if listing_is_known(item, known):
            skipped += 1
            continue
        kept.append(item)
    return kept, skipped


def write_skip_urls_file(path: Path, keys: set[str] | list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = sorted({k for k in keys if k})
    path.write_text(json.dumps(payload) + "\n")
    return path


def load_skip_urls_file(path: Path | None) -> set[str]:
    if not path or not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return set()
    if isinstance(raw, list):
        return {str(x) for x in raw if x}
    if isinstance(raw, dict):
        urls = raw.get("urls") or []
        return {str(x) for x in urls if x}
    return set()


if __name__ == "__main__":
    keys = load_known_url_keys()
    print(f"known url keys: {len(keys)}")
