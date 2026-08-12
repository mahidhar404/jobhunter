#!/usr/bin/env python3
"""Unit tests: honest fill metrics (no browser). Dummy-only fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fast_fill import _finalize, is_verified_fill_row  # noqa: E402
from scorecard_fast import (  # noqa: E402
    assert_honest_advance,
    assert_honest_filled,
    _row_verified,
    _verified_filled_count,
)


def test_placeholder_readback_never_counts():
    from ashby_widgets import is_empty_ui_value
    from fast_fill import _value_matches_readback

    assert is_empty_ui_value("Type here...") is True
    assert is_empty_ui_value("62701") is False
    assert _value_matches_readback("62701", "Type here...") is False
    assert _value_matches_readback("62701", "62701") is True
    row = {
        "type": "ADDRESS_ZIP",
        "ok": True,
        "verified": True,
        "value": "62701",
        "readback": "Type here...",
    }
    assert is_verified_fill_row(row) is False


def test_stuck_status_never_counts_as_filled():
    assert is_verified_fill_row({"status": "stuck", "verified": True, "readback": "x"}) is False
    assert _row_verified({"status": "stuck", "verified": True, "readback": "x"}) is False


def test_verified_readback_counts():
    row = {"status": "filled", "verified": True, "readback": "Test", "ok": True}
    assert is_verified_fill_row(row) is True
    assert _row_verified(row) is True


def test_ok_without_verified_does_not_count():
    row = {"ok": True, "via": "greenhouse_selector_pack", "type": "EMAIL", "value": "a@b.c"}
    assert is_verified_fill_row(row) is False
    assert _row_verified(row) is False


def test_finalize_demotes_unverified_and_rejects_success_with_banner():
    report = {
        "filled": [
            {"type": "EMAIL", "ok": True, "verified": True, "readback": "a@b.c"},
            {"type": "PHONE", "ok": True},  # no readback
        ],
        "leftovers": [],
        "extracted_count": 2,
        "verdict": "SUCCESS",
        "validation_after_advance": {"present": True, "banner": "Errors Found"},
        "entry_prepass": {"final_clicks": 0},
    }
    out = _finalize(report)
    assert out["verdict"] == "FAIL"
    assert out["filled_count"] == 1
    assert len(out["filled"]) == 1
    assert any(l.get("reason") == "unverified_readback" for l in out["leftovers"])


def test_scorecard_rejects_success_with_validation():
    bad = {
        "never_submit": True,
        "submit_clicked": False,
        "verdict": "SUCCESS",
        "validation_after_advance": {"present": True},
    }
    try:
        assert_honest_advance(bad, path=Path("x.json"))
        raise AssertionError("expected assert_honest_advance to fail")
    except AssertionError as e:
        assert "validation_after_advance" in str(e)


def test_scorecard_rejects_success_status_stuck():
    bad = {
        "never_submit": True,
        "verdict": "SUCCESS",
        "filled": [{"status": "stuck", "verified": True, "readback": "x"}],
    }
    try:
        assert_honest_filled(bad, path=Path("x.json"))
        raise AssertionError("expected assert_honest_filled to fail")
    except AssertionError as e:
        assert "status=stuck" in str(e)


def test_scorecard_allows_fail_with_banner():
    ok = {
        "never_submit": True,
        "submit_clicked": False,
        "verdict": "FAIL",
        "validation_after_advance": {"present": True},
        "filled": [{"verified": True, "readback": "Test", "status": "filled"}],
    }
    assert_honest_advance(ok)
    assert_honest_filled(ok)
    assert _verified_filled_count(ok) == 1


def test_flash_off_when_unrequested():
    from scorecard_fast import assert_flash_off_when_unrequested

    assert_flash_off_when_unrequested(
        {"flash_called": False, "flash_leftovers_requested": False}
    )
    assert_flash_off_when_unrequested(
        {"flash_called": True, "flash_leftovers_requested": True}
    )
    try:
        assert_flash_off_when_unrequested(
            {"flash_called": True, "flash_leftovers_requested": False}
        )
        raise AssertionError("expected flash_off failure")
    except AssertionError as e:
        assert "flash_called" in str(e)


def test_eval_gate_exit_codes():
    from eval_suite import gate_exit_code

    quality_only = {
        "safety_fail_n": 0,
        "quality_fail_n": 1,
        "safety": {"never_submit_all": True, "flash_called_while_off": 0},
    }
    rows_q = [{"pass": False, "slo_fails": ["verified_coverage=0.6<0.9"], "never_submit": True}]
    assert gate_exit_code(rows_q, quality_only) == 0
    assert gate_exit_code(rows_q, quality_only, strict_safety=True) == 0
    assert gate_exit_code(rows_q, quality_only, strict=True, strict_safety=True) == 2

    safety = {
        "safety_fail_n": 1,
        "quality_fail_n": 0,
        "safety": {"never_submit_all": True, "flash_called_while_off": 1},
    }
    rows_s = [{"pass": False, "slo_fails": ["flash_called_while_off"], "never_submit": True}]
    assert gate_exit_code(rows_s, safety, strict_safety=True) == 1
    assert gate_exit_code(rows_s, safety, strict=True, strict_safety=True) == 1


def test_gh_select_shown_counts():
    row = {"via": "gh_select", "ok": True, "shown": "United States", "picked": "United States"}
    assert is_verified_fill_row(row) is True


def test_finalize_demotes_success_when_stuck_on_same_page():
    report = {
        "filled": [
            {"type": "EMAIL", "ok": True, "verified": True, "readback": "a@b.c"},
        ],
        "leftovers": [],
        "extracted_count": 1,
        "verdict": "SUCCESS",
        "stuck_on_same_page": True,
        "advanced": True,
        "page_fingerprint_before": "aaa",
        "page_fingerprint_after": "aaa",
        "entry_prepass": {"final_clicks": 0},
    }
    out = _finalize(report)
    assert out["verdict"] == "FAIL"
    assert out["stuck_on_same_page"] is True


def test_finalize_demotes_success_when_required_empties():
    report = {
        "filled": [],
        "leftovers": [],
        "extracted_count": 0,
        "verdict": "SUCCESS",
        "required_empty_before_advance": [{"id": "phone", "reason": "empty_required_input"}],
        "entry_prepass": {"final_clicks": 0},
    }
    out = _finalize(report)
    assert out["verdict"] == "FAIL"


def test_empty_linkedin_readback_never_counts():
    """Ashby LinkedIn false success: verified=True + empty/null readback → leftover."""
    assert (
        is_verified_fill_row(
            {
                "type": "LINKEDIN",
                "ok": True,
                "verified": True,
                "value": "https://www.linkedin.com/in/test-dummy-000000000",
                "readback": "",
                "verified_value": None,
            }
        )
        is False
    )
    assert (
        is_verified_fill_row(
            {
                "via": "ashby_widgets",
                "type": "LINKEDIN",
                "ok": True,
                "verified": True,
                "mode": "fill",
                "value": "https://www.linkedin.com/in/test-dummy-000000000",
                "readback": "",
            }
        )
        is False
    )
    ok_row = {
        "via": "ashby_widgets",
        "type": "LINKEDIN",
        "ok": True,
        "verified": True,
        "mode": "fill",
        "value": "https://www.linkedin.com/in/test-dummy-000000000",
        "readback": "https://www.linkedin.com/in/test-dummy-000000000",
        "verified_value": "https://www.linkedin.com/in/test-dummy-000000000",
    }
    assert is_verified_fill_row(ok_row) is True

    report = {
        "filled": [
            {
                "type": "LINKEDIN",
                "ok": True,
                "verified": True,
                "value": "https://www.linkedin.com/in/test-dummy-000000000",
                "readback": "",
                "verified_value": None,
            }
        ],
        "leftovers": [],
        "extracted_count": 1,
        "entry_prepass": {"final_clicks": 0},
    }
    out = _finalize(report)
    assert out["filled_count"] == 0
    assert any(l.get("type") == "LINKEDIN" for l in out["leftovers"])


def test_uuid_linkedin_replay_scrub():
    from record_replay import _is_uuid_only_selector, _scrub_row

    bad = {
        "selector": 'input[name="29324a55-7524-4e99-942d-ea043ed0297a"]:visible',
        "type": "LINKEDIN",
    }
    assert _is_uuid_only_selector(bad["selector"]) is True
    clean = _scrub_row(bad)
    assert clean is not None
    assert "linkedin" in clean["selector"].lower() or "LinkedIn" in clean["selector"]
    assert _is_uuid_only_selector(clean["selector"]) is False


def test_finalize_demotes_success_when_demoted_false_verified():
    report = {
        "filled": [
            {"type": "EMAIL", "ok": True, "verified": True, "readback": "a@b.c"},
        ],
        "leftovers": [],
        "extracted_count": 1,
        "verdict": "SUCCESS",
        "demoted_false_verified": [
            {"type": "LINKEDIN", "reason": "live_empty_after_claimed_verified"}
        ],
        "entry_prepass": {"final_clicks": 0},
    }
    out = _finalize(report)
    assert out["verdict"] == "FAIL"
    assert out.get("verdict_reason") == "demoted_false_verified"


def test_finalize_demotes_success_when_required_empty_after_fill():
    report = {
        "filled": [],
        "leftovers": [],
        "extracted_count": 0,
        "verdict": "SUCCESS",
        "required_empty_after_fill": [{"id": "linkedin", "reason": "empty"}],
        "entry_prepass": {"final_clicks": 0},
    }
    out = _finalize(report)
    assert out["verdict"] == "FAIL"
    assert out.get("verdict_reason") == "required_empty_after_fill"


def test_gh_select_skip_already_correct_match():
    """Tax Relief thrash: already-correct react-select display must soft-match."""
    from gh_select import _shown_matches_cands

    assert _shown_matches_cands("$80,000 - $100,000", ["Open / negotiable", "$80,000"])
    assert _shown_matches_cands("University of Alabama", ["University of Alabama", "Alabama"])
    assert not _shown_matches_cands("Select...", ["Yes", "No"])
    assert not _shown_matches_cands("", ["Yes"])


def test_verified_select_rejects_type_without_select():
    """Extend GH: typed filter essay must never count as committed select."""
    from verified_select import (
        is_placeholder_select_value,
        is_uncommitted_filter_text,
        normalize_select_answer,
        select_readback_ok,
        self_test,
    )

    self_test()
    essay = "Yes, I am currently based in Illinois (Springfield, IL)."
    assert is_placeholder_select_value("Select...")
    assert is_uncommitted_filter_text(essay, essay)
    assert not select_readback_ok(essay, ["Yes", "No"], typed_frag=essay)
    assert select_readback_ok("Yes", ["Yes", "No"])
    assert (
        normalize_select_answer(
            "Are you currently based in any of these states?\nIllinois",
            essay,
            field_type="LOCATION",
        )
        == "Yes"
    )
    assert (
        normalize_select_answer(
            "Will you require immigration sponsorship by Extend?",
            "No I will not need sponsorship",
            field_type="SPONSORSHIP",
        )
        == "No"
    )


def test_flash_based_in_returns_yes_not_essay():
    from flash_leftovers import answer_leftover_field

    out = answer_leftover_field(
        "Are you currently based in any of these states?\nCalifornia\nIllinois",
        ftype="LOCATION",
        use_llm=False,
    )
    assert out == "Yes" or out.lower().startswith("yes")
    # Even with empty type, label heuristic should return Yes
    out2 = answer_leftover_field(
        "Are you currently based in any of these states? California Colorado Florida Illinois",
        ftype=None,
        use_llm=False,
    )
    assert out2 == "Yes"


def test_field_attempt_cap_unfillable_after_2(tmp_path):
    from field_attempt_log import FieldAttemptLog

    log = FieldAttemptLog(tmp_path, run_id="testskip", url="https://example.com", platform="greenhouse")
    for _ in range(2):
        log.record(
            field_type="NAME_FIRST",
            label="Preferred First Name",
            success=False,
            error="live_empty_after_claimed_verified",
        )
    assert log.is_unfillable(field_type="NAME_FIRST", label="Preferred First Name")
    assert log.fail_count_for(field_type="NAME_FIRST", label="Preferred First Name") >= 2
    assert not log.is_unfillable(field_type="EMAIL", label="Email")


def test_should_demote_committed_match_not_live_empty():
    from fast_fill import should_demote_claimed_text_fill

    assert not should_demote_claimed_text_fill(
        sel_found=True, live_rb="Yes", intended="Yes", claimed_rb="Yes"
    )
    # Live probe missed value but prior verify readback still matches — keep row.
    assert not should_demote_claimed_text_fill(
        sel_found=True, live_rb="", intended="Yes", claimed_rb="Yes"
    )
    # Required-empty with empty claimed readback — SPA wipe, demote.
    assert should_demote_claimed_text_fill(
        sel_found=True,
        live_rb="",
        intended="Yes",
        claimed_rb="",
        id_still_empty=True,
    )
    # Stale required-empty must not demote when verified readback still matches.
    assert not should_demote_claimed_text_fill(
        sel_found=True,
        live_rb="",
        intended="Yes",
        claimed_rb="Yes",
        id_still_empty=True,
    )


def test_gh_select_race_decline_not_demoted_on_empty_live():
    """GH Dragos: RACE Decline verified then remount blank — keep, don't false-demote."""
    from fast_fill import _is_gh_select_fill_row, should_demote_claimed_text_fill

    intended = "Decline to self identify"
    claimed = "Decline To Self Identify"
    assert _is_gh_select_fill_row(
        {"via": "gh_select_sweep", "mode": "gh_select", "type": "RACE"}
    )
    assert _is_gh_select_fill_row({"via": "deterministic_reclaim_gh_select"})
    assert not should_demote_claimed_text_fill(
        sel_found=True,
        live_rb="",
        intended=intended,
        claimed_rb=claimed,
        field_type="RACE",
    )


