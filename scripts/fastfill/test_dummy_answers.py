"""Regression: deterministic dummy answers + GH option scoring (grvty live).

Live fill_live_20260802_052849 showed:
  Disability intended "I do not have a disability" → readback "I do not want to answer"
  Veteran intended "I am not a protected veteran" → readback "I don't wish to answer"
  Degree intended Master's → readback "Bachelor's Degree"
  Discipline unclassified → blank
  Employment Eligibility unclassified → blank
  Flash flash_zero_fill / invoked=false

Root causes covered here:
  1. Decline aliases exact-matched at score 100 and beat preferred soft-match 80/90
  2. EEO type fragment typed "want/wish to answer" and filtered to Decline only
  3. Bachelor + Master both scored 80; option-list index 0 (Bachelor) won
  4. already_correct treated Decline as OK because Decline was in alias list
"""

from __future__ import annotations

from dummy_answers import (
    CATALOG_COVERAGE,
    DETERMINISTIC_ANSWERS,
    GH_DEGREE_OPTIONS,
    GH_DISABILITY_OPTIONS,
    GH_HISPANIC_OPTIONS,
    GH_VETERAN_OPTIONS,
    answer_for,
    coverage_types,
)
from field_map import (
    DUMMY_ADDRESS,
    DUMMY_PROFILE,
    build_value_map,
    classify_field,
)
from gh_select import (
    _score_option,
    _shown_matches_cands,
    _type_fragment_for,
    aliases_for,
)
from verified_select import clear_closest_match, rank_option_matches


def _pick(options: list[str], field_type: str, value: str):
    cands = aliases_for(field_type, value)
    ranked = rank_option_matches(options, cands, _score_option)
    return clear_closest_match(ranked, at_last_word=True, min_score=40)


def test_catalog_covers_required_types():
    required = {
        "DISABILITY",
        "VETERAN",
        "HISPANIC",
        "GENDER",
        "WORK_AUTH",
        "SPONSORSHIP",
        "CLEARANCE",
        "DEGREE",
        "SCHOOL",
        "DISCIPLINE",
        "SALARY_EXPECTED",
        "NAME_FULL",
    }
    assert required <= set(coverage_types())
    assert required <= set(DETERMINISTIC_ANSWERS)


def test_build_value_map_matches_catalog():
    vals = build_value_map(DUMMY_PROFILE, DUMMY_ADDRESS)
    for key, expect in DETERMINISTIC_ANSWERS.items():
        if key == "RACE":
            continue  # Decline by policy
        assert vals.get(key), f"missing {key}"
        if key in (
            "DISABILITY",
            "VETERAN",
            "HISPANIC",
            "GENDER",
            "WORK_AUTH",
            "DEGREE",
            "DISCIPLINE",
            "SCHOOL",
            "SALARY_EXPECTED",
            "CLEARANCE",
            "NAME_FULL",
        ):
            got = str(vals[key]).lower()
            exp = expect.lower()
            assert exp in got or got in exp or got == exp, (key, vals[key], expect)


def test_classify_grvty_labels():
    assert classify_field({"label": "Discipline", "name": "", "id": "", "placeholder": ""})[0] == "DISCIPLINE"
    assert (
        classify_field(
            {
                "label": "Employment Eligibility Information*",
                "name": "",
                "id": "",
                "placeholder": "",
            }
        )[0]
        == "WORK_AUTH"
    )
    assert classify_field({"label": "Degree", "name": "", "id": "", "placeholder": ""})[0] == "DEGREE"
    assert (
        classify_field(
            {"label": "Disability Status", "name": "", "id": "", "placeholder": ""}
        )[0]
        == "DISABILITY"
    )
    for ftype, label, _ans in CATALOG_COVERAGE:
        if ftype in ("MARKETING_CONSENT",):
            continue  # soft label
        got, _ = classify_field({"label": label, "name": "", "id": "", "placeholder": ""})
        # Affirmation may resolve NAME_FULL; clearance labels vary
        if got is None and ftype in ("BACKGROUND_CHECK", "MARKETING_CONSENT"):
            continue
        if got:
            assert got == ftype or (
                ftype == "NAME_FULL" and got in ("NAME_FULL", "NAME_FIRST")
            ), (label, got, ftype)


