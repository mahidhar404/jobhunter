#!/usr/bin/env python3
"""Headed live probe: Walmart how-heard hierarchy + screenshots (dummy, never submit).

Clears any existing chip, runs fill_hierarchical_how_heard, saves screenshots at:
  how_heard_open, after_subsection, after_source_chip, before_hold_decision.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

URL = (
    "https://walmart.wd504.myworkdayjobs.com/WalmartExternal/job/"
    "USA-ISD-Office---DGTC-AR-BENTONVILLE-Home-Office/"
    "XMLNAME--USA--Senior--Machine-Learning-Engineer_R-2593485-1"
)
OUT = (
    ROOT
    / "skyvern_runtime"
    / "real_job_results"
    / "walmart_how_heard_verify_20260810"
)


async def _shot(page, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    await page.screenshot(path=str(path), full_page=True)
    print(f"[shot] {path}", flush=True)
    return path


async def _goto_contact(page) -> None:
    from exp_workday_selectors import workday_two_phase_on_page
    from run_identity import prepare_dummy_run

    identity = prepare_dummy_run()
    values = dict(identity.fill_values or {})
    # Reach contact via Workday phase A/B only; never submit
    await workday_two_phase_on_page(
        page,
        values,
        click_create_account=True,
        do_apply_clicks=True,
        resume_pdf=identity.resume_pdf,
        step_report={"platform": "workday", "_step_report": {}},
    )


async def main() -> None:
    from playwright.async_api import async_playwright
    from fill_verify import how_heard_category_candidates, how_heard_leaf_candidates
    from page_progress import (
        attach_footer_primary,
        may_enter_review_hold,
        probe_footer_primary,
    )
    from verified_select import (
        fill_hierarchical_how_heard,
        how_heard_source_committed,
        settle_open_listbox,
    )

    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)
        # CAPTCHA? Wait for human
        try:
            from captcha_pause import wait_for_human_captcha_if_needed

            await wait_for_human_captcha_if_needed(
                page, headed=True, captcha_wait=True, timeout_s=300
            )
        except Exception as e:
            print(f"[captcha] skip/wait helper: {e}", flush=True)

        print("[probe] driving to My Information (dummy, never submit)…", flush=True)
        try:
            await _goto_contact(page)
        except Exception as e:
            print(f"[probe] workday drive error (continuing if on form): {e}", flush=True)

        await _shot(page, "live_before_how_heard")

        inp = page.locator(
            'input[name="source--source"], '
            '[data-automation-id="formField-source"] input, '
            '[data-automation-id="multiSelectContainer"] input'
        ).first
        if not await inp.count():
            print("[probe] how-heard input not found — blocker", flush=True)
            await _shot(page, "live_blocker_no_how_heard")
            meta = {"ok": False, "blocker": "how_heard_input_missing", "url": page.url}
            (OUT / "hierarchy_live_meta.json").write_text(json.dumps(meta, indent=2))
            await browser.close()
            return

        # Clear existing chip so hierarchy is exercised (not keep-path)
        try:
            wrap = page.locator(
                '[data-automation-id="formField-source"], '
                '[data-automation-id="multiSelectContainer"]'
            ).first
            clear_btn = wrap.locator(
                'button[aria-label*="Remove" i], button[aria-label*="Clear" i], '
                '[data-automation-id*="delete" i], [data-automation-id*="remove" i]'
            ).first
            if await clear_btn.count() and await clear_btn.is_visible(timeout=800):
                await clear_btn.click(timeout=2000)
                await page.wait_for_timeout(400)
            # Also click chip X if present
            chip_x = wrap.locator('[aria-label*="Remove" i], button').first
            for _ in range(3):
                try:
                    xs = wrap.locator("div, span, button").filter(has_text="×")
                    if await xs.count():
                        await xs.first.click(timeout=1000)
                        await page.wait_for_timeout(300)
                except Exception:
                    break
        except Exception as e:
            print(f"[probe] clear chip: {e}", flush=True)

        await inp.click(timeout=3000)
        await page.wait_for_timeout(400)
        await _shot(page, "how_heard_open")

        hier = await fill_hierarchical_how_heard(
            page,
            inp,
            leaf_candidates=how_heard_leaf_candidates({"HOW_HEARD": "Indeed"}),
            category_candidates=how_heard_category_candidates(
                {"HOW_HEARD": "Internet job board"}
            ),
            wait_ms=600,
        )
        print("[probe] hierarchical result:", json.dumps(hier, default=str)[:500], flush=True)
        if hier.get("subsection"):
            await _shot(page, "after_subsection")
        await _shot(page, "after_source_chip")

        wrap_txt = ""
        try:
            wrap_txt = await page.locator(
                '[data-automation-id="formField-source"]'
            ).first.inner_text()
        except Exception:
            pass
        committed = how_heard_source_committed(wrap_txt, ["Indeed", "LinkedIn"])
        try:
            await settle_open_listbox(page)
        except Exception:
            pass

        footer = await probe_footer_primary(page, {})
        report = {
            "platform": "workday",
            "coverage_path": "workday_multipage",
            "workday_current_step": "contact",
            "workday": {"phase_b": {"present": True}, "phase_e": {}},
            "workday_wizard_progress": (
                "Autofill completed My Information current My Experience "
                "Application Questions Voluntary Disclosures Review"
            ),
        }
        if footer.get("label"):
            attach_footer_primary(
                report,
                kind=footer.get("kind") or "UNKNOWN",
                label=footer.get("label") or "",
            )
        hold_ok = may_enter_review_hold(report)
        await _shot(page, "before_hold_decision")

        meta = {
            "ok": bool(hier.get("ok") and committed),
            "hierarchical": hier,
            "wrap_text": (wrap_txt or "")[:200],
            "committed_chip": committed,
            "footer": footer,
            "may_enter_review_hold": hold_ok,
            "url": page.url,
            "never_submit": True,
        }
        (OUT / "hierarchy_live_meta.json").write_text(json.dumps(meta, indent=2, default=str))
        print("[probe] meta:", json.dumps(meta, indent=2, default=str)[:800], flush=True)
        await page.wait_for_timeout(5000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
