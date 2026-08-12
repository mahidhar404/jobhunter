#!/usr/bin/env python3
"""Exhaustive completion-detection honesty matrix (Stream B).

True/false grid for required_empty, gaps, unanswered choices, can_claim_ready,
hold_incomplete, apply_progress_verdict_gates, and evaluate_cycle_success /
apply_midwizard_to_decision demotions.

Dummy-only · never submit · no PII.
Run: skyvern_runtime/venv/bin/python scripts/fastfill/test_completion_detection_matrix.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
CASES = HERE / "gym" / "ats" / "cases"
sys.path.insert(0, str(HERE))

# ---------------------------------------------------------------------------
# Matrix registry (signal → tests)
# ---------------------------------------------------------------------------
COMPLETION_MATRIX: list[dict[str, str]] = []


def _row(cell_id: str, signal: str, test: str, desc: str) -> dict[str, str]:
    r = {"cell_id": cell_id, "signal": signal, "test": test, "description": desc}
    COMPLETION_MATRIX.append(r)
    return r


# --- required_empty ---
_row("req_empty_after_blocks_ready", "required_empty_after_fill", "test_req_empty_after_blocks_ready",
     "Post-fill required empties → can_claim_ready False")
_row("req_empty_before_blocks_ready", "required_empty_before_advance", "test_req_empty_before_blocks_ready",
     "Pre-advance required empties → can_claim_ready False")
_row("req_empty_demotes_success", "required_empty_after_fill", "test_req_empty_demotes_success",
     "apply_progress_verdict_gates demotes SUCCESS")
_row("req_empty_demotes_cycle", "required_empty_after_fill", "test_req_empty_demotes_cycle",
     "apply_midwizard_to_decision demotes cycle SUCCESS")

# --- gaps ---
_row("gaps_block_ready_true", "gaps_after_save", "test_gaps_block_ready",
     "Validation gaps → gaps_block_ready + can_claim_ready False")
_row("gaps_cookie_filtered", "gaps_after_save", "test_gaps_cookie_alerts_filtered",
     "Cookie alert_node gaps do not block")
_row("gaps_instruction_filtered", "gaps_after_save", "test_gaps_instruction_stripped",
     "CURRENT TEAMMATES instruction → not a gap")
_row("gaps_demotes_cycle", "gaps_after_save", "test_gaps_demotes_cycle",
     "evaluate_cycle_success refuses gaps_after_save")

# --- unanswered choices (miss-scan) ---
_row("miss_unanswered_radio", "unanswered_choice", "test_miss_unanswered_radio",
     "Unanswered required radio → miss scan row")
_row("miss_answered_aria_checked", "unanswered_choice", "test_miss_answered_aria_checked",
     "aria-checked No → zero misses (false incomplete guard)")
_row("miss_promotes_required_empty", "unanswered_choice", "test_miss_syncs_required_empty",
     "Required miss → required_empty_after_fill sync")

# --- can_claim_ready (composite gates) ---
_row("ready_clean_true", "can_claim_ready", "test_ready_clean_true",
     "All gates pass + FINAL footer → Ready True")
_row("ready_listbox_false", "can_claim_ready", "test_ready_listbox_open_false",
     "listbox_open / mid_widget_open → Ready False")
_row("ready_advance_blocked_false", "can_claim_ready", "test_ready_advance_blocked_false",
     "advance_blocked_reason → Ready False")
_row("ready_hold_incomplete_false", "can_claim_ready", "test_ready_hold_incomplete_false",
     "hold_incomplete → Ready False")
_row("ready_footer_advance_false", "can_claim_ready", "test_ready_footer_advance_false",
     "ADVANCE footer → Ready False even if phase says review")

# --- hold_incomplete ---
_row("hold_incomplete_demotes_taxonomy", "hold_incomplete", "test_hold_incomplete_demotes",
     "hold_incomplete → FAIL_MIDWIZARD via apply_midwizard_to_decision")
_row("may_enter_review_false_midwizard", "hold_incomplete", "test_may_enter_review_false",
     "may_enter_review_hold False when wizard incomplete")

# --- evaluate_cycle_success demotions ---
_row("cycle_listbox_demote", "evaluate_cycle_success", "test_cycle_listbox_demote",
     "mid_widget_open demotes cycle SUCCESS")
_row("cycle_advance_blocked_demote", "evaluate_cycle_success", "test_cycle_advance_blocked_demote",
     "advance_blocked_reason demotes cycle SUCCESS")
_row("cycle_leftovers_demote", "evaluate_cycle_success", "test_cycle_leftovers_demote",
     "hard leftovers demote cycle SUCCESS")
_row("cycle_footer_advance_demote", "evaluate_cycle_success", "test_cycle_footer_advance_demote",
     "ADVANCE footer + ready demotes via taxonomy")

# --- vision_judge radio / instruction ---
_row("vision_aria_checked_complete", "vision_judge", "test_vision_aria_checked_not_blank",
     "judge_page: aria-checked radio → not FAIL_BLANK unchecked")


_READY_BASE: dict[str, Any] = {
    "verdict": "SUCCESS",
    "advanced_incomplete": False,
    "validation_after_advance": None,
    "required_empty_before_advance": [],
    "required_empty_after_fill": [],
    "leftovers": [],
    "gaps_after_save": [],
    "vision_judge_live": {"complete": True, "verdict": "COMPLETE"},
    "footer_primary_kind": "FINAL",
    "footer_primary_label": "Submit Application",
}


def _vision_dom() -> dict[str, Any]:
    return {
        "complete": True,
        "empty_fields": [],
        "confidence": "high",
        "source": "dom",
        "never_submit": True,
        "submit_clicked": False,
    }


def _cycle_report(**overrides: Any) -> dict[str, Any]:
    base = {
        "never_submit": True,
        "submit_clicked": False,
        "identity_email": "randommail6969+abc@gmail.com",
        "leftovers": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# required_empty
# ---------------------------------------------------------------------------


def test_req_empty_after_blocks_ready() -> None:
    from page_progress import can_claim_ready

    assert can_claim_ready(
        {**_READY_BASE, "required_empty_after_fill": [{"id": "email", "label": "Email"}]}
    ) is False


def test_req_empty_before_blocks_ready() -> None:
    from page_progress import can_claim_ready

    assert can_claim_ready(
        {**_READY_BASE, "required_empty_before_advance": [{"id": "phone", "label": "Phone"}]}
    ) is False


def test_req_empty_demotes_success() -> None:
    from page_progress import apply_progress_verdict_gates

    report = {**_READY_BASE, "required_empty_after_fill": [{"label": "Zip"}]}
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "FAIL"
    assert report.get("verdict_reason") == "required_empty_after_fill"


def test_req_empty_demotes_cycle() -> None:
    from fail_taxonomy import apply_midwizard_to_decision

    report = _cycle_report(
        required_empty_after_fill=[{"id": "email"}],
        verdict="SUCCESS",
    )
    decision = apply_midwizard_to_decision(
        report, {"success": True, "verdict": "SUCCESS", "reasons": []}
    )
    assert decision["success"] is False
    assert decision["verdict"] == "FAIL_MIDWIZARD"


# ---------------------------------------------------------------------------
# gaps
# ---------------------------------------------------------------------------


def test_gaps_block_ready() -> None:
    from form_gaps import gaps_block_ready
    from page_progress import can_claim_ready

    gaps = [{"label": "Email is required", "reason": "error_node"}]
    assert gaps_block_ready(gaps) is True
    assert can_claim_ready(
        {**_READY_BASE, "gaps_after_save": gaps, "gaps_block_ready": True}
    ) is False


def test_gaps_cookie_alerts_filtered() -> None:
    from form_gaps import gaps_block_ready, normalize_gaps

    raw = [
        {"label": "We use cookies for analytics.", "reason": "alert_node"},
        {"label": "Email is required", "reason": "alert_node"},
    ]
    norm = normalize_gaps(raw)
    assert len(norm) == 1
    assert gaps_block_ready(
        [{"label": "We use cookies only.", "reason": "alert_node"}]
    ) is False


def test_gaps_instruction_stripped() -> None:
    from form_gaps import is_instruction_only_gap, normalize_gaps

    assert is_instruction_only_gap("CURRENT TEAMMATES: Please apply via internal site")
    norm = normalize_gaps(
        [{"label": "CURRENT TEAMMATES: Please apply via", "reason": "required_empty"}]
    )
    assert norm == []


def test_gaps_demotes_cycle() -> None:
    from cycle_orchestrate import evaluate_cycle_success

    decision = evaluate_cycle_success(
        _cycle_report(
            gaps_after_save=[{"label": "Email is required", "reason": "error_node"}],
            gaps_block_ready=True,
        ),
        _vision_dom(),
    )
    assert decision["success"] is False


# ---------------------------------------------------------------------------
# unanswered choices
# ---------------------------------------------------------------------------

UNANSWERED_RADIO_HTML = """
<html><body>
  <fieldset>
    <legend>Are you authorized to work in the US?*</legend>
    <label><input type="radio" name="work_auth" aria-required="true" value="yes" /> Yes</label>
    <label><input type="radio" name="work_auth" aria-required="true" value="no" /> No</label>
  </fieldset>
