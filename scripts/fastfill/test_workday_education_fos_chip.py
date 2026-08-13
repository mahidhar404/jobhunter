#!/usr/bin/env python3
"""Regression: Workday education Field of Study chip skip (NXP Science-Computer)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "gym/ats/cases/workday_education_fos_chip/form.html"
WRONG_FIXTURE = ROOT / "gym/ats/cases/workday_education_fos_wrong_chip/form.html"
EXPANDED_FIXTURE = ROOT / "gym/ats/cases/workday_education_fos_expanded_chip/form.html"
RELOCK_FIXTURE = ROOT / "gym/ats/cases/workday_wrong_autofill_relock/form.html"


def test_fos_arts_other_not_committed_for_cs_intent():
    from verified_select import field_of_study_committed, field_of_study_taxonomy_match

    assert not field_of_study_taxonomy_match("Computer Science", "Arts-Other")
    assert not field_of_study_committed(
        "Field of Study* Arts-Other",
        ["Computer Science"],
        dom_chip=True,
    )
    assert not field_of_study_committed(
        "Arts-Other",
        ["Computer Science", "Other"],
        dom_chip=True,
    )


def test_action_judge_wrong_autofill_fos():
    from action_judge import judge_field_action

    row = judge_field_action(
        field="education/fieldOfStudy",
        before="Arts-Other",
        after="Arts-Other",
        intent="Computer Science",
        action="fill",
    )
    assert row["verdict"] == "wrong_autofill"
    assert row["reason"] == "autofill_mismatch_intent"
    assert row["thrash"] is False


def test_supervisor_wrong_autofill_not_ok():
    from action_supervisor import ActionSupervisor

    async def _run():
        with __import__("tempfile").TemporaryDirectory() as td:
            report: dict = {"_attempt_cycle_dir": td}
            sup = ActionSupervisor(td)
            audit = await sup.audit_after_action(
                report,
                field="education/fieldOfStudy",
                field_type="FIELD_OF_STUDY",
                intent="Computer Science",
                before="Arts-Other",
                after="Arts-Other",
                action="fill",
            )
            assert audit["supervisor_verdict"] == "WRONG"
            assert audit["judge_verdict"] == "wrong_autofill"

    asyncio.run(_run())


def test_fos_taxonomy_science_computer_matches_cs():
    from verified_select import field_of_study_committed, field_of_study_taxonomy_match

    assert field_of_study_taxonomy_match("Computer Science", "Science-Computer")
    assert field_of_study_committed(
        "Field of Study* Science-Computer ×",
        ["Computer Science"],
        dom_chip=True,
    )
    assert field_of_study_committed(
        "1 item selected, Science-Computer",
        ["Computer Science"],
    )


def test_workday_wrap_text_has_chip_bare_label():
    from verified_select import workday_wrap_text_has_chip

    assert workday_wrap_text_has_chip("Field of Study* Science-Computer")
    assert not workday_wrap_text_has_chip("Field of Study* Select One")


async def _run_fos_second_pass(html: str) -> tuple[list[str], list[str]]:
    from playwright.async_api import async_playwright

    import exp_workday_selectors as wd
    from field_map import FIELD_OF_STUDY

    fill_actions: list[str] = []
    filter_types: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        async def _track_type(route, request):
            if request.method == "POST":
                pass
            await route.continue_()

        await page.set_content(html)
        values = {FIELD_OF_STUDY: "Computer Science"}
        phase: dict = {"filled": [], "missed": []}
        report: dict = {"filled": []}

        # Hook filter input to detect thrash typing
        await page.evaluate(
            """() => {
              window.__fosFilterTypes = [];
              const inp = document.getElementById('fos-filter');
              if (inp) {
                inp.addEventListener('input', () => {
                  window.__fosFilterTypes.push(inp.value);
                });
              }
            }"""
        )

        await wd._fill_education_field_of_study(page, values, phase, report=report)
        filter_types = await page.evaluate("() => window.__fosFilterTypes || []")

        for row in phase.get("filled") or []:
            mode = str(row.get("mode") or "")
            reason = str(row.get("reason") or "")
            if reason != "already_correct_skip" and mode not in ("combobox",):
                fill_actions.append(f"{mode}:{reason}")

        await browser.close()
    return fill_actions, filter_types


def test_education_fos_chip_second_pass_zero_fill():
    html = FIXTURE.read_text()
    fill_actions, filter_types = asyncio.run(_run_fos_second_pass(html))
    assert filter_types == [], f"filter thrash: {filter_types}"
    assert fill_actions == [], f"unexpected fill actions: {fill_actions}"


def test_education_fos_probe_skip_helper():
    async def _run():
        from playwright.async_api import async_playwright

        import exp_workday_selectors as wd

        html = FIXTURE.read_text()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            row = await wd._probe_fos_already_correct(
                page, ["Computer Science"], "Computer Science", {}
            )
            await browser.close()
            return row

    row = asyncio.run(_run())
    assert row is not None
    assert row.get("reason") == "already_correct_skip"
    assert "science" in str(row.get("readback") or "").lower()


def test_education_fos_empty_chip_not_skip():
    async def _run():
        from playwright.async_api import async_playwright

        import exp_workday_selectors as wd

        html = FIXTURE.read_text().replace(
            '<div id="fos-chip-wrap">',
            '<div id="fos-chip-wrap" style="display:none">',
        )
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            row = await wd._probe_fos_already_correct(
                page, ["Computer Science"], "Computer Science", {}
            )
            await browser.close()
            return row

    row = asyncio.run(_run())
    assert row is None


def test_education_fos_wrong_chip_reclaim():
    html = WRONG_FIXTURE.read_text()

    async def _run():
        from playwright.async_api import async_playwright

        import exp_workday_selectors as wd
        from field_map import FIELD_OF_STUDY

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            values = {FIELD_OF_STUDY: "Computer Science"}
            phase: dict = {"filled": [], "missed": []}
            report: dict = {"filled": []}
            await wd._fill_education_field_of_study(page, values, phase, report=report)
            chip = await page.locator("#fos-chip").inner_text()
            await browser.close()
            return chip, phase

    chip, phase = asyncio.run(_run())
    assert "science" in chip.lower() and "computer" in chip.lower(), chip
    filled = phase.get("filled") or []
    assert any(f.get("verified") for f in filled), filled


def test_education_fos_relock_fixture_reclaim():
    """Dedicated re-lock gym case: Arts-Other must reclaim, not lock-skip."""
    html = RELOCK_FIXTURE.read_text()

    async def _run():
        from playwright.async_api import async_playwright

        import exp_workday_selectors as wd
        from field_map import FIELD_OF_STUDY

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            skip = await wd._probe_fos_already_correct(
                page, ["Computer Science", "Science-Computer"], "Computer Science", {}
            )
            assert skip is None
            values = {FIELD_OF_STUDY: "Computer Science"}
            phase: dict = {"filled": [], "missed": []}
            report: dict = {"filled": []}
            await wd._fill_education_field_of_study(page, values, phase, report=report)
            chip = await page.locator("#fos-chip").inner_text()
            await browser.close()
            return chip, phase

    chip, phase = asyncio.run(_run())
    assert "science" in chip.lower() and "computer" in chip.lower(), chip
    assert any(f.get("verified") for f in (phase.get("filled") or [])), phase


def test_education_fos_alias_prefers_matching_discipline_chip():
    """Arts-Other on fieldOfStudy must not hide Science-Computer on discipline."""
    html = """<!DOCTYPE html><html><body>
    <div data-automation-id="formField-fieldOfStudy">
      <span data-automation-id="selectedItem">Arts-Other
        <button data-automation-id="deleteSelected">x</button></span>
      <input data-automation-id="fieldOfStudy" value="" />
    </div>
    <div data-automation-id="formField-discipline">
      <span data-automation-id="selectedItem">Science-Computer
        <button data-automation-id="deleteSelected">x</button></span>
      <input data-automation-id="discipline" value="" />
    </div>
    <script>
      document.querySelectorAll('[data-automation-id="deleteSelected"]').forEach(btn => {
        btn.addEventListener('click', () => btn.closest('[data-automation-id="selectedItem"]')?.remove());
      });
    </script>
    </body></html>"""

    async def _run():
        from playwright.async_api import async_playwright

        import exp_workday_selectors as wd
        from field_map import FIELD_OF_STUDY

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            chip = await wd._read_fos_formfield_display(
                page, intent="Computer Science", candidates=["Computer Science"]
            )
            skip = await wd._probe_fos_already_correct(
                page, ["Computer Science"], "Computer Science", {}
            )
            assert skip is None, "must not skip while Arts-Other remains on fieldOfStudy"
            phase: dict = {"filled": [], "missed": []}
            await wd._fill_education_field_of_study(
                page, {FIELD_OF_STUDY: "Computer Science"}, phase, report={}
            )
            fos_wrap = page.locator('[data-automation-id="formField-fieldOfStudy"]')
            fos_txt = (await fos_wrap.inner_text()).lower()
            skip_after = await wd._probe_fos_already_correct(
                page, ["Computer Science"], "Computer Science", {}
            )
            await browser.close()
            return chip, skip, skip_after, fos_txt, phase

    chip, skip, skip_after, fos_txt, phase = asyncio.run(_run())
    assert "science" in (chip or "").lower(), chip
    assert skip is None
    assert "arts-other" not in fos_txt, fos_txt
    assert skip_after is not None
    assert skip_after.get("reason") == "already_correct_skip"
    assert all(
        "arts-other" not in str(f.get("readback") or "").lower()
        for f in (phase.get("filled") or [])
    ), phase


def test_education_fos_dual_alias_clears_wrong_wrap():
    """Dual-alias gym: Arts-Other on fieldOfStudy + Science-Computer on discipline."""
    dual_fixture = ROOT / "gym/ats/cases/workday_education_fos_dual_alias/form.html"
    if not dual_fixture.is_file():
        pytest = __import__("pytest")
        pytest.skip("dual-alias fixture missing")
    html = dual_fixture.read_text()

    async def _run():
        from playwright.async_api import async_playwright

        import exp_workday_selectors as wd
        from field_map import FIELD_OF_STUDY

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            assert await wd._any_fos_mismatched_chip(
                page, ["Computer Science", "Science-Computer"], "Computer Science"
            )
            values = {FIELD_OF_STUDY: "Computer Science"}
            phase: dict = {"filled": [], "missed": []}
            await wd._fill_education_field_of_study(page, values, phase, report={})
            fos_txt = (
                await page.locator('[data-automation-id="formField-fieldOfStudy"]').inner_text()
            ).lower()
            disc_txt = (
                await page.locator('[data-automation-id="formField-discipline"]').inner_text()
            ).lower()
            await browser.close()
            return fos_txt, disc_txt, phase

    fos_txt, disc_txt, phase = asyncio.run(_run())
    assert "arts-other" not in fos_txt, fos_txt
    assert "science" in disc_txt and "computer" in disc_txt, disc_txt
    assert not any(
        "arts-other" in str(f.get("readback") or "").lower()
        for f in (phase.get("filled") or [])
    ), phase


def test_degree_display_matches_master_intent():
    from verified_select import (
        degree_display_matches_intent,
        looks_like_workday_internal_id,
    )

    assert looks_like_workday_internal_id("3fa85f64-5717-4562-b3fc-2c963f66afa6")
    assert not looks_like_workday_internal_id("Master's Degree")
    assert degree_display_matches_intent("Master's Degree", "Master's Degree")
    assert degree_display_matches_intent("Master of Science (M.S.)", "Master's Degree")
    assert degree_display_matches_intent("Degree*\nMaster", "Master's Degree")
    assert not degree_display_matches_intent(
        "3fa85f64-5717-4562-b3fc-2c963f66afa6", "Master's Degree"
    )


def test_degree_master_label_field_done():
    from field_done import field_is_done_from_readback
    from field_map import DEGREE

    v = field_is_done_from_readback(
        "Degree*\nMaster",
        {"type": DEGREE},
        "Master's Degree",
    )
    assert v.ok, v.reason


def test_education_fos_wrong_chip_listbox_closed():
    """Wrong chip reclaim must close listbox (no advance block)."""
    html = WRONG_FIXTURE.read_text()

    async def _run():
        from playwright.async_api import async_playwright

        import exp_workday_selectors as wd
        from field_map import FIELD_OF_STUDY
        from verified_select import listbox_still_open

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            # Gym loads Arts-Other + Expanded listbox (1212Z failure shape)
            assert await listbox_still_open(page)
            values = {FIELD_OF_STUDY: "Computer Science"}
            phase: dict = {"filled": [], "missed": []}
            report: dict = {"filled": []}
            await wd._fill_education_field_of_study(page, values, phase, report=report)
            assert not await listbox_still_open(page), "listbox must close after reclaim"
            chip = await page.locator("#fos-chip").inner_text()
            await browser.close()
            return chip, phase

    chip, phase = asyncio.run(_run())
    assert "science" in chip.lower() and "computer" in chip.lower(), chip
    assert any(f.get("verified") for f in (phase.get("filled") or [])), phase


def test_education_fos_expanded_chip_settle_closes_listbox():
    """1301Z shape: Science-Computer chip committed but listbox Expanded → force-close."""
    html = EXPANDED_FIXTURE.read_text()

    async def _run():
        from playwright.async_api import async_playwright

        from verified_select import (
            fos_chip_committed_on_page,
            fos_widget_expanded,
            listbox_still_open,
            settle_before_advance,
            settle_fos_widget_until_closed,
        )

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            assert await listbox_still_open(page), "fixture must start with open listbox"
            assert await fos_widget_expanded(page)
            assert await fos_chip_committed_on_page(
                page, ["Computer Science", "Science-Computer"], "Computer Science"
            )
            chip_before = (await page.locator("#fos-chip").inner_text() or "").strip()
            settled = await settle_fos_widget_until_closed(
                page, candidates=["Computer Science", "Science-Computer"], intent="Computer Science"
            )
            assert settled, "committed chip must settle even with stale Expanded chrome"
            assert not await listbox_still_open(page), "listbox must close after settle"
            assert not await fos_widget_expanded(page), "aria-expanded must clear"
            chip_after = (await page.locator("#fos-chip").inner_text() or "").strip()
            assert "science" in chip_after.lower() and "computer" in chip_after.lower()
            assert chip_before.split()[0:1] == chip_after.split()[0:1] or (
                "science-computer" in chip_before.lower()
                and "science-computer" in chip_after.lower()
            ), (chip_before, chip_after)
            report: dict = {
                "fill_values": {"FIELD_OF_STUDY": "Computer Science", "MAJOR": "Computer Science"}
            }
            detail = await settle_before_advance(page, report)
            assert detail.get("settled"), detail
            assert not report.get("listbox_open"), report
            await browser.close()

    asyncio.run(_run())


_FOS_SKIP_NO_MATCH_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>FoS skip no-match</title></head>
<body>
  <div data-automation-id="formField-fieldOfStudy" id="fos-field">
    <label>Field of Study</label>
    <input id="fos-filter" type="text" role="combobox" aria-expanded="true"
           data-automation-id="educationSection_fieldOfStudy" value="" />
    <div id="fos-menu" role="listbox">
      <div role="option" data-automation-id="promptOption">Biology</div>
      <div role="option" data-automation-id="promptOption">Arts-Other</div>
    </div>
  </div>
  <div data-automation-id="formField-skills" id="skills">
    <span>Skills</span>
    <div data-automation-id="promptOption" class="skill-chip suggested-skill" role="option">Python</div>
    <div data-automation-id="promptOption" class="skill-chip suggested-skill" role="option">Leadership</div>
  </div>
  <button data-automation-id="bottom-navigation-next-button">Save and Continue</button>
  <script>
    const filter = document.getElementById('fos-filter');
    const menu = document.getElementById('fos-menu');
    document.addEventListener('click', (e) => {
      if (!document.getElementById('fos-field').contains(e.target)) {
        menu.style.display = 'none';
        filter.setAttribute('aria-expanded', 'false');
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        menu.style.display = 'none';
        filter.setAttribute('aria-expanded', 'false');
      }
    });
  </script>
</body></html>
"""


