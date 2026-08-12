#!/usr/bin/env python3
"""HOW_HEARD source priority — first matching dropdown option wins (dummy only)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def test_priority_list_order() -> None:
    from fill_verify import HOW_HEARD_SOURCE_PRIORITY, how_heard_priority_labels

    labels = how_heard_priority_labels()
    assert labels[0] == "LinkedIn"
    assert labels[1] == "Indeed"
    assert "BuiltIn" in labels
    assert labels[-1] == "Other"
    assert HOW_HEARD_SOURCE_PRIORITY[0][0] == "LinkedIn"


def test_pick_linkedin_over_glassdoor_other() -> None:
    from fill_verify import pick_how_heard_from_options

    opts = ["LinkedIn", "Glassdoor", "Other"]
    assert pick_how_heard_from_options(opts) == "LinkedIn"


def test_pick_linkedin_over_indeed() -> None:
    from fill_verify import pick_how_heard_from_options

    opts = ["Indeed", "LinkedIn"]
    assert pick_how_heard_from_options(opts) == "LinkedIn"


def test_pick_indeed_when_linkedin_missing() -> None:
    from fill_verify import pick_how_heard_from_options

    opts = ["Indeed", "Glassdoor", "Other"]
    assert pick_how_heard_from_options(opts) == "Indeed"


def test_pick_builtin_aliases() -> None:
    from fill_verify import pick_how_heard_from_options

    assert pick_how_heard_from_options(["Built In", "Other"]) == "Built In"
    assert pick_how_heard_from_options(["builtin.com", "Other"]) == "builtin.com"


def test_miss_falls_back_to_other() -> None:
    from fill_verify import pick_how_heard_from_options

    assert pick_how_heard_from_options(["Employee Referral", "Other"]) == "Other"
    assert pick_how_heard_from_options(["Campus Recruiting", "Other"]) == "Other"


def test_honest_miss_when_no_match() -> None:
    from fill_verify import pick_how_heard_from_options

    assert pick_how_heard_from_options(["Employee Referral", "Campus"]) is None
    assert pick_how_heard_from_options([]) is None


def test_job_board_generic_when_only_boards() -> None:
    from fill_verify import pick_how_heard_from_options

    assert pick_how_heard_from_options(["Job Board", "Social Media"]) == "Job Board"


def test_candidates_follow_priority() -> None:
    from fill_verify import how_heard_candidates, how_heard_leaf_candidates

    leaves = how_heard_leaf_candidates()
    assert leaves[:3] == ["LinkedIn", "Indeed", "BuiltIn"]

    cands = how_heard_candidates({"HOW_HEARD": "Internet job board"})
    assert cands[0] == "LinkedIn"
    assert "Internet job board" in cands


def test_gh_aliases_for_uses_priority() -> None:
    from gh_select import aliases_for

    cands = aliases_for("HOW_HEARD", "Internet job board")
    assert cands[0] == "LinkedIn"
    assert "Indeed" in cands
    assert "Other" in cands


def test_verified_rejects_unrelated_how_heard_chip() -> None:
    """Wrong committed chip must not count as filled (Glassdoor when LinkedIn intended)."""
    from fill_verify import is_verified_fill_row

    row = {
        "type": "HOW_HEARD",
        "value": "LinkedIn",
        "picked": "LinkedIn",
        "readback": "1 item selected, Glassdoor",
        "option_clicked": True,
        "verified": True,
        "ok": True,
    }
    assert is_verified_fill_row(row) is False


def test_verified_accepts_priority_leaf_without_chrome() -> None:
    from fill_verify import is_verified_fill_row

    row = {
        "type": "HOW_HEARD",
        "value": "Internet job board",
        "picked": "LinkedIn",
        "readback": "LinkedIn",
        "verified": True,
        "ok": True,
    }
    assert is_verified_fill_row(row) is True


def test_gh_select_wrong_readback_not_verified() -> None:
    from fill_verify import is_verified_fill_row

    row = {
        "type": "DEGREE",
        "via": "gh_select",
        "value": "Master's Degree",
        "picked": "A.A.",
        "shown": "A.A.",
        "verified": True,
        "ok": True,
        "aliases_tried": ["Master's Degree", "Master of Science"],
    }
    assert is_verified_fill_row(row) is False


def test_gh_select_how_heard_priority_readback_verified() -> None:
    """GH commits LinkedIn while dummy value is Internet job board category."""
    from fill_verify import is_verified_fill_row

    row = {
        "type": "HOW_HEARD",
        "via": "gh_select",
        "value": "Internet job board",
        "picked": "LinkedIn",
        "shown": "LinkedIn",
        "verified": True,
        "ok": True,
        "aliases_tried": ["LinkedIn", "Indeed", "Internet job board"],
    }
    assert is_verified_fill_row(row) is True


def main() -> None:
    test_priority_list_order()
    test_pick_linkedin_over_glassdoor_other()
    test_pick_linkedin_over_indeed()
    test_pick_indeed_when_linkedin_missing()
    test_pick_builtin_aliases()
    test_miss_falls_back_to_other()
    test_honest_miss_when_no_match()
    test_job_board_generic_when_only_boards()
    test_candidates_follow_priority()
    test_gh_aliases_for_uses_priority()
    test_verified_rejects_unrelated_how_heard_chip()
    test_verified_accepts_priority_leaf_without_chrome()
    test_gh_select_wrong_readback_not_verified()
    test_gh_select_how_heard_priority_readback_verified()
    print("test_how_heard_priority: OK")


if __name__ == "__main__":
    main()
