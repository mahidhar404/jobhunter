"""Regression: Workday Country Phone Code → United States (+1), never job boards."""

from __future__ import annotations


def test_classify_country_phone_code():
    from field_map import (
        ADDRESS_COUNTRY,
        DUMMY_ADDRESS,
        DUMMY_PROFILE,
        PHONE,
        PHONE_COUNTRY_CODE,
        PHONE_DEVICE,
        build_unique_values,
        classify_field,
    )

    ftype, _ = classify_field(
        {"label": "Country Phone Code", "name": "countryPhoneCode", "id": ""}
    )
    assert ftype == PHONE_COUNTRY_CODE

    ftype2, _ = classify_field(
        {
            "label": "Country Phone Code",
            "name": "phoneNumber--countryPhoneCode",
            "id": "phoneNumber--countryPhoneCode",
        }
    )
    assert ftype2 == PHONE_COUNTRY_CODE
    assert ftype2 != ADDRESS_COUNTRY
    assert ftype2 != PHONE

    device, _ = classify_field(
        {"label": "Phone Device Type", "name": "phoneType", "id": ""}
    )
    assert device == PHONE_DEVICE

    vals = build_unique_values(DUMMY_PROFILE, DUMMY_ADDRESS)
    assert "United States" in str(vals.get(PHONE_COUNTRY_CODE) or "")
    assert "+1" in str(vals.get(PHONE_COUNTRY_CODE) or "")


def test_phone_country_search_sanitizer_rejects_job_boards():
    from verified_select import (
        how_heard_scope_reject_aid,
        how_heard_source_committed,
        is_safe_phone_country_search,
        looks_like_phone_country_or_address_chip,
        phone_country_code_candidates,
        phone_country_code_search_query,
        reject_confusable_country_option,
    )

    assert phone_country_code_search_query("Indeed") == "United States"
    assert phone_country_code_search_query("Internet job board") == "United States"
    assert phone_country_code_search_query("LinkedIn") == "United States"
    assert phone_country_code_search_query("greenhouse") == "United States"
    assert phone_country_code_search_query("") == "United States"
    assert phone_country_code_search_query("United States") == "United States"
    assert phone_country_code_search_query("United States (+1)") == "United States"

    assert not is_safe_phone_country_search("Indeed")
    assert not is_safe_phone_country_search("Internet job board")
    assert is_safe_phone_country_search("United States")
    assert is_safe_phone_country_search("+1")

    from verified_select import sanitized_typeahead_token

    assert sanitized_typeahead_token("PHONE_COUNTRY_CODE", "Indeed", ["Indeed"]) == (
        "United States"
    )
    assert sanitized_typeahead_token(
        "PHONE_COUNTRY_CODE", "United States (+1)", []
    ) == "United States"

    cands = phone_country_code_candidates({"HOW_HEARD": "Indeed"})
    assert any("United States" in c for c in cands)
    assert not any("indeed" in c.lower() for c in cands)

    assert reject_confusable_country_option("United States", "Australia") is True
    assert reject_confusable_country_option("United States", "Australia (+61)") is True
    assert (
        reject_confusable_country_option("United States", "United States (+1)")
        is False
    )
    assert (
        reject_confusable_country_option(
            "United States", "United States of America (+1)"
        )
        is False
    )

    # Bare multiSelect Country Phone Code must NEVER count as how-heard commit
    assert looks_like_phone_country_or_address_chip("United States (+1)")
    assert looks_like_phone_country_or_address_chip(
        "Country Phone Code\n1 item selected, United States (+1)"
    )
    assert not looks_like_phone_country_or_address_chip(
        "1 item selected, Indeed"
    )
    assert not how_heard_source_committed(
        "1 item selected, United States (+1)", ["Indeed", "LinkedIn"]
    )
    assert how_heard_source_committed("1 item selected, Indeed", ["Indeed"])
    assert how_heard_scope_reject_aid("formField-countryPhoneCode")
    assert how_heard_scope_reject_aid("phoneNumber--countryPhoneCode")
    assert not how_heard_scope_reject_aid("formField-source")


