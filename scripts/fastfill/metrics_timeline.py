#!/usr/bin/env python3
"""Phase 5 — append-only metrics timeline built from eval_summary.json.

Each eval run reduces its ``eval_summary.json`` (produced by ``eval_suite.py``)
to one compact row appended to ``learning_store/metrics_timeline.jsonl``. Rows
carry overall pass rate, per-ATS pass rates, safety counters and fail-reason
histogram — the trend the Ops dashboard charts and the ratchet gate defends.

Ratchet floors turn the timeline into a regression gate: a new row must not drop
pass_rate below the running best (minus a small epsilon) and must keep safety
fails at zero. Additive + dummy-only; carries no PII (only counts and platform /
field-type keys).

CLI::

    python metrics_timeline.py --from out/eval_summary.json [--label nightly]
    python metrics_timeline.py --check           # ratchet vs history
    python metrics_timeline.py --self-test
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STORE_DIR = HERE / "learning_store"
TIMELINE_PATH = STORE_DIR / "metrics_timeline.jsonl"
SELECTOR_STATS_PATH = STORE_DIR / "selector_stats.json"

# Ratchet: how far below the historical best pass_rate a new run may fall before
# it is a regression. Small epsilon absorbs single-case noise on tiny suites.
RATCHET_EPSILON = 0.02


def _field_type_stats() -> dict[str, Any]:
    """Best-effort per-field-type verified/attempt counts from selector_stats."""
    try:
        data = json.loads(SELECTOR_STATS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, Any] = {}
    types = data.get("by_type") if isinstance(data, dict) else None
    if isinstance(types, dict):
        for ftype, rec in types.items():
            if not isinstance(rec, dict):
                continue
            ok = int(rec.get("verified") or rec.get("ok") or 0)
            n = int(rec.get("attempts") or rec.get("n") or 0)
            out[str(ftype)] = {
                "n": n,
                "verified": ok,
                "rate": round(ok / n, 4) if n else None,
            }
    return out


def build_row(summary: dict, *, ts: float | None = None, label: str = "eval_suite") -> dict:
    """Reduce an eval_summary dict to one timeline row (counts only, no PII)."""
    ts = time.time() if ts is None else ts
    rollup = summary.get("slo_rollup") or {}
    by_platform_raw = rollup.get("by_platform") or {}
    by_platform: dict[str, Any] = {}
    for plat, b in by_platform_raw.items():
        if not isinstance(b, dict):
            continue
        n = int(b.get("n") or 0)
        passed = int(b.get("passed") or 0)
        by_platform[str(plat)] = {
            "n": n,
            "passed": passed,
            "pass_rate": round(passed / n, 4) if n else None,
            "blocked": int(b.get("blocked") or 0),
            "flash_called": int(b.get("flash_called") or 0),
        }
    n = int(summary.get("n") or 0)
    passed = int(summary.get("passed") or 0)
    safety = rollup.get("safety") or {}
    return {
        "ts": round(ts, 3),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)),
        "label": label,
        "experiment": summary.get("experiment"),
        "n": n,
        "passed": passed,
        "failed": int(summary.get("failed") or 0),
        "pass_rate": round(passed / n, 4) if n else None,
        "elapsed_seconds": summary.get("elapsed_seconds"),
        "never_submit_all": bool(safety.get("never_submit_all", True)),
        "safety_fail_n": int(rollup.get("safety_fail_n") or 0),
        "quality_fail_n": int(rollup.get("quality_fail_n") or 0),
        "fail_reasons": dict(rollup.get("fail_reasons") or {}),
        "by_platform": by_platform,
        "by_field_type": _field_type_stats(),
    }


def append_row(
    summary_path: Path | str,
    *,
    timeline_path: Path | str | None = None,
    label: str = "eval_suite",
) -> dict:
    """Read eval_summary.json, append one reduced row, return the row."""
    summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    row = build_row(summary, label=label)
    tl = Path(timeline_path) if timeline_path else TIMELINE_PATH
    tl.parent.mkdir(parents=True, exist_ok=True)
    with tl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def load_timeline(timeline_path: Path | str | None = None) -> list[dict]:
    tl = Path(timeline_path) if timeline_path else TIMELINE_PATH
    if not tl.is_file():
        return []
    rows: list[dict] = []
    for line in tl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def ratchet_check(
    row: dict, history: list[dict], *, epsilon: float = RATCHET_EPSILON
) -> tuple[bool, list[str]]:
    """(ok, violations). Fails on safety fails or pass_rate below best-epsilon."""
    violations: list[str] = []
    if int(row.get("safety_fail_n") or 0) > 0:
        violations.append(f"safety_fail_n={row.get('safety_fail_n')}")
    if not row.get("never_submit_all", True):
        violations.append("never_submit_all=false")
    prior_rates = [
        r.get("pass_rate")
        for r in history
        if isinstance(r.get("pass_rate"), (int, float))
    ]
    cur = row.get("pass_rate")
    if isinstance(cur, (int, float)) and prior_rates:
        floor = max(prior_rates) - epsilon
        if cur < floor:
            violations.append(f"pass_rate={cur:.4f}<floor={floor:.4f}")
    return (not violations), violations


def _self_test() -> int:
    summary = {
        "experiment": "fast_fill_eval_suite",
        "n": 4,
        "passed": 3,
        "failed": 1,
        "elapsed_seconds": 12.3,
        "slo_rollup": {
            "safety_fail_n": 0,
            "quality_fail_n": 1,
            "fail_reasons": {"coverage": 1},
            "by_platform": {"greenhouse": {"n": 2, "passed": 2}, "lever": {"n": 2, "passed": 1}},
            "safety": {"never_submit_all": True},
        },
    }
    row = build_row(summary, ts=1000.0)
    assert row["pass_rate"] == 0.75, row
    assert row["by_platform"]["greenhouse"]["pass_rate"] == 1.0
    ok, v = ratchet_check(row, [{"pass_rate": 0.7}])
    assert ok, v
    ok, v = ratchet_check({"pass_rate": 0.5, "safety_fail_n": 0, "never_submit_all": True}, [{"pass_rate": 0.9}])
    assert not ok and v, v
    ok, v = ratchet_check({"pass_rate": 0.99, "safety_fail_n": 1, "never_submit_all": True}, [])
    assert not ok, v
    print("metrics_timeline self-test OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="metrics timeline reducer + ratchet")
    ap.add_argument("--from", dest="src", help="path to eval_summary.json")
    ap.add_argument("--timeline", help="override timeline jsonl path")
    ap.add_argument("--label", default="eval_suite")
    ap.add_argument("--check", action="store_true", help="ratchet latest vs history")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    if args.src:
        row = append_row(args.src, timeline_path=args.timeline, label=args.label)
        print(f"appended timeline row: pass_rate={row['pass_rate']} n={row['n']}")

    if args.check:
        rows = load_timeline(args.timeline)
        if not rows:
            print("no timeline rows to check")
            return 0
        ok, violations = ratchet_check(rows[-1], rows[:-1])
        if ok:
            print(f"ratchet OK (pass_rate={rows[-1].get('pass_rate')})")
            return 0
        print("RATCHET REGRESSION: " + "; ".join(violations))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
