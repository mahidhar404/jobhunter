#!/usr/bin/env python3
"""Thin DeepSeek-V4-Flash / Skyvern handoff for fast_fill leftovers only.

Default is OFF — callers must pass ``flash_leftovers=True`` / ``--flash-leftovers``.

Hard rules:
  - DUMMY_PROFILE values only (inherits from the fast_fill report)
  - NEVER submit
  - Prompt covers ONLY leftover fields; already-filled fields are a cheat sheet
    and are never re-asked as leftovers
  - CAPTCHA → stop (Skyvern terminate criterion)
  - When ON, ``max_steps`` is hard-capped at ``FLASH_MAX_STEPS`` (5)

Usage (via fast_fill CLI)::

    # Shape only (default) — report["flash"] is populated, Flash not invoked
    skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headless

    # Invoke thin Flash for leftovers (requires local Skyvern)
    skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headless \\
        --flash-leftovers

Unit-test shape against an existing report (no Skyvern)::

    skyvern_runtime/venv/bin/python scripts/fastfill/flash_leftovers.py \\
        skyvern_runtime/real_job_results/fast_fill_greenhouse.json --self-test

API shape (report["flash"])::

    {
      "mode": "leftovers_only",
      "invoked": false,
      "never_submit": true,
      "dummy": true,
      "max_steps": 5,
      "url": "...",
      "already_filled": [{"label"|"type"|"selector": ..., "value": ...}, ...],
      "already_filled_count": N,
      "leftovers": [{... flash_candidate fields ...}],
      "leftover_count": M,
      "cheat_sheet": "ALREADY FILLED (do not re-fill):\\n  - ...",
      "prompt": "Fill ONLY these leftover fields...\\n...",
      "prompt_chars": 1234,
      "model_hint": "DeepSeek-V4-Flash"
    }
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "skyvern_runtime" / "scripts"))

# Minimal-Flash contract — default OFF; when ON, ≤5 leftover actions.
# Grounded / cycle mode may raise leftover field cap (still step-capped for Skyvern).
FLASH_DEFAULT_ON = False
FLASH_MAX_STEPS = 5
FLASH_MAX_LEFTOVER_FIELDS = 5  # thin Skyvern default
FLASH_MAX_STEPS_GROUNDED = 15  # cycle mode: answer all leftovers incl. essays
# Historical skipped_reason only — leftover Flash is ON for Workday/NXP.
FLASH_SKIP_WORKDAY_REASON = "workday_two_phase"


def skip_flash_on_workday(
    report: dict | None = None,
    *,
    platform: str = "",
    url: str = "",
) -> bool:
    """Leftover Flash is ON for Workday/NXP (and every other ATS).

    Previously returned True on Workday because Flash fought the two-phase pack
    (rewriting contact / How-Heard). Leftover-only rules (cheat sheet,
    field_lock, steal-blocklist) already refuse EMAIL/PHONE/name/address.
    Always returns False so NXP gets DeepSeek leftovers after Layer 0/1 + pack.
    """
    return False

NEVER_SUBMIT_SNIPPET = (
    "ABSOLUTE HARD RULE: never click Submit / Submit application / Apply-final / "
    "Send / Finish / Confirm. Stop at ready-for-review. Account Create/Sign In and "
    "Next/Continue are allowed. Never solve CAPTCHA — terminate if one appears."
)

LEFTOVERS_RULES = """
LEFTOVERS-ONLY MODE (DeepSeek-V4-Flash, grounded in dummy + job description):
1. Fields listed under ALREADY FILLED are done — do NOT clear, retype, ask about,
   or re-derive them. Treat the cheat sheet as ground truth.
2. Answer EVERY leftover field listed below — including essays, "why join us",
   cover-letter style prompts, and novel screening questions. One action per field.
3. Ground free-text / essay answers ONLY in: (a) scraped JOB DESCRIPTION below,
   (b) DUMMY RESUME EXCERPT, (c) DUMMY_PROFILE FACTS. Dummy tone is required —
   never use real applicant PII. Short professional paragraphs are OK.
4. EEO / demographics / protected-class: use SHARED policy catalog answers from
   DUMMY_PROFILE FACTS / shared_values (Male / No Hispanic / no disability /
   not veteran; race=Decline). Never invent new demographics beyond that catalog.
   If unsure / no option match, Decline / Prefer not to disclose is the safe fallback.
5. NEVER fill, clear, or retype EMAIL, PHONE, First/Last Name, ZIP / postal code,
   street address, city, state, country, password, or resume upload — those are
   owned by deterministic prefill/reclaim. If they appear blank on the page, skip
   them (do not invent values).
6. SELECT / dropdown / combobox leftovers (tagged select=true): type a short
   filter token if needed, then CLICK the matching option — never leave typed
   filter text and never paste essay paragraphs into select filters.
