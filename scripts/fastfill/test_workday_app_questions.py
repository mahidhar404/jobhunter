#!/usr/bin/env python3
"""Unit tests for Workday App Questions / SUCCESS gate helpers (no live ATS)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from workday_selectors import (  # noqa: E402
    _dummy_answer_for_wd_label,
    _finalize_workday_verdict,
    _required_empties_as_leftovers,
)
from field_map import DEGREE, HOW_HEARD, SCHOOL  # noqa: E402


def test_dummy_answer_work_auth_yes() -> None:
    cands = _dummy_answer_for_wd_label(
        "Are you legally authorized to work in the United States?*"
    )
    assert cands and cands[0].lower().startswith("yes"), cands


def test_dummy_answer_sponsorship_no() -> None:
    cands = _dummy_answer_for_wd_label(
        "Will you now or in the future require sponsorship for employment visa status?*"
    )
    assert cands and cands[0].lower().startswith("no"), cands


def test_dummy_answer_education_level() -> None:
    cands = _dummy_answer_for_wd_label(
        "Highest level of education completed?*",
        {DEGREE: "M.S., Example Studies"},
    )
    assert any("master" in c.lower() for c in cands), cands


def test_dummy_answer_essay_empty() -> None:
    assert _dummy_answer_for_wd_label("Please describe why you want this role") == []


def test_dummy_answer_how_heard() -> None:
    cands = _dummy_answer_for_wd_label(
        "How did you hear about this position?*",
        {HOW_HEARD: "Internet job board"},
    )
    assert "Internet job board" in cands


def test_dummy_answer_over_18_yes() -> None:
    cands = _dummy_answer_for_wd_label("Are you over 18?*")
    assert cands and cands[0].lower() == "yes", cands
    # ATS3-010: must not include bare "No" fall-through
    assert not any(c.strip().lower() == "no" for c in cands)


def test_wd_county_is_combobox_in_pack() -> None:
    """ATS3-009: regionSubdivision1 (county) must be combobox mode in pack."""
    from exp_workday_selectors import WD_SELECTOR_PACK

    county_rows = [
        r for r in WD_SELECTOR_PACK if "regionSubdivision1" in (r[0] or "")
    ]
    assert county_rows, "county selector missing from WD_SELECTOR_PACK"
    assert all(r[2] == "combobox" for r in county_rows), county_rows


def test_wd_county_is_combobox_in_two_phase_plan() -> None:
    """Two-phase fill plan must match CSS pack: county = combobox, not text."""
    from exp_workday_selectors import build_contact_fill_plan
    from field_map import ADDRESS_COUNTY, ADDRESS_LINE2

    plan, _ = build_contact_fill_plan(
        {ADDRESS_COUNTY: "Sangamon", ADDRESS_LINE2: "Apt 1A"}
    )
    county = [r for r in plan if r[0] == "addressSection_regionSubdivision1"]
    addr2 = [r for r in plan if r[0] == "addressSection_addressLine2"]
    assert county and county[0][2] is True, county
    assert addr2 and addr2[0][2] is False, addr2


def test_dummy_answer_school() -> None:
    cands = _dummy_answer_for_wd_label(
        "School*",
        {SCHOOL: "University of Alabama, Tuscaloosa"},
    )
    assert "University of Alabama, Tuscaloosa" in cands


def test_finalize_demotes_contact_only_success() -> None:
    report = {
        "verdict": "SUCCESS",
        "reached_contact": True,
        "ready_for_review": False,
        "required_empty_before_advance": [],
    }
    assert _finalize_workday_verdict(report) == "FAIL"
    assert report["verdict_reason"] == "multipage_incomplete_not_ready_for_review"


def test_finalize_success_at_review() -> None:
    report = {
        "verdict": "FAIL",
        "ready_for_review": True,
        "required_empty_before_advance": [],
        "advanced_incomplete": False,
        "stuck_on_same_page": False,
    }
    assert _finalize_workday_verdict(report) == "SUCCESS"


def test_finalize_required_empties_fail() -> None:
    report = {
        "verdict": "SUCCESS",
        "ready_for_review": True,
        "required_empty_before_advance": [
            {"id": "x", "reason": "empty_required_combobox", "label": "Education*"}
        ],
    }
    assert _finalize_workday_verdict(report) == "FAIL"


def test_empties_as_leftovers() -> None:
    rows = _required_empties_as_leftovers(
        [{"id": "abc", "reason": "empty_required_combobox", "label": "Sponsor?*"}]
    )
    assert len(rows) == 1
    assert rows[0]["flash_candidate"] is True
    assert rows[0]["label"] == "Sponsor?*"


def main() -> None:
    test_dummy_answer_work_auth_yes()
    test_dummy_answer_sponsorship_no()
    test_dummy_answer_education_level()
    test_dummy_answer_essay_empty()
    test_dummy_answer_how_heard()
    test_dummy_answer_over_18_yes()
    test_wd_county_is_combobox_in_pack()
    test_wd_county_is_combobox_in_two_phase_plan()
    test_dummy_answer_school()
    test_finalize_demotes_contact_only_success()
    test_finalize_success_at_review()
    test_finalize_required_empties_fail()
    test_empties_as_leftovers()
    print("test_workday_app_questions: OK")


if __name__ == "__main__":
    main()