def test_fos_skip_allows_advance_helper():
    from verified_select import fos_skip_allows_advance

    assert fos_skip_allows_advance(None) is False
    assert fos_skip_allows_advance({}) is False
    assert fos_skip_allows_advance({"fos_skip": True}) is True
    assert fos_skip_allows_advance(
        {
            "filled": [
                {
                    "type": "FIELD_OF_STUDY",
                    "automation_id": "education/educationSection_fieldOfStudy",
                    "reason": "no_matching_option",
                    "fos_skip": True,
                    "optional_miss": True,
                }
            ]
        }
    ) is True
    assert fos_skip_allows_advance(
        {"filled": [{"type": "FIELD_OF_STUDY", "verified": True, "reason": "fos_chip_match"}]}
    ) is False


def test_education_fos_no_match_skips_and_closes_listbox():
    """1045Z: no matching FoS option → skip, close listbox, do not bind Skills."""

    async def _run():
        from playwright.async_api import async_playwright

        import exp_workday_selectors as wd
        from field_map import FIELD_OF_STUDY
        from verified_select import (
            fos_skip_allows_advance,
            fos_widget_expanded,
            listbox_still_open,
            settle_before_advance,
        )

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(_FOS_SKIP_NO_MATCH_HTML)
            assert await listbox_still_open(page), "fixture starts with FoS listbox open"
            values = {FIELD_OF_STUDY: "Computer Science"}
            phase: dict = {"filled": [], "missed": []}
            report: dict = {"filled": [], "fill_values": values}
            await wd._fill_education_field_of_study(page, values, phase, report=report)
            assert not await fos_widget_expanded(page), "FoS chrome must close after skip"
            assert not await listbox_still_open(page), "listbox must close after FoS skip"
            skills = await page.locator("#skills").inner_text()
            assert "Python" in skills and "Leadership" in skills
            fos_val = await page.locator("#fos-filter").input_value()
            assert not (fos_val or "").strip() or "python" not in fos_val.lower()
            assert fos_skip_allows_advance(report), report
            rows = list(phase.get("filled") or []) + list(phase.get("missed") or [])
            assert any(
                r.get("fos_skip") or r.get("reason") == "no_matching_option"
                for r in rows
            ), (phase, report)
            detail = await settle_before_advance(page, report)
            assert detail.get("settled") or not detail.get("still_open"), detail
            assert report.get("advance_blocked_reason") != "listbox_still_open"
            assert not report.get("listbox_open")
            await browser.close()

    asyncio.run(_run())


