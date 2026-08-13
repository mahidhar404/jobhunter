"""Ashby-specific question widgets (Yes/No buttons, EEO radios, consents).

Ashby application forms use custom Yes/No button pairs (not radio inputs)
inside `.ashby-application-form-field-entry` / `[class*="_fieldEntry_"]`.
Skyvern extract_form_fields.js routinely misses these, so Layer 0/1 never
sees WORK_AUTH / SPONSORSHIP / hub / prior-employer questions.

Dummy-only. Never submit. EEO → DUMMY_PROFILE answers (Male / no disability /
no veteran preferred; Decline aliases as fallback).
"""

from __future__ import annotations

import logging
import re
from typing import Any

_log = logging.getLogger("ashby_widgets")

from field_map import (
    ADDRESS_CITY,
    ADDRESS_COUNTRY,
    ADDRESS_STATE,
    ADDRESS_ZIP,
    DISABILITY,
    EMAIL,
    GENDER,
    GITHUB,
    INTEREST,
    LINKEDIN,
    LATIN_AMERICA,
    NAME_FULL,
    PHONE,
    PORTFOLIO,
    RACE,
    SALARY_EXPECTED,
    SPONSORSHIP,
    TALENT_HUB,
    TERMS_CONSENT,
    VETERAN,
    WORK_AUTH,
    WORKED_HERE_BEFORE,
    classify_field,
    validate_filled,
)
from gh_select import aliases_for, _score_option

# Ashby custom questions use opaque UUID ``name`` attrs — prefer label scope.
_ASHBY_URL_TYPES = frozenset({LINKEDIN, GITHUB, PORTFOLIO})
_ASHBY_TEXT_TYPES = frozenset(
    {INTEREST, SALARY_EXPECTED, ADDRESS_CITY, LINKEDIN, GITHUB, PORTFOLIO}
)
_ASHBY_URL_LABEL_HINTS: dict[str, re.Pattern[str]] = {
    LINKEDIN: re.compile(r"linked\s*in", re.I),
    GITHUB: re.compile(r"git\s*hub", re.I),
    PORTFOLIO: re.compile(
        r"portfolio|personal\s*(web)?\s*site|(^|\s)website(\s|$)|web\s*page",
        re.I,
    ),
}
# Contact fields Ashby resume-parse often overwrites after upload.
_ASHBY_CONTACT_SELECTORS: dict[str, str] = {
    NAME_FULL: (
        "input[name='_systemfield_name'], input[name='name'], "
        "input[autocomplete='name']"
    ),
    EMAIL: (
        "input[type=email], input[name='email'], "
        "input[name='_systemfield_email'], input[autocomplete='email']"
    ),
    PHONE: (
        "input[type=tel], input[name='phone'], "
        "input[name='_systemfield_phone'], input[autocomplete='tel']"
    ),
}

# Ashby Location combobox often reveals a dependent zip text field after pick.
_ASHBY_LOCATION_COMBO = (
    "label.ashby-application-form-question-title:has-text('Location') "
    "~ div [role=combobox], "
    ".ashby-application-form-field-entry:has(label:has-text('Location')) [role=combobox], "
    "[class*=\"_fieldEntry_\"]:has(label:has-text('Location')) [role=combobox], "
    "input[placeholder='Start typing...'][role=combobox]"
)
_ASHBY_ZIP_ENTRY = (
    ".ashby-application-form-field-entry:has(label:has-text('zip')), "
    ".ashby-application-form-field-entry:has(label:has-text('postal')), "
    ".ashby-application-form-field-entry:has([class*='_heading_']:has-text('zip')), "
    ".ashby-application-form-field-entry:has([class*='_heading_']:has-text('postal')), "
    "[class*=\"_fieldEntry_\"]:has(label:has-text('zip')), "
    "[class*=\"_fieldEntry_\"]:has(label:has-text('postal')), "
    "[class*=\"_fieldEntry_\"]:has([class*='_heading_']:has-text('zip')), "
    "[class*=\"_fieldEntry_\"]:has([class*='_heading_']:has-text('postal'))"
)
_ASHBY_ZIP_HEADING = re.compile(
    r"home\s+zip\s+code|what is your home zip|zip\s+code|postal\s+code",
    re.I,
)
_ASHBY_ZIP_INPUT = (
    f"{_ASHBY_ZIP_ENTRY} input[type=text], "
    f"{_ASHBY_ZIP_ENTRY} input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio]), "
    f"{_ASHBY_ZIP_ENTRY} textarea"
)

# UI placeholders that must never count as a filled value.
_EMPTY_UI_VALUES = frozenset(
    {
        "",
        "type here...",
        "type here",
        "start typing...",
        "start typing",
        "enter text",
        "select",
        "select one",
        "choose",
        "—",
        "-",
    }
)

# Yes/No + consent + EEO types we fill from Ashby field-entry blocks.
_ASHBY_CHOICE_TYPES = frozenset(
    {
        WORK_AUTH,
        SPONSORSHIP,
        TALENT_HUB,
        WORKED_HERE_BEFORE,
        TERMS_CONSENT,
        GENDER,
        RACE,
        VETERAN,
        DISABILITY,
        LATIN_AMERICA,
    }
)


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def is_empty_ui_value(text: str | None) -> bool:
    """True when value is blank or a generic Ashby/ATS placeholder."""
    t = (text or "").strip().lower()
    if t in _EMPTY_UI_VALUES:
        return True
    if t.startswith("type here") or t.startswith("start typing"):
        return True
    if t.startswith("enter ") and len(t) < 40:
        return True
    return False


def _value_already_correct(want: str, current: str) -> bool:
    """True when live UI value already soft-matches intended (skip thrash).

    ATS2-004: use soft_value_match / value_matches_readback (word-boundary +
    confusable guards) instead of bidirectional substring (``w in c or c in w``).
    """
    if is_empty_ui_value(current):
        return False
    w = (want or "").strip()
    c = (current or "").strip()
    if not w or not c:
        return False
    try:
        from verified_select import soft_value_match, value_matches_readback

        if soft_value_match(w, c) or value_matches_readback(w, c):
            return True
    except Exception:
        wn, cn = _norm(w), _norm(c)
        if wn and cn and wn == cn:
            return True
    wd = "".join(ch for ch in want if ch.isdigit())
    cd = "".join(ch for ch in current if ch.isdigit())
    if wd and cd and len(wd) >= 5 and (wd == cd or wd[-5:] == cd[-5:]):
        return True
    return False


async def _find_ashby_zip_via_dom(page):
    """Last-resort: locate zip input via label walk (Airwallex custom entries)."""
    try:
        found = await page.evaluate(
            """() => {
              const isVis = el => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 2 && r.height > 2
                  && s.visibility !== 'hidden' && s.display !== 'none';
              };
              for (const lab of document.querySelectorAll(
                'label, [class*="question"], [class*="Question"], [class*="_heading_"]'
              )) {
                const t = (lab.innerText || '').toLowerCase();
                if (!/home\\s+zip|what is your home zip|zip\\s+code|postal/.test(t)) continue;
                let entry = lab.closest(
                  '.ashby-application-form-field-entry, [class*="_fieldEntry_"], [class*="fieldEntry"]'
                ) || lab.parentElement;
                for (let i = 0; i < 10 && entry; i++) {
                  for (const inp of entry.querySelectorAll(
                    'input, textarea, [contenteditable="true"], [role="textbox"]'
                  )) {
                    if (!isVis(inp)) continue;
                    const ty = (inp.type || '').toLowerCase();
                    if (['hidden','file','checkbox','radio','submit','button'].includes(ty)) continue;
                    inp.setAttribute('data-fastfill-zip-probe', '1');
                    return true;
                  }
                  entry = entry.parentElement;
                }
              }
              return false;
            }"""
        )
        if found:
            loc = page.locator('[data-fastfill-zip-probe="1"]').first
            if await loc.count():
                return loc
    except Exception:
        pass
    return None


async def _try_fill_zip_via_dom(page, zip_val: str) -> tuple[bool, str]:
    """Direct DOM fill when Playwright locators miss Ashby _heading_ zip entries."""
    try:
        rb = await page.evaluate(
            """(zip) => {
              const isVis = el => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 2 && r.height > 2
                  && s.visibility !== 'hidden' && s.display !== 'none';
              };
              const setVal = (inp, v) => {
                inp.focus();
                inp.value = v;
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                return (inp.value || '').trim();
              };
              for (const lab of document.querySelectorAll(
                'label, [class*="question"], [class*="Question"], [class*="_heading_"]'
              )) {
                const t = (lab.innerText || '').toLowerCase();
                if (!/home\\s+zip|what is your home zip|zip\\s+code|postal/.test(t)) continue;
                let entry = lab.closest(
                  '.ashby-application-form-field-entry, [class*="_fieldEntry_"], [class*="fieldEntry"]'
                ) || lab.parentElement;
                for (let i = 0; i < 10 && entry; i++) {
                  for (const inp of entry.querySelectorAll('input, textarea')) {
                    if (!isVis(inp)) continue;
                    const ty = (inp.type || '').toLowerCase();
                    if (['hidden','file','checkbox','radio','submit','button'].includes(ty)) continue;
                    const cur = (inp.value || '').trim();
                    if (cur && cur.includes(String(zip))) return cur;
                    return setVal(inp, String(zip));
                  }
                  entry = entry.parentElement;
                }
              }
              return '';
            }""",
            str(zip_val)[:16],
        )
        rb_s = str(rb or "").strip()
        ok = bool(rb_s) and str(zip_val) in rb_s and any(c.isdigit() for c in rb_s)
        return ok, rb_s
    except Exception:
        return False, ""


def _html_has_ashby_zip_question(html: str) -> bool:
    """True when page HTML includes an Ashby zip/postal question label."""
    low = (html or "").lower()
    if not re.search(r"home\s+zip|what is your home zip|zip\s+code|postal\s+code", low):
        return False
    return bool(
        re.search(
            r"home\s+zip|what is your home zip|zip\s+code|postal",
            low,
        )
    )


async def _ashby_zip_field_present(page) -> bool:
    """True when the form includes a zip/postal question or mounted input.

    ATS3-002: HTML zip question with input not-yet-mounted is still present
    (wait for mount after Location). Only treat as absent when neither the
    question text nor a fillable zip input exists.
    """
    try:
        html = await page.content()
        if _html_has_ashby_zip_question(html):
            return True
        loc = await _wait_ashby_zip_input(page, timeout_ms=2500)
        return loc is not None
    except Exception:
        return False


async def _scroll_toward_zip_hint(page, *, allow_wheel: bool = True) -> bool:
    """Scroll form toward zip/postal — prefer heading scroll, wheel only as last resort.

    Returns True when any scroll action ran. Callers must cap wheel/scroll budgets
    (ATS2-017 / FILL3-005) — never thrash forever.
    """
    for fn in (
        lambda: page.get_by_text(
            re.compile(r"home\s+zip\s+code|what is your home zip|postal\s+code", re.I)
        ).first.scroll_into_view_if_needed(timeout=800),
        lambda: page.locator(_ASHBY_ZIP_ENTRY).first.scroll_into_view_if_needed(
            timeout=800
        ),
    ):
        try:
            await fn()
            await page.wait_for_timeout(200)
            return True
        except Exception:
            continue
    if allow_wheel:
        try:
            await page.mouse.wheel(0, 400)
            await page.wait_for_timeout(200)
            return True
        except Exception:
            pass
    return False


