#!/usr/bin/env python3
"""Focused tests for stale/stuck skip timers (no browser)."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
import sys

sys.path.insert(0, str(HERE))

from stale_skip import (  # noqa: E402
    DEFAULT_CAPTCHA_BUDGET_S,
    DEFAULT_STALE_NO_PROGRESS_S,
    DEFAULT_STALE_ZERO_ACTIVITY_S,
    apply_stale_skip,
    captcha_budget_s,
    consume_job_skip_sentinel,
    detect_stale_skip,
    should_skip_agent4_wait,
    stale_budget_for_steps,
    stale_no_progress_s,
    suppress_no_progress_skip,
    write_fix_skipped,
)


def test_captcha_budget_env() -> None:
    os.environ["FASTFILL_CAPTCHA_TIMEOUT_S"] = "95"
    assert captcha_budget_s() == 95.0
    os.environ.pop("FASTFILL_CAPTCHA_TIMEOUT_S", None)
    assert captcha_budget_s() == DEFAULT_CAPTCHA_BUDGET_S


def test_stale_defaults_raised() -> None:
    os.environ.pop("FASTFILL_STALE_NO_PROGRESS_S", None)
    os.environ.pop("FASTFILL_STALE_ZERO_ACTIVITY_S", None)
    assert DEFAULT_STALE_NO_PROGRESS_S >= 180.0
    assert stale_no_progress_s() == DEFAULT_STALE_NO_PROGRESS_S
    assert DEFAULT_STALE_ZERO_ACTIVITY_S <= DEFAULT_STALE_NO_PROGRESS_S
    assert stale_budget_for_steps([{"action": "run_start"}]) == DEFAULT_STALE_ZERO_ACTIVITY_S
    assert (
        stale_budget_for_steps([{"action": "fill_text"}]) == DEFAULT_STALE_NO_PROGRESS_S
    )


def test_agent4_skip_unfixable() -> None:
    assert should_skip_agent4_wait({"blocker": "captcha"})
    assert should_skip_agent4_wait({}, fail_class="BLOCKED")
    assert should_skip_agent4_wait(
        {"captcha_wait": {"via": "job_skip", "timed_out": True}}
    )
    assert should_skip_agent4_wait({"decision": {"verdict": "FAIL_BLANK"}}) is False


def test_detect_captcha_budget_skip() -> None:
    with tempfile.TemporaryDirectory() as td:
        ad = Path(td)
        (ad / "fill_steps.jsonl").write_text(
            json.dumps({"step": 1, "action": "run_start"}) + "\n", encoding="utf-8"
        )
        (ad / ".captcha_waiting.json").write_text(
            json.dumps({"ts": time.time() - 150, "timeout_s": 600}),
            encoding="utf-8",
        )
        os.environ["FASTFILL_CAPTCHA_TIMEOUT_S"] = "100"
        skip = detect_stale_skip(ad, [{"step": 1}])
        assert skip is not None
        assert skip["fail_class"] == "BLOCKED"
        assert skip["reason"] == "captcha_attended_budget"
        out = apply_stale_skip(ad, skip, url="https://dashboard.stripe.com/login")
        assert out.get("skipped")
        assert (ad / "FIX_SKIPPED.md").is_file()
        assert (ad / "FIX_APPLIED.md").is_file()
        payload = consume_job_skip_sentinel(ad)
        assert payload is not None
        os.environ.pop("FASTFILL_CAPTCHA_TIMEOUT_S", None)


def test_detect_no_progress_skip() -> None:
    with tempfile.TemporaryDirectory() as td:
        ad = Path(td)
        steps_path = ad / "fill_steps.jsonl"
        steps_path.write_text(
            json.dumps({"step": 3, "action": "fill_text"}) + "\n", encoding="utf-8"
        )
        os.utime(steps_path, (time.time() - 200, time.time() - 200))
        os.environ["FASTFILL_STALE_NO_PROGRESS_S"] = "60"
        skip = detect_stale_skip(ad, [{"step": 3, "action": "fill_text"}])
        assert skip is not None
        assert skip["fail_class"] == "FAIL_STUCK"
        os.environ.pop("FASTFILL_STALE_NO_PROGRESS_S", None)


def test_hold_suppresses_no_progress_skip() -> None:
    """Headed hold / .fill_paused must not stale-skip mid-review."""
    with tempfile.TemporaryDirectory() as td:
        ad = Path(td)
        steps_path = ad / "fill_steps.jsonl"
        steps = [{"step": 5, "action": "fill_text"}]
        steps_path.write_text(json.dumps(steps[0]) + "\n", encoding="utf-8")
        os.utime(steps_path, (time.time() - 300, time.time() - 300))
        (ad / ".fill_paused").write_text("monitor pause\n", encoding="utf-8")
        os.environ["FASTFILL_STALE_NO_PROGRESS_S"] = "60"
        assert suppress_no_progress_skip(ad, steps) == "fill_paused"
        assert detect_stale_skip(ad, steps) is None
        os.environ.pop("FASTFILL_STALE_NO_PROGRESS_S", None)

        # hold_snapshot recent also suppresses (no .fill_paused)
        ad2 = Path(td) / "hold"
        ad2.mkdir()
        sp2 = ad2 / "fill_steps.jsonl"
        sp2.write_text(json.dumps(steps[0]) + "\n", encoding="utf-8")
        os.utime(sp2, (time.time() - 300, time.time() - 300))
        (ad2 / "hold_snapshot.json").write_text("{}\n", encoding="utf-8")
        os.environ["FASTFILL_STALE_NO_PROGRESS_S"] = "60"
        assert suppress_no_progress_skip(ad2, steps) == "hold_review_active"
        assert detect_stale_skip(ad2, steps) is None
        os.environ.pop("FASTFILL_STALE_NO_PROGRESS_S", None)


def test_recent_fill_step_suppresses_skip() -> None:
    """fill_steps still advancing (fresh mtime) → no FAIL_STUCK."""
    with tempfile.TemporaryDirectory() as td:
        ad = Path(td)
        steps_path = ad / "fill_steps.jsonl"
        steps = [{"step": 8, "action": "fill_text", "ts": time.time()}]
        steps_path.write_text(json.dumps(steps[0]) + "\n", encoding="utf-8")
        # Fresh mtime — well within mid-fill budget
        os.environ["FASTFILL_STALE_NO_PROGRESS_S"] = "180"
        assert detect_stale_skip(ad, steps) is None
        # Even with a short budget, age from step ts is ~0
        os.environ["FASTFILL_STALE_NO_PROGRESS_S"] = "30"
        assert detect_stale_skip(ad, steps) is None
        os.environ.pop("FASTFILL_STALE_NO_PROGRESS_S", None)


def test_captcha_wait_suppresses_stale_no_progress() -> None:
    """Under CAPTCHA budget: captcha rule owns the window; no FAIL_STUCK."""
    with tempfile.TemporaryDirectory() as td:
        ad = Path(td)
        steps_path = ad / "fill_steps.jsonl"
        steps = [{"step": 1, "action": "run_start"}]
        steps_path.write_text(json.dumps(steps[0]) + "\n", encoding="utf-8")
        os.utime(steps_path, (time.time() - 300, time.time() - 300))
        (ad / ".captcha_waiting.json").write_text(
            json.dumps({"ts": time.time() - 40, "timeout_s": 120}),
            encoding="utf-8",
        )
        os.environ["FASTFILL_CAPTCHA_TIMEOUT_S"] = "120"
        os.environ["FASTFILL_STALE_NO_PROGRESS_S"] = "60"
        os.environ["FASTFILL_STALE_ZERO_ACTIVITY_S"] = "60"
        assert suppress_no_progress_skip(ad, steps) == "captcha_wait_active"
        assert detect_stale_skip(ad, steps) is None
        # Past captcha budget → BLOCKED (captcha rule), not FAIL_STUCK
        (ad / ".captcha_waiting.json").write_text(
            json.dumps({"ts": time.time() - 200, "timeout_s": 120}),
            encoding="utf-8",
        )
        skip = detect_stale_skip(ad, steps)
        assert skip is not None
        assert skip["reason"] == "captcha_attended_budget"
        assert skip["fail_class"] == "BLOCKED"
        os.environ.pop("FASTFILL_CAPTCHA_TIMEOUT_S", None)
        os.environ.pop("FASTFILL_STALE_NO_PROGRESS_S", None)
        os.environ.pop("FASTFILL_STALE_ZERO_ACTIVITY_S", None)


def test_agent4_wait_suppresses_no_progress() -> None:
    with tempfile.TemporaryDirectory() as td:
        ad = Path(td)
        steps_path = ad / "fill_steps.jsonl"
        steps = [
            {"step": 1, "action": "fill_text"},
            {"step": 2, "action": "run_end"},
        ]
        steps_path.write_text(
            "\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8"
        )
        os.utime(steps_path, (time.time() - 300, time.time() - 300))
        (ad / "RETRY_AFTER_FIX.txt").write_text("waiting\n", encoding="utf-8")
        os.environ["FASTFILL_STALE_NO_PROGRESS_S"] = "60"
        # run_end alone suppresses; also agent4 marker
        why = suppress_no_progress_skip(ad, steps)
        assert why in ("run_end_complete", "agent4_wait_active")
        assert detect_stale_skip(ad, steps) is None
        # Without run_end, RETRY_AFTER_FIX still suppresses
        steps2 = [{"step": 1, "action": "fill_text"}]
        ad2 = Path(td) / "a4"
        ad2.mkdir()
        sp2 = ad2 / "fill_steps.jsonl"
        sp2.write_text(json.dumps(steps2[0]) + "\n", encoding="utf-8")
        os.utime(sp2, (time.time() - 300, time.time() - 300))
        (ad2 / "RETRY_AFTER_FIX.txt").write_text("waiting\n", encoding="utf-8")
        assert suppress_no_progress_skip(ad2, steps2) == "agent4_wait_active"
        assert detect_stale_skip(ad2, steps2) is None
        os.environ.pop("FASTFILL_STALE_NO_PROGRESS_S", None)


def test_login_wall_after_force_create() -> None:
    with tempfile.TemporaryDirectory() as td:
        ad = Path(td)
        (ad / ".force_create_account").write_text("x\n", encoding="utf-8")
        (ad / ".captcha_waiting.json").write_text(
            json.dumps(
                {
                    "ts": time.time() - 90,
                    "page_url": "https://dashboard.stripe.com/login",
                }
            ),
            encoding="utf-8",
        )
        os.environ["FASTFILL_CAPTCHA_TIMEOUT_S"] = "300"  # captcha budget not hit
        os.environ["FASTFILL_LOGIN_WALL_SKIP_S"] = "60"
        issues = [{"kind": "product_login_wall", "corrective": "force_create_account"}]
        skip = detect_stale_skip(ad, [{"step": 1}], issues=issues)
        assert skip is not None
        assert skip["fail_class"] == "login_wall"
        os.environ.pop("FASTFILL_CAPTCHA_TIMEOUT_S", None)
        os.environ.pop("FASTFILL_LOGIN_WALL_SKIP_S", None)


def test_write_fix_skipped() -> None:
    with tempfile.TemporaryDirectory() as td:
        ad = Path(td)
        p = write_fix_skipped(
            ad, reason="unit", fail_class="BLOCKED", also_fix_applied_note=True
        )
        assert p.is_file()
        assert "BLOCKED" in p.read_text(encoding="utf-8")


def test_resolve_captcha_timeout() -> None:
    from captcha_pause import DEFAULT_CAPTCHA_TIMEOUT_S, resolve_captcha_timeout_s

    os.environ.pop("FASTFILL_CAPTCHA_TIMEOUT_S", None)
    assert resolve_captcha_timeout_s(None) == float(DEFAULT_CAPTCHA_TIMEOUT_S)
    os.environ["FASTFILL_CAPTCHA_TIMEOUT_S"] = "120"
    assert resolve_captcha_timeout_s(None) == 120.0
    assert resolve_captcha_timeout_s(90) == 90.0
    os.environ.pop("FASTFILL_CAPTCHA_TIMEOUT_S", None)


if __name__ == "__main__":
    test_captcha_budget_env()
    test_stale_defaults_raised()
    test_agent4_skip_unfixable()
    test_detect_captcha_budget_skip()
    test_detect_no_progress_skip()
    test_hold_suppresses_no_progress_skip()
    test_recent_fill_step_suppresses_skip()
    test_captcha_wait_suppresses_stale_no_progress()
    test_agent4_wait_suppresses_no_progress()
    test_login_wall_after_force_create()
    test_write_fix_skipped()
    test_resolve_captcha_timeout()
    print("test_stale_skip: OK")
