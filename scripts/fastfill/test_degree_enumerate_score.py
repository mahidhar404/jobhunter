"""Regression: Degree enumerate→score must never commit A.A. for Master's.

Elanco Workday (Clinical-Laboratory-Research-Scientist): type-intended-first
+ loose soft-match scored \"Associate … Degree\" at 65 via shared token
\"degree\" while Master's rows were not yet virtualized → committed A.A.

Target path: open → enumerate options → score all → commit best ≥ threshold
(or leave blank). Dummy-only. Never submit.
"""

from __future__ import annotations

import re

from gh_select import _score_option, aliases_for
from verified_select import (
    commit_min_score_for,
    pick_best_scored_option,
    sanitized_typeahead_token,
)


# Typical Workday degree catalog (early alphabet virtualized first)
WD_DEGREE_OPTIONS = [
    "A.A.",
    "A.S.",
    "Associate of Arts (A.A.)",
    "Associate of Arts Degree",
    "Associate Degree",
    "Associate of Science (A.S.)",
    "Bachelor's Degree",
    "Bachelor of Science (B.S.)",
    "High School Diploma",
    "Master's Degree",
    "Master of Science (M.S.)",
    "M.S.",
    "Master of Arts (M.A.)",
    "Doctorate (Academic)",
]


def test_masters_aliases_drop_associate_and_bachelor():
    cands = aliases_for("DEGREE", "Master's Degree")
    assert any("Master" in c for c in cands)
    assert not any(re.search(r"bachelor|associate|\ba\.a\b", c, re.I) for c in cands)
    assert not any(re.search(r"doctor of philosophy", c, re.I) for c in cands)


def test_score_rejects_aa_and_associate_for_masters():
    for opt in (
        "A.A.",
        "A.S.",
        "Associate of Arts (A.A.)",
        "Associate of Arts Degree",
        "Associate Degree",
        "Bachelor's Degree",
    ):
        assert _score_option(opt, "Master's Degree") == 0, opt
        assert _score_option(opt, "Master") == 0, opt
        assert _score_option(opt, "M.S.") == 0, opt


def test_enumerate_score_picks_masters_when_present():
    intended = "Master's Degree"
    cands = aliases_for("DEGREE", intended)
    min_s = commit_min_score_for("DEGREE")
    assert min_s >= 70
    pick = pick_best_scored_option(
        WD_DEGREE_OPTIONS, cands, _score_option, intent=intended, min_score=min_s
    )
    assert pick is not None
    _idx, text, score = pick
    assert score >= min_s
    assert re.search(r"master|\bm\.?s\.?\b", text, re.I), text
    assert not re.search(r"associate|\ba\.a\.?\b|\ba\.s\.?\b", text, re.I), text


def test_enumerate_score_skips_when_only_associate_visible():
    """Virtualized early window: only A.* / Associate — must NOT commit."""
    intended = "Master's Degree"
    cands = aliases_for("DEGREE", intended)
    early = [
        "A.A.",
        "A.S.",
        "Associate of Arts (A.A.)",
        "Associate of Arts Degree",
        "Associate Degree",
        "Associate of Science (A.S.)",
    ]
    pick = pick_best_scored_option(
        early,
        cands,
        _score_option,
        intent=intended,
        min_score=commit_min_score_for("DEGREE"),
    )
    assert pick is None, pick


def test_enumerate_score_prefers_ms_label():
    intended = "Master of Science"
    cands = aliases_for("DEGREE", intended)
    opts = [
        "Associate Degree",
        "Bachelor's Degree",
        "Master of Science (M.S.)",
        "Master's Degree",
    ]
    pick = pick_best_scored_option(
        opts, cands, _score_option, intent=intended, min_score=70
    )
    assert pick is not None
    assert "Master" in pick[1]
    assert "Associate" not in pick[1]
    assert "Bachelor" not in pick[1]


def test_sanitized_degree_filter_token():
    tok = sanitized_typeahead_token("DEGREE", "Master's Degree", ["Master's Degree"])
    assert tok == "Master"
    assert "Degree" not in tok


def test_date_digits_already_correct_rejects_partial_and_2202():
    from exp_workday_selectors import _date_digits_already_correct

    assert _date_digits_already_correct("2022", "2022")
    assert _date_digits_already_correct("01", "1")
    assert not _date_digits_already_correct("2", "2022")
    assert not _date_digits_already_correct("22", "2022")
    assert not _date_digits_already_correct("2202", "2022")
    assert not _date_digits_already_correct("202", "2022")


if __name__ == "__main__":
    test_masters_aliases_drop_associate_and_bachelor()
    test_score_rejects_aa_and_associate_for_masters()
    test_enumerate_score_picks_masters_when_present()
    test_enumerate_score_skips_when_only_associate_visible()
    test_enumerate_score_prefers_ms_label()
    test_sanitized_degree_filter_token()
    test_date_digits_already_correct_rejects_partial_and_2202()
    print("test_degree_enumerate_score: OK")
