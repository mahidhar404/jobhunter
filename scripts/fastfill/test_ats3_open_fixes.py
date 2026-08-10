"""Focused tests for ATS3-011/013/014 + ATS2-015/016 open-item fixes (no browser)."""

from __future__ import annotations

import asyncio
import inspect


def test_ats3_011_spa_moved_helpers():
    from exp_workday_selectors import (
        _clear_false_stuck_after_spa_move,
        _gate_then_advance,
        _phase_b_contact,
        _poll_wd_spa_after_advance,
        _wd_spa_moved,
        _wd_spa_step_hint_from_probe,
        _wd_spa_step_probe,
    )

    assert _wd_spa_moved(
        {"contact": True, "progress": "My Info"},
        {"contact": False, "experience": True, "progress": "My Experience"},
    )
    assert _wd_spa_moved(
        {"contact": True},
        {"contact": False, "experience": False, "appQ": False},
    )
    assert not _wd_spa_moved(
        {"contact": True, "progress": "My Info"},
        {"contact": True, "progress": "My Info"},
    )
    assert _wd_spa_moved(
        {"contact": True, "progress": "Step 1 of 5"},
        {"contact": True, "progress": "Step 2 of 5"},
    )
    assert _wd_spa_step_hint_from_probe(
        {"contact": True}, {"experience": True}
    ) == "myExperiencePage"
    assert _wd_spa_step_hint_from_probe(
        {"contact": True}, {"contact": False}
    ) == "left_contact"

    # ATS2-011: contact phase_b + gate both use shared SPA settle/clear
    for fn in (_gate_then_advance, _phase_b_contact):
        src = inspect.getsource(fn)
        assert "_poll_wd_spa_after_advance" in src
        assert "_clear_false_stuck_after_spa_move" in src
    assert callable(_wd_spa_step_probe)
    assert callable(_poll_wd_spa_after_advance)
    assert callable(_clear_false_stuck_after_spa_move)


def test_ats2_011_clear_false_stuck_helper():
    """SPA DOM moved after contact Next clears sticky stuck (ATS2-011)."""
    from exp_workday_selectors import _clear_false_stuck_after_spa_move

    report = {"stuck_on_same_page": True, "advanced_count": 0}
    phase: dict = {}
    progress = {"stuck_on_same_page": True}
    before = {"fingerprint": "fpA", "url": "https://wd.example/apply", "title": "App"}
    after = {
        "fingerprint": "fpA",
        "url": "https://wd.example/apply",
        "title": "App",
        "step_hint": "",
    }
    out = _clear_false_stuck_after_spa_move(
        report,
        phase,
        progress,
        before,
        after,
        {"contact": True},
        {"contact": False, "experience": True},
        advanced=True,
    )
    assert report["stuck_on_same_page"] is False
    assert phase.get("spa_stuck_cleared") is True
    assert progress["stuck_on_same_page"] is False
    assert out["fingerprint"] != "fpA"


def test_ats2_016_poll_helper_replaces_long_sleeps():
    from exp_workday_selectors import (
        _click_workday_apply_path,
        _fallback_apply_manually_from_autofill,
        _poll_spa_settle,
        _poll_wd_spa_after_advance,
        _wait_contact_phase,
        _wait_step,
    )

    assert callable(_poll_spa_settle)
    src_apply = inspect.getsource(_click_workday_apply_path)
    assert "_poll_spa_settle" in src_apply
    assert "wait_for_timeout(4000)" not in src_apply
    assert "wait_for_timeout(4500)" not in src_apply
    assert "wait_for_timeout(1500)" not in src_apply
    src_fb = inspect.getsource(_fallback_apply_manually_from_autofill)
    assert "_poll_spa_settle" in src_fb
    assert "wait_for_timeout(3500)" not in src_fb

    # Thales stall: post-advance / contact waits must poll fast and early-exit
    src_spa = inspect.getsource(_poll_wd_spa_after_advance)
    assert "poll_ms" in src_spa
    assert "wait_for_timeout(350)" not in src_spa
    src_contact = inspect.getsource(_wait_contact_phase)
    assert "poll_ms" in src_contact
    assert "wait_for_timeout(1000)" not in src_contact
    src_step = inspect.getsource(_wait_step)
    assert "wait_for_timeout(800)" not in src_step
    assert "wait_for_timeout(150)" in src_step


