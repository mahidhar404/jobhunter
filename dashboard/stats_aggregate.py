"""Classical dashboard statistics computed from job dictionaries."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


STUCK_STATUSES = frozenset({"stuck", "blocked_captcha"})
READY_STATUSES = frozenset({"ready_for_review"})
PROGRESS_STATUSES = frozenset(
    {"tailoring", "navigating", "filling", "resuming", "resume_ready"}
)
OPEN_STATUSES = frozenset({"discovered"})
LEGACY_SKIPPED_STATUSES = frozenset(
    {"skipped_manual", "skipped_duplicate", "skipped_contract", "skipped_easy_apply"}
)


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def queue_bucket(status: Any) -> str:
    """Match ``dashboard/static/app.js`` queueBucket classification."""
    status = status if isinstance(status, str) else ""
    if status == "deleted" or status in LEGACY_SKIPPED_STATUSES:
        return "deleted"
    if status in STUCK_STATUSES:
        return "stuck"
    if status in READY_STATUSES:
        return "ready"
    if status in PROGRESS_STATUSES:
        return "progress"
    if status in OPEN_STATUSES or status == "cancelled":
        return "open"
    if status == "applied":
        return "applied"
    return "open"


def _ranked_counts(values: Iterable[str]) -> list[dict[str, Any]]:
    labels: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for raw in values:
        label = raw.strip() or "Unknown"
        key = label.casefold()
        labels.setdefault(key, label)
        counts[key] += 1
    return [
        {"name": labels[key], "count": count}
        for key, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], labels[item[0]].casefold()),
        )
    ]


def _source(job: dict[str, Any]) -> str:
    value = job.get("source") or job.get("site") or "Unknown"
    return str(value)


def _city(applied_address: Any) -> str | None:
    if isinstance(applied_address, dict):
        city = str(applied_address.get("city") or "").strip()
        return city or None
    if not isinstance(applied_address, str):
        return None
    parts = [part.strip() for part in applied_address.split(",") if part.strip()]
    if len(parts) >= 3:
        return parts[-2]
    if len(parts) == 2:
        return parts[0]
    return None


def aggregate_stats(
    jobs: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return deterministic aggregate statistics without mutating ``jobs``."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    week_start = current - timedelta(days=7)
    month_start = current - timedelta(days=30)

    active_jobs = [job for job in jobs if queue_bucket(job.get("status")) != "deleted"]
    funnel = {name: 0 for name in ("open", "ready", "stuck", "progress", "applied")}
    applied_week = 0
    applied_month = 0
    over_14d = 0
    over_30d = 0
    due: list[tuple[datetime, dict[str, Any]]] = []
    cities: list[str] = []

    for job in active_jobs:
        bucket = queue_bucket(job.get("status"))
        funnel[bucket] += 1

        if bucket == "open":
            created_at = _as_utc(job.get("created_at"))
            if created_at is not None:
                age = current - created_at
                if age > timedelta(days=14):
                    over_14d += 1
                if age > timedelta(days=30):
                    over_30d += 1

        if bucket != "applied":
            continue
        applied_at = _as_utc(job.get("applied_at"))
        if applied_at is not None:
            if applied_at >= week_start:
                applied_week += 1
            if applied_at >= month_start:
                applied_month += 1
            if applied_at <= week_start:
                due.append(
                    (
                        applied_at,
                        {
                            "id": job.get("id"),
                            "company": job.get("company"),
                            "title": job.get("title"),
                            "applied_at": job.get("applied_at"),
                        },
                    )
                )
        city = _city(job.get("applied_address"))
        if city:
            cities.append(city)

    due.sort(key=lambda item: (item[0], str(item[1].get("id") or "")))
    return {
        "applied_week": applied_week,
        "applied_month": applied_month,
        "funnel": funnel,
        "open_aging": {"over_14d": over_14d, "over_30d": over_30d},
        "by_source": _ranked_counts(_source(job) for job in active_jobs),
        "by_city": _ranked_counts(cities),
        "follow_ups_due": [item for _, item in due],
    }
