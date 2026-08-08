"""Unit tests for universal typable-dropdown algorithm (no browser)."""

from __future__ import annotations


def test_nudge_listbox_helper_exported():
    from verified_select import nudge_listbox_after_type, wait_for_option_texts

    assert callable(nudge_listbox_after_type)
    assert callable(wait_for_option_texts)


def test_how_heard_triggers_async_nudge_flag():
    """HOW_HEARD / hear-about labels must enable the async option nudge path."""
    import inspect
    from verified_select import typable_dropdown_narrow_and_click

    src = inspect.getsource(typable_dropdown_narrow_and_click)
    assert "needs_async_nudge" in src
    assert "HOW_HEARD" in src
    assert "nudge_listbox_after_type" in src


def test_location_filter_never_committed_from_input():
    from verified_select import (
        is_location_field,
        is_location_uncommitted_display,
        is_uncommitted_filter_text,
        location_display_matches,
        location_option_aliases,
        select_readback_ok,
    )

    loc = "Springfield, Illinois, United States"
    aliases = location_option_aliases(
        "Springfield", state="IL", state_full="Illinois", country="United States"
    )
    assert is_location_field("ADDRESS_CITY", "Location")
    assert is_uncommitted_filter_text(loc, "Springfield", from_input=True)
    # Matching full line is committed — skip thrash (Airwallex)
    assert location_display_matches(loc, aliases, city="Springfield")
    assert not is_location_uncommitted_display(
        loc, city="Springfield", aliases=aliases
    )
    # Non-matching place line (wrong state) — still needs list pick
    wrong = "Springfield, Ohio, United States"
    assert not location_display_matches(wrong, aliases, city="Springfield")
    assert is_location_uncommitted_display(wrong, city="Springfield", aliases=aliases)
    assert not is_location_uncommitted_display(
        loc, city="Springfield", option_clicked=True, dependent_revealed=True
    )


def test_yes_no_word_split_and_match():
    from verified_select import (
        clear_closest_match,
        rank_option_matches,
        split_select_words,
    )

    assert split_select_words("Yes") == ["Yes"]
    assert split_select_words("no") == ["No"]
    ranked = rank_option_matches(
        ["Yes", "No, I will require sponsorship"],
        ["Yes"],
    )
    clear = clear_closest_match(ranked, at_last_word=True)
    assert clear is not None
    assert clear[1] == "Yes"


def test_springfield_narrowing_prefers_illinois():
    from verified_select import clear_closest_match, rank_option_matches

    opts = [
        "Springfield, Illinois, United States",
        "Springfield, Ohio, United States",
        "Springfield, Massachusetts, United States",
    ]
    # Ambiguous when only city token is alias
    ranked_amb = rank_option_matches(opts, ["Springfield"])
    assert clear_closest_match(ranked_amb, at_last_word=False) is None
    # Full target aliases → Illinois wins at last word
    ranked_il = rank_option_matches(
        opts,
        ["Springfield, Illinois, United States"],
    )
    clear = clear_closest_match(ranked_il, at_last_word=True)
    assert clear is not None
    assert "Illinois" in clear[1]


def test_normalize_llm_essay_to_yes_no():
    from verified_select import normalize_select_answer

    essay = "Yes, I am currently based in Illinois (Springfield, IL)."
    assert (
        normalize_select_answer(
            "Are you currently based in any of these states?\nIllinois",
            essay,
            field_type="LOCATION",
        )
        == "Yes"
    )
    assert (
        normalize_select_answer(
            "Will you require immigration sponsorship?",
            "No, I will not require sponsorship for employment.",
            field_type="SPONSORSHIP",
        )
        == "No"
    )


def test_placeholder_and_readback_guards():
    from verified_select import (
        is_placeholder_select_value,
        is_uncommitted_filter_text,
        select_readback_ok,
    )

    assert is_placeholder_select_value("Select...")
    assert is_placeholder_select_value("Select one")
    essay = "Yes, I am currently based in Illinois (Springfield, IL)."
    assert is_uncommitted_filter_text(essay, essay)
    assert not select_readback_ok(essay, ["Yes", "No"], typed_frag=essay)
    assert select_readback_ok("Yes", ["Yes", "No"])


def test_location_aliases_and_search_query():
    from verified_select import location_option_aliases, location_search_query

    assert location_search_query("Springfield, IL, USA") == "Springfield"
    aliases = location_option_aliases(
        "Springfield", state="IL", country="United States"
    )
    assert any("Illinois" in a for a in aliases)
    assert aliases[0].startswith("Springfield")


def test_self_test_runs():
    from verified_select import self_test

    self_test()


