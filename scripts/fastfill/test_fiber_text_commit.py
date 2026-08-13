#!/usr/bin/env python3
"""Fiber native-setter + __reactProps$.onChange for stubborn Workday TEXT.

Dummy-only. Never submit. Models NXP addressLine2 empty_readback.
County is combobox (promptOption), not stubborn fiber text.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

DUMMY_ADDR2 = "Apt 1A"
DUMMY_COUNTY = "Sangamon"

# Fiber-only commit: DOM may show typed text briefly, but React state only
# updates when __reactProps$.onChange is invoked (NXP addressLine2).
REACT_STUBBORN_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>stubborn-fiber</title></head>
<body>
  <label for="addr2">Address Line 2*</label>
  <input id="addr2" data-automation-id="addressSection_addressLine2"
         name="addressLine2" value="" />
  <div id="log"></div>
  <script>
  (function () {
    const el = document.getElementById('addr2');
    let state = '';
    const desc = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype, 'value');
    const paint = (v) => {
      if (desc && desc.set) desc.set.call(el, v);
      else el.value = v;
    };
    const commitFiber = (v, why) => {
      state = String(v || '');
      paint(state);
      document.getElementById('log').textContent = why + ':' + state;
    };
    el.__reactProps$exp = {
      onChange: function (e) {
        const v = (e && e.target && e.target.value != null)
          ? e.target.value : (el.value || '');
        commitFiber(v, 'fiber_onChange');
      },
      onBlur: function () { paint(state); }
    };
    setInterval(function () {
      if (el.value !== state) paint(state);
    }, 40);
    el.addEventListener('input', function () { /* ignore for state */ });
    el.addEventListener('change', function () { /* ignore for state */ });
    el.addEventListener('blur', function () { paint(state); });
    window.__reactState = () => state;
    window.__fiberCommit = (v) => {
      paint(v);
      el.__reactProps$exp.onChange({ target: el, currentTarget: el });
    };
  })();
  </script>
</body></html>
"""

REACT_SOFT_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>soft-react</title></head>
<body>
  <label for="county">County*</label>
  <input id="county" data-automation-id="addressSection_regionSubdivision1"
         name="regionSubdivision1" value="" />
  <script>
  (function () {
    const el = document.getElementById('county');
    let state = '';
    const setState = (v) => {
      state = String(v || '');
      const desc = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype, 'value');
      if (desc && desc.set) desc.set.call(el, state);
      else el.value = state;
    };
    el.addEventListener('input', () => setState(el.value));
    el.addEventListener('change', () => setState(el.value));
    setInterval(() => {
      if (el.value !== state) {
        const desc = Object.getOwnPropertyDescriptor(
          HTMLInputElement.prototype, 'value');
        if (desc && desc.set) desc.set.call(el, state);
        else el.value = state;
      }
    }, 30);
    window.__reactState = () => state;
  })();
  </script>
