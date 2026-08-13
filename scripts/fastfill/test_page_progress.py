#!/usr/bin/env python3
"""Unit tests: stuck-on-same-page / progress verdict gates (no browser)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from page_progress import (  # noqa: E402
    apply_progress_verdict_gates,
    budgeted_progress_decision,
    compute_stuck_on_same_page,
    flash_attempt_failed,
    is_essay_leftover,
    note_advance_result,
    note_settle_cycle,
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


def test_budgeted_empty_cycle_stops():
    """Empty settle cycles must STOP — not keep cycling pages doing nothing."""
    d0 = budgeted_progress_decision(
        settle_count=1,
        empty_cycle_count=1,
        max_empty_cycles=2,
        filled_this_cycle=0,
        advanced_this_cycle=False,
    )
    assert d0["action"] == "CONTINUE", d0

    d_stop = budgeted_progress_decision(
        settle_count=2,
        empty_cycle_count=2,
        max_empty_cycles=2,
        filled_this_cycle=0,
        advanced_this_cycle=False,
    )
    assert d_stop["action"] == "STOP", d_stop
    assert d_stop["reason"] == "empty_cycle"

    # Simulated empty refill loop on a stuck page
    report: dict = {"verdict": "SUCCESS", "stuck_on_same_page": False}
    for _ in range(3):
        decision = note_settle_cycle(report, filled_this_cycle=0, advanced_this_cycle=False)
    assert decision["action"] == "STOP"
    assert decision["reason"] == "empty_cycle"
    assert report["progress_stop"] is True
    assert report["empty_cycle_count"] >= 2
    assert report["verdict"] == "FAIL"
    assert report.get("progress_stop_reason") == "empty_cycle"


def test_budgeted_stuck_on_same_page_stops():
    d = budgeted_progress_decision(
        settle_count=1,
        empty_cycle_count=0,
        stuck_on_same_page=True,
        filled_this_cycle=2,
        advanced_this_cycle=False,
    )
    assert d["action"] == "STOP"
    assert d["reason"] == "stuck_on_same_page"


def test_budgeted_max_advances_stops():
    d = budgeted_progress_decision(
        settle_count=1,
        advance_count=4,
        empty_cycle_count=0,
        max_advances=4,
        filled_this_cycle=1,
        advanced_this_cycle=True,
    )
    assert d["action"] == "STOP"
    assert d["reason"] == "max_advances"


def test_progress_gates_empty_cycle_demotes_success():
    """apply_progress_verdict_gates must FAIL SUCCESS when empty_cycle budget is spent."""
    from page_progress import apply_progress_verdict_gates

    report: dict = {
        "verdict": "SUCCESS",
        "empty_cycle_count": 2,
        "settle_count": 2,
        "advanced_count": 0,
        "leftovers": [],
        "required_empty_before_advance": [],
        "required_empty_after_fill": [],
    }
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "FAIL"
    assert report.get("progress_stop") is True
    assert report.get("progress_stop_reason") == "empty_cycle"
    assert report.get("verdict_reason") == "empty_cycle"


def test_note_workday_phase_cycle_increments_empty():
    from page_progress import note_workday_phase_cycle

    report: dict = {}
    phase = {"filled": []}
    d1 = note_workday_phase_cycle(report, phase, advanced=False)
    d2 = note_workday_phase_cycle(report, phase, advanced=False)
    assert d2["action"] == "STOP"
    assert d2["reason"] == "empty_cycle"
    assert report["empty_cycle_count"] >= 2
    assert d1["empty_cycle_count"] == 1


def test_workday_selectors_wire_empty_cycle():
    src = (HERE / "exp_workday_selectors.py").read_text(encoding="utf-8")
    assert "note_workday_phase_cycle" in src
    assert "progress_stop" in src


def _done_name_hh_filled() -> list[dict]:
    """Verified dummy First/Last/How-Heard rows (field_is_done true)."""
    return [
        {
            "type": "NAME_FIRST",
            "label": "First Name",
            "value": "Jane",
            "readback": "Jane",
            "verified": True,
        },
        {
            "type": "NAME_LAST",
            "label": "Last Name",
            "value": "Dummy",
            "readback": "Dummy",
            "verified": True,
        },
        {
            "type": "HOW_HEARD",
            "label": "How Did You Hear About Us?",
            "value": "Internet job board",
            "readback": (
                "How Did You Hear About Us?* 1 item selected, Internet job board"
            ),
            "verified": True,
        },
    ]


def _vision_false_empty_name_hh() -> dict:
    return {
        "complete": False,
        "verdict": "FAIL_BLANK",
        "empty_fields": [
            {"label": "First Name", "kind": "blank"},
            {"label": "Last Name", "kind": "blank"},
            {"label": "How Did You Hear About Us?", "kind": "blank"},
        ],
        "never_submit": True,
        "submit_clicked": False,
        "source": "dom",
    }


def test_vision_empty_ignored_when_name_hh_field_is_done():
    """0842Z: vision must not override field_is_done First/Last/How Heard."""
    from field_done import field_is_done_from_row, filled_rows_honest
    from page_progress import can_claim_ready, vision_blocks_ready

    filled = _done_name_hh_filled()
    assert all(field_is_done_from_row(r).ok for r in filled)
    report = {
        "verdict": "SUCCESS",
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "filled": filled,
        "vision_judge_live": _vision_false_empty_name_hh(),
        "vision_incomplete": True,
        "blocker": "vision_incomplete",
    }
    assert filled_rows_honest(report) is True
    # Ready path must reconcile before blocker=vision_incomplete vetoes.
    assert can_claim_ready(report) is True
    assert vision_blocks_ready(report) is False
    vj = report["vision_judge_live"]
    assert vj.get("complete") is True
    assert vj.get("verdict") == "COMPLETE"
    assert vj.get("empty_fields") == []
    assert report.get("vision_incomplete") is not True
    assert report.get("blocker") != "vision_incomplete"


def test_vision_still_blocks_when_real_empty_remains():
    """Phone still empty → vision FAIL_BLANK must keep blocking Ready."""
    from page_progress import can_claim_ready, vision_blocks_ready

    report = {
        "verdict": "SUCCESS",
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "filled": _done_name_hh_filled(),
        "vision_judge_live": {
            "complete": False,
            "verdict": "FAIL_BLANK",
            "empty_fields": [
                {"label": "First Name", "kind": "blank"},
                {"label": "Phone", "kind": "blank"},
            ],
            "never_submit": True,
            "submit_clicked": False,
            "source": "dom",
        },
        "vision_incomplete": True,
        "blocker": "vision_incomplete",
    }
    assert vision_blocks_ready(report) is True
    assert can_claim_ready(report) is False
    kept = [e.get("label") for e in report["vision_judge_live"].get("empty_fields") or []]
    assert "Phone" in kept
    assert "First Name" not in kept


def test_fail_closed_vision_does_not_invent_done_name_hh():
    """Fail-closed judge_error must not pack already-verified First/Last/HH."""
    from page_progress import can_claim_ready, vision_blocks_ready

    report = {
        "verdict": "SUCCESS",
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "filled": _done_name_hh_filled(),
        "vision_judge_live": {
            "complete": False,
            "verdict": "AMBIGUOUS",
            "empty_fields": [
                {"label": "First Name", "kind": "blank"},
                {"label": "Last Name", "kind": "blank"},
                {"label": "How Did You Hear About Us?", "kind": "blank"},
                {"label": "judge_error: timeout", "kind": "blank"},
            ],
            "never_submit": True,
            "submit_clicked": False,
            "source": "dom",
            "confidence": "ambiguous",
        },
        "vision_incomplete": True,
        "blocker": "vision_incomplete",
    }
    # Name/HH empties dropped; leftover judge_error still fail-closed
    assert vision_blocks_ready(report) is True
    assert can_claim_ready(report) is False
    labels = [e.get("label") for e in report["vision_judge_live"].get("empty_fields") or []]
    assert any(str(l).startswith("judge_error:") for l in labels)
    assert "First Name" not in labels
    assert "Last Name" not in labels
    assert "How Did You Hear About Us?" not in labels


def test_apply_live_vision_gate_ignores_done_name_hh():
    """apply_live_vision_gate must not set vision_incomplete for pack-verified names."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    from page_progress import apply_live_vision_gate, can_claim_ready

    report = {
        "verdict": "SUCCESS",
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "filled": _done_name_hh_filled(),
    }
    fake_page = object()

    async def _run():
        with patch(
            "vision_judge.judge_page",
            new=AsyncMock(return_value=_vision_false_empty_name_hh()),
        ):
            result = await apply_live_vision_gate(fake_page, report)
        return result

    result = asyncio.run(_run())
    assert result.get("complete") is True
    assert result.get("empty_fields") == []
    assert report.get("vision_incomplete") is not True
    assert report.get("blocker") != "vision_incomplete"
    assert can_claim_ready(report) is True