async def _wait_ashby_zip_dom_event(page, *, timeout_ms: int = 3500) -> bool:
    """Event-driven wait: resolve when a fillable zip/postal input mounts (ATS2-017)."""
    if timeout_ms <= 0:
        return False
    try:
        return bool(
            await page.wait_for_function(
                """() => {
                  const isVis = el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 2 && r.height > 2
                      && s.visibility !== 'hidden' && s.display !== 'none';
                  };
                  const labs = document.querySelectorAll(
                    'label, [class*="_heading_"], [class*="question"], [class*="Question"]'
                  );
                  for (const lab of labs) {
                    const t = (lab.innerText || '').toLowerCase();
                    if (!/home\\s+zip|what is your home zip|zip\\s+code|postal\\s+code|\\bzip\\b|\\bpostal\\b/.test(t))
                      continue;
                    let entry = lab.closest(
                      '.ashby-application-form-field-entry, [class*="_fieldEntry_"], [class*="fieldEntry"]'
                    ) || lab.parentElement;
                    for (let i = 0; i < 8 && entry; i++) {
                      for (const inp of entry.querySelectorAll(
                        'input, textarea, [contenteditable="true"], [role="textbox"]'
                      )) {
                        if (!isVis(inp)) continue;
                        const ty = (inp.type || '').toLowerCase();
                        if (['hidden','file','checkbox','radio','submit','button'].includes(ty))
                          continue;
                        return true;
                      }
                      entry = entry.parentElement;
                    }
                  }
                  for (const inp of document.querySelectorAll(
                    'input[autocomplete="postal-code"], input[name*="zip" i], input[name*="postal" i],'
                    + 'input[placeholder*="zip" i], input[aria-label*="zip" i]'
                  )) {
                    if (isVis(inp)) return true;
                  }
                  return false;
                }""",
                timeout=timeout_ms,
            )
        )
    except Exception:
        return False


def classify_ashby_zip_miss(*, html_has_zip_question: bool) -> str:
    """Honest leftover taxonomy for Location→zip miss (ATS2-017).

    - ``zip_dependent_never_revealed``: zip question in HTML but input never mounts
    - ``zip_field_not_found_after_location``: no HTML question either (locator miss)
    """
    if html_has_zip_question:
        return "zip_dependent_never_revealed"
    return "zip_field_not_found_after_location"


async def _wait_ashby_zip_input(page, *, timeout_ms: int = 8000):
    """Poll until a real fillable zip <input>/<textarea> is visible."""
    import time

    start = time.monotonic()
    deadline = timeout_ms / 1000.0
    # Prefer explicit name from Ashby's required zip UUID when known; else label scope.
    # Include type-less inputs + get_by_label — Airwallex "home zip code" was missed
    # when only `input[type=text]` under field-entry matched.
    candidates = [
        (
            ".ashby-application-form-field-entry:has([class*='_heading_']:has-text('home zip')) "
            "input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio])"
        ),
        (
            ".ashby-application-form-field-entry:has([class*='_heading_']:has-text('zip')) "
            "input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio])"
        ),
        (
            "[class*=\"_fieldEntry_\"]:has([class*='_heading_']:has-text('home zip')) "
            "input:not([type=hidden]):not([type=file])"
        ),
        (
            "[class*=\"_fieldEntry_\"]:has([class*='_heading_']:has-text('zip')) "
            "input:not([type=hidden]):not([type=file])"
        ),
        (
            ".ashby-application-form-field-entry:has(label:has-text('zip')) "
            "input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio])"
        ),
        (
            ".ashby-application-form-field-entry:has(label:has-text('Zip')) "
            "input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio])"
        ),
        (
            ".ashby-application-form-field-entry:has(label:has-text('postal')) "
            "input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio])"
        ),
        (
            "[class*=\"_fieldEntry_\"]:has(label:has-text('zip')) "
            "input:not([type=hidden]):not([type=file])"
        ),
        (
            "[class*=\"_fieldEntry_\"]:has(label:has-text('Zip')) "
            "input:not([type=hidden]):not([type=file])"
        ),
        (
            "[class*=\"_fieldEntry_\"]:has(label:has-text('home zip')) "
            "input:not([type=hidden]):not([type=file])"
        ),
        "label:has-text('zip code') >> xpath=ancestor::*[contains(@class,'field') or contains(@class,'Field')][1] //input",
        "input[autocomplete='postal-code']",
        "input[name*='zip' i]",
        "input[name*='postal' i]",
        "input[placeholder*='zip' i]",
        "input[placeholder*='postal' i]",
        "input[aria-label*='zip' i]",
        "input[aria-label*='postal' i]",
        _ASHBY_ZIP_INPUT,
    ]
    while time.monotonic() - start < deadline:
        # Playwright label API (Airwallex: "What is your home zip code?")
        try:
            by_role = page.get_by_role(
                "textbox", name=re.compile(r"home\s+zip|zip\s+code|postal", re.I)
            ).first
            if await by_role.count() and await by_role.is_visible(timeout=200):
                return by_role
        except Exception:
            pass
        try:
            by_lab = page.get_by_label(re.compile(r"zip|postal", re.I)).first
            if await by_lab.count() and await by_lab.is_visible(timeout=200):
                tag = await by_lab.evaluate("el => (el.tagName || '').toLowerCase()")
                if tag in ("input", "textarea"):
                    return by_lab
        except Exception:
            pass
        # Label text not always htmlFor-associated on Ashby — climb from text node
        try:
            by_txt = page.get_by_text(_ASHBY_ZIP_HEADING).first
            if await by_txt.count():
                try:
                    await by_txt.scroll_into_view_if_needed(timeout=800)
                except Exception:
                    pass
                for xp in (
                    "xpath=ancestor::*[contains(@class,'field') or contains(@class,'Field') or contains(@class,'entry')][1]//input[not(@type='hidden') and not(@type='file')]",
                    "xpath=ancestor::*[contains(@class,'field') or contains(@class,'Field') or contains(@class,'entry')][1]//*[@contenteditable='true' or @role='textbox']",
                    "xpath=following::input[not(@type='hidden')][1]",
                ):
                    inp = by_txt.locator(xp).first
                    if await inp.count() and await inp.is_visible(timeout=200):
                        return inp
        except Exception:
            pass
        for sel in candidates:
            loc = page.locator(sel).first
            try:
                if await loc.count() == 0:
                    continue
                if not await loc.is_visible(timeout=250):
                    continue
                tag = await loc.evaluate(
                    "el => (el.tagName || '').toLowerCase()"
                )
                if tag not in ("input", "textarea") and not (
                    await loc.evaluate(
                        "el => el.isContentEditable || el.getAttribute('role')==='textbox'"
                    )
                ):
                    continue
                itype = (
                    (await loc.get_attribute("type")) or "text"
                ).lower()
                if itype in ("hidden", "file", "checkbox", "radio", "submit", "button"):
                    continue
                # Confirm it sits under a zip/postal label when possible
                near_zip = await loc.evaluate(
                    """el => {
                      const entry = el.closest(
                        '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
                      ) || el.parentElement;
                      const lab = entry && entry.querySelector('label');
                      const t = (lab && lab.innerText || el.getAttribute('aria-label')
                        || el.placeholder || '').toLowerCase();
                      return /zip|postal/.test(t) || /zip|postal/.test(el.name || '');
                    }"""
                )
                if near_zip or "postal" in sel.lower() or "zip" in sel.lower():
                    return loc
            except Exception:
                continue
        try:
            js_loc = await _find_ashby_zip_via_dom(page)
            if js_loc is not None:
                return js_loc
        except Exception:
            pass
        try:
            await page.wait_for_timeout(350)
        except Exception:
            break
    return None