def test_disability_prefers_no_over_decline():
    """Live bug: Decline exact-alias score 100 beat preferred soft 80/90."""
    intended = answer_for("DISABILITY")
    # Cross-polarity: Decline option vs concrete alias = 0
    assert _score_option("I do not want to answer", intended) == 0
    assert _score_option(
        "No, I do not have a disability and have not had one in the past", intended
    ) >= 70
    pick = _pick(GH_DISABILITY_OPTIONS, "DISABILITY", intended)
    assert pick is not None
    assert "disability" in pick[1].lower()
    assert "want to answer" not in pick[1].lower()
    # Type fragment must not filter to Decline
    frag = _type_fragment_for("DISABILITY", aliases_for("DISABILITY", intended))
    assert "want to answer" not in frag.lower()
    assert "wish to answer" not in frag.lower()
    # Wrong Decline readback must NOT already_correct_skip
    assert not _shown_matches_cands(
        "I do not want to answer",
        aliases_for("DISABILITY", intended),
        field_type="DISABILITY",
    )


def test_veteran_prefers_not_veteran_over_decline():
    intended = answer_for("VETERAN")
    assert _score_option("I don't wish to answer", intended) == 0
    pick = _pick(GH_VETERAN_OPTIONS, "VETERAN", intended)
    assert pick is not None
    assert "not a protected veteran" in pick[1].lower()
    frag = _type_fragment_for("VETERAN", aliases_for("VETERAN", intended))
    assert "wish to answer" not in frag.lower()
    assert not _shown_matches_cands(
        "I don't wish to answer",
        aliases_for("VETERAN", intended),
        field_type="VETERAN",
    )


def test_hispanic_prefers_no_over_decline():
    intended = answer_for("HISPANIC")
    assert intended.lower() == "no"
    pick = _pick(GH_HISPANIC_OPTIONS, "HISPANIC", intended)
    assert pick is not None
    assert pick[1].lower() == "no"
    assert not _shown_matches_cands(
        "Decline To Self Identify",
        aliases_for("HISPANIC", intended),
        field_type="HISPANIC",
    )


def test_degree_masters_beats_bachelors():
    """Live bug: Master+Bachelor both scored 80; Bachelor at index 0 won."""
    intended = answer_for("DEGREE")
    cands = aliases_for("DEGREE", intended)
    assert not any("bachelor" in c.lower() for c in cands)
    pick = _pick(GH_DEGREE_OPTIONS, "DEGREE", intended)
    assert pick is not None
    assert "Master" in pick[1]
    assert "Bachelor" not in pick[1]
    # Bachelor readback is NOT already-correct for Master's intent
    assert not _shown_matches_cands(
        "Bachelor's Degree", cands, field_type="DEGREE"
    )


def test_rank_preferred_phase_ignores_decline_exact():
    """Even with Decline aliases present, preferred phase must win."""
    cands = aliases_for("DISABILITY", "I do not have a disability")
    assert any("want to answer" in c.lower() for c in cands)  # fallback still listed
    ranked = rank_option_matches(GH_DISABILITY_OPTIONS, cands, _score_option)
    assert ranked
    assert "want to answer" not in ranked[0][2].lower()


def test_dummy_profile_eeo_policy():
    eeo = DUMMY_PROFILE["eeo_demographic"]
    assert eeo["gender"].lower() == "male"
    assert eeo["hispanic_or_latino"].lower() == "no"
    assert "disability" in eeo["disability_status"].lower()
    assert "not" in eeo["veteran_status"].lower()
    deg0 = DUMMY_PROFILE["education"]["degrees"][0]
    assert "Master" in deg0["degree"]
    assert deg0["discipline"] == "Computer Science"
    assert "Alabama" in deg0["school"]


if __name__ == "__main__":
    test_catalog_covers_required_types()
    test_build_value_map_matches_catalog()
    test_classify_grvty_labels()
    test_disability_prefers_no_over_decline()
    test_veteran_prefers_not_veteran_over_decline()
    test_hispanic_prefers_no_over_decline()
    test_degree_masters_beats_bachelors()
    test_rank_preferred_phase_ignores_decline_exact()
    test_dummy_profile_eeo_policy()
    print("test_dummy_answers: OK")