def test_ats3_005_soft_value_match_gender_polarity():
    """ATS3-005: Male must not soft-match Female (shared matcher)."""
    from verified_select import soft_value_match, value_matches_readback

    assert soft_value_match("Male", "Female") is False
    assert soft_value_match("Female", "Male") is False
    assert soft_value_match("Male", "Male") is True
    assert soft_value_match("man", "woman") is False
    assert soft_value_match("Male", "Prefer male") is True
    assert value_matches_readback("Male", "Female") is False


def test_ats3_007_expanded_confusable_states():
    from verified_select import reject_confusable_state_option, soft_value_match

    assert reject_confusable_state_option("VA", "Vermont")
    assert reject_confusable_state_option("MI", "Minnesota")
    assert reject_confusable_state_option("CO", "Connecticut")
    assert soft_value_match("Virginia", "Vermont") is False
    assert soft_value_match("Michigan", "Minnesota") is False


def test_ats3_008_clear_closest_rejects_weak_scores():
    from verified_select import clear_closest_match

    ranked = [(42, 0, "Somewhat related school")]
    assert clear_closest_match(ranked, at_last_word=True, intent="Target University") is None
    ranked_ok = [(100, 0, "Target University")]
    clear = clear_closest_match(ranked_ok, at_last_word=True, intent="Target University")
    assert clear is not None and clear[1] == "Target University"


def test_ats3_013_early_unique_high_and_full_first():
    """ATS3-013: unique high score early-exits; ATS3-008 weak floors stay."""
    import inspect
    from verified_select import (
        _early_unique_high_match,
        clear_closest_match,
        typable_dropdown_narrow_and_click,
    )

    # Unique high → early commit candidate
    ranked = [(92, 0, "Springfield, Illinois, United States"), (40, 1, "Springfield, MA")]
    early = _early_unique_high_match(ranked, intent="Springfield, Illinois, United States")
    assert early is not None and early[0] == 0

    # Ambiguous mid scores → no early (avoid wrong pick)
    amb = [(72, 0, "University A"), (68, 1, "University B")]
    assert _early_unique_high_match(amb, intent="University A", min_score=80) is None

    # ATS3-008 floors still reject weak last-word
    weak = [(42, 0, "Somewhat related school")]
    assert clear_closest_match(weak, at_last_word=True, intent="Target University") is None

    src = inspect.getsource(typable_dropdown_narrow_and_click)
    assert "full_string_first" in src or "full_first" in src
    assert "_append_into_filter" in src
    assert "_early_unique_high_match" in src


def test_ats3_006_fiber_js_uses_token_bound():
    """ATS3-006: fiber scoring uses tokenBound, not raw substring +20."""
    from verified_select import _FIBER_SEARCH_SELECT_JS

    assert "tokenBound" in _FIBER_SEARCH_SELECT_JS
    assert "ATS3-006" in _FIBER_SEARCH_SELECT_JS
    # Must not still use the old raw includes bonus alone
    assert "else if (ot.includes(cl) || cl.includes(ot)) s += 20" not in _FIBER_SEARCH_SELECT_JS
    assert "tokenBound(cl, ot) || tokenBound(ot, cl)" in _FIBER_SEARCH_SELECT_JS


def test_ats3_016_default_score_gender_safe():
    """ATS3-016: Male must not score against Female via soft path."""
    from verified_select import _default_score_option

    assert _default_score_option("Female", "Male") == 0
    assert _default_score_option("Male", "Female") == 0
    assert _default_score_option("Male", "Male") >= 80


def test_ats3_012_ashby_no_escape_after_location():
    """ATS3-012: ashby Location settle uses Tab + zip wait, not Escape."""
    import inspect
    from ashby_widgets import fill_ashby_location_then_zip

    src = inspect.getsource(fill_ashby_location_then_zip)
    assert "ATS3-012" in src
    assert 'press("Tab")' in src or "press('Tab')" in src
    assert "Escape" not in src or "cancel dependent" in src.lower()
    # Must not press Escape after option click
    assert 'press("Escape")' not in src
    assert "press_escape" not in src


def test_ats2_017_commit_probe_prefers_tab_not_escape():
    """ATS2-017: fill_typable_dropdown uses Tab when commit_probe armed."""
    import inspect
    from verified_select import fill_typable_dropdown

    src = inspect.getsource(fill_typable_dropdown)
    assert "commit_probe is not None" in src
    assert 'press("Tab")' in src or "press('Tab')" in src
    # Escape path remains for non-probe fields (CAPTCHA-gated)
    assert "press_escape_unless_captcha" in src


