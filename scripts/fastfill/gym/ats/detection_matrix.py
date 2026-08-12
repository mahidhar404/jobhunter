#!/usr/bin/env python3
"""Four-dimension detection matrix — field, option, page-complete, what-next.

Parameterized adversarial fixtures. Dummy-only; never submit.
Run: python gym/ats/detection_matrix.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable

from async_util import sync_run

HERE = Path(__file__).resolve().parent
FASTFILL = HERE.parent.parent
CASES = HERE / "cases"
if str(FASTFILL) not in sys.path:
    sys.path.insert(0, str(FASTFILL))

# dimension → list of {id, case, test_fn_name, description}
DETECTION_MATRIX: list[dict[str, str]] = []

def _row(dim: str, cell_id: str, case: str, test: str, desc: str) -> dict[str, str]:
    r = {
        "dimension": dim,
        "cell_id": cell_id,
        "case": case,
        "test": test,
        "description": desc,
    }
    DETECTION_MATRIX.append(r)
    return r


# --- detect-field ---
_row("detect_field", "filled_text_readback", "unit", "test_detect_field_filled_text",
     "Text fill verified only with matching readback")
_row("detect_field", "empty_placeholder", "unit", "test_detect_field_empty_placeholder",
     "Placeholder readback never counts filled")
_row("detect_field", "wrong_value_readback", "unit", "test_detect_field_wrong_value",
     "Mismatched readback → not verified")
_row("detect_field", "click_claimed_no_readback", "unit", "test_detect_field_click_not_verified",
     "verified=True without readback → False")
_row("detect_field", "help_text_not_gap", "wd_radio_aria_checked", "test_detect_field_help_text_noise",
     "Instruction sibling text stripped from gaps")
_row("detect_field", "aria_checked_answered", "wd_radio_aria_checked", "test_detect_field_aria_checked",
     "Radio No via aria-checked → not required_empty")
_row("detect_field", "open_listbox_uncommitted", "false_complete_listbox_open",
     "test_detect_field_open_listbox", "Open listbox + placeholder → not committed")

# --- detect-option ---
_row("detect_option", "chip_committed", "unit", "test_detect_option_chip_committed",
     "HOW_HEARD chip + option_clicked → committed")
_row("detect_option", "filter_uncommitted", "unit", "test_detect_option_filter_fragment",
     "Filter token without chip → uncommitted")
_row("detect_option", "decline_not_race", "gh_race_decline", "test_detect_option_decline_only",
     "RACE decline option committed; never concrete race")
_row("detect_option", "typable_must_click", "gh_typable_commit", "test_detect_option_typable_click",
     "Typable: typing ≠ commit until option clicked")
_row("detect_option", "hierarchical_leaf_chip", "workday_how_heard_hierarchical_chip",
     "test_detect_option_hierarchical_leaf_chip",
     "Workday source--source: category→leaf chip; readback==picked")
_row("detect_option", "decline_not_race_html", "gh_race_decline", "test_detect_option_decline_html",
     "GH race fixture: decline aliases only")

# --- page-complete ---
_row("page_complete", "ready_honest", "unit", "test_page_complete_ready_true",
     "can_claim_ready when gates pass")
_row("page_complete", "false_required_empty", "unit", "test_page_complete_required_empty",
     "required_empty_after_fill blocks Ready")
_row("page_complete", "false_listbox_open", "unit", "test_page_complete_listbox_open",
     "listbox_open blocks Ready")
_row("page_complete", "false_gaps", "unit", "test_page_complete_gaps_block",
     "gaps_after_save blocks Ready + cycle SUCCESS")
_row("page_complete", "false_midwizard", "midwizard_sticky_submit", "test_page_complete_midwizard",
     "ADVANCE footer + empty required → not complete")
_row("page_complete", "false_listbox_html", "false_complete_listbox_open",
     "test_page_complete_listbox_html", "Open listbox HTML fails gold when empty")
_row("page_complete", "may_enter_review_final", "unit", "test_page_complete_review_hold",
     "FINAL footer allows review hold when complete")

# --- what-next ---
_row("what_next", "footer_next_advance", "unit", "test_what_next_footer_next",
     "Next → ADVANCE; blocks review hold")
_row("what_next", "footer_submit_final", "unit", "test_what_next_footer_submit",
     "Submit → FINAL; review-eligible")
_row("what_next", "sticky_advance_wins", "midwizard_sticky_submit", "test_what_next_sticky_advance",
     "Sticky Submit + Next → primary is ADVANCE")
_row("what_next", "auth_reveal_email", "workday_auth_gate", "test_what_next_auth_gate_html",
     "Auth gate HTML → reveal_email action")
_row("what_next", "auth_reveal_email_unit", "unit", "test_what_next_auth_reveal",
     "Sign in with email hidden → reveal_email")
_row("what_next", "auth_create_account", "unit", "test_what_next_auth_create",
     "Create form present → create_account")
_row("what_next", "auth_switch_then_create", "unit", "test_what_next_auth_switch",
     "Sign-in form + create link + no stored → switch_then_create")
_row("what_next", "auth_stored_signin", "unit", "test_what_next_auth_stored_signin",
     "Stored creds on sign-in gate → sign_in")
_row("what_next", "auth_gate_fixture", "workday_auth_gate", "test_what_next_auth_gate_fixture",
     "Gym auth gate: reveal → switch → create click path")
_row("what_next", "auth_gate_direct", "workday_auth_gate_direct", "test_what_next_auth_gate_direct_html",
     "Direct sign-in form → switch_then_create")
_row("what_next", "probe_footer_primary", "midwizard_sticky_submit", "test_what_next_probe_footer",
     "probe_footer_primary picks Next over sticky Submit")
_row("what_next", "settle_listbox_before_advance", "unit", "test_what_next_settle_listbox",
     "Advance blocked when listbox still open")
_row("what_next", "cycle_demotes_incomplete", "unit", "test_what_next_cycle_demotes",
     "evaluate_cycle_success demotes mid-wizard SUCCESS")

# --- thrash ---
_row("thrash", "field_lock_skip", "gh_howheard_multiselect", "test_thrash_field_lock_skip",
     "Second fill_gh_select → skipped_already_correct")
_row("thrash", "thrash_demotes", "unit", "test_thrash_demotes_success_unit",
     "thrash_retouches demotes SUCCESS verdict")
_row("thrash", "how_heard_priority", "gh_howheard_multiselect", "test_thrash_how_heard_priority",
     "LinkedIn priority — no alias walk")
_row("thrash", "hierarchical_chip_commit", "workday_how_heard_hierarchical_chip",
     "test_thrash_hierarchical_chip_commit",
     "Workday hierarchy: leaf chip + listbox closed after fill")
_row("thrash", "arrowdown_waste", "unit", "test_thrash_arrowdown_at_most_one",
     "Stable menu: ArrowDown ≤1")

# --- crossfill ---
_row("crossfill", "phone_not_jobboard", "crossfill_phone_country", "test_crossfill_phone_html",
     "Phone country chip ≠ LinkedIn job board")
_row("crossfill", "accommodations_not_consent", "crossfill_accommodations", "test_crossfill_accommodations_html",
     "ACCOMMODATIONS ≠ MARKETING_CONSENT classify")
_row("crossfill", "noncompete_not_workauth", "unit", "test_crossfill_noncompete_unit",
     "Noncompete label ≠ WORK_AUTH")
_row("crossfill", "privacy_not_name", "unit", "test_crossfill_privacy_unit",
     "Privacy notice ≠ NAME_FULL")

# ---------------------------------------------------------------------------
# detect-field
# ---------------------------------------------------------------------------


def test_detect_field_filled_text() -> None:
    from fill_verify import is_verified_fill_row

    assert is_verified_fill_row(
        {"type": "EMAIL", "value": "ada@test.com", "readback": "ada@test.com", "verified": True, "ok": True}
    )


def test_detect_field_empty_placeholder() -> None:
    from fill_verify import is_verified_fill_row

    assert not is_verified_fill_row(
        {"type": "SCHOOL", "value": "MIT", "readback": "Select...", "verified": True}
    )
    assert not is_verified_fill_row(
        {"type": "SCHOOL", "value": "MIT", "readback": "Select One", "verified": True}
    )


def test_detect_field_wrong_value() -> None:
    from fill_verify import is_verified_fill_row

    assert not is_verified_fill_row(
        {
            "type": "DEGREE",
            "value": "Master's Degree",
            "readback": "Associate's Degree",
            "verified": True,
            "ok": True,
        }
    )


def test_detect_field_click_not_verified() -> None:
    from fill_verify import is_verified_fill_row

    assert not is_verified_fill_row(
        {"type": "PHONE", "value": "+15551234567", "verified": True, "ok": True}
    )


def test_detect_field_help_text_noise() -> None:
    from form_gaps import is_instruction_only_gap, normalize_gaps

    assert is_instruction_only_gap("CURRENT TEAMMATES: Please apply via internal site")
    norm = normalize_gaps(
        [{"label": "CURRENT TEAMMATES: Please apply via", "reason": "required_empty"}]
    )
    assert norm == []


async def _async_detect_field_aria_checked() -> None:
    from form_gaps import collect_form_gaps
    from leftover_miss_scan import UNANSWERED_CHOICE_JS

    html = (CASES / "wd_radio_aria_checked" / "form.html").read_text(encoding="utf-8")
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        gaps = await collect_form_gaps(page)
        misses = await page.evaluate(UNANSWERED_CHOICE_JS)
        await browser.close()
    assert gaps == [], gaps
    assert misses == [], misses


def test_detect_field_aria_checked() -> None:
    sync_run(_async_detect_field_aria_checked())


async def _async_detect_field_open_listbox() -> None:
    import json

    from score import score_page

    html = (CASES / "false_complete_listbox_open" / "form.html").read_text(encoding="utf-8")
    gold = json.loads((CASES / "false_complete_listbox_open" / "gold.json").read_text())
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        expanded = await page.get_attribute("#degree-control", "aria-expanded")
        result = await score_page(page, gold)
        await browser.close()
    assert expanded == "true"
    assert result.get("ok") is False


def test_detect_field_open_listbox() -> None:
    sync_run(_async_detect_field_open_listbox())


# ---------------------------------------------------------------------------
# detect-option
# ---------------------------------------------------------------------------


def test_detect_option_chip_committed() -> None:
    from fill_verify import is_verified_fill_row

    assert is_verified_fill_row(
        {
            "type": "HOW_HEARD",
            "value": "LinkedIn",
            "picked": "LinkedIn",
            "readback": "1 item selected LinkedIn",
            "option_clicked": True,
            "verified": True,
            "ok": True,
        }
    )


def test_detect_option_filter_fragment() -> None:
    from fill_verify import is_verified_fill_row

    assert not is_verified_fill_row(
        {
            "type": "HOW_HEARD",
            "value": "Internet job board",
            "readback": "Internet",
            "option_clicked": False,
            "verified": True,
        }
    )


def test_detect_option_decline_only() -> None:
    from gh_select import is_decline_like_alias

    assert is_decline_like_alias("I don't wish to answer")
    assert is_decline_like_alias("Prefer not to disclose")
    assert not is_decline_like_alias("Asian")


async def _async_detect_option_typable_click() -> None:
    from playwright.async_api import async_playwright

    html = (CASES / "gh_typable_commit" / "form.html").read_text(encoding="utf-8")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        await page.fill("#location-input", "San Francisco")
        uncommitted = await page.get_attribute("#location-input", "data-committed")
        assert not uncommitted, "type-only must not commit option"
        await page.locator('[role="option"]:has-text("San Francisco")').click()
        committed = await page.get_attribute("#location-input", "data-committed")
        assert committed and "San Francisco" in committed
        await browser.close()


def test_detect_option_typable_click() -> None:
    sync_run(_async_detect_option_typable_click())


async def _async_detect_option_hierarchical_leaf_chip() -> None:
    import json

    from fill_verify import is_verified_fill_row
    from playwright.async_api import async_playwright
    from verified_select import (
        fill_hierarchical_how_heard,
        how_heard_source_committed,
        listbox_still_open,
        settle_open_listbox,
    )

    html = (CASES / "workday_how_heard_hierarchical_chip" / "form.html").read_text(encoding="utf-8")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        inp = page.locator('input[name="source--source"]')
        hier = await fill_hierarchical_how_heard(
            page,
            inp,
            leaf_candidates=["LinkedIn", "Indeed"],
            category_candidates=["Internet job board", "Job Board"],
        )
        assert hier.get("ok") and hier.get("committed"), hier
        picked = str(hier.get("picked") or "")
        rb = str(hier.get("readback") or "")
        assert how_heard_source_committed(rb, ["LinkedIn"]), rb
        assert is_verified_fill_row(
            {
                "type": "HOW_HEARD",
                "value": "LinkedIn",
                "picked": picked,
                "readback": rb,
                "option_clicked": True,
                "verified": True,
                "ok": True,
                "committed": True,
            }
        )
        await settle_open_listbox(page)
        assert not await listbox_still_open(page)
        await browser.close()


def test_detect_option_hierarchical_leaf_chip() -> None:
    sync_run(_async_detect_option_hierarchical_leaf_chip())


def test_detect_option_decline_html() -> None:
    from gh_select import is_decline_like_alias

    assert is_decline_like_alias("I don't wish to answer")
    assert not is_decline_like_alias("Asian")


# ---------------------------------------------------------------------------
# page-complete
# ---------------------------------------------------------------------------

_READY_BASE = {
    "verdict": "SUCCESS",
    "advanced_incomplete": False,
    "validation_after_advance": None,
    "required_empty_before_advance": [],
    "required_empty_after_fill": [],
    "leftovers": [],
    "gaps_after_save": [],
    "vision_judge_live": {"complete": True, "verdict": "PASS"},
}


def test_page_complete_ready_true() -> None:
    from page_progress import can_claim_ready

    assert can_claim_ready({**_READY_BASE, "footer_primary_kind": "FINAL"})


def test_page_complete_required_empty() -> None:
    from page_progress import can_claim_ready

    assert not can_claim_ready(
        {**_READY_BASE, "required_empty_after_fill": [{"id": "email"}], "footer_primary_kind": "FINAL"}
    )


def test_page_complete_listbox_open() -> None:
    from page_progress import can_claim_ready

    assert not can_claim_ready({**_READY_BASE, "listbox_open": True, "footer_primary_kind": "FINAL"})


def test_page_complete_gaps_block() -> None:
    from form_gaps import gaps_block_ready
    from page_progress import can_claim_ready

    gaps = [{"label": "Email is required", "reason": "error_node"}]
    assert gaps_block_ready(gaps)
    assert not can_claim_ready(
        {**_READY_BASE, "gaps_after_save": gaps, "gaps_block_ready": True, "footer_primary_kind": "FINAL"}
    )


async def _async_page_complete_midwizard() -> None:
    import json

    from playwright.async_api import async_playwright

    from score import score_page

    html = (CASES / "midwizard_sticky_submit" / "form.html").read_text(encoding="utf-8")
    gold = json.loads((CASES / "midwizard_sticky_submit" / "gold.json").read_text())
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        result = await score_page(page, gold)
        await browser.close()
    assert result.get("footer_ok")
    assert not result.get("ok"), "incomplete midwizard must fail gold"


def test_page_complete_midwizard() -> None:
    sync_run(_async_page_complete_midwizard())


async def _async_page_complete_listbox_html() -> None:
    import json

    from playwright.async_api import async_playwright

    from score import score_page

    html = (CASES / "false_complete_listbox_open" / "form.html").read_text(encoding="utf-8")
    gold = json.loads((CASES / "false_complete_listbox_open" / "gold.json").read_text())
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        result = await score_page(page, gold)
        await browser.close()
    assert not result.get("ok")


def test_page_complete_listbox_html() -> None:
    sync_run(_async_page_complete_listbox_html())


def test_page_complete_review_hold() -> None:
    from page_progress import may_enter_review_hold

    complete = {
        **_READY_BASE,
        "footer_primary_kind": "FINAL",
        "footer_primary_label": "Submit application",
        "workday": {"phase_e": {"stopped_at_review": True}},
    }
    assert may_enter_review_hold(complete) is True
    incomplete = {**complete, "footer_primary_kind": "ADVANCE", "footer_primary_label": "Next"}
    assert may_enter_review_hold(incomplete) is False


# ---------------------------------------------------------------------------
# what-next
# ---------------------------------------------------------------------------


def test_what_next_footer_next() -> None:
    from page_progress import footer_primary_wizard_incomplete

    assert footer_primary_wizard_incomplete("ADVANCE", "Save and Continue") is True
    assert footer_primary_wizard_incomplete("ADVANCE", "Next") is True


def test_what_next_footer_submit() -> None:
    from page_progress import footer_primary_wizard_incomplete

    assert footer_primary_wizard_incomplete("FINAL", "Submit application") is False


async def _async_what_next_sticky_advance() -> None:
    import json

    from playwright.async_api import async_playwright

    from score import score_page

    html = (CASES / "midwizard_sticky_submit" / "form.html").read_text(encoding="utf-8")
    gold = json.loads((CASES / "midwizard_sticky_submit" / "gold.json").read_text())
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        result = await score_page(page, gold)
        await browser.close()
    assert gold.get("footer_kind") == "ADVANCE"
    assert result.get("field_results"), "required fields listed"
    assert not result.get("ok")


def test_what_next_sticky_advance() -> None:
    sync_run(_async_what_next_sticky_advance())


async def _async_what_next_auth_gate_html() -> None:
    import json

    from playwright.async_api import async_playwright

    from score import score_page

    html = (CASES / "workday_auth_gate" / "form.html").read_text(encoding="utf-8")
    gold = json.loads((CASES / "workday_auth_gate" / "gold.json").read_text())
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        result = await score_page(page, gold)
        await browser.close()
    assert result.get("ok") is True
    assert result.get("auth_action") == "reveal_email"


def test_what_next_auth_gate_html() -> None:
    sync_run(_async_what_next_auth_gate_html())


def test_what_next_auth_reveal() -> None:
    from exp_workday_selectors import workday_auth_gate_action

    assert (
        workday_auth_gate_action(
            has_create_form=False,
            has_signin_form=False,
            has_email_field=False,
            has_sign_in_with_email=True,
            has_create_account_link=False,
            prefer_stored_signin=False,
        )
        == "reveal_email"
    )


def test_what_next_auth_create() -> None:
    from exp_workday_selectors import workday_auth_gate_action

    assert (
        workday_auth_gate_action(
            has_create_form=True,
            has_signin_form=False,
            has_email_field=True,
            has_sign_in_with_email=False,
            has_create_account_link=False,
            prefer_stored_signin=False,
        )
        == "create_account"
    )


def test_what_next_auth_switch() -> None:
    from exp_workday_selectors import workday_auth_gate_action

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


def test_what_next_auth_stored_signin() -> None:
    from exp_workday_selectors import workday_auth_gate_action

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


async def _async_what_next_auth_gate_fixture() -> None:
    import json

    from playwright.async_api import async_playwright

    import exp_workday_selectors as w
    from score import score_page

    html = (CASES / "workday_auth_gate" / "form.html").read_text(encoding="utf-8")
    gold = json.loads((CASES / "workday_auth_gate" / "gold.json").read_text())
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        result = await score_page(page, gold)
        assert result.get("auth_action") == "reveal_email"
        reveal = await w._reveal_email_auth_form(page)
        assert any(c.get("action") == "clicked" for c in reveal)
        switch = await w._switch_to_create_account(page)
        assert any(c.get("action") == "clicked" for c in switch)
        assert await w._create_account_form(page)
        await browser.close()


def test_what_next_auth_gate_fixture() -> None:
    sync_run(_async_what_next_auth_gate_fixture())


async def _async_what_next_auth_gate_direct_html() -> None:
    import json

    from playwright.async_api import async_playwright

    from score import score_page

    html = (CASES / "workday_auth_gate_direct" / "form.html").read_text(encoding="utf-8")
    gold = json.loads((CASES / "workday_auth_gate_direct" / "gold.json").read_text())
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        result = await score_page(page, gold)
        await browser.close()
    assert result.get("ok") is True, result
    assert result.get("auth_action") == "switch_then_create"


def test_what_next_auth_gate_direct_html() -> None:
    sync_run(_async_what_next_auth_gate_direct_html())


async def _async_what_next_probe_footer() -> None:
    import json

    from playwright.async_api import async_playwright

    from page_progress import probe_footer_primary

    html = (CASES / "midwizard_sticky_submit" / "form.html").read_text(encoding="utf-8")
    gold = json.loads((CASES / "midwizard_sticky_submit" / "gold.json").read_text())
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        report: dict = {}
        out = await probe_footer_primary(page, report)
        await browser.close()
    assert out.get("kind") == gold.get("footer_kind") == "ADVANCE"
    assert out.get("label") == "Next"
    assert report.get("footer_primary_blocks_review_hold") is True


def test_what_next_probe_footer() -> None:
    sync_run(_async_what_next_probe_footer())


def test_what_next_settle_listbox() -> None:
    import inspect

    import exp_workday_selectors as wd

    src = inspect.getsource(wd._click_next_advance)
    assert "listbox_still_open" in src


def test_what_next_cycle_demotes() -> None:
    from cycle_orchestrate import evaluate_cycle_success
    from fail_taxonomy import apply_midwizard_to_decision

    report = {
        "never_submit": True,
        "submit_clicked": False,
        "identity_email": "randommail6969+abc@gmail.com",
        "leftovers": [],
        "ready_for_review": True,
        "footer_primary_kind": "ADVANCE",
        "footer_primary_label": "Next",
    }
    vision = {"complete": True, "empty_fields": [], "confidence": "high", "source": "dom"}
    decision = evaluate_cycle_success(report, vision)
    decision = apply_midwizard_to_decision(report, decision)
    assert decision["success"] is False
    assert decision["verdict"] == "FAIL_MIDWIZARD"


# ---------------------------------------------------------------------------
# thrash
# ---------------------------------------------------------------------------


def test_thrash_field_lock_skip() -> None:
    from adversarial import test_field_lock_prevents_second_select_click

    test_field_lock_prevents_second_select_click()


def test_thrash_demotes_success_unit() -> None:
    from adversarial import test_thrash_demotes_success

    test_thrash_demotes_success()


def test_thrash_how_heard_priority() -> None:
    from adversarial import test_how_heard_single_priority_commit

    test_how_heard_single_priority_commit()


def test_thrash_hierarchical_chip_commit() -> None:
    from adversarial import test_fill_workday_how_heard_hierarchical_chip

    test_fill_workday_how_heard_hierarchical_chip()


def test_thrash_arrowdown_at_most_one() -> None:
    from adversarial import test_enumerate_stable_arrowdown_at_most_one

    test_enumerate_stable_arrowdown_at_most_one()


# ---------------------------------------------------------------------------
# crossfill
# ---------------------------------------------------------------------------


def test_crossfill_phone_html() -> None:
    from adversarial import test_crossfill_phone_country_case

    test_crossfill_phone_country_case()


def test_crossfill_accommodations_html() -> None:
    from adversarial import test_crossfill_accommodations_case

    test_crossfill_accommodations_case()


def test_crossfill_noncompete_unit() -> None:
    from adversarial import test_crossfill_noncompete_not_work_auth

    test_crossfill_noncompete_not_work_auth()


def test_crossfill_privacy_unit() -> None:
    from adversarial import test_crossfill_privacy_not_name_full

    test_crossfill_privacy_not_name_full()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_TEST_MAP: dict[str, Callable[[], None]] = {
    "test_detect_field_filled_text": test_detect_field_filled_text,
    "test_detect_field_empty_placeholder": test_detect_field_empty_placeholder,
    "test_detect_field_wrong_value": test_detect_field_wrong_value,
    "test_detect_field_click_not_verified": test_detect_field_click_not_verified,
    "test_detect_field_help_text_noise": test_detect_field_help_text_noise,
    "test_detect_field_aria_checked": test_detect_field_aria_checked,
    "test_detect_field_open_listbox": test_detect_field_open_listbox,
    "test_detect_option_chip_committed": test_detect_option_chip_committed,
    "test_detect_option_filter_fragment": test_detect_option_filter_fragment,
    "test_detect_option_decline_only": test_detect_option_decline_only,
    "test_detect_option_typable_click": test_detect_option_typable_click,
    "test_detect_option_hierarchical_leaf_chip": test_detect_option_hierarchical_leaf_chip,
    "test_detect_option_decline_html": test_detect_option_decline_html,
    "test_page_complete_ready_true": test_page_complete_ready_true,
    "test_page_complete_required_empty": test_page_complete_required_empty,
    "test_page_complete_listbox_open": test_page_complete_listbox_open,
    "test_page_complete_gaps_block": test_page_complete_gaps_block,
    "test_page_complete_midwizard": test_page_complete_midwizard,
    "test_page_complete_listbox_html": test_page_complete_listbox_html,
    "test_page_complete_review_hold": test_page_complete_review_hold,
    "test_what_next_footer_next": test_what_next_footer_next,
    "test_what_next_footer_submit": test_what_next_footer_submit,
    "test_what_next_sticky_advance": test_what_next_sticky_advance,
    "test_what_next_auth_gate_html": test_what_next_auth_gate_html,
    "test_what_next_auth_reveal": test_what_next_auth_reveal,
    "test_what_next_auth_create": test_what_next_auth_create,
    "test_what_next_auth_switch": test_what_next_auth_switch,
    "test_what_next_auth_stored_signin": test_what_next_auth_stored_signin,
    "test_what_next_auth_gate_fixture": test_what_next_auth_gate_fixture,
    "test_what_next_probe_footer": test_what_next_probe_footer,
    "test_what_next_settle_listbox": test_what_next_settle_listbox,
    "test_what_next_cycle_demotes": test_what_next_cycle_demotes,
    "test_thrash_field_lock_skip": test_thrash_field_lock_skip,
    "test_thrash_demotes_success_unit": test_thrash_demotes_success_unit,
    "test_thrash_how_heard_priority": test_thrash_how_heard_priority,
    "test_thrash_hierarchical_chip_commit": test_thrash_hierarchical_chip_commit,
    "test_thrash_arrowdown_at_most_one": test_thrash_arrowdown_at_most_one,
    "test_crossfill_phone_html": test_crossfill_phone_html,
    "test_crossfill_accommodations_html": test_crossfill_accommodations_html,
    "test_crossfill_noncompete_unit": test_crossfill_noncompete_unit,
    "test_crossfill_privacy_unit": test_crossfill_privacy_unit,
}


def run_detection_matrix() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for cell in DETECTION_MATRIX:
        name = cell["test"]
        fn = _TEST_MAP.get(name)
        if fn is None:
            results.append({"cell": cell["cell_id"], "test": name, "ok": False, "error": "missing test fn"})
            continue
        try:
            fn()
            results.append(
                {
                    "dimension": cell["dimension"],
                    "cell_id": cell["cell_id"],
                    "test": name,
                    "ok": True,
                }
            )
        except Exception as e:
            results.append(
                {
                    "dimension": cell["dimension"],
                    "cell_id": cell["cell_id"],
                    "test": name,
                    "ok": False,
                    "error": str(e)[:240],
                }
            )
    passed = sum(1 for r in results if r.get("ok"))
    return {
        "ok": passed == len(DETECTION_MATRIX),
        "passed": passed,
        "failed": len(DETECTION_MATRIX) - passed,
        "total": len(DETECTION_MATRIX),
        "results": results,
        "dimensions": sorted({c["dimension"] for c in DETECTION_MATRIX}),
    }


def main() -> int:
    out = run_detection_matrix()
    if not out["ok"]:
        import json

        print("detection_matrix FAILED:", json.dumps(out, indent=2))
        return 1
    print(
        f"detection_matrix OK ({out['passed']}/{out['total']} cells, "
        f"dims={','.join(out['dimensions'])})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
