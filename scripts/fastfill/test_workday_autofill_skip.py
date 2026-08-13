#!/usr/bin/env python3
"""Unit/gym: Workday post-resume autofill — probe readback, skip thrash (dummy-only)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

NXP_FIXTURE = HERE / "gym/ats/cases/workday_nxp_phone_contact/form.html"


def test_value_matches_readback_prefilled_contact_fields():
    from verified_select import value_matches_readback

    assert value_matches_readback("Test", "Test")
    assert value_matches_readback("Test", "Test Candidate")
    assert value_matches_readback("405-555-0100", "4055550100", mode="phone")
    assert value_matches_readback("405-555-0100", "(405) 555-0100", mode="phone")
    assert not value_matches_readback("Test", "")
    assert not value_matches_readback("Test", "Type here...")


def test_lock_already_correct_skip_helper():
    from exp_workday_selectors import _lock_already_correct_skip

    report: dict = {}
    row = {
        "automation_id": "legalNameSection_firstName",
        "status": "filled",
        "reason": "already_correct_skip",
        "verified": True,
        "skipped_already_correct": True,
        "readback": "Test",
        "value": "Test",
    }
    out = _lock_already_correct_skip(
        report,
        row,
        automation_id="legalNameSection_firstName",
        field_type="NAME_FIRST",
    )
    assert out is row
    assert out.get("skipped_already_correct") is True


async def _read_combobox_on_html(html: str, sel: str) -> str:
    from playwright.async_api import async_playwright

    from verified_select import read_combobox_display

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        loc = page.locator(sel).first
        out = await read_combobox_display(loc)
        await browser.close()
        return out


def test_read_combobox_display_phone_country_chip():
    html = NXP_FIXTURE.read_text()
    snip = asyncio.run(
        _read_combobox_on_html(html, "#country-filter")
    )
    assert "United States" in snip
    assert "(+1)" in snip


def test_fill_automation_id_skips_prefilled_text():
    """Prefilled name/phone → already_correct_skip without fill()."""
    import exp_workday_selectors as wd

    fill_calls: list[str] = []

    class _Loc:
        def __init__(self, value: str):
            self._value = value

        async def count(self):
            return 1

        async def is_visible(self, timeout=0):
            return True

        async def evaluate(self, _js):
            return "input"

        async def get_attribute(self, name):
            if name == "role":
                return ""
            if name == "type":
                return "text"
            return ""

        async def input_value(self):
            return self._value

        def locator(self, _sel):
            return self

        async def fill(self, *_a, **_k):
            fill_calls.append("fill")

        async def scroll_into_view_if_needed(self):
            pass

        async def click(self, **_k):
            pass

    class _First:
        def __init__(self, loc=None):
            self._loc = loc

        async def count(self):
            return 1 if self._loc is not None else 0

        async def input_value(self):
            return self._loc._value if self._loc else ""

        async def get_attribute(self, name):
            if self._loc:
                return await self._loc.get_attribute(name)
            return ""

    class _Chain:
        def __init__(self, loc=None):
            self.first = _First(loc)

    class _Page:
        def locator(self, sel):
            for aid in ("legalNameSection_firstName", "phone-number"):
                if aid in sel:
                    val = "Test" if "firstName" in aid else "405-555-0100"
                    return _Chain(_Loc(val))
            return _Chain(None)

    async def fake_resolve(_page, automation_id):
        vals = {
            "legalNameSection_firstName": "Test",
            "phone-number": "405-555-0100",
        }
        return _Loc(vals.get(automation_id, "")), f'[data-automation-id="{automation_id}"]'

    async def fake_read(loc):
        if hasattr(loc, "_value"):
            return loc._value
        return await loc.input_value()

    async def run():
        page = _Page()
        with patch.object(wd, "_resolve_contact_locator", fake_resolve):
            with patch.object(wd, "_read_field_value", fake_read):
                for aid, val in (
                    ("legalNameSection_firstName", "Test"),
                    ("phone-number", "405-555-0100"),
                ):
                    r = await wd._fill_automation_id(
                        page, aid, val, combobox=False, report={}
                    )
                    assert r.get("reason") == "already_correct_skip", r
                    assert r.get("skipped_already_correct") is True

    asyncio.run(run())
    assert fill_calls == [], "must not call fill on matching autofill"


def main() -> int:
    test_value_matches_readback_prefilled_contact_fields()
    test_lock_already_correct_skip_helper()
    test_read_combobox_display_phone_country_chip()
    test_fill_automation_id_skips_prefilled_text()
    print("test_workday_autofill_skip: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