def test_locked_fos_skips_major_alias_without_retype():
    """Once FoS locked, education fill must lock_skip Major aliases (no overwrite)."""
    html = FIXTURE.read_text()

    async def _run():
        from playwright.async_api import async_playwright

        import exp_workday_selectors as wd
        from field_lock import attach_field_locks, lock_verified_field
        from field_map import FIELD_OF_STUDY

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            report: dict = {"filled": []}
            attach_field_locks(report)
            lock_verified_field(
                report,
                field_type=FIELD_OF_STUDY,
                automation_id="education/fieldOfStudy",
                readback="Science-Computer",
                via="prior_layer",
            )
            chip0 = (await page.locator("#fos-chip").inner_text() or "").strip()
            phase: dict = {"filled": [], "missed": []}
            await wd._fill_education_field_of_study(
                page,
                {FIELD_OF_STUDY: "Computer Science"},
                phase,
                report=report,
            )
            chip1 = (await page.locator("#fos-chip").inner_text() or "").strip()
            await browser.close()
            return chip0, chip1, phase

    chip0, chip1, phase = asyncio.run(_run())
    assert chip0 == chip1, (chip0, chip1)
    assert any(
        f.get("skipped_locked") or f.get("reason") == "field_locked_skip"
        for f in (phase.get("filled") or [])
    ), phase


