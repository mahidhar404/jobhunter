#!/usr/bin/env python3
"""Leftover Flash stays ON for Workday/NXP (inpage + Skyvern).

Dummy-only. No browser. Never submit. skip_flash_on_workday is a no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def test_skip_flash_on_workday_platform():
    from flash_leftovers import skip_flash_on_workday

    assert skip_flash_on_workday({"platform": "workday"}) is False
    assert skip_flash_on_workday(platform="workday") is False
    assert skip_flash_on_workday({"platform": "greenhouse"}) is False
    assert skip_flash_on_workday(platform="greenhouse") is False
    assert skip_flash_on_workday({"platform": "ashby"}) is False
    assert skip_flash_on_workday(platform="ashby") is False
    assert skip_flash_on_workday(
        {"url": "https://jobs.ashbyhq.com/bjak/x/application"}
    ) is False


def test_skip_flash_on_workday_two_phase():
    from flash_leftovers import skip_flash_on_workday

    assert (
        skip_flash_on_workday({"experiment": "workday_two_phase", "platform": "unknown"})
        is False
    )
    assert skip_flash_on_workday({"experiment": "generic_dom"}) is False


def test_skip_flash_on_workday_url():
    from flash_leftovers import skip_flash_on_workday

    assert (
        skip_flash_on_workday(
            url="https://nxp.wd3.myworkdayjobs.com/en-US/careers/job/x/apply"
        )
        is False
    )
    assert skip_flash_on_workday({"url": "https://boards.greenhouse.io/acme/jobs/1"}) is False


def test_run_fill_visible_keeps_flash_on_workday():
    """run_fill_visible.sh must NOT force FASTFILL_FLASH_LEFTOVERS=0 on Workday."""
    src = (HERE / "run_fill_visible.sh").read_text(encoding="utf-8")
    # Optional disable still exists
    assert "FASTFILL_FLASH_LEFTOVERS=0" in src
    # Default remains ON
    assert "FASTFILL_FLASH_LEFTOVERS:-1" in src
    # Must not force Flash off based on Workday host
    assert "FLASH_ENV=0" not in src
    assert "export FASTFILL_FLASH_LEFTOVERS=0" not in src


def test_leftover_rows_drop_done_country_phone():
    """0842Z dual-oracle: filled countryPhoneCode must drop live_required_empty leftover."""
    from flash_leftovers import _leftover_rows

    report = {
        "filled": [
            {
                "type": "countryPhoneCode",
                "automation_id": "countryPhoneCode",
                "ok": True,
                "verified": True,
                "value": "United States (+1)",
                "readback": "Country Phone Code* 1 item selected, United States (+1)",
            }
        ],
        "leftovers": [
            {
                "label": "phonenumber--countryphonecode",
                "reason": "live_required_empty:empty_required_input",
                "flash_candidate": True,
            }
        ],
    }
    assert _leftover_rows(report) == []


def test_run_flash_leftovers_does_not_skip_workday():
    """Skyvern path must not short-circuit on workday_two_phase / NXP URL."""
    import asyncio

    from flash_leftovers import run_flash_leftovers

    report = {
        "platform": "workday",
        "experiment": "workday_two_phase",
        "url": "https://nxp.wd3.myworkdayjobs.com/careers",
        "filled": [],
        "leftovers": [{"label": "Salary", "type": "SALARY", "flash_candidate": True}],
        "dummy": True,
        "never_submit": True,
    }

    async def _run():
        return await run_flash_leftovers(
            report["url"], report, invoke=False, max_steps=5
        )

    payload = asyncio.run(_run())
    assert payload.get("invoked") is False
    assert payload.get("skipped_reason") != "workday_two_phase"
    assert payload.get("never_submit") is True
    assert payload.get("submit_clicked") is not True
    leftovers = payload.get("leftovers") or []
    assert any(str(r.get("type") or "").upper() == "SALARY" for r in leftovers)


def test_inpage_flash_does_not_skip_workday():
    """run_inpage_flash_leftovers must not early-return on Workday."""
    import inspect

    from fast_fill import run_inpage_flash_leftovers

    src = inspect.getsource(run_inpage_flash_leftovers)
    assert "skip_flash_on_workday" not in src
    assert "workday_two_phase" not in src
    assert "FLASH_SKIP_WORKDAY_REASON" not in src


def main() -> int:
    test_skip_flash_on_workday_platform()
    test_skip_flash_on_workday_two_phase()
    test_skip_flash_on_workday_url()
    test_run_fill_visible_keeps_flash_on_workday()
    test_leftover_rows_drop_done_country_phone()
    test_run_flash_leftovers_does_not_skip_workday()
    test_inpage_flash_does_not_skip_workday()
    print("test_flash_workday_skip: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
