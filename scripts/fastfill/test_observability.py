#!/usr/bin/env python3
"""Phase 5 tests: metrics timeline reducer + ratchet, and PII-masked tracing.

No network. Verifies:
  - build_row reduces an eval_summary to counts + per-ATS pass rates (no PII).
  - append_row + load_timeline round-trip.
  - ratchet_check fails on safety fails and on pass_rate below best-epsilon.
  - mask_pii redacts emails / phones / SSNs (incl. nested).
  - trace_llm writes a PII-masked local JSONL mirror (and optional Langfuse);
    Langfuse absence never raises.

DUMMY / synthetic fixtures only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import metrics_timeline as mt  # noqa: E402
import tracing  # noqa: E402


_SUMMARY = {
    "experiment": "fast_fill_eval_suite",
    "n": 4,
    "passed": 3,
    "failed": 1,
    "elapsed_seconds": 9.9,
    "slo_rollup": {
        "safety_fail_n": 0,
        "quality_fail_n": 1,
        "fail_reasons": {"coverage": 1},
        "by_platform": {
            "greenhouse": {"n": 2, "passed": 2, "blocked": 0, "flash_called": 1},
            "lever": {"n": 2, "passed": 1, "blocked": 0, "flash_called": 0},
        },
        "safety": {"never_submit_all": True},
    },
}


def test_build_row_counts_and_rates():
    row = mt.build_row(_SUMMARY, ts=1000.0, label="unit")
    assert row["pass_rate"] == 0.75
    assert row["by_platform"]["greenhouse"]["pass_rate"] == 1.0
    assert row["by_platform"]["lever"]["pass_rate"] == 0.5
    assert row["safety_fail_n"] == 0
    assert row["label"] == "unit"
    # no PII fields leak in
    assert set(row["fail_reasons"]) == {"coverage"}


def test_append_and_load_roundtrip(tmp_path):
    summary_path = tmp_path / "eval_summary.json"
    summary_path.write_text(json.dumps(_SUMMARY))
    tl = tmp_path / "metrics_timeline.jsonl"
    row = mt.append_row(summary_path, timeline_path=tl, label="x")
    rows = mt.load_timeline(tl)
    assert len(rows) == 1
    assert rows[0]["pass_rate"] == row["pass_rate"] == 0.75


def test_ratchet_passes_when_steady():
    row = mt.build_row(_SUMMARY, ts=1.0)
    ok, v = mt.ratchet_check(row, [{"pass_rate": 0.72}])
    assert ok, v


def test_ratchet_fails_on_pass_rate_drop():
    row = {"pass_rate": 0.5, "safety_fail_n": 0, "never_submit_all": True}
    ok, v = mt.ratchet_check(row, [{"pass_rate": 0.9}])
    assert not ok
    assert any("pass_rate" in x for x in v)


def test_ratchet_fails_on_safety():
    row = {"pass_rate": 0.99, "safety_fail_n": 2, "never_submit_all": True}
    ok, v = mt.ratchet_check(row, [])
    assert not ok
    assert any("safety_fail_n" in x for x in v)


def test_self_test_runs():
    assert mt._self_test() == 0


# --- tracing -------------------------------------------------------------


def test_mask_pii_scalar():
    out = tracing.mask_pii("mail a@b.com call 405-555-0100 ssn 123-45-6789")
    assert "a@b.com" not in out and "{{EMAIL}}" in out
    assert "{{PHONE}}" in out
    assert "{{SSN}}" in out


def test_mask_pii_nested():
    out = tracing.mask_pii({"x": ["reach a@b.com"], "y": 3})
    assert out["x"][0] == "reach {{EMAIL}}"
    assert out["y"] == 3


def test_trace_noop_when_disabled(monkeypatch, tmp_path):
    # Tracing is default-ON now; FASTFILL_TRACE=0 turns it off.
    monkeypatch.setenv("FASTFILL_TRACE", "0")
    tp = tmp_path / "t.jsonl"
    assert tracing.trace_llm("x", prompt="a@b.com", traces_path=tp) is None
    assert not tp.exists()


def test_trace_on_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("FASTFILL_TRACE", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    tp = tmp_path / "t.jsonl"
    row = tracing.trace_llm("x", prompt="a@b.com", traces_path=tp)
    assert row is not None and tp.exists()
    assert "{{EMAIL}}" in row["prompt"]


def test_trace_writes_masked_jsonl_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("FASTFILL_TRACE", "1")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    tp = tmp_path / "t.jsonl"
    row = tracing.trace_llm(
        "leftover_llm", prompt="email me a@b.com", response="ok 405-555-0100", model="m", traces_path=tp
    )
    assert row is not None
    written = json.loads(tp.read_text().splitlines()[0])
    assert "a@b.com" not in json.dumps(written)
    assert "{{EMAIL}}" in written["prompt"]
    assert "{{PHONE}}" in written["response"]
