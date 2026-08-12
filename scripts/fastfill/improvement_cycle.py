#!/usr/bin/env python3
"""Exhaustive fill improvement cycle — sole control-plane entrypoint.

Phases: baseline → train/fix → (optional) A/B → plateau forks → cost.
Writes durable decisions to learning_store/improvement_decisions.jsonl.
Dummy-only; never-submit; never solve CAPTCHA; bot-pressure cooldown on bursts.

CLI::

    python improvement_cycle.py --self-test
    python improvement_cycle.py --status
    python improvement_cycle.py --phase baseline
    python improvement_cycle.py --phase all --mode unattended --limit 4
    python improvement_cycle.py --phase train --mode attended --with-monitor --limit 4
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STORE = HERE / "learning_store"
DECISIONS = STORE / "improvement_decisions.jsonl"
RESULTS = ROOT / "skyvern_runtime" / "real_job_results" / "improvement_cycles"

sys.path.insert(0, str(HERE))

from captcha_cooldown import CaptchaCooldownState, sleep_cooldown  # noqa: E402
from fail_taxonomy import (  # noqa: E402
    FIX_PRIORITY,
    apply_midwizard_to_decision,
    classify_attempt,
    top_fix_class,
)
from stale_skip import (  # noqa: E402
    agent4_wait_s,
    captcha_budget_s,
)


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def append_decision(row: dict) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    row = {"ts": _utc(), **row}
    with DECISIONS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def load_decisions(limit: int = 50) -> list[dict]:
    if not DECISIONS.is_file():
        return []
    rows: list[dict] = []
    for line in DECISIONS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows[-limit:]


def _run_py(
    script: str,
    args: list[str],
    *,
    env: dict | None = None,
    timeout: int | None = None,
) -> int:
    cmd = [sys.executable, str(HERE / script), *args]
    e = dict(os.environ)
    if env:
        e.update(env)
    # Always dummy-safe for subprocess fills
    e.setdefault("TEST_MODE", "1")
    e.pop("FASTFILL_ALLOW_REAL", None)
    e["FASTFILL_REAL_PROFILE"] = "0"
    print(f"[improvement] $ {' '.join(cmd)}", flush=True)
    p = subprocess.run(cmd, env=e, cwd=str(ROOT), timeout=timeout)
    return int(p.returncode)


def _run_gym_ats(*, smoke: bool = True) -> dict[str, Any]:
    """Run ATS offline gym; returns {ok, ...}."""
    try:
        from gym.ats.runner import run_ats_gym

        return run_ats_gym(smoke=smoke)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _run_gym_formfactory(*, smoke: bool = True) -> dict[str, Any]:
    try:
        import importlib

        mod = importlib.import_module("gym.formfactory_runner")
        return mod.run_formfactory_gym(smoke=smoke)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def phase_baseline(*, dry: bool = False) -> dict[str, Any]:
    """Phase A: offline gates + gym smoke + optional short dry cycle."""
    results: dict[str, Any] = {"phase": "baseline", "checks": {}}
    for name, argv in (
        ("fail_taxonomy", ["fail_taxonomy.py"]),
        ("captcha_cooldown", ["captcha_cooldown.py"]),
        ("live_gate", ["live_gate.py", "--self-test"]),
        ("playbooks", ["playbooks.py", "--self-test"]),
        ("regression_deepeval", ["regression_deepeval.py", "--self-test"]),
        ("regression_gates", ["regression_gates.py", "--self-test"]),
        ("metrics_timeline", ["metrics_timeline.py", "--self-test"]),
        ("answer_memory_ab", ["answer_memory_ab.py", "--self-test"]),
    ):
        # Scripts that are modules with __main__
        rc = _run_py(argv[0], argv[1:])
        results["checks"][name] = rc
        if rc != 0:
            append_decision(
                {
                    "phase": "baseline",
                    "decision": "KILL",
                    "reason": f"baseline_check_failed:{name}",
                    "checks": results["checks"],
                }
            )
            results["decision"] = "KILL"
            return results

    # Mid-wizard characterization (in-process)
    d = apply_midwizard_to_decision(
        {
            "ready_for_review": True,
            "footer_kind": "ADVANCE",
            "never_submit": True,
            "platform": "workday",
        },
        {"success": True, "verdict": "SUCCESS", "reasons": []},
    )
    if d.get("success") or d.get("verdict") != "FAIL_MIDWIZARD":
        append_decision(
            {
                "phase": "baseline",
                "decision": "KILL",
                "reason": "midwizard_fixture_not_demoted",
            }
        )
        results["decision"] = "KILL"
        return results
    results["checks"]["midwizard_fixture"] = 0

    # Offline gym smoke (ATS + FormFactory) — must pass before live arming
    ats = _run_gym_ats(smoke=True)
    results["checks"]["ats_gym"] = 0 if ats.get("ok") else 1
    results["ats_gym"] = {
        k: ats.get(k) for k in ("ok", "error", "cases") if k in ats or k == "ok"
    }
    if not ats.get("ok"):
        append_decision(
            {
                "phase": "baseline",
                "decision": "KILL",
                "reason": "ats_gym_failed",
                "ats_gym": results["ats_gym"],
            }
        )
        results["decision"] = "KILL"
        return results

    ff = _run_gym_formfactory(smoke=True)
    ff_ok = bool(ff.get("ok"))
    # Soft threshold for FormFactory smoke: require ok flag; accuracy logged
    results["checks"]["formfactory_gym"] = 0 if ff_ok else 1
    results["formfactory_gym"] = {
        k: ff.get(k)
        for k in ("ok", "n", "passed", "field_accuracy", "error", "cases")
        if k in ff or k == "ok"
    }
    if not ff_ok:
        append_decision(
            {
                "phase": "baseline",
                "decision": "KILL",
                "reason": "formfactory_gym_failed",
                "formfactory_gym": results["formfactory_gym"],
            }
        )
        results["decision"] = "KILL"
        return results

    # Scorecard gate (may be soft if no artifacts)
    rc = _run_py("scorecard_fast.py", ["--gate"])
    results["checks"]["scorecard_gate"] = rc

    # Dry cycle + regression lane
    rc = _run_py("cycle_orchestrate.py", ["--self-test"])
    results["checks"]["cycle_self_test"] = rc
    if rc != 0:
        append_decision(
            {
                "phase": "baseline",
                "decision": "KILL",
                "reason": "cycle_self_test_failed",
                "checks": results["checks"],
            }
        )
        results["decision"] = "KILL"
        return results

    if not dry:
        # Seed empty timeline with a synthetic baseline row (counts only)
        try:
            from metrics_timeline import TIMELINE_PATH, build_row, load_timeline

            if not load_timeline():
                TIMELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
                row = build_row(
                    {
                        "n": 0,
                        "passed": 0,
                        "failed": 0,
                        "slo_rollup": {
                            "safety_fail_n": 0,
                            "quality_fail_n": 0,
                            "fail_reasons": {},
                            "by_platform": {},
                            "safety": {"never_submit_all": True},
                        },
                    },
                    label="baseline_seed",
                )
                with TIMELINE_PATH.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, sort_keys=True) + "\n")
                results["timeline_seeded"] = True
        except Exception as e:  # noqa: BLE001
            results["timeline_seed_error"] = str(e)

    append_decision(
        {
            "phase": "baseline",
            "decision": "BASELINE_SET",
            "reason": "offline_gates_green",
            "checks": results["checks"],
            "mode": "unattended",
        }
    )
    results["decision"] = "BASELINE_SET"
    return results


def start_live_monitor(*, interval: float = 4.0) -> subprocess.Popen:
    """Spawn live_fill_monitor --watch-latest --correct (sibling of train)."""
    cmd = [
        sys.executable,
        "-u",
        str(HERE / "live_fill_monitor.py"),
        "--watch-latest",
        "--correct",
        "--interval",
        str(interval),
    ]
    e = dict(os.environ)
    e.setdefault("TEST_MODE", "1")
    e["FASTFILL_REAL_PROFILE"] = "0"
    e.pop("FASTFILL_ALLOW_REAL", None)
    print(f"[improvement] starting monitor: {' '.join(cmd)}", flush=True)
    return subprocess.Popen(cmd, env=e, cwd=str(ROOT))


def stop_live_monitor(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def phase_train(
    *,
    limit: int = 4,
    headed: bool = False,
    captcha_burst: int = 3,
    captcha_cooldown_s: int = 180,
    max_fix_iters: int = 8,
    working_streak: int = 3,
    min_platforms: int = 3,
    require_workday: bool = True,
    plateau_epsilon: float = 0.02,
    dry_run: bool = False,
    with_monitor: bool = False,
    urls_json: Path | str | None = None,
) -> dict[str, Any]:
    """Phase B: run a variety cycle, classify, cooldown on CAPTCHA bursts, decide."""
    from live_gate import live_fill_allowed

    if not dry_run:
        ok, reason = live_fill_allowed(force=False)
        if not ok:
            append_decision(
                {
                    "phase": "train",
                    "decision": "KILL",
                    "reason": f"live_refused:{reason}",
                }
            )
            return {
                "phase": "train",
                "decision": "KILL",
                "exit_code": 3,
                "reason": reason,
                "message": (
                    "Live train blocked. Run --phase train_offline then "
                    "--phase gate_live (or --force-live)."
                ),
            }

    RESULTS.mkdir(parents=True, exist_ok=True)
    run_id = f"imp_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    out_base = RESULTS / run_id
    out_base.mkdir(parents=True, exist_ok=True)

    captcha_state = CaptchaCooldownState(
        burst_n=captcha_burst,
        cooldown_s=float(captcha_cooldown_s),
    )
    taxonomy_counts: dict[str, int] = {k: 0 for k in FIX_PRIORITY}
    taxonomy_counts.update({"BLOCKED": 0, "SUCCESS": 0, "FAIL_ENV": 0, "SAFETY_ABORT": 0})

    budget = captcha_budget_s()  # attended default ~120s (env-overridable)
    a4_wait = agent4_wait_s(headed=headed)

    argv = [
        "--limit",
        str(limit),
        "--success-streak",
        str(working_streak),
        "--min-platforms",
        str(min_platforms),
        "--max-retries",
        "2",
    ]
    if urls_json:
        argv.extend(["--urls-json", str(urls_json)])
    if dry_run:
        argv.append("--dry-run")
    elif headed:
        argv.append("--headed")
        argv.extend(["--captcha-timeout", str(int(budget))])
    else:
        argv.extend(["--headless", "--no-captcha-wait"])

    # Attended: short Agent4 wait; unfixable CAPTCHA/login_wall skips wait entirely.
    # Unattended: never block on Agent4 markers.
    # Headed still shows the browser, but do not stop for manual ❚❚ between actions
    # during the improvement loop (CAPTCHA pause remains separate, short budget).
    cycle_env = {
        "FASTFILL_AGENT4_WAIT_S": str(int(a4_wait)) if headed else "0",
        "FASTFILL_CAPTCHA_TIMEOUT_S": str(int(budget)),
    }
    if headed:
        cycle_env["FASTFILL_FILL_PAUSE"] = "0"

    monitor_proc: subprocess.Popen | None = None
    if with_monitor and headed and not dry_run:
        monitor_proc = start_live_monitor()

    # Run cycle as subprocess; parse latest rollup under real_job_results
    before = set(p.name for p in (ROOT / "skyvern_runtime" / "real_job_results").glob("cycle_*"))
    try:
        rc = _run_py(
            "cycle_orchestrate.py",
            argv,
            env=cycle_env,
            timeout=None if dry_run else 3600 * 3,
        )
    finally:
        stop_live_monitor(monitor_proc)

    after = {
        p
        for p in (ROOT / "skyvern_runtime" / "real_job_results").glob("cycle_*")
        if p.name not in before
    }
    rollup_path = None
    if after:
        newest = max(after, key=lambda p: p.stat().st_mtime)
        cand = newest / "rollup.json"
        if cand.is_file():
            rollup_path = cand
    # Fallback: newest cycle rollup overall
    if rollup_path is None:
        cycles = sorted(
            (ROOT / "skyvern_runtime" / "real_job_results").glob("cycle_*/rollup.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        rollup_path = cycles[0] if cycles else None

    rollup: dict[str, Any] = {}
    if rollup_path and rollup_path.is_file():
        rollup = json.loads(rollup_path.read_text(encoding="utf-8"))

    platforms_ok = list(rollup.get("platforms_ok") or [])
    attempts = rollup.get("attempts") or []
    success_n = 0
    for a in attempts:
        # Prefer taxonomy from attempt if present; else classify from fields
        verdict = str(a.get("verdict") or "").upper()
        fake_report = {
            "platform": a.get("platform"),
            "blocker": "captcha" if verdict == "BLOCKED" else None,
            "never_submit": True,
            "advanced_incomplete": verdict == "FAIL_MIDWIZARD",
        }
        fake_decision = {
            "success": bool(a.get("success")),
            "verdict": verdict or ("SUCCESS" if a.get("success") else "FAIL_BLANK"),
            "reasons": [],
        }
        # Load richer report if out_dir present
        out_dir = a.get("out_dir")
        if out_dir:
            rp = Path(out_dir) / "report.json"
            sp = Path(out_dir) / "cycle_summary.json"
            if rp.is_file():
                try:
                    fake_report.update(json.loads(rp.read_text(encoding="utf-8")))
                except Exception:
                    pass
            if sp.is_file():
                try:
                    summ = json.loads(sp.read_text(encoding="utf-8"))
                    fake_decision.update(summ.get("decision") or {})
                except Exception:
                    pass
        classified = classify_attempt(fake_report, fake_decision)
        code = classified["code"]
        taxonomy_counts[code] = taxonomy_counts.get(code, 0) + 1
        if code == "SUCCESS":
            success_n += 1
        if code == "BLOCKED":
            captcha_state.record_blocked()
            action = captcha_state.next_action()
            if action["action"] == "cooldown":
                append_decision(
                    {
                        "phase": "fix",
                        "run_id": run_id,
                        "decision": "COOLDOWN",
                        "reason": action["reason"],
                        "signals": {
                            "sleep_s": action["sleep_s"],
                            "hits": action["hits"],
                            "escalations": action["escalations"],
                        },
                        "mode": "attended" if headed else "unattended",
                    }
                )
                print(
                    f"[improvement] bot pressure — sleeping {action['sleep_s']:.0f}s "
                    f"(no browser)",
                    flush=True,
                )
                sleep_cooldown(float(action["sleep_s"]))
                captcha_state.mark_cooldown_done()
            elif action["action"] == "pause_bot_pressure":
                append_decision(
                    {
                        "phase": "fix",
                        "run_id": run_id,
                        "decision": "PAUSE_BOT_PRESSURE",
                        "reason": action["reason"],
                        "signals": {"taxonomy": taxonomy_counts, "rollup": str(rollup_path)},
                        "mode": "attended" if headed else "unattended",
                    }
                )
                return {
                    "decision": "PAUSE_BOT_PRESSURE",
                    "exit_code": 4,
                    "taxonomy": taxonomy_counts,
                    "run_id": run_id,
                }

    lane = rollup.get("regression_lane") or {}
    lane_ok = lane.get("ok", True) if lane else True
    fix_class = top_fix_class(taxonomy_counts)

    # Working streak heuristics from rollup
    streak = int(rollup.get("success_streak_final") or 0)
    workday_ok = any("workday" in str(p).lower() for p in platforms_ok)
    workday_attempted = any(
        "workday" in str(a.get("platform") or "").lower() for a in attempts
    )
    mid_wrong_thrash = (
        taxonomy_counts.get("FAIL_MIDWIZARD", 0)
        + taxonomy_counts.get("FAIL_WRONG_VALUE", 0)
        + taxonomy_counts.get("FAIL_THRASH", 0)
    )

    working = (
        streak >= working_streak
        and len(platforms_ok) >= min_platforms
        and lane_ok
        and mid_wrong_thrash == 0
        and (workday_ok or not require_workday or not workday_attempted)
        and rc == 0
    )

    decision = "CONTINUE"
    reason = "more_fix_needed"
    exit_code = 0
    if taxonomy_counts.get("SAFETY_ABORT", 0) > 0:
        decision, reason, exit_code = "KILL", "safety_abort", 3
    elif not lane_ok and os.environ.get("FASTFILL_CYCLE_REGRESSION_SOFT", "0") != "1":
        decision, reason, exit_code = "KILL", "regression_lane_failed", 1
    elif working:
        decision, reason, exit_code = "PROMOTE", "working_streak_held", 0
    elif max_fix_iters <= 0:
        decision, reason, exit_code = "PLATEAU", "max_fix_iters", 2
    elif fix_class:
        decision, reason = "CONTINUE", f"fix_next:{fix_class}"
        exit_code = 0
    else:
        decision, reason, exit_code = "PLATEAU", "no_fixable_progress", 2

    signals = {
        "cycle_success_n": success_n,
        "platforms_ok": platforms_ok,
        "blocked_n": taxonomy_counts.get("BLOCKED", 0),
        "regression_lane_ok": lane_ok,
        "fail_taxonomy": taxonomy_counts,
        "top_fix_class": fix_class,
        "streak": streak,
        "cycle_rc": rc,
        "plateau_epsilon": plateau_epsilon,
    }
    append_decision(
        {
            "phase": "fix",
            "run_id": run_id,
            "decision": decision,
            "reason": reason,
            "signals": signals,
            "mode": "attended" if headed else "unattended",
            "rollup_path": str(rollup_path) if rollup_path else None,
        }
    )
    # Persist snapshot
    (out_base / "summary.json").write_text(
        json.dumps(
            {"decision": decision, "reason": reason, "signals": signals, "rollup": rollup},
            indent=2,
            default=str,
        )
    )
    return {
        "decision": decision,
        "reason": reason,
        "exit_code": exit_code,
        "taxonomy": taxonomy_counts,
        "fix_class": fix_class,
        "run_id": run_id,
        "signals": signals,
    }


def phase_train_offline(
    *,
    max_iters: int = 10,
    full_gym: bool = False,
) -> dict[str, Any]:
    """Run offline gym (+ baseline units) until SLO or max_iters; write OFFLINE_GATE_PASS."""
    from live_gate import write_offline_gate_pass

    history: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for i in range(max(1, max_iters)):
        ats = _run_gym_ats(smoke=True)
        ff = _run_gym_formfactory(smoke=not full_gym)
        # Unit lane quick check
        rc_reg = _run_py("regression_deepeval.py", ["--self-test"])
        rc_gate = _run_py("live_gate.py", ["--self-test"])
        ff_acc = float(ff.get("field_accuracy") or 0.0)
        # FormFactory smoke SLO: ok and accuracy >= 0.80 when reported
        ff_slo = bool(ff.get("ok")) and (ff_acc >= 0.80 if "field_accuracy" in ff else True)
        ats_slo = bool(ats.get("ok"))
        units_slo = rc_reg == 0 and rc_gate == 0
        round_ok = ats_slo and ff_slo and units_slo
        last = {
            "iter": i + 1,
            "ats_ok": ats_slo,
            "formfactory_ok": ff_slo,
            "formfactory_accuracy": ff_acc,
            "units_ok": units_slo,
            "ok": round_ok,
        }
        history.append(last)
        append_decision(
            {
                "phase": "train_offline",
                "decision": "OFFLINE_PASS" if round_ok else "OFFLINE_CONTINUE",
                "reason": "gym_slo" if round_ok else "gym_or_units_failed",
                **last,
            }
        )
        if round_ok:
            path = write_offline_gate_pass(
                {
                    "ok": True,
                    "ats_gym": {"ok": True},
                    "formfactory_gym": {
                        "ok": True,
                        "field_accuracy": ff_acc,
                        "smoke": not full_gym,
                    },
                    "regression_deepeval": rc_reg,
                    "iters": i + 1,
                    "history": history,
                }
            )
            return {
                "phase": "train_offline",
                "decision": "OFFLINE_PASS",
                "exit_code": 0,
                "offline_gate_pass": str(path),
                "history": history,
            }
        # No auto code-fix here — Agent4 / human iterates; loop records failures
        # and stops at max_iters so we don't spin forever.
    append_decision(
        {
            "phase": "train_offline",
            "decision": "OFFLINE_PLATEAU",
            "reason": "max_iters_without_slo",
            "history": history,
        }
    )
    return {
        "phase": "train_offline",
        "decision": "OFFLINE_PLATEAU",
        "exit_code": 4,
        "history": history,
        "last": last,
    }


def phase_gate_live() -> dict[str, Any]:
    """Arm live canary only if OFFLINE_GATE_PASS exists (re-check gym smoke)."""
    from live_gate import arm_canary, read_offline_gate_pass

    offline = read_offline_gate_pass()
    if not offline or not offline.get("ok"):
        # Fresh gym check → write pass if green
        train = phase_train_offline(max_iters=1)
        if train.get("decision") != "OFFLINE_PASS":
            append_decision(
                {
                    "phase": "gate_live",
                    "decision": "KILL",
                    "reason": "offline_not_green",
                    "train_offline": train.get("decision"),
                }
            )
            return {
                "phase": "gate_live",
                "decision": "KILL",
                "exit_code": 3,
                "train_offline": train,
            }
    else:
        # Re-verify smoke quickly
        ats = _run_gym_ats(smoke=True)
        ff = _run_gym_formfactory(smoke=True)
        if not ats.get("ok") or not ff.get("ok"):
            append_decision(
                {
                    "phase": "gate_live",
                    "decision": "KILL",
                    "reason": "gym_regressed_before_arm",
                }
            )
            return {"phase": "gate_live", "decision": "KILL", "exit_code": 3}

    path = arm_canary(reason="gate_live")
    append_decision(
        {
            "phase": "gate_live",
            "decision": "ARMED",
            "reason": "offline_green",
            "armed_path": str(path),
        }
    )
    return {
        "phase": "gate_live",
        "decision": "ARMED",
        "exit_code": 0,
        "armed_path": str(path),
    }


def phase_canary_live(
    *,
    limit: int = 7,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run eval_suite --limit N once, write LIVE_CANARY_DONE, hard stop."""
    from live_gate import live_fill_allowed, require_live_allowed, write_canary_done

    require_live_allowed(force=force)
    if dry_run:
        write_canary_done({"dry_run": True, "n": limit, "passed": 0})
        append_decision(
            {
                "phase": "canary_live",
                "decision": "CANARY_DONE",
                "reason": "dry_run",
                "limit": limit,
            }
        )
        return {
            "phase": "canary_live",
            "decision": "CANARY_DONE",
            "exit_code": 0,
            "dry_run": True,
        }

    rc = _run_py(
        "eval_suite.py",
        ["--limit", str(limit)] + (["--force-live"] if force else []),
    )
    # eval_suite may exit non-zero on strict; canary still "done"
    summary_path = ROOT / "skyvern_runtime" / "eval_results"
    # Prefer latest eval_summary if present
    passed = None
    n = limit
    try:
        # Most recent eval_summary.json under eval_results
        candidates = sorted(
            summary_path.rglob("eval_summary.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            summary = json.loads(candidates[0].read_text(encoding="utf-8"))
            passed = summary.get("passed")
            n = summary.get("n", limit)
    except Exception:
        pass

    done_path = write_canary_done(
        {
            "limit": limit,
            "eval_suite_rc": rc,
            "passed": passed,
            "n": n,
            "force": force,
        }
    )
    append_decision(
        {
            "phase": "canary_live",
            "decision": "CANARY_DONE",
            "reason": "eval_suite_limit_complete",
            "eval_suite_rc": rc,
            "passed": passed,
            "n": n,
            "done_path": str(done_path),
        }
    )
    ok_arm, _ = live_fill_allowed(force=False)
    assert not ok_arm, "canary done must disarm live"
    return {
        "phase": "canary_live",
        "decision": "CANARY_DONE",
        "exit_code": 0 if rc == 0 else 1,
        "eval_suite_rc": rc,
        "done_path": str(done_path),
        "passed": passed,
        "n": n,
        "message": "Hard stop — delete LIVE_CANARY_DONE and re-run gate_live to arm again.",
    }


def status_report() -> dict[str, Any]:
    rows = load_decisions(30)
    latest = rows[-1] if rows else None
    try:
        from live_gate import gate_status

        gates = gate_status()
    except Exception as e:  # noqa: BLE001
        gates = {"error": str(e)}
    return {
        "decisions_path": str(DECISIONS),
        "n_decisions": len(load_decisions(10_000)),
        "latest": latest,
        "recent": rows[-10:],
        "live_gate": gates,
    }


def _self_test() -> int:
    # In-process unit pieces
    import fail_taxonomy as ft
    import captcha_cooldown as cc
    import stale_skip as ss
    import live_gate as lg

    ft._self_test()
    cc._self_test()
    ss._self_test()
    lg._self_test()
    # Promote compare strictness: answer_memory already self-tests
    _run_py("answer_memory_ab.py", ["--self-test"])
    _run_py("playbooks.py", ["--self-test"])
    # Dry baseline (includes gym smoke — may be slow)
    r = phase_baseline(dry=True)
    assert r.get("decision") == "BASELINE_SET", r
    # Dry train must not need live arm when dry_run=True
    t = phase_train(limit=1, dry_run=True, working_streak=99, min_platforms=99, require_workday=False)
    assert t.get("decision") in ("CONTINUE", "PLATEAU", "PROMOTE", "KILL"), t
    assert captcha_budget_s() >= 5.0
    # Offline train one iter should write pass if gym green
    off = phase_train_offline(max_iters=1)
    assert off.get("decision") in ("OFFLINE_PASS", "OFFLINE_PLATEAU"), off
    print("improvement_cycle self-test OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Exhaustive fill improvement cycle")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument(
        "--phase",
        choices=(
            "baseline",
            "train",
            "train_offline",
            "gate_live",
            "canary_live",
            "all",
            "offline_then_canary",
        ),
        default="all",
    )
    ap.add_argument("--mode", choices=("unattended", "attended"), default="unattended")
    ap.add_argument("--limit", type=int, default=4)
    ap.add_argument("--working-streak", type=int, default=3)
    ap.add_argument("--min-platforms", type=int, default=3)
    ap.add_argument("--require-workday", action="store_true", default=True)
    ap.add_argument("--no-require-workday", action="store_true")
    ap.add_argument("--max-fix-iters", type=int, default=8)
    ap.add_argument("--max-iters", type=int, default=10, help="train_offline max iterations")
    ap.add_argument("--plateau-epsilon", type=float, default=0.02)
    ap.add_argument("--captcha-burst", type=int, default=3)
    ap.add_argument("--captcha-cooldown-s", type=int, default=180)
    ap.add_argument(
        "--with-monitor",
        action="store_true",
        help="Attended: also start live_fill_monitor --watch-latest --correct",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--force-live",
        action="store_true",
        help="Bypass live_gate arming (emergency / explicit canary)",
    )
    ap.add_argument(
        "--full-gym",
        action="store_true",
        help="FormFactory full suite during train_offline",
    )
    ap.add_argument(
        "--urls-json",
        type=Path,
        default=None,
        help="Explicit never-seen / custom queue JSON passed to cycle_orchestrate",
    )
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if args.status:
        print(json.dumps(status_report(), indent=2, default=str))
        return 0

    if args.force_live:
        os.environ["FASTFILL_FORCE_LIVE"] = "1"

    require_wd = bool(args.require_workday) and not args.no_require_workday
    headed = args.mode == "attended"
    exit_code = 0

    if args.phase in ("baseline", "all"):
        base = phase_baseline(dry=bool(args.dry_run))
        print(json.dumps({"baseline": base}, indent=2, default=str))
        if base.get("decision") == "KILL":
            return 3

    if args.phase in ("train_offline", "offline_then_canary"):
        off = phase_train_offline(max_iters=args.max_iters, full_gym=bool(args.full_gym))
        print(json.dumps({"train_offline": off}, indent=2, default=str))
        if off.get("decision") != "OFFLINE_PASS":
            return int(off.get("exit_code") or 4)

    if args.phase in ("gate_live", "offline_then_canary"):
        gated = phase_gate_live()
        print(json.dumps({"gate_live": gated}, indent=2, default=str))
        if gated.get("decision") != "ARMED":
            return int(gated.get("exit_code") or 3)

    if args.phase in ("canary_live", "offline_then_canary"):
        canary_limit = 7 if args.phase == "offline_then_canary" else args.limit
        canary = phase_canary_live(
            limit=canary_limit,
            force=bool(args.force_live),
            dry_run=bool(args.dry_run),
        )
        print(json.dumps({"canary_live": canary}, indent=2, default=str))
        print(canary.get("message") or "Canary complete — hard stop.", flush=True)
        return int(canary.get("exit_code") or 0)

    if args.phase in ("train", "all"):
        train = phase_train(
            limit=args.limit,
            headed=headed,
            captcha_burst=args.captcha_burst,
            captcha_cooldown_s=args.captcha_cooldown_s,
            max_fix_iters=args.max_fix_iters,
            working_streak=args.working_streak,
            min_platforms=args.min_platforms,
            require_workday=require_wd,
            plateau_epsilon=args.plateau_epsilon,
            dry_run=bool(args.dry_run),
            with_monitor=bool(args.with_monitor),
            urls_json=args.urls_json,
        )
        print(json.dumps({"train": train}, indent=2, default=str))
        exit_code = int(train.get("exit_code") or 0)
        # Hint next fix class for Agent4
        if train.get("fix_class"):
            print(
                f"[improvement] NEXT FIX CLASS: {train['fix_class']} "
                f"(see CYCLE_AGENTS.md / fail_taxonomy.FIX_PRIORITY)",
                flush=True,
            )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
