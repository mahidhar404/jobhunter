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


def test_typable_dropdown_how_heard_does_not_type_as_commit():
    """MCP NXP: How-Heard is click → category → leaf → chip, never fiber type."""
    from verified_select import fill_workday_combobox, typable_dropdown_narrow_and_click

    src = inspect.getsource(typable_dropdown_narrow_and_click)
    assert "HOW_HEARD" in src
    assert "skip_type" in src
    prefer = inspect.getsource(typable_dropdown_narrow_and_click)
    # prefer_fiber must not include HOW_HEARD (SOURCE/SCHOOL/STATE only)
    assert '"HOW_HEARD"' not in prefer.split("prefer_fiber")[1].split(")")[0]
    combo = inspect.getsource(fill_workday_combobox)
    assert "fill_hierarchical_how_heard" in combo
    assert "how_heard_no_chip" in combo


def test_how_heard_fill_uses_hierarchical_mode():
    import exp_workday_selectors as wd

    src = inspect.getsource(wd._fill_how_heard)
    assert "fill_hierarchical_how_heard" in src
    assert "hierarchical_how_heard" in src
    # Stop alias thrash once chip committed
    assert "_probe_how_heard_already_committed" in src
    assert "already_correct_keep" in src
    assert "settle_open_listbox" in src
    assert "readback_mismatch_picked" in src


def test_how_heard_probe_keep_exported():
    from exp_workday_selectors import (
        _probe_how_heard_already_committed,
        _read_how_heard_display,
    )
    from verified_select import how_heard_source_committed, settle_open_listbox

    assert callable(_probe_how_heard_already_committed)
    assert callable(_read_how_heard_display)
    assert callable(how_heard_source_committed)
    assert callable(settle_open_listbox)
    assert how_heard_source_committed(
        "How Did You Hear About Us?*\n1 item selected, Indeed",
        ["Indeed", "LinkedIn"],
    )
    # Valid sibling leaf is done (CareerBuilder vs Indeed) — unknown agency is not.
    assert how_heard_source_committed("1 item selected, CareerBuilder", ["Indeed"])
    assert not how_heard_source_committed("1 item selected, Antal Talent", ["Indeed"])
    assert not how_heard_source_committed("0 items selected", ["Indeed"])
    assert not how_heard_source_committed("", ["Indeed"])


def test_fiber_settles_menu_after_pick():
    """After fiber searchSelect success, Escape settle so menu is not left open."""
    import inspect

    from verified_select import fiber_search_select

    src = inspect.getsource(fiber_search_select)
    assert "settle_open_listbox" in src


def test_picked_option_not_uncommitted_filter():
    """Indeed chip / picked label must not look like typed filter thrash."""
    from verified_select import is_uncommitted_filter_text

    assert not is_uncommitted_filter_text(
        "Indeed", "Indeed", picked="Indeed", from_input=True
    )
    assert is_uncommitted_filter_text(
        "Indeed", "Indeed", picked=None, from_input=True
    )
    assert not is_uncommitted_filter_text(
        "1 item selected, Indeed", "Indeed", picked="Indeed", from_input=True
    )


def test_previous_worker_scopes_include_quantiphi_wording():
    import exp_workday_selectors as wd

    sels = " ".join(wd.WD_CONTACT_SELECTORS.get("worked_here_before", []))
    assert "Have you been employed" in sels or "employed by Quantiphi" in sels
    radio_src = inspect.getsource(wd._fill_radio_yes_no)
    assert "_verify_radio_checked" in radio_src
    assert "Have you been employed" in radio_src


if __name__ == "__main__":
    test_fiber_search_select_exported()
    test_fiber_search_select_js_calls_tab_onkeydown()
    test_typable_dropdown_how_heard_does_not_type_as_commit()
    test_how_heard_fill_uses_hierarchical_mode()
    test_how_heard_probe_keep_exported()
    test_fiber_settles_menu_after_pick()
    test_picked_option_not_uncommitted_filter()
    test_previous_worker_scopes_include_quantiphi_wording()
    print("test_workday_search_select: OK")