def test_battle_gym_reclaims_major_keeps_discipline():
    """Battle gym: Major Arts-Other must become Science-Computer; Discipline stays."""
    battle = ROOT / "gym/ats/cases/workday_battle_multipage/form.html"
    html = battle.read_text()

    async def _run():
        from playwright.async_api import async_playwright

        import exp_workday_selectors as wd
        from field_map import FIELD_OF_STUDY

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            await page.evaluate("() => window.__battleGym.showStep(2)")
            phase: dict = {"filled": [], "missed": []}
            await wd._fill_education_field_of_study(
                page,
                {FIELD_OF_STUDY: "Computer Science"},
                phase,
                report={"filled": []},
            )
            major = await page.evaluate(
                "() => (document.querySelector('#major-chip')||{}).getAttribute?.('data-committed') || ''"
            )
            disc = await page.evaluate(
                "() => (document.querySelector('#disc-chip')||{}).getAttribute?.('data-committed') || ''"
            )
            await browser.close()
            return major, disc, phase

    major, disc, phase = asyncio.run(_run())
    assert major in ("Science-Computer", "Computer Science"), (major, phase)
    assert disc in ("Science-Computer", "Computer Science"), (disc, phase)


def test_value_matches_fos_taxonomy():
    from verified_select import value_matches_readback

    assert value_matches_readback("Computer Science", "Science-Computer")
    assert not value_matches_readback("Computer Science", "Arts-Other")