def test_ats3_001_ashby_commit_requires_option_or_dependent():
    """ATS3-001: display match alone must not force Location committed."""
    import inspect
    from ashby_widgets import fill_ashby_location_then_zip

    src = inspect.getsource(fill_ashby_location_then_zip)
    assert "ATS3-001" in src
    assert "if not committed and location_display_matches" in src
    idx = src.index("if not committed and location_display_matches")
    chunk = src[idx : idx + 900]
    assert 'loc_result.get("option_clicked")' in chunk
    assert 'loc_result.get("dependent_revealed")' in chunk
    assert chunk.index("option_clicked") < chunk.index("committed = True")

def test_ats2_017_classify_zip_miss_taxonomy():
    """ATS2-017: HTML zip question ⇒ zip_dependent_never_revealed (not absent)."""
    from ashby_widgets import classify_ashby_zip_miss

    assert (
        classify_ashby_zip_miss(html_has_zip_question=True)
        == "zip_dependent_never_revealed"
    )
    assert (
        classify_ashby_zip_miss(html_has_zip_question=False)
        == "zip_field_not_found_after_location"
    )


def test_ats2_017_await_zip_caps_scroll_budget():
    """ATS2-017: scroll budget capped; event-driven wait; no forever wheel."""
    import inspect
    from ashby_widgets import _await_zip_after_location, _scroll_toward_zip_hint

    src = inspect.getsource(_await_zip_after_location)
    assert "max_scrolls" in src
    assert "scroll_budget" in src
    assert "_wait_ashby_zip_dom_event" in src
    assert "allow_wheel" in src
    # Must not alternate endless mouse.wheel every odd attempt
    assert "attempt % 2" not in src
    scroll_src = inspect.getsource(_scroll_toward_zip_hint)
    assert "allow_wheel" in scroll_src


def test_ats2_017_reopen_location_and_honest_leftover():
    """ATS2-017: re-open Location once; honest leftover never early N/A."""
    import asyncio
    import inspect
    from unittest.mock import AsyncMock, MagicMock

    from ashby_widgets import fill_ashby_location_then_zip
    import ashby_widgets as aw
    from field_map import ADDRESS_CITY, ADDRESS_COUNTRY, ADDRESS_STATE, ADDRESS_ZIP

    src = inspect.getsource(fill_ashby_location_then_zip)
    assert "_reopen_location_for_zip_reveal" in src
    assert "classify_ashby_zip_miss" in src
    assert "zip_dependent_never_revealed" in src or "classify_ashby_zip_miss" in src

    page = MagicMock()
    page.content = AsyncMock(
        return_value="<div>What is your home zip code?</div><div>Location</div>"
    )
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.mouse = MagicMock()
    page.mouse.wheel = AsyncMock()
    page.locator = MagicMock(return_value=MagicMock())
    combo = MagicMock()
    combo.count = AsyncMock(return_value=0)
    page.locator.return_value.first = combo

    async def _none(*a, **k):
        return None

    async def _present(*a, **k):
        return True

    async def _run():
        orig_wait = aw._wait_ashby_zip_input
        orig_await = aw._await_zip_after_location
        orig_present = aw._ashby_zip_field_present
        orig_reopen = aw._reopen_location_for_zip_reveal
        orig_dom = aw._try_fill_zip_via_dom
        aw._wait_ashby_zip_input = _none
        aw._await_zip_after_location = _none
        aw._ashby_zip_field_present = _present

        async def _reopen(*a, **k):
            return {"ok": False, "option_clicked": False, "reason": "location_reopen_for_zip"}

        async def _dom_fail(*a, **k):
            return False, ""

        aw._reopen_location_for_zip_reveal = _reopen
        aw._try_fill_zip_via_dom = _dom_fail
        try:
            return await fill_ashby_location_then_zip(
                page,
                {
                    ADDRESS_ZIP: "62704",
                    ADDRESS_CITY: "Springfield",
                    ADDRESS_STATE: "IL",
                    ADDRESS_COUNTRY: "United States",
                },
            )
        finally:
            aw._wait_ashby_zip_input = orig_wait
            aw._await_zip_after_location = orig_await
            aw._ashby_zip_field_present = orig_present
            aw._reopen_location_for_zip_reveal = orig_reopen
            aw._try_fill_zip_via_dom = orig_dom

    rows = asyncio.run(_run())
    zip_rows = [r for r in rows if r.get("type") == ADDRESS_ZIP]
    assert zip_rows, f"expected zip leftover row, got {rows!r}"
    zr = zip_rows[-1]
    assert zr.get("ok") is False
    assert zr.get("reason") == "zip_dependent_never_revealed"
    assert zr.get("not_applicable") is False
    assert zr.get("location_reopened") is True
    assert not any(r.get("reason") == "zip_field_absent_on_form" for r in zip_rows)


