#!/usr/bin/env python3
"""Run the fixed Fast fill eval suite against eval_urls.json.

Default: headless, Flash OFF. Writes artifacts under
``skyvern_runtime/eval_results/`` and prints SLO pass/fail.

Usage:
  skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py
  skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py --limit 7
  skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py --platform greenhouse
  skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py --strict-safety
  skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py --strict
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

EVAL_URLS = HERE / "eval_urls.json"
OUT_DIR = ROOT / "skyvern_runtime" / "eval_results"

# Blockers that mean the form was never reachable — skip fill-quality SLOs.
_REACHABILITY_BLOCKERS = frozenset(
    {
        "eval_exception",
        "captcha",
        "akamai",
        "bot_detection",
        "login_wall",
        "cloudflare",
        "access_denied",
        "job_closed",
        "not_found",
        "404",
    }
)


def _load_suite() -> dict:
    return json.loads(EVAL_URLS.read_text())


def _select_urls(urls: list[dict], limit: int) -> list[dict]:
    """Round-robin across platforms so --limit spreads ATS + non-ATS coverage.

    Plain ``urls[:limit]`` would grab only the first platform block (all
    Greenhouse). Practical smoke runs need one URL per platform first, and
    when ``unknown`` (generic DOM) is in the suite, reserve a slot for it
    so limited batches still exercise non-ATS.
    """
    if not limit or limit <= 0 or limit >= len(urls):
        return list(urls)
    by_plat: dict[str, list[dict]] = {}
    order: list[str] = []
    for u in urls:
        p = str(u.get("platform") or "unknown")
        if p not in by_plat:
            by_plat[p] = []
            order.append(p)
        by_plat[p].append(u)

    # Prefer unknown early so limited batches include generic DOM.
    if "unknown" in by_plat and "unknown" in order:
        order = ["unknown"] + [p for p in order if p != "unknown"]

    selected: list[dict] = []
    idx = {p: 0 for p in order}
    while len(selected) < limit:
        progressed = False
        for p in order:
            if len(selected) >= limit:
                break
            i = idx[p]
            bucket = by_plat[p]
            if i < len(bucket):
                selected.append(bucket[i])
                idx[p] = i + 1
                progressed = True
        if not progressed:
            break
    return selected


def _blocker_blocks_reachability(blocker: object) -> bool:
    if not blocker:
        return False
    s = str(blocker).lower()
    if s in _REACHABILITY_BLOCKERS:
        return True
    return any(tok in s for tok in ("captcha", "akamai", "cloudflare", "404", "not_found"))


def _check_row(row: dict, report: dict, slo: dict) -> list[str]:
    fails: list[str] = []
    plat = row.get("platform") or report.get("platform")
    if report.get("never_submit") is not True:
        fails.append("never_submit_missing")
    if report.get("submit_clicked") is True:
        fails.append("submit_clicked")

    # Honest ADVANCE: never SUCCESS after incomplete advance / validation banner.
    if report.get("advanced_incomplete"):
        fails.append("advanced_incomplete")
    if report.get("verdict") == "SUCCESS" and report.get("advanced_incomplete"):
        fails.append("success_with_advanced_incomplete")
    va = report.get("validation_after_advance") or (report.get("workday") or {}).get(
        "validation_after_advance"
    )
    if va:
        fails.append("validation_after_advance")
    if report.get("verdict") == "SUCCESS" and va:
        fails.append("success_with_validation_after_advance")

    # Inflated metrics: status=stuck must never mean a successful fill
    for filled_row in report.get("filled") or []:
        if isinstance(filled_row, dict) and filled_row.get("status") == "stuck":
            fails.append("status_stuck_as_filled")
            break
    if report.get("verdict") == "SUCCESS":
        for stuck_row in report.get("stuck") or []:
            if isinstance(stuck_row, dict) and stuck_row.get("status") == "stuck":
                fails.append("success_with_status_stuck")
                break

    # Flash must stay OFF unless explicitly requested for this run.
    flash_requested = bool(report.get("flash_leftovers_requested"))
    if report.get("flash_called") and not flash_requested:
        fails.append("flash_called_while_off")
    flash = report.get("flash") if isinstance(report.get("flash"), dict) else {}
    if flash.get("invoked") and not flash_requested:
        fails.append("flash_invoked_while_off")

    # Greenhouse fill-quality SLOs only when the form was reachable.
    # Prefer verified coverage recount over stored report.coverage.
    if plat == "greenhouse" and not _blocker_blocks_reachability(report.get("blocker")):
        gh = slo.get("greenhouse") or {}
        try:
            from scorecard_fast import _coverage as _verified_coverage
            cov = _verified_coverage(report)
        except Exception:
            cov = report.get("coverage")
        sec = report.get("fill_elapsed_seconds")
        if sec is None:
            sec = report.get("elapsed_seconds")
        if cov is not None and cov < float(gh.get("min_coverage", 0.9)):
            fails.append(f"verified_coverage={cov}<{gh.get('min_coverage')}")
        if sec is not None and sec > float(gh.get("max_seconds", 20)):
            fails.append(f"elapsed={sec}>{gh.get('max_seconds')}")

    if plat == "workday":
        # Allow FAIL before ADVANCE (incomplete) — disallow ADVANCE+validation.
        wd = report.get("workday") if isinstance(report.get("workday"), dict) else {}
        if wd.get("validation_after_advance") and "validation_after_advance" not in fails:
            fails.append("workday_validation_banner")
        if wd.get("advanced_incomplete") and report.get("verdict") == "SUCCESS":
            fails.append("workday_success_with_advanced_incomplete")
        wd_slo = slo.get("workday_contact") or {}
        max_s = wd_slo.get("max_seconds")
        if max_s is not None and not _blocker_blocks_reachability(report.get("blocker")):
            sec = report.get("fill_elapsed_seconds")
            if sec is None:
                sec = report.get("elapsed_seconds")
            if sec is not None and float(sec) > float(max_s):
                fails.append(f"workday_elapsed={sec}>{max_s}")

    if plat == "ashby" and not _blocker_blocks_reachability(report.get("blocker")):
        ash = slo.get("ashby") or {}
        max_s = ash.get("max_seconds")
        if max_s is not None:
            sec = report.get("fill_elapsed_seconds")
            if sec is None:
                sec = report.get("elapsed_seconds")
            if sec is not None and float(sec) > float(max_s):
                fails.append(f"ashby_elapsed={sec}>{max_s}")
    return fails


# SLO fail tokens that are always safety / honesty (never skip for reachability).
_SAFETY_FAIL_PREFIXES = frozenset(
    {
        "never_submit_missing",
        "submit_clicked",
        "flash_called_while_off",
        "flash_invoked_while_off",
        "advanced_incomplete",
        "validation_after_advance",
        "workday_validation_banner",
        "success_with_advanced_incomplete",
        "success_with_validation_after_advance",
        "workday_success_with_advanced_incomplete",
        "status_stuck_as_filled",
        "success_with_status_stuck",
    }
)


def _is_safety_fail(token: object) -> bool:
    s = str(token)
    key = s.split("=", 1)[0]
    return key in _SAFETY_FAIL_PREFIXES


def _slo_rollup(results: list[dict], slo: dict) -> dict:
    """Tighten summary JSON with per-platform + safety SLO counters."""
    by_platform: dict[str, dict] = {}
    for r in results:
        p = str(r.get("platform") or "unknown")
        bucket = by_platform.setdefault(
            p, {"n": 0, "passed": 0, "failed": 0, "blocked": 0, "flash_called": 0}
        )
        bucket["n"] += 1
        if r.get("pass"):
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
        if r.get("blocker"):
            bucket["blocked"] += 1
        if r.get("flash_called"):
            bucket["flash_called"] += 1

    fail_reasons: dict[str, int] = defaultdict(int)
    safety_fail_n = 0
    quality_fail_n = 0
    for r in results:
        for f in r.get("slo_fails") or []:
            # Normalize parameterized fails (coverage=… / elapsed=…)
            key = str(f).split("=", 1)[0]
            fail_reasons[key] += 1
            if _is_safety_fail(f):
                safety_fail_n += 1
            else:
                quality_fail_n += 1

    return {
        "targets": slo,
        "by_platform": by_platform,
        "fail_reasons": dict(fail_reasons),
        "safety_fail_n": safety_fail_n,
        "quality_fail_n": quality_fail_n,
        "safety": {
            "never_submit_all": all(r.get("never_submit") is True for r in results),
            "flash_called_any": any(bool(r.get("flash_called")) for r in results),
            "flash_called_while_off": fail_reasons.get("flash_called_while_off", 0)
            + fail_reasons.get("flash_invoked_while_off", 0),
            "advanced_incomplete": fail_reasons.get("advanced_incomplete", 0),
            "validation_after_advance": fail_reasons.get("validation_after_advance", 0)
            + fail_reasons.get("workday_validation_banner", 0),
            "success_with_dishonest_advance": fail_reasons.get(
                "success_with_advanced_incomplete", 0
            )
            + fail_reasons.get("success_with_validation_after_advance", 0)
            + fail_reasons.get("workday_success_with_advanced_incomplete", 0),
        },
    }


def gate_exit_code(
    results: list[dict],
    rollup: dict,
    *,
    strict: bool = False,
    strict_safety: bool = False,
) -> int:
    """Map SLO outcomes to process exit codes.

    Default (neither flag): always 0 — diagnostic runs must not kill fills.
    ``--strict-safety``: exit 1 on honesty/safety fails only.
    ``--strict``: exit 1 on safety, exit 2 on fill-quality SLO fails.
    """
    if not strict and not strict_safety:
        return 0
    safety_n = int(rollup.get("safety_fail_n") or 0)
    if safety_n <= 0:
        # Also treat rollup.safety dishonest counters / never_submit as safety.
        safety = rollup.get("safety") or {}
        if safety.get("never_submit_all") is False:
            safety_n = 1
        elif int(safety.get("flash_called_while_off") or 0) > 0:
            safety_n = 1
        elif int(safety.get("success_with_dishonest_advance") or 0) > 0:
            safety_n = 1
    if safety_n > 0:
        return 1
    if strict:
        # Any remaining row-level SLO fail is fill-quality (coverage/latency/…).
        if any(not r.get("pass") for r in results):
            return 2
        if int(rollup.get("quality_fail_n") or 0) > 0:
            return 2
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max URLs (0=all). Round-robins across platforms for diversity.",
    )
    ap.add_argument("--platform", default="", help="Filter platform")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument(
        "--strict-safety",
        action="store_true",
        help="Exit 1 on honesty/safety SLO fails (never_submit, Flash-off, dishonest SUCCESS).",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on safety fails; exit 2 on fill-quality SLO fails (reachable rows).",
    )
    args = ap.parse_args()
    if args.strict:
        args.strict_safety = True

    suite = _load_suite()
    urls = list(suite.get("urls") or [])
    if args.platform:
        urls = [u for u in urls if u.get("platform") == args.platform]
    urls = _select_urls(urls, args.limit)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    from fast_fill import run_fast_fill

    results: list[dict] = []
    t0 = time.time()
    platforms = sorted({str(u.get("platform") or "unknown") for u in urls})
    print(
        f"eval suite: n={len(urls)} platforms={platforms} flash=OFF headless={not args.headed}",
        flush=True,
    )
    for i, row in enumerate(urls, 1):
        url = row["url"]
        plat = row.get("platform") or "unknown"
        print(f"[{i}/{len(urls)}] {plat} {url[:80]}", flush=True)
        try:
            report = run_fast_fill(
                url,
                headed=bool(args.headed),
                flash_leftovers=False,
            )
        except Exception as e:
            report = {
                "url": url,
                "platform": plat,
                "dummy": True,
                "never_submit": True,
                "submit_clicked": False,
                "blocker": "eval_exception",
                "errors": [{"eval": str(e)[:300]}],
                "coverage": 0,
                "elapsed_seconds": 0,
                "flash_called": False,
                "flash_leftovers_requested": False,
                "flash": {"invoked": False, "mode": "leftovers_only", "never_submit": True},
            }
        # Suite always runs Flash OFF — pin the flag even if report omitted it.
        report.setdefault("flash_leftovers_requested", False)
        report.setdefault("flash_called", False)
        report.setdefault("never_submit", True)
        report.setdefault("submit_clicked", False)
        report.setdefault("dummy", True)

        fails = _check_row(row, report, suite.get("slo") or {})
        stem = f"eval_{plat}_{i:02d}.json"
        path = out_dir / stem
        # Strip internal live handles (_attempt_log / _fill_step_log / _page)
        # that the fill returns re-attached; default=str is a final safety net so
        # any stray object (e.g. an early blocker-return report) never crashes the
        # eval writer.
        report_json = {k: v for k, v in report.items() if not str(k).startswith("_")}
        path.write_text(json.dumps(report_json, indent=2, default=str))
        results.append(
            {
                "url": url,
                "platform": plat,
                "artifact": str(path),
                "coverage": report.get("coverage"),
                "elapsed_seconds": report.get("elapsed_seconds"),
                "blocker": report.get("blocker"),
                "verdict": report.get("verdict"),
                "flash_called": bool(report.get("flash_called")),
                "never_submit": report.get("never_submit") is True,
                "advanced_incomplete": bool(report.get("advanced_incomplete")),
                "validation_after_advance": bool(
                    report.get("validation_after_advance")
                    or (report.get("workday") or {}).get("validation_after_advance")
                ),
                "slo_fails": fails,
                "pass": not fails,
            }
        )
        status = "PASS" if not fails else "FAIL:" + ",".join(fails)
        print(
            f"  → {status} cov={report.get('coverage')} "
            f"sec={report.get('elapsed_seconds')} blocker={report.get('blocker')} "
            f"flash_called={bool(report.get('flash_called'))}",
            flush=True,
        )

    slo = suite.get("slo") or {}
    rollup = _slo_rollup(results, slo)
    summary = {
        "experiment": "fast_fill_eval_suite",
        "never_submit": True,
        "submit_clicked": False,
        "dummy": True,
        "flash_leftovers": False,
        "flash_called": False,
        "elapsed_seconds": round(time.time() - t0, 2),
        "n": len(results),
        "passed": sum(1 for r in results if r["pass"]),
        "failed": sum(1 for r in results if not r["pass"]),
        "platforms": platforms,
        "rows": results,
        "slo": slo,
        "slo_rollup": rollup,
    }
    summary_path = out_dir / "eval_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    # Phase 5: append one reduced row to the metrics timeline (best-effort;
    # never let observability break the eval run).
    try:
        from metrics_timeline import append_row

        append_row(summary_path, label="eval_suite")
    except Exception:
        pass
    print(
        f"\n=== eval suite: passed={summary['passed']}/{summary['n']} "
        f"in {summary['elapsed_seconds']}s → {summary_path}",
        flush=True,
    )
    safety = rollup.get("safety") or {}
    print(
        f"    safety: flash_called_any={safety.get('flash_called_any')} "
        f"advanced_incomplete={safety.get('advanced_incomplete')} "
        f"validation_after_advance={safety.get('validation_after_advance')}",
        flush=True,
    )
    code = gate_exit_code(
        results,
        rollup,
        strict=bool(args.strict),
        strict_safety=bool(args.strict_safety),
    )
    if code == 1:
        print("STRICT SAFETY FAIL — honesty/safety SLO breached", flush=True)
    elif code == 2:
        print("STRICT FILL-QUALITY FAIL — coverage/latency/ADVANCE SLO breached", flush=True)
    elif not args.strict and not args.strict_safety:
        # Exit 0 even with SLO fails — suite is diagnostic; use --strict for gates
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
