#!/usr/bin/env python3
"""Unit tests: Workday date spin already_correct_skip + no thrash (dummy-only)."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from exp_workday_selectors import (  # noqa: E402
    _committed_spin_parts,
    _date_digits_already_correct,
    _date_spin_matches,
    _date_spin_verify,
    _display_has_committed_digits,
    _fill_date_spin,
)


def test_display_placeholder_not_committed():
    assert not _display_has_committed_digits("MM")
    assert not _display_has_committed_digits("YYYY")
    assert not _display_has_committed_digits("")
    assert _display_has_committed_digits("08")
    assert _display_has_committed_digits("2017")


def test_committed_spin_parts_from_inputs():
    rb = {
        "month_input": "08",
        "year_input": "2017",
        "month_display": "MM",
        "year_display": "YYYY",
    }
    assert _committed_spin_parts(rb) == ("08", "2017")


def test_date_spin_matches_prefilled_08_2017():
    assert _date_spin_matches("08", "08", kind="month")
    assert _date_spin_matches("8", "08", kind="month")
    assert _date_spin_matches("2017", "2017", kind="year")
    assert _date_spin_matches("01", "01", kind="month")
    assert _date_spin_matches("2023", "2023", kind="year")


def test_digits_already_correct_no_retype():
    assert _date_digits_already_correct("08", "08")
    assert _date_digits_already_correct("8", "08")
    assert _date_digits_already_correct("2017", "2017")
    assert _date_digits_already_correct("01", "01")
    assert _date_digits_already_correct("2023", "2023")


def test_should_skip_end_date_present_disabled():
    from workday_date_readback import should_skip_end_date

    assert should_skip_end_date(present_checked=True, end_enabled=False) is True
    assert should_skip_end_date(present_checked=False, end_enabled=False) is True
    assert should_skip_end_date(present_checked=True, end_enabled=True) is False
    assert should_skip_end_date(present_checked=False, end_enabled=True) is False


def test_committed_spin_parts_display_fallback():
    from workday_date_readback import committed_spin_parts, spin_part_matches

    rb = {
        "month_input": "",
        "year_input": "",
        "month_display": "08",
        "year_display": "2017",
    }
    assert committed_spin_parts(rb) == ("08", "2017")
    assert spin_part_matches("", "08", "08", kind="month")
    assert spin_part_matches("08", "MM", "08", kind="month")
    assert not spin_part_matches("01", "08", "08", kind="month")


def test_fill_date_spin_has_early_verify_and_cap():
    import exp_workday_selectors as wd

    src = inspect.getsource(_fill_date_spin)
    assert "pre_verify:already_correct_skip" in src
    assert "max_adjust_cycles" in src
    assert "already_correct_skip" in src
    assert "field_locked_skip" in src
    assert "_close_date_widget" in src
    assert "all_skipped" in src
    assert "present_disabled_end_skip" in src
    assert "max_adjust_cycles >= 2" in src
    assert "_type_month_year_via_tab" in src
    assert "month_tab_year" in src
    assert "offscreen_skip" in src
    assert "autofill_committed_skip" in src
    assert "pre_verify:committed_digits" in src
    assert "_target_for" not in src
    off_block = src.split('if tech == "offscreen_skip"')[1].split("all_skipped")[0]
    assert "optional_miss" not in off_block
    pair_src = inspect.getsource(wd._type_month_year_via_tab)
    assert 'press("Tab")' in pair_src
    assert ".fill(" not in pair_src
    assert "scrollIntoView({block:'center'" in pair_src
    type_src = inspect.getsource(wd._type_digits_into)
    assert ".fill(" not in type_src
    assert "already_correct_skip" in type_src
    assert "offscreen_skip" in type_src
    assert "scrollIntoView({block:'center'" in type_src
    ready_src = inspect.getsource(wd._wait_for_autofill_resume_ready)
    assert "wait_while_paused" not in ready_src
    assert "timeout_ms: int = 15000" in ready_src
    assert "15.0" in ready_src


def test_committed_012024_skip_lock_drops_required_dates_empty():
    """1154Z: committed 01/2024 must skip-lock and DROP formField-start/endDate."""
    from field_done import field_is_done_from_readback, filter_required_empty_from_report

    v = field_is_done_from_readback(
        {
            "month_input": "01",
            "year_input": "2024",
            "month_display": "MM",
            "year_display": "YYYY",
        },
        {"widget": "date_spin", "month": "08", "year": "2017"},
        "08/2017",
    )
    assert v.ok, v
    assert v.reason == "autofill_committed_skip"

    report = {
        "filled": [
            {
                "automation_id": "workExperience-1/startDate",
                "type": "EXPERIENCE_DATE",
                "mode": "date_spin",
                "value": "01/2024",
                "readback": "01/2024",
                "verified": True,
                "ok": True,
                "reason": "autofill_committed_skip",
                "skipped_already_correct": True,
            },
            {
                "automation_id": "workExperience-1/endDate",
                "type": "EXPERIENCE_DATE",
                "mode": "date_spin",
                "value": "12/2024",
                "readback": "12/2024",
                "verified": True,
                "ok": True,
                "reason": "already_correct_skip",
                "skipped_already_correct": True,
            },
        ],
    }
    empties = [
        {
            "id": "formField-endDate",
            "label": "To*",
            "reason": "empty_required_date_field",
        },
        {
            "id": "formField-startDate",
            "label": "From*",
            "reason": "empty_required_date_field",
        },
    ]
    kept = filter_required_empty_from_report(report, empties)
    assert kept == [], kept


def test_month_from_unclassified_is_invented_leftover():
    """NXP 0925Z: Month / Month — From* must not count as leftovers or block ADVANCE."""
    from leftover_miss_scan import demote_invented_leftovers, is_invented_leftover
    from field_done import filter_required_empty_from_report
    from workday_date_readback import is_date_spin_theater_label

    assert is_date_spin_theater_label("Month")
    assert is_date_spin_theater_label("Month — From*")
    assert is_date_spin_theater_label("Month - From*")
    assert is_date_spin_theater_label("dateSectionMonth-display")
    assert is_date_spin_theater_label("Year — From*")
    assert is_date_spin_theater_label("Year — To (Actual or Expected)")
    assert is_date_spin_theater_label("Year")
    assert not is_date_spin_theater_label("First Name*")
    report = {
        "filled": [
            {
                "automation_id": "workExperience-1/startDate",
                "mode": "date_spin",
                "type": "EXPERIENCE_DATE",
                "month": "08",
                "year": "2017",
                "readback": {"month_input": "08", "year_input": "2017"},
                "verified": True,
                "ok": True,
            }
        ],
        "leftovers": [
            {"label": "Month — From*", "reason": "unclassified"},
            {"label": "Month", "reason": "unclassified"},
            {"label": "Overall Result (GPA)", "reason": "unclassified"},
            {"label": "First Name*", "reason": "live_required_empty:empty_required_input"},
        ],
    }
    assert is_invented_leftover(report["leftovers"][0], report)
    assert is_invented_leftover(report["leftovers"][1], report)
    n = demote_invented_leftovers(report)
    assert n >= 2, n
    assert all(
        "month" not in str(u.get("label") or "").lower() for u in report["leftovers"]
    ), report["leftovers"]
    empties = [
        {"id": "dateSectionMonth-display", "label": "Month — From*", "reason": "empty_required_date_display"},
        {"id": "formField-startDate", "label": "From*", "reason": "empty_required_date_field"},
    ]
    kept = filter_required_empty_from_report(report, empties)
    assert all(
        "month" not in str(k.get("label") or "").lower() for k in kept
    ), kept


def test_verify_ignores_placeholder_display():
    """Input committed + display still MM/YYYY must verify OK (NXP thrash root)."""
    import asyncio

    class _FakeLoc:
        def __init__(self, val: str):
            self._val = val

        async def input_value(self):
            return self._val

        async def inner_text(self):
            return self._val

    class _FakePage:
        pass

    async def _fake_list(page, which, mode="any", root=None, allow_page_fallback=True):
        if which == "month":
            return [_FakeLoc("08" if mode == "from" else "01")]
        return [_FakeLoc("2017" if mode == "from" else "2023")]

    async def _fake_paired(inp):
        return None

    import exp_workday_selectors as wd

    orig_list = wd._list_date_inputs
    orig_paired = wd._paired_display_for_input
    wd._list_date_inputs = _fake_list
    wd._paired_display_for_input = _fake_paired
    try:

        async def _run():
            ok, rb = await _date_spin_verify(
                _FakePage(), "08", "2017", nth=0, from_only=True
            )
            assert ok, rb
            ok2, rb2 = await _date_spin_verify(
                _FakePage(), "01", "2023", nth=0, to_only=True
            )
            assert ok2, rb2

        asyncio.run(_run())
    finally:
        wd._list_date_inputs = orig_list
        wd._paired_display_for_input = orig_paired


def test_normalize_spin_readback_dict_to_mm_yyyy():
    from workday_date_readback import normalize_spin_readback

    rb = {
        "month_input": "08",
        "year_input": "2017",
        "month_display": "MM",
        "year_display": "YYYY",
    }
    assert normalize_spin_readback(rb) == "08/2017"
    assert normalize_spin_readback("08/2017") == "08/2017"


def test_supervisor_ok_on_date_spin_dict_readback():
    import asyncio
    import tempfile

    from action_supervisor import ActionSupervisor

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as td:
            report: dict = {"_attempt_cycle_dir": td}
            sup = ActionSupervisor(td)
            rb = {
                "month_input": "08",
                "year_input": "2017",
                "month_display": "MM",
                "year_display": "YYYY",
            }
            audit = await sup.audit_after_action(
                report,
                field="education-1-fromDate",
                field_type="EXPERIENCE_DATE",
                intent="08/2017",
                before="",
                after="",
                action="date_spin",
                page=None,
            )
            # Simulate audit_fill_row normalization path
            from action_supervisor import audit_fill_row
            from unittest.mock import MagicMock

            row = {
                "type": "EXPERIENCE_DATE",
                "mode": "date_spin",
                "widget": "date_spin",
                "readback": rb,
                "value": "08/2017",
                "verified": True,
                "ok": True,
            }
            result = await audit_fill_row(
                MagicMock(),
                report,
                row,
                intent="08/2017",
            )
            assert result is not None
            assert result["supervisor_verdict"] == "OK", result
            assert row.get("readback") == "08/2017"

    asyncio.run(_run())


def test_verify_display_fallback_when_input_placeholder():
    """Fiber often paints 08/2017 on the display while input is still MM."""
    import asyncio

    class _FakeLoc:
        def __init__(self, val: str):
            self._val = val

        async def input_value(self):
            return self._val

        async def inner_text(self):
            return self._val

    class _FakePage:
        pass

    displays = {"month": _FakeLoc("08"), "year": _FakeLoc("2017")}
    month_locs = [_FakeLoc("")]
    year_locs = [_FakeLoc("")]

    async def _fake_list2(page, which, mode="any", root=None, allow_page_fallback=True):
        return month_locs if which == "month" else year_locs

    async def _fake_paired2(inp):
        if inp is month_locs[0]:
            return displays["month"]
        return displays["year"]

    import exp_workday_selectors as wd

    orig_list = wd._list_date_inputs
    orig_paired = wd._paired_display_for_input
    wd._list_date_inputs = _fake_list2
    wd._paired_display_for_input = _fake_paired2
    try:

        async def _run():
            ok, rb = await _date_spin_verify(
                _FakePage(), "08", "2017", nth=0, from_only=True
            )
            assert ok, rb
            assert rb["month_display"] == "08"
            assert rb["year_display"] == "2017"

        asyncio.run(_run())
    finally:
        wd._list_date_inputs = orig_list
        wd._paired_display_for_input = orig_paired


def main() -> int:
    test_normalize_spin_readback_dict_to_mm_yyyy()
    test_supervisor_ok_on_date_spin_dict_readback()
    test_display_placeholder_not_committed()
    test_committed_spin_parts_from_inputs()
    test_committed_spin_parts_display_fallback()
    test_should_skip_end_date_present_disabled()
    test_date_spin_matches_prefilled_08_2017()
    test_digits_already_correct_no_retype()
    test_fill_date_spin_has_early_verify_and_cap()
    test_committed_012024_skip_lock_drops_required_dates_empty()
    test_month_from_unclassified_is_invented_leftover()
    test_verify_ignores_placeholder_display()
    test_verify_display_fallback_when_input_placeholder()
    print("test_workday_date_spin_skip: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
