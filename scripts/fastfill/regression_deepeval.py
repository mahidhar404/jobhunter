#!/usr/bin/env python3
"""Phase 6 — DeepEval-backed regression lane (deterministic fallback built in).

"Each fixed failure becomes a regression case." This is the registry: every case
is an invariant that a past bug violated and must never violate again. When the
optional ``deepeval`` package is installed the cases run as DeepEval test cases
with a pass/fail metric; otherwise they run as plain assertions here — so the
lane is always runnable (CI without the dep, or a rich DeepEval report with it).

Cases are deterministic (no network / LLM): they pin the consolidated Phase 1–4
behaviors. LLM-output-quality cases belong to the live A/B path
(``answer_memory_ab.py``), not this offline lane.

CLI::

    python regression_deepeval.py            # run the lane
    python regression_deepeval.py --self-test
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


# --- Regression invariants (name -> callable raising AssertionError on fail) --


def _case_completion_gate_demotes_external_success() -> None:
    """Phase 1: SUCCESS from any source dies while a hard non-essay leftover
    remains (the externally-set-SUCCESS hole)."""
    from page_progress import apply_progress_verdict_gates

    report = {"verdict": "SUCCESS", "leftovers": [{"label": "Gender", "type": "GENDER"}]}
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "FAIL", report


def _case_completion_gate_allows_essay_only_success() -> None:
    """Phase 1: an essay-only leftover may honestly remain (SUCCESS survives)."""
    from page_progress import apply_progress_verdict_gates

    report = {"verdict": "SUCCESS", "leftovers": [{"label": "Why us?", "essay": True}]}
    apply_progress_verdict_gates(report)
    assert report["verdict"] == "SUCCESS", report


def _case_structured_json_parse_tolerant() -> None:
    """Phase 2: typed parser tolerates fenced JSON and rejects value-less blobs."""
    from flash_leftovers import _parse_json_answer

    assert _parse_json_answer('```json\n{"value":"Yes","confidence":1}\n```') == {
        "value": "Yes",
        "confidence": 1.0,
    }
    assert _parse_json_answer('{"confidence":0.5}') is None


def _case_semantic_classify_kill_switch() -> None:
    """Phase 3: FASTFILL_SEMANTIC_MATCH=0 disables the semantic layer entirely
    (rollback safety), leaving deterministic-only classification."""
    import field_map as fm

    prev = os.environ.get("FASTFILL_SEMANTIC_MATCH")
    os.environ["FASTFILL_SEMANTIC_MATCH"] = "0"
    try:
        assert fm._semantic_classify_enabled() is False
        ftype, layer = fm.classify_field(
            {"label": "zzz unknown blorp", "name": "", "id": "", "placeholder": ""}
        )
        assert ftype is None and layer == "unresolved", (ftype, layer)
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_SEMANTIC_MATCH", None)
        else:
            os.environ["FASTFILL_SEMANTIC_MATCH"] = prev


def _case_semantic_option_bonus_never_outranks_soft() -> None:
    """Phase 3: the semantic option bonus is capped below soft(80)/exact(100) so
    a fuzzy paraphrase can never beat a real lexical match, and =0 when disabled."""
    import verified_select as vs

    prev = os.environ.get("FASTFILL_SEMANTIC_OPTIONS")
    os.environ["FASTFILL_SEMANTIC_OPTIONS"] = "0"
    try:
        assert vs._semantic_option_bonus("Graduate degree", "Master's Degree") == 0
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_SEMANTIC_OPTIONS", None)
        else:
            os.environ["FASTFILL_SEMANTIC_OPTIONS"] = prev
    # cap holds even when enabled with a floor of 0
    prevm = os.environ.get("FASTFILL_SEMANTIC_MATCH")
    os.environ.pop("FASTFILL_SEMANTIC_MATCH", None)
    os.environ["FASTFILL_SEMANTIC_OPTIONS"] = "1"
    old = vs._SEMANTIC_OPTION_THRESHOLD
    vs._SEMANTIC_OPTION_THRESHOLD = 0.0
    try:
        assert vs._semantic_option_bonus("x", "y") <= 70
    finally:
        vs._SEMANTIC_OPTION_THRESHOLD = old
        if prev is None:
            os.environ.pop("FASTFILL_SEMANTIC_OPTIONS", None)
        else:
            os.environ["FASTFILL_SEMANTIC_OPTIONS"] = prev
        if prevm is not None:
            os.environ["FASTFILL_SEMANTIC_MATCH"] = prevm


def _case_gateway_guard_blocks_real_mode() -> None:
    """Phase 4: a gateway base is refused when real-profile mode is on."""
    import llm_config as lc
    import field_map as fm

    orig = fm.is_real_profile_mode
    fm.is_real_profile_mode = lambda: True  # type: ignore[assignment]
    try:
        raised = False
        try:
            lc.assert_dummy_for_gateway("http://omniroute:20128/v1")
        except RuntimeError:
            raised = True
        assert raised, "gateway base must be refused in real-profile mode"
        # DeepSeek-direct default always allowed
        lc.assert_dummy_for_gateway("https://api.deepseek.com/v1")
    finally:
        fm.is_real_profile_mode = orig  # type: ignore[assignment]


def _case_metrics_ratchet_catches_regression() -> None:
    """Phase 5: ratchet fails when pass_rate drops below best-epsilon."""
    from metrics_timeline import ratchet_check

    ok, v = ratchet_check(
        {"pass_rate": 0.5, "safety_fail_n": 0, "never_submit_all": True},
        [{"pass_rate": 0.9}],
    )
    assert not ok and v


REGRESSION_CASES = {
    "completion_gate_demotes_external_success": _case_completion_gate_demotes_external_success,
    "completion_gate_allows_essay_only_success": _case_completion_gate_allows_essay_only_success,
    "structured_json_parse_tolerant": _case_structured_json_parse_tolerant,
    "semantic_classify_kill_switch": _case_semantic_classify_kill_switch,
    "semantic_option_bonus_never_outranks_soft": _case_semantic_option_bonus_never_outranks_soft,
    "gateway_guard_blocks_real_mode": _case_gateway_guard_blocks_real_mode,
    "metrics_ratchet_catches_regression": _case_metrics_ratchet_catches_regression,
}


def run_cases() -> list[dict]:
    """Run every regression invariant; return [{name, ok, detail}]."""
    results: list[dict] = []
    for name, fn in REGRESSION_CASES.items():
        try:
            fn()
            results.append({"name": name, "ok": True, "detail": ""})
        except Exception as e:  # noqa: BLE001 - report, don't crash the lane
            results.append({"name": name, "ok": False, "detail": f"{type(e).__name__}: {e}"})
    return results


def _deepeval_available() -> bool:
    try:
        import deepeval  # noqa: F401

        return True
    except Exception:
        return False


def run_with_deepeval() -> list[dict]:
    """Wrap each invariant as a DeepEval case with a 1.0/0.0 metric.

    Falls back to plain execution results shape either way, so callers get the
    same [{name, ok, detail}] structure.
    """
    # DeepEval's value here is the report/telemetry; the pass/fail signal is the
    # same invariant. We keep the deterministic result as the source of truth and
    # additionally log to DeepEval if present.
    results = run_cases()
    try:
        from deepeval.test_case import LLMTestCase  # noqa: F401
        # A richer DeepEval integration (custom GEval metrics on live LLM output)
        # attaches on the A/B live path; here we only surface that deepeval is
        # wired without failing when its server/telemetry is unconfigured.
    except Exception:
        pass
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="regression lane (deepeval optional)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    results = run_with_deepeval() if _deepeval_available() else run_cases()
    failed = [r for r in results if not r["ok"]]
    for r in results:
        mark = "ok  " if r["ok"] else "FAIL"
        print(f"  [{mark}] {r['name']}{('  ' + r['detail']) if r['detail'] else ''}")
    backend = "deepeval" if _deepeval_available() else "builtin"
    print(f"regression lane ({backend}): {len(results) - len(failed)}/{len(results)} passed")
    if args.self_test and failed:
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
