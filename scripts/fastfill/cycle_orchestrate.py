#!/usr/bin/env python3
"""Multi-agent Test → Verify → Fix cycle orchestrator (dummy only).

Variety queue rotates Greenhouse, Lever, Ashby, Workday, mid-tier, unknown
from ``eval_urls.json``. Always runs with ``--flash-leftovers`` (grounded).

Flow per URL:
  1. Prefill + grounded Flash leftovers (Agent1)
  2. Screenshot → vision judge (Agent2)
  3. Attribution (Agent3)
  4. FAIL → log + retry same URL after fixes (max 2); SUCCESS → streak

Hard rules: DUMMY_PROFILE only, never Submit, never CAPTCHA solve, EEO=Decline.
Headed: CAPTCHA pause for human (Enter), hold browser open, in-session leftover
refill passes on the same page. Resume must attach+verify dummy PDF.

Usage::

    # Help / dry path (no browser)
    skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py --help
    skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py \\
        --dry-run --fixture skyvern_runtime/real_job_results/fast_fill_ashby.json

    # Live cycle (headed Flash leftovers + hold-open + captcha wait + refill)
    skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py \\
        --limit 3 --headed --success-streak 2 --min-platforms 2
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

EVAL_URLS = HERE / "eval_urls.json"
RESULTS_ROOT = ROOT / "skyvern_runtime" / "real_job_results"
FAILURES_LOG = RESULTS_ROOT / "cycle_failures.jsonl"

# Variety rotation slots (mid-tier = secondary ATS packs)
VARIETY_SLOTS = (
    "greenhouse",
    "lever",
    "ashby",
    "workday",
    "mid_tier",
    "unknown",
)
MID_TIER = frozenset(
    {
        "icims",
        "smartrecruiters",
        "workable",
        "bamboohr",
        "recruitee",
        "rippling",
        "dayforce",
        "applytojob",
        "oracle",
        "personio",
        "jobvite",
        "taleo",
        "successfactors",
        "ukg",
        "breezy",
        "jobscore",
        "gem",
        "dover",
        "phenom",
    }
)


def _utc_stamp() -> str:
    # Include microseconds so rapid dry-runs / retries don't collide.
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def load_eval_urls() -> list[dict]:
    data = json.loads(EVAL_URLS.read_text())
    return [u for u in (data.get("urls") or []) if isinstance(u, dict) and u.get("url")]


def slot_for_platform(platform: str) -> str:
    p = (platform or "unknown").lower()
    if p in VARIETY_SLOTS:
        return p
    if p in MID_TIER:
        return "mid_tier"
    return "unknown"


def build_variety_queue(
    urls: list[dict],
    *,
    limit: int | None = None,
    seed: int | None = None,
    platforms: list[str] | None = None,
) -> list[dict]:
    """Rotate slots; pick a random URL within each slot from eval_urls."""
    rng = random.Random(seed)
    by_slot: dict[str, list[dict]] = {s: [] for s in VARIETY_SLOTS}
    for u in urls:
        slot = slot_for_platform(str(u.get("platform") or "unknown"))
        by_slot.setdefault(slot, []).append(u)

    if platforms:
        want = [p.lower() for p in platforms]
        slots = [s for s in VARIETY_SLOTS if s in want or any(s == w for w in want)]
        if not slots:
            slots = list(VARIETY_SLOTS)
    else:
        slots = list(VARIETY_SLOTS)

    queue: list[dict] = []
    # Round-robin until limit
    max_n = limit if limit and limit > 0 else len(urls)
    idx = {s: 0 for s in slots}
    # Shuffle each bucket once
    for s in slots:
        rng.shuffle(by_slot[s])

    while len(queue) < max_n:
        progressed = False
        for s in slots:
            if len(queue) >= max_n:
                break
            bucket = by_slot.get(s) or []
            if not bucket:
                continue
            i = idx[s] % len(bucket)
            row = dict(bucket[i])
            row["_slot"] = s
            queue.append(row)
            idx[s] = i + 1
            progressed = True
        if not progressed:
            break
    return queue


def _dummy_email_ok(report: dict) -> bool:
    email = str(
        report.get("identity_email")
        or report.get("email_alias")
        or report.get("email")
        or ""
    ).lower()
    if not email:
        # Check filled EMAIL rows
        for f in report.get("filled") or []:
            if str(f.get("type") or "").upper() == "EMAIL":
                email = str(f.get("value") or "").lower()
                break
    return "randommail6969" in email and "@" in email


def evaluate_cycle_success(
    report: dict,
    vision: dict,
    attribution: dict | None = None,
) -> dict[str, Any]:
    """SUCCESS = vision zero unanswered + never_submit + dummy email (+ wizard).

    Heuristic-only vision (source=heuristic_report) can never SUCCESS — PNG may
    still show blanks while leftovers=0. Source must be in HONEST_COMPLETE_SOURCES.
    """
    from vision_judge import HONEST_COMPLETE_SOURCES

    reasons: list[str] = []
    empties = vision.get("empty_fields") or []
    source = str(vision.get("source") or "")
    complete = vision.get("complete") is True and len(empties) == 0

    # Honest source required for SUCCESS (missing/stub/heuristic → fail)
    if source == "heuristic_report":
        complete = False
        reasons.append("vision_source_heuristic_not_honest")
    elif not source:
        complete = False
        reasons.append("vision_source_missing")
    elif source not in HONEST_COMPLETE_SOURCES:
        complete = False
        reasons.append(f"vision_source_untrusted:{source}")

    if report.get("never_submit") is not True:
        reasons.append("never_submit_false")
    if report.get("submit_clicked") is True:
        reasons.append("submit_clicked")
    if not _dummy_email_ok(report):
        reasons.append("missing_or_non_dummy_email")
    if report.get("blocker"):
        reasons.append(f"blocker:{report.get('blocker')}")
    if report.get("chromium_fail_fast") or report.get("blocker") == "chromium_missing":
        reasons.append("chromium_missing_fail_fast")
    if report.get("advanced_incomplete"):
        reasons.append("advanced_incomplete")
    if report.get("validation_after_advance"):
        reasons.append("validation_after_advance")
    if report.get("stuck_on_same_page"):
        reasons.append("stuck_on_same_page")
    # Live blanks / false_verified demotions → cannot SUCCESS
    req_after = report.get("required_empty_after_fill") or []
    if req_after:
        complete = False
        reasons.append(f"required_empty_after_fill:{len(req_after)}")
    demoted = report.get("demoted_false_verified") or []
    if demoted:
        complete = False
        reasons.append(f"demoted_false_verified:{len(demoted)}")
    # Resume field present but not verified → cannot SUCCESS
    if report.get("resume_field_present") and not report.get("resume_verified"):
        reasons.append("resume_missing")
    elif report.get("resume_gate") == "missing_or_unverified":
        reasons.append("resume_missing")
    else:
        # Infer from leftovers
        for u in report.get("leftovers") or []:
            if isinstance(u, dict) and u.get("type") == "RESUME_UPLOAD" and u.get(
                "reason"
            ) in ("resume_missing", "resume_upload_failed", "resume_unverified"):
                reasons.append("resume_missing")
                break
    if not complete:
        reasons.append("vision_not_complete")
        if empties:
            reasons.append(f"empty_fields:{len(empties)}")
    if vision.get("confidence") == "ambiguous" and not complete:
        reasons.append("vision_ambiguous")

    # Prefill regressions do NOT fail this run if vision is complete
    prefill_regs = []
    if attribution:
        prefill_regs = attribution.get("prefill_regressions") or []

    # Deduplicate reasons while preserving order
    seen_r: set[str] = set()
    reasons = [r for r in reasons if not (r in seen_r or seen_r.add(r))]  # type: ignore

    success = len(reasons) == 0
    verdict = "SUCCESS" if success else "FAIL"
    if report.get("blocker") == "chromium_missing" or report.get("chromium_fail_fast"):
        verdict = "FAIL_ENV"
    elif report.get("blocker"):
        verdict = "BLOCKED"
    elif "stuck_on_same_page" in reasons:
        verdict = "FAIL_STUCK"
    elif "resume_missing" in reasons:
        verdict = "FAIL_BLANK"
    elif any(r.startswith("empty_fields") or r == "vision_not_complete" for r in reasons):
        verdict = "FAIL_BLANK"
    elif prefill_regs and success:
        # Note for fixer; still SUCCESS this run
        verdict = "SUCCESS"

    return {
        "success": success,
        "verdict": verdict,
        "reasons": reasons,
        "prefill_regression_count": len(prefill_regs),
        "vision_complete": complete,
        "vision_source": source,
        "never_submit": report.get("never_submit") is True,
        "dummy_email_ok": _dummy_email_ok(report),
        "resume_verified": bool(report.get("resume_verified")),
    }


def append_failure(entry: dict) -> None:
    FAILURES_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FAILURES_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def captcha_unresolved_should_skip_retries(summary: dict) -> bool:
    """True → move to next variety URL; never burn BLOCKED×3 on CAPTCHA.

    Headed path already waited in-session (Enter / sentinel / gone / timeout).
    Unresolved CAPTCHA/cloudflare must not consume fix-retries.
    """
    from captcha_pause import CAPTCHA_BLOCKERS

    if summary.get("captcha_human_solved"):
        return False
    blocker = summary.get("blocker")
    if blocker in CAPTCHA_BLOCKERS:
        return True
    decision = summary.get("decision") or {}
    reasons = decision.get("reasons") or []
    if decision.get("verdict") == "BLOCKED" and any(
        str(r).startswith("blocker:captcha")
        or str(r).startswith("blocker:cloudflare")
        for r in reasons
    ):
        return True
    cw = summary.get("captcha_wait")
    if isinstance(cw, dict) and (cw.get("timed_out") or cw.get("via") == "timeout"):
        return True
    return False


def run_dry_cycle(
    fixture_report: dict,
    *,
    out_dir: Path,
) -> dict[str, Any]:
    """Smoke path: attribution + vision judge from fixture report (no browser)."""
    from fill_attribution import analyze_fill_attribution, write_attribution
    from vision_judge import judge_from_report, write_vision_judge

    out_dir.mkdir(parents=True, exist_ok=True)
    report = dict(fixture_report)
    report.setdefault("never_submit", True)
    report.setdefault("submit_clicked", False)
    report.setdefault("dummy", True)

    vision = judge_from_report(report, screenshot=out_dir / "after_fill.png")
    write_vision_judge(vision, out_dir / "vision_judge.json")
    attr = analyze_fill_attribution(report, vision=vision)
    write_attribution(attr, out_dir / "attribution.json")
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))

    decision = evaluate_cycle_success(report, vision, attr)
    summary = {
        "mode": "dry_run",
        "out_dir": str(out_dir),
        "decision": decision,
        "attribution_summary": attr.get("summary"),
        "vision_verdict": vision.get("verdict"),
        "never_submit": True,
        "flash_leftovers": True,
        "dummy": True,
    }
    (out_dir / "cycle_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def run_live_attempt(
    url: str,
    *,
    platform: str,
    out_dir: Path,
    headed: bool = True,
    hold_seconds: int = 0,
    captcha_wait: bool | None = None,
    captcha_timeout_s: float = 600,
    refill_passes: int = 2,
    refill_wait_enter: bool | None = None,
) -> dict[str, Any]:
    """One live fast_fill + flash-leftovers + screenshot + attribution + vision.

    Headed defaults: captcha pause ON, hold browser open, 2 in-session auto
    refill passes (no Enter babysitting). Use refill_wait_enter=True only when
    explicitly requested.
    """
    from fill_attribution import analyze_fill_attribution, write_attribution
    from fast_fill import (
        VARIETY_MAX_HOLD_SECONDS,
        refuse_headed_if_chrome_busy,
        run_fast_fill,
    )
    from vision_judge import judge_screenshot, write_vision_judge

    out_dir.mkdir(parents=True, exist_ok=True)
    shot = out_dir / "after_fill.png"
    report_path = out_dir / "report.json"

    # Headed cycle: brief hold for screenshot review AFTER auto-refill; captcha
    # wait stays long (human Enter). Cap ≤120s (never 3600) — 8GB Mac OOM.
    if headed and hold_seconds <= 0:
        hold_seconds = 45  # short review window; CAPTCHA pause is separate
    if headed and hold_seconds > VARIETY_MAX_HOLD_SECONDS:
        allow_long = (os.environ.get("FASTFILL_ALLOW_LONG_HOLD") or "").strip() in (
            "1",
            "true",
            "yes",
        )
        if not allow_long:
            hold_seconds = VARIETY_MAX_HOLD_SECONDS
    if captcha_wait is None:
        captcha_wait = bool(headed)
    # Default OFF — auto-loop leftover/Flash refill; never ask human to fill School/salary
    if refill_wait_enter is None:
        refill_wait_enter = False

    if headed:
        cap_hit = refuse_headed_if_chrome_busy()
        if cap_hit:
            summary = {
                "url": url,
                "platform": platform,
                "success": False,
                "blocker": "headed_cap",
                "headed_cap": cap_hit.get("headed_cap"),
                "reasons": ["headed_cap_chrome_busy"],
                "chromium_fail_fast": True,
            }
            (out_dir / "cycle_summary.json").write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
            print(
                f"[cycle] headed_cap REFUSED: "
                f"{(cap_hit.get('headed_cap') or {}).get('message')}",
                flush=True,
            )
            return summary

    # Per-attempt sentinel avoids cross-talk when parallel/agent fills share workspace
    prev_sentinel = os.environ.get("FASTFILL_CAPTCHA_CONTINUE_FILE")
    attempt_sentinel = out_dir / ".captcha_continue"
    os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = str(attempt_sentinel)
    try:
        report = run_fast_fill(
            url,
            headed=headed,
            headless=not headed,
            screenshot=shot,
            flash_leftovers=True,
            hold_seconds=hold_seconds if headed else 0,
            captcha_wait=captcha_wait,
            captcha_timeout_s=captcha_timeout_s,
            refill_passes=int(refill_passes) if headed else 0,
            refill_wait_enter=refill_wait_enter if headed else False,
            out=report_path,
        )
    finally:
        if prev_sentinel is None:
            os.environ.pop("FASTFILL_CAPTCHA_CONTINUE_FILE", None)
        else:
            os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = prev_sentinel
    assert report.get("never_submit") is True
    assert report.get("submit_clicked") is False

    vision = judge_screenshot(shot if shot.exists() else None, report=report)
    # Prefer live DOM when page still available — not applicable here (browser closed).
    # Force non-COMPLETE if heuristic + PNG (already handled in vision_judge).
    write_vision_judge(vision, out_dir / "vision_judge.json")
    attr = analyze_fill_attribution(report, vision=vision)
    write_attribution(attr, out_dir / "attribution.json")
    decision = evaluate_cycle_success(report, vision, attr)

    summary = {
        "mode": "live",
        "url": url,
        "platform": platform,
        "out_dir": str(out_dir),
        "decision": decision,
        "attribution_summary": attr.get("summary"),
        "vision": {
            "complete": vision.get("complete"),
            "verdict": vision.get("verdict"),
            "empty_count": len(vision.get("empty_fields") or []),
            "confidence": vision.get("confidence"),
            "source": vision.get("source"),
        },
        "flash_called": report.get("flash_called"),
        "never_submit": True,
        "dummy": True,
        "identity_email": report.get("identity_email"),
        "captcha_wait": report.get("captcha_wait"),
        "captcha_human_solved": report.get("captcha_human_solved"),
        "resume_verified": report.get("resume_verified"),
        "in_session_refills": report.get("in_session_refills"),
        "blocker": report.get("blocker"),
        "chromium_fail_fast": bool(report.get("chromium_fail_fast")),
        "unfillable_after_2": bool(report.get("unfillable_after_2")),
        "unfillable_count": report.get("unfillable_count")
        or (report.get("field_attempt_log") or {}).get("unfillable_count"),
        "unfillable_keys": (report.get("field_attempt_log") or {}).get("unfillable_keys")
        or [],
        "field_attempt_log": report.get("field_attempt_log"),
        "fixer_trigger_path": report.get("fixer_trigger_path")
        or (report.get("field_attempt_log") or {}).get("fixer_trigger"),
    }
    (out_dir / "cycle_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def run_cycle(
    *,
    limit: int = 6,
    headed: bool = True,
    success_streak: int = 3,
    min_platforms: int = 2,
    max_retries: int = 2,
    seed: int | None = None,
    dry_run: bool = False,
    fixture: Path | None = None,
    platforms: list[str] | None = None,
    hold_seconds: int = 0,
    captcha_wait: bool | None = None,
    captcha_timeout_s: float = 600,
    refill_passes: int = 2,
    refill_wait_enter: bool | None = None,
) -> dict[str, Any]:
    """Drive the variety cycle until streak or queue exhausted."""
    run_id = f"cycle_{_utc_stamp()}"
    base = RESULTS_ROOT / run_id
    base.mkdir(parents=True, exist_ok=True)

    if dry_run:
        if not fixture or not fixture.exists():
            # Built-in minimal fixture
            fixture_report = {
                "url": "https://example.com/dry",
                "platform": "ashby",
                "never_submit": True,
                "submit_clicked": False,
                "identity_email": "randommail6969+drytest@gmail.com",
                "filled": [
                    {
                        "type": "EMAIL",
                        "via": "ashby_selector_pack",
                        "ok": True,
                        "verified": True,
                        "value": "randommail6969+drytest@gmail.com",
                        "label": "Email",
                    }
                ],
                "leftovers": [
                    {
                        "label": "Why do you want to join us?",
                        "type": "INTEREST",
                        "essay": True,
                        "flash_candidate": True,
                        "reason": "fixture_blank_essay",
                    }
                ],
                "flash_called": True,
                "flash": {"invoked": True},
            }
        else:
            fixture_report = json.loads(fixture.read_text())
        summary = run_dry_cycle(fixture_report, out_dir=base / "dry_00")
        rollup = {
            "run_id": run_id,
            "mode": "dry_run",
            "attempts": [summary],
            "stopped_reason": "dry_run",
            "base_dir": str(base),
        }
        (base / "rollup.json").write_text(json.dumps(rollup, indent=2))
        return rollup

    queue = build_variety_queue(
        load_eval_urls(), limit=limit, seed=seed, platforms=platforms
    )
    attempts: list[dict] = []
    streak = 0
    platforms_ok: set[str] = set()
    stopped_reason = "queue_exhausted"

    for qi, item in enumerate(queue):
        url = item["url"]
        platform = str(item.get("platform") or "unknown")
        slot = item.get("_slot") or slot_for_platform(platform)
        retries = 0
        while True:
            attempt_dir = base / f"{qi:02d}_{slot}_r{retries}"
            t0 = time.time()
            try:
                summary = run_live_attempt(
                    url,
                    platform=platform,
                    out_dir=attempt_dir,
                    headed=headed,
                    hold_seconds=hold_seconds,
                    captcha_wait=captcha_wait,
                    captcha_timeout_s=captcha_timeout_s,
                    refill_passes=refill_passes,
                    refill_wait_enter=refill_wait_enter,
                )
            except Exception as e:
                summary = {
                    "mode": "live",
                    "url": url,
                    "platform": platform,
                    "out_dir": str(attempt_dir),
                    "decision": {
                        "success": False,
                        "verdict": "FAIL",
                        "reasons": [f"exception:{e}"[:200]],
                    },
                    "error": str(e)[:300],
                    "never_submit": True,
                    "dummy": True,
                }
                attempt_dir.mkdir(parents=True, exist_ok=True)
                (attempt_dir / "cycle_summary.json").write_text(
                    json.dumps(summary, indent=2)
                )
            summary["elapsed_seconds"] = round(time.time() - t0, 2)
            summary["slot"] = slot
            summary["retry"] = retries
            attempts.append(summary)

            if summary.get("decision", {}).get("success"):
                streak += 1
                platforms_ok.add(platform)
                if streak >= success_streak and len(platforms_ok) >= min_platforms:
                    stopped_reason = (
                        f"success_streak={streak} platforms={sorted(platforms_ok)}"
                    )
                    break
                break  # next variety URL

            # FAIL path
            streak = 0
            decision = summary.get("decision") or {}
            reasons = decision.get("reasons") or []
            # CAPTCHA already waited in-session; don't burn 3 retries as BLOCKED×3
            captcha_gave_up = captcha_unresolved_should_skip_retries(summary)
            append_failure(
                {
                    "ts": _utc_stamp(),
                    "run_id": run_id,
                    "url": url,
                    "platform": platform,
                    "slot": slot,
                    "retry": retries,
                    "decision": decision,
                    "out_dir": summary.get("out_dir"),
                    "note": (
                        "CAPTCHA timed out / unresolved — next URL (no retry burn)"
                        if captcha_gave_up
                        else (
                            "Agent4 fixer should address attribution regressions / "
                            "blank_bugs then retest (orchestrator retries same URL)"
                        )
                    ),
                }
            )
            if captcha_gave_up:
                break  # next variety URL — do not retry CAPTCHA BLOCKED
            # Chromium missing: fail-fast — do not burn ×3 retries
            if (
                summary.get("chromium_fail_fast")
                or summary.get("blocker") == "chromium_missing"
                or "chromium_missing_fail_fast" in reasons
                or decision.get("verdict") == "FAIL_ENV"
                or any("chromium" in str(r) for r in reasons)
            ):
                stopped_reason = "chromium_missing_fail_fast"
                break
            if retries >= max_retries:
                break  # next variety URL
            retries += 1
            # Block until Agent4 writes FIX_APPLIED.md (or skip sentinel / timeout)
            fix_marker = attempt_dir / "FIX_APPLIED.md"
            skip_marker = attempt_dir / "FIX_SKIPPED.md"
            unfillable_md = attempt_dir / "UNFILLABLE_AFTER_2.md"
            fixer_trigger = attempt_dir / "FIXER_TRIGGER.md"
            wait_s = float(os.environ.get("FASTFILL_AGENT4_WAIT_S") or "120")
            attempt_hints = []
            if unfillable_md.is_file():
                attempt_hints.append(f"UNFILLABLE_AFTER_2: {unfillable_md}")
            if fixer_trigger.is_file():
                attempt_hints.append(f"FIXER_TRIGGER: {fixer_trigger}")
            fal = (summary.get("field_attempt_log") or {}) if isinstance(summary, dict) else {}
            # Prefer report-derived keys from cycle summary when present
            ukeys = fal.get("unfillable_keys") or summary.get("unfillable_keys") or []
            hint_block = ""
            if attempt_hints or ukeys:
                hint_block = (
                    "\nYogesh rule — fields failed ≥2 times (must-fix):\n"
                    + ("\n".join(f"  - {h}" for h in attempt_hints) + "\n" if attempt_hints else "")
                    + (
                        "  - keys: " + ", ".join(str(k) for k in ukeys[:20]) + "\n"
                        if ukeys
                        else ""
                    )
                    + "Fix class bugs (gh_select / packs / resume / demote), then FIX_APPLIED.md.\n"
                )
            if wait_s <= 0:
                (attempt_dir / "RETRY_AFTER_FIX.txt").write_text(
                    "FAIL — Agent4 wait skipped (FASTFILL_AGENT4_WAIT_S=0).\n"
                    "See attribution.json + vision_judge.json.\n"
                    f"{hint_block}"
                    "Never Submit. Dummy only. Never solve CAPTCHA.\n"
                )
                print("[agent4] wait skipped (FASTFILL_AGENT4_WAIT_S=0)", flush=True)
                continue
            (attempt_dir / "RETRY_AFTER_FIX.txt").write_text(
                "FAIL — Agent4 must write FIX_APPLIED.md (or FIX_SKIPPED.md) before retry.\n"
                "See attribution.json + vision_judge.json.\n"
                f"{hint_block}"
                "Never Submit. Dummy only. Never solve CAPTCHA.\n"
                f"Waiting up to {wait_s:.0f}s for Fixer (FASTFILL_AGENT4_WAIT_S).\n"
                "Headed: browser was held open for in-session refill; "
                "next retry opens a fresh session after your code fix.\n"
            )
            if fixer_trigger.is_file() or unfillable_md.is_file():
                print(
                    f"[agent4] UNFILLABLE_AFTER_2 present — Fixer must address "
                    f"{fixer_trigger.name if fixer_trigger.is_file() else unfillable_md.name}",
                    flush=True,
                )
            print(
                f"[agent4] waiting ≤{wait_s:.0f}s for {fix_marker.name} "
                f"(or {skip_marker.name}) before retry…",
                flush=True,
            )
            waited = 0.0
            while waited < wait_s:
                if fix_marker.exists() or skip_marker.exists():
                    break
                time.sleep(2.0)
                waited += 2.0
            if not fix_marker.exists() and not skip_marker.exists():
                (attempt_dir / "AGENT4_TIMEOUT.txt").write_text(
                    f"No FIX_APPLIED.md within {wait_s:.0f}s — retrying anyway "
                    "(set FASTFILL_AGENT4_WAIT_S=0 to skip wait).\n"
                )
                print("[agent4] timeout — proceeding with retry", flush=True)
            elif skip_marker.exists():
                print("[agent4] FIX_SKIPPED.md — proceeding with retry", flush=True)
            else:
                print("[agent4] FIX_APPLIED.md seen — retrying", flush=True)
        else:
            continue
        if stopped_reason.startswith("success_streak") or stopped_reason.startswith(
            "chromium_missing"
        ):
            break

    rollup = {
        "run_id": run_id,
        "mode": "live",
        "attempts": [
            {
                "url": a.get("url"),
                "platform": a.get("platform"),
                "slot": a.get("slot"),
                "retry": a.get("retry"),
                "verdict": (a.get("decision") or {}).get("verdict"),
                "success": (a.get("decision") or {}).get("success"),
                "out_dir": a.get("out_dir"),
                "resume_verified": a.get("resume_verified"),
                "captcha_human_solved": a.get("captcha_human_solved"),
            }
            for a in attempts
        ],
        "success_streak_final": streak,
        "platforms_ok": sorted(platforms_ok),
        "stopped_reason": stopped_reason,
        "base_dir": str(base),
        "flash_leftovers_always": True,
        "headed_hold_open": bool(headed),
        "refill_passes": int(refill_passes) if headed else 0,
        "captcha_wait": bool(captcha_wait if captcha_wait is not None else headed),
        "never_submit": True,
        "dummy": True,
    }
    (base / "rollup.json").write_text(json.dumps(rollup, indent=2))
    return rollup


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=6,
        help="Max variety URLs to attempt (default 6 = one per slot)",
    )
    ap.add_argument("--headed", action="store_true", default=True)
    ap.add_argument("--headless", action="store_true", help="Force headless browser")
    ap.add_argument(
        "--success-streak",
        type=int,
        default=3,
        help="Stop after N consecutive SUCCESSes (default 3)",
    )
    ap.add_argument(
        "--min-platforms",
        type=int,
        default=2,
        help="Require SUCCESSes across ≥K platforms before stop (default 2)",
    )
    ap.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Max fix-retries per URL after FAIL (default 2)",
    )
    ap.add_argument("--seed", type=int, default=None, help="RNG seed for variety picks")
    ap.add_argument(
        "--platform",
        action="append",
        dest="platforms",
        help="Restrict slots (repeatable): greenhouse, lever, ashby, workday, mid_tier, unknown",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="No browser — attribution + vision heuristic on fixture report",
    )
    ap.add_argument(
        "--fixture",
        type=Path,
        help="Report JSON for --dry-run (default: built-in essay-blank fixture)",
    )
    ap.add_argument(
        "--hold-seconds",
        type=int,
        default=0,
        help=(
            "Headed post-fill hold seconds (0 = cycle default 45s; hard cap 120 unless "
            "FASTFILL_ALLOW_LONG_HOLD=1). Never use 3600 in auto/variety fleets."
        ),
    )
    ap.add_argument(
        "--captcha-wait",
        action="store_true",
        default=None,
        help="Pause for human CAPTCHA solve (default ON when headed)",
    )
    ap.add_argument(
        "--no-captcha-wait",
        action="store_true",
        help="Disable CAPTCHA pause (BLOCKED immediately even when headed)",
    )
    ap.add_argument(
        "--captcha-timeout",
        type=int,
        default=600,
        help="Seconds to wait for human CAPTCHA (default 600)",
    )
    ap.add_argument(
        "--refill-passes",
        type=int,
        default=2,
        help="Same-session leftover refill passes before close (headed default 2)",
    )
    ap.add_argument(
        "--refill-wait-enter",
        action="store_true",
        default=None,
        help=(
            "OPTIONAL: wait for Enter between refill passes. Default is auto-refill "
            "(no human babysitting for School/Degree/salary leftovers)."
        ),
    )
    ap.add_argument(
        "--no-refill-wait-enter",
        action="store_true",
        help="Auto-loop refill (default). Kept for backward compatibility.",
    )
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="Queue + dry-run smoke without writing under a long live cycle",
    )
    args = ap.parse_args()

    if args.self_test:
        q = build_variety_queue(load_eval_urls(), limit=6, seed=1)
        slots = [x.get("_slot") for x in q]
        assert len(q) == 6
        assert "greenhouse" in slots and "ashby" in slots
        # Resume gate + captcha resolve unit smoke
        from captcha_pause import CAPTCHA_WAIT_MESSAGE, resolve_captcha_wait
        from resume_upload import apply_resume_success_gate

        assert resolve_captcha_wait(headed=True, captcha_wait=None) is True
        assert resolve_captcha_wait(headed=False, captcha_wait=None) is False
        assert "press Enter" in CAPTCHA_WAIT_MESSAGE
        assert CAPTCHA_WAIT_MESSAGE == (
            "CAPTCHA detected — solve it in the browser, then press Enter here to continue"
        )
        import inspect

        from captcha_pause import wait_for_human_captcha

        assert "skipped_no_tty" not in inspect.getsource(wait_for_human_captcha)
        from fast_fill import resolve_refill_wait_enter

        # Auto-refill default — never require Enter for School/salary leftovers
        assert resolve_refill_wait_enter(None) is False
        assert resolve_refill_wait_enter(True) is True
        # CAPTCHA unresolved → skip retries (no BLOCKED×3)
        assert captcha_unresolved_should_skip_retries(
            {
                "blocker": "captcha",
                "decision": {
                    "verdict": "BLOCKED",
                    "reasons": ["blocker:captcha"],
                },
            }
        )
        assert not captcha_unresolved_should_skip_retries(
            {"captcha_human_solved": True, "blocker": None}
        )
        miss = apply_resume_success_gate(
            {
                "verdict": "SUCCESS",
                "resume_upload": {"field_present": True, "attempted": True},
                "filled": [],
                "leftovers": [],
            }
        )
        assert miss["verdict"] == "FAIL"
        assert miss["resume_gate"] == "missing_or_unverified"
        dry = run_cycle(dry_run=True, fixture=args.fixture)
        # Resume-missing fails cycle SUCCESS
        bad = evaluate_cycle_success(
            {
                "never_submit": True,
                "submit_clicked": False,
                "identity_email": "randommail6969+x@gmail.com",
                "resume_field_present": True,
                "resume_verified": False,
                "leftovers": [],
            },
            {"complete": True, "empty_fields": [], "confidence": "high"},
        )
        assert bad["success"] is False and "resume_missing" in bad["reasons"]
        print(json.dumps({"queue_slots": slots, "dry": dry}, indent=2))
        print("self-test OK")
        return 0

    headed = False if args.headless else bool(args.headed)
    if args.no_captcha_wait:
        captcha_wait: bool | None = False
    elif args.captcha_wait:
        captcha_wait = True
    else:
        captcha_wait = None
    if args.no_refill_wait_enter:
        refill_wait_enter: bool | None = False
    elif args.refill_wait_enter:
        refill_wait_enter = True
    else:
        refill_wait_enter = None
    rollup = run_cycle(
        limit=args.limit,
        headed=headed,
        success_streak=args.success_streak,
        min_platforms=args.min_platforms,
        max_retries=args.max_retries,
        seed=args.seed,
        dry_run=bool(args.dry_run),
        fixture=args.fixture,
        platforms=args.platforms,
        hold_seconds=args.hold_seconds,
        captcha_wait=captcha_wait,
        captcha_timeout_s=float(args.captcha_timeout),
        refill_passes=int(args.refill_passes),
        refill_wait_enter=refill_wait_enter,
    )
    print(json.dumps(rollup, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
