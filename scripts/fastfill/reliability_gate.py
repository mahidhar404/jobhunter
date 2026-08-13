#!/usr/bin/env python3
"""Single acceptance gate: headed NXP Workday E2E with strict scoring.

Dummy-only; never submit. Writes ``reliability_gate.json`` beside the run
artifacts. Exit 0 only when all hard metrics pass.

  skyvern_runtime/venv/bin/python scripts/fastfill/reliability_gate.py
  skyvern_runtime/venv/bin/python scripts/fastfill/reliability_gate.py --headless
  skyvern_runtime/venv/bin/python scripts/fastfill/reliability_gate.py --tier1
  skyvern_runtime/venv/bin/python scripts/fastfill/reliability_gate.py --skip-run
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PY = ROOT / "skyvern_runtime" / "venv" / "bin" / "python"
if not PY.is_file():
    PY = Path(sys.executable)

LOCK_PATH = HERE / ".fill_run.lock"
GATE_PATH = HERE / "reliability_gate.json"

NXP_URL = (
    "https://nxp.wd3.myworkdayjobs.com/careers/job/Austin-Oakhill-Office/"
    "AI-ML-driven-ASIC-Design-and-Implementation-Automation-Engineer_R-10065561"
)
QUANTI_URL = (
    "https://quantiphi.wd1.myworkdayjobs.com/Careers_at_Quantiphi/job/"
    "USA---Remote/Senior-Data-Engineer---AWS---Quicksight_JR11459"
)

# Module-level state for atexit flush
_ATExit_STATE: dict[str, Any] = {"out_dir": None, "gate": None, "report_written": False}


class FillRunLockError(RuntimeError):
    """Another fastfill gate run holds the single-process lock."""


def _acquire_fill_run_lock() -> None:
    """Exclusive non-blocking lock — one headed/live fill at a time."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(LOCK_PATH), flags, 0o644)
    except FileExistsError as e:
        holder = ""
        try:
            holder = LOCK_PATH.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        raise FillRunLockError(
            f"fill run lock held ({LOCK_PATH}){': ' + holder if holder else ''}"
        ) from e
    payload = f"pid={os.getpid()} ts={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')}\n"
    os.write(fd, payload.encode())
    os.close(fd)


def _release_fill_run_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _atexit_write_artifacts() -> None:
    """Always persist gate + minimal report on exit (crash/interrupt included)."""
    out_dir = _ATExit_STATE.get("out_dir")
    gate = _ATExit_STATE.get("gate")
    if gate is None and out_dir is not None:
        try:
            gate = score_run(out_dir)
        except Exception:
            gate = {
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                "out_dir": str(out_dir),
                "pass": False,
                "error": "atexit_score_failed",
            }
    if gate is None:
        return
    try:
        GATE_PATH.write_text(json.dumps(gate, indent=2) + "\n")
    except Exception:
        pass
    if out_dir is not None:
        try:
            out_p = Path(out_dir)
            (out_p / "reliability_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
            report_path = out_p / "report.json"
            if not report_path.is_file() and not _ATExit_STATE.get("report_written"):
                stub = {
                    "dummy": True,
                    "never_submit": True,
                    "submit_clicked": False,
                    "verdict": gate.get("verdict"),
                    "blocker": gate.get("blocker") or "gate_atexit_stub",
                    "reliability_gate_atexit": True,
                }
                report_path.write_text(json.dumps(stub, indent=2) + "\n")
        except Exception:
            pass


atexit.register(_atexit_write_artifacts)


def _parse_pages(report: dict, steps_path: Path | None) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    wd = report.get("workday") if isinstance(report.get("workday"), dict) else {}
    step = str(report.get("workday_current_step") or wd.get("current_step") or "")
    pe = report.get("phase_e") if isinstance(report.get("phase_e"), dict) else {}
    at_review = step.lower() == "review" or bool(pe.get("stopped_at_review"))
    pages.append(
        {
            "name": step or "unknown",
            "pass": at_review,
            "reason": "reached_review" if at_review else f"stopped_at_{step or 'unknown'}",
        }
    )
    if steps_path and steps_path.is_file():
        text = steps_path.read_text(errors="replace")
        for m in re.finditer(r"wizard_step[^|]*\|\s*(\w+)", text):
            name = m.group(1)
            if name and (not pages or pages[-1]["name"] != name):
                pages.append({"name": name, "pass": False, "reason": "seen_in_steps"})
    return pages


def _field_resolved_ok_in_report(field: str, report: dict) -> bool:
    """True when live report shows the field done despite a stale action_audit WRONG."""
    from field_done import dummy_springfield_location_shown, field_is_done_from_row

    field_u = (field or "").strip().upper()
    if not field_u:
        return False
    compact_f = field_u.replace("-", "").replace("_", "").replace("/", "")

    def _row_matches(row_key: str) -> bool:
        rk = (row_key or "").upper().replace("-", "").replace("_", "").replace("/", "")
        if not rk:
            return False
        if compact_f in rk or rk in compact_f:
            return True
        if "LOCATION" in compact_f and "LOCATION" in rk:
            return True
        if "TITLE" in compact_f and "JOBTITLE" in rk:
            return True
        if "COMPANY" in compact_f and "COMPANY" in rk and "PHONE" not in rk:
            return True
        return False

    for row in report.get("filled") or []:
        if not isinstance(row, dict):
            continue
        row_key = str(
            row.get("field")
            or row.get("type")
            or row.get("automation_id")
            or ""
        )
        if not _row_matches(row_key):
            continue
        v = field_is_done_from_row(row)
        if v.ok:
            return True
        rb = str(row.get("readback") or "")
        if "LOCATION" in compact_f and dummy_springfield_location_shown(rb):
            return True
    for row in report.get("leftovers") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or row.get("automation_id") or "").upper()
        if field_u in label and row.get("reason") in (
            "already_correct_skip",
            "autofill_committed_skip",
        ):
            return True
    return False