def test_midwizard_footer_advance_refuses_ready_not_next():
    """1138Z: Save and Continue + phase_c not advanced is Ready-incomplete.

    ``workday_wizard_incomplete`` / footer ADVANCE must still refuse Ready.
    ``advance_page_if_ready`` must NOT STOP Next with wizard_incomplete — page
    empties already filtered; click Experience Next.
    """
    from page_progress import (
        attach_footer_primary,
        can_claim_ready,
        may_enter_review_hold,
        workday_wizard_incomplete,
    )

    src = (HERE / "fill_contract.py").read_text(encoding="utf-8")
    assert 'AdvanceDecision(False, "wizard_incomplete")' not in src
    assert "wizard_incomplete" in src  # still cleared when empties filtered

    report = {
        "platform": "workday",
        "coverage_path": "workday_multipage",
        "verdict": "SUCCESS",
        "blocker": None,
        "leftovers": [],
        "required_empty_before_advance": [],
        "required_empty_after_fill": [],
        "workday_current_step": "experience",
        "workday": {
            "phase_c": {"present": True, "advanced": False},
            "phase_e": None,
        },
        "vision_judge_live": {
            "complete": True,
            "verdict": "COMPLETE",
            "empty_fields": [],
            "never_submit": True,
            "submit_clicked": False,
        },
    }
    attach_footer_primary(
        report, kind="ADVANCE", label="Save and Continue", source="workday_bottom_nav"
    )
    assert workday_wizard_incomplete(report) is True
    assert may_enter_review_hold(report) is False
    assert can_claim_ready(report) is False


def test_flash_attempt_failed_workday_skip_not_failure():
    """Legacy Workday skip reports are not flash_leftovers_failed (backward compat)."""
    report = {
        "verdict": "SUCCESS",
        "flash_leftovers_requested": True,
        "leftover_count": 2,
        "flash": {
            "invoked": False,
            "skipped_reason": "workday_two_phase",
        },
    }
    assert flash_attempt_failed(report) is False
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "SUCCESS"
    assert report.get("verdict_reason") != "flash_leftovers_failed"


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
    test_budgeted_empty_cycle_stops()
    test_budgeted_stuck_on_same_page_stops()
    test_budgeted_max_advances_stops()
    test_progress_gates_empty_cycle_demotes_success()
    test_note_workday_phase_cycle_increments_empty()
    test_workday_selectors_wire_empty_cycle()
    test_vision_empty_ignored_when_name_hh_field_is_done()
    test_vision_still_blocks_when_real_empty_remains()
    test_fail_closed_vision_does_not_invent_done_name_hh()
    test_apply_live_vision_gate_ignores_done_name_hh()
    test_midwizard_footer_advance_refuses_ready_not_next()
    test_flash_attempt_failed_workday_skip_not_failure()
    print("test_page_progress: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
