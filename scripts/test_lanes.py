#!/usr/bin/env python3
"""Lane gate + INR / multi-currency salary tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from discovery_filters import (  # noqa: E402
    auto_delete_reason,
    extract_inr_salary,
    extract_native_salary,
    lane_for_job,
    listing_matches_lanes,
    normalize_regions,
)


def test_normalize_legacy_us():
    assert normalize_regions(["us"]) == ("worldwide",)
    assert normalize_regions(["india", "us"]) == ("india", "worldwide")


def test_lane_for_job():
    assert lane_for_job("Bengaluru") == "india"
    assert lane_for_job("Remote - India") == "india"
    assert lane_for_job("Berlin, Germany") == "worldwide"
    assert lane_for_job("Remote") == "worldwide"
    assert lane_for_job("San Francisco, CA", work_mode="remote") == "worldwide"
    assert lane_for_job("San Francisco, CA", work_mode="onsite") == "unknown"
    assert lane_for_job("Austin, TX", work_mode="hybrid") == "unknown"


def test_listing_matches_lanes():
    assert listing_matches_lanes("Bengaluru", ["india"])
    assert not listing_matches_lanes("Bengaluru", ["worldwide"])
    assert listing_matches_lanes("Berlin", ["worldwide"], work_mode="onsite")
    assert not listing_matches_lanes(
        "New York, NY", ["worldwide"], work_mode="onsite", description="on-site office"
    )
    assert listing_matches_lanes(
        "Remote, US", ["worldwide"], work_mode="remote"
    )


def test_auto_delete_us_onsite():
    assert auto_delete_reason(
        title="Engineer",
        location="Austin, TX",
        description="Hybrid role in office",
        work_mode="hybrid",
    ) == "us_onsite_or_hybrid"


def test_inr_and_native_salary():
    inr = extract_inr_salary(description="CTC 12-18 LPA")
    assert inr and inr["min_lpa"] == 12 and inr["currency"] == "INR"
    inr2 = extract_inr_salary(description="Salary ₹15,00,000")
    assert inr2 and abs(inr2["min_lpa"] - 15) < 0.01
    nat = extract_native_salary(description="Salary €70,000 - €90,000")
    assert nat and nat["currency"] == "EUR"


if __name__ == "__main__":
    test_normalize_legacy_us()
    test_lane_for_job()
    test_listing_matches_lanes()
    test_auto_delete_us_onsite()
    test_inr_and_native_salary()
    print("ok: lane + salary tests passed")
