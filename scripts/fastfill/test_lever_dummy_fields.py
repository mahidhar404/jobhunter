#!/usr/bin/env python3
"""Regression tests for Lever dummy-only custom fields (no browser)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def test_twitter_url_classifies_and_uses_dummy_url():
    from field_map import DUMMY_PROFILE, TWITTER, build_value_map, classify_field

    ftype, _ = classify_field(
        {
            "label": "Twitter URL",
            "name": "urls[Twitter]",
            "autocomplete": "url",
            "type": "url",
        }
    )
    assert ftype == TWITTER
    assert build_value_map(DUMMY_PROFILE)[TWITTER] == DUMMY_PROFILE["links"]["twitter"]


def test_sponsorship_policy_is_explicit_dummy_data():
    from field_map import DUMMY_PROFILE, SPONSORSHIP, build_value_map

    policy = DUMMY_PROFILE["work_authorization"]
    assert policy["requires_sponsorship"] == "No"
    assert policy["status"] == "US Citizen"
    assert build_value_map(DUMMY_PROFILE)[SPONSORSHIP] == "No"


def test_additional_links_answer_uses_dummy_urls_without_llm():
    from flash_leftovers import synthesize_grounded_answer
    from page_progress import is_essay_leftover

    label = "Additional LinkedIn/GitHub/Portfolio links"
    assert is_essay_leftover({"label": label, "html_type": "textarea"}) is True
    answer = synthesize_grounded_answer(label, job_context={"title": "Senior AI Engineer"})
    assert "github.com/test-dummy-account" in answer
    assert "linkedin.com/in/test-dummy-000000000" in answer


def test_already_checked_polarity():
    """ATS-010 unit: wrong pre-checked sponsorship must not skip."""
    from field_map import SPONSORSHIP
    from lever_widgets import radio_already_matches_desired

    opts = [
        {"label": "Yes, I require sponsorship", "value": "Yes", "checked": True},
        {"label": "No", "value": "No", "checked": False},
    ]
    assert radio_already_matches_desired(SPONSORSHIP, "No", opts) is False
    opts[0]["checked"] = False
    opts[1]["checked"] = True
    assert radio_already_matches_desired(SPONSORSHIP, "No", opts) is True


if __name__ == "__main__":
    test_twitter_url_classifies_and_uses_dummy_url()
    test_sponsorship_policy_is_explicit_dummy_data()
    test_additional_links_answer_uses_dummy_urls_without_llm()
    test_already_checked_polarity()
    from lever_widgets import self_test as lever_widgets_self_test

    lever_widgets_self_test()
    print("test_lever_dummy_fields: OK")
