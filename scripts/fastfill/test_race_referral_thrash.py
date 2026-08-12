"""Capco GH: race decline commit + referral=No leftover thrash regressions.

Dummy-only. Never invents EEO race. Never submits.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock


def test_race_decline_survives_country_filter_and_commits():
    """Live Capco: Decline intent must not filter options to Asian/White only.

    reject_confusable_country_option treated multi-word Decline / race labels as
    countries → pick_best returned None → ArrowDown highlight thrash, no commit.
    """
    from gh_select import _score_option, aliases_for, is_decline_like_alias
    from verified_select import (
        looks_like_country_option,
        pick_best_scored_option,
        reject_confusable_country_option,
    )

    opts = [
        "American Indian or Alaska Native",
        "Asian",
        "Black or African American",
        "Hispanic or Latino",
        "Native Hawaiian or Other Pacific Islander",
        "White",
        "Two or More Races",
        "I don't wish to answer",
    ]
    cands = aliases_for("RACE", "Decline to self identify")
    assert any(is_decline_like_alias(c) for c in cands)
    assert "I don't wish to answer" in cands

    # Decline / race prose is not a country
    assert looks_like_country_option("Decline to self identify") is False
    assert looks_like_country_option("I don't wish to answer") is False
    assert (
        reject_confusable_country_option(
            "Decline to self identify", "I don't wish to answer"
        )
        is False
    )
    assert (
        reject_confusable_country_option("Decline to self identify", "Asian") is False
    )
    # Real countries still reject
    assert reject_confusable_country_option("United States", "Australia") is True

    pick = pick_best_scored_option(
        opts, cands, _score_option, intent=cands[0], min_score=50
    )
    assert pick is not None, "must commit decline — not leave listbox open"
    idx, text, score = pick
    assert idx == 7
    assert is_decline_like_alias(text)
    assert "wish to answer" in text.lower() or "decline" in text.lower()
    assert score >= 70
    # Must not invent a race
    assert text.lower() not in {
        "asian",
        "white",
        "american indian or alaska native",
        "black or african american",
    }


def test_flash_race_never_llm_invents():
    """answer_leftover_field(RACE) returns Decline catalog — never Asian/etc."""
    from flash_leftovers import answer_leftover_field, validate_eeo_against_catalog

    val = answer_leftover_field(
        "Please identify your race or ethnicity",
        ftype="RACE",
        use_llm=True,
    )
    assert "decline" in val.lower() or "wish" in val.lower() or "prefer not" in val.lower()
    assert validate_eeo_against_catalog("RACE", "Asian").lower().find("decline") >= 0
    assert "asian" not in val.lower()


def test_employee_referral_classify_and_email_na():
    from field_map import (
        EMPLOYEE_REFERRAL,
        REFERRAL_EMAIL,
        classify_field,
        build_value_map,
        DUMMY_PROFILE,
        DUMMY_ADDRESS,
    )
    from dummy_answers import answer_for, shared_values

    yesno_lab = (
        "Were you referred to this role by a current Capco Employee? "
        "If yes, please confirm the employee's Capco email address."
    )
    ftype, _ = classify_field({"label": yesno_lab, "name": "", "id": ""})
    assert ftype == EMPLOYEE_REFERRAL, ftype

    email_lab = "Capco employee email address"
    ftype_e, _ = classify_field({"label": email_lab, "name": "", "id": ""})
    assert ftype_e == REFERRAL_EMAIL, ftype_e

    vals = shared_values()
    assert vals[EMPLOYEE_REFERRAL] == "No"
    assert vals[REFERRAL_EMAIL] == "N/A"
    assert answer_for(EMPLOYEE_REFERRAL) == "No"
    assert answer_for(REFERRAL_EMAIL) == "N/A"
    composed = build_value_map(DUMMY_PROFILE, DUMMY_ADDRESS)
    assert composed[EMPLOYEE_REFERRAL] == "No"
    assert composed[REFERRAL_EMAIL] == "N/A"


def test_flash_skips_locked_verified_referral_select():
    """Once referral=No is verified+locked, inpage Flash must not reopen it."""
    from field_lock import attach_field_locks, lock_verified_field, gate_field_action

    report: dict = {}
    attach_field_locks(report)
    label = (
        "Were you referred to this role by a current Capco Employee? "
        "If yes, please confirm the employee's Capco email address."
    )
    lock_verified_field(
        report,
        {
            "type": "EMPLOYEE_REFERRAL",
            "label": label,
            "ok": True,
            "verified": True,
            "readback": "No",
            "via": "gh_select_sweep",
        },
        field_type="EMPLOYEE_REFERRAL",
        label=label,
        readback="No",
        via="gh_select_sweep",
    )
    g = gate_field_action(
        report, field_type="EMPLOYEE_REFERRAL", label=label, selector=""
    )
    assert g is not None
    assert g.get("action") == "lock_skip"
    assert g.get("readback") == "No"

    # Same type, alternate leftover id (question_*) must still type-lock skip
    # via locked_types() in run_inpage_flash_leftovers.
    sess = report["_field_locks"]
    assert "EMPLOYEE_REFERRAL" in sess.locked_types()


def test_enumerate_race_does_not_arrowdown_thrash_without_commit():
    """Stable race menu: ≤1 ArrowDown nudge; pick declines; no open-without-commit."""

    async def _run():
        from verified_select import enumerate_listbox_options, pick_best_scored_option
        from gh_select import _score_option, aliases_for

        opts_texts = [
            "American Indian or Alaska Native",
            "Asian",
            "Black or African American",
            "White",
            "I don't wish to answer",
        ]
        arrow = {"n": 0}
        page = MagicMock()
        page.keyboard = MagicMock()

        async def _press(key):
            if key == "ArrowDown":
                arrow["n"] += 1

        page.keyboard.press = AsyncMock(side_effect=_press)
        page.wait_for_timeout = AsyncMock()

        # Minimal locator: count + nth.inner_text
        class _Loc:
            def __init__(self, texts):
                self._texts = texts

            async def count(self):
                return len(self._texts)

            def nth(self, i):
                m = MagicMock()
                m.inner_text = AsyncMock(return_value=self._texts[i])
                return m

            def first(self):
                return self

            async def evaluate(self, *_a, **_k):
                return None

        def _locator(sel):
            if ":visible" in sel or "listbox" in sel or "menu" in sel:
                box = MagicMock()
                box.count = AsyncMock(return_value=0)
                box.first = box
                return box
            return _Loc(opts_texts)

        page.locator = _locator

        _opts, texts = await enumerate_listbox_options(
            page, max_scrolls=5, timeout_ms=400
        )
        assert texts, "must read options"
        assert arrow["n"] <= 1, f"ArrowDown thrash without commit: {arrow['n']}"
        cands = aliases_for("RACE", "Decline to self identify")
        pick = pick_best_scored_option(
            texts, cands, _score_option, intent=cands[0], min_score=50
        )
        assert pick is not None
        assert "wish to answer" in pick[1].lower()

    asyncio.run(_run())


if __name__ == "__main__":
    test_race_decline_survives_country_filter_and_commits()
    test_flash_race_never_llm_invents()
    test_employee_referral_classify_and_email_na()
    test_flash_skips_locked_verified_referral_select()
    test_enumerate_race_does_not_arrowdown_thrash_without_commit()
    print("test_race_referral_thrash: OK")
