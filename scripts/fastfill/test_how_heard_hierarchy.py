#!/usr/bin/env python3
"""Hierarchical Workday how-heard + false hold gates (fixture + unit).

Dummy DOM only — never live ATS, never submit.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# NXP-shaped hierarchy: open → Website > → Web - * leaf → chip
HIERARCHY_HTML = """
<html><body>
<nav aria-label="progress">
  Autofill completed My Information current My Experience Application Questions
  Voluntary Disclosures Review
</nav>
<div data-automation-id="contactInformationPage">
  <div data-automation-id="formField-source" id="hh-wrap">
    <label>How Did You Hear About Us?*</label>
    <div data-automation-id="multiSelectContainer">
      <span id="chip-chrome">0 items selected</span>
      <input id="hh-input" name="source--source" data-automation-id="source--source"
             role="combobox" aria-required="true" value="" />
    </div>
    <div id="menu" role="listbox" style="display:none; border:1px solid #ccc; padding:8px;">
      <div id="subsection-hdr" style="display:none;">
        <button id="back-btn" aria-label="back">←</button>
        <strong id="subsection-title">Website</strong>
      </div>
      <div id="top-opts"></div>
      <div id="sub-opts" style="display:none;"></div>
    </div>
  </div>
  <button data-automation-id="bottom-navigation-next-button">Save and Continue</button>
</div>
<script>
const CATEGORIES = ["Advertising", "Employee Referral", "Event", "Website", "Job Board"];
const WEB_LEAVES = ["Web - CareerBuilder", "Web - Craigslist", "Web - Indeed", "Web - LinkedIn"];
const BOARD_LEAVES = ["CareerBuilder", "Indeed", "LinkedIn"];
const input = document.getElementById("hh-input");
const menu = document.getElementById("menu");
const topOpts = document.getElementById("top-opts");
const subOpts = document.getElementById("sub-opts");
const subHdr = document.getElementById("subsection-hdr");
const subTitle = document.getElementById("subsection-title");
const chrome = document.getElementById("chip-chrome");

