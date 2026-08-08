"""Shared compose: dummy and real share policy; differ only on unique keys.

Never loads real profile.json PII — uses a synthetic mini-profile only.
"""

from __future__ import annotations

from dummy_answers import (
    DETERMINISTIC_ANSWERS,
    SHARED_FILL_POLICY,
    SHARED_VALUE_TYPES,
    UNIQUE_VALUE_TYPES,
    assert_shared_policy_synced,
    shared_values,
)
from field_map import (
    ADDRESS_CITY,
    ADDRESS_LINE1,
    CURRENT_COMPANY,
    CURRENT_TITLE,
    DEGREE,
    DISABILITY,
    DUMMY_ADDRESS,
    DUMMY_PROFILE,
    EMAIL,
    GENDER,
    INTEREST,
    LINKEDIN,
    NAME_FULL,
    NOTICE_PERIOD,
    PHONE,
    RELOCATION,
    SCHOOL,
    SPONSORSHIP,
    WORK_AUTH,
    YEARS_EXPERIENCE,
    build_unique_values,
    build_value_map,
    compose_fill_values,
    overlay_dummy_policy_on_real,
)
from gh_select import aliases_for, _score_option
from learning import lookup_learned, is_reusable_learning
from verified_select import clear_closest_match, rank_option_matches


def _fake_real_profile() -> tuple[dict, str]:
    """Synthetic real-shaped profile (not profile.json)."""
    profile = {
        "personal": {"full_name": "Ada Realton"},
        "contact": {
            "email": "ada.realton@example.com",
            "phone": "212-555-0199",
        },
        "links": {"linkedin": "https://www.linkedin.com/in/ada-realton"},
        "education": {
            "degrees": [
                {
                    "degree": "PhD",
                    "discipline": "Physics",
                    "school": "Real University",
                    "graduation_date": "May 2020",
                }
            ]
        },
        "experience": {
            "total_years_of_experience": 9.0,
            "current_company": "RealCorp",
            "current_title": "Staff Engineer",
        },
        # Deliberately wrong / empty policy — compose must ignore (use shared)
        "eeo_demographic": {
            "gender": "Decline to self identify",
            "hispanic_or_latino": "Decline To Self Identify",
            "race_ethnicity": "Decline to self identify",
            "veteran_status": "I don't wish to answer",
            "disability_status": "I do not want to answer",
        },
        "work_authorization": {"requires_sponsorship": "Yes"},
        "work_preferences": {
            "relocation": "No",
            "notice_period": "3 months",
        },
        "standard_screening_answers": {},
        "custom_question_answers": {},
        "address": {"country": "United States"},
        "account": {"password": "NotUsedInOverlay1!"},
    }
    address = "42 Real Street, Brooklyn, NY 11201"
    return profile, address


def test_shared_policy_synced():
    assert_shared_policy_synced()
    sv = shared_values()
    assert set(sv) == set(SHARED_VALUE_TYPES)
    assert sv["GENDER"] == SHARED_FILL_POLICY["eeo_demographic"]["gender"]
    assert sv["SPONSORSHIP"] == "No"
    assert "interested in this role" in sv["INTEREST"].lower()


def test_dummy_and_real_share_all_shared_keys():
    """Core architecture: shared keys identical after compose."""
    profile, address = _fake_real_profile()
    dummy = build_value_map(DUMMY_PROFILE, DUMMY_ADDRESS)
    real = build_value_map(profile, address)
    for key in SHARED_VALUE_TYPES:
        assert dummy.get(key) == real.get(key), (
            f"shared drift {key}: dummy={dummy.get(key)!r} real={real.get(key)!r}"
        )
        assert dummy.get(key) == shared_values().get(key), key


def test_dummy_and_real_differ_on_unique_identity_edu():
    profile, address = _fake_real_profile()
    dummy = build_value_map(DUMMY_PROFILE, DUMMY_ADDRESS)
    real = build_value_map(profile, address)
    assert dummy[NAME_FULL] != real[NAME_FULL]
    assert dummy[EMAIL] != real[EMAIL]
    assert dummy[PHONE] != real[PHONE]
    assert dummy[SCHOOL] != real[SCHOOL]
    assert dummy[DEGREE] != real[DEGREE]
    assert dummy[CURRENT_COMPANY] != real[CURRENT_COMPANY]
    assert dummy[YEARS_EXPERIENCE] != real[YEARS_EXPERIENCE]
    assert real[NAME_FULL] == "Ada Realton"
    assert real[SCHOOL] == "Real University"
    assert real[DEGREE] == "PhD"


