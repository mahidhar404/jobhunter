#!/usr/bin/env python3
"""Workday radio completion / hold-incomplete false-positive guards.

Owens & Minor class: previously-employed Yes/No answered via aria-checked while
native input.checked stays false; sibling "CURRENT TEAMMATES…" instruction must
not block Ready / hold.

Dummy HTML only — never submit.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Owens & Minor pattern: No selected via aria-checked; instruction sibling text.
OWENS_MINOR_PREVIOUS_WORKER_HTML = """
<html><head><style>
  body { font-family: sans-serif; padding: 16px; }
  label { display: block; margin: 8px 0; }
  input[type=radio] { width: 16px; height: 16px; vertical-align: middle; }
</style></head><body>
  <div data-automation-id="formField-previousWorker" aria-required="true">
    <legend>Have you previously been employed by O&amp;M?*</legend>
    <p>CURRENT TEAMMATES: Please apply via your internal career site.</p>
    <label>
      <input type="radio" name="candidateIsPreviousWorker" value="true"
             aria-required="true" /> Yes
    </label>
    <label>
      <input type="radio" name="candidateIsPreviousWorker" value="false"
             aria-required="true" aria-checked="true" /> No
    </label>
  </div>
  <div data-automation-id="formField-source">
    <label>How Did You Hear About Us?*</label>
    <span>Corporate Website</span>
  </div>
</body></html>
"""

# Unanswered — should still flag.
PREVIOUS_WORKER_UNANSWERED_HTML = """
<html><head><style>
  body { font-family: sans-serif; padding: 16px; }
  label { display: block; margin: 8px 0; }
  input[type=radio] { width: 16px; height: 16px; vertical-align: middle; }
</style></head><body>
  <div data-automation-id="formField-previousWorker">
    <legend>Have you previously been employed by O&amp;M?*</legend>
    <p>CURRENT TEAMMATES: Please apply via your internal career site.</p>
    <label><input type="radio" name="candidateIsPreviousWorker" value="true"
                   aria-required="true" /> Yes</label>
    <label><input type="radio" name="candidateIsPreviousWorker" value="false"
                   aria-required="true" /> No</label>
  </div>
</body></html>
"""

INSTRUCTION_ONLY_HTML = """
<html><body>
  <div data-automation-id="formField-previousWorker" aria-required="true">
    <p>CURRENT TEAMMATES: Please apply via your internal career site only.</p>
  </div>
</body></html>
"""


async def _eval_html(html: str, js: str) -> list:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        out = await page.evaluate(js)
        await browser.close()
        return out if isinstance(out, list) else []


def test_form_gaps_no_false_positive_when_no_selected():
    from form_gaps import COLLECT_GAPS_JS, collect_form_gaps, gaps_block_ready, normalize_gaps

    async def _run():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(OWENS_MINOR_PREVIOUS_WORKER_HTML)
            gaps = await collect_form_gaps(page)
            await browser.close()
            return gaps

    gaps = asyncio.run(_run())
    assert gaps == [], gaps
    assert gaps_block_ready(gaps) is False

    # Bare formField[aria-required] must not become a required_empty gap.
    raw = asyncio.run(_eval_html(INSTRUCTION_ONLY_HTML, COLLECT_GAPS_JS))
    norm = normalize_gaps(raw)
    assert norm == [], norm


def test_leftover_miss_scan_skips_answered_workday_radio():
    from leftover_miss_scan import UNANSWERED_CHOICE_JS, promote_l01_misses

    misses = asyncio.run(_eval_html(OWENS_MINOR_PREVIOUS_WORKER_HTML, UNANSWERED_CHOICE_JS))
    assert misses == [], misses

    async def _promote():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(OWENS_MINOR_PREVIOUS_WORKER_HTML)
            report = {
                "filled": [
                    {
                        "type": "WORKED_HERE_BEFORE",
                        "automation_id": "worked_here_before",
                        "readback": "No",
                        "verified": True,
                    }
                ],
                "leftovers": [],
            }
            summary = await promote_l01_misses(page, report)
            await browser.close()
            return report, summary

    report, summary = asyncio.run(_promote())
    assert summary["added"] == 0
    assert report.get("required_empty_after_fill") in (None, [])


def test_unanswered_previous_worker_still_detected():
    from leftover_miss_scan import UNANSWERED_CHOICE_JS

    misses = asyncio.run(_eval_html(PREVIOUS_WORKER_UNANSWERED_HTML, UNANSWERED_CHOICE_JS))
    assert misses, misses
    labels = " ".join(str(m.get("label") or "") for m in misses).lower()
    assert "previously" in labels and "employed" in labels
    assert "current teammates" not in labels


def test_instruction_gap_filter():
    from form_gaps import is_instruction_only_gap, normalize_gaps

    assert is_instruction_only_gap("CURRENT TEAMMATES: Please apply via your site") is True
    assert is_instruction_only_gap("Please apply via internal career site") is True
    assert (
        is_instruction_only_gap("Have you previously been employed by O&M?*")
        is False
    )

    norm = normalize_gaps(
        [
            {
                "label": "CURRENT TEAMMATES: Please apply via y…",
                "reason": "required_empty",
            },
            {
                "label": "Email is required",
                "reason": "alert_node",
            },
        ]
    )
    assert not any("teammates" in g["label"].lower() for g in norm)


def test_can_claim_ready_with_verified_worked_here_no_gaps():
    from page_progress import can_claim_ready

    report = {
        "verdict": "SUCCESS",
        "advanced_incomplete": False,
        "validation_after_advance": None,
        "required_empty_before_advance": [],
        "required_empty_after_fill": [],
        "gaps_after_save": [],
        "leftovers": [],
        "vision_judge_live": {"complete": True, "verdict": "PASS"},
        "footer_primary_kind": "FINAL",
        "filled": [
            {
                "type": "WORKED_HERE_BEFORE",
                "automation_id": "worked_here_before",
                "readback": "No",
                "verified": True,
            }
        ],
    }
    assert can_claim_ready(report) is True


# Sandoz pattern: "ever been employed by" + aria-checked on No (input.checked false).
SANDOZ_PREVIOUS_WORKER_HTML = """
<html><head><style>
  body { font-family: sans-serif; padding: 16px; }
  label { display: block; margin: 8px 0; }
  input[type=radio] { width: 16px; height: 16px; vertical-align: middle; }
