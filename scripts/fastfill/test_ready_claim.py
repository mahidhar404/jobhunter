#!/usr/bin/env python3
"""Honesty gates: can_claim_ready / finalize_ready_flag (no browser, dummy-only)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from page_progress import (  # noqa: E402
    can_claim_ready,
    finalize_ready_flag,
    reconcile_stale_advance_gate,
)


def _vision_ok() -> dict:
    return {
        "complete": True,
        "verdict": "COMPLETE",
        "empty_fields": [],
        "never_submit": True,
        "submit_clicked": False,
    }


def test_auth_wall_clears_ready_flag():
    report = {
        "verdict": "FAIL",
        "blocker": "auth_wall",
        "ready_for_review": True,
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "vision_judge_live": _vision_ok(),
    }
    assert can_claim_ready(report) is False
    out = finalize_ready_flag(report)
    assert out["ready_for_review"] is False
    assert out.get("ready_claim_refused") is True


def test_success_clean_can_claim():
    report = {
        "verdict": "SUCCESS",
        "blocker": None,
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "advanced_incomplete": False,
        "validation_after_advance": False,
        "vision_judge_live": _vision_ok(),
    }
    assert can_claim_ready(report) is True
    report["ready_for_review"] = True
    finalize_ready_flag(report)
    assert report["ready_for_review"] is True
    assert not report.get("ready_claim_refused")


def test_ready_without_vision_refused():
    """FILL-005: no Ready when vision_judge_live missing."""
    report = {
        "verdict": "SUCCESS",
        "blocker": None,
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "advanced_incomplete": False,
        "validation_after_advance": False,
    }
    assert can_claim_ready(report) is False


def test_hard_leftover_blocks_ready():
    report = {
        "verdict": "SUCCESS",
        "blocker": None,
        "leftovers": [
            {"label": "Phone", "type": "PHONE", "reason": "empty"},
        ],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "vision_judge_live": _vision_ok(),
    }
    assert can_claim_ready(report) is False


def test_essay_leftover_allows_ready():
    report = {
        "verdict": "SUCCESS",
        "blocker": None,
        "leftovers": [
            {"label": "Cover letter", "type": "COVER_LETTER", "essay": True},
        ],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "vision_judge_live": _vision_ok(),
    }
    assert can_claim_ready(report) is True


def test_vision_judge_live_blocks_ready():
    """complete=False or FAIL_BLANK/BLOCKED/AMBIGUOUS → not Ready."""
    base = {
        "verdict": "SUCCESS",
        "blocker": None,
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "advanced_incomplete": False,
        "validation_after_advance": False,
    }
    for verdict in ("FAIL_BLANK", "BLOCKED", "AMBIGUOUS"):
        report = {
            **base,
            "ready_for_review": True,
            "vision_judge_live": {
                "complete": False,
                "verdict": verdict,
                "empty_fields": [{"label": "Phone", "kind": "blank"}],
                "never_submit": True,
            },
        }
        assert can_claim_ready(report) is False, verdict
        finalize_ready_flag(report)
        assert report["ready_for_review"] is False

    # Explicit complete=False even with COMPLETE-looking verdict string
    report2 = {
        **base,
        "ready_for_review": True,
        "vision_judge_live": {
            "complete": False,
            "verdict": "COMPLETE",
            "empty_fields": [],
            "never_submit": True,
        },
    }
    assert can_claim_ready(report2) is False
    finalize_ready_flag(report2)
    assert report2["ready_for_review"] is False

    # Honest COMPLETE does not block
    report3 = {
        **base,
        "vision_judge_live": {
            "complete": True,
            "verdict": "COMPLETE",
            "empty_fields": [],
            "never_submit": True,
        },
    }
    assert can_claim_ready(report3) is True


def test_reconcile_does_not_set_ready_on_auth_wall():
    report = {
        "verdict": "FAIL",
        "verdict_reason": "leftovers_remain",
        "advance_blocked_reason": None,
        "stale_advance_gate_cleared": True,
        "required_empty_after_fill": [],
        "demoted_false_verified": [],
        "leftovers": [],
        "blocker": "auth_wall",
        "vision_judge_live": _vision_ok(),
    }
    reconcile_stale_advance_gate(report)
    # May promote verdict when leftovers drained, but must not claim Ready
    # while auth_wall blocks honesty.
    assert can_claim_ready(report) is False
    assert report.get("ready_for_review") is not True


def test_hold_sets_ready_only_when_claimable():
    from fast_fill import HOLD_INDEFINITE, _hold_for_review

    class _Gone:
        def is_connected(self):
            return False

    async def _blocked():
        report = {
            "verdict": "FAIL",
            "blocker": "auth_wall",
            "leftovers": [],
            "required_empty_after_fill": [],
            "required_empty_before_advance": [],
            "vision_judge_live": _vision_ok(),
        }
        await _hold_for_review(seconds=HOLD_INDEFINITE, report=report, browser=_Gone())
        assert report.get("hold_indefinite") is True
        assert report.get("ready_for_review") is not True

    async def _clean():
        report = {
            "verdict": "SUCCESS",
            "blocker": None,
            "leftovers": [],
            "required_empty_after_fill": [],
            "required_empty_before_advance": [],
            "vision_judge_live": _vision_ok(),
        }
        await _hold_for_review(seconds=HOLD_INDEFINITE, report=report, browser=_Gone())
        assert report.get("hold_indefinite") is True
        assert report.get("ready_for_review") is True

    async def _no_vision():
        report = {
            "verdict": "SUCCESS",
            "blocker": None,
            "leftovers": [],
            "required_empty_after_fill": [],
            "required_empty_before_advance": [],
        }
        await _hold_for_review(seconds=HOLD_INDEFINITE, report=report, browser=_Gone())
        assert report.get("hold_indefinite") is True
        assert report.get("ready_for_review") is not True

    asyncio.run(_blocked())
    asyncio.run(_clean())
    asyncio.run(_no_vision())


def main() -> int:
    test_auth_wall_clears_ready_flag()
    test_success_clean_can_claim()
    test_ready_without_vision_refused()
    test_hard_leftover_blocks_ready()
    test_essay_leftover_allows_ready()
    test_vision_judge_live_blocks_ready()
    test_reconcile_does_not_set_ready_on_auth_wall()
    test_hold_sets_ready_only_when_claimable()
    print("test_ready_claim: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
