#!/usr/bin/env python3
"""Unit tests: stuck-on-same-page / progress verdict gates (no browser)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from page_progress import (  # noqa: E402
    apply_progress_verdict_gates,
    compute_stuck_on_same_page,
    flash_attempt_failed,
    is_essay_leftover,
    note_advance_result,
    step_fingerprint,
)


def test_step_fingerprint_changes_with_path():
    a = step_fingerprint("https://ex.com/apply/step1", title="My Info")
    b = step_fingerprint("https://ex.com/apply/step2", title="My Info")
    assert a != b


def test_step_fingerprint_changes_with_step_hint():
    a = step_fingerprint("https://ex.com/apply", title="App", step_hint="My Info")
    b = step_fingerprint("https://ex.com/apply", title="App", step_hint="Experience")
    assert a != b


def test_stuck_when_next_existed_and_unchanged():
    """ATS3-003: Next visible + refuse-to-click is NOT stuck without advance_clicked."""
    assert (
        compute_stuck_on_same_page(
            next_existed=True,
            fingerprint_before="aaa",
            fingerprint_after="aaa",
            advance_clicked=False,
        )
        is False
    )


def test_not_stuck_fail_before_advance_without_click():
    """Generic ADVANCE refuse (required empties) must not sticky-stuck."""
    report: dict = {}
    note_advance_result(
        report,
        fingerprint_before="aaa",
        fingerprint_after="aaa",
        next_existed=False,
        advance_clicked=False,
    )
    assert report["stuck_on_same_page"] is False
    assert report.get("advanced_count", 0) == 0


def test_not_stuck_when_fingerprint_moved():
    assert (
        compute_stuck_on_same_page(
            next_existed=True,
            fingerprint_before="aaa",
            fingerprint_after="bbb",
            advance_clicked=True,
        )
        is False
    )


def test_not_stuck_when_no_next():
    assert (
        compute_stuck_on_same_page(
            next_existed=False,
            fingerprint_before="aaa",
            fingerprint_after="aaa",
        )
        is False
    )


def test_stuck_when_advance_clicked_unchanged():
    assert (
        compute_stuck_on_same_page(
            next_existed=False,
            fingerprint_before="aaa",
            fingerprint_after="aaa",
            advance_clicked=True,
        )
        is True
    )


def test_note_advance_increments_advanced_count_on_move():
    report: dict = {}
    note_advance_result(
        report,
        fingerprint_before="aaa",
        fingerprint_after="bbb",
        next_existed=True,
        advance_clicked=True,
    )
    assert report["advanced_count"] == 1
    assert report["stuck_on_same_page"] is False
    assert len(report["pages_seen"]) == 2


def test_note_advance_marks_stuck_when_unchanged():
    report: dict = {}
    note_advance_result(
        report,
        fingerprint_before="aaa",
        fingerprint_after="aaa",
        next_existed=True,
        advance_clicked=True,
    )
    assert report["advanced_count"] == 0
    assert report["stuck_on_same_page"] is True


def test_verdict_demoted_on_stuck():
    report = {
        "verdict": "SUCCESS",
        "stuck_on_same_page": True,
        "leftover_count": 0,
    }
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "FAIL"
    assert report.get("verdict_reason") == "stuck_on_same_page"


def test_verdict_demoted_on_required_empties():
    report = {
        "verdict": "SUCCESS",
        "required_empty_before_advance": [{"id": "x", "reason": "empty_required_input"}],
        "leftover_count": 0,
    }
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "FAIL"


def test_reconcile_clears_stale_advance_gate():
    from page_progress import reconcile_stale_advance_gate

    report = {
        "verdict": "FAIL",
        "advance_blocked_reason": "required_fields_empty",
        "required_empty_before_advance": [
            {"id": "question_18017622008", "reason": "empty_required_input"}
        ],
        "required_empty_after_fill": [],
        "blocker": "page_incomplete",
        "leftovers": [],
        "demoted_false_verified": [],
        "page_advance": {
            "advance_blocked_reason": "required_fields_empty",
            "required_empty_before_advance": [
                {"id": "question_18017622008", "reason": "empty_required_input"}
            ],
        },
    }
    reconcile_stale_advance_gate(report)
    assert report["advance_blocked_reason"] is None
    assert report["required_empty_before_advance"] == []
    assert report["blocker"] is None
    assert report["verdict"] == "SUCCESS"
    assert report.get("stale_advance_gate_cleared") is True
    assert report["page_advance"]["advance_blocked_reason"] is None


def test_reconcile_keeps_fail_when_req_after_nonempty():
    from page_progress import reconcile_stale_advance_gate

    report = {
        "verdict": "FAIL",
        "advance_blocked_reason": "required_fields_empty",
        "required_empty_before_advance": [{"id": "phone", "reason": "empty_required_input"}],
        "required_empty_after_fill": [{"id": "phone", "reason": "empty_required_input"}],
        "leftovers": [],
    }
    reconcile_stale_advance_gate(report)
    assert report["advance_blocked_reason"] == "required_fields_empty"
    assert report["verdict"] == "FAIL"


def test_reconcile_keeps_fail_when_race_demoted():
    from page_progress import reconcile_stale_advance_gate

    report = {
        "verdict": "FAIL",
        "advance_blocked_reason": "required_fields_empty",
        "required_empty_after_fill": [],
        "demoted_false_verified": [
            {"type": "RACE", "reason": "live_empty_after_claimed_verified"}
        ],
        "leftovers": [
            {
                "type": "RACE",
                "label": "Please identify your race",
                "reason": "live_empty_after_claimed_verified",
            }
        ],
    }
    reconcile_stale_advance_gate(report)
    assert report["advance_blocked_reason"] is None
    assert report["verdict"] == "FAIL"  # demoted / leftover remains


def test_reconcile_promotes_after_leftovers_drained():
    """Second finalize: advance already cleared, leftovers now 0 → SUCCESS."""
    from page_progress import can_claim_ready, reconcile_stale_advance_gate

    report = {
        "verdict": "FAIL",
        "verdict_reason": "leftovers_remain",
        "advance_blocked_reason": None,
        "stale_advance_gate_cleared": True,
        "required_empty_after_fill": [],
        "demoted_false_verified": [],
        "leftovers": [],
        "vision_judge_live": {
            "complete": True,
            "verdict": "COMPLETE",
            "empty_fields": [],
            "never_submit": True,
        },
    }
    reconcile_stale_advance_gate(report)
    assert report["verdict"] == "SUCCESS"
    assert can_claim_ready(report) is True
    assert report.get("ready_for_review") is True


def test_can_claim_ready_and_finalize():
    from page_progress import can_claim_ready, finalize_ready_flag

    blocked = {
        "verdict": "FAIL",
        "blocker": "auth_wall",
        "ready_for_review": True,
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
    }
    assert can_claim_ready(blocked) is False
    finalize_ready_flag(blocked)
    assert blocked["ready_for_review"] is False
    assert blocked.get("ready_claim_refused") is True


def test_verdict_demoted_on_advance_fingerprint_unchanged():
    report = {
        "verdict": "SUCCESS",
        "advanced": True,
        "page_fingerprint_before": "aaa",
        "page_fingerprint_after": "aaa",
        "leftover_count": 0,
    }
    apply_progress_verdict_gates(report)
    assert report["stuck_on_same_page"] is True
    assert report["verdict"] == "FAIL"


def test_flash_failed_with_leftovers_demotes_success():
    report = {
        "verdict": "SUCCESS",
        "flash_leftovers_requested": True,
        "leftover_count": 2,
        "flash": {"invoked": False, "error": "skyvern_import_failed"},
    }
    assert flash_attempt_failed(report) is True
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "FAIL"
    assert report.get("verdict_reason") == "flash_leftovers_failed"


def test_flash_success_with_essay_leftovers_ok():
    report = {
        "verdict": "SUCCESS",
        "flash_leftovers_requested": True,
        "leftover_count": 1,
        "leftovers": [{"label": "Cover letter", "type": "COVER_LETTER", "essay": True}],
        "flash": {"invoked": True, "status": "completed"},
    }
    assert flash_attempt_failed(report) is False
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "SUCCESS"


def test_flash_attempt_failed_skyvern_deferred_invoked_false():
    """FILL3-001: skyvern_deferred + invoked=false must not count as Flash failed."""
    report = {
        "verdict": "SUCCESS",
        "flash_leftovers_requested": True,
        "leftover_count": 2,
        "leftovers": [
            {"label": "Cover letter", "type": "COVER_LETTER", "essay": True},
            {"label": "Why join us?", "essay": True},
        ],
        "flash": {
            "invoked": False,
            "inpage_ran": True,
            "skyvern_deferred": (
                "headed_hold_open — inpage leftovers only; Skyvern skipped"
            ),
            "flash_engine": "inpage",
        },
    }
    assert flash_attempt_failed(report) is False
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "SUCCESS"
    assert report.get("verdict_reason") != "flash_leftovers_failed"


def test_flash_attempt_failed_skyvern_deferred_with_hard_error_still_fails():
    """FILL3-001: deferred Skyvern still fails when flash.error is set."""
    report = {
        "verdict": "SUCCESS",
        "flash_leftovers_requested": True,
        "leftover_count": 1,
        "flash": {
            "invoked": False,
            "skyvern_deferred": "headed_hold_open",
            "error": "inpage_boom",
        },
    }
    assert flash_attempt_failed(report) is True


def test_flash_attempt_failed_inpage_ran_essay_only_ok():
    """FILL3-013: inpage_ran + essay-only leftovers + invoked=false is not failure."""
    report = {
        "flash_leftovers_requested": True,
        "leftover_count": 1,
        "leftovers": [{"label": "Tell us about yourself", "essay": True}],
        "flash": {"invoked": False, "inpage_ran": True},
    }
    assert flash_attempt_failed(report) is False


def test_is_essay_leftover():
    assert is_essay_leftover({"type": "COVER_LETTER"}) is True
    assert is_essay_leftover({"label": "Tell us about yourself"}) is True
    assert is_essay_leftover({"label": "Years of experience", "type": "YOE"}) is False
    # FILL2-004
    assert is_essay_leftover({"label": "Additional LinkedIn URL"}) is False
    assert is_essay_leftover({"label": "GitHub URL"}) is False
    assert is_essay_leftover({"label": "Why do you want this role?"}) is True


def main() -> int:
    test_step_fingerprint_changes_with_path()
    test_step_fingerprint_changes_with_step_hint()
    test_stuck_when_next_existed_and_unchanged()
    test_not_stuck_fail_before_advance_without_click()
    test_not_stuck_when_fingerprint_moved()
    test_not_stuck_when_no_next()
    test_stuck_when_advance_clicked_unchanged()
    test_note_advance_increments_advanced_count_on_move()
    test_note_advance_marks_stuck_when_unchanged()
    test_verdict_demoted_on_stuck()
    test_verdict_demoted_on_required_empties()
    test_reconcile_clears_stale_advance_gate()
    test_reconcile_keeps_fail_when_req_after_nonempty()
    test_reconcile_keeps_fail_when_race_demoted()
    test_reconcile_promotes_after_leftovers_drained()
    test_can_claim_ready_and_finalize()
    test_verdict_demoted_on_advance_fingerprint_unchanged()
    test_flash_failed_with_leftovers_demotes_success()
    test_flash_success_with_essay_leftovers_ok()
    test_flash_attempt_failed_skyvern_deferred_invoked_false()
    test_flash_attempt_failed_skyvern_deferred_with_hard_error_still_fails()
    test_flash_attempt_failed_inpage_ran_essay_only_ok()
    test_is_essay_leftover()
    print("test_page_progress: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
