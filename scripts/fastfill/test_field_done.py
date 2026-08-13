#!/usr/bin/env python3
"""Unit tests: unified field_is_done contract (6 Workday patterns, dummy-only)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PHONE_FIXTURE = ROOT / "gym/ats/cases/workday_nxp_phone_contact/form.html"
FOS_FIXTURE = ROOT / "gym/ats/cases/workday_education_fos_chip/form.html"
FOS_WRONG_FIXTURE = ROOT / "gym/ats/cases/workday_education_fos_wrong_chip/form.html"
RADIO_FIXTURE = ROOT / "gym/ats/cases/wd_radio_aria_checked/form.html"
HOW_HEARD_FIXTURE = ROOT / "gym/ats/cases/workday_how_heard_hierarchical_chip/form.html"


def test_phone_country_chip_done():
    from field_done import field_is_done_from_readback
    from field_map import PHONE_COUNTRY_CODE

    v = field_is_done_from_readback(
        "United States of America (+1)",
        {"type": PHONE_COUNTRY_CODE},
        "United States (+1)",
    )
    assert v.ok, v.reason
    assert v.reason == "phone_country_chip"


def test_fos_science_computer_done():
    from field_done import field_is_done_from_readback
    from field_map import FIELD_OF_STUDY

    v = field_is_done_from_readback(
        "Field of Study* Science-Computer ×",
        {"type": FIELD_OF_STUDY, "dom_chip": True},
        "Computer Science",
    )
    assert v.ok, v.reason
    assert v.reason == "fos_chip_match"


def test_fos_arts_other_wrong_not_done():
    from field_done import field_is_done_from_readback
    from field_map import FIELD_OF_STUDY

    v = field_is_done_from_readback(
        "Field of Study 1 item selected, Arts-Other Arts-Other",
        {"type": FIELD_OF_STUDY, "dom_chip": True},
        "Computer Science",
    )
    assert not v.ok
    assert v.reason == "fos_chip_wrong_value"


def test_aria_radio_done():
    from field_done import field_is_done_from_readback

    v = field_is_done_from_readback(
        "No",
        {"mode": "radio", "aria_checked": True, "picked": "No"},
        "No",
    )
    assert v.ok, v.reason


def test_date_spin_committed():
    from field_done import field_is_done_from_readback

    v = field_is_done_from_readback(
        {"month_input": "08", "year_input": "2017", "month_display": "MM", "year_display": "YYYY"},
        {"widget": "date_spin", "month": "08", "year": "2017"},
        "08-2017",
    )
    assert v.ok, v.reason
    assert v.reason == "date_spin_committed"


def test_present_disabled_end_done():
    from field_done import field_is_done_from_readback

    v = field_is_done_from_readback(
        {
            "month_input": "",
            "year_input": "",
            "month_display": "MM",
            "year_display": "YYYY",
        },
        {
            "widget": "date_spin",
            "present_checked": True,
            "end_disabled": True,
        },
        "Present",
    )
    assert v.ok, v.reason
    assert v.reason == "present_disabled_end_skip"

    v2 = field_is_done_from_readback(
        "Present",
        {"widget": "date_spin", "present_checked": True, "end_disabled": True},
        "Present",
    )
    assert v2.ok, v2.reason
    assert v2.reason == "present_disabled_end_skip"

    cisco = field_is_done_from_readback(
        {
            "month_input": "",
            "year_input": "",
            "month_display": "MM",
            "year_display": "YYYY",
        },
        {"widget": "date_spin", "present_checked": True, "end_disabled": False},
        "06/2023",
    )
    assert not cisco.ok


def test_how_heard_chip_done():
    from field_done import field_is_done_from_readback
    from field_map import HOW_HEARD

    v = field_is_done_from_readback(
        "How Did You Hear About Us?* 1 item selected, Internet job board",
        {"type": HOW_HEARD},
        "Internet job board",
    )
    assert v.ok, v.reason


def test_master_matches_masters_degree_select_one_type():
    """0842: truncated Workday chip 'Master' must match intent Master's Degree.

    select_one:Degree / formField-degree must use the DEGREE matcher (not
    generic text_match) so lock_skip is already_correct, not STUCK.
    """
    from field_done import field_is_done_from_readback

    for meta in (
        {"type": "select_one:Degree"},
        {"type": "DEGREE", "automation_id": "formField-degree"},
        {"type": "education/degree"},
    ):
        v = field_is_done_from_readback("Master", meta, "Master's Degree")
        assert v.ok, (meta, v)
        assert v.reason == "degree_match", (meta, v)


def test_text_autofill_match():
    from field_done import field_is_done_from_readback

    v = field_is_done_from_readback("Test Dummy", {"type": "NAME_FULL"}, "Test Dummy")
    assert v.ok, v.reason
    assert v.reason == "text_match"


def test_text_autofill_mismatch():
    from field_done import field_is_done_from_readback

    v = field_is_done_from_readback("Arts-Other", {"type": "FIELD_OF_STUDY"}, "Computer Science")
    assert not v.ok


async def _browser_case(html_path: Path, fn) -> None:
    from playwright.async_api import async_playwright

    html = html_path.read_text(encoding="utf-8")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        await fn(page)
        await browser.close()


async def _browser_case_html(html: str, fn) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        await fn(page)
        await browser.close()


def test_live_phone_country_fixture():
    from field_done import field_is_done
    from field_map import PHONE_COUNTRY_CODE

    async def _run(page):
        v = await field_is_done(page, {"type": PHONE_COUNTRY_CODE}, "United States (+1)")
        assert v.ok, v.reason

    asyncio.run(_browser_case(PHONE_FIXTURE, _run))


def test_live_fos_fixture():
    from field_done import field_is_done
    from field_map import FIELD_OF_STUDY

    async def _run(page):
        v = await field_is_done(
            page,
            {"type": FIELD_OF_STUDY, "automation_id": "formField-fieldOfStudy"},
            "Computer Science",
        )
        assert v.ok, v.reason

    asyncio.run(_browser_case(FOS_FIXTURE, _run))


def test_live_fos_wrong_fixture():
    from field_done import field_is_done
    from field_map import FIELD_OF_STUDY

    async def _run(page):
        v = await field_is_done(
            page,
            {"type": FIELD_OF_STUDY, "automation_id": "formField-fieldOfStudy"},
            "Computer Science",
        )
        assert not v.ok

    asyncio.run(_browser_case(FOS_WRONG_FIXTURE, _run))


def test_live_radio_fixture():
    from field_done import field_is_done

    async def _run(page):
        v = await field_is_done(
            page,
            {
                "mode": "radio",
                "name": "candidateIsPreviousWorker",
                "picked": "No",
            },
            "No",
        )
        assert v.ok, v.reason

    asyncio.run(_browser_case(RADIO_FIXTURE, _run))


def test_filter_phone_country_false_empties():
    from field_done import filter_phone_country_false_empties

    rows = [
        {"label": "Country Phone Code*", "id": "phoneNumber--countryPhoneCode"},
        {"label": "Phone Number*", "id": "phoneNumber"},
    ]
    kept = filter_phone_country_false_empties(rows, "United States (+1)")
    assert len(kept) == 1
    assert kept[0]["label"].startswith("Phone Number")


def test_filter_required_empty_from_report_experience_text():
    from field_done import filter_required_empty_from_report

    report = {
        "filled": [
            {
                "automation_id": "workExperience-1/jobTitle",
                "type": "EXPERIENCE_TITLE",
                "value": "Applied AI/ML Analyst",
                "readback": "Applied AI/ML Analyst",
                "verified": True,
                "ok": True,
            },
            {
                "automation_id": "workExperience-1/company",
                "type": "EXPERIENCE_COMPANY",
                "value": "Example Corp",
                "readback": "Example Corp",
                "verified": True,
                "ok": True,
            },
        ],
    }
    empties = [
        {"id": "jobTitle", "label": "Job Title*", "reason": "empty_required_input"},
        {"id": "companyName", "label": "Company*", "reason": "empty_required_input"},
        {"id": "formField-startDate", "label": "From*", "reason": "empty_required_date_field"},
    ]
    kept = filter_required_empty_from_report(report, empties)
    assert len(kept) == 1, kept
    assert kept[0]["id"] == "formField-startDate"


def test_filter_required_empty_nxp_1116z_jobtitle_aid_type():
    """1116Z: type=workExperience-1/jobTitle (not EXPERIENCE_TITLE) still covers Job Title*."""
    from field_done import filter_required_empty_from_report

    report = {
        "filled": [
            {
                "automation_id": "workExperience-1/jobTitle",
                "type": "workExperience-1/jobTitle",
                "value": "Applied AI/ML Analyst",
                "readback": "Applied AI/ML Analyst",
                "verified": True,
                "ok": True,
                "mode": "skip",
            },
            {
                "automation_id": "workExperience-1/company",
                "type": "workExperience-1/company",
                "value": "Example Corp",
                "readback": "Example Corp | Remote",
                "verified": True,
                "ok": True,
                "reason": "already_correct_keep",
            },
            {
                "automation_id": "workExperience-1/startDate",
                "type": "workExperience-1/startDate",
                "mode": "date_spin",
                "value": "01/2024",
                "readback": "01/2024",
                "verified": True,
                "ok": True,
                "reason": "already_correct_skip",
                "skipped_already_correct": True,
            },
            {
                "automation_id": "workExperience-1/endDate",
                "type": "workExperience-1/endDate",
                "mode": "date_spin",
                "value": "12/2024",
                "readback": "12/2024",
                "verified": True,
                "ok": True,
                "reason": "already_correct_skip",
                "skipped_already_correct": True,
            },
        ],
    }
    empties = [
        {"id": "jobTitle", "label": "Job Title*", "reason": "empty_required_input"},
        {"id": "companyName", "label": "Company*", "reason": "empty_required_input"},
        {"id": "formField-startDate", "label": "From*", "reason": "empty_required_date_field"},
        {"id": "formField-endDate", "label": "To*", "reason": "empty_required_date_field"},
    ]
    kept = filter_required_empty_from_report(report, empties)
    assert kept == [], kept


def test_filter_required_empty_unclassified_from_to_when_dates_skip_done():
    """1138Z: From*/To* unclassified leftovers are done when dates skip-locked."""
    from field_done import filter_required_empty_from_report

    report = {
        "filled": [
            {
                "automation_id": "workExperience-1/startDate",
                "type": "workExperience-1/startDate",
                "mode": "date_spin",
                "value": "01/2024",
                "readback": "01/2024",
                "verified": True,
                "ok": True,
                "reason": "already_correct_skip",
                "skipped_already_correct": True,
            },
            {
                "automation_id": "workExperience-1/endDate",
                "type": "workExperience-1/endDate",
                "mode": "date_spin",
                "value": "12/2024",
                "readback": "12/2024",
                "verified": True,
                "ok": True,
                "reason": "already_correct_skip",
                "skipped_already_correct": True,
            },
        ],
    }
    empties = [
        {"id": "From*", "label": "From*", "reason": "unclassified"},
        {"id": "formField-startDate", "label": "From*", "reason": "empty_required_date_field"},
        {"id": "formField-endDate", "label": "To*", "reason": "empty_required_date_field"},
        {"id": "To*", "label": "To*", "reason": "unclassified"},
    ]
    kept = filter_required_empty_from_report(report, empties)
    assert kept == [], kept


def test_filter_required_empty_keeps_blank_job_title():
    """Real empty Job Title* / Company* must still block ADVANCE."""
    from field_done import filter_required_empty_from_report

    report = {
        "filled": [
            {
                "automation_id": "workExperience-1/jobTitle",
                "type": "EXPERIENCE_TITLE",
                "value": "Applied AI/ML Analyst",
                "readback": "",
                "verified": False,
                "ok": False,
                "reason": "empty_readback",
            }
        ]
    }
    empties = [
        {"id": "jobTitle", "label": "Job Title*", "reason": "empty_required_input"},
        {"id": "companyName", "label": "Company*", "reason": "empty_required_input"},
    ]
    kept = filter_required_empty_from_report(report, empties)
    ids = {k["id"] for k in kept}
    assert "jobTitle" in ids, kept
    assert "companyName" in ids, kept


def test_filter_required_empty_from_report_date_spin():
    from field_done import filter_required_empty_from_report

    report = {
        "filled": [
            {
                "automation_id": "workExperience-1/startDate",
                "mode": "date_spin",
                "type": "EXPERIENCE_DATE",
                "month": "01",
                "year": "2022",
                "readback": {"month_input": "01", "year_input": "2022"},
                "verified": True,
                "ok": True,
            },
        ],
    }
    empties = [
        {"id": "dateSectionMonth-display", "label": "MM / YYYY", "reason": "empty_required_date_display"},
        {"id": "formField-startDate", "label": "From*", "reason": "empty_required_date_field"},
        {"id": "formField-endDate", "label": "To*", "reason": "empty_required_date_field"},
    ]
    kept = filter_required_empty_from_report(report, empties)
    assert len(kept) == 1, kept
    assert kept[0]["id"] == "formField-endDate"


def test_filter_required_empty_address_state_covers_country_region():
    """Verified ADDRESS_STATE must drop countryRegion false-empties (NXP 2244Z)."""
    from field_done import filter_required_empty_from_report
    from field_map import ADDRESS_STATE

    report = {
        "filled": [
            {
                "type": ADDRESS_STATE,
                "automation_id": "addressSection_countryRegion",
                "readback": "Illinois",
                "value": "IL",
                "verified": True,
                "ok": True,
            }
        ]
    }
    empties = [
        {
            "id": "addressSection_countryRegion",
            "label": "State / Province *",
            "reason": "empty_required",
        },
        {"id": "phoneNumber", "label": "Phone Number*", "reason": "empty_required"},
    ]
    kept = filter_required_empty_from_report(report, empties)
    assert len(kept) == 1, kept
    assert kept[0]["id"] == "phoneNumber"


def test_filter_required_empty_drops_not_in_dom_apt():
    """NXP 0842Z: Apt not_in_dom must not block Contact Next."""
    from field_done import filter_required_empty_from_report

    report = {
        "filled": [
            {
                "type": "ADDRESS_LINE1",
                "automation_id": "addressSection_addressLine1",
                "readback": "100 Example Ave",
                "verified": True,
                "ok": True,
            }
        ]
    }
    empties = [
        {
            "id": "addressSection_addressLine2",
            "label": "Address Line 2",
            "reason": "not_in_dom",
        },
        {
            "id": "addressSection_city",
            "label": "City*",
            "reason": "empty_required_input",
        },
    ]
    kept = filter_required_empty_from_report(report, empties)
    assert all(
        "addressline2" not in str(k.get("id") or "").lower().replace("_", "")
        for k in kept
    ), kept
    assert len(kept) == 1 and kept[0]["id"] == "addressSection_city", kept


def test_filter_required_empty_phone_country_chip_covers_empty():
    """US +1 chip committed → Country Phone Code empty_required is false."""
    from field_done import filter_required_empty_from_report
    from field_map import PHONE_COUNTRY_CODE

    report = {
        "filled": [
            {
                "type": PHONE_COUNTRY_CODE,
                "automation_id": "countryPhoneCode",
                "readback": "United States of America (+1)",
                "value": "United States (+1)",
                "verified": True,
                "ok": True,
            }
        ]
    }
    empties = [
        {
            "id": "phoneNumber--countryPhoneCode",
            "label": "Country Phone Code*",
            "reason": "empty_required_input",
        },
        {
            "id": "phone-number",
            "label": "Phone Number*",
            "reason": "empty_required_input",
        },
    ]
    kept = filter_required_empty_from_report(report, empties)
    assert len(kept) == 1, kept
    assert kept[0]["id"] == "phone-number", kept


def test_filter_required_empty_county_not_in_dom_after_state_does_not_fail():
    """After Illinois, absent county must not force required_fields_empty FAIL."""
    from field_done import filter_required_empty_from_report
    from field_map import ADDRESS_STATE

    report = {
        "filled": [
            {
                "type": ADDRESS_STATE,
                "automation_id": "addressSection_countryRegion",
                "readback": "Illinois",
                "value": "IL",
                "verified": True,
                "ok": True,
            }
        ]
    }
    empties = [
        {
            "id": "addressSection_regionSubdivision1",
            "label": "County",
            "reason": "not_in_dom",
        },
        {
            "id": "addressSection_postalCode",
            "label": "Postal Code*",
            "reason": "empty_required_input",
        },
    ]
    kept = filter_required_empty_from_report(report, empties)
    assert all(
        "regionsubdivision" not in str(k.get("id") or "").lower() for k in kept
    ), kept
    assert len(kept) == 1 and kept[0]["id"] == "addressSection_postalCode", kept


def test_filter_required_empty_covers_legal_names():
    """NXP 0842Z: First/Last already Test/Dummy must not block Contact Next."""
    from field_done import filter_required_empty_from_report
    from field_map import NAME_FIRST, NAME_LAST

    report = {
        "filled": [
            {
                "type": NAME_FIRST,
                "automation_id": "legalNameSection_firstName",
                "readback": "Test",
                "value": "Test",
                "verified": True,
                "ok": True,
            },
            {
                "type": NAME_LAST,
                "automation_id": "legalNameSection_lastName",
                "readback": "Dummy",
                "value": "Dummy",
                "verified": True,
                "ok": True,
            },
        ]
    }
    empties = [
        {"id": "legalName--firstName", "label": "First Name*", "reason": "empty_required_input"},
        {"id": "legalName--lastName", "label": "Last Name*", "reason": "empty_required_input"},
        {"id": "phoneNumber", "label": "Phone Number*", "reason": "empty_required_input"},
    ]
    kept = filter_required_empty_from_report(report, empties)
    assert len(kept) == 1, kept
    assert kept[0]["id"] == "phoneNumber"


def test_live_probe_drops_filled_experience_title():
    """1116Z: Job Title* required_empty while input already holds dummy title."""
    from field_done import filter_required_empty_false_incomplete

    html = """<!DOCTYPE html><html><body>
    <div data-automation-id="formField-jobTitle">
      <label>Job Title*</label>
      <div data-automation-id="jobTitle">
        <input name="jobTitle" aria-required="true" value="Applied AI/ML Analyst" />
      </div>
    </div>
    <div data-automation-id="formField-company">
      <label>Company*</label>
      <input name="companyName" aria-required="true" value="Example Corp | Remote" />
    </div>
    </body></html>"""

    async def _run(page):
        empties = [
            {"id": "jobTitle", "label": "Job Title*", "reason": "empty_required_input"},
            {"id": "companyName", "label": "Company*", "reason": "empty_required_input"},
        ]
        kept = await filter_required_empty_false_incomplete(page, {"filled": []}, empties)
        assert kept == [], kept

    asyncio.run(_browser_case_html(html, _run))


def test_live_probe_keeps_blank_job_title():
    from field_done import filter_required_empty_false_incomplete

    html = """<!DOCTYPE html><html><body>
    <label>Job Title*</label>
    <input name="jobTitle" aria-required="true" value="" />
    <label>Company*</label>
    <input name="companyName" aria-required="true" value="" />
    </body></html>"""

    async def _run(page):
        empties = [
            {"id": "jobTitle", "label": "Job Title*", "reason": "empty_required_input"},
            {"id": "companyName", "label": "Company*", "reason": "empty_required_input"},
        ]
        kept = await filter_required_empty_false_incomplete(page, {"filled": []}, empties)
        ids = {k["id"] for k in kept}
        assert ids == {"jobTitle", "companyName"}, kept

    asyncio.run(_browser_case_html(html, _run))


def test_live_probe_drops_filled_legal_names():
    """0842Z: required_empty First/Last* while inputs already hold dummy names."""
    from field_done import filter_required_empty_false_incomplete

    html = """<!DOCTYPE html><html><body>
    <label>First Name*</label>
    <input name="legalName--firstName" value="Test" aria-required="true" />
    <label>Last Name*</label>
    <input name="legalName--lastName" value="Dummy" aria-required="true" />
    </body></html>"""

    async def _run(page):
        empties = [
            {"id": "legalName--firstName", "label": "First Name*", "reason": "empty_required_input"},
            {"id": "legalName--lastName", "label": "Last Name*", "reason": "empty_required_input"},
        ]
        report = {
            "fill_values": {"NAME_FIRST": "Test", "NAME_LAST": "Dummy"},
            "filled": [],
        }
        kept = await filter_required_empty_false_incomplete(page, report, empties)
        assert kept == [], kept

    asyncio.run(_browser_case_html(html, _run))


def test_discipline_type_uses_fos_readback():
    from field_done import field_is_done_from_readback

    v = field_is_done_from_readback(
        "Field of Study* Science-Computer ×",
        {"type": "DISCIPLINE", "dom_chip": True},
        "Computer Science",
    )
    assert v.ok, v.reason


def test_fos_aria_selected_chip_live():
    """ARIA selectedItem / aria-label is a secondary FoS oracle (inline HTML)."""
    from field_done import field_is_done
    from field_map import FIELD_OF_STUDY

    html = """<!DOCTYPE html><html><body>
    <div data-automation-id="formField-fieldOfStudy" aria-label="Science-Computer">
      <span data-automation-id="selectedItem" aria-label="Science-Computer"
            aria-selected="true" data-committed="Science-Computer"></span>
    </div>
    </body></html>"""

    async def _run(page):
        v = await field_is_done(
            page,
            {"type": FIELD_OF_STUDY, "automation_id": "formField-fieldOfStudy"},
            "Computer Science",
        )
        assert v.ok, v.reason

    async def _browser():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            await _run(page)
            await browser.close()

    asyncio.run(_browser())


BATTLE_FIXTURE = ROOT / "gym/ats/cases/workday_battle_multipage/form.html"


def test_how_heard_alias_aid_probe_is_fast_uncommitted():
    """``automation_id=how_heard`` is not in the DOM — must not 30s-timeout."""
    from field_done import field_is_done
    from field_map import HOW_HEARD

    async def _run(page):
        import time

        t0 = time.monotonic()
        v = await field_is_done(
            page,
            {"type": HOW_HEARD, "automation_id": "how_heard"},
            "Web - LinkedIn",
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 5, elapsed
        assert not v.ok, v

    asyncio.run(_browser_case(BATTLE_FIXTURE, _run))


def test_how_heard_alias_aid_done_after_web_linkedin_chip():
    from field_done import field_is_done
    from field_map import HOW_HEARD

    async def _run(page):
        await page.evaluate(
            """() => {
              const opts = [...document.querySelectorAll('[data-automation-id="promptOption"]')];
              const web = opts.find((o) => (o.textContent || '').includes('Website'));
              if (web) web.click();
            }"""
        )
        await page.wait_for_timeout(40)
        await page.evaluate(
            """() => {
              const opts = [...document.querySelectorAll('[data-automation-id="promptOption"]')];
              const leaf = opts.find((o) => (o.textContent || '').trim() === 'Web - LinkedIn');
              if (leaf) leaf.click();
            }"""
        )
        v = await field_is_done(
            page,
            {"type": HOW_HEARD, "automation_id": "how_heard"},
            "Web - LinkedIn",
        )
        assert v.ok, v

    asyncio.run(_browser_case(BATTLE_FIXTURE, _run))


def test_required_empty_skips_how_heard_after_chip():
    from exp_workday_selectors import _required_empty_on_page

    async def _run(page):
        await page.evaluate(
            """() => {
              const opts = [...document.querySelectorAll('[data-automation-id="promptOption"]')];
              const web = opts.find((o) => (o.textContent || '').includes('Website'));
              if (web) web.click();
            }"""
        )
        await page.wait_for_timeout(40)
        await page.evaluate(
            """() => {
              const opts = [...document.querySelectorAll('[data-automation-id="promptOption"]')];
              const leaf = opts.find((o) => (o.textContent || '').trim() === 'Web - LinkedIn');
              if (leaf) leaf.click();
            }"""
        )
        empties = await _required_empty_on_page(page)
        hh = [
            e
            for e in empties
            if "source" in str(e.get("id") or "").lower()
            or "how" in str(e.get("label") or "").lower()
        ]
        assert not hh, empties

    asyncio.run(_browser_case(BATTLE_FIXTURE, _run))


def test_required_empty_skips_disabled_end_date_when_present():
    from exp_workday_selectors import _required_empty_on_page

    async def _run(page):
        await page.evaluate("() => window.__battleGym.showStep(1)")
        empties = await _required_empty_on_page(page)
        end_rows = [
            e
            for e in empties
            if "enddate" in str(e.get("id") or "").lower()
            or str(e.get("label") or "").strip().lower() == "to"
        ]
        assert not end_rows, empties

    asyncio.run(_browser_case(BATTLE_FIXTURE, _run))


def test_is_verified_fill_row_matches_field_is_done_from_row():
    """Pack metrics and contract must share one completion oracle."""
    from field_done import field_is_done_from_row
    from fill_verify import is_verified_fill_row
    from field_map import FIELD_OF_STUDY, HOW_HEARD, PHONE_COUNTRY_CODE

    fixtures = [
        {"type": "EMAIL", "value": "ada@test.com", "readback": ""},
        {
            "type": "SCHOOL",
            "value": "MIT",
            "readback": "Select...",
            "verified": True,
        },
        {
            "type": FIELD_OF_STUDY,
            "value": "Computer Science",
            "readback": "Field of Study* Science-Computer ×",
            "dom_chip": True,
            "verified": True,
            "ok": True,
        },
        {
            "type": PHONE_COUNTRY_CODE,
            "value": "United States (+1)",
            "readback": "United States of America (+1)",
            "verified": True,
            "ok": True,
        },
        {
            "type": HOW_HEARD,
            "value": "LinkedIn",
            "picked": "LinkedIn",
            "readback": "Internet",
            "verified": True,
            "ok": True,
        },
        {
            "type": HOW_HEARD,
            "value": "LinkedIn",
            "picked": "LinkedIn",
            "readback": "1 item selected, LinkedIn",
            "verified": True,
            "ok": True,
        },
        {
            "type": "NAME_FULL",
            "value": "Test Dummy",
            "readback": "Test Dummy",
            "verified": True,
            "ok": True,
        },
        {"status": "stuck", "verified": True, "readback": "x"},
    ]
    for row in fixtures:
        wrapper = is_verified_fill_row(row)
        ssot = field_is_done_from_row(row).ok
        assert wrapper is ssot, (row, wrapper, ssot, field_is_done_from_row(row))


def test_filled_rows_honest_ignores_not_in_dom():
    from field_done import filled_rows_honest

    assert filled_rows_honest(
        {
            "filled": [
                {
                    "type": "ADDRESS_COUNTRY",
                    "reason": "not_in_dom",
                    "verified": False,
                    "ok": False,
                    "status": "missed",
                },
                {
                    "type": "EMAIL",
                    "value": "randommail6969@gmail.com",
                    "readback": "randommail6969@gmail.com",
                    "verified": True,
                    "ok": True,
                },
            ]
        }
    )


if __name__ == "__main__":
    import sys

    failed = 0
    for name in [n for n in dir() if n.startswith("test_")]:
        try:
            globals()[name]()
            print("OK", name)
        except Exception as e:
            print("FAIL", name, e)
            failed += 1
    raise SystemExit(failed)
