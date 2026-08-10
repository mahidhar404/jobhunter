#!/usr/bin/env python3
"""Workday fill helpers (implementation). Prefer ``import workday_selectors``.

Production entry is ``fast_fill.run_fast_fill`` (imports packs / ``workday_two_phase_on_page``
via ``workday_selectors``). This module's CLI always delegates to ``run_fast_fill``
(no standalone Chromium / ``--deep`` bypass of headed-cap).

Hard rules:
  - DUMMY_PROFILE / Test Dummy only — never profile.json
  - NEVER click final Submit application
  - NEVER solve CAPTCHA — stop and report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from field_map import (  # noqa: E402
    ADDRESS_CITY,
    ADDRESS_COUNTY,
    ADDRESS_COUNTRY,
    ADDRESS_LINE1,
    ADDRESS_LINE2,
    ADDRESS_STATE,
    ADDRESS_ZIP,
    CURRENT_COMPANY,
    CURRENT_TITLE,
    DEGREE,
    DISABILITY,
    DUMMY_PDF,
    EMAIL,
    GENDER,
    HISPANIC,
    HOW_HEARD,
    NAME_FIRST,
    NAME_FULL,
    NAME_LAST,
    PASSWORD,
    PASSWORD_CONFIRM,
    PHONE,
    RACE,
    SCHOOL,
    VETERAN,
    WORKED_HERE_BEFORE,
    RESUME_UPLOAD,
    validate_filled,
)
from button_gate import (  # noqa: E402
    NAV_KINDS,
    gate_locator_click,
)
from run_identity import prepare_dummy_run  # noqa: E402

WORKDAY_NOTES = ROOT / "ats_notes" / "workday.md"
LISTINGS_DIR = ROOT / "listings"
OUT_DIR = ROOT / "skyvern_runtime" / "real_job_results"

# Prefer: ``from workday_selectors import …`` (thin re-export of this module).
# This file remains the Workday implementation home.


async def _escape_unless_captcha(page) -> bool:
    """FILL3-019: dismiss listboxes via Escape only when CAPTCHA is absent."""
    try:
        from captcha_pause import press_escape_unless_captcha

        return bool(await press_escape_unless_captcha(page))
    except Exception:
        return False


# Contact pack from ats_notes/workday.md.
# Country BEFORE region (state); phone device type BEFORE phone number.
# addressSection_countryRegion = State/Province (NOT country) on wd5+ apply flow.
WD_CONTACT_PACK: list[tuple[str, str]] = [
    ("legalNameSection_firstName", NAME_FIRST),
    ("legalNameSection_lastName", NAME_LAST),
    ("addressSection_country", ADDRESS_COUNTRY),
    ("addressSection_addressLine1", ADDRESS_LINE1),
    ("addressSection_addressLine2", ADDRESS_LINE2),
    ("addressSection_city", ADDRESS_CITY),
    ("addressSection_countryRegion", ADDRESS_STATE),
    ("addressSection_regionSubdivision1", ADDRESS_COUNTY),
    ("addressSection_postalCode", ADDRESS_ZIP),
    ("phone-device-type", "PHONE_DEVICE"),  # combobox; filled as "Mobile" if present
    ("phone-number", PHONE),
]

# Optional extras often required on contact/My Information (dummy values only).
WD_CONTACT_EXTRAS: list[tuple[str, str, str]] = [
    # (logical_id, field_type, widget)
    ("contact_email", EMAIL, "text"),
    ("how_heard", HOW_HEARD, "combobox"),
    ("worked_here_before", WORKED_HERE_BEFORE, "radio"),
]

# USPS abbrev → Workday state combobox display name (dummy address uses IL).
_US_STATE_NAMES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}

# Newer Workday apply-flow (Cisco wd5+): inputs use name=/id= instead of
# legalNameSection_* automation ids. Keep legacy aids as primary keys; try
# these fallbacks when filling / detecting contact.
WD_CONTACT_SELECTORS: dict[str, list[str]] = {
    "legalNameSection_firstName": [
        '[data-automation-id="legalNameSection_firstName"]',
        'input[name="legalName--firstName"]',
        '#name--legalName--firstName',
        '[data-automation-id="formField-legalName--firstName"] input',
    ],
    "legalNameSection_lastName": [
        '[data-automation-id="legalNameSection_lastName"]',
        'input[name="legalName--lastName"]',
        '#name--legalName--lastName',
        '[data-automation-id="formField-legalName--lastName"] input',
    ],
    "addressSection_country": [
        '[data-automation-id="addressSection_country"]',
        '[data-automation-id="country--country"]',
        '[data-automation-id="formField-country"] button',
        '[data-automation-id="formField-country"] [role="button"]',
        '[data-automation-id="formField-country"]',
        'button[name="country"]',
    ],
    "addressSection_addressLine1": [
        '[data-automation-id="addressSection_addressLine1"]',
        'input[name="addressLine1"]',
        '#address--addressLine1',
        '[data-automation-id="formField-addressLine1"] input',
    ],
    "addressSection_city": [
        '[data-automation-id="addressSection_city"]',
        'input[name="city"]',
        '#address--city',
        '[data-automation-id="formField-city"] input',
    ],
    # State / province — NOT country. Do not include formField-country here.
    "addressSection_countryRegion": [
        '[data-automation-id="addressSection_countryRegion"]',
        '[data-automation-id="formField-countryRegion"] button',
        '[data-automation-id="formField-countryRegion"] [role="button"]',
        '[data-automation-id="formField-countryRegion"]',
        'button[name="countryRegion"]',
    ],
    "addressSection_regionSubdivision1": [
        '[data-automation-id="addressSection_regionSubdivision1"]',
        'input[name="regionSubdivision1"]',
        '#address--regionSubdivision1',
        '[data-automation-id="formField-regionSubdivision1"] input',
        '[data-automation-id="formField-county"] input',
    ],
    "addressSection_postalCode": [
        '[data-automation-id="addressSection_postalCode"]',
        'input[name="postalCode"]',
        '#address--postalCode',
        '[data-automation-id="formField-postalCode"] input',
    ],
    "phone-number": [
        'input[data-automation-id="phone-number"]',
        '[data-automation-id="phone-number"] input',
        '[data-automation-id="formField-phoneNumber"] input[type="text"]',
        '[data-automation-id="formField-phoneNumber"] input:not([type="hidden"])',
        'input[name="phoneNumber"]:not([type="hidden"])',
        '#phoneNumber--phoneNumber',
        '[data-automation-id="phone-number"]',
    ],
    "phone-device-type": [
        '[data-automation-id="phone-device-type"]',
        '[data-automation-id="formField-phoneType"] button',
        '[data-automation-id="formField-phoneType"] [role="button"]',
        '[data-automation-id="formField-phoneType"]',
        '[data-automation-id="phoneType"]',
        'button[name="phoneType"]',
    ],
    "how_heard": [
        'input[name="source--source"]',
        '[data-automation-id="source--source"]',
        '[data-automation-id="formField-source"] input',
        '[data-automation-id="formField-source"] button',
        '[data-automation-id="formField-source"] [role="button"]',
        '[data-automation-id="formField-source"] [role="combobox"]',
        '[data-automation-id="source"]',
        '[data-automation-id="formField-howDidYouHear"] button',
        '[data-automation-id="formField-howDidYouHear"] input',
        '[data-automation-id="formField-candidateSource"] button',
        '[data-automation-id="formField-candidateSource"] input',
        'button[name="source"]',
        'input[name="source"]',
        'label:has-text("How Did You Hear") ~ * button',
        'label:has-text("How Did You Hear About Us") ~ * [role="button"]',
        'label:has-text("How Did You Hear About Us") ~ * input',
        'label:has-text("Where Did You Hear") ~ * button',
        'label:has-text("Where Did You Hear About Us") ~ * [role="button"]',
        'label:has-text("Where Did You Hear About Us") ~ * input',
        'label:has-text("Where did you hear") ~ * [role="combobox"]',
    ],
    "contact_email": [
        'input[name="emailAddress"]',
        '#emailAddress',
        '[data-automation-id="emailAddress"]',
        '[data-automation-id="formField-emailAddress"] input',
        '[data-automation-id="formField-email"] input',
        'input[data-automation-id="email"]',
        # Prefer contact/My Info email — not create-account password form
        '[data-automation-id="applyFlowMyInfoPage"] input[type="email"]',
        '[data-automation-id="contactInformationPage"] input[type="email"]',
        'input[type="email"][name*="email" i]',
    ],
    "worked_here_before": [
        'input[name="candidateIsPreviousWorker"]',
        '[data-automation-id="formField-previousWorker"]',
        '[data-automation-id="previousWorker"]',
        '[data-automation-id="formField-candidateIsPreviousWorker"]',
        'fieldset:has-text("previously worked")',
        'fieldset:has-text("Cisco before")',
        'fieldset:has-text("worked at BBH")',
        'fieldset:has-text("employed by")',
        'fieldset:has-text("employed previously")',
        'fieldset:has-text("Have you been employed")',
        'div:has-text("previously been employed"):has(input[type="radio"])',
        'div:has-text("previously worked at"):has(input[type="radio"])',
        'div:has-text("employed by"):has(input[type="radio"])',
        'div:has-text("employed previously"):has(input[type="radio"])',
        'div:has-text("Have you been employed"):has(input[type="radio"])',
        'div:has-text("employed by Quantiphi"):has(input[type="radio"])',
    ],
}

# CSS pack for fast_fill.apply_selector_pack fallback. Live Workday uses
# workday_two_phase_on_page (phone device type owned there, not this pack).
# ATS3-009: county (regionSubdivision1) is often Select One combobox.
_WD_COMBOBOX_AIDS = frozenset(
    {
        "addressSection_country",
        "addressSection_countryRegion",
        "addressSection_regionSubdivision1",
        "phone-device-type",
    }
)


def _build_wd_selector_pack() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for aid, ftype in WD_CONTACT_PACK:
        # ATS2-009: include phone-device-type for pack-only paths; two_phase
        # still owns the dedicated filler and skips already-correct.
        sels = WD_CONTACT_SELECTORS.get(aid) or []
        primary = sels[0] if sels else f'[data-automation-id="{aid}"]'
        mode = "combobox" if aid in _WD_COMBOBOX_AIDS else "fill"
        rows.append((primary, ftype, mode))
    rows.extend(
        [
            ('[data-automation-id="email"]', EMAIL, "fill"),
            ('[data-automation-id="password"]', PASSWORD, "fill"),
            ('[data-automation-id="verifyPassword"]', PASSWORD_CONFIRM, "fill"),
            ('[data-automation-id="file-upload-input-ref"]', RESUME_UPLOAD, "file"),
        ]
    )
    return rows


WD_SELECTOR_PACK: list[tuple[str, str, str]] = _build_wd_selector_pack()

VALIDATION_BANNER_NEEDLES = (
    "errors found",
    "error: the field",
    "is required and must have a value",
    "please correct the following",
    "please fix the following",
    "you must complete all required",
)

# Error-banner phrases only — do NOT include the normal "Already have an
# account? Sign In" link text (that caused a false Sign-In divert on Cisco).
ALREADY_REGISTERED_NEEDLES = (
    "already registered",
    "already associated with an account",
    "email address is already in use",
    "email address is already registered",
    "an account with this email already exists",
    "this email is already registered",
)

CREATE_ACCOUNT_SELECTORS = [
    '[data-automation-id="createAccountSubmitButton"]',
    "button:has-text('Create Account')",
    "a:has-text('Create Account')",
]

SIGN_IN_SELECTORS = [
    '[data-automation-id="signInSubmitButton"]',
    "button:has-text('Sign In')",
    "button:has-text('Sign in')",
    "a:has-text('Sign In')",
]

SIGN_IN_LINK_SELECTORS = [
    # Prefer in-form "Sign In" link — NOT the header utilityButtonSignIn
    '[data-automation-id="signInLink"]',
    "a:has-text('Sign In')",
    "button:has-text('Sign In')",
]

APPLY_PRIMARY_SELECTORS = [
    '[data-automation-id="adventureButton"]',
    "button:has-text('Apply')",
    "a:has-text('Apply')",
    "text=Apply",
]

APPLY_WITH_RESUME_SELECTORS = [
    '[data-automation-id="applyWithResume"]',
    '[data-automation-id="autofillWithResume"]',
    "a:has-text('Apply with Resume')",
    "button:has-text('Apply with Resume')",
    "a:has-text('Apply With Resume')",
    "button:has-text('Apply With Resume')",
    "text=Apply with Resume",
    "text=Apply With Resume",
    "a:has-text('Autofill with Resume')",
    "button:has-text('Autofill with Resume')",
    "text=Autofill with Resume",
]

# Real-profile only — NEVER click in dummy/test_mode (loads prior real PII).
USE_MY_LAST_APPLICATION_SELECTORS = [
    "a:has-text('Use My Last Application')",
    "button:has-text('Use My Last Application')",
    "text=Use My Last Application",
]

# Legacy alias — primary Apply only (no manual/resume sub-paths)
APPLY_SELECTORS = APPLY_PRIMARY_SELECTORS

APPLY_MANUAL_SELECTORS = [
    '[data-automation-id="applyManually"]',
    "a:has-text('Apply Manually')",
    "button:has-text('Apply Manually')",
    "text=Apply Manually",
]


def prefer_manual_after_autofill_risk(report: dict | None) -> bool:
    """True when Autofill-with-Resume should not be re-attempted this run.

    After a CAPTCHA already cleared once (or autofill stuck/upload failed),
    prefer Apply Manually — fewer upload/parse round-trips Cloudflare scores.
    """
    if not isinstance(report, dict):
        return False
    if report.get("prefer_manual_entry"):
        return True
    if report.get("autofill_captcha_seen"):
        return True
    if report.get("captcha_human_solved"):
        return True
    blocker = str(report.get("blocker") or "")
    if blocker in ("captcha", "cloudflare"):
        return True
    cw = report.get("captcha_wait")
    if isinstance(cw, dict) and (
        cw.get("solved_gone") or cw.get("via") in ("enter", "sentinel", "gone")
    ):
        return True
    reasons = report.get("autofill_risk_reasons") or []
    if reasons:
        return True
    return False


def mark_autofill_risk(report: dict | None, *, reason: str) -> None:
    """Record that autofill path is captcha-prone / stuck — prefer manual next."""
    if not isinstance(report, dict):
        return
    report["prefer_manual_entry"] = True
    report.setdefault("autofill_risk_reasons", [])
    if reason and reason not in report["autofill_risk_reasons"]:
        report["autofill_risk_reasons"].append(reason)
    if reason in ("captcha", "cloudflare", "captcha_reappeared", "interactive_captcha"):
        report["autofill_captcha_seen"] = True


def upload_stuck_reason(upload_meta: dict | None) -> str | None:
    """Classify resume upload failure that should trigger manual fallback."""
    if not isinstance(upload_meta, dict):
        return None
    reason = str(
        upload_meta.get("reason")
        or (upload_meta.get("result") or {}).get("reason")
        or upload_meta.get("error")
        or ""
    ).lower()
    if not reason:
        if upload_meta.get("verified") is False and upload_meta.get("attempted"):
            return "upload_unverified"
        return None
    needles = (
        "no_file_input",
        "chooser_unverified",
        "filechooser",
        "file_chooser",
        "upload_error",
        "pdf_missing",
        "resume_unverified",
        "probe_empty_after_upload",
        "timeout",
    )
    for n in needles:
        if n in reason:
            return n
    if upload_meta.get("verified") is False and upload_meta.get("attempted"):
        return "upload_unverified"
    return None


def parse_automation_ids(notes_text: str) -> list[str]:
    """Extract data-automation-id values cited in ats_notes/workday.md."""
    ids = re.findall(
        r'data-automation-id="([^"]+)"|`([a-zA-Z0-9_-]+)`',
        notes_text,
    )
    out: list[str] = []
    seen: set[str] = set()
    for a, b in ids:
        aid = a or b
        if not aid or aid in seen:
            continue
        if a or re.search(
            r"(Section|Page|Button|button|phone|email|password|file-upload|"
            r"workExperience|formField|dateSection|adventure|apply|createAccount|"
            r"signIn|bottom-navigation|hispanic|ethnicity|veteran|gender|agreement)",
            aid,
        ):
            seen.add(aid)
            out.append(aid)
    return out


def pick_myworkday_url(listings_dir: Path = LISTINGS_DIR) -> dict:
    """Pick first public myworkdayjobs URL from newest *-qualified.json."""
    files = sorted(listings_dir.glob("*-qualified.json"), reverse=True)
    for path in files:
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        jobs = data if isinstance(data, list) else data.get("jobs") or []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            for key in (
                "job_url_direct",
                "apply_url",
                "url",
                "job_url",
                "application_url",
                "link",
            ):
                url = job.get(key) or ""
                if isinstance(url, str) and "myworkdayjobs.com" in url.lower():
                    return {
                        "url": url,
                        "company": job.get("company") or job.get("company_name"),
                        "title": job.get("title") or job.get("job_title"),
                        "source_file": path.name,
                        "url_key": key,
                    }
    raise RuntimeError(f"no myworkdayjobs URL in {listings_dir}/*-qualified.json")


def _tenant_key(url: str) -> str:
    return urlparse(url).netloc or url


def _street_line(address_text: str) -> str:
    """Bare street from DUMMY_ADDRESS (before city/state/zip)."""
    m = re.match(r"^(.+?),\s*[A-Za-z .'\-]+,\s*[A-Z]{2}\s+\d{5}", address_text)
    return m.group(1).strip() if m else address_text.split(",")[0].strip()


def _detect_hard_blocker(page_text: str, title: str, url: str) -> str | None:
    """CAPTCHA / bot-wall only — never solve; stop and report."""
    blob = f"{title}\n{url}\n{page_text}".lower()
    checks = [
        ("captcha", ("captcha", "recaptcha", "hcaptcha", "cf-challenge", "challenge-platform")),
        ("akamai", ("akamai", "access denied", "reference #", "pardon our interruption",
                    "bot detection", "unusual traffic")),
        ("cloudflare", ("just a moment", "cf-browser-verification", "attention required")),
    ]
    for name, needles in checks:
        if any(n in blob for n in needles):
            return name
    return None


async def _hard_blocker_live(page, *, limit: int = 6000) -> str | None:
    """Text hard-blocker corroborated by a *visible* interactive challenge.

    Cloudflare Turnstile / reCAPTCHA leave 'captcha' / 'challenge-platform' /
    'just a moment' strings in the DOM even after a managed challenge passes, so
    a pure-text match falsely hard-blocks a fully interactive page (observed on
    Workday create-account: the interactive challenge cleared via captcha_pause,
    yet the text detector re-flagged captcha and skipped the whole flow). For
    captcha/cloudflare, require an actually-visible interactive challenge before
    blocking; akamai / access-denied walls stay text-only (no widget to see).
    """
    body = await _body_text(page, limit)
    try:
        title = await page.title()
    except Exception:
        title = ""
    name = _detect_hard_blocker(body, title, getattr(page, "url", "") or "")
    if not name:
        return None
    if name in ("captcha", "cloudflare"):
        try:
            from captcha_pause import page_shows_interactive_captcha

            if await page_shows_interactive_captcha(page):
                return name
            return None  # script/widget in DOM but no active challenge — passed
        except Exception:
            return name  # fail closed if the live check is unavailable
    return name


def _already_registered(page_text: str) -> bool:
    low = (page_text or "").lower()
    return any(n in low for n in ALREADY_REGISTERED_NEEDLES)


async def _body_text(page, limit: int = 6000) -> str:
    try:
        return (await page.inner_text("body", timeout=5000))[:limit]
    except Exception:
        return ""


async def _wait_for_apply(page, timeout_ms: int = 15000) -> None:
    """Workday job pages are SPA — wait for Apply before clicking."""
    try:
        await page.wait_for_selector(
            '[data-automation-id="adventureButton"], '
            '[data-automation-id="applyManually"], '
            '[data-automation-id="applyWithResume"], '
            'button:has-text("Apply with Resume"), '
            'button:has-text("Autofill with Resume"), '
            'button:has-text("Apply"), a:has-text("Apply")',
            timeout=timeout_ms,
            state="visible",
        )
    except Exception:
        pass


async def _poll_spa_settle(
    page,
    *,
    timeout_ms: int = 2800,
    poll_ms: int = 250,
    settle_ms: int = 120,
    predicates: list | None = None,
) -> bool:
    """ATS2-016 / ATS3-015: poll for SPA readiness instead of fixed 3.5–4.5s sleeps.

    Each predicate is ``async (page) -> bool``.
    """
    preds = list(predicates or [])
    deadline = time.time() + max(200, timeout_ms) / 1000.0
    interval = max(80, int(poll_ms))
    while time.time() < deadline:
        for pred in preds:
            try:
                if await pred(page):
                    if settle_ms > 0:
                        await page.wait_for_timeout(settle_ms)
                    return True
            except Exception:
                continue
        await page.wait_for_timeout(interval)
    for pred in preds:
        try:
            if await pred(page):
                return True
        except Exception:
            continue
    return False


async def _wd_spa_step_probe(page) -> dict:
    """DOM landmarks for Workday multipage SPA (ATS3-011)."""
    try:
        return await page.evaluate(
            """() => {
              const visible = (id) => {
                const el = document.querySelector(`[data-automation-id="${id}"]`);
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
              };
              const anyVisible = (ids) => ids.some(visible);
              return {
                contact: anyVisible([
                  'contactInformationPage', 'applyFlowMyInfoPage',
                  'legalNameSection_firstName',
                  'source--source', 'formField-source',
                ]) || !!(
                  document.querySelector('input[name="legalName--firstName"]')
                  || document.querySelector('input[name="source--source"]')
                  || document.querySelector('[data-automation-id="source--source"]')
                  || document.querySelector('#name--legalName--firstName')
                ),
                experience: anyVisible([
                  'myExperiencePage', 'workExperienceSection', 'workExperience-1',
                  'educationHistorySection', 'educationSection',
                  'file-upload-input-ref', 'skillsSection',
                ]),
                appQ: anyVisible([
                  'applicationQuestionsPage', 'questionnairePage',
                  'secondaryQuestionnairePage',
                ]),
                eeo: anyVisible([
                  'voluntaryDisclosuresPage', 'voluntaryDisclosures',
                ]),
                selfId: anyVisible(['selfIdentificationPage', 'selfIdentifyPage']),
                review: anyVisible(['reviewPage', 'reviewSubmitPage']),
                progress: (() => {
                  const el = document.querySelector(
                    '[data-automation-id="wizardProgress"],'
                    + '[data-automation-id="progressBar"],'
                    + '[aria-current="step"],'
                    + '[data-automation-id="pageHeader"],'
                    + '[data-automation-id="stepHeader"]'
                  );
                  if (!el) return '';
                  return (el.innerText || el.textContent || '')
                    .trim().replace(/\\s+/g, ' ').slice(0, 80);
                })(),
              };
            }"""
        )
    except Exception:
        return {}


def _wd_spa_moved(before: dict | None, after: dict | None) -> bool:
    """True when SPA probes show a real step change after ADVANCE."""
    b = before or {}
    a = after or {}
    if a.get("experience") or a.get("appQ") or a.get("eeo") or a.get("selfId") or a.get("review"):
        return True
    if b.get("contact") and not a.get("contact"):
        return True
    bp = str(b.get("progress") or "").strip().lower()
    ap = str(a.get("progress") or "").strip().lower()
    if bp and ap and bp != ap:
        return True
    return False


def _wd_spa_step_hint_from_probe(before_dom: dict, moved_dom: dict) -> str:
    """Synthetic step_hint when URL/title fingerprint stays flat after SPA move."""
    if moved_dom.get("experience"):
        return "myExperiencePage"
    if moved_dom.get("appQ"):
        return "applicationQuestionsPage"
    if moved_dom.get("eeo"):
        return "voluntaryDisclosuresPage"
    if moved_dom.get("selfId"):
        return "selfIdentificationPage"
    if moved_dom.get("review"):
        return "reviewPage"
    if before_dom.get("contact") and not moved_dom.get("contact"):
        return "left_contact"
    return (moved_dom.get("progress") or "spa_dom_moved")[:80]


def detect_workday_current_step(probe: dict | None = None, *, progress_text: str = "") -> str:
    """Map SPA probe / progress text → contact|experience|app_questions|eeo|self_id|review|unknown."""
    p = probe or {}
    if p.get("review"):
        return "review"
    if p.get("selfId"):
        return "self_id"
    if p.get("eeo"):
        return "eeo"
    if p.get("appQ"):
        return "app_questions"
    if p.get("experience"):
        return "experience"
    if p.get("contact"):
        return "contact"
    prog = str(progress_text or p.get("progress") or "").strip().lower()
    if prog:
        try:
            from page_progress import normalize_workday_step_label

            canon = normalize_workday_step_label(prog)
            if canon == "review":
                return "review"
            if canon == "self identify":
                return "self_id"
            if canon == "voluntary disclosures":
                return "eeo"
            if canon == "application questions":
                return "app_questions"
            if canon == "my experience":
                return "experience"
            if canon == "my information":
                return "contact"
        except Exception:
            pass
        if "review" in prog:
            return "review"
        if "self ident" in prog:
            return "self_id"
        if "voluntary" in prog or "disclosure" in prog:
            return "eeo"
        if "application question" in prog or "questionnaire" in prog:
            return "app_questions"
        if "experience" in prog:
            return "experience"
        if "information" in prog or "my info" in prog:
            return "contact"
    return "unknown"


async def _detect_workday_current_step(page) -> tuple[str, dict]:
    """Live SPA probe + current step id for mid-wizard resume."""
    probe = await _wd_spa_step_probe(page)
    step = detect_workday_current_step(probe, progress_text=str(probe.get("progress") or ""))
    return step, probe


async def _run_workday_phases_from(
    page,
    values: dict,
    report: dict,
    *,
    start: str,
) -> None:
    """Run Phase C→E starting at ``start`` (experience|app_questions|eeo|self_id|review).

    Never submits. Stops on hard blockers / validation. Used for first-pass
    multipage and mid-wizard resume after captcha / contact-absent re-entry.
    """
    if start == "review":
        report["workday_current_step"] = "review"
        report.setdefault(
            "phase_e",
            {"name": "E_self_id", "skipped": "already_on_review", "stopped_at_review": True},
        )
        pe = report["phase_e"]
        pe["stopped_at_review"] = True
        try:
            from page_progress import apply_live_vision_gate, can_claim_ready

            await apply_live_vision_gate(page, report)
            if can_claim_ready(report):
                report["ready_for_review"] = True
                report["verdict"] = "SUCCESS"
        except Exception as e:
            report.setdefault("errors", []).append({"review_resume_vision": str(e)[:120]})
        return

    if start not in ("experience", "app_questions", "eeo", "self_id"):
        return

    async def _pause() -> None:
        try:
            from fill_pause import ensure_fill_pause_ready

            await ensure_fill_pause_ready(page, report.get("_step_report") or report)
        except Exception:
            pass

    hard = (
        "captcha",
        "akamai",
        "cloudflare",
        "email_verify",
        "validation_errors",
    )

    def _ok_to_continue() -> bool:
        return (
            not report.get("validation_after_advance")
            and report.get("blocker") not in hard
        )

    # --- Experience ---
    if start == "experience":
        report["phase_c"] = await _phase_c_experience(page, values, report)
        await _pause()
        pc = report.get("phase_c") or {}
        # Sparse Experience (title/company only) — one deterministic retry before
        # giving up on ADVANCE (Thales-class early hold).
        if (
            pc.get("present")
            and not pc.get("advanced")
            and not report.get("validation_after_advance")
            and report.get("blocker") in (None, "page_incomplete")
            and not pc.get("_retried_sparse")
        ):
            filled_aids = {
                str(f.get("automation_id") or "")
                for f in (pc.get("filled") or [])
                if isinstance(f, dict)
            }
            has_title = any("jobTitle" in a for a in filled_aids)
            has_company = any("/company" in a or a.endswith("company") for a in filled_aids)
            has_dates = any(
                "startDate" in a or "endDate" in a or "Date" in a for a in filled_aids
            )
            if (has_title or has_company) and not has_dates:
                report["blocker"] = None
                report["advance_blocked_reason"] = None
                report["phase_c"] = await _phase_c_experience(page, values, report)
                await _pause()
                pc = report.get("phase_c") or {}
                pc["_retried_sparse"] = True
                report["phase_c"] = pc
        if pc.get("advanced") and not (
            pc.get("validation_after_advance") or report.get("validation_after_advance")
        ):
            if report.get("blocker") == "page_incomplete":
                report["blocker"] = None
            report["advance_blocked_reason"] = None
        if not (pc.get("advanced") and _ok_to_continue()):
            return

    # --- Application questions ---
    if start in ("experience", "app_questions"):
        if start == "app_questions" or (report.get("phase_c") or {}).get("advanced"):
            report["phase_c2"] = await _phase_app_questions(page, values, report)
            await _pause()
            c2 = report.get("phase_c2") or {}
            if c2.get("advanced") or c2.get("skipped"):
                if report.get("blocker") == "page_incomplete" and c2.get("advanced"):
                    report["blocker"] = None
                    report["advance_blocked_reason"] = None
            if not ((c2.get("advanced") or c2.get("skipped")) and _ok_to_continue()):
                return
        else:
            return

    # --- EEO / voluntary disclosures ---
    if start in ("experience", "app_questions", "eeo"):
        c2 = report.get("phase_c2") or {}
        if start == "eeo" or c2.get("advanced") or c2.get("skipped"):
            report["phase_d"] = await _phase_d_eeo(page, values, report)
            await _pause()
            if not (
                (report.get("phase_d") or {}).get("advanced") and _ok_to_continue()
            ):
                return
        else:
            return

    # --- Self-ID → Review ---
    if start in ("experience", "app_questions", "eeo", "self_id"):
        pd = report.get("phase_d") or {}
        if start == "self_id" or pd.get("advanced"):
            report["phase_e"] = await _phase_e_self_id(page, values, report)


async def _poll_wd_spa_after_advance(
    page,
    phase: dict,
    before: dict,
    *,
    polls: int = 20,
    poll_ms: int = 120,
) -> tuple[dict, dict]:
    """Poll SPA landmarks after Next click (ATS2-011 / ATS3-011).

    Workday often keeps URL/title flat; return (after_fp, moved_dom) with a
    differentiated fingerprint when DOM shows we left the prior step.

    Fast poll (default 120ms) + early exit as soon as the next step mounts —
    avoids the ~5–10s human-pacing stall after ADVANCE before Phase C fill.
    """
    from page_progress import capture_step_fingerprint, step_fingerprint

    before_dom = await _wd_spa_step_probe(page)
    phase["spa_dom_before"] = before_dom
    after = await capture_step_fingerprint(page)
    moved_dom: dict = {}
    interval = max(60, int(poll_ms))
    for _ in range(polls):
        if after.get("fingerprint") and after["fingerprint"] != before["fingerprint"]:
            break
        if (
            (before.get("step_hint") or "").strip()
            and (after.get("step_hint") or "").strip()
            and (before.get("step_hint") or "").strip()
            != (after.get("step_hint") or "").strip()
        ):
            break
        moved_dom = await _wd_spa_step_probe(page)
        if _wd_spa_moved(before_dom, moved_dom):
            after = await capture_step_fingerprint(page)
            if after["fingerprint"] == before["fingerprint"]:
                after = dict(after)
                after["step_hint"] = _wd_spa_step_hint_from_probe(before_dom, moved_dom)
                after["fingerprint"] = step_fingerprint(
                    after.get("url") or "",
                    title=after.get("title") or "",
                    step_hint=after.get("step_hint") or "",
                )
            phase["spa_dom_moved"] = moved_dom
            break
        await page.wait_for_timeout(interval)
        after = await capture_step_fingerprint(page)
    else:
        await page.wait_for_timeout(80)
        moved_dom = await _wd_spa_step_probe(page)
        if _wd_spa_moved(before_dom, moved_dom):
            phase["spa_dom_moved"] = moved_dom
            if after["fingerprint"] == before["fingerprint"]:
                after = dict(after)
                after["step_hint"] = _wd_spa_step_hint_from_probe(before_dom, moved_dom)
                after["fingerprint"] = step_fingerprint(
                    after.get("url") or "",
                    title=after.get("title") or "",
                    step_hint=after.get("step_hint") or "",
                )
    return after, moved_dom


def _clear_false_stuck_after_spa_move(
    report: dict,
    phase: dict,
    progress: dict,
    before: dict,
    after: dict,
    before_dom: dict,
    moved_dom: dict,
    *,
    advanced: bool,
) -> dict:
    """Clear sticky stuck when SPA DOM moved despite flat URL fingerprint (ATS2-011)."""
    from page_progress import step_fingerprint

    if not (
        advanced
        and progress.get("stuck_on_same_page")
        and _wd_spa_moved(before_dom, phase.get("spa_dom_moved") or moved_dom)
    ):
        return after
    report["stuck_on_same_page"] = False
    phase["stuck_on_same_page"] = False
    progress["stuck_on_same_page"] = False
    report["advanced_count"] = int(report.get("advanced_count") or 0) + 1
    phase["spa_stuck_cleared"] = True
    if after["fingerprint"] == before["fingerprint"]:
        after = dict(after)
        hint = after.get("step_hint") or "spa_dom_moved"
        after["step_hint"] = hint
        after["fingerprint"] = step_fingerprint(
            after.get("url") or "",
            title=after.get("title") or "",
            step_hint=f"{hint}|cleared",
        )
        phase["fingerprint_after"] = after["fingerprint"]
        report["page_fingerprint_after"] = after["fingerprint"]
    return after


def _log_wd_entry_click(step_report: dict | None, *, text: str, reason: str, ok: bool) -> None:
    """Emit Workday entry path choice to parent fill step log."""
    if not step_report:
        return
    try:
        from fill_step_log import note_step

        note_step(
            step_report,
            action="click_entry",
            label=str(text or "")[:80],
            via="workday_entry",
            reason=reason,
            extra={"ok": ok},
        )
    except Exception:
        pass


async def _click_workday_apply_path(
    page,
    *,
    step_report: dict | None = None,
    report: dict | None = None,
) -> list[dict]:
    """Apply → prefer Autofill/Apply with Resume; manual when resume path absent
    or when autofill is captcha-prone (prefer_manual_after_autofill_risk).

    ATS-003: never click \"Use My Last Application\" in dummy/test_mode.
    """
    clicks: list[dict] = []
    await _wait_for_apply(page)
    primary = await _click_gated(page, APPLY_PRIMARY_SELECTORS)
    clicks.extend(primary)
    # ATS2-016: poll for entry-path UI instead of fixed 4s sleep
    await _poll_spa_settle(
        page,
        timeout_ms=3200,
        poll_ms=250,
        predicates=[
            _on_autofill_with_resume_url,
            _workday_resume_upload_present,
            _create_account_form,
            _password_only_signin,
            _contact_phase_present,
        ],
    )
    await _wait_for_apply(page, timeout_ms=8000)

    prefer_manual = prefer_manual_after_autofill_risk(report)
    resume_ok = False
    resume: list[dict] = []
    if not prefer_manual:
        resume = await _click_gated(page, APPLY_WITH_RESUME_SELECTORS)
        resume_ok = any(c.get("action") == "clicked" for c in resume)
        # Real profile only: Use My Last may be the only resume path
        test_mode = True
        if report is not None:
            test_mode = bool(report.get("test_mode", report.get("dummy", True)))
        if not resume_ok and not test_mode:
            last = await _click_gated(page, USE_MY_LAST_APPLICATION_SELECTORS)
            if any(c.get("action") == "clicked" for c in last):
                resume = last
                resume_ok = True
    else:
        if report is not None:
            report["workday_skipped_autofill"] = "prefer_manual_entry"

    if resume_ok:
        clicks.extend(resume)
        picked = next(c for c in resume if c.get("action") == "clicked")
        text = picked.get("text") or "Autofill with Resume"
        low = str(text).lower()
        if "use my last" in low:
            reason = "use_my_last_application"
        elif "autofill" in low:
            reason = "autofill_with_resume"
        else:
            reason = "apply_with_resume"
        _log_wd_entry_click(
            step_report,
            text=text,
            reason=reason,
            ok=True,
        )
        if report is not None:
            report["workday_entry_path"] = reason
            # FILL3-012: Use My Last pre-fills prior/real answers. Soft-match
            # already_correct_keep is intentional — do not thrash correct prior
            # values. Soft-match false-positives remain a residual risk; Pause
            # "Continue skips already filled" amplifies keep. Never enable this
            # path in dummy/test_mode (ATS-003).
            if reason == "use_my_last_application":
                report["prefill_keep_policy"] = "use_my_last_soft_match_keep"
        # Short SPA poll only — no fixed multi-second settle after resume click
        await _poll_spa_settle(
            page,
            timeout_ms=2800,
            poll_ms=200,
            predicates=[
                _on_autofill_with_resume_url,
                _workday_resume_upload_present,
                _create_account_form,
                _password_only_signin,
                _contact_phase_present,
            ],
        )
        # Live captcha after Autofill click → mark risk (manual on next opportunity)
        hard = await _hard_blocker_live(page)
        if hard in ("captcha", "cloudflare") and report is not None:
            mark_autofill_risk(report, reason="captcha_after_autofill_entry")
        return clicks

    manual = await _click_gated(page, APPLY_MANUAL_SELECTORS)
    manual_ok = any(c.get("action") == "clicked" for c in manual)
    if manual_ok:
        clicks.extend(manual)
        picked = next(c for c in manual if c.get("action") == "clicked")
        _log_wd_entry_click(
            step_report,
            text=picked.get("text") or "Apply Manually",
            reason=(
                "apply_manually_prefer_after_captcha"
                if prefer_manual
                else "apply_manually_fallback"
            ),
            ok=True,
        )
        if report is not None:
            report["workday_entry_path"] = (
                "apply_manually_prefer_after_captcha"
                if prefer_manual
                else "apply_manually_fallback"
            )
    await _poll_spa_settle(
        page,
        timeout_ms=3500,
        poll_ms=250,
        predicates=[
            _create_account_form,
            _password_only_signin,
            _contact_phase_present,
            _on_autofill_with_resume_url,
        ],
    )
    return clicks


async def _click_gated(
    page,
    selectors: list[str],
    *,
    stop_on_match: str | None = None,
    stop_after_click: bool = False,
) -> list[dict]:
    """Click ENTRY/ADVANCE controls from selectors; refuse FINAL.

    ``button:has-text("Continue")`` / ``text=Apply`` can resolve to FINAL
    siblings. Walk matches, pass type/aria/value into the gate, and never
    treat UNKNOWN as navigable.
    """
    clicked: list[dict] = []
    for sel in selectors:
        try:
            root = page.locator(sel)
            n = await root.count()
        except Exception as e:
            clicked.append({"selector": sel, "action": "error", "error": str(e)[:120]})
            continue
        if n == 0:
            continue
        for i in range(min(n, 8)):
            loc = root.nth(i)
            try:
                try:
                    await loc.wait_for(state="visible", timeout=2500 if i else 4000)
                except Exception:
                    if not await loc.is_visible():
                        continue
                resolved = await gate_locator_click(
                    loc, intent_label="", allow_kinds=NAV_KINDS
                )
                text = (resolved.get("actual") or "").strip() or sel
                if not resolved.get("ok"):
                    clicked.append({
                        "selector": sel,
                        "text": text,
                        "action": "refused",
                        "reason": resolved.get("reason"),
                        "kind": resolved.get("kind"),
                    })
                    continue
                low = text.lower()
                # Extra belt: never click job-level Submit even if mislabeled
                if "submit" in low and "create account" not in low:
                    clicked.append({
                        "selector": sel,
                        "text": text,
                        "action": "refused",
                        "reason": "submit-like without create-account",
                        "kind": resolved.get("kind"),
                    })
                    continue
                await loc.scroll_into_view_if_needed()
                url0 = page.url or ""
                await loc.click(timeout=8000)
                # ATS2-016: short poll for SPA reaction instead of fixed 4s
                async def _url_moved(p):
                    return (p.url or "") != url0

                await _poll_spa_settle(
                    page,
                    timeout_ms=2200,
                    poll_ms=200,
                    settle_ms=100,
                    predicates=[
                        _url_moved,
                        _create_account_form,
                        _password_only_signin,
                        _contact_phase_present,
                        _on_autofill_with_resume_url,
                        _workday_resume_upload_present,
                    ],
                )
                clicked.append({
                    "selector": sel,
                    "text": text,
                    "action": "clicked",
                    "kind": resolved.get("kind"),
                })
                if stop_after_click:
                    return clicked
                if stop_on_match and stop_on_match in (low + " " + sel.lower()):
                    return clicked
                if stop_on_match is None and (
                    "apply" in low
                    or "adventure" in sel.lower()
                    or "applyManually" in sel
                ):
                    return clicked
                # Matched + clicked one node for this selector — stop scanning
                break
            except Exception as e:
                clicked.append({
                    "selector": sel,
                    "action": "error",
                    "error": str(e)[:120],
                })
    return clicked


from fill_verify import how_heard_candidates as _how_heard_candidates  # noqa: E402
from fill_verify import is_verified_fill_row as _is_verified_fill  # noqa: E402
from verified_select import expand_state_value as _expand_state_value  # noqa: E402
from verified_select import value_matches_readback as _value_matches_readback  # noqa: E402


def _norm_digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


async def _read_field_value(loc) -> str:
    """Read visible value from input/textarea/combobox button.

    For Workday How-Heard / source filter inputs, prefer formField chip chrome
    via ``read_combobox_display`` so already-committed sources skip re-fill.
    """
    try:
        from verified_select import read_combobox_display

        combo = await read_combobox_display(loc)
        if combo:
            return combo
    except Exception:
        pass
    try:
        tag = (await loc.evaluate("el => el.tagName")).lower()
        role = (await loc.get_attribute("role")) or ""
        if tag in ("input", "textarea"):
            return (await loc.input_value()) or ""
        if tag == "button" or role in ("combobox", "button"):
            txt = (await loc.inner_text()).strip()
            if txt:
                return txt
            return (await loc.get_attribute("aria-label") or "").strip()
        # wrapper: nested input first
        nested = loc.locator("input:not([type='hidden']), textarea").first
        if await nested.count():
            try:
                return (await nested.input_value()) or ""
            except Exception:
                pass
        return (await loc.inner_text()).strip()
    except Exception:
        return ""


async def _read_how_heard_display(page) -> str:
    """Read How-Heard / source formField chip chrome (not filter input alone)."""
    sels = (
        '[data-automation-id="formField-source"]',
        '[data-automation-id*="formField-source"]',
        '[data-automation-id="formField-how_heard"]',
        '[data-automation-id="formField-howDidYouHear"]',
        '[data-automation-id="formField-candidateSource"]',
        '[data-automation-id="multiSelectContainer"]',
    )
    for sel in sels:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            try:
                if not await loc.is_visible(timeout=250):
                    continue
            except Exception:
                pass
            snip = ((await loc.inner_text()) or "").strip()
            if snip:
                return snip[:240]
        except Exception:
            continue
    return ""


async def _probe_how_heard_already_committed(
    page, candidates: list[str]
) -> dict | None:
    """If How-Heard already has a committed chip/token, return keep result.

    Stops Indeed → Company Website → LinkedIn → Other alias thrash once any
    concrete source is selected (prefer matching intended; else keep any chip).
    """
    from verified_select import (
        how_heard_source_committed,
        is_multiselect_uncommitted,
        multiselect_has_chip,
        settle_open_listbox,
        soft_value_match,
    )

    snip = await _read_how_heard_display(page)
    if not snip or is_multiselect_uncommitted(snip):
        return None
    if not how_heard_source_committed(snip, candidates):
        # Still accept any ≥1 chip even if label isn't in our alias list
        if not multiselect_has_chip(snip):
            return None
    matched = ""
    for c in candidates:
        if soft_value_match(c, snip):
            matched = c
            break
    if not matched and multiselect_has_chip(snip):
        # Concrete chip present — keep it; do not thrash to next alias
        matched = str(candidates[0] if candidates else "selected")
    if not matched:
        return None
    try:
        await settle_open_listbox(page)
    except Exception:
        pass
    return {
        "automation_id": "how_heard",
        "status": "filled",
        "reason": "already_correct_keep",
        "mode": "how_heard_chip",
        "type": HOW_HEARD,
        "value": matched,
        "readback": snip[:120],
        "option_text": matched,
        "picked": matched,
        "option_clicked": False,
        "verified": True,
        "committed": True,
        "skipped_already_correct": True,
    }


async def _resolve_contact_locator(page, automation_id: str):
    """Return (locator, matched_selector) for classic or apply-flow contact fields."""
    selectors = WD_CONTACT_SELECTORS.get(
        automation_id,
        [f'[data-automation-id="{automation_id}"]'],
    )
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.count() == 0:
                continue
            # Prefer a visible match when possible
            try:
                if await loc.is_visible(timeout=400):
                    return loc, sel
            except Exception:
                pass
            return loc, sel
        except Exception:
            continue
    return page.locator(f'[data-automation-id="{automation_id}"]').first, (
        f'[data-automation-id="{automation_id}"]'
    )


_PHONE_DEVICE_VALUES = frozenset({
    "mobile", "home", "work", "cell", "cellular", "office", "other",
    "landline", "telephone",
})
_PHONE_DEVICE_PREFERRED = ("Mobile", "Cell", "Cellular", "Home")
_PHONE_DEVICE_MOBILE_TOKENS = frozenset({"mobile", "cell", "cellular"})
_PHONE_DEVICE_HOME_TOKENS = frozenset({"home", "landline", "telephone"})


def _looks_like_dial_code_option(text: str) -> bool:
    """True for country phone-code rows — aligned with gh_select (ATS-007)."""
    try:
        from gh_select import looks_like_dial_code_option as _gh_dial

        return bool(_gh_dial(text))
    except Exception:
        pass
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    if re.search(r"\(\s*\+\d{1,4}\s*\)|\+\s*\d{1,4}\b|^\s*\+\d{1,4}\s*$", t):
        return True
    if "+" in t and any(
        x in low for x in ("united states", "united kingdom", "canada", "australia")
    ):
        return True
    return False


# NANP +1 territories that are NOT United States — bare "+1" must never verify as US.
_NANP_NON_US_TERRITORIES = (
    "anguilla",
    "antigua",
    "aruba",
    "bahamas",
    "barbados",
    "belize",
    "bermuda",
    "british virgin",
    "canada",
    "cayman",
    "dominica",
    "dominican",
    "grenada",
    "guam",
    "jamaica",
    "montserrat",
    "northern mariana",
    "puerto rico",
    "saint kitts",
    "st kitts",
    "saint lucia",
    "st lucia",
    "saint vincent",
    "st vincent",
    "sint maarten",
    "trinidad",
    "tobago",
    "turks and caicos",
    "us virgin",
    "u.s. virgin",
    "virgin islands",
    "american samoa",
    "curacao",
    "curaçao",
)


def _is_us_country_phone_readback(shown: str) -> bool:
    """True only when Country Phone Code display is clearly United States (+1).

    ATS-008 / ATS2-002: bare ``+1`` / ``Jamaica (+1)`` / other NANP must fail.
    """
    s = (shown or "").strip()
    if not s:
        return False
    low = s.lower()
    if any(t in low for t in _NANP_NON_US_TERRITORIES):
        return False
    us_named = "united states" in low or bool(re.search(r"\busa\b", low))
    if not us_named:
        return False
    # Prefer dial present, but "United States of America" alone is OK
    return True


def _is_phone_device_readback(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    if _looks_like_dial_code_option(text):
        return False
    return any(v in low for v in _PHONE_DEVICE_VALUES)


def _phone_device_matches_intent(want: str, readback: str) -> bool:
    """ATS-006: Mobile intent must not verify as Home (and vice versa)."""
    w = (want or "Mobile").strip().lower()
    r = (readback or "").strip().lower()
    if not r or not _is_phone_device_readback(readback):
        return False
    if any(t in w for t in _PHONE_DEVICE_MOBILE_TOKENS):
        return any(t in r for t in _PHONE_DEVICE_MOBILE_TOKENS)
    if any(t in w for t in _PHONE_DEVICE_HOME_TOKENS):
        return any(t in r for t in _PHONE_DEVICE_HOME_TOKENS) and not any(
            t in r for t in _PHONE_DEVICE_MOBILE_TOKENS
        )
    # Other intents (Work/Office): require token overlap with want
    return w in r or any(tok and tok in r for tok in w.split())


async def _fill_phone_device_type(
    page, loc, sel: str, value: str = "Mobile", *, _recursion_retry: bool = True
) -> dict:
    """Fill Workday Phone Device Type — Mobile, never dial-code / country list.

    Markel-class tenants use ``formField-phoneType`` button. The generic typable
    combobox path can RecursionError or land on Country Phone Code (Anguilla).
    Prefer: open → click matching device option → verify readback.
    ATS2-015: on RecursionError, Escape + retry once; else degrade explicitly.
    """
    want = (value or "Mobile").strip() or "Mobile"
    cands = list(
        dict.fromkeys(
            [want, *[p for p in _PHONE_DEVICE_PREFERRED if p.lower() != want.lower()]]
        )
    )
    result: dict = {
        "automation_id": "phone-device-type",
        "status": "missed",
        "mode": "combobox",
        "value": want,
        "selector": sel,
        "type": "PHONE_DEVICE",
        "verified": False,
    }
    try:
        existing = (await _read_field_value(loc) or "").strip()
    except Exception:
        existing = ""
    # Already Mobile/Cell — skip thrash (Landline / dial-code must be overwritten)
    if existing and _phone_device_matches_intent(want, existing):
        result.update(
            {
                "status": "filled",
                "reason": "already_correct_skip",
                "readback": existing[:120],
                "verified": True,
                "skipped_already_correct": True,
            }
        )
        return result

    try:
        await loc.scroll_into_view_if_needed()
        await loc.click(timeout=4000)
        await page.wait_for_timeout(350)
    except Exception as e:
        result["reason"] = "open_failed"
        result["error"] = str(e)[:120]
        return result

    # Direct option click — never type into filter (avoids dial-code list thrash)
    clicked = False
    picked = None
    try:
        for cand in cands:
            ok, txt = await _click_matching_option(page, cand, device_type=True)
            if ok and txt and not _looks_like_dial_code_option(txt):
                clicked = True
                picked = txt
                break

        if not clicked:
            # Scan visible options once for device tokens
            opts = page.locator('[role="option"], [data-automation-id="promptOption"]')
            n = min(await opts.count(), 24)
            for i in range(n):
                el = opts.nth(i)
                try:
                    if not await el.is_visible(timeout=300):
                        continue
                    txt = ((await el.inner_text()) or "").strip()
                except Exception:
                    continue
                if not txt or _looks_like_dial_code_option(txt):
                    continue
                low = txt.lower()
                if any(c.lower() in low for c in cands) or low in _PHONE_DEVICE_VALUES:
                    await el.click(timeout=3000)
                    clicked = True
                    picked = txt
                    break
    except RecursionError as e:
        try:
            await _escape_unless_captcha(page)
        except Exception:
            pass
        if _recursion_retry:
            await page.wait_for_timeout(450)
            out = await _fill_phone_device_type(
                page, loc, sel, value, _recursion_retry=False
            )
            out["retried_after_recursion"] = True
            if not out.get("verified"):
                out["reason"] = out.get("reason") or "fill_error_after_recursion_retry"
                out["degraded"] = True
                out["error"] = out.get("error") or f"RecursionError:{e}"[:120]
            return out
        result["reason"] = "fill_error"
        result["error"] = f"RecursionError:{e}"[:120]
        result["degraded"] = True
        return result
    except Exception as e:
        result["scan_error"] = str(e)[:80]

    await page.wait_for_timeout(300)
    try:
        readback = (await _read_field_value(loc) or picked or "").strip()
    except Exception:
        readback = (picked or "").strip()
    result["readback"] = readback[:120]
    result["option_clicked"] = clicked
    result["option_text"] = (picked or "")[:80] if picked else None
    result["algorithm"] = "phone_device_click"

    if readback and _looks_like_dial_code_option(readback):
        result["reason"] = "dial_code_not_device"
        try:
            await _escape_unless_captcha(page)
        except Exception:
            pass
        return result

    ok = bool(
        readback
        and _phone_device_matches_intent(want, readback)
    )
    if ok:
        result["status"] = "filled"
        result["verified"] = True
    else:
        result["reason"] = result.get("reason") or (
            "no_matching_option" if not clicked else "readback_mismatch"
        )
        try:
            await _escape_unless_captcha(page)
        except Exception:
            pass
    return result


async def _fill_country_phone_code(
    page, values: dict | None = None, *, _recursion_retry: bool = True
) -> dict:
    """Ensure Country Phone Code is United States (+1), not Anguilla/etc."""
    detail: dict = {
        "automation_id": "countryPhoneCode",
        "status": "missed",
        "mode": "combobox",
        "verified": False,
        "type": "PHONE_COUNTRY_CODE",
    }
    selectors = [
        '[data-automation-id="formField-countryPhoneCode"] button',
        '[data-automation-id="formField-countryPhoneCode"] [role="combobox"]',
        '[data-automation-id="formField-countryPhoneCode"] [role="button"]',
        '[data-automation-id="formField-phoneCountry"] button',
        '[data-automation-id="formField-phoneCountryCode"] button',
        '[data-automation-id="countryPhoneCode"]',
        '[data-automation-id="phone-country-code"]',
        '[data-automation-id="phoneCountry"]',
        'button[name="countryPhoneCode"]',
        'button[id*="countryPhoneCode" i]',
        'button[id*="phoneCountry" i]',
    ]
    loc = None
    sel = ""
    for s in selectors:
        try:
            cand = page.locator(s).first
            if await cand.count() == 0:
                continue
            if await cand.is_visible(timeout=400):
                loc, sel = cand, s
                break
        except Exception:
            continue
    # Label-scoped fallback (TSYS / Markel when automation-id differs)
    if loc is None:
        try:
            lab = page.locator("label, div, span").filter(
                has_text=re.compile(r"country\s+phone\s+code", re.I)
            ).first
            if await lab.count():
                field = lab.locator(
                    "xpath=ancestor::*[contains(@data-automation-id,'formField')][1]"
                ).first
                if await field.count() == 0:
                    field = lab.locator("xpath=ancestor::div[1]")
                btn = field.locator(
                    'button[aria-haspopup="listbox"], [role="combobox"], button'
                ).first
                if await btn.count() and await btn.is_visible(timeout=400):
                    loc, sel = btn, "label:Country Phone Code"
        except Exception:
            pass
    if loc is None:
        # Last resort: any visible button showing Anguilla / wrong +1 territory
        try:
            btns = page.locator('button[aria-haspopup="listbox"]:visible')
            n = min(await btns.count(), 20)
            for i in range(n):
                b = btns.nth(i)
                try:
                    txt = ((await b.inner_text()) or "").strip().lower()
                except Exception:
                    continue
                if "anguilla" in txt or (
                    "(+1)" in txt and "united states" not in txt and "america" not in txt
                ):
                    loc, sel = b, "button:wrong_dial_territory"
                    break
        except Exception:
            pass
    if loc is None:
        detail["reason"] = "not_in_dom"
        return detail
    detail["selector"] = sel
    try:
        shown = ((await _read_field_value(loc)) or "").strip()
    except Exception:
        shown = ""
    detail["readback_before"] = shown[:120]
    # Already US (+1) — require country name; bare +1 alone is NOT enough (ATS-008/ATS2-002)
    if shown and _is_us_country_phone_readback(shown):
        detail.update(
            {
                "status": "filled",
                "verified": True,
                "reason": "already_correct_skip",
                "readback": shown[:120],
                "skipped_already_correct": True,
            }
        )
        return detail

    cands = [
        "United States of America (+1)",
        "United States (+1)",
        "United States of America",
        "United States",
        # Bare +1 last — only accepted when readback names United States
        "+1",
    ]
    try:
        await loc.scroll_into_view_if_needed()
        await loc.click(timeout=4000)
        await page.wait_for_timeout(300)
        # Prefer typing United States into filter if input exists
        filt = page.locator(
            '[data-automation-id="formField-countryPhoneCode"] input:not([type="hidden"]), '
            'input[data-automation-id="countryPhoneCode"]'
        ).first
        if await filt.count() and await filt.is_visible(timeout=400):
            from verified_select import _type_into_filter

            await _type_into_filter(filt, "United States", timeout_ms=3500)
            await page.wait_for_timeout(400)
        for cand in cands:
            ok, txt = await _click_matching_option(page, cand, device_type=False)
            if not ok or not txt:
                continue
            # Never treat NANP non-US option labels as a hit (ATS2-002)
            if any(t in (txt or "").lower() for t in _NANP_NON_US_TERRITORIES):
                continue
            await page.wait_for_timeout(250)
            readback = (await _read_field_value(loc) or txt or "").strip()
            detail["readback"] = readback[:120]
            detail["option_text"] = (txt or "")[:80]
            detail["option_clicked"] = True
            if _is_us_country_phone_readback(readback):
                detail["status"] = "filled"
                detail["verified"] = True
                detail["value"] = cand
                return detail
        detail["reason"] = "no_matching_option"
        await _escape_unless_captcha(page)
    except RecursionError as e:
        try:
            await _escape_unless_captcha(page)
        except Exception:
            pass
        if _recursion_retry:
            await page.wait_for_timeout(450)
            out = await _fill_country_phone_code(
                page, values, _recursion_retry=False
            )
            out["retried_after_recursion"] = True
            if not out.get("verified"):
                out["reason"] = out.get("reason") or "fill_error_after_recursion_retry"
                out["degraded"] = True
                out["error"] = out.get("error") or f"RecursionError:{e}"[:120]
            return out
        detail["reason"] = "fill_error"
        detail["error"] = f"RecursionError:{e}"[:120]
        detail["degraded"] = True
    except Exception as e:
        detail["reason"] = "fill_error"
        detail["error"] = str(e)[:160]
    return detail


async def _click_matching_option(
    page, value: str, *, device_type: bool = False, reject_dial: bool = False
) -> tuple[bool, str | None]:
    """Click a listbox option that actually matches value. Never first-option fallback."""
    candidates = _expand_state_value(value) or [value]
    want_device = device_type or any(
        (c or "").strip().lower() in _PHONE_DEVICE_VALUES for c in candidates
    )
    # State fills must never click dial-code rows (Anguilla (+1) polluted TSYS).
    block_dial = reject_dial or want_device or any(
        len((c or "").strip()) <= 2 or (c or "").strip().lower() in {
            n.lower() for n in _US_STATE_NAMES.values()
        } or (c or "").strip().upper() in _US_STATE_NAMES
        for c in candidates
    )
    for cand in candidates:
        patterns = [
            page.get_by_role("option", name=re.compile(rf"^{re.escape(cand)}$", re.I)),
            page.get_by_role("option", name=re.compile(re.escape(cand), re.I)),
            page.locator(f'[role="option"]:has-text({json.dumps(cand)})'),
        ]
        for opt in patterns:
            try:
                n = await opt.count()
                for i in range(min(n, 12)):
                    el = opt.nth(i)
                    if not await el.is_visible(timeout=500):
                        continue
                    txt = (await el.inner_text()).strip()
                    low = txt.lower()
                    if "submit" in low:
                        continue
                    # Never pick a country dial-code when filling phone device / state
                    if (want_device or block_dial) and _looks_like_dial_code_option(txt):
                        continue
                    # ATS2-012: prefer soft/word-boundary match over raw substring
                    matched = False
                    try:
                        from verified_select import soft_value_match

                        expand = _expand_state_value(cand) or [cand]
                        matched = any(soft_value_match(c, txt) for c in expand)
                    except Exception:
                        matched = False
                    if not matched:
                        # Fallback: exact / startswith only (no loose ``in``)
                        cl = cand.lower()
                        if low == cl or low.startswith(cl + " ") or low.startswith(cl + "-"):
                            matched = True
                        elif cl == low[: len(cl)] and len(cl) >= 3:
                            matched = True
                    if not matched:
                        continue
                    await el.click(timeout=3000)
                    return True, txt[:80]
            except Exception:
                continue
    return False, None


async def _fill_country_region_state(
    page, loc, sel: str, value: str, *, _recursion_retry: bool = True
) -> dict:
    """Fill Workday State/Province (countryRegion) — Illinois not Idaho.

    Never call Playwright ``.fill()`` on the State *button* / formField wrapper
    (hangs 30s). Prefer: open prompt → keyboard type full name → click
    ``promptOption``. Fiber searchSelect only when a real ``<input>`` exists.
    """
    from verified_select import (
        expand_state_value,
        fiber_search_select,
        nudge_listbox_after_type,
        reject_confusable_state_option,
        soft_value_match,
        value_matches_readback,
    )

    cands = expand_state_value(value) or [value]
    fill_value = cands[0]  # Illinois before IL
    result: dict = {
        "automation_id": "addressSection_countryRegion",
        "status": "missed",
        "mode": "combobox",
        "value": value,
        "selector": sel,
        "type": ADDRESS_STATE,
        "verified": False,
    }

    async def _real_filter_input():
        """Return a visible <input> under the State field, or None."""
        for cand_sel in (
            f'{sel} input:not([type="hidden"])',
            '[data-automation-id="formField-countryRegion"] input:not([type="hidden"])',
            'input[id*="countryRegion" i]',
            '[data-automation-id="addressSection_countryRegion"] input',
        ):
            try:
                inp = page.locator(cand_sel).first
                if await inp.count() == 0:
                    continue
                tag = (
                    await inp.evaluate("el => (el.tagName || '').toLowerCase()")
                ) or ""
                if tag != "input":
                    continue
                if not await inp.is_visible(timeout=300):
                    continue
                return inp
            except Exception:
                continue
        return None

    async def _verify_committed(picked: str = "") -> bool:
        readback = await _read_field_value(loc) or picked
        result["readback"] = (readback or "")[:120]
        if not readback:
            return False
        if reject_confusable_state_option(fill_value, readback):
            result["reason"] = "confusable_state_idaho"
            return False
        ok = value_matches_readback(
            fill_value, readback, mode="combobox"
        ) or soft_value_match(fill_value, readback)
        if ok:
            result.update(
                {
                    "status": "filled",
                    "verified": True,
                    "option_clicked": True,
                    "option_text": (picked or readback)[:80],
                }
            )
        return ok

    try:
        # Already correct?
        try:
            existing = await _read_field_value(loc)
        except Exception:
            existing = ""
        if (
            existing
            and soft_value_match(fill_value, existing)
            and not reject_confusable_state_option(fill_value, existing)
        ):
            result.update(
                {
                    "status": "filled",
                    "verified": True,
                    "readback": existing[:120],
                    "reason": "already_correct_skip",
                    "skipped_already_correct": True,
                }
            )
            return result

        # Open the State prompt
        try:
            await loc.scroll_into_view_if_needed()
        except Exception:
            pass
        try:
            await loc.click(timeout=2500, force=True)
            await page.wait_for_timeout(250)
        except Exception:
            pass

        filt = await _real_filter_input()

        # Path A: real input + fiber searchSelect
        if filt is not None:
            try:
                fiber = await fiber_search_select(
                    page,
                    filt,
                    fill_value,
                    aliases=cands,
                    wait_ms=1400,
                )
                picked = str(fiber.get("picked") or "")
                if (
                    fiber.get("option_clicked")
                    and picked
                    and not reject_confusable_state_option(fill_value, picked)
                ):
                    await page.wait_for_timeout(300)
                    if await _verify_committed(picked):
                        result["algorithm"] = "fiber_search_select"
                        result["fiber_status"] = fiber.get("status")
                        return result
            except Exception as e:
                result["fiber_error"] = str(e)[:80]

        # Path B: keyboard type (never .fill on button) + click promptOption
        try:
            await loc.click(timeout=2000, force=True)
            await page.wait_for_timeout(200)
        except Exception:
            pass
        filt = await _real_filter_input()
        typed_via = "keyboard"
        if filt is not None:
            try:
                await filt.click(timeout=1500, force=True)
                # Clear via keys — avoid long Playwright fill timeouts
                await page.keyboard.press("Meta+a")
                await page.keyboard.press("Backspace")
                await page.keyboard.type(fill_value, delay=28)
                typed_via = "input_keyboard"
            except Exception:
                await page.keyboard.type(fill_value, delay=28)
        else:
            await page.keyboard.type(fill_value, delay=28)

        await page.wait_for_timeout(500)
        try:
            await nudge_listbox_after_type(page, filt, allow_enter=True)
            await page.wait_for_timeout(400)
        except Exception:
            pass

        ok_click, opt = await _click_matching_option(
            page, fill_value, reject_dial=True
        )
        if ok_click and opt and not reject_confusable_state_option(fill_value, opt):
            await page.wait_for_timeout(300)
            if await _verify_committed(opt):
                result["algorithm"] = f"type_click_state:{typed_via}"
                return result
            result["reason"] = "readback_mismatch_after_click"
        else:
            # Last resort: click any promptOption that soft-matches Illinois
            try:
                opts = page.locator(
                    '[data-automation-id="promptOption"], [role="option"]'
                )
                n = min(await opts.count(), 20)
                for i in range(n):
                    el = opts.nth(i)
                    try:
                        txt = ((await el.inner_text()) or "").strip()
                    except Exception:
                        continue
                    if not txt or reject_confusable_state_option(fill_value, txt):
                        continue
                    if _looks_like_dial_code_option(txt):
                        continue
                    if soft_value_match(fill_value, txt) or fill_value.lower() in txt.lower():
                        await el.click(timeout=2500)
                        await page.wait_for_timeout(300)
                        if await _verify_committed(txt):
                            result["algorithm"] = "promptOption_scan"
                            return result
            except Exception as e:
                result["scan_error"] = str(e)[:80]
            result["reason"] = result.get("reason") or "no_matching_option"
            result["option_text"] = (opt or "")[:80] if opt else None

        # Escape any open listbox, then recover if UI already shows Illinois
        # (resume autofill / prior commit) — Quantiphi Gate V1: visual Illinois
        # with JSON miss when option-click failed but autofill value remained.
        try:
            await _escape_unless_captcha(page)
            await page.wait_for_timeout(200)
        except Exception:
            pass
        try:
            final_rb = (await _read_field_value(loc) or "").strip()
        except Exception:
            final_rb = ""
        if (
            final_rb
            and not reject_confusable_state_option(fill_value, final_rb)
            and (
                value_matches_readback(fill_value, final_rb, mode="combobox")
                or soft_value_match(fill_value, final_rb)
                or any(
                    soft_value_match(c, final_rb)
                    for c in cands
                )
            )
        ):
            result.update(
                {
                    "status": "filled",
                    "verified": True,
                    "readback": final_rb[:120],
                    "reason": result.get("reason") or "already_correct_skip",
                    "algorithm": result.get("algorithm") or "post_miss_reread",
                    "option_text": final_rb[:80],
                }
            )
            return result

        rb = str(result.get("readback") or final_rb or "")
        if rb and reject_confusable_state_option(fill_value, rb):
            result["verified"] = False
            result["status"] = "missed"
            result["reason"] = "confusable_state_idaho"
        return result
    except RecursionError as e:
        try:
            await _escape_unless_captcha(page)
        except Exception:
            pass
        if _recursion_retry:
            await page.wait_for_timeout(450)
            out = await _fill_country_region_state(
                page, loc, sel, value, _recursion_retry=False
            )
            out["retried_after_recursion"] = True
            if not out.get("verified"):
                out["reason"] = out.get("reason") or "fill_error_after_recursion_retry"
                out["degraded"] = True
                out["error"] = out.get("error") or f"RecursionError:{e}"[:120]
            return out
        result["reason"] = "fill_error"
        result["error"] = f"RecursionError:{e}"[:120]
        result["degraded"] = True
        return result
    except Exception as e:
        result["reason"] = "fill_error"
        result["error"] = str(e)[:200]
        return result


async def _fill_automation_id(page, automation_id: str, value: str, *, combobox: bool = False) -> dict:
    """Fill input/button tied to data-automation-id. Returns status dict.

    Comboboxes: click → type → click matching role=option. Never press Enter
    (Enter can submit the whole Workday step). Never click a non-matching option.
    After fill, read back the value; only status=filled when verified non-empty match.
    """
    loc, sel = await _resolve_contact_locator(page, automation_id)
    try:
        count = await loc.count()
        if count == 0:
            return {"automation_id": automation_id, "status": "missed", "reason": "not_in_dom"}
        try:
            visible = await loc.is_visible(timeout=1500)
        except Exception:
            visible = False
        if not visible:
            return {"automation_id": automation_id, "status": "missed", "reason": "not_visible"}

        tag = (await loc.evaluate("el => el.tagName")).lower()
        role = (await loc.get_attribute("role")) or ""
        fill_value = value
        if automation_id == "addressSection_countryRegion":
            fill_value = _expand_state_value(value)[0]
            return await _fill_country_region_state(page, loc, sel, value)
        if automation_id == "phone-device-type":
            return await _fill_phone_device_type(page, loc, sel, value or "Mobile")

        # SKIP thrash: already-correct text/combobox — do not clear/retype
        if not (combobox or role == "combobox" or tag == "button"):
            # Resolve nested input early for readback
            inner_pre = page.locator(
                f'{sel} input:not([type="hidden"]), '
                f'input[data-automation-id="{automation_id}"]'
            ).first
            target_pre = inner_pre if await inner_pre.count() else loc
            if tag == "div":
                nested_pre = loc.locator("input:not([type='hidden']), textarea").first
                if await nested_pre.count():
                    target_pre = nested_pre
            is_pw = (
                automation_id in ("password", "verifyPassword")
                or ((await target_pre.get_attribute("type") or "").lower() == "password")
            )
            try:
                existing = await target_pre.input_value() if is_pw else await _read_field_value(target_pre)
            except Exception:
                existing = ""
            if is_pw:
                # Password: non-empty inputValue is enough (never log cleartext).
                if fill_value and len(existing or "") > 0:
                    return {
                        "automation_id": automation_id,
                        "status": "filled",
                        "reason": "already_correct_skip",
                        "mode": "fill",
                        "value": "***",
                        "readback": "***",
                        "selector": sel,
                        "verified": True,
                        "skipped_already_correct": True,
                    }
            elif _value_matches_readback(fill_value, existing, mode="fill"):
                return {
                    "automation_id": automation_id,
                    "status": "filled",
                    "reason": "already_correct_skip",
                    "mode": "fill",
                    "value": value,
                    "readback": (existing or "")[:120],
                    "selector": sel,
                    "verified": True,
                    "skipped_already_correct": True,
                }
        else:
            # Combobox/button: skip reopen when shown value already matches
            try:
                existing_cb = await _read_field_value(loc)
            except Exception:
                existing_cb = ""
            if _value_matches_readback(fill_value, existing_cb, mode="combobox"):
                return {
                    "automation_id": automation_id,
                    "status": "filled",
                    "reason": "already_correct_skip",
                    "mode": "combobox",
                    "value": value,
                    "readback": (existing_cb or "")[:120],
                    "selector": sel,
                    "option_clicked": False,
                    "option_text": existing_cb[:80] if existing_cb else None,
                    "verified": True,
                    "skipped_already_correct": True,
                }
            # How-Heard / source: any committed chip → keep (stop alias thrash)
            if automation_id in ("how_heard", "source--source", "source"):
                try:
                    from verified_select import how_heard_source_committed

                    if how_heard_source_committed(existing_cb, [fill_value]):
                        try:
                            from verified_select import settle_open_listbox

                            await settle_open_listbox(page)
                        except Exception:
                            pass
                        return {
                            "automation_id": automation_id,
                            "status": "filled",
                            "reason": "already_correct_keep",
                            "mode": "combobox",
                            "value": value,
                            "readback": (existing_cb or "")[:120],
                            "selector": sel,
                            "option_clicked": False,
                            "option_text": (existing_cb or "")[:80] or None,
                            "verified": True,
                            "committed": True,
                            "skipped_already_correct": True,
                        }
                except Exception:
                    pass

        if combobox or role == "combobox" or tag == "button":
            from verified_select import fill_workday_combobox, settle_open_listbox

            typed = page.locator(
                f"{sel} input, "
                f'input[data-automation-id="{automation_id}"]'
            ).first
            filter_loc = typed if await typed.count() else loc
            cands = _expand_state_value(fill_value) or [fill_value]
            reject_opt = (
                (lambda t: _looks_like_dial_code_option(t))
                if automation_id == "phone-device-type"
                else None
            )
            detail = await fill_workday_combobox(
                page,
                loc,
                str(fill_value),
                aliases=cands,
                filter_input=filter_loc,
                read_committed=lambda: _read_field_value(loc),
                timeout_ms=7000 if automation_id in ("how_heard", "source--source") else 5000,
                label=automation_id,
                field_type=(
                    HOW_HEARD
                    if automation_id in ("how_heard", "source--source", "source")
                    else ""
                ),
                reject_option=reject_opt,
            )
            ok = bool(detail.get("ok") and detail.get("committed"))
            readback = str(detail.get("readback") or detail.get("picked") or "")
            if not readback:
                readback = await _read_field_value(loc)
            if not ok and readback:
                ok = _value_matches_readback(fill_value, readback, mode="combobox")
            if ok:
                try:
                    await settle_open_listbox(page)
                except Exception:
                    pass
            return {
                "automation_id": automation_id,
                "status": "filled" if ok else "missed",
                "reason": (
                    detail.get("reason")
                    if detail.get("skipped_already_correct")
                    else (None if ok else detail.get("error") or "readback_mismatch")
                ),
                "mode": "combobox",
                "value": value,
                "readback": readback[:120] if readback else "",
                "selector": sel,
                "option_clicked": bool(detail.get("option_clicked")),
                "option_text": (detail.get("picked") or "")[:80] or None,
                "verified": ok,
                "algorithm": detail.get("algorithm"),
                "steps": detail.get("steps"),
                "skipped_already_correct": bool(detail.get("skipped_already_correct")),
                "committed": ok,
            }

        # If locator is a wrapper div (formField-*), drill into nested input
        inner = page.locator(
            f'{sel} input:not([type="hidden"]), '
            f'input[data-automation-id="{automation_id}"]'
        ).first
        target = inner if await inner.count() else loc
        if tag == "div":
            nested = loc.locator("input:not([type='hidden']), textarea").first
            if await nested.count():
                target = nested

        await target.scroll_into_view_if_needed()
        try:
            await target.click(timeout=4000)
        except Exception:
            try:
                await target.click(timeout=4000, force=True)
            except Exception:
                pass
        try:
            await target.fill(str(value), timeout=4000)
        except Exception:
            # React-controlled: triple-click + type
            await target.click(timeout=4000, force=True, click_count=3)
            await page.keyboard.type(str(value), delay=20)
        await page.wait_for_timeout(200)
        # blur to commit
        try:
            await page.keyboard.press("Tab")
        except Exception:
            pass
        # Password fields: browsers may mask equality readback — accept non-empty
        # inputValue length instead of text equality for password/verifyPassword.
        is_password_field = (
            automation_id in ("password", "verifyPassword")
            or ((await target.get_attribute("type") or "").lower() == "password")
        )
        if is_password_field:
            try:
                readback = await target.input_value()
            except Exception:
                readback = await _read_field_value(target)
            ok = bool(value) and len(readback or "") > 0
            # Prefer equality when readable; still treat non-empty as filled
            if ok and readback and not _value_matches_readback(value, readback, mode="fill"):
                # Some browsers return bullets / empty string for type=password —
                # length>0 after fill is enough.
                if len(readback) == 0:
                    ok = False
            return {
                "automation_id": automation_id,
                "status": "filled" if ok else "missed",
                "reason": None if ok else "password_empty_after_fill",
                "mode": "fill",
                "value": "***" if value else "",
                "readback": "***" if readback else "",
                "selector": sel,
                "verified": ok,
            }
        readback = await _read_field_value(target)
        mode = "phone" if "phone" in automation_id else "fill"
        ok = _value_matches_readback(value, readback, mode=mode)
        return {
            "automation_id": automation_id,
            "status": "filled" if ok else "missed",
            "reason": None if ok else "readback_empty_or_mismatch",
            "mode": "fill",
            "value": value,
            "readback": readback[:120] if readback else "",
            "selector": sel,
            "verified": ok,
        }
    except Exception as e:
        return {
            "automation_id": automation_id,
            "status": "missed",
            "reason": "fill_error",
            "error": str(e)[:200],
            "selector": sel,
            "verified": False,
        }


async def _fill_radio_yes_no(page, automation_id: str, value: str) -> dict:
    """Pick Yes/No radio near a worked-here / previous-employee question."""
    want = (value or "No").strip().lower()
    if want in ("true", "yes", "y"):
        labels = ["Yes"]
        bool_vals = ("true", "yes", "y", "1")
    else:
        labels = ["No"]
        bool_vals = ("false", "no", "n", "0")
    # Scope to previous-worker / cisco-before / BBH / Quantiphi containers
    scopes = WD_CONTACT_SELECTORS.get(automation_id, []) + [
        'div:has-text("previously been employed")',
        'div:has-text("previously worked")',
        'div:has-text("worked for Cisco")',
        'div:has-text("Cisco before")',
        'div:has-text("worked at BBH")',
        'div:has-text("Have you been employed")',
        'div:has-text("employed by Quantiphi")',
        'fieldset:has-text("Have you been employed")',
        'fieldset:has-text("employed previously")',
        'fieldset',
    ]

    async def _verify_radio_checked(el) -> bool:
        try:
            tag = (await el.evaluate("e => (e.tagName || '').toLowerCase()")).lower()
        except Exception:
            tag = ""
        if tag == "input":
            try:
                return bool(await el.is_checked())
            except Exception:
                return False
        # Label / text click — find associated radio in ancestor
        try:
            checked = await el.evaluate(
                """(node) => {
                  const root = node.closest('fieldset, [role="radiogroup"], [data-automation-id*="formField"], div')
                    || node.parentElement;
                  if (!root) return false;
                  const radios = [...root.querySelectorAll('input[type="radio"]')];
                  return radios.some(r => r.checked);
                }"""
            )
            return bool(checked)
        except Exception:
            return False

    # Direct name=candidateIsPreviousWorker (BBH / Quantiphi)
    try:
        for bv in bool_vals:
            loc = page.locator(
                f'input[name="candidateIsPreviousWorker"][value="{bv}"]'
            ).first
            if await loc.count() == 0:
                continue
            try:
                await loc.scroll_into_view_if_needed()
            except Exception:
                pass
            try:
                await loc.check(timeout=2000, force=True)
            except Exception:
                try:
                    await loc.click(timeout=2000, force=True)
                except Exception:
                    continue
            await page.wait_for_timeout(200)
            if await _verify_radio_checked(loc):
                return {
                    "automation_id": automation_id,
                    "status": "filled",
                    "mode": "radio_value",
                    "value": labels[0],
                    "selector": f"candidateIsPreviousWorker[value={bv}]",
                    "verified": True,
                    "readback": labels[0],
                }
    except Exception:
        pass
    for scope_sel in scopes:
        try:
            scope = page.locator(scope_sel).first
            if await scope.count() == 0:
                continue
            if not await scope.is_visible(timeout=400):
                continue
            for lab in labels:
                for cand in (
                    scope.get_by_role("radio", name=re.compile(rf"^{lab}$", re.I)),
                    scope.locator(f'label:has-text("{lab}")'),
                    scope.locator(f'input[type="radio"][value="{lab}"]'),
                    scope.locator(
                        f'input[type="radio"][value="{"false" if lab == "No" else "true"}"]'
                    ),
                    scope.get_by_text(lab, exact=True),
                ):
                    try:
                        el = cand.first
                        if await el.count() == 0:
                            continue
                        await el.scroll_into_view_if_needed()
                        try:
                            await el.check(timeout=2000, force=True)
                        except Exception:
                            await el.click(timeout=3000, force=True)
                        await page.wait_for_timeout(300)
                        verified = await _verify_radio_checked(el)
                        if not verified:
                            # Re-probe any checked radio in scope
                            try:
                                verified = await scope.evaluate(
                                    """(root) => [...root.querySelectorAll('input[type="radio"]')]
                                      .some(r => r.checked)"""
                                )
                            except Exception:
                                verified = False
                        if verified:
                            return {
                                "automation_id": automation_id,
                                "status": "filled",
                                "mode": "radio",
                                "value": lab,
                                "selector": scope_sel,
                                "verified": True,
                                "readback": lab,
                            }
                    except Exception:
                        continue
        except Exception:
            continue
    # Global fallback: first visible Yes/No near "previously" / "employed by … previously"
    try:
        block = page.locator(
            r'text=/previously (been )?employed|employed by .+ previously|'
            r'have you been employed|previously worked|worked .+ before|worked at \w+/i'
        ).first
        if await block.count():
            root = block.locator(
                "xpath=ancestor::*[self::fieldset or self::div][1]"
            )
            lab = labels[0]
            el = root.get_by_text(lab, exact=True).first
            if await el.count():
                await el.click(timeout=3000)
                await page.wait_for_timeout(250)
                verified = await _verify_radio_checked(el)
                if not verified:
                    try:
                        verified = await root.evaluate(
                            """(r) => [...r.querySelectorAll('input[type="radio"]')]
                              .some(x => x.checked)"""
                        )
                    except Exception:
                        verified = False
                if verified:
                    return {
                        "automation_id": automation_id,
                        "status": "filled",
                        "mode": "radio",
                        "value": lab,
                        "verified": True,
                        "readback": lab,
                    }
    except Exception:
        pass
    return {
        "automation_id": automation_id,
        "status": "missed",
        "reason": "radio_not_found",
        "value": value,
        "verified": False,
    }


async def _validation_banner_present(page) -> dict | None:
    """Detect Workday 'Errors Found' / required-field validation after ADVANCE."""
    body = (await _body_text(page, 8000)).lower()
    hits = [n for n in VALIDATION_BANNER_NEEDLES if n in body]
    banner_text = ""
    for sel in (
        '[data-automation-id="errorMessage"]',
        '[data-automation-id="formErrorMessage"]',
        '[data-automation-id="errorBanner"]',
        'text=Errors Found',
        '[role="alert"]',
    ):
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible(timeout=400):
                banner_text = (await loc.inner_text())[:400]
                break
        except Exception:
            continue
    if not hits and not banner_text:
        return None
    return {
        "present": True,
        "needles": hits,
        "banner": banner_text or "errors inferred from page text",
        "snippet": body[:500],
    }


# Shared page-complete probe (also imported by fast_fill advance helpers).
# Cisco/Workday: ``currentlyWorkHere`` may be DOM-checked while React still
# requires To — never treat Present as exempting end dates.
REQUIRED_EMPTY_JS = """() => {
  const out = [];
  const isVisible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0
      && window.getComputedStyle(el).visibility !== 'hidden';
  };
  const ignoreId = (id) => {
    const s = String(id || '').toLowerCase();
    return (
      s.includes('countryphonecode')
      || s.includes('phone-country')
      || s.includes('search')
      || s.includes('autocomplete')
    );
  };
  const isDatePlaceholder = (s) => {
    const t = String(s || '').trim().toUpperCase();
    return !t || t === 'MM' || t === 'M' || t === 'YYYY'
      || t === 'YY' || t === 'DD' || t === 'D'
      || t === 'MONTH' || t === 'YEAR';
  };
  const fieldLabel = (el) => {
    try {
      const wrap = el.closest('[data-automation-id*="formField"], fieldset, [role="group"], label')
        || el.parentElement;
      const raw = (wrap && (wrap.innerText || wrap.textContent) || '').replace(/\\s+/g, ' ').trim();
      // Drop trailing "Select One" / option noise; keep question text
      return raw.replace(/\\bSelect One\\b/ig, '').trim().slice(0, 160);
    } catch (e) {
      return '';
    }
  };
  const push = (el, reason) => {
    const id = el.getAttribute('data-automation-id')
      || el.getAttribute('name')
      || el.id
      || el.tagName;
    if (ignoreId(id)) return;
    const lab = fieldLabel(el);
    const row = {id: String(id).slice(0, 80), reason};
    if (lab) row.label = lab;
    out.push(row);
  };
  const inEndDateField = (el) => !!(
    el.closest('[data-automation-id="formField-endDate"]')
    || el.closest('[data-automation-id*="endDate"]')
  );
  const inStartDateField = (el) => !!(
    el.closest('[data-automation-id="formField-startDate"]')
    || el.closest('[data-automation-id*="startDate"]')
  );
  // Present checked is a FAIL-before-ADVANCE signal on Cisco: React still
  // requires To, and the gate used to skip To → dishonest ADVANCE.
  document.querySelectorAll(
    'input[name="currentlyWorkHere"], '
    + 'input[type=checkbox][data-automation-id*="currentlyWork" i]'
  ).forEach((cur) => {
    if (!isVisible(cur)) return;
    if (cur.checked || cur.getAttribute('aria-checked') === 'true') {
      push(cur, 'currently_work_here_checked');
    }
  });
  document.querySelectorAll(
    'input[aria-required="true"], input[required], textarea[aria-required="true"]'
  ).forEach((el) => {
    if (!isVisible(el)) return;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') return;
    if (el.type === 'hidden' || el.type === 'checkbox' || el.type === 'radio') return;
    if (el.getAttribute('aria-hidden') === 'true') return;
    if (ignoreId(el.name) || ignoreId(el.id) || ignoreId(el.getAttribute('data-automation-id'))) return;
    const wrap = el.closest('[data-automation-id*="formField"], [data-automation-id*="phone"]');
    if (wrap && wrap.querySelector('[data-automation-id="deleteSelected"], [aria-label*="remove" i], [data-automation-id*="selectedItem"]')) {
      return;
    }
    const v = (el.value || '').trim();
    const aid = el.getAttribute('data-automation-id') || '';
    if (aid.includes('dateSection')) {
      // NEVER skip end/To when Present is checked — React often still requires To.
      const root = el.closest('[data-automation-id*="formField"]')
        || el.closest('[data-automation-id*="date"]')
        || el.parentElement;
      const disp = root && root.querySelector(
        '[data-automation-id$="-display"], [data-automation-id*="display"]'
      );
      const dt = disp ? (disp.innerText || '').trim() : '';
      // Input digits win — display can lag; only flag when VALUE is empty/placeholder
      if (isDatePlaceholder(v)) {
        push(el, 'empty_required_date_spin');
        return;
      }
      // If value is set, ignore sticky display placeholder
      if (isDatePlaceholder(dt) && !/\\d/.test(v)) {
        push(el, 'empty_required_date_spin');
        return;
      }
    }
    if (!v) push(el, 'empty_required_input');
  });
  // From*/To displays: only flag when paired INPUT also lacks digits
  document.querySelectorAll(
    '[data-automation-id="dateSectionMonth-display"], '
    + '[data-automation-id="dateSectionYear-display"]'
  ).forEach((disp) => {
    if (!isVisible(disp)) return;
    if (!isDatePlaceholder(disp.innerText || '')) return;
    const aid = disp.getAttribute('data-automation-id') || '';
    const inputAid = aid.replace('-display', '-input');
    const field = disp.closest('[data-automation-id*="formField"]')
      || disp.closest('[data-automation-id*="Date"]')
      || disp.parentElement
      || disp;
    const paired = field.querySelector(`[data-automation-id="${inputAid}"]`)
      || disp.parentElement?.querySelector(`[data-automation-id="${inputAid}"]`);
    const iv = paired ? (paired.value || '').trim() : '';
    if (iv && !isDatePlaceholder(iv) && /\\d/.test(iv)) return;
    if (paired && (paired.disabled || paired.getAttribute('aria-disabled') === 'true')) return;
    push(disp, 'empty_required_date_display');
  });
  // Each visible start/end date formField must have BOTH month+year non-placeholder
  document.querySelectorAll(
    '[data-automation-id="formField-startDate"], '
    + '[data-automation-id="formField-endDate"], '
    + '[data-automation-id*="formField-startDate"], '
    + '[data-automation-id*="formField-endDate"]'
  ).forEach((field) => {
    if (!isVisible(field)) return;
    const monthIns = field.querySelectorAll(
      'input[data-automation-id="dateSectionMonth-input"]'
    );
    const yearIns = field.querySelectorAll(
      'input[data-automation-id="dateSectionYear-input"]'
    );
    if (!monthIns.length && !yearIns.length) return;
    const monthOk = Array.from(monthIns).some((el) => {
      const t = (el.value || '').trim();
      return t && !isDatePlaceholder(t) && /\\d/.test(t);
    });
    const yearOk = Array.from(yearIns).some((el) => {
      const t = (el.value || '').trim();
      return t && !isDatePlaceholder(t) && /\\d/.test(t);
    });
    if (!monthOk || !yearOk) {
      push(field, 'empty_required_date_field');
    }
  });
  document.querySelectorAll(
    'button[aria-required="true"], [role="combobox"][aria-required="true"]'
  ).forEach((el) => {
    if (!isVisible(el)) return;
    const t = (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase();
    if (!t || t === 'select one' || t === 'select' || t.startsWith('select ')) {
      const wrap = el.closest('[data-automation-id*="formField"], fieldset, div');
      const lab = wrap ? (wrap.innerText || '').slice(0, 200) : '';
      if (lab.includes('*') || el.getAttribute('aria-required') === 'true') {
        push(el, 'empty_required_combobox');
      }
    }
  });
  document.querySelectorAll('button[aria-haspopup="listbox"]').forEach((el) => {
    if (!isVisible(el)) return;
    const t = (el.innerText || '').trim().toLowerCase();
    if (t !== 'select one' && t !== 'select') return;
    const wrap = el.closest('[data-automation-id*="formField"]') || el.parentElement;
    const lab = wrap ? (wrap.innerText || '').slice(0, 120) : '';
    if (lab.includes('*')) push(el, 'empty_required_combobox');
  });
  // Workday multi-select (how heard): filter typed but no committed chip
  document.querySelectorAll(
    '[data-automation-id="formField-source"], [data-automation-id*="formField-source"]'
  ).forEach((field) => {
    if (!isVisible(field)) return;
    const t = (field.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    if (!t.includes('0 items selected')) return;
    const inp = field.querySelector(
      'input[name="source--source"], [data-automation-id="source--source"]'
    );
    push(inp || field, 'empty_required_multiselect');
  });
  const seen = new Set();
  return out.filter((x) => {
    const k = x.id + '|' + x.reason;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  }).slice(0, 30);
}"""


async def _required_empty_on_page(page) -> list[dict]:
    """Heuristic: visible required inputs/comboboxes still empty / Select One.

    Ignores Workday combobox *search* inputs (e.g. countryPhoneCode) whose
    display chip already shows a selected value — those look aria-required
    but are not user-facing empties.

    Date spins: Playwright fill() can set input.value while the visible
    ``*-display`` still shows MM/YYYY placeholders and React state is empty.
    Treat placeholder displays under From* and To* as empty.

    Never skip To/end when ``currentlyWorkHere`` is checked (Cisco React still
    requires To). A checked Present box is itself a page-incomplete signal.
    """
    try:
        return await page.evaluate(REQUIRED_EMPTY_JS)
    except Exception:
        return []


async def _email_field_present(page) -> bool:
    return await page.locator('[data-automation-id="email"]').count() > 0


async def _password_only_signin(page) -> bool:
    """Sign-in form: email+password, no verifyPassword."""
    email = await page.locator('[data-automation-id="email"]').count()
    pw = await page.locator('[data-automation-id="password"]').count()
    verify = await page.locator('[data-automation-id="verifyPassword"]').count()
    return email > 0 and pw > 0 and verify == 0


async def _create_account_form(page) -> bool:
    verify = await page.locator('[data-automation-id="verifyPassword"]').count()
    create_btn = await page.locator(
        '[data-automation-id="createAccountSubmitButton"], '
        'button:has-text("Create Account")'
    ).count()
    return verify > 0 or create_btn > 0


async def _contact_phase_present(page) -> bool:
    """True only when contact fields exist — NOT progress-bar step labels.

    Covers classic ``contactInformationPage`` / ``legalNameSection_*``, the
    newer apply-flow ``applyFlowMyInfoPage`` + ``name=legalName--firstName``,
    and Thales/wd3 source / phone landmarks so we do not burn a 10s wait after
    SPA mount when the classic container id is absent.
    """
    probes = [
        '[data-automation-id="contactInformationPage"]',
        '[data-automation-id="applyFlowMyInfoPage"]',
        '[data-automation-id="legalNameSection_firstName"]',
        'input[name="legalName--firstName"]',
        '#name--legalName--firstName',
        '[data-automation-id="formField-legalName--firstName"]',
        # Thales / wd3 My Information (fields mount before page container id)
        '[data-automation-id="source--source"]',
        'input[name="source--source"]',
        '[data-automation-id="formField-source"]',
        '[data-automation-id="phone-number"]',
        'input[name="phoneNumber"]',
        '[data-automation-id="formField-phoneNumber"]',
        'input[data-automation-id="legalNameSection_lastName"]',
        'input[name="legalName--lastName"]',
    ]
    for sel in probes:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            # Prefer visible, but count>0 is enough for SPA mid-paint
            return True
        except Exception:
            continue
    return False


async def _wait_contact_phase(page, timeout_ms: int = 8000, *, poll_ms: int = 150) -> bool:
    """Wait for Phase B contact fields after auth / Next.

    Short poll interval + early exit when any contact landmark mounts.
    Caps default at 8s (was 20s @ 1s ticks ≈ long stall after SPA paint).
    """
    deadline = time.time() + max(200, timeout_ms) / 1000.0
    interval = max(60, int(poll_ms))
    while time.time() < deadline:
        if await _contact_phase_present(page):
            return True
        body = await _body_text(page, 2000)
        if _detect_hard_blocker(body, await page.title(), page.url):
            return False
        await page.wait_for_timeout(interval)
    return await _contact_phase_present(page)


async def _read_create_account_checkbox_state(page) -> tuple[bool, str]:
    """Return (checked, readback) for the create-account terms checkbox."""
    selectors = (
        '[data-automation-id="createAccountCheckbox"]',
        'input[data-automation-id="createAccountCheckbox"]',
        '[data-automation-id="createAccountCheckbox"] input',
        'div[data-automation-id="createAccountCheckbox"]',
    )
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.count() == 0:
                continue
            checked = await loc.get_attribute("aria-checked")
            if checked == "true":
                return True, "true"
            nested = loc.locator('input[type="checkbox"], [role="checkbox"]').first
            if await nested.count():
                nc = await nested.get_attribute("aria-checked")
                if nc == "true":
                    return True, "true"
                try:
                    if await nested.is_checked():
                        return True, "true"
                except Exception:
                    pass
            try:
                if await loc.is_checked():
                    return True, "true"
            except Exception:
                pass
        except Exception:
            continue
    return False, ""


def _create_account_checkbox_result(
    *,
    mode: str,
    selector: str = "",
    verified: bool = True,
    reason: str | None = None,
) -> dict:
    """Normalized createAccountCheckbox fill row (honest readback for metrics)."""
    row: dict = {
        "automation_id": "createAccountCheckbox",
        "status": "filled" if verified else "missed",
        "mode": mode,
        "selector": selector or '[data-automation-id="createAccountCheckbox"]',
        "readback": "true" if verified else "",
        "verified": verified,
    }
    if reason:
        row["reason"] = reason
    return row


async def _check_create_account_terms(page) -> dict:
    """Tick Workday create-account terms checkbox (required to enable submit).

    Do NOT click "Privacy Statement" / "Terms and Conditions" link text — that
    opens a modal and leaves the Create Account button disabled (observed on Cisco).
    """
    last_err = None
    selectors = [
        '[data-automation-id="createAccountCheckbox"]',
        'input[data-automation-id="createAccountCheckbox"]',
        '[data-automation-id="createAccountCheckbox"] input',
        'input[type="checkbox"][id*="createAccount"]',
        'input[type="checkbox"][name*="agree"]',
        'div[data-automation-id="createAccountCheckbox"]',
        'label:has(input[type="checkbox"])',
        '[data-automation-id="createAccountForm"] [role="checkbox"]',
        'form [role="checkbox"]',
    ]
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.count() == 0:
                continue
            if not await loc.is_visible(timeout=600):
                continue
            role = (await loc.get_attribute("role")) or ""
            already, rb = await _read_create_account_checkbox_state(page)
            if already:
                return _create_account_checkbox_result(
                    mode="already_checked", selector=sel, verified=True
                )
            # Nested checkbox inside label/div
            nested = loc.locator('input[type="checkbox"], [role="checkbox"]').first
            target = nested if await nested.count() else loc
            input_type = (await target.get_attribute("type")) or ""
            ttag = (await target.evaluate("el => el.tagName")).lower()
            if ttag == "input" or input_type == "checkbox":
                await target.check(timeout=3000, force=True)
            else:
                await target.click(timeout=3000, force=True)
            await page.wait_for_timeout(500)
            checked, rb = await _read_create_account_checkbox_state(page)
            return _create_account_checkbox_result(
                mode="check",
                selector=sel,
                verified=checked,
                reason=None if checked else "checkbox_unverified",
            )
        except Exception as e:
            last_err = str(e)[:120]
            continue
    # Safe label phrases only — never Privacy/Terms *links*
    for text in (
        "I agree",
        "I have read and agree",
        "I accept",
        "Agree to",
    ):
        loc = page.get_by_text(text, exact=False).first
        try:
            if await loc.count() and await loc.is_visible(timeout=500):
                # Prefer clicking associated checkbox, not a nested <a>
                box = loc.locator("xpath=ancestor::label[1]//input[@type='checkbox'] | ancestor::*[contains(@data-automation-id,'heckbox')][1]").first
                if await box.count():
                    await box.click(timeout=3000, force=True)
                else:
                    await loc.click(timeout=3000, force=True)
                await page.wait_for_timeout(500)
                checked, _rb = await _read_create_account_checkbox_state(page)
                return _create_account_checkbox_result(
                    mode="label_click",
                    selector=f"text={text}",
                    verified=checked,
                    reason=None if checked else "checkbox_unverified",
                )
        except Exception:
            continue
    return {
        "automation_id": "createAccountCheckbox",
        "status": "missed",
        "reason": "checkbox_not_found",
        "error": last_err,
        "verified": False,
    }


async def _click_create_account_enabled(page) -> list[dict]:
    """Click Create Account when enabled; report disabled state instead of hanging."""
    results: list[dict] = []
    for sel in CREATE_ACCOUNT_SELECTORS:
        loc = page.locator(sel).first
        try:
            if await loc.count() == 0:
                continue
            try:
                await loc.wait_for(state="visible", timeout=4000)
            except Exception:
                if not await loc.is_visible():
                    continue
            resolved = await gate_locator_click(
                loc, intent_label="", allow_kinds=NAV_KINDS
            )
            text = (resolved.get("actual") or "").strip() or "Create Account"
            if not resolved.get("ok"):
                results.append({
                    "selector": sel,
                    "text": text,
                    "action": "refused",
                    "reason": resolved.get("reason"),
                    "kind": resolved.get("kind"),
                })
                continue
            disabled = await loc.is_disabled()
            aria_dis = await loc.get_attribute("aria-disabled")
            cls = (await loc.get_attribute("class")) or ""
            if disabled or aria_dis == "true" or "disabled" in cls.lower():
                results.append({
                    "selector": sel,
                    "text": text,
                    "action": "blocked",
                    "reason": "create_account_button_disabled",
                    "kind": resolved.get("kind"),
                })
                continue
            await loc.scroll_into_view_if_needed()
            try:
                await loc.click(timeout=5000)
            except Exception:
                # Button may still be non-actionable if terms unchecked; force once
                # only when aria-disabled is clearly false (never FINAL path).
                await loc.click(timeout=3000, force=True)
            async def _left_create_auth(p):
                if await _contact_phase_present(p):
                    return True
                if await _password_only_signin(p):
                    return True
                # Still on create form → keep polling; leave when form gone
                return not await _create_account_form(p)

            await _poll_spa_settle(
                page,
                timeout_ms=2800,
                poll_ms=250,
                predicates=[_left_create_auth],
            )
            results.append({
                "selector": sel,
                "text": text,
                "action": "clicked",
                "kind": resolved.get("kind"),
            })
            return results
        except Exception as e:
            results.append({"selector": sel, "action": "error", "error": str(e)[:160]})
    return results


async def _upsert_web_keys_after_auth(page, values: dict, report: dict | None = None) -> None:
    """Persist site password after successful create (never log secrets)."""
    try:
        from urllib.parse import urlparse as _urlparse

        from web_keys import company_from_host, upsert

        host = (_urlparse(getattr(page, "url", "") or "").hostname or "").strip().lower()
        if not host:
            return
        pw = str(values.get(PASSWORD) or values.get(PASSWORD_CONFIRM) or "").strip()
        email = str(values.get(EMAIL) or "").strip()
        if not pw:
            return
        upsert(
            host,
            company=company_from_host(host),
            email=email,
            password=pw,
            job_id=(report or {}).get("job_id"),
            source="fastfill",
        )
        if isinstance(report, dict):
            report["web_keys_upserted"] = True
    except Exception as e:
        if isinstance(report, dict):
            report.setdefault("errors", []).append({"web_keys_upsert": str(e)[:120]})


async def _try_create_account(page, values: dict, *, click_submit: bool = True) -> dict:
    """Phase A: fill create-account with DUMMY_PROFILE; gated Create Account click."""
    detail: dict = {
        "attempted": False,
        "filled": [],
        "missed": [],
        "clicks": [],
        "path": "create_account",
    }
    email_loc = page.locator('[data-automation-id="email"]').first
    if await email_loc.count() == 0:
        return detail
    if not await _create_account_form(page):
        detail["skipped"] = "not_create_account_form"
        return detail

    detail["attempted"] = True
    for aid, key in (
        ("email", EMAIL),
        ("password", PASSWORD),
        ("verifyPassword", PASSWORD_CONFIRM),
    ):
        val = values.get(key) or ""
        r = await _fill_automation_id(page, aid, str(val))
        bucket = "filled" if r["status"] == "filled" else "missed"
        detail[bucket].append(r)

    # Auth-complete gate: never click Create Account if email/password incomplete.
    pw = str(values.get(PASSWORD) or "").strip()
    pw2 = str(values.get(PASSWORD_CONFIRM) or pw).strip()
    auth_fields_missed = [
        r for r in detail["missed"]
        if r.get("automation_id") in ("email", "password", "verifyPassword")
    ]
    if auth_fields_missed or not pw or not pw2:
        detail["auth_incomplete"] = True
        detail["clicks"] = [{
            "action": "skipped",
            "reason": "auth_incomplete",
            "missed": [r.get("automation_id") for r in auth_fields_missed],
        }]
        return detail

    try:
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(300)
    except Exception:
        pass

    cb = await _check_create_account_terms(page)
    bucket = "filled" if cb.get("status") == "filled" else "missed"
    detail[bucket].append(cb)

    # Give Workday time to enable Create Account after terms check
    await page.wait_for_timeout(800)
    try:
        await page.locator(
            '[data-automation-id="createAccountSubmitButton"], button:has-text("Create Account")'
        ).first.wait_for(state="visible", timeout=3000)
    except Exception:
        pass

    if click_submit:
        detail["clicks"] = await _click_create_account_enabled(page)
        await page.wait_for_timeout(1500)
    else:
        detail["clicks"] = [{
            "action": "skipped",
            "reason": "create-account submit disabled",
        }]
    return detail


async def _try_sign_in(page, values: dict, *, click_submit: bool = True) -> dict:
    """Phase A fallback: Sign In with same dummy email/password."""
    detail: dict = {
        "attempted": False,
        "filled": [],
        "missed": [],
        "clicks": [],
        "path": "sign_in",
    }
    if not await _email_field_present(page):
        return detail

    detail["attempted"] = True
    for aid, key in (("email", EMAIL), ("password", PASSWORD)):
        val = values.get(key) or ""
        r = await _fill_automation_id(page, aid, str(val))
        bucket = "filled" if r["status"] == "filled" else "missed"
        detail[bucket].append(r)

    auth_fields_missed = [
        r for r in detail["missed"]
        if r.get("automation_id") in ("email", "password")
    ]
    pw = str(values.get(PASSWORD) or "").strip()
    if auth_fields_missed or not pw:
        detail["auth_incomplete"] = True
        detail["clicks"] = [{
            "action": "skipped",
            "reason": "auth_incomplete",
            "missed": [r.get("automation_id") for r in auth_fields_missed],
        }]
        return detail

    if click_submit:
        # Prefer explicit submit button; avoid header "Sign In" text matches
        clicks: list[dict] = []
        for sel in (
            '[data-automation-id="signInSubmitButton"]',
            "button[type='submit']:has-text('Sign In')",
            "button:has-text('Sign In')",
        ):
            loc = page.locator(sel).first
            try:
                if await loc.count() == 0:
                    continue
                if not await loc.is_visible(timeout=2000):
                    continue
                resolved = await gate_locator_click(
                    loc, intent_label="Sign In", allow_kinds=NAV_KINDS
                )
                text = (resolved.get("actual") or "").strip() or "Sign In"
                if not resolved.get("ok"):
                    clicks.append({
                        "selector": sel, "text": text,
                        "action": "refused", "reason": resolved.get("reason"),
                        "kind": resolved.get("kind"),
                    })
                    continue
                disabled = await loc.is_disabled()
                aria_dis = await loc.get_attribute("aria-disabled")
                cls = (await loc.get_attribute("class")) or ""
                if disabled or aria_dis == "true" or "disabled" in cls.lower():
                    clicks.append({
                        "selector": sel, "text": text,
                        "action": "blocked", "reason": "sign_in_button_disabled",
                    })
                    continue
                await loc.scroll_into_view_if_needed()
                try:
                    await loc.click(timeout=5000)
                except Exception:
                    await loc.click(timeout=3000, force=True)
                async def _left_signin(p):
                    if await _contact_phase_present(p):
                        return True
                    return not await _password_only_signin(p)

                await _poll_spa_settle(
                    page,
                    timeout_ms=3000,
                    poll_ms=250,
                    predicates=[_left_signin, _contact_phase_present],
                )
                clicks.append({
                    "selector": sel, "text": text,
                    "action": "clicked", "kind": resolved.get("kind"),
                })
                break
            except Exception as e:
                clicks.append({"selector": sel, "action": "error", "error": str(e)[:160]})
        detail["clicks"] = clicks
    return detail


async def _switch_to_sign_in(page) -> list[dict]:
    """Click Sign In link when create-account shows already-registered."""
    return await _click_gated(page, SIGN_IN_LINK_SELECTORS, stop_on_match="sign in")


async def _phase_a_auth(page, values: dict, report: dict) -> dict:
    """Auth wall: create account, or sign in if already registered."""
    phase: dict = {
        "name": "A_auth",
        "create_account": None,
        "sign_in": None,
        "already_registered": False,
        "account_created": False,
        "signed_in": False,
    }

    # Ensure PASSWORD / PASSWORD_CONFIRM via web_keys before any auth fill.
    prefer_stored_signin = False
    stored = None
    try:
        from urllib.parse import urlparse as _urlparse

        from web_keys import company_from_host, ensure_password_for_company, lookup

        host = (_urlparse(page.url or "").hostname or "").strip().lower() or None
        company = company_from_host(host)
        stored = lookup(host) if host else None
        if (
            stored
            and (stored.get("email") or "").strip()
            and (stored.get("password") or "").strip()
        ):
            prefer_stored_signin = True
            phase["prefer_stored_signin"] = True
            # Overlay stored credentials for host reuse (prefer Sign In).
            se = (stored.get("email") or "").strip()
            values[EMAIL] = se
            ensure_password_for_company(
                stored.get("company") or company,
                values,
                host=host,
                email=se,
            )
        elif stored and (stored.get("password") or "").strip():
            ensure_password_for_company(
                stored.get("company") or company,
                values,
                host=host,
                email=str(values.get(EMAIL) or ""),
            )
            if (stored.get("email") or "").strip() and not (values.get(EMAIL) or "").strip():
                values[EMAIL] = stored["email"]
        else:
            ensure_password_for_company(
                company, values, host=host, email=str(values.get(EMAIL) or "")
            )
    except Exception:
        prefer_stored_signin = False

    body = await _body_text(page)
    hard = await _hard_blocker_live(page)
    if hard:
        report["blocker"] = hard
        report["blocker_detail"] = body[:500]
        phase["stopped"] = hard
        return phase

    async def _create_account_flow() -> None:
        """Fill + click Create Account, settle the SPA, and record outcome.

        Shared by the fresh-dummy path and the stored-key fallback so both get
        the full post-create handling (already-registered → Sign In, contact
        redirect, web_keys upsert) instead of just a bare click.
        """
        ca = await _try_create_account(page, values, click_submit=True)
        phase["create_account"] = ca
        report["create_account"] = ca
        if ca.get("clicks"):
            report["clicks"].extend(
                c for c in ca["clicks"] if c.get("action") != "skipped"
            )

        created_ok = any(c.get("action") == "clicked" for c in (ca.get("clicks") or []))
        if created_ok:
            for _ in range(20):
                if await _contact_phase_present(page):
                    break
                if await _password_only_signin(page):
                    break
                body_w = await _body_text(page, 2500)
                if _already_registered(body_w) or _detect_hard_blocker(
                    body_w, await page.title(), page.url
                ):
                    break
                if not await _create_account_form(page):
                    break
                await page.wait_for_timeout(750)

        body2 = await _body_text(page)
        hard2 = await _hard_blocker_live(page)
        if hard2:
            report["blocker"] = hard2
            report["blocker_detail"] = body2[:500]
            phase["stopped"] = hard2
            return

        if _already_registered(body2):
            phase["already_registered"] = True
            if await _create_account_form(page) and not await _password_only_signin(page):
                switch = await _switch_to_sign_in(page)
                phase["sign_in_switch_clicks"] = switch
                report["clicks"].extend(switch)
                await page.wait_for_timeout(2000)
            si = await _try_sign_in(page, values, click_submit=True)
            phase["sign_in"] = si
            report["sign_in"] = si
            if si.get("clicks"):
                report["clicks"].extend(si["clicks"])
        elif any(c.get("action") == "blocked" for c in (ca.get("clicks") or [])):
            phase["create_blocked"] = True
        elif created_ok:
            if await _contact_phase_present(page):
                phase["account_created"] = True
            elif await _password_only_signin(page):
                phase["account_created"] = True
                phase["post_create_sign_in"] = True
                si = await _try_sign_in(page, values, click_submit=True)
                phase["sign_in"] = si
                report["sign_in"] = si
                if si.get("clicks"):
                    report["clicks"].extend(si["clicks"])
            elif not await _create_account_form(page):
                phase["account_created"] = True
            if phase.get("account_created"):
                await _upsert_web_keys_after_auth(page, values, report)

    # The apply-path click (Apply Manually / Autofill with Resume) may still be
    # resolving into the auth form when we get here. Poll briefly so a create /
    # sign-in form that mounts a beat later is not missed — missing it made
    # _phase_a_auth skip both branches while the generic fill layer typed
    # email/password, leaving the classic filled-but-Create-Account-never-clicked
    # state (the recurring bug the user reported).
    for _ in range(16):  # ~6s
        if await _create_account_form(page) or await _password_only_signin(page):
            break
        if await _contact_phase_present(page):
            break
        await page.wait_for_timeout(375)

    # Create-first auth. If a Create Account form is present, CREATE a dummy
    # account rather than preferring Sign In. never-submit only guards the FINAL
    # application submit; "Create Account" gates as ADVANCE, so clicking it is
    # allowed and dummy accounts are explicitly permitted. The old "prefer Sign
    # In when web_keys has a stored key" path switched forms mid-detection and
    # frequently left the form filled-but-unclicked. A stored email is already
    # registered (that's why a key exists), so reusing it on create would trip
    # "email already in use" — mint a FRESH dummy +alias for a clean new account.
    # Sign In is only attempted when the page is actually a sign-in form.
    if await _create_account_form(page):
        if prefer_stored_signin:
            phase["stored_key_present"] = True
            try:
                from field_map import allocate_random_run_email

                fresh = (allocate_random_run_email() or {}).get("email")
                if fresh:
                    values[EMAIL] = fresh
                    phase["fresh_email_minted"] = True
                    if values.get(PASSWORD) and not values.get(PASSWORD_CONFIRM):
                        values[PASSWORD_CONFIRM] = values[PASSWORD]
            except Exception as e:
                phase["fresh_email_error"] = str(e)[:120]
        await _create_account_flow()
        if phase.get("stopped"):
            return phase

    elif await _password_only_signin(page) or await _email_field_present(page):
        # Actual sign-in form (no create-account form present) — use stored /
        # dummy creds. Overwritten above under prefer_stored_signin.
        si = await _try_sign_in(page, values, click_submit=True)
        phase["sign_in"] = si
        report["sign_in"] = si
        if si.get("clicks"):
            report["clicks"].extend(si["clicks"])

    body = await _body_text(page)
    hard = await _hard_blocker_live(page)
    if hard:
        report["blocker"] = hard
        report["blocker_detail"] = body[:500]
        phase["stopped"] = hard
        return phase

    # After Sign In, some tenants bounce to job page — re-hit Apply if needed
    if (
        phase.get("sign_in")
        and any(c.get("action") == "clicked" for c in (phase["sign_in"].get("clicks") or []))
        and not await _contact_phase_present(page)
        and not await _create_account_form(page)
        and not await _password_only_signin(page)
    ):
        await _wait_for_apply(page, timeout_ms=8000)
        more = await _click_workday_apply_path(
            page, step_report=report.get("_step_report"), report=report
        )
        report["clicks"].extend(more)
        await _poll_spa_settle(
            page,
            timeout_ms=2500,
            poll_ms=250,
            predicates=[
                _contact_phase_present,
                _on_autofill_with_resume_url,
                _create_account_form,
            ],
        )
        if not await _contact_phase_present(page):
            # After captcha risk: prefer Apply Manually — do not re-hit Autofill
            if prefer_manual_after_autofill_risk(report):
                more2 = await _click_gated(page, APPLY_MANUAL_SELECTORS)
                if any(c.get("action") == "clicked" for c in more2):
                    report["clicks"].extend(more2)
                    picked = next(c for c in more2 if c.get("action") == "clicked")
                    _log_wd_entry_click(
                        report.get("_step_report"),
                        text=picked.get("text") or "Apply Manually",
                        reason="apply_manually_prefer_after_captcha",
                        ok=True,
                    )
                    report["workday_entry_path"] = "apply_manually_prefer_after_captcha"
            else:
                # Re-try resume path only (primary Apply already clicked above)
                resume_retry = await _click_gated(page, APPLY_WITH_RESUME_SELECTORS)
                if any(c.get("action") == "clicked" for c in resume_retry):
                    report["clicks"].extend(resume_retry)
                    picked = next(c for c in resume_retry if c.get("action") == "clicked")
                    text = picked.get("text") or "Autofill with Resume"
                    reason = (
                        "autofill_with_resume"
                        if "autofill" in str(text).lower()
                        else "apply_with_resume"
                    )
                    _log_wd_entry_click(
                        report.get("_step_report"),
                        text=text,
                        reason=reason,
                        ok=True,
                    )
                    report["workday_entry_path"] = reason
                else:
                    more2 = await _click_gated(page, APPLY_MANUAL_SELECTORS)
                    if any(c.get("action") == "clicked" for c in more2):
                        report["clicks"].extend(more2)
                        picked = next(c for c in more2 if c.get("action") == "clicked")
                        _log_wd_entry_click(
                            report.get("_step_report"),
                            text=picked.get("text") or "Apply Manually",
                            reason="apply_manually_fallback",
                            ok=True,
                        )
            await _poll_spa_settle(
                page,
                timeout_ms=2800,
                poll_ms=250,
                predicates=[
                    _contact_phase_present,
                    _create_account_form,
                    _on_autofill_with_resume_url,
                ],
            )

    # signed_in / account_created once we leave auth and/or reach contact
    still_auth = await _create_account_form(page) or await _password_only_signin(page)
    title_l = ((await page.title()) or "").lower()
    url_l = (page.url or "").lower()
    if await _contact_phase_present(page):
        # Reaching My Information means auth succeeded (create or sign-in)
        phase["signed_in"] = True
        if phase.get("account_created") or phase.get("create_account"):
            phase["account_created"] = True
    elif (
        phase.get("sign_in")
        and any(c.get("action") == "clicked" for c in (phase["sign_in"].get("clicks") or []))
        and not still_auth
        and "sign in" not in title_l
        and "create account" not in title_l
        and "/login" not in url_l
    ):
        phase["signed_in"] = True

    return phase


async def _on_autofill_with_resume_url(page) -> bool:
    return "autofillwithresume" in (page.url or "").lower()


async def _wait_for_resume_upload_ui(page, timeout_ms: int = 20000) -> bool:
    """Wait until Workday resume Select-files / file-input is present."""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if await _workday_resume_upload_present(page):
            return True
        await page.wait_for_timeout(500)
    return await _workday_resume_upload_present(page)


async def _resume_filename_visible(page) -> str:
    """Return uploaded resume filename from upload chrome / FileList, else ''.

    ATS-004: never treat arbitrary body PDF copy as an uploaded filename.
    """
    try:
        info = await page.evaluate(
            """() => {
  const pick = (t) => (t || '').replace(/\\s+/g, ' ').trim();
  // FileList on inputs
  for (const inp of document.querySelectorAll(
    'input[type=file], [data-automation-id="file-upload-input-ref"]'
  )) {
    if (inp.files && inp.files.length > 0) {
      return {name: inp.files[0].name || '', via: 'filelist'};
    }
  }
  // Upload-chrome only — never scan full body for .pdf (policy/job PDF FPs)
  const uploadRoots = [];
  for (const sel of [
    '[data-automation-id="file-upload-drop-zone"]',
    '[data-automation-id*="file-upload" i]',
    '[data-automation-id="file-upload-filename"]',
    '[data-automation-id="uploadedFileName"]',
    '[class*="fileUpload"]',
    '[class*="FileUpload"]',
    '[class*="file-upload"]',
  ]) {
    document.querySelectorAll(sel).forEach((el) => uploadRoots.push(el));
  }
  const nameSels = [
    '[data-automation-id="file-upload-filename"]',
    '[data-automation-id="uploadedFileName"]',
    '[class*="fileName"]',
    '[class*="FileName"]',
  ];
  const scan = (root) => {
    for (const sel of nameSels) {
      const el = root.querySelector ? root.querySelector(sel) : null;
      if (!el && root.matches && root.matches(sel)) {
        const t = pick(root.innerText || root.textContent || '');
        if (/\\.(pdf|docx?|txt|rtf)\\b/i.test(t)) return t.slice(0, 120);
      }
      if (el) {
        const t = pick(el.innerText || el.textContent || '');
        if (/\\.(pdf|docx?|txt|rtf)\\b/i.test(t)) return t.slice(0, 120);
      }
    }
    // Filename chip text inside upload root only
    const t = pick((root.innerText || root.textContent || '')).slice(0, 400);
    const m = t.match(/([\\w.\\- ]+\\.(pdf|docx?|txt|rtf))\\b/i);
    return m ? m[1] : '';
  };
  for (const root of uploadRoots) {
    const n = scan(root);
    if (n) return {name: n, via: 'upload_chrome'};
  }
  // Global filename automation ids (still scoped selectors, not body)
  for (const sel of nameSels) {
    const el = document.querySelector(sel);
    if (el) {
      const t = pick(el.innerText || el.textContent || '');
      if (/\\.(pdf|docx?|txt|rtf)\\b/i.test(t)) return {name: t.slice(0, 120), via: 'dom'};
    }
  }
  return {name: '', via: ''};
}"""
        )
        return str((info or {}).get("name") or "")[:120]
    except Exception:
        return ""


async def _probe_workday_resume_filelist(page) -> dict:
    """Probe resume file input presence + FileList (FILL3-011).

    Returns ``input_present`` / ``files_on_input`` / optional ``name``.
    """
    out: dict = {"input_present": False, "files_on_input": False, "name": ""}
    try:
        info = await page.evaluate(
            """() => {
              const sels = [
                '[data-automation-id="file-upload-input-ref"]',
                'input[type="file"]',
              ];
              let input = null;
              for (const s of sels) {
                const el = document.querySelector(s);
                if (el) { input = el; break; }
              }
              if (!input) return {input_present: false, files_on_input: false, name: ''};
              const files = input.files;
              const ok = !!(files && files.length > 0);
              const name = ok ? ((files[0] && files[0].name) || '') : '';
              return {input_present: true, files_on_input: ok, name: name};
            }"""
        )
        if isinstance(info, dict):
            out["input_present"] = bool(info.get("input_present"))
            out["files_on_input"] = bool(info.get("files_on_input"))
            out["name"] = str(info.get("name") or "")[:120]
    except Exception:
        pass
    return out


async def _wait_for_autofill_resume_ready(
    page, *, timeout_ms: int = 25000
) -> dict:
    """After upload: wait for filename / parse settle before Continue."""
    out: dict = {
        "ready": False,
        "filename": "",
        "waited_ms": 0,
        "input_present": None,
        "files_on_input": None,
    }
    t0 = time.time()
    deadline = t0 + timeout_ms / 1000
    while time.time() < deadline:
        # FILL3-009: honor Pause between long autofill-resume polls
        try:
            from fill_pause import wait_while_paused

            await wait_while_paused(page, None)
        except Exception:
            pass
        name = await _resume_filename_visible(page)
        if name:
            out["filename"] = name
            probe = await _probe_workday_resume_filelist(page)
            out["input_present"] = probe.get("input_present")
            out["files_on_input"] = probe.get("files_on_input")
            # FILL3-011: ready for advance only when FileList ok or input gone
            try:
                from resume_upload import autofill_filename_verify_ok

                out["ready"] = autofill_filename_verify_ok(
                    filename=name,
                    input_present=out.get("input_present"),
                    files_on_input=out.get("files_on_input"),
                )
            except Exception:
                out["ready"] = True
            # Brief settle for ATS parse into contact fields
            await page.wait_for_timeout(1500)
            out["waited_ms"] = int((time.time() - t0) * 1000)
            return out
        # Parsing spinner / "Uploading" text — keep waiting
        try:
            body = (await _body_text(page, 1500)).lower()
            if "upload failed" in body or "could not upload" in body:
                out["error"] = "upload_failed_banner"
                break
        except Exception:
            pass
        await page.wait_for_timeout(600)
    out["filename"] = await _resume_filename_visible(page)
    probe = await _probe_workday_resume_filelist(page)
    out["input_present"] = probe.get("input_present")
    out["files_on_input"] = probe.get("files_on_input")
    try:
        from resume_upload import autofill_filename_verify_ok

        out["ready"] = autofill_filename_verify_ok(
            filename=out.get("filename"),
            input_present=out.get("input_present"),
            files_on_input=out.get("files_on_input"),
        )
    except Exception:
        out["ready"] = bool(out["filename"])
    out["waited_ms"] = int((time.time() - t0) * 1000)
    return out


async def _workday_resume_upload_present(page) -> bool:
    """True when Workday resume upload UI is visible (autofill or experience step).

    ATS-005: do not treat choice-screen body copy (\"Autofill with Resume\") as
    upload UI — require file input / Select files controls.
    """
    probes = (
        '[data-automation-id="file-upload-input-ref"]',
        '[data-automation-id="file-upload-select-button"]',
        '[data-automation-id="file-upload-select-files"]',
        '[data-automation-id="file-upload-drop-zone"]',
        'button:has-text("Select files")',
        'button:has-text("Select Files")',
        'input[type="file"]',
    )
    for sel in probes:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                try:
                    if await loc.is_visible(timeout=400):
                        return True
                except Exception:
                    # Hidden file inputs still count when on autofill URL
                    if await _on_autofill_with_resume_url(page):
                        return True
        except Exception:
            continue
    # On autofill URL: Upload button near file chrome only (not Apply modal copy)
    if await _on_autofill_with_resume_url(page):
        try:
            upload_btn = page.locator(
                '[data-automation-id*="file-upload" i] button:has-text("Upload"), '
                'button:has-text("Select files"), button:has-text("Select Files")'
            ).first
            if await upload_btn.count() and await upload_btn.is_visible(timeout=400):
                return True
        except Exception:
            pass
    return False


async def _upload_workday_resume_page(page, resume_path: str | Path) -> dict:
    """Upload resume via Workday file-upload-* controls (autofill / experience).

    Prefer ``set_input_files`` on the hidden/visible file input FIRST. Only fall
    back to click + ``expect_file_chooser`` when no input is reachable — avoids
    the 8s filechooser stall and dialog events Cloudflare inspects.
    """
    resume_path = Path(str(resume_path))
    result: dict = {
        "ok": False,
        "verified": False,
        "mode": "file",
        "type": "RESUME_UPLOAD",
        "value": resume_path.name,
        "path": str(resume_path),
        "attach_preference": "set_input_files_first",
    }
    if not resume_path.is_file():
        result["reason"] = "pdf_missing"
        return result

    try:
        from resume_upload import file_chooser_fallback_timeout_ms
    except ImportError:  # pragma: no cover
        def file_chooser_fallback_timeout_ms(*, workday=False):  # type: ignore
            return 3500

    async def _verify_files_on(loc) -> tuple[bool, str]:
        try:
            info = await loc.evaluate(
                """(el) => {
                  const files = el && el.files;
                  if (!files || files.length < 1) return {ok: false, name: ''};
                  return {ok: true, name: files[0].name || ''};
                }"""
            )
        except Exception:
            return False, ""
        if (info or {}).get("ok") and (info or {}).get("name"):
            return True, str(info["name"])[:120]
        return False, ""

    async def _any_file_input():
        loc = page.locator(
            '[data-automation-id="file-upload-input-ref"], input[type="file"]'
        )
        try:
            if await loc.count() > 0:
                return loc.first
        except Exception:
            pass
        return None

    upload = await _any_file_input()
    # Brief poll if SPA has not mounted the input yet
    if upload is None:
        for _ in range(6):
            await page.wait_for_timeout(200)
            upload = await _any_file_input()
            if upload is not None:
                break

    # --- Primary path: set_input_files ---
    if upload is not None:
        try:
            await upload.set_input_files(str(resume_path))
            await page.wait_for_timeout(400)
            verified, readback = await _verify_files_on(upload)
            if not verified:
                # SPA remount — re-resolve and retry once
                upload2 = await _any_file_input()
                if upload2 is not None:
                    await upload2.set_input_files(str(resume_path))
                    await page.wait_for_timeout(400)
                    verified, readback = await _verify_files_on(upload2)
            if not verified:
                # Workday often remounts empty FileList but shows filename chrome
                try:
                    name = await _resume_filename_visible(page)
                    if name:
                        verified = True
                        readback = name[:120]
                except Exception:
                    pass
            result.update(
                {
                    "ok": verified,
                    "verified": verified,
                    "mode": "set_input_files",
                    "readback": readback or resume_path.name,
                    "selector": '[data-automation-id="file-upload-input-ref"]',
                    "reason": "files_on_input" if verified else "resume_unverified",
                }
            )
            if verified:
                return result
        except Exception as e:
            result["set_input_error"] = str(e)[:120]

    # --- Fallback: click + file chooser only when no reachable input OR set_input_files raised ---
    if result.get("verified"):
        return result
    if upload is not None and not result.get("set_input_error"):
        # Input was reachable; set_input_files ran without raise but unverified —
        # skip chooser (avoids multi-second stall Cloudflare scores as dialog noise).
        if not result.get("reason"):
            result["reason"] = "resume_unverified"
        return result

    fc_timeout = file_chooser_fallback_timeout_ms(workday=True)
    for sel_txt in (
        '[data-automation-id="file-upload-select-button"]',
        'button:has-text("Select files")',
        'button:has-text("Select Files")',
        'text=Select files',
        'button:has-text("Upload")',
        'button:has-text("Attach")',
    ):
        sel = page.locator(sel_txt).first
        if not await sel.count():
            continue
        try:
            if not await sel.is_visible(timeout=800):
                continue
        except Exception:
            continue
        try:
            async with page.expect_file_chooser(timeout=fc_timeout) as fc_info:
                await sel.click(timeout=3000)
            chooser = await fc_info.value
            await chooser.set_files(str(resume_path))
            await page.wait_for_timeout(500)
            verified = False
            readback = resume_path.name
            for fi in range(min(await page.locator('input[type="file"]').count(), 6)):
                inp = page.locator('input[type="file"]').nth(fi)
                ok, name = await _verify_files_on(inp)
                if ok:
                    verified = True
                    readback = name
                    break
            if not verified:
                try:
                    name = await _resume_filename_visible(page)
                    if name:
                        verified = True
                        readback = name[:120]
                except Exception:
                    pass
            result.update(
                {
                    "ok": verified,
                    "verified": verified,
                    "mode": "file_chooser",
                    "file_chooser_fallback": True,
                    "readback": readback,
                    "selector": sel_txt,
                    "reason": "files_on_input" if verified else "chooser_unverified",
                }
            )
            if verified:
                return result
        except Exception:
            continue

    if not result.get("reason"):
        result["reason"] = "no_file_input" if upload is None else "resume_unverified"
    return result


async def _advance_from_autofill_resume(page) -> list[dict]:
    """Click Continue/Next after resume on autofill-with-resume interstitial."""
    extra = [
        '[data-automation-id="continueButton"]',
        'button:has-text("Continue Application")',
    ]
    return await _click_gated(
        page, NEXT_BUTTON_SELECTORS + extra, stop_after_click=True
    )


async def _log_autofill_fallback(step_report: dict | None, *, reason: str) -> None:
    if not step_report:
        return
    try:
        from fill_step_log import note_step

        note_step(
            step_report,
            action="click_entry",
            label="Apply Manually",
            via="workday_entry",
            reason=reason,
        )
    except Exception:
        pass


async def _fallback_apply_manually_from_autofill(
    page, values: dict, report: dict
) -> bool:
    """Re-enter via Apply Manually when autofill/resume path never reaches contact."""
    if await _contact_phase_present(page):
        return False

    await _log_autofill_fallback(
        report.get("_step_report"), reason="autofill_stuck_fallback_manual"
    )
    fb: dict = {"attempted": True, "reached_contact": False}
    report["autofill_fallback"] = fb

    manual = await _click_gated(page, APPLY_MANUAL_SELECTORS)
    report.setdefault("clicks", []).extend(manual)
    if any(c.get("action") == "clicked" for c in manual):
        await _poll_spa_settle(
            page,
            timeout_ms=2800,
            poll_ms=250,
            predicates=[_contact_phase_present, _create_account_form],
        )
        if await _contact_phase_present(page):
            fb["via"] = "click_apply_manually"
            fb["reached_contact"] = True
            return True

    url = page.url or ""
    if "autofillwithresume" in url.lower():
        manual_url = re.sub(
            r"/apply/autofillWithResume\b",
            "/apply/applyManually",
            url,
            count=1,
            flags=re.IGNORECASE,
        )
        if manual_url != url:
            try:
                await page.goto(
                    manual_url, wait_until="domcontentloaded", timeout=45000
                )
                await _poll_spa_settle(
                    page,
                    timeout_ms=2800,
                    poll_ms=250,
                    predicates=[_contact_phase_present, _create_account_form],
                )
                fb["via"] = "url_rewrite_apply_manually"
                if await _contact_phase_present(page):
                    fb["reached_contact"] = True
                    return True
            except Exception as e:
                fb["url_error"] = str(e)[:120]

    if await _password_only_signin(page) or await _email_field_present(page):
        si = await _try_sign_in(page, values, click_submit=True)
        fb["sign_in"] = bool(si.get("clicks"))
        report.setdefault("sign_in_fallback", si)
        if si.get("clicks"):
            report.setdefault("clicks", []).extend(si["clicks"])
        await _poll_spa_settle(
            page,
            timeout_ms=2500,
            poll_ms=250,
            predicates=[_contact_phase_present],
        )
        if await _contact_phase_present(page):
            fb["reached_contact"] = True
            return True

    job_url = str(report.get("url") or "")
    if job_url and job_url.split("?")[0] != (page.url or "").split("?")[0]:
        try:
            await page.goto(job_url, wait_until="domcontentloaded", timeout=45000)
            await _wait_for_apply(page)
            report.setdefault("clicks", []).extend(
                await _click_gated(page, APPLY_PRIMARY_SELECTORS)
            )
            await _poll_spa_settle(
                page,
                timeout_ms=2000,
                poll_ms=200,
                predicates=[
                    _on_autofill_with_resume_url,
                    _create_account_form,
                    _password_only_signin,
                ],
            )
            report.setdefault("clicks", []).extend(
                await _click_gated(page, APPLY_MANUAL_SELECTORS)
            )
            await _poll_spa_settle(
                page,
                timeout_ms=2800,
                poll_ms=250,
                predicates=[
                    _contact_phase_present,
                    _create_account_form,
                    _password_only_signin,
                ],
            )
            if await _password_only_signin(page):
                si = await _try_sign_in(page, values, click_submit=True)
                if si.get("clicks"):
                    report.setdefault("clicks", []).extend(si["clicks"])
                await _poll_spa_settle(
                    page,
                    timeout_ms=2500,
                    poll_ms=250,
                    predicates=[_contact_phase_present],
                )
            fb["via"] = fb.get("via") or "job_page_reentry"
        except Exception as e:
            fb["reentry_error"] = str(e)[:120]

    reached = await _contact_phase_present(page)
    fb["reached_contact"] = reached
    return reached


async def _handle_autofill_resume_after_auth(
    page, values: dict, report: dict
) -> dict:
    """After auth on apply-with-resume: upload resume, advance, or manual fallback.

    Keep résumé/autofill preference when it works. When captcha / hard blocker /
    stuck upload appears, fall back to Apply Manually quickly — do not re-click
    Autofill with Resume after captcha already cleared once.
    """
    summary: dict = {"handled": False, "reached_contact": False}

    # Fast exit to manual when prior captcha / risk already recorded
    if prefer_manual_after_autofill_risk(report):
        hard = await _hard_blocker_live(page)
        if hard in ("captcha", "cloudflare"):
            mark_autofill_risk(report, reason="interactive_captcha")
            summary["skipped_reason"] = "prefer_manual_captcha_risk"
            summary["path"] = "manual_prefer"
            fb = await _fallback_apply_manually_from_autofill(page, values, report)
            summary["fallback_manual"] = fb
            summary["reached_contact"] = fb
            summary["handled"] = True
            return summary

    # Live captcha on autofill page → abandon autofill immediately
    hard0 = await _hard_blocker_live(page)
    if hard0 in ("captcha", "cloudflare"):
        mark_autofill_risk(report, reason="captcha_on_autofill")
        summary["handled"] = True
        summary["skipped_reason"] = "captcha_on_autofill"
        summary["path"] = "manual_fallback"
        fb = await _fallback_apply_manually_from_autofill(page, values, report)
        summary["fallback_manual"] = fb
        summary["reached_contact"] = fb
        return summary

    on_autofill = await _on_autofill_with_resume_url(page)
    resume_ui = await _workday_resume_upload_present(page)
    # Prefer Autofill when choice screen still visible after auth bounce —
    # but NEVER re-attempt Autofill if captcha was already cleared this run.
    if (
        not on_autofill
        and not resume_ui
        and not prefer_manual_after_autofill_risk(report)
    ):
        retry = await _click_gated(page, APPLY_WITH_RESUME_SELECTORS)
        if any(c.get("action") == "clicked" for c in retry):
            report.setdefault("clicks", []).extend(retry)
            picked = next(c for c in retry if c.get("action") == "clicked")
            text = picked.get("text") or "Autofill with Resume"
            reason = (
                "autofill_with_resume"
                if "autofill" in str(text).lower()
                else "apply_with_resume"
            )
            _log_wd_entry_click(
                report.get("_step_report"), text=text, reason=reason, ok=True
            )
            report["workday_entry_path"] = reason
            await _poll_spa_settle(
                page,
                timeout_ms=3000,
                poll_ms=200,
                predicates=[
                    _on_autofill_with_resume_url,
                    _workday_resume_upload_present,
                    _create_account_form,
                ],
            )
            hard_r = await _hard_blocker_live(page)
            if hard_r in ("captcha", "cloudflare"):
                mark_autofill_risk(report, reason="captcha_after_autofill_retry")
                summary["handled"] = True
                summary["path"] = "manual_fallback"
                summary["skipped_reason"] = "captcha_after_autofill_retry"
                fb = await _fallback_apply_manually_from_autofill(page, values, report)
                summary["fallback_manual"] = fb
                summary["reached_contact"] = fb
                return summary
            on_autofill = await _on_autofill_with_resume_url(page)
            resume_ui = await _workday_resume_upload_present(page)
    if not on_autofill and not resume_ui:
        if prefer_manual_after_autofill_risk(report):
            summary["handled"] = True
            summary["path"] = "manual_prefer"
            fb = await _fallback_apply_manually_from_autofill(page, values, report)
            summary["fallback_manual"] = fb
            summary["reached_contact"] = fb
            return summary
        summary["skipped_reason"] = "no_autofill_or_resume_ui"
        return summary

    summary["handled"] = True
    summary["on_autofill_url"] = on_autofill
    summary["path"] = "autofill"
    report["workday_entry_path"] = report.get("workday_entry_path") or (
        "autofill_with_resume" if on_autofill else "apply_with_resume"
    )

    # Shorter UI wait — if upload chrome never mounts, fall back to manual fast
    ui_ok = await _wait_for_resume_upload_ui(page, timeout_ms=8000)
    summary["resume_ui_wait"] = ui_ok
    if not ui_ok and on_autofill:
        # Do NOT re-click Autofill (re-triggers Cloudflare). Fall back to manual.
        mark_autofill_risk(report, reason="resume_ui_never_mounted")
        summary["path"] = "manual_fallback"
        summary["skipped_reason"] = "resume_ui_never_mounted"
        fb = await _fallback_apply_manually_from_autofill(page, values, report)
        summary["fallback_manual"] = fb
        summary["reached_contact"] = fb
        return summary

    if await _create_account_form(page):
        cb = await _check_create_account_terms(page)
        ca = report.get("create_account")
        if not ca and isinstance(report.get("phase_a"), dict):
            ca = (report["phase_a"] or {}).get("create_account")
        if isinstance(ca, dict):
            filled = list(ca.get("filled") or [])
            updated = False
            for i, row in enumerate(filled):
                aid = str(row.get("automation_id") or "")
                if "createAccountCheckbox" in aid:
                    if cb.get("verified"):
                        filled[i] = cb
                    updated = True
                    break
            if not updated and cb.get("status") == "filled":
                filled.append(cb)
            ca["filled"] = filled
            report["create_account"] = ca
            if isinstance(report.get("phase_a"), dict):
                report["phase_a"]["create_account"] = ca

    resume_path = values.get("_resume_pdf") or str(DUMMY_PDF)
    try:
        from resume_upload import ensure_resume_uploaded, report_has_verified_resume

        ru = await ensure_resume_uploaded(page, values, report, force=True)
        summary["upload"] = {
            k: ru.get(k)
            for k in ("attempted", "verified", "field_present", "skipped")
            if k in ru
        }
        if isinstance(ru.get("result"), dict):
            summary["upload"]["reason"] = ru["result"].get("reason")
            summary["upload"]["mode"] = ru["result"].get("mode")
        if ru.get("verified"):
            report["resume_verified"] = True
            report["resume_field_present"] = True
            try:
                from resume_upload import sync_resume_verified_from_phase_a

                # phase_a_resume not attached yet; sync after caller assigns summary
            except Exception:
                pass
        else:
            stuck = upload_stuck_reason(ru) or upload_stuck_reason(ru.get("result"))
            if stuck:
                mark_autofill_risk(report, reason=f"upload_stuck:{stuck}")
                summary["upload_stuck"] = stuck
    except Exception as e:
        summary["upload_error"] = str(e)[:120]
        mark_autofill_risk(report, reason="upload_exception")

    try:
        from resume_upload import report_has_verified_resume
    except ImportError:
        def report_has_verified_resume(_r):  # type: ignore
            return False

    if not report_has_verified_resume(report):
        wd_up = await _upload_workday_resume_page(page, resume_path)
        summary["workday_upload"] = wd_up
        if wd_up.get("verified"):
            report.setdefault("filled", []).append(
                {
                    "via": "workday_autofill_resume",
                    "layer": "0.5",
                    "type": "RESUME_UPLOAD",
                    "mode": wd_up.get("mode") or "file",
                    "selector": wd_up.get("selector"),
                    "value": wd_up.get("value"),
                    "readback": wd_up.get("readback"),
                    "ok": True,
                    "verified": True,
                    "automation_id": "file-upload-input-ref",
                }
            )
        else:
            stuck = upload_stuck_reason(wd_up)
            if stuck:
                mark_autofill_risk(report, reason=f"workday_upload_stuck:{stuck}")
                summary["workday_upload_stuck"] = stuck
                # Stuck upload on autofill → manual sooner (skip long ready poll)
                summary["path"] = "manual_fallback"
                fb = await _fallback_apply_manually_from_autofill(page, values, report)
                summary["fallback_manual"] = fb
                summary["reached_contact"] = fb
                return summary

    # Captcha may reappear after upload clicks — abandon before long ready wait
    hard_u = await _hard_blocker_live(page)
    if hard_u in ("captcha", "cloudflare"):
        mark_autofill_risk(report, reason="captcha_reappeared")
        summary["path"] = "manual_fallback"
        summary["skipped_reason"] = "captcha_reappeared_after_upload"
        fb = await _fallback_apply_manually_from_autofill(page, values, report)
        summary["fallback_manual"] = fb
        summary["reached_contact"] = fb
        return summary

    # Wait for ATS filename / parse settle before Continue (shorter than before)
    ready = await _wait_for_autofill_resume_ready(page, timeout_ms=16000)
    summary["autofill_ready"] = ready
    if ready.get("filename") and not report_has_verified_resume(report):
        # FILL3-011: filename chrome alone is not verified when FileList empty
        try:
            from resume_upload import autofill_filename_verify_ok

            fn_ok = autofill_filename_verify_ok(
                filename=ready.get("filename"),
                input_present=ready.get("input_present"),
                files_on_input=ready.get("files_on_input"),
            )
        except Exception:
            fn_ok = bool(ready.get("ready"))
        row = {
            "via": "workday_autofill_resume",
            "layer": "0.5",
            "type": "RESUME_UPLOAD",
            "mode": "filename_visible",
            "value": ready.get("filename"),
            "readback": ready.get("filename"),
            "input_present": ready.get("input_present"),
            "files_on_input": ready.get("files_on_input"),
            "automation_id": "file-upload-filename",
        }
        if fn_ok:
            row.update({"ok": True, "verified": True, "reason": "filename_visible"})
            report.setdefault("filled", []).append(row)
            report["resume_verified"] = True
            report["resume_field_present"] = True
        else:
            row.update(
                {
                    "ok": False,
                    "verified": False,
                    "reason": "filename_visible_filelist_empty",
                }
            )
            report.setdefault("filled", []).append(row)
            summary["filename_visible_blocked"] = "filelist_empty_with_input"

    # ATS-002 / FILL3-011: NEVER Continue without a verified resume upload
    resume_ok = report_has_verified_resume(report)
    summary["resume_verified_before_advance"] = resume_ok
    if resume_ok and (
        on_autofill or await _workday_resume_upload_present(page) or ready.get("ready")
    ):
        adv = await _advance_from_autofill_resume(page)
        summary["advance_clicks"] = adv
        summary["advanced"] = any(c.get("action") == "clicked" for c in adv)
        report.setdefault("clicks", []).extend(adv)
        await _poll_spa_settle(
            page,
            timeout_ms=2500,
            poll_ms=250,
            predicates=[_contact_phase_present],
        )
    elif not resume_ok:
        summary["advance_blocked_reason"] = "resume_not_verified"
        if ready.get("filename") and ready.get("input_present") and not ready.get(
            "files_on_input"
        ):
            summary["advance_blocked_reason"] = "filename_visible_filelist_empty"
        report.setdefault("errors", []).append(
            {"autofill_resume": summary["advance_blocked_reason"]}
        )

    if not await _contact_phase_present(page):
        await _wait_contact_phase(page, timeout_ms=4500)
    summary["reached_contact"] = await _contact_phase_present(page)

    if (
        not summary["reached_contact"]
        and resume_ok
        and (ready.get("ready") or report_has_verified_resume(report))
    ):
        # If captcha reappeared after Continue, prefer manual over another advance
        hard_a = await _hard_blocker_live(page)
        if hard_a in ("captcha", "cloudflare"):
            mark_autofill_risk(report, reason="captcha_reappeared")
        else:
            adv2 = await _advance_from_autofill_resume(page)
            summary["advance_retry"] = adv2
            report.setdefault("clicks", []).extend(adv2)
            await _poll_spa_settle(
                page,
                timeout_ms=2800,
                poll_ms=250,
                predicates=[_contact_phase_present],
            )
            if not await _contact_phase_present(page):
                await _wait_contact_phase(page, timeout_ms=3500)
            summary["reached_contact"] = await _contact_phase_present(page)

    if not summary["reached_contact"]:
        mark_autofill_risk(report, reason="autofill_stuck_no_contact")
        fb = await _fallback_apply_manually_from_autofill(page, values, report)
        summary["fallback_manual"] = fb
        summary["reached_contact"] = fb
        if fb:
            summary["path"] = "manual_fallback"

    return summary


NEXT_BUTTON_SELECTORS = [
    '[data-automation-id="bottom-navigation-next-button"]',
    'button:has-text("Save and Continue")',
    'button:has-text("Save & Continue")',
    'button:has-text("Next")',
    'button:has-text("Continue")',
]


async def _click_next_advance(page) -> list[dict]:
    """Click Workday Next/Continue once if button_gate allows ADVANCE. Never FINAL."""
    return await _click_gated(page, NEXT_BUTTON_SELECTORS, stop_after_click=True)


# _is_verified_fill / _how_heard_candidates imported from fill_verify at module load


async def _fill_how_heard(page, values: dict | None = None) -> dict:
    """Fill Workday How-Heard via automation-id / label / multiselect (dummy only).

    Prefer fiber ``searchSelect`` (ChamPro) before type+nudge. Never tries bare
    ``Internet`` — that is uncommitted filter text, not a chip.

    Once a chip/token is verified committed, **stop** — do not thrash aliases
    (Indeed → Company Website → LinkedIn → Other…). Prefer
    ``already_correct_keep`` when live readback already has a concrete source.
    """
    aid = "how_heard"
    candidates = _how_heard_candidates(values)
    # Learned option aliases (auto-apply style) before static candidates
    try:
        from option_mappings import lookup_aliases
        from urllib.parse import urlparse

        host = ""
        try:
            host = urlparse(page.url).hostname or ""
        except Exception:
            host = ""
        learned = lookup_aliases(
            platform="workday",
            host=host,
            field_type=HOW_HEARD,
            label="How Did You Hear About Us",
            canonical=candidates[0] if candidates else "",
        )
        for a in learned:
            if a and a not in candidates:
                candidates.insert(0, a)
    except Exception:
        pass
    result: dict = {
        "automation_id": aid,
        "status": "missed",
        "reason": "not_attempted",
        "verified": False,
        "type": HOW_HEARD,
    }

    def _learn(ok_result: dict) -> dict:
        if not _is_verified_fill(ok_result):
            return ok_result
        try:
            from option_mappings import upsert_mapping
            from urllib.parse import urlparse

            host = ""
            try:
                host = urlparse(page.url).hostname or ""
            except Exception:
                host = ""
            upsert_mapping(
                platform="workday",
                host=host,
                field_type=HOW_HEARD,
                label="How Did You Hear About Us",
                canonical=str(
                    ok_result.get("value")
                    or (candidates[0] if candidates else "")
                ),
                chosen_option=str(
                    ok_result.get("option_text")
                    or ok_result.get("readback")
                    or ok_result.get("value")
                    or ""
                ),
            )
        except Exception:
            pass
        return ok_result

    async def _settle_ok(ok_result: dict) -> dict:
        """Close open search menu after commit; learn mapping."""
        try:
            from verified_select import settle_open_listbox

            await settle_open_listbox(page)
        except Exception:
            pass
        return _learn(ok_result)

    # Already committed? Keep and stop — never reopen for next alias.
    keep0 = await _probe_how_heard_already_committed(page, candidates)
    if keep0 is not None:
        return _learn(keep0)

    other_tried = False
    for cand in candidates:
        # Prefer one concrete Other* option once — do not cycle Other variants
        if re.search(r"^other\b", str(cand), re.I):
            if other_tried:
                continue
            other_tried = True
        result = await _fill_automation_id(page, aid, cand, combobox=True)
        result.setdefault("type", HOW_HEARD)
        if _is_verified_fill(result):
            return await _settle_ok(result)
        # Chip may have committed even when this candidate's readback gate failed
        keep_mid = await _probe_how_heard_already_committed(page, candidates)
        if keep_mid is not None:
            return _learn(keep_mid)
        if result.get("reason") in ("not_in_dom", "not_visible"):
            break
    # Fiber searchSelect on source / how_heard filter inputs (Quantiphi etc.)
    if not _is_verified_fill(result):
        try:
            from verified_select import fiber_search_select

            fiber_inps = page.locator(
                'input[name="source--source"], '
                '[data-automation-id="source--source"], '
                '[data-automation-id="formField-source"] input, '
                '[data-automation-id="formField-how_heard"] input, '
                '[data-automation-id="multiSelectContainer"] input'
            )
            n_inps = await fiber_inps.count()
            for ii in range(min(n_inps, 4)):
                inp = fiber_inps.nth(ii)
                try:
                    if not await inp.is_visible(timeout=400):
                        continue
                except Exception:
                    continue
                # Re-check before each fiber pass — prior alias may have stuck a chip
                keep_f = await _probe_how_heard_already_committed(page, candidates)
                if keep_f is not None:
                    return _learn(keep_f)
                other_fiber = False
                for cand in candidates:
                    if re.search(r"^other\b", str(cand), re.I):
                        if other_fiber:
                            continue
                        other_fiber = True
                    fiber = await fiber_search_select(
                        page,
                        inp,
                        str(cand),
                        aliases=candidates,
                        wait_ms=1600,
                    )
                    if not (fiber.get("option_clicked") and fiber.get("picked")):
                        # Stop alias thrash if a chip appeared mid-loop
                        keep_f2 = await _probe_how_heard_already_committed(
                            page, candidates
                        )
                        if keep_f2 is not None:
                            return _learn(keep_f2)
                        continue
                    body_snip = ""
                    try:
                        wrap = page.locator(
                            '[data-automation-id="formField-source"], '
                            '[data-automation-id="formField-how_heard"], '
                            '[data-automation-id="multiSelectContainer"]'
                        ).first
                        body_snip = ((await wrap.inner_text()) or "")[:200]
                    except Exception:
                        body_snip = str(fiber.get("picked") or "")
                    verified = "0 items selected" not in body_snip.lower() and bool(
                        fiber.get("picked") or body_snip
                    )
                    result = {
                        "automation_id": aid,
                        "status": "filled" if verified else "missed",
                        "mode": "fiber_search_select",
                        "type": HOW_HEARD,
                        "value": cand,
                        "readback": (body_snip or fiber.get("picked") or "")[:120],
                        "option_text": fiber.get("picked"),
                        "picked": fiber.get("picked"),
                        "option_clicked": True,
                        "verified": verified,
                        "committed": verified,
                        "algorithm": "fiber_search_select",
                        "fiber_status": fiber.get("status"),
                        "reason": None if verified else "multiselect_no_chip",
                    }
                    if verified:
                        return await _settle_ok(result)
                    keep_f3 = await _probe_how_heard_already_committed(
                        page, candidates
                    )
                    if keep_f3 is not None:
                        return _learn(keep_f3)
        except Exception as e:
            result = {
                "automation_id": aid,
                "status": "missed",
                "reason": "fiber_search_error",
                "error": str(e)[:120],
                "verified": False,
                "type": HOW_HEARD,
            }
    # BBH/wd5: multi-select source--source shows "0 items selected" until option chip
    if not _is_verified_fill(result):
        keep_pre = await _probe_how_heard_already_committed(page, candidates)
        if keep_pre is not None:
            return _learn(keep_pre)
        for cand in candidates:
            if re.search(r"^other\b", str(cand), re.I) and other_tried:
                continue
            for hear_label in (
                "How Did You Hear About Us",
                "Where Did You Hear About Us",
                "Where did you hear about us",
                "How did you hear about us",
                "How Did You Hear",
            ):
                sr = await _fill_select_one_by_label(
                    page,
                    hear_label,
                    [cand],
                )
                if sr.get("verified") or _is_verified_fill(sr):
                    return await _settle_ok({
                        "automation_id": aid,
                        "status": "filled",
                        "mode": "select_one",
                        "type": HOW_HEARD,
                        "value": cand,
                        "readback": sr.get("readback"),
                        "verified": True,
                        "option_clicked": sr.get("option_clicked"),
                        "committed": sr.get("committed", True),
                    })
            inp = page.locator(
                'input[name="source--source"], '
                '[data-automation-id="source--source"], '
                '[data-automation-id="formField-source"] input'
            ).first
            try:
                if await inp.count() and await inp.is_visible(timeout=400):
                    await inp.click(timeout=3000)
                    await page.wait_for_timeout(200)
                    await inp.fill("")
                    await page.keyboard.type(str(cand), delay=25)
                    await page.wait_for_timeout(500)
                    # Quantiphi/WD prompts often need ArrowDown/icon/Enter before options load
                    try:
                        from verified_select import nudge_listbox_after_type

                        await nudge_listbox_after_type(page, inp, allow_enter=True)
                        await page.wait_for_timeout(450)
                    except Exception:
                        pass
                    ok, opt = await _click_matching_option(page, str(cand))
                    if ok:
                        body_snip = ""
                        try:
                            wrap = page.locator(
                                '[data-automation-id="formField-source"]'
                            ).first
                            body_snip = ((await wrap.inner_text()) or "")[:200]
                        except Exception:
                            body_snip = opt or ""
                        verified = "0 items selected" not in body_snip.lower() and bool(
                            opt or body_snip
                        )
                        result = {
                            "automation_id": aid,
                            "status": "filled" if verified else "missed",
                            "mode": "multiselect_source",
                            "type": HOW_HEARD,
                            "value": cand,
                            "readback": (body_snip or opt or "")[:120],
                            "option_text": opt,
                            "option_clicked": True,
                            "verified": verified,
                            "reason": None if verified else "multiselect_no_chip",
                        }
                        if verified:
                            return await _settle_ok(result)
                        keep_ms = await _probe_how_heard_already_committed(
                            page, candidates
                        )
                        if keep_ms is not None:
                            return _learn(keep_ms)
                    try:
                        await _escape_unless_captcha(page)
                    except Exception:
                        pass
            except Exception:
                pass
    if result.get("status") == "missed" and result.get("reason") in (
        "not_in_dom",
        "not_visible",
        "no_matching_option",
        "not_attempted",
        "multiselect_no_chip",
        "fiber_search_error",
    ):
        keep_late = await _probe_how_heard_already_committed(page, candidates)
        if keep_late is not None:
            return _learn(keep_late)
        label_btn = page.locator(
            'label:has-text("How Did You Hear"), '
            'label:has-text("Where Did You Hear"), '
            'label:has-text("Where did you hear"), '
            'legend:has-text("How Did You Hear"), '
            'legend:has-text("Where Did You Hear"), '
            '[data-automation-id="formField-source"]'
        ).first
        try:
            if await label_btn.count():
                root = label_btn.locator(
                    "xpath=ancestor-or-self::*[contains(@data-automation-id,'formField') "
                    "or self::fieldset or self::div][1]"
                )
                btn = root.locator(
                    'button, [role="button"], [role="combobox"], input'
                ).first
                if await btn.count() and await btn.is_visible(timeout=500):
                    from verified_select import fill_workday_combobox

                    for cand in candidates:
                        if re.search(r"^other\b", str(cand), re.I) and other_tried:
                            continue
                        wd = await fill_workday_combobox(
                            page,
                            btn,
                            str(cand),
                            aliases=[cand, *candidates],
                            read_committed=lambda: _read_field_value(btn),
                            timeout_ms=7000,
                            label="How Did You Hear",
                            field_type=HOW_HEARD,
                        )
                        readback = await _read_field_value(btn)
                        opt = wd.get("picked") or ""
                        verified = bool(wd.get("ok")) and (
                            _value_matches_readback(
                                str(cand), readback or opt or "", mode="combobox"
                            )
                            or (
                                "0 items selected"
                                not in ((readback or "") + (opt or "")).lower()
                                and bool(opt)
                            )
                        )
                        result = {
                            "automation_id": aid,
                            "status": "filled" if verified else "missed",
                            "mode": "combobox_label",
                            "type": HOW_HEARD,
                            "value": cand,
                            "readback": (readback or opt or "")[:120],
                            "option_text": opt,
                            "option_clicked": bool(wd.get("option_clicked")),
                            "verified": verified,
                            "reason": None if verified else "readback_mismatch",
                            "algorithm": wd.get("algorithm"),
                        }
                        if verified:
                            return await _settle_ok(result)
                        keep_cb = await _probe_how_heard_already_committed(
                            page, candidates
                        )
                        if keep_cb is not None:
                            return _learn(keep_cb)
                        try:
                            await _escape_unless_captcha(page)
                        except Exception:
                            pass
        except Exception as e:
            result = {
                "automation_id": aid,
                "status": "missed",
                "reason": "label_fill_error",
                "error": str(e)[:120],
                "verified": False,
                "type": HOW_HEARD,
            }
    # Final keep probe — never leave an open menu spinning after a late chip
    keep_final = await _probe_how_heard_already_committed(page, candidates)
    if keep_final is not None:
        return _learn(keep_final)
    result.setdefault("type", HOW_HEARD)
    return _learn(result) if _is_verified_fill(result) else result


def _log_wd_fill_step(report: dict, result: dict) -> None:
    """Emit one Workday contact fill row to the parent fast_fill step log (live)."""
    parent = report.get("_step_report")
    if not parent or not isinstance(result, dict):
        return
    try:
        from fill_step_log import log_row_as_step

        row = {
            "label": result.get("automation_id") or result.get("type") or "",
            "type": result.get("type") or result.get("automation_id") or "",
            "readback": result.get("readback"),
            "value": result.get("value"),
            "via": "workday_contact_pack",
            "mode": result.get("mode"),
            "selector": result.get("selector"),
            "verified": result.get("verified"),
            "option_clicked": result.get("option_clicked"),
            "committed": result.get("committed"),
            "ok": result.get("status") == "filled" and result.get("verified") is not False,
            "reason": result.get("reason"),
        }
        log_row_as_step(parent, row)
    except Exception as e:
        import logging

        logging.getLogger("exp_workday_selectors").debug(
            "step log emit failed: %s", e
        )


async def _fill_contact_extras(page, values: dict) -> list[dict]:
    """Fill How-Heard / worked-here / contact email using dummy profile values only."""
    results: list[dict] = []
    for aid, ftype, widget in WD_CONTACT_EXTRAS:
        val = values.get(ftype)
        if not val:
            results.append({
                "automation_id": aid,
                "status": "missed",
                "reason": "no_dummy_value",
                "type": ftype,
                "verified": False,
            })
            continue
        if widget == "radio":
            rr = await _fill_radio_yes_no(page, aid, str(val))
            rr.setdefault("type", ftype)
            results.append(rr)
            continue
        if widget == "text":
            result = await _fill_automation_id(page, aid, str(val), combobox=False)
            result.setdefault("type", ftype)
            results.append(result)
            continue
        # Combobox how-heard (shared helper — never bare "Internet")
        if ftype == HOW_HEARD or aid == "how_heard":
            result = await _fill_how_heard(page, values)
            results.append(result)
            continue
        result = await _fill_automation_id(page, aid, str(val), combobox=True)
        result.setdefault("type", ftype)
        results.append(result)
    return results


async def _phase_b_contact(page, fill_plan: list[tuple[str, str, bool]], report: dict) -> dict:
    """Fill contact pack; ADVANCE only when required visible fields are complete.

    Never click Save and Continue into a validation-error state. Metrics only
    count verified non-empty readbacks. Exhausted incomplete pack → no ADVANCE
    (blocker/page_incomplete); never label successful fills as stuck.
    """
    phase: dict = {
        "name": "B_contact",
        "present": False,
        "filled": [],
        "stuck": [],  # unused — kept empty so reporters never confuse stuck≠filled
        "missed": [],
        "next_clicks": [],
        "advanced": False,
        "advance_blocked_reason": None,
        "validation_after_advance": None,
        "required_empty_before_advance": [],
    }
    present = await _wait_contact_phase(page)
    phase["present"] = present
    report["contact_page_present"] = present

    body = await _body_text(page)
    hard = _detect_hard_blocker(body, await page.title(), page.url)
    if hard:
        report["blocker"] = hard
        report["blocker_detail"] = body[:500]
        phase["stopped"] = hard
        report["verdict"] = "FAIL"
        return phase

    if not present:
        phase["missed"] = [
            {"automation_id": aid, "status": "missed", "reason": "contact_page_absent", "verified": False}
            for aid, _, _ in fill_plan
        ]
        report["stuck"] = []
        report["filled"] = []
        report["missed"] = phase["missed"]
        report["verdict"] = "FAIL"
        return phase

    filled: list[dict] = []
    missed: list[dict] = []
    for aid, val, combobox in fill_plan:
        try:
            from fill_pause import wait_while_paused

            await wait_while_paused(page, report.get("_step_report") or report)
        except Exception:
            pass
        result = await _fill_automation_id(page, aid, val, combobox=combobox)
        result.setdefault("type", next((ft for a, ft in WD_CONTACT_PACK if a == aid), aid))
        _log_wd_fill_step(report, result)
        if _is_verified_fill(result):
            result["status"] = "filled"
            filled.append(result)
        else:
            result["status"] = "missed"
            result.setdefault("verified", False)
            missed.append(result)

    # Country Phone Code (Markel-class): fix Anguilla/+wrong territory → US +1
    try:
        cpc = await _fill_country_phone_code(page)
        _log_wd_fill_step(report, cpc)
        if _is_verified_fill(cpc):
            cpc["status"] = "filled"
            filled.append(cpc)
        elif cpc.get("reason") not in ("not_in_dom", "not_visible"):
            missed.append(cpc)
    except Exception as e:
        phase.setdefault("errors", []).append(f"country_phone_code:{str(e)[:80]}")

    # Dummy-profile extras (how heard / worked here / contact email)
    extras_values = report.get("_contact_values") or {}
    if extras_values:
        for result in await _fill_contact_extras(page, extras_values):
            result.setdefault("type", result.get("type") or result.get("automation_id"))
            _log_wd_fill_step(report, result)
            if _is_verified_fill(result):
                result["status"] = "filled"
                filled.append(result)
            else:
                # Extra not present on page is OK (not_in_dom / radio_not_found)
                if result.get("reason") in (
                    "not_in_dom",
                    "not_visible",
                    "radio_not_found",
                    "no_matching_option",
                ):
                    result["optional_miss"] = True
                missed.append(result)

    # Sweep remaining required Select Ones / known blanks before ADVANCE gate
    try:
        await _fill_required_select_ones(page, extras_values or {}, phase)
        for row in phase.get("filled") or []:
            if row not in filled and _is_verified_fill(row):
                filled.append(row)
        for row in phase.get("missed") or []:
            if row not in missed:
                missed.append(row)
    except Exception as e:
        phase.setdefault("errors", []).append(f"select_ones:{str(e)[:100]}")

    phase["filled"] = filled
    phase["stuck"] = []  # never alias successful fills as stuck
    phase["missed"] = missed
    report["filled"] = filled
    report["stuck"] = []
    report["missed"] = missed
    report["reached_contact"] = True
    if report.get("blocker") == "auth_wall":
        report["blocker"] = None

    pack_missed = [
        m for m in missed
        if not m.get("optional_miss")
        and m.get("automation_id") in {aid for aid, _, _ in fill_plan}
    ]
    required_empty = await _required_empty_on_page(page)
    # Second-chance: labeled empties we can answer from dummy map
    if required_empty and extras_values:
        try:
            await _fill_required_select_ones(page, extras_values, phase)
            # Email leftover by id
            for emp in list(required_empty):
                eid = str(emp.get("id") or "")
                lab = str(emp.get("label") or "")
                if "email" in eid.lower() or re.search(r"\bemail\b", lab, re.I):
                    email_val = extras_values.get(EMAIL)
                    if email_val:
                        r = await _fill_automation_id(
                            page, "contact_email", str(email_val), combobox=False
                        )
                        if _is_verified_fill(r):
                            filled.append(r)
                if (
                    "source" in eid.lower()
                    or re.search(
                        r"how (did|do) you (hear|learn)|where did you hear|"
                        r"hear about (us|this)|referral source",
                        lab,
                        re.I,
                    )
                ):
                    # Shared helper — never bare "Internet"
                    hr = await _fill_how_heard(page, extras_values)
                    if _is_verified_fill(hr):
                        filled.append(hr)
            required_empty = await _required_empty_on_page(page)
            phase["filled"] = filled
            report["filled"] = filled
        except Exception as e:
            phase.setdefault("errors", []).append(f"empty_sweep:{str(e)[:100]}")

    phase["required_empty_before_advance"] = required_empty
    report["required_empty_before_advance"] = required_empty

    # ChamPro gaps() snapshot before deciding ADVANCE
    try:
        from form_gaps import collect_form_gaps, merge_gaps_into_report, normalize_gaps

        pre_gaps = await collect_form_gaps(page)
        for emp in required_empty or []:
            pre_gaps.append(
                {
                    "label": str(emp.get("label") or emp.get("id") or "required")[:160],
                    "reason": "required_empty",
                    "automation_id": str(emp.get("id") or "")[:80],
                }
            )
        # Drop false-positive gaps for fields we already verified this phase
        verified_blob = " ".join(
            str(f.get("automation_id") or "")
            + " "
            + str(f.get("type") or "")
            + " "
            + str(f.get("readback") or "")
            for f in filled
            if _is_verified_fill(f)
        ).lower()
        filtered = []
        for g in normalize_gaps(pre_gaps):
            lab = (g.get("label") or "").lower()
            if re.search(
                r"how (did|do) you (hear|learn)|where did you hear|hear about (us|this)",
                lab,
            ) and (
                "how_heard" in verified_blob
                or "indeed" in verified_blob
                or "internet job board" in verified_blob
                or "item selected" in lab
            ):
                continue
            if (
                "employed" in lab
                and "previously" in lab
                and "worked_here" in verified_blob
            ):
                continue
            if "item selected" in lab and not lab.startswith("0 item"):
                continue
            filtered.append(g)
        merge_gaps_into_report(report, filtered)
        phase["gaps_before_advance"] = report.get("gaps_after_save") or []
    except Exception as e:
        phase.setdefault("errors", []).append(f"gaps_pre:{str(e)[:80]}")

    can_advance = not pack_missed and not required_empty
    if not can_advance:
        reason = (
            "pack_incomplete" if pack_missed else _advance_block_reason(required_empty)
        )
        phase["advance_blocked_reason"] = reason
        phase["next_clicks"] = []
        report["advance_blocked_reason"] = reason
        report["verdict"] = "FAIL"
        report["blocker"] = report.get("blocker") or "contact_incomplete"
        report["advanced_incomplete"] = False
        # Exhausted deterministic fills — do NOT ADVANCE; not "stuck" (we blocked on purpose)
        try:
            from page_progress import capture_step_fingerprint, note_advance_result, record_page_seen

            before = await capture_step_fingerprint(page)
            record_page_seen(report, before["fingerprint"], meta=before)
            note_advance_result(
                report,
                fingerprint_before=before["fingerprint"],
                fingerprint_after=before["fingerprint"],
                next_existed=False,
                advance_clicked=False,
            )
            phase["stuck_on_same_page"] = False
            report["stuck_on_same_page"] = False
        except Exception:
            report.setdefault("stuck_on_same_page", False)
        return phase

    # All required visible fields look filled — ADVANCE once (never FINAL)
    _note_advance = None
    _capture_fp = None
    before = {"fingerprint": ""}
    try:
        from page_progress import capture_step_fingerprint as _capture_fp
        from page_progress import note_advance_result as _note_advance

        before = await _capture_fp(page)
    except Exception:
        pass
    next_clicks = await _click_next_advance(page)
    phase["next_clicks"] = next_clicks
    report.setdefault("clicks", []).extend(next_clicks)
    advanced = any(c.get("action") == "clicked" for c in next_clicks)
    phase["advanced"] = advanced
    report["advanced"] = advanced

    # ATS2-011: same SPA settle as _gate_then_advance — contact Next often
    # leaves URL/title flat; a fixed 1.5s sleep falsely sticky-stuck and skipped C–E.
    if _note_advance is not None and before.get("fingerprint"):
        try:
            after, moved_dom = await _poll_wd_spa_after_advance(page, phase, before)
            progress = _note_advance(
                report,
                fingerprint_before=before["fingerprint"],
                fingerprint_after=after["fingerprint"],
                next_existed=True,
                advance_clicked=advanced,
            )
            phase["fingerprint_before"] = before["fingerprint"]
            phase["fingerprint_after"] = after["fingerprint"]
            phase["stuck_on_same_page"] = progress["stuck_on_same_page"]
            after = _clear_false_stuck_after_spa_move(
                report,
                phase,
                progress,
                before,
                after,
                phase.get("spa_dom_before") or {},
                moved_dom,
                advanced=advanced,
            )
            if progress["stuck_on_same_page"]:
                report["verdict"] = "FAIL"
        except Exception:
            try:
                await page.wait_for_timeout(400)
                if _capture_fp is not None:
                    after = await _capture_fp(page)
                    progress = _note_advance(
                        report,
                        fingerprint_before=before["fingerprint"],
                        fingerprint_after=after["fingerprint"],
                        next_existed=True,
                        advance_clicked=advanced,
                    )
                    phase["stuck_on_same_page"] = progress["stuck_on_same_page"]
                    if progress["stuck_on_same_page"]:
                        report["verdict"] = "FAIL"
            except Exception:
                pass
    else:
        await page.wait_for_timeout(400)
    validation = await _validation_banner_present(page)
    phase["validation_after_advance"] = validation
    report["validation_after_advance"] = validation
    # Post-Save gaps oracle (ChamPro): what Workday still flags after Save
    try:
        from form_gaps import collect_form_gaps, merge_gaps_into_report

        post_gaps = await collect_form_gaps(page)
        merge_gaps_into_report(report, post_gaps)
        phase["gaps_after_save"] = report.get("gaps_after_save") or []
        if post_gaps and (validation or report.get("stuck_on_same_page")):
            report["blocker"] = report.get("blocker") or "page_incomplete"
            report["verdict"] = "FAIL"
    except Exception as e:
        phase.setdefault("errors", []).append(f"gaps_post:{str(e)[:80]}")
    if validation:
        report["blocker"] = "validation_errors"
        report["verdict"] = "FAIL"
        report["advanced_incomplete"] = True
        await _shot(page, report)
        return phase

    # Contact page complete without validation warning
    report["advanced_incomplete"] = False
    if not report.get("stuck_on_same_page"):
        report["verdict"] = "SUCCESS"
    return phase


async def _automation_visible(page, automation_id: str) -> bool:
    loc = page.locator(f'[data-automation-id="{automation_id}"]').first
    try:
        return await loc.count() > 0 and await loc.is_visible(timeout=300)
    except Exception:
        return False


async def _wait_step(page, automation_id: str, timeout_ms: int = 12000) -> bool:
    """Poll for a Workday step container; exit as soon as it mounts."""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if await _automation_visible(page, automation_id):
            return True
        body = await _body_text(page, 2000)
        if _detect_hard_blocker(body, await page.title(), page.url):
            return False
        await page.wait_for_timeout(150)
    return await _automation_visible(page, automation_id)


def _advance_block_reason(required_empty: list[dict]) -> str:
    """Map page-complete empties to a stable advance_blocked_reason."""
    reasons = {str(r.get("reason") or "") for r in required_empty}
    if "currently_work_here_checked" in reasons:
        return "currently_work_here_checked"
    if reasons & {
        "empty_required_date_spin",
        "empty_required_date_display",
        "empty_required_date_field",
    }:
        return "required_dates_empty"
    return "required_fields_empty"


async def _all_date_spins_committed(page) -> bool:
    """True when every visible From/To month+year INPUT has digits (not placeholders)."""
    for mode in ("from", "to"):
        mons = await _list_date_inputs(page, "month", mode=mode)
        yrs = await _list_date_inputs(page, "year", mode=mode)
        if mode == "from" and not mons and not yrs:
            return False
        for loc in list(mons) + list(yrs):
            try:
                v = ((await loc.input_value()) or "").strip()
            except Exception:
                return False
            if not v or not any(c.isdigit() for c in v):
                return False
            if v.upper() in {"MM", "YYYY", "M", "Y"}:
                return False
    return True


async def _gate_then_advance(page, report: dict, phase: dict) -> bool:
    """ADVANCE once only when required visible fields are empty-free. Else FAIL.

    Prefer FAIL-before-ADVANCE. ``advanced_incomplete`` is True only when we
    actually clicked Next and then saw a validation banner (dishonest ADVANCE).
    Tracks step fingerprints so stuck-on-same-page cannot be reported as SUCCESS.
    """
    from page_progress import capture_step_fingerprint, note_advance_result

    report.setdefault("pages_seen", [])
    report.setdefault("advanced_count", 0)
    report.setdefault("stuck_on_same_page", False)

    before = await capture_step_fingerprint(page)
    required_empty = await _required_empty_on_page(page)
    # Workday date displays can lag or false-positive while INPUTs are committed.
    # If every From/To input has digits, drop date-* empties for this gate.
    date_reasons = {
        "empty_required_date_spin",
        "empty_required_date_display",
        "empty_required_date_field",
    }
    if required_empty and any(
        str(r.get("reason") or "") in date_reasons for r in required_empty
    ):
        try:
            if await _all_date_spins_committed(page):
                required_empty = [
                    r for r in required_empty
                    if str(r.get("reason") or "") not in date_reasons
                ]
                phase["date_gate_trusted_inputs"] = True
        except Exception as e:
            phase["date_gate_trust_error"] = str(e)[:120]
    phase["required_empty_before_advance"] = required_empty
    report["required_empty_before_advance"] = required_empty

    next_existed = False
    try:
        for sel in NEXT_BUTTON_SELECTORS:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible(timeout=400):
                next_existed = True
                break
    except Exception:
        pass

    if required_empty:
        reason = _advance_block_reason(required_empty)
        phase["advance_blocked_reason"] = reason
        report["advance_blocked_reason"] = reason
        report["blocker"] = report.get("blocker") or "page_incomplete"
        report["verdict"] = "FAIL"
        # Did NOT advance — honest FAIL, not stuck (Next still visible is expected)
        report["advanced_incomplete"] = False
        phase["advanced"] = False
        after = await capture_step_fingerprint(page)
        progress = note_advance_result(
            report,
            fingerprint_before=before["fingerprint"],
            fingerprint_after=after["fingerprint"],
            next_existed=False,
            advance_clicked=False,
        )
        phase["stuck_on_same_page"] = progress["stuck_on_same_page"]
        phase["fingerprint_before"] = before["fingerprint"]
        phase["fingerprint_after"] = after["fingerprint"]
        return False
    next_clicks = await _click_next_advance(page)
    phase["next_clicks"] = next_clicks
    report.setdefault("clicks", []).extend(next_clicks)
    advanced = any(c.get("action") == "clicked" for c in next_clicks)
    phase["advanced"] = advanced
    report["advanced"] = advanced
    # Workday SPA: URL/title often unchanged — poll for step container / fingerprint
    after, moved_dom = await _poll_wd_spa_after_advance(page, phase, before)
    before_dom = phase.get("spa_dom_before") or {}

    progress = note_advance_result(
        report,
        fingerprint_before=before["fingerprint"],
        fingerprint_after=after["fingerprint"],
        next_existed=next_existed or advanced,
        advance_clicked=advanced,
    )
    phase["fingerprint_before"] = before["fingerprint"]
    phase["fingerprint_after"] = after["fingerprint"]
    phase["stuck_on_same_page"] = progress["stuck_on_same_page"]
    after = _clear_false_stuck_after_spa_move(
        report,
        phase,
        progress,
        before,
        after,
        before_dom,
        moved_dom,
        advanced=advanced,
    )
    if progress["stuck_on_same_page"]:
        report["verdict"] = "FAIL"
    validation = await _validation_banner_present(page)
    phase["validation_after_advance"] = validation
    report["validation_after_advance"] = validation
    if validation:
        report["blocker"] = "validation_errors"
        report["verdict"] = "FAIL"
        report["advanced_incomplete"] = True
        await _shot(page, report)
        return False
    # Successful ADVANCE — clear stale page_incomplete from earlier phases
    if report.get("blocker") == "page_incomplete":
        report["blocker"] = None
    if report.get("advance_blocked_reason") in (
        "required_fields_empty",
        "required_dates_empty",
        "experience_dates_incomplete",
        "currently_work_here_checked",
    ):
        report["advance_blocked_reason"] = None
    report["advanced_incomplete"] = False
    return advanced and not progress["stuck_on_same_page"]


def _parse_month_year(date_str: str | None) -> tuple[str, str]:
    """Return (MM, YYYY) from resume-ish dates like 'Jan 2024' or '03/2022'."""
    if not date_str:
        return "", ""
    s = str(date_str).strip()
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "sept": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    m = re.search(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)[a-z]*\.?\s+(\d{4})",
        s,
        re.I,
    )
    if m:
        key = m.group(1).lower()
        if key.startswith("sept"):
            key = "sept"
        else:
            key = key[:3]
        return months.get(key, "01"), m.group(2)
    m = re.search(r"(\d{1,2})\s*/\s*(\d{4})", s)
    if m:
        return f"{int(m.group(1)):02d}", m.group(2)
    m = re.search(r"(\d{4})", s)
    if m:
        return "01", m.group(1)
    return "", ""


def _normalize_month_digits(month: str) -> str:
    s = str(month or "").strip()
    if s.isdigit():
        return f"{int(s):02d}"
    return s


def _date_spin_matches(got: str, want: str, *, kind: str) -> bool:
    """True when readback is a real committed value (not MM/YYYY placeholder)."""
    raw = (got or "").strip()
    upper = raw.upper()
    if not raw or upper in {
        "MM", "M", "YYYY", "YY", "DD", "D", "MONTH", "YEAR",
    }:
        return False
    digits = re.sub(r"\D", "", raw)
    want_d = re.sub(r"\D", "", str(want or ""))
    if not digits or not want_d:
        return False
    if kind == "month":
        try:
            return int(digits) == int(want_d)
        except ValueError:
            return False
    # year: full year must appear
    return want_d in digits or digits == want_d


async def _date_input_name(loc) -> str:
    """Accessible name for a Workday date spin input (aria-label or <label>)."""
    try:
        return await loc.evaluate(
            """(el) => {
              const al = (el.getAttribute('aria-label') || '').trim();
              if (al) return al;
              if (el.labels && el.labels.length) {
                return (el.labels[0].textContent || '').trim();
              }
              const id = el.getAttribute('aria-labelledby');
              if (id) {
                const parts = id.split(/\\s+/).map((x) => {
                  const n = document.getElementById(x);
                  return n ? (n.textContent || '') : '';
                });
                const t = parts.join(' ').trim();
                if (t) return t;
              }
              const wrap = el.closest('[data-automation-id*="formField"]');
              if (wrap) {
                // Prefer a label that points at this input, not the whole section text
                const labs = wrap.querySelectorAll('label');
                for (const lab of labs) {
                  if (lab.htmlFor && el.id && lab.htmlFor === el.id) {
                    return (lab.textContent || '').trim();
                  }
                  if (lab.contains(el)) return (lab.textContent || '').trim();
                }
                // Last resort: first short label in the formField (not whole WE block)
                const lab = wrap.querySelector('label, legend');
                if (lab) {
                  const t = (lab.textContent || '').trim();
                  if (t.length <= 40) return t;
                }
              }
              return (el.placeholder || '').trim();
            }"""
        )
    except Exception:
        try:
            return ((await loc.get_attribute("aria-label")) or "").strip()
        except Exception:
            return ""


async def _list_date_inputs(page, which: str, *, mode: str) -> list:
    """Return visible month/year INPUT locators filtered by From/To name.

    ``which``: "month" | "year"
    ``mode``: "from" | "to" | "any"
    Cisco: label text "Month — From*" vs plain "Month" for To (often no aria-label).
    """
    aid = (
        "dateSectionMonth-input" if which == "month" else "dateSectionYear-input"
    )
    locs = page.locator(f'[data-automation-id="{aid}"]:visible')
    try:
        n = await locs.count()
    except Exception:
        return []
    named: list[tuple] = []
    for i in range(n):
        loc = locs.nth(i)
        name = (await _date_input_name(loc) or "").lower()
        named.append((loc, name))
    any_from = any("from" in name for _, name in named)
    out = []
    for loc, name in named:
        has_from = "from" in name
        if mode == "from":
            if any_from and not has_from:
                continue
            if not any_from:
                # No From tags at all — treat first half as From by DOM order later
                pass
        if mode == "to":
            if has_from:
                continue
            if any_from:
                pass  # non-From with From present => To
            else:
                continue  # ambiguous; caller may use mode=any
        out.append(loc)
    # If mode=from and nothing matched but inputs exist without names, use even indices
    if mode == "from" and not out and named and not any_from:
        out = [loc for i, (loc, _) in enumerate(named) if i % 2 == 0]
    if mode == "to" and not out and named and not any_from:
        out = [loc for i, (loc, _) in enumerate(named) if i % 2 == 1]
    return out


async def _paired_display_for_input(inp):
    """Nearest *-display for a dateSection*-input (optional)."""
    try:
        aid = (await inp.get_attribute("data-automation-id")) or ""
        disp_aid = aid.replace("-input", "-display")
        if disp_aid == aid:
            return None
        # Walk up a few ancestors; avoid grabbing a distant From input's display
        for xpath in (
            "xpath=ancestor::*[position()<=3]",
            "xpath=..",
        ):
            group = inp.locator(xpath)
            disp = group.locator(f'[data-automation-id="{disp_aid}"]').first
            if await disp.count():
                return disp
    except Exception:
        pass
    return None


async def _read_date_spin_pair(
    page, nth: int, *, from_only: bool = False, to_only: bool = False
) -> dict:
    """Read month/year from the nth From- or To-filtered INPUT (+ display)."""
    mode = "from" if from_only else ("to" if to_only else "any")
    out = {
        "month_input": "",
        "month_display": "",
        "year_input": "",
        "year_display": "",
        "mode": mode,
        "nth": nth,
    }
    mons = await _list_date_inputs(page, "month", mode=mode)
    yrs = await _list_date_inputs(page, "year", mode=mode)
    if nth < len(mons):
        try:
            out["month_input"] = ((await mons[nth].input_value()) or "").strip()
        except Exception:
            pass
        disp = await _paired_display_for_input(mons[nth])
        if disp is not None:
            try:
                out["month_display"] = ((await disp.inner_text()) or "").strip()
            except Exception:
                pass
    if nth < len(yrs):
        try:
            out["year_input"] = ((await yrs[nth].input_value()) or "").strip()
        except Exception:
            pass
        disp = await _paired_display_for_input(yrs[nth])
        if disp is not None:
            try:
                out["year_display"] = ((await disp.inner_text()) or "").strip()
            except Exception:
                pass
    return out


async def _date_spin_verify(
    page,
    month: str,
    year: str,
    *,
    nth: int,
    from_only: bool = False,
    to_only: bool = False,
) -> tuple[bool, dict]:
    """Require committed digits on the filtered input (display when present)."""
    rb = await _read_date_spin_pair(
        page, nth, from_only=from_only, to_only=to_only
    )
    mon_ok = True
    if month:
        mon_ok = _date_spin_matches(rb["month_input"], month, kind="month")
        if rb["month_display"]:
            mon_ok = mon_ok and _date_spin_matches(
                rb["month_display"], month, kind="month"
            )
    yr_ok = True
    if year:
        yr_ok = _date_spin_matches(rb["year_input"], year, kind="year")
        if rb["year_display"]:
            yr_ok = yr_ok and _date_spin_matches(
                rb["year_display"], year, kind="year"
            )
    return mon_ok and yr_ok, rb


async def _type_digits_into(page, loc, digits: str) -> str:
    """Click/focus → clear → type digits → Tab (never Enter). No JS .value.

    Skips clear/retype when the spin already shows the intended digits.
    """
    try:
        await loc.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    # SKIP thrash: already-correct date spin
    try:
        raw0 = (await loc.input_value()) or ""
    except Exception:
        try:
            raw0 = (await loc.inner_text()) or ""
        except Exception:
            raw0 = ""
    dig0 = re.sub(r"\D", "", raw0)
    want = re.sub(r"\D", "", str(digits))
    if dig0 and want and (dig0 == want or dig0.endswith(want) or want.endswith(dig0)):
        return "already_correct_skip"
    await loc.click(timeout=2500, force=True)
    await page.wait_for_timeout(60)
    cleared = False
    try:
        await loc.evaluate("el => { el.focus(); el.select(); }")
        cleared = True
    except Exception:
        pass
    if not cleared:
        # Try both Meta+a and Control+a — Linux headless often no-ops Meta+a
        # without raising, so do not break after the first "success".
        for chord in ("Meta+a", "Control+a"):
            try:
                await page.keyboard.press(chord)
                cleared = True
            except Exception:
                continue
    try:
        await page.keyboard.press("Backspace")
    except Exception:
        pass
    await page.wait_for_timeout(40)
    technique = "press_sequentially"
    try:
        await loc.press_sequentially(str(digits), delay=40)
    except Exception:
        technique = "keyboard_type"
        await page.keyboard.type(str(digits), delay=40)
    try:
        await page.keyboard.press("Tab")
    except Exception:
        try:
            await loc.blur()
        except Exception:
            pass
    await page.wait_for_timeout(150)
    return technique


async def _arrow_fill_spin(page, loc, target: int, *, max_steps: int = 24) -> bool:
    """Fallback: ArrowUp/ArrowDown on a focused spin until readback matches."""
    try:
        await loc.click(timeout=2000, force=True)
    except Exception:
        return False
    await page.wait_for_timeout(40)
    for _ in range(max_steps):
        try:
            raw = ""
            try:
                raw = (await loc.input_value()) or ""
            except Exception:
                raw = (await loc.inner_text()) or ""
            digits = re.sub(r"\D", "", raw)
            cur = int(digits) if digits else 0
            if cur == target:
                await page.keyboard.press("Tab")
                return True
            await page.keyboard.press("ArrowUp" if cur < target else "ArrowDown")
            await page.wait_for_timeout(30)
        except Exception:
            return False
    return False


async def _fill_date_spin(
    page,
    scope,
    month: str,
    year: str,
    *,
    nth: int = 0,
    from_only: bool = False,
    to_only: bool = False,
) -> dict:
    """Fill Workday month/year via keyboard on aria-filtered INPUTs.

    Target ``dateSectionMonth-input`` / ``Year-input`` by aria-label
    (\"Month — From*\" vs \"Month\"). Optionally click paired ``*-display``.
    Never Enter; never JS .value alone.
    """
    detail: dict = {
        "status": "missed",
        "verified": False,
        "month": month,
        "year": year,
        "nth": nth,
        "from_only": from_only,
        "to_only": to_only,
    }
    if not month and not year:
        detail["reason"] = "no_date"
        return detail

    month_n = _normalize_month_digits(month) if month else ""
    year_n = str(year or "").strip()
    detail["month"] = month_n
    detail["year"] = year_n
    mode = "from" if from_only else ("to" if to_only else "any")

    try:
        await page.wait_for_timeout(250)
        mons = await _list_date_inputs(page, "month", mode=mode)
        yrs = await _list_date_inputs(page, "year", mode=mode)
        detail["pool_month"] = len(mons)
        detail["pool_year"] = len(yrs)

        if nth >= len(mons) and nth >= len(yrs):
            detail["reason"] = "date_inputs_not_found"
            return detail

        mon_inp = mons[nth] if nth < len(mons) else None
        yr_inp = yrs[nth] if nth < len(yrs) else None

        async def _target_for(inp):
            disp = await _paired_display_for_input(inp) if inp is not None else None
            if disp is not None:
                try:
                    if await disp.is_visible(timeout=400):
                        return disp, "display"
                except Exception:
                    pass
            return inp, "input"

        month_variants = [month_n]
        if month_n.startswith("0") and len(month_n) == 2:
            month_variants.append(str(int(month_n)))

        ok = False
        techniques: list[str] = []
        for mv in month_variants:
            techniques = []
            if month_n and mon_inp is not None:
                tgt, kind = await _target_for(mon_inp)
                techniques.append(
                    f"month:{kind}:{await _type_digits_into(page, tgt, mv)}"
                )
            if year_n and yr_inp is not None:
                tgt, kind = await _target_for(yr_inp)
                techniques.append(
                    f"year:{kind}:{await _type_digits_into(page, tgt, year_n)}"
                )
            await page.wait_for_timeout(200)
            ok, rb = await _date_spin_verify(
                page, month_n, year_n, nth=nth, from_only=from_only, to_only=to_only
            )
            detail["readback"] = rb
            detail["techniques"] = list(techniques)
            if ok:
                break

        if not ok and mon_inp is not None:
            techniques = []
            if month_n:
                techniques.append(
                    f"month:input_retry:{await _type_digits_into(page, mon_inp, month_n)}"
                )
            if year_n and yr_inp is not None:
                techniques.append(
                    f"year:input_retry:{await _type_digits_into(page, yr_inp, year_n)}"
                )
            await page.wait_for_timeout(200)
            ok, rb = await _date_spin_verify(
                page, month_n, year_n, nth=nth, from_only=from_only, to_only=to_only
            )
            detail["readback"] = rb
            detail["techniques"] = list(techniques)

        if not ok and mon_inp is not None:
            techniques = []
            try:
                target_m = int(re.sub(r"\D", "", month_n) or "0")
                if target_m and await _arrow_fill_spin(
                    page, mon_inp, target_m, max_steps=14
                ):
                    techniques.append("month:arrow")
            except Exception:
                pass
            if year_n and yr_inp is not None:
                techniques.append(
                    f"year:arrow_fallback_type:"
                    f"{await _type_digits_into(page, yr_inp, year_n)}"
                )
            await page.wait_for_timeout(200)
            ok, rb = await _date_spin_verify(
                page, month_n, year_n, nth=nth, from_only=from_only, to_only=to_only
            )
            detail["readback"] = rb
            if techniques:
                detail["techniques"] = list(techniques)

        detail["mode"] = "date_spin"
        detail["status"] = "filled" if ok else "missed"
        detail["verified"] = ok
        if not ok:
            detail["reason"] = "date_readback_empty"
    except Exception as e:
        detail["error"] = str(e)[:160]
    return detail


async def _phase_c_experience(page, values: dict, report: dict) -> dict:
    """Fill myExperiencePage: resume upload + first work entry from dummy resume.

    Cisco wd5+ (and similar): Work Experience starts empty — only an Add
    control exists until clicked. Never ADVANCE with required empty fields.
    """
    phase: dict = {
        "name": "C_experience",
        "present": False,
        "filled": [],
        "missed": [],
        "advanced": False,
    }
    present = await _wait_step(page, "myExperiencePage", timeout_ms=8000)
    if not present:
        # Some tenants land on experience without that exact id (Cisco: Add + upload)
        for _ in range(12):
            present = (
                await _automation_visible(page, "workExperience-1")
                or await _automation_visible(page, "file-upload-input-ref")
                or await _automation_visible(page, "workExperienceSection")
                or await _automation_visible(page, "workExperiencePanel")
                or await _automation_visible(page, "myExperiencePage")
            )
            if present:
                break
            try:
                body = await _body_text(page, 3000)
                low = body.lower()
                if "my experience" in low and (
                    "work experience" in low or "resume" in low or "select files" in low
                ):
                    present = True
                    break
            except Exception:
                pass
            await page.wait_for_timeout(200)
    phase["present"] = present
    if not present:
        phase["skipped"] = "experience_page_absent"
        return phase

    resume_path = values.get("_resume_pdf") or str(DUMMY_PDF)
    upload_done = False
    upload = page.locator(
        '[data-automation-id="file-upload-input-ref"], '
        'input[type="file"]'
    ).first
    try:
        if await upload.count() == 0:
            # Cisco: "Select files" / Drop files may reveal the input
            for sel_txt in (
                'button:has-text("Select files")',
                'button:has-text("Select Files")',
                'text=Select files',
                '[data-automation-id="file-upload-select-button"]',
                'button:has-text("Upload")',
            ):
                sel = page.locator(sel_txt).first
                if not await sel.count():
                    continue
                try:
                    if not await sel.is_visible(timeout=600):
                        continue
                except Exception:
                    continue
                try:
                    async with page.expect_file_chooser(timeout=4000) as fc_info:
                        await sel.click(timeout=3000)
                    chooser = await fc_info.value
                    await chooser.set_files(str(resume_path))
                    await page.wait_for_timeout(1000)
                    verified = False
                    readback = Path(str(resume_path)).name
                    try:
                        info = await upload.evaluate(
                            """(el) => {
                              const files = el && el.files;
                              if (!files || files.length < 1) return {ok: false, name: ''};
                              return {ok: true, name: files[0].name || ''};
                            }"""
                        ) if await upload.count() else {}
                        if not info:
                            n_files = await page.locator('input[type="file"]').count()
                            for fi in range(min(n_files, 6)):
                                inp = page.locator('input[type="file"]').nth(fi)
                                info = await inp.evaluate(
                                    """(el) => {
                                      const files = el && el.files;
                                      if (!files || files.length < 1) return {ok: false, name: ''};
                                      return {ok: true, name: files[0].name || ''};
                                    }"""
                                )
                                if (info or {}).get("ok"):
                                    break
                        verified = bool((info or {}).get("ok") and (info or {}).get("name"))
                        if (info or {}).get("name"):
                            readback = str(info["name"])[:120]
                    except Exception:
                        verified = False
                    phase["filled"].append({
                        "automation_id": "file-upload-select-files",
                        "status": "filled" if verified else "missed",
                        "mode": "file_chooser",
                        "type": "RESUME_UPLOAD",
                        "value": str(resume_path),
                        "readback": readback,
                        "verified": verified,
                        "ok": verified,
                    })
                    upload_done = verified
                    if not verified:
                        phase["missed"].append({
                            "automation_id": "file-upload-select-files",
                            "status": "missed",
                            "reason": "resume_unverified",
                            "verified": False,
                        })
                    break
                except Exception:
                    continue
        if not upload_done and await upload.count():
            await upload.set_input_files(str(resume_path))
            await page.wait_for_timeout(1000)
            verified = False
            readback = Path(str(resume_path)).name
            try:
                info = await upload.evaluate(
                    """(el) => {
                      const files = el && el.files;
                      if (!files || files.length < 1) return {ok: false, name: ''};
                      return {ok: true, name: files[0].name || ''};
                    }"""
                )
                verified = bool((info or {}).get("ok") and (info or {}).get("name"))
                if (info or {}).get("name"):
                    readback = str(info["name"])[:120]
            except Exception:
                verified = False
            if not verified:
                # Retry once
                try:
                    await upload.set_input_files(str(resume_path))
                    await page.wait_for_timeout(600)
                    info = await upload.evaluate(
                        """(el) => {
                          const files = el && el.files;
                          if (!files || files.length < 1) return {ok: false, name: ''};
                          return {ok: true, name: files[0].name || ''};
                        }"""
                    )
                    verified = bool((info or {}).get("ok") and (info or {}).get("name"))
                    if (info or {}).get("name"):
                        readback = str(info["name"])[:120]
                except Exception:
                    pass
            phase["filled"].append({
                "automation_id": "file-upload-input-ref",
                "status": "filled" if verified else "missed",
                "mode": "file",
                "type": "RESUME_UPLOAD",
                "value": str(resume_path),
                "readback": readback,
                "verified": verified,
                "ok": verified,
            })
            upload_done = verified
            if not verified:
                phase["missed"].append({
                    "automation_id": "file-upload-input-ref",
                    "status": "missed",
                    "reason": "resume_unverified",
                    "verified": False,
                })
    except Exception as e:
        phase["missed"].append({
            "automation_id": "file-upload-input-ref",
            "status": "missed",
            "reason": "upload_error",
            "error": str(e)[:160],
            "verified": False,
        })

    positions: list[dict] = []
    try:
        from resume_parser import parse_resume

        parsed = parse_resume(resume_path)
        positions = list(parsed.get("positions") or [])[:1]
    except Exception as e:
        phase.setdefault("errors", []).append({"resume_parse": str(e)[:120]})

    if not positions:
        # Minimal fallback from dummy values
        positions = [{
            "title": values.get(CURRENT_TITLE) or "Applied AI/ML Analyst",
            "company": values.get(CURRENT_COMPANY) or "Example Corp",
            "location": values.get(ADDRESS_CITY) or "Springfield, IL",
            "start": "Jan 2022",
            "end": "Present",
        }]

    async def _click_add_work(idx: int):
        """Click Add for empty Work Experience (Cisco) or Add Another."""
        headed = page.locator(
            '[data-automation-id="workExperienceSection"], '
            '[data-automation-id="workExperiencePanel"], '
            'h2:has-text("Work Experience"), '
            'h3:has-text("Work Experience"), '
            'div:has-text("Work Experience")'
        ).first
        add_candidates = [
            '[data-automation-id="workExperienceSection"] button:has-text("Add")',
            '[data-automation-id="workExperiencePanel"] button:has-text("Add")',
            'button[aria-label*="Add Work" i]',
            'button[aria-label*="Add Another" i]',
            'button:has-text("Add Work Experience")',
            'button:has-text("Add Another")',
            'button[data-automation-id="Add"]',
        ]
        add = None
        if await headed.count():
            for sel in (
                'button:has-text("Add Work Experience")',
                'button:has-text("Add Another")',
                'button:has-text("Add")',
                'button[aria-label*="Add" i]',
            ):
                local_add = headed.locator(sel).first
                try:
                    if await local_add.count() and await local_add.is_visible(timeout=400):
                        add = local_add
                        break
                except Exception:
                    continue
        if add is None:
            for sel in add_candidates:
                loc = page.locator(sel).first
                try:
                    if await loc.count() and await loc.is_visible(timeout=400):
                        add = loc
                        break
                except Exception:
                    continue
        if add is None:
            # Role-based fallback
            try:
                role_add = page.get_by_role(
                    "button", name=re.compile(r"^\s*Add( Another)?( Work)?", re.I)
                ).first
                if await role_add.count() and await role_add.is_visible(timeout=400):
                    add = role_add
            except Exception:
                pass
        if add is None:
            return False
        try:
            text = (await add.inner_text()).strip() or (
                await add.get_attribute("aria-label") or "Add"
            )
            resolved = await gate_locator_click(
                add, intent_label=text, allow_kinds=NAV_KINDS
            )
            if not resolved.get("ok"):
                return False
            await add.click(timeout=3000)
            await page.wait_for_timeout(1100)
            # Wait for new row
            for _ in range(6):
                scope = page.locator(
                    f'[data-automation-id="workExperience-{idx}"]'
                ).first
                if await scope.count():
                    return True
                any_row = page.locator('[data-automation-id^="workExperience-"]')
                if await any_row.count() >= idx:
                    return True
                # form fields appeared without numbered id
                if await page.locator(
                    '[data-automation-id="jobTitle"], '
                    'input[name*="jobTitle" i], '
                    'input[aria-label*="Job Title" i]'
                ).count():
                    return True
                await page.wait_for_timeout(400)
            return True  # clicked; fill will probe
        except Exception:
            return False

    for idx, pos in enumerate(positions, start=1):
        scope = page.locator(f'[data-automation-id="workExperience-{idx}"]').first
        if await scope.count() == 0:
            await _click_add_work(idx)
            scope = page.locator(f'[data-automation-id="workExperience-{idx}"]').first
            if await scope.count() == 0:
                scope = page.locator(
                    '[data-automation-id^="workExperience-"]'
                ).nth(idx - 1)
            if await scope.count() == 0:
                scope = page.locator(
                    '[data-automation-id*="workExperience"]'
                ).nth(idx - 1)
            if await scope.count() == 0:
                # Last resort: page-level job title fields (single empty form)
                scope = page
        page_scope = scope is page
        if (not page_scope) and await scope.count() == 0:
            phase["missed"].append({
                "automation_id": f"workExperience-{idx}",
                "status": "missed",
                "reason": "not_in_dom",
                "verified": False,
            })
            continue
        for aid, key in (
            ("jobTitle", "title"),
            ("company", "company"),
            ("location", "location"),
        ):
            val = (pos.get(key) or "").strip()
            if not val:
                continue
            # Prefer scoped automation id
            if page_scope:
                loc = page.locator(f'[data-automation-id="{aid}"]').nth(idx - 1)
            else:
                loc = scope.locator(f'[data-automation-id="{aid}"]').first
            try:
                if await loc.count() == 0:
                    loc = page.locator(f'[data-automation-id="{aid}"]').nth(idx - 1)
                if await loc.count() == 0:
                    # aria-label fallbacks (Cisco / adventure wrappers)
                    labels = {
                        "jobTitle": r"Job Title",
                        "company": r"Company",
                        "location": r"Location",
                    }
                    loc = page.get_by_label(
                        re.compile(labels.get(aid, aid), re.I)
                    ).nth(idx - 1)
                if await loc.count() == 0:
                    phase["missed"].append({
                        "automation_id": aid,
                        "status": "missed",
                        "reason": "not_in_dom",
                        "verified": False,
                    })
                    continue
                inner = loc.locator("input, textarea").first
                target = inner if await inner.count() else loc
                await target.click(timeout=3000)
                await target.fill(val, timeout=4000)
                phase["filled"].append({
                    "automation_id": f"workExperience-{idx}/{aid}",
                    "status": "filled",
                    "value": val,
                    "verified": True,
                    "mode": "fill",
                })
            except Exception as e:
                phase["missed"].append({
                    "automation_id": f"workExperience-{idx}/{aid}",
                    "status": "missed",
                    "reason": "fill_error",
                    "error": str(e)[:120],
                    "verified": False,
                })
        start_m, start_y = _parse_month_year(pos.get("start") or pos.get("start_date"))
        if not start_y:
            # Resume parser sometimes omits dates — use deterministic dummy fallback
            start_m, start_y = "01", "2022"
            phase.setdefault("date_fallbacks", []).append(f"workExperience-{idx}/start")
        end_raw = pos.get("end") or pos.get("end_date") or ""
        end_m, end_y = _parse_month_year(end_raw)
        is_present = bool(re.search(r"present|current|now", str(end_raw), re.I)) or not end_raw

        # Cisco: currentlyWorkHere force-check sets DOM.checked but React still
        # requires To. Prefer filling From+To; leave Present unchecked so To
        # spins stay enabled and bind via the same keyboard path as From.
        try:
            boxes = page.locator('input[name="currentlyWorkHere"]')
            n_box = await boxes.count()
            for bi in range(n_box):
                b = boxes.nth(bi)
                try:
                    if await b.is_checked():
                        await b.uncheck(timeout=1500, force=True)
                except Exception:
                    try:
                        if await b.is_checked():
                            await b.click(timeout=1500, force=True)
                    except Exception:
                        pass
        except Exception:
            pass

        if page_scope:
            start_scope = page
        else:
            start_scope = scope.locator(
                '[data-automation-id="formField-startDate"], '
                'div:has([data-automation-id="dateSectionMonth-input"])'
            ).first
            if await start_scope.count() == 0:
                start_scope = scope
        start_nth = idx - 1

        dr = await _fill_date_spin(
            page, start_scope, start_m, start_y, nth=start_nth, from_only=True
        )
        dr["automation_id"] = f"workExperience-{idx}/startDate"
        (phase["filled"] if dr.get("verified") else phase["missed"]).append(dr)

        # Always fill To (distinct year so readback cannot alias From)
        if not end_y or is_present:
            end_m = end_m or start_m or "06"
            try:
                sy = int(start_y or "2022")
                end_y = str(max(sy + 1, 2023))
            except Exception:
                end_y = "2023"
            end_m = end_m or "06"
            phase.setdefault("date_fallbacks", []).append(
                f"workExperience-{idx}/end"
            )
        if page_scope:
            end_scope = page
        else:
            end_scope = scope.locator(
                '[data-automation-id="formField-endDate"]'
            ).first
            if await end_scope.count() == 0:
                end_scope = scope
        to_pool = await _list_date_inputs(page, "month", mode="to")
        to_nth = start_nth if start_nth < len(to_pool) else max(0, len(to_pool) - 1)
        dr2 = await _fill_date_spin(
            page,
            end_scope,
            end_m or "06",
            end_y,
            nth=to_nth,
            from_only=False,
            to_only=True,
        )
        dr2["automation_id"] = f"workExperience-{idx}/endDate"
        dr2["to_pool"] = len(to_pool)
        if dr2.get("verified"):
            phase["filled"].append(dr2)
        else:
            phase["missed"].append(dr2)

    # Education (school/degree) when section present — often required before ADVANCE
    try:
        await _fill_education_section(page, values, phase)
    except Exception as e:
        phase.setdefault("errors", []).append(f"education:{str(e)[:100]}")

    # Any remaining required Select Ones on experience page (education level, etc.)
    try:
        await _fill_required_select_ones(page, values, phase)
    except Exception as e:
        phase.setdefault("errors", []).append(f"select_ones:{str(e)[:100]}")

    # Re-verify From+To after all row mutations
    await page.wait_for_timeout(250)
    for item in list(phase["filled"]):
        aid = str(item.get("automation_id") or "")
        if item.get("mode") != "date_spin":
            continue
        is_start = "startDate" in aid
        is_end = "endDate" in aid
        if not is_start and not is_end:
            continue
        nth = int(item.get("nth") or 0)
        month = str(item.get("month") or "")
        year = str(item.get("year") or "")
        ok, rb = await _date_spin_verify(
            page, month, year, nth=nth, from_only=is_start, to_only=is_end
        )
        item["readback_final"] = rb
        if ok:
            continue
        retry = await _fill_date_spin(
            page, page, month, year, nth=nth, from_only=is_start, to_only=is_end
        )
        item["retry"] = {
            "verified": retry.get("verified"),
            "readback": retry.get("readback"),
            "techniques": retry.get("techniques"),
            "reason": retry.get("reason"),
        }
        if retry.get("verified"):
            item["verified"] = True
            item["status"] = "filled"
            item["readback"] = retry.get("readback")
        else:
            item["verified"] = False
            item["status"] = "missed"
            item["reason"] = "date_cleared_after_fill"
            phase["filled"].remove(item)
            phase["missed"].append(item)

    # Role Description is free-text — never invent. Leave empty; do NOT ADVANCE
    # if required dates/titles still missing (gate handles required_empty).
    report.setdefault("filled", []).extend(phase["filled"])
    report.setdefault("missed", []).extend(phase["missed"])
    # If start/end dates still missed, OR Present left checked — block ADVANCE
    date_misses = [
        m for m in phase["missed"]
        if (
            "startDate" in str(m.get("automation_id") or "")
            or "endDate" in str(m.get("automation_id") or "")
        )
        and not m.get("optional_miss")
    ]
    present_checked = [
        f for f in phase["filled"]
        if "currentlyWorkHere" in str(f.get("automation_id") or "")
        and f.get("mode") == "check"
    ]
    if date_misses or present_checked:
        reason = (
            "currently_work_here_checked" if present_checked and not date_misses
            else "experience_dates_incomplete"
        )
        phase["advance_blocked_reason"] = reason
        report["advance_blocked_reason"] = reason
        report["blocker"] = report.get("blocker") or "page_incomplete"
        report["verdict"] = "FAIL"
        report["advanced_incomplete"] = False
        phase["advanced"] = False
        if present_checked:
            phase["present_checked_blocked"] = True
        return phase
    await _gate_then_advance(page, report, phase)
    return phase


async def _fill_eeo_combobox(page, automation_id: str, value: str) -> dict:
    """Decline-oriented EEO combobox fill (dummy Decline values only)."""
    candidates = [value]
    low = (value or "").lower()
    if "decline" in low:
        for alt in (
            "Decline To Self Identify",
            "Decline to self identify",
            "I don't wish to answer",
            "Prefer not to say",
            "I do not want to answer",
        ):
            if alt.lower() not in {c.lower() for c in candidates}:
                candidates.append(alt)
    last = {
        "automation_id": automation_id,
        "status": "missed",
        "reason": "not_attempted",
        "verified": False,
    }
    for cand in candidates:
        last = await _fill_automation_id(page, automation_id, cand, combobox=True)
        if _is_verified_fill(last):
            last["status"] = "filled"
            return last
        if last.get("reason") in ("not_in_dom", "not_visible"):
            return last
    return last


def _dummy_answer_for_wd_label(label: str, values: dict | None = None) -> list[str]:
    """Safe dummy answers for Workday Select One / required comboboxes.

    Never invent EEO demographics (Decline only). Never invent essays.
    Returns ordered candidates for click→type→option matching.
    """
    lab = (label or "").lower()
    values = values or {}
    # Essays / open narrative — do not invent
    if re.search(
        r"\b(essay|describe|explain|tell us|why do you want|cover letter|"
        r"additional (information|comments)|motivation)\b",
        lab,
    ):
        return []
    # EEO / demographic — Decline only
    if re.search(
        r"\b(gender|sex|hispanic|latino|ethnicity|race|veteran|disability|"
        r"lgbt|sexual orientation|gender identity)\b",
        lab,
    ):
        return [
            "Decline to self identify",
            "Decline To Self Identify",
            "I do not want to answer",
            "Prefer not to say",
            "Decline",
        ]
    # Work authorization → Yes (dummy is US-authorized)
    if re.search(r"authorized to work|work authorization|legally authorized", lab):
        return ["Yes", "I am authorized", "Authorized"]
    # Sponsorship / conviction / prior worker conflicts → No
    if re.search(
        r"sponsor|visa|conviction|felony|criminal|export control|non-compet|"
        r"conflict of interest|relative.*(work|employ)|previously (employed|worked)",
        lab,
    ):
        return ["No", "No, I do not", "I do not require sponsorship"]
    if re.search(
        r"how (did|do) you (hear|learn)|where did you hear|"
        r"hear about (us|this)|referral source|\bsource--source\b",
        lab,
    ):
        return _how_heard_candidates(values)
    # Age gate — dummy is 18+ (DUMMY_PROFILE.standard_screening_answers).
    # ATS3-010: never fall through to "No" when Yes labels miss.
    if re.search(r"over\s*18|18\s*years|at least 18|age of majority", lab):
        return ["Yes", "I am 18 or older", "18 or older", "Yes, I am"]
    if re.search(r"school|universit|college|institution", lab):
        school = (values.get(SCHOOL) or "University of Alabama, Tuscaloosa").strip()
        return [school, "University of Alabama, Tuscaloosa"]
    if re.search(
        r"degree|qualification|education level|level of education|"
        r"highest.*(education|degree)|education completed",
        lab,
    ):
        deg = (values.get(DEGREE) or "Master's Degree").strip()
        # Workday often wants level, not "M.S., Example Studies"
        return [
            "Master's Degree",
            "Masters",
            "Master's",
            "Bachelor's Degree",
            deg,
            "Master of Science",
        ]
    if re.search(r"phone.*type|device type", lab):
        return ["Mobile", "Home", "Cell"]
    if re.search(r"country", lab) and "region" not in lab:
        return [str(values.get(ADDRESS_COUNTRY) or "United States"), "United States"]
    if re.search(r"state|province|region", lab):
        st = str(values.get(ADDRESS_STATE) or "Illinois")
        return [_expand_state_value(st)[0], st, "Illinois"]
    # ATS-016: relocation / commute willingness → Yes (shared policy), not catch-all No
    if re.search(r"relocat|willing to (relocate|commute)|commute", lab):
        rel = str(
            (values or {}).get("RELOCATION")
            or "Yes, willing to relocate"
        )
        return [rel, "Yes", "Yes, willing to relocate", "No"]
    # Generic Yes/No questions → No (dummy policy)
    if re.search(r"\b(yes|no)\b|are you|do you|have you|will you", lab):
        return ["No", "Yes"]
    return []


def _required_empties_as_leftovers(empties: list[dict] | None) -> list[dict]:
    """Promote page-complete empties to flash_candidate leftovers (honest blanks)."""
    out: list[dict] = []
    for e in empties or []:
        if not isinstance(e, dict):
            continue
        label = (e.get("label") or e.get("id") or "required_empty")[:160]
        out.append({
            "label": label,
            "type": "WD_REQUIRED_EMPTY",
            "reason": e.get("reason") or "empty_required",
            "automation_id": e.get("id"),
            "flash_candidate": True,
            "essay": bool(
                re.search(
                    r"\b(essay|describe|explain|tell us|why do you want|cover letter)\b",
                    label,
                    re.I,
                )
            ),
        })
    return out


def _finalize_workday_verdict(report: dict) -> str:
    """SUCCESS only at review with no dishonest ADVANCE / stuck / required blanks.

    Contact-only or mid-wizard SUCCESS is a metrics lie (see dates9 artifact).
    """
    if report.get("advanced_incomplete") or report.get("validation_after_advance"):
        report["verdict"] = "FAIL"
        report.setdefault("verdict_reason", "validation_or_incomplete_advance")
        return report["verdict"]
    if report.get("stuck_on_same_page"):
        report["verdict"] = "FAIL"
        report.setdefault("verdict_reason", "stuck_on_same_page")
        return report["verdict"]
    required = report.get("required_empty_before_advance") or []
    if required:
        report["verdict"] = "FAIL"
        report.setdefault("verdict_reason", "required_empties_remain")
        report["blocker"] = report.get("blocker") or "page_incomplete"
        return report["verdict"]
    if report.get("ready_for_review"):
        report["verdict"] = "SUCCESS"
        report.pop("verdict_reason", None)
        return report["verdict"]
    # Demote any prior SUCCESS that never reached review
    if report.get("verdict") == "SUCCESS":
        report["verdict"] = "FAIL"
        report["verdict_reason"] = "multipage_incomplete_not_ready_for_review"
        report["blocker"] = report.get("blocker") or "multipage_incomplete"
        return report["verdict"]
    if report.get("blocker"):
        report["verdict"] = "FAIL"
        return report["verdict"]
    if report.get("reached_contact"):
        report["verdict"] = "FAIL"
        report["verdict_reason"] = "multipage_incomplete_not_ready_for_review"
        report["blocker"] = report.get("blocker") or "multipage_incomplete"
        return report["verdict"]
    report["verdict"] = "FAIL"
    return report["verdict"]


async def _list_empty_select_ones(page) -> list[dict]:
    """Visible required Select One / empty comboboxes with labels (for fillers)."""
    try:
        return await page.evaluate(
            """() => {
              const out = [];
              const isVisible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0
                  && window.getComputedStyle(el).visibility !== 'hidden';
              };
              const labelOf = (el) => {
                const wrap = el.closest('[data-automation-id*="formField"], fieldset, [role="group"]')
                  || el.parentElement;
                return ((wrap && wrap.innerText) || '').replace(/\\s+/g, ' ').trim()
                  .replace(/\\bSelect One\\b/ig, '').trim().slice(0, 160);
              };
              const pushBtn = (el) => {
                if (!isVisible(el)) return;
                const t = (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase();
                const empty = !t || t === 'select one' || t === 'select' || t.startsWith('select ');
                if (!empty) return;
                const lab = labelOf(el);
                const req = el.getAttribute('aria-required') === 'true'
                  || (lab && lab.includes('*'));
                if (!req && !lab.includes('*')) return;
                out.push({
                  id: (el.getAttribute('data-automation-id') || el.id || 'selectOne').slice(0, 80),
                  label: lab,
                  reason: 'empty_required_combobox',
                });
              };
              document.querySelectorAll(
                'button[aria-haspopup="listbox"], [role="combobox"], button[aria-required="true"]'
              ).forEach(pushBtn);
              const seen = new Set();
              return out.filter((x) => {
                const k = (x.label || '') + '|' + x.id;
                if (seen.has(k)) return false;
                seen.add(k);
                return true;
              }).slice(0, 20);
            }"""
        ) or []
    except Exception:
        return []


async def _fill_select_one_by_label(page, label: str, candidates: list[str]) -> dict:
    """Click a Select One whose formField label matches, then pick a candidate option."""
    detail: dict = {
        "automation_id": f"select_one:{(label or '')[:40]}",
        "status": "missed",
        "label": (label or "")[:160],
        "verified": False,
        "mode": "select_one",
    }
    if not candidates:
        detail["reason"] = "no_safe_dummy_answer"
        detail["essay_skip"] = True
        return detail
    # Prefer button inside a formField whose text contains a distinctive slice of label
    needle = re.sub(r"[*:\s]+$", "", (label or "").strip())[:48]
    if not needle:
        detail["reason"] = "empty_label"
        return detail
    # State/Province → dedicated countryRegion path (never .fill on button)
    lab_l = (label or "").lower()
    if re.search(r"\b(state|province)\b", lab_l) and "country" not in lab_l:
        try:
            loc, sel = await _resolve_contact_locator(
                page, "addressSection_countryRegion"
            )
            if await loc.count() and await loc.is_visible(timeout=600):
                st = await _fill_country_region_state(
                    page, loc, sel, candidates[0]
                )
                st["automation_id"] = detail["automation_id"]
                st["label"] = detail["label"]
                st["mode"] = "select_one_countryRegion"
                return st
        except Exception as e:
            detail["countryRegion_route_error"] = str(e)[:80]
    try:
        # Scope: formFields containing the label text
        fields = page.locator('[data-automation-id*="formField"]')
        n = await fields.count()
        target_btn = None
        for i in range(min(n, 40)):
            field = fields.nth(i)
            try:
                txt = ((await field.inner_text()) or "").lower()
            except Exception:
                continue
            if needle.lower()[:24] not in txt and not any(
                w in txt for w in needle.lower().split()[:3] if len(w) > 4
            ):
                continue
            btn = field.locator(
                'button[aria-haspopup="listbox"], [role="combobox"], button'
            ).first
            if await btn.count() == 0:
                continue
            try:
                if not await btn.is_visible(timeout=400):
                    continue
            except Exception:
                continue
            cur = ((await btn.inner_text()) or "").strip().lower()
            if cur and cur not in ("select one", "select") and not cur.startswith("select "):
                # already filled — do not reopen (same-page thrash)
                try:
                    from verified_select import settle_open_listbox

                    await settle_open_listbox(page)
                except Exception:
                    pass
                detail["status"] = "filled"
                detail["verified"] = True
                detail["committed"] = True
                detail["readback"] = cur[:120]
                detail["already_set"] = True
                detail["reason"] = "already_correct_keep"
                detail["skipped_already_correct"] = True
                return detail
            target_btn = btn
            break
        if target_btn is None:
            # Fallback: any visible Select One button
            btns = page.locator('button[aria-haspopup="listbox"]:visible')
            bn = await btns.count()
            for i in range(min(bn, 15)):
                b = btns.nth(i)
                cur = ((await b.inner_text()) or "").strip().lower()
                if cur in ("select one", "select") or cur.startswith("select "):
                    # Check nearby label
                    try:
                        near = await b.evaluate(
                            """(el) => {
                              const w = el.closest('[data-automation-id*="formField"]') || el.parentElement;
                              return (w && w.innerText || '').slice(0, 200);
                            }"""
                        )
                    except Exception:
                        near = ""
                    if needle.lower()[:20] in (near or "").lower() or not needle:
                        target_btn = b
                        break
        if target_btn is None:
            detail["reason"] = "select_one_not_found"
            return detail
        await target_btn.scroll_into_view_if_needed()
        from verified_select import fill_workday_combobox

        last_miss = "no_matching_option"
        for cand in candidates:
            wd = await fill_workday_combobox(
                page,
                target_btn,
                str(cand),
                aliases=[cand, *[c for c in candidates if c != cand]],
                read_committed=lambda: target_btn.inner_text(),
                timeout_ms=5000,
                label=needle,
            )
            readback = str(wd.get("readback") or wd.get("picked") or "")
            if not readback:
                try:
                    readback = ((await target_btn.inner_text()) or "").strip()
                except Exception:
                    readback = ""
            filled_ok = bool(wd.get("ok")) and readback.lower() not in (
                "select one",
                "select",
            )
            if filled_ok:
                detail.update(
                    {
                        "status": "filled",
                        "verified": True,
                        "value": cand,
                        "readback": readback[:120],
                        "option_text": wd.get("picked"),
                        "option_clicked": bool(wd.get("option_clicked")),
                        "algorithm": wd.get("algorithm"),
                        "steps": wd.get("steps"),
                    }
                )
                return detail
            last_miss = wd.get("error") or last_miss
        detail["reason"] = last_miss
        try:
            await _escape_unless_captcha(page)
        except Exception:
            pass
    except Exception as e:
        detail["error"] = str(e)[:160]
        detail["reason"] = "select_one_error"
    return detail


async def _fill_required_select_ones(page, values: dict, phase: dict) -> list[dict]:
    """Fill visible required Select One comboboxes with safe dummy answers."""
    results: list[dict] = []
    empties = await _list_empty_select_ones(page)
    try:
        gate_empties = await _required_empty_on_page(page)
    except Exception:
        gate_empties = []
    seen_labels: set[str] = set()
    queue: list[dict] = []
    for e in list(empties) + [
        g for g in (gate_empties or [])
        if "combobox" in str(g.get("reason") or "")
    ]:
        lab = (e.get("label") or "").strip()
        key = lab.lower()[:80] or str(e.get("id") or "")
        if not key or key in seen_labels:
            continue
        seen_labels.add(key)
        queue.append(e)
    for e in queue[:12]:
        lab = e.get("label") or ""
        cands = _dummy_answer_for_wd_label(lab, values)
        if not cands:
            miss = {
                "automation_id": f"select_one:{(lab or e.get('id') or '')[:40]}",
                "status": "missed",
                "reason": "no_safe_dummy_answer",
                "label": lab[:160],
                "verified": False,
                "essay_skip": True,
            }
            results.append(miss)
            phase.setdefault("missed", []).append(miss)
            continue
        r = await _fill_select_one_by_label(page, lab, cands)
        results.append(r)
        if r.get("verified"):
            phase.setdefault("filled", []).append(r)
        else:
            phase.setdefault("missed", []).append(r)
    return results


async def _fill_education_section(page, values: dict, phase: dict) -> None:
    """Add + fill first education row (school/degree) when educationSection present."""
    present = (
        await _automation_visible(page, "educationSection")
        or await _automation_visible(page, "educationPanel")
        or await _automation_visible(page, "formField-school")
    )
    if not present:
        try:
            body = (await _body_text(page, 2500)).lower()
            present = "education" in body and (
                "school" in body or "degree" in body or "add" in body
            )
        except Exception:
            present = False
    if not present:
        return
    phase["education_present"] = True
    # Add education row if empty
    has_school = await page.locator(
        '[data-automation-id*="school" i], '
        '[data-automation-id="formField-school"] input, '
        'input[name*="school" i]'
    ).count()
    if has_school == 0:
        for sel in (
            '[data-automation-id="educationSection"] button:has-text("Add")',
            'button[aria-label*="Add Education" i]',
            'button:has-text("Add Education")',
            'button:has-text("Add Another")',
        ):
            btn = page.locator(sel).first
            try:
                if await btn.count() and await btn.is_visible(timeout=500):
                    resolved = await gate_locator_click(
                        btn, intent_label="Add Education", allow_kinds=NAV_KINDS
                    )
                    if not resolved.get("ok"):
                        continue
                    await btn.click(timeout=3000)
                    await page.wait_for_timeout(600)
                    phase.setdefault("filled", []).append({
                        "automation_id": "educationSection/Add",
                        "status": "filled",
                        "mode": "click",
                        "verified": True,
                    })
                    break
            except Exception:
                continue
    school = (values.get(SCHOOL) or "University of Alabama, Tuscaloosa").strip()
    degree = (values.get(DEGREE) or "Master's Degree").strip()
    # School combobox / input
    for aid, val, combobox in (
        ("school", school, True),
        ("degree", degree, True),
        ("educationSection_school", school, True),
        ("formField-school", school, True),
        ("formField-degree", degree, True),
    ):
        # Prefer Select One path by label when automation id fill misses
        r = await _fill_automation_id(page, aid, val, combobox=combobox)
        if _is_verified_fill(r):
            r["automation_id"] = f"education/{aid}"
            phase.setdefault("filled", []).append(r)
            continue
        # Label-based Select One
        lab = "School" if "school" in aid.lower() else "Degree"
        sr = await _fill_select_one_by_label(
            page,
            lab,
            _dummy_answer_for_wd_label(lab + " " + aid, values) or [val],
        )
        if sr.get("verified"):
            phase.setdefault("filled", []).append(sr)
        elif r.get("reason") not in ("not_in_dom", "not_visible"):
            phase.setdefault("missed", []).append(r)

async def _phase_app_questions(page, values: dict, report: dict) -> dict:
    """Optional applicationQuestions step (Cisco: between experience and EEO).

    Never invent essays. Fill known Yes/No with dummy No when labeled for
    sponsorship / conviction / etc. Fill required Select One comboboxes via
    label→dummy map. ADVANCE only if required_empty is clear.
    Never passthrough-skip while required Select Ones remain.
    """
    phase: dict = {
        "name": "C2_app_questions",
        "present": False,
        "filled": [],
        "missed": [],
        "advanced": False,
    }
    present = await _wait_step(page, "applicationQuestionsPage", timeout_ms=8000)
    select_ones = await _list_empty_select_ones(page)
    if not present:
        body = (await _body_text(page, 2500)).lower()
        if "application question" in body or (
            "my experience" not in body
            and "question" in body
            and "voluntary" not in body
        ):
            present = "application question" in body or bool(select_ones)
        # Already past this step (on EEO / self-id) — only if no required Select Ones
        if (
            await _automation_visible(page, "voluntaryDisclosuresPage")
            or await _automation_visible(page, "selfIdentificationPage")
        ) and not select_ones:
            phase["skipped"] = "app_questions_already_passed"
            phase["advanced"] = True  # passthrough
            return phase
    if not present:
        body = (await _body_text(page, 2000)).lower()
        # Required empties / Select Ones mean we ARE on a questions-like page
        gate_empties = []
        try:
            gate_empties = await _required_empty_on_page(page)
        except Exception:
            pass
        has_blanks = bool(select_ones) or any(
            str(e.get("reason") or "").startswith("empty_required")
            for e in (gate_empties or [])
        )
        if has_blanks and not (
            "voluntary disclosure" in body or "self identif" in body
        ):
            present = True
            phase["detected_via"] = "required_empties"
        elif "voluntary disclosure" in body or "self identif" in body:
            phase["skipped"] = "app_questions_absent"
            phase["advanced"] = True
            return phase
        elif "application question" not in body and not has_blanks:
            phase["skipped"] = "app_questions_absent"
            phase["advanced"] = True
            return phase
        else:
            present = True
    phase["present"] = present

    # Safe dummy Yes/No only — never essays / open text
    no_patterns = (
        r"sponsor",
        r"visa",
        r"authorized to work",
        r"conviction",
        r"felony",
        r"previously (employed|worked)",
        r"relative.*work",
        r"export control",
        r"non-compet",
        r"conflict of interest",
    )
    try:
        radios = page.locator('input[type="radio"]:visible')
        n = await radios.count()
        clicked_groups: set[str] = set()
        for i in range(min(n, 40)):
            r = radios.nth(i)
            try:
                name = (await r.get_attribute("name")) or f"r{i}"
                if name in clicked_groups:
                    continue
                val = ((await r.get_attribute("value")) or "").lower()
                lab = ""
                try:
                    lab = (
                        await r.evaluate(
                            """(el) => {
                              const g = el.closest('fieldset, [role=group], div');
                              return (g && g.innerText || '').slice(0, 200);
                            }"""
                        )
                        or ""
                    ).lower()
                except Exception:
                    pass
                if not any(re.search(p, lab, re.I) for p in no_patterns):
                    continue
                if val in ("yes", "true", "y"):
                    continue
                # Prefer No
                if val in ("no", "false", "n") or "no" in (
                    (await r.get_attribute("aria-label") or "").lower()
                ):
                    await r.check(timeout=1500, force=True)
                    clicked_groups.add(name)
                    phase["filled"].append({
                        "automation_id": f"app_q:{name}",
                        "status": "filled",
                        "mode": "radio_no",
                        "verified": True,
                    })
            except Exception:
                continue
    except Exception as e:
        phase.setdefault("errors", []).append(str(e)[:120])

    # Required Select One comboboxes (Cisco App Questions gap)
    await _fill_required_select_ones(page, values, phase)

    report.setdefault("filled", []).extend(
        [f for f in phase["filled"] if f not in (report.get("filled") or [])]
    )
    report.setdefault("missed", []).extend(phase.get("missed") or [])
    await _gate_then_advance(page, report, phase)
    return phase


async def _phase_d_eeo(page, values: dict, report: dict) -> dict:
    """voluntaryDisclosuresPage — Decline EEO from DUMMY_PROFILE only."""
    phase: dict = {
        "name": "D_eeo",
        "present": False,
        "filled": [],
        "missed": [],
        "advanced": False,
    }
    present = await _wait_step(page, "voluntaryDisclosuresPage", timeout_ms=12000)
    if not present:
        # After app-questions ADVANCE, poll a bit longer
        for _ in range(6):
            if await _automation_visible(page, "voluntaryDisclosuresPage"):
                present = True
                break
            body = (await _body_text(page, 2000)).lower()
            if "voluntary disclosure" in body or "gender" in body and "veteran" in body:
                present = True
                break
            await page.wait_for_timeout(700)
    phase["present"] = present
    if not present:
        phase["skipped"] = "eeo_page_absent"
        return phase

    pack = [
        ("gender", values.get(GENDER) or "Decline to self identify"),
        ("hispanicOrLatino", values.get(HISPANIC) or "No"),
        ("ethnicityDropdown", values.get(RACE) or "Decline to self identify"),
        ("veteranStatus", values.get(VETERAN) or "Decline to self identify"),
    ]
    for aid, val in pack:
        r = await _fill_eeo_combobox(page, aid, str(val))
        if _is_verified_fill(r):
            phase["filled"].append(r)
        else:
            if r.get("reason") in ("not_in_dom", "not_visible"):
                r["optional_miss"] = True
            phase["missed"].append(r)

    # Agreement checkbox when present
    agree = page.locator(
        '[data-automation-id="agreementCheckbox"], '
        'input[data-automation-id="agreementCheckbox"]'
    ).first
    try:
        if await agree.count() and await agree.is_visible(timeout=600):
            try:
                await agree.check(timeout=3000, force=True)
            except Exception:
                await agree.click(timeout=3000, force=True)
            phase["filled"].append({
                "automation_id": "agreementCheckbox",
                "status": "filled",
                "mode": "check",
                "verified": True,
            })
    except Exception as e:
        phase["missed"].append({
            "automation_id": "agreementCheckbox",
            "status": "missed",
            "error": str(e)[:120],
            "verified": False,
        })

    report.setdefault("filled", []).extend(phase["filled"])
    report.setdefault("missed", []).extend(
        [m for m in phase["missed"] if not m.get("optional_miss")]
    )
    await _gate_then_advance(page, report, phase)
    return phase


async def _phase_e_self_id(page, values: dict, report: dict) -> dict:
    """selfIdentificationPage — name, today date, disability Decline."""
    phase: dict = {
        "name": "E_self_id",
        "present": False,
        "filled": [],
        "missed": [],
        "advanced": False,
        "stopped_at_review": False,
    }
    present = await _wait_step(page, "selfIdentificationPage", timeout_ms=12000)
    phase["present"] = present
    if not present:
        phase["skipped"] = "self_id_page_absent"
        return phase

    full = values.get(NAME_FULL) or (
        f"{values.get(NAME_FIRST, '')} {values.get(NAME_LAST, '')}".strip()
    )
    if full:
        name_loc = page.locator(
            '[data-automation-id="name"] input, '
            'input[data-automation-id="name"], '
            'input[aria-label*="Name"]'
        ).first
        try:
            if await name_loc.count():
                await name_loc.fill(str(full), timeout=4000)
                phase["filled"].append({
                    "automation_id": "name",
                    "status": "filled",
                    "value": full,
                    "verified": True,
                })
        except Exception as e:
            phase["missed"].append({
                "automation_id": "name",
                "status": "missed",
                "error": str(e)[:120],
                "verified": False,
            })

    # Today date via picker
    try:
        icon = page.locator('[data-automation-id="dateIcon"]').first
        if await icon.count() and await icon.is_visible(timeout=600):
            await icon.click(timeout=3000)
            await page.wait_for_timeout(300)
            today = page.locator(
                '[data-automation-id="datePickerSelectedToday"], '
                'button:has-text("Today")'
            ).first
            if await today.count():
                await today.click(timeout=3000)
                phase["filled"].append({
                    "automation_id": "datePickerSelectedToday",
                    "status": "filled",
                    "verified": True,
                    "mode": "today",
                })
    except Exception as e:
        phase["missed"].append({
            "automation_id": "dateIcon",
            "status": "missed",
            "error": str(e)[:120],
            "verified": False,
        })

    disability = values.get(DISABILITY) or "Decline to self identify"
    r = await _fill_eeo_combobox(page, "disabilityStatus", str(disability))
    # Some tenants use radio labels
    if not _is_verified_fill(r):
        for text in (
            "I do not want to answer",
            "Decline",
            "Prefer not to say",
        ):
            try:
                loc = page.get_by_text(text, exact=False).first
                if await loc.count() and await loc.is_visible(timeout=400):
                    await loc.click(timeout=3000)
                    checked = False
                    try:
                        # Prefer role=radio near the label
                        radio = page.get_by_role(
                            "radio", name=re.compile(re.escape(text), re.I)
                        ).first
                        if await radio.count():
                            checked = bool(await radio.is_checked())
                        else:
                            # ATS-012: no soft-accept — require an actual checked input
                            inp = loc.locator(
                                "xpath=ancestor::label[1]//input[@type='radio']"
                                " | xpath=preceding::input[@type='radio'][1]"
                                " | xpath=following::input[@type='radio'][1]"
                            ).first
                            if await inp.count():
                                checked = bool(await inp.is_checked())
                    except Exception:
                        checked = False
                    r = {
                        "automation_id": "disabilityStatus",
                        "status": "filled" if checked else "missed",
                        "value": text,
                        "readback": text if checked else "",
                        "verified": bool(checked),
                        "mode": "label_click",
                    }
                    if checked:
                        break
            except Exception:
                continue
    if _is_verified_fill(r):
        phase["filled"].append(r)
    else:
        phase["missed"].append(r)

    report.setdefault("filled", []).extend(phase["filled"])
    report.setdefault("missed", []).extend(phase["missed"])

    # Stop at review: ADVANCE only if page-complete; never FINAL Submit
    required_empty = await _required_empty_on_page(page)
    phase["required_empty_before_advance"] = required_empty
    if required_empty:
        phase["advance_blocked_reason"] = "required_fields_empty"
        report["blocker"] = report.get("blocker") or "self_id_incomplete"
        report["verdict"] = "FAIL"
        return phase

    # Click next once to reach review if possible; button_gate refuses Submit
    next_clicks = await _click_next_advance(page)
    phase["next_clicks"] = next_clicks
    report.setdefault("clicks", []).extend(next_clicks)
    phase["advanced"] = any(c.get("action") == "clicked" for c in next_clicks)
    await page.wait_for_timeout(1200)
    validation = await _validation_banner_present(page)
    phase["validation_after_advance"] = validation
    if validation:
        report["blocker"] = "validation_errors"
        report["verdict"] = "FAIL"
        report["advanced_incomplete"] = True
        return phase
    if not phase.get("advanced"):
        report["verdict"] = "FAIL"
        report.setdefault("verdict_reason", "self_id_advance_not_clicked")
        report["blocker"] = report.get("blocker") or "self_id_incomplete"
        return phase
    phase["stopped_at_review"] = True
    # Attach early so can_claim_ready / workday_wizard_incomplete see Review.
    report["phase_e"] = phase
    report["workday_current_step"] = "review"
    # DOM/vision judge before Ready — fail closed on blanks / AMBIGUOUS.
    try:
        from page_progress import (
            apply_live_vision_gate,
            can_claim_ready,
            vision_blocks_ready,
        )

        await apply_live_vision_gate(page, report)
        if can_claim_ready(report):
            report["ready_for_review"] = True
            report["verdict"] = "SUCCESS"
        else:
            report["ready_for_review"] = False
            if report.get("vision_incomplete") or vision_blocks_ready(report):
                report["verdict"] = "FAIL"
                report.setdefault("verdict_reason", "vision_incomplete")
    except Exception as e:
        report["vision_incomplete"] = True
        report["ready_for_review"] = False
        report["verdict"] = "FAIL"
        report.setdefault("verdict_reason", "vision_gate_error")
        report.setdefault("errors", []).append({"vision_gate": str(e)[:120]})
    return phase


def build_contact_fill_plan(values: dict) -> tuple[list[tuple[str, str, bool]], list[dict]]:
    """Build (aid, value, combobox) plan + missed rows for missing dummy values."""
    fill_plan: list[tuple[str, str, bool]] = []
    missed: list[dict] = []
    combobox_aids = {
        "addressSection_country",
        "addressSection_countryRegion",
        "phone-device-type",
    }
    for aid, ftype in WD_CONTACT_PACK:
        combobox = aid in combobox_aids
        if ftype == "PHONE_DEVICE":
            fill_plan.append((aid, "Mobile", True))
            continue
        val = values.get(ftype)
        if not val or not validate_filled(ftype, str(val)):
            missed.append({
                "automation_id": aid,
                "status": "missed",
                "reason": "no_dummy_value",
                "type": ftype,
                "verified": False,
            })
            continue
        fill_plan.append((aid, str(val), combobox))
    return fill_plan, missed


async def workday_two_phase_on_page(
    page,
    values: dict,
    *,
    click_create_account: bool = True,
    do_apply_clicks: bool = True,
    resume_pdf: str | Path | None = None,
    step_report: dict | None = None,
) -> dict:
    """Phase A (auth) + Phase B–E (contact→experience→EEO→self-id) on an open page.

    Caller owns browser lifecycle. Dummy values only. Never clicks FINAL Submit.
    CAPTCHA / bot wall → stop and set report['blocker'].
    """
    values = dict(values)
    if resume_pdf:
        values["_resume_pdf"] = str(resume_pdf)
    elif not values.get("_resume_pdf"):
        values["_resume_pdf"] = str(DUMMY_PDF)

    report: dict = {
        "experiment": "workday_two_phase",
        "phases": "A_auth+B_contact+C_experience+D_eeo+E_self_id",
        "dummy": True,
        "identity_email": values.get(EMAIL),
        "resume_pdf": values.get("_resume_pdf"),
        "url": page.url,
        "contact_pack": [aid for aid, _ in WD_CONTACT_PACK],
        "clicks": [],
        "stuck": [],
        "missed": [],
        "errors": [],
        "blocker": None,
        "page_title": None,
        "final_url": None,
        "phase_a": None,
        "phase_b": None,
        "phase_c": None,
        "phase_c2": None,
        "phase_d": None,
        "phase_e": None,
        "click_create_account": click_create_account,
        "never_submit": True,
        "submit_clicked": False,
        "advanced_incomplete": False,
        "_step_report": step_report,
    }

    fill_plan, pre_missed = build_contact_fill_plan(values)
    report["missed"].extend(pre_missed)
    # Stash dummy values for Phase B extras (how heard / worked here)
    report["_contact_values"] = values

    report["page_title"] = await page.title()
    report["final_url"] = page.url

    body = await _body_text(page, 4000)
    early = await _hard_blocker_live(page)
    if early:
        report["blocker"] = early
        report["blocker_detail"] = body[:500]
        return report

    if do_apply_clicks:
        report["clicks"] = await _click_workday_apply_path(
            page, step_report=step_report, report=report
        )

    report["page_title"] = await page.title()
    report["final_url"] = page.url
    body = await _body_text(page)
    hard = await _hard_blocker_live(page)
    if hard:
        report["blocker"] = hard
        report["blocker_detail"] = body[:500]
        return report

    if not click_create_account:
        ca = await _try_create_account(page, values, click_submit=False)
        report["create_account"] = ca
        report["phase_a"] = {"name": "A_auth", "create_account": ca, "click_disabled": True}
    else:
        report["phase_a"] = await _phase_a_auth(page, values, report)

    report["page_title"] = await page.title()
    report["final_url"] = page.url

    if report.get("blocker") in ("captcha", "akamai", "cloudflare"):
        return report

    report["phase_a_resume"] = await _handle_autofill_resume_after_auth(
        page, values, report
    )

    # Mid-wizard resume: if already past contact (Experience / Questions / …),
    # do NOT FAIL on contact_absent — jump to the current step and keep advancing.
    current_step, spa_probe = await _detect_workday_current_step(page)
    report["workday_current_step"] = current_step
    report["workday_wizard_progress"] = str(spa_probe.get("progress") or "")[:160]
    report["workday_spa_probe"] = spa_probe

    if current_step in (
        "experience",
        "app_questions",
        "eeo",
        "self_id",
        "review",
    ):
        report["phase_b"] = {
            "name": "B_contact",
            "present": False,
            "skipped": f"already_on_{current_step}",
            "advanced": current_step != "review",
            "filled": [],
            "missed": [],
            "next_clicks": [],
        }
        report["advanced"] = True
        report["reached_contact"] = True
        report["contact_page_present"] = False
        report.pop("_contact_values", None)
        await _run_workday_phases_from(
            page, values, report, start=current_step
        )
    else:
        report["phase_b"] = await _phase_b_contact(page, fill_plan, report)
        report.pop("_contact_values", None)
        try:
            from fill_pause import ensure_fill_pause_ready

            await ensure_fill_pause_ready(page, report.get("_step_report") or report)
        except Exception:
            pass

        # ATS2-011: recover multipage when SPA left contact but phase_b sticky-stuck
        # on flat URL fingerprint (can_continue would otherwise skip C–E forever).
        if (
            report.get("stuck_on_same_page")
            and report.get("advanced")
            and not report.get("validation_after_advance")
        ):
            try:
                pb = report.get("phase_b") or {}
                before_dom = pb.get("spa_dom_before") or {}
                moved = pb.get("spa_dom_moved") or await _wd_spa_step_probe(page)
                if _wd_spa_moved(before_dom, moved) or (
                    moved.get("experience")
                    or moved.get("appQ")
                    or moved.get("eeo")
                    or moved.get("selfId")
                    or moved.get("review")
                ):
                    report["stuck_on_same_page"] = False
                    pb["stuck_on_same_page"] = False
                    pb["spa_stuck_cleared"] = True
                    pb["spa_dom_moved"] = moved
                    report["phase_b"] = pb
                    if report.get("verdict") == "FAIL" and report.get(
                        "verdict_reason"
                    ) in (None, "", "stuck_on_same_page"):
                        report["verdict"] = "SUCCESS"
                        report.pop("verdict_reason", None)
            except Exception:
                pass

        # After contact ADVANCE, re-probe — SPA may already show Experience.
        if report.get("advanced") and not report.get("validation_after_advance"):
            try:
                post_step, post_probe = await _detect_workday_current_step(page)
                report["workday_current_step"] = post_step
                report["workday_wizard_progress"] = str(
                    post_probe.get("progress") or report.get("workday_wizard_progress") or ""
                )[:160]
                if post_step in (
                    "experience",
                    "app_questions",
                    "eeo",
                    "self_id",
                    "review",
                ):
                    current_step = post_step
            except Exception:
                current_step = "experience"

        # Multipage: experience → EEO → self-id when contact ADVANCEd cleanly
        can_continue = (
            bool(report.get("advanced"))
            and not report.get("validation_after_advance")
            and not report.get("stuck_on_same_page")
            and report.get("blocker") not in (
                "captcha",
                "akamai",
                "cloudflare",
                "email_verify",
                "contact_incomplete",
                "validation_errors",
            )
        )
        if can_continue:
            start = (
                current_step
                if current_step
                in ("experience", "app_questions", "eeo", "self_id", "review")
                else "experience"
            )
            await _run_workday_phases_from(page, values, report, start=start)

    report["page_title"] = await page.title()
    report["final_url"] = page.url

    if not report.get("blocker") and not report.get("reached_contact"):
        if await _create_account_form(page) or await _password_only_signin(page):
            report["blocker"] = "auth_wall"

    verified = [r for r in (report.get("filled") or []) if _is_verified_fill(r)]
    report["filled"] = verified
    report["stuck"] = []  # successful fills are never labeled stuck
    report["stuck_count"] = 0
    report["filled_count"] = len(verified)
    report["missed_count"] = len(report.get("missed") or [])
    phase_a = report.get("phase_a") or {}
    ca = report.get("create_account") or phase_a.get("create_account") or {}
    si = report.get("sign_in") or phase_a.get("sign_in") or {}
    if report.get("advanced_incomplete") or report.get("validation_after_advance"):
        report["verdict"] = "FAIL"
    # Promote remaining required empties to leftovers (Flash/refill can see them)
    req_empty = report.get("required_empty_before_advance") or []
    if req_empty:
        promo = _required_empties_as_leftovers(req_empty)
        existing = report.setdefault("leftovers", [])
        if not isinstance(existing, list):
            existing = []
            report["leftovers"] = existing
        seen = {
            (str(u.get("label") or ""), str(u.get("automation_id") or ""))
            for u in existing
            if isinstance(u, dict)
        }
        for row in promo:
            key = (str(row.get("label") or ""), str(row.get("automation_id") or ""))
            if key in seen:
                continue
            existing.append(row)
            seen.add(key)
        report["leftover_count"] = len(existing)
    # Post L0/1 unanswered radios/selects → flash_candidates (Workday app questions)
    if page is not None and not report.get("blocker"):
        try:
            from leftover_miss_scan import promote_l01_misses

            await promote_l01_misses(page, report)
            report["leftover_count"] = len(report.get("leftovers") or [])
        except Exception as e:
            report.setdefault("errors", []).append({"l01_miss_scan": str(e)[:120]})
    # Live DOM judge when page still open (covers contact-only / mid-wizard stops
    # that never hit Phase E Ready path).
    if page is not None and not isinstance(report.get("vision_judge_live"), dict):
        try:
            from page_progress import apply_live_vision_gate, can_claim_ready, finalize_ready_flag

            await apply_live_vision_gate(page, report)
            if report.get("ready_for_review") and not can_claim_ready(report):
                report["ready_for_review"] = False
            finalize_ready_flag(report)
        except Exception as e:
            report.setdefault("errors", []).append({"vision_gate": str(e)[:120]})
    try:
        from page_progress import apply_progress_verdict_gates

        apply_progress_verdict_gates(report)
    except Exception:
        pass
    verdict = _finalize_workday_verdict(report)
    report["metrics"] = {
        "account_created": bool(phase_a.get("account_created")),
        "signed_in": bool(phase_a.get("signed_in")),
        "already_registered": bool(phase_a.get("already_registered")),
        "create_account_filled": len(
            [r for r in (ca.get("filled") or []) if _is_verified_fill(r) or r.get("verified") is True]
        ),
        "sign_in_filled": len(
            [r for r in (si.get("filled") or []) if _is_verified_fill(r) or r.get("verified") is True]
        ),
        "contact_filled_verified": report["filled_count"],
        "contact_missed": report["missed_count"],
        "contact_page_present": bool(report.get("contact_page_present")),
        "reached_contact": bool(report.get("reached_contact")),
        "advanced": bool(report.get("advanced")),
        "advanced_count": int(report.get("advanced_count") or 0),
        "pages_seen": len(report.get("pages_seen") or []),
        "stuck_on_same_page": bool(report.get("stuck_on_same_page")),
        "advance_blocked_reason": report.get("advance_blocked_reason"),
        "advanced_incomplete": bool(report.get("advanced_incomplete")),
        "validation_errors": bool(report.get("validation_after_advance")),
        "experience_present": bool((report.get("phase_c") or {}).get("present")),
        "app_questions_present": bool((report.get("phase_c2") or {}).get("present")),
        "eeo_present": bool((report.get("phase_d") or {}).get("present")),
        "self_id_present": bool((report.get("phase_e") or {}).get("present")),
        "ready_for_review": bool(report.get("ready_for_review")),
        "leftover_count": int(report.get("leftover_count") or 0),
        "verdict": verdict,
        "verdict_reason": report.get("verdict_reason"),
        "blocker": report.get("blocker"),
        "workday_current_step": report.get("workday_current_step"),
    }
    report["submit_clicked"] = False
    report["never_submit"] = True
    return report



def main() -> int:
    """CLI always delegates to fast_fill (no standalone Chromium / --deep path)."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=None, help="Override myworkdayjobs URL")
    ap.add_argument(
        "--headed",
        action="store_true",
        default=True,
        help="Show a visible Chromium window (default for interactive demos)",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        help="Run without a visible browser window (CI/batch)",
    )
    ap.add_argument(
        "--deep",
        action="store_true",
        help="Deprecated: ignored; always uses fast_fill.run_fast_fill",
    )
    ap.add_argument(
        "--no-click-create-account",
        action="store_true",
        help="Deprecated: ignored (fast_fill owns create-account behavior)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=OUT_DIR / "exp_workday_selectors.json",
    )
    args = ap.parse_args()
    headed = not bool(args.headless)
    if args.deep:
        print(
            "[workday_selectors] --deep is deprecated; delegating to fast_fill "
            "(headed-cap + honesty finalize apply).",
            flush=True,
        )
    from fast_fill import run_fast_fill

    target = args.url
    if not target:
        job_meta = pick_myworkday_url()
        target = job_meta["url"]
    print(
        f"[workday_selectors → fast_fill] headed={headed} "
        "(dummy-only, never submit)…",
        flush=True,
    )
    report = run_fast_fill(target, headed=headed, out=args.out)
    print(
        json.dumps(
            {
                "delegated_to": "fast_fill.run_fast_fill",
                "platform": report.get("platform"),
                "url": report.get("url"),
                "filled": report.get("filled_count"),
                "leftovers": report.get("leftover_count"),
                "coverage": report.get("coverage"),
                "blocker": report.get("blocker"),
                "submit_clicked": report.get("submit_clicked"),
                "out": report.get("report_path"),
            },
            indent=2,
        )
    )
    return 0 if report.get("verdict") == "SUCCESS" else 1



if __name__ == "__main__":
    raise SystemExit(main())
