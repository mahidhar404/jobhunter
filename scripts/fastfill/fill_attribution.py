#!/usr/bin/env python3
"""Prefill vs LLM fill attribution for the multi-agent fix cycle.

Deterministic field types (contact, address, phone, zip, resume, WORK_AUTH,
SPONSORSHIP, TERMS, HOW_HEARD, LOCATION, common Yes/No) belong on prefill.
If Flash / inpage_flash filled them, that is a ``prefill_regression``.

EEO / demographics are answered from SHARED catalog / shared_values
(``llm_expected`` with post-LLM catalog validation); Decline remains
prefill/API fallback. Essays / novel free-text are also ``llm_expected``.
Still-empty after Flash are ``blank_bugs``. Screenshot empties that conflict
with verified fills are ``false_success``.

Usage::

    from fill_attribution import analyze_fill_attribution, write_attribution

    attr = analyze_fill_attribution(report, vision_empties=[...])
    write_attribution(attr, out_dir / "attribution.json")
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Canonical types that MUST be filled by deterministic layers, not LLM.
DETERMINISTIC_TYPES: frozenset[str] = frozenset(
    {
        # Contact
        "NAME_FIRST",
        "NAME_LAST",
        "NAME_FULL",
        "NAME_MIDDLE",
        "RELATIVE_NAME",
        "EMAIL",
        "PHONE",
        "PHONE_EXTENSION",
        # Address / location
        "ADDRESS_LINE1",
        "ADDRESS_CITY",
        "ADDRESS_STATE",
        "ADDRESS_ZIP",
        "ADDRESS_COUNTRY",
        "LOCATION",
        # Links (policy URLs from DUMMY_PROFILE)
        "LINKEDIN",
        "GITHUB",
        "PORTFOLIO",
        "TWITTER",
        # Files
        "RESUME_UPLOAD",
        # Auth / screening Yes-No
        "WORK_AUTH",
        "US_RESIDENCE",
        "SPONSORSHIP",
        "RELOCATION",
        "TALENT_HUB",
        "AGE_18",
        "FELONY",
        "WORKED_HERE_BEFORE",
        "SERVICE_MEMBER",
        # Misc policy
        "HOW_HEARD",
        "TERMS_CONSENT",
        "ACCOMMODATIONS",
        "ACCOMMODATIONS_DETAILS",
        "EMPLOYEE_REFERRAL",
        "REFERRAL_EMAIL",
        "PASSWORD",
        "PASSWORD_CONFIRM",
        "NOTICE_PERIOD",
        "YEARS_EXPERIENCE",
        "SCHOOL",
        "DEGREE",
        "DISCIPLINE",
        "MAJOR",
        "FIELD_OF_STUDY",
        "EDUCATION_START_YEAR",
        "EDUCATION_END_YEAR",
        "CURRENT_COMPANY",
        "CURRENT_TITLE",
        "APPLYING_FOR",
        "COMMUTE",
        "MARKETING_CONSENT",
        # Compensation policy strings (never invent $) — catalog, not Flash
        "SALARY_EXPECTED",
        "SALARY_CURRENT",
        "CLEARANCE",
        "CLEARANCE_TYPE",
        "US_CITIZEN",
        "VISA_STATUS",
    }
)

# Types / labels that LLM is expected to answer (grounded in dummy+JD).
# Salary / School / Degree are NOT here — they are DETERMINISTIC_TYPES.
LLM_EXPECTED_TYPES: frozenset[str] = frozenset(
    {
        "COVER_LETTER",
        "INTEREST",
        "ESSAY",
        "MOTIVATION",
        # EEO: SHARED catalog only (validated); Decline is fallback
        "GENDER",
        "RACE",
        "HISPANIC",
        "VETERAN",
        "DISABILITY",
        "AGE_RANGE",
        "EEO",
    }
)

# Contact/address types Flash must never invent — reclaim or blank_bug only.
# PHONE_EXTENSION is optional blank — never Flash/Skyvern (essay dump risk).
FLASH_FORBIDDEN_TYPES: frozenset[str] = frozenset(
    {
        "NAME_FIRST",
        "NAME_LAST",
        "NAME_FULL",
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

_LLM_VIA_RE = re.compile(
    r"^(?:inpage_flash|flash_leftovers|flash|skyvern|llm)",
    re.I,
)
_PREFILL_VIA_RE = re.compile(
    r"(?:selector_pack|extract\+classify|ashby_widgets|ashby_location|"
    r"gh_select|replay|learned|workday_|layer\s*[01]|entry_prepass|"
    r"prefill_reclaim|deterministic_reclaim|greenhouse_post_resume_reassert|"
    r"ashby_reassert|ashby_post_resume)",
    re.I,
)
_ESSAY_LABEL_RE = re.compile(
    r"\b(?:essay|cover[\s_-]*letter|motivation|"
    r"tell[\s_-]*us[\s_-]*about|describe\b|explain\b|"
    r"why[\s_-]*(?:do[\s_-]*you[\s_-]*want|are[\s_-]*you|join)|"
    r"pros[\s_-]*and[\s_-]*cons)\b",
    re.I,
)
_EEO_TYPE_RE = re.compile(
    r"^(?:GENDER|RACE|HISPANIC|VETERAN|DISABILITY|AGE_RANGE|EEO)",
    re.I,
)
_YESNO_LABEL_RE = re.compile(
    r"\b(?:yes\s*/\s*no|are\s+you|do\s+you|have\s+you|"
    r"willing\s+to|authorized|sponsor|relocat|consent|agree)\b",
    re.I,
)


def _norm_type(raw: Any) -> str:
    return str(raw or "").strip().upper()


def _via_of(row: dict) -> str:
    return str(row.get("via") or row.get("layer") or "").strip()


def is_llm_via(via: str) -> bool:
    v = (via or "").strip()
    if not v:
        return False
    return bool(_LLM_VIA_RE.match(v)) or "flash" in v.lower()


def is_prefill_via(via: str) -> bool:
    v = (via or "").strip()
    if not v:
        return False
    if is_llm_via(v):
        return False
    return bool(_PREFILL_VIA_RE.search(v)) or v in ("0", "0.5", "1", "layer0", "layer1")


def is_deterministic_type(ftype: str, *, label: str = "") -> bool:
    t = _norm_type(ftype)
    if t in DETERMINISTIC_TYPES:
        return True
    # EEO is LLM-expected (DeepSeek + dummy); not deterministic Decline-only.
    # Unclassified Yes/No with screening-ish labels → deterministic
    if not t and _YESNO_LABEL_RE.search(label or ""):
        return True
    return False


def is_llm_expected_type(ftype: str, *, label: str = "") -> bool:
    t = _norm_type(ftype)
    if t in LLM_EXPECTED_TYPES or _EEO_TYPE_RE.match(t) or t.startswith("EEO"):
        return True
    # Deterministic catalog wins over essay-ish label heuristics
    if t and is_deterministic_type(t, label=label):
        return False
    if _ESSAY_LABEL_RE.search(label or ""):
        return True
    return False


def is_flash_forbidden_type(
    ftype: str, *, label: str = "", name: str = "", selector: str = ""
) -> bool:
    """True for contact/address/resume/phone-ext types Flash must not invent."""
    t = _norm_type(ftype)
    lab = (label or "").lower()
    # Phone Extension is optional blank — forbid Flash/Skyvern handoff entirely
    # (empty must not burn tokens or receive essays). Leave blank via reclaim.
    if t == "PHONE_EXTENSION":
        return True
    try:
        from field_map import is_phone_extension_field

        if is_phone_extension_field(
            lab, t, name=name or "", selector=selector or ""
        ):
            return True
    except Exception:
        if re.search(
            r"phone[\s_-]*ext(?:ension)?|"
            r"(?:^|[\s_/|-])ext(?:ension)?(?:[\s_.-]*(?:#|no\.?|num(?:ber)?))?\s*$|"
            r"\bext\.(?:\s*(?:#|no|num|number))?\b",
            lab,
        ) and not re.search(r"contract|file[\s_-]*ext|lease|visa[\s_-]*ext", lab):
            return True
        nm = (name or "").strip().lower()
        if not nm and selector:
            m = re.search(r"""name\s*=\s*['"]?([^'"\]\s]+)""", selector, re.I)
            nm = (m.group(1) if m else "").strip().lower()
        if nm in ("extension", "ext", "phone_ext", "phoneextension"):
            return True
    if t in FLASH_FORBIDDEN_TYPES:
        return True
    # Unclassified but clearly email/zip/phone from label (not phone-ext)
    if re.search(
        r"\b(?:e-?mail|zip|postal[\s_-]*code|phone|mobile|first[\s_-]*name|last[\s_-]*name)\b",
        lab,
    ):
        if not is_llm_expected_type(t, label=label):
            return True
    return False


