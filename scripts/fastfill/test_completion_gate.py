#!/usr/bin/env python3
"""Phase 1 completion-gate tests (no browser).

Pins the consolidated behavior:
  - outstanding_required_blanks() is the single shared "not done" definition
    (union of required_empty_before/after + hard non-essay leftovers +
    demoted_false_verified), and it EXCLUDES essays / already_correct rows.
  - apply_progress_verdict_gates() is the sole SUCCESS authority: a SUCCESS set
    by ANY source (e.g. Workday-merged verdict) cannot survive when hard
    non-essay leftovers remain, even with no required_empty_after_fill key.
  - The gate is idempotent.
  - FASTFILL_STRICT_COMPLETION=0 rolls the new refusal back.

DUMMY / synthetic fixtures only — never real applicant PII (repo rule).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import page_progress  # noqa: E402
from page_progress import (  # noqa: E402
    apply_progress_verdict_gates,
    outstanding_required_blanks,
)


# ---------------------------------------------------------------------------
# outstanding_required_blanks: the shared "not done" definition
# ---------------------------------------------------------------------------


def test_outstanding_blanks_unions_required_and_hard_leftovers():
    report = {
        "required_empty_before_advance": [{"label": "First Name"}],
        "required_empty_after_fill": ["Work Auth"],  # string entry tolerated
        "leftovers": [
            {"label": "Gender", "type": "GENDER"},  # hard non-essay
            {"label": "Why join us?", "essay": True},  # essay excluded
            {"label": "Prior title", "reason": "already_correct_keep"},  # excluded
        ],
        "demoted_false_verified": [{"label": "Zip"}],
    }
    blanks = outstanding_required_blanks(report)
    labels = {b.get("label") for b in blanks}
    assert "First Name" in labels
    assert "Work Auth" in labels
    assert "Gender" in labels
    assert "Zip" in labels
    # Essays and already_correct rows must NOT count as outstanding blanks
    assert "Why join us?" not in labels
    assert "Prior title" not in labels


def test_outstanding_blanks_empty_when_only_essays_remain():
    report = {"leftovers": [{"label": "Cover letter", "essay": True}]}
    assert outstanding_required_blanks(report) == []


# ---------------------------------------------------------------------------
# Gate is the sole SUCCESS authority (closes the externally-set-SUCCESS hole)
# ---------------------------------------------------------------------------


def test_external_success_with_hard_leftover_demoted_to_fail():
    """A SUCCESS set outside reconcile (e.g. Workday merge) with a hard non-essay
    leftover and NO required_empty_after_fill key must be demoted to FAIL."""
    report = {
        "verdict": "SUCCESS",
        "leftovers": [{"label": "Gender", "type": "GENDER"}],
    }
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "FAIL"
    assert report.get("verdict_reason") == "leftovers_remain"


def test_success_survives_with_only_essay_leftovers():
    report = {
        "verdict": "SUCCESS",
        "leftovers": [{"label": "Why do you want to work here?", "essay": True}],
    }
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "SUCCESS"


def test_success_demoted_when_required_empty_after_fill_present():
    report = {
        "verdict": "SUCCESS",
        "required_empty_after_fill": [{"label": "Phone"}],
    }
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "FAIL"


def test_gate_is_idempotent():
    report = {
        "verdict": "SUCCESS",
        "leftovers": [{"label": "Gender", "type": "GENDER"}],
    }
    apply_progress_verdict_gates(report)
    first = (report.get("verdict"), report.get("verdict_reason"), report.get("ready_for_review"))
    apply_progress_verdict_gates(report)
    second = (report.get("verdict"), report.get("verdict_reason"), report.get("ready_for_review"))
    assert first == second == ("FAIL", "leftovers_remain", report.get("ready_for_review"))


def test_ready_not_set_when_blanks_remain():
    report = {
        "verdict": "SUCCESS",
        "ready_for_review": True,
        "leftovers": [{"label": "Gender", "type": "GENDER"}],
    }
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "FAIL"
    assert not report.get("ready_for_review")


# ---------------------------------------------------------------------------
# Workday multipage hold / Ready gate (Thales early "holding for review")
# ---------------------------------------------------------------------------


def test_workday_progress_unfinished_after_current():
    from page_progress import (
        may_enter_review_hold,
        workday_progress_unfinished_after_current,
        workday_wizard_incomplete,
    )

    prog = (
        "Autofill completed My Information completed My Experience current "
        "Application Questions Voluntary Disclosures Self Identify Review"
    )
    assert workday_progress_unfinished_after_current(prog) is True

    review_prog = "Autofill My Information My Experience Review current"
    assert workday_progress_unfinished_after_current(review_prog) is False


def test_workday_mid_experience_blocks_review_hold():
    from page_progress import (
        can_claim_ready,
        finalize_ready_flag,
        may_enter_review_hold,
        workday_wizard_incomplete,
    )

    report = {
        "platform": "workday",
        "coverage_path": "workday_multipage",
        "verdict": "SUCCESS",  # dishonest mid-wizard SUCCESS must not Ready
        "blocker": None,
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "advanced": True,
        "reached_contact": True,
        "workday_current_step": "experience",
        "workday_wizard_progress": (
            "My Information completed My Experience current "
            "Application Questions Voluntary Disclosures Review"
        ),
        "workday": {
            "phase_b": {"advanced": True},
            "phase_c": {
                "present": True,
                "advanced": False,
                "filled": [
                    {"automation_id": "workExperience-1/jobTitle", "verified": True},
                    {"automation_id": "workExperience-1/company", "verified": True},
                ],
            },
            "phase_e": None,
        },
        "vision_judge_live": {
            "complete": True,
            "verdict": "COMPLETE",
            "empty_fields": [],
            "never_submit": True,
            "submit_clicked": False,
        },
        "ready_for_review": True,
    }
    assert workday_wizard_incomplete(report) is True
    assert may_enter_review_hold(report) is False
    assert can_claim_ready(report) is False
    finalize_ready_flag(report)
    assert report.get("ready_for_review") is not True
    assert report.get("ready_claim_reason") == "workday_wizard_incomplete"


def test_workday_review_stop_allows_ready_when_clean():
    from page_progress import (
        can_claim_ready,
        may_enter_review_hold,
        workday_wizard_incomplete,
    )

    report = {
        "platform": "workday",
        "coverage_path": "workday_multipage",
        "verdict": "SUCCESS",
        "blocker": None,
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "workday_current_step": "review",
        "workday": {
            "phase_e": {"stopped_at_review": True, "advanced": True},
        },
        "vision_judge_live": {
            "complete": True,
            "verdict": "COMPLETE",
            "empty_fields": [],
            "never_submit": True,
            "submit_clicked": False,
        },
    }
    assert workday_wizard_incomplete(report) is False
    assert can_claim_ready(report) is True
    assert may_enter_review_hold(report) is True


def test_detect_workday_current_step_from_probe():
    from exp_workday_selectors import detect_workday_current_step

    assert (
        detect_workday_current_step({"experience": True, "contact": False})
        == "experience"
    )
    assert detect_workday_current_step({"review": True}) == "review"
    assert (
        detect_workday_current_step(
            {}, progress_text="My Experience current Application Questions"
        )
        == "experience"
    )


# ---------------------------------------------------------------------------
# Footer primary (Next vs Submit) — first-class hold / Ready signal
# ---------------------------------------------------------------------------


def test_footer_primary_kind_from_label():
    from page_progress import footer_primary_kind_from_label, footer_primary_wizard_incomplete

    assert footer_primary_kind_from_label("Next") == "ADVANCE"
    assert footer_primary_kind_from_label("Save and Continue") == "ADVANCE"
    assert footer_primary_kind_from_label("Continue") == "ADVANCE"
    assert footer_primary_kind_from_label("Submit") == "FINAL"
    assert footer_primary_kind_from_label("Submit Application") == "FINAL"

    assert footer_primary_wizard_incomplete("ADVANCE", "Next") is True
    assert footer_primary_wizard_incomplete("ADVANCE", "Save and Continue") is True
    assert footer_primary_wizard_incomplete("FINAL", "Submit Application") is False
    assert footer_primary_wizard_incomplete("UNKNOWN", "Back") is True
    assert footer_primary_wizard_incomplete(None, None) is None
    assert footer_primary_wizard_incomplete("", "") is None


def test_footer_next_blocks_review_hold_even_if_phase_says_review():
    """Thales-class: Next still visible → must NOT hold for review / Ready."""
    from page_progress import (
        attach_footer_primary,
        can_claim_ready,
        finalize_ready_flag,
        may_enter_review_hold,
        workday_wizard_incomplete,
    )

    report = {
        "platform": "workday",
        "coverage_path": "workday_multipage",
        "verdict": "SUCCESS",
        "blocker": None,
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        # Stale / dishonest Review claim while footer still says Next
        "workday_current_step": "review",
        "workday": {
            "phase_e": {"stopped_at_review": True, "advanced": True},
        },
        "vision_judge_live": {
            "complete": True,
            "verdict": "COMPLETE",
            "empty_fields": [],
            "never_submit": True,
            "submit_clicked": False,
        },
        "ready_for_review": True,
    }
    attach_footer_primary(
        report, kind="ADVANCE", label="Next", source="workday_bottom_nav"
    )
    assert report.get("footer_primary_kind") == "ADVANCE"
    assert report.get("footer_primary_label") == "Next"
    assert report.get("footer_primary_blocks_review_hold") is True
    assert workday_wizard_incomplete(report) is True
    assert may_enter_review_hold(report) is False
    assert can_claim_ready(report) is False
    finalize_ready_flag(report)
    assert report.get("ready_for_review") is not True
    assert report.get("ready_claim_reason") == "footer_primary_advance"


def test_footer_submit_allows_review_hold_when_clean():
    """FINAL footer (Submit) → Review end; may hold; still never click Submit."""
    from page_progress import (
        attach_footer_primary,
        can_claim_ready,
        may_enter_review_hold,
        workday_wizard_incomplete,
    )

    report = {
        "platform": "workday",
        "coverage_path": "workday_multipage",
        "verdict": "SUCCESS",
        "blocker": None,
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        # Mid-wizard metadata must not override live Submit primary
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
        report,
        kind="FINAL",
        label="Submit Application",
        source="workday_bottom_nav",
    )
    assert report.get("footer_primary_blocks_review_hold") is False
    assert workday_wizard_incomplete(report) is False
    assert can_claim_ready(report) is True
    assert may_enter_review_hold(report) is True


# ---------------------------------------------------------------------------
# Rollback flag
# ---------------------------------------------------------------------------


def test_strict_flag_off_restores_prior_behavior(monkeypatch=None):
    """FASTFILL_STRICT_COMPLETION=0: SUCCESS with a hard leftover is NOT demoted
    by the new consolidated refusal (pre-Phase-1 behavior)."""
    prev = page_progress._STRICT_COMPLETION
    try:
        if monkeypatch is not None:
            monkeypatch.setattr(page_progress, "_STRICT_COMPLETION", False)
        else:
            page_progress._STRICT_COMPLETION = False
        report = {
            "verdict": "SUCCESS",
            "leftovers": [{"label": "Gender", "type": "GENDER"}],
        }
        apply_progress_verdict_gates(report)
        assert report["verdict"] == "SUCCESS"
    finally:
        page_progress._STRICT_COMPLETION = prev


def main() -> int:
    test_outstanding_blanks_unions_required_and_hard_leftovers()
    test_outstanding_blanks_empty_when_only_essays_remain()
    test_external_success_with_hard_leftover_demoted_to_fail()
    test_success_survives_with_only_essay_leftovers()
    test_success_demoted_when_required_empty_after_fill_present()
    test_gate_is_idempotent()
    test_ready_not_set_when_blanks_remain()
    test_workday_progress_unfinished_after_current()
    test_workday_mid_experience_blocks_review_hold()
    test_workday_review_stop_allows_ready_when_clean()
    test_detect_workday_current_step_from_probe()
    test_footer_primary_kind_from_label()
    test_footer_next_blocks_review_hold_even_if_phase_says_review()
    test_footer_submit_allows_review_hold_when_clean()
    test_strict_flag_off_restores_prior_behavior()
    print("test_completion_gate: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