def test_thales_contact_probes_include_source():
    """Thales wd3 mounts source--source before classic contactInformationPage."""
    from exp_workday_selectors import (
        WD_CONTACT_SELECTORS,
        _contact_phase_present,
        _dummy_answer_for_wd_label,
        _fill_how_heard,
    )
    from field_map import HOW_HEARD
    from fill_verify import how_heard_candidates

    src = inspect.getsource(_contact_phase_present)
    assert "source--source" in src
    assert "formField-source" in src
    hh = WD_CONTACT_SELECTORS.get("how_heard") or []
    assert any("source--source" in s for s in hh)
    assert any("Where Did You Hear" in s or "Where did you hear" in s for s in hh)

    vals = {HOW_HEARD: "Internet job board"}
    assert how_heard_candidates(vals)[0]
    for lab in (
        "Where did you hear about us?",
        "source--source",
        "How Did You Hear About Us",
        "Referral source",
    ):
        cands = _dummy_answer_for_wd_label(lab, vals)
        assert cands, lab
        assert any("internet" in c.lower() or "job board" in c.lower() for c in cands)

    hh_src = inspect.getsource(_fill_how_heard)
    assert "Where Did You Hear" in hh_src
    assert "source--source" in hh_src


def test_how_heard_classifies_workday_source_id():
    from field_map import HOW_HEARD, classify_field

    for field in (
        {"label": "source--source", "name": "source--source", "id": "source--source"},
        {"label": "", "name": "source--source", "id": ""},
        {"label": "Where did you hear about us?", "name": "", "id": ""},
        {"label": "Where Did You Hear About Us", "name": "source", "id": "source--source"},
        {"label": "", "name": "", "id": "formField-source"},
    ):
        ftype, layer = classify_field(field)
        assert ftype == HOW_HEARD, (field, ftype, layer)
        assert layer in ("layer1_regex", "semantic", "layer0_autocomplete")


def test_ats2_015_recursion_retry_once():
    from exp_workday_selectors import (
        _fill_country_phone_code,
        _fill_country_region_state,
        _fill_phone_device_type,
    )

    for fn in (_fill_phone_device_type, _fill_country_phone_code, _fill_country_region_state):
        src = inspect.getsource(fn)
        assert "_recursion_retry" in src
        assert "retried_after_recursion" in src
        assert "fill_error_after_recursion_retry" in src
        assert "degraded" in src


def test_ats3_014_gh_pack_has_geo():
    from field_map import ADDRESS_COUNTRY, ADDRESS_STATE, LOCATION
    from fast_fill import GH_SELECTOR_PACK

    types = {t for _, t, _ in GH_SELECTOR_PACK}
    assert ADDRESS_COUNTRY in types
    assert ADDRESS_STATE in types
    assert LOCATION in types
    geo = [(s, t, m) for s, t, m in GH_SELECTOR_PACK if t in (ADDRESS_COUNTRY, ADDRESS_STATE, LOCATION)]
    assert all(m == "combobox" for _, _, m in geo)
    assert any("Country" in s and "select__control" in s for s, _, _ in geo)
    assert any("Location" in s and "select__control" in s for s, _, _ in geo)
    assert any("State" in s and "select__control" in s for s, _, _ in geo)


def test_ats2_015_phone_device_retry_flag_roundtrip():
    """Simulate RecursionError path sets retry/degrade without silent miss."""

    async def _boom(*a, **k):
        raise RecursionError("maximum recursion depth exceeded")

    async def _run():
        import exp_workday_selectors as wd

        class FakeLoc:
            async def scroll_into_view_if_needed(self):
                return None

            async def click(self, timeout=0):
                return None

        page = type("P", (), {"wait_for_timeout": _async_noop, "keyboard": _KB()})()
        orig_click = wd._click_matching_option
        wd._click_matching_option = _boom
        try:
            out = await wd._fill_phone_device_type(page, FakeLoc(), "sel", "Mobile")
        finally:
            wd._click_matching_option = orig_click
        assert out.get("retried_after_recursion") is True
        assert out.get("verified") is not True
        assert out.get("degraded") is True or out.get("reason") in (
            "fill_error",
            "fill_error_after_recursion_retry",
            "no_matching_option",
            "open_failed",
        )
        assert out.get("reason") != "missed" or out.get("error")
        return out

    class _KB:
        async def press(self, *_a, **_k):
            return None

    async def _async_noop(*_a, **_k):
        return None

    asyncio.run(_run())


if __name__ == "__main__":
    test_ats3_011_spa_moved_helpers()
    test_ats2_011_clear_false_stuck_helper()
    test_ats2_016_poll_helper_replaces_long_sleeps()
    test_thales_contact_probes_include_source()
    test_how_heard_classifies_workday_source_id()
    test_ats2_015_recursion_retry_once()
    test_ats3_014_gh_pack_has_geo()
    test_ats2_015_phone_device_retry_flag_roundtrip()
    print("test_ats3_open_fixes: OK")