def partition_leftover_ownership(
    leftovers: list[dict],
) -> dict[str, list[dict]]:
    """Split leftovers into reclaim (deterministic) vs llm (essays/novel).

    Flash/Skyvern prompt should only receive ``llm`` rows. Contact/address
    forbidden types always go to reclaim even if essay heuristics fire.
    """
    reclaim: list[dict] = []
    llm: list[dict] = []
    other: list[dict] = []
    for row in leftovers or []:
        if not isinstance(row, dict):
            continue
        ftype = _norm_type(row.get("type") or row.get("automation_id"))
        label = str(row.get("label") or row.get("automation_id") or "")
        if is_flash_forbidden_type(ftype, label=label) or is_deterministic_type(
            ftype, label=label
        ):
            reclaim.append(row)
        elif is_llm_expected_type(ftype, label=label) or row.get("essay"):
            llm.append(row)
        else:
            # Novel unclassified → LLM grounded path (Agent1 zero-blank goal)
            other.append(row)
            llm.append(row)
    return {"reclaim": reclaim, "llm": llm, "other": other}


def _row_ok(row: dict) -> bool:
    if row.get("ok") is False:
        return False
    if row.get("verified") is False and row.get("ok") is not True:
        return False
    return True


def _norm_label(s: str) -> str:
    """Normalize label for fuzzy false_success matching (truncation / whitespace)."""
    t = re.sub(r"\s+", " ", (s or "").strip().lower())
    t = t.replace("*", "").strip()
    return t[:80]


