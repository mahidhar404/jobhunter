#!/usr/bin/env python3
"""Durable blocklist of apply/job URLs the user deleted from the dashboard.

Soft-delete keeps jobs in jobs.json with status=deleted; Empty Deleted
removes full rows but leaves URL/id tombstones here so discovery never
re-adds the same listing (write_discovered_jobs / dedup_listings skip
these keys).

Format (blocked_urls.json):
  {
    "urls": ["https://normalized...", ...],
    "ids": ["job-id", ...],
    "tombstones": [{"id", "company", "title", "url"}, ...],
    "updated_at": "..."
  }
"""
from __future__ import annotations

import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path

from apply_urls import normalize_url
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).parent.parent
BLOCKED_URLS_FILE = ROOT / "blocked_urls.json"
LOCK_FILE = BLOCKED_URLS_FILE.with_suffix(".json.lock")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty() -> dict:
    return {"urls": [], "ids": [], "tombstones": [], "updated_at": None}


def _path_only_key(url: str) -> str:
    """Scheme+host+path only — catches the same posting when tracking
    query params (gh_src, etc.) differ between scrapes."""
    key = normalize_url(url) or (url or "").strip()
    if not key:
        return ""
    try:
        p = urlparse(key)
    except ValueError:
        return key.split("?", 1)[0].rstrip("/")
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (p.path or "").rstrip("/")
    scheme = (p.scheme or "https").lower()
    return urlunparse((scheme, host, path, "", "", ""))


def block_keys_for_url(url: str) -> list[str]:
    """Normalized keys that should all match this apply/job URL."""
    keys: list[str] = []
    n = normalize_url(url) or (url or "").strip()
    if n:
        keys.append(n)
    po = _path_only_key(url)
    if po and po not in keys:
        keys.append(po)
    return keys