def test_gh_country_dial_plus_one_not_mismatch_demote():
    """GH Country* live '+1' must not false-demote United States commit."""
    from fast_fill import should_demote_claimed_text_fill
    from verified_select import value_matches_readback

    assert value_matches_readback("United States", "+1") is True
    assert not should_demote_claimed_text_fill(
        sel_found=True,
        live_rb="+1",
        intended="United States",
        claimed_rb="+1",
        field_type="ADDRESS_COUNTRY",
    )


def test_finalize_clears_stale_advance_when_req_after_empty():
    """Stale try_advance FAIL must not survive when required_empty_after_fill=[]."""
    report = {
        "filled": [
            {
                "type": "RACE",
                "ok": True,
                "verified": True,
                "value": "Decline to self identify",
                "readback": "Decline To Self Identify",
                "via": "gh_select_sweep",
                "mode": "gh_select",
                "label": "Please identify your race",
            }
        ],
        "leftovers": [],
        "extracted_count": 1,
        "verdict": "FAIL",
        "advance_blocked_reason": "required_fields_empty",
        "required_empty_before_advance": [
            {"id": "question_18017622008", "reason": "empty_required_input"}
        ],
        "required_empty_after_fill": [],
        "demoted_false_verified": [],
        "entry_prepass": {"final_clicks": 0},
        "never_submit": True,
        "submit_clicked": False,
    }
    out = _finalize(report)
    assert out.get("advance_blocked_reason") in (None, "")
    assert out.get("verdict") == "SUCCESS"
    assert out.get("stale_advance_gate_cleared") is True


def test_value_matches_skips_placeholder_but_keeps_correct():
    from fast_fill import _value_matches_readback

    assert _value_matches_readback("Test", "Test") is True
    assert _value_matches_readback("Test Candidate", "Test") is True
    assert _value_matches_readback("Test", "Type here...") is False
    assert _value_matches_readback("Test", "") is False


