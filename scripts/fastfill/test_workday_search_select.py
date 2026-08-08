"""Unit tests for ChamPro-style fiber searchSelect (no live browser)."""

from __future__ import annotations

import inspect


def test_fiber_search_select_exported():
    from verified_select import fiber_search_select, nudge_listbox_after_type

    assert callable(fiber_search_select)
    assert callable(nudge_listbox_after_type)


def test_fiber_search_select_js_calls_tab_onkeydown():
    """Root ChamPro trick: fiber onKeyDown with key Tab (not typing alone)."""
    from verified_select import _FIBER_SEARCH_SELECT_JS

    src = _FIBER_SEARCH_SELECT_JS
    assert "__reactProps" in src
    assert "onKeyDown" in src
    assert "Tab" in src
    assert "promptOption" in src
    assert "ambiguous" in src


def test_typable_dropdown_prefers_fiber_for_how_heard():
    from verified_select import typable_dropdown_narrow_and_click

    src = inspect.getsource(typable_dropdown_narrow_and_click)
    assert "fiber_search_select" in src
    assert "prefer_fiber" in src
    assert "HOW_HEARD" in src


def test_how_heard_fill_uses_fiber_mode():
    import exp_workday_selectors as wd

    src = inspect.getsource(wd._fill_how_heard)
    assert "fiber_search_select" in src
    assert 'mode": "fiber_search_select"' in src or "fiber_search_select" in src


def test_previous_worker_scopes_include_quantiphi_wording():
    import exp_workday_selectors as wd

    sels = " ".join(wd.WD_CONTACT_SELECTORS.get("worked_here_before", []))
    assert "Have you been employed" in sels or "employed by Quantiphi" in sels
    radio_src = inspect.getsource(wd._fill_radio_yes_no)
    assert "_verify_radio_checked" in radio_src
    assert "Have you been employed" in radio_src
