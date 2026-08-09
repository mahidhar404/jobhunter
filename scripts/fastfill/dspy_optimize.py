#!/usr/bin/env python3
"""Phase 7 (optional/later) — DSPy prompt-optimization hook.

Scaffold for optimizing the leftover-answer prompt against the eval metrics
(``metrics_timeline`` pass rate) using DSPy. It is intentionally inert until
``dspy`` is installed AND ``FASTFILL_DSPY=1`` — a missing dep or unset flag makes
this a no-op that reports "not configured", so nothing here can affect a fill.

Why a hook and not a full optimizer: DSPy optimization needs a labelled dev set
and a running LLM; that is a live, opt-in workflow (skyvern_runtime/venv), not a
default. This module gives the seam (metric + entry point) so that work slots in
without touching the fill path.

CLI::

    python dspy_optimize.py --status
    FASTFILL_DSPY=1 python dspy_optimize.py --optimize   # requires dspy + data
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def enabled() -> bool:
    return os.environ.get("FASTFILL_DSPY", "0") == "1"


def _dspy_available() -> bool:
    try:
        import dspy  # noqa: F401

        return True
    except Exception:
        return False


def coverage_metric(example, prediction, *_a, **_k) -> float:
    """DSPy-shaped metric: 1.0 when the predicted answer is non-empty + matches
    the expected shape, else 0.0. (Coverage-first: never leave a field empty.)"""
    got = getattr(prediction, "answer", None) or getattr(prediction, "value", None) or ""
    want_nonempty = bool(str(got).strip())
    expected = getattr(example, "answer", None)
    if expected is None:
        return 1.0 if want_nonempty else 0.0
    return 1.0 if want_nonempty and str(got).strip() == str(expected).strip() else 0.0


def status() -> dict:
    return {
        "flag_enabled": enabled(),
        "dspy_installed": _dspy_available(),
        "ready": enabled() and _dspy_available(),
    }


def optimize(_trainset=None) -> dict:
    """Run DSPy optimization if fully configured; otherwise a documented no-op."""
    st = status()
    if not st["ready"]:
        return {
            "ran": False,
            "reason": "dspy not installed" if not st["dspy_installed"] else "FASTFILL_DSPY!=1",
            **st,
        }
    # Live optimization is intentionally left to the opt-in workflow; this hook
    # only proves the seam exists. Wiring a real BootstrapFewShot/MIPRO run here
    # requires a labelled leftover dev set + a live LLM (skyvern_runtime/venv).
    return {"ran": False, "reason": "optimizer wiring is an opt-in follow-up", **st}


def main() -> int:
    ap = argparse.ArgumentParser(description="DSPy optimization hook (optional)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--optimize", action="store_true")
    args = ap.parse_args()
    if args.optimize:
        print(optimize())
    else:
        print(status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
