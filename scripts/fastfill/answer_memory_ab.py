#!/usr/bin/env python3
"""Phase 6 — answer-memory A/B harness (evidence gate for Phase 3b).

The semantic answer-memory (continuous_learn.similar_leftover_answers behind
FASTFILL_SEMANTIC_MEMORY / FASTFILL_ANSWER_MEMORY) ships default-OFF. This
harness is the evidence that decides whether to turn it on: run the SAME
live-dummy eval twice — memory OFF (baseline) then ON (treatment) — reduce both
to metrics rows, and promote memory ONLY if it beats baseline pass rate with no
safety regression and no NEW fail reasons. Measurement only; it never flips the
flag itself.

Design is injectable so it is testable without a browser: ``run_ab`` takes a
``runner(env) -> summary_dict`` callable. The CLI's default runner shells out to
``eval_suite`` with the flag toggled and reads the resulting eval_summary.json.

CLI::

    python answer_memory_ab.py --suite suites/quick.json --out out_ab
    python answer_memory_ab.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from metrics_timeline import build_row  # noqa: E402

# The two env vars that enable the semantic answer memory (either turns it on).
MEMORY_ENV_VARS = ("FASTFILL_SEMANTIC_MEMORY", "FASTFILL_ANSWER_MEMORY")


def compare(baseline: dict, treatment: dict) -> dict:
    """Decide whether the treatment (memory ON) should be promoted.

    Promote iff: treatment pass_rate **strictly greater** than baseline,
    treatment has zero safety fails and never_submit_all holds, and it introduces
    no fail reason absent from baseline.
    """
    b = build_row(baseline, label="baseline")
    t = build_row(treatment, label="treatment")
    reasons: list[str] = []

    b_rate = b.get("pass_rate") or 0.0
    t_rate = t.get("pass_rate") or 0.0
    if t_rate <= b_rate:
        reasons.append(f"pass_rate did not beat baseline {t_rate:.4f}<={b_rate:.4f}")

    if int(t.get("safety_fail_n") or 0) > 0:
        reasons.append(f"treatment safety_fail_n={t['safety_fail_n']}")
    if not t.get("never_submit_all", True):
        reasons.append("treatment never_submit_all=false")

    new_reasons = set(t.get("fail_reasons") or {}) - set(b.get("fail_reasons") or {})
    if new_reasons:
        reasons.append("new fail reasons: " + ",".join(sorted(new_reasons)))

    return {
        "promote": not reasons,
        "reasons": reasons,
        "baseline_pass_rate": b_rate,
        "treatment_pass_rate": t_rate,
        "delta": round(t_rate - b_rate, 4),
        "baseline": b,
        "treatment": t,
    }


def run_ab(runner: Callable[[dict[str, str]], dict]) -> dict:
    """Run baseline (memory off) then treatment (memory on) via ``runner``.

    ``runner`` receives an env-overrides dict and returns an eval_summary dict.
    """
    off_env = {v: "0" for v in MEMORY_ENV_VARS}
    on_env = {v: "1" for v in MEMORY_ENV_VARS}
    baseline = runner(off_env)
    treatment = runner(on_env)
    verdict = compare(baseline, treatment)
    verdict["ts"] = round(time.time(), 3)
    return verdict


def _default_runner(suite: str, out_dir: Path) -> Callable[[dict[str, str]], dict]:
    """Runner that shells eval_suite with env overrides and reads its summary."""
    def _run(env_overrides: dict[str, str]) -> dict:
        env = dict(os.environ)
        env.update(env_overrides)
        tag = "on" if env_overrides.get(MEMORY_ENV_VARS[0]) == "1" else "off"
        run_out = out_dir / f"mem_{tag}"
        run_out.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(HERE / "eval_suite.py"),
            "--out-dir",
            str(run_out),
            "--strict",
        ]
        # Optional platform filter encoded in suite path name is ignored; eval_suite
        # always loads eval_urls.json. Keep suite arg for logging only.
        _ = suite
        subprocess.run(cmd, env=env, check=False)
        summary_path = run_out / "eval_summary.json"
        return json.loads(summary_path.read_text(encoding="utf-8"))

    return _run


def _self_test() -> int:
    baseline = {"n": 4, "passed": 2, "slo_rollup": {"safety_fail_n": 0, "fail_reasons": {}, "safety": {"never_submit_all": True}}}
    better = {"n": 4, "passed": 3, "slo_rollup": {"safety_fail_n": 0, "fail_reasons": {}, "safety": {"never_submit_all": True}}}
    worse = {"n": 4, "passed": 1, "slo_rollup": {"safety_fail_n": 0, "fail_reasons": {"coverage": 1}, "safety": {"never_submit_all": True}}}
    unsafe = {"n": 4, "passed": 4, "slo_rollup": {"safety_fail_n": 1, "fail_reasons": {}, "safety": {"never_submit_all": True}}}

    assert compare(baseline, better)["promote"] is True
    v = compare(baseline, worse)
    assert v["promote"] is False and v["reasons"]
    assert compare(baseline, unsafe)["promote"] is False
    # Tied pass_rate must NOT promote (strict beat required)
    tied = {"n": 4, "passed": 2, "slo_rollup": {"safety_fail_n": 0, "fail_reasons": {}, "safety": {"never_submit_all": True}}}
    assert compare(baseline, tied)["promote"] is False

    # run_ab wiring with an injected runner
    seq = {"0": worse, "1": better}
    v = run_ab(lambda env: seq[env[MEMORY_ENV_VARS[0]]])
    assert v["promote"] is True  # treatment(better) vs baseline(worse)
    print("answer_memory_ab self-test OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="answer-memory A/B (memory off vs on)")
    ap.add_argument("--suite", help="eval suite json path (live run)")
    ap.add_argument("--out", default="out_ab", help="output dir")
    ap.add_argument("--baseline", help="offline: memory-off eval_summary.json")
    ap.add_argument("--treatment", help="offline: memory-on eval_summary.json")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()

    # Offline (browser-free) mode: compare two pre-recorded eval summaries. This
    # is the Phase 7 offline fixture harness — no live fill needed.
    if args.baseline and args.treatment:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        treatment = json.loads(Path(args.treatment).read_text(encoding="utf-8"))
        verdict = compare(baseline, treatment)
        print(
            f"offline A/B: baseline={verdict['baseline_pass_rate']} "
            f"treatment={verdict['treatment_pass_rate']} delta={verdict['delta']} "
            f"-> promote={verdict['promote']}"
        )
        if verdict["reasons"]:
            print("  reasons: " + "; ".join(verdict["reasons"]))
        return 0 if verdict["promote"] else 3

    if not args.suite:
        ap.error("--suite (live) or --baseline/--treatment (offline) required")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    verdict = run_ab(_default_runner(args.suite, out_dir))
    (out_dir / "ab_verdict.json").write_text(json.dumps(verdict, indent=2))
    print(
        f"A/B: baseline={verdict['baseline_pass_rate']} "
        f"treatment={verdict['treatment_pass_rate']} delta={verdict['delta']} "
        f"-> promote={verdict['promote']}"
    )
    if verdict["reasons"]:
        print("  reasons: " + "; ".join(verdict["reasons"]))
    return 0 if verdict["promote"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