def test_il_not_idaho_substring():
    """State abbrev must not false-match via substring (IL vs Idaho, ID vs Idaho)."""
    from fast_fill import _value_matches_readback
    from verified_select import expand_state_value, soft_value_match, value_matches_readback

    assert _value_matches_readback("IL", "Illinois") is True
    assert value_matches_readback("IL", "Illinois") is True
    assert _value_matches_readback("IL", "Idaho") is False
    assert _value_matches_readback("ID", "Idaho") is True
    assert soft_value_match("IL", "Idaho") is False
    # expand lives in verified_select — no Workday import required
    assert "Illinois" in expand_state_value("IL")
    assert soft_value_match("IL", "Illinois") is False  # short token needs expand
    assert value_matches_readback("Illinois", "IL") is True


def test_county_classifies_before_state():
    from field_map import ADDRESS_COUNTY, ADDRESS_STATE, classify_field

    ftype, layer = classify_field(
        {
            "label": "County*",
            "name": "regionSubdivision1",
            "id": "address--regionSubdivision1",
            "placeholder": "",
            "autocomplete": "",
            "input_type": "text",
        }
    )
    assert ftype == ADDRESS_COUNTY, f"got {ftype} via {layer}"
    assert ftype != ADDRESS_STATE


def test_is_verified_fill_row_rejects_multiselect_uncommitted():
    from fast_fill import is_verified_fill_row

    row = {
        "verified": True,
        "status": "filled",
        "type": "HOW_HEARD",
        "value": "Internet",
        "readback": "0 items selected",
    }
    assert is_verified_fill_row(row) is False


def test_how_heard_filter_text_not_verified():
    """Typed filter token without chip must not count as verified HOW_HEARD."""
    from fast_fill import is_verified_fill_row

    row = {
        "verified": True,
        "ok": True,
        "status": "filled",
        "type": "HOW_HEARD",
        "value": "Internet job board",
        "readback": "Internet",
        "mode": "combobox",
        "option_clicked": True,
    }
    assert is_verified_fill_row(row) is False


def test_select_readback_rejects_idaho_for_illinois():
    """DOM Idaho must not verify when aliases are Illinois/IL (ONEOK bug)."""
    from gh_select import _score_option
    from verified_select import (
        clear_closest_match,
        reject_confusable_state_option,
        select_readback_ok,
        soft_value_match,
        states_are_confusable,
    )

    cands = ["Illinois", "IL"]
    assert select_readback_ok("Idaho", cands, picked="Illinois", score_fn=_score_option) is False
    assert select_readback_ok("Idaho", cands, picked="Idaho", score_fn=_score_option) is False
    assert select_readback_ok("Illinois", cands, picked="Illinois", score_fn=_score_option) is True
    assert _score_option("Idaho", "IL") == 0
    assert _score_option("Illinois", "IL") >= 70
    assert soft_value_match("IL", "Idaho") is False
    assert soft_value_match("Illinois", "Idaho") is False
    assert states_are_confusable("Illinois", "Idaho")
    assert reject_confusable_state_option("IL", "Idaho")
    ranked = [(55, 0, "Illinois")]
    clear = clear_closest_match(ranked, at_last_word=True, intent="Illinois")
    assert clear is not None and clear[1] == "Illinois"
    ranked_bad = [(90, 0, "Idaho")]
    assert clear_closest_match(ranked_bad, at_last_word=True, intent="Illinois") is None


def test_resume_accept_empty_filelist_with_ui_hint():
    from resume_upload import accept_resume_after_empty_filelist

    assert accept_resume_after_empty_filelist(
        {"present": True, "empty": True, "workday_uploaded_ui": True}
    )
    assert accept_resume_after_empty_filelist(
        {"present": True, "empty": True, "uploaded_ui": False}, page_hint=True
    )
    assert not accept_resume_after_empty_filelist(
        {"present": True, "empty": True, "uploaded_ui": False}, page_hint=False
    )
    assert not accept_resume_after_empty_filelist(
        {"present": True, "empty": False, "uploaded_ui": True}
    )


def test_finalize_mid_run_skips_run_end():
    """Mid-run _finalize must not emit run_end / freeze step log."""
    import tempfile
    from pathlib import Path

    from fast_fill import _finalize
    from fill_step_log import FillStepLog, attach_fill_step_log

    report = {
        "url": "https://example.com",
        "platform": "workday",
        "dummy": True,
        "test_mode": True,
        "filled": [],
        "leftovers": [],
        "never_submit": True,
    }
    with tempfile.TemporaryDirectory() as td:
        log = FillStepLog(td, run_id="t_mid", url=report["url"], platform="workday")
        report["_fill_step_log"] = log
        log.step("run_start", reason="test")
        _finalize(report)  # mid-run default
        assert report.get("_step_logged_run_end") is not True
        lines = Path(log.jsonl_path).read_text().splitlines()
        assert not any('"run_end"' in ln for ln in lines)
        _finalize(report, close_step_log=True)
        assert report.get("_step_logged_run_end") is True
        lines2 = Path(log.jsonl_path).read_text().splitlines()
        assert any('"run_end"' in ln for ln in lines2)


def test_ashby_value_already_correct():
    from ashby_widgets import _value_already_correct

    assert _value_already_correct("62701", "62701") is True
    assert _value_already_correct("https://linkedin.com/in/x", "https://linkedin.com/in/x") is True
    assert _value_already_correct("62701", "Type here...") is False
    assert _value_already_correct("62701", "") is False
    # ATS2-004: longer wrong display containing a short token must not skip
    assert _value_already_correct("IL", "Idaho") is False
    assert _value_already_correct("IL", "Illinois") is True
    assert _value_already_correct("Yes", "Yesterday I moved") is False


def test_ats2_002_us_country_phone_readback():
    from exp_workday_selectors import _is_us_country_phone_readback

    assert _is_us_country_phone_readback("United States (+1)") is True
    assert _is_us_country_phone_readback("United States of America (+1)") is True
    assert _is_us_country_phone_readback("+1") is False
    assert _is_us_country_phone_readback("Jamaica (+1)") is False
    assert _is_us_country_phone_readback("Anguilla (+1)") is False
    assert _is_us_country_phone_readback("Canada (+1)") is False
    assert _is_us_country_phone_readback("Barbados (+1)") is False


def test_ats2_008_generic_pack_state_combobox_phone_tight():
    from fast_fill import GENERIC_SELECTOR_PACK
    from field_map import ADDRESS_STATE, PHONE

    state_rows = [r for r in GENERIC_SELECTOR_PACK if r[1] == ADDRESS_STATE]
    assert state_rows and state_rows[0][2] == "combobox"
    phone_rows = [r for r in GENERIC_SELECTOR_PACK if r[1] == PHONE]
    assert phone_rows
    sel = phone_rows[0][0].lower()
    assert "device" in sel and "ext" in sel  # exclusions present
    assert "name*='phone'" in sel or 'name*="phone"' in sel or "name*='phone" in sel


def test_ats2_009_wd_pack_includes_phone_device():
    from exp_workday_selectors import WD_SELECTOR_PACK

    assert any("phone-device-type" in (r[0] or "") for r in WD_SELECTOR_PACK)
    assert any(r[1] == "PHONE_DEVICE" for r in WD_SELECTOR_PACK)


