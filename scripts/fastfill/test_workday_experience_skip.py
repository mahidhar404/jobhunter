#!/usr/bin/env python3
"""Unit tests: My Experience verify-before-touch — zero thrash on second pass."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def test_action_judge_thrash_rewrite():
    from action_judge import judge_field_action

    row = judge_field_action(
        field="workExperience-1/jobTitle",
        before="Applied AI/ML Analyst",
        after="Senior ML Engineer",
        intent="Applied AI/ML Analyst",
        action="fill",
    )
    assert row["verdict"] == "thrash_rewrite"
    assert row["thrash"] is True

    skip = judge_field_action(
        field="workExperience-1/jobTitle",
        before="Applied AI/ML Analyst",
        after="Applied AI/ML Analyst",
        intent="Applied AI/ML Analyst",
        action="fill",
    )
    assert skip["verdict"] == "correct_skip"
    assert skip["thrash"] is False


def test_action_judge_autofill_committed():
    from action_judge import judge_field_action

    row = judge_field_action(
        field="workExperience-1/company",
        before="NXP Semiconductors",
        after="NXP Semiconductors",
        intent="Example Corp",
        action="fill",
    )
    assert row["verdict"] == "wrong_autofill"
    assert row["reason"] == "autofill_mismatch_intent"


async def _run_experience_text_skip():
    import exp_workday_selectors as wd

    fill_calls: list[str] = []

    class _Loc:
        def __init__(self, value: str):
            self._value = value

        async def count(self):
            return 1

        async def is_visible(self, timeout=0):
            return True

        async def evaluate(self, _js):
            return "input"

        async def get_attribute(self, name):
            if name == "role":
                return ""
            if name == "type":
                return "text"
            return ""

        async def input_value(self):
            return self._value

        def locator(self, _sel):
            return self

        async def fill(self, val, **_k):
            fill_calls.append(val)

        async def click(self, **_k):
            pass

        async def scroll_into_view_if_needed(self, **_k):
            pass

    class _First:
        def __init__(self, loc=None):
            self._loc = loc

        async def count(self):
            return 1 if self._loc is not None else 0

    class _Chain:
        def __init__(self, loc=None):
            self._loc = loc

        @property
        def first(self):
            return self

        async def count(self):
            return 1 if self._loc is not None else 0

        def locator(self, _sel):
            return _Chain(self._loc)

        def nth(self, _i):
            return self

        async def input_value(self):
            return self._loc._value if self._loc else ""

        async def click(self, **_k):
            pass

        async def fill(self, val, **_k):
            fill_calls.append(val)

    class _Page:
        def locator(self, sel):
            if "jobTitle" in sel:
                return _Chain(_Loc("Applied AI/ML Analyst"))
            if "company" in sel:
                return _Chain(_Loc("Example Corp"))
            return _Chain(None)

        def get_by_label(self, _pat):
            return _Chain(None)

    page = _Page()
    scope = _Chain(_Loc("Applied AI/ML Analyst"))
    report: dict = {}

    async def fake_read(loc):
        if hasattr(loc, "_loc") and loc._loc and hasattr(loc._loc, "_value"):
            return loc._loc._value
        if hasattr(loc, "_value"):
            return loc._value
        return ""

    with patch.object(wd, "_read_field_value", fake_read):
        with patch.object(wd, "_resolve_experience_locator") as resolve:
            async def _resolve(_page, _scope, _ps, _idx, aid):
                vals = {
                    "jobTitle": "Applied AI/ML Analyst",
                    "company": "Example Corp",
                    "location": "Springfield, Illinois, United States",
                }
                return _Chain(_Loc(vals.get(aid, "")))

            resolve.side_effect = _resolve
            r1 = await wd._fill_experience_text_field(
                page,
                scope,
                page_scope=False,
                idx=1,
                aid="jobTitle",
                val="Applied AI/ML Analyst",
                report=report,
            )
            assert r1.get("reason") == "already_correct_skip", r1
            r2 = await wd._fill_experience_text_field(
                page,
                scope,
                page_scope=False,
                idx=1,
                aid="company",
                val="Other Corp",
                report=report,
            )
            # Example Corp is dummy — skip+lock even when parser intent differs.
            assert r2.get("reason") == "autofill_committed_skip", r2
            assert r2.get("verified") is True
            assert r2.get("skipped_already_correct") is True
            assert r2.get("status") == "filled"
            r_loc = await wd._fill_experience_text_field(
                page,
                scope,
                page_scope=False,
                idx=1,
                aid="location",
                val="Austin, TX",
                report=report,
            )
            assert r_loc.get("reason") in (
                "already_correct_skip",
                "autofill_committed_skip",
            ), r_loc
            assert r_loc.get("verified") is True
            assert r_loc.get("skipped_already_correct") is True

            async def _resolve_nxp(_page, _scope, _ps, _idx, aid):
                vals = {"company": "NXP Semiconductors"}
                return _Chain(_Loc(vals.get(aid, "")))

            resolve.side_effect = _resolve_nxp
            r3 = await wd._fill_experience_text_field(
                page,
                scope,
                page_scope=False,
                idx=1,
                aid="company",
                val="Example Corp",
                report=report,
            )
            assert r3.get("reason") == "autofill_mismatch_no_skip", r3
            assert r3.get("verified") is not True

    assert fill_calls == [], "second pass must not call fill() on prefilled rows"


def test_experience_text_matches_dummy():
    """0925Z: dummy readback is done even when parser val differs."""
    from exp_workday_selectors import (
        _experience_dummy_intents,
        _experience_text_matches_dummy,
    )

    intents = _experience_dummy_intents("jobTitle", "Senior ML Engineer")
    assert "Applied AI/ML Analyst" in intents
    assert "Senior ML Engineer" in intents

    ok, matched = _experience_text_matches_dummy(
        "Applied AI/ML Analyst",
        "jobTitle",
        "Senior ML Engineer",
        "EXPERIENCE_TITLE",
    )
    assert ok is True
    assert matched == "Applied AI/ML Analyst"

    ok_co, matched_co = _experience_text_matches_dummy(
        "Example Corp",
        "company",
        "Other Corp",
        "EXPERIENCE_COMPANY",
    )
    assert ok_co is True
    assert matched_co == "Example Corp"

    bad, _ = _experience_text_matches_dummy(
        "NXP Semiconductors",
        "company",
        "Example Corp",
        "EXPERIENCE_COMPANY",
    )
    assert bad is False

    loc_ok, loc_matched = _experience_text_matches_dummy(
        "Springfield, Illinois, United States",
        "location",
        "Austin, TX",
        "EXPERIENCE_LOCATION",
    )
    assert loc_ok is True, "1045Z: dummy Places location is skip-if-done"
    assert "springfield" in loc_matched.lower()

    loc_il, _ = _experience_text_matches_dummy(
        "Springfield, IL",
        "location",
        "Somewhere Else",
        "EXPERIENCE_LOCATION",
    )
    assert loc_il is True

    loc_bad, _ = _experience_text_matches_dummy(
        "Austin, TX",
        "location",
        "Springfield, IL",
        "EXPERIENCE_LOCATION",
    )
    assert loc_bad is False

    loc_no_comma, loc_nc = _experience_text_matches_dummy(
        "Springfield IL",
        "location",
        "Austin, TX",
        "EXPERIENCE_LOCATION",
    )
    assert loc_no_comma is True, "1116Z: Springfield IL (no comma) is dummy skip-if-done"
    assert "springfield" in loc_nc.lower()

    co_remote, _ = _experience_text_matches_dummy(
        "Example Corp | Remote",
        "company",
        "Other Corp",
        "EXPERIENCE_COMPANY",
    )
    assert co_remote is True


def test_dummy_on_screen_mismatch_is_done_not_leftover():
    """1138Z: dummy title/company on screen must not leftover as autofill_mismatch."""
    from leftover_miss_scan import is_invented_leftover

    report = {
        "filled": [
            {
                "automation_id": "workExperience-1/jobTitle",
                "type": "EXPERIENCE_TITLE",
                "value": "Applied AI/ML Analyst",
                "readback": "Applied AI/ML Analyst",
                "verified": True,
                "ok": True,
                "reason": "already_correct_skip",
                "skipped_already_correct": True,
                "mode": "skip",
            },
            {
                "automation_id": "workExperience-1/company",
                "type": "EXPERIENCE_COMPANY",
                "value": "Example Corp",
                "readback": "Example Corp",
                "verified": True,
                "ok": True,
                "reason": "already_correct_skip",
                "skipped_already_correct": True,
                "mode": "skip",
            },
        ],
        "leftovers": [
            {
                "label": "workExperience-1/jobTitle",
                "reason": "autofill_mismatch_no_skip",
                "automation_id": "workExperience-1/jobTitle",
            },
            {
                "label": "workExperience-1/company",
                "reason": "autofill_mismatch_no_skip",
                "automation_id": "workExperience-1/company",
            },
            {
                "label": "Role Description",
                "reason": "unclassified",
            },
        ],
    }
    assert is_invented_leftover(report["leftovers"][0], report) is True
    assert is_invented_leftover(report["leftovers"][1], report) is True
    assert is_invented_leftover(report["leftovers"][2], report) is False

    nxp = {
        "filled": [],
        "leftovers": [
            {
                "label": "workExperience-1/company",
                "reason": "autofill_mismatch_no_skip",
                "automation_id": "workExperience-1/company",
                "readback": "NXP Semiconductors",
            }
        ],
    }
    assert is_invented_leftover(nxp["leftovers"][0], nxp) is False


def test_gpa_optional_not_leftover_or_block():
    """Optional GPA must not leftover or block ADVANCE."""
    from exp_workday_selectors import _required_empties_as_leftovers
    from form_gaps import gaps_block_ready, normalize_gaps
    from leftover_miss_scan import is_invented_leftover

    empties = [
        {"id": "gpa", "label": "Overall Result (GPA)", "reason": "empty_required_input"},
        {"id": "jobTitle", "label": "Job Title*", "reason": "empty_required_input"},
    ]
    promo = _required_empties_as_leftovers(empties)
    assert all("gpa" not in str(r.get("label") or "").lower() for r in promo)

    gpa_row = {
        "label": "Overall Result (GPA)",
        "reason": "live_required_empty:empty_required_input",
        "automation_id": "formField-gpa",
    }
    assert is_invented_leftover(gpa_row, {"leftovers": [gpa_row]}) is True

    norm = normalize_gaps(
        [
            {
                "label": "Overall Result (GPA)",
                "reason": "required_empty",
                "automation_id": "formField-gpa",
            },
            {
                "label": "Job Title*",
                "reason": "required_empty",
                "automation_id": "jobTitle",
            },
        ]
    )
    labels = [g["label"] for g in norm]
    assert "Overall Result (GPA)" not in labels
    assert labels == ["Job Title*"]
    assert gaps_block_ready(
        [{"label": "Overall Result (GPA)", "reason": "required_empty"}]
    ) is False


def test_vision_job_title_ignored_when_experience_done():
    """0925Z: vision FAIL_BLANK on Job Title* must not override supervisor/field_is_done."""
    from page_progress import reconcile_vision_with_done, vision_blocks_ready

    report = {
        "filled": [
            {
                "automation_id": "workExperience-1/jobTitle",
                "type": "EXPERIENCE_TITLE",
                "value": "Applied AI/ML Analyst",
                "readback": "Applied AI/ML Analyst",
                "verified": True,
                "ok": True,
            }
        ],
        "vision_judge_live": {
            "complete": False,
            "verdict": "FAIL_BLANK",
            "empty_fields": [{"label": "Job Title*", "kind": "blank", "required": True}],
        },
        "vision_incomplete": True,
        "blocker": "vision_incomplete",
    }
    reconcile_vision_with_done(report)
    assert vision_blocks_ready(report) is False
    vj = report["vision_judge_live"]
    assert vj.get("verdict") == "COMPLETE"
    assert vj.get("empty_fields") == []


def test_experience_helpers_present():
    import inspect

    import exp_workday_selectors as wd

    assert inspect.isfunction(wd._fill_experience_text_field)
    assert inspect.isfunction(wd._append_experience_date_skip)
    src = inspect.getsource(wd._phase_c_experience)
    assert "_fill_experience_text_field" in src
    assert "date_autofill_accepted" in src
    assert "skip_end" in src
    assert "_should_skip_end_date" in src
    assert "present_disabled_end_skip" in inspect.getsource(wd._append_experience_date_skip)
    assert "autofill_committed_skip" in inspect.getsource(wd._append_experience_date_skip)
    # Must not uncheck Present when To is disabled (NXP)
    assert "to_enabled and not skip_end" in src
    gate_src = inspect.getsource(wd._phase_c_experience)
    assert "dates_done" in gate_src
    assert "skip_end" in gate_src
    fill_src = inspect.getsource(wd._fill_date_spin)
    assert "_type_month_year_via_tab" in fill_src
    assert "offscreen_skip" in fill_src
    assert "autofill_committed_skip" in fill_src
    off_block = fill_src.split('if tech == "offscreen_skip"')[1].split("all_skipped")[0]
    assert "optional_miss" not in off_block
    tab_src = inspect.getsource(wd._type_month_year_via_tab)
    assert "scrollIntoView({block:'center'" in tab_src
    assert ".fill(" not in tab_src
    hh_src = inspect.getsource(wd._fill_how_heard)
    assert "force_close_how_heard_widget" in hh_src
    ready_src = inspect.getsource(wd._wait_for_autofill_resume_ready)
    assert "wait_while_paused" not in ready_src


def test_accept_committed_present_disabled_skips_to():
    """Present + disabled To → accept From digits, empty To, no rewrite."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    import exp_workday_selectors as wd

    async def _run():
        rb_from = {
            "month_input": "08",
            "year_input": "2017",
            "month_display": "MM",
            "year_display": "YYYY",
        }
        rb_to = {
            "month_input": "",
            "year_input": "",
            "month_display": "MM",
            "year_display": "YYYY",
        }

        async def fake_read(page, nth, from_only=False, to_only=False, root=None, allow_page_fallback=True):
            return rb_from if from_only else rb_to

        async def fake_verify(page, month, year, *, nth, from_only=False, to_only=False, root=None, allow_page_fallback=True):
            if from_only and month == "08" and year == "2017":
                return True, rb_from
            return False, rb_to

        with patch.object(wd, "_read_date_spin_pair", fake_read), patch.object(
            wd, "_date_spin_verify", fake_verify
        ), patch.object(
            wd, "_currently_work_here_checked", AsyncMock(return_value=True)
        ), patch.object(
            wd, "_end_date_inputs_enabled", AsyncMock(return_value=False)
        ):
            fm, fy, tm, ty, ok = await wd._accept_committed_experience_dates(
                object(),
                start_nth=0,
                to_nth=0,
                start_m="01",
                start_y="2022",
                end_m="06",
                end_y="2023",
            )
        assert ok is True
        assert (fm, fy) == ("08", "2017")
        assert (tm, ty) == ("", "")

    asyncio.run(_run())