def test_compose_ignores_profile_policy_sections():
    """Real profile EEO/sponsorship must not leak into the value map."""
    profile, address = _fake_real_profile()
    out = build_value_map(profile, address)
    assert out[GENDER] == DETERMINISTIC_ANSWERS["GENDER"]
    assert out[SPONSORSHIP] == "No"
    assert out[RELOCATION] == DETERMINISTIC_ANSWERS["RELOCATION"]
    assert out[NOTICE_PERIOD] == DETERMINISTIC_ANSWERS["NOTICE_PERIOD"]
    assert "want to answer" not in out[DISABILITY].lower()
    assert out[WORK_AUTH] == DETERMINISTIC_ANSWERS["WORK_AUTH"]
    assert INTEREST in out and out[INTEREST] == shared_values()[INTEREST]


def test_overlay_keeps_real_contact_and_education():
    profile, address = _fake_real_profile()
    real_vals = build_value_map(profile, address)
    out = overlay_dummy_policy_on_real(real_vals, real_address_present=True)

    assert out[NAME_FULL] == "Ada Realton"
    assert out[EMAIL] == "ada.realton@example.com"
    assert out[PHONE] == "212-555-0199"
    assert out[LINKEDIN] == "https://www.linkedin.com/in/ada-realton"
    assert out[SCHOOL] == "Real University"
    assert out[DEGREE] == "PhD"
    assert out[ADDRESS_LINE1] == address
    assert out[ADDRESS_CITY] == "Brooklyn"


def test_overlay_applies_shared_eeo_and_work_auth():
    profile, address = _fake_real_profile()
    real_vals = build_unique_values(profile, address)
    # Unique alone must NOT have shared EEO
    assert GENDER not in real_vals or real_vals.get(GENDER) in ("", None)
    out = compose_fill_values(real_vals)
    assert out[GENDER] == DETERMINISTIC_ANSWERS["GENDER"]
    assert out[SPONSORSHIP] == DETERMINISTIC_ANSWERS["SPONSORSHIP"]


def test_overlay_does_not_force_dummy_experience():
    profile, address = _fake_real_profile()
    out = build_value_map(profile, address)
    assert out[CURRENT_COMPANY] == "RealCorp"
    assert out[CURRENT_TITLE] == "Staff Engineer"
    assert str(out[YEARS_EXPERIENCE]) == "9.0"
    assert out[CURRENT_COMPANY] != "Example Corp"


def test_overlay_falls_back_dummy_address_when_empty():
    """Compat helper may fill Springfield — prepare_real_run must NOT use this."""
    profile, _ = _fake_real_profile()
    real_vals = build_value_map(profile, "")
    out = overlay_dummy_policy_on_real(real_vals, real_address_present=False)

    assert "Springfield" in str(out.get(ADDRESS_LINE1) or "")
    assert out.get(ADDRESS_CITY) == "Springfield"
    assert out[NAME_FULL] == "Ada Realton"
    assert out[EMAIL] == "ada.realton@example.com"


def test_build_value_map_empty_address_does_not_inject_springfield():
    """Real compose path: empty address stays empty (no dummy Springfield)."""
    profile, _ = _fake_real_profile()
    out = build_value_map(profile, "")
    assert "Springfield" not in str(out.get(ADDRESS_LINE1) or "")
    assert (out.get(ADDRESS_CITY) or "") == ""
    assert out[NAME_FULL] == "Ada Realton"
    assert out[GENDER] == DETERMINISTIC_ANSWERS["GENDER"]


def test_flash_handoff_uses_run_fill_values_not_hardcoded_dummy():
    """Real-mode Flash prompt must ground on run unique identity, not Test Dummy."""
    from flash_leftovers import build_leftovers_handoff, build_leftovers_prompt

    profile, address = _fake_real_profile()
    vals = build_value_map(profile, address)
    report = {
        "url": "https://example.com/jobs/1",
        "platform": "greenhouse",
        "dummy": False,
        "filled": [],
        "leftovers": [
            {
                "label": "Why do you want this role?",
                "type": "INTEREST",
                "reason": "unmapped",
                "flash_candidate": True,
            }
        ],
        "fill_values": vals,
        "identity_email": "ada.realton@example.com",
        "email": "ada.realton@example.com",
    }
    prompt = build_leftovers_prompt(report, grounded=True, values=vals)
    assert "Ada Realton" in prompt
    assert "ada.realton@example.com" in prompt
    assert "name: Ada Realton" in prompt
    # Must not ground on the dummy fixture resume when dummy=False
    assert "DUMMY RESUME EXCERPT" not in prompt
    assert "Test Dummy 405-555-0100" not in prompt
    assert "SHARED" in prompt or "shared" in prompt.lower()
    hand = build_leftovers_handoff(report, grounded=True, values=vals)
    assert hand.get("dummy") is False
    assert hand.get("never_submit") is True
    assert "Ada Realton" in (hand.get("prompt") or "")
    assert "Test Dummy 405-555-0100" not in (hand.get("prompt") or "")


