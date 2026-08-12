#!/usr/bin/env python3
"""One-command headed improvement cycle + live monitor + stale-skip policy.

Starts attended train with:
  - FASTFILL_FILL_PAUSE=0
  - short CAPTCHA attended budget (default 120s via FASTFILL_CAPTCHA_TIMEOUT_S)
  - short Agent4 wait (default 45s; unfixable CAPTCHA/login_wall → skip wait)
  - live_fill_monitor --watch-latest --correct (corrects when it can; skips stale)

Dummy-only. Never submit. Never solve CAPTCHA.

Usage::

    skyvern_runtime/venv/bin/python scripts/fastfill/run_headed_cycle_with_monitor.py \\
      --limit 4 --working-streak 3 --min-platforms 3 --require-workday

Env overrides:
  FASTFILL_CAPTCHA_TIMEOUT_S=120     # CAPTCHA sit budget before skip
  FASTFILL_STALE_NO_PROGRESS_S=180   # mid-fill no fill_steps progress → skip
  FASTFILL_STALE_ZERO_ACTIVITY_S=120 # only run_start/navigate, never filled
  FASTFILL_LOGIN_WALL_SKIP_S=60      # login wall + force_create tried → skip
  FASTFILL_AGENT4_WAIT_S=45          # Fixer wait (0 = never wait)
  FASTFILL_HOLD_SUPPRESS_GRACE_S=120 # hold_snapshot mtime grace (no skip)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=4)
    ap.add_argument("--working-streak", type=int, default=3)
    ap.add_argument("--min-platforms", type=int, default=3)
    ap.add_argument("--require-workday", action="store_true", default=True)
    ap.add_argument("--no-require-workday", action="store_true")
    ap.add_argument("--captcha-burst", type=int, default=3)
    ap.add_argument("--captcha-cooldown-s", type=int, default=180)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument(
        "--force-live",
        action="store_true",
        help="Bypass live_gate arming (emergency only).",
    )
    ap.add_argument(
        "--urls-json",
        type=Path,
        default=None,
        help="Explicit never-seen / custom queue JSON",
    )
    args = ap.parse_args(argv)

    # Dummy-safe + attended skip budgets (prefer skip over 600s Stripe sit;
    # mid-fill budget is higher so slow Workday / holds / Agent4 waits are safe)
    os.environ.setdefault("TEST_MODE", "1")
    os.environ["FASTFILL_REAL_PROFILE"] = "0"
    os.environ.pop("FASTFILL_ALLOW_REAL", None)
    os.environ.setdefault("FASTFILL_FILL_PAUSE", "0")
    os.environ.setdefault("FASTFILL_CAPTCHA_TIMEOUT_S", "120")
    os.environ.setdefault("FASTFILL_AGENT4_WAIT_S", "45")
    os.environ.setdefault("FASTFILL_STALE_NO_PROGRESS_S", "180")
    os.environ.setdefault("FASTFILL_STALE_ZERO_ACTIVITY_S", "120")
    os.environ.setdefault("FASTFILL_LOGIN_WALL_SKIP_S", "60")
    os.environ.setdefault("FASTFILL_HOLD_SUPPRESS_GRACE_S", "120")
    if args.force_live:
        os.environ["FASTFILL_FORCE_LIVE"] = "1"

    from improvement_cycle import _self_test, phase_train
    from live_gate import live_fill_allowed
    from stale_skip import captcha_budget_s, stale_no_progress_s

    if args.self_test:
        return _self_test()

    if not args.dry_run:
        ok, reason = live_fill_allowed(force=bool(args.force_live))
        if not ok:
            print(
                f"[headed+monitor] REFUSING live: {reason}. "
                "Run improvement_cycle --phase train_offline && --phase gate_live "
                "or pass --force-live.",
                flush=True,
            )
            return 3

    require_wd = bool(args.require_workday) and not args.no_require_workday
    print(
        f"[headed+monitor] captcha_budget={captcha_budget_s():.0f}s "
        f"stale_no_progress={stale_no_progress_s():.0f}s "
        f"agent4_wait={os.environ.get('FASTFILL_AGENT4_WAIT_S')} "
        f"fill_pause=0 with_monitor=1 dummy=1",
        flush=True,
    )
    train = phase_train(
        limit=args.limit,
        headed=True,
        captcha_burst=args.captcha_burst,
        captcha_cooldown_s=args.captcha_cooldown_s,
        working_streak=args.working_streak,
        min_platforms=args.min_platforms,
        require_workday=require_wd,
        dry_run=bool(args.dry_run),
        with_monitor=True,
        urls_json=args.urls_json,
    )
    import json

    print(json.dumps({"train": train}, indent=2, default=str))
    if train.get("fix_class"):
        print(
            f"[headed+monitor] NEXT FIX CLASS: {train['fix_class']}",
            flush=True,
        )
    return int(train.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