</body></html>
"""


async def _page_html(html: str):
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(html)
    return pw, browser, page


async def _close(pw, browser) -> None:
    await browser.close()
    await pw.stop()


def test_miss_unanswered_radio() -> None:
    from leftover_miss_scan import UNANSWERED_CHOICE_JS

    async def _run():
        pw, browser, page = await _page_html(UNANSWERED_RADIO_HTML)
        try:
            return await page.evaluate(UNANSWERED_CHOICE_JS)
        finally:
            await _close(pw, browser)

    misses = asyncio.run(_run())
    assert misses, misses
    assert any(m.get("reason") == "unanswered_radio_group" for m in misses)


def test_miss_answered_aria_checked() -> None:
    from form_gaps import collect_form_gaps
    from leftover_miss_scan import UNANSWERED_CHOICE_JS

    async def _run():
        html = (CASES / "wd_radio_aria_checked" / "form.html").read_text(encoding="utf-8")
        pw, browser, page = await _page_html(html)
        try:
            gaps = await collect_form_gaps(page)
            misses = await page.evaluate(UNANSWERED_CHOICE_JS)
            return gaps, misses
        finally:
            await _close(pw, browser)

    gaps, misses = asyncio.run(_run())
    assert gaps == [], gaps
    assert misses == [], misses


def test_miss_syncs_required_empty() -> None:
    from leftover_miss_scan import promote_l01_misses

    async def _run():
        pw, browser, page = await _page_html(UNANSWERED_RADIO_HTML)
        try:
            report: dict = {"leftovers": [], "filled": []}
            summary = await promote_l01_misses(page, report)
            return report, summary
        finally:
            await _close(pw, browser)

    report, summary = asyncio.run(_run())
    assert summary["added"] >= 1
    empties = report.get("required_empty_after_fill") or []
    assert empties, empties
    labels = " ".join(str(e.get("label") or "") for e in empties).lower()
    assert "authorized" in labels or "work" in labels


# ---------------------------------------------------------------------------
# can_claim_ready
# ---------------------------------------------------------------------------


def test_ready_clean_true() -> None:
    from page_progress import can_claim_ready

    assert can_claim_ready(_READY_BASE) is True


def test_ready_listbox_open_false() -> None:
    from page_progress import can_claim_ready

    assert can_claim_ready({**_READY_BASE, "listbox_open": True}) is False
    assert can_claim_ready({**_READY_BASE, "mid_widget_open": True}) is False


def test_ready_advance_blocked_false() -> None:
    from page_progress import can_claim_ready

    assert can_claim_ready(
        {**_READY_BASE, "advance_blocked_reason": "listbox_still_open"}
    ) is False


def test_ready_hold_incomplete_false() -> None:
    from page_progress import can_claim_ready

    assert can_claim_ready({**_READY_BASE, "hold_incomplete": True}) is False


def test_ready_footer_advance_false() -> None:
    from page_progress import can_claim_ready, finalize_ready_flag

    report = {
        **_READY_BASE,
        "platform": "workday",
        "workday_current_step": "review",
        "workday": {"phase_e": {"stopped_at_review": True}},
        "footer_primary_kind": "ADVANCE",
        "footer_primary_label": "Next",
        "ready_for_review": True,
    }
    assert can_claim_ready(report) is False
    finalize_ready_flag(report)
    assert report.get("ready_for_review") is not True


# ---------------------------------------------------------------------------
# hold_incomplete / may_enter_review_hold
# ---------------------------------------------------------------------------


def test_hold_incomplete_demotes() -> None:
    from fail_taxonomy import apply_midwizard_to_decision

    decision = apply_midwizard_to_decision(
        _cycle_report(hold_incomplete=True, ready_for_review=True, verdict="SUCCESS"),
        {"success": True, "verdict": "SUCCESS", "reasons": []},
    )
    assert decision["success"] is False
    assert decision["verdict"] == "FAIL_MIDWIZARD"


def test_may_enter_review_false() -> None:
    from page_progress import may_enter_review_hold

    incomplete = {
        **_READY_BASE,
        "platform": "workday",
        "workday_current_step": "experience",
        "footer_primary_kind": "ADVANCE",
        "footer_primary_label": "Save and Continue",
    }
    assert may_enter_review_hold(incomplete) is False


# ---------------------------------------------------------------------------
# evaluate_cycle_success demotions
# ---------------------------------------------------------------------------


def test_cycle_listbox_demote() -> None:
    from cycle_orchestrate import evaluate_cycle_success

    decision = evaluate_cycle_success(
        _cycle_report(listbox_open=True, mid_widget_open=True),
        _vision_dom(),
    )
    assert decision["success"] is False
    assert any("mid_widget_open" in r for r in decision.get("reasons") or [])


def test_cycle_advance_blocked_demote() -> None:
    from cycle_orchestrate import evaluate_cycle_success

    decision = evaluate_cycle_success(
        _cycle_report(advance_blocked_reason="listbox_still_open"),
        _vision_dom(),
    )
    assert decision["success"] is False


def test_cycle_leftovers_demote() -> None:
    from cycle_orchestrate import evaluate_cycle_success

    decision = evaluate_cycle_success(
        _cycle_report(leftovers=[{"label": "Phone", "type": "PHONE"}]),
        _vision_dom(),
    )
    assert decision["success"] is False


def test_cycle_footer_advance_demote() -> None:
    from cycle_orchestrate import evaluate_cycle_success
    from fail_taxonomy import apply_midwizard_to_decision

    report = _cycle_report(
        ready_for_review=True,
        footer_primary_kind="ADVANCE",
        footer_primary_label="Next",
    )
    decision = evaluate_cycle_success(report, _vision_dom())
    decision = apply_midwizard_to_decision(report, decision)
    assert decision["success"] is False
    assert decision["verdict"] == "FAIL_MIDWIZARD"


# ---------------------------------------------------------------------------
# vision_judge
# ---------------------------------------------------------------------------


def test_vision_aria_checked_not_blank() -> None:
    from vision_judge import finalize_verdict

    async def _run():
        from vision_judge import judge_page

        html = (CASES / "wd_radio_aria_checked" / "form.html").read_text(encoding="utf-8")
        pw, browser, page = await _page_html(html)
        try:
            return await judge_page(page)
        finally:
            await _close(pw, browser)

    result = asyncio.run(_run())
    result = finalize_verdict(result)
    blocking = [
        e
        for e in (result.get("empty_fields") or [])
        if isinstance(e, dict)
        and e.get("kind") == "unchecked"
        and "previously" in str(e.get("label") or "").lower()
    ]
    assert blocking == [], blocking
    assert result.get("verdict") in ("COMPLETE", "AMBIGUOUS"), result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_TEST_MAP: dict[str, Callable[[], None]] = {
    "test_req_empty_after_blocks_ready": test_req_empty_after_blocks_ready,
    "test_req_empty_before_blocks_ready": test_req_empty_before_blocks_ready,
    "test_req_empty_demotes_success": test_req_empty_demotes_success,
    "test_req_empty_demotes_cycle": test_req_empty_demotes_cycle,
    "test_gaps_block_ready": test_gaps_block_ready,
    "test_gaps_cookie_alerts_filtered": test_gaps_cookie_alerts_filtered,
    "test_gaps_instruction_stripped": test_gaps_instruction_stripped,
    "test_gaps_demotes_cycle": test_gaps_demotes_cycle,
    "test_miss_unanswered_radio": test_miss_unanswered_radio,
    "test_miss_answered_aria_checked": test_miss_answered_aria_checked,
    "test_miss_syncs_required_empty": test_miss_syncs_required_empty,
    "test_ready_clean_true": test_ready_clean_true,
    "test_ready_listbox_open_false": test_ready_listbox_open_false,
    "test_ready_advance_blocked_false": test_ready_advance_blocked_false,
    "test_ready_hold_incomplete_false": test_ready_hold_incomplete_false,
    "test_ready_footer_advance_false": test_ready_footer_advance_false,
    "test_hold_incomplete_demotes": test_hold_incomplete_demotes,
    "test_may_enter_review_false": test_may_enter_review_false,
    "test_cycle_listbox_demote": test_cycle_listbox_demote,
    "test_cycle_advance_blocked_demote": test_cycle_advance_blocked_demote,
    "test_cycle_leftovers_demote": test_cycle_leftovers_demote,
    "test_cycle_footer_advance_demote": test_cycle_footer_advance_demote,
    "test_vision_aria_checked_not_blank": test_vision_aria_checked_not_blank,
}


def run_completion_matrix() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for cell in COMPLETION_MATRIX:
        name = cell["test"]
        fn = _TEST_MAP.get(name)
        if fn is None:
            results.append(
                {"cell_id": cell["cell_id"], "signal": cell["signal"], "ok": False, "error": "missing fn"}
            )
            continue
        try:
            fn()
            results.append(
                {
                    "cell_id": cell["cell_id"],
                    "signal": cell["signal"],
                    "test": name,
                    "ok": True,
                }
            )
        except Exception as e:
            results.append(
                {
                    "cell_id": cell["cell_id"],
                    "signal": cell["signal"],
                    "test": name,
                    "ok": False,
                    "error": str(e)[:240],
                }
            )
    passed = sum(1 for r in results if r.get("ok"))
    return {
        "ok": passed == len(COMPLETION_MATRIX),
        "passed": passed,
        "failed": len(COMPLETION_MATRIX) - passed,
        "total": len(COMPLETION_MATRIX),
        "signals": sorted({c["signal"] for c in COMPLETION_MATRIX}),
        "results": results,
    }


def main() -> int:
    out = run_completion_matrix()
    if not out["ok"]:
        import json

        print("completion_detection_matrix FAILED:", json.dumps(out, indent=2))
        return 1
    print(
        f"test_completion_detection_matrix: OK ({out['passed']}/{out['total']} cells, "
        f"signals={','.join(out['signals'])})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
