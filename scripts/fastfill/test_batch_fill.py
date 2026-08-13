#!/usr/bin/env python3
"""Unit + headed-off Playwright tests: Layer 0/1 vanilla batch fill.

Dummy-only; never submit. No real PII.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from batch_fill import (  # noqa: E402
    BATCH_SKIP_TYPES,
    batch_fill_simple,
    batch_result_verified,
    is_batchable_row,
    normalize_batch_mode,
    selector_is_batch_safe,
)
from field_map import EMAIL, NAME_FIRST, NAME_LAST, PHONE  # noqa: E402


def test_batch_skips_widgets():
    assert not is_batchable_row(
        {"selector": "input", "value": "Indeed", "type": "HOW_HEARD"}
    )
    assert not is_batchable_row(
        {"selector": "input", "value": "CS", "type": "FIELD_OF_STUDY"}
    )
    assert not is_batchable_row(
        {"selector": "input", "value": "United States (+1)", "type": "PHONE_COUNTRY_CODE"}
    )
    assert not is_batchable_row(
        {"selector": "input", "value": "2020", "type": "EDUCATION_START_YEAR"}
    )
    assert not is_batchable_row(
        {
            "selector": "[data-automation-id='school']",
            "value": "Alabama",
            "type": "SCHOOL",
            "mode": "searchSelect",
        }
    )
    assert not is_batchable_row(
        {"selector": "input", "value": "x", "type": "EMAIL", "mode": "combobox"}
    )
    assert not is_batchable_row(
        {"selector": "input[type=file]", "value": "r.pdf", "type": "RESUME_UPLOAD", "mode": "file"}
    )
    assert not is_batchable_row(
        {"selector": "input", "value": "No", "type": "SPONSORSHIP", "mode": "radio"}
    )
    assert not selector_is_batch_safe(
        "label:has-text('LinkedIn') >> xpath=following::input[1]"
    )
    assert "PHONE_DEVICE" in BATCH_SKIP_TYPES
    assert "DEGREE" in BATCH_SKIP_TYPES


def test_batchable_email_name():
    assert is_batchable_row(
        {"selector": "#email", "value": "dummy@example.com", "type": EMAIL, "mode": "fill"}
    )
    assert is_batchable_row(
        {"selector": "#first_name", "value": "Test", "type": NAME_FIRST}
    )
    assert is_batchable_row(
        {"selector": "select[name=eeo]", "value": "Male", "type": "GENDER", "mode": "select"}
    )
    assert is_batchable_row(
        {
            "selector": "input[type=checkbox]",
            "value": "Yes",
            "type": "TERMS_CONSENT",
            "mode": "checkbox",
        }
    )
    assert normalize_batch_mode("fill") == "text"


def test_batch_result_empty_readback_not_success():
    plan = {"selector": "#a", "value": "Apt 1A", "type": "ADDRESS_LINE2", "mode": "text"}
    assert batch_result_verified(plan, {"selector": "#a", "ok": True, "readback": ""}) is False
    assert (
        batch_result_verified(plan, {"selector": "#a", "ok": False, "reason": "empty_readback"})
        is False
    )
    assert (
        batch_result_verified(plan, {"selector": "#a", "ok": True, "readback": "Apt 1A"})
        is True
    )


def test_batch_lock_skip_helper():
    from fast_fill import _pack_item_locked
    from field_lock import attach_field_locks, lock_verified_field

    report: dict = {}
    attach_field_locks(report)
    lock_verified_field(
        report,
        {"type": EMAIL, "ok": True, "verified": True, "readback": "locked@example.com"},
        field_type=EMAIL,
        selector="#email",
        via="test",
    )
    assert _pack_item_locked(report, EMAIL, "#email", None) is True
    assert _pack_item_locked(report, NAME_FIRST, "#first_name", None) is False
    assert _pack_item_locked(None, EMAIL, "#email", None) is False


def test_batch_fallback_on_miss_unit():
    plan = {"selector": "#missing", "value": "Test", "type": NAME_FIRST, "mode": "text"}
    assert batch_result_verified(plan, None) is False
    assert batch_result_verified(plan, {"ok": False, "reason": "not_found"}) is False
    assert (
        batch_result_verified(
            plan, {"selector": "#missing", "ok": False, "reason": "batch_error:boom"}
        )
        is False
    )


async def _browser(html: str, fn) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        await fn(page)
        await browser.close()


def test_batch_fill_simple_snaps_vanilla():
    html = """
    <form>
      <input id="first_name" />
      <input id="email" type="email" />
      <select id="how"><option>Select</option><option>Indeed</option></select>
    </form>
    """

    async def _run(page):
        plan = [
            {"selector": "#first_name", "value": "Test", "type": NAME_FIRST, "mode": "text"},
            {"selector": "#email", "value": "dummy@example.com", "type": EMAIL, "mode": "text"},
            {"selector": "#how", "value": "Indeed", "type": "HOW_HEARD", "mode": "select"},
        ]
        rows = [r for r in plan if is_batchable_row(r)]
        assert len(rows) == 2
        results = await batch_fill_simple(page, plan)
        by = {r["selector"]: r for r in results}
        assert by["#first_name"]["ok"] is True
        assert by["#first_name"]["readback"] == "Test"
        assert by["#email"]["ok"] is True
        assert await page.locator("#first_name").input_value() == "Test"
        assert await page.locator("#email").input_value() == "dummy@example.com"
        assert "#how" not in by
        shown = await page.locator("#how").evaluate(
            "el => (el.options[el.selectedIndex] || {}).text || ''"
        )
        assert "Indeed" not in str(shown)

    asyncio.run(_browser(html, _run))


def test_apply_selector_pack_batches_and_lock_skip():
    from fast_fill import apply_selector_pack
    from field_lock import attach_field_locks, lock_verified_field

    html = """
    <form>
      <input id="first_name" />
      <input id="last_name" />
      <input id="email" type="email" />
      <input id="phone" type="tel" />
    </form>
    """
    values = {
        NAME_FIRST: "Test",
        NAME_LAST: "Dummy",
        EMAIL: "dummy@example.com",
        PHONE: "405-555-0100",
    }

    async def _run(page):
        report: dict = {}
        attach_field_locks(report)
        lock_verified_field(
            report,
            {"type": EMAIL, "ok": True, "verified": True, "readback": "locked@example.com"},
            field_type=EMAIL,
            selector="#email",
            via="prior_layer",
        )
        await page.locator("#email").fill("locked@example.com")
        filled = await apply_selector_pack(page, "greenhouse", values, report=report)
        types = {r.get("type") for r in filled}
        assert NAME_FIRST in types
        assert NAME_LAST in types
        assert await page.locator("#first_name").input_value() == "Test"
        assert await page.locator("#last_name").input_value() == "Dummy"
        assert await page.locator("#email").input_value() == "locked@example.com"
        assert EMAIL not in types

    asyncio.run(_browser(html, _run))


def test_apply_selector_pack_fallback_empty_readback_calls_fill_selector():
    """Batch miss must call sequential _fill_selector (not claim success)."""
    import batch_fill as bf
    import fast_fill as ff
    from fast_fill import apply_selector_pack

    html = """
    <form>
      <input id="first_name" />
      <input id="last_name" />
      <input id="email" type="email" />
      <input id="phone" type="tel" />
    </form>
    """
    values = {
        NAME_FIRST: "Test",
        NAME_LAST: "Dummy",
        EMAIL: "dummy@example.com",
        PHONE: "405-555-0100",
    }
    calls: list[str] = []

    async def _run(page):
        real_fill = ff._fill_selector

        async def _wrap(page, sel, ftype, value, *, mode="fill", report=None):
            calls.append(sel)
            return await real_fill(page, sel, ftype, value, mode=mode, report=report)

        async def _fake_batch(page, plan):
            return [
                {
                    "selector": r["selector"],
                    "ok": False,
                    "reason": "empty_readback",
                    "readback": "",
                }
                for r in plan
            ]

        saved_bf = bf.batch_fill_simple
        ff._fill_selector = _wrap  # type: ignore[method-assign]
        bf.batch_fill_simple = _fake_batch
        try:
            await apply_selector_pack(page, "greenhouse", values)
        finally:
            ff._fill_selector = real_fill  # type: ignore[method-assign]
            bf.batch_fill_simple = saved_bf

        assert "#first_name" in calls
        assert "#email" in calls
        assert await page.locator("#first_name").input_value() == "Test"

    asyncio.run(_browser(html, _run))


def test_skip_ashby_location_zip_on_workday():
    """0842: never run ashby_location_zip on Workday (postalCode already filled)."""
    from fast_fill import skip_ashby_location_zip
    import inspect

    assert skip_ashby_location_zip("workday")
    assert skip_ashby_location_zip(
        "unknown", {"platform": "workday", "url": "https://nxp.wd3.myworkdayjobs.com/x"}
    )
    assert skip_ashby_location_zip(
        "", {"url": "https://nxp.wd3.myworkdayjobs.com/en-US/careers"}
    )
    assert not skip_ashby_location_zip("ashby")
    assert not skip_ashby_location_zip("greenhouse")
    demote_src = inspect.getsource(__import__("fast_fill")._demote_filled_against_required_empty)
    assert "skip_ashby_location_zip" in demote_src
    pack_src = inspect.getsource(__import__("fast_fill").apply_selector_pack)
    assert "skip_ashby_location_zip" in pack_src


if __name__ == "__main__":
    failed = 0
    for name in [n for n in dir() if n.startswith("test_")]:
        try:
            globals()[name]()
            print("OK", name)
        except Exception as e:
            print("FAIL", name, type(e).__name__, e)
            failed += 1
    raise SystemExit(failed)