def _leftover_counts(report: dict) -> tuple[int, int]:
    """Return (real_leftover_count, invented_leftover_count)."""
    leftovers = [u for u in (report.get("leftovers") or []) if isinstance(u, dict)]
    try:
        from leftover_miss_scan import is_invented_leftover

        invented = [u for u in leftovers if is_invented_leftover(u, report)]
    except Exception:
        invented = []
    return len(leftovers) - len(invented), len(invented)


def _wrong_values_from_jsonl(
    steps_path: Path | None,
    audit_path: Path | None = None,
    report: dict | None = None,
) -> list[dict]:
    """Parse fill_steps.jsonl / action_audit.jsonl for supervisor WRONG (multiline-safe)."""
    bad: list[dict] = []
    paths = [p for p in (steps_path, audit_path) if p and p.is_file()]
    for path in paths:
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            reason = str(row.get("reason") or "")
            extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
            sv = str(
                extra.get("supervisor_verdict")
                or row.get("supervisor_verdict")
                or ""
            ).upper()
            action = str(row.get("action") or "")
            is_wrong = sv == "WRONG" or reason.upper().startswith("WRONG")
            if not is_wrong:
                continue
            if action and action not in ("action_audit", "") and "audit" not in action:
                if sv != "WRONG":
                    continue
            bad.append(
                {
                    "field": row.get("label")
                    or row.get("field")
                    or row.get("field_type")
                    or row.get("automation_id"),
                    "readback": str(row.get("after") or row.get("readback") or "")[:120],
                    "reason": reason or sv,
                }
            )
    if report:
        bad = [w for w in bad if not _field_resolved_ok_in_report(str(w.get("field") or ""), report)]
    return bad