def test_ats2_005_option_mapping_rejects_confusable(tmp_path=None):
    from pathlib import Path
    from option_mappings import lookup_aliases, upsert_mapping

    path = Path(tmp_path) if tmp_path else Path("/tmp/ff_option_map_poison")
    path = path if path.suffix == ".json" else path / "option_mappings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    bad = upsert_mapping(
        platform="workday",
        host="evil.wd5.myworkdayjobs.com",
        field_type="ADDRESS_STATE",
        label="State",
        canonical="Illinois",
        chosen_option="Idaho",
        path=path,
    )
    assert bad == {}
    upsert_mapping(
        platform="workday",
        host="evil.wd5.myworkdayjobs.com",
        field_type="ADDRESS_STATE",
        label="State",
        canonical="Illinois",
        chosen_option="Illinois",
        path=path,
    )
    aliases = lookup_aliases(
        platform="workday",
        host="evil.wd5.myworkdayjobs.com",
        field_type="ADDRESS_STATE",
        canonical="Illinois",
        path=path,
    )
    assert "Illinois" in aliases
    assert "Idaho" not in aliases


def test_ats2_007_learning_skips_how_heard_gender():
    from learning import is_reusable_learning

    assert not is_reusable_learning("How did you hear about us?", "Internet job board")
    assert not is_reusable_learning("Select your gender", "Male")
    assert is_reusable_learning("Will you require sponsorship?", "No")


def test_eeo_answer_uses_profile_or_decline_fallback_without_llm(monkeypatch=None):
    """Without API, EEO leftovers use DUMMY_PROFILE (Male / etc.), not hard-skip."""
    from flash_leftovers import answer_leftover_field

    out = answer_leftover_field(
        "Gender",
        ftype="GENDER",
        resume_excerpt="DUMMY RESUME EXCERPT:\n  Test Candidate",
        profile_facts="DUMMY_PROFILE FACTS:\n  name: Test",
        use_llm=False,
    )
    assert out
    assert "male" in out.lower() or "decline" in out.lower() or "prefer not" in out.lower()


def test_eeo_prompt_catalog_only_no_invent():
    from flash_leftovers import LEFTOVERS_RULES, build_dummy_profile_facts, validate_eeo_against_catalog

    assert "never invent" in LEFTOVERS_RULES.lower()
    assert "MAY invent" not in LEFTOVERS_RULES
    facts = build_dummy_profile_facts()
    assert "Male" in facts or "male" in facts.lower()
    assert "Decline" in facts  # race fallback still documented
    assert validate_eeo_against_catalog("GENDER", "Non-binary invent") == "Male"


def test_dummy_eeo_policy_prefers_concrete_answers():
    from field_map import DUMMY_PROFILE, build_value_map, classify_field

    eeo = DUMMY_PROFILE["eeo_demographic"]
    assert eeo["gender"].lower() == "male"
    assert "disability" in eeo["disability_status"].lower()
    assert "not" in eeo["veteran_status"].lower() or eeo["veteran_status"].lower().startswith("no")
    assert eeo["hispanic_or_latino"].lower() in ("no", "not hispanic or latino")
    assert "decline" in eeo["race_ethnicity"].lower()
    vals = build_value_map(DUMMY_PROFILE)
    assert vals["GENDER"].lower() == "male"
    assert "disability" in vals["DISABILITY"].lower()
    assert vals["HISPANIC"].lower() in ("no", "not hispanic or latino")
    assert vals["DEGREE"] and "master" in vals["DEGREE"].lower()
    assert vals.get("DISCIPLINE", "").lower() == "computer science"
    # Clearance / citizenship classify
    assert (
        classify_field(
            {
                "label": "Do you have a TS/SCI with Polygraph Security Clearance?*",
                "name": "",
                "id": "",
                "placeholder": "",
            }
        )[0]
        == "CLEARANCE"
    )
    assert (
        classify_field(
            {
                "label": "Security Clearance Type*",
                "name": "",
                "id": "",
                "placeholder": "",
            }
        )[0]
        == "CLEARANCE_TYPE"
    )
    assert vals["CLEARANCE"].lower() == "no"
    assert vals["SALARY_EXPECTED"]
    assert classify_field(
        {"label": "Discipline", "name": "", "id": "", "placeholder": ""}
    )[0] == "DISCIPLINE"
    assert classify_field(
        {
            "label": "Employment Eligibility Information*",
            "name": "",
            "id": "",
            "placeholder": "",
        }
    )[0] == "WORK_AUTH"


def test_vision_zip_placeholder_false_success_attribution(tmp_path):
    """Zip blank on screenshot: heuristic vision + attribution flag false_success."""
    import tempfile

    from fill_attribution import analyze_fill_attribution
    from vision_judge import judge_from_report

    report = {
        "url": "https://jobs.ashbyhq.com/example/1",
        "platform": "ashby",
        "never_submit": True,
        "flash_called": False,
        "leftovers": [],
        "verdict": "SUCCESS",
        "filled": [
            {
                "type": "ADDRESS_ZIP",
                "label": "Zip",
                "via": "ashby_widgets",
                "ok": True,
                "verified": True,
                "value": "62701",
                "readback": "Type here...",
            }
        ],
    }
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tf.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        png_path = Path(tf.name)
    try:
        vision = judge_from_report(report, screenshot=png_path)
        assert vision["complete"] is False
        assert vision["verdict"] == "FAIL_BLANK"
        assert any(e.get("type") == "ADDRESS_ZIP" for e in vision["empty_fields"])
        attr = analyze_fill_attribution(report, vision=vision)
        issues = {f.get("issue") for f in attr["false_success"]}
        assert "screenshot_empty_but_report_claims_filled" in issues or (
            "screenshot_empty_but_report_claims_type_filled" in issues
        )
    finally:
        png_path.unlink(missing_ok=True)


def test_vision_heuristic_never_complete_with_png(tmp_path):
    """Magnit-style: leftovers=0 + PNG must not yield heuristic COMPLETE."""
    import tempfile

    from vision_judge import judge_from_report, judge_screenshot

    empty_report = {"leftovers": [], "never_submit": True, "verdict": "SUCCESS"}
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tf.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        png_path = Path(tf.name)
    try:
        r = judge_from_report(empty_report, screenshot=png_path)
        assert r["complete"] is False
        assert r["source"] == "heuristic_report"
        assert r["verdict"] in ("AMBIGUOUS", "FAIL_BLANK")
        r2 = judge_screenshot(png_path, report=empty_report)
        assert r2["complete"] is False
    finally:
        png_path.unlink(missing_ok=True)


def test_finalize_reconciles_verified_fill_leftover_by_selector():
    """A verified essay/URL fill with an empty label but a concrete selector must
    clear its lingering extract-time leftover (Ashby "favorite AI paper" essay
    filled via replay had label="" → false FAIL before selector reconciliation)."""
    from fast_fill import _finalize

    report = {
        "url": "https://jobs.ashbyhq.com/x/y",
        "platform": "ashby",
        "verdict": "SUCCESS",
        "filled": [
            {
                "type": "COVER_LETTER",
                "label": "",
                "selector": 'textarea[name="essay-uuid"]:visible',
                "value": "A grounded two-sentence answer.",
                "readback": "A grounded two-sentence answer.",
                "ok": True,
                "verified": True,
            }
        ],
        "leftovers": [
            {
                "type": None,
                "label": "In two sentences, describe your favorite AI paper",
                "selector": 'textarea[name="essay-uuid"]:visible',
                "reason": "unclassified",
                "essay": True,
                "flash_candidate": True,
            }
        ],
    }
    out = _finalize(dict(report))
    labels = [l.get("label") for l in (out.get("leftovers") or [])]
    assert "In two sentences, describe your favorite AI paper" not in labels
    assert out["never_submit"] is True and out["submit_clicked"] is False