function renderTop() {
  topOpts.innerHTML = "";
  subOpts.style.display = "none";
  subHdr.style.display = "none";
  CATEGORIES.forEach(c => {
    const el = document.createElement("div");
    el.setAttribute("role", "option");
    el.setAttribute("data-automation-id", "promptOption");
    el.setAttribute("aria-expanded", "false");
    el.textContent = c + " >";
    el.onclick = () => openSub(c);
    topOpts.appendChild(el);
  });
  menu.style.display = "block";
}
function openSub(cat) {
  topOpts.innerHTML = "";
  subHdr.style.display = "block";
  subOpts.style.display = "block";
  subTitle.textContent = cat;
  subOpts.innerHTML = "";
  const leaves = cat === "Website" ? WEB_LEAVES : BOARD_LEAVES;
  leaves.forEach(l => {
    const el = document.createElement("div");
    el.setAttribute("role", "option");
    el.setAttribute("data-automation-id", "promptOption");
    el.textContent = l;
    el.onclick = () => pickLeaf(l);
    subOpts.appendChild(el);
  });
}
function pickLeaf(l) {
  chrome.textContent = "1 item selected, " + l;
  input.value = "1 item selected, " + l;
  menu.style.display = "none";
}
document.getElementById("back-btn").onclick = () => renderTop();
input.addEventListener("focus", renderTop);
input.addEventListener("click", renderTop);
</script>
</body></html>
"""


def test_category_helpers_and_candidates() -> None:
    from fill_verify import (
        how_heard_candidates,
        how_heard_category_candidates,
        how_heard_leaf_candidates,
        is_how_heard_category_option,
    )
    from verified_select import how_heard_source_committed

    assert is_how_heard_category_option("Internet job board")
    assert is_how_heard_category_option("Job Board")
    assert is_how_heard_category_option("Website >")
    assert not is_how_heard_category_option("Indeed")
    assert not is_how_heard_category_option("Web - LinkedIn")

    leaves = how_heard_leaf_candidates({"HOW_HEARD": "Internet job board"})
    assert leaves[0] == "LinkedIn"
    assert "Internet job board" not in leaves

    cands = how_heard_candidates({"HOW_HEARD": "Internet job board"})
    assert cands[0] == "LinkedIn"
    assert "Internet job board" in cands
    assert cands.index("LinkedIn") < cands.index("Internet job board")

    cats = how_heard_category_candidates({"HOW_HEARD": "Internet job board"})
    assert "Website" in cats
    assert "Internet job board" in cats

    # Category filter text alone is NOT committed
    assert not how_heard_source_committed("Internet job board", cands)
    assert not how_heard_source_committed("Job Board", cands)
    assert not how_heard_source_committed("0 items selected", cands)
    # Real chip is committed
    assert how_heard_source_committed("1 item selected, Indeed", cands)
    assert how_heard_source_committed("1 item selected, LinkedIn", ["Indeed", "LinkedIn"])


def test_false_hold_refused_on_advance_footer() -> None:
    from page_progress import (
        attach_footer_primary,
        may_enter_review_hold,
        workday_wizard_incomplete,
    )

    report = {
        "platform": "workday",
        "coverage_path": "workday_multipage",
        "workday_current_step": "contact",
        "workday_wizard_progress": (
            "Autofill completed My Information current My Experience "
            "Application Questions Voluntary Disclosures Review"
        ),
        "workday": {"phase_b": {"present": True, "advanced": False}, "phase_e": {}},
        "vision_judge_live": {"complete": True, "status": "COMPLETE"},
    }
    attach_footer_primary(report, kind="ADVANCE", label="Save and Continue")
    assert workday_wizard_incomplete(report) is True
    assert may_enter_review_hold(report) is False


async def _run_fixture() -> None:
    from playwright.async_api import async_playwright
    from exp_workday_selectors import _fill_how_heard, _is_verified_fill
    from page_progress import attach_footer_primary, may_enter_review_hold
    from verified_select import (
        fill_hierarchical_how_heard,
        how_heard_source_committed,
        listbox_still_open,
        settle_before_advance,
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(HIERARCHY_HTML)

        # Hierarchical helper alone
        inp = page.locator("#hh-input")
        hier = await fill_hierarchical_how_heard(
            page,
            inp,
            leaf_candidates=["LinkedIn", "Indeed"],
            category_candidates=["Website", "Job Board", "Internet job board"],
        )
        assert hier.get("ok") and hier.get("committed"), hier
        assert hier.get("path") == "category_then_leaf", hier
        assert "Website" in str(hier.get("subsection") or "")
        picked = str(hier.get("picked") or hier.get("readback") or "")
        assert "linkedin" in picked.lower(), hier
        chrome = await page.locator("#chip-chrome").inner_text()
        assert how_heard_source_committed(chrome, ["LinkedIn"])
        assert "1 item selected" in chrome.lower()

        menu_disp = await page.locator("#menu").evaluate(
            "el => getComputedStyle(el).display"
        )
        assert menu_disp == "none", menu_disp
        assert not await listbox_still_open(page)
        settle0 = await settle_before_advance(
            page, {"fill_values": {"HOW_HEARD": "LinkedIn"}}
        )
        assert settle0.get("settled"), settle0
        assert not await listbox_still_open(page)

        # Fresh page — Playwright set_content twice on one page drops inline JS
        page2 = await browser.new_page()
        await page2.set_content(HIERARCHY_HTML)
        report: dict = {
            "platform": "workday",
            "coverage_path": "workday_multipage",
            "workday_current_step": "contact",
        }
        hh = await _fill_how_heard(
            page2, {"HOW_HEARD": "Internet job board"}, report=report
        )
        assert _is_verified_fill(hh), hh
        assert hh.get("mode") == "hierarchical_how_heard" or hh.get("verified")
        rb = str(hh.get("readback") or "")
        assert "linkedin" in rb.lower() or "1 item selected" in rb.lower(), hh
        assert "0 items selected" not in rb.lower()
        menu2 = await page2.locator("#menu").evaluate(
            "el => getComputedStyle(el).display"
        )
        assert menu2 == "none", menu2
        assert not await listbox_still_open(page2)

        attach_footer_primary(report, kind="ADVANCE", label="Save and Continue")
        report["workday_wizard_progress"] = (
            "My Information current My Experience Application Questions Review"
        )
        report["workday"] = {"phase_b": {"present": True}, "phase_e": {}}
        assert may_enter_review_hold(report) is False

        out_dir = (
            Path(__file__).resolve().parents[2]
            / "skyvern_runtime"
            / "real_job_results"
            / "walmart_how_heard_fixture"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        await page2.screenshot(
            path=str(out_dir / "after_source_chip.png"), full_page=True
        )
        await browser.close()
    print("test_how_heard_hierarchy fixture: OK")


COMMITTED_CHIP_HTML = """
<html><body>
<div data-automation-id="contactInformationPage">
  <div data-automation-id="formField-source" id="hh-wrap">
    <label>How Did You Hear About Us?*</label>
    <div data-automation-id="multiSelectContainer">
      <span id="chip-chrome">1 item selected, Web - CareerBuilder</span>
      <input id="hh-input" name="source--source" data-automation-id="source--source"
             role="combobox" aria-required="true" aria-expanded="true" value="" />
    </div>
    <div id="menu" role="listbox" style="display:block; border:1px solid #ccc; padding:8px;">
      <div role="option" data-automation-id="promptOption">Web - CareerBuilder</div>
      <div role="option" data-automation-id="promptOption">Web - Glassdoor</div>
      <div role="option" data-automation-id="promptOption">Web - LinkedIn</div>
    </div>
  </div>
  <button id="state-btn" data-automation-id="addressSection_countryRegion">State</button>
  <button data-automation-id="bottom-navigation-next-button">Save and Continue</button>