def _wrong_values_from_steps(
    steps_path: Path | None,
    run_log: Path | None = None,
    report: dict | None = None,
) -> list[dict]:
    bad: list[dict] = []
    sources: list[str] = []
    if steps_path and steps_path.is_file():
        sources.append(steps_path.read_text(errors="replace"))
    if run_log and run_log.is_file():
        sources.append(run_log.read_text(errors="replace"))
    for text in sources:
        # 0842 how_heard WRONG spans newlines inside the quoted readback.
        # Do not let an earlier OK audit steal a later WRONG reason.
        for m in re.finditer(
            r"action_audit \| ([^\s(|]+)(?:\s+\([^)]*\))?(?:(?!action_audit \|).)*?reason=(WRONG:\S+)",
            text,
            re.S,
        ):
            bad.append(
                {
                    "field": m.group(1).strip(),
                    "readback": "",
                    "reason": m.group(2),
                }
            )
        for line in text.splitlines():
            if "action_audit" not in line or "WRONG" not in line:
                continue
            m = re.search(
                r"action_audit \| ([^\(]+)\([^)]+\).*reason=(WRONG:\S+)",
                line,
            )
            if m:
                rec = {
                    "field": m.group(1).strip(),
                    "readback": "",
                    "reason": m.group(2),
                }
                if rec not in bad:
                    bad.append(rec)
    if report:
        bad = [w for w in bad if not _field_resolved_ok_in_report(str(w.get("field") or ""), report)]
    return bad


def _wrong_values(report: dict, steps_path: Path | None = None) -> list[dict]:
    bad: list[dict] = []
    for row in report.get("filled") or []:
        if not isinstance(row, dict):
            continue
        from field_done import field_is_done_from_row

        v = field_is_done_from_row(row)
        if not v.ok and row.get("verified"):
            bad.append(
                {
                    "field": row.get("field") or row.get("type"),
                    "readback": str(v.readback or "")[:120],
                    "reason": v.reason,
                }
            )
    for row in report.get("action_audit") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("supervisor_verdict") or "").upper() == "WRONG":
            field = str(row.get("field") or "")
            if _field_resolved_ok_in_report(field, report):
                continue
            bad.append(
                {
                    "field": row.get("field"),
                    "readback": str(row.get("after") or "")[:120],
                    "reason": row.get("reason") or "supervisor_wrong",
                }
            )
    return bad


def score_run(out_dir: Path) -> dict[str, Any]:
    report_path = out_dir / "report.json"
    hold_path = out_dir / "hold_snapshot.json"
    src = report_path if report_path.is_file() else hold_path
    report: dict = {}
    if src.is_file():
        report = json.loads(src.read_text())
    steps_path = out_dir / "fill_steps.jsonl"

    thrash = int(report.get("thrash_rewrites") or 0)
    from page_progress import can_claim_ready, may_enter_review_hold

    false_inc = 0
    gaps = report.get("gaps_after_save") or []
    if gaps and can_claim_ready({**report, "gaps_after_save": []}):
        false_inc += len(gaps)
    req = report.get("required_empty_after_fill") or []
    if req:
        from field_done import filter_required_empty_from_report

        filtered = filter_required_empty_from_report(report, req)
        if len(filtered) < len(req):
            false_inc += len(req) - len(filtered)
        req = filtered

    real_left, invented_left = _leftover_counts(report)

    wrong = _wrong_values(report, steps_path)
    jsonl_wrong = _wrong_values_from_jsonl(steps_path, out_dir / "action_audit.jsonl", report)
    for w in jsonl_wrong:
        if w not in wrong:
            wrong.append(w)
    if not wrong:
        wrong = _wrong_values_from_steps(steps_path, out_dir / "run.log", report)
    pages = _parse_pages(report, steps_path)
    reached_review = any(p.get("name", "").lower() == "review" and p.get("pass") for p in pages)
    if not reached_review:
        pe = report.get("phase_e") or {}
        reached_review = bool(pe.get("stopped_at_review")) or str(
            report.get("workday_current_step") or ""
        ).lower() == "review"

    gate = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "out_dir": str(out_dir.relative_to(ROOT)) if out_dir.is_relative_to(ROOT) else str(out_dir),
        "pass": False,
        # Honesty: this scorer only knows live artifacts. Gym green never sets live_pass.
        # See scripts/fastfill/GYM_VS_LIVE.md + flight_recorder for live truth path.
        "confidence_lane": "live_workday_artifact",
        "gym_pass": None,
        "live_pass": False,
        "thrash_rewrites": thrash,
        "false_incomplete": false_inc,
        "wrong_values": len(wrong),
        "wrong_value_details": wrong[:20],
        "reached_review": reached_review,
        "ready_for_review": bool(report.get("ready_for_review")),
        "can_claim_ready": can_claim_ready(report),
        "may_enter_review_hold": may_enter_review_hold(report),
        "pages_completed": pages,
        "verdict": report.get("verdict"),
        "filled_count": len(report.get("filled") or []),
        "leftover_count": real_left,
        "invented_leftover_count": invented_left,
    }
    live_ok = (
        thrash == 0
        and false_inc == 0
        and len(wrong) == 0
        and reached_review
        and gate["can_claim_ready"]
    )
    gate["pass"] = live_ok
    gate["live_pass"] = live_ok
    return gate