def test_finalize_reconciles_workday_automation_id_leftover():
    """WD pack misses by automation_id must clear when extract+classify verified the same field."""
    from fast_fill import _finalize

    report = {
        "url": "https://jci.wd5.myworkdayjobs.com/x",
        "platform": "workday",
        "verdict": "FAIL",
        "filled": [
            {
                "type": "NAME_FIRST",
                "label": "First Name*",
                "selector": 'input[name="legalName--firstName"]:visible',
                "value": "Test",
                "readback": "Test",
                "ok": True,
                "verified": True,
            },
            {
                "type": "ADDRESS_COUNTRY",
                "automation_id": "addressSection_country",
                "selector": '[data-automation-id="formField-country"] button',
                "value": "United States of America",
                "readback": "United States of America",
                "ok": True,
                "verified": True,
            },
        ],
        "leftovers": [
            {
                "label": "legalNameSection_firstName",
                "automation_id": "legalNameSection_firstName",
                "reason": "not_in_dom",
            },
            {
                "label": "addressSection_country",
                "automation_id": "addressSection_country",
                "reason": "fill_error",
            },
            {
                "label": "how_heard",
                "automation_id": "how_heard",
                "reason": "hierarchical_no_chip",
            },
        ],
    }
    out = _finalize(dict(report))
    labels = [l.get("label") for l in (out.get("leftovers") or [])]
    assert "legalNameSection_firstName" not in labels
    assert "addressSection_country" not in labels
    assert "how_heard" in labels


def test_finalize_keeps_unverified_leftover():
    """Reconciliation only clears VERIFIED fills — an unverified same-selector
    attempt must NOT silently clear a genuine leftover."""
    from fast_fill import _finalize

    report = {
        "url": "https://jobs.ashbyhq.com/x/y",
        "platform": "ashby",
        "verdict": "FAIL",
        "filled": [],
        "leftovers": [
            {
                "type": None,
                "label": "Describe a project",
                "selector": 'textarea[name="q1"]:visible',
                "reason": "unclassified",
                "essay": True,
            }
        ],
    }
    out = _finalize(dict(report))
    labels = [l.get("label") for l in (out.get("leftovers") or [])]
    assert "Describe a project" in labels


def test_optional_demographic_empty_does_not_block_complete():
    """Voluntary EEO self-ID (race) left blank stays visible but is non-blocking.

    GH cascading-ethnicity tenants leave the "race" sub-select empty on purpose to
    preserve the required Hispanic/Latino=No answer; that must not FAIL the gate.
    """
    from vision_judge import finalize_verdict

    result = {
        "source": "dom",
        "confidence": "high",
        "empty_fields": [
            {
                "label": "Please identify your race",
                "kind": "blank",
                "required": False,
                "optional_demographic": True,
            }
        ],
    }
    out = finalize_verdict(dict(result))
    assert out["complete"] is True
    assert out["verdict"] == "COMPLETE"
    # still surfaced for the reviewer
    assert any(
        e.get("label") == "Please identify your race" for e in out["empty_fields"]
    )


def test_optional_location_derived_empty_does_not_block_complete():
    """Workable Places-autocomplete derived city/postcode/country (non-required).

    They only populate on a place-suggestion selection (impossible with a dummy
    address), so blank derived components must not FAIL a submittable form.
    """
    from vision_judge import finalize_verdict

    result = {
        "source": "dom",
        "confidence": "high",
        "empty_fields": [
            {"label": "city", "kind": "blank", "required": False, "optional_location": True},
            {"label": "postcode", "kind": "blank", "required": False, "optional_location": True},
            {"label": "country", "kind": "blank", "required": False, "optional_location": True},
        ],
    }
    out = finalize_verdict(dict(result))
    assert out["complete"] is True
    assert out["verdict"] == "COMPLETE"


def test_required_empty_still_blocks_complete():
    """The carve-outs are scoped: a genuinely required empty still FAILs."""
    from vision_judge import finalize_verdict

    result = {
        "source": "dom",
        "confidence": "high",
        "empty_fields": [
            {"label": "First name", "kind": "blank", "required": True},
        ],
    }
    out = finalize_verdict(dict(result))
    assert out["complete"] is False
    assert out["verdict"] == "FAIL_BLANK"


def test_is_select_field_vs_essay():
    from verified_select import is_select_field

    assert is_select_field("SPONSORSHIP", "Will you require sponsorship?")
    assert is_select_field(
        "LOCATION",
        "Are you currently based in any of these states?",
    )
    assert not is_select_field(
        "COVER_LETTER",
        "Why do you want to join us?",
        {"essay": True},
    )


def test_flash_select_leftover_tagging():
    from flash_leftovers import build_leftovers_handoff

    report = {
        "url": "https://example.com",
        "platform": "greenhouse",
        "filled": [],
        "leftovers": [
            {
                "label": "Gender",
                "type": "GENDER",
                "selector": "#gender",
                "reason": "gh_select_failed",
                "flash_candidate": True,
            },
            {
                "label": "Tell us about yourself",
                "type": "COVER_LETTER",
                "selector": "textarea",
                "reason": "no_value",
                "flash_candidate": True,
            },
        ],
    }
    hand = build_leftovers_handoff(report, grounded=True)
    by_label = {r.get("label"): r for r in hand.get("leftovers") or []}
    assert by_label["Gender"].get("select") is True
    assert by_label["Tell us about yourself"].get("essay") is True
    assert "CLICK_OPTION" in hand["prompt"]


def test_finalize_honest_leftover_count():
    from flash_leftovers import flash_candidate_count

    report = {
        "filled": [],
        "leftovers": [
            {
                "label": "Essay",
                "type": "COVER_LETTER",
                "flash_candidate": True,
            }
        ],
        "extracted_count": 1,
        "entry_prepass": {"final_clicks": 0},
    }
    assert flash_candidate_count(report) == 1
    out = _finalize(report)
    assert out.get("flash_leftover_count") == 1
    assert out.get("leftovers_zero_lie") is False


def test_finalize_detects_leftovers_zero_lie():
    report = {
        "filled": [{"type": "EMAIL", "ok": True, "verified": True, "readback": "a@b.c"}],
        "leftovers": [],
        "required_empty_after_fill": [{"id": "phone", "reason": "empty_required_input"}],
        "extracted_count": 2,
        "verdict": "SUCCESS",
        "entry_prepass": {"final_clicks": 0},
    }
    out = _finalize(report)
    assert out.get("leftovers_zero_lie") is True
    assert out["verdict"] == "FAIL"


def test_promote_demoted_flash_leftovers():
    from fast_fill import (
        _demoted_flash_leftovers,
        _flash_filled_count,
        _promote_demoted_flash_leftovers,
    )

    report = {
        "leftovers": [
            {
                "label": "LinkedIn",
                "type": "LINKEDIN",
                "reason": "live_empty_after_claimed_verified",
                "flash_candidate": True,
            },
            {
                "label": "Zip",
                "type": "ADDRESS_ZIP",
                "reason": "unfillable_after_2",
                "flash_candidate": False,
            },
            {
                "label": "Essay",
                "type": "COVER_LETTER",
                "reason": "no_value",
                "flash_candidate": True,
            },
        ]
    }
    assert len(_demoted_flash_leftovers(report)) == 2
    n = _promote_demoted_flash_leftovers(report)
    assert n == 2
    assert report["leftovers"][0]["flash_candidate"] is True
    assert report["leftovers"][1]["flash_candidate"] is True
    assert report["leftovers"][1]["reason"] == "live_empty_after_claimed_verified"
    assert report["leftovers"][1]["flash_force_reason"] == "pass2_after_zero_fill"

    assert _flash_filled_count({"filled_count": 3}) == 3
    assert _flash_filled_count({"llm_fills": 1, "deterministic_reclaims": 2}) == 3
    assert _flash_filled_count({}) == 0


