#!/usr/bin/env python3
"""Unit tests for page-complete gate (FAIL-before-ADVANCE).

Dummy HTML only — no live ATS, never submit.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from workday_selectors import (  # noqa: E402
    REQUIRED_EMPTY_JS,
    _advance_block_reason,
)


PRESENT_CHECKED_HTML = """
<html><body>
  <div data-automation-id="workExperience-1">
    <input name="currentlyWorkHere" type="checkbox" checked />
    <div data-automation-id="formField-endDate">
      <span data-automation-id="dateSectionMonth-display">MM</span>
      <span data-automation-id="dateSectionYear-display">YYYY</span>
      <input data-automation-id="dateSectionMonth-input" aria-required="true" value="" />
      <input data-automation-id="dateSectionYear-input" aria-required="true" value="" />
    </div>
    <div data-automation-id="formField-startDate">
      <span data-automation-id="dateSectionMonth-display" aria-label="Month — From*">01</span>
      <span data-automation-id="dateSectionYear-display" aria-label="Year — From*">2022</span>
      <input data-automation-id="dateSectionMonth-input" aria-label="Month — From*"
             aria-required="true" value="01" />
      <input data-automation-id="dateSectionYear-input" aria-label="Year — From*"
             aria-required="true" value="2022" />
    </div>
  </div>
  <button data-automation-id="bottom-navigation-next-button">Save and Continue</button>
</body></html>
"""

COMPLETE_DATES_HTML = """
<html><body>
  <div data-automation-id="workExperience-1">
    <input name="currentlyWorkHere" type="checkbox" />
    <div data-automation-id="formField-startDate">
      <span data-automation-id="dateSectionMonth-display" aria-label="Month — From*">01</span>
      <span data-automation-id="dateSectionYear-display" aria-label="Year — From*">2022</span>
      <input data-automation-id="dateSectionMonth-input" aria-label="Month — From*"
             aria-required="true" value="01" />
      <input data-automation-id="dateSectionYear-input" aria-label="Year — From*"
             aria-required="true" value="2022" />
    </div>
    <div data-automation-id="formField-endDate">
      <span data-automation-id="dateSectionMonth-display" aria-label="Month">06</span>
      <span data-automation-id="dateSectionYear-display" aria-label="Year">2023</span>
      <input data-automation-id="dateSectionMonth-input" aria-label="Month"
             aria-required="true" value="06" />
      <input data-automation-id="dateSectionYear-input" aria-label="Year"
             aria-required="true" value="2023" />
    </div>
  </div>
</body></html>
"""


def test_advance_block_reason_mapping() -> None:
    assert (
        _advance_block_reason([{"id": "x", "reason": "currently_work_here_checked"}])
        == "currently_work_here_checked"
    )
    assert (
        _advance_block_reason([{"id": "x", "reason": "empty_required_date_display"}])
        == "required_dates_empty"
    )
    assert (
        _advance_block_reason([{"id": "x", "reason": "empty_required_input"}])
        == "required_fields_empty"
    )


SELECT_ONE_HTML = """
<html><body>
  <div data-automation-id="formField-educationLevel">
    <label>Highest level of education completed?*</label>
    <button aria-haspopup="listbox" aria-required="true">Select One</button>
  </div>
  <div data-automation-id="formField-sponsor">
    <label>Will you require sponsorship?*</label>
    <button aria-haspopup="listbox">Select One</button>
  </div>
</body></html>
"""


async def _eval_html(html: str) -> list[dict]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        out = await page.evaluate(REQUIRED_EMPTY_JS)
        await browser.close()
        return out


def test_present_checked_blocks_advance() -> None:
    empties = asyncio.run(_eval_html(PRESENT_CHECKED_HTML))
    reasons = {e.get("reason") for e in empties}
    assert "currently_work_here_checked" in reasons, empties
    # To placeholders must also be flagged (no Present skip)
    assert reasons & {
        "empty_required_date_display",
        "empty_required_date_spin",
        "empty_required_date_field",
    }, empties
    assert _advance_block_reason(empties) == "currently_work_here_checked"


def test_complete_dates_allow_advance() -> None:
    empties = asyncio.run(_eval_html(COMPLETE_DATES_HTML))
    assert empties == [], empties


def test_select_one_empties_include_label() -> None:
    empties = asyncio.run(_eval_html(SELECT_ONE_HTML))
    assert empties, empties
    assert any(e.get("reason") == "empty_required_combobox" for e in empties), empties
    labels = " ".join(str(e.get("label") or "") for e in empties).lower()
    assert "education" in labels or "sponsorship" in labels or "sponsor" in labels, empties


def main() -> int:
    test_advance_block_reason_mapping()
    test_present_checked_blocks_advance()
    test_complete_dates_allow_advance()
    test_select_one_empties_include_label()
    print("test_page_complete_gate: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
