#!/usr/bin/env python3
"""Unit tests: field lock + thrash + page-complete → Next gates.

Dummy-only; no live ATS. Never submit.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from field_lock import (  # noqa: E402
    FieldLockSession,
    analyze_step_log_waste,
    apply_thrash_verdict_gate,
    attach_field_locks,
    clear_locks_on_advance,
    field_identity_key,
    fold_lock_metrics,
    gate_field_action,
    lock_verified_field,
)


def test_identity_prefers_automation_id() -> None:
    k1 = field_identity_key(field_type="HOW_HEARD", automation_id="how_heard", label="X")
    k2 = field_identity_key(field_type="HOW_HEARD", automation_id="how_heard", label="Y")
    assert k1 == k2
    assert "aid:how_heard" in k1


def test_lock_then_gate_skips_and_counts_thrash() -> None:
    s = FieldLockSession()
    g0 = s.gate(field_type="NAME_FIRST", automation_id="legalNameSection_firstName")
    assert g0["action"] == "proceed"
    s.lock(
        field_type="NAME_FIRST",
        automation_id="legalNameSection_firstName",
        readback="Ada",
        via="contact_pack",
    )
    assert s.is_locked(field_type="NAME_FIRST", automation_id="legalNameSection_firstName")
    g1 = s.gate(field_type="NAME_FIRST", automation_id="legalNameSection_firstName")
    assert g1["action"] == "lock_skip"
    assert g1["thrash"] is True
    assert s.thrash_retouches == 1
    # lock_skip_result shape
    skip = s.lock_skip_result(g1, automation_id="legalNameSection_firstName", field_type="NAME_FIRST")
    assert skip["skipped_locked"] is True
    assert skip["reason"] == "field_locked_skip"
    assert skip["verified"] is True


def test_how_heard_alias_walk_dies_after_lock() -> None:
    """Once Indeed chip locked, LinkedIn/Other identities (same aid) never proceed."""
    s = FieldLockSession()
    assert s.gate(field_type="HOW_HEARD", automation_id="how_heard")["action"] == "proceed"
    s.lock(
        field_type="HOW_HEARD",
        automation_id="how_heard",
        readback="1 item selected, Indeed",
        via="how_heard",
    )
    for alias in ("LinkedIn", "Company Website", "Other", "Glassdoor"):
        g = s.gate(
            field_type="HOW_HEARD",
            automation_id="how_heard",
            label=alias,
        )
        assert g["action"] == "lock_skip", alias
    assert s.thrash_retouches == 4


def test_clear_locks_on_advance_new_page() -> None:
    report: dict = {}
    sess = attach_field_locks(report)
    sess.lock(field_type="EMAIL", automation_id="contact_email", readback="a@b.c")
    assert sess.is_locked(field_type="EMAIL", automation_id="contact_email")
    info = clear_locks_on_advance(report)
    assert info and info["cleared_locks"] == 1
    assert not sess.is_locked(field_type="EMAIL", automation_id="contact_email")
    # New page may fill again
    assert sess.gate(field_type="EMAIL", automation_id="contact_email")["action"] == "proceed"


def test_time_to_first_fill_after_advance() -> None:
    s = FieldLockSession()
    assert s.time_to_first_fill_s() is None
    s.lock(field_type="NAME_FIRST", automation_id="fn", readback="Ada")
    ttf = s.time_to_first_fill_s()
    assert ttf is not None and ttf >= 0
    s.clear_for_new_page()
    assert s.time_to_first_fill_s() is None
    s.lock(field_type="NAME_LAST", automation_id="ln", readback="Lovelace")
    assert s.time_to_first_fill_s() is not None


def test_review_hold_refused_while_footer_advance() -> None:
    from page_progress import may_enter_review_hold, attach_footer_primary

    report = {
        "platform": "workday",
        "coverage_path": "workday_multipage",
        "verdict": "SUCCESS",
        "ready_for_review": True,
        "workday_current_step": "review",
        "workday": {"phase_e": {"stopped_at_review": True}},
        "vision_judge_live": {"complete": True, "verdict": "COMPLETE", "empty_fields": []},
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
    }
    attach_footer_primary(report, kind="ADVANCE", label="Save and Continue")
    assert report.get("footer_primary_blocks_review_hold") is True
    assert may_enter_review_hold(report) is False

    # Also on non-Workday when footer was probed ADVANCE
    generic = {
        "platform": "greenhouse",
        "verdict": "SUCCESS",
        "vision_judge_live": {"complete": True, "verdict": "COMPLETE", "empty_fields": []},
        "leftovers": [],
    }
    attach_footer_primary(generic, kind="ADVANCE", label="Next")
    assert may_enter_review_hold(generic) is False


def test_thrash_demotes_success() -> None:
    report: dict = {"verdict": "SUCCESS", "dummy": True}
    attach_field_locks(report)
    sess = report["_field_locks"]
    sess.lock(field_type="X", automation_id="x", readback="1")
    sess.gate(field_type="X", automation_id="x")  # thrash
    apply_thrash_verdict_gate(report)
    assert report["verdict"] == "FAIL"
    assert report.get("thrash_demoted") is True
    assert report.get("thrash_retouches") == 1
    m = fold_lock_metrics(report)
    assert m["thrash_retouches"] == 1
    assert "X|aid:x" in m["per_field_attempts"] or m["locked_count"] >= 0


def test_gate_field_action_uses_step_report_parent() -> None:
    parent: dict = {}
    attach_field_locks(parent)
    lock_verified_field(
        parent,
        field_type="PHONE",
        automation_id="phoneNumber",
        readback="555",
        via="pack",
    )
    nested = {"_step_report": parent}
    g = gate_field_action(nested, field_type="PHONE", automation_id="phoneNumber")
    assert g is not None and g["action"] == "lock_skip"


def test_analyze_walmart_style_waste() -> None:
    """Synthetic trace mirroring Walmart contact re-walk waste."""
    steps = [
        {"step": 1, "ts": "2026-08-10T06:55:36Z", "action": "fill_text", "field_type": "NAME_FIRST"},
        {"step": 2, "ts": "2026-08-10T06:55:37Z", "action": "fill_text", "field_type": "NAME_LAST"},
        {"step": 3, "ts": "2026-08-10T06:57:25Z", "action": "select_word_by_word", "field_type": "HOW_HEARD", "after": "Indeed"},
        {"step": 4, "ts": "2026-08-10T06:57:26Z", "action": "select_word_by_word", "field_type": "HOW_HEARD", "after": "LinkedIn"},
        {"step": 5, "ts": "2026-08-10T06:57:47Z", "action": "fill_text", "field_type": "NAME_FIRST"},
        {"step": 6, "ts": "2026-08-10T06:57:47Z", "action": "skip_already_correct", "field_type": "NAME_LAST"},
        {"step": 7, "ts": "2026-08-10T06:57:50Z", "action": "lock_skip", "field_type": "NAME_FIRST", "skipped_locked": True},
    ]
    waste = analyze_step_log_waste(steps)
    assert waste["duplicate_fills"], waste
    assert waste["how_heard_attempts"] == 2
    assert any(g["gap_s"] >= 2.5 for g in waste["long_gaps_ge_2_5s"])
    assert waste["lock_skips"] >= 1


def test_filter_locked_types_for_pack_loop() -> None:
    """Pack/refill loops should drop locked types before calling fill (no thrash)."""
    report: dict = {}
    sess = attach_field_locks(report)
    for ft, aid in (("NAME_FIRST", "fn"), ("NAME_LAST", "ln"), ("EMAIL", "em")):
        sess.lock(field_type=ft, automation_id=aid, readback="x")
    locked = sess.locked_types()
    plan = [
        ("fn", "Ada", False),
        ("ln", "Lovelace", False),
        ("city", "Austin", False),
    ]
    # Simulate filter by automation_id lock check
    remaining = []
    for aid, val, cb in plan:
        ft = {"fn": "NAME_FIRST", "ln": "NAME_LAST", "city": "ADDRESS_CITY"}[aid]
        if sess.is_locked(field_type=ft, automation_id=aid):
            continue
        remaining.append((aid, val, cb))
    assert len(remaining) == 1
    assert remaining[0][0] == "city"
    assert sess.thrash_retouches == 0  # filtered, never gated


def test_resume_singleton_lock_blocks_other_selectors() -> None:
    s = FieldLockSession()
    s.lock(
        field_type="RESUME_UPLOAD",
        automation_id="file-upload-input-ref",
        readback="dummy.pdf",
        via="ensure_resume",
    )
    g = s.gate(
        field_type="RESUME_UPLOAD",
        selector="input[type=file]",
        label="Resume / CV",
    )
    assert g["action"] == "lock_skip"
    assert g.get("singleton_type") == "RESUME_UPLOAD"
    assert s.thrash_retouches == 1


def test_fos_family_lock_blocks_major_alias_pass2() -> None:
    """Yogesh rule: locked FoS must not be retouched via Major/Discipline aliases."""
    from field_lock import filter_locked_leftovers

    s = FieldLockSession()
    s.lock(
        field_type="FIELD_OF_STUDY",
        automation_id="education/fieldOfStudy",
        readback="Science-Computer",
        via="workday_already_correct_skip",
    )
    for aid, lab in (
        ("education/Major", "Major"),
        ("edu_prompt:Major", "Major"),
        ("select_one:Major", "Major"),
        ("formField-discipline", "Discipline"),
        ("major", "Field of Study"),
    ):
        g = s.gate(field_type="MAJOR", automation_id=aid, label=lab)
        assert g["action"] == "lock_skip", (aid, g)
    assert s.is_locked(field_type="DISCIPLINE", automation_id="formField-discipline")

    report: dict = {
        "filled": [
            {
                "type": "FIELD_OF_STUDY",
                "automation_id": "education/fieldOfStudy",
                "verified": True,
                "readback": "Science-Computer",
            }
        ],
        "leftovers": [
            {
                "label": "education/Major",
                "reason": "listbox_still_open",
                "automation_id": "education/Major",
                "flash_candidate": True,
            },
            {
                "label": "worked_here_before",
                "reason": "radio_not_found",
                "automation_id": "worked_here_before",
                "flash_candidate": True,
            },
        ],
    }
    attach_field_locks(report)
    lock_verified_field(
        report,
        field_type="FIELD_OF_STUDY",
        automation_id="education/fieldOfStudy",
        readback="Science-Computer",
        via="test",
    )
    kept = filter_locked_leftovers(report)
    assert all(r.get("automation_id") != "education/Major" for r in kept)
    assert any(r.get("automation_id") == "worked_here_before" for r in kept)


def test_lock_verified_field_refuses_unverified() -> None:
    """Honesty: unverified/wrong rows must not lock (lock-on-commit only)."""
    report: dict = {}
    attach_field_locks(report)
    denied = lock_verified_field(
        report,
        {
            "type": "EMAIL",
            "automation_id": "contact_email",
            "readback": "wrong@example.com",
            "verified": False,
            "ok": False,
        },
        field_type="EMAIL",
        automation_id="contact_email",
        readback="wrong@example.com",
        via="test",
    )
    assert denied is None
    sess = report["_field_locks"]
    assert not sess.is_locked(field_type="EMAIL", automation_id="contact_email")
    ok = lock_verified_field(
        report,
        {
            "type": "EMAIL",
            "automation_id": "contact_email",
            "readback": "dummy@example.com",
            "verified": True,
            "ok": True,
        },
        field_type="EMAIL",
        automation_id="contact_email",
        readback="dummy@example.com",
        via="test",
    )
    assert ok is not None
    assert sess.is_locked(field_type="EMAIL", automation_id="contact_email")


def test_lock_verified_field_refuses_intent_as_readback() -> None:
    """Empty DOM readback must not lock using intent/value (overwrite lock_skip)."""
    report: dict = {}
    attach_field_locks(report)
    denied = lock_verified_field(
        report,
        {
            "type": "ADDRESS_LINE2",
            "automation_id": "addressSection_addressLine2",
            "value": "Apt 1A",
            "readback": "",
            "verified": True,
            "ok": True,
        },
        field_type="ADDRESS_LINE2",
        automation_id="addressSection_addressLine2",
        via="test",
    )
    assert denied is None
    sess = report["_field_locks"]
    assert not sess.is_locked(
        field_type="ADDRESS_LINE2", automation_id="addressSection_addressLine2"
    )


def test_address_state_ontology_blocks_sibling_aid() -> None:
    """Lock countryRegion → sibling formField-countryRegion must lock_skip."""
    s = FieldLockSession()
    s.lock(
        field_type="ADDRESS_STATE",
        automation_id="addressSection_countryRegion",
        readback="Illinois",
        via="test",
    )
    assert s.is_locked(
        field_type="ADDRESS_STATE", automation_id="formField-countryRegion"
    )
    g = s.gate(
        field_type="ADDRESS_STATE",
        automation_id="formField-countryRegion",
        label="State / Province",
    )
    assert g["action"] == "lock_skip"
    # Phone country must not collapse into address state
    g_phone = s.gate(
        field_type="PHONE_COUNTRY_CODE",
        automation_id="countryPhoneCode",
        label="Country Phone Code",
    )
    assert g_phone["action"] == "proceed"


def main() -> None:
    test_identity_prefers_automation_id()
    test_lock_then_gate_skips_and_counts_thrash()
    test_how_heard_alias_walk_dies_after_lock()
    test_clear_locks_on_advance_new_page()
    test_time_to_first_fill_after_advance()
    test_review_hold_refused_while_footer_advance()
    test_thrash_demotes_success()
    test_gate_field_action_uses_step_report_parent()
    test_analyze_walmart_style_waste()
    test_filter_locked_types_for_pack_loop()
    test_resume_singleton_lock_blocks_other_selectors()
    test_fos_family_lock_blocks_major_alias_pass2()
    test_lock_verified_field_refuses_unverified()
    test_lock_verified_field_refuses_intent_as_readback()
    test_address_state_ontology_blocks_sibling_aid()
    print("test_field_lock: OK")


if __name__ == "__main__":
    main()
