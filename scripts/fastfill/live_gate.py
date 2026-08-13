#!/usr/bin/env python3
"""Offline-first live canary arming for fastfill.

Artifacts under ``skyvern_runtime/real_job_results/``:

- ``OFFLINE_GATE_PASS.json`` — gym + regression SLOs green
- ``LIVE_CANARY_ARMED`` — empty marker; written only by ``arm_canary``
- ``LIVE_CANARY_DONE.json`` — written after ``eval_suite --limit 7``; blocks further live

Live fills (eval_suite, headed cycle, phase_train) refuse unless armed and not done,
unless ``--force-live`` / ``FASTFILL_FORCE_LIVE=1``.

Dummy-only; never-submit; never CAPTCHA.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ARTIFACT_DIR = ROOT / "skyvern_runtime" / "real_job_results"

OFFLINE_GATE_PASS = ARTIFACT_DIR / "OFFLINE_GATE_PASS.json"
LIVE_CANARY_ARMED = ARTIFACT_DIR / "LIVE_CANARY_ARMED"
LIVE_CANARY_DONE = ARTIFACT_DIR / "LIVE_CANARY_DONE.json"


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def force_live_enabled() -> bool:
    return os.environ.get("FASTFILL_FORCE_LIVE", "").strip() in ("1", "true", "yes")


def ensure_artifact_dir() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def write_offline_gate_pass(payload: dict[str, Any]) -> Path:
    ensure_artifact_dir()
    body = {"ts": _utc(), **payload}
    OFFLINE_GATE_PASS.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    return OFFLINE_GATE_PASS


def read_offline_gate_pass() -> dict[str, Any] | None:
    if not OFFLINE_GATE_PASS.is_file():
        return None
    try:
        return json.loads(OFFLINE_GATE_PASS.read_text(encoding="utf-8"))
    except Exception:
        return None


def arm_canary(*, reason: str = "offline_gate_pass") -> Path:
    """Create LIVE_CANARY_ARMED; clears LIVE_CANARY_DONE so a new canary can run."""
    ensure_artifact_dir()
    if not OFFLINE_GATE_PASS.is_file():
        raise RuntimeError("cannot arm canary: OFFLINE_GATE_PASS.json missing")
    if LIVE_CANARY_DONE.is_file():
        LIVE_CANARY_DONE.unlink()
    LIVE_CANARY_ARMED.write_text(
        json.dumps({"ts": _utc(), "reason": reason}, indent=2) + "\n"
    )
    return LIVE_CANARY_ARMED


def disarm_canary() -> None:
    if LIVE_CANARY_ARMED.is_file():
        LIVE_CANARY_ARMED.unlink()


def write_canary_done(payload: dict[str, Any]) -> Path:
    ensure_artifact_dir()
    body = {"ts": _utc(), **payload}
    LIVE_CANARY_DONE.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    # Consumed: require re-arm for another live pass
    disarm_canary()
    return LIVE_CANARY_DONE


def gate_status() -> dict[str, Any]:
    offline = read_offline_gate_pass()
    return {
        "artifact_dir": str(ARTIFACT_DIR),
        "offline_gate_pass": bool(OFFLINE_GATE_PASS.is_file()),
        "offline_gate": offline,
        "live_canary_armed": LIVE_CANARY_ARMED.is_file(),
        "live_canary_done": LIVE_CANARY_DONE.is_file(),
        "force_live": force_live_enabled(),
        "live_allowed": live_fill_allowed(force=False)[0],
    }


def live_fill_allowed(*, force: bool = False) -> tuple[bool, str]:
    """Return (ok, reason). force=True or FASTFILL_FORCE_LIVE bypasses gates."""
    if force or force_live_enabled():
        return True, "force_live"
    if LIVE_CANARY_DONE.is_file():
        return False, "live_canary_done_present_rearm_required"
    if not LIVE_CANARY_ARMED.is_file():
        return False, "live_canary_not_armed"
    if not OFFLINE_GATE_PASS.is_file():
        return False, "offline_gate_pass_missing"
    return True, "armed"


def require_live_allowed(*, force: bool = False) -> None:
    ok, reason = live_fill_allowed(force=force)
    if not ok:
        raise SystemExit(
            f"[live_gate] REFUSING live fill: {reason}. "
            "Pass --force-live / FASTFILL_FORCE_LIVE=1, or write "
            "OFFLINE_GATE_PASS.json + LIVE_CANARY_ARMED under real_job_results."
        )


def _self_test() -> int:
    import tempfile

    global ARTIFACT_DIR, OFFLINE_GATE_PASS, LIVE_CANARY_ARMED, LIVE_CANARY_DONE
    old = (ARTIFACT_DIR, OFFLINE_GATE_PASS, LIVE_CANARY_ARMED, LIVE_CANARY_DONE)
    try:
        with tempfile.TemporaryDirectory() as td:
            ARTIFACT_DIR = Path(td)
            OFFLINE_GATE_PASS = ARTIFACT_DIR / "OFFLINE_GATE_PASS.json"
            LIVE_CANARY_ARMED = ARTIFACT_DIR / "LIVE_CANARY_ARMED"
            LIVE_CANARY_DONE = ARTIFACT_DIR / "LIVE_CANARY_DONE.json"

            ok, reason = live_fill_allowed()
            assert not ok and "not_armed" in reason

            try:
                arm_canary()
                assert False, "arm without offline should fail"
            except RuntimeError:
                pass

            write_offline_gate_pass({"ok": True, "ats_gym": {"ok": True}})
            arm_canary(reason="test")
            ok, reason = live_fill_allowed()
            assert ok and reason == "armed", (ok, reason)

            write_canary_done({"n": 7, "passed": 3})
            ok, reason = live_fill_allowed()
            assert not ok and "done" in reason
            assert not LIVE_CANARY_ARMED.is_file()

            # Re-arm after fresh offline
            write_offline_gate_pass({"ok": True, "round": 2})
            arm_canary()
            assert live_fill_allowed()[0]
    finally:
        ARTIFACT_DIR, OFFLINE_GATE_PASS, LIVE_CANARY_ARMED, LIVE_CANARY_DONE = old
    print("live_gate self-test OK")
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="live canary gate")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    if args.status:
        print(json.dumps(gate_status(), indent=2, default=str))
        raise SystemExit(0)
    ap.print_help()
    raise SystemExit(2)
