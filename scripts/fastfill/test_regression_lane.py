#!/usr/bin/env python3
"""Phase 6 tests: A/B harness verdict logic + regression lane invariants.

No network. DUMMY / synthetic fixtures only.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import answer_memory_ab as ab  # noqa: E402
import regression_deepeval as rl  # noqa: E402


def _summary(passed, n=4, safety=0, fail_reasons=None):
    return {
        "n": n,
        "passed": passed,
        "slo_rollup": {
            "safety_fail_n": safety,
            "fail_reasons": fail_reasons or {},
            "safety": {"never_submit_all": True},
        },
    }


def test_ab_promotes_when_treatment_better():
    v = ab.compare(_summary(2), _summary(3))
    assert v["promote"] is True
    assert v["delta"] == 0.25


def test_ab_blocks_on_pass_rate_regression():
    v = ab.compare(_summary(3), _summary(1, fail_reasons={"coverage": 1}))
    assert v["promote"] is False
    assert any("pass_rate" in r for r in v["reasons"])


def test_ab_blocks_on_new_fail_reason_even_if_equal_rate():
    v = ab.compare(_summary(2), _summary(2, fail_reasons={"new_thing": 1}))
    assert v["promote"] is False
    assert any("new fail reasons" in r for r in v["reasons"])


def test_ab_blocks_on_safety_regression():
    v = ab.compare(_summary(2), _summary(4, safety=1))
    assert v["promote"] is False


def test_ab_run_ab_toggles_env():
    seen = []

    def runner(env):
        seen.append(env)
        return _summary(3) if env[ab.MEMORY_ENV_VARS[0]] == "1" else _summary(2)

    v = ab.run_ab(runner)
    assert seen[0][ab.MEMORY_ENV_VARS[0]] == "0"  # baseline first
    assert seen[1][ab.MEMORY_ENV_VARS[0]] == "1"  # then treatment
    assert v["promote"] is True


def test_ab_self_test():
    assert ab._self_test() == 0


def test_regression_lane_all_pass():
    results = rl.run_cases()
    failed = [r for r in results if not r["ok"]]
    assert not failed, failed
    assert len(results) == len(rl.REGRESSION_CASES)
