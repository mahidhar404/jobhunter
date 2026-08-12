#!/usr/bin/env python3
"""Regression tests for the Capco cycle residual fill issues.

Covers the residuals closed after the a63fdadb hunt:
  1. RACE decline commits (never invents a race) across option wordings.
  2. HOW_HEARD / Job Board react-select commits when the dummy string is not an
     exact option (must alias to "Job Board" / "Indeed", not zero the menu).
  3. SPONSORSHIP phantom (no select__control) is suppressed when sponsorship
     intent is already satisfied — never thrashes.
  4. Ashby unclassified Yes/No experience-years screening is answered honestly
     from dummy resume truth (3.0 yrs), never left blank, never invents EEO.
  5. Privacy Notice Acknowledgement classifies to TERMS_CONSENT (checkbox → Yes).

Dummy-only. Never submits. Never invents EEO.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Real Greenhouse react-select markup: label.select__label + .select__container >
# .select__control (placeholder); menu (.select__menu) mounts on open.
_GH_SELECT_TEMPLATE = """
<!DOCTYPE html><html><head><meta charset=utf-8><style>
.select__menu {{ position:absolute; background:#fff; border:1px solid #ccc; }}
.select__option {{ padding:6px; cursor:pointer; }}
.select__multi-value {{ display:inline-block; background:#e6f0ff; margin:2px; padding:2px 6px; }}
</style></head><body>
<div class="field">
  <label class="select__label" id="lbl">{label}</label>
  <div class="select__container">
    <div class="select__control" tabindex="0">
      <div class="select__value-container">
        <div class="select__placeholder">Select...</div>
        <input class="select__input" role="combobox" aria-expanded="false" />
      </div>
    </div>
  </div>
</div>
<script>
(function() {{
  var MULTI = {multi};
  var OPTIONS = {options};
  var control = document.querySelector('.select__control');
  var container = document.querySelector('.select__container');
  var valueContainer = document.querySelector('.select__value-container');
  var input = document.querySelector('.select__input');
  var menu = null;
  var selected = [];
  function render() {{
    valueContainer.querySelectorAll('.select__single-value,.select__multi-value').forEach(function(n){{n.remove();}});
    var ph = valueContainer.querySelector('.select__placeholder');
    if (selected.length === 0) {{ if (ph) ph.style.display=''; return; }}
    if (ph) ph.style.display='none';
    if (MULTI) {{
      selected.forEach(function(s){{
        var chip = document.createElement('div');
        chip.className='select__multi-value';
        var lab = document.createElement('div');
        lab.className='select__multi-value__label';
        lab.textContent = s;
        chip.appendChild(lab);
        valueContainer.insertBefore(chip, input);
      }});
    }} else {{
      var sv = document.createElement('div');
      sv.className='select__single-value';
      sv.textContent = selected[0];
      valueContainer.insertBefore(sv, input);
    }}
  }}
  function filtered() {{
    var q = (input.value||'').toLowerCase();
    return OPTIONS.filter(function(o){{ return o.toLowerCase().indexOf(q) >= 0; }});
  }}
  function openMenu() {{
    closeMenu();
    menu = document.createElement('div');
    menu.className='select__menu';
    filtered().forEach(function(o, i){{
      var d = document.createElement('div');
      d.className='select__option';
      d.id='react-select-2-option-'+i;
      d.textContent=o;
      d.addEventListener('mousedown', function(e){{
        e.preventDefault();
        if (MULTI) {{ if (selected.indexOf(o)<0) selected.push(o); }}
        else {{ selected=[o]; }}
        input.value='';
        render();
        closeMenu();
      }});
      menu.appendChild(d);
    }});
    container.appendChild(menu);
    control.setAttribute('aria-expanded','true');
  }}
  function closeMenu() {{ if (menu) {{ menu.remove(); menu=null; }} control.setAttribute('aria-expanded','false'); }}
  control.addEventListener('click', function(){{ if (menu) closeMenu(); else openMenu(); }});
  input.addEventListener('input', function(){{ if (menu) openMenu(); }});
  input.addEventListener('focus', function(){{ openMenu(); }});
}})();
</script></body></html>
"""


async def _load_gh_select(page, *, label, options, multi=False):
    html = _GH_SELECT_TEMPLATE.format(
        label=label, options=json.dumps(options), multi="true" if multi else "false"
    )
    await page.set_content(html, wait_until="domcontentloaded")
    await page.wait_for_timeout(60)


async def _browser_cases():
    from playwright.async_api import async_playwright
    from gh_select import fill_gh_select, is_decline_like_alias

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        # --- RACE decline commits across option wordings, never invents a race ---
        race_menus = [
            ["Asian", "Black or African American", "White", "Two or More Races", "I don't wish to answer"],
            ["Asian", "White", "I don\u2019t wish to answer"],  # curly apostrophe
            ["Asian", "White", "Hispanic or Latino", "Prefer not to disclose"],
            ["Asian", "White", "Do not wish to disclose"],
            ["Asian", "White", "Decline to self-identify"],
        ]
        for opts in race_menus:
            await _load_gh_select(page, label="Race / Ethnicity", options=opts)
            r = await fill_gh_select(
                page, "Race / Ethnicity", "Decline to self identify", field_type="RACE"
            )
            assert r.get("ok"), f"RACE decline failed to commit on {opts}: {r}"
            shown = str(r.get("shown") or "")
            assert is_decline_like_alias(shown), f"RACE committed non-decline {shown!r}"
            assert shown.lower() not in {
                "asian", "white", "black or african american", "hispanic or latino",
            }, f"RACE invented a race: {shown!r}"

        # --- HOW_HEARD react-select must alias-commit (not zero the menu) ---
        howheard_menus = [
            (["LinkedIn", "Indeed", "Job Board", "Company Website", "Referral", "Other"], True),
            (["LinkedIn", "Job Board", "Company Website", "Other"], True),
            (["LinkedIn", "Indeed", "Company Website", "Other"], False),
        ]
        for opts, multi in howheard_menus:
            await _load_gh_select(
                page, label="How did you hear about this job?", options=opts, multi=multi
            )
            r = await fill_gh_select(
                page,
                "How did you hear about this job?",
                "Internet job board",
                field_type="HOW_HEARD",
            )
            assert r.get("ok"), f"HOW_HEARD failed to commit on {opts}: {r}"
            shown = (r.get("shown") or "").lower()
            assert shown, f"HOW_HEARD empty commit on {opts}: {r}"
            assert any(
                tok in shown
                for tok in (
                    "job board",
                    "indeed",
                    "online",
                    "other",
                    "internet",
                    "linkedin",
                )
            ), f"HOW_HEARD committed unexpected {shown!r} on {opts}"

        await browser.close()


def test_browser_race_and_howheard_commit():
    asyncio.run(_browser_cases())


def test_ashby_experience_years_yesno_honest():
    from ashby_widgets import _ashby_yesno_default_for_label as f

    vals = {"YEARS_EXPERIENCE": "3.0"}  # dummy resume truth
    # 5+ years with only 3 dummy years → honest No
    assert f("Do you have 5+ years of experience in software engineering?", vals) is False
    assert f("Do you have a minimum of 10 years experience?", vals) is False
    # thresholds the dummy meets → Yes
    assert f("Do you have at least 2 years of professional experience?", vals) is True
    assert f("Do you have 3 or more years of experience?", vals) is True
    # no threshold → has relevant experience → Yes
    assert f("Do you have experience with Python?", vals) is True
    assert f("Do you have relevant experience?", vals) is True
    # unrelated question → None (defer to Flash), never invents an answer
    assert f("What is your favorite color?", vals) is None
    # honesty is threshold-driven, not always-yes
    assert f("Do you have 4 years of experience?", vals) is False
    assert f("Do you have 2 years of experience?", vals) is True


def test_ashby_experience_uses_profile_years_when_higher():
    from ashby_widgets import _ashby_yesno_default_for_label as f

    # A profile with 8 years should answer Yes to a 5+ screening.
    assert f("Do you have 5+ years of experience?", {"YEARS_EXPERIENCE": "8"}) is True
    # Default (no value) uses dummy 3.0 → 5+ is No.
    assert f("Do you have 5+ years of experience?", None) is False


def test_sponsorship_phantom_suppressed_when_satisfied():
    from fast_fill import _sponsorship_intent_satisfied
    from field_map import SPONSORSHIP, VISA_STATUS, WORK_AUTH

    assert _sponsorship_intent_satisfied({SPONSORSHIP}) is True
    assert _sponsorship_intent_satisfied({VISA_STATUS}) is True
    assert _sponsorship_intent_satisfied({WORK_AUTH}) is True
    assert _sponsorship_intent_satisfied({"EMAIL", "PHONE"}) is False
    assert _sponsorship_intent_satisfied(set()) is False


def test_privacy_acknowledgement_terms_consent():
    from field_map import classify_field, TERMS_CONSENT
    from dummy_answers import shared_values

    for lab in (
        "Capco Job Candidate Privacy Notice Acknowledgement*",
        "I acknowledge that I have read and understood the Privacy Notice",
        "Privacy Acknowledgement",
        "Candidate Privacy Statement",
    ):
        ftype, _ = classify_field({"label": lab, "name": "", "id": ""})
        assert ftype == TERMS_CONSENT, f"{lab!r} → {ftype} (want TERMS_CONSENT)"
    # Never invents; consent answer is the deterministic Yes (checkbox → checked).
    assert shared_values()[TERMS_CONSENT] == "Yes"


def test_howheard_type_fragment_never_zeroes_menu():
    """The HOW_HEARD filter fragment must be a short leaf, never the full essay."""
    from gh_select import _type_fragment_for, aliases_for

    cands = aliases_for("HOW_HEARD", "Internet job board")
    frag = _type_fragment_for("HOW_HEARD", cands)
    # Must not be the full multi-word dummy value that zeroes real menus.
    assert frag.lower() != "internet job board"
    assert len(frag.split()) <= 2


def main() -> None:
    test_ashby_experience_years_yesno_honest()
    test_ashby_experience_uses_profile_years_when_higher()
    test_sponsorship_phantom_suppressed_when_satisfied()
    test_privacy_acknowledgement_terms_consent()
    test_howheard_type_fragment_never_zeroes_menu()
    test_browser_race_and_howheard_commit()
    print("test_capco_residuals: OK")


if __name__ == "__main__":
    main()
