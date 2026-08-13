#!/usr/bin/env python3
"""Playwright HTML fixture: field lock + page-complete → Next once.

Dummy DOM only — no live ATS, never submit.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CONTACT_HTML = """
<html><body>
  <div data-automation-id="contactInformationPage">
    <div data-automation-id="formField-legalNameSection_firstName">
      <label>First Name*</label>
      <input data-automation-id="legalNameSection_firstName" aria-required="true" value="" />
    </div>
    <div data-automation-id="formField-how_heard">
      <label>How Did You Hear About Us?*</label>
      <input data-automation-id="how_heard" role="combobox" value="1 item selected, Indeed" />
      <div>1 item selected, Indeed</div>
    </div>
    <button data-automation-id="bottom-navigation-next-button">Save and Continue</button>
  </div>
</body></html>
"""

INCOMPLETE_HTML = """
<html><body>
  <div data-automation-id="contactInformationPage">
    <input data-automation-id="legalNameSection_firstName" aria-required="true" value="" />
    <button data-automation-id="bottom-navigation-next-button">Next</button>
  </div>
</body></html>
"""


async def _run() -> None:
    from playwright.async_api import async_playwright
    from field_lock import (
        attach_field_locks,
        clear_locks_on_advance,
        get_field_locks,
        lock_verified_field,
    )
    from page_progress import (
        attach_footer_primary,
        footer_primary_wizard_incomplete,
        may_enter_review_hold,
    )
    from exp_workday_selectors import _fill_automation_id, _is_verified_fill

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(CONTACT_HTML)

        report: dict = {"platform": "workday", "coverage_path": "workday_multipage"}
        attach_field_locks(report)

        # First fill locks
        r1 = await _fill_automation_id(
            page,
            "legalNameSection_firstName",
            "Ada",
            combobox=False,
            report=report,
        )
        assert _is_verified_fill(r1), r1
        lock_verified_field(
            report,
            r1,
            field_type="NAME_FIRST",
            automation_id="legalNameSection_firstName",
            via="fixture",
        )
        assert get_field_locks(report).is_locked(
            field_type="NAME_FIRST", automation_id="legalNameSection_firstName"
        )

        # Re-entry filtered: pack-style is_locked skip (no thrash)
        sess = get_field_locks(report)
        assert sess is not None
        assert sess.is_locked(field_type="NAME_FIRST", automation_id="legalNameSection_firstName")
        # Safety-net gate would thrash — pack must filter first
        before_thrash = sess.thrash_retouches
        # Simulate mistaken re-call without filter:
        r2 = await _fill_automation_id(
            page,
            "legalNameSection_firstName",
            "Ada",
            combobox=False,
            report=report,
        )
        assert r2.get("skipped_locked") or r2.get("reason") == "field_locked_skip", r2
        assert sess.thrash_retouches == before_thrash + 1

        # How-heard already committed → lock; alias walk dies
        lock_verified_field(
            report,
            field_type="HOW_HEARD",
            automation_id="how_heard",
            readback="1 item selected, Indeed",
            via="fixture",
        )
        from exp_workday_selectors import _fill_how_heard

        hh = await _fill_how_heard(
            page, {"HOW_HEARD": "Internet job board"}, report=report
        )
        assert hh.get("skipped_locked") or hh.get("reason") == "field_locked_skip", hh

        # Page complete + ADVANCE → never review-hold (live gate is
        # fill_contract / can_claim_ready, not a fourth boolean helper).
        attach_footer_primary(report, kind="ADVANCE", label="Save and Continue")
        assert footer_primary_wizard_incomplete("ADVANCE", "Save and Continue") is True
        assert may_enter_review_hold(report) is False

        # Incomplete required empties: wizard still ADVANCE, not review-hold
        await page.set_content(INCOMPLETE_HTML)
        assert footer_primary_wizard_incomplete("ADVANCE", "Next") is True

        clear_locks_on_advance(report)
        assert not sess.is_locked(
            field_type="NAME_FIRST", automation_id="legalNameSection_firstName"
        )

        await browser.close()
    print("test_field_lock_fixture: OK")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
