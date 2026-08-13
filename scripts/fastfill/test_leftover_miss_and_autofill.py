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
                "value": "No",
                "readback": "No",
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


def test_nxp_0842_invented_leftovers_demoted():
    """0842 leftovers: not_in_dom optional + phone-country false empty."""
    from leftover_miss_scan import demote_invented_leftovers, is_invented_leftover

    report = {
        "filled": [
            {
                "type": "countryPhoneCode",
                "automation_id": "countryPhoneCode",
                "ok": True,
                "verified": True,
                "value": "United States (+1)",
                "readback": (
                    "Country Phone Code* 1 item selected, "
                    "United States of America (+1) United States of America (+1)"
                ),
            }
        ],
        "leftovers": [
            {
                "label": "addressSection_addressLine2",
                "reason": "not_in_dom",
                "automation_id": "addressSection_addressLine2",
            },
            {
                "label": "addressSection_regionSubdivision1",
                "reason": "not_in_dom",
                "automation_id": "addressSection_regionSubdivision1",
            },
            {
                "label": "worked_here_before",
                "reason": "radio_not_found",
                "automation_id": "worked_here_before",
            },
            {
                "label": "phonenumber--countryphonecode",
                "reason": "live_required_empty:empty_required_input",
                "automation_id": None,
            },
            {
                "label": "First Name*",
                "reason": "live_required_empty:empty_required_input",
                "automation_id": "formField-legalName--firstName",
            },
        ],
    }
    assert is_invented_leftover(report["leftovers"][0], report) is True
    assert is_invented_leftover(report["leftovers"][3], report) is True
    assert is_invented_leftover(report["leftovers"][4], report) is False
    n = demote_invented_leftovers(report)
    assert n == 4, n
    assert len(report["leftovers"]) == 1
    assert report["leftovers"][0]["label"] == "First Name*"
    assert report.get("invented_leftover_count") == 4


def test_phone_country_leftover_kept_when_chip_missing():
    from leftover_miss_scan import is_invented_leftover

    row = {
        "label": "phonenumber--countryphonecode",
        "reason": "live_required_empty:empty_required_input",
    }
    assert is_invented_leftover(row, {"filled": [], "leftovers": [row]}) is False


def test_gate_counts_invented_leftovers_and_multiline_wrong():
    from reliability_gate import _leftover_counts, _wrong_values_from_steps

    report = {
        "filled": [
            {
                "type": "countryPhoneCode",
                "automation_id": "countryPhoneCode",
                "ok": True,
                "verified": True,
                "value": "United States (+1)",
                "readback": "United States of America (+1)",
            }
        ],
        "leftovers": [
            {
                "label": "addressSection_addressLine2",
                "reason": "not_in_dom",
                "automation_id": "addressSection_addressLine2",
            },
            {
                "label": "phonenumber--countryphonecode",
                "reason": "live_required_empty:empty_required_input",
            },
            {
                "label": "First Name*",
                "reason": "live_required_empty:empty_required_input",
            },
        ],
    }
    real, invented = _leftover_counts(report)
    assert real == 1, (real, invented)
    assert invented == 2, (real, invented)

    log = (
        '[fill-step 011] 08:43:08 action_audit | contact_email "" → "x" '
        "via=workday_automation_id reason=OK:empty_to_filled\n"
        "[fill-step 012] 08:43:11 action_audit | HOW_HEARD (how_heard) \"\" → \"How Did You Hear About Us?*\n"
        "1 item selected, Web - CareerBuilder\n"
        "\n"
        'Web - CareerBuilder" via=how_heard reason=WRONG:empty_to_filled\n'
        '[fill-step 018] 08:44:17 action_audit | select_one:Degree "" → "Master" '
        "via=select_one_by_label reason=WRONG:empty_to_filled\n"
    )
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "run.log"
        p.write_text(log)
        bad = _wrong_values_from_steps(None, p, report)
    fields = [str(w.get("field") or "") for w in bad]
    assert any("HOW_HEARD" in f for f in fields), bad
    assert any("Degree" in f for f in fields), bad
    assert not any("contact_email" in f for f in fields), bad


def test_experience_required_empty_leftovers_demoted_when_filled():
    """1116Z: Job Title*/From*/To* WD_REQUIRED_EMPTY are invented once dummy is shown."""
    from leftover_miss_scan import demote_invented_leftovers, is_invented_leftover

    report = {
        "filled": [
            {
                "automation_id": "workExperience-1/jobTitle",
                "type": "workExperience-1/jobTitle",
                "value": "Applied AI/ML Analyst",
                "readback": "Applied AI/ML Analyst",
                "verified": True,
                "ok": True,
            },
            {
                "automation_id": "workExperience-1/startDate",
                "type": "workExperience-1/startDate",
                "mode": "date_spin",
                "value": "01/2024",
                "readback": "01/2024",
                "verified": True,
                "ok": True,
                "reason": "already_correct_skip",
                "skipped_already_correct": True,
            },
            {
                "automation_id": "workExperience-1/endDate",
                "type": "workExperience-1/endDate",
                "mode": "date_spin",
                "value": "12/2024",
                "readback": "12/2024",
                "verified": True,
                "ok": True,
                "reason": "already_correct_skip",
                "skipped_already_correct": True,
            },
        ],
        "leftovers": [
            {
                "label": "Job Title*",
                "type": "WD_REQUIRED_EMPTY",
                "reason": "empty_required_input",
                "automation_id": "jobTitle",
            },
            {
                "label": "From*",
                "type": "WD_REQUIRED_EMPTY",
                "reason": "empty_required_date_field",
                "automation_id": "formField-startDate",
            },
            {
                "label": "To*",
                "type": "WD_REQUIRED_EMPTY",
                "reason": "empty_required_date_field",
                "automation_id": "formField-endDate",
            },
            {
                "label": "formfield-startdate",
                "reason": "live_required_empty:empty_required_date_field",
            },
            {
                "label": "Role Description",
                "reason": "unclassified",
            },
        ],
    }
    assert is_invented_leftover(report["leftovers"][0], report) is True
    assert is_invented_leftover(report["leftovers"][1], report) is True
    assert is_invented_leftover(report["leftovers"][2], report) is True
    assert is_invented_leftover(report["leftovers"][3], report) is True
    assert is_invented_leftover(report["leftovers"][4], report) is False
    n = demote_invented_leftovers(report)
    assert n == 4, n
    assert len(report["leftovers"]) == 1
    assert report["leftovers"][0]["label"] == "Role Description"