def test_action_judge_springfield_il_not_wrong():
    """1116Z: dummy Springfield IL vs parser Remote must not WRONG location."""
    from action_judge import judge_field_action

    row = judge_field_action(
        field="EXPERIENCE_LOCATION",
        before="Springfield IL",
        after="Springfield IL",
        intent="Remote",
        action="fill",
    )
    assert row["verdict"] == "correct_skip", row
    assert row["reason"] == "dummy_location_shown"


def test_experience_dates_done_when_from_ok_and_present_disabled():
    """0925Z gate: From committed + Present/disabled To must not block ADVANCE."""
    import inspect

    import exp_workday_selectors as wd

    src = inspect.getsource(wd._phase_c_experience)
    assert "dates_done = bool(from_ok) and (bool(to_ok) or skip_end)" in src
    assert "if dates_done:" in src
    assert "date_misses = []" in src


def main() -> int:
    test_action_judge_thrash_rewrite()
    test_action_judge_autofill_committed()
    asyncio.run(_run_experience_text_skip())
    test_experience_text_matches_dummy()
    test_dummy_on_screen_mismatch_is_done_not_leftover()
    test_experience_helpers_present()
    test_accept_committed_present_disabled_skips_to()
    test_action_judge_springfield_il_not_wrong()
    test_experience_dates_done_when_from_ok_and_present_disabled()
    print("test_workday_experience_skip: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