def test_ats3_002_zip_present_when_html_has_question():
    """ATS3-002: HTML zip question ⇒ present even before input mounts."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from ashby_widgets import _ashby_zip_field_present
    import ashby_widgets as aw

    page = MagicMock()
    page.content = AsyncMock(
        return_value="<div>What is your home zip code?</div>"
    )

    async def _none(*a, **k):
        return None

    async def _run():
        orig = aw._wait_ashby_zip_input
        aw._wait_ashby_zip_input = _none
        try:
            return await _ashby_zip_field_present(page)
        finally:
            aw._wait_ashby_zip_input = orig

    assert asyncio.run(_run()) is True


def test_ashby_zip_heading_entry_selector():
    """Ashby tenants (Airwallex) use div._heading_ not always <label> for zip."""
    from ashby_widgets import _ASHBY_ZIP_ENTRY, _html_has_ashby_zip_question

    html = (
        '<div class="ashby-application-form-field-entry">'
        '<div class="_heading_abc">What is your home zip code?</div>'
        '<input name="field-uuid" />'
        "</div>"
    )
    assert _html_has_ashby_zip_question(html)
    assert "_heading_" in _ASHBY_ZIP_ENTRY


def test_expand_state_value_local():
    from verified_select import expand_state_value, value_matches_readback

    assert expand_state_value("IL") == ["Illinois", "IL"]
    assert expand_state_value("Illinois") == ["Illinois", "IL"]
    assert value_matches_readback("IL", "Illinois") is True
    assert value_matches_readback("IL", "Idaho") is False


def test_ats001_filtered_index_remap():
    """Click index must be original Illinois index, not filtered[0]."""
    from verified_select import (
        clear_closest_match,
        filter_options_preserving_indices,
        rank_option_matches,
        remap_ranked_to_original,
    )

    texts = ["Idaho", "Illinois"]
    filtered, orig = filter_options_preserving_indices(texts, "Illinois")
    assert filtered == ["Illinois"]
    assert orig == [1]
    remapped = remap_ranked_to_original(
        rank_option_matches(filtered, ["Illinois"]), orig
    )
    clear = clear_closest_match(remapped, at_last_word=True, intent="Illinois")
    assert clear is not None and clear[0] == 1 and clear[1] == "Illinois"


def test_ats2_001_placeholder_slots_preserve_locator_index():
    """ATS2-001: empty/Select… slots must keep Illinois at locator nth(2)."""
    from verified_select import (
        clear_closest_match,
        filter_options_preserving_indices,
        is_placeholder_select_value,
        rank_option_matches,
        remap_ranked_to_original,
    )

    # Mimic wait_for_option_texts after ATS2-001 fix (placeholders → "")
    raw = ["Select...", "Idaho", "Illinois"]
    texts = ["" if (not t or is_placeholder_select_value(t)) else t for t in raw]
    assert texts == ["", "Idaho", "Illinois"]
    filtered, orig = filter_options_preserving_indices(texts, "Illinois")
    assert filtered == ["Illinois"]
    assert orig == [2]
    remapped = remap_ranked_to_original(
        rank_option_matches(filtered, ["Illinois", "IL"]), orig
    )
    clear = clear_closest_match(remapped, at_last_word=True, intent="Illinois")
    assert clear is not None
    assert clear[0] == 2 and clear[1] == "Illinois"


if __name__ == "__main__":
    test_location_filter_never_committed_from_input()
    test_yes_no_word_split_and_match()
    test_springfield_narrowing_prefers_illinois()
    test_normalize_llm_essay_to_yes_no()
    test_placeholder_and_readback_guards()
    test_location_aliases_and_search_query()
    test_ats3_005_soft_value_match_gender_polarity()
    test_ats3_007_expanded_confusable_states()
    test_ats3_008_clear_closest_rejects_weak_scores()
    test_ats3_013_early_unique_high_and_full_first()
    test_ats3_006_fiber_js_uses_token_bound()
    test_ats3_016_default_score_gender_safe()
    test_ats3_012_ashby_no_escape_after_location()
    test_ats2_017_commit_probe_prefers_tab_not_escape()
    test_ats2_017_classify_zip_miss_taxonomy()
    test_ats2_017_await_zip_caps_scroll_budget()
    test_ats2_017_reopen_location_and_honest_leftover()
    test_ats3_001_ashby_commit_requires_option_or_dependent()
    test_ats3_002_zip_present_when_html_has_question()
    test_ashby_zip_heading_entry_selector()
    test_expand_state_value_local()
    test_ats001_filtered_index_remap()
    test_ats2_001_placeholder_slots_preserve_locator_index()
    test_self_test_runs()
    print("test_verified_select: OK")
