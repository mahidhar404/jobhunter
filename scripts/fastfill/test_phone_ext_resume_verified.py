#!/usr/bin/env python3
"""Unit tests: phone-extension shape guard + resume_verified ↔ phase_a merge."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def test_phone_extension_classified():
    from field_map import PHONE_EXTENSION, classify_field

    ftype, _ = classify_field(
        {"label": "Phone Extension", "name": "extension", "id": "", "placeholder": ""}
    )
    assert ftype == PHONE_EXTENSION

    ftype2, _ = classify_field(
        {"label": "Phone Number", "name": "phone-number", "id": "", "placeholder": ""}
    )
    assert ftype2 == "PHONE"


def test_value_ok_rejects_essay_into_extension():
    from field_map import (
        PHONE_EXTENSION,
        is_phone_extension_field,
        is_short_numeric_field,
        value_ok_for_field_shape,
    )

    essay = (
        "I'm interested in this role based on the posted description and "
        "how it aligns with my relevant experience."
    )
    assert is_phone_extension_field("Phone Extension")
    assert is_short_numeric_field("Phone Extension", PHONE_EXTENSION)
    assert value_ok_for_field_shape("", label="Phone Extension") is True
    assert value_ok_for_field_shape("1234", label="Phone Extension") is True
    assert value_ok_for_field_shape(essay, label="Phone Extension") is False
    assert (
        value_ok_for_field_shape(essay, label="Phone Extension", ftype=PHONE_EXTENSION)
        is False
    )
    assert value_ok_for_field_shape("x", label="Phone Extension") is False


def test_flash_forbidden_includes_phone_extension():
    """FILL-003: PHONE_EXTENSION is Flash-forbidden (leave blank; never essay)."""
    from fill_attribution import is_flash_forbidden_type

    assert is_flash_forbidden_type("PHONE", label="Phone Number") is True
    assert is_flash_forbidden_type("", label="Phone Extension") is True
    assert is_flash_forbidden_type("PHONE_EXTENSION", label="Phone Extension") is True
    # Phone Extension label must not be treated as inventable PHONE steal
    assert is_flash_forbidden_type("PHONE", label="Phone Extension") is True


def test_answer_leftover_skips_phone_extension():
    from flash_leftovers import answer_leftover_field, synthesize_grounded_answer

    assert answer_leftover_field("Phone Extension", ftype=None, use_llm=False) == ""
    assert (
        answer_leftover_field("Phone Extension", ftype="PHONE_EXTENSION", use_llm=True)
        == ""
    )
    assert synthesize_grounded_answer("Phone Extension") == ""
    # Interest still synthesizes for real essay labels
    interest = synthesize_grounded_answer("Why are you interested in this role?")
    assert "interested" in interest.lower()


def test_phone_extension_deferred_from_flash():
    """Empty PHONE_EXTENSION must be deferred — never flash_leftovers handoff."""
    from flash_leftovers import is_deterministic_leftover, partition_flash_leftovers

    row = {
        "label": "Phone Extension",
        "type": "PHONE_EXTENSION",
        "reason": "empty",
        "flash_candidate": True,
    }
    assert is_deterministic_leftover(row, values={"PHONE_EXTENSION": ""}) is True
    parts = partition_flash_leftovers([row], values={"PHONE_EXTENSION": ""})
    assert not any(r.get("label") == "Phone Extension" for r in parts["flash_leftovers"])
    assert any(
        r.get("label") == "Phone Extension" for r in parts["deferred_deterministic"]
    )


def test_resume_verified_from_phase_a_upload():
    from resume_upload import (
        apply_resume_success_gate,
        report_has_verified_resume,
        sync_resume_verified_from_phase_a,
    )

    report = {
        "filled": [],
        "leftovers": [],
        "resume_verified": False,
        "phase_a_resume": {
            "handled": True,
            "upload": {"attempted": True, "verified": True, "field_present": True},
            "autofill_ready": {
                "ready": True,
                "filename": "dummy_resume_run_abc.pdf successfully uploaded",
            },
        },
    }
    assert report_has_verified_resume(report) is True
    assert sync_resume_verified_from_phase_a(report) is True
    assert report["resume_verified"] is True
    assert report["resume_field_present"] is True

    # Nested under workday only
    report2 = {
        "filled": [],
        "leftovers": [],
        "resume_verified": False,
        "workday": {
            "phase_a_resume": {
                "upload": {"verified": True, "attempted": True},
            }
        },
    }
    assert report_has_verified_resume(report2) is True
    apply_resume_success_gate(report2)
    assert report2["resume_verified"] is True


def test_merge_workday_sets_resume_verified():
    from fast_fill import _merge_workday_into_report

    report = {
        "filled": [],
        "leftovers": [],
        "resume_verified": False,
        "errors": [],
    }
    wd = {
        "phase_a_resume": {
            "handled": True,
            "upload": {"verified": True, "attempted": True, "field_present": True},
        },
        "workday_entry_path": "autofill_with_resume",
        "verdict": "FAIL",
        "blocker": "contact_incomplete",
    }
    _merge_workday_into_report(report, wd, {})
    assert report["phase_a_resume"]["upload"]["verified"] is True
    assert report["resume_verified"] is True


def main():
    test_phone_extension_classified()
    test_value_ok_rejects_essay_into_extension()
    test_flash_forbidden_includes_phone_extension()
    test_answer_leftover_skips_phone_extension()
    test_phone_extension_deferred_from_flash()
    test_resume_verified_from_phase_a_upload()
    test_merge_workday_sets_resume_verified()
    print("test_phone_ext_resume_verified: OK")


if __name__ == "__main__":
    main()
