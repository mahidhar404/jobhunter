#!/usr/bin/env python3
"""PartyRock receives dashboard job-header context with every description."""

from partyrock_config import build_partyrock_input


def test_partyrock_input_includes_role_company_and_location() -> None:
    payload = build_partyrock_input(
        "Build production ML systems.",
        "Austin, TX",
        company="Example Corp",
        title="Generative AI Automation Engineer - Remote Job",
    )
    assert payload.startswith(
        "Role Title: Generative AI Automation Engineer - Remote Job\n"
        "Company: Example Corp\n"
        "Location: Austin, TX\n"
    )
    assert "Job Description:\nBuild production ML systems." in payload


def test_partyrock_input_marks_missing_location() -> None:
    payload = build_partyrock_input("Build production ML systems.", "")
    assert payload.startswith(
        "Role Title: Unknown\n"
        "Company: Unknown\n"
        "Location: Unknown\n"
    )
    assert "Job Description:\nBuild production ML systems." in payload