async def _await_zip_after_location(
    page,
    *,
    timeout_ms: int = 28000,
    location_option_clicked: bool = False,
    max_scrolls: int = 3,
) -> Any:
    """Poll for dependent zip after Location commit (ATS2-017).

    Longer settle + Tab blur, then event-driven mount wait. Scroll budget is
    capped (``max_scrolls``) — no endless wheel thrash (FILL3-005).
    """
    import time

    settle_ms = 2200 if location_option_clicked else 1400
    try:
        await page.wait_for_timeout(settle_ms)
    except Exception:
        pass

    # Blur Location combobox — Ashby often mounts zip only after list pick + blur.
    try:
        combo = page.locator(_ASHBY_LOCATION_COMBO).first
        if await combo.count():
            await combo.press("Tab")
            await page.wait_for_timeout(500)
    except Exception:
        pass

    start = time.monotonic()
    deadline = timeout_ms / 1000.0
    slice_ms = max(3500, timeout_ms // 5)
    scroll_budget = max(0, int(max_scrolls))
    while time.monotonic() - start < deadline:
        remaining_ms = int(max(800, (deadline - time.monotonic()) * 1000))
        zip_loc = await _wait_ashby_zip_input(
            page, timeout_ms=min(slice_ms, remaining_ms)
        )
        if zip_loc is not None:
            try:
                if await zip_loc.count() > 0:
                    return zip_loc
            except Exception:
                return zip_loc

        # Event-driven: wait for DOM mount instead of fixed sleep + wheel.
        rem_after = int(max(400, (deadline - time.monotonic()) * 1000))
        mounted = await _wait_ashby_zip_dom_event(
            page, timeout_ms=min(4000, rem_after)
        )
        if mounted:
            zip_loc = await _wait_ashby_zip_input(page, timeout_ms=1500)
            if zip_loc is not None:
                return zip_loc

        if scroll_budget > 0:
            # Prefer heading scroll; allow one wheel only on the last scroll slot.
            await _scroll_toward_zip_hint(
                page, allow_wheel=(scroll_budget == 1)
            )
            scroll_budget -= 1
        else:
            try:
                await page.wait_for_timeout(400)
            except Exception:
                break
        # If zip question never appears in HTML, keep polling lightly (tenant lag).
        try:
            html = await page.content()
            if not _html_has_ashby_zip_question(html):
                await page.wait_for_timeout(350)
        except Exception:
            pass
    return None


async def _reopen_location_for_zip_reveal(
    page,
    *,
    city_val: str,
    state_val: str,
    country_val: str,
) -> dict:
    """Re-open Location combobox and re-pick once when zip never mounted (ATS2-017)."""
    from verified_select import (
        fill_location_autocomplete,
        location_option_aliases,
    )

    st_full = "Illinois" if str(state_val).upper() == "IL" else str(state_val)
    aliases = location_option_aliases(
        str(city_val),
        state=str(state_val),
        state_full=st_full,
        country=str(country_val),
    )

    async def _zip_visible() -> bool:
        loc = await _wait_ashby_zip_input(page, timeout_ms=700)
        return loc is not None

    out: dict = {"ok": False, "option_clicked": False, "reason": "no_combo"}
    try:
        combo = page.locator(_ASHBY_LOCATION_COMBO).first
        if not await combo.count():
            return out
        try:
            await combo.click(timeout=2500, force=True)
            await page.wait_for_timeout(250)
        except Exception:
            pass
        loc_result = await fill_location_autocomplete(
            page,
            combo,
            city=str(city_val),
            state=str(state_val),
            state_full=st_full,
            country=str(country_val),
            aliases=aliases,
            commit_probe=_zip_visible,
            timeout_ms=6500,
        )
        out = dict(loc_result or {})
        out["reason"] = out.get("reason") or "location_reopen_for_zip"
        # Tab blur — never Escape (ATS3-012).
        try:
            await combo.press("Tab")
        except Exception:
            try:
                await page.keyboard.press("Tab")
            except Exception:
                pass
        await page.wait_for_timeout(600)
    except Exception as e:
        out["error"] = str(e)[:120]
        out["reason"] = "location_reopen_failed"
    return out


async def fill_ashby_location_then_zip(page, values: dict) -> list[dict]:
    """Fill/confirm Location first, wait for dependent zip, then ADDRESS_ZIP with live readback.

    Ashby often remounts the zip input after the Location combobox commits. Filling
    zip too early (or trusting a stale extract readback) leaves the visible field
    showing ``Type here...`` while the report falsely claims verified.

    Location must be **selected from the listbox** (type → wait → click option).
    Filter text like ``Springfield, Illinois, United States`` is NOT a commit —
    Airwallex: type-without-select left zip hidden.
    """
    from verified_select import (
        fill_location_autocomplete,
        location_display_matches,
        location_option_aliases,
        probe_location_committed,
        read_location_autocomplete_value,
    )

    filled: list[dict] = []
    zip_val = values.get(ADDRESS_ZIP)
    city_val = values.get(ADDRESS_CITY) or "Springfield"
    state_val = values.get(ADDRESS_STATE) or "IL"
    country_val = values.get(ADDRESS_COUNTRY) or "United States"
    if not zip_val or not validate_filled(ADDRESS_ZIP, str(zip_val)):
        return filled

    # Tenant forms without a zip question (e.g. current Airwallex posting) — not a fill fail.
    if not await _ashby_zip_field_present(page):
        filled.append(
            {
                "via": "ashby_location_zip",
                "type": ADDRESS_ZIP,
                "ok": True,
                "verified": False,
                "reason": "zip_field_absent_on_form",
                "not_applicable": True,
                "readback": "",
            }
        )
        return filled

    async def _zip_visible() -> bool:
        loc = await _wait_ashby_zip_input(page, timeout_ms=900)
        try:
            return (
                loc is not None
                and await loc.count() > 0
                and await loc.is_visible(timeout=200)
            )
        except Exception:
            return loc is not None

    # Ensure Location combobox is committed before zip (zip is often dependent).
    # Never treat filter input_value as already-correct without option click / zip.
    try:
        combo = page.locator(_ASHBY_LOCATION_COMBO).first
        if await combo.count() and city_val:
            st_full = (
                "Illinois" if str(state_val).upper() == "IL" else str(state_val)
            )
            aliases = location_option_aliases(
                str(city_val),
                state=str(state_val),
                state_full=st_full,
                country=str(country_val),
            )
            shown_pre = await read_location_autocomplete_value(combo)
            probe_pre = await probe_location_committed(
                page,
                combo,
                aliases,
                commit_probe=_zip_visible,
                city=str(city_val),
                state=str(state_val),
                state_full=st_full,
                country=str(country_val),
            )
            display_ok = location_display_matches(
                shown_pre,
                aliases,
                city=str(city_val),
                state=str(state_val),
                state_full=st_full,
                country=str(country_val),
            )
            zip_already = await _zip_visible()
            # Skip Location retype only when committed AND dependent zip is visible.
            # Filter text matching Springfield, IL, US without list pick hides zip on Airwallex.
            if (probe_pre.get("committed") or display_ok) and zip_already:
                loc_result = {
                    "ok": True,
                    "verified": True,
                    "committed": True,
                    "skipped_already_correct": True,
                    "reason": "location_already_committed_skip",
                    "readback": (probe_pre.get("shown") or shown_pre or "")[:120],
                    "picked": (probe_pre.get("shown") or shown_pre or "")[:120],
                    "option_clicked": False,
                    "dependent_revealed": bool(probe_pre.get("dependent_revealed")),
                }
            else:
                loc_result = await fill_location_autocomplete(
                    page,
                    combo,
                    city=str(city_val),
                    state=str(state_val),
                    state_full=st_full,
                    country=str(country_val),
                    aliases=aliases,
                    commit_probe=_zip_visible,
                    timeout_ms=6500,
                )
                if loc_result.get("skipped_already_correct") and not await _zip_visible():
                    # Display matched but zip still hidden — force list pick once.
                    loc_result = await fill_location_autocomplete(
                        page,
                        combo,
                        city=str(city_val),
                        state=str(state_val),
                        state_full=st_full,
                        country=str(country_val),
                        aliases=aliases,
                        commit_probe=_zip_visible,
                        timeout_ms=6500,
                    )
                    loc_result["reason"] = loc_result.get("reason") or "location_repick_for_zip"
            shown = loc_result.get("readback") or await read_location_autocomplete_value(
                combo
            )
            if loc_result.get("skipped_already_correct"):
                committed = True
            else:
                committed = bool(loc_result.get("committed") or loc_result.get("ok"))
            if not committed and location_display_matches(
                shown,
                aliases,
                city=str(city_val),
                state=str(state_val),
                state_full=st_full,
                country=str(country_val),
            ):
                # ATS3-001: display match alone is NOT a commit — require list
                # pick or dependent zip reveal (filter text ≠ committed Location).
                if bool(loc_result.get("option_clicked")) or bool(
                    loc_result.get("dependent_revealed")
                ):
                    committed = True
            filled.append(
                {
                    "via": "ashby_location_zip",
                    "layer": "ashby",
                    "type": ADDRESS_CITY,
                    "mode": "location_autocomplete",
                    "label": "Location",
                    "ok": committed,
                    "verified": committed,
                    "committed": committed,
                    "option_clicked": bool(loc_result.get("option_clicked")),
                    "value": str(city_val)[:80],
                    "picked": (loc_result.get("picked") or "")[:120],
                    "readback": (shown or loc_result.get("picked") or "")[:120],
                    "query": loc_result.get("query"),
                    "skipped_already_correct": bool(
                        loc_result.get("skipped_already_correct")
                    ),
                    "reason": (
                        "already_correct_skip"
                        if loc_result.get("skipped_already_correct")
                        else (None if committed else loc_result.get("error"))
                    ),
                    "options": (loc_result.get("options") or [])[:8],
                    "dependent_revealed": bool(loc_result.get("dependent_revealed")),
                }
            )
            try:
                await page.wait_for_timeout(700)
            except Exception:
                pass
            if bool(loc_result.get("option_clicked")):
                # ATS3-012: prefer Tab blur + wait for zip visibility.
                # Escape can cancel dependent-field reveal (Airwallex zip).
                try:
                    await combo.press("Tab")
                except Exception:
                    try:
                        await page.keyboard.press("Tab")
                    except Exception:
                        pass
                zip_early = await _wait_ashby_zip_input(page, timeout_ms=4500)
                if zip_early is None:
                    await _scroll_toward_zip_hint(page)
                    await _wait_ashby_zip_input(page, timeout_ms=3000)
            if not committed:
                filled.append(
                    {
                        "via": "ashby_location_zip",
                        "type": ADDRESS_ZIP,
                        "ok": False,
                        "verified": False,
                        "reason": "blocked_on_location_uncommitted",
                        "flash_candidate": True,
                    }
                )
                return filled
    except Exception as e:
        filled.append(
            {
                "via": "ashby_location_zip",
                "type": ADDRESS_CITY,
                "ok": False,
                "reason": "location_ensure_failed",
                "error": str(e)[:120],
            }
        )

    option_clicked = bool(
        (filled[-1] if filled else {}).get("option_clicked")
        or (filled[-1] if filled else {}).get("reason")
        in ("location_repick_for_zip", "location_reopen_for_zip")
    )
    # ATS2-017: longer event-driven wait; capped scrolls (no wheel thrash forever).
    zip_loc = await _await_zip_after_location(
        page,
        timeout_ms=28000,
        location_option_clicked=option_clicked,
        max_scrolls=3,
    )

    # One Location re-open if dependent zip still missing (tenant remount race).
    location_reopened = False
    if zip_loc is None and city_val:
        reopen = await _reopen_location_for_zip_reveal(
            page,
            city_val=str(city_val),
            state_val=str(state_val),
            country_val=str(country_val),
        )
        location_reopened = True
        filled.append(
            {
                "via": "ashby_location_zip",
                "type": ADDRESS_CITY,
                "mode": "location_reopen_for_zip",
                "ok": bool(reopen.get("ok") or reopen.get("committed")),
                "option_clicked": bool(reopen.get("option_clicked")),
                "reason": reopen.get("reason") or "location_reopen_for_zip",
                "dependent_revealed": bool(reopen.get("dependent_revealed")),
            }
        )
        zip_loc = await _await_zip_after_location(
            page,
            timeout_ms=18000,
            location_option_clicked=bool(reopen.get("option_clicked")),
            max_scrolls=2,
        )

    if zip_loc is None:
        html_has_q = False
        try:
            html = await page.content()
            html_has_q = _html_has_ashby_zip_question(html)
            if html_has_q:
                ok_dom, rb_dom = await _try_fill_zip_via_dom(page, str(zip_val))
                if ok_dom:
                    filled.append(
                        {
                            "via": "ashby_location_zip",
                            "layer": "ashby",
                            "type": ADDRESS_ZIP,
                            "label": "What is your home zip code?",
                            "selector": "dom_zip_fill",
                            "ok": True,
                            "verified": True,
                            "value": str(zip_val)[:16],
                            "readback": rb_dom[:120],
                            "reason": "dom_zip_fill",
                            "flash_candidate": False,
                            "location_reopened": location_reopened,
                        }
                    )
                    return filled
        except Exception:
            pass
        miss_reason = classify_ashby_zip_miss(html_has_zip_question=html_has_q)
        filled.append(
            {
                "via": "ashby_location_zip",
                "type": ADDRESS_ZIP,
                "ok": False,
                "verified": False,
                "reason": miss_reason,
                "flash_candidate": True,
                "html_has_zip_question": html_has_q,
                "location_reopened": location_reopened,
                # Honest leftover: zip question present but input never mounted.
                "not_applicable": False,
            }
        )
        return filled

    try:
        if await zip_loc.count() == 0:
            html_has_q = False
            try:
                html_has_q = _html_has_ashby_zip_question(await page.content())
            except Exception:
                pass
            filled.append(
                {
                    "via": "ashby_location_zip",
                    "type": ADDRESS_ZIP,
                    "ok": False,
                    "verified": False,
                    "reason": classify_ashby_zip_miss(
                        html_has_zip_question=html_has_q
                    ),
                    "flash_candidate": True,
                    "html_has_zip_question": html_has_q,
                    "location_reopened": location_reopened,
                }
            )
            return filled

        async def _read_zip_value(target) -> str:
            try:
                return (await target.input_value() or "").strip()
            except Exception:
                pass
            try:
                return (
                    await target.evaluate(
                        "el => (el.value || el.innerText || el.textContent || '').trim()"
                    )
                    or ""
                ).strip()
            except Exception:
                return ""

        async def _commit_zip(target) -> str:
            # SKIP thrash: do not clear/retype when zip already correct
            existing = await _read_zip_value(target)
            if _value_already_correct(str(zip_val), existing):
                return existing
            await target.click(timeout=3000, force=True)
            try:
                await target.fill("")
            except Exception:
                # Some Ashby controls reject fill on the wrapper — use keyboard.
                try:
                    await target.press("Meta+a")
                    await target.press("Backspace")
                except Exception:
                    pass
            try:
                await target.fill(str(zip_val)[:16])
            except Exception:
                try:
                    await target.type(str(zip_val)[:16], delay=20)
                except Exception:
                    await target.evaluate(
                        "(el, v) => { el.focus(); el.value = v; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }",
                        str(zip_val)[:16],
                    )
            try:
                await target.press("Tab")
            except Exception:
                try:
                    await target.blur()
                except Exception:
                    pass
            try:
                await page.wait_for_timeout(450)
            except Exception:
                pass
            # Re-resolve after possible remount
            fresh = await _wait_ashby_zip_input(page, timeout_ms=2500)
            use = fresh if fresh is not None else target
            return await _read_zip_value(use)

        readback = await _commit_zip(zip_loc)
        if is_empty_ui_value(readback) or str(zip_val) not in readback:
            zip_loc2 = await _wait_ashby_zip_input(page, timeout_ms=3000)
            if zip_loc2 is not None:
                readback = await _commit_zip(zip_loc2)

        ok = (
            not is_empty_ui_value(readback)
            and str(zip_val) in readback
            and any(c.isdigit() for c in readback)
        )
        filled.append(
            {
                "via": "ashby_location_zip",
                "layer": "ashby",
                "type": ADDRESS_ZIP,
                "label": "What is your home zip code?",
                "selector": "ashby_zip_input",
                "ok": ok,
                "verified": ok,
                "value": str(zip_val)[:16],
                "readback": readback[:120],
                "reason": None if ok else "zip_readback_empty_or_placeholder",
                "flash_candidate": not ok,
            }
        )
    except Exception as e:
        filled.append(
            {
                "via": "ashby_location_zip",
                "type": ADDRESS_ZIP,
                "ok": False,
                "verified": False,
                "reason": "zip_fill_failed",
                "error": str(e)[:160],
                "flash_candidate": True,
            }
        )
    return filled


async def list_ashby_field_entries(page) -> list[dict[str, Any]]:
    """Return Ashby question blocks with label + control shape (no PII)."""
    try:
        rows = await page.evaluate(
            """() => {
  const roots = Array.from(document.querySelectorAll(
    '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
  ));
  return roots.map((el) => {
    const lab = el.querySelector(
      'label.ashby-application-form-question-title, label[class*="_heading_"]'
    );
    let label = (lab && lab.innerText || '').trim().replace(/\\s+/g, ' ');
    // Bottom data-consent checkbox often has no heading — use option text / id.
    if (!label) {
      const agree = el.querySelector(
        'input[type=checkbox][id*="consent" i], input[type=checkbox][name="I agree"],' +
        'label[for*="consent" i], input[id*="data_consent" i]'
      );
      if (agree) {
        label = (
          (agree.labels && agree.labels[0] && agree.labels[0].innerText) ||
          agree.getAttribute('name') ||
          'I agree'
        ).trim();
        if (/^i agree$/i.test(label) || /consent/i.test(agree.id || '')) {
          label = 'I agree to data privacy and terms';
        }
      }
    }
    const yesnoWrap = el.querySelector('[class*="_yesno_"]');
    let yesnoBtns = yesnoWrap
      ? Array.from(yesnoWrap.querySelectorAll('button'))
      : [];
    // Segmented Yes/No without _yesno_ class (Truelogic LATAM gate)
    if (!yesnoWrap) {
      const seg = Array.from(el.querySelectorAll('button, [role="button"]')).filter((b) => {
        const t = (b.innerText || b.textContent || '').trim().toLowerCase();
        return t === 'yes' || t === 'no';
      });
      if (seg.length >= 2) yesnoBtns = seg;
    }
    const yesno = !!yesnoWrap || (
      yesnoBtns.length >= 2
      && yesnoBtns.some((b) => /^yes$/i.test((b.innerText || b.textContent || '').trim()))
      && yesnoBtns.some((b) => /^no$/i.test((b.innerText || b.textContent || '').trim()))
    );
    const yesBtn = yesnoBtns.length > 0;
    const radios = Array.from(el.querySelectorAll('input[type=radio]')).map((r) => ({
      id: r.id || '',
      name: r.name || '',
      opt: (
        (r.labels && r.labels[0] && r.labels[0].innerText) ||
        r.getAttribute('aria-label') ||
        ''
      ).trim().slice(0, 120),
      checked: !!r.checked,
    }));
    const roleRadios = Array.from(el.querySelectorAll('[role=radio]')).map((r) => ({
      id: r.id || '',
      name: r.getAttribute('name') || r.getAttribute('aria-label') || '',
      opt: (
        (r.innerText || r.textContent || r.getAttribute('aria-label') || '')
      ).trim().slice(0, 120),
      checked: r.getAttribute('aria-checked') === 'true',
      ariaChecked: r.getAttribute('aria-checked') || '',
    }));
    const checks = Array.from(el.querySelectorAll('input[type=checkbox]')).map((c) => ({
      id: c.id || '',
      name: c.name || '',
      opt: (
        (c.labels && c.labels[0] && c.labels[0].innerText) ||
        c.getAttribute('name') ||
        ''
      ).trim().slice(0, 120),
      checked: !!c.checked,
    }));
    const yesnoSelected = (() => {
      const wrap = el.querySelector('[class*="_yesno_"]');
      const scope = wrap || el;
      const on = scope.querySelector(
        'button[aria-pressed="true"], button[class*="selected"], button[data-selected="true"]'
      );
      if (on) return (on.innerText || on.textContent || '').trim();
      for (const b of scope.querySelectorAll('button, [role="button"]')) {
        const cls = (b.className || '').toLowerCase();
        const t = (b.innerText || b.textContent || '').trim().toLowerCase();
        if (/selected|active|pressed/.test(cls)) return (b.innerText || b.textContent || '').trim();
        if ((t === 'yes' || t === 'no') && (
          b.getAttribute('aria-pressed') === 'true'
          || b.getAttribute('data-selected') === 'true'
          || /selected|active|pressed/.test(cls)
        )) return (b.innerText || b.textContent || '').trim();
      }
      return '';
    })();
    const text = el.querySelector(
      'textarea, input[type=text], input[type=email], input[type=tel], input[type=url]'
    );
    const file = el.querySelector('input[type=file]');
    const combo = el.querySelector('[role=combobox], input[role=combobox]');
    const textVal = text ? (text.value || '').trim() : '';
    const textPh = text ? (text.placeholder || '').trim().toLowerCase() : '';
    const textEmpty = !!text && (
      !textVal
      || textVal.toLowerCase() === textPh
      || textPh.startsWith('type here')
      || textPh.startsWith('start typing')
    );
    return {
      label: label.slice(0, 200),
      yesno,
      yesBtn,
      yesnoSelected,
      radios,
      roleRadios,
      checks,
      hasText: !!text,
      textName: text ? (text.name || text.id || '') : '',
      textTag: text ? text.tagName.toLowerCase() : '',
      textValue: textVal.slice(0, 80),
      textEmpty,
      hasFile: !!file,
      hasCombo: !!combo,
      path: el.getAttribute('data-field-path') || '',
    };
  });
}"""
        )
    except Exception:
        return []
    return [r for r in (rows or []) if isinstance(r, dict)]


async def _click_yesno_in_entry(page, label: str, want_yes: bool, *, report: dict | None = None) -> dict:
    """Click Yes or No button inside the Ashby field-entry for ``label``.

    Handles both ``[class*="_yesno_"]`` wrappers and plain segmented button pairs.
    """
    from fill_step_log import note_step

    target = "Yes" if want_yes else "No"
    frag = re.escape(label[:48].strip())
    before_sel = ""
    try:
        entry = page.locator(
            ".ashby-application-form-field-entry, [class*=\"_fieldEntry_\"]"
        ).filter(has=page.locator("label", has_text=re.compile(frag, re.I)))
        # SKIP thrash: already selected
        try:
            selected = await entry.evaluate(
                """(el) => {
                  const wrap = el.querySelector('[class*="_yesno_"]');
                  const scope = wrap || el;
                  const on = scope.querySelector('button[aria-pressed="true"]');
                  if (on) return (on.innerText || on.textContent || '').trim();
                  for (const b of scope.querySelectorAll('button, [role="button"]')) {
                    const cls = (b.className || '').toLowerCase();
                    const t = (b.innerText || b.textContent || '').trim();
                    if (/selected|active|pressed/.test(cls)) return t;
                    if ((t.toLowerCase() === 'yes' || t.toLowerCase() === 'no')
                        && (b.getAttribute('aria-pressed') === 'true'
                          || b.getAttribute('data-selected') === 'true'
                          || /selected|active|pressed/.test(cls))) return t;
                  }
                  return '';
                }"""
            )
            before_sel = str(selected or "")
            if selected and selected.lower() == target.lower():
                note_step(
                    report,
                    action="skip_already_correct",
                    label=label[:80],
                    field_type="yes_no",
                    before=before_sel,
                    after=before_sel,
                    via="ashby_widgets",
                    layer="ashby",
                    reason="yesno_already_selected",
                )
                return {
                    "ok": True,
                    "verified": True,
                    "picked": selected,
                    "readback": selected,
                    "skipped_already_correct": True,
                }
        except Exception:
            pass
        btn = entry.locator('[class*="_yesno_"] button', has_text=re.compile(rf"^{target}$", re.I))
        if await btn.count() == 0:
            btn = entry.locator("button", has_text=re.compile(rf"^{target}$", re.I))
        if await btn.count() == 0:
            btn = entry.get_by_role("button", name=re.compile(rf"^{target}$", re.I))
        loc = btn.first
        if await loc.count() == 0:
            note_step(
                report,
                action="click_yes_no",
                label=label[:80],
                field_type="yes_no",
                before=before_sel,
                after="",
                via="ashby_widgets",
                layer="ashby",
                reason="yesno_button_missing",
                extra={"wanted": target},
            )
            return {"ok": False, "reason": "yesno_button_missing", "wanted": target}
        await loc.click(timeout=3000, force=True)
        try:
            readback = await entry.evaluate(
                """(el) => {
                  const wrap = el.querySelector('[class*="_yesno_"]');
                  const scope = wrap || el;
                  const on = scope.querySelector('button[aria-pressed="true"]');
                  if (on) return (on.innerText || on.textContent || '').trim();
                  for (const b of scope.querySelectorAll('button, [role="button"]')) {
                    const cls = (b.className || '').toLowerCase();
                    const t = (b.innerText || b.textContent || '').trim();
                    if (/selected|active|pressed/.test(cls)) return t;
                    if ((t.toLowerCase() === 'yes' || t.toLowerCase() === 'no')
                        && (b.getAttribute('aria-pressed') === 'true'
                          || b.getAttribute('data-selected') === 'true'
                          || /selected|active|pressed/.test(cls))) return t;
                  }
                  return '';
                }"""
            )
        except Exception:
            readback = target
        ok = bool(readback) and readback.lower() == target.lower()
        note_step(
            report,
            action="click_yes_no",
            label=label[:80],
            field_type="yes_no",
            before=before_sel,
            after=readback or target,
            via="ashby_widgets",
            layer="ashby",
            reason=None if ok else "yesno_readback_mismatch",
            extra={"wanted": target, "ok": ok},
        )
        return {
            "ok": ok,
            "verified": ok,
            "picked": readback or target,
            "readback": readback or target,
        }
    except Exception as e:
        note_step(
            report,
            action="click_yes_no",
            label=label[:80],
            field_type="yes_no",
            before=before_sel,
            after="",
            via="ashby_widgets",
            layer="ashby",
            reason="yesno_click_failed",
            extra={"error": str(e)[:120]},
        )
        return {"ok": False, "reason": "yesno_click_failed", "error": str(e)[:120]}


async def _click_option_in_entry(page, label: str, candidates: list[str], *, role: str = "radio") -> dict:
    """Click radio/checkbox option under a labeled Ashby field-entry."""
    frag = re.escape((label or "")[:40].strip()) if label else ""
    cands = [c for c in (candidates or []) if c]
    try:
        if frag:
            entry = page.locator(
                ".ashby-application-form-field-entry, [class*=\"_fieldEntry_\"]"
            ).filter(has=page.locator("label", has_text=re.compile(frag, re.I)))
        else:
            entry = page.locator(
                ".ashby-application-form-field-entry, [class*=\"_fieldEntry_\"]"
            ).filter(
                has=page.locator(
                    "input[id*='consent' i], input[name='I agree'], "
                    "input[id*='data_consent' i]"
                )
            )
        # Collect visible options with checked state; score via gh_select._score_option
        options = await entry.evaluate(
            """(el) => {
              const out = [];
              for (const inp of el.querySelectorAll('input[type=radio], input[type=checkbox]')) {
                let olab = '';
                const id = inp.id;
                if (id) {
                  const l = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                  if (l) olab = (l.innerText || l.textContent || '').trim();
                }
                if (!olab) {
                  const wrap = inp.closest('label');
                  if (wrap) olab = (wrap.innerText || wrap.textContent || '').trim();
                }
                if (!olab) olab = inp.value || '';
                out.push({label: olab.slice(0, 120), checked: !!inp.checked, id: inp.id || ''});
              }
              return out;
            }"""
        )
        for opt in options or []:
            if opt.get("checked") and opt.get("label"):
                olab = str(opt["label"])
                if any(_score_option(olab, c) >= 50 for c in cands):
                    return {
                        "ok": True,
                        "verified": True,
                        "picked": olab,
                        "readback": olab,
                        "skipped_already_correct": True,
                    }
        best = None
        best_score = -1
        for opt in options or []:
            olab = str(opt.get("label") or "")
            if not olab:
                continue
            for i, cand in enumerate(cands):
                s = _score_option(olab, cand)
                if s > best_score:
                    best_score = s
                    best = (opt, olab, cand)
        if best and best_score >= 50:
            opt, olab, _cand = best
            opt_id = opt.get("id")
            if opt_id:
                loc = entry.locator(f"#{opt_id}").first
            else:
                loc = entry.get_by_role(role, name=re.compile(re.escape(olab[:40]), re.I)).first
            if await loc.count():
                try:
                    if role == "checkbox":
                        await loc.check(timeout=3000, force=True)
                    else:
                        await loc.check(timeout=3000, force=True)
                    verified = False
                    try:
                        verified = await loc.is_checked()
                    except Exception as e:
                        _log.debug("consent is_checked failed: %s", e)
                        verified = False
                    return {
                        "ok": verified,
                        "verified": verified,
                        "picked": olab,
                        "readback": olab if verified else "",
                        "reason": None if verified else "consent_not_checked",
                    }
                except Exception as e:
                    _log.debug("consent check click failed: %s", e)
        for cand in cands:
            if not cand:
                continue
            opt = entry.get_by_role(role, name=re.compile(re.escape(cand), re.I)).first
            if await opt.count() == 0:
                opt = entry.locator("label", has_text=re.compile(re.escape(cand), re.I)).first
            if await opt.count() == 0:
                continue
            try:
                if role == "checkbox":
                    await opt.check(timeout=3000, force=True)
                else:
                    await opt.check(timeout=3000, force=True)
                checked = False
                try:
                    checked = bool(await opt.is_checked())
                except Exception as e:
                    _log.debug("consent option is_checked failed: %s", e)
                    checked = False
                return {
                    "ok": checked,
                    "verified": checked,
                    "picked": cand,
                    "readback": cand if checked else "",
                    "reason": None if checked else "consent_not_checked",
                }
            except Exception as e:
                _log.debug("consent option click failed: %s", e)
                continue
        # Global fallback for lone "I agree" / "I consent" at page bottom
        for cand in cands:
            opt = page.get_by_role(role, name=re.compile(re.escape(cand), re.I)).first
            if await opt.count() and await opt.is_visible(timeout=400):
                if role == "checkbox":
                    await opt.check(timeout=3000, force=True)
                else:
                    await opt.check(timeout=3000, force=True)
                checked = False
                try:
                    checked = bool(await opt.is_checked())
                except Exception as e:
                    _log.debug("consent fallback is_checked failed: %s", e)
                    checked = False
                return {
                    "ok": checked,
                    "verified": checked,
                    "picked": cand,
                    "readback": cand if checked else "",
                    "reason": None if checked else "consent_not_checked",
                }
        return {"ok": False, "reason": "option_not_found", "candidates": candidates[:5]}
    except Exception as e:
        return {"ok": False, "reason": "option_click_failed", "error": str(e)[:120]}


def _want_yes(val: str) -> bool:
    low = (val or "").strip().lower()
    if low in ("no", "false", "0", "n"):
        return False
    if low.startswith("no ") or low.startswith("no,"):
        return False
    return True


def _ashby_yesno_default_for_label(label: str, values: dict | None = None) -> bool | None:
    """Dummy policy for unclassified Ashby Yes/No segmented controls."""
    low = (label or "").lower()
    if re.search(
        r"latin\s*america|\blatam\b|based in (mexico|brazil|argentina|colombia|chile|peru)",
        low,
    ):
        ans = str((values or {}).get(LATIN_AMERICA) or "Yes")
        return _want_yes(ans)
    if re.search(
        r"authorized|legally.*work|work authorization|eligible to work|right to work",
        low,
    ):
        return True
    if re.search(r"sponsorship|visa|require.*immigration|h-?1b", low):
        return False
    if re.search(r"previously (worked|employed)|worked here before", low):
        return False
    # Experience-years screening ("Do you have 5+ years…", "at least 3 years of
    # experience?"). Answer honestly from dummy resume truth: dummy total years
    # of experience (default 3.0) vs the threshold in the question. Never invent
    # EEO — this is a non-EEO screening fact grounded in the dummy resume.
    if re.search(r"\byears?\b", low) and re.search(
        r"experience|worked|working|background|professional", low
    ):
        m = re.search(
            r"(\d+(?:\.\d+)?)\s*\+?\s*(?:or\s+more\s+)?years?|"
            r"(?:at\s+least|minimum\s+of|min\.?|more\s+than|over)\s+(\d+(?:\.\d+)?)\s*years?",
            low,
        )
        if m:
            threshold = float(m.group(1) or m.group(2) or 0)
            try:
                dummy_years = float(
                    str((values or {}).get("YEARS_EXPERIENCE") or "3.0") or 3.0
                )
            except (TypeError, ValueError):
                dummy_years = 3.0
            return dummy_years >= threshold
        # "Do you have experience with X?" (no threshold) — dummy has relevant
        # experience → Yes.
        return True
    # Generic "Do you have experience …" without the word "years".
    if re.search(r"do you have (?:relevant |prior |professional )?experience", low):
        return True
    # Hybrid / office / relocation willingness — dummy Yes (Plaid leftover)
    if re.search(
        r"hybrid|in[\s_-]*office|on[\s_-]*site|come[\s_-]*into[\s_-]*the[\s_-]*office|"
        r"days?\s*/\s*week[\s_-]*in[\s_-]*office|able to meet this requirement|"
        r"relocat|willing to (work|travel|commute|move)",
        low,
    ):
        return True
    return None


_SCREENING_STRONG_RE = re.compile(
    r"fluent|native|full[\s-]*professional|expert|advanced|extensive|"
    r"production|deployed|professional\s+experience|highly\s+proficient|"
    r"designed\s+and\s+(built|deployed)|significantly|ship(?:ping|ped)?",
    re.I,
)


def is_terms_consent_label(label: str) -> bool:
    """True for TERMS_CONSENT (dummy-yes) — never marketing opt-in."""
    low = (label or "").lower().strip()
    if re.search(
        r"marketing|newsletter|sms|promotional|talent\s*community|opt[\s_-]*in",
        low,
    ):
        return False
    if re.match(r"consent\s*\*?$", low):
        return True
    if re.search(
        r"i\s+(agree|consent)|terms\s*(and|&)\s*conditions|"
        r"privacy\s*(policy|notice)|data\s+privacy|data[\s_-]*consent",
        low,
    ):
        return True
    return False


def ashby_screening_dummy_answer(label: str, values: dict | None = None) -> str:
    """One dummy token per Ashby screening group. Never invents EEO."""
    low = (label or "").lower()
    if re.search(r"gender|race|ethnic|hispanic|veteran|disabilit|lgbtq", low):
        return ""
    vals = values or {}
    if is_terms_consent_label(label):
        return str(vals.get(TERMS_CONSENT) or "Yes")
    if re.search(r"english|proficiency|language\s+(skill|level)", low):
        return "Fluent"
    if re.search(r"production", low):
        return "production"
    if re.search(
        r"machine learning|\bml\b|\bai\b|enjoy most|best describes|best reflects",
        low,
    ):
        return "production"
    return "Yes"


def entry_radios_unanswered(entry: dict | None) -> bool:
    """True when this field-entry's native / role radios have no selection."""
    if not isinstance(entry, dict):
        return False
    radios = list(entry.get("radios") or []) + list(entry.get("roleRadios") or [])
    if not radios:
        return False
    return not any(
        (isinstance(r, dict) and (r.get("checked") or str(r.get("ariaChecked") or "").lower() == "true"))
        for r in radios
    )


def entry_checks_unanswered(entry: dict | None) -> bool:
    if not isinstance(entry, dict):
        return False
    checks = entry.get("checks") or []
    if not checks:
        return False
    return not any(c.get("checked") for c in checks if isinstance(c, dict))


async def click_ashby_choice_option(
    page,
    label: str,
    value: str,
    *,
    report: dict | None = None,
) -> dict:
    """Click Yes/No, rating radio, or checkbox option under an Ashby field label.

    Used by Flash leftovers when selector is missing (silent miss recovery).
    Always scoped to ONE labeled field-entry — never treats a sibling radio
    group as already done.
    """
    from fill_step_log import note_step

    lab = (label or "").strip()
    if not lab:
        return {"ok": False, "reason": "no_label"}
    want_raw = (value or "").strip()
    # Prefer Yes/No when value is yes/no-ish — fall through on miss so
    # screening MCQs ("Yes I have production…") still click a radio in-group.
    if re.match(r"^(yes|no)\b", want_raw, re.I) or not want_raw:
        want = _want_yes(want_raw) if want_raw else _ashby_yesno_default_for_label(lab)
        if want is not None:
            yn = await _click_yesno_in_entry(page, lab, want, report=report)
            if yn.get("ok"):
                return yn

    frag = re.escape(lab[:48])
    entry = page.locator(
        ".ashby-application-form-field-entry, [class*=\"_fieldEntry_\"]"
    ).filter(has=page.locator("label", has_text=re.compile(frag, re.I)))
    if await entry.count() == 0:
        return {"ok": False, "reason": "entry_not_found"}

    # Rating / radio / checkbox option by visible text
    token = want_raw[:60] if want_raw else ashby_screening_dummy_answer(lab)[:60]
    # Impression/rating scales: prefer N/A or mid option when Flash returns prose
    low_lab = lab.lower()
    if re.search(r"rate|impression|scale|how would you", low_lab):
        for prefer in ("N/A", "NA", "3", "2", "4", "1"):
            btn = entry.locator(
                "button, [role=radio], label, input[type=radio] + *",
                has_text=re.compile(rf"^{re.escape(prefer)}$", re.I),
            ).first
            try:
                if await btn.count() and await btn.is_visible(timeout=300):
                    await btn.click(timeout=2000)
                    verified = False
                    try:
                        # Rating/radio commit: aria-checked / aria-pressed / checked
                        aria = (
                            (await btn.get_attribute("aria-checked"))
                            or (await btn.get_attribute("aria-pressed"))
                            or ""
                        ).lower()
                        if aria in ("true", "1"):
                            verified = True
                        else:
                            inp = entry.locator(
                                "input[type=radio]:checked, [aria-checked='true']"
                            ).first
                            if await inp.count():
                                verified = True
                    except Exception:
                        verified = False
                    note_step(
                        report,
                        action="click_choice",
                        label=lab[:80],
                        after=prefer,
                        via="ashby_choice_flash",
                        reason="rating_scale",
                    )
                    return {
                        "ok": verified,
                        "verified": verified,
                        "picked": prefer,
                        "mode": "rating",
                        "reason": None if verified else "choice_not_committed",
                    }
            except Exception:
                continue

    if token:
        opt = entry.get_by_text(re.compile(re.escape(token[:40]), re.I)).first
        try:
            if await opt.count() and await opt.is_visible(timeout=400):
                await opt.click(timeout=2000)
                verified = False
                try:
                    aria = (
                        (await opt.get_attribute("aria-checked"))
                        or (await opt.get_attribute("aria-pressed"))
                        or ""
                    ).lower()
                    if aria in ("true", "1"):
                        verified = True
                    else:
                        # Closest radio/checkbox in entry
                        checked = entry.locator(
                            "input[type=radio]:checked, input[type=checkbox]:checked, "
                            "[aria-checked='true'], [aria-pressed='true']"
                        ).first
                        if await checked.count():
                            verified = True
                except Exception:
                    verified = False
                note_step(
                    report,
                    action="click_choice",
                    label=lab[:80],
                    after=token[:40],
                    via="ashby_choice_flash",
                )
                return {
                    "ok": verified,
                    "verified": verified,
                    "picked": token[:80],
                    "mode": "option",
                    "reason": None if verified else "choice_not_committed",
                }
        except Exception as e:
            return {"ok": False, "reason": "option_click_failed", "error": str(e)[:100]}

    # Every required radio GROUP in this labeled entry gets one pick.
    # Do NOT gate on "which of the following" — screening MCQs
    # (production / English / ML / enjoy-most) were skipped that way.
    radios = entry.locator("input[type=radio]:not(:disabled), [role=radio]")
    try:
        rn = await radios.count()
    except Exception:
        rn = 0
    if rn > 0:
        async def _radio_label(rloc) -> str:
            try:
                rid = await rloc.get_attribute("id")
                if rid:
                    lab_el = entry.locator(f'label[for="{rid}"]').first
                    if await lab_el.count():
                        return (await lab_el.inner_text() or "").strip()
            except Exception:
                pass
            try:
                return (
                    await rloc.evaluate(
                        "(el) => { const p = el.closest('label') "
                        "|| el.parentElement; return (p && (p.innerText "
                        "|| p.textContent)) || el.getAttribute('aria-label') "
                        "|| el.innerText || ''; }"
                    )
                    or ""
                ).strip()
            except Exception:
                return ""

        scored: list[tuple[int, object, str]] = []
        dummy_tok = (token or ashby_screening_dummy_answer(lab) or "").lower()
        for i in range(min(rn, 24)):
            r = radios.nth(i)
            lt = await _radio_label(r)
            if not lt:
                continue
            score = 0
            low_opt = lt.lower()
            if dummy_tok and dummy_tok in low_opt:
                score += 80
            if token and token.lower() in low_opt:
                score += 70
            if _SCREENING_STRONG_RE.search(lt):
                score += 40
            scored.append((score, r, lt[:80]))
        chosen = None
        picked_label = ""
        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            if scored[0][0] > 0:
                _, chosen, picked_label = scored[0]
            else:
                _, chosen, picked_label = scored[0]
                picked_label = picked_label or "first_option"
        if chosen is None:
            chosen = radios.first
            picked_label = (await _radio_label(chosen))[:80] or "first_option"
        try:
            already = False
            try:
                already = bool(await chosen.is_checked())
            except Exception:
                aria = (await chosen.get_attribute("aria-checked") or "").lower()
                already = aria in ("true", "1")
            if not already:
                try:
                    await chosen.check(timeout=2000)
                except Exception:
                    await chosen.click(timeout=2000)
            verified = False
            try:
                verified = bool(await chosen.is_checked())
            except Exception:
                aria = (await chosen.get_attribute("aria-checked") or "").lower()
                verified = aria in ("true", "1")
            if not verified:
                chk = entry.locator(
                    "input[type=radio]:checked, [role=radio][aria-checked='true']"
                ).first
                verified = bool(await chk.count())
            note_step(
                report,
                action="click_choice",
                label=lab[:80],
                after=picked_label[:40],
                via="ashby_choice_flash",
                reason="radio_single_select",
            )
            return {
                "ok": verified,
                "verified": verified,
                "picked": picked_label,
                "mode": "radio",
                "reason": None if verified else "radio_not_committed",
            }
        except Exception as e:
            return {"ok": False, "reason": "radio_failed", "error": str(e)[:100]}

    # TERMS_CONSENT dummy-yes: check the consent box in this entry only.
    # Never click Submit Application.
    boxes = entry.locator("input[type=checkbox]:not(:disabled)")
    try:
        n = await boxes.count()
        if n > 0 and (
            is_terms_consent_label(lab)
            or re.search(
                r"preferred|location|why (are you|do you)|interested|"
                r"which of the following|consent",
                low_lab,
            )
        ):
            box = None
            picked_label = "first_checkbox"
            consentish = is_terms_consent_label(lab)
            if consentish and n == 1:
                box = boxes.first
                picked_label = "consent"
            elif token:
                for i in range(min(n, 24)):
                    b = boxes.nth(i)
                    try:
                        lab_txt = ""
                        try:
                            lid = await b.get_attribute("id")
                            if lid:
                                lab_el = entry.locator(f'label[for="{lid}"]').first
                                if await lab_el.count():
                                    lab_txt = (await lab_el.inner_text() or "").strip()
                        except Exception:
                            pass
                        if not lab_txt:
                            try:
                                lab_txt = (
                                    await b.evaluate(
                                        """(el) => {
                                          const p = el.closest('label')
                                            || el.parentElement;
                                          return (p && p.innerText) || '';
                                        }"""
                                    )
                                    or ""
                                ).strip()
                            except Exception:
                                lab_txt = ""
                        if token.lower() in lab_txt.lower() or (
                            consentish
                            and re.search(r"agree|consent|accept", lab_txt, re.I)
                        ):
                            box = b
                            picked_label = lab_txt[:80] or token[:80]
                            break
                    except Exception:
                        continue
            if box is None and n == 1:
                box = boxes.first
                picked_label = "sole_checkbox"
            if box is None and token and not consentish:
                return {
                    "ok": False,
                    "reason": "checkbox_token_not_found",
                    "token": token[:60],
                }
            if box is None:
                return {"ok": False, "reason": "checkbox_ambiguous_no_token"}
            if not await box.is_checked():
                await box.check(timeout=2000)
            verified = bool(await box.is_checked())
            note_step(
                report,
                action="click_choice",
                label=lab[:80],
                after=picked_label[:40],
                via="ashby_choice_flash",
                reason="checkbox_group",
            )
            return {
                "ok": verified,
                "verified": verified,
                "picked": picked_label[:80],
                "mode": "checkbox",
                "reason": None if verified else "checkbox_not_checked",
            }
    except Exception as e:
        return {"ok": False, "reason": "checkbox_failed", "error": str(e)[:100]}

    return {"ok": False, "reason": "no_matching_option"}


async def _ashby_entry_text_input(page, label: str, text_name: str = ""):
    """Locate the text/url input under an Ashby field-entry for ``label``.

    Prefers label-scoped DOM (stable across tenants) over UUID ``name=`` alone.
    """
    frag = re.escape((label or "")[:48].strip()) if label else ""
    if frag:
        entry = page.locator(
            ".ashby-application-form-field-entry, [class*=\"_fieldEntry_\"]"
        ).filter(has=page.locator("label", has_text=re.compile(frag, re.I)))
        loc = entry.locator(
            "input[type=text], input[type=url], input:not([type]), textarea, "
            "input[type=email], input[type=tel]"
        ).first
        try:
            if await loc.count() and await loc.is_visible(timeout=400):
                return loc, (
                    f".ashby-application-form-field-entry:has(label:has-text("
                    f"'{label[:40]}')) input, textarea"
                )
        except Exception:
            pass
    if text_name:
        sel = (
            f"textarea[name=\"{text_name}\"], input[name=\"{text_name}\"], "
            f"input[name=\"{text_name}\"]:visible"
        )
        loc = page.locator(sel).first
        try:
            if await loc.count() and await loc.is_visible(timeout=400):
                return loc, sel
        except Exception:
            pass
    return None, None


async def fill_ashby_url_by_label(page, ftype: str, value: str) -> dict:
    """Fill LINKEDIN / GITHUB / PORTFOLIO via field-entry label (not UUID name).

    Requires non-empty ``input_value`` readback before ``verified=True``.
    Empty readback → leftover candidate (never false success).
    """
    if ftype not in _ASHBY_URL_TYPES:
        return {"ok": False, "reason": "not_url_type", "type": ftype}
    val = (value or "").strip()
    if not val or not validate_filled(ftype, val):
        return {"ok": False, "reason": "no_value", "type": ftype, "flash_candidate": True}
    hint = _ASHBY_URL_LABEL_HINTS.get(ftype)
    if not hint:
        return {"ok": False, "reason": "no_label_hint", "type": ftype}

    # Prefer semantic name*= / placeholder, then label-scoped entry.
    candidates = []
    if ftype == LINKEDIN:
        candidates.extend(
            [
                "input[name*='linkedin' i]",
                "input[id*='linkedin' i]",
                "input[placeholder*='LinkedIn' i]",
                "input[type=url][placeholder*='LinkedIn' i]",
            ]
        )
    elif ftype == GITHUB:
        candidates.extend(
            [
                "input[name*='github' i]",
                "input[id*='github' i]",
                "input[placeholder*='GitHub' i]",
            ]
        )
    elif ftype == PORTFOLIO:
        candidates.extend(
            [
                "input[name*='portfolio' i]",
                "input[placeholder*='Portfolio' i]",
                "input[placeholder*='Website' i]",
            ]
        )
    if ftype == LINKEDIN:
        candidates.append(
            ".ashby-application-form-field-entry:has(label:has-text('LinkedIn')) "
            "input[type=text], "
            ".ashby-application-form-field-entry:has(label:has-text('LinkedIn')) "
            "input[type=url], "
            ".ashby-application-form-field-entry:has(label:has-text('LinkedIn')) "
            "input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio]), "
            "[class*=\"_fieldEntry_\"]:has(label:has-text('LinkedIn')) input"
        )
    elif ftype == GITHUB:
        candidates.append(
            ".ashby-application-form-field-entry:has(label:has-text('GitHub')) "
            "input[type=text], "
            ".ashby-application-form-field-entry:has(label:has-text('GitHub')) "
            "input:not([type=hidden]):not([type=file]), "
            "[class*=\"_fieldEntry_\"]:has(label:has-text('GitHub')) input"
        )
    else:
        candidates.append(
            ".ashby-application-form-field-entry:has(label:has-text('Portfolio')) "
            "input[type=text], "
            ".ashby-application-form-field-entry:has(label:has-text('Website')) "
            "input:not([type=hidden]):not([type=file]), "
            "[class*=\"_fieldEntry_\"]:has(label:has-text('Website')) input"
        )

    loc = None
    used_sel = ""
    if ftype == LINKEDIN:
        try:
            by_lab = page.get_by_label(re.compile(r"linked\s*in", re.I)).first
            if await by_lab.count() and await by_lab.is_visible(timeout=350):
                tag = await by_lab.evaluate("el => (el.tagName || '').toLowerCase()")
                if tag in ("input", "textarea"):
                    loc = by_lab
                    used_sel = "get_by_label:LinkedIn"
        except Exception:
            pass
    for sel in candidates:
        try:
            cand = page.locator(sel).first
            if await cand.count() == 0:
                continue
            if not await cand.is_visible(timeout=350):
                continue
            # Confirm label proximity for generic selectors
            near = await cand.evaluate(
                """(el, reSrc) => {
                  const entry = el.closest(
                    '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
                  ) || el.parentElement;
                  const lab = entry && entry.querySelector('label');
                  const t = (
                    (lab && lab.innerText) || el.getAttribute('aria-label')
                    || el.placeholder || el.name || ''
                  );
                  try { return new RegExp(reSrc, 'i').test(t); }
                  catch (e) { return false; }
                }""",
                hint.pattern,
            )
            if not near and "linkedin" not in sel.lower() and "github" not in sel.lower():
                continue
            loc = cand
            used_sel = sel
            break
        except Exception:
            continue

    if loc is None:
        # Last resort: scan field entries by label regex
        try:
            entries = await list_ashby_field_entries(page)
            for entry in entries:
                label = str(entry.get("label") or "")
                if not hint.search(label) or not entry.get("hasText"):
                    continue
                loc, used_sel = await _ashby_entry_text_input(
                    page, label, str(entry.get("textName") or "")
                )
                if loc is not None:
                    break
        except Exception:
            pass

    if loc is None:
        return {
            "ok": False,
            "verified": False,
            "type": ftype,
            "reason": "url_field_not_found",
            "flash_candidate": True,
        }

    try:
        # SKIP thrash: never clear/retype when URL already matches
        try:
            existing = (await loc.input_value() or "").strip()
        except Exception:
            existing = ""
        if _value_already_correct(val, existing):
            return {
                "via": "ashby_widgets",
                "layer": "ashby",
                "type": ftype,
                "label": ftype,
                "selector": used_sel[:180] or "ashby_url_by_label",
                "mode": "fill",
                "ok": True,
                "verified": True,
                "value": val[:200],
                "readback": existing[:120],
                "reason": "already_correct_skip",
                "skipped_already_correct": True,
                "verified_value": existing[:120],
            }
        await loc.click(timeout=3000, force=True)
        try:
            await loc.fill("")
        except Exception:
            try:
                await loc.press("Meta+a")
                await loc.press("Backspace")
            except Exception:
                pass
        await loc.fill(val[:500])
        try:
            await loc.press("Tab")
        except Exception:
            try:
                await loc.blur()
            except Exception:
                pass
        try:
            await page.wait_for_timeout(250)
        except Exception:
            pass
        readback = ""
        try:
            readback = (await loc.input_value() or "").strip()
        except Exception:
            readback = ""
        ok = (
            not is_empty_ui_value(readback)
            and bool(readback)
            and (
                _norm(val)[:24] in _norm(readback)
                or _norm(readback)[:24] in _norm(val)
                or (ftype == LINKEDIN and "linkedin.com" in _norm(readback))
            )
        )
        return {
            "via": "ashby_widgets",
            "layer": "ashby",
            "type": ftype,
            "label": ftype,
            "selector": used_sel[:180] or "ashby_url_by_label",
            "mode": "fill",
            "ok": ok,
            "verified": ok,
            "value": val[:200],
            "readback": (readback or "")[:120],
            "reason": None if ok else "url_readback_empty_or_mismatch",
            "flash_candidate": not ok,
            "verified_value": readback[:120] if ok else None,
        }
    except Exception as e:
        return {
            "via": "ashby_widgets",
            "type": ftype,
            "ok": False,
            "verified": False,
            "reason": "url_fill_failed",
            "error": str(e)[:120],
            "flash_candidate": True,
        }


async def live_ashby_url_readback(page, ftype: str = LINKEDIN) -> str:
    """Authoritative live input_value for an Ashby URL field (by label)."""
    hint = _ASHBY_URL_LABEL_HINTS.get(ftype) or _ASHBY_URL_LABEL_HINTS[LINKEDIN]
    try:
        return await page.evaluate(
            """(reSrc) => {
              const re = new RegExp(reSrc, 'i');
              const entries = Array.from(document.querySelectorAll(
                '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
              ));
              for (const el of entries) {
                const lab = el.querySelector(
                  'label.ashby-application-form-question-title, label[class*="_heading_"], label'
                );
                const t = (lab && lab.innerText || '').trim();
                if (!re.test(t)) continue;
                const inp = el.querySelector(
                  'input[type=text], input[type=url], input:not([type]), textarea'
                );
                if (!inp) continue;
                const r = inp.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                return (inp.value || '').trim();
              }
              return '';
            }""",
            hint.pattern,
        )
    except Exception:
        return ""


async def reassert_ashby_contact_after_resume(page, values: dict) -> list[dict]:
    """Re-fill contact + URL fields after Ashby resume parse may wipe them.

    Ashby often async-parses the uploaded PDF and overwrites name/email (and
    can clear LinkedIn). Call this after a verified resume upload settles.
    """
    filled: list[dict] = []
    try:
        await page.wait_for_timeout(900)
    except Exception:
        pass

    for ftype in (NAME_FULL, EMAIL, PHONE):
        val = values.get(ftype)
        if not val or not validate_filled(ftype, str(val)):
            continue
        sel = _ASHBY_CONTACT_SELECTORS.get(ftype)
        if not sel:
            continue
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0 or not await loc.is_visible(timeout=400):
                continue
            current = ""
            try:
                current = (await loc.input_value() or "").strip()
            except Exception:
                current = ""
            want = str(val).strip()
            needs = is_empty_ui_value(current) or (
                _norm(want)[:12] not in _norm(current)
                and _norm(current)[:12] not in _norm(want)
            )
            # Resume parse often injects a different person name — force dummy back.
            if ftype == NAME_FULL and current and _norm(want) != _norm(current):
                needs = True
            if ftype == EMAIL and current and "@" in current and _norm(want) != _norm(current):
                needs = True
            if not needs:
                filled.append(
                    {
                        "via": "ashby_post_resume_reassert",
                        "type": ftype,
                        "ok": True,
                        "verified": True,
                        "value": want[:120],
                        "readback": current[:120],
                        "reason": "already_correct_skip",
                        "skipped_already_correct": True,
                        "selector": sel,
                    }
                )
                continue
            await loc.click(timeout=2500, force=True)
            await loc.fill(want[:200])
            try:
                await loc.press("Tab")
            except Exception:
                pass
            readback = ""
            try:
                readback = (await loc.input_value() or "").strip()
            except Exception:
                readback = ""
            ok = not is_empty_ui_value(readback) and (
                _norm(want)[:16] in _norm(readback)
                or _norm(readback)[:16] in _norm(want)
            )
            filled.append(
                {
                    "via": "ashby_post_resume_reassert",
                    "layer": "ashby",
                    "type": ftype,
                    "selector": sel,
                    "mode": "fill",
                    "ok": ok,
                    "verified": ok,
                    "value": want[:120],
                    "readback": readback[:120],
                    "reason": None if ok else "contact_readback_mismatch",
                    "flash_candidate": not ok,
                    "verified_value": readback[:120] if ok else None,
                }
            )
        except Exception as e:
            filled.append(
                {
                    "via": "ashby_post_resume_reassert",
                    "type": ftype,
                    "ok": False,
                    "verified": False,
                    "reason": "contact_reassert_failed",
                    "error": str(e)[:120],
                    "flash_candidate": True,
                }
            )

    for ftype in _ASHBY_URL_TYPES:
        val = values.get(ftype)
        if not val:
            continue
        row = await fill_ashby_url_by_label(page, ftype, str(val))
        row["via"] = "ashby_post_resume_reassert"
        # Missing optional GitHub/Portfolio on this tenant ≠ leftover blank.
        if row.get("reason") == "url_field_not_found" and ftype != LINKEDIN:
            continue
        filled.append(row)

    # Location → zip: skip retype thrash when Location already committed (zip-absent tenants).
    try:
        zip_absent = not await _ashby_zip_field_present(page)
        combo = page.locator(_ASHBY_LOCATION_COMBO).first
        loc_committed = False
        if await combo.count():
            city_val = values.get(ADDRESS_CITY) or "Springfield"
            state_val = values.get(ADDRESS_STATE) or "IL"
            country_val = values.get(ADDRESS_COUNTRY) or "United States"
            st_full = (
                "Illinois" if str(state_val).upper() == "IL" else str(state_val)
            )
            from verified_select import (
                location_display_matches,
                location_option_aliases,
                probe_location_committed,
            )

            aliases = location_option_aliases(
                str(city_val),
                state=str(state_val),
                state_full=st_full,
                country=str(country_val),
            )
            probe = await probe_location_committed(
                page,
                combo,
                aliases,
                city=str(city_val),
                state=str(state_val),
                state_full=st_full,
                country=str(country_val),
            )
            shown = probe.get("shown") or ""
            loc_committed = bool(probe.get("committed")) or location_display_matches(
                shown,
                aliases,
                city=str(city_val),
                state=str(state_val),
                state_full=st_full,
                country=str(country_val),
            )
        if zip_absent and loc_committed:
            filled.append(
                {
                    "via": "ashby_post_resume_reassert",
                    "type": ADDRESS_ZIP,
                    "ok": True,
                    "verified": False,
                    "reason": "zip_field_absent_on_form",
                    "not_applicable": True,
                    "readback": "",
                }
            )
        else:
            zip_rows = await fill_ashby_location_then_zip(page, values)
            for row in zip_rows or []:
                row = dict(row)
                row["via"] = "ashby_post_resume_reassert"
                filled.append(row)
    except Exception as e:
        filled.append(
            {
                "via": "ashby_post_resume_reassert",
                "type": ADDRESS_ZIP,
                "ok": False,
                "verified": False,
                "reason": "zip_reassert_failed",
                "error": str(e)[:120],
                "flash_candidate": True,
            }
        )

    return filled


async def fill_ashby_widgets(page, values: dict, *, report: dict | None = None) -> list[dict]:
    """Fill Ashby Yes/No, consent, and EEO Decline widgets via field-entry DOM.

    Also fills INTEREST / SALARY / LINKEDIN / GITHUB / PORTFOLIO text via
    field-entry labels (UUID ``name`` attrs are tenant-unstable).
    """
    try:
        from fill_pause import wait_while_paused as _wait_fill_pause
    except Exception:
        _wait_fill_pause = None  # type: ignore[assignment]

    async def _pause_gate() -> None:
        if _wait_fill_pause is None:
            return
        try:
            await _wait_fill_pause(page, report)
        except Exception:
            pass

    await _pause_gate()
    filled: list[dict] = []
    entries = await list_ashby_field_entries(page)
    seen_types: set[str] = set()

    # URL fields first via stable label scope (pack name*=linkedin misses UUIDs).
    for ftype in (LINKEDIN, GITHUB, PORTFOLIO):
        await _pause_gate()
        val = values.get(ftype)
        if not val or not validate_filled(ftype, str(val)):
            continue
        row = await fill_ashby_url_by_label(page, ftype, str(val))
        if row.get("ok") and row.get("verified"):
            filled.append(row)
            seen_types.add(ftype)
        elif row.get("flash_candidate") or row.get("ok") is False:
            # Keep failed URL attempts visible for leftovers / Flash.
            if row.get("reason") != "url_field_not_found" or ftype == LINKEDIN:
                filled.append(row)

    for entry in entries:
        await _pause_gate()
        label = str(entry.get("label") or "")
        if not label:
            continue

        # Unclassified Ashby segmented Yes/No (e.g. "based in Latin America?")
        if entry.get("yesno") and not entry.get("yesnoSelected"):
            fake_probe = {
                "label": label,
                "name": entry.get("path") or "",
                "id": entry.get("path") or "",
                "type": "radio_group",
                "placeholder": "",
                "aria_label": "",
                "autocomplete": "",
            }
            ftype_probe, _ = classify_field(fake_probe)
            if not ftype_probe:
                want = _ashby_yesno_default_for_label(label, values)
                if want is not None:
                    result = await _click_yesno_in_entry(page, label, want, report=report)
                    row = {
                        "via": "ashby_widgets",
                        "layer": "ashby",
                        "type": "YES_NO_SCREENING",
                        "label": label[:80],
                        "mode": "yesno_segmented",
                        "ok": bool(result.get("ok")),
                        "verified": bool(result.get("verified")),
                        "value": "Yes" if want else "No",
                        "picked": result.get("picked"),
                        "readback": result.get("readback"),
                        "reason": result.get("reason") or "ashby_yesno_default",
                        "flash_candidate": not bool(result.get("ok")),
                    }
                    filled.append(row)
                    continue
                # No safe default for unclassified Yes/No → Flash
                filled.append(
                    {
                        "via": "ashby_widgets",
                        "layer": "ashby",
                        "type": "YES_NO_SCREENING",
                        "label": label[:80],
                        "mode": "yesno_segmented",
                        "ok": False,
                        "verified": False,
                        "reason": "unclassified_yesno_needs_flash",
                        "flash_candidate": True,
                    }
                )
                continue

        fake = {
            "label": label,
            "name": entry.get("path") or "",
            "id": entry.get("path") or "",
            "type": (
                "radio_group"
                if (
                    entry.get("radios")
                    or entry.get("roleRadios")
                    or entry.get("yesno")
                )
                else "checkbox"
                if entry.get("checks")
                else "text"
            ),
            "placeholder": "",
            "aria_label": "",
            "autocomplete": "",
        }
        ftype, layer = classify_field(fake)
        if not ftype:
            # Unclassified screening radios / consent: click THIS entry only
            # (never treat one sibling group as all screening done).
            if entry_radios_unanswered(entry):
                want = ashby_screening_dummy_answer(label, values)
                result = await click_ashby_choice_option(
                    page, label, want, report=report
                )
                ok = bool(result.get("ok"))
                filled.append(
                    {
                        "via": "ashby_widgets",
                        "layer": "ashby",
                        "type": None,
                        "label": label[:80],
                        "mode": result.get("mode") or "radio",
                        "ok": ok,
                        "verified": ok,
                        "value": want[:80],
                        "picked": result.get("picked"),
                        "readback": result.get("picked") or "",
                        "reason": None if ok else (
                            result.get("reason") or "unclassified_unanswered_choice"
                        ),
                        "flash_candidate": not ok,
                    }
                )
                continue
            if entry_checks_unanswered(entry) and is_terms_consent_label(label):
                want = str(values.get(TERMS_CONSENT) or "Yes")
                result = await click_ashby_choice_option(
                    page, label, want, report=report
                )
                ok = bool(result.get("ok"))
                filled.append(
                    {
                        "via": "ashby_widgets",
                        "layer": "ashby",
                        "type": TERMS_CONSENT,
                        "label": label[:80],
                        "mode": "checkbox",
                        "ok": ok,
                        "verified": ok,
                        "value": want,
                        "picked": result.get("picked"),
                        "reason": None if ok else (
                            result.get("reason") or "consent_not_checked"
                        ),
                        "flash_candidate": not ok,
                    }
                )
                continue
            continue

        # Text / interest / salary / URLs inside Ashby blocks (extract may still
        # get these; pack path fills contact — second chance for missed labels).
        if entry.get("hasText") and ftype in _ASHBY_TEXT_TYPES:
            if ftype in seen_types:
                continue
            val = values.get(ftype)
            if not val or not validate_filled(ftype, str(val)):
                continue
            # URL types: prefer dedicated label helper (already attempted above).
            if ftype in _ASHBY_URL_TYPES:
                row = await fill_ashby_url_by_label(page, ftype, str(val))
                if row.get("ok") and row.get("verified"):
                    row["label"] = label[:80]
                    row["layer"] = layer or "ashby"
                    filled.append(row)
                    seen_types.add(ftype)
                else:
                    filled.append({**row, "label": label[:80]})
                continue
            try:
                loc, sel = await _ashby_entry_text_input(
                    page, label, str(entry.get("textName") or "")
                )
                if loc is None or not sel:
                    continue
                try:
                    existing = (await loc.input_value() or "").strip()
                except Exception:
                    existing = ""
                if _value_already_correct(str(val), existing):
                    filled.append(
                        {
                            "via": "ashby_widgets",
                            "layer": layer or "ashby",
                            "type": ftype,
                            "label": label[:80],
                            "selector": sel,
                            "mode": "fill",
                            "ok": True,
                            "verified": True,
                            "value": str(val)[:200],
                            "readback": existing[:120],
                            "reason": "already_correct_skip",
                            "skipped_already_correct": True,
                            "verified_value": existing[:120],
                        }
                    )
                    seen_types.add(ftype)
                    continue
                await loc.fill(str(val)[:2000])
                readback = ""
                try:
                    readback = (await loc.input_value() or "").strip()
                except Exception:
                    readback = ""
                ok = (
                    not is_empty_ui_value(readback)
                    and bool(readback)
                    and _norm(str(val))[:20] in _norm(readback)
                )
                filled.append(
                    {
                        "via": "ashby_widgets",
                        "layer": layer or "ashby",
                        "type": ftype,
                        "label": label[:80],
                        "selector": sel,
                        "mode": "fill",
                        "ok": ok,
                        "verified": ok,
                        "value": str(val)[:200],
                        "readback": readback[:120],
                        "reason": None if ok else "text_readback_empty_or_mismatch",
                        "flash_candidate": not ok,
                        "verified_value": readback[:120] if ok else None,
                    }
                )
                if ok:
                    seen_types.add(ftype)
            except Exception as e:
                filled.append(
                    {
                        "via": "ashby_widgets",
                        "type": ftype,
                        "label": label[:80],
                        "ok": False,
                        "verified": False,
                        "reason": "text_fill_failed",
                        "error": str(e)[:100],
                        "flash_candidate": True,
                    }
                )
            continue

        if ftype not in _ASHBY_CHOICE_TYPES:
            # INTEREST / novel screening classified as text — still click the
            # radio GROUP in this entry (one pick; siblings stay independent).
            if entry_radios_unanswered(entry):
                want = ashby_screening_dummy_answer(label, values) or str(
                    values.get(ftype) or "Yes"
                )
                result = await click_ashby_choice_option(
                    page, label, want, report=report
                )
                ok = bool(result.get("ok"))
                filled.append(
                    {
                        "via": "ashby_widgets",
                        "layer": layer or "ashby",
                        "type": ftype,
                        "label": label[:80],
                        "mode": result.get("mode") or "radio",
                        "ok": ok,
                        "verified": ok,
                        "value": want[:80],
                        "picked": result.get("picked"),
                        "reason": None if ok else (
                            result.get("reason") or "screening_radio_click_failed"
                        ),
                        "flash_candidate": not ok,
                    }
                )
            continue
        if ftype in seen_types and ftype != TERMS_CONSENT:
            continue

        val = values.get(ftype)
        if ftype == TERMS_CONSENT:
            val = val or "Yes"
        if not val:
            # Classified but no dummy value — still hand to Flash
            unanswered = (
                (entry.get("yesno") and not entry.get("yesnoSelected"))
                or (
                    entry.get("radios")
                    and not any(
                        r.get("checked")
                        for r in (entry.get("radios") or [])
                        if isinstance(r, dict)
                    )
                )
            )
            if unanswered:
                filled.append(
                    {
                        "via": "ashby_widgets",
                        "layer": layer or "ashby",
                        "type": ftype,
                        "label": label[:80],
                        "mode": "yesno" if entry.get("yesno") else "radio",
                        "ok": False,
                        "verified": False,
                        "reason": "no_value_unanswered_choice",
                        "flash_candidate": True,
                    }
                )
            continue

        if entry.get("yesno"):
            result = await _click_yesno_in_entry(
                page, label, _want_yes(str(val)), report=report
            )
            ok = bool(result.get("ok"))
            filled.append(
                {
                    "via": "ashby_widgets",
                    "layer": layer or "ashby",
                    "type": ftype,
                    "label": label[:80],
                    "mode": "yesno",
                    "ok": ok,
                    "verified": ok,
                    "value": str(val)[:80],
                    "picked": result.get("picked"),
                    "readback": result.get("readback"),
                    "reason": None if ok else (result.get("reason") or "yesno_click_failed"),
                    "flash_candidate": not ok,
                }
            )
            if ok:
                seen_types.add(ftype)
            continue

        # Radios / consent checkboxes
        cands = aliases_for(ftype, str(val))
        if ftype == TERMS_CONSENT:
            cands = list(
                dict.fromkeys(
                    [
                        *cands,
                        "I consent",
                        "I agree",
                        "Yes",
                        "Agree",
                        "Consent",
                    ]
                )
            )
        role = "checkbox" if (entry.get("checks") and not entry.get("radios")) else "radio"
        # Consent often mixes: radio "I consent" + checkbox "I agree"
        if ftype == TERMS_CONSENT and entry.get("checks"):
            role = "checkbox"
        result = await _click_option_in_entry(page, label, cands, role=role)
        if not result.get("ok") and ftype == TERMS_CONSENT:
            # Try the other role
            other = "radio" if role == "checkbox" else "checkbox"
            result = await _click_option_in_entry(page, label, cands, role=other)
        ok = bool(result.get("ok"))
        filled.append(
            {
                "via": "ashby_widgets",
                "layer": layer or "ashby",
                "type": ftype,
                "label": label[:80],
                "mode": role,
                "ok": ok,
                "verified": ok,
                "value": str(val)[:80],
                "picked": result.get("picked"),
                "readback": result.get("readback"),
                "reason": None if ok else (result.get("reason") or "radio_click_failed"),
                "flash_candidate": not ok,
            }
        )
        if ok:
            seen_types.add(ftype)

    # Lone bottom consent with weak label — second pass by id token
    if TERMS_CONSENT not in seen_types and values.get(TERMS_CONSENT):
        try:
            agree = page.locator(
                "input[type=checkbox][id*='data_consent' i], "
                "input[type=checkbox][id*='consent_ack' i], "
                "input[type=checkbox][name='I agree']"
            ).first
            if await agree.count():
                await agree.check(timeout=3000, force=True)
                checked = False
                try:
                    checked = bool(await agree.is_checked())
                except Exception:
                    checked = False
                filled.append(
                    {
                        "via": "ashby_widgets",
                        "type": TERMS_CONSENT,
                        "label": "data_consent_ack",
                        "mode": "checkbox",
                        "ok": checked,
                        "verified": checked,
                        "value": "Yes",
                        "picked": "I agree",
                        "readback": "I agree" if checked else "",
                        "reason": None if checked else "checkbox_not_checked",
                        "flash_candidate": not checked,
                    }
                )
        except Exception:
            pass
        try:
            consent = page.get_by_role("radio", name=re.compile(r"i\s+consent", re.I)).first
            if await consent.count() and await consent.is_visible(timeout=400):
                await consent.check(timeout=3000, force=True)
                checked = False
                try:
                    checked = bool(await consent.is_checked())
                except Exception:
                    checked = False
                filled.append(
                    {
                        "via": "ashby_widgets",
                        "type": TERMS_CONSENT,
                        "label": "privacy_consent",
                        "mode": "radio",
                        "ok": checked,
                        "verified": checked,
                        "value": "Yes",
                        "picked": "I consent",
                        "readback": "I consent" if checked else "",
                        "reason": None if checked else "radio_not_checked",
                        "flash_candidate": not checked,
                    }
                )
        except Exception:
            pass

    # Location → dependent zip (Places combobox must commit before zip fill)
    try:
        zip_rows = await fill_ashby_location_then_zip(page, values)
        filled.extend(zip_rows or [])
    except Exception as e:
        filled.append(
            {
                "via": "ashby_widgets",
                "type": ADDRESS_ZIP,
                "ok": False,
                "verified": False,
                "reason": "location_zip_pass_failed",
                "error": str(e)[:120],
                "flash_candidate": True,
            }
        )

    try:
        from fill_contract import finalize_widget_rows

        filled = await finalize_widget_rows(
            page, report, filled, via="ashby_widgets"
        )
    except Exception:
        pass
    return filled


def self_test() -> None:
    """Pure unit checks (no browser)."""
    assert _want_yes("Yes") is True
    assert _want_yes("No") is False
    assert _value_already_correct("62701", "62701") is True
    assert not _value_already_correct("62701", "Type here...")
    assert _score_option("No", "No") >= 90
    assert _score_option("No, I will require visa sponsorship", "No") == 0
    fake = {
        "label": "Are you currently based in Latin America?",
        "name": "",
        "id": "",
        "type": "radio_group",
    }
    ftype, _ = classify_field(fake)
    assert ftype == LATIN_AMERICA
    print("ashby_widgets.self_test: OK")


if __name__ == "__main__":
    self_test()
