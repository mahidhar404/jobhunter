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

# Walmart-shaped hierarchy: type → Job Board subsection → Indeed leaf → chip
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
        <strong>Job Board</strong>
      </div>
      <div id="top-opts"></div>
      <div id="sub-opts" style="display:none;"></div>
    </div>
  </div>
  <button data-automation-id="bottom-navigation-next-button">Save and Continue</button>
</div>
<script>
const CATEGORIES = ["Internet job board", "Job Board", "Social Media"];
const LEAVES = ["CareerBuilder", "Comparably", "Getting Hired", "Google For Jobs",
                "Indeed", "Jobillico", "LinkedIn"];
const input = document.getElementById("hh-input");
const menu = document.getElementById("menu");
const topOpts = document.getElementById("top-opts");
const subOpts = document.getElementById("sub-opts");
const subHdr = document.getElementById("subsection-hdr");
const chrome = document.getElementById("chip-chrome");
let inSub = false;
let chip = null;

function renderTop(q) {
  topOpts.innerHTML = "";
  subOpts.style.display = "none";
  subHdr.style.display = "none";
  inSub = false;
  const qq = (q || "").toLowerCase();
  const cats = CATEGORIES.filter(c => !qq || c.toLowerCase().includes(qq)
    || (qq === "indeed" && c.toLowerCase().includes("job board")));
  // When querying a leaf, still show Job Board category (Walmart behavior)
  const show = cats.length ? cats : CATEGORIES;
  show.forEach(c => {
    const el = document.createElement("div");
    el.setAttribute("role", "option");
    el.setAttribute("data-automation-id", "promptOption");
    el.setAttribute("aria-expanded", "false");
    el.textContent = c;
    el.onclick = () => openSub(c);
    topOpts.appendChild(el);
  });
  // Also surface leaf if typed exactly and not only categories
  LEAVES.filter(l => qq && l.toLowerCase().includes(qq)).forEach(l => {
    const el = document.createElement("div");
    el.setAttribute("role", "option");
    el.setAttribute("data-automation-id", "promptOption");
    el.textContent = l;
    el.onclick = () => pickLeaf(l);
    topOpts.appendChild(el);
  });
  menu.style.display = "block";
}
function openSub(cat) {
  inSub = true;
  topOpts.innerHTML = "";
  subHdr.style.display = "block";
  subOpts.style.display = "block";
  subOpts.innerHTML = "";
  LEAVES.forEach(l => {
    const el = document.createElement("div");
    el.setAttribute("role", "option");
    el.setAttribute("data-automation-id", "promptOption");
    el.textContent = l;
    el.onclick = () => pickLeaf(l);
    subOpts.appendChild(el);
  });
}
function pickLeaf(l) {
  chip = l;
  chrome.textContent = "1 item selected, " + l;
  input.value = "1 item selected, " + l;
  menu.style.display = "none";
}
document.getElementById("back-btn").onclick = () => renderTop(input.value);
input.addEventListener("focus", () => renderTop(input.value));
input.addEventListener("input", () => renderTop(input.value));
input.addEventListener("click", () => renderTop(input.value));
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
    assert not is_how_heard_category_option("Indeed")
    assert not is_how_heard_category_option("LinkedIn")

    leaves = how_heard_leaf_candidates({"HOW_HEARD": "Internet job board"})
    assert leaves[0] == "Indeed"
    assert "Internet job board" not in leaves

    cands = how_heard_candidates({"HOW_HEARD": "Internet job board"})
    assert cands[0] == "Indeed"
    assert "Internet job board" in cands
    assert cands.index("Indeed") < cands.index("Internet job board")

    cats = how_heard_category_candidates({"HOW_HEARD": "Internet job board"})
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
    from verified_select import fill_hierarchical_how_heard, how_heard_source_committed

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(HIERARCHY_HTML)

        # Hierarchical helper alone
        inp = page.locator("#hh-input")
        hier = await fill_hierarchical_how_heard(
            page,
            inp,
            leaf_candidates=["Indeed", "LinkedIn"],
            category_candidates=["Internet job board", "Job Board"],
        )
        assert hier.get("ok") and hier.get("committed"), hier
        assert "Indeed" in str(hier.get("picked") or hier.get("readback") or "")
        chrome = await page.locator("#chip-chrome").inner_text()
        assert how_heard_source_committed(chrome, ["Indeed"])
        assert "1 item selected" in chrome.lower()

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
        assert "indeed" in rb.lower() or "1 item selected" in rb.lower(), hh
        assert "0 items selected" not in rb.lower()

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


def main() -> None:
    test_category_helpers_and_candidates()
    test_false_hold_refused_on_advance_footer()
    asyncio.run(_run_fixture())
    print("test_how_heard_hierarchy: OK")


if __name__ == "__main__":
    main()
