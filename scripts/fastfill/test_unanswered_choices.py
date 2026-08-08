#!/usr/bin/env python3
"""Unit tests for unanswered_choices → flash_candidate promotion."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from unanswered_choices import (  # noqa: E402
    REASON,
    leftover_from_ashby_entry,
    leftover_from_generic_radio_group,
    leftover_from_lever_scan_row,
    promote_unanswered_rows,
)


def test_ashby_yesno_unselected():
    row = leftover_from_ashby_entry(
        {"label": "Are you authorized to work?*", "yesno": True, "yesnoSelected": ""}
    )
    assert row is not None
    assert row["reason"] == REASON
    assert row["flash_candidate"] is True
    assert row["mode"] == "yesno"


def test_ashby_yesno_selected_skipped():
    assert (
        leftover_from_ashby_entry(
            {"label": "Authorized?", "yesno": True, "yesnoSelected": "Yes"}
        )
        is None
    )


def test_ashby_radio_none_checked():
    row = leftover_from_ashby_entry(
        {
            "label": "Gender*",
            "radios": [
                {"name": "g", "checked": False},
                {"name": "g", "checked": False},
            ],
        }
    )
    assert row is not None
    assert row["mode"] == "radio"
    assert row["flash_candidate"] is True


def test_ashby_required_empty_text():
    row = leftover_from_ashby_entry(
        {
            "label": "LinkedIn URL*",
            "hasText": True,
            "textEmpty": True,
            "textName": "linkedin",
        }
    )
    assert row is not None
    assert row["mode"] == "text"


def test_lever_radio_unanswered():
    row = leftover_from_lever_scan_row(
        {
            "kind": "radio",
            "name": "cards[abc]",
            "label": "Do you require sponsorship?",
            "anyChecked": False,
            "options": [{"label": "Yes"}, {"label": "No"}],
        }
    )
    assert row is not None
    assert row["reason"] == REASON
    assert row["platform"] == "lever"


def test_lever_radio_checked_skipped():
    assert (
        leftover_from_lever_scan_row(
            {
                "kind": "radio",
                "label": "Auth",
                "anyChecked": True,
            }
        )
        is None
    )


def test_generic_radio_group():
    row = leftover_from_generic_radio_group(
        {
            "name": "candidateIsPreviousWorker",
            "label": "Have you worked here before?*",
            "anyChecked": False,
            "platform": "workday",
        }
    )
    assert row is not None
    assert row["platform"] == "workday"


def test_promote_dedupes_and_skips_filled():
    report = {
        "leftovers": [
            {
                "label": "Do you require sponsorship?",
                "reason": REASON,
                "flash_candidate": True,
            }
        ],
        "filled": [
            {
                "label": "Gender*",
                "ok": True,
                "verified": True,
                "type": "GENDER",
            }
        ],
    }
    rows = [
        {
            "label": "Do you require sponsorship?",
            "reason": REASON,
            "flash_candidate": True,
            "name": "x",
        },
        {
            "label": "Gender*",
            "reason": REASON,
            "flash_candidate": True,
        },
        {
            "label": "Veteran status*",
            "reason": REASON,
            "flash_candidate": True,
            "name": "vet",
        },
    ]
    added = promote_unanswered_rows(report, rows)
    assert added == 1
    assert report["unanswered_choices_promoted"] == 1
    labels = [u["label"] for u in report["leftovers"]]
    assert "Veteran status*" in labels
    assert labels.count("Do you require sponsorship?") == 1


def test_required_empty_js_mentions_radio_group():
    from fast_fill import GENERIC_REQUIRED_EMPTY_JS

    assert "empty_required_radio_group" in GENERIC_REQUIRED_EMPTY_JS
    assert "input[type=radio]" in GENERIC_REQUIRED_EMPTY_JS


def main() -> None:
    test_ashby_yesno_unselected()
    test_ashby_yesno_selected_skipped()
    test_ashby_radio_none_checked()
    test_ashby_required_empty_text()
    test_lever_radio_unanswered()
    test_lever_radio_checked_skipped()
    test_generic_radio_group()
    test_promote_dedupes_and_skips_filled()
    test_required_empty_js_mentions_radio_group()
    print("test_unanswered_choices: OK")


if __name__ == "__main__":
    main()
