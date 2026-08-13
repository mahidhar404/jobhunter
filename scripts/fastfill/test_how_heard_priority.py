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


def test_any_valid_leaf_chip_is_committed() -> None:
    """0842Z: CareerBuilder vs Glassdoor is not a fight — any valid source chip is done."""
    from verified_select import committed_how_heard_leaf, how_heard_source_committed

    cb = "1 item selected, Web - CareerBuilder"
    gd = "1 item selected, Glassdoor"
    assert how_heard_source_committed(cb, ["Glassdoor", "LinkedIn"])
    assert how_heard_source_committed(gd, ["CareerBuilder", "LinkedIn"])
    assert committed_how_heard_leaf(cb) == "CareerBuilder"
    assert committed_how_heard_leaf(gd) == "Glassdoor"
    # Empty / category / unknown agency still not done
    assert not how_heard_source_committed("0 items selected", ["Glassdoor"])
    assert not how_heard_source_committed("Internet job board", ["Glassdoor"])
    assert not how_heard_source_committed(
        "1 item selected, Antal Talent", ["LinkedIn", "Glassdoor"]
    )


def test_verified_accepts_sibling_valid_how_heard_chip() -> None:
    """Committed Glassdoor chip is done even when dummy intent was LinkedIn."""
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
    assert is_verified_fill_row(row) is True


def test_verified_rejects_unrelated_how_heard_chip() -> None:
    """Unknown agency chip must not count as a valid source (Antal Talent ≠ LinkedIn)."""
    from fill_verify import is_verified_fill_row

    row = {
        "type": "HOW_HEARD",
        "value": "LinkedIn",
        "picked": "LinkedIn",
        "readback": "1 item selected, Antal Talent",
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


def test_web_prefix_leaf_soft_match() -> None:
    from fill_verify import how_heard_option_matches_priority, pick_how_heard_from_options

    opts = ["Web - CareerBuilder", "Web - Craigslist", "Web - Indeed"]
    assert pick_how_heard_from_options(opts) == "Web - Indeed"
    assert pick_how_heard_from_options(["LinkedIn", "Web - LinkedIn", "Indeed"]) == (
        "Web - LinkedIn"
    )
    assert how_heard_option_matches_priority("CareerBuilder", "Web - CareerBuilder")
    assert how_heard_option_matches_priority("LinkedIn", "Web - LinkedIn")


def test_category_rank_prefers_website() -> None:
    from verified_select import _rank_how_heard_categories

    ranked = _rank_how_heard_categories(
        ["Advertising >", "Employee Referral >", "Website >", "Job Board >"],
        ["Website", "Job Board", "Internet job board"],
    )
    assert ranked[0].startswith("Website")


def test_dummy_internet_job_board_still_ranks_website_first() -> None:
    """Dummy HOW_HEARD category must not rank Job Board above Website."""
    from fill_verify import how_heard_category_candidates
    from verified_select import _rank_how_heard_categories

    cats = how_heard_category_candidates({"HOW_HEARD": "Internet job board"})
    assert cats[0] == "Website"
    ranked = _rank_how_heard_categories(
        ["Website >", "Job Board >", "Employee Referral >"],
        cats,
    )
    assert ranked[0].startswith("Website")


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
    test_any_valid_leaf_chip_is_committed()
    test_verified_accepts_sibling_valid_how_heard_chip()
    test_verified_rejects_unrelated_how_heard_chip()
    test_verified_accepts_priority_leaf_without_chrome()
    test_gh_select_wrong_readback_not_verified()
    test_gh_select_how_heard_priority_readback_verified()
    test_web_prefix_leaf_soft_match()
    test_category_rank_prefers_website()
    test_dummy_internet_job_board_still_ranks_website_first()
    print("test_how_heard_priority: OK")


if __name__ == "__main__":
    main()
