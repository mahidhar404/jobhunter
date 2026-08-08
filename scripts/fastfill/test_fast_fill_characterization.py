#!/usr/bin/env python3
"""Characterization tests for scripts/fastfill/fast_fill.py.

These pin the CURRENT observable behavior of the deterministic, side-effect-light
seams that the two "god functions" (``run_fast_fill_async`` ~1,692 lines and
``fill_from_extract`` ~960 lines) delegate to: pure helpers, decision branches,
and report/dict transforms callable in isolation WITHOUT a live browser.

Goal: give a green regression net so behavior-preserving extract-method
refactors of the god functions can be verified. Safety-critical invariants
(never-submit / never-CAPTCHA / EEO-decline refusal / SUCCESS→FAIL demotion on
validation-after-advance) are pinned explicitly.

DUMMY / synthetic data only — never real applicant PII (repo rule). All inputs
here are hand-built fixtures, not real ``profile.json`` values.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import fast_fill as ff  # noqa: E402
from button_map import FINAL, UNKNOWN  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny pure string/shape helpers
# ---------------------------------------------------------------------------


def test_clean_label_collapses_whitespace_and_nbsp_and_caps_120():
    assert ff._clean_label("  hello   world \n\t") == "hello world"
    assert ff._clean_label("a\xa0b") == "a b"
    assert ff._clean_label("") == ""
    long = "x" * 300
    assert ff._clean_label(long) == "x" * 120


def test_street_line_extracts_leading_street_from_full_address():
    # "street, city, ST 12345" -> street only
    assert (
        ff._street_line("123 Main St, Springfield, IL 62704")
        == "123 Main St"
    )
    # No full match -> first comma segment
    assert ff._street_line("456 Oak Ave, Apt 2") == "456 Oak Ave"
    assert ff._street_line("") == ""


def test_normalize_extracted_maps_skyvern_shape_to_classify_shape():
    out = ff._normalize_extracted(
        {
            "type": "text",
            "label": "First Name",
            "ariaLabel": "first",
            "name": "fn",
            "id": "first_name",
            "selector": "#first_name",
            "options": [{"label": "a"}],
        }
    )
    assert out["label"] == "First Name"
    assert out["aria_label"] == "first"
    assert out["name"] == "fn"
    assert out["id"] == "first_name"
    assert out["type"] == "text"
    assert out["input_type"] == "text"
    assert out["tag"] == "input"
    assert out["selector"] == "#first_name"
    assert out["options"] == [{"label": "a"}]
    # required default
    assert out["required"] is False


def test_normalize_extracted_uses_input_type_fallback_for_type():
    out = ff._normalize_extracted({"input_type": "email"})
    assert out["type"] == "email"
    assert out["input_type"] == "email"


def test_playwright_sel_strips_visible_and_hidden_pseudos():
    assert ff._playwright_sel("#a:visible") == "#a"
    assert ff._playwright_sel("div:hidden") == "div"
    assert ff._playwright_sel("") == ""
    assert ff._playwright_sel("#plain") == "#plain"


# ---------------------------------------------------------------------------
# Blocker / platform detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("please solve the captcha", "captcha"),
        ("Access denied - bot detection", "akamai"),
        ("Just a moment...", "cloudflare"),
        ("Please verify your email to continue", "email_verify"),
        ("Welcome to the application form", None),
    ],
)
def test_detect_blocker(text, expected):
    assert ff._detect_blocker(text, "title", "https://x.test/") == expected


def test_detect_blocker_matches_in_title_or_url_too():
    assert ff._detect_blocker("", "reCAPTCHA challenge", "https://x/") == "captcha"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://boards.greenhouse.io/acme/jobs/123", "greenhouse"),
        ("https://acme.myworkdayjobs.com/en-US/careers", "workday"),
        ("https://jobs.lever.co/acme/abc/apply", "lever"),
        ("https://jobs.ashbyhq.com/acme/xyz", "ashby"),
        ("https://careers.example.com/apply", "unknown"),
        ("", "unknown"),
    ],
)
def test_detect_platform(url, expected):
    assert ff.detect_platform(url) == expected


def test_coverage_path_for():
    assert ff.coverage_path_for("workday") == "workday_multipage"
    assert ff.coverage_path_for("unknown") == "generic_dom"
    # A platform present in SELECTOR_PACKS gets pack+generic
    for plat in ff.SELECTOR_PACKS:
        assert ff.coverage_path_for(plat) == "selector_pack+generic_dom"
        break
    # A known-but-unpacked platform still falls back to generic_dom
    assert ff.coverage_path_for("definitely_not_a_pack") == "generic_dom"


def test_url_quality_prefers_direct_hosts_and_penalizes_trackers():
    direct = ff._url_quality("workday", "https://acme.myworkdayjobs.com/careers")
    tracker = ff._url_quality(
        "workday", "https://acme.myworkdayjobs.com/careers?rx_url=recruitics"
    )
    assert direct > tracker
    assert ff._url_quality("lever", "https://jobs.lever.co/a/b/apply") == 20
    assert ff._url_quality("greenhouse", "https://x/jobs/1") == 10


# ---------------------------------------------------------------------------
# Entry-click candidate ranking (pure decision branch of run_fast_fill_async)
# ---------------------------------------------------------------------------


def _ctrl(kind, *, gate_ok=True, text="", href=""):
    return {"kind": kind, "gate_ok": gate_ok, "text": text, "href": href}


def test_pick_click_candidates_drops_final_and_ungated():
    classified = [
        _ctrl(FINAL, text="Submit application"),
        _ctrl("ENTRY", gate_ok=False, text="Apply"),
        _ctrl("ENTRY", gate_ok=True, text="Apply"),
    ]
    picked = ff.pick_click_candidates(classified, allow_advance=False)
    assert [c["text"] for c in picked] == ["Apply"]


def test_pick_click_candidates_advance_gated_by_flag():
    classified = [_ctrl("ADVANCE", text="Next")]
    assert ff.pick_click_candidates(classified, allow_advance=False) == []
    assert len(ff.pick_click_candidates(classified, allow_advance=True)) == 1


def test_pick_click_candidates_ranks_resume_entry_first_and_penalizes_manual():
    classified = [
        _ctrl("ENTRY", text="Apply Manually"),
        _ctrl("RESUME_ENTRY", text="Apply with resume"),
        _ctrl("ENTRY", text="Apply", href="/jobapplication"),
    ]
    picked = ff.pick_click_candidates(classified, allow_advance=False)
    # RESUME_ENTRY ranks before ENTRY; "Apply Manually" is penalized last
    assert picked[0]["kind"] == "RESUME_ENTRY"
    assert picked[-1]["text"] == "Apply Manually"


def test_entry_click_reason_and_manual_candidate():
    assert ff._entry_click_reason({"kind": "RESUME_ENTRY"}) == "apply_with_resume"
    assert (
        ff._entry_click_reason({"kind": "ENTRY", "text": "Apply Manually"})
        == "apply_manually_fallback"
    )
    assert ff._entry_click_reason({"kind": "ADVANCE", "text": "Next"}) == "ADVANCE"
    assert ff._is_manual_entry_candidate({"text": "Apply Manually"}) is True
    assert ff._is_manual_entry_candidate({"text": "Apply"}) is False


def test_classify_controls_shape_and_gate_fields():
    rows = ff.classify_controls(
        [{"text": "Apply now", "tag": "a", "type": "", "aria_label": ""}]
    )
    assert len(rows) == 1
    r = rows[0]
    assert set(
        ["text", "tag", "type", "aria_label", "href", "kind", "gate_ok", "gate_reason"]
    ).issubset(r.keys())
    assert r["text"] == "Apply now"


# ---------------------------------------------------------------------------
# SAFETY: cookie-dismiss gate must refuse Submit / EEO decline / ambiguous
# ---------------------------------------------------------------------------


def test_cookie_gate_refuses_empty_label():
    res = ff.cookie_control_safe_to_click("")
    assert res["ok"] is False
    assert res["kind"] == FINAL


def test_cookie_gate_refuses_eeo_decline_phrases():
    for lab in ("Decline to self-identify", "Prefer not to say", "Decline"):
        res = ff.cookie_control_safe_to_click(lab)
        assert res["ok"] is False, lab


def test_cookie_gate_refuses_non_exact_and_ambiguous_short():
    assert ff.cookie_control_safe_to_click("Save and continue")["ok"] is False
    # short ambiguous words are refused even though not in the allow-list
    assert ff.cookie_control_safe_to_click("OK")["ok"] is False


def test_cookie_gate_refuses_submit_like():
    # "Submit" is neither exact-allowed nor safe — must never pass
    assert ff.cookie_control_safe_to_click("Submit")["ok"] is False


def test_cookie_gate_allows_reject_all():
    res = ff.cookie_control_safe_to_click("Reject all")
    assert res["ok"] is True
    assert res["reason"] == "allowed"


# ---------------------------------------------------------------------------
# Field / widget classification predicates (fill_from_extract seams)
# ---------------------------------------------------------------------------


def test_is_custom_widget_by_role_type_and_button_label():
    assert ff._is_custom_widget({"role": "combobox"}) is True
    assert ff._is_custom_widget({"type": "search-dropdown"}) is True
    assert ff._is_custom_widget({"tag": "button", "label": "Country"}) is True
    assert ff._is_custom_widget({"tag": "button", "label": "First Name"}) is False
    assert ff._is_custom_widget({"tag": "input", "type": "text"}) is False


def test_is_gh_select_fill_row():
    assert ff._is_gh_select_fill_row({"mode": "gh_select"}) is True
    assert ff._is_gh_select_fill_row({"via": "gh_select"}) is True
    assert ff._is_gh_select_fill_row({"via": "inpage_gh_select"}) is True
    assert ff._is_gh_select_fill_row({"via": "extract+classify"}) is False
    assert ff._is_gh_select_fill_row("nope") is False


# ---------------------------------------------------------------------------
# SAFETY: should_demote_claimed_text_fill (anti-false-verify logic)
# ---------------------------------------------------------------------------


def test_demote_never_when_selector_missing():
    assert (
        ff.should_demote_claimed_text_fill(
            sel_found=False, live_rb="", intended="John"
        )
        is False
    )


def test_demote_when_live_empty_and_no_valid_claim():
    assert (
        ff.should_demote_claimed_text_fill(
            sel_found=True, live_rb="", intended="John", claimed_rb=""
        )
        is True
    )


def test_no_demote_when_live_readback_matches_intended():
    assert (
        ff.should_demote_claimed_text_fill(
            sel_found=True, live_rb="John", intended="John"
        )
        is False
    )


def test_no_demote_when_claimed_readback_still_valid_even_if_live_empty():
    assert (
        ff.should_demote_claimed_text_fill(
            sel_found=True, live_rb="", intended="John", claimed_rb="John"
        )
        is False
    )


# ---------------------------------------------------------------------------
# Attempt/telemetry helpers tolerate None report (no crash, no side effect)
# ---------------------------------------------------------------------------


def test_field_capped_unfillable_none_report_is_false():
    assert ff._field_capped_unfillable(None, field_type="EMAIL", label="Email") is False


def test_record_fill_attempt_none_report_is_noop():
    # Must not raise
    ff._record_fill_attempt(None, {"type": "EMAIL", "ok": True})


# ---------------------------------------------------------------------------
# Demote-driven report transforms (pure dict manipulation)
# ---------------------------------------------------------------------------


def test_demoted_refill_types_collects_from_demoted_and_leftovers():
    report = {
        "leftovers": [
            {"type": "PHONE", "reason": "live_empty_after_claimed_verified"},
            {"type": "EMAIL", "reason": "no_value"},
        ]
    }
    got = ff._demoted_refill_types(report, [{"type": "NAME_FIRST"}])
    assert got == {"NAME_FIRST", "PHONE"}


def test_purge_stale_filled_after_demote_drops_matching_types():
    report = {
        "filled": [
            {"type": "PHONE", "ok": True},
            {"type": "EMAIL", "ok": True},
        ]
    }
    n = ff._purge_stale_filled_after_demote(report, {"PHONE"})
    assert n == 1
    assert [f["type"] for f in report["filled"]] == ["EMAIL"]
    assert ff._purge_stale_filled_after_demote(report, set()) == 0


def test_ensure_leftovers_for_demoted_types_adds_missing_flash_candidates():
    report = {"leftovers": []}
    demoted = [{"type": "PHONE", "label": "Phone", "selector": "#p", "readback": "x"}]
    ff._ensure_leftovers_for_demoted_types(report, {"PHONE"}, demoted)
    assert len(report["leftovers"]) == 1
    row = report["leftovers"][0]
    assert row["type"] == "PHONE"
    assert row["flash_candidate"] is True
    assert row["reason"] == "live_empty_after_claimed_verified"


def test_ensure_leftovers_skips_types_already_present():
    report = {"leftovers": [{"type": "PHONE"}]}
    ff._ensure_leftovers_for_demoted_types(report, {"PHONE"}, [{"type": "PHONE"}])
    assert len(report["leftovers"]) == 1


def test_already_types_skip_refill_excludes_demoted():
    report = {
        "filled": [
            {"type": "PHONE", "ok": True},
            {"type": "EMAIL", "ok": True},
            {"type": "NAME", "ok": False},
        ],
        "leftovers": [
            {"type": "PHONE", "reason": "live_empty_after_claimed_verified"}
        ],
    }
    got = ff._already_types_skip_refill(report)
    assert "EMAIL" in got
    assert "PHONE" not in got  # demoted -> needs reclaim
    assert "NAME" not in got  # ok is False


def test_apply_demote_result_noop_when_no_demoted():
    report = {"filled": [{"type": "PHONE", "ok": True}]}
    ff._apply_demote_result(report, {"demoted": []})
    assert "demote_side_effects" not in report
    assert len(report["filled"]) == 1


def test_apply_demote_result_purges_and_records_side_effects():
    report = {
        "url": "https://x.test/apply",
        "platform": "greenhouse",
        "filled": [{"type": "PHONE", "ok": True}],
        "leftovers": [],
    }
    ff._apply_demote_result(
        report, {"demoted": [{"type": "PHONE", "label": "Phone", "selector": "#p"}]}
    )
    assert report["filled"] == []
    assert "demote_side_effects" in report
    assert "PHONE" in report["demote_side_effects"]["demoted_types"]
    assert any(u["type"] == "PHONE" for u in report["leftovers"])


# ---------------------------------------------------------------------------
# Flash-leftover selection helpers (pure)
# ---------------------------------------------------------------------------


def test_flash_candidate_leftovers_filters_blocker_and_false_flag():
    report = {
        "leftovers": [
            {"type": "A", "flash_candidate": True},
            {"type": "B", "flash_candidate": False},
            {"type": "C", "reason": "blocker: captcha"},
            {"type": "D"},  # default flash_candidate is not False -> included
        ]
    }
    got = [u["type"] for u in ff._flash_candidate_leftovers(report)]
    assert got == ["A", "D"]


def test_leftover_row_fp_key_is_stable_and_lowercased():
    k = ff._leftover_row_fp_key(
        {"type": "EMAIL", "label": "  Your Email  ", "selector": "#e"}
    )
    assert k == "EMAIL|your email|#e"


def test_leftover_set_fingerprint_is_deterministic():
    report = {"leftovers": [{"type": "A", "label": "a", "selector": "#a"}]}
    fp1 = ff._leftover_set_fingerprint(report)
    fp2 = ff._leftover_set_fingerprint(report)
    assert fp1 == fp2 and len(fp1) == 20


def test_demoted_flash_leftovers_selects_by_reason():
    report = {
        "leftovers": [
            {"type": "A", "reason": "live_empty_after_claimed_verified"},
            {"type": "B", "reason": "unfillable_after_2"},
            {"type": "C", "reason": "no_value"},
        ]
    }
    got = [u["type"] for u in ff._demoted_flash_leftovers(report)]
    assert got == ["A", "B"]


def test_promote_demoted_flash_leftovers_promotes_and_normalizes_reason():
    report = {
        "leftovers": [
            {"type": "A", "label": "a", "selector": "#a", "reason": "unfillable_after_2"},
        ],
        "filled": [],
        "flash": {},
    }
    n = ff._promote_demoted_flash_leftovers(report)
    assert n == 1
    row = report["leftovers"][0]
    assert row["flash_candidate"] is True
    assert row["reason"] == "live_empty_after_claimed_verified"


def test_promote_demoted_flash_skips_recent_flash_fingerprint():
    row = {
        "type": "A",
        "label": "a",
        "selector": "#a",
        "reason": "live_empty_after_claimed_verified",
    }
    report = {
        "leftovers": [dict(row)],
        "filled": [],
        "flash": {"attempted": [dict(row)]},
    }
    n = ff._promote_demoted_flash_leftovers(report)
    assert n == 0
    assert report["leftovers"][0]["flash_candidate"] is False
    assert report["leftovers"][0]["flash_skip_reason"] == "recent_flash_same_fingerprint"


def test_flash_filled_count_prefers_explicit_then_len_then_sum():
    assert ff._flash_filled_count({"filled_count": 3}) == 3
    assert ff._flash_filled_count({"filled": [1, 2]}) == 2
    assert ff._flash_filled_count({"llm_fills": 2, "deterministic_reclaims": 1}) == 3
    assert ff._flash_filled_count({}) == 0


# ---------------------------------------------------------------------------
# SAFETY: Workday merge demotes false SUCCESS and honors validation banners
# ---------------------------------------------------------------------------


def test_merge_workday_demotes_success_on_validation_after_advance():
    report = {"verdict": "SUCCESS", "leftovers": [], "pages_seen": []}
    ff._merge_workday_into_report(
        report,
        {"verdict": "SUCCESS", "validation_after_advance": {"banner": "required"},
         "ready_for_review": True},
        {},
    )
    assert report["verdict"] == "FAIL"
    assert report["workday"]["verdict"] == "FAIL"


def test_merge_workday_demotes_success_when_not_ready_for_review():
    report = {"verdict": "SUCCESS", "leftovers": [], "pages_seen": []}
    ff._merge_workday_into_report(report, {"verdict": "SUCCESS"}, {})
    assert report["verdict"] == "FAIL"
    assert report["verdict_reason"] == "multipage_incomplete_not_ready_for_review"


def test_merge_workday_promotes_leftovers_without_dupes():
    report = {"leftovers": [{"label": "X", "automation_id": "x1"}], "pages_seen": []}
    ff._merge_workday_into_report(
        report,
        {
            "ready_for_review": True,
            "verdict": "PARTIAL",
            "leftovers": [
                {"label": "X", "automation_id": "x1"},  # dupe
                {"label": "Y", "automation_id": "y1"},
            ],
        },
        {},
    )
    labels = sorted(u["label"] for u in report["leftovers"])
    assert labels == ["X", "Y"]


# ---------------------------------------------------------------------------
# Headless / hold / refill resolution (env-driven, deterministic)
# ---------------------------------------------------------------------------


def test_resolve_headless_precedence():
    assert ff._resolve_headless(headed=True) is False
    assert ff._resolve_headless(headed=False) is True
    assert ff._resolve_headless(headless=True) is True
    assert ff._resolve_headless(headless=False) is False
    assert ff._resolve_headless() is True  # default headless


def test_hold_is_active():
    assert ff.hold_is_active(0) is False
    assert ff.hold_is_active(None) is False
    assert ff.hold_is_active(30) is True
    assert ff.hold_is_active(ff.HOLD_INDEFINITE) is True


def test_resolve_refill_wait_enter_default_off():
    assert ff.resolve_refill_wait_enter(None) is False
    assert ff.resolve_refill_wait_enter(True) is True
    assert ff.resolve_refill_wait_enter(False) is False


def test_resolve_hold_seconds_headless_default_zero(monkeypatch):
    monkeypatch.delenv("FASTFILL_ALLOW_LONG_HOLD", raising=False)
    monkeypatch.delenv("FASTFILL_HOLD_MS", raising=False)
    assert ff._resolve_hold_seconds(hold_seconds=None, headed=False) == 0


def test_resolve_hold_seconds_headed_default(monkeypatch):
    monkeypatch.delenv("FASTFILL_ALLOW_LONG_HOLD", raising=False)
    monkeypatch.delenv("FASTFILL_HOLD_MS", raising=False)
    assert (
        ff._resolve_hold_seconds(hold_seconds=None, headed=True)
        == ff.DEFAULT_HEADED_HOLD_SECONDS
    )


def test_resolve_hold_seconds_indefinite_and_cap(monkeypatch):
    monkeypatch.delenv("FASTFILL_ALLOW_LONG_HOLD", raising=False)
    monkeypatch.delenv("FASTFILL_HOLD_MS", raising=False)
    assert ff._resolve_hold_seconds(hold_seconds=-1, headed=True) == ff.HOLD_INDEFINITE
    # Positive request above VARIETY cap is capped unless allow-long
    assert (
        ff._resolve_hold_seconds(hold_seconds=100000, headed=True)
        == ff.VARIETY_MAX_HOLD_SECONDS
    )
    monkeypatch.setenv("FASTFILL_ALLOW_LONG_HOLD", "1")
    assert ff._resolve_hold_seconds(hold_seconds=100000, headed=True) == 100000


# ---------------------------------------------------------------------------
# GH city aliases (pure — uses verified_select.location_option_aliases)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Extracted fill_from_extract sub-computations (behavior-preserving helpers)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,ftype",
    [
        ("Twitter URL", "text"),
        ("Instagram", "text"),
        ("Partnership Program", "text"),
        ("Type your response", "text"),
    ],
)
def test_unclassified_skip_quietly_true_cases(label, ftype):
    assert ff._unclassified_skip_quietly(label, {"type": ftype}) is True


def test_unclassified_skip_quietly_bare_yes_no_only_for_choice_types():
    assert ff._unclassified_skip_quietly("Yes", {"type": "radio_group"}) is True
    assert ff._unclassified_skip_quietly("No", {"type": "checkbox"}) is True
    # Bare Yes/No that is NOT a choice widget is not skipped quietly
    assert ff._unclassified_skip_quietly("Yes", {"type": "text"}) is False


def test_unclassified_skip_quietly_normal_label_false():
    assert ff._unclassified_skip_quietly("Why do you want this job?", {}) is False


def test_sponsorship_radio_candidates_uses_dummy_status_only_for_status_lists():
    dummy_status = str(
        (ff.DUMMY_PROFILE.get("work_authorization") or {}).get("status") or ""
    ).strip()
    # citizenship-status option list without a direct "No" -> use dummy status
    status_opts = [
        {"label": "U.S. Citizen"},
        {"label": "Permanent Resident"},
        {"label": "OPT"},
    ]
    got = ff._sponsorship_radio_candidates(status_opts, ["No"])
    if dummy_status:
        assert got == [dummy_status]
    else:
        assert got == ["No"]


def test_sponsorship_radio_candidates_keeps_default_when_direct_no_present():
    opts = [{"label": "Yes, I require sponsorship"}, {"label": "No"}]
    assert ff._sponsorship_radio_candidates(opts, ["No"]) == ["No"]


def test_sponsorship_radio_candidates_keeps_default_for_plain_yes_no():
    opts = [{"label": "Yes"}, {"label": "No"}]
    assert ff._sponsorship_radio_candidates(opts, ["No"]) == ["No"]


def test_skip_already_filled_type_skips_plain_repeat():
    assert ff._skip_already_filled_type("EMAIL", "Email", {"EMAIL"}) is True


def test_skip_already_filled_type_not_when_first_occurrence():
    assert ff._skip_already_filled_type("EMAIL", "Email", set()) is False


def test_skip_already_filled_type_allows_multi_types():
    from field_map import HOW_HEARD, RESUME_UPLOAD, WORK_AUTH

    assert ff._skip_already_filled_type(RESUME_UPLOAD, "Resume", {RESUME_UPLOAD}) is False
    assert ff._skip_already_filled_type(WORK_AUTH, "Work auth", {WORK_AUTH}) is False
    # HOW_HEARD is allow-multi -> never skipped
    assert ff._skip_already_filled_type(HOW_HEARD, "How did you hear", {HOW_HEARD}) is False


def test_skip_already_filled_type_how_heard_other_specify_still_fills():
    # (redundant with allow-multi, but pins the inner other/specify branch)
    from field_map import HOW_HEARD

    assert (
        ff._skip_already_filled_type(HOW_HEARD, "If Other, please specify", {HOW_HEARD})
        is False
    )


def test_radio_group_name_prefers_field_name():
    assert ff._radio_group_name({"name": "gender"}, "#g") == "gender"


def test_radio_group_name_parses_selector_when_field_name_missing():
    assert (
        ff._radio_group_name({}, 'input[name="cards[abc][field0]"][value="x"]')
        == "cards[abc][field0]"
    )
    assert ff._radio_group_name({}, "input[value='x']") == ""
    assert ff._radio_group_name({}, "") == ""


def test_gh_city_aliases_defaults_illinois():
    primary, aliases = ff._gh_city_aliases({}, "")
    assert isinstance(aliases, list) and aliases
    assert "Springfield" in primary
    # Illinois is forced for the default Springfield city
    assert any("Illinois" in a for a in aliases)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
