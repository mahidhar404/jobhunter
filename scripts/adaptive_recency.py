#!/usr/bin/env python3
"""Adaptive discovery recency window.

If the last *successful* Discover finished N days ago, scrape with a
``N + 1`` day lookback (one extra day of safety), then clamp:

- floor 7 so a daily run cannot miss jobs the way Built In's old 1-day
  filter did
- cap 10 (Discover UI 1–10 day window) so a long gap does not scrape forever

Never-run / unknown last success uses the floor, unless ``jobs.json``
shows a long gap (then the cap).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

ADAPTIVE_DAYS_FLOOR = 7
ADAPTIVE_DAYS_CAP = 10
# Discover UI + Built In ``daysSinceUpdated``: any integer 1–10.
BUILTIN_SUPPORTED_DAYS = tuple(range(1, 11))


def days_since_timestamp(last_success: datetime, *, now: datetime | None = None) -> int:
    """Whole 24h periods since ``last_success`` (0 if in the future)."""
    now = now or datetime.now(timezone.utc)
    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    seconds = (now - last_success).total_seconds()
    if seconds < 0:
        return 0
    return int(seconds // 86400)


def unclamped_adaptive_days(days_since_success: int) -> int:
    """Raw formula: last success N days ago → N+1 lookback."""
    return max(0, int(days_since_success)) + 1


def clamp_adaptive_days(
    days: int,
    *,
    floor: int = ADAPTIVE_DAYS_FLOOR,
    cap: int = ADAPTIVE_DAYS_CAP,
) -> int:
    floor = max(1, int(floor))
    cap = max(floor, int(cap))
    return max(floor, min(cap, int(days)))


def adaptive_recency_days(
    last_success: datetime | None,
    *,
    now: datetime | None = None,
    floor: int = ADAPTIVE_DAYS_FLOOR,
    cap: int = ADAPTIVE_DAYS_CAP,
    jobs_gap_days: int | None = None,
) -> int:
    """Return the lookback window in days.

    * Last success known: ``clamp(N+1)``.
    * Never-run: ``floor``, or ``clamp(jobs_gap+1)`` when a jobs.json gap
      is known (cap if the gap is huge).
    """
    now = now or datetime.now(timezone.utc)
    if last_success is not None:
        n = days_since_timestamp(last_success, now=now)
        return clamp_adaptive_days(unclamped_adaptive_days(n), floor=floor, cap=cap)
    if jobs_gap_days is None:
        return clamp_adaptive_days(floor, floor=floor, cap=cap)
    if int(jobs_gap_days) >= cap:
        return cap
    return clamp_adaptive_days(
        unclamped_adaptive_days(int(jobs_gap_days)), floor=floor, cap=cap
    )


def snap_builtin_days(
    days: int,
    supported: tuple[int, ...] = BUILTIN_SUPPORTED_DAYS,
) -> int:
    """Round up to the next Built In-supported New Jobs value."""
    ordered = tuple(sorted(int(v) for v in supported))
    for value in ordered:
        if value >= days:
            return value
    return ordered[-1]


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def newest_job_age_days(
    jobs_payload,
    *,
    now: datetime | None = None,
) -> int | None:
    """Age in whole days of the newest ``created_at`` in a jobs.json payload."""
    now = now or datetime.now(timezone.utc)
    if isinstance(jobs_payload, dict):
        jobs = jobs_payload.get("jobs") or []
    elif isinstance(jobs_payload, list):
        jobs = jobs_payload
    else:
        return None
    newest: datetime | None = None
    for job in jobs:
        if not isinstance(job, dict):
            continue
        dt = parse_iso_datetime(job.get("created_at") or job.get("updated_at"))
        if dt is None:
            continue
        if newest is None or dt > newest:
            newest = dt
    if newest is None:
        return None
    return days_since_timestamp(newest, now=now)


def newest_job_age_days_from_file(
    path: Path,
    *,
    now: datetime | None = None,
) -> int | None:
    try:
        import json
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    return newest_job_age_days(raw, now=now)