def _read_unlocked() -> dict:
    if not BLOCKED_URLS_FILE.exists():
        return _empty()
    try:
        raw = json.loads(BLOCKED_URLS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(raw, dict):
        return _empty()
    urls = raw.get("urls") or []
    ids = raw.get("ids") or []
    tombs = raw.get("tombstones") or []
    slim = []
    for t in tombs:
        if isinstance(t, dict) and (t.get("id") or t.get("url")):
            slim.append({
                "id": t.get("id"),
                "company": t.get("company"),
                "title": t.get("title"),
                "url": t.get("url"),
            })
    return {
        "urls": [u for u in urls if isinstance(u, str) and u],
        "ids": [i for i in ids if isinstance(i, str) and i],
        "tombstones": slim,
        "updated_at": raw.get("updated_at"),
    }


def load_blocked_url_set() -> set[str]:
    """Normalized URL keys currently blocked (read-only, no lock needed for
    discovery skip checks — stale reads only risk a brief re-add race that
    the next delete would re-block)."""
    data = _read_unlocked()
    out: set[str] = set()
    for u in data["urls"]:
        for key in block_keys_for_url(u):
            out.add(key)
    return out


def urls_from_job(job: dict | None) -> list[str]:
    """Collect every apply/job URL on a jobs.json entry."""
    if not isinstance(job, dict):
        return []
    found: list[str] = []
    for f in ("apply_url", "job_url", "source_url"):
        u = job.get(f)
        if u:
            found.append(str(u))
    for u in job.get("alternate_urls") or []:
        if u:
            found.append(str(u))
    return found


def _block_payload_for_job(
    job: dict | None, *, keep_tombstone: bool = True
) -> tuple[list[str], str | None, dict | None]:
    """Normalize one job into (url_keys, job_id, optional tombstone)."""
    if not isinstance(job, dict):
        return [], None, None
    raw_urls = urls_from_job(job)
    job_id = job.get("id")
    job_id_s = str(job_id) if job_id else None
    normed: list[str] = []
    for u in raw_urls:
        normed.extend(block_keys_for_url(u))
    seen: set[str] = set()
    uniq: list[str] = []
    for k in normed:
        if k and k not in seen:
            seen.add(k)
            uniq.append(k)
    normed = uniq
    if not normed and not job_id_s:
        return [], None, None

    primary_url = str(
        job.get("apply_url") or job.get("job_url") or job.get("source_url") or ""
    )
    tomb = None
    if keep_tombstone:
        tomb = {
            "id": job_id_s,
            "company": job.get("company"),
            "title": job.get("title"),
            "url": primary_url or (normed[0] if normed else None),
        }
    return normed, job_id_s, tomb


def _merge_block_payloads(
    data: dict,
    payloads: list[tuple[list[str], str | None, dict | None]],
) -> list[str]:
    """Mutate blocklist `data` with many payloads; return all URL keys added."""
    url_set: set[str] = set()
    for u in data["urls"]:
        url_set.update(block_keys_for_url(u))
    id_set = set(data["ids"])
    all_keys: list[str] = []
    new_tombs: list[dict] = []
    replace_ids: set[str] = set()
    replace_urls: set[str] = set()
    for normed, job_id, tomb in payloads:
        for key in normed:
            url_set.add(key)
            all_keys.append(key)
        if job_id:
            id_set.add(job_id)
        if tomb:
            tid = tomb.get("id")
            turl = tomb.get("url")
            if tid:
                replace_ids.add(str(tid))
            if turl:
                replace_urls.add(str(turl))
            new_tombs.append(tomb)

    if new_tombs:
        base = [
            t for t in (data.get("tombstones") or [])
            if isinstance(t, dict)
            and not (
                (t.get("id") and str(t.get("id")) in replace_ids)
                or (t.get("url") and str(t.get("url")) in replace_urls)
            )
        ]
        uniq_tombs: list[dict] = []
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        for tomb in new_tombs:
            tid = str(tomb.get("id") or "")
            turl = str(tomb.get("url") or "")
            if tid and tid in seen_ids:
                continue
            if (not tid) and turl and turl in seen_urls:
                continue
            if tid:
                seen_ids.add(tid)
            if turl:
                seen_urls.add(turl)
            uniq_tombs.append(tomb)
        data["tombstones"] = base + uniq_tombs
    elif "tombstones" not in data:
        data["tombstones"] = []

    data["urls"] = sorted(url_set)
    data["ids"] = sorted(id_set)
    data["updated_at"] = _now_iso()
    return all_keys


def block_deleted_job(job: dict | None, *, keep_tombstone: bool = True) -> list[str]:
    """Persist URLs (+ job id) so discovery never re-adds this listing.

    When keep_tombstone is True, also stores a slim {id, company, title, url}
    record for Empty Deleted / audit. Returns the normalized URL keys written.
    """
    return block_deleted_jobs_batch([job] if job is not None else [], keep_tombstone=keep_tombstone)


def block_deleted_jobs_batch(
    jobs: list | None, *, keep_tombstone: bool = True
) -> list[str]:
    """Persist URL/id tombstones for many jobs in a single lock + write.

    Soft-delete already blocks on each delete; Empty Deleted must still ensure
    tombstones exist before purging rows — but calling block_deleted_job once
    per job rewrites blocked_urls.json thousands of times (~minutes) and
    freezes the dashboard. One batch write keeps Empty Deleted snappy.
    """
    payloads: list[tuple[list[str], str | None, dict | None]] = []
    for job in jobs or []:
        payload = _block_payload_for_job(job, keep_tombstone=keep_tombstone)
        if payload[0] or payload[1]:
            payloads.append(payload)
    if not payloads:
        return []

    LOCK_FILE.touch(exist_ok=True)
    with open(LOCK_FILE, "r+") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            data = _read_unlocked()
            all_keys = _merge_block_payloads(data, payloads)
            BLOCKED_URLS_FILE.write_text(json.dumps(data, indent=2) + "\n")
            # Dedupe return while preserving order.
            seen: set[str] = set()
            out: list[str] = []
            for k in all_keys:
                if k not in seen:
                    seen.add(k)
                    out.append(k)
            return out
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def is_url_blocked(url: str, blocked: set[str] | None = None) -> bool:
    s = blocked if blocked is not None else load_blocked_url_set()
    return any(k in s for k in block_keys_for_url(url))


def unblock_job(job: dict | None) -> dict:
    """Remove a listing from the durable blocklist (soft-delete restore).

    Clears matching URL keys, job id, and tombstones. Safe no-op if nothing
    matched. Returns {"urls": [...], "ids": [...], "tombstones_removed": N}.
    """
    raw_urls = urls_from_job(job)
    job_id = (job or {}).get("id") if isinstance(job, dict) else None
    remove_keys: set[str] = set()
    for u in raw_urls:
        remove_keys.update(k for k in block_keys_for_url(u) if k)
    # Also drop path/normalize variants stored against primary fields alone.
    if isinstance(job, dict):
        for f in ("apply_url", "job_url", "source_url"):
            u = job.get(f)
            if u:
                remove_keys.update(k for k in block_keys_for_url(str(u)) if k)

    if not remove_keys and not job_id:
        return {"urls": [], "ids": [], "tombstones_removed": 0}

    LOCK_FILE.touch(exist_ok=True)
    with open(LOCK_FILE, "r+") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            data = _read_unlocked()
            url_set: set[str] = set()
            for u in data["urls"]:
                url_set.update(block_keys_for_url(u))
            removed_urls = sorted(url_set & remove_keys)
            url_set -= remove_keys
            id_set = set(data["ids"])
            removed_ids: list[str] = []
            if job_id and str(job_id) in id_set:
                id_set.remove(str(job_id))
                removed_ids.append(str(job_id))
            tombs = list(data.get("tombstones") or [])
            keep_tombs = []
            tombs_removed = 0
            for t in tombs:
                if not isinstance(t, dict):
                    continue
                tid = t.get("id")
                turl = t.get("url") or ""
                tkeys = set(block_keys_for_url(turl)) if turl else set()
                drop = bool(
                    (job_id and tid == str(job_id))
                    or (tkeys & remove_keys)
                )
                if drop:
                    tombs_removed += 1
                else:
                    keep_tombs.append(t)
            data["urls"] = sorted(url_set)
            data["ids"] = sorted(id_set)
            data["tombstones"] = keep_tombs
            data["updated_at"] = _now_iso()
            BLOCKED_URLS_FILE.write_text(json.dumps(data, indent=2) + "\n")
            return {
                "urls": removed_urls,
                "ids": removed_ids,
                "tombstones_removed": tombs_removed,
            }
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)