def test_dummy_answer_country_phone_never_indeed():
    from workday_selectors import _dummy_answer_for_wd_label

    cands = _dummy_answer_for_wd_label(
        "Country Phone Code",
        {"HOW_HEARD": "Indeed", "PHONE_COUNTRY_CODE": "United States (+1)"},
    )
    assert cands
    assert not any("indeed" in c.lower() for c in cands)
    assert any("united states" in c.lower() or c.strip() == "+1" for c in cands)


def test_how_heard_selectors_never_bare_multiselect():
    """Regression: bare multiSelectContainer typed Indeed into dial code."""
    import inspect

    import exp_workday_selectors as wd
    import verified_select as vs

    hh_src = inspect.getsource(wd._fill_how_heard)
    assert "is_how_heard_safe_filter_input" in hh_src
    assert "PHONE_COUNTRY_CODE" in vs._ENUMERATE_FIRST_TYPES
    # Every multiSelectContainer input must be scoped under a formField-* line
    lines = [ln.strip() for ln in hh_src.splitlines()]
    for i, ln in enumerate(lines):
        if "multiSelectContainer\"] input" not in ln and "multiSelectContainer'] input" not in ln:
            continue
        prev = lines[i - 1] if i else ""
        assert "formField-" in prev or "formField-" in ln, (prev, ln)
    read_src = inspect.getsource(wd._read_how_heard_display)
    assert '[data-automation-id="multiSelectContainer"]' not in read_src
    assert "looks_like_phone_country_or_address_chip" in read_src
    # Shared helper must reject dial aids
    assert vs.how_heard_scope_reject_aid("formField-countryPhoneCode")
    assert '[data-automation-id="multiSelectContainer"] input' not in vs._HOW_HEARD_INPUT_SELS or (
        "formField-source" in vs._HOW_HEARD_INPUT_SELS
        and "formField-countryPhoneCode" not in vs._HOW_HEARD_INPUT_SELS
    )
    # Bare multiSelect alone must not be the whole selector string
    bare = '[data-automation-id="multiSelectContainer"] input'
    # Allow only when preceded by formField scope in the concatenated selector
    parts = [p.strip() for p in vs._HOW_HEARD_INPUT_SELS.split(",")]
    for p in parts:
        if "multiSelectContainer" in p:
            assert "formField-" in p, p
    assert bare not in {p.strip() for p in parts}


def test_semantic_does_not_score_australia_for_us():
    """Morningstar root cause: semantic sim scored Australia≈US at 70."""
    from verified_select import _default_score_option, clear_closest_match, rank_option_matches

    assert _default_score_option("Australia", "United States") == 0
    assert _default_score_option("Australia (+61)", "United States") == 0
    assert _default_score_option("United States (+1)", "United States") >= 80

    opts = [
        "Afghanistan",
        "Albania",
        "American Samoa",
        "Australia",
        "Austria",
        "Bahamas",
    ]
    ranked = rank_option_matches(opts, ["United States"], _default_score_option)
    assert clear_closest_match(ranked, at_last_word=True, intent="United States") is None
    assert not any(t == "Australia" and s >= 50 for s, _, t in ranked)


def test_workday_us_phone_readback_helpers():
    from exp_workday_selectors import (
        _is_us_address_country_readback,
        _is_us_country_phone_readback,
        _is_wrong_non_us_phone_code,
    )

    assert _is_us_country_phone_readback("United States (+1)")
    assert _is_us_country_phone_readback("United States of America (+1)")
    assert not _is_us_country_phone_readback("Australia (+61)")
    assert not _is_us_country_phone_readback("Australia")
    assert _is_wrong_non_us_phone_code("Australia (+61)")
    assert _is_us_address_country_readback("United States")
    assert not _is_us_address_country_readback("Australia")