def test_promote_skips_already_correct_and_recent_flash_fp():
    """FILL3-020: do not re-Flash soft-match keep / same leftover fingerprint."""
    from fast_fill import _promote_demoted_flash_leftovers

    report = {
        "filled": [
            {
                "type": "LINKEDIN",
                "label": "LinkedIn",
                "selector": "",
                "reason": "already_correct_keep",
                "ok": True,
                "verified": True,
            }
        ],
        "flash": {
            "inpage_attempted": [
                {
                    "type": "ADDRESS_ZIP",
                    "label": "Zip",
                    "selector": "",
                    "ok": False,
                    "reason": "no matching option",
                }
            ]
        },
        "leftovers": [
            {
                "label": "LinkedIn",
                "type": "LINKEDIN",
                "selector": "",
                "reason": "live_empty_after_claimed_verified",
                "flash_candidate": True,
            },
            {
                "label": "Zip",
                "type": "ADDRESS_ZIP",
                "selector": "",
                "reason": "unfillable_after_2",
                "flash_candidate": False,
            },
            {
                "label": "School",
                "type": "SCHOOL",
                "selector": "",
                "reason": "live_empty_after_claimed_verified",
                "flash_candidate": True,
            },
        ],
    }
    n = _promote_demoted_flash_leftovers(report)
    assert n == 1  # only School promoted
    assert report["leftovers"][0]["flash_candidate"] is False
    assert report["leftovers"][0]["flash_skip_reason"] == "already_correct_soft_match"
    assert report["leftovers"][1]["flash_candidate"] is False
    assert report["leftovers"][1]["flash_skip_reason"] == "recent_flash_same_fingerprint"
    assert report["leftovers"][2]["flash_candidate"] is True
    assert report["flash_promote_skipped"] == 2


def test_leftover_set_fingerprint_stable():
    """FILL3-006: leftover fingerprint stable when set unchanged."""
    from fast_fill import _leftover_set_fingerprint

    a = {
        "leftovers": [
            {"type": "SCHOOL", "label": "School", "reason": "blank", "flash_candidate": True},
            {"type": "DEGREE", "label": "Degree", "reason": "blank", "flash_candidate": True},
        ]
    }
    b = {
        "leftovers": [
            {"type": "DEGREE", "label": "Degree", "reason": "blank", "flash_candidate": True},
            {"type": "SCHOOL", "label": "School", "reason": "blank", "flash_candidate": True},
        ]
    }
    c = {
        "leftovers": [
            {"type": "SCHOOL", "label": "School", "reason": "blank", "flash_candidate": True},
        ]
    }
    assert _leftover_set_fingerprint(a) == _leftover_set_fingerprint(b)
    assert _leftover_set_fingerprint(a) != _leftover_set_fingerprint(c)


def test_gh_country_rejects_phone_dial_code():
    """Dragos Country*: 'United States +1' is valid; bare +1 needs picked rescue."""
    from gh_select import (
        _label_needle,
        _score_option,
        _shown_matches_cands,
        aliases_for,
        country_name_from_dial_option,
        looks_like_dial_code_option,
    )
    from verified_select import select_readback_ok

    cands = aliases_for("ADDRESS_COUNTRY", "United States")
    assert looks_like_dial_code_option("United States +1")
    assert country_name_from_dial_option("United States +1") == "United States"
    assert _score_option("United States +1", "United States") >= 80
    assert _shown_matches_cands(
        "United States +1", cands, field_type="ADDRESS_COUNTRY"
    )
    # Bare +1 alone (no picked) must not verify against name-only cands
    assert not select_readback_ok("+1", ["United States", "USA", "US"], score_fn=_score_option)
    # shown=+1 with picked=United States +1 is the live GH commit pattern
    assert select_readback_ok(
        "+1",
        cands,
        picked="United States +1",
        score_fn=_score_option,
    )
    assert select_readback_ok(
        "United States", cands, picked="United States", score_fn=_score_option
    )


def test_iti_flag_matches_country_not_any_flag():
    """ATS-011: non-US intent must not verify on arbitrary iti flag."""
    from gh_select import iti_flag_matches_country

    assert iti_flag_matches_country(
        "iti__flag iti__us", ["United States"], "United States +1"
    )
    assert not iti_flag_matches_country(
        "iti__flag iti__gb", ["United States"], "United States +1"
    )
    assert iti_flag_matches_country(
        "iti__flag iti__gb", ["United Kingdom"], "United Kingdom +44"
    )
    assert not iti_flag_matches_country(
        "iti__flag iti__fr", ["Canada"], "Canada +1"
    )
    # Unknown mapping → refuse soft-accept
    assert not iti_flag_matches_country("iti__flag iti__xx", ["Narnia"], "Narnia")


def test_gh_sponsorship_needle_matches_dragos_label():
    """Needle must be a substring of the live label (not 'immigration' when absent)."""
    from gh_select import _label_needle

    dragos = (
        "Will you now or in the future require sponsorship to work "
        "in the United States?*"
    )
    needle = _label_needle(dragos)
    assert needle == "require sponsorship"
    assert needle.lower() in dragos.lower().replace("*", "")
    assert _label_needle(
        "Will you now require immigration sponsorship for employment?"
    ) == "require immigration sponsorship"
    # Tax Relief: "without the need…" — needle must include "the" when present
    tra = (
        "Are you currently authorized to work in the United States without "
        "the need for visa sponsorship, now or in the future?"
    )
    n_tra = _label_needle(tra)
    assert n_tra.lower() in tra.lower()
    assert "without" in n_tra.lower() and "sponsorship" in n_tra.lower()
    # SMS consent: never return bare "marketing" when label only says SMS
    sms = "Do you consent to receiving SMS from TRA at the number provided?"
    assert _label_needle(sms) == "SMS"
    assert _label_needle(sms).lower() in sms.lower()


def test_without_need_sponsorship_is_work_auth_yes():
    """Tax Relief polarity: without-need question → WORK_AUTH=Yes, not SPONSORSHIP=No."""
    from field_map import WORK_AUTH, SPONSORSHIP, classify_field

    tra = (
        "Are you currently authorized to work in the United States without "
        "the need for visa sponsorship, now or in the future?"
    )
    ftype, _ = classify_field({"label": tra, "name": "", "id": ""})
    assert ftype == WORK_AUTH, f"got {ftype}"
    # Classic require-sponsorship still SPONSORSHIP
    dragos = (
        "Will you now or in the future require sponsorship to work "
        "in the United States?*"
    )
    ftype2, _ = classify_field({"label": dragos, "name": "", "id": ""})
    assert ftype2 == SPONSORSHIP


def test_phone_device_helpers_reject_dial_codes():
    """Phone device type must not treat Anguilla (+1) as a device."""
    from exp_workday_selectors import (
        _is_phone_device_readback,
        _looks_like_dial_code_option,
        _phone_device_matches_intent,
    )

    assert _looks_like_dial_code_option("Anguilla (+1)")
    assert _looks_like_dial_code_option("United States of America (+1)")
    # ATS-007: align with gh_select — bare +1 / United States +1 are dial rows
    assert _looks_like_dial_code_option("United States +1")
    assert _looks_like_dial_code_option("+1")
    assert not _looks_like_dial_code_option("Mobile")
    assert not _looks_like_dial_code_option("Landline")
    assert _is_phone_device_readback("Mobile")
    assert _is_phone_device_readback("Landline")
    assert not _is_phone_device_readback("Anguilla (+1)")
    assert not _is_phone_device_readback("")
    # ATS-006: Mobile intent must not verify as Home
    assert _phone_device_matches_intent("Mobile", "Mobile")
    assert _phone_device_matches_intent("Mobile", "Cell")
    assert not _phone_device_matches_intent("Mobile", "Home")
    assert not _phone_device_matches_intent("Mobile", "Landline")
    assert _phone_device_matches_intent("Home", "Home")
    assert not _phone_device_matches_intent("Home", "Mobile")