def _labels_overlap(a: str, b: str) -> bool:
    """True if labels refer to the same field despite truncation."""
    na, nb = _norm_label(a), _norm_label(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # Report truncates to 60 chars; vision may use full question text
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 24 and longer.startswith(shorter[:24]):
        return True
    if len(shorter) >= 30 and shorter in longer:
        return True
    return False


def analyze_fill_attribution(
    report: dict,
    *,
    vision_empties: list[dict | str] | None = None,
    vision: dict | None = None,
) -> dict[str, Any]:
    """Attribute each filled/leftover field to prefill vs LLM vs blank bug.

    Returns dict with keys:
      prefill, flash, regressions (alias prefill_regressions),
      llm_expected, blank_bugs, false_success, summary, ...
    """
    filled = [f for f in (report.get("filled") or []) if isinstance(f, dict)]
    leftovers = [u for u in (report.get("leftovers") or []) if isinstance(u, dict)]

    prefill_rows: list[dict] = []
    flash_rows: list[dict] = []
    prefill_regressions: list[dict] = []
    llm_expected: list[dict] = []
    blank_bugs: list[dict] = []
    other_filled: list[dict] = []

    for f in filled:
        if not _row_ok(f):
            continue
        ftype = _norm_type(f.get("type") or f.get("automation_id"))
        label = str(f.get("label") or "")
        via = _via_of(f)
        entry = {
            "type": ftype or None,
            "label": label[:120],
            "via": via,
            "selector": (f.get("selector") or "")[:160],
            "verified": bool(f.get("verified") or f.get("ok")),
        }
        if is_llm_via(via):
            flash_rows.append(entry)
            if is_deterministic_type(ftype, label=label):
                prefill_regressions.append(
                    {
                        **entry,
                        "issue": "deterministic_type_filled_only_via_llm",
                        "fix_hint": "upgrade pack/classifier/widgets so prefill owns this type",
                    }
                )
            elif is_llm_expected_type(ftype, label=label):
                llm_expected.append({**entry, "status": "filled_by_llm"})
            else:
                # Novel screening answered by LLM — expected
                llm_expected.append({**entry, "status": "novel_filled_by_llm"})
        elif is_prefill_via(via) or via:
            prefill_rows.append(entry)
        else:
            other_filled.append(entry)

    # Leftovers still present after Flash = blank bugs (esp. essays)
    for u in leftovers:
        if u.get("flash_candidate") is False:
            continue
        reason = str(u.get("reason") or "")
        if reason.startswith("blocker:"):
            continue
        # Field not present on this form — not a blank / regression
        if reason == "url_field_not_found":
            continue
        ftype = _norm_type(u.get("type") or u.get("automation_id"))
        label = str(u.get("label") or u.get("automation_id") or "")
        entry = {
            "type": ftype or None,
            "label": label[:120],
            "reason": reason[:120],
            "selector": (u.get("selector") or "")[:160],
            "essay": bool(u.get("essay") or is_llm_expected_type(ftype, label=label)),
        }
        if is_llm_expected_type(ftype, label=label) or entry["essay"]:
            blank_bugs.append({**entry, "issue": "essay_or_free_text_still_blank"})
            llm_expected.append({**entry, "status": "still_blank"})
        elif is_deterministic_type(ftype, label=label):
            blank_bugs.append({**entry, "issue": "deterministic_still_blank"})
            prefill_regressions.append(
                {
                    **entry,
                    "via": "unfilled",
                    "issue": "deterministic_blank_after_prefill_and_llm",
                    "fix_hint": "prefill missed this type; Flash also failed",
                }
            )
        else:
            blank_bugs.append({**entry, "issue": "leftover_unanswered"})

    # Vision empties vs claimed fills → false_success
    false_success: list[dict] = []
    empties = list(vision_empties or [])
    if vision and isinstance(vision.get("empty_fields"), list):
        empties = empties or list(vision["empty_fields"])
    filled_ok = [f for f in filled if _row_ok(f)]
    filled_labels = {
        _norm_label(str(f.get("label") or ""))
        for f in filled_ok
        if f.get("label")
    }
    filled_types = {
        _norm_type(f.get("type"))
        for f in filled_ok
        if f.get("type")
    }
    for empty in empties:
        if isinstance(empty, dict):
            elabel = str(empty.get("label") or empty.get("name") or "")
            etype = _norm_type(empty.get("type"))
            kind = str(empty.get("kind") or "")
            hint = empty
        else:
            elabel = str(empty)
            etype = ""
            kind = ""
            hint = {"label": elabel}
        el_l = _norm_label(elabel)
        matched_label = False
        if el_l and el_l in filled_labels:
            matched_label = True
        elif elabel:
            for fl in filled_ok:
                if _labels_overlap(elabel, str(fl.get("label") or "")):
                    matched_label = True
                    break
        # Unchecked sponsorship / Yes-No: also match by deterministic type from label
        if not etype and kind in ("unchecked", "placeholder", "blank", "essay_empty", ""):
            if re.search(r"sponsor|us\s+citizen|permanent\s+resident|\bopt\b", elabel, re.I):
                etype = "SPONSORSHIP"
            elif re.search(r"linked[\s_-]*in", elabel, re.I):
                etype = "LINKEDIN"
            elif re.search(r"github", elabel, re.I):
                etype = "GITHUB"
            elif re.search(r"portfolio|personal\s+website|website\s+url", elabel, re.I):
                etype = "PORTFOLIO"
            elif re.search(r"school", elabel, re.I):
                etype = "SCHOOL"
            elif re.search(r"degree", elabel, re.I):
                etype = "DEGREE"
            elif re.search(r"desired\s+salary|expected\s+salary", elabel, re.I):
                etype = "SALARY_EXPECTED"
            elif re.search(r"reside", elabel, re.I):
                etype = "LOCATION"
            elif re.search(r"commute", elabel, re.I):
                etype = "COMMUTE"
        if matched_label:
            false_success.append(
                {
                    **hint,
                    "issue": "screenshot_empty_but_report_claims_filled",
                    "matched_label": elabel[:120],
                }
            )
        elif etype and etype in filled_types:
            false_success.append(
                {
                    **hint,
                    "issue": "screenshot_empty_but_report_claims_type_filled",
                    "matched_type": etype,
                }
            )
        elif etype and is_deterministic_type(etype, label=elabel):
            # Vision blank on deterministic type that was NOT in filled → blank_bug
            blank_bugs.append(
                {
                    "type": etype,
                    "label": elabel[:120],
                    "kind": kind or "blank",
                    "issue": "vision_blank_deterministic",
                    "reason": "vision_empty_not_in_filled",
                }
            )
            if etype not in filled_types:
                prefill_regressions.append(
                    {
                        "type": etype,
                        "label": elabel[:120],
                        "via": "unfilled",
                        "issue": "deterministic_blank_after_prefill_and_llm",
                        "fix_hint": "prefill missed this type; Flash also failed",
                    }
                )

    flash_payload = report.get("flash") if isinstance(report.get("flash"), dict) else {}
    summary = {
        "prefill_count": len(prefill_rows),
        "flash_count": len(flash_rows),
        "prefill_regression_count": len(prefill_regressions),
        "llm_expected_count": len(llm_expected),
        "blank_bug_count": len(blank_bugs),
        "false_success_count": len(false_success),
        "flash_invoked": bool(
            report.get("flash_called") or flash_payload.get("invoked")
        ),
        "never_submit": report.get("never_submit") is True,
        "platform": report.get("platform"),
        "url": report.get("url"),
    }

    return {
        "prefill": prefill_rows,
        "flash": flash_rows,
        "regressions": prefill_regressions,
        "prefill_regressions": prefill_regressions,
        "llm_expected": llm_expected,
        "blank_bugs": blank_bugs,
        "false_success": false_success,
        "other_filled": other_filled,
        "summary": summary,
        "deterministic_catalog_size": len(DETERMINISTIC_TYPES),
        "dummy": True,
        "never_submit": True,
    }


def write_attribution(attr: dict, path: Path | str) -> Path:
    """Write attribution JSON beside cycle artifacts."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(attr, indent=2))
    return out


def self_test() -> dict[str, Any]:
    """Fixture-based unit check (no browser)."""
    report = {
        "url": "https://example.com/jobs/1",
        "platform": "ashby",
        "never_submit": True,
        "flash_called": True,
        "filled": [
            {
                "type": "EMAIL",
                "label": "Email",
                "via": "ashby_selector_pack",
                "ok": True,
                "verified": True,
                "value": "randommail6969+abc@gmail.com",
            },
            {
                "type": "ADDRESS_ZIP",
                "label": "Zip",
                "via": "inpage_flash",
                "ok": True,
                "verified": True,
                "value": "62701",
            },
            {
                "type": "INTEREST",
                "label": "Why do you want to join us?",
                "via": "inpage_flash",
                "ok": True,
                "verified": True,
                "value": "Excited about the role...",
            },
            {
                "type": "WORK_AUTH",
                "label": "Authorized to work?",
                "via": "extract+classify",
                "ok": True,
                "verified": True,
                "value": "Yes",
            },
        ],
        "leftovers": [
            {
                "label": "Tell us about a hard problem you solved",
                "type": "COVER_LETTER",
                "reason": "no_dummy_essay",
                "flash_candidate": True,
                "essay": True,
            },
            {
                "label": "Phone",
                "type": "PHONE",
                "reason": "no_value",
                "flash_candidate": True,
            },
        ],
        "flash": {"invoked": True, "mode": "inpage_leftovers"},
    }
    attr = analyze_fill_attribution(
        report,
        vision_empties=[{"label": "Email", "type": "EMAIL"}],
    )
    assert any(r["type"] == "ADDRESS_ZIP" for r in attr["prefill_regressions"])
    assert any(
        (r.get("status") == "filled_by_llm" and r.get("type") == "INTEREST")
        for r in attr["llm_expected"]
    )
    assert any(b.get("type") == "COVER_LETTER" for b in attr["blank_bugs"])
    assert any(b.get("type") == "PHONE" for b in attr["blank_bugs"])
    assert any(f.get("issue", "").startswith("screenshot_empty") for f in attr["false_success"])
    assert attr["summary"]["prefill_count"] >= 2
    assert attr["summary"]["flash_count"] >= 2

    # W01: deterministic_reclaim via must NOT count as prefill_regression
    reclaim_report = {
        "url": "https://example.com/jobs/2",
        "platform": "greenhouse",
        "never_submit": True,
        "flash_called": False,
        "filled": [
            {
                "type": "EMAIL",
                "label": "Email",
                "via": "deterministic_reclaim",
                "ok": True,
                "verified": True,
                "value": "randommail6969+xyz@gmail.com",
            },
            {
                "type": "ADDRESS_ZIP",
                "label": "Zip",
                "via": "prefill_reclaim",
                "ok": True,
                "verified": True,
                "value": "62701",
            },
            {
                "type": "COVER_LETTER",
                "label": "Why join us?",
                "via": "inpage_flash",
                "ok": True,
                "verified": True,
                "value": "Grounded dummy essay…",
            },
        ],
        "leftovers": [],
        "flash": {"invoked": True, "mode": "inpage_leftovers"},
    }
    attr_r = analyze_fill_attribution(reclaim_report)
    assert not any(
        r.get("type") in ("EMAIL", "ADDRESS_ZIP") for r in attr_r["prefill_regressions"]
    ), "reclaim via must not be logged as Flash steal"
    assert attr_r["summary"]["prefill_count"] >= 2
    assert any(
        r.get("type") == "COVER_LETTER" for r in attr_r["llm_expected"]
    )

    # LinkedIn URL (vision) ↔ LINKEDIN (report type) false_success
    linkedin_report = {
        "url": "https://jobs.ashbyhq.com/example/1",
        "platform": "ashby",
        "never_submit": True,
        "flash_called": False,
        "filled": [
            {
                "type": "LINKEDIN",
                "label": "",
                "via": "replay",
                "ok": True,
                "verified": True,
                "value": "https://www.linkedin.com/in/test-dummy-000000000",
            }
        ],
        "leftovers": [],
        "flash": {"invoked": False},
    }
    attr2 = analyze_fill_attribution(
        linkedin_report,
        vision_empties=[{"label": "LinkedIn URL", "kind": "blank"}],
    )
    assert any(
        f.get("matched_type") == "LINKEDIN"
        or "linkedin" in str(f.get("label") or "").lower()
        for f in attr2["false_success"]
    ), attr2["false_success"]
    return {"ok": True, "summary": attr["summary"], "linkedin_false_success": True}


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("report_json", nargs="?", type=Path, help="fast_fill report JSON")
    ap.add_argument("--self-test", action="store_true", help="Run fixture unit test")
    ap.add_argument("--out", type=Path, help="Write attribution JSON")
    ap.add_argument(
        "--vision",
        type=Path,
        help="Optional vision_judge JSON (empty_fields used for false_success)",
    )
    args = ap.parse_args()

    if args.self_test:
        result = self_test()
        print(json.dumps(result, indent=2))
        print("self-test OK")
        return 0

    if not args.report_json:
        ap.error("report_json required unless --self-test")
    report = json.loads(args.report_json.read_text())
    vision = None
    if args.vision and args.vision.exists():
        vision = json.loads(args.vision.read_text())
    attr = analyze_fill_attribution(report, vision=vision)
    if args.out:
        write_attribution(attr, args.out)
        print(f"wrote {args.out}")
    print(json.dumps(attr["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
