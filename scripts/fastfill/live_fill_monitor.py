#!/usr/bin/env python3
"""Live headed-fill monitor / corrector (DOM + step-log watchdog).

Watches an active cycle attempt (``fill_steps.jsonl``, CAPTCHA_WAITING,
screenshots metadata) and intervenes on known wrong-path patterns:

* Sign-in wall filled via Greenhouse pack (Stripe ``#email``) → request
  create-account corrective (``.force_create_account``) + optional pause
* Pure password / product login with no Create account → write alert +
  FIXER hint (never invent SUCCESS)
* Mid-wizard false-complete / salary blank leftovers → MONITOR_ALERT only
  (Agent4 / FIX_APPLIED path)

Never solves CAPTCHA. Never submits. Dummy-only observability.

Usage::

    # Alongside attended improvement cycle (auto-discovers newest cycle_*):
    skyvern_runtime/venv/bin/python scripts/fastfill/live_fill_monitor.py \\
      --watch-latest --correct

    # Pin a cycle / attempt:
    skyvern_runtime/venv/bin/python scripts/fastfill/live_fill_monitor.py \\
      --cycle-dir skyvern_runtime/real_job_results/cycle_YYYYMMDD… \\
      --correct --once

Corrective actions (safe):
  - touch ``.force_create_account`` (fast_fill consumes before pack / on gate)
  - touch ``.fill_paused`` (cooperative pause between actions)
  - write ``MONITOR_ALERT.md`` + ``CORRECTIVE_ACTION.json``
  - never touch CAPTCHA solve; never FINAL/submit
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = ROOT / "skyvern_runtime" / "real_job_results"

sys.path.insert(0, str(HERE))

from iframe_ctx import (  # noqa: E402
    create_account_sentinel_path,
    create_account_link_priority,
    normalize_auth_label,
    sign_in_wall_from_signals,
)


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def newest_cycle_dir(base: Path = RESULTS) -> Path | None:
    cycles = sorted(
        base.glob("cycle_*"),
        key=lambda p: p.stat().st_mtime if p.is_dir() else 0,
        reverse=True,
    )
    return cycles[0] if cycles else None


def list_attempt_dirs(cycle_dir: Path) -> list[Path]:
    if not cycle_dir.is_dir():
        return []
    out = [
        p
        for p in cycle_dir.iterdir()
        if p.is_dir() and (p / "fill_steps.jsonl").is_file()
    ]
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def read_jsonl_tail(path: Path, *, max_lines: int = 80) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _captcha_waiting_url(attempt_dir: Path) -> str:
    md = attempt_dir / "CAPTCHA_WAITING.md"
    if md.is_file():
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        for line in text.splitlines():
            if line.strip().startswith("url="):
                return line.strip()[4:].strip()
            if "dashboard.stripe.com" in line or "/login" in line.lower():
                # fall through — still scan
                pass
        # last non-empty line often has url=
        for line in reversed(text.splitlines()):
            if "url=" in line:
                return line.split("url=", 1)[-1].strip()
    js = attempt_dir / ".captcha_waiting.json"
    if js.is_file():
        try:
            data = json.loads(js.read_text(encoding="utf-8"))
            return str(data.get("url") or "")
        except Exception:
            pass
    return ""


def detect_wrong_paths(attempt_dir: Path, steps: list[dict]) -> list[dict]:
    """Return list of issue dicts for this attempt."""
    issues: list[dict] = []
    wait_url = _captcha_waiting_url(attempt_dir)
    pack_email_on_auth = False
    for s in steps:
        action = str(s.get("action") or "")
        via = str(s.get("via") or "")
        ftype = str(s.get("field_type") or s.get("label") or "")
        url = str(s.get("url") or "")
        reason = str(s.get("reason") or "")
        # Greenhouse / ATS pack filled EMAIL while on login-looking host or later
        # CAPTCHA waiting shows product login.
        if (
            action in ("fill_text", "fill")
            and ftype.upper() in ("EMAIL",)
            and "selector_pack" in via
        ):
            if sign_in_wall_from_signals(
                url=url or wait_url,
                body=reason,
                email_count=1,
                password_count=0,
                appish_count=0,
            ) or sign_in_wall_from_signals(
                url=wait_url,
                body="sign in to your account",
                email_count=1,
                password_count=1,
                appish_count=0,
            ):
                pack_email_on_auth = True
                issues.append(
                    {
                        "kind": "sign_in_pack_fill",
                        "severity": "high",
                        "step": s.get("step"),
                        "detail": f"EMAIL via {via} on auth wall url={wait_url or url}",
                        "corrective": "force_create_account",
                    }
                )
        if action == "selector_pack_skipped" and "sign_in" in reason:
            issues.append(
                {
                    "kind": "sign_in_wall_skipped_pack",
                    "severity": "info",
                    "step": s.get("step"),
                    "detail": reason,
                    "corrective": None,
                }
            )
        # Mid-wizard / incomplete advance signals in step reasons
        blob = f"{action} {reason} {via}".lower()
        if "advanced_incomplete" in blob or "validation_after_advance" in blob:
            issues.append(
                {
                    "kind": "midwizard_advance",
                    "severity": "high",
                    "step": s.get("step"),
                    "detail": reason or action,
                    "corrective": "pause_for_fix",
                }
            )
        if "salary" in blob and any(
            x in blob for x in ("leftover", "blank", "empty", "unfilled")
        ):
            issues.append(
                {
                    "kind": "salary_blank_leftover",
                    "severity": "medium",
                    "step": s.get("step"),
                    "detail": reason or action,
                    "corrective": "pause_for_fix",
                }
            )
        # Wrong Country Phone Code / Address Country (Australia +61 vs US dummy)
        after = str(s.get("after") or s.get("extra", {}).get("readback") or "")
        ftu = ftype.upper()
        lab = str(s.get("label") or "").lower()
        if (
            ftu in ("ADDRESS_COUNTRY", "PHONE_COUNTRY_CODE")
            or "countryphonecode" in lab
            or "country phone" in lab
            or lab == "addresssection_country"
        ):
            low_after = after.lower()
            if "australia" in low_after or "(+61)" in low_after:
                issues.append(
                    {
                        "kind": "wrong_phone_country_code",
                        "severity": "high",
                        "step": s.get("step"),
                        "detail": (
                            f"{ftype or lab} committed {after!r} — force United States (+1); "
                            "never job-board search tokens"
                        ),
                        "corrective": "force_us_phone_country",
                    }
                )
            if (
                ftu == "PHONE_COUNTRY_CODE"
                and reason == "not_in_dom"
            ):
                issues.append(
                    {
                        "kind": "phone_country_code_missed",
                        "severity": "high",
                        "step": s.get("step"),
                        "detail": "Country Phone Code not_in_dom — try phoneNumber--countryPhoneCode",
                        "corrective": "force_us_phone_country",
                    }
                )
            typed = str(
                s.get("typed")
                or s.get("value")
                or s.get("extra", {}).get("typed_frag")
                or s.get("extra", {}).get("search_query")
                or ""
            ).lower()
            if ftu == "PHONE_COUNTRY_CODE" and re.search(
                r"indeed|linkedin|glassdoor|job\s*board|greenhouse|lever",
                typed,
            ):
                issues.append(
                    {
                        "kind": "phone_country_job_board_token",
                        "severity": "high",
                        "step": s.get("step"),
                        "detail": (
                            f"Country Phone Code typed job-board token {typed!r} — "
                            "search United States / +1 only"
                        ),
                        "corrective": "force_us_phone_country",
                    }
                )

    if wait_url and "dashboard.stripe.com" in wait_url and not pack_email_on_auth:
        # CAPTCHA on Stripe product login even if pack step URL still greenhouse
        for s in steps:
            if (
                str(s.get("field_type") or "").upper() == "EMAIL"
                and "selector_pack" in str(s.get("via") or "")
            ):
                issues.append(
                    {
                        "kind": "sign_in_pack_fill",
                        "severity": "high",
                        "step": s.get("step"),
                        "detail": f"pack EMAIL then CAPTCHA on {wait_url}",
                        "corrective": "force_create_account",
                    }
                )
                break
        else:
            issues.append(
                {
                    "kind": "product_login_wall",
                    "severity": "high",
                    "detail": f"CAPTCHA wait on {wait_url}",
                    "corrective": "force_create_account",
                }
            )
    return issues


def _write_alert(attempt_dir: Path, issues: list[dict]) -> Path:
    path = attempt_dir / "MONITOR_ALERT.md"
    lines = [
        f"# Live fill monitor alert — {_utc()}",
        "",
        "Never submit. Never solve CAPTCHA. Dummy only.",
        "",
        "## Issues",
    ]
    for i in issues:
        lines.append(
            f"- **{i.get('kind')}** ({i.get('severity')}): {i.get('detail')} "
            f"→ corrective=`{i.get('corrective')}`"
        )
    lines.extend(
        [
            "",
            "## Safe interventions",
            f"- Create-account sentinel: `{create_account_sentinel_path()}`",
            "- Pause: touch `.fill_paused` (or set FASTFILL_FILL_PAUSE_FILE)",
            "- CAPTCHA: human solve only, then Continue / `.captcha_continue`",
            "",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (attempt_dir / "CORRECTIVE_ACTION.json").write_text(
        json.dumps(
            {"ts": _utc(), "issues": issues, "never_submit": True},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def apply_correctives(attempt_dir: Path, issues: list[dict], *, correct: bool) -> list[str]:
    """Apply safe sentinels. Returns list of actions taken."""
    taken: list[str] = []
    if not correct or not issues:
        return taken
    # Scope create-account sentinel to this attempt
    os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = str(
        attempt_dir / ".captcha_continue"
    )
    kinds = {i.get("kind") for i in issues}
    correctives = {i.get("corrective") for i in issues}
    if "force_create_account" in correctives:
        path = create_account_sentinel_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"monitor {_utc()}\n", encoding="utf-8")
            taken.append(f"wrote {path}")
        except Exception as e:
            taken.append(f"create_account_sentinel_failed:{e}")
    if "pause_for_fix" in correctives or "midwizard_advance" in kinds:
        pause = attempt_dir / ".fill_paused"
        try:
            # Prefer attempt-scoped pause via env if fill honors FASTFILL_FILL_PAUSE_FILE
            os.environ.setdefault("FASTFILL_FILL_PAUSE_FILE", str(pause))
            pause.write_text(f"monitor pause {_utc()}\n", encoding="utf-8")
            taken.append(f"wrote {pause}")
        except Exception as e:
            taken.append(f"pause_failed:{e}")
    if "force_us_phone_country" in correctives:
        tip = attempt_dir / ".force_us_phone_country"
        try:
            tip.write_text(
                "correct Country Phone Code + Address Country to United States (+1); "
                "never type Indeed/LinkedIn/job board into dial filter; never submit\n",
                encoding="utf-8",
            )
            taken.append(f"wrote {tip}")
        except Exception as e:
            taken.append(f"force_us_phone_country_failed:{e}")
    return taken


def scan_once(
    cycle_dir: Path,
    *,
    correct: bool = False,
    seen: set[str] | None = None,
) -> dict[str, Any]:
    seen = seen if seen is not None else set()
    out: dict[str, Any] = {
        "ts": _utc(),
        "cycle_dir": str(cycle_dir),
        "attempts": [],
    }
    from stale_skip import apply_stale_skip, detect_stale_skip

    for attempt in list_attempt_dirs(cycle_dir):
        steps = read_jsonl_tail(attempt / "fill_steps.jsonl")
        issues = detect_wrong_paths(attempt, steps)
        # Dedup by kind+step so we don't spam sentinels every poll
        fresh = []
        for i in issues:
            key = f"{attempt.name}:{i.get('kind')}:{i.get('step')}"
            if key in seen:
                continue
            seen.add(key)
            fresh.append(i)
        row: dict[str, Any] = {
            "attempt": attempt.name,
            "steps": len(steps),
            "issues": issues,
            "fresh": fresh,
        }
        if fresh:
            alert = _write_alert(attempt, issues)
            row["alert"] = str(alert)
            row["actions"] = apply_correctives(attempt, fresh, correct=correct)
            print(
                f"[monitor] {attempt.name}: {len(fresh)} new issue(s) "
                f"→ {row.get('actions')}",
                flush=True,
            )
        # Stale/stuck skip — CAPTCHA budget, login wall no recovery, no progress
        skip = detect_stale_skip(attempt, steps, issues=issues)
        if skip:
            skip_key = f"{attempt.name}:stale_skip:{skip.get('reason')}"
            if skip_key not in seen:
                seen.add(skip_key)
                url = _captcha_waiting_url(attempt)
                result = apply_stale_skip(
                    attempt, skip, url=url, mode="attended"
                )
                row["stale_skip"] = result
                print(
                    f"[monitor] SKIP {attempt.name}: {skip.get('fail_class')} "
                    f"— {skip.get('reason')} (next job)",
                    flush=True,
                )
        out["attempts"].append(row)
    return out


def self_test() -> None:
    # Pure detection without filesystem cycle
    steps = [
        {
            "step": 7,
            "action": "fill_text",
            "field_type": "EMAIL",
            "via": "greenhouse_selector_pack",
            "url": "https://job-boards.greenhouse.io/stripe/jobs/5515078",
        }
    ]
    # Simulate CAPTCHA waiting on Stripe login via temp dir
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        ad = Path(td)
        (ad / "CAPTCHA_WAITING.md").write_text(
            "url=https://dashboard.stripe.com/login\n", encoding="utf-8"
        )
        issues = detect_wrong_paths(ad, steps)
        assert any(i["kind"] == "sign_in_pack_fill" for i in issues), issues
        assert create_account_link_priority("Create account") == 0
        assert normalize_auth_label("Sign in.") == "sign in"
        # Stale skip after budget
        import os
        import time

        from stale_skip import apply_stale_skip, detect_stale_skip

        (ad / "fill_steps.jsonl").write_text(
            json.dumps(steps[0]) + "\n", encoding="utf-8"
        )
        (ad / ".captcha_waiting.json").write_text(
            json.dumps({"ts": time.time() - 200, "timeout_s": 120, "url": "https://dashboard.stripe.com/login"}),
            encoding="utf-8",
        )
        os.environ["FASTFILL_CAPTCHA_TIMEOUT_S"] = "90"
        skip = detect_stale_skip(ad, steps, issues=issues)
        assert skip and skip["fail_class"] == "BLOCKED", skip
        apply_stale_skip(ad, skip)
        assert (ad / "FIX_SKIPPED.md").is_file()
        os.environ.pop("FASTFILL_CAPTCHA_TIMEOUT_S", None)
    print("live_fill_monitor.self_test: OK")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--cycle-dir", type=Path, default=None)
    ap.add_argument("--watch-latest", action="store_true")
    ap.add_argument("--correct", action="store_true", help="Write safe sentinels")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--max-s", type=float, default=0.0, help="0 = run until Ctrl-C")
    args = ap.parse_args(argv)

    if args.self_test:
        self_test()
        return 0

    cycle = args.cycle_dir
    if args.watch_latest or cycle is None:
        cycle = newest_cycle_dir()
    if cycle is None or not Path(cycle).is_dir():
        print("[monitor] no cycle dir found", flush=True)
        return 1
    cycle = Path(cycle)
    print(f"[monitor] watching {cycle} correct={args.correct}", flush=True)

    seen: set[str] = set()
    t0 = time.time()
    while True:
        # Refresh to newest when --watch-latest
        if args.watch_latest:
            latest = newest_cycle_dir()
            if latest and latest != cycle:
                cycle = latest
                seen.clear()
                print(f"[monitor] switched → {cycle}", flush=True)
        report = scan_once(cycle, correct=args.correct, seen=seen)
        if args.once:
            print(json.dumps(report, indent=2)[:4000])
            return 0
        if args.max_s and (time.time() - t0) >= args.max_s:
            return 0
        time.sleep(max(1.0, float(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