</div>
<script>
const input = document.getElementById("hh-input");
const menu = document.getElementById("menu");
let openCount = 0;
function openMenu() {
  openCount += 1;
  menu.style.display = "block";
  input.setAttribute("aria-expanded", "true");
}
input.addEventListener("focus", openMenu);
input.addEventListener("click", openMenu);
document.body.addEventListener("click", (e) => {
  if (e.target === input || menu.contains(e.target)) return;
  menu.style.display = "none";
  input.setAttribute("aria-expanded", "false");
});
window.__hhOpenCount = () => openCount;
</script>
</body></html>
"""


async def _run_already_committed_skip() -> None:
    """CareerBuilder chip + Glassdoor intent → skip, close listbox, no second open."""
    from playwright.async_api import async_playwright
    from verified_select import (
        fill_hierarchical_how_heard,
        how_heard_source_committed,
        listbox_still_open,
        settle_before_advance,
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(COMMITTED_CHIP_HTML)
        assert await listbox_still_open(page), "fixture starts with open how-heard listbox"

        inp = page.locator("#hh-input")
        hier = await fill_hierarchical_how_heard(
            page,
            inp,
            leaf_candidates=["LinkedIn", "Glassdoor"],
            category_candidates=["Website", "Job Board"],
        )
        assert hier.get("skipped_already_correct") or hier.get("status") == "already_committed", hier
        assert hier.get("ok") and hier.get("committed"), hier
        chrome = await page.locator("#chip-chrome").inner_text()
        assert how_heard_source_committed(chrome, ["Glassdoor"])
        assert "careerbuilder" in chrome.lower()
        # Must not reopen / click a different leaf
        assert not hier.get("option_clicked"), hier
        menu_disp = await page.locator("#menu").evaluate("el => getComputedStyle(el).display")
        assert menu_disp == "none", menu_disp
        assert not await listbox_still_open(page)

        # Second pass: still no reopen
        open_before = await page.evaluate("() => window.__hhOpenCount()")
        hier2 = await fill_hierarchical_how_heard(
            page,
            inp,
            leaf_candidates=["LinkedIn", "Glassdoor"],
            category_candidates=["Website", "Job Board"],
        )
        open_after = await page.evaluate("() => window.__hhOpenCount()")
        assert hier2.get("skipped_already_correct") or hier2.get("status") == "already_committed", hier2
        assert not hier2.get("option_clicked"), hier2
        assert open_after == open_before, (open_before, open_after)
        assert not await listbox_still_open(page)

        # settle_before_advance must keep listbox closed (Next / other fields)
        report: dict = {"fill_values": {"HOW_HEARD": "Glassdoor"}}
        detail = await settle_before_advance(page, report)
        assert detail.get("settled"), detail
        assert not report.get("listbox_open"), report
        assert not await listbox_still_open(page)
        await browser.close()


def test_already_committed_chip_skip_second_pass_no_reopen() -> None:
    """0842Z: valid leaf already committed → skip, close listbox, never reopen."""
    asyncio.run(_run_already_committed_skip())


def test_how_heard_lock_skip_second_attempt() -> None:
    """After hierarchical commit, second _fill_how_heard must lock_skip — thrash 0."""
    import asyncio
    from exp_workday_selectors import _fill_how_heard, _is_verified_fill
    from field_lock import attach_field_locks, gate_field_action

    async def _run():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(HIERARCHY_HTML)
            report: dict = {"platform": "workday", "coverage_path": "workday_multipage"}
            attach_field_locks(report)
            hh1 = await _fill_how_heard(
                page, {"HOW_HEARD": "Internet job board"}, report=report
            )
            assert _is_verified_fill(hh1), hh1
            hh2 = await _fill_how_heard(
                page, {"HOW_HEARD": "Internet job board"}, report=report
            )
            assert hh2.get("skipped_locked") or hh2.get("reason") in (
                "field_locked_skip",
                "already_correct_keep",
            ), hh2
            thrash2 = int(getattr(report.get("_field_locks"), "thrash_retouches", 0) or 0)
            assert thrash2 == 1, f"expected one lock_skip retouch, got {thrash2}"
            await browser.close()

    asyncio.run(_run())


def main() -> None:
    test_category_helpers_and_candidates()
    test_false_hold_refused_on_advance_footer()
    test_already_committed_chip_skip_second_pass_no_reopen()
    test_how_heard_lock_skip_second_attempt()
    asyncio.run(_run_fixture())
    print("test_how_heard_hierarchy: OK")


if __name__ == "__main__":
    main()
