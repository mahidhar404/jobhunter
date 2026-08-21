#!/usr/bin/env python3
"""Unit tests for adaptive discovery recency (N+1, floor 7, cap 10)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from adaptive_recency import (  # noqa: E402
    adaptive_recency_days,
    clamp_adaptive_days,
    newest_job_age_days,
    snap_builtin_days,
    unclamped_adaptive_days,
)


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def test_five_days_ago_unclamped_is_six() -> None:
    assert unclamped_adaptive_days(5) == 6


def test_five_days_ago_clamped_floor_seven() -> None:
    last = NOW - timedelta(days=5)
    assert adaptive_recency_days(last, now=NOW) == 7
    assert clamp_adaptive_days(6) == 7


def test_floor_seven_on_same_day() -> None:
    last = NOW - timedelta(hours=3)
    assert adaptive_recency_days(last, now=NOW) == 7


def test_twenty_days_ago_is_twenty_one() -> None:
    last = NOW - timedelta(days=20)
    assert adaptive_recency_days(last, now=NOW) == 10
    assert unclamped_adaptive_days(20) == 21


def test_cap_ten() -> None:
    last = NOW - timedelta(days=40)
    assert adaptive_recency_days(last, now=NOW) == 10
    assert clamp_adaptive_days(11) == 10


def test_never_run_uses_floor() -> None:
    assert adaptive_recency_days(None, now=NOW) == 7


def test_never_run_long_jobs_gap_uses_cap() -> None:
    assert adaptive_recency_days(None, now=NOW, jobs_gap_days=45) == 10


def test_never_run_moderate_jobs_gap() -> None:
    # 10 days since newest job → 11, clamped to cap 10
    assert adaptive_recency_days(None, now=NOW, jobs_gap_days=10) == 10


def test_snap_builtin_clamps_1_to_10() -> None:
    assert snap_builtin_days(7) == 7
    assert snap_builtin_days(8) == 8
    assert snap_builtin_days(6) == 6
    assert snap_builtin_days(1) == 1
    assert snap_builtin_days(11) == 10
    assert snap_builtin_days(0) == 1


def test_newest_job_age_days() -> None:
    payload = {
        "jobs": [
            {"created_at": "2026-08-09T12:00:00+00:00"},
            {"created_at": "2026-08-01T12:00:00+00:00"},
        ]
    }
    assert newest_job_age_days(payload, now=NOW) == 10


if __name__ == "__main__":
    test_five_days_ago_unclamped_is_six()
    test_five_days_ago_clamped_floor_seven()
    test_floor_seven_on_same_day()
    test_twenty_days_ago_is_twenty_one()
    test_cap_ten()
    test_never_run_uses_floor()
    test_never_run_long_jobs_gap_uses_cap()
    test_never_run_moderate_jobs_gap()
    test_snap_builtin_clamps_1_to_10()
    test_newest_job_age_days()
    print("ok: adaptive recency tests passed")
