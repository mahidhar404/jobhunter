#!/usr/bin/env python3
"""CLI runner for the offline ATS gym."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from score import score_page  # noqa: E402


def list_cases() -> list[str]:
    if not CASES_DIR.is_dir():
        return []
    return sorted(p.name for p in CASES_DIR.iterdir() if p.is_dir() and (p / "form.html").is_file())


def load_case(case_id: str) -> dict[str, Any]:
    case_dir = CASES_DIR / case_id
    if not case_dir.is_dir():
        raise FileNotFoundError(f"unknown case: {case_id}")
    html = (case_dir / "form.html").read_text(encoding="utf-8")
    gold = json.loads((case_dir / "gold.json").read_text(encoding="utf-8"))
    meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))
    return {"id": case_id, "html": html, "gold": gold, "meta": meta, "dir": case_dir}


async def _run_case(page, case_id: str, *, mini_fill: bool = False) -> dict[str, Any]:
    case = load_case(case_id)
    await page.set_content(case["html"], wait_until="domcontentloaded")
    await page.wait_for_timeout(100)

    if mini_fill and case_id == "salary_blank_skip":
        await page.fill("#salary-input", "95000")

    result = await score_page(page, case["gold"])
    return {
        "id": case_id,
        "meta": case["meta"],
        "score": result,
        "mini_fill": mini_fill,
    }


async def _run_fill_score_cases() -> list[dict[str, Any]]:
    """Fill key gym cases then score against gold (commit verify)."""
    from adversarial import (
        test_fill_gh_howheard_priority,
        test_fill_gh_race_decline,
        test_fill_gh_react_select_school,
        test_fill_workday_how_heard_hierarchical_chip,
    )

    results: list[dict[str, Any]] = []
    fill_tests = [
        ("gh_race_decline", test_fill_gh_race_decline),
        ("gh_react_select", test_fill_gh_react_select_school),
        ("gh_howheard_multiselect", test_fill_gh_howheard_priority),
        ("workday_how_heard_hierarchical_chip", test_fill_workday_how_heard_hierarchical_chip),
    ]
    for case_id, fn in fill_tests:
        try:
            fn()
            results.append({"id": case_id, "phase": "fill", "ok": True})
        except Exception as e:
            results.append(
                {
                    "id": case_id,
                    "phase": "fill",
                    "ok": False,
                    "detail": str(e)[:200],
                }
            )
    return results


async def _self_test_async() -> dict[str, Any]:
    from playwright.async_api import async_playwright

    cases = list_cases()
    if not cases:
        raise RuntimeError("no gym cases found")

    results: list[dict[str, Any]] = []
    ok = True

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        for case_id in cases:
            empty_result = await _run_case(page, case_id, mini_fill=False)
            empty_score = empty_result["score"]
            # Empty forms should generally fail gold (except edge cases)
            if empty_score.get("ok"):
                # midwizard with empty required is expected to fail overall
                pass  # ok if some pass, requirement says NOT all pass is ok
            results.append(
                {
                    "id": case_id,
                    "phase": "empty",
                    "ok": True,  # empty scoring ran without error
                    "score_ok": empty_score.get("ok"),
                    "detail": empty_score.get("detail"),
                }
            )

        # Deterministic mini-fill smoke for salary_blank_skip
        fill_result = await _run_case(page, "salary_blank_skip", mini_fill=True)
        fill_score = fill_result["score"]
        salary_field = next(
            (f for f in fill_score.get("field_results", []) if f.get("key") == "salary"),
            None,
        )
        if not fill_score.get("ok"):
            ok = False
            results.append(
                {
                    "id": "salary_blank_skip",
                    "phase": "mini_fill",
                    "ok": False,
                    "detail": fill_score.get("detail"),
                }
            )
        elif not salary_field or not salary_field.get("matched"):
            ok = False
            results.append(
                {
                    "id": "salary_blank_skip",
                    "phase": "mini_fill",
                    "ok": False,
                    "detail": "salary field did not match after mini-fill",
                }
            )
        else:
            results.append(
                {
                    "id": "salary_blank_skip",
                    "phase": "mini_fill",
                    "ok": True,
                    "detail": fill_score.get("detail"),
                }
            )

        await browser.close()

    # Browser fill+score smoke (uses adversarial fill tests)
    fill_results = await _run_fill_score_cases()
    for fr in fill_results:
        results.append(fr)
        if not fr.get("ok"):
            ok = False

    # Adversarial unit + guard suite
    try:
        from adversarial import run_adversarial_suite

        adv = run_adversarial_suite()
        results.append(
            {
                "id": "_adversarial",
                "phase": "adversarial",
                "ok": adv.get("ok"),
                "passed": adv.get("passed"),
                "failed": adv.get("failed"),
                "detail": [
                    r for r in (adv.get("results") or []) if not r.get("ok")
                ],
            }
        )
        if not adv.get("ok"):
            ok = False
    except Exception as e:
        ok = False
        results.append(
            {"id": "_adversarial", "phase": "adversarial", "ok": False, "detail": str(e)[:200]}
        )

    # Four-dimension detection matrix
    try:
        from detection_matrix import run_detection_matrix

        det = run_detection_matrix()
        results.append(
            {
                "id": "_detection_matrix",
                "phase": "detection",
                "ok": det.get("ok"),
                "passed": det.get("passed"),
                "failed": det.get("failed"),
                "dimensions": det.get("dimensions"),
                "detail": [r for r in (det.get("results") or []) if not r.get("ok")],
            }
        )
        if not det.get("ok"):
            ok = False
    except Exception as e:
        ok = False
        results.append(
            {"id": "_detection_matrix", "phase": "detection", "ok": False, "detail": str(e)[:200]}
        )

    # Sanity: not every empty case should pass (exclude intentional pre-answered fixtures)
    empty_pass_cases = {"wd_radio_aria_checked", "workday_auth_gate", "workday_auth_gate_direct"}
    scorable = [c for c in cases if c not in empty_pass_cases]
    empty_pass_count = sum(
        1
        for r in results
        if r.get("phase") == "empty"
        and r.get("id") not in empty_pass_cases
        and r.get("score_ok")
    )
    if scorable and empty_pass_count == len(scorable):
        ok = False
        results.append(
            {
                "id": "_sanity",
                "phase": "empty",
                "ok": False,
                "detail": "all empty cases passed gold — expected at least some failures",
            }
        )

    return {"ok": ok, "cases": results}


def run_ats_gym(smoke: bool = True) -> dict[str, Any]:
    """Run gym self-test; exported for improvement_cycle integration."""
    import asyncio

    if not smoke:
        return {"ok": True, "cases": [], "skipped": True}

    return asyncio.run(_self_test_async())


async def _run_single_case_async(case_id: str) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        result = await _run_case(page, case_id, mini_fill=False)
        await browser.close()
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline ATS gym runner")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--case", type=str, default="")
    args = ap.parse_args()

    if args.list:
        for cid in list_cases():
            meta_path = CASES_DIR / cid / "meta.json"
            desc = ""
            if meta_path.is_file():
                desc = json.loads(meta_path.read_text()).get("description", "")
            print(f"{cid}\t{desc}")
        return 0

    if args.self_test:
        result = run_ats_gym(smoke=True)
        if not result.get("ok"):
            print("self-test FAILED:", json.dumps(result, indent=2))
            return 1
        print("ats gym self-test OK")
        return 0

    if args.case:
        import asyncio

        result = asyncio.run(_run_single_case_async(args.case))
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("score", {}).get("ok") else 1

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