def test_filtered_option_index_maps_illinois_not_idaho():
    """ATS-001: filtered ranking indices must remap to unfiltered locator indices."""
    from verified_select import (
        clear_closest_match,
        filter_options_preserving_indices,
        rank_option_matches,
        remap_ranked_to_original,
        reject_confusable_state_option,
    )

    texts = ["Idaho", "Illinois", "Iowa"]
    assert reject_confusable_state_option("Illinois", "Idaho")
    filtered, orig = filter_options_preserving_indices(texts, "Illinois")
    assert "Idaho" not in filtered
    assert filtered[0] == "Illinois"
    assert orig[0] == 1  # original locator index of Illinois
    ranked = rank_option_matches(filtered, ["Illinois", "IL"])
    remapped = remap_ranked_to_original(ranked, orig)
    clear = clear_closest_match(remapped, at_last_word=True, intent="Illinois")
    assert clear is not None
    best_i, picked, _ = clear
    assert picked == "Illinois"
    assert best_i == 1  # click opts.nth(1), NOT nth(0)=Idaho
    # ATS-015: all-confusable → empty (no fallback to full list)
    only_id = ["Idaho"]
    f2, o2 = filter_options_preserving_indices(only_id, "Illinois")
    assert f2 == []
    assert o2 == []


def test_lever_already_checked_requires_correct_polarity():
    """ATS-010: pre-checked wrong Yes/No must not count as already_checked."""
    from field_map import SPONSORSHIP, WORK_AUTH
    from lever_widgets import pick_radio_option, radio_already_matches_desired

    sponsor_opts = [
        {
            "label": "Yes, I will require sponsorship",
            "value": "Yes",
            "checked": True,
        },
        {
            "label": "No, I will not require sponsorship",
            "value": "No",
            "checked": False,
        },
    ]
    assert not radio_already_matches_desired(SPONSORSHIP, "No", sponsor_opts)
    # Correct polarity already selected
    good = [
        {**sponsor_opts[0], "checked": False},
        {**sponsor_opts[1], "checked": True},
    ]
    assert radio_already_matches_desired(SPONSORSHIP, "No", good)
    pick = pick_radio_option(SPONSORSHIP, "No", sponsor_opts)
    assert pick is not None
    assert "no" in str(pick.get("label") or "").lower()

    auth_opts = [
        {"label": "No", "value": "No", "checked": True},
        {"label": "Yes", "value": "Yes", "checked": False},
    ]
    assert not radio_already_matches_desired(WORK_AUTH, "Yes", auth_opts)
    auth_good = [
        {"label": "No", "value": "No", "checked": False},
        {"label": "Yes", "value": "Yes", "checked": True},
    ]
    assert radio_already_matches_desired(WORK_AUTH, "Yes", auth_good)


def test_enrich_gh_id_leftover_maps_catalog():
    from fast_fill import enrich_gh_id_leftover, enrich_report_gh_id_leftovers, _gh_city_aliases
    from field_map import ADDRESS_COUNTRY, LOCATION

    loc = enrich_gh_id_leftover(
        {
            "label": "candidate-location",
            "type": None,
            "reason": "live_required_empty:empty_required_input",
            "flash_candidate": True,
        }
    )
    assert loc["type"] == LOCATION
    assert loc.get("ownership") == "prefill_reclaim"
    assert loc.get("flash_candidate") is False

    ctry = enrich_gh_id_leftover(
        {
            "label": "country",
            "type": None,
            "reason": "live_required_empty:empty_required_input",
        }
    )
    assert ctry["type"] == ADDRESS_COUNTRY

    report = {
        "leftovers": [
            {"label": "candidate-location", "type": None, "flash_candidate": True},
            {"label": "essay why", "type": None, "flash_candidate": True},
        ]
    }
    n = enrich_report_gh_id_leftovers(report)
    assert n == 1
    assert report["leftovers"][0]["type"] == LOCATION

    primary, aliases = _gh_city_aliases({}, "Springfield")
    assert "Illinois" in primary or any("Illinois" in a for a in aliases)
    assert any("Springfield" in a for a in aliases)


def test_classify_candidate_location_id():
    from field_map import ADDRESS_CITY, LOCATION, classify_field

    ftype, _ = classify_field(
        {
            "label": "",
            "name": "candidate-location",
            "id": "candidate-location",
            "type": "text",
            "placeholder": "",
            "aria_label": "",
            "autocomplete": "",
        }
    )
    assert ftype in (LOCATION, ADDRESS_CITY)


def test_classify_us_residence_not_address_line2():
    """Dragos GH: 'United' must not match ADDRESS_LINE2 bare 'unit' substring."""
    from field_map import ADDRESS_LINE2, US_RESIDENCE, classify_field

    label = "Do you currently live in the United States?*"
    ftype, layer = classify_field(
        {
            "label": label,
            "name": "",
            "id": "question_18017623008",
            "type": "text",
            "placeholder": "Select...",
            "aria_label": "",
            "autocomplete": "",
        }
    )
    assert ftype == US_RESIDENCE, f"got {ftype} via {layer} (unit⊂United bug)"
    assert ftype != ADDRESS_LINE2

    # Real apt/unit labels still classify as line 2
    apt, _ = classify_field(
        {"label": "Apartment / Unit", "name": "", "id": "", "placeholder": ""}
    )
    assert apt == ADDRESS_LINE2
    line2, _ = classify_field(
        {"label": "Address Line 2", "name": "address-line2", "id": "", "placeholder": ""}
    )
    assert line2 == ADDRESS_LINE2


def test_how_heard_chip_readback_is_verified():
    """Fiber searchSelect '1 item selected, Indeed' must count as verified."""
    from fill_verify import is_verified_fill_row
    from verified_select import is_uncommitted_filter_text, multiselect_has_chip

    rb = "How Did You Hear About Us?*\n1 item selected, Indeed\n\nIndeed"
    assert multiselect_has_chip(rb)
    assert not is_uncommitted_filter_text(rb, "Indeed", picked="Indeed", from_input=True)
    row = {
        "type": "HOW_HEARD",
        "automation_id": "how_heard",
        "value": "Indeed",
        "option_text": "Indeed",
        "picked": "Indeed",
        "option_clicked": True,
        "verified": True,
        "mode": "fiber_search_select",
        "readback": rb,
        "status": "filled",
    }
    assert is_verified_fill_row(row) is True


def test_how_heard_picked_label_without_chrome_verified():
    """Walmart: after click, readback may be just 'Indeed' (no N items chrome)."""
    from fill_verify import is_verified_fill_row

    row = {
        "type": "HOW_HEARD",
        "automation_id": "how_heard",
        "value": "Indeed",
        "picked": "Indeed",
        "option_text": "Indeed",
        "option_clicked": True,
        "verified": True,
        "committed": True,
        "readback": "Indeed",
        "status": "filled",
    }
    assert is_verified_fill_row(row) is True
    # Exact candidate token alone is keepable (stop thrash); filter fragments are not
    exact_keep = {
        "type": "HOW_HEARD",
        "value": "Indeed",
        "readback": "Indeed",
        "verified": True,
        "status": "filled",
    }
    assert is_verified_fill_row(exact_keep) is True
    frag = {
        "type": "HOW_HEARD",
        "value": "Internet job board",
        "readback": "Internet",
        "verified": True,
        "status": "filled",
        "option_clicked": True,
    }
    assert is_verified_fill_row(frag) is False


def test_guard_words_refuse_dangerous_maps():
    """ChamPro: mis-map worse than no map — guard-words refuse ambiguous types."""
    from field_map import (
        ADDRESS_COUNTRY,
        ADDRESS_LINE2,
        NAME_FIRST,
        PHONE,
        US_RESIDENCE,
        classify_field,
        guard_words_reject,
    )

    assert guard_words_reject(
        ADDRESS_LINE2, "Do you currently live in the United States?"
    )
    us, _ = classify_field(
        {"label": "Do you currently live in the United States?", "name": "", "id": ""}
    )
    assert us == US_RESIDENCE

    phone, layer = classify_field(
        {"label": "Phone Device Type", "name": "phone-device-type", "id": ""}
    )
    assert phone != PHONE, f"device type must not be PHONE ({phone} via {layer})"

    name, _ = classify_field(
        {"label": "Emergency Contact First Name", "name": "emergencyFirst", "id": ""}
    )
    assert name != NAME_FIRST

    country, _ = classify_field(
        {"label": "Country Phone Code", "name": "countryPhoneCode", "id": ""}
    )
    assert country != ADDRESS_COUNTRY
    try:
        from field_map import PHONE_COUNTRY_CODE as _PCC

        assert country == _PCC
    except ImportError:
        pass


