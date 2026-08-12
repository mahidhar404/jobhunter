#!/usr/bin/env python3
"""Regression: Workday education Field of Study fill path (Elanco-class)."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def test_fos_before_school_in_dummy_answers():
    """Polluted wrap labels with 'University…' must still yield FoS, not school."""
    from field_map import FIELD_OF_STUDY, SCHOOL
    from workday_selectors import _dummy_answer_for_wd_label

    polluted = (
        "School University of Alabama, Tuscaloosa "
        "Degree Master's Degree Field of Study"
    )
    cands = _dummy_answer_for_wd_label(
        polluted,
        {FIELD_OF_STUDY: "Computer Science", SCHOOL: "University of Alabama, Tuscaloosa"},
    )
    assert cands, cands
    assert any("computer" in c.lower() for c in cands), cands
    # Must not return only school names for a FoS-bearing label
    assert not all("alabama" in c.lower() or "university" in c.lower() for c in cands), cands


def test_dummy_answer_field_of_study_clean():
    from field_map import FIELD_OF_STUDY
    from workday_selectors import _dummy_answer_for_wd_label

    cands = _dummy_answer_for_wd_label(
        "Field of Study",
        {FIELD_OF_STUDY: "Computer Science"},
    )
    assert cands and "Computer Science" in cands[0]


def test_fos_selectors_include_formfield():
    from exp_workday_selectors import WD_CONTACT_SELECTORS

    sels = WD_CONTACT_SELECTORS.get("formField-fieldOfStudy") or []
    joined = " ".join(sels)
    assert "formField-fieldOfStudy" in joined
    assert "fieldOfStudy" in joined


def test_education_fos_fill_uses_combobox_not_plain_text():
    """Elanco FoS is typable searchSelect — never bare combobox=False only."""
    import exp_workday_selectors as wd

    src = inspect.getsource(wd._fill_education_field_of_study)
    assert "combobox=True" in src
    assert "formField-fieldOfStudy" in src
    assert "_fill_typable_edu_prompt" in src
    assert "FIELD_OF_STUDY" in src
    # Plain-text-only fallback removed from dedicated FoS path
    assert "combobox=False" not in src

    edu_src = inspect.getsource(wd._fill_education_section)
    assert "_fill_education_field_of_study" in edu_src


def test_select_one_passes_nested_filter_for_fos():
    import exp_workday_selectors as wd

    src = inspect.getsource(wd._fill_select_one_by_label)
    assert "filter_input" in src
    assert "FIELD_OF_STUDY" in src
    # FoS inferred before school so university wrap text cannot steal type
    fos_i = src.find('ftype = "FIELD_OF_STUDY"')
    school_i = src.find('ftype = "SCHOOL"')
    assert fos_i > 0 and school_i > fos_i, (fos_i, school_i)


def test_automation_id_maps_fos_field_type():
    import exp_workday_selectors as wd

    src = inspect.getsource(wd._fill_automation_id)
    assert "fieldofstudy" in src.lower()
    assert "FIELD_OF_STUDY" in src
    assert "aliases_for" in src


def test_fos_candidates_include_computer_science():
    from field_map import DISCIPLINE, FIELD_OF_STUDY, MAJOR
    from exp_workday_selectors import _fos_candidates

    cands = _fos_candidates(
        {FIELD_OF_STUDY: "Computer Science", DISCIPLINE: "Computer Science", MAJOR: "CS"}
    )
    assert any("computer science" in c.lower() for c in cands), cands


def test_select_one_infers_phone_country_before_degree():
    """Country Phone Code label must not get degree/how-heard candidates."""
    import inspect

    import exp_workday_selectors as wd

    src = inspect.getsource(wd._fill_select_one_by_label)
    phone_i = src.find('ftype = "PHONE_COUNTRY_CODE"')
    fos_i = src.find('ftype = "FIELD_OF_STUDY"')
    assert phone_i > 0 and fos_i > phone_i, (phone_i, fos_i)
    assert "phone_country_code_candidates" in src


if __name__ == "__main__":
    test_fos_before_school_in_dummy_answers()
    test_dummy_answer_field_of_study_clean()
    test_fos_selectors_include_formfield()
    test_education_fos_fill_uses_combobox_not_plain_text()
    test_select_one_passes_nested_filter_for_fos()
    test_automation_id_maps_fos_field_type()
    test_fos_candidates_include_computer_science()
    test_select_one_infers_phone_country_before_degree()
    print("ok")
