#!/usr/bin/env python3
"""Hyper-exhaustive adversarial verify suite for the ATS gym.

Maps fail_taxonomy classes + ALLOWED_PLAYBOOKS to unit/browser fixtures.
Dummy-only; never submit; never CAPTCHA; never invent EEO.

Run via:
  python gym/ats/adversarial.py
  python gym/ats/runner.py --self-test  (includes this suite)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from async_util import sync_run

HERE = Path(__file__).resolve().parent
FASTFILL = HERE.parent.parent
CASES = HERE / "cases"
if str(FASTFILL) not in sys.path:
    sys.path.insert(0, str(FASTFILL))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# ---------------------------------------------------------------------------
# Coverage matrix (class → case / test) — mirrored in COVERAGE.md
# ---------------------------------------------------------------------------
COVERAGE_MATRIX: list[dict[str, str]] = [
    # False incomplete
    {"class": "false_incomplete", "taxonomy": "FAIL_BLANK", "playbook": "radio",
     "case": "wd_radio_aria_checked", "test": "test_false_incomplete_aria_checked_gaps"},
    {"class": "false_incomplete", "taxonomy": "FAIL_BLANK", "playbook": "radio",
     "case": "wd_radio_aria_checked", "test": "test_false_incomplete_instruction_gap"},
    {"class": "false_incomplete", "taxonomy": "FAIL_BLANK", "playbook": "workday_how_heard",
     "case": "unit", "test": "test_how_heard_chip_committed_verified"},
    # False complete
    {"class": "false_complete", "taxonomy": "FAIL_BLANK", "playbook": "text_input",
     "case": "unit", "test": "test_can_claim_ready_refuses_required_empty"},
    {"class": "false_complete", "taxonomy": "FAIL_MIDWIZARD", "playbook": "react_select_portal",
     "case": "false_complete_listbox_open", "test": "test_false_complete_listbox_open_case"},
    {"class": "false_complete", "taxonomy": "FAIL_MIDWIZARD", "playbook": "react_select_portal",
     "case": "unit", "test": "test_can_claim_ready_refuses_listbox_open"},
    {"class": "false_complete", "taxonomy": "FAIL_MIDWIZARD", "playbook": "native_select",
     "case": "unit", "test": "test_can_claim_ready_refuses_advance_blocked"},
    {"class": "false_complete", "taxonomy": "FAIL_WRONG_VALUE", "playbook": "text_input",
     "case": "unit", "test": "test_verified_rejects_wrong_readback"},
    {"class": "false_complete", "taxonomy": "FAIL_BLANK", "playbook": "workday_how_heard",
     "case": "unit", "test": "test_verified_rejects_uncommitted_multiselect"},
    {"class": "false_complete", "taxonomy": "FAIL_BLANK", "playbook": "workday_how_heard",
     "case": "workday_how_heard_hierarchical_chip", "test": "test_workday_hierarchical_empty_fails_gold"},
    # Thrash
    {"class": "thrash", "taxonomy": "FAIL_THRASH", "playbook": "workday_how_heard",
     "case": "unit", "test": "test_field_lock_blocks_reopen"},
    {"class": "thrash", "taxonomy": "FAIL_THRASH", "playbook": "workday_how_heard",
     "case": "unit", "test": "test_thrash_demotes_success"},
    {"class": "thrash", "taxonomy": "FAIL_THRASH", "playbook": "workday_how_heard",
     "case": "unit", "test": "test_how_heard_priority_no_alias_thrash"},
    # Select commit
    {"class": "select_commit", "taxonomy": "FAIL_BLANK", "playbook": "react_select_portal",
     "case": "gh_race_decline", "test": "test_fill_gh_race_decline"},
    {"class": "select_commit", "taxonomy": "FAIL_BLANK", "playbook": "react_select_portal",
     "case": "gh_react_select", "test": "test_fill_gh_react_select_school"},
    {"class": "select_commit", "taxonomy": "FAIL_BLANK", "playbook": "workday_how_heard",
     "case": "gh_howheard_multiselect", "test": "test_fill_gh_howheard_priority"},
    {"class": "select_commit", "taxonomy": "FAIL_BLANK", "playbook": "workday_how_heard",
     "case": "workday_how_heard_hierarchical_chip", "test": "test_fill_workday_how_heard_hierarchical_chip"},
    {"class": "select_commit", "taxonomy": "FAIL_BLANK", "playbook": "typable_commit",
     "case": "gh_typable_commit", "test": "test_fill_gh_typable_commit"},
    {"class": "select_commit", "taxonomy": "FAIL_BLANK", "playbook": "react_select_portal",
     "case": "portal_listbox", "test": "test_portal_listbox_case_loads"},
    # Cross-fill
    {"class": "cross_fill", "taxonomy": "FAIL_WRONG_VALUE", "playbook": "checkbox",
     "case": "crossfill_accommodations", "test": "test_crossfill_accommodations_case"},
    {"class": "cross_fill", "taxonomy": "FAIL_WRONG_VALUE", "playbook": "checkbox",
     "case": "unit", "test": "test_crossfill_accommodations_not_consent"},
    {"class": "cross_fill", "taxonomy": "FAIL_WRONG_VALUE", "playbook": "radio",
     "case": "unit", "test": "test_crossfill_noncompete_not_work_auth"},
    {"class": "cross_fill", "taxonomy": "FAIL_WRONG_VALUE", "playbook": "workday_how_heard",
     "case": "crossfill_phone_country", "test": "test_crossfill_phone_country_case"},
    {"class": "cross_fill", "taxonomy": "FAIL_WRONG_VALUE", "playbook": "workday_how_heard",
     "case": "unit", "test": "test_crossfill_phone_country_not_job_board"},
    {"class": "cross_fill", "taxonomy": "FAIL_WRONG_VALUE", "playbook": "text_input",
     "case": "unit", "test": "test_crossfill_privacy_not_name_full"},
    # Auth gate
    {"class": "auth_gate", "taxonomy": "FAIL_BLANK", "playbook": "text_input",
     "case": "workday_auth_gate", "test": "test_workday_auth_gate_case"},
    {"class": "auth_gate", "taxonomy": "FAIL_BLANK", "playbook": "text_input",
     "case": "workday_auth_gate_direct", "test": "test_workday_auth_gate_direct_case"},
    {"class": "auth_gate", "taxonomy": "FAIL_BLANK", "playbook": "text_input",
     "case": "unit", "test": "test_auth_sign_in_with_email_create_account"},
    # Advance honesty
    {"class": "advance_honesty", "taxonomy": "FAIL_MIDWIZARD", "playbook": "native_select",
     "case": "midwizard_sticky_submit", "test": "test_midwizard_footer_advance"},
    {"class": "advance_honesty", "taxonomy": "FAIL_MIDWIZARD", "playbook": "react_select_portal",
     "case": "unit", "test": "test_fail_taxonomy_demotes_midwizard_success"},
    {"class": "advance_honesty", "taxonomy": "FAIL_BLANK", "playbook": "native_select",
     "case": "unit", "test": "test_evaluate_cycle_success_refuses_gaps"},
    {"class": "advance_honesty", "taxonomy": "FAIL_MIDWIZARD", "playbook": "react_select_portal",
     "case": "unit", "test": "test_settle_before_advance_blocks_listbox"},
    # Playbooks detection
    {"class": "playbooks", "taxonomy": "SUCCESS", "playbook": "native_select",
     "case": "unit", "test": "test_playbooks_allowed_detect"},
    # Click accuracy — no waste
    {"class": "click_waste", "taxonomy": "FAIL_THRASH", "playbook": "react_select_portal",
     "case": "unit", "test": "test_enumerate_stable_arrowdown_at_most_one"},
    {"class": "click_waste", "taxonomy": "FAIL_THRASH", "playbook": "workday_how_heard",
     "case": "unit", "test": "test_how_heard_single_priority_commit"},
    {"class": "click_waste", "taxonomy": "FAIL_THRASH", "playbook": "workday_how_heard",
     "case": "unit", "test": "test_fill_steps_single_how_heard_attempt"},
    # Click accuracy — no wrong
    {"class": "click_wrong", "taxonomy": "FAIL_WRONG_VALUE", "playbook": "react_select_portal",
     "case": "unit", "test": "test_degree_pick_rejects_aa_for_masters"},
    {"class": "click_wrong", "taxonomy": "FAIL_WRONG_VALUE", "playbook": "react_select_portal",
     "case": "unit", "test": "test_soft_match_rejects_male_in_female"},
    {"class": "click_wrong", "taxonomy": "FAIL_WRONG_VALUE", "playbook": "react_select_portal",
     "case": "gh_race_decline", "test": "test_race_decline_never_picks_concrete_race"},
    # Non-fixable taxonomy
    {"class": "blocked", "taxonomy": "BLOCKED", "playbook": "text_input",
     "case": "unit", "test": "test_fail_taxonomy_captcha_blocked"},
]


async def _load_case_html(case_id: str) -> str:
    path = CASES / case_id / "form.html"
    if not path.is_file():
        raise FileNotFoundError(case_id)
    return path.read_text(encoding="utf-8")


async def _page_with_html(html: str):
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(html, wait_until="domcontentloaded")
    await page.wait_for_timeout(80)
    return pw, browser, page


async def _close(pw, browser) -> None:
    await browser.close()
    await pw.stop()


# ---------------------------------------------------------------------------
# 1. False incomplete
# ---------------------------------------------------------------------------


def test_false_incomplete_instruction_gap() -> None:
    from form_gaps import is_instruction_only_gap, normalize_gaps

    assert is_instruction_only_gap("CURRENT TEAMMATES: Please apply via your site")
    assert is_instruction_only_gap("Please apply via internal career site")
    assert is_instruction_only_gap("Have you previously been employed by O&M?*") is False
    norm = normalize_gaps(
        [{"label": "CURRENT TEAMMATES: Please apply via y…", "reason": "required_empty"}]
    )
    assert not any("teammates" in g["label"].lower() for g in norm)


async def _async_false_incomplete_aria_checked_gaps() -> None:
    from form_gaps import collect_form_gaps, gaps_block_ready
    from leftover_miss_scan import UNANSWERED_CHOICE_JS

    html = await _load_case_html("wd_radio_aria_checked")
    pw, browser, page = await _page_with_html(html)
    try:
        gaps = await collect_form_gaps(page)
        assert gaps == [], f"false incomplete gaps: {gaps}"
        assert gaps_block_ready(gaps) is False
        misses = await page.evaluate(UNANSWERED_CHOICE_JS)
        assert misses == [], f"false incomplete leftover misses: {misses}"
    finally:
        await _close(pw, browser)


def test_false_incomplete_aria_checked_gaps() -> None:
    sync_run(_async_false_incomplete_aria_checked_gaps())


def test_how_heard_chip_committed_verified() -> None:
    from fill_verify import is_verified_fill_row

    row = {
        "type": "HOW_HEARD",
        "value": "LinkedIn",
        "picked": "LinkedIn",
        "readback": "1 item selected LinkedIn",
        "option_clicked": True,
        "verified": True,
        "ok": True,
    }
    assert is_verified_fill_row(row) is True
    frag = {**row, "readback": "Internet", "option_clicked": False}
    assert is_verified_fill_row(frag) is False


# ---------------------------------------------------------------------------
# 2. False complete
# ---------------------------------------------------------------------------


def test_can_claim_ready_refuses_required_empty() -> None:
    from page_progress import can_claim_ready

    base = {
        "verdict": "SUCCESS",
        "advanced_incomplete": False,
        "validation_after_advance": None,
        "vision_judge_live": {"complete": True, "verdict": "PASS"},
        "footer_primary_kind": "FINAL",
        "leftovers": [],
    }
    assert can_claim_ready({**base, "required_empty_after_fill": [{"id": "email"}]}) is False


def test_can_claim_ready_refuses_listbox_open() -> None:
    from page_progress import can_claim_ready

    base = {
        "verdict": "SUCCESS",
        "advanced_incomplete": False,
        "validation_after_advance": None,
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "vision_judge_live": {"complete": True, "verdict": "PASS"},
        "footer_primary_kind": "FINAL",
        "leftovers": [],
    }
    assert can_claim_ready({**base, "listbox_open": True}) is False
    assert can_claim_ready({**base, "mid_widget_open": True}) is False


def test_can_claim_ready_refuses_advance_blocked() -> None:
    from page_progress import can_claim_ready

    base = {
        "verdict": "SUCCESS",
        "advanced_incomplete": False,
        "validation_after_advance": None,
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "vision_judge_live": {"complete": True, "verdict": "PASS"},
        "footer_primary_kind": "FINAL",
        "leftovers": [],
    }
    assert can_claim_ready({**base, "advance_blocked_reason": "listbox_still_open"}) is False


def test_verified_rejects_wrong_readback() -> None:
    from fill_verify import is_verified_fill_row

    row = {
        "type": "DEGREE",
        "value": "Master's Degree",
        "readback": "Associate's Degree",
        "verified": True,
        "ok": True,
    }
    assert is_verified_fill_row(row) is False


def test_verified_rejects_uncommitted_multiselect() -> None:
    from fill_verify import is_verified_fill_row

    row = {
        "type": "HOW_HEARD",
        "value": "Internet job board",
        "readback": "0 items selected",
        "verified": True,
    }
    assert is_verified_fill_row(row) is False


# ---------------------------------------------------------------------------
# 3. Thrash
# ---------------------------------------------------------------------------


def test_field_lock_blocks_reopen() -> None:
    from field_lock import FieldLockSession, attach_field_locks, gate_field_action

    report: dict = {}
    attach_field_locks(report)
    sess = report["_field_locks"]
    assert isinstance(sess, FieldLockSession)
    g1 = gate_field_action(report, field_type="HOW_HEARD", automation_id="how_heard")
    assert g1 is None or g1.get("action") == "proceed"
    sess.lock(field_type="HOW_HEARD", automation_id="how_heard", readback="LinkedIn", via="test")
    g2 = gate_field_action(report, field_type="HOW_HEARD", automation_id="how_heard")
    assert g2 is not None and g2.get("action") == "lock_skip"
    g3 = gate_field_action(report, field_type="HOW_HEARD", automation_id="how_heard", label="Indeed")
    assert g3 is not None and g3.get("action") == "lock_skip"


def test_thrash_demotes_success() -> None:
    from field_lock import apply_thrash_verdict_gate, attach_field_locks

    report: dict = {"verdict": "SUCCESS"}
    attach_field_locks(report)
    report["_field_locks"].thrash_retouches = 1
    apply_thrash_verdict_gate(report)
    assert report["verdict"] == "FAIL"
    assert report.get("thrash_demoted") is True


def test_how_heard_priority_no_alias_thrash() -> None:
    from fill_verify import how_heard_candidates, pick_how_heard_from_options

    opts = ["LinkedIn", "Indeed", "Job Board", "Other"]
    assert pick_how_heard_from_options(opts) == "LinkedIn"
    cands = how_heard_candidates({"HOW_HEARD": "Internet job board"})
    assert cands[0] == "LinkedIn"
    assert pick_how_heard_from_options(["Indeed", "Glassdoor"]) == "Indeed"


# ---------------------------------------------------------------------------
# 4. Select commit (browser fills)
# ---------------------------------------------------------------------------


async def _fill_gh_select_case(case_id: str, label: str, value: str, field_type: str) -> dict:
    from gh_select import fill_gh_select

    html = await _load_case_html(case_id)
    pw, browser, page = await _page_with_html(html)
    try:
        return await fill_gh_select(page, label, value, field_type=field_type)
    finally:
        await _close(pw, browser)


def test_fill_gh_race_decline() -> None:
    from gh_select import is_decline_like_alias

    r = sync_run(
        _fill_gh_select_case(
            "gh_race_decline",
            "Race / Ethnicity",
            "Decline to self identify",
            "RACE",
        )
    )
    assert r.get("ok"), r
    shown = str(r.get("shown") or "")
    assert is_decline_like_alias(shown)
    assert shown.lower() not in {"asian", "white", "hispanic or latino"}


def test_fill_gh_react_select_school() -> None:
    r = sync_run(
        _fill_gh_select_case(
            "gh_react_select",
            "School",
            "University of Cambridge",
            "SCHOOL",
        )
    )
    assert r.get("ok"), r
    shown = str(r.get("shown") or "").lower()
    assert "cambridge" in shown


def test_fill_gh_howheard_priority() -> None:
    r = sync_run(
        _fill_gh_select_case(
            "gh_howheard_multiselect",
            "How did you hear about this job?",
            "Internet job board",
            "HOW_HEARD",
        )
    )
    assert r.get("ok"), r
    shown = (r.get("shown") or "").lower()
    assert "linkedin" in shown, f"priority should commit LinkedIn, got {shown!r}"


async def _async_fill_workday_how_heard_hierarchical_chip() -> None:
    """Workday source--source: category → leaf → chip → listbox closed → gold pass."""
    from exp_workday_selectors import _fill_how_heard, _is_verified_fill
    from fill_verify import is_verified_fill_row
    from score import score_page
    from verified_select import (
        fill_hierarchical_how_heard,
        how_heard_source_committed,
        listbox_still_open,
        settle_open_listbox,
    )

    case_id = "workday_how_heard_hierarchical_chip"
    html = await _load_case_html(case_id)
    gold = json.loads((CASES / case_id / "gold.json").read_text(encoding="utf-8"))
    pw, browser, page = await _page_with_html(html)
    try:
        inp = page.locator('input[name="source--source"]')
        hier = await fill_hierarchical_how_heard(
            page,
            inp,
            leaf_candidates=["LinkedIn", "Indeed"],
            category_candidates=["Internet job board", "Job Board"],
        )
        assert hier.get("ok") and hier.get("committed"), hier
        picked = str(hier.get("picked") or hier.get("value") or "")
        rb = str(hier.get("readback") or "")
        assert "linkedin" in picked.lower() or "linkedin" in rb.lower(), hier
        assert how_heard_source_committed(rb, ["LinkedIn", "Indeed"]), rb
        assert is_verified_fill_row(
            {
                "type": "HOW_HEARD",
                "value": "LinkedIn",
                "picked": picked,
                "readback": rb,
                "option_clicked": bool(hier.get("option_clicked")),
                "verified": True,
                "ok": True,
                "committed": True,
            }
        ), f"readback must match picked leaf: {rb!r} picked={picked!r}"
        await settle_open_listbox(page)
        assert not await listbox_still_open(page), "listbox must close after chip commit"
        scored = await score_page(page, gold)
        assert scored.get("ok"), scored
    finally:
        await _close(pw, browser)

    # Integration: full _fill_how_heard path on fresh page
    pw2, browser2, page2 = await _page_with_html(html)
    try:
        report: dict[str, Any] = {"platform": "workday", "coverage_path": "workday_multipage"}
        hh = await _fill_how_heard(page2, {"HOW_HEARD": "Internet job board"}, report=report)
        assert _is_verified_fill(hh), hh
        rb2 = str(hh.get("readback") or "")
        assert how_heard_source_committed(rb2, ["LinkedIn", "Indeed"]), rb2
        assert "0 items selected" not in rb2.lower()
    finally:
        await _close(pw2, browser2)


def test_fill_workday_how_heard_hierarchical_chip() -> None:
    sync_run(_async_fill_workday_how_heard_hierarchical_chip())


async def _async_workday_hierarchical_empty_fails_gold() -> None:
    from score import score_page

    case_id = "workday_how_heard_hierarchical_chip"
    html = await _load_case_html(case_id)
    gold = json.loads((CASES / case_id / "gold.json").read_text(encoding="utf-8"))
    pw, browser, page = await _page_with_html(html)
    try:
        chrome = await page.locator("#chip-chrome").inner_text()
        assert "0 items selected" in chrome.lower()
        result = await score_page(page, gold)
        assert result.get("ok") is False, "empty hierarchical how-heard must fail gold"
    finally:
        await _close(pw, browser)


def test_workday_hierarchical_empty_fails_gold() -> None:
    sync_run(_async_workday_hierarchical_empty_fails_gold())


def test_fill_gh_typable_commit() -> None:
    """Typable commit: one option click; typing alone must not satisfy gold."""

    async def _run() -> None:
        html = await _load_case_html("gh_typable_commit")
        pw, browser, page = await _page_with_html(html)
        try:
            await page.fill("#location-input", "San")
            await page.wait_for_timeout(120)
            # Typing without click — must not commit
            partial = await page.get_attribute("#location-input", "data-committed")
            assert not partial, "typing alone must not commit"
            opt = page.locator('[role="option"]:has-text("San Francisco")')
            assert await opt.count() >= 1
            await opt.click()
            committed = await page.get_attribute("#location-input", "data-committed")
            assert committed and "San Francisco" in committed
            val = await page.input_value("#location-input")
            assert "San Francisco" in val
        finally:
            await _close(pw, browser)

    sync_run(_run())


def test_race_decline_never_picks_concrete_race() -> None:
    """RACE decline only — never click Asian/White/etc."""
    from gh_select import is_decline_like_alias

    r = sync_run(
        _fill_gh_select_case(
            "gh_race_decline",
            "Race / Ethnicity",
            "Decline to self identify",
            "RACE",
        )
    )
    assert r.get("ok"), r
    shown = str(r.get("shown") or r.get("picked") or "")
    assert is_decline_like_alias(shown)
    assert shown.lower() not in {
        "asian",
        "white",
        "black or african american",
        "hispanic or latino",
    }


def test_portal_listbox_case_loads() -> None:
    from score import score_page

    async def _run() -> None:
        html = await _load_case_html("portal_listbox")
        gold = json.loads((CASES / "portal_listbox" / "gold.json").read_text())
        pw, browser, page = await _page_with_html(html)
        try:
            result = await score_page(page, gold)
            assert result.get("ok") is False, "empty portal listbox must fail gold"
        finally:
            await _close(pw, browser)

    sync_run(_run())


async def _async_false_complete_listbox_open() -> None:
    from score import score_page

    html = await _load_case_html("false_complete_listbox_open")
    gold = json.loads((CASES / "false_complete_listbox_open" / "gold.json").read_text())
    pw, browser, page = await _page_with_html(html)
    try:
        expanded = await page.get_attribute("#degree-control", "aria-expanded")
        assert expanded == "true", "fixture must start with open listbox"
        result = await score_page(page, gold)
        assert result.get("ok") is False, "uncommitted open listbox must fail gold"
        from page_progress import can_claim_ready

        snap = {
            "verdict": "SUCCESS",
            "advanced_incomplete": False,
            "validation_after_advance": None,
            "required_empty_after_fill": [],
            "required_empty_before_advance": [],
            "vision_judge_live": {"complete": True, "verdict": "PASS"},
            "footer_primary_kind": "FINAL",
            "leftovers": [],
            "listbox_open": True,
        }
        assert can_claim_ready(snap) is False
    finally:
        await _close(pw, browser)


def test_false_complete_listbox_open_case() -> None:
    sync_run(_async_false_complete_listbox_open())


async def _async_crossfill_phone_country_case() -> None:
    from field_map import classify_field
    from score import score_page
    from verified_select import looks_like_phone_country_or_address_chip

    html = await _load_case_html("crossfill_phone_country")
    gold = json.loads((CASES / "crossfill_phone_country" / "gold.json").read_text())
    pw, browser, page = await _page_with_html(html)
    try:
        result = await score_page(page, gold)
        assert result.get("ok") is False, "empty phone+how_heard must fail gold"
        ftype_phone, _ = classify_field(
            {"label": "Phone Country Code", "name": "", "id": "phone-country-control"}
        )
        ftype_heard, _ = classify_field(
            {"label": "How Did You Hear About Us?", "name": "", "id": "how-heard-control"}
        )
        assert ftype_phone != ftype_heard
        assert looks_like_phone_country_or_address_chip("United States (+1)")
        assert looks_like_phone_country_or_address_chip("LinkedIn") is False
    finally:
        await _close(pw, browser)


def test_crossfill_phone_country_case() -> None:
    sync_run(_async_crossfill_phone_country_case())


async def _async_crossfill_accommodations_case() -> None:
    from field_map import ACCOMMODATIONS, MARKETING_CONSENT, classify_field
    from score import score_page

    html = await _load_case_html("crossfill_accommodations")
    gold = json.loads((CASES / "crossfill_accommodations" / "gold.json").read_text())
    pw, browser, page = await _page_with_html(html)
    try:
        result = await score_page(page, gold)
        assert result.get("ok") is False, "empty accommodations+consent must fail gold"
        ftype_acc, _ = classify_field(
            {
                "label": "Do you need accommodations to participate in the interview process?",
                "name": "accommodations",
                "id": "",
            }
        )
        ftype_mkt, _ = classify_field(
            {"label": "Marketing consent — may we email you about future roles?", "name": "", "id": ""}
        )
        assert ftype_acc == ACCOMMODATIONS
        assert ftype_mkt == MARKETING_CONSENT
        assert ftype_acc != ftype_mkt
    finally:
        await _close(pw, browser)


def test_crossfill_accommodations_case() -> None:
    sync_run(_async_crossfill_accommodations_case())


async def _async_workday_auth_gate_case() -> None:
    import exp_workday_selectors as w
    from score import score_page

    html = await _load_case_html("workday_auth_gate")
    gold = json.loads((CASES / "workday_auth_gate" / "gold.json").read_text())
    pw, browser, page = await _page_with_html(html)
    try:
        result = await score_page(page, gold)
        assert result.get("ok") is True, result
        assert result.get("auth_action") == "reveal_email"

        # Full what-next path: reveal → switch → create (dummy-only; never submit).
        reveal = await w._reveal_email_auth_form(page)
        assert any(c.get("action") == "clicked" for c in reveal), reveal
        assert await w._email_field_present(page)
        assert await w._create_account_link_present(page)
        assert (
            w.workday_auth_gate_action(
                has_create_form=await w._create_account_form(page),
                has_signin_form=await w._password_only_signin(page),
                has_email_field=await w._email_field_present(page),
                has_sign_in_with_email=await w._sign_in_with_email_present(page),
                has_create_account_link=await w._create_account_link_present(page),
                prefer_stored_signin=False,
            )
            == "switch_then_create"
        )
        switch = await w._switch_to_create_account(page)
        assert any(c.get("action") == "clicked" for c in switch), switch
        assert await w._create_account_form(page)
        assert (
            w.workday_auth_gate_action(
                has_create_form=True,
                has_signin_form=False,
                has_email_field=True,
                has_sign_in_with_email=False,
                has_create_account_link=True,
                prefer_stored_signin=False,
            )
            == "create_account"
        )
        assert (
            w.workday_auth_gate_action(
                has_create_form=False,
                has_signin_form=True,
                has_email_field=True,
                has_sign_in_with_email=False,
                has_create_account_link=True,
                prefer_stored_signin=True,
            )
            == "sign_in"
        )
    finally:
        await _close(pw, browser)


def test_workday_auth_gate_case() -> None:
    sync_run(_async_workday_auth_gate_case())


async def _async_workday_auth_gate_direct_case() -> None:
    import exp_workday_selectors as w
    from score import score_page

    html = await _load_case_html("workday_auth_gate_direct")
    gold = json.loads((CASES / "workday_auth_gate_direct" / "gold.json").read_text())
    pw, browser, page = await _page_with_html(html)
    try:
        result = await score_page(page, gold)
        assert result.get("ok") is True, result
        assert result.get("auth_action") == "switch_then_create"
        assert await w._password_only_signin(page)
        assert await w._create_account_link_present(page)
        assert not await w._create_account_form(page)
        switch = await w._switch_to_create_account(page)
        assert any(c.get("action") == "clicked" for c in switch), switch
        assert await w._create_account_form(page)
    finally:
        await _close(pw, browser)


def test_workday_auth_gate_direct_case() -> None:
    sync_run(_async_workday_auth_gate_direct_case())


# ---------------------------------------------------------------------------
# 5. Cross-fill / wrong type
# ---------------------------------------------------------------------------


def test_crossfill_accommodations_not_consent() -> None:
    from field_map import ACCOMMODATIONS, MARKETING_CONSENT, classify_field

    ftype_acc, _ = classify_field(
        {"label": "Do you need accommodations to participate in the interview process?", "name": "", "id": ""}
    )
    ftype_mkt, _ = classify_field(
        {"label": "Marketing consent — may we email you?", "name": "", "id": ""}
    )
    assert ftype_acc == ACCOMMODATIONS
    assert ftype_mkt == MARKETING_CONSENT
    assert ftype_acc != ftype_mkt


def test_crossfill_noncompete_not_work_auth() -> None:
    from field_map import classify_field

    ftype, _ = classify_field(
        {"label": "Are you subject to a non-compete agreement?", "name": "", "id": ""}
    )
    assert ftype != "WORK_AUTH"


def test_crossfill_phone_country_not_job_board() -> None:
    from verified_select import looks_like_phone_country_or_address_chip

    assert looks_like_phone_country_or_address_chip("United States (+1)")
    assert looks_like_phone_country_or_address_chip("LinkedIn") is False


def test_crossfill_privacy_not_name_full() -> None:
    from field_map import TERMS_CONSENT, classify_field

    ftype, _ = classify_field(
        {"label": "Capco Job Candidate Privacy Notice Acknowledgement*", "name": "", "id": ""}
    )
    assert ftype == TERMS_CONSENT
    ftype2, _ = classify_field({"label": "Full Legal Name", "name": "name", "id": ""})
    assert ftype2 != TERMS_CONSENT


# ---------------------------------------------------------------------------
# 6. Auth gate
# ---------------------------------------------------------------------------


def test_auth_sign_in_with_email_create_account() -> None:
    from exp_workday_selectors import workday_auth_gate_action
    from iframe_ctx import auth_advance_priority, create_account_link_priority

    assert create_account_link_priority("Create account") == 0
    assert auth_advance_priority("Create account") < auth_advance_priority("Sign in.")
    assert (
        workday_auth_gate_action(
            has_create_form=False,
            has_signin_form=False,
            has_email_field=False,
            has_sign_in_with_email=True,
            has_create_account_link=True,
            prefer_stored_signin=False,
        )
        == "reveal_email"
    )
    assert (
        workday_auth_gate_action(
            has_create_form=False,
            has_signin_form=True,
            has_email_field=True,
            has_sign_in_with_email=False,
            has_create_account_link=True,
            prefer_stored_signin=False,
        )
        == "switch_then_create"
    )
    assert (
        workday_auth_gate_action(
            has_create_form=False,
            has_signin_form=True,
            has_email_field=True,
            has_sign_in_with_email=False,
            has_create_account_link=True,
            prefer_stored_signin=True,
        )
        == "sign_in"
    )
    assert (
        workday_auth_gate_action(
            has_create_form=True,
            has_signin_form=False,
            has_email_field=True,
            has_sign_in_with_email=False,
            has_create_account_link=True,
            prefer_stored_signin=False,
        )
        == "create_account"
    )


# ---------------------------------------------------------------------------
# 7. Advance honesty
# ---------------------------------------------------------------------------


async def _async_midwizard_footer() -> None:
    from score import score_page

    html = await _load_case_html("midwizard_sticky_submit")
    gold = json.loads((CASES / "midwizard_sticky_submit" / "gold.json").read_text())
    pw, browser, page = await _page_with_html(html)
    try:
        result = await score_page(page, gold)
        assert result.get("footer_ok") is True
        assert result.get("ok") is False, "empty required fields must fail gold"
    finally:
        await _close(pw, browser)


def test_midwizard_footer_advance() -> None:
    sync_run(_async_midwizard_footer())


def test_fail_taxonomy_demotes_midwizard_success() -> None:
    from fail_taxonomy import apply_midwizard_to_decision

    d = apply_midwizard_to_decision(
        {
            "ready_for_review": True,
            "footer_primary_kind": "ADVANCE",
            "never_submit": True,
        },
        {"success": True, "verdict": "SUCCESS", "reasons": []},
    )
    assert d["success"] is False
    assert d["verdict"] == "FAIL_MIDWIZARD"


def test_evaluate_cycle_success_refuses_gaps() -> None:
    from cycle_orchestrate import evaluate_cycle_success

    decision = evaluate_cycle_success(
        {
            "never_submit": True,
            "submit_clicked": False,
            "identity_email": "randommail6969+abc@gmail.com",
            "leftovers": [],
            "gaps_after_save": [{"label": "Email is required", "reason": "error_node"}],
            "gaps_block_ready": True,
        },
        {"complete": True, "empty_fields": [], "confidence": "high", "source": "dom"},
    )
    assert decision["success"] is False


def test_settle_before_advance_blocks_listbox() -> None:
    import inspect

    import exp_workday_selectors as wd

    src = inspect.getsource(wd._click_next_advance)
    assert "listbox_still_open" in src
    assert "advance_blocked_reason" in src


# ---------------------------------------------------------------------------
# 8. Click accuracy — no waste, no wrong
# ---------------------------------------------------------------------------


def test_enumerate_stable_arrowdown_at_most_one() -> None:
    import concurrent.futures

    from test_verified_select import test_enumerate_stable_options_early_exits_arrowdown

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(test_enumerate_stable_options_early_exits_arrowdown).result()


def test_degree_pick_rejects_aa_for_masters() -> None:
    from gh_select import _score_option, aliases_for
    from verified_select import commit_min_score_for, pick_best_scored_option

    texts = [
        "A.A.",
        "Associate Degree",
        "Bachelor's Degree",
        "Master's Degree",
        "Doctorate (Academic)",
    ]
    intended = "Master's Degree"
    cands = aliases_for("DEGREE", intended)
    pick = pick_best_scored_option(
        texts,
        cands,
        _score_option,
        intent=intended,
        min_score=commit_min_score_for("DEGREE"),
    )
    assert pick is not None
    assert "Master" in pick[1]
    assert pick[1] != "A.A."


def test_soft_match_rejects_male_in_female() -> None:
    from verified_select import soft_value_match

    assert soft_value_match("Male", "Male")
    assert not soft_value_match("Male", "Female")
    assert not soft_value_match("IL", "Idaho")


def test_how_heard_single_priority_commit() -> None:
    from fill_verify import pick_how_heard_from_options

    opts = ["LinkedIn", "Indeed", "Job Board", "Other"]
    picked = pick_how_heard_from_options(opts)
    assert picked == "LinkedIn"
    # Second walk must not pick a lower-priority when LinkedIn present
    assert pick_how_heard_from_options(["Indeed", "Job Board"]) == "Indeed"


async def _async_how_heard_no_reopen_after_commit() -> None:
    from gh_select import fill_gh_select

    html = await _load_case_html("gh_howheard_multiselect")
    pw, browser, page = await _page_with_html(html)
    try:
        r1 = await fill_gh_select(
            page,
            "How did you hear about this job?",
            "Internet job board",
            field_type="HOW_HEARD",
        )
        assert r1.get("ok"), r1
        r2 = await fill_gh_select(
            page,
            "How did you hear about this job?",
            "Internet job board",
            field_type="HOW_HEARD",
        )
        assert r2.get("ok"), r2
        assert r2.get("skipped_already_correct") is True, "must not reopen committed select"
    finally:
        await _close(pw, browser)


def test_field_lock_prevents_second_select_click() -> None:
    sync_run(_async_how_heard_no_reopen_after_commit())


def test_fill_steps_single_how_heard_attempt() -> None:
    """Step-log waste analyzer flags duplicate how-heard fills, not lock skips."""
    from field_lock import analyze_step_log_waste

    ok = analyze_step_log_waste(
        [
            {"step": 1, "ts": "2026-08-10T06:00:00Z", "action": "select_word_by_word", "field_type": "HOW_HEARD", "label": "How heard"},
            {"step": 2, "ts": "2026-08-10T06:00:01Z", "action": "skip_already_correct", "field_type": "HOW_HEARD", "label": "How heard"},
        ]
    )
    assert ok["duplicate_fills"] == []
    bad = analyze_step_log_waste(
        [
            {"step": 1, "ts": "2026-08-10T06:00:00Z", "action": "select_word_by_word", "field_type": "HOW_HEARD", "label": "How heard"},
            {"step": 2, "ts": "2026-08-10T06:00:01Z", "action": "select_word_by_word", "field_type": "HOW_HEARD", "label": "How heard"},
        ]
    )
    assert bad["duplicate_fills"], "two select attempts on same field must register as waste"


# ---------------------------------------------------------------------------
# Playbooks + taxonomy extras
# ---------------------------------------------------------------------------


def test_playbooks_allowed_detect() -> None:
    from playbooks import ALLOWED_PLAYBOOKS, detect_playbook, is_allowed_playbook

    assert is_allowed_playbook("react_select_portal")
    assert detect_playbook({"tag": "select"}) == "native_select"
    assert detect_playbook({"role": "combobox", "class": "select__control"}) == "react_select_portal"
    assert len(ALLOWED_PLAYBOOKS) >= 8


def test_fail_taxonomy_captcha_blocked() -> None:
    from fail_taxonomy import classify_attempt

    c = classify_attempt({"blocker": "captcha"}, {"verdict": "BLOCKED"})
    assert c["code"] == "BLOCKED"
    assert c["fixable"] is False


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_ALL_TESTS: list[tuple[str, Callable[[], None]]] = [
    ("test_false_incomplete_instruction_gap", test_false_incomplete_instruction_gap),
    ("test_false_incomplete_aria_checked_gaps", test_false_incomplete_aria_checked_gaps),
    ("test_how_heard_chip_committed_verified", test_how_heard_chip_committed_verified),
    ("test_can_claim_ready_refuses_required_empty", test_can_claim_ready_refuses_required_empty),
    ("test_can_claim_ready_refuses_listbox_open", test_can_claim_ready_refuses_listbox_open),
    ("test_can_claim_ready_refuses_advance_blocked", test_can_claim_ready_refuses_advance_blocked),
    ("test_verified_rejects_wrong_readback", test_verified_rejects_wrong_readback),
    ("test_verified_rejects_uncommitted_multiselect", test_verified_rejects_uncommitted_multiselect),
    ("test_field_lock_blocks_reopen", test_field_lock_blocks_reopen),
    ("test_thrash_demotes_success", test_thrash_demotes_success),
    ("test_how_heard_priority_no_alias_thrash", test_how_heard_priority_no_alias_thrash),
    ("test_fill_gh_race_decline", test_fill_gh_race_decline),
    ("test_fill_gh_react_select_school", test_fill_gh_react_select_school),
    ("test_fill_gh_howheard_priority", test_fill_gh_howheard_priority),
    ("test_fill_workday_how_heard_hierarchical_chip", test_fill_workday_how_heard_hierarchical_chip),
    ("test_workday_hierarchical_empty_fails_gold", test_workday_hierarchical_empty_fails_gold),
    ("test_fill_gh_typable_commit", test_fill_gh_typable_commit),
    ("test_race_decline_never_picks_concrete_race", test_race_decline_never_picks_concrete_race),
    ("test_portal_listbox_case_loads", test_portal_listbox_case_loads),
    ("test_false_complete_listbox_open_case", test_false_complete_listbox_open_case),
    ("test_crossfill_phone_country_case", test_crossfill_phone_country_case),
    ("test_crossfill_accommodations_case", test_crossfill_accommodations_case),
    ("test_workday_auth_gate_case", test_workday_auth_gate_case),
    ("test_workday_auth_gate_direct_case", test_workday_auth_gate_direct_case),
    ("test_crossfill_accommodations_not_consent", test_crossfill_accommodations_not_consent),
    ("test_crossfill_noncompete_not_work_auth", test_crossfill_noncompete_not_work_auth),
    ("test_crossfill_phone_country_not_job_board", test_crossfill_phone_country_not_job_board),
    ("test_crossfill_privacy_not_name_full", test_crossfill_privacy_not_name_full),
    ("test_auth_sign_in_with_email_create_account", test_auth_sign_in_with_email_create_account),
    ("test_midwizard_footer_advance", test_midwizard_footer_advance),
    ("test_fail_taxonomy_demotes_midwizard_success", test_fail_taxonomy_demotes_midwizard_success),
    ("test_evaluate_cycle_success_refuses_gaps", test_evaluate_cycle_success_refuses_gaps),
    ("test_settle_before_advance_blocks_listbox", test_settle_before_advance_blocks_listbox),
    ("test_enumerate_stable_arrowdown_at_most_one", test_enumerate_stable_arrowdown_at_most_one),
    ("test_degree_pick_rejects_aa_for_masters", test_degree_pick_rejects_aa_for_masters),
    ("test_soft_match_rejects_male_in_female", test_soft_match_rejects_male_in_female),
    ("test_how_heard_single_priority_commit", test_how_heard_single_priority_commit),
    ("test_field_lock_prevents_second_select_click", test_field_lock_prevents_second_select_click),
    ("test_fill_steps_single_how_heard_attempt", test_fill_steps_single_how_heard_attempt),
    ("test_playbooks_allowed_detect", test_playbooks_allowed_detect),
    ("test_fail_taxonomy_captcha_blocked", test_fail_taxonomy_captcha_blocked),
]


def run_adversarial_suite() -> dict[str, Any]:
    """Run all adversarial tests; return {ok, passed, failed, results}."""
    results: list[dict[str, Any]] = []
    for name, fn in _ALL_TESTS:
        try:
            fn()
            results.append({"test": name, "ok": True})
        except Exception as e:
            results.append({"test": name, "ok": False, "error": str(e)[:240]})
    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed
    return {
        "ok": failed == 0,
        "passed": passed,
        "failed": failed,
        "total": len(results),
        "results": results,
        "coverage_rows": len(COVERAGE_MATRIX),
    }


def main() -> int:
    out = run_adversarial_suite()
    if not out["ok"]:
        print("adversarial suite FAILED:", json.dumps(out, indent=2))
        return 1
    print(f"adversarial suite OK ({out['passed']}/{out['total']} tests, {out['coverage_rows']} matrix rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