def test_gaps_block_ready():
    from form_gaps import gaps_block_ready, merge_gaps_into_report, normalize_gaps
    from page_progress import can_claim_ready

    gaps = normalize_gaps(
        [{"label": "How Did You Hear About Us?", "reason": "required_empty"}]
    )
    assert gaps_block_ready(gaps)
    report = {"verdict": "SUCCESS"}
    merge_gaps_into_report(report, gaps)
    assert can_claim_ready(report) is False
    # Ready also requires live vision judge (fail-closed without it)
    report2 = {
        "verdict": "SUCCESS",
        "gaps_after_save": [],
        "vision_judge_live": {"complete": True, "verdict": "COMPLETE"},
    }
    assert can_claim_ready(report2) is True


def test_option_mappings_roundtrip(tmp_path=None):
    from pathlib import Path
    from option_mappings import lookup_aliases, upsert_mapping

    path = Path(tmp_path) if tmp_path else Path("/tmp/ff_option_map_test")
    path = path if path.suffix == ".json" else path / "option_mappings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    upsert_mapping(
        platform="workday",
        host="quantiphi.wd1.myworkdayjobs.com",
        field_type="HOW_HEARD",
        label="How Did You Hear About Us",
        canonical="Internet job board",
        chosen_option="Job Board",
        path=path,
    )
    aliases = lookup_aliases(
        platform="workday",
        host="quantiphi.wd1.myworkdayjobs.com",
        field_type="HOW_HEARD",
        label="How Did You Hear About Us",
        canonical="Internet job board",
        path=path,
    )
    assert "Job Board" in aliases
    # Refuse email-looking values
    upsert_mapping(
        platform="workday",
        host="x.com",
        field_type="EMAIL",
        label="Email",
        canonical="a@b.com",
        chosen_option="a@b.com",
        path=path,
    )
    assert lookup_aliases(
        platform="workday", host="x.com", field_type="EMAIL", path=path
    ) == []


def test_batch_fill_skips_how_heard():
    from batch_fill import is_batchable_row

    assert is_batchable_row(
        {"selector": "input[name=first]", "value": "Test", "type": "NAME_FIRST"}
    )
    assert not is_batchable_row(
        {"selector": "input", "value": "Job Board", "type": "HOW_HEARD"}
    )


def test_contamination_skips_how_heard_reopen():
    import asyncio
    from contamination import contamination_sweep

    class _FakePage:
        pass

    async def _run():
        return await contamination_sweep(
            _FakePage(),
            [
                {
                    "type": "HOW_HEARD",
                    "verified": True,
                    "value": "Job Board",
                    "readback": "Job Board",
                },
                {
                    "type": "NAME_FIRST",
                    "verified": True,
                    "value": "Test",
                    "readback": "Test",
                    "selector": "input[name=missing]",
                },
            ],
        )

    sweep = asyncio.run(_run())
    assert sweep["ok_count"] >= 1


def test_gh_country_value_matches_dial_readback():
    from verified_select import value_matches_readback
    from fill_verify import is_verified_fill_row

    assert value_matches_readback("United States", "+1") is True
    assert value_matches_readback("United States", "United States +1") is True
    assert value_matches_readback("United States", "Canada") is False
    row = {
        "type": "ADDRESS_COUNTRY",
        "ok": True,
        "verified": True,
        "value": "United States",
        "readback": "+1",
        "picked": "United States +1",
        "shown": "+1",
        "via": "gh_select",
    }
    assert is_verified_fill_row(row) is True
    row2 = {**row, "via": "deterministic_reclaim_gh_select"}
    assert is_verified_fill_row(row2) is True


if __name__ == "__main__":
    test_placeholder_readback_never_counts()
    test_stuck_status_never_counts_as_filled()
    test_verified_readback_counts()
    test_ok_without_verified_does_not_count()
    test_finalize_demotes_unverified_and_rejects_success_with_banner()
    test_scorecard_rejects_success_with_validation()
    test_scorecard_rejects_success_status_stuck()
    test_scorecard_allows_fail_with_banner()
    test_flash_off_when_unrequested()
    test_eval_gate_exit_codes()
    test_gh_select_shown_counts()
    test_finalize_demotes_success_when_stuck_on_same_page()
    test_finalize_demotes_success_when_required_empties()
    test_empty_linkedin_readback_never_counts()
    test_uuid_linkedin_replay_scrub()
    test_finalize_demotes_success_when_demoted_false_verified()
    test_finalize_demotes_success_when_required_empty_after_fill()
    test_gh_select_skip_already_correct_match()
    test_verified_select_rejects_type_without_select()
    test_flash_based_in_returns_yes_not_essay()
    test_field_attempt_cap_unfillable_after_2(__import__("pathlib").Path("/tmp/ff_attempt_cap_test"))
    test_should_demote_committed_match_not_live_empty()
    test_gh_select_race_decline_not_demoted_on_empty_live()
    test_gh_country_dial_plus_one_not_mismatch_demote()
    test_finalize_clears_stale_advance_when_req_after_empty()
    test_value_matches_skips_placeholder_but_keeps_correct()
    test_il_not_idaho_substring()
    test_county_classifies_before_state()
    test_classify_us_residence_not_address_line2()
    test_how_heard_chip_readback_is_verified()
    test_how_heard_picked_label_without_chrome_verified()
    test_guard_words_refuse_dangerous_maps()
    test_gaps_block_ready()
    test_option_mappings_roundtrip(__import__("pathlib").Path("/tmp/ff_option_map_test"))
    test_batch_fill_skips_how_heard()
    test_contamination_skips_how_heard_reopen()
    test_is_verified_fill_row_rejects_multiselect_uncommitted()
    test_how_heard_filter_text_not_verified()
    test_select_readback_rejects_idaho_for_illinois()
    test_resume_accept_empty_filelist_with_ui_hint()
    test_finalize_mid_run_skips_run_end()
    test_ashby_value_already_correct()
    test_ats2_002_us_country_phone_readback()
    test_ats2_008_generic_pack_state_combobox_phone_tight()
    test_ats2_009_wd_pack_includes_phone_device()
    test_ats2_005_option_mapping_rejects_confusable(
        __import__("pathlib").Path("/tmp/ff_option_map_poison")
    )
    test_ats2_007_learning_skips_how_heard_gender()
    test_eeo_answer_uses_profile_or_decline_fallback_without_llm()
    test_eeo_prompt_catalog_only_no_invent()
    test_dummy_eeo_policy_prefers_concrete_answers()
    test_vision_zip_placeholder_false_success_attribution(__import__("pathlib").Path("/tmp/ff_zip_attr_test"))
    test_vision_heuristic_never_complete_with_png(__import__("pathlib").Path("/tmp/ff_png_block_test"))
    test_promote_demoted_flash_leftovers()
    test_promote_skips_already_correct_and_recent_flash_fp()
    test_leftover_set_fingerprint_stable()
    test_is_select_field_vs_essay()
    test_flash_select_leftover_tagging()
    test_finalize_honest_leftover_count()
    test_finalize_detects_leftovers_zero_lie()
    test_gh_country_rejects_phone_dial_code()
    test_iti_flag_matches_country_not_any_flag()
    test_gh_sponsorship_needle_matches_dragos_label()
    test_without_need_sponsorship_is_work_auth_yes()
    test_phone_device_helpers_reject_dial_codes()
    test_filtered_option_index_maps_illinois_not_idaho()
    test_lever_already_checked_requires_correct_polarity()
    test_enrich_gh_id_leftover_maps_catalog()
    test_classify_candidate_location_id()
    test_gh_country_value_matches_dial_readback()
    print("test_honest_metrics: OK")