def test_committed_us_phone_country_readback():
    from verified_select import (
        filter_phone_country_false_empties,
        is_committed_us_phone_country_readback,
        phone_country_empty_row,
        phone_country_verified_snips_from_report,
    )

    assert is_committed_us_phone_country_readback("United States (+1)")
    assert is_committed_us_phone_country_readback("United States of America (+1)")
    assert not is_committed_us_phone_country_readback("Select One")
    assert not is_committed_us_phone_country_readback("Australia (+61)")
    assert not is_committed_us_phone_country_readback("+1")

    rows = [
        {"id": "phoneNumber--countryPhoneCode", "reason": "empty_required_input"},
        {"id": "phone-number", "reason": "empty_required_input"},
    ]
    kept = filter_phone_country_false_empties(
        rows, "United States of America (+1)"
    )
    assert len(kept) == 1
    assert kept[0]["id"] == "phone-number"
    assert phone_country_empty_row({"id": "countryPhoneCode"})
    assert phone_country_empty_row({"label": "Country Phone Code*"})

    # Experience-page hold: live snip absent but verified fill row has chip readback
    report = {
        "filled": [
            {
                "type": "PHONE_COUNTRY_CODE",
                "automation_id": "countryPhoneCode",
                "verified": True,
                "readback": (
                    "Country Phone Code* 1 item selected, "
                    "United States of America (+1) United States of America (+1)"
                ),
            }
        ]
    }
    fallbacks = phone_country_verified_snips_from_report(report)
    assert fallbacks
    kept2 = filter_phone_country_false_empties(
        rows, None, fallback_snips=fallbacks
    )
    assert len(kept2) == 1
    assert kept2[0]["id"] == "phone-number"


async def _eval_fixture_html(html_path) -> list[dict]:
    import asyncio
    from pathlib import Path

    from playwright.async_api import async_playwright

    from workday_selectors import _required_empty_on_page

    html = Path(html_path).read_text()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        out = await _required_empty_on_page(page)
        await browser.close()
        return out


def test_nxp_phone_contact_fixture_zero_required_empty():
    import asyncio
    from pathlib import Path

    from form_gaps import collect_form_gaps, gaps_block_ready

    case = (
        Path(__file__).resolve().parent
        / "gym/ats/cases/workday_nxp_phone_contact/form.html"
    )
    empties = asyncio.run(_eval_fixture_html(case))
    assert empties == [], empties

    async def _gaps():
        from playwright.async_api import async_playwright

        html = case.read_text()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            gaps = await collect_form_gaps(page)
            await browser.close()
            return gaps

    gaps = asyncio.run(_gaps())
    assert not gaps_block_ready(gaps), gaps
    # Mid-wizard ADVANCE footer still blocks review-hold Ready — that is OK.
    # Honesty fix: phone country chip must not appear in required_empty / gaps.
    assert not any(
        "countryphonecode" in str(g.get("automation_id") or g.get("label") or "").lower()
        for g in gaps
    )


def test_nxp_phone_empty_chip_still_incomplete():
    import asyncio
    from pathlib import Path

    from form_gaps import collect_form_gaps, gaps_block_ready

    case = (
        Path(__file__).resolve().parent
        / "gym/ats/cases/workday_nxp_phone_contact/form.html"
    )
    html = case.read_text().replace(
        '<div id="country-chip-wrap">',
        '<div id="country-chip-wrap" style="display:none">',
    )

    async def _run():
        from playwright.async_api import async_playwright

        from workday_selectors import _required_empty_on_page

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            await page.evaluate(
                """() => {
                  const f = document.getElementById('country-filter');
                  f.style.display = 'block';
                  f.value = '';
                }"""
            )
            empties = await _required_empty_on_page(page)
            gaps = await collect_form_gaps(page)
            await browser.close()
            return empties, gaps

    empties, gaps = asyncio.run(_run())
    assert any(
        "countryphonecode" in str(e.get("id") or "").lower()
        or "country phone" in str(e.get("label") or "").lower()
        for e in empties
    ) or gaps_block_ready(gaps), (empties, gaps)


def test_fill_verify_phone_country_code_row():
    from fill_verify import is_verified_fill_row

    row = {
        "type": "PHONE_COUNTRY_CODE",
        "status": "filled",
        "verified": True,
        "readback": "United States of America (+1)",
        "value": "United States (+1)",
    }
    assert is_verified_fill_row(row) is True
    assert is_verified_fill_row({**row, "readback": "Select One", "verified": True}) is False


if __name__ == "__main__":
    test_classify_country_phone_code()
    test_phone_country_search_sanitizer_rejects_job_boards()
    test_dummy_answer_country_phone_never_indeed()
    test_how_heard_selectors_never_bare_multiselect()
    test_semantic_does_not_score_australia_for_us()
    test_workday_us_phone_readback_helpers()
    test_committed_us_phone_country_readback()
    test_nxp_phone_contact_fixture_zero_required_empty()
    test_nxp_phone_empty_chip_still_incomplete()
    test_fill_verify_phone_country_code_row()
    print("ok")