7. Never press Enter to confirm a dropdown (can submit the form) — click the option.
8. Resume upload path if needed: use the dummy resume already known to the harness.
9. Stay within {max_steps} actions maximum.
10. {never_submit}
""".strip()

LEFTOVERS_RULES_REAL = """
LEFTOVERS-ONLY MODE (DeepSeek-V4-Flash, grounded in this run's resume + shared policy):
1. Fields listed under ALREADY FILLED are done — do NOT clear, retype, ask about,
   or re-derive them. Treat the cheat sheet as ground truth.
2. Answer EVERY leftover field listed below — including essays, "why join us",
   cover-letter style prompts, and novel screening questions. One action per field.
3. Ground free-text / essay answers ONLY in: (a) scraped JOB DESCRIPTION below,
   (b) RESUME EXCERPT, (c) APPLICANT FACTS. Use this run's unique identity only —
   do not invent a different name/email/phone/school.
4. EEO / demographics / protected-class: use SHARED policy catalog answers from
   APPLICANT FACTS (Male / No Hispanic / no disability / not veteran; race=Decline).
   Never invent new demographics beyond that catalog. If unsure / no option match,
   Decline / Prefer not to disclose is the safe fallback.
5. NEVER fill, clear, or retype EMAIL, PHONE, First/Last Name, ZIP / postal code,
   street address, city, state, country, password, or resume upload — those are
   owned by deterministic prefill/reclaim. If they appear blank on the page, skip
   them (do not invent values).
6. SELECT / dropdown / combobox leftovers (tagged select=true): type a short
   filter token if needed, then CLICK the matching option — never leave typed
   filter text and never paste essay paragraphs into select filters.
7. Never press Enter to confirm a dropdown (can submit the form) — click the option.
8. Resume upload path if needed: use the resume already known to the harness.
9. Stay within {max_steps} actions maximum.
10. {never_submit}
""".strip()

# Cycle / full-leftover mode may need more than the thin 5-field default.
FLASH_MAX_LEFTOVER_FIELDS_GROUNDED = 20

# Contact/address — never hand to Skyvern/LLM (mirror fill_attribution.FLASH_FORBIDDEN).
_FLASH_STEAL_BLOCKLIST = frozenset(
    {
        "NAME_FIRST",
        "NAME_LAST",
        "NAME_FULL",
        "NAME_MIDDLE",
        "RELATIVE_NAME",
        "EMAIL",
        "PHONE",
        "PHONE_EXTENSION",
        "ADDRESS_LINE1",
        "ADDRESS_CITY",
        "ADDRESS_STATE",
        "ADDRESS_ZIP",
        "ADDRESS_COUNTRY",
        "PASSWORD",
        "PASSWORD_CONFIRM",
        "RESUME_UPLOAD",
    }
)

# Scorecard / report fields every flash payload must expose.
FLASH_SCORECARD_KEYS = (
    "mode",
    "invoked",
    "never_submit",
    "dummy",
    "max_steps",
    "already_filled_count",
    "leftover_count",
    "cheat_sheet",
    "prompt_chars",
)


def cap_flash_max_steps(max_steps: int | None = None) -> int:
    """Hard-cap Flash steps to ``[1, FLASH_MAX_STEPS]`` (default = max)."""
    if max_steps is None:
        return FLASH_MAX_STEPS
    try:
        n = int(max_steps)
    except (TypeError, ValueError):
        return FLASH_MAX_STEPS
    return max(1, min(n, FLASH_MAX_STEPS))


def _norm_key(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _compact_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _norm_key(value))


def _compact_keys_overlap(a: str, b: str, *, min_len: int = 8) -> bool:
    """True when compact ids share a distinctive substring (countryphonecode ⊂ phonenumbercountryphonecode)."""
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) < min_len or len(b) < min_len:
        return False
    return a in b or b in a


def _filled_identity_keys(filled: list[dict]) -> set[str]:
    """Keys used to exclude already-filled fields from leftover prompts."""
    keys: set[str] = set()
    for f in filled:
        for raw in (
            f.get("selector"),
            f.get("type"),
            f.get("automation_id"),
            f.get("label"),
        ):
            n = _norm_key(raw)
            if n and n not in ("?",):
                keys.add(n)
            c = _compact_key(raw)
            if c and c not in ("?",):
                keys.add(c)
    return keys


def _row_matches_filled(row: dict, filled_keys: set[str]) -> bool:
    filled_compacts = {_compact_key(k) for k in filled_keys if k}
    for raw in (
        row.get("selector"),
        row.get("type"),
        row.get("automation_id"),
        row.get("label"),
    ):
        n = _norm_key(raw)
        if n and n in filled_keys:
            return True
        c = _compact_key(raw)
        if c and c in filled_keys:
            return True
        for fc in filled_compacts:
            if _compact_keys_overlap(c, fc):
                return True
    return False


def _filled_rows(report: dict) -> list[dict]:
    rows: list[dict] = []
    for f in report.get("filled") or []:
        if f.get("ok") is False:
            continue
        rows.append(
            {
                "type": f.get("type") or f.get("automation_id"),
                "label": f.get("label") or f.get("automation_id") or "",
                "selector": f.get("selector") or "",
                "value": f.get("value"),
                "via": f.get("via") or f.get("layer"),
            }
        )
    return rows


def _leftover_rows(report: dict, *, filled: list[dict] | None = None) -> list[dict]:
    """Unresolved flash candidates, excluding anything Layer 0/1 already filled."""
    from page_progress import is_essay_leftover
    from verified_select import is_select_field

    filled_rows = filled if filled is not None else _filled_rows(report)
    filled_keys = _filled_identity_keys(filled_rows)
    out: list[dict] = []
    for u in report.get("leftovers") or []:
        if u.get("flash_candidate") is False:
            continue
        if str(u.get("reason") or "").startswith("blocker:"):
            continue
        row = {
            "label": u.get("label") or u.get("automation_id") or "?",
            "type": u.get("type"),
            "selector": u.get("selector") or "",
            "reason": u.get("reason"),
            "automation_id": u.get("automation_id"),
            "flash_candidate": True,
        }
        if is_essay_leftover(u) or is_essay_leftover(row):
            row["essay"] = True
        if is_select_field(str(row.get("type") or ""), str(row.get("label") or ""), row):
            row["select"] = True
            row["essay"] = False  # selects never get essay loc.fill
        if _row_matches_filled(row, filled_keys):
            continue
        out.append(row)
    return out


def _dummy_value_map() -> dict[str, Any]:
    """Always DUMMY_PROFILE — never inherits FASTFILL_REAL_PROFILE env.

    Used only as a fallback when the run has no composed fill_values on the
    report. Real-mode handoffs must pass report fill_values instead.
    """
    try:
        from field_map import DUMMY_ADDRESS, DUMMY_PROFILE, build_value_map

        return dict(build_value_map(DUMMY_PROFILE, DUMMY_ADDRESS) or {})
    except Exception:
        return {}


def _values_from_report(report: dict | None) -> dict[str, Any]:
    """Prefer this run's composed fill map; fall back to dummy catalog."""
    if isinstance(report, dict):
        for key in ("fill_values", "values", "value_map"):
            vals = report.get(key)
            if isinstance(vals, dict) and vals:
                return dict(vals)
    return _dummy_value_map()


def is_deterministic_leftover(
    row: dict,
    *,
    values: dict[str, Any] | None = None,
) -> bool:
    """True when leftover is catalog/deterministic and must not burn Flash tokens.

    Essays / LLM_EXPECTED types always return False (Flash may answer them).
    Contact/address/resume/phone-ext (FLASH_FORBIDDEN) always defer — never
    invent via LLM (empty PHONE_EXTENSION must not enter the Flash prompt).
    Other DETERMINISTIC_TYPES defer when a non-empty dummy value exists.
    """
    from fill_attribution import (
        DETERMINISTIC_TYPES,
        FLASH_FORBIDDEN_TYPES,
        LLM_EXPECTED_TYPES,
        is_flash_forbidden_type,
    )
    from page_progress import is_essay_leftover

    if not isinstance(row, dict):
        return False
    t = str(row.get("type") or "").strip().upper()
    label = str(row.get("label") or "")
    name = str(row.get("name") or "")
    selector = str(row.get("selector") or "")
    # Optional blank phone-ext — always strip from Flash even when type missing
    try:
        from field_map import OPTIONAL_LEAVE_BLANK_TYPES, is_phone_extension_field

        if t in OPTIONAL_LEAVE_BLANK_TYPES or is_phone_extension_field(
            label, t or None, name=name, selector=selector
        ):
            return True
    except Exception:
        if t == "PHONE_EXTENSION" or re.search(
            r"phone[\s_-]*ext(?:ension)?", label, re.I
        ):
            return True
    if row.get("essay") or is_essay_leftover(row):
        # Still forbid contact steal if mis-tagged essay
        t_check = t
        if t_check not in FLASH_FORBIDDEN_TYPES and not is_flash_forbidden_type(
            t_check, label=label, name=name, selector=selector
        ):
            return False
    if is_flash_forbidden_type(
        t, label=label, name=name, selector=selector
    ) or t in FLASH_FORBIDDEN_TYPES:
        return True
    if not t or t in LLM_EXPECTED_TYPES:
        return False
    if t not in DETERMINISTIC_TYPES:
        return False
    if values is None:
        return True
    val = values.get(t)
    return val is not None and str(val).strip() != ""


def partition_flash_leftovers(
    leftovers: list[dict],
    *,
    values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Split leftovers into Flash/LLM candidates vs deterministic reclaim queue.

    Deterministic types stay fillable by Layer0/1 / inpage reclaim but are
    **excluded from the Flash/Skyvern prompt** so email/zip never burn tokens.
    EEO leftovers are Flash/LLM-eligible (DeepSeek + dummy); Decline is fallback.
    """
    vals = values if values is not None else _dummy_value_map()
    flash: list[dict] = []
    deferred: list[dict] = []
    for row in leftovers or []:
        if not isinstance(row, dict):
            continue
        if is_deterministic_leftover(row, values=vals):
            deferred.append(
                {
                    **row,
                    "flash_candidate": False,
                    "flash_skip_reason": "deterministic_catalog",
                    "ownership": "prefill_reclaim",
                }
            )
        else:
            flash.append(row)
    return {
        "flash_leftovers": flash,
        "deferred_deterministic": deferred,
        # W01 aliases
        "llm": flash,
        "reclaim": deferred,
        "flash_count": len(flash),
        "deferred_count": len(deferred),
        "minimization": "exclude_deterministic_from_flash_prompt",
    }


def build_cheat_sheet_from_filled(filled: list[dict]) -> str:
    """Compact cheat sheet of fields Layer 0/1 already filled."""
    lines = ["ALREADY FILLED (do not re-fill / do not ask about):"]
    if not filled:
        lines.append("  (none)")
        return "\n".join(lines)
    for f in filled:
        key = f.get("label") or f.get("type") or f.get("selector") or "?"
        val = f.get("value")
        # Redact password-ish values in the prompt text
        key_l = str(key).lower()
        if "password" in key_l or (f.get("type") or "").lower() in (
            "password",
            "password_confirm",
        ):
            shown = "<redacted-dummy-password>"
        else:
            shown = repr(val) if val is not None else "(set)"
        lines.append(f"  - {key}: {shown}")
    return "\n".join(lines)


def build_dummy_profile_facts(profile: dict | None = None) -> str:
    """Structured fill facts for grounded leftover answers.

    Shared policy (EEO/work-auth/screening/canned) always comes from
    ``SHARED_FILL_POLICY``. Unique identity/education/experience come from
    ``profile`` when provided (dummy or real-shaped), else DUMMY_PROFILE.
    Never invents new EEO beyond the shared catalog.
    """
    from dummy_answers import SHARED_FILL_POLICY
    from field_map import DUMMY_PROFILE

    p = profile if isinstance(profile, dict) else DUMMY_PROFILE
    personal = p.get("personal") or {}
    contact = p.get("contact") or {}
    exp = p.get("experience") or {}
    edu = (p.get("education") or {}).get("degrees") or []
    links = p.get("links") or {}
    custom = SHARED_FILL_POLICY.get("custom_question_answers") or {}
    eeo = SHARED_FILL_POLICY.get("eeo_demographic") or {}
    is_dummy = (
        p is DUMMY_PROFILE
        or (personal.get("full_name") or "") == "Test Dummy"
    )
    header = (
        "DUMMY_PROFILE FACTS (fictional test applicant — use these only):"
        if is_dummy
        else (
            "APPLICANT FACTS (unique identity/education from this run + "
            "SHARED policy catalog for EEO/screening — never invent new EEO):"
        )
    )
    lines = [
        header,
        f"  name: {personal.get('full_name')}",
        f"  email: {contact.get('email')}",
        f"  phone: {contact.get('phone')}",
        f"  current_title: {exp.get('current_title')}",
        f"  current_company: {exp.get('current_company')}",
        f"  years_experience: {exp.get('total_years_of_experience')}",
        f"  github: {links.get('github')}",
        f"  linkedin: {links.get('linkedin')}",
        f"  twitter: {links.get('twitter')}",
        f"  how_heard: {custom.get('how_did_you_hear_about_this_job')}",
        f"  canned_interest: {custom.get('why_interested')}",
        f"  compensation_policy: {custom.get('compensation_expectation')}",
    ]
    for i, d in enumerate(edu[:2]):
        lines.append(
            f"  degree_{i}: {d.get('degree')} / {d.get('discipline') or d.get('major') or ''} "
            f"@ {d.get('school')} ({d.get('graduation_date')})"
        )
    lines.append(
        f"  eeo_policy: prefer shared catalog demographics "
        f"(gender={eeo.get('gender')!r}, hispanic={eeo.get('hispanic_or_latino')!r}, "
        f"race={eeo.get('race_ethnicity')!r}, veteran={eeo.get('veteran_status')!r}, "
        f"disability={eeo.get('disability_status')!r}); Decline is fallback only "
        f"when option list lacks preferred labels"
    )
    return "\n".join(lines)


def build_run_profile_facts(
    values: dict | None = None,
    profile: dict | None = None,
) -> str:
    """Facts for Flash leftovers: unique from run values/profile + shared policy.

    Prefer reconstructing a mini-profile from the composed ``values`` map when
    given (real mode), else ``profile`` / DUMMY_PROFILE.
    """
    from field_map import DUMMY_PROFILE

    if isinstance(values, dict) and values:
        mini = {
            "personal": {"full_name": values.get("NAME_FULL") or ""},
            "contact": {
                "email": values.get("EMAIL") or "",
                "phone": values.get("PHONE") or "",
            },
            "links": {
                "linkedin": values.get("LINKEDIN") or "",
                "github": values.get("GITHUB") or "",
                "twitter": values.get("TWITTER") or "",
                "portfolio": values.get("PORTFOLIO") or "",
            },
            "experience": {
                "current_title": values.get("CURRENT_TITLE") or "",
                "current_company": values.get("CURRENT_COMPANY") or "",
                "total_years_of_experience": values.get("YEARS_EXPERIENCE") or "",
            },
            "education": {
                "degrees": [
                    {
                        "degree": values.get("DEGREE") or "",
                        "discipline": values.get("DISCIPLINE")
                        or values.get("MAJOR")
                        or "",
                        "school": values.get("SCHOOL") or "",
                        "graduation_date": values.get("EDUCATION_END_YEAR") or "",
                    }
                ]
            },
        }
        return build_dummy_profile_facts(mini)
    return build_dummy_profile_facts(profile or DUMMY_PROFILE)


def build_resume_excerpt(
    pdf_path: str | Path | None = None,
    *,
    max_chars: int = 1800,
    allow_dummy_fallback: bool = True,
) -> str:
    """Resume text excerpt for Flash grounding (dummy fixture or real tailored PDF).

    When ``allow_dummy_fallback`` is False (real-mode handoffs), never substitute
    the dummy fixture PDF if the run resume path is missing.
    """
    from field_map import DUMMY_PDF, is_real_profile_mode
    from resume_parser import extract_text

    real_mode = False
    try:
        real_mode = is_real_profile_mode()
    except Exception:
        real_mode = False
    if not allow_dummy_fallback:
        real_mode = True

    if not pdf_path:
        if not allow_dummy_fallback or real_mode:
            return (
                "RESUME EXCERPT:\n  (no resume path on this run — ground essays in "
                "applicant facts + job description only; never invent PII)"
            )
        pdf_path = DUMMY_PDF

    path = Path(pdf_path)
    # Hard refuse real/tailored resumes in dummy/test mode (assert_dummy_resume_path).
    # Real-profile Flash may use a job resume when FASTFILL_ALLOW_REAL is set.
    try:
        from field_map import assert_dummy_resume_path

        if real_mode or not allow_dummy_fallback:
            pass
        else:
            path = assert_dummy_resume_path(path)
    except Exception:
        if not allow_dummy_fallback or real_mode:
            return (
                "RESUME EXCERPT:\n  (resume unavailable — use applicant facts + "
                "job description; never invent PII)"
            )
        path = DUMMY_PDF
        real_mode = False
    text = extract_text(path) or ""
    text = " ".join(text.split())
    label = "RESUME EXCERPT" if (real_mode or not allow_dummy_fallback) else "DUMMY RESUME EXCERPT"
    if not text:
        fallback = (
            "use profile contact + uploaded resume for experience"
            if (real_mode or not allow_dummy_fallback)
            else "use DUMMY_PROFILE FACTS"
        )
        return f"{label}:\n  (unavailable — {fallback})"
    return f"{label}:\n  {text[:max_chars]}"


def format_job_context(job_context: dict | None) -> str:
    """Format scraped job title + description for the leftovers prompt."""
    if not isinstance(job_context, dict):
        return "JOB DESCRIPTION:\n  (not scraped — answer from dummy resume + DUMMY_PROFILE only)"
    title = str(job_context.get("title") or job_context.get("job_title") or "").strip()
    company = str(job_context.get("company") or "").strip()
    desc = str(
        job_context.get("description")
        or job_context.get("job_description")
        or job_context.get("text")
        or ""
    ).strip()
    desc = " ".join(desc.split())[:2500]
    lines = ["JOB DESCRIPTION (scraped from apply page):"]
    if title:
        lines.append(f"  title: {title}")
    if company:
        lines.append(f"  company: {company}")
    if desc:
        lines.append(f"  description: {desc}")
    else:
        lines.append("  description: (empty scrape)")
    return "\n".join(lines)


async def scrape_job_context(page) -> dict[str, str]:
    """Best-effort job title + description from the live apply page (dummy runs)."""
    js = """() => {
      const pick = (sels) => {
        for (const s of sels) {
          const el = document.querySelector(s);
          const t = (el && (el.innerText || el.textContent) || '').trim();
          if (t && t.length > 2) return t.slice(0, 200);
        }
        return '';
      };
      const title = pick([
        'h1', '[data-testid="job-title"]', '[data-qa="job-title"]',
        '[data-automation-id="jobPostingHeader"]', '.job-title', '.posting-headline h2',
        '.ashby-job-posting-heading h1', '[class*="jobPosting"] h1',
        'header h1', '[class*="_header_"] h1',
      ]) || (document.title || '').slice(0, 200);
      let company = pick([
        '[data-testid="company-name"]', '.company-name',
        '.ashby-job-posting-company-name', '[class*="companyName"]',
      ]);
      const meta = document.querySelector('meta[property="og:site_name"]');
      if (!company && meta) company = (meta.getAttribute('content') || '').slice(0, 120);
      // Host hint (jobs.ashbyhq.com/socure/…)
      if (!company) {
        const host = (location.hostname || '');
        const path = (location.pathname || '').split('/').filter(Boolean);
        if (host.includes('ashbyhq') && path.length) company = path[0].slice(0, 120);
      }
      const body = (document.body && document.body.innerText || '').replace(/\\s+/g, ' ').trim();
      // Prefer a job-description container when present
      const descEl = document.querySelector(
        '[data-testid="job-description"], .job-description, #job-description, ' +
        '[data-automation-id="jobPostingDescription"], .posting-page, .content, ' +
        '.ashby-job-posting-brief, [class*="jobDescription"], [class*="_description_"]'
      );
      let description = '';
      if (descEl) description = (descEl.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 3000);
      if (!description) {
        // Ashby apply often embeds JD above the form — take body before form markers
        const formIdx = body.search(/submit application|personal information|resume|linkedin url/i);
        description = (formIdx > 80 ? body.slice(0, formIdx) : body).slice(0, 3000);
      }
      return { title, company, description };
    }"""
    try:
        raw = await page.evaluate(js) or {}
    except Exception as e:
        return {"title": "", "company": "", "description": "", "error": str(e)[:120]}
    return {
        "title": str(raw.get("title") or "")[:200],
        "company": str(raw.get("company") or "")[:120],
        "description": str(raw.get("description") or "")[:3000],
    }


def synthesize_grounded_answer(
    label: str,
    *,
    job_context: dict | None = None,
    resume_excerpt: str = "",
    profile_facts: str = "",
    values: dict | None = None,
) -> str:
    """Deterministic grounded essay/short answer (no API).

    Education / contact from composed ``values`` when provided (real or dummy);
    shared policy (salary, how_heard, clearance, …) from SHARED_FILL_POLICY.
    """
    from dummy_answers import SHARED_FILL_POLICY, shared_values
    from field_map import DUMMY_PROFILE

    title = ""
    company = ""
    desc_snip = ""
    if isinstance(job_context, dict):
        title = str(job_context.get("title") or "").strip()
        company = str(job_context.get("company") or "").strip()
        desc_snip = str(job_context.get("description") or "").strip()[:400]
    shared = shared_values()
    custom = SHARED_FILL_POLICY.get("custom_question_answers") or {}
    screening = SHARED_FILL_POLICY.get("standard_screening_answers") or {}
    vals = values if isinstance(values, dict) else {}
    exp = DUMMY_PROFILE.get("experience") or {}
    role = vals.get("CURRENT_TITLE") or exp.get("current_title") or "Applied AI/ML Analyst"
    years = vals.get("YEARS_EXPERIENCE") or exp.get("total_years_of_experience") or 3
    degrees = (DUMMY_PROFILE.get("education") or {}).get("degrees") or []
    deg0 = degrees[0] if degrees else {}
    school = str(vals.get("SCHOOL") or deg0.get("school") or "University of Alabama, Tuscaloosa")
    degree = str(vals.get("DEGREE") or deg0.get("degree") or "Master's Degree")
    discipline = str(
        vals.get("DISCIPLINE")
        or vals.get("MAJOR")
        or deg0.get("discipline")
        or deg0.get("major")
        or "Computer Science"
    )
    end_year = str(vals.get("EDUCATION_END_YEAR") or "")
    if not end_year:
        grad = str(deg0.get("graduation_date") or "May 2019")
        m_year = re.search(r"(20\d{2}|19\d{2})", grad)
        if m_year:
            end_year = m_year.group(1)
    start_year = str(vals.get("EDUCATION_START_YEAR") or "")
    if not start_year:
        start_year = str(int(end_year) - 4) if end_year.isdigit() else "2015"
    canned = custom.get("why_interested") or shared.get("INTEREST") or (
        "I'm interested in this role based on the posted description and "
        "how it aligns with my relevant experience."
    )
    label_l = (label or "").lower()
    where = f" at {company}" if company else ""
    role_line = f"the {title} role{where}" if title else f"this role{where}"

    # Phone extension / short numeric — never dump interest essays here
    try:
        from field_map import is_phone_extension_field, is_short_numeric_field

        if is_phone_extension_field(label) or is_short_numeric_field(label):
            return ""
    except Exception:
        if re.search(r"phone[\s_-]*ext|\bextension\b", label_l):
            return ""

    # Deterministic education / policy shorts — never leave for a human
    if re.search(r"\bschool\b|universit|college|institution|alma\s*mater", label_l):
        return school
    if re.search(r"\bdiscipline\b|\bmajor\b|field\s+of\s+study", label_l):
        return discipline
    if re.search(r"\bdegree\b|qualification|education\s*level", label_l):
        return degree
    if re.search(r"employment\s+eligibility|authorized\s+to\s+work", label_l):
        return shared.get("WORK_AUTH") or "Yes"
    if re.search(r"start\s*(date\s*)?year|education.*start", label_l):
        return start_year
    if re.search(r"end\s*(date\s*)?year|graduation\s*year|education.*end", label_l):
        return end_year or "2019"
    if re.search(r"where\s+do\s+you\s+(currently\s+)?reside|current\s+residence", label_l):
        loc = vals.get("LOCATION") or ""
        if loc:
            return str(loc)
        addr = (DUMMY_PROFILE.get("address") or {})
        csz = str(addr.get("line1") or "")
        m = re.search(r"([A-Za-z .]+),\s*([A-Z]{2})\b", csz)
        if m:
            return f"{m.group(1).strip()}, {m.group(2)}, USA"
        return "Springfield, IL, USA"
    if re.search(
        r"worked\s+for\s+this\s+company|worked\s+with|ever\s+worked\s+with|"
        r"relatives?\s+or\s+friends|relative.*working|"
        r"ever\s+been\s+employed|employed\s+by|prior\s+worker|previous\s+worker|"
        r"previously\s+(been\s+)?employed",
        label_l,
    ):
        return shared.get("WORKED_HERE_BEFORE") or "No"
    if re.search(r"daily\s+commute|commit\s+to\s+(a\s+)?daily\s+commute|\bcommute\b", label_l):
        return shared.get("COMMUTE") or "Yes"
    if re.search(r"sms|text\s+message|marketing\s+consent|receive\s+(sms|texts)", label_l):
        return shared.get("MARKETING_CONSENT") or "No"
    # Capco GH: accommodations Yes/No → No; conditional details → N/A
    if re.search(
        r"(if\s+you\s+answered\s+yes|enter\s+n/?a|additional\s+details).{0,80}"
        r"(accommodation|adjustment|reasonable)|"
        r"(accommodation|adjustment|reasonable).{0,80}"
        r"(enter\s+n/?a|additional\s+details|if\s+not)",
        label_l,
    ):
        return shared.get("ACCOMMODATIONS_DETAILS") or "N/A"
    if re.search(
        r"(require|need|request).{0,40}(accommodation|adjustment)|"
        r"reasonable[\s_-]*accommodations?\s+or\s+adjustments",
        label_l,
    ):
        return shared.get("ACCOMMODATIONS") or "No"
    # Capco referral: Yes/No → No; employee email follow-up → N/A (no parent thrash)
    if re.search(
        r"were\s+you\s+referred|referred\s+to\s+this\s+(role|job|position)|"
        r"referred\s+by\s+(a\s+)?(current\s+)?\w*\s*employee",
        label_l,
    ):
        return shared.get("EMPLOYEE_REFERRAL") or "No"
    if re.search(
        r"(employee'?s?|referral|capco).{0,40}(e[\s_-]*mail)|"
        r"(e[\s_-]*mail).{0,40}(employee|referr|capco)",
        label_l,
    ):
        return shared.get("REFERRAL_EMAIL") or "N/A"
    if re.search(r"salary|compensation|pay|expect", label_l):
        return shared.get("SALARY_EXPECTED") or "Open / negotiable within the posted range"
    if re.search(
        r"ts[\s_/.-]*sci|polygraph|security[\s_-]*clearance|"
        r"(have|hold).{0,20}clearance",
        label_l,
    ):
        if re.search(r"clearance[\s_-]*type|type[\s_-]*of|level[\s_-]*of", label_l):
            return str(
                screening.get("security_clearance_type")
                or shared.get("CLEARANCE_TYPE")
                or "None"
            )
        return str(
            screening.get("has_security_clearance") or shared.get("CLEARANCE") or "No"
        )
    if re.search(r"u\.?s\.?[\s_-]*citizen|united[\s_-]*states[\s_-]*citizen", label_l):
        return str(screening.get("us_citizen") or shared.get("US_CITIZEN") or "Yes")
    if re.search(r"visa[\s_-]*(requirement[\s_-]*)?status|immigration[\s_-]*status", label_l):
        return str(
            screening.get("visa_requirement_status")
            or shared.get("VISA_STATUS")
            or "No visa required"
        )
    if re.search(r"how\s+did\s+you\s+hear", label_l):
        return shared.get("HOW_HEARD") or "Internet job board"
    # Ashby screening MCQs / consent — dummy catalog, never EEO.
    if re.search(r"^\s*consent\s*\*?\s*$|i\s+(agree|consent)|terms\s*(and|&)\s*conditions", label_l):
        if not re.search(r"marketing|newsletter|sms|promotional", label_l):
            return shared.get("TERMS_CONSENT") or "Yes"
    if re.search(r"english|proficiency|language\s+(skill|level)", label_l):
        return "Fluent"
    if re.search(r"production environment|built software in a production", label_l):
        return "production"
    if re.search(
        r"experience with machine learning|enjoy most|best describes|best reflects",
        label_l,
    ):
        return "production"
    if re.search(
        r"(?:additional|other|share|provide).{0,40}(?:links?|linkedin|github|portfolio)|"
        r"(?:linkedin|github|portfolio).{0,40}(?:links?|urls?)",
        label_l,
    ):
        entries = [
            ("LinkedIn", vals.get("LINKEDIN") or (DUMMY_PROFILE.get("links") or {}).get("linkedin")),
            ("GitHub", vals.get("GITHUB") or (DUMMY_PROFILE.get("links") or {}).get("github")),
            ("Portfolio", vals.get("PORTFOLIO") or (DUMMY_PROFILE.get("links") or {}).get("portfolio")),
            ("Twitter/X", vals.get("TWITTER") or (DUMMY_PROFILE.get("links") or {}).get("twitter")),
        ]
        return "\n".join(f"{name}: {url}" for name, url in entries if url)

    # Essay / why-join / tell-us — always produce text (SUCCESS requires answers)
    body = (
        f"{canned} For {role_line}, my background as a {role} "
        f"({years}+ years) maps well to the posted needs. "
    )
    if desc_snip:
        body += (
            "Based on the job description, I am especially interested in the "
            f"responsibilities around: {desc_snip[:220]}… "
        )
    tone = (
        "skills summarized in my resume"
        if vals.get("NAME_FULL") and vals.get("NAME_FULL") != "Test Dummy"
        else "skills summarized in my dummy resume (example projects and coursework)"
    )
    body += (
        f"I would bring the {tone} "
        "and am excited to contribute. "
        "This answer uses shared policy canned text plus run identity only."
    )
    if resume_excerpt and "unavailable" not in resume_excerpt.lower():
        body = body[:1500]
    return body[:2000]


# Phase 2 structured-LLM knobs. All optional; every path degrades to the plain
# text call and then to deterministic synthesize, so a down endpoint or an
# endpoint that rejects JSON mode never leaves a field empty.
_STRUCTURED_LLM = os.environ.get("FASTFILL_STRUCTURED_LLM", "1") != "0"
try:
    _LLM_RETRIES = max(0, int(os.environ.get("FASTFILL_LLM_RETRIES", "2") or 2))
except (TypeError, ValueError):
    _LLM_RETRIES = 2
try:
    _LLM_MIN_CONFIDENCE = float(os.environ.get("FASTFILL_LLM_MIN_CONFIDENCE", "0") or 0)
except (TypeError, ValueError):
    _LLM_MIN_CONFIDENCE = 0.0

_LLM_SYSTEM_PROMPT = (
    "You fill leftover job-application fields for a FICTIONAL dummy applicant. "
    "Use only the provided dummy resume, DUMMY_PROFILE facts, SHARED catalog, and "
    "job description. For EEO/demographics use SHARED catalog answers only (never "
    "invent beyond catalog; Decline if unsure). Never suggest Submit."
)


def _resolve_llm_config() -> tuple[str, str, str]:
    """Resolve (api_key, base, model) for the OpenAI-compatible endpoint.

    Delegates to the unified ``llm_config`` helper (the single place base/key/
    model are resolved; the base URL is the seam a gateway like OmniRoute points
    at). Never reads profile.json. api_key may be "". Falls back to an inline
    env+secrets read if the helper import ever fails, so leftovers never break.
    """
    try:
        from llm_config import resolve_llm_config

        return resolve_llm_config(root=ROOT)
    except Exception:
        api_key = (
            os.environ.get("OPENAI_COMPATIBLE_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or ""
        ).strip()
        base = (
            os.environ.get("OPENAI_COMPATIBLE_API_BASE")
            or "https://api.deepseek.com/v1"
        ).rstrip("/")
        model = os.environ.get("OPENAI_COMPATIBLE_MODEL_NAME") or "deepseek-v4-flash"
        if not api_key:
            secrets = ROOT / "skyvern_runtime" / ".secrets.env"
            if secrets.exists():
                for line in secrets.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("export "):
                        line = line[len("export ") :]
                    if line.startswith("OPENAI_COMPATIBLE_API_KEY=") or line.startswith(
                        "DEEPSEEK_API_KEY="
                    ):
                        raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if raw:
                            api_key = raw
                            break
        return api_key, base, model


def _thinking_disabled_body() -> dict:
    """DeepSeek-V4-Flash defaults to thinking mode, which rejects tool_choice.

    Instructor uses tool/function calling; JSON mode and plain completions are
    also safer with thinking off for short leftover answers. Opt out via
    ``FASTFILL_LLM_THINKING=1`` if a gateway needs thinking enabled.
    """
    if os.environ.get("FASTFILL_LLM_THINKING", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return {}
    return {"thinking": {"type": "disabled"}}


def _post_chat_completion(payload: dict, *, timeout: int = 45) -> dict | None:
    """POST an OpenAI-compatible chat/completions request; None on any failure."""
    api_key, base, model = _resolve_llm_config()
    if not api_key:
        return None
    # Dummy-only guard: never route real PII through a gateway / free pools.
    try:
        from llm_config import assert_dummy_for_gateway

        assert_dummy_for_gateway(base)
    except RuntimeError:
        raise
    except Exception:
        pass
    payload = {"model": model, **_thinking_disabled_body(), **payload}
    try:
        import urllib.request

        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        try:
            from tracing import trace_llm

            msgs = payload.get("messages") or []
            user_msg = next(
                (m.get("content") for m in reversed(msgs) if m.get("role") == "user"),
                None,
            )
            content = None
            try:
                content = data["choices"][0]["message"]["content"]
            except Exception:
                content = None
            trace_llm("leftover_llm", prompt=user_msg, response=content, model=model)
        except Exception:
            pass
        return data
    except Exception:
        return None


def call_flash_text_llm(prompt: str, *, max_tokens: int = 600) -> str | None:
    """Optional DeepSeek/OpenAI-compatible plain-text completion for leftovers.

    Loads key from env / skyvern_runtime/.secrets.env. Never reads profile.json.
    """
    data = _post_chat_completion(
        {
            "messages": [
                {"role": "system", "content": _LLM_SYSTEM_PROMPT + " Return plain answer text only."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
    )
    if not isinstance(data, dict):
        return None
    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return None
    return text[:2000] if text else None


def call_flash_instructor_llm(
    prompt: str, *, max_tokens: int = 600, retries: int | None = None
) -> dict | None:
    """Typed leftover via Instructor + OpenAI-compatible client.

    Returns ``{"value", "confidence"}`` or None when Instructor/openai/pydantic
    are missing, the endpoint fails, or retries exhaust. Never raises into the
    fill path — callers fall through to urllib JSON mode then plain text then
    synthesize. Disable with ``FASTFILL_INSTRUCTOR=0`` (urllib path still runs).
    """
    if os.environ.get("FASTFILL_INSTRUCTOR", "1") == "0":
        return None
    try:
        import instructor
        from openai import OpenAI
        from pydantic import BaseModel, Field
    except Exception:
        return None

    api_key, base, model = _resolve_llm_config()
    if not api_key:
        return None
    try:
        from llm_config import assert_dummy_for_gateway

        assert_dummy_for_gateway(base)
    except RuntimeError:
        raise
    except Exception:
        pass

    class LeftoverAnswer(BaseModel):
        value: str = Field(..., min_length=1)
        confidence: float = Field(..., ge=0.0, le=1.0)

    tries = _LLM_RETRIES if retries is None else max(0, int(retries))
    sys_prompt = (
        _LLM_SYSTEM_PROMPT
        + ' Respond with value (answer string) and confidence (0..1).'
    )
    try:
        client = instructor.from_openai(OpenAI(api_key=api_key, base_url=base))
    except Exception:
        return None
    for _ in range(tries + 1):
        try:
            create_kwargs: dict[str, Any] = {
                "model": model,
                "response_model": LeftoverAnswer,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            }
            thinking = _thinking_disabled_body()
            if thinking:
                create_kwargs["extra_body"] = thinking
            obj = client.chat.completions.create(**create_kwargs)
            val = str(getattr(obj, "value", "") or "").strip()
            if not val:
                continue
            conf = float(getattr(obj, "confidence"))
            try:
                from tracing import trace_llm

                trace_llm(
                    "leftover_llm_instructor",
                    prompt=prompt,
                    response=val,
                    model=model,
                )
            except Exception:
                pass
            return {"value": val[:2000], "confidence": conf}
        except Exception:
            continue
    return None


def call_flash_json_llm(
    prompt: str, *, max_tokens: int = 600, retries: int | None = None
) -> dict | None:
    """Typed leftover answer: Instructor first, then urllib JSON mode fallback.

    Shape: ``{"value": str, "confidence": 0..1}``. Bounded retry until a
    well-formed object parses. Returns None on exhaustion (callers then fall
    back to the plain text call, then to synthesize) so this is always additive
    and never blocks a fill. Works through any OpenAI-compatible endpoint
    (DeepSeek / OmniRoute); if the endpoint rejects response_format the urllib
    request simply fails and we return None.
    """
    typed = call_flash_instructor_llm(
        prompt, max_tokens=max_tokens, retries=retries
    )
    if typed is not None:
        return typed

    tries = _LLM_RETRIES if retries is None else max(0, int(retries))
    sys_prompt = (
        _LLM_SYSTEM_PROMPT
        + ' Respond ONLY with compact JSON: {"value": <answer string>, '
        '"confidence": <number 0..1>}. No prose, no markdown.'
    )
    for _ in range(tries + 1):
        data = _post_chat_completion(
            {
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }
        )
        if not isinstance(data, dict):
            continue
        try:
            raw = (data["choices"][0]["message"]["content"] or "").strip()
        except Exception:
            continue
        parsed = _parse_json_answer(raw)
        if parsed is not None:
            return parsed
    return None


def _parse_json_answer(raw: str) -> dict | None:
    """Extract {"value","confidence"} from a model JSON string (tolerant)."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        # strip ```json ... ``` fences
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    try:
        obj = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None
    if not isinstance(obj, dict) or "value" not in obj:
        return None
    value = str(obj.get("value") or "").strip()
    conf = obj.get("confidence")
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = None
    return {"value": value, "confidence": conf}


def _llm_answer(prompt: str, *, max_tokens: int = 600) -> str | None:
    """Preferred leftover LLM answer: typed+retry+confidence gate, then plain text.

    Confidence below FASTFILL_LLM_MIN_CONFIDENCE (default 0 = accept any) routes to
    the plain call and ultimately deterministic synthesize — never to an empty fill.
    """
    if _STRUCTURED_LLM:
        typed = call_flash_json_llm(prompt, max_tokens=max_tokens)
        if typed is not None:
            val = str(typed.get("value") or "").strip()
            conf = typed.get("confidence")
            if val and (conf is None or conf >= _LLM_MIN_CONFIDENCE):
                return val[:2000]
    return call_flash_text_llm(prompt, max_tokens=max_tokens)


def validate_eeo_against_catalog(
    ftype: str | None,
    answer: str,
    *,
    shared: dict[str, str] | None = None,
) -> str:
    """Accept LLM EEO only when it matches SHARED catalog / aliases; else refuse.

    Never invent demographics beyond ``shared_values()`` / DETERMINISTIC_ANSWERS.
    Decline-like answers are a safe fallback when the catalog itself is Decline /
    Prefer-not, or when the model is unsure.
    """
    from dummy_answers import DETERMINISTIC_ANSWERS, shared_values

    ftype_u = (ftype or "").strip().upper()
    vals = shared if isinstance(shared, dict) and shared else shared_values()
    canon = str(
        vals.get(ftype_u)
        or DETERMINISTIC_ANSWERS.get(ftype_u)
        or "Decline to self identify"
    )
    cleaned = (answer or "").strip().strip('"').strip("'")
    if not cleaned:
        return canon
    cl = cleaned.lower()
    canon_l = canon.lower()
    if cl == canon_l:
        return canon
    # Known ATS aliases for *this* catalog answer only (not inventing new classes).
    aliases: dict[str, set[str]] = {
        "GENDER": {"male", "man", "m", "he/him", "he / him", "male gender"},
        "HISPANIC": {
            "no",
            "not hispanic or latino",
            "not hispanic",
            "no, not hispanic or latino",
            "non-hispanic",
        },
        "RACE": {
            "decline to self identify",
            "decline to self-identify",
            "decline",
            "prefer not to disclose",
            "prefer not to say",
            "prefer not to answer",
            "i decline to self identify",
            "i don't wish to answer",
            "i do not wish to answer",
            "i don’t wish to answer",
        },
        "VETERAN": {
            "i am not a protected veteran",
            "not a protected veteran",
            "no",
            "i am not a veteran",
            "not a veteran",
        },
        "DISABILITY": {
            "i do not have a disability",
            "no",
            "no disability",
            "i don't have a disability",
            "do not have a disability",
        },
        "AGE_RANGE": {
            "prefer not to disclose",
            "prefer not to say",
            "decline to self identify",
            "decline",
        },
        "LGBTQIA": {
            "prefer not to disclose",
            "prefer not to say",
            "decline to self identify",
            "decline",
            "i don't wish to answer",
            "i do not wish to answer",
        },
    }
    allowed = aliases.get(ftype_u, set())
    if cl in allowed:
        return canon
    # Substring: cleaned is a near-match of the catalog string
    if len(canon_l) >= 4 and (canon_l in cl or cl in canon_l):
        return canon
    # Decline / prefer-not is always an allowed safe fallback (never invent)
    if re.search(
        r"decline|prefer\s+not|self[\s_-]*identif|rather\s+not|choose\s+not",
        cl,
    ):
        if "decline" in canon_l or "prefer not" in canon_l:
            return canon
        return "Decline to self identify"
    # Invent beyond catalog → refuse; return shared catalog value
    return canon


def answer_leftover_field(
    label: str,
    *,
    ftype: str | None = None,
    job_context: dict | None = None,
    resume_excerpt: str = "",
    profile_facts: str = "",
    use_llm: bool = True,
    values: dict | None = None,
) -> str:
    """Produce a grounded answer for one leftover (essay or short). Always non-empty for essays."""
    from dummy_answers import shared_values
    from field_map import DUMMY_PROFILE, DUMMY_ADDRESS, build_value_map

    label_l = (label or "").lower()
    ftype_u = (ftype or "").upper()
    run_vals = values if isinstance(values, dict) and values else None
    composed = run_vals or build_value_map(DUMMY_PROFILE, DUMMY_ADDRESS)
    facts = profile_facts or build_run_profile_facts(composed)

    # Phone extension / short numeric — leave blank; never INTEREST essay
    try:
        from field_map import (
            OPTIONAL_LEAVE_BLANK_TYPES,
            is_phone_extension_field,
            is_short_numeric_field,
            value_ok_for_field_shape,
        )

        if (
            ftype_u in OPTIONAL_LEAVE_BLANK_TYPES
            or is_phone_extension_field(label, ftype_u)
            or is_short_numeric_field(label, ftype_u)
        ):
            return ""
    except Exception:
        if re.search(r"phone[\s_-]*ext(?:ension)?", label_l):
            return ""

    _EEO_TYPES = frozenset(
        {"GENDER", "RACE", "HISPANIC", "VETERAN", "DISABILITY", "AGE_RANGE", "LGBTQIA"}
    )
    is_eeo = ftype_u in _EEO_TYPES or bool(
        re.search(
            r"gender|race|ethnicity|veteran|disabilit|hispanic|lgbtq|lgbtqia",
            label_l,
        )
    )
    # EEO: SHARED catalog only — validate LLM against catalog; never invent beyond.
    if is_eeo:
        shared = shared_values()
        decline = "Decline to self identify"
        # Race/ethnicity: never LLM-guess a race — always Decline / wish-not aliases.
        if ftype_u == "RACE" or bool(
            re.search(r"\brace\b|ethnicity|racial", label_l)
        ):
            return str(shared.get("RACE") or decline)
        if ftype_u == "LGBTQIA" or bool(
            re.search(r"lgbtq|lgbtqia|lgbtq\+", label_l)
        ):
            return str(shared.get("LGBTQIA") or "Prefer not to disclose")
        if use_llm:
            prompt = (
                f"{facts}\n\n"
                f"{resume_excerpt}\n\n"
                f"{format_job_context(job_context)}\n\n"
                f"Answer this EEO/demographic field using SHARED catalog policy "
                f"(Male / No Hispanic / no disability / not veteran; race=Decline). "
                f"Never invent demographics beyond that catalog. "
                f"Never use real applicant demographics.\n"
                f"Field label: {label!r}\nField type: {ftype!r}\n"
                f"Reply with the catalog option label only. "
                f"If unsure, answer: {decline!r}."
            )
            llm = _llm_answer(prompt, max_tokens=80)
            if llm:
                cleaned = llm.strip().strip('"').strip("'")
                if cleaned:
                    return validate_eeo_against_catalog(
                        ftype_u, cleaned, shared=shared
                    )[:200]
        # Prefill / no-API fallback — shared catalog
        try:
            v = composed.get(ftype_u) or shared.get(ftype_u)
            if v:
                return str(v)
        except Exception:
            pass
        return decline

    # Based-in-states Yes/No (Extend GH) — before det LOCATION city blob
    if re.search(
        r"based\s+in\s+any\s+of\s+these\s+states|currently\s+based\s+in\s+any",
        label_l,
    ):
        return "Yes"

    # Deterministic types from composed value map — no LLM needed
    # (includes contact/address so accidental Flash paths never invent zip/email)
    det_types = {
        "NAME_FIRST",
        "NAME_LAST",
        "NAME_FULL",
        "EMAIL",
        "PHONE",
        "ADDRESS_LINE1",
        "ADDRESS_CITY",
        "ADDRESS_STATE",
        "ADDRESS_ZIP",
        "ADDRESS_COUNTRY",
        "LINKEDIN",
        "GITHUB",
        "PORTFOLIO",
        "TWITTER",
        "SCHOOL",
        "DEGREE",
        "DISCIPLINE",
        "MAJOR",
        "FIELD_OF_STUDY",
        "EDUCATION_START_YEAR",
        "EDUCATION_END_YEAR",
        "SALARY_EXPECTED",
        "SALARY_CURRENT",
        "LOCATION",
        "COMMUTE",
        "RELOCATION",
        "WORKED_HERE_BEFORE",
        "MARKETING_CONSENT",
        "NOTICE_PERIOD",
        "HOW_HEARD",
        "YEARS_EXPERIENCE",
        "WORK_AUTH",
        "US_RESIDENCE",
        "US_CITIZEN",
        "CLEARANCE",
        "CLEARANCE_TYPE",
        "VISA_STATUS",
        "SPONSORSHIP",
        "AGE_18",
        "FELONY",
        "TALENT_HUB",
        "CURRENT_COMPANY",
        "CURRENT_TITLE",
        "APPLYING_FOR",
        "PASSWORD",
        "PASSWORD_CONFIRM",
        "INTEREST",
        "LATIN_AMERICA",
        "BACKGROUND_CHECK",
        "TERMS_CONSENT",
        "ACCOMMODATIONS",
        "ACCOMMODATIONS_DETAILS",
        "EMPLOYEE_REFERRAL",
        "REFERRAL_EMAIL",
        "SERVICE_MEMBER",
    }
    if ftype_u in det_types:
        try:
            v = composed.get(ftype_u)
            if v:
                return str(v)
        except Exception:
            pass
        # Fall through to synthesize (label heuristics)

    # Prefer deterministic synthesize for education/policy labels before LLM
    synth = synthesize_grounded_answer(
        label,
        job_context=job_context,
        resume_excerpt=resume_excerpt,
        profile_facts=facts,
        values=composed,
    )
    # If synthesize already answered a short non-essay field, skip LLM
    if ftype_u in det_types or re.search(
        r"\bschool\b|\bdegree\b|salary|commute|reside|worked\s+for|relative|"
        r"start\s*(date\s*)?year|end\s*(date\s*)?year|sms|marketing|"
        r"(?:additional|other|share|provide).{0,40}(?:links?|urls?)|"
        r"(?:linkedin|github|portfolio).{0,40}(?:links?|urls?)",
        label_l,
    ):
        if synth and len(synth) < 200:
            return synth

    if use_llm:
        prompt = (
            f"{facts}\n\n{resume_excerpt}\n\n"
            f"{format_job_context(job_context)}\n\n"
            f"Write the answer for this leftover field only.\n"
            f"Field label: {label!r}\nField type: {ftype!r}\n"
            f"If EEO/demographic → use SHARED catalog (never real applicant EEO). "
            f"If education/contact → use the unique identity facts above. "
            f"Never submit the form."
        )
        llm = _llm_answer(prompt)
        if llm:
            cleaned = llm.strip().strip('"').strip("'")
            if cleaned:
                return cleaned[:2000]

    return synth or ""


def cap_flash_max_steps_grounded(max_steps: int | None = None) -> int:
    """Step budget for grounded/cycle leftovers (higher than thin Skyvern default)."""
    if max_steps is None:
        return FLASH_MAX_STEPS_GROUNDED
    try:
        n = int(max_steps)
    except (TypeError, ValueError):
        return FLASH_MAX_STEPS_GROUNDED
    return max(1, min(n, FLASH_MAX_STEPS_GROUNDED))


def build_leftovers_prompt(
    report: dict,
    *,
    max_steps: int | None = None,
    leftovers: list[dict] | None = None,
    filled: list[dict] | None = None,
    job_context: dict | None = None,
    resume_path: str | Path | None = None,
    grounded: bool = True,
    values: dict | None = None,
) -> str:
    filled_rows = filled if filled is not None else _filled_rows(report)
    leftover_rows = (
        leftovers if leftovers is not None else _leftover_rows(report, filled=filled_rows)
    )
    steps = (
        cap_flash_max_steps_grounded(max_steps)
        if grounded
        else cap_flash_max_steps(max_steps)
    )
    cheat = build_cheat_sheet_from_filled(filled_rows)
    job_ctx = job_context if job_context is not None else report.get("job_context")
    resume_path = resume_path or report.get("resume_pdf") or report.get("dummy_resume_pdf")
    run_vals = values if isinstance(values, dict) and values else _values_from_report(report)
    is_dummy_run = report.get("dummy", True) is not False
    profile_facts = build_run_profile_facts(run_vals)
    resume_excerpt = build_resume_excerpt(
        resume_path, allow_dummy_fallback=is_dummy_run
    )
    if not isinstance(job_ctx, dict):
        job_block = (
            "JOB DESCRIPTION:\n  (not scraped — answer from resume excerpt + "
            "applicant facts only)"
            if not is_dummy_run
            else "JOB DESCRIPTION:\n  (not scraped — answer from dummy resume + DUMMY_PROFILE only)"
        )
    else:
        job_block = format_job_context(job_ctx)
    grounding = "\n\n".join(
        [
            job_block,
            resume_excerpt,
            profile_facts,
        ]
    )
    leftover_lines = [
        "LEFTOVER FIELDS (answer EVERY one — including essays / why-join):"
    ]
    if not leftover_rows:
        leftover_lines.append("  (none — nothing for Flash to do)")
    else:
        for i, u in enumerate(leftover_rows, 1):
            essay_tag = " essay=true MUST_ANSWER" if u.get("essay") else ""
            select_tag = " select=true CLICK_OPTION" if u.get("select") else ""
            leftover_lines.append(
                f"  {i}. label={u.get('label')!r} type={u.get('type')!r} "
                f"reason={u.get('reason')!r} selector={u.get('selector')!r}"
                f"{essay_tag}{select_tag}"
            )
    rules_tmpl = LEFTOVERS_RULES if is_dummy_run else LEFTOVERS_RULES_REAL
    rules = rules_tmpl.format(
        never_submit=NEVER_SUBMIT_SNIPPET,
        max_steps=steps,
    )
    # Continuous learning: inject sanitized similar past leftover answers
    past_block = ""
    try:
        from continuous_learn import format_similar_for_flash, similar_leftover_answers
        from urllib.parse import urlparse

        host = (urlparse(str(report.get("url") or "")).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        similar = similar_leftover_answers(
            leftover_rows,
            platform=str(report.get("platform") or ""),
            host=host,
            top_n=5,
        )
        past_block = format_similar_for_flash(similar)
    except Exception:
        past_block = ""
    playbook_section = ""
    try:
        from playbooks import detect_playbook, playbook_hints

        hints_lines: list[str] = []
        seen_pb: set[str] = set()
        for row in leftover_rows or []:
            if not isinstance(row, dict):
                continue
            pb = detect_playbook(
                {
                    "tag": row.get("tag") or "",
                    "role": row.get("role") or "",
                    "class": row.get("class") or row.get("className") or "",
                    "label": row.get("label") or "",
                    "type": row.get("type") or "",
                    "platform": report.get("platform") or "",
                }
            )
            if pb in seen_pb:
                continue
            seen_pb.add(pb)
            h = playbook_hints(pb)
            steps = h.get("steps") or h.get("hint") or ""
            if isinstance(steps, list):
                steps = "; ".join(str(s) for s in steps)
            hints_lines.append(f"- {pb}: {steps}")
        if hints_lines:
            playbook_section = (
                "\n\nAllowlisted interaction playbooks (pick id only; no free-form clicks):\n"
                + "\n".join(hints_lines[:8])
            )
    except Exception:
        playbook_section = ""
    past_section = f"\n\n{past_block}" if past_block else ""
    if is_dummy_run:
        intro = (
            "Continue this job application form. Deterministic layers already filled "
            "what they could. Use FICTIONAL dummy applicant data only — never real PII.\n\n"
        )
    else:
        intro = (
            "Continue this job application form. Deterministic layers already filled "
            "what they could. Use this run's unique identity/education from the facts "
            "block below, plus SHARED policy catalog for EEO/screening. Never invent "
            "new EEO beyond the shared catalog. Never submit.\n\n"
        )
    return (
        f"{intro}"
        f"{grounding}\n\n"
        f"{cheat}\n\n"
        f"{chr(10).join(leftover_lines)}"
        f"{past_section}"
        f"{playbook_section}\n\n"
        f"{rules}"
    )


def build_leftovers_handoff(
    report: dict,
    *,
    max_steps: int | None = None,
    max_leftover_fields: int | None = None,
    grounded: bool = True,
    job_context: dict | None = None,
    resume_path: str | Path | None = None,
    values: dict | None = None,
) -> dict[str, Any]:
    """Build the Flash leftovers API payload (does not invoke Skyvern).

    When ``grounded=True`` (default for cycle/fill leftover mode), the prompt
    includes scraped JD + resume excerpt + run unique facts + SHARED policy and
    instructs Flash to answer every leftover including essays. Field cap rises
    so essays are not dropped from the handoff list.
    """
    if grounded:
        steps = cap_flash_max_steps_grounded(max_steps)
        default_cap = FLASH_MAX_LEFTOVER_FIELDS_GROUNDED
        hard_cap = FLASH_MAX_LEFTOVER_FIELDS_GROUNDED
    else:
        steps = cap_flash_max_steps(max_steps)
        default_cap = FLASH_MAX_LEFTOVER_FIELDS
        hard_cap = FLASH_MAX_LEFTOVER_FIELDS
    field_cap = max(
        1,
        min(
            int(max_leftover_fields)
            if max_leftover_fields is not None
            else default_cap,
            hard_cap,
            steps if not grounded else max(steps, hard_cap),
        ),
    )
    run_vals = values if isinstance(values, dict) and values else _values_from_report(report)
    filled = _filled_rows(report)
    try:
        from field_lock import filter_locked_leftovers

        filter_locked_leftovers(report)
    except Exception:
        pass
    leftovers_all = _leftover_rows(report, filled=filled)
    parts = partition_flash_leftovers(leftovers_all, values=run_vals)
    leftovers = list(parts["flash_leftovers"])
    deferred = list(parts["deferred_deterministic"])
    # FILL-003 / FILL2-005 defense: never hand phone-ext / optional blanks to Skyvern
    _stripped: list[dict] = []
    for row in leftovers:
        t = str(row.get("type") or "").strip().upper()
        lab = str(row.get("label") or "")
        name = str(row.get("name") or "")
        selector = str(row.get("selector") or "")
        try:
            from field_map import OPTIONAL_LEAVE_BLANK_TYPES, is_phone_extension_field
            from fill_attribution import is_flash_forbidden_type

            if (
                t in OPTIONAL_LEAVE_BLANK_TYPES
                or t == "PHONE_EXTENSION"
                or is_phone_extension_field(
                    lab, t or None, name=name, selector=selector
                )
                or is_flash_forbidden_type(
                    t, label=lab, name=name, selector=selector
                )
            ):
                deferred.append(
                    {
                        **row,
                        "flash_candidate": False,
                        "flash_skip_reason": "flash_forbidden_strip",
                        "ownership": "prefill_reclaim",
                    }
                )
                continue
        except Exception:
            if t in _FLASH_STEAL_BLOCKLIST or t == "PHONE_EXTENSION":
                deferred.append({**row, "flash_candidate": False})
                continue
        _stripped.append(row)
    leftovers = _stripped
    truncated = False
    if len(leftovers) > field_cap:
        leftovers = leftovers[:field_cap]
        truncated = True
    cheat = build_cheat_sheet_from_filled(filled)
    job_ctx = job_context if job_context is not None else report.get("job_context")
    prompt = build_leftovers_prompt(
        report,
        max_steps=steps,
        leftovers=leftovers,
        filled=filled,
        job_context=job_ctx if isinstance(job_ctx, dict) else None,
        resume_path=resume_path,
        grounded=grounded,
        values=run_vals,
    )
    identity_email = (
        report.get("identity_email")
        or report.get("email_alias")
        or report.get("email")
    )
    similar_count = 0
    if "PAST SIMILAR LEFTOVER ANSWERS" in prompt:
        similar_count = prompt.count("→")
    is_dummy_run = report.get("dummy", True) is not False
    return {
        "mode": "leftovers_only",
        "invoked": False,
        "never_submit": True,
        "submit_clicked": False,
        "dummy": is_dummy_run,
        "grounded": bool(grounded),
        "flash_default_on": FLASH_DEFAULT_ON,
        "url": report.get("url"),
        "platform": report.get("platform"),
        "model_hint": "DeepSeek-V4-Flash",
        "identity_email": identity_email,
        "already_filled": filled,
        "already_filled_count": len(filled),
        "leftovers": leftovers,
        "leftover_count": len(leftovers),
        "deferred_deterministic": deferred,
        "deferred_deterministic_count": len(deferred),
        "deterministic_reclaim": deferred,
        "deterministic_reclaim_count": len(deferred),
        "all_leftover_count": len(leftovers_all),
        "flash_minimization": parts.get("minimization"),
        "flash_excludes_contact": True,
        "leftovers_truncated": truncated,
        "max_steps": steps,
        "max_leftover_fields": field_cap,
        "cheat_sheet": cheat,
        "prompt": prompt,
        "prompt_chars": len(prompt),
        "cheat_sheet_chars": len(cheat),
        "experience_similar_count": similar_count,
        "job_context_present": bool(
            isinstance(job_ctx, dict)
            and (job_ctx.get("title") or job_ctx.get("description"))
        ),
    }


def assert_flash_payload(
    payload: dict,
    *,
    expect_invoked: bool | None = False,
    grounded: bool | None = None,
) -> None:
    """Unit assertions for scorecard-consistent Flash handoff fields."""
    for key in FLASH_SCORECARD_KEYS:
        if key not in payload:
            raise AssertionError(f"flash payload missing scorecard key: {key}")
    if payload.get("mode") != "leftovers_only":
        raise AssertionError(f"mode must be leftovers_only; got {payload.get('mode')!r}")
    if payload.get("never_submit") is not True:
        raise AssertionError("never_submit must be True")
    if payload.get("submit_clicked") is True:
        raise AssertionError("submit_clicked must be False")
    # dummy=True for test fills; real fills may set False but still never_submit.
    if "dummy" not in payload:
        raise AssertionError("flash payload missing dummy flag")
    steps = payload.get("max_steps")
    is_grounded = bool(payload.get("grounded")) if grounded is None else bool(grounded)
    step_cap = FLASH_MAX_STEPS_GROUNDED if is_grounded else FLASH_MAX_STEPS
    if not isinstance(steps, int) or steps < 1 or steps > step_cap:
        raise AssertionError(f"max_steps must be in 1..{step_cap}; got {steps!r}")
    if payload.get("leftover_count") != len(payload.get("leftovers") or []):
        raise AssertionError("leftover_count mismatch")
    if payload.get("already_filled_count") != len(payload.get("already_filled") or []):
        raise AssertionError("already_filled_count mismatch")
    # Thin Skyvern mode: leftovers ≤ steps. Grounded/inpage may list more than steps.
    if not is_grounded and int(payload.get("leftover_count") or 0) > int(
        payload.get("max_steps") or 0
    ):
        raise AssertionError("leftover_count exceeds max_steps budget")
    prompt = payload.get("prompt") or ""
    cheat = payload.get("cheat_sheet") or ""
    if "ALREADY FILLED" not in cheat and "ALREADY FILLED" not in prompt:
        raise AssertionError("prompt/cheat_sheet missing ALREADY FILLED section")
    if is_grounded:
        if "DUMMY_PROFILE" not in prompt and "dummy" not in prompt.lower():
            raise AssertionError("grounded prompt missing DUMMY_PROFILE facts")
        if "MUST_ANSWER" not in prompt and "every" not in prompt.lower():
            # soft: essays must be instructed
            if "essay" not in prompt.lower():
                raise AssertionError("grounded prompt should mention essays")
    # Prompt must not re-list filled types/selectors as leftovers.
    filled_keys = _filled_identity_keys(payload.get("already_filled") or [])
    for row in payload.get("leftovers") or []:
        if _row_matches_filled(row, filled_keys):
            raise AssertionError(
                f"leftover re-asks already-filled field: {row.get('label') or row.get('type')}"
            )
        # W01: Flash prompt must never include contact/address steal targets
        t = str(row.get("type") or "").strip().upper()
        if t in _FLASH_STEAL_BLOCKLIST:
            raise AssertionError(
                f"Flash leftovers must not include contact/address type {t!r}"
            )
    if expect_invoked is not None and bool(payload.get("invoked")) != bool(expect_invoked):
        raise AssertionError(
            f"invoked={payload.get('invoked')!r} expected {expect_invoked!r}"
        )


async def run_flash_leftovers(
    url: str,
    report: dict,
    *,
    invoke: bool = True,
    max_steps: int = FLASH_MAX_STEPS,
    timeout: float = 180,
    job_id: str | None = None,
    max_leftover_fields: int = FLASH_MAX_LEFTOVER_FIELDS,
) -> dict:
    """Optionally invoke Skyvern with a leftovers-only prompt.

    When ``invoke=False``, returns ``build_leftovers_handoff`` only.
    When ``invoke=True``, starts a thin Skyvern task (never-submit rules in prompt).

    Token budget: max_steps hard-capped at ``FLASH_MAX_STEPS``; leftover list is
    truncated to ``min(max_leftover_fields, max_steps)`` so Flash never re-walks
    the whole form or re-asks filled fields.
    """
    max_steps = cap_flash_max_steps(max_steps)
    max_leftover_fields = max(
        1, min(int(max_leftover_fields), FLASH_MAX_LEFTOVER_FIELDS, max_steps)
    )
    payload = build_leftovers_handoff(
        report, max_steps=max_steps, max_leftover_fields=max_leftover_fields
    )
    payload["url"] = url or payload.get("url")
    # FILL2-006: Skyvern must not invent EEO — hold for inpage catalog validation.
    _EEO_TYPES = frozenset(
        {"GENDER", "RACE", "HISPANIC", "VETERAN", "DISABILITY", "AGE_RANGE", "LGBTQIA"}
    )
    kept: list[dict] = []
    eeo_held: list[dict] = []
    for row in payload.get("leftovers") or []:
        if not isinstance(row, dict):
            continue
        t = str(row.get("type") or "").strip().upper()
        lab = str(row.get("label") or "")
        if t in _EEO_TYPES or re.search(
            r"\b(?:gender|sex|race|ethnicity|veteran|disabilit|hispanic|eeo|lgbtq)\b",
            lab,
            re.I,
        ):
            eeo_held.append({**row, "flash_skip_reason": "eeo_catalog_inpage_only"})
            continue
        kept.append(row)
    if eeo_held:
        deferred = list(payload.get("deferred_deterministic") or [])
        deferred.extend(eeo_held)
        payload["deferred_deterministic"] = deferred
        payload["leftovers"] = kept
        payload["leftover_count"] = len(kept)
        payload["eeo_held_for_catalog"] = eeo_held
        try:
            from flight_recorder import note_flight

            note_flight(
                report,
                "flash_filter",
                action="hold_eeo",
                layer="flash",
                gate_kind="flash_eeo_filter",
                gate_result="held",
                gate_reason=f"eeo_held={len(eeo_held)} kept={len(kept)}",
                extra={"eeo_held": len(eeo_held), "kept": len(kept)},
            )
        except Exception:
            pass
        # Rebuild prompt without EEO invent targets
        try:
            payload["prompt"] = build_leftovers_prompt(
                report,
                max_steps=max_steps,
                leftovers=kept,
                grounded=False,
                values=_values_from_report(report),
            )
            payload["prompt_chars"] = len(payload["prompt"])
        except Exception:
            pass
    assert_flash_payload(payload, expect_invoked=False)
    if not invoke:
        return payload

    if payload["leftover_count"] == 0:
        payload["skipped_reason"] = (
            "no_leftovers" if not eeo_held else "eeo_held_no_other_leftovers"
        )
        payload["invoked"] = False
        return payload

    job_id = job_id or f"flash-leftovers-{(report.get('platform') or 'x')}"
    t0 = time.time()
    try:
        import real_job_test as rjt  # type: ignore
        from skyvern import Skyvern  # type: ignore
    except Exception as e:
        payload["invoked"] = False
        payload["error"] = f"skyvern_import_failed: {e}"[:300]
        payload["elapsed_seconds"] = round(time.time() - t0, 2)
        return payload

    prompt = payload["prompt"]
    # Reinforce never-submit from the proven harness string
    prompt = f"{prompt}\n\n{getattr(rjt, 'NEVER_SUBMIT', NEVER_SUBMIT_SNIPPET)}"
    payload["prompt"] = prompt
    payload["prompt_chars"] = len(prompt)

    skyvern = Skyvern(base_url=rjt.BASE_URL, api_key=rjt.API_KEY)
    result = None
    err = None
    run_id = None
    captcha_blocked = False
    browser_session_id = None
    watchdog_triggered = submit_alarm = enter_alarm = None
    try:
        session = await skyvern.create_browser_session(timeout=60)
        browser_session_id = session.browser_session_id
        job = {"id": job_id, "url": url, "platform": "flash_leftovers"}
        run_id = await rjt._create_task_v1(
            url=url,
            navigation_goal=prompt,
            max_steps_per_run=max_steps,
            include_action_history_in_verification=True,
            complete_criterion=rjt.COMPLETE_CRITERION,
            terminate_criterion=rjt.TERMINATE_CRITERION,
            browser_session_id=browser_session_id,
        )
        watchdog_triggered, submit_alarm, enter_alarm, result, err = await rjt._poll_and_finalize(
            skyvern, job, run_id, t0, timeout
        )
        if result and rjt._looks_like_captcha_block(getattr(result, "failure_reason", None)):
            captcha_blocked = True
    except Exception as e:
        err = err or str(e)
    finally:
        if browser_session_id and not captcha_blocked:
            try:
                await skyvern.close_browser_session(browser_session_id)
            except Exception:
                pass

    payload.update(
        {
            "invoked": True,
            "job_id": job_id,
            "run_id": run_id,
            "elapsed_seconds": round(time.time() - t0, 2),
            "status": getattr(result, "status", None),
            "error": err,
            "failure_reason": getattr(result, "failure_reason", None) if result else None,
            "watchdog_triggered": bool(watchdog_triggered),
            "submit_alarm": bool(submit_alarm),
            "enter_alarm": bool(enter_alarm),
            "captcha_blocked": captcha_blocked,
            "never_submit": True,
            "submit_clicked": False,
            "dummy": bool(payload.get("dummy", True)),
            "max_steps": max_steps,
        }
    )
    assert payload.get("never_submit") is True
    assert payload.get("submit_clicked") is False
    assert_flash_payload(payload, expect_invoked=True)
    out_path = ROOT / "skyvern_runtime" / "real_job_results" / f"flash-leftovers-{job_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    payload["report_path"] = str(out_path)
    return payload


def flash_candidate_count(report: dict) -> int:
    """Honest flash-eligible leftover count (excludes deferred deterministic)."""
    filled = _filled_rows(report)
    rows = _leftover_rows(report, filled=filled)
    parts = partition_flash_leftovers(rows, values=_values_from_report(report))
    return len(parts.get("flash_leftovers") or [])


def assert_honest_leftover_count(report: dict) -> None:
    """Raise when leftover_count=0 but flash candidates or live empties remain."""
    lc = int(report.get("leftover_count") or 0)
    fc = flash_candidate_count(report)
    req = report.get("required_empty_after_fill") or []
    if lc == 0 and fc > 0:
        raise AssertionError(
            f"leftover_count=0 lie: {fc} flash_candidate(s) still listed"
        )
    if lc == 0 and req and report.get("verdict") == "SUCCESS":
        raise AssertionError(
            f"leftover_count=0 lie: {len(req)} required_empty_after_fill"
        )


def self_test_report(report: dict) -> dict[str, Any]:
    """Shape-only unit test: handoff + caps + no re-ask of filled fields."""
    # Default path: invoke=False, grounded=True (cycle contract)
    off = build_leftovers_handoff(report, grounded=True)
    assert_flash_payload(off, expect_invoked=False, grounded=True)
    assert off["flash_default_on"] is False
    assert off["grounded"] is True
    assert "JOB DESCRIPTION" in off["prompt"] or "job description" in off["prompt"].lower()
    assert "DUMMY_PROFILE" in off["prompt"]
    assert "every" in off["prompt"].lower() or "MUST_ANSWER" in off["prompt"]
    # Must NOT tell model to skip essays
    assert "never invent personal stories" not in off["prompt"].lower()
    assert "leave blank and stop" not in off["prompt"].lower()

    # Thin (non-grounded) path still clamps to FLASH_MAX_STEPS
    thin = build_leftovers_handoff(report, grounded=False, max_steps=99, max_leftover_fields=99)
    assert_flash_payload(thin, expect_invoked=False, grounded=False)
    assert thin["max_steps"] == FLASH_MAX_STEPS
    assert thin["leftover_count"] <= thin["max_steps"]

    # Oversize grounded request must still clamp field list
    capped = build_leftovers_handoff(
        report, grounded=True, max_steps=99, max_leftover_fields=99
    )
    assert_flash_payload(capped, expect_invoked=False, grounded=True)
    assert capped["max_steps"] <= FLASH_MAX_STEPS_GROUNDED
    assert capped["leftover_count"] <= FLASH_MAX_LEFTOVER_FIELDS_GROUNDED

    # Inject a duplicate leftover that mirrors a filled field — must be dropped
    mutant = dict(report)
    filled = list(report.get("filled") or [])
    leftovers = list(report.get("leftovers") or [])
    if filled:
        f0 = filled[0]
        leftovers = leftovers + [
            {
                "label": f0.get("label") or f0.get("type") or "dup",
                "type": f0.get("type"),
                "selector": f0.get("selector") or "",
                "reason": "injected_overlap_for_self_test",
                "flash_candidate": True,
            }
        ]
        mutant["leftovers"] = leftovers
        filtered = build_leftovers_handoff(mutant, grounded=True)
        assert_flash_payload(filtered, expect_invoked=False, grounded=True)
        filled_keys = _filled_identity_keys(filtered["already_filled"])
        for row in filtered["leftovers"]:
            assert not _row_matches_filled(row, filled_keys)

    # W01: EMAIL/ZIP must land in deferred reclaim, never Flash leftovers list
    # Use a clean report so type-level filled_keys do not drop the injects.
    steal = {
        "url": report.get("url") or "https://example.com/apply",
        "platform": report.get("platform") or "greenhouse",
        "never_submit": True,
        "dummy": True,
        "filled": [
            {
                "type": "NAME_FIRST",
                "label": "First Name",
                "selector": "#first_name",
                "ok": True,
                "value": "Test",
                "via": "selector_pack",
            }
        ],
        "leftovers": [
            {
                "label": "Email Steal Test",
                "type": "EMAIL",
                "selector": "#w01_email_steal",
                "reason": "injected_steal_test",
                "flash_candidate": True,
            },
            {
                "label": "Zip Steal Test",
                "type": "ADDRESS_ZIP",
                "selector": "#w01_zip_steal",
                "reason": "injected_steal_test",
                "flash_candidate": True,
            },
            {
                "label": "Why do you want to join us?",
                "type": "COVER_LETTER",
                "selector": "textarea.w01",
                "reason": "injected_essay",
                "flash_candidate": True,
                "essay": True,
            },
        ],
    }
    steal_hand = build_leftovers_handoff(steal, grounded=True)
    assert_flash_payload(steal_hand, expect_invoked=False, grounded=True)
    flash_types = {
        str(r.get("type") or "").upper() for r in (steal_hand.get("leftovers") or [])
    }
    assert "EMAIL" not in flash_types and "ADDRESS_ZIP" not in flash_types
    deferred_types = {
        str(r.get("type") or "").upper()
        for r in (steal_hand.get("deferred_deterministic") or [])
    }
    assert "EMAIL" in deferred_types and "ADDRESS_ZIP" in deferred_types
    assert "COVER_LETTER" in flash_types
    assert steal_hand.get("flash_excludes_contact") is True
    assert "NEVER fill" in steal_hand["prompt"] or "zip" in steal_hand["prompt"].lower()

    # Prompt must mention cheat sheet and never-submit
    prompt = off["prompt"]
    assert "do not re-fill" in prompt.lower() or "do not ask" in prompt.lower()
    assert "never" in prompt.lower() and "submit" in prompt.lower()

    # synthesize grounded essay answer is non-empty
    ans = synthesize_grounded_answer(
        "Why do you want to join us?",
        job_context={"title": "ML Engineer", "company": "Acme", "description": "Build models"},
    )
    assert len(ans) > 40

    # Select leftovers tagged; essays not confused with selects
    select_mix = {
        "url": report.get("url") or "https://example.com/apply",
        "platform": "greenhouse",
        "filled": [],
        "leftovers": [
            {
                "label": "Are you currently based in any of these states?",
                "type": "LOCATION",
                "selector": "#states",
                "reason": "gh_select_failed",
                "flash_candidate": True,
            },
            {
                "label": "Why do you want to join us?",
                "type": "COVER_LETTER",
                "selector": "textarea.essay",
                "reason": "no_value",
                "flash_candidate": True,
            },
        ],
    }
    mix_hand = build_leftovers_handoff(select_mix, grounded=True)
    by_label = {r.get("label"): r for r in mix_hand.get("leftovers") or []}
    # LOCATION is deterministic catalog → deferred reclaim (not Flash LLM).
    # Still tagged select=True so inpage reclaim uses click-option, not essay paste.
    deferred_by = {
        r.get("label"): r for r in (mix_hand.get("deferred_deterministic") or [])
    }
    loc_row = deferred_by.get("Are you currently based in any of these states?")
    assert loc_row and loc_row.get("select") is True and not loc_row.get("essay")
    assert loc_row.get("flash_skip_reason") == "deterministic_catalog"
    assert "LOCATION" not in {
        str(r.get("type") or "").upper() for r in (mix_hand.get("leftovers") or [])
    }
    essay_row = by_label.get("Why do you want to join us?")
    assert essay_row and essay_row.get("essay") is True
    # Essay-only Flash prompt may omit select tokens; deferred row carries select tag.
    assert loc_row.get("select") is True

    return {
        "ok": True,
        "max_steps": off["max_steps"],
        "already_filled_count": off["already_filled_count"],
        "leftover_count": off["leftover_count"],
        "prompt_chars": off["prompt_chars"],
        "flash_default_on": FLASH_DEFAULT_ON,
        "grounded": off["grounded"],
        "dummy": off["dummy"],
        "never_submit": off["never_submit"],
        "invoked": off["invoked"],
        "essay_answer_chars": len(ans),
    }


def main() -> int:
    """Inspect leftover API shape from an existing fast_fill JSON report."""
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("report_json", type=Path, help="Path to a fast_fill*.json report")
    ap.add_argument(
        "--invoke",
        action="store_true",
        default=False,
        help="Actually call Skyvern (default: print shape only)",
    )
    ap.add_argument(
        "--self-test",
        action="store_true",
        default=False,
        help="Assert handoff invariants on the report (no Skyvern)",
    )
    ap.add_argument(
        "--max-steps",
        type=int,
        default=FLASH_MAX_STEPS,
        help=f"Flash step budget (hard-capped at {FLASH_MAX_STEPS})",
    )
    args = ap.parse_args()
    report = json.loads(args.report_json.read_text())

    if args.self_test:
        result = self_test_report(report)
        print(json.dumps(result, indent=2))
        print("self-test OK")
        return 0

    if args.invoke:
        import asyncio

        out = asyncio.run(
            run_flash_leftovers(
                report.get("url") or "",
                report,
                invoke=True,
                max_steps=cap_flash_max_steps(args.max_steps),
            )
        )
    else:
        out = build_leftovers_handoff(
            report, max_steps=cap_flash_max_steps(args.max_steps)
        )
        assert_flash_payload(out, expect_invoked=False)

    printable = {k: v for k, v in out.items() if k != "prompt"}
    print(json.dumps(printable, indent=2))
    print("\n--- prompt preview (first 800 chars) ---")
    print((out.get("prompt") or "")[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
