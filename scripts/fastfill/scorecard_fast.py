#!/usr/bin/env python3
"""Coverage / latency scorecard for fast-fill artifacts (dummy-only runs).

Reads ``skyvern_runtime/real_job_results/fast_fill_*.json`` and ``exp_*.json``,
plus ``skyvern_runtime/eval_results/eval_*.json`` when pointed there.
Asserts ``never_submit`` + honest ADVANCE on every report, and prints a
coverage/latency table including a ``flash_called`` column.

Usage:
  skyvern_runtime/venv/bin/python scripts/fastfill/scorecard_fast.py
  skyvern_runtime/venv/bin/python scripts/fastfill/scorecard_fast.py --dir skyvern_runtime/eval_results
  skyvern_runtime/venv/bin/python scripts/fastfill/scorecard_fast.py --eval --gate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_DIR = ROOT / "skyvern_runtime" / "real_job_results"
EVAL_DIR = ROOT / "skyvern_runtime" / "eval_results"


def assert_never_submit(report: dict, *, path: Path | None = None) -> None:
    """Hard safety: every fast-fill / exp / eval report must declare never_submit."""
    where = f" in {path}" if path else ""
    if report.get("never_submit") is not True:
        raise AssertionError(f"never_submit must be True{where}; got {report.get('never_submit')!r}")
    if report.get("submit_clicked") is True:
        raise AssertionError(f"submit_clicked must not be True{where}")
    # Nested rows (matrix / multi-url experiments / eval summary)
    for row in report.get("rows") or []:
        if not isinstance(row, dict):
            continue
        if row.get("submit_clicked") is True:
            raise AssertionError(f"row submit_clicked True{where}: {row.get('platform') or row.get('url')}")
        if (row.get("final_clicks") or 0) != 0:
            raise AssertionError(f"row final_clicks != 0{where}: {row}")
        if row.get("never_submit") is False:
            raise AssertionError(f"row never_submit False{where}: {row.get('platform') or row.get('url')}")
    for row in report.get("results") or []:
        if not isinstance(row, dict):
            continue
        if row.get("never_submit") is False:
            raise AssertionError(f"nested never_submit False{where}")
        if row.get("submit_clicked") is True:
            raise AssertionError(f"nested submit_clicked True{where}")
        if (row.get("final_clicks") or 0) != 0:
            raise AssertionError(f"nested final_clicks != 0{where}")


def assert_honest_advance(report: dict, *, path: Path | None = None) -> None:
    """Fail the scorecard when a run claims success after incomplete ADVANCE.

    Historical FAIL artifacts with validation banners are allowed (they are
    evidence of the bug). SUCCESS + validation/incomplete is never allowed.
    """
    where = f" in {path}" if path else ""
    if report.get("advanced_incomplete") is True and report.get("verdict") == "SUCCESS":
        raise AssertionError(f"SUCCESS with advanced_incomplete{where}")
    if report.get("validation_after_advance") and report.get("verdict") == "SUCCESS":
        raise AssertionError(f"SUCCESS with validation_after_advance{where}")
    wd = report.get("workday") if isinstance(report.get("workday"), dict) else {}
    if wd.get("advanced_incomplete") is True and (
        report.get("verdict") == "SUCCESS" or wd.get("verdict") == "SUCCESS"
    ):
        raise AssertionError(f"SUCCESS with workday.advanced_incomplete{where}")
    if wd.get("validation_after_advance") and (
        report.get("verdict") == "SUCCESS" or wd.get("verdict") == "SUCCESS"
    ):
        raise AssertionError(f"SUCCESS with workday.validation_after_advance{where}")
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    if (
        metrics.get("advanced_incomplete") is True
        and metrics.get("verdict") == "SUCCESS"
    ):
        raise AssertionError(f"metrics SUCCESS with advanced_incomplete{where}")
    if metrics.get("validation_errors") is True and metrics.get("verdict") == "SUCCESS":
        raise AssertionError(f"metrics SUCCESS with validation_errors{where}")
    if (
        metrics.get("validation_errors") is True
        and report.get("advanced")
        and metrics.get("verdict") == "SUCCESS"
    ):
        raise AssertionError(f"advanced SUCCESS with validation_errors{where}")
    # Eval summary nested rows: SUCCESS + dishonest advance flags
    for row in report.get("rows") or []:
        if not isinstance(row, dict):
            continue
        if row.get("verdict") != "SUCCESS":
            continue
        if row.get("advanced_incomplete") is True:
            raise AssertionError(
                f"eval row SUCCESS with advanced_incomplete{where}: {row.get('platform')}"
            )
        if row.get("validation_after_advance"):
            raise AssertionError(
                f"eval row SUCCESS with validation_after_advance{where}: {row.get('platform')}"
            )


def assert_honest_filled(report: dict, *, path: Path | None = None) -> None:
    """Reject inflated fill metrics: status=stuck meaning filled on SUCCESS.

    Historical FAIL/SUCCESS artifacts may still copy verified rows into ``stuck[]``
    with status=filled — scorecard recounts verified rows and ignores that list.
    A SUCCESS row whose own ``status`` field is literally ``stuck`` is never allowed.
    """
    where = f" in {path}" if path else ""
    stuck_as_fill = []
    for key in ("filled", "stuck"):
        for row in report.get(key) or []:
            if isinstance(row, dict) and row.get("status") == "stuck":
                stuck_as_fill.append(row.get("automation_id") or row.get("type") or key)
    if stuck_as_fill and report.get("verdict") == "SUCCESS":
        raise AssertionError(
            f"SUCCESS labels fills as status=stuck{where}: {stuck_as_fill[:5]}"
        )


def assert_flash_off_when_unrequested(report: dict, *, path: Path | None = None) -> None:
    """Fail when Flash ran without an explicit leftovers request.

    Eval suite and default fast_fill keep Flash OFF. Opt-in runs set
    ``flash_leftovers_requested`` (or nest ``flash.requested``).
    """
    where = f" in {path}" if path else ""
    requested = bool(
        report.get("flash_leftovers_requested")
        or report.get("flash_leftovers")
        or (isinstance(report.get("flash"), dict) and report["flash"].get("requested"))
    )
    called = _flash_called(report)
    if called and not requested:
        raise AssertionError(f"flash_called while Flash not requested{where}")
    # Nested eval / matrix rows
    for row in list(report.get("rows") or []) + list(report.get("results") or []):
        if not isinstance(row, dict):
            continue
        row_req = bool(
            row.get("flash_leftovers_requested")
            or row.get("flash_leftovers")
            or requested  # parent requested covers suite summary
        )
        row_called = bool(row.get("flash_called"))
        flash = row.get("flash") if isinstance(row.get("flash"), dict) else {}
        if flash.get("invoked"):
            row_called = True
        if row_called and not row_req and report.get("experiment") == "fast_fill_eval_suite":
            raise AssertionError(
                f"eval row flash_called while suite Flash OFF{where}: "
                f"{row.get('platform') or row.get('url')}"
            )


def _row_verified(row: dict) -> bool:
    """Scorecard recount — same SSoT as ``field_done`` / ``is_verified_fill_row``."""
    from fill_verify import is_verified_fill_row

    return is_verified_fill_row(row)


def _verified_filled_count(report: dict) -> int | None:
    """Prefer recounting verified rows over trusting inflated filled_count."""
    rows = report.get("filled")
    if isinstance(rows, list) and rows:
        return sum(1 for r in rows if _row_verified(r))
    # Workday contact pack list
    pack = report.get("contact_pack")
    if isinstance(pack, list) and pack:
        return sum(1 for r in pack if _row_verified(r))
    if isinstance(pack, dict) and isinstance(pack.get("filled"), list):
        return sum(1 for r in pack["filled"] if _row_verified(r))
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    if metrics.get("contact_filled_verified") is not None:
        return int(metrics["contact_filled_verified"])
    if report.get("filled_count") is not None:
        # No row detail — keep count but only if not clearly stuck-aliased alone
        return int(report["filled_count"])
    return None


def _coverage(report: dict) -> float | None:
    filled = _verified_filled_count(report)
    left = report.get("leftover_count")
    if left is None and isinstance(report.get("leftovers"), list):
        left = len(report["leftovers"])
    if left is None and isinstance(report.get("unresolved"), list):
        left = len(report["unresolved"])
    extracted = report.get("extracted_count") or 0
    if filled is None:
        # Fall back to stored coverage only when we cannot recount
        if report.get("coverage") is not None:
            try:
                return float(report["coverage"])
            except (TypeError, ValueError):
                return None
        return None
    denom = max(int(extracted or 0), int(filled) + int(left or 0), 1)
    return round(int(filled) / denom, 3)


def _elapsed(report: dict) -> float | None:
    """Prefer fill_elapsed_seconds (excludes hold) for SLO; else wall elapsed."""
    for key in ("fill_elapsed_seconds", "elapsed_seconds", "duration_seconds", "total_seconds"):
        if report.get(key) is not None:
            try:
                return float(report[key])
            except (TypeError, ValueError):
                pass
    timing = report.get("timing")
    if isinstance(timing, dict):
        for key in ("fill_elapsed_seconds", "elapsed_seconds", "total", "seconds"):
            if timing.get(key) is not None:
                try:
                    return float(timing[key])
                except (TypeError, ValueError):
                    pass
    return None


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def assert_flash_off_honesty(report: dict, *, path: Path | None = None) -> None:
    """Fail when Flash ran without being requested (eval / scorecard safety)."""
    where = f" in {path}" if path else ""
    requested = bool(report.get("flash_leftovers_requested"))
    if requested:
        return
    if report.get("flash_called") is True:
        raise AssertionError(f"flash_called while Flash OFF{where}")
    flash = report.get("flash") if isinstance(report.get("flash"), dict) else {}
    if flash.get("invoked") is True:
        raise AssertionError(f"flash.invoked while Flash OFF{where}")


def load_eval_slo(eval_urls: Path | None = None) -> dict:
    path = eval_urls or (HERE / "eval_urls.json")
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data.get("slo") if isinstance(data.get("slo"), dict) else {}


def check_row_slo(row: dict, slo: dict) -> list[str]:
    """Return SLO fail reasons for a scorecard row (empty = pass)."""
    fails: list[str] = []
    if not slo:
        return fails
    plat = str(row.get("label") or row.get("platform") or "").split("/")[0].lower()
    # Matrix labels are platform names; eval rows too.
    blocker = row.get("blocker")
    reachability_skip = blocker in {
        "captcha",
        "akamai",
        "cloudflare",
        "login_wall",
        "job_closed",
        "404",
        "chromium_missing",
        "navigation_failed",
    }
    if plat == "greenhouse" and not reachability_skip:
        gh = slo.get("greenhouse") or {}
        cov = row.get("coverage")
        sec = row.get("elapsed_s")
        if cov is not None and float(cov) < float(gh.get("min_coverage", 0.9)):
            fails.append(f"coverage={cov}<{gh.get('min_coverage')}")
        if sec is not None and float(sec) > float(gh.get("max_seconds", 20)):
            fails.append(f"elapsed={sec}>{gh.get('max_seconds')}")
        if row.get("flash_called") and int(gh.get("flash_tokens", 0)) == 0:
            # Suite default Flash OFF
            fails.append("flash_called_while_off")
    if plat == "workday" and not reachability_skip:
        wd = slo.get("workday_contact") or {}
        sec = row.get("elapsed_s")
        max_s = wd.get("max_seconds")
        if max_s is not None and sec is not None and float(sec) > float(max_s):
            fails.append(f"workday_elapsed={sec}>{max_s}")
    if plat == "ashby" and not reachability_skip:
        ash = slo.get("ashby") or {}
        sec = row.get("elapsed_s")
        max_s = ash.get("max_seconds")
        if max_s is not None and sec is not None and float(sec) > float(max_s):
            fails.append(f"ashby_elapsed={sec}>{max_s}")
    return fails


def _filled_count(report: dict) -> int | None:
    verified = _verified_filled_count(report)
    if verified is not None:
        return verified
    metrics = report.get("metrics")
    if isinstance(metrics, dict) and metrics.get("filled") is not None:
        return int(metrics["filled"])
    attempted = report.get("attempted")
    if isinstance(attempted, list):
        return sum(1 for a in attempted if isinstance(a, dict) and a.get("ok") and _row_verified(a))
    return None


def _leftover_count(report: dict) -> int | None:
    if report.get("leftover_count") is not None:
        return int(report["leftover_count"])
    if isinstance(report.get("leftovers"), list):
        return len(report["leftovers"])
    if report.get("unresolved_count") is not None:
        return int(report["unresolved_count"])
    if isinstance(report.get("unresolved"), list):
        return len(report["unresolved"])
    if report.get("missed_count") is not None:
        return int(report["missed_count"])
    return None


def _label(path: Path, report: dict) -> str:
    stem = path.stem
    for prefix in ("fast_fill_", "exp_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    plat = report.get("platform")
    exp = report.get("experiment")
    if plat and stem and stem != plat and stem != "coverage_matrix":
        return f"{plat}/{stem}"
    return str(plat or exp or stem)


def _kind(path: Path) -> str:
    if path.name.startswith("fast_fill_"):
        return "fast_fill"
    if path.name.startswith("exp_"):
        return "exp"
    if path.name.startswith("eval_"):
        return "eval"
    return "other"


def _flash_called(report: dict) -> bool:
    """Honest flash column: top-level flag or nested flash.invoked."""
    if report.get("flash_called") is True:
        return True
    flash = report.get("flash")
    if isinstance(flash, dict) and flash.get("invoked") is True:
        return True
    return False


def expand_rows(
    path: Path,
    report: dict,
    *,
    gate_flash_off: bool = False,
) -> list[dict[str, Any]]:
    """One scorecard row per artifact; matrix/eval summary expand to per-row."""
    assert_never_submit(report, path=path)
    assert_honest_advance(report, path=path)
    assert_honest_filled(report, path=path)
    # Flash-off: always for eval suite artifacts; for other dirs only with --gate
    # so intentional --flash-leftovers cycle runs (requested=true) still score.
    if gate_flash_off or report.get("experiment") == "fast_fill_eval_suite":
        assert_flash_off_when_unrequested(report, path=path)
    rows: list[dict[str, Any]] = []

    # Coverage matrix: prefer per-platform rows
    matrix_rows = report.get("rows")
    if report.get("experiment") == "fast_fill_coverage_matrix" and isinstance(matrix_rows, list):
        for mr in matrix_rows:
            if not isinstance(mr, dict):
                continue
            rows.append(
                {
                    "file": path.name,
                    "kind": "fast_fill",
                    "label": mr.get("platform") or "matrix_row",
                    "url": (mr.get("url") or "")[:60],
                    "filled": mr.get("filled"),
                    "leftovers": mr.get("leftovers"),
                    "coverage": mr.get("coverage"),
                    "elapsed_s": mr.get("elapsed_seconds"),
                    "blocker": mr.get("blocker"),
                    "flash_called": bool(mr.get("flash_called")),
                    "never_submit": mr.get("never_submit", True) is not False,
                    "dummy": report.get("dummy", True),
                    "pass": mr.get("pass"),
                }
            )
        return rows

    # Eval suite summary: expand to per-URL rows (do not double-count if
    # individual eval_*.json artifacts are also present — caller skips summary).
    if report.get("experiment") == "fast_fill_eval_suite" and isinstance(matrix_rows, list):
        for mr in matrix_rows:
            if not isinstance(mr, dict):
                continue
            rows.append(
                {
                    "file": path.name,
                    "kind": "eval",
                    "label": mr.get("platform") or "eval_row",
                    "url": (mr.get("url") or "")[:60],
                    "filled": mr.get("filled") or mr.get("filled_count"),
                    "leftovers": mr.get("leftovers") or mr.get("leftover_count"),
                    "coverage": mr.get("coverage"),
                    "elapsed_s": mr.get("elapsed_seconds"),
                    "blocker": mr.get("blocker"),
                    "flash_called": bool(mr.get("flash_called")),
                    "never_submit": mr.get("never_submit", True) is not False,
                    "dummy": report.get("dummy", True),
                    "pass": mr.get("pass"),
                    "note": (
                        "FAIL:" + ",".join(mr.get("slo_fails") or [])
                        if mr.get("slo_fails")
                        else ("PASS" if mr.get("pass") else None)
                    ),
                }
            )
        return rows

    # Multi-URL entry-prepass: summarize totals + optional per-result lines
    if report.get("experiment") in (
        "cli_entry_prepass",
        "exp_entry_prepass",  # legacy scorecard JSON
    ) and isinstance(report.get("results"), list):
        totals = report.get("totals") or {}
        rows.append(
            {
                "file": path.name,
                "kind": "exp",
                "label": "entry_prepass",
                "url": f"n={len(report.get('results') or [])}",
                "filled": totals.get("form_reached") or totals.get("forms_reached"),
                "leftovers": None,
                "coverage": None,
                "elapsed_s": totals.get("elapsed_seconds"),
                "blocker": None,
                "flash_called": False,
                "never_submit": True,
                "dummy": report.get("dummy", True),
                "note": "entry/buttons only",
            }
        )
        return rows

    rows.append(
        {
            "file": path.name,
            "kind": _kind(path),
            "label": _label(path, report),
            "url": (report.get("url") or report.get("tenant") or "")[:60],
            "filled": _filled_count(report),
            "leftovers": _leftover_count(report),
            "coverage": _coverage(report),
            "elapsed_s": _elapsed(report),
            "blocker": report.get("blocker"),
            "flash_called": _flash_called(report),
            "never_submit": report.get("never_submit") is True,
            "dummy": report.get("dummy"),
            "verdict": report.get("verdict"),
        }
    )
    return rows


def _artifact_paths(results_dir: Path) -> list[Path]:
    paths = (
        sorted(results_dir.glob("fast_fill_*.json"))
        + sorted(results_dir.glob("exp_*.json"))
        + sorted(results_dir.glob("eval_*.json"))
    )
    # De-dupe while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        if p.name in seen:
            continue
        seen.add(p.name)
        out.append(p)
    return out


def collect(results_dir: Path, *, gate_flash_off: bool = False) -> list[dict[str, Any]]:
    paths = _artifact_paths(results_dir)
    # Prefer individual platform fast_fill_*.json over the matrix file for the
    # main table — but include matrix expansion when platform files are absent.
    platform_files = {
        p.name
        for p in paths
        if p.name.startswith("fast_fill_") and p.name != "fast_fill_coverage_matrix.json"
    }
    # Prefer per-run eval_PLATFORM_NN.json over eval_summary.json expansion
    per_run_eval = {
        p.name
        for p in paths
        if p.name.startswith("eval_") and p.name != "eval_summary.json"
    }
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in paths:
        if path.name == "fast_fill_coverage_matrix.json" and platform_files:
            # Still validate never_submit on the matrix file
            try:
                data = json.loads(path.read_text())
                assert_never_submit(data, path=path)
                assert_honest_advance(data, path=path)
                assert_honest_filled(data, path=path)
                if gate_flash_off or data.get("experiment") == "fast_fill_eval_suite":
                    assert_flash_off_when_unrequested(data, path=path)
            except Exception as e:
                errors.append(f"{path.name}: {e}")
            continue
        if path.name == "eval_summary.json" and per_run_eval:
            # Validate safety on summary; expand only when no per-run artifacts
            try:
                data = json.loads(path.read_text())
                assert_never_submit(data, path=path)
                assert_honest_advance(data, path=path)
                assert_honest_filled(data, path=path)
                assert_flash_off_when_unrequested(data, path=path)
            except Exception as e:
                errors.append(f"{path.name}: {e}")
            continue
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                errors.append(f"{path.name}: not a JSON object")
                continue
            rows.extend(expand_rows(path, data, gate_flash_off=gate_flash_off))
        except Exception as e:
            errors.append(f"{path.name}: {e}")
    if errors:
        raise SystemExit("scorecard failed safety/parse checks:\n  " + "\n  ".join(errors))
    return rows


def _fmt(v: Any, width: int, *, right: bool = False) -> str:
    if v is None:
        s = "-"
    elif isinstance(v, float):
        s = f"{v:.3f}" if v <= 1.5 and width >= 6 else f"{v:.2f}"
    else:
        s = str(v)
    return s.rjust(width) if right else s.ljust(width)[:width]


def print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No fast_fill_*.json / exp_*.json / eval_*.json artifacts found.")
        return

    print("=== fast-fill scorecard (dummy-only; never_submit + honest metrics asserted) ===")
    header = (
        f"{'kind':10} {'label':22} {'filled':>6} {'left':>5} {'cov':>6} "
        f"{'sec':>7} {'ns':>3} {'fl':>3} blocker"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        cov = r.get("coverage")
        cov_s = f"{cov:.3f}" if isinstance(cov, float) else "-"
        sec = r.get("elapsed_s")
        sec_s = f"{sec:.2f}" if isinstance(sec, (int, float)) else "-"
        ns = "Y" if r.get("never_submit") else "N"
        fl = "Y" if r.get("flash_called") else "N"
        blocker = r.get("blocker") or r.get("note") or "-"
        print(
            f"{_fmt(r.get('kind'), 10)} {_fmt(r.get('label'), 22)} "
            f"{_fmt(r.get('filled'), 6, right=True)} {_fmt(r.get('leftovers'), 5, right=True)} "
            f"{cov_s.rjust(6)} {sec_s.rjust(7)} {ns.rjust(3)} {fl.rjust(3)} {blocker}"
        )

    # Summary over fill-capable rows (have coverage or filled count)
    fill_rows = [r for r in rows if r.get("coverage") is not None or r.get("filled") is not None]
    secs = [r["elapsed_s"] for r in fill_rows if isinstance(r.get("elapsed_s"), (int, float))]
    covs = [r["coverage"] for r in fill_rows if isinstance(r.get("coverage"), float)]
    print("-" * len(header))
    print(f"artifacts_scored={len(rows)}  fill_rows={len(fill_rows)}")
    if covs:
        print(f"coverage: mean={sum(covs)/len(covs):.3f}  min={min(covs):.3f}  max={max(covs):.3f}")
    if secs:
        secs_sorted = sorted(secs)
        p50 = _percentile(secs_sorted, 50)
        p95 = _percentile(secs_sorted, 95)
        print(
            f"latency_s: mean={sum(secs)/len(secs):.2f}  min={min(secs):.2f}  "
            f"max={max(secs):.2f}  p50={p50:.2f}  p95={p95:.2f}  "
            f"(prefers fill_elapsed_seconds)"
        )
    bad_ns = [r for r in rows if not r.get("never_submit")]
    if bad_ns:
        raise SystemExit(f"never_submit missing/false on {len(bad_ns)} row(s)")
    flash_on = sum(1 for r in rows if r.get("flash_called"))
    print(f"never_submit: OK on all scored rows  flash_called_rows={flash_on}")
    return {"secs": secs, "covs": covs, "flash_on": flash_on}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_DIR,
        help="Directory with fast_fill_/exp_/eval_*.json (default: real_job_results)",
    )
    ap.add_argument(
        "--eval",
        action="store_true",
        help=f"Score {EVAL_DIR} (eval suite artifacts)",
    )
    ap.add_argument(
        "--gate",
        action="store_true",
        help="Hard gate: Flash-off-when-unrequested on all scored reports; exit 1 on breach.",
    )
    ap.add_argument(
        "--assert-slo",
        action="store_true",
        help="Exit 2 if Greenhouse/Workday/Ashby latency/coverage SLOs fail (eval_urls.json).",
    )
    ap.add_argument(
        "--slo-file",
        type=Path,
        default=None,
        help="Path to eval_urls.json for --assert-slo",
    )
    ap.add_argument("--json", action="store_true", help="Emit machine-readable rows")
    args = ap.parse_args()

    results_dir = EVAL_DIR if args.eval else args.dir
    if not results_dir.is_dir():
        print(f"No results dir: {results_dir}", file=sys.stderr)
        return 1

    # Eval dir always enforces Flash-off (suite contract).
    gate_flash = bool(args.gate or args.eval or args.assert_slo)
    rows = collect(results_dir, gate_flash_off=gate_flash)
    if args.json:
        print(
            json.dumps(
                {
                    "rows": rows,
                    "never_submit_asserted": True,
                    "honest_advance_asserted": True,
                    "honest_filled_asserted": True,
                    "flash_off_gated": gate_flash,
                    "dir": str(results_dir),
                },
                indent=2,
            )
        )
    else:
        print_table(rows)
    if args.gate:
        print("scorecard --gate: OK")
    if args.assert_slo:
        slo = load_eval_slo(args.slo_file)
        slo_fails: list[str] = []
        for r in rows:
            fails = check_row_slo(r, slo)
            if fails:
                slo_fails.append(f"{r.get('label')}: {','.join(fails)}")
        if slo_fails:
            print("SLO FAILS:", file=sys.stderr)
            for line in slo_fails:
                print(f"  {line}", file=sys.stderr)
            return 2
        print("SLO: OK (--assert-slo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
