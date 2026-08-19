#!/usr/bin/env python3
"""Focused FILL3 Medium plague fixes (006/009/011/012/018/020). No browser."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))


def test_fill3_018_test_mode_already_explicit():
    """FILL3-018 / DASH2-011: raw API requires explicit test_mode (already fixed)."""
    import server as srv

    try:
        srv._parse_test_mode({})
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "test_mode required" in str(e)
    assert srv._parse_test_mode({"test_mode": True}) is True
    assert srv._parse_test_mode({"test_mode": False}) is False


def test_fill3_012_use_my_last_sets_prefill_keep_policy():
    """FILL3-012: Use My Last documents soft-match keep policy (real mode only)."""
    import inspect

    import exp_workday_selectors as wd

    src = inspect.getsource(wd._click_workday_apply_path)
    assert "prefill_keep_policy" in src
    assert "use_my_last_soft_match_keep" in src
    assert "FILL3-012" in src


def main() -> int:
    test_fill3_018_test_mode_already_explicit()
    test_fill3_012_use_my_last_sets_prefill_keep_policy()
    # Re-export coverage from sibling modules' helpers
    from resume_upload import autofill_filename_verify_ok

    assert autofill_filename_verify_ok(
        filename="x.pdf", input_present=True, files_on_input=False
    ) is False
    from fast_fill import _leftover_set_fingerprint, _promote_demoted_flash_leftovers
    from fill_pause import _INSTALL_OVERLAY_JS

    assert "jh-log" in _INSTALL_OVERLAY_JS
    assert "Pause fill" in _INSTALL_OVERLAY_JS
    fp = _leftover_set_fingerprint(
        {"leftovers": [{"type": "A", "label": "a", "reason": "r", "flash_candidate": True}]}
    )
    assert len(fp) == 20
    n = _promote_demoted_flash_leftovers(
        {
            "filled": [],
            "flash": {},
            "leftovers": [
                {
                    "type": "SCHOOL",
                    "label": "School",
                    "reason": "live_empty_after_claimed_verified",
                }
            ],
        }
    )
    assert n == 1
    print("test_fill3_medium_plague: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
