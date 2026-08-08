#!/usr/bin/env python3
"""Dashboard Ready promotion must require honest report gates (not hold alone)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


def test_report_allows_ready_requires_honest_flag():
    # Ready flag alone is not enough when blocker blocks claim
    assert (
        srv._report_allows_ready(
            {
                "ready_for_review": True,
                "verdict": "FAIL",
                "blocker": "auth_wall",
            }
        )
        is False
    )
    # Honest clean SUCCESS + live vision judge
    assert (
        srv._report_allows_ready(
            {
                "ready_for_review": True,
                "verdict": "SUCCESS",
                "blocker": None,
                "leftovers": [],
                "required_empty_after_fill": [],
                "required_empty_before_advance": [],
                "vision_judge_live": {
                    "complete": True,
                    "verdict": "COMPLETE",
                    "empty_fields": [],
                    "never_submit": True,
                },
            }
        )
        is True
    )
    # Missing vision → not Ready (FILL-005)
    assert (
        srv._report_allows_ready(
            {
                "ready_for_review": True,
                "verdict": "SUCCESS",
                "blocker": None,
                "leftovers": [],
                "required_empty_after_fill": [],
                "required_empty_before_advance": [],
            }
        )
        is False
    )
    # Flag false → not ready even if clean
    assert (
        srv._report_allows_ready(
            {
                "ready_for_review": False,
                "verdict": "SUCCESS",
                "blocker": None,
                "leftovers": [],
            }
        )
        is False
    )


def test_hold_alone_not_ready_signal():
    """hold_indefinite / hold_seconds must not imply Ready without report gate."""
    rep = {
        "ready_for_review": False,
        "hold_indefinite": True,
        "hold_seconds_applied": -1,
        "verdict": "FAIL",
        "blocker": "auth_wall",
        "leftovers": [],
    }
    assert srv._report_allows_ready(rep) is False


def main() -> int:
    test_report_allows_ready_requires_honest_flag()
    test_hold_alone_not_ready_signal()
    print("test_ready_from_fill_report: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
