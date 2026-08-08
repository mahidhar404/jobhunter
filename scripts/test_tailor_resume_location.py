#!/usr/bin/env python3
"""PartyRock receives the job location with every job description."""

from partyrock_config import build_partyrock_input


def test_partyrock_input_includes_location() -> None:
    payload = build_partyrock_input(
        "Build production ML systems.",
        "Austin, TX",
    )
    assert payload.startswith("Location: Austin, TX\n")
    assert "Job Description:\nBuild production ML systems." in payload


def test_partyrock_input_marks_missing_location() -> None:
    payload = build_partyrock_input("Build production ML systems.", "")
    assert payload.startswith("Location: Unknown\n")
    assert "Job Description:\nBuild production ML systems." in payload
