#!/usr/bin/env python3
"""CI-like regression gates for fastfill SOTA (dummy-only, never-submit).

Merge lane (no browser by default)::

  skyvern_runtime/venv/bin/python scripts/fastfill/regression_gates.py
  skyvern_runtime/venv/bin/python scripts/fastfill/regression_gates.py --self-test

Optional cycle contract (Agent2/3/4 artifacts)::

  skyvern_runtime/venv/bin/python scripts/fastfill/regression_gates.py \\
    --cycle-dir skyvern_runtime/real_job_results/cycle_live_…/ashby_r0

Does **not** kill fills: unit + scorecard + optional artifact checks only.
Full ``eval_suite --strict`` is opt-in (``--run-eval``) because it needs network.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EVAL_DIR = ROOT / "skyvern_runtime" / "eval_results"
PY = ROOT / "skyvern_runtime" / "venv" / "bin" / "python"
if not PY.is_file():
    PY = Path(sys.executable)


def _run(cmd: list[str], *, label: str) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, f"[{label}]\n{out}"


def gate_unit_script(script: str) -> tuple[int, str]:
    """Run a unit test module; exit 0 only when all tests pass."""
    return _run([str(PY), str(HERE / script)], label=script)


def gate_tier1() -> tuple[int, str]:
    """Offline merge hygiene: unit + gym + re-score last live artifact.

    Does **not** run a headed browser. Exit 0 with gym green is ``gym_pass`` only.
    ``live_pass`` comes solely from scoring an existing live artifact (may be stale)
    and is reported separately — never promote gym green to live signoff.
    See ``GYM_VS_LIVE.md``.
    """
    parts: list[str] = []
    worst = 0
    gym_labels = {
        "test_field_done.py",
        "test_action_supervisor.py",
        "test_fill_contract.py",
        "adversarial.py",
        "detection_matrix.py",
    }
    gym_codes: list[int] = []
    live_code: int | None = None
    live_pass: bool | None = None

    steps: list[tuple[str, Callable[[], tuple[int, str]]]] = [
        ("test_field_done.py", lambda: gate_unit_script("test_field_done.py")),
        ("test_action_supervisor.py", lambda: gate_unit_script("test_action_supervisor.py")),
    ]
    fill_contract = HERE / "test_fill_contract.py"
    if fill_contract.is_file():
        steps.append(
            ("test_fill_contract.py", lambda: gate_unit_script("test_fill_contract.py"))
        )
    steps.extend(
        [
            (
                "adversarial.py",
                lambda: _run([str(PY), str(HERE / "gym/ats/adversarial.py")], label="adversarial"),
            ),
            (
                "detection_matrix.py",
                lambda: _run(
                    [str(PY), str(HERE / "gym/ats/detection_matrix.py")],
                    label="detection_matrix",
                ),
            ),
            (
                "reliability_gate --skip-run",
                lambda: _run(
                    [str(PY), str(HERE / "reliability_gate.py"), "--skip-run"],
                    label="reliability_gate --skip-run",
                ),
            ),
        ]
    )
    for label, fn in steps:
        c, o = fn()
        parts.append(o)
        if label in gym_labels:
            gym_codes.append(c)
        elif label == "reliability_gate --skip-run":
            live_code = c
            # Prefer explicit live_pass from gate JSON when present
            gate_path = HERE / "reliability_gate.json"
            if gate_path.is_file():
                try:
                    g = json.loads(gate_path.read_text(encoding="utf-8"))
                    if "live_pass" in g:
                        live_pass = bool(g.get("live_pass"))
                    else:
                        live_pass = bool(g.get("pass"))
                except Exception:
                    live_pass = c == 0
            else:
                live_pass = c == 0
        if c != 0:
            worst = max(worst, c if c else 1)

    gym_pass = all(c == 0 for c in gym_codes) if gym_codes else False
    summary = {
        "confidence_lane": "offline_tier1_hygiene",
        "gym_pass": gym_pass,
        "live_pass": live_pass,
        "live_score_exit": live_code,
        "live_signoff_ok": bool(live_pass),
        "note": (
            "gym_pass≠live_pass. Headed reliability_gate (no --skip-run) + "
            "flight_recorder are live truth. See GYM_VS_LIVE.md."
        ),
    }
    parts.append("[tier1_honesty]\n" + json.dumps(summary, indent=2))
    # Persist beside reliability_gate for dashboards
    out = HERE / "tier1_gate_honesty.json"
    try:
        out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass
    # Exit non-zero if gym failed OR live artifact score failed (honest).
    # Callers must still read live_pass — gym green alone is not signoff.
    return worst, "\n".join(parts)


def gate_unit_honesty() -> tuple[int, str]:
    """L0: honest metrics + vision + attribution self-tests."""
    parts: list[str] = []
    code = 0
    for script, args in (
        ("test_honest_metrics.py", []),
        ("vision_judge.py", ["--self-test"]),
        ("fill_attribution.py", ["--self-test"]),
    ):
        c, o = _run([str(PY), str(HERE / script), *args], label=script)
        parts.append(o)
        if c != 0:
            code = 1
    return code, "\n".join(parts)


def gate_scorecard_eval(*, fail_if_missing: bool = False) -> tuple[int, str]:
    """L1: scorecard on eval_results with --gate (Flash-off + honesty)."""
    if not EVAL_DIR.is_dir():
        msg = "[scorecard] FAIL — no eval_results dir (run eval_suite first)"
        return (1 if fail_if_missing else 0), msg
    if not any(EVAL_DIR.glob("eval_*.json")):
        msg = "[scorecard] FAIL — eval_results/ exists but no eval_*.json artifacts"
        return (1 if fail_if_missing else 0), msg
    return _run(
        [str(PY), str(HERE / "scorecard_fast.py"), "--eval", "--gate"],
        label="scorecard_fast --eval --gate",
    )


def gate_eval_summary_safety(summary_path: Path | None = None) -> tuple[int, str]:
    """Re-check last eval_summary for safety fails without re-running browsers."""
    path = summary_path or (EVAL_DIR / "eval_summary.json")
    if not path.is_file():
        return 0, f"[eval_summary] skip — missing {path}"
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return 1, f"[eval_summary] parse fail: {e}"

    from eval_suite import gate_exit_code

    rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    rollup = data.get("slo_rollup") if isinstance(data.get("slo_rollup"), dict) else {}
    # Recompute safety counters if older summary lacks safety_fail_n
    if "safety_fail_n" not in rollup and rows:
        from eval_suite import _slo_rollup

        rollup = _slo_rollup(rows, data.get("slo") or {})
    code = gate_exit_code(rows, rollup, strict=False, strict_safety=True)
    msg = (
        f"[eval_summary] {path.name} safety_fail_n={rollup.get('safety_fail_n')} "
        f"quality_fail_n={rollup.get('quality_fail_n')} exit={code}"
    )
    return code, msg


def check_agent2_vision(judge: dict, *, shot_exists: bool) -> list[str]:
    """Agent2 merge-block rules for SUCCESS-shaped COMPLETE claims."""
    fails: list[str] = []
    if judge.get("never_submit") is False:
        fails.append("agent2_never_submit_false")
    complete = bool(judge.get("complete"))
    empties = judge.get("empty_fields") or []
    source = str(judge.get("source") or "")
    verdict = str(judge.get("verdict") or "")
    if complete and empties:
        fails.append("agent2_complete_with_empty_fields")
    if complete and source == "heuristic_report" and shot_exists:
        fails.append("agent2_heuristic_complete_with_png")
    if verdict == "COMPLETE" and (empties or (source == "heuristic_report" and shot_exists)):
        fails.append("agent2_verdict_complete_dishonest")
    return fails


def check_agent3_attribution(
    attr: dict,
    *,
    claim_success: bool,
    fail_prefill_regressions: bool = False,
) -> list[str]:
    """Agent3: false_success / blank_bugs block SUCCESS claims."""
    fails: list[str] = []
    if not claim_success:
        return fails
    fs = attr.get("false_success") or []
    blanks = attr.get("blank_bugs") or []
    regs = attr.get("prefill_regressions") or attr.get("regressions") or []
    if fs:
        fails.append(f"agent3_false_success_n={len(fs)}")
    if blanks:
        fails.append(f"agent3_blank_bugs_n={len(blanks)}")
    if fail_prefill_regressions and regs:
        fails.append(f"agent3_prefill_regressions_n={len(regs)}")
    return fails


def check_agent4_fix_marker(cycle_dir: Path) -> list[str]:
    """If retry-after-fix was requested, require FIX_APPLIED or FIX_SKIPPED."""
    fails: list[str] = []
    needs_fix = (cycle_dir / "RETRY_AFTER_FIX.txt").is_file() or (
        cycle_dir / "UNFILLABLE_AFTER_2.md"
    ).is_file()
    if not needs_fix:
        return fails
    if (cycle_dir / "FIX_APPLIED.md").is_file():
        return fails
    if (cycle_dir / "FIX_SKIPPED.md").is_file():
        return fails
    fails.append("agent4_missing_FIX_APPLIED_or_SKIPPED")
    return fails


def gate_cycle_dir(
    cycle_dir: Path,
    *,
    fail_prefill_regressions: bool = False,
) -> tuple[int, str]:
    """L4: Agent2/3/4 contract on one attempt directory."""
    if not cycle_dir.is_dir():
        return 3, f"[cycle] not a directory: {cycle_dir}"
    fails: list[str] = []
    notes: list[str] = []

    report: dict[str, Any] = {}
    for name in ("report.json",):
        p = cycle_dir / name
        if p.is_file():
            try:
                report = json.loads(p.read_text())
            except Exception as e:
                fails.append(f"report_parse:{e}")
            break

    claim_success = str(report.get("verdict") or "").upper() in ("SUCCESS", "COMPLETE")
    vision_path = cycle_dir / "vision_judge.json"
    shot = cycle_dir / "after_fill.png"
    if not shot.is_file():
        # common aliases
        for alt in ("fast_fill.png", "screenshot.png"):
            if (cycle_dir / alt).is_file():
                shot = cycle_dir / alt
                break
    if vision_path.is_file():
        try:
            judge = json.loads(vision_path.read_text())
            a2 = check_agent2_vision(judge, shot_exists=shot.is_file())
            fails.extend(a2)
            if judge.get("complete") is True:
                claim_success = True
            notes.append(f"agent2 source={judge.get('source')} complete={judge.get('complete')}")
        except Exception as e:
            fails.append(f"vision_parse:{e}")
    elif claim_success:
        fails.append("agent2_missing_vision_judge_on_success")

    attr_path = cycle_dir / "attribution.json"
    if attr_path.is_file():
        try:
            attr = json.loads(attr_path.read_text())
            a3 = check_agent3_attribution(
                attr,
                claim_success=claim_success,
                fail_prefill_regressions=fail_prefill_regressions,
            )
            fails.extend(a3)
            notes.append(
                f"agent3 false_success={len(attr.get('false_success') or [])} "
                f"blank_bugs={len(attr.get('blank_bugs') or [])}"
            )
        except Exception as e:
            fails.append(f"attribution_parse:{e}")
    elif claim_success:
        fails.append("agent3_missing_attribution_on_success")

    fails.extend(check_agent4_fix_marker(cycle_dir))

    if report.get("never_submit") is False or report.get("submit_clicked") is True:
        fails.append("cycle_submit_safety")

    if fails:
        return 1, f"[cycle] FAIL {cycle_dir}: {fails}\n  " + "; ".join(notes)
    return 0, f"[cycle] OK {cycle_dir}\n  " + "; ".join(notes)


def self_test() -> dict[str, Any]:
    """Fixture tests for gate helpers (no browser)."""
    from eval_suite import gate_exit_code

    # Diagnostic default stays 0 even with quality fails
    rows = [{"pass": False, "slo_fails": ["verified_coverage=0.5<0.9"], "never_submit": True}]
    rollup = {"safety_fail_n": 0, "quality_fail_n": 1, "safety": {"never_submit_all": True}}
    assert gate_exit_code(rows, rollup) == 0
    assert gate_exit_code(rows, rollup, strict_safety=True) == 0
    assert gate_exit_code(rows, rollup, strict=True, strict_safety=True) == 2

    bad_safety = [
        {
            "pass": False,
            "slo_fails": ["flash_called_while_off"],
            "never_submit": True,
        }
    ]
    rollup_s = {
        "safety_fail_n": 1,
        "quality_fail_n": 0,
        "safety": {"never_submit_all": True, "flash_called_while_off": 1},
    }
    assert gate_exit_code(bad_safety, rollup_s, strict_safety=True) == 1

    from scorecard_fast import assert_flash_off_when_unrequested

    try:
        assert_flash_off_when_unrequested(
            {"flash_called": True, "flash_leftovers_requested": False}
        )
        raise AssertionError("expected flash_off assert")
    except AssertionError as e:
        assert "flash_called" in str(e)

    assert_flash_off_when_unrequested(
        {"flash_called": True, "flash_leftovers_requested": True}
    )

    a2 = check_agent2_vision(
        {
            "complete": True,
            "empty_fields": [],
            "source": "heuristic_report",
            "verdict": "COMPLETE",
            "never_submit": True,
        },
        shot_exists=True,
    )
    assert "agent2_heuristic_complete_with_png" in a2

    a3 = check_agent3_attribution(
        {"false_success": [{"type": "LINKEDIN"}], "blank_bugs": []},
        claim_success=True,
    )
    assert any(x.startswith("agent3_false_success") for x in a3)

    return {"ok": True, "checks": ["gate_exit_code", "flash_off", "agent2", "agent3"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument(
        "--cycle-dir",
        type=Path,
        action="append",
        default=[],
        help="Agent2/3/4 attempt dir to gate (repeatable)",
    )
    ap.add_argument(
        "--fail-prefill-regressions",
        action="store_true",
        help="On SUCCESS cycle dirs, also fail if prefill_regressions non-empty",
    )
    ap.add_argument(
        "--skip-scorecard",
        action="store_true",
        help="Skip eval scorecard lane (unit + summary only)",
    )
    ap.add_argument(
        "--skip-eval-summary",
        action="store_true",
        help="Skip re-checking eval_summary.json safety",
    )
    ap.add_argument(
        "--run-eval",
        action="store_true",
        help="Opt-in: run eval_suite (needs network). Use with --eval-strict*",
    )
    ap.add_argument("--eval-limit", type=int, default=0)
    ap.add_argument("--eval-strict-safety", action="store_true")
    ap.add_argument("--eval-strict", action="store_true")
    ap.add_argument(
        "--tier1",
        action="store_true",
        help="Tier-1 lane: field_done + action_supervisor + gym + reliability_gate --skip-run",
    )
    ap.add_argument(
        "--fail-missing-eval",
        action="store_true",
        help="Fail (not skip) when skyvern_runtime/eval_results/ is missing or empty",
    )
    args = ap.parse_args()

    if args.self_test:
        result = self_test()
        print(json.dumps(result, indent=2))
        print("regression_gates self-test OK")
        return 0

    if args.tier1:
        c, o = gate_tier1()
        print(o)
        honesty_path = HERE / "tier1_gate_honesty.json"
        gym_p = live_p = None
        if honesty_path.is_file():
            try:
                h = json.loads(honesty_path.read_text(encoding="utf-8"))
                gym_p, live_p = h.get("gym_pass"), h.get("live_pass")
            except Exception:
                pass
        if c == 0:
            print(
                f"\n=== regression_gates --tier1: exit 0 "
                f"(gym_pass={gym_p} live_pass={live_p}) — NOT live signoff ==="
            )
        else:
            print(
                f"\n=== regression_gates --tier1: FAIL exit={c} "
                f"(gym_pass={gym_p} live_pass={live_p}) ==="
            )
        print("See GYM_VS_LIVE.md — headed reliability_gate + flight_recorder for live.")
        return c

    logs: list[str] = []
    worst = 0

    c, o = gate_unit_honesty()
    logs.append(o)
    worst = max(worst, c)

    if not args.skip_scorecard:
        c, o = gate_scorecard_eval(fail_if_missing=True)
        logs.append(o)
        worst = max(worst, 1 if c else 0)

    if not args.skip_eval_summary:
        c, o = gate_eval_summary_safety()
        logs.append(o)
        worst = max(worst, c)

    for d in args.cycle_dir or []:
        c, o = gate_cycle_dir(
            d, fail_prefill_regressions=bool(args.fail_prefill_regressions)
        )
        logs.append(o)
        worst = max(worst, c)

    if args.run_eval:
        cmd = [str(PY), str(HERE / "eval_suite.py")]
        if args.eval_limit:
            cmd.extend(["--limit", str(args.eval_limit)])
        if args.eval_strict:
            cmd.append("--strict")
        elif args.eval_strict_safety:
            cmd.append("--strict-safety")
        c, o = _run(cmd, label="eval_suite")
        logs.append(o)
        worst = max(worst, c)

    print("\n".join(logs))
    if worst == 0:
        print("\n=== regression_gates: PASS ===")
    else:
        print(f"\n=== regression_gates: FAIL exit={worst} ===")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
