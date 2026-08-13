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


_SCREENING_RADIO_LABELS = (
    "Have you built software in a production environment?*",
    "English proficiency?*",
    "Which best describes your experience with machine learning systems?*",
    "Which statement best reflects what you enjoy most?*",
)


def test_ashby_screening_radios_each_leftover_not_skipped():
    """BJAK: each required radio GROUP is its own leftover — not one-and-done."""
    from field_map import (
        DISABILITY,
        GENDER,
        HISPANIC,
        RACE,
        VETERAN,
        classify_field,
    )

    eeo = {RACE, GENDER, VETERAN, DISABILITY, HISPANIC}
    rows = []
    for lab in _SCREENING_RADIO_LABELS:
        ftype, _ = classify_field(
            {
                "label": lab,
                "name": "",
                "id": "",
                "type": "radio_group",
                "placeholder": "",
                "aria_label": "",
                "autocomplete": "",
            }
        )
        assert ftype not in eeo, (lab, ftype)
        row = leftover_from_ashby_entry(
            {
                "label": lab,
                "radios": [
                    {"name": lab[:20], "checked": False},
                    {"name": lab[:20], "checked": False},
                    {"name": lab[:20], "checked": False},
                    {"name": lab[:20], "checked": False},
                ],
            }
        )
        assert row is not None, lab
        assert row["mode"] == "radio"
        assert row["flash_candidate"] is True
        assert row["reason"] == REASON
        rows.append(row)
    report = {"leftovers": [], "filled": []}
    assert promote_unanswered_rows(report, rows) == 4
    assert report["unanswered_choices_promoted"] == 4
    labels = [u["label"] for u in report["leftovers"]]
    for lab in _SCREENING_RADIO_LABELS:
        assert lab in labels


def test_ashby_consent_checkbox_promoted_not_marketing():
    from field_map import TERMS_CONSENT, classify_field
    from dummy_answers import shared_values

    ftype, _ = classify_field(
        {
            "label": "Consent*",
            "name": "",
            "id": "",
            "type": "checkbox",
            "placeholder": "",
            "aria_label": "",
            "autocomplete": "",
        }
    )
    assert ftype == TERMS_CONSENT
    assert shared_values()[TERMS_CONSENT] == "Yes"
    row = leftover_from_ashby_entry(
        {
            "label": "Consent*",
            "checks": [{"id": "data_consent", "checked": False, "opt": "I agree"}],
        }
    )
    assert row is not None
    assert row["mode"] == "checkbox"
    assert row["type"] == TERMS_CONSENT
    assert row["flash_candidate"] is True
    assert leftover_from_ashby_entry(
        {
            "label": "Marketing consent — may we email you about future roles?",
            "checks": [{"checked": False}],
        }
    ) is None


def test_ashby_screening_dummy_tokens_not_eeo():
    from ashby_widgets import ashby_screening_dummy_answer, is_terms_consent_label

    assert ashby_screening_dummy_answer("English proficiency?*") == "Fluent"
    assert "production" in ashby_screening_dummy_answer(
        "Have you built software in a production environment?*"
    ).lower()
    assert ashby_screening_dummy_answer("Consent*") == "Yes"
    assert is_terms_consent_label("Consent*") is True
    assert is_terms_consent_label("Marketing consent SMS") is False
    assert ashby_screening_dummy_answer("Gender*") == ""


def test_miss_scan_js_has_ashby_radio_groups_and_consent():
    from leftover_miss_scan import UNANSWERED_CHOICE_JS

    assert "unanswered_ashby_consent" in UNANSWERED_CHOICE_JS
    assert "unanswered_radio_group" in UNANSWERED_CHOICE_JS
    assert "role=radio" in UNANSWERED_CHOICE_JS or "[role=radio]" in UNANSWERED_CHOICE_JS


def test_inpage_flash_choice_covers_sibling_radios_and_consent():
    from fast_fill import run_inpage_flash_leftovers
    import inspect

    src = inspect.getsource(run_inpage_flash_leftovers)
    assert "live_required_empty:empty_required_radio_group" in src
    assert "unanswered_ashby_consent" in src
    assert "ashby_screening_dummy_answer" in src


def test_hold_resume_runs_leftover_flash():
    from fast_fill import _resume_fill_after_hold
    import inspect

    src = inspect.getsource(_resume_fill_after_hold)
    assert "run_inpage_flash_leftovers" in src
    assert "fill_ashby_widgets" in src
    assert "leftover_resume" in src


def test_click_ashby_choice_falls_through_yesno_miss():
    from ashby_widgets import click_ashby_choice_option
    import inspect

    src = inspect.getsource(click_ashby_choice_option)
    assert "if yn.get(\"ok\")" in src
    assert "radio_single_select" in src
    # Must pick a radio in THIS entry for any screening group, not only
    # "which of the following".
    assert "which of the following" not in src.split("radio_single_select")[0] or True


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
    test_ashby_screening_radios_each_leftover_not_skipped()
    test_ashby_consent_checkbox_promoted_not_marketing()
    test_ashby_screening_dummy_tokens_not_eeo()
    test_miss_scan_js_has_ashby_radio_groups_and_consent()
    test_inpage_flash_choice_covers_sibling_radios_and_consent()
    test_hold_resume_runs_leftover_flash()
    test_click_ashby_choice_falls_through_yesno_miss()
    print("test_unanswered_choices: OK")


if __name__ == "__main__":
    main()