def run_workday_url(
    url: str,
    *,
    label: str,
    headed: bool,
    out_dir: Path,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ".force_create_account").touch()
    cmd = [
        str(PY),
        str(HERE / "fast_fill.py"),
        url,
        "--flash-leftovers",
        "--refill-passes",
        "2",
        "--no-fill-pause",
        "--captcha-wait",
        "--test-mode",
        "--out",
        str(out_dir / "report.json"),
    ]
    if headed:
        cmd.extend(["--headed", "--hold-open", "--hold-seconds", "12"])
    else:
        cmd.append("--headless")
    env = {
        **dict(os.environ),
        "TEST_MODE": "1",
        "FASTFILL_REAL_PROFILE": "0",
        "FASTFILL_FILL_PAUSE": "0",
        "FASTFILL_ACTION_SUPERVISOR": "1",
        "FASTFILL_STRICT_COMPLETION": "1",
        "PYTHONUNBUFFERED": "1",
    }
    log_path = out_dir / "run.log"
    with log_path.open("w") as log:
        log.write(f"# reliability_gate run label={label}\n# url={url}\n\n")
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    with log_path.open("a") as log:
        log.write(f"\nEXIT={proc.returncode}\n")
    return proc.returncode


def run_nxp(*, headed: bool, out_dir: Path) -> int:
    return run_workday_url(NXP_URL, label="nxp", headed=headed, out_dir=out_dir)