</body></html>
"""


async def _page_html(html: str, fn) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        await fn(page)
        await browser.close()


def test_fiber_text_commit_js_uses_native_setter_and_react_props():
    from verified_select import _FIBER_TEXT_COMMIT_JS, fiber_text_commit

    src = _FIBER_TEXT_COMMIT_JS
    assert "__reactProps" in src
    assert "onChange" in src
    assert "getOwnPropertyDescriptor" in src
    assert "_valueTracker" in src
    assert "fiber_text_commit" in src
    assert "KeyboardEvent" in src
    assert "Tab" in src
    assert callable(fiber_text_commit)


def test_is_stubborn_text_field_addr2_not_state_or_county():
    from verified_select import is_stubborn_text_field

    assert is_stubborn_text_field(
        automation_id="addressSection_addressLine2", field_type="ADDRESS_LINE2"
    )
    assert is_stubborn_text_field(
        automation_id="addressSection_addressLine1", field_type="ADDRESS_LINE1"
    )
    assert not is_stubborn_text_field(
        automation_id="addressSection_regionSubdivision1",
        field_type="ADDRESS_COUNTY",
    )
    assert not is_stubborn_text_field(
        selector='[data-automation-id="formField-county"] input'
    )
    assert not is_stubborn_text_field(
        automation_id="addressSection_countryRegion", field_type="ADDRESS_STATE"
    )
    assert not is_stubborn_text_field(
        automation_id="addressSection_country", field_type="ADDRESS_COUNTRY"
    )


def test_workday_text_fill_calls_fiber_commit():
    import exp_workday_selectors as wd
    import fast_fill as ff

    src = inspect.getsource(wd._fill_automation_id_impl)
    assert "fill_text_fiber_then_read" in src
    assert "is_stubborn_text_field" in src
    sel_src = inspect.getsource(ff._fill_selector)
    assert "fill_text_fiber_then_read" in sel_src
    assert "is_stubborn_text_field" in sel_src
    assert "addressSection_addressLine2" in wd.WD_CONTACT_SELECTORS


def test_playwright_fill_empty_readback_on_stubborn_fiber():
    """Baseline: loc.fill() is wiped by fiber re-render (NXP empty_readback)."""

    async def _run(page):
        loc = page.locator("#addr2").first
        await loc.fill(DUMMY_ADDR2)
        await page.wait_for_timeout(150)
        rs = str(await page.evaluate("() => window.__reactState()") or "")
        assert rs == "", rs

    asyncio.run(_page_html(REACT_STUBBORN_HTML, _run))


def test_fiber_text_commit_survives_stubborn_rerender():
    from verified_select import fiber_text_commit

    async def _run(page):
        loc = page.locator("#addr2").first
        detail = await fiber_text_commit(loc, DUMMY_ADDR2)
        assert detail.get("ok"), detail
        assert detail.get("fiber_onChange") is True, detail
        await page.wait_for_timeout(150)
        rb = await loc.input_value()
        rs = await page.evaluate("() => window.__reactState()")
        assert DUMMY_ADDR2 in (rb or ""), (rb, detail)
        assert DUMMY_ADDR2 in (rs or ""), (rs, detail)

    asyncio.run(_page_html(REACT_STUBBORN_HTML, _run))


def test_fill_text_fiber_then_read_stubborn_addr2():
    from verified_select import fill_text_fiber_then_read

    async def _run(page):
        loc = page.locator("#addr2").first
        out = await fill_text_fiber_then_read(
            loc, DUMMY_ADDR2, stubborn=True, page=page
        )
        assert out.get("fiber_onChange") is True, out
        rb = await loc.input_value()
        rs = await page.evaluate("() => window.__reactState()")
        assert DUMMY_ADDR2 in (rb or "") and DUMMY_ADDR2 in (rs or ""), (rb, rs, out)

    asyncio.run(_page_html(REACT_STUBBORN_HTML, _run))


def test_fill_selector_addr2_stubborn_via_contract():
    """_fill_selector must commit fiber text and still go through Tech10."""
    from field_map import ADDRESS_LINE2
    from fast_fill import _fill_selector

    async def _run(page):
        report: dict = {"dummy": True, "never_submit": True}
        row = await _fill_selector(
            page,
            '[data-automation-id="addressSection_addressLine2"]',
            ADDRESS_LINE2,
            DUMMY_ADDR2,
            mode="fill",
            report=report,
        )
        assert row.get("verified") or row.get("ok"), row
        assert DUMMY_ADDR2 in str(row.get("readback") or ""), row
        rs = await page.evaluate("() => window.__reactState()")
        assert DUMMY_ADDR2 in (rs or ""), (rs, row)

    asyncio.run(_page_html(REACT_STUBBORN_HTML, _run))


def test_workday_automation_id_county_soft_react():
    from exp_workday_selectors import _fill_automation_id

    async def _run(page):
        report: dict = {"dummy": True, "never_submit": True}
        row = await _fill_automation_id(
            page,
            "addressSection_regionSubdivision1",
            DUMMY_COUNTY,
            combobox=False,
            report=report,
        )
        assert row.get("verified") or row.get("status") == "filled", row
        assert DUMMY_COUNTY.lower() in str(row.get("readback") or "").lower(), row

    asyncio.run(_page_html(REACT_SOFT_HTML, _run))


def test_workday_automation_id_addr2_stubborn():
    from exp_workday_selectors import _fill_automation_id

    async def _run(page):
        report: dict = {"dummy": True, "never_submit": True}
        row = await _fill_automation_id(
            page,
            "addressSection_addressLine2",
            DUMMY_ADDR2,
            combobox=False,
            report=report,
        )
        assert row.get("verified") or row.get("status") == "filled", row
        assert DUMMY_ADDR2 in str(row.get("readback") or ""), row
        rs = await page.evaluate("() => window.__reactState()")
        assert DUMMY_ADDR2 in (rs or ""), (rs, row)
        assert row.get("fiber_onChange") or row.get("algorithm") == "fiber_text_commit", row

    asyncio.run(_page_html(REACT_STUBBORN_HTML, _run))


if __name__ == "__main__":
    test_fiber_text_commit_js_uses_native_setter_and_react_props()
    test_is_stubborn_text_field_addr2_not_state_or_county()
    test_workday_text_fill_calls_fiber_commit()
    test_playwright_fill_empty_readback_on_stubborn_fiber()
    test_fiber_text_commit_survives_stubborn_rerender()
    test_fill_text_fiber_then_read_stubborn_addr2()
    test_fill_selector_addr2_stubborn_via_contract()
    test_workday_automation_id_county_soft_react()
    test_workday_automation_id_addr2_stubborn()
    print("test_fiber_text_commit: OK")