def test_unique_and_shared_partition_disjoint():
    overlap = SHARED_VALUE_TYPES & UNIQUE_VALUE_TYPES
    assert not overlap, f"partition overlap: {overlap}"


def test_phd_degree_aliases_prefer_doctorate():
    from dummy_answers import GH_DEGREE_OPTIONS

    cands = aliases_for("DEGREE", "PhD")
    assert any("Doctorate" in c or "PhD" in c or "Ph.D" in c for c in cands)
    # Bachelor must not be in PhD alias list
    assert not any("Bachelor" in c for c in cands)
    ranked = rank_option_matches(GH_DEGREE_OPTIONS, cands, _score_option)
    pick = clear_closest_match(ranked, at_last_word=True, min_score=40)
    assert pick is not None
    _idx, text, _score = pick
    assert "Doctorate" in text


def test_lookup_learned_not_blocked_for_policy():
    """Real mode must still be able to read global policy learnings (no mode gate)."""
    assert is_reusable_learning("Will you require sponsorship?", "No")
    got = lookup_learned("Will you require sponsorship?")
    assert got is None or isinstance(got, str)


def test_dummy_profile_policy_matches_shared_block():
    """DUMMY_PROFILE policy sections must be the shared block (no diverging copy)."""
    for key, block in SHARED_FILL_POLICY.items():
        assert DUMMY_PROFILE.get(key) == block, key


def test_masters_degree_aliases_prefer_master_not_bachelor():
    """Real M.S. / Master's must not soft-match Bachelor (live grvty bug)."""
    from dummy_answers import GH_DEGREE_OPTIONS

    for value in ("Master's Degree", "M.S.", "M.S., Computer Science", "Masters"):
        cands = aliases_for("DEGREE", value)
        assert not any("Bachelor" in c for c in cands), (value, cands)
        ranked = rank_option_matches(GH_DEGREE_OPTIONS, cands, _score_option)
        pick = clear_closest_match(ranked, at_last_word=True, min_score=40)
        assert pick is not None, value
        _idx, text, _score = pick
        assert "Master" in text or "M.S" in text or "M.S." in text, (value, text)


def test_env_address_preferred_over_empty_compose():
    """FASTFILL_ADDRESS_TEXT must win so Start's pick matches the fill."""
    import os

    from field_map import resolve_real_address_text

    prev = os.environ.get("FASTFILL_ADDRESS_TEXT")
    try:
        os.environ["FASTFILL_ADDRESS_TEXT"] = "9 Env Lane, Austin, TX 78701"
        got = resolve_real_address_text(job_id="nonexistent-job-id-xyz")
        assert got == "9 Env Lane, Austin, TX 78701"
        assert "Springfield" not in got
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_ADDRESS_TEXT", None)
        else:
            os.environ["FASTFILL_ADDRESS_TEXT"] = prev


def test_flash_real_rules_not_dummy_grounding():
    from flash_leftovers import LEFTOVERS_RULES, LEFTOVERS_RULES_REAL

    assert "DUMMY RESUME" in LEFTOVERS_RULES or "dummy" in LEFTOVERS_RULES.lower()
    assert "SHARED policy" in LEFTOVERS_RULES_REAL or "shared policy" in LEFTOVERS_RULES_REAL.lower()
    assert "Test Dummy" not in LEFTOVERS_RULES_REAL
    assert "never invent a different name" in LEFTOVERS_RULES_REAL.lower() or (
        "do not invent a different name" in LEFTOVERS_RULES_REAL.lower()
    )


if __name__ == "__main__":
    test_shared_policy_synced()
    test_dummy_and_real_share_all_shared_keys()
    test_dummy_and_real_differ_on_unique_identity_edu()
    test_compose_ignores_profile_policy_sections()
    test_overlay_keeps_real_contact_and_education()
    test_overlay_applies_shared_eeo_and_work_auth()
    test_overlay_does_not_force_dummy_experience()
    test_overlay_falls_back_dummy_address_when_empty()
    test_build_value_map_empty_address_does_not_inject_springfield()
    test_flash_handoff_uses_run_fill_values_not_hardcoded_dummy()
    test_unique_and_shared_partition_disjoint()
    test_phd_degree_aliases_prefer_doctorate()
    test_lookup_learned_not_blocked_for_policy()
    test_dummy_profile_policy_matches_shared_block()
    test_masters_degree_aliases_prefer_master_not_bachelor()
    test_env_address_preferred_over_empty_compose()
    test_flash_real_rules_not_dummy_grounding()
    print("OK test_real_policy_overlay")
