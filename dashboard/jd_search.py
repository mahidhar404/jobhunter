"""Async JD token search — never used by GET /api/jobs list hot path."""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any


def search_jobs_jd_tokens(
    tokens: list[str],
    *,
    jobs: list[dict] | None = None,
    load_jobs: Callable[[], dict] | None = None,
    load_raw_description: Callable[[dict], tuple[str, str]] | None = None,
    timeout_s: float = 2.5,
    limit: int = 8000,
) -> dict[str, list[str]]:
    """Grep jd_full (preview fallback) for tokens — one pass, timed.

    Returns ``{token: [job_id, ...]}``. Used by GET /api/jobs/search only —
    never call from the slim /api/jobs list path.
    """
    cleaned: list[str] = []
    for raw in tokens or []:
        for part in re.split(r"[\s,]+", str(raw or "").lower()):
            part = part.strip()
            if part and part not in cleaned:
                cleaned.append(part)
            if len(cleaned) >= 16:
                break
        if len(cleaned) >= 16:
            break
    hits: dict[str, list[str]] = {t: [] for t in cleaned}
    if not cleaned:
        return hits
    if jobs is None:
        if load_jobs is None:
            raise ValueError("jobs or load_jobs required")
        jobs = load_jobs().get("jobs", [])
    if load_raw_description is None:
        raise ValueError("load_raw_description required")
    start = time.monotonic()
    scanned = 0
    try:
        timeout = float(timeout_s)
    except (TypeError, ValueError):
        timeout = 2.5
    try:
        max_scan = max(1, int(limit))
    except (TypeError, ValueError):
        max_scan = 8000
    for job in jobs:
        if scanned >= max_scan:
            break
        if time.monotonic() - start > timeout:
            break
        jid = str(job.get("id") or "").strip()
        if not jid:
            continue
        raw, _source = load_raw_description(job)
        scanned += 1
        if not raw:
            continue
        text = raw.lower()
        for tok in cleaned:
            if tok in text:
                hits[tok].append(jid)
    return hits


# Typing hint for callers that wire server loaders.
AnyJob = dict[str, Any]
