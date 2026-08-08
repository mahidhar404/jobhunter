#!/usr/bin/env python3
"""FILL3-003 / FILL2-S01: alert_node gaps filtered via looks_like_gap_message."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from form_gaps import (  # noqa: E402
    gaps_block_ready,
    looks_like_gap_message,
    merge_gaps_into_report,
    normalize_gaps,
)


def test_looks_like_gap_message_validation():
    assert looks_like_gap_message("This field is required") is True
    assert looks_like_gap_message("Email is required") is True
    assert looks_like_gap_message("Please enter your phone number") is True
    assert looks_like_gap_message("Please select a location") is True
    assert looks_like_gap_message("1 error found") is True
    assert looks_like_gap_message("Must have a value") is True


def test_looks_like_gap_message_rejects_cookie_marketing():
    assert looks_like_gap_message("We use cookies to improve your experience") is False
    assert looks_like_gap_message("Accept all cookies") is False
    assert looks_like_gap_message("Sign up for our newsletter") is False
    assert looks_like_gap_message("This site uses analytics") is False


def test_normalize_gaps_filters_alert_noise():
    raw = [
        {
            "label": "We use cookies to improve your experience. Accept all.",
            "reason": "alert_node",
            "automation_id": "",
        },
        {
            "label": "Email is required",
            "reason": "alert_node",
            "automation_id": "email",
        },
        {
            "label": "Something went wrong on the server",
            "reason": "error_node",
            "automation_id": "errorMessage",
        },
        {
            "label": "First Name *",
            "reason": "required_empty",
            "automation_id": "name",
        },
    ]
    norm = normalize_gaps(raw)
    labels = [g["label"] for g in norm]
    assert "Email is required" in labels
    assert "Something went wrong on the server" in labels  # error_node kept
    assert "First Name *" in labels
    assert not any("cookies" in L.lower() for L in labels)
    assert gaps_block_ready(norm) is True


def test_normalize_gaps_all_cookie_alerts_do_not_block_ready():
    raw = [
        {
            "label": "We use cookies for analytics and marketing.",
            "reason": "alert_node",
        },
        {
            "label": "This website uses cookies.",
            "reason": "alert_node",
        },
    ]
    norm = normalize_gaps(raw)
    assert norm == []
    assert gaps_block_ready(norm) is False
    report: dict = {}
    merge_gaps_into_report(report, raw)
    assert report.get("gaps_block_ready") is False
    assert report.get("gaps_after_save") == []


def test_error_node_without_gap_words_still_kept():
    """Workday errorMessage / aria-invalid keep even without required-pattern."""
    norm = normalize_gaps(
        [{"label": "Invalid entry", "reason": "error_node", "automation_id": "x"}]
    )
    assert len(norm) == 1
    assert gaps_block_ready(norm) is True


def main() -> int:
    test_looks_like_gap_message_validation()
    test_looks_like_gap_message_rejects_cookie_marketing()
    test_normalize_gaps_filters_alert_noise()
    test_normalize_gaps_all_cookie_alerts_do_not_block_ready()
    test_error_node_without_gap_words_still_kept()
    print("test_form_gaps_alert_filter: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
