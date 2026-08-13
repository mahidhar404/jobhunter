#!/usr/bin/env python3
"""Regression: Workday State/Province (countryRegion) Illinois — NXP 2244Z."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "gym/ats/cases/workday_address_state_illinois/form.html"


async def _browser(html_path: Path, fn) -> None:
    from playwright.async_api import async_playwright

    html = html_path.read_text(encoding="utf-8")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        await fn(page)
        await browser.close()


def test_expand_state_illinois_before_il():
    from verified_select import expand_state_value, soft_value_match, value_matches_readback

    cands = expand_state_value("IL")
    assert cands[0] == "Illinois"
    assert "IL" in cands
    # soft_value_match intentionally rejects IL⊂Illinois; expand + value_matches
    assert not soft_value_match("IL", "Illinois")
    assert value_matches_readback("IL", "Illinois", mode="combobox")
    assert soft_value_match("Illinois", "Illinois")
    assert not soft_value_match("Illinois", "Idaho")


def test_field_done_state_il_matches_illinois_readback():
    from field_done import field_is_done_from_readback
    from field_map import ADDRESS_STATE

    v = field_is_done_from_readback(
        "Illinois",
        {"type": ADDRESS_STATE, "automation_id": "addressSection_countryRegion"},
        "IL",
    )
    assert v.ok, v
    assert v.reason == "state_match"

    bad = field_is_done_from_readback(
        "Idaho",
        {"type": ADDRESS_STATE, "automation_id": "addressSection_countryRegion"},
        "IL",
    )
    assert not bad.ok, bad


def test_commit_fill_never_verifies_intent_as_readback():
    """NXP 2244Z: null readback + value=Illinois must NOT become verified."""
    from fill_contract import commit_fill
    from field_map import ADDRESS_STATE

    async def _run():
        with tempfile.TemporaryDirectory() as td:
            report: dict = {"_attempt_cycle_dir": td, "dummy": True}

            async def _fill_miss() -> dict:
                return {
                    "automation_id": "addressSection_countryRegion",
                    "status": "missed",
                    "mode": "combobox",
                    "value": "IL",
                    "type": ADDRESS_STATE,
                    "verified": False,
                    "reason": "no_matching_option",
                    "readback": None,
                    "option_text": None,
                }

            # No page locator → must not invent Illinois from intent
            fr = await commit_fill(
                None,
                {
                    "type": ADDRESS_STATE,
                    "automation_id": "addressSection_countryRegion",
                    "mode": "combobox",
                },
                "IL",
                _fill_miss,
                via="test",
                report=report,
            )
            assert fr.verified is False, fr
            assert fr.row.get("verified") is False
            assert fr.row.get("reason") in (
                "no_matching_option",
                "empty_readback",
                "field_not_done",
            ) or "empty" in str(fr.row.get("reason") or "")

    asyncio.run(_run())


def test_fill_country_region_illinois_with_how_heard_open():
    from exp_workday_selectors import _fill_country_region_state, _is_verified_fill
    from field_map import ADDRESS_STATE

    async def _run(page):
        # How-Heard listbox is open in fixture — settle must clear it first
        loc = page.locator('[data-automation-id="addressSection_countryRegion"]').first
        sel = '[data-automation-id="addressSection_countryRegion"]'
        result = await _fill_country_region_state(page, loc, sel, "IL")
        assert result.get("type") == ADDRESS_STATE
        assert _is_verified_fill(result), result
        assert result.get("status") == "filled"
        assert result.get("reason") != "no_matching_option"
        rb = str(result.get("readback") or "")
        assert "Illinois" in rb or soft_rb_ok(rb), result
        # Button must show Illinois, not Idaho / Select One
        btn = await loc.inner_text()
        assert "Illinois" in btn
        assert "Idaho" not in btn

    def soft_rb_ok(rb: str) -> bool:
        from verified_select import soft_value_match

        return soft_value_match("IL", rb) or soft_value_match("Illinois", rb)

    asyncio.run(_browser(FIXTURE, _run))


def test_click_matching_option_prompt_without_role():
    """promptOption without role=option must still be clickable for Illinois."""
    from exp_workday_selectors import _click_matching_option

    async def _run(page):
        # Close how-heard first so State menu is the active list
        await page.keyboard.press("Escape")
        await page.locator("#state-btn").click()
        await page.wait_for_timeout(100)
        ok, txt = await _click_matching_option(page, "IL", reject_dial=True)
        assert ok, (ok, txt)
        assert txt and "Illinois" in txt
        assert "Idaho" not in (txt or "")

    asyncio.run(_browser(FIXTURE, _run))


def test_live_field_is_done_state_after_commit():
    from field_done import field_is_done
    from field_map import ADDRESS_STATE

    async def _run(page):
        await page.keyboard.press("Escape")
        await page.locator("#state-btn").click()
        await page.locator(
            '[data-automation-id="promptOption"][data-automation-label="Illinois"]'
        ).click()
        v = await field_is_done(
            page,
            {
                "type": ADDRESS_STATE,
                "automation_id": "addressSection_countryRegion",
            },
            "IL",
        )
        assert v.ok, v
        assert "Illinois" in str(v.readback or "")

    asyncio.run(_browser(FIXTURE, _run))


def test_state_path_keeps_prompt_option_not_fiber_text():
    """Illinois stays role_click/promptOption; fiber_text_commit is post-verify only."""
    import inspect

    from exp_workday_selectors import _fill_country_region_state

    src = inspect.getsource(_fill_country_region_state)
    assert "_click_matching_option" in src
    assert "role_click_promptOption" in src
    assert "fiber_post_verify_reread" in src
    assert "await fiber_text_commit" not in src
    assert "fill_text_fiber_then_read" not in src
    assert "promptOption" in src


def test_select_one_uses_mcp_scroll_and_no_fos_cascade():
    """Select One: scrollIntoView helper; FoS/How-Heard one-shot skip."""
    import inspect

    from exp_workday_selectors import _fill_select_one_by_label

    src = inspect.getsource(_fill_select_one_by_label)
    assert "scroll_widget_into_view" in src
    assert "one_shot" in src
    assert "skipped_no_commit" in src
    assert "keyboard.press(\"Enter\")" not in src


def test_already_illinois_chip_skips_rewrite():
    from exp_workday_selectors import _fill_country_region_state, _is_verified_fill

    async def _run(page):
        await page.keyboard.press("Escape")
        await page.locator("#state-btn").click()
        await page.locator(
            '[data-automation-id="promptOption"][data-automation-label="Illinois"]'
        ).click()
        loc = page.locator('[data-automation-id="addressSection_countryRegion"]').first
        result = await _fill_country_region_state(
            page, loc, '[data-automation-id="addressSection_countryRegion"]', "IL"
        )
        assert _is_verified_fill(result), result
        assert result.get("skipped_already_correct") or result.get("reason") in (
            "already_correct_skip",
            "state_committed",
            "fiber_post_verify_reread",
        )
        assert "Illinois" in str(result.get("readback") or "")
        assert "Idaho" not in str(result.get("readback") or "")

    asyncio.run(_browser(FIXTURE, _run))


def test_county_fill_plan_is_combobox_not_fiber_text() -> None:
    """regionSubdivision1 must be combobox in two-phase (same as CSS pack)."""
    from exp_workday_selectors import build_contact_fill_plan
    from field_map import ADDRESS_COUNTY
    from verified_select import is_stubborn_text_field

    plan, _ = build_contact_fill_plan({ADDRESS_COUNTY: "Sangamon"})
    county = [r for r in plan if r[0] == "addressSection_regionSubdivision1"]
    assert county and county[0][2] is True, county
    assert not is_stubborn_text_field(
        automation_id="addressSection_regionSubdivision1",
        field_type="ADDRESS_COUNTY",
    )


def test_contact_core_required_excludes_apt_and_absent_county() -> None:
    """NXP 0842Z: Apt/county not_in_dom must not pack_incomplete Contact Next."""
    from exp_workday_selectors import (
        WD_CONTACT_CORE_REQUIRED_AIDS,
        _contact_pack_blocking_misses,
    )

    assert "addressSection_addressLine2" not in WD_CONTACT_CORE_REQUIRED_AIDS
    assert "addressSection_regionSubdivision1" not in WD_CONTACT_CORE_REQUIRED_AIDS
    assert "phone-number" in WD_CONTACT_CORE_REQUIRED_AIDS
    assert "addressSection_countryRegion" in WD_CONTACT_CORE_REQUIRED_AIDS
    missed = [
        {
            "automation_id": "addressSection_addressLine2",
            "reason": "not_in_dom",
            "status": "missed",
        },
        {
            "automation_id": "addressSection_regionSubdivision1",
            "reason": "not_in_dom",
            "status": "missed",
        },
        {
            "automation_id": "addressSection_city",
            "reason": "empty_readback",
            "status": "missed",
        },
    ]
    fill_plan = [
        ("addressSection_addressLine2", "Apt 1A", False),
        ("addressSection_regionSubdivision1", "Sangamon", True),
        ("addressSection_city", "Springfield", False),
    ]
    blocking = _contact_pack_blocking_misses(missed, fill_plan)
    aids = {m["automation_id"] for m in blocking}
    assert "addressSection_addressLine2" not in aids, blocking
    assert "addressSection_regionSubdivision1" not in aids, blocking
    assert "addressSection_city" in aids, blocking


def test_county_not_in_dom_after_illinois_is_optional_miss() -> None:
    """Illinois fixture has no county — poll must optional_miss, not FAIL."""
    from exp_workday_selectors import (
        _is_verified_fill,
        _maybe_fill_county_after_state,
    )
    from field_map import ADDRESS_COUNTY, ADDRESS_STATE

    async def _run(page):
        await page.keyboard.press("Escape")
        await page.locator("#state-btn").click()
        await page.locator(
            '[data-automation-id="promptOption"][data-automation-label="Illinois"]'
        ).click()
        row = await _maybe_fill_county_after_state(
            page,
            {ADDRESS_COUNTY: "Sangamon", ADDRESS_STATE: "IL"},
            report={"dummy": True},
        )
        assert row.get("optional_miss") is True, row
        assert row.get("reason") in ("not_in_dom", "not_visible"), row
        assert not _is_verified_fill(row)

    asyncio.run(_browser(FIXTURE, _run))


def test_required_empty_skips_phone_country_chip_on_nxp_fixture() -> None:
    """Pack gate: US +1 chip must drop Country Phone Code from required_empty."""
    from exp_workday_selectors import _required_empty_on_page
    from field_done import filter_required_empty_false_incomplete
    from field_map import PHONE_COUNTRY_CODE

    phone_fixture = ROOT / "gym/ats/cases/workday_nxp_phone_contact/form.html"

    async def _run(page):
        empties = await _required_empty_on_page(page)
        report = {
            "filled": [
                {
                    "type": PHONE_COUNTRY_CODE,
                    "automation_id": "countryPhoneCode",
                    "readback": "United States of America (+1)",
                    "verified": True,
                    "ok": True,
                }
            ]
        }
        filtered = await filter_required_empty_false_incomplete(
            page, report, empties
        )
        phone_c = [
            e
            for e in filtered
            if (
                "country" in str(e.get("label") or e.get("id") or "").lower()
                and "phone" in str(e.get("label") or e.get("id") or "").lower()
            )
            or "countryphonecode" in str(e.get("id") or "").lower().replace(" ", "")
        ]
        assert not phone_c, (empties, filtered)

    asyncio.run(_browser(phone_fixture, _run))


if __name__ == "__main__":
    test_expand_state_illinois_before_il()
    test_field_done_state_il_matches_illinois_readback()
    test_commit_fill_never_verifies_intent_as_readback()
    test_click_matching_option_prompt_without_role()
    test_fill_country_region_illinois_with_how_heard_open()
    test_live_field_is_done_state_after_commit()
    test_state_path_keeps_prompt_option_not_fiber_text()
    test_select_one_uses_mcp_scroll_and_no_fos_cascade()
    test_already_illinois_chip_skips_rewrite()
    test_county_fill_plan_is_combobox_not_fiber_text()
    test_contact_core_required_excludes_apt_and_absent_county()
    test_county_not_in_dom_after_illinois_is_optional_miss()
    test_required_empty_skips_phone_country_chip_on_nxp_fixture()
    print("test_workday_address_state: OK")
