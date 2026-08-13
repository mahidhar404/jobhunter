#!/usr/bin/env python3
"""Drive production Workday fill functions against the battle gym HTML.

Loads ``gym/ats/cases/workday_battle_multipage/form.html`` and calls the same
fill path live uses (``workday_two_phase_on_page``, ``_fill_automation_id``,
contact pack, ``verified_select``, locks) plus flight recorder. Scores vs
``gold.json``. Dummy-only. Never submit.

    skyvern_runtime/venv/bin/python scripts/fastfill/run_battle_fill.py
    skyvern_runtime/venv/bin/python scripts/fastfill/run_battle_fill.py --json
    skyvern_runtime/venv/bin/python scripts/fastfill/run_battle_fill.py --headed
    skyvern_runtime/venv/bin/python scripts/fastfill/run_battle_fill.py --headed --hold-open
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
GYM_CASE = ROOT / "gym" / "ats" / "cases" / "workday_battle_multipage"
FORM_HTML = (GYM_CASE / "form.html").resolve()
DEFAULT_HEADED_HOLD_SECONDS = 25
HEADED_SLOW_MO_MS = 280
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gym" / "ats"))

# Headless gym: no pause overlay, flight on for the cycle log.
# Headed watch still skips the Pause overlay so the fill auto-plays slowly.
os.environ.setdefault("FASTFILL_FILL_PAUSE", "0")
os.environ["FASTFILL_FLIGHT"] = "1"


def _gym_values() -> dict[str, Any]:
    """Dummy profile values aligned to battle-gym gold (still dummy PII)."""
    from field_map import (
        ADDRESS_LINE1,
        DUMMY_ADDRESS,
        DUMMY_PROFILE,
        build_value_map,
    )

    values = dict(build_value_map(DUMMY_PROFILE, DUMMY_ADDRESS) or {})
    # Fiber-stub gold is a street line, not the full dummy mailing string.
    values[ADDRESS_LINE1] = "100 Test Street"
    return values


async def _never_submit_ok(page) -> bool:
    submitted = await page.evaluate(
        """() => {
          const el = document.getElementById('btn-submit');
          return !!(el && el.getAttribute('data-submitted') === 'true');
        }"""
    )
    return not submitted


async def _hold_browser(page, *, hold_seconds: int) -> None:
    """Keep Chromium open so a human can see Review. Never submit."""
    if hold_seconds == 0:
        return
    if hold_seconds < 0:
        print("Hold-open: close the browser window or Ctrl+C when done watching.")
        try:
            while not page.is_closed():
                await page.wait_for_timeout(1000)
        except Exception:
            return
        return
    print(f"Holding browser open {hold_seconds}s after fill (Review should be visible).")
    try:
        await page.wait_for_timeout(hold_seconds * 1000)
    except Exception:
        return


async def run_battle_fill(
    *,
    headed: bool = False,
    hold_seconds: int | None = None,
) -> dict[str, Any]:
    """Open gym HTML, run production fill, score vs gold. Never submit."""
    from playwright.async_api import async_playwright

    from exp_workday_selectors import workday_two_phase_on_page
    from field_map import EMAIL
    from flight_recorder import attach_flight_recorder
    from score import score_page

    gold = json.loads((GYM_CASE / "gold.json").read_text(encoding="utf-8"))
    values = _gym_values()
    form_uri = FORM_HTML.as_uri()
    if hold_seconds is None:
        hold_seconds = DEFAULT_HEADED_HOLD_SECONDS if headed else 0

    out: dict[str, Any] = {
        "ok": False,
        "dummy": True,
        "never_submit": True,
        "submit_clicked": False,
        "case": "workday_battle_multipage",
        "identity_email": values.get(EMAIL),
        "form_uri": form_uri,
        "headed": headed,
        "hold_seconds": hold_seconds,
    }

    if headed:
        print(f"WATCH gym HTML: {form_uri}")
        print(f"file: {FORM_HTML}")
        print("Dummy PII only · never submit · flight recorder ON · slow_mo for watching")

    async with async_playwright() as pw:
        launch_kwargs: dict[str, Any] = {"headless": not headed}
        if headed:
            launch_kwargs["slow_mo"] = HEADED_SLOW_MO_MS
        browser = await pw.chromium.launch(**launch_kwargs)
        page = await browser.new_page()
        await page.goto(form_uri, wait_until="domcontentloaded")
        await page.wait_for_timeout(80 if not headed else 600)

        empty = await score_page(page, gold)
        out["empty_score_ok"] = bool(empty.get("ok"))
        if empty.get("ok"):
            out["detail"] = "empty gym must fail gold"
            await browser.close()
            return out

        with tempfile.TemporaryDirectory(prefix="battle_fill_") as td:
            report: dict[str, Any] = {
                "platform": "workday",
                "coverage_path": "workday_multipage",
                "dummy": True,
                "headed": headed,
                "never_submit": True,
                "submit_clicked": False,
                "url": form_uri,
                "_attempt_cycle_dir": td,
            }
            attach_flight_recorder(report, out_dir=td, run_id="battle_gym", force=True)
            report["fill_values"] = values
            wd = await workday_two_phase_on_page(
                page,
                values,
                click_create_account=False,
                do_apply_clicks=False,
                step_report=report,
            )
            out["workday"] = {
                "verdict": wd.get("verdict"),
                "advanced": wd.get("advanced"),
                "blocker": wd.get("blocker"),
                "advance_blocked_reason": wd.get("advance_blocked_reason"),
                "workday_current_step": wd.get("workday_current_step"),
                "phase_b_advanced": (wd.get("phase_b") or {}).get("advanced"),
                "phase_c_advanced": (wd.get("phase_c") or {}).get("advanced"),
                "phase_c_edu_advanced": (wd.get("phase_c_edu") or {}).get("advanced"),
                "phase_c2_advanced": (wd.get("phase_c2") or {}).get("advanced"),
                "filled_count": wd.get("filled_count"),
                "missed_count": wd.get("missed_count"),
            }
            out["flight_log"] = report.get("flight_log_path")
            out["flight_jsonl"] = report.get("flight_jsonl_path")

            try:
                from field_done import filled_rows_honest, field_is_done_from_row

                if not filled_rows_honest(report):
                    out["dishonest_filled"] = [
                        {
                            "type": r.get("type"),
                            "aid": r.get("automation_id"),
                            "reason": r.get("reason"),
                            "verified": r.get("verified"),
                        }
                        for r in (report.get("filled") or [])
                        if isinstance(r, dict) and not field_is_done_from_row(r).ok
                    ][:12]
            except Exception:
                pass
            try:
                from exp_workday_selectors import _required_empty_on_page

                out["required_empty"] = await _required_empty_on_page(page)
            except Exception as e:
                out["required_empty"] = [{"error": str(e)[:120]}]
            try:
                out["listbox_open"] = await page.evaluate(
                    """() => [...document.querySelectorAll('[aria-expanded="true"]')]
                      .filter((el) => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                      })
                      .map((el) => ({
                        id: el.id || el.getAttribute('data-automation-id') || el.tagName,
                        expanded: el.getAttribute('aria-expanded'),
                      }))"""
                )
            except Exception:
                out["listbox_open"] = []
            flight_src = Path(str(report.get("flight_log_path") or ""))
            if flight_src.is_file():
                persist = Path(tempfile.gettempdir()) / "job-hunter-battle-fill-flight.log"
                persist.write_text(flight_src.read_text(encoding="utf-8"), encoding="utf-8")
                out["flight_log_persist"] = str(persist)
            jsonl_src = Path(str(report.get("flight_jsonl_path") or ""))
            if jsonl_src.is_file():
                persist_j = Path(tempfile.gettempdir()) / "job-hunter-battle-fill-flight.jsonl"
                persist_j.write_text(jsonl_src.read_text(encoding="utf-8"), encoding="utf-8")
                out["flight_jsonl_persist"] = str(persist_j)
            try:
                out["reached_review"] = await page.evaluate(
                    """() => {
                      const el = document.querySelector('[data-automation-id="reviewPage"]');
                      return !!(el && el.classList.contains('active'));
                    }"""
                )
            except Exception:
                out["reached_review"] = False

            scored = await score_page(page, gold)
            out["score"] = {
                "ok": scored.get("ok"),
                "footer_ok": scored.get("footer_ok"),
                "spa_ok": scored.get("spa_ok"),
                "listbox_ok": scored.get("listbox_ok"),
                "detail": scored.get("detail"),
                "field_results": [
                    {
                        "key": f.get("key"),
                        "matched": f.get("matched"),
                        "expected": f.get("expected"),
                        "actual": f.get("actual"),
                    }
                    for f in (scored.get("field_results") or [])
                    if f.get("required") and not f.get("matched")
                ],
            }
            out["submit_clicked"] = not await _never_submit_ok(page)
            out["never_submit"] = not out["submit_clicked"]
            out["ok"] = bool(
                scored.get("ok")
                and scored.get("footer_ok")
                and scored.get("spa_ok", True)
                and scored.get("listbox_ok", True)
                and out["never_submit"]
            )
            if out["submit_clicked"]:
                out["detail"] = "Submit was clicked — FAIL"
            elif not scored.get("ok"):
                out["detail"] = scored.get("detail")
            else:
                out["detail"] = "battle fill vs gold OK"

            await _hold_browser(page, hold_seconds=hold_seconds)

        await browser.close()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Battle gym production fill vs gold")
    parser.add_argument("--headed", action="store_true", help="Visible Chromium (slow_mo + hold)")
    parser.add_argument(
        "--hold-open",
        action="store_true",
        help="Keep browser open until the window is closed or Ctrl+C",
    )
    parser.add_argument(
        "--hold-seconds",
        type=int,
        default=None,
        help="Seconds to keep browser open after fill (headed default 25)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    hold = args.hold_seconds
    if args.hold_open:
        hold = -1
    result = asyncio.run(run_battle_fill(headed=args.headed, hold_seconds=hold))
    try:
        (ROOT / "battle_fill_last.json").write_text(
            json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
        )
    except Exception:
        pass
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        status = "PASS" if result.get("ok") else "FAIL"
        print(f"battle fill {status}: {result.get('detail')}")
        print(f"  form: {result.get('form_uri')}")
        print(f"  reached_review={result.get('reached_review')} never_submit={result.get('never_submit')}")
        wd = result.get("workday") or {}
        print(
            "  step={step} blocker={blocker} reason={reason} "
            "B={b} C={c} edu={edu} Q={q}".format(
                step=wd.get("workday_current_step"),
                blocker=wd.get("blocker"),
                reason=wd.get("advance_blocked_reason"),
                b=wd.get("phase_b_advanced"),
                c=wd.get("phase_c_advanced"),
                edu=wd.get("phase_c_edu_advanced"),
                q=wd.get("phase_c2_advanced"),
            )
        )
        misses = (result.get("score") or {}).get("field_results") or []
        for row in misses:
            print(
                f"  miss {row.get('key')}: expected {row.get('expected')!r} "
                f"got {row.get('actual')!r}"
            )
        dishonest = result.get("dishonest_filled") or []
        if dishonest and not result.get("ok"):
            print(f"  dishonest_filled: {dishonest!r}")
        empties = result.get("required_empty") or []
        if empties and not result.get("ok"):
            print(f"  required_empty ({len(empties)}): {empties[:8]!r}")
        open_boxes = result.get("listbox_open") or []
        if open_boxes and not result.get("ok"):
            print(f"  listbox_open: {open_boxes!r}")
        if result.get("flight_log_persist") or result.get("flight_log"):
            print(f"  flight: {result.get('flight_log_persist') or result.get('flight_log')}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