</style></head><body>
  <div data-automation-id="formField-candidateIsPreviousWorker" aria-required="true">
    <legend>Have you ever been employed by a Sandoz Company?*</legend>
    <label>
      <input type="radio" name="candidateIsPreviousWorker" value="true"
             aria-required="true" /> Yes
    </label>
    <label>
      <input type="radio" name="candidateIsPreviousWorker" value="false"
             aria-required="true" aria-checked="true" /> No
    </label>
  </div>
</body></html>
"""


def test_sandoz_employed_by_label_classifies_worked_here():
    from field_map import WORKED_HERE_BEFORE, classify_field, is_worked_here_label

    lab = "Have you ever been employed by a Sandoz Company?*"
    assert is_worked_here_label(lab) is True
    ftype, _ = classify_field({"label": lab, "name": "", "id": ""})
    assert ftype == WORKED_HERE_BEFORE, ftype


def test_form_gaps_sandoz_no_false_positive():
    from form_gaps import collect_form_gaps, gaps_block_ready

    async def _run():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(SANDOZ_PREVIOUS_WORKER_HTML)
            gaps = await collect_form_gaps(page)
            await browser.close()
            return gaps

    gaps = asyncio.run(_run())
    assert gaps == [], gaps
    assert gaps_block_ready(gaps) is False


def test_field_lock_singleton_blocks_sandoz_flash_revisit():
    from field_lock import attach_field_locks, gate_field_action, lock_verified_field

    report: dict = {}
    attach_field_locks(report)
    lock_verified_field(
        report,
        {
            "type": "WORKED_HERE_BEFORE",
            "automation_id": "worked_here_before",
            "readback": "No",
            "verified": True,
            "via": "workday_contact_pack",
        },
        field_type="WORKED_HERE_BEFORE",
        automation_id="worked_here_before",
    )
    g = gate_field_action(
        report,
        field_type=None,
        label="Have you ever been employed by a Sandoz Company?*",
        selector='input[name="candidateIsPreviousWorker"][value="true"]',
    )
    assert g is not None
    assert g.get("action") == "lock_skip", g
    assert int(g.get("thrash_retouches") or 0) >= 1


def main() -> int:
    test_form_gaps_no_false_positive_when_no_selected()
    test_leftover_miss_scan_skips_answered_workday_radio()
    test_unanswered_previous_worker_still_detected()
    test_instruction_gap_filter()
    test_can_claim_ready_with_verified_worked_here_no_gaps()
    test_sandoz_employed_by_label_classifies_worked_here()
    test_form_gaps_sandoz_no_false_positive()
    test_field_lock_singleton_blocks_sandoz_flash_revisit()
    print("test_workday_radio_completion: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