def test_1154z_date_required_empty_and_offscreen_leftovers_demoted():
    """1154Z leftover filter: committed 01/2024 drops From*/To* + offscreen dates."""
    from leftover_miss_scan import demote_invented_leftovers, is_invented_leftover
    from workday_date_readback import is_date_spin_theater_label

    assert is_date_spin_theater_label("Year — From*")
    assert is_date_spin_theater_label("Year — To (Actual or Expected)")
    assert is_date_spin_theater_label("Year")

    report = {
        "filled": [
            {
                "automation_id": "workExperience-1/startDate",
                "type": "EXPERIENCE_DATE",
                "mode": "date_spin",
                "value": "01/2024",
                "readback": "01/2024",
                "verified": True,
                "ok": True,
                "reason": "autofill_committed_skip",
                "skipped_already_correct": True,
            },
            {
                "automation_id": "workExperience-1/endDate",
                "type": "EXPERIENCE_DATE",
                "mode": "date_spin",
                "value": "12/2024",
                "readback": "12/2024",
                "verified": True,
                "ok": True,
                "reason": "already_correct_skip",
                "skipped_already_correct": True,
            },
        ],
        "leftovers": [
            {
                "label": "To*",
                "type": "WD_REQUIRED_EMPTY",
                "reason": "empty_required_date_field",
                "automation_id": "formField-endDate",
            },
            {
                "label": "From*",
                "type": "WD_REQUIRED_EMPTY",
                "reason": "empty_required_date_field",
                "automation_id": "formField-startDate",
            },
            {
                "label": "workExperience-1/startDate",
                "reason": "offscreen_skip",
                "automation_id": "workExperience-1/startDate",
            },
            {
                "label": "workExperience-1/endDate",
                "reason": "offscreen_skip",
                "automation_id": "workExperience-1/endDate",
            },
            {
                "label": "Year — From*",
                "reason": "unclassified",
            },
            {
                "label": "Role Description",
                "reason": "unclassified",
            },
        ],
    }
    assert is_invented_leftover(report["leftovers"][0], report) is True
    assert is_invented_leftover(report["leftovers"][1], report) is True
    assert is_invented_leftover(report["leftovers"][2], report) is True
    assert is_invented_leftover(report["leftovers"][3], report) is True
    assert is_invented_leftover(report["leftovers"][4], report) is True
    assert is_invented_leftover(report["leftovers"][5], report) is False
    n = demote_invented_leftovers(report)
    assert n == 5, (n, report["leftovers"])
    assert len(report["leftovers"]) == 1
    assert report["leftovers"][0]["label"] == "Role Description"


def test_blank_job_title_leftover_not_invented():
    from leftover_miss_scan import is_invented_leftover

    row = {
        "label": "Job Title*",
        "type": "WD_REQUIRED_EMPTY",
        "reason": "empty_required_input",
        "automation_id": "jobTitle",
    }
    assert is_invented_leftover(row, {"filled": [], "leftovers": [row]}) is False


def test_merge_skips_phone_country_when_chip_filled():
    from leftover_miss_scan import merge_miss_leftovers

    report = {
        "leftovers": [],
        "filled": [
            {
                "type": "PHONE_COUNTRY_CODE",
                "automation_id": "countryPhoneCode",
                "ok": True,
                "verified": True,
                "value": "United States (+1)",
                "readback": "United States of America (+1)",
            }
        ],
    }
    n = merge_miss_leftovers(
        report,
        [
            {
                "label": "phonenumber--countryphonecode",
                "kind": "combobox",
                "reason": "empty_required_select",
                "name": "phonenumber--countryphonecode",
                "selector": "",
            }
        ],
    )
    assert n == 0, report.get("leftovers")


if __name__ == "__main__":
    test_miss_scan_self_test()
    test_miss_scan_promotes_radio_and_yesno()
    test_miss_scan_skips_verified_fill()
    test_autofill_resume_classified_resume_entry()
    test_pick_click_prefers_autofill_over_manual()
    test_workday_apply_resume_selectors_include_autofill()
    test_generic_required_empty_js_mentions_radio_group()
    test_nxp_0842_invented_leftovers_demoted()
    test_phone_country_leftover_kept_when_chip_missing()
    test_experience_required_empty_leftovers_demoted_when_filled()
    test_1154z_date_required_empty_and_offscreen_leftovers_demoted()
    test_blank_job_title_leftover_not_invented()
    test_merge_skips_phone_country_when_chip_filled()
    test_gate_counts_invented_leftovers_and_multiline_wrong()
    print("test_leftover_miss_and_autofill: OK")
