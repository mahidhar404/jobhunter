"""Shared posted-date extraction and job-field merge rules.

Exact ``datePosted`` (ld+json / schema.org) beats a relative
"Posted / Reposted N … ago" approximation. Approximate dates land in
``date_posted_fallback`` and render with ``~`` in the dashboard.

Used by Built In scrape, LinkedIn HTTP resolve/backfill, and any other
HTML source that embeds the same patterns.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_DATE_POSTED_RE = re.compile(r'"datePosted"\s*:\s*"([^"]+)"')
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
# Card/header chrome carries only a relative string ("Posted 2 Days Ago",
# "Posted Yesterday", "Reposted 3 Hours Ago"). Day-granular at best, and it
# drifts as the page ages, so it never overwrites a real "datePosted".
_RELATIVE_POSTED_RE = re.compile(
    r"(?:Re)?[Pp]osted\s+(?:(\d+)\+?\s*(minute|hour|day|week|month)s?\s*ago"
    r"|(today|yesterday))",
    re.IGNORECASE,
)
_RELATIVE_UNIT_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30}


def extract_date_posted(html: str) -> tuple[str | None, str | None]:
    """(exact, approximate) posted dates as YYYY-MM-DD; either may be None.

    ``exact`` comes from embedded schema.org JobPosting ``datePosted``.
    ``approximate`` is derived from a relative "Posted N Days Ago" string and
    is only meaningful when ``exact`` is None.
    """
    exact = None
    md = _DATE_POSTED_RE.search(html or "")
    if md:
        raw = md.group(1).strip()
        exact = raw[:10] if _ISO_DATE_RE.match(raw) else raw

    approx = None
    mr = _RELATIVE_POSTED_RE.search(html or "")
    if mr:
        if mr.group(3):
            days = 0 if mr.group(3).lower() == "today" else 1
        else:
            days = int(mr.group(1)) * _RELATIVE_UNIT_DAYS[mr.group(2).lower()]
        approx = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    return exact, approx


def apply_posted_dates(
    job: dict,
    exact: str | None,
    approx: str | None,
    *,
    source: str | None = None,
) -> bool:
    """Merge extracted dates onto ``job``. Exact beats approx; never weaken.

    - Exact fills ``date_posted`` when missing and clears fallback.
    - Approx fills ``date_posted_fallback`` only when neither exact nor
      fallback is already set.
    - Optional ``date_posted_source`` is set when a field is written.

    Returns True when the job dict was mutated.
    """
    if not isinstance(job, dict):
        return False
    exact_s = str(exact).strip() if exact else ""
    approx_s = str(approx).strip() if approx else ""
    changed = False

    if exact_s:
        if not str(job.get("date_posted") or "").strip():
            job["date_posted"] = exact_s
            # Exact wins — drop a weaker relative fallback if present.
            if job.get("date_posted_fallback"):
                job["date_posted_fallback"] = None
            changed = True
        if changed and source:
            job["date_posted_source"] = source
        return changed

    if approx_s:
        if str(job.get("date_posted") or "").strip():
            return False
        if str(job.get("date_posted_fallback") or "").strip():
            return False
        job["date_posted_fallback"] = approx_s
        if source:
            job["date_posted_source"] = source
        return True

    return False
