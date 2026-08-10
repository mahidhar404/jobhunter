#!/usr/bin/env python3
"""Unit tests: set_input_files-first upload + captcha→manual fallback (no browser)."""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def test_upload_prefers_set_input_files_first():
    from resume_upload import (
        UPLOAD_ATTACH_PREFERENCE,
        file_chooser_fallback_timeout_ms,
        should_use_file_chooser_fallback,
        upload_resume_to_page,
    )

    assert UPLOAD_ATTACH_PREFERENCE == "set_input_files_first"
    assert should_use_file_chooser_fallback(file_input_reachable=True) is False
    assert should_use_file_chooser_fallback(file_input_reachable=False) is True
    # No Workday 8s stall on chooser fallback
    assert file_chooser_fallback_timeout_ms(workday=True) <= 4500
    assert file_chooser_fallback_timeout_ms(workday=False) <= 3500

    src = inspect.getsource(upload_resume_to_page)
    assert "set_input_files" in src
    # Primary path must not open with 8000ms filechooser
    assert "timeout=8000" not in src
    assert "expect_file_chooser" in src  # fallback still exists
    # Preference marker present in function body
    assert "UPLOAD_ATTACH_PREFERENCE" in src or "set_input_files_first" in src


def test_micro_jitter_bounded_and_on_by_default():
    from resume_upload import micro_jitter_ms

    os.environ.pop("FASTFILL_MICRO_JITTER", None)
    os.environ.pop("FASTFILL_MICRO_JITTER_MS", None)
    # On by default: ~150–250ms around 200ms center
    for _ in range(20):
        n = micro_jitter_ms()
        assert 150 <= n <= 250, n

    # Explicit disable
    for off in ("0", "false", "off"):
        os.environ["FASTFILL_MICRO_JITTER"] = off
        assert micro_jitter_ms() == 0
    os.environ.pop("FASTFILL_MICRO_JITTER", None)

    # FASTFILL_MICRO_JITTER_MS overrides base; hard-capped at 300
    os.environ["FASTFILL_MICRO_JITTER_MS"] = "999"
    for _ in range(10):
        n = micro_jitter_ms()
        assert 250 <= n <= 300, n  # center clamped to 300 → 250–300
    os.environ["FASTFILL_MICRO_JITTER_MS"] = "50"
    for _ in range(10):
        n = micro_jitter_ms()
        assert 0 <= n <= 100, n  # base 50 ± 50
    os.environ.pop("FASTFILL_MICRO_JITTER", None)
    os.environ.pop("FASTFILL_MICRO_JITTER_MS", None)


def test_prefer_manual_after_autofill_risk():
    from exp_workday_selectors import (
        mark_autofill_risk,
        prefer_manual_after_autofill_risk,
        upload_stuck_reason,
    )

    assert prefer_manual_after_autofill_risk(None) is False
    assert prefer_manual_after_autofill_risk({}) is False

    assert prefer_manual_after_autofill_risk({"captcha_human_solved": True}) is True
    assert prefer_manual_after_autofill_risk({"blocker": "captcha"}) is True
    assert prefer_manual_after_autofill_risk({"blocker": "cloudflare"}) is True
    assert prefer_manual_after_autofill_risk(
        {"captcha_wait": {"solved_gone": True}}
    ) is True

    r: dict = {}
    mark_autofill_risk(r, reason="captcha_reappeared")
    assert r["prefer_manual_entry"] is True
    assert r["autofill_captcha_seen"] is True
    assert "captcha_reappeared" in r["autofill_risk_reasons"]
    assert prefer_manual_after_autofill_risk(r) is True

    assert upload_stuck_reason({"reason": "no_file_input"}) == "no_file_input"
    assert upload_stuck_reason({"reason": "chooser_unverified"}) == "chooser_unverified"
    assert upload_stuck_reason({"attempted": True, "verified": False}) == "upload_unverified"
    assert upload_stuck_reason({"verified": True}) is None


def test_click_apply_path_skips_autofill_when_prefer_manual():
    from exp_workday_selectors import _click_workday_apply_path, _handle_autofill_resume_after_auth

    src = inspect.getsource(_click_workday_apply_path)
    assert "prefer_manual_after_autofill_risk" in src
    assert "apply_manually_prefer_after_captcha" in src
    # No fixed 1.5s / 6s resume settle loops
    assert "wait_for_timeout(1500)" not in src
    assert "for _ in range(12)" not in src

    src_h = inspect.getsource(_handle_autofill_resume_after_auth)
    assert "prefer_manual_after_autofill_risk" in src_h
    assert "mark_autofill_risk" in src_h
    assert "resume_ui_never_mounted" in src_h
    assert "captcha_reappeared" in src_h
    # Must not re-click Autofill when UI wait fails
    assert "resume_ui_wait_retry" not in src_h
    # Shorter ready wait than the old 28s
    assert "timeout_ms=28000" not in src_h
    assert "timeout_ms=16000" in src_h


def test_workday_upload_set_input_files_first():
    from exp_workday_selectors import _upload_workday_resume_page

    src = inspect.getsource(_upload_workday_resume_page)
    assert "set_input_files_first" in src
    assert "set_input_files" in src
    # Must not lead with 8000ms filechooser
    assert "timeout=8000" not in src
    # Chooser is fallback only
    assert "file_chooser_fallback" in src or "expect_file_chooser" in src
    # set_input_files appears before expect_file_chooser in source order
    i_set = src.find("set_input_files")
    i_fc = src.find("expect_file_chooser")
    assert i_set >= 0 and i_fc >= 0
    assert i_set < i_fc


if __name__ == "__main__":
    test_upload_prefers_set_input_files_first()
    test_micro_jitter_bounded_and_on_by_default()
    test_prefer_manual_after_autofill_risk()
    test_click_apply_path_skips_autofill_when_prefer_manual()
    test_workday_upload_set_input_files_first()
    print("test_captcha_path_hardening: OK")