def test_gate_degree_hash_wrong_suppressed_when_master_ok():
    """DEGREE WRONG:empty_to_filled on hash write must not count if Master UI OK."""
    import tempfile

    from reliability_gate import _field_resolved_ok_in_report, _wrong_values_from_steps

    report = {
        "filled": [
            {
                "type": "select_one:Degree",
                "value": "Master's Degree",
                "readback": "Master",
                "ok": True,
                "verified": True,
                "automation_id": "select_one:Degree",
            },
            {
                "type": "education/degree",
                "value": "Master's Degree",
                "readback": "Master's Degree",
                "ok": True,
                "verified": True,
                "automation_id": "education/degree",
            },
        ],
        "leftovers": [
            {"label": "formField-degree", "reason": "already_correct_skip"},
        ],
    }
    assert _field_resolved_ok_in_report("DEGREE", report)
    log_text = (
        'action_audit | DEGREE (formField-degree) "" → "8ad570cd421e10e1a4821a0d8ffc2e82" '
        "via=workday_automation_id reason=WRONG:empty_to_filled\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        f.write(log_text)
        log_path = Path(f.name)
    try:
        wrong = _wrong_values_from_steps(None, log_path, report)
        assert wrong == [], wrong
    finally:
        log_path.unlink(missing_ok=True)


if __name__ == "__main__":
    test_fos_arts_other_not_committed_for_cs_intent()
    test_action_judge_wrong_autofill_fos()
    test_supervisor_wrong_autofill_not_ok()
    test_fos_taxonomy_science_computer_matches_cs()
    test_workday_wrap_text_has_chip_bare_label()
    test_education_fos_chip_second_pass_zero_fill()
    test_education_fos_probe_skip_helper()
    test_education_fos_empty_chip_not_skip()
    test_education_fos_wrong_chip_reclaim()
    test_education_fos_relock_fixture_reclaim()
    test_education_fos_alias_prefers_matching_discipline_chip()
    test_education_fos_dual_alias_clears_wrong_wrap()
    test_degree_display_matches_master_intent()
    test_degree_master_label_field_done()
    test_education_fos_wrong_chip_listbox_closed()
    test_education_fos_expanded_chip_settle_closes_listbox()
    test_fos_skip_allows_advance_helper()
    test_education_fos_no_match_skips_and_closes_listbox()
    test_locked_fos_skips_major_alias_without_retype()
    test_battle_gym_reclaims_major_keeps_discipline()
    test_value_matches_fos_taxonomy()
    test_gate_degree_hash_wrong_suppressed_when_master_ok()
    print("ok")
