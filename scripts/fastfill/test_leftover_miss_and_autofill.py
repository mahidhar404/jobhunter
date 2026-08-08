#!/usr/bin/env python3
"""Unit tests: L0/1 miss scan → flash_candidates + Autofill-with-Resume preference."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def test_miss_scan_self_test():
    from leftover_miss_scan import self_test

    self_test()


def test_miss_scan_promotes_radio_and_yesno():
    from leftover_miss_scan import merge_miss_leftovers, misses_to_leftover_rows

    report = {"leftovers": [], "filled": []}
    misses = [
        {
            "label": "Do you require sponsorship?*",
            "kind": "radio_group",
            "reason": "unanswered_radio_group",
            "name": "cards[xyz]",
            "selector": 'input[type=radio][name="cards[xyz]"]',
        },
        {
            "label": "Are you currently based in Latin America?",
            "kind": "yesno_segmented",
            "reason": "unanswered_ashby_yesno",
            "name": "",
            "selector": "",
        },
    ]
    n = merge_miss_leftovers(report, misses)
    assert n == 2
    assert all(u["flash_candidate"] is True for u in report["leftovers"])
    assert all(str(u["reason"]).startswith("l01_miss_scan:") for u in report["leftovers"])
    rows = misses_to_leftover_rows(misses)
    assert rows[0]["via"] == "leftover_miss_scan"
    # Second merge is idempotent
    assert merge_miss_leftovers(report, misses) == 0


def test_miss_scan_skips_verified_fill():
    from leftover_miss_scan import merge_miss_leftovers

    report = {
        "leftovers": [],
        "filled": [
            {
                "label": "Do you require sponsorship?*",
                "type": "SPONSORSHIP",
                "ok": True,
                "verified": True,
            }
        ],
    }
    n = merge_miss_leftovers(
        report,
        [
            {
                "label": "Do you require sponsorship?*",
                "kind": "radio_group",
                "reason": "unanswered_radio_group",
                "name": "x",
                "selector": "",
            }
        ],
    )
    assert n == 0


def test_autofill_resume_classified_resume_entry():
    from button_map import RESUME_ENTRY, classify_button

    for text in (
        "Autofill with Resume",
        "Apply with Resume",
        "Apply With Resume",
        "Use My Last Application",
        "Autofill from Resume",
    ):
        assert classify_button(text) == RESUME_ENTRY, text


def test_pick_click_prefers_autofill_over_manual():
    from fast_fill import pick_click_candidates

    classified = [
        {
            "kind": "ENTRY",
            "text": "Apply Manually",
            "gate_ok": True,
            "href": "",
            "gate_reason": None,
        },
        {
            "kind": "RESUME_ENTRY",
            "text": "Autofill with Resume",
            "gate_ok": True,
            "href": "",
            "gate_reason": None,
        },
    ]
    ranked = pick_click_candidates(classified, allow_advance=False)
    assert ranked[0]["text"] == "Autofill with Resume"
    assert ranked[0]["kind"] == "RESUME_ENTRY"


def test_workday_apply_resume_selectors_include_autofill():
    from exp_workday_selectors import (
        APPLY_WITH_RESUME_SELECTORS,
        USE_MY_LAST_APPLICATION_SELECTORS,
    )

    blob = "\n".join(APPLY_WITH_RESUME_SELECTORS).lower()
    assert "autofill with resume" in blob
    assert "apply with resume" in blob
    assert "autofillwithresume" in blob or "applywithresume" in blob
    # ATS-003: Use My Last must NOT be in dummy/test Autofill pack
    assert "use my last" not in blob
    last_blob = "\n".join(USE_MY_LAST_APPLICATION_SELECTORS).lower()
    assert "use my last application" in last_blob


def test_generic_required_empty_js_mentions_radio_group():
    from fast_fill import GENERIC_REQUIRED_EMPTY_JS

    assert "empty_required_radio_group" in GENERIC_REQUIRED_EMPTY_JS
    assert "input[type=radio]" in GENERIC_REQUIRED_EMPTY_JS


if __name__ == "__main__":
    test_miss_scan_self_test()
    test_miss_scan_promotes_radio_and_yesno()
    test_miss_scan_skips_verified_fill()
    test_autofill_resume_classified_resume_entry()
    test_pick_click_prefers_autofill_over_manual()
    test_workday_apply_resume_selectors_include_autofill()
    test_generic_required_empty_js_mentions_radio_group()
    print("test_leftover_miss_and_autofill: OK")
