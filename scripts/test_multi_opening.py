#!/usr/bin/env python3
"""Tests for scripts/multi_opening.py."""
from multi_opening import detect_multi_opening


def test_positive_phrases():
    assert detect_multi_opening(description="Multiple positions available.")
    assert detect_multi_opening(description="We have multiple openings!")
    assert detect_multi_opening(description="We are hiring for multiple roles across the team.")
    assert detect_multi_opening(description="We're hiring multiple Database Engineers.")
    assert detect_multi_opening(description="recruiting for multiple positions across levels")
    assert detect_multi_opening(description="fill multiple Data Scientists openings to help")
    assert detect_multi_opening(description="Several openings may be filled from this announcement.")
    assert detect_multi_opening(description="More than one position may be filled.")
    assert detect_multi_opening(description="Sr. Data Scientist (2 openings)")
    assert detect_multi_opening(description="Number of Openings Available: 3")
    assert detect_multi_opening(title="Data Scientist [Multiple Positions Available]")
    assert detect_multi_opening(title="Staff/Senior Agentic AI Engineers (Multiple roles)")


def test_false_positives():
    assert not detect_multi_opening(
        description="Enthusiasm for taking on multiple roles and responsibilities"
    )
    assert not detect_multi_opening(description="Number of Openings Available 1")
    assert not detect_multi_opening(description="We work with multiple data sources daily.")
    assert not detect_multi_opening(description="This role has one opening.")
    assert not detect_multi_opening(title="Senior Data Scientist", description="")
    assert not detect_multi_opening(title="", description="")


def test_case_insensitive():
    assert detect_multi_opening(description="MULTIPLE POSITIONS MAY BE FILLED.")


if __name__ == "__main__":
    test_positive_phrases()
    test_false_positives()
    test_case_insensitive()
    print("ok")