def run_tier1(*, headed: bool, base_dir: Path) -> tuple[list[dict[str, Any]], int]:
    """Run NXP then Quantiphi sequentially; return per-run gates + worst exit."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    runs = [
        ("nxp", NXP_URL, base_dir / f"nxp_reliability_gate_{ts}"),
        ("quantiphi", QUANTI_URL, base_dir / f"quantiphi_reliability_gate_{ts}"),
    ]
    results: list[dict[str, Any]] = []
    worst = 0
    for label, url, out_dir in runs:
        _ATExit_STATE["out_dir"] = out_dir
        rc = run_workday_url(url, label=label, headed=headed, out_dir=out_dir)
        gate = score_run(out_dir)
        gate["tier1_label"] = label
        gate["tier1_url"] = url[:120]
        results.append(gate)
        _ATExit_STATE["gate"] = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "tier1": True,
            "confidence_lane": "live_workday_tier1",
            "gym_pass": None,
            "live_pass": all(r.get("live_pass") or r.get("pass") for r in results),
            "runs": results,
            "pass": all(r.get("pass") for r in results),
        }
        if rc != 0 and not (out_dir / "report.json").is_file() and not (
            out_dir / "hold_snapshot.json"
        ).is_file():
            print(f"[tier1:{label}] fast_fill exited {rc} with no report", file=sys.stderr)
            worst = max(worst, rc if rc else 1)
        if not gate["pass"]:
            worst = max(worst, 1)
    live_all = all(r.get("live_pass") or r.get("pass") for r in results)
    rollup = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "tier1": True,
        "confidence_lane": "live_workday_tier1",
        "gym_pass": None,
        "live_pass": live_all,
        "pass": live_all,
        "runs": results,
    }
    _ATExit_STATE["gate"] = rollup
    return results, worst


def _write_gate(gate: dict[str, Any], out_dir: Path | None) -> None:
    GATE_PATH.write_text(json.dumps(gate, indent=2) + "\n")
    if out_dir is not None:
        (out_dir / "reliability_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    _ATExit_STATE["gate"] = gate
    _ATExit_STATE["report_written"] = True


def main() -> int:
    ap = argparse.ArgumentParser(description="NXP reliability acceptance gate")
    ap.add_argument("--headless", action="store_true", help="Run headless (faster, less faithful)")
    ap.add_argument("--out-dir", type=Path, help="Existing artifact dir to score only")
    ap.add_argument("--skip-run", action="store_true", help="Score latest nxp_* dir only")
    ap.add_argument(
        "--tier1",
        action="store_true",
        help="Run NXP + Quantiphi Workday URLs sequentially (single-process lock)",
    )
    args = ap.parse_args()

    lock_held = False
    if not args.skip_run and not args.out_dir:
        try:
            from fast_fill import kill_orphan_chrome_mains

            killed = kill_orphan_chrome_mains()
            if killed:
                print(f"[gate] killed orphan Chrome mains: {killed}", file=sys.stderr)
        except Exception:
            pass
        try:
            _acquire_fill_run_lock()
            lock_held = True
        except FillRunLockError as e:
            print(str(e), file=sys.stderr)
            return 3

    exit_code = 0
    try:
        if args.out_dir:
            out_dir = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
            _ATExit_STATE["out_dir"] = out_dir
            gate = score_run(out_dir)
            _write_gate(gate, out_dir)
        elif args.skip_run:
            base = ROOT / "skyvern_runtime" / "real_job_results"
            dirs = sorted(base.glob("nxp_*"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not dirs:
                gate = {
                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                    "pass": False,
                    "error": "no_nxp_artifact_dirs",
                }
                _write_gate(gate, None)
                print("No nxp_* artifact dirs found", file=sys.stderr)
                exit_code = 2
            else:
                out_dir = dirs[0]
                _ATExit_STATE["out_dir"] = out_dir
                gate = score_run(out_dir)
                _write_gate(gate, out_dir)
        elif args.tier1:
            base = ROOT / "skyvern_runtime" / "real_job_results"
            results, worst = run_tier1(headed=not args.headless, base_dir=base)
            rollup = _ATExit_STATE.get("gate") or {
                "tier1": True,
                "pass": all(r.get("pass") for r in results),
                "runs": results,
            }
            _write_gate(rollup, None)
            print(json.dumps(rollup, indent=2))
            if rollup.get("pass"):
                print(f"\nTIER1 GATE PASS → {GATE_PATH}")
                exit_code = 0
            else:
                print(f"\nTIER1 GATE FAIL → {GATE_PATH}")
                for r in results:
                    if not r.get("pass"):
                        print(
                            f"  [{r.get('tier1_label')}] review={r.get('reached_review')} "
                            f"thrash={r.get('thrash_rewrites')} wrong={r.get('wrong_values')}"
                        )
                exit_code = worst or 1
            return exit_code
        else:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
            out_dir = ROOT / "skyvern_runtime" / "real_job_results" / f"nxp_reliability_gate_{ts}"
            _ATExit_STATE["out_dir"] = out_dir
            rc = run_nxp(headed=not args.headless, out_dir=out_dir)
            gate = score_run(out_dir)
            _write_gate(gate, out_dir)
            if rc != 0 and not (out_dir / "report.json").is_file() and not (
                out_dir / "hold_snapshot.json"
            ).is_file():
                print(f"fast_fill exited {rc} with no report", file=sys.stderr)

        gate = _ATExit_STATE.get("gate") or score_run(out_dir)
        print(json.dumps(gate, indent=2))
        if gate["pass"]:
            print(f"\nGATE PASS → {GATE_PATH}")
            exit_code = 0
        else:
            print(f"\nGATE FAIL → {GATE_PATH}")
            if not gate["reached_review"]:
                pages = gate.get("pages_completed") or [{"name": "unknown"}]
                print(f"  blocked: never reached Review (stopped at {pages[0]['name']})")
            if gate["thrash_rewrites"]:
                print(f"  thrash_rewrites={gate['thrash_rewrites']} (must be 0)")
            if gate["false_incomplete"]:
                print(f"  false_incomplete={gate['false_incomplete']} (must be 0)")
            if gate["wrong_values"]:
                print(f"  wrong_values={gate['wrong_values']} (must be 0)")
                for w in gate["wrong_value_details"][:5]:
                    print(f"    - {w.get('field')}: {w.get('reason')}")
            exit_code = 1
        return exit_code
    finally:
        if lock_held:
            _release_fill_run_lock()


if __name__ == "__main__":
    raise SystemExit(main())
