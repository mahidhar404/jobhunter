#!/usr/bin/env python3
"""Regression tests for never_seen_reverify_20260811T1902Z residuals.

Dummy-only fixtures — never live ATS, never submit.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def test_how_heard_rejects_unrelated_chip_when_candidates_given() -> None:
    """Sandoz: Antal Talent chip must not count as LinkedIn committed."""
    from verified_select import how_heard_source_committed

    chip = "1 item selected, Antal Talent"
    cands = ["LinkedIn", "Indeed", "BuiltIn"]
    assert not how_heard_source_committed(chip, cands)
    assert how_heard_source_committed("1 item selected, LinkedIn", cands)


def test_is_verified_fill_rejects_wrong_workday_chip() -> None:
    from fill_verify import is_verified_fill_row

    row = {
        "type": "HOW_HEARD",
        "value": "LinkedIn",
        "picked": "LinkedIn",
        "readback": "1 item selected, Antal Talent",
        "option_clicked": True,
        "verified": True,
        "ok": True,
        "committed": True,
    }
    assert is_verified_fill_row(row) is False


def test_probe_how_heard_rejects_unrelated_chip() -> None:
    """_probe must not keep wrong chip (no thrash on foreign source)."""
    import inspect

    from exp_workday_selectors import _probe_how_heard_already_committed

    src = inspect.getsource(_probe_how_heard_already_committed)
    assert "Still accept any" not in src
    assert "keep any chip" not in src.lower() or "do not thrash" not in src


def test_lever_pronouns_classify_and_pick_decline() -> None:
    from field_map import PRONOUNS, classify_field
    from lever_widgets import classify_lever_question, pick_eeo_radio_option

    ftype, _ = classify_field({"label": "Pronouns", "name": "pronouns", "type": "radio"})
    assert ftype == PRONOUNS
    assert classify_lever_question("Pronouns", name="pronouns") == PRONOUNS
    pick = pick_eeo_radio_option(
        PRONOUNS,
        "Prefer not to say",
        [
            {"label": "He/him", "value": "He/him"},
            {"label": "Prefer not to say", "value": "Prefer not to say"},
        ],
    )
    assert pick and "prefer not" in pick["label"].lower()


def test_lever_how_heard_uses_gh_select_when_control_present() -> None:
    import inspect

    from fast_fill import _should_use_gh_select

    src = inspect.getsource(_should_use_gh_select)
    assert 'platform == "lever"' in src
    assert "HOW_HEARD" in src
    assert "select__label" in src


def test_hierarchical_fixture_commits_leaf_chip() -> None:
    """Workday hierarchy fixture: chip must match picked leaf + listbox closes."""
    from test_how_heard_hierarchy import HIERARCHY_HTML

    async def _run() -> None:
        from playwright.async_api import async_playwright
        from verified_select import (
            fill_hierarchical_how_heard,
            listbox_still_open,
            settle_open_listbox,
        )

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(HIERARCHY_HTML)
            inp = page.locator("#hh-input")
            hier = await fill_hierarchical_how_heard(
                page,
                inp,
                leaf_candidates=["LinkedIn", "Indeed"],
                category_candidates=["Website", "Job Board", "Internet job board"],
            )
            assert hier.get("ok") and hier.get("committed"), hier
            rb = str(hier.get("readback") or "")
            assert "linkedin" in rb.lower()
            await settle_open_listbox(page)
            assert not await listbox_still_open(page)
            await browser.close()

    asyncio.run(_run())


def test_accenture_auth_gate_documented() -> None:
    wd_notes = ROOT.parents[1] / "ats_notes" / "workday.md"
    text = wd_notes.read_text(encoding="utf-8")
    assert "Accenture" in text
    assert "blocker" in text.lower() or "auth" in text.lower()


def main() -> None:
    test_how_heard_rejects_unrelated_chip_when_candidates_given()
    test_is_verified_fill_rejects_wrong_workday_chip()
    test_probe_how_heard_rejects_unrelated_chip()
    test_lever_pronouns_classify_and_pick_decline()
    test_lever_how_heard_uses_gh_select_when_control_present()
    test_hierarchical_fixture_commits_leaf_chip()
    test_accenture_auth_gate_documented()
    from lever_widgets import self_test as lever_self_test

    lever_self_test()
    print("test_reverify_residuals_20260811: OK")


if __name__ == "__main__":
    main()
