"""Regression: cross-fill / soft-match / completion-gate quality guards.

Covers the broader failure class beyond phone/degree/FoS:
- Male⊂Female / IL⊂Idaho raw substring clicks
- Fiber sc==0 early commit
- Contamination soft-match polarity
- Bare promptIcon nudge selectors
- Date scope wiring
- REQUIRED_EMPTY ignoreId not swallowing searchSelect
- Visa status vs sponsorship routing
- HOW_HEARD over-broad "If Other" / essay
- Layer0 country→phone override
- can_claim_ready refuses stuck / mid-widget
- settle_before_advance / listbox_still_open helpers
- Answer memory rejects job-board into non-how-heard

Dummy-only. Never submit.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def test_soft_value_match_rejects_gender_and_state_substring():
    from verified_select import soft_value_match

    assert soft_value_match("Male", "Male")
    assert not soft_value_match("Male", "Female")
    assert not soft_value_match("Female", "Male")
    assert soft_value_match("Illinois", "Illinois")
    assert not soft_value_match("IL", "Idaho")
    assert not soft_value_match("Illinois", "Idaho")


def test_click_text_path_uses_soft_value_match_not_raw_in():
    import verified_select as vs

    src = inspect.getsource(vs.typable_dropdown_narrow_and_click)
    assert "click_option_exact_text" in src
    assert 'want_n.lower() in t.lower()' not in src
    # Fiber never accepts sc==0
    assert "sc == 0" not in src
    assert "sc >= min_score" in src
    click_src = inspect.getsource(vs.click_option_exact_text)
    assert "soft_value_match" in click_src
    assert 'want_n.lower() in t.lower()' not in click_src
    assert 'wl in tl' not in click_src


def test_nudge_listbox_never_bare_prompt_icon():
    import verified_select as vs

    src = inspect.getsource(vs.nudge_listbox_after_type)
    assert '[data-automation-id="promptIcon"]' not in src or "scoped" in src.lower()
    # Page-global bare multiSelect prompt must not appear as first-click target
    bare_lines = [
        ln
        for ln in src.splitlines()
        if "promptIcon" in ln and "formField" not in ln and "xpath" not in ln.lower()
    ]
    # Fallback selectors must be formField-scoped
    assert "formField-source" in src
    assert "formField-how_heard" in src
    for ln in bare_lines:
        assert "page.locator" not in ln or "formField" in ln, ln


def test_click_best_option_raised_floor():
    import verified_select as vs

    src = inspect.getsource(vs.click_best_option)
    assert "at_last_word=False" in src
    assert "min_score=40" not in src or "commit_min_score_for" in src
    assert "reject_confusable_country_option" in src


def test_commit_min_score_stricter_for_critical_types():
    from verified_select import commit_min_score_for

    assert commit_min_score_for("DEGREE") >= 70
    assert commit_min_score_for("PHONE_COUNTRY_CODE") >= 70
    assert commit_min_score_for("FIELD_OF_STUDY") >= 70
    assert commit_min_score_for("GENDER") >= 70
    assert commit_min_score_for("SCHOOL") >= 65


def test_contamination_default_rejects_male_in_female():
    from contamination import _default_soft_match

    assert _default_soft_match("Male", "Male")
    assert not _default_soft_match("Male", "Female")
    assert not _default_soft_match("IL", "Idaho")


def test_contamination_source_not_blindly_skipped():
    """Contamination uses soft_value_match — Male⊄Female — even if how-heard skipped."""
    import contamination as c

    src = inspect.getsource(c.contamination_sweep)
    assert "soft_match or _default_soft_match" in src or "_default_soft_match" in src
    assert 'ftype in ("HOW_HEARD", "SOURCE", "SCHOOL", "LOCATION")' in src


def test_required_empty_ignore_id_narrow():
    import exp_workday_selectors as wd

    js = wd.REQUIRED_EMPTY_JS
    assert "countryphonecode" in js
    # Must NOT ignore every id containing 'search' / 'autocomplete'
    assert "s.includes('search')" not in js
    assert "s.includes('autocomplete')" not in js


def test_date_fill_uses_scope_root():
    import exp_workday_selectors as wd

    src = inspect.getsource(wd._fill_date_spin)
    assert "root=root" in src or "root=scope" in src or "root=root" in src
    assert "_list_date_inputs(page, \"month\", mode=mode, root=" in src.replace("'", '"') or (
        "root=root" in src and "_list_date_inputs" in src
    )
    list_src = inspect.getsource(wd._list_date_inputs)
    assert "root" in list_src
    edu_src = inspect.getsource(wd._fill_education_section)
    # Must not use page-wide last To index into scoped fill
    assert "len(to_pool) - 1" not in edu_src


def test_visa_status_not_bare_no():
    from workday_selectors import _dummy_answer_for_wd_label

    cands = _dummy_answer_for_wd_label("Visa Requirement Status", {})
    assert cands
    assert not (len(cands) == 1 and cands[0] == "No")
    assert any("visa" in c.lower() or "n/a" in c.lower() or "none" in c.lower() for c in cands)

    sponsor = _dummy_answer_for_wd_label("Do you require visa sponsorship?", {})
    assert sponsor and sponsor[0].lower().startswith("no")


def test_how_heard_not_classify_bare_if_other_essay():
    from field_map import HOW_HEARD, INTEREST, classify_field

    ftype, _ = classify_field(
        {"label": "If Other, please specify how you heard about us", "name": "", "id": ""}
    )
    assert ftype == HOW_HEARD

    # Bare "If Other" / expand without hear/source context must NOT be HOW_HEARD
    ftype2, _ = classify_field(
        {"label": "If Other, please describe your experience", "name": "", "id": ""}
    )
    assert ftype2 != HOW_HEARD

    ftype3, _ = classify_field(
        {"label": "Expand on the above", "name": "essay_1", "id": ""}
    )
    assert ftype3 != HOW_HEARD


def test_layer0_country_phone_override():
    from field_map import ADDRESS_COUNTRY, PHONE_COUNTRY_CODE, classify_field

    ftype, layer = classify_field(
        {
            "label": "Country Phone Code",
            "name": "countryPhoneCode",
            "id": "",
            "autocomplete": "country",
        }
    )
    assert ftype == PHONE_COUNTRY_CODE
    assert ftype != ADDRESS_COUNTRY


def test_can_claim_ready_refuses_stuck_and_mid_widget():
    from page_progress import can_claim_ready

    base = {
        "verdict": "PARTIAL",
        "advanced_incomplete": False,
        "validation_after_advance": None,
        "required_empty_before_advance": [],
        "required_empty_after_fill": [],
        "leftovers": [],
        "vision_judge_live": {"complete": True, "verdict": "PASS"},
        "footer_primary_kind": "FINAL",
        "footer_primary_label": "Submit Application",
    }
    assert can_claim_ready({**base, "stuck_on_same_page": True}) is False
    assert can_claim_ready({**base, "listbox_open": True}) is False
    assert can_claim_ready({**base, "mid_widget_open": True}) is False


def test_settle_before_advance_helpers_exist():
    from verified_select import listbox_still_open, settle_before_advance, settle_open_listbox

    assert callable(listbox_still_open)
    assert callable(settle_before_advance)
    assert callable(settle_open_listbox)
    import exp_workday_selectors as wd

    src = inspect.getsource(wd._click_next_advance)
    assert "settle_before_advance" in src
    assert "listbox_still_open" in src


def test_fos_verify_requires_soft_match_not_picked_alone():
    import exp_workday_selectors as wd

    src = inspect.getsource(wd._fill_typable_edu_prompt)
    assert "fr.get(\"picked\")" not in src or "_readback_matches" in src
    assert "if ok or fr.get(\"picked\")" not in src
    assert "school_or_degree" in src or "fieldofstudy" in src


def test_semantic_bonus_disabled_for_countries():
    from verified_select import _semantic_option_bonus

    assert _semantic_option_bonus("Australia", "United States") == 0
    assert _semantic_option_bonus("United States (+1)", "United States") == 0


def test_answer_memory_rejects_job_board_for_phone():
    from continuous_learn import similar_leftover_answers
    import continuous_learn as cl

    src = inspect.getsource(cl.similar_leftover_answers)
    assert "is_safe_phone_country_search" in src or "_NON_COUNTRY_SEARCH_RE" in src


def test_phone_country_prior_fixes_still_present():
    from verified_select import (
        phone_country_code_search_query,
        sanitized_typeahead_token,
    )

    assert phone_country_code_search_query("Indeed") == "United States"
    assert sanitized_typeahead_token("PHONE_COUNTRY_CODE", "Indeed", ["Indeed"]) == (
        "United States"
    )


def test_can_claim_ready_refuses_advance_blocked_reason():
    from page_progress import can_claim_ready

    base = {
        "verdict": "PARTIAL",
        "advanced_incomplete": False,
        "validation_after_advance": None,
        "required_empty_before_advance": [],
        "required_empty_after_fill": [],
        "leftovers": [],
        "vision_judge_live": {"complete": True, "verdict": "PASS"},
        "footer_primary_kind": "FINAL",
        "footer_primary_label": "Submit Application",
    }
    assert can_claim_ready(base) is True
    assert (
        can_claim_ready({**base, "advance_blocked_reason": "listbox_still_open"})
        is False
    )
    assert (
        can_claim_ready({**base, "advance_blocked_reason": "required_fields_empty"})
        is False
    )


def test_click_next_advance_fail_closed_on_listbox():
    """_click_next_advance must set advance_blocked_reason when listbox open."""
    import exp_workday_selectors as wd

    src = inspect.getsource(wd._click_next_advance)
    assert "listbox_still_open" in src
    assert 'report["advance_blocked_reason"]' in src or "advance_blocked_reason" in src
    assert "listbox_still_open" in src
    # Must not swallow settle errors into a blind click
    assert "settle.get(\"error\")" in src or "settle.get('error')" in src


def test_contact_phase_no_success_when_advance_blocked():
    """Contact must not mint SUCCESS when Next was blocked (listbox / miss)."""
    import exp_workday_selectors as wd

    # Source of fill_contact / phase B advance tail
    src = inspect.getsource(wd)
    assert "listbox_still_open" in src
    assert 'reason = (\n            "listbox_still_open"' in src or (
        '"listbox_still_open"' in src and "advance_not_clicked" in src
    )
    # The dishonest path "Contact page complete without validation" is gone
    assert "Contact page complete without validation warning" not in src


def test_generic_advance_settles_listbox_before_click():
    import fast_fill as ff

    src = inspect.getsource(ff.try_advance_if_page_complete)
    assert "settle_before_advance" in src
    assert "listbox_still_open" in src


def test_footer_probe_prefers_advance_over_sticky_submit():
    from page_progress import FOOTER_PRIMARY_PROBE_JS

    js = FOOTER_PRIMARY_PROBE_JS
    assert "advanceRe" in js
    assert "finalRe" in js
    assert "prefer = 2" in js
    assert "prefer = 1" in js


def test_fail_taxonomy_reads_footer_primary_kind():
    from fail_taxonomy import apply_midwizard_to_decision, _footer_is_advance

    assert _footer_is_advance({"footer_primary_kind": "ADVANCE"}) is True
    assert _footer_is_advance({"footer_primary_kind": "FINAL"}) is False
    d = apply_midwizard_to_decision(
        {
            "ready_for_review": True,
            "footer_primary_kind": "ADVANCE",
            "never_submit": True,
            "platform": "greenhouse",
        },
        {"success": True, "verdict": "SUCCESS", "reasons": []},
    )
    assert d["success"] is False
    assert d["verdict"] == "FAIL_MIDWIZARD"


def test_lgbtqia_classifies_and_prefers_not_disclose():
    from dummy_answers import DETERMINISTIC_ANSWERS, shared_values
    from field_map import LGBTQIA, classify_field

    lab = "Do you identify as part of the LGBTQIA+ community?"
    ftype, _ = classify_field({"label": lab, "type": "radio", "name": ""})
    assert ftype == LGBTQIA, ftype
    assert shared_values()["LGBTQIA"] == "Prefer not to disclose"
    assert DETERMINISTIC_ANSWERS["LGBTQIA"] == "Prefer not to disclose"


def test_worked_with_employer_classifies():
    from field_map import WORKED_HERE_BEFORE, classify_field

    lab = "Do you now or have you ever worked with Lindblad Expeditions?"
    ftype, _ = classify_field({"label": lab, "type": "textarea", "name": ""})
    assert ftype == WORKED_HERE_BEFORE, ftype


def test_visa_string_blocked_on_worked_here_shape():
    from field_map import WORKED_HERE_BEFORE, value_ok_for_field_shape

    assert value_ok_for_field_shape(
        "No visa required",
        label="Do you now or have you ever worked with Lindblad Expeditions?",
        ftype=WORKED_HERE_BEFORE,
    ) is False
    assert value_ok_for_field_shape(
        "No",
        label="Do you now or have you ever worked with Lindblad Expeditions?",
        ftype=WORKED_HERE_BEFORE,
    ) is True


def test_pick_eeo_select_male_not_female():
    from lever_widgets import pick_eeo_select_option

    pick = pick_eeo_select_option(
        "GENDER",
        [
            {"label": "Female", "value": "Female"},
            {"label": "Male", "value": "Male"},
        ],
    )
    assert pick and pick["label"].lower() == "male"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok {t.__name__}")
    print(f"test_crossfill_completion_guards: {len(tests)} passed")
