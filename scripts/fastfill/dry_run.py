"""Zero-LLM dry run: dummy resume + DUMMY_PROFILE → value map + cheat sheet.

Proves the fast path uses Test Dummy data only (never profile.json), and
prints how many fields Layer 0/1 already resolve before DeepSeek-V4-Flash
would be asked anything.

By default allocates a random per-run email (allocate_random_run_email) so two
consecutive dry runs never share an address. Pass --base-fixture to check the
static fixture PDF against base DUMMY_PROFILE only (no allocation).

Usage:
  .venv/bin/python3 scripts/fastfill/dry_run.py
  .venv/bin/python3 scripts/fastfill/dry_run.py --check-consistency
  .venv/bin/python3 scripts/fastfill/dry_run.py --compile-resume
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from field_map import (  # noqa: E402
    CURRENT_COMPANY,
    CURRENT_TITLE,
    DEGREE,
    DUMMY_ADDRESS,
    DUMMY_PDF,
    DUMMY_PROFILE,
    EMAIL,
    NAME_FULL,
    PHONE,
    SCHOOL,
    assert_dummy_is_clean,
    build_value_map,
    load_profile,
    validate_filled,
)
from resume_parser import parse_resume, resume_value_map  # noqa: E402
from run_identity import prepare_dummy_run  # noqa: E402

# Layer 0/1 keys that must resolve for a usable dummy fill (non-Flash).
_REQUIRED_VALUE_KEYS = (
    NAME_FULL,
    EMAIL,
    PHONE,
    CURRENT_COMPANY,
    CURRENT_TITLE,
    SCHOOL,
    DEGREE,
    "DISCIPLINE",
    "ADDRESS_CITY",
    "ADDRESS_STATE",
    "ADDRESS_ZIP",
    "WORK_AUTH",
    "SPONSORSHIP",
    "GENDER",
    "VETERAN",
    "DISABILITY",
    "HISPANIC",
    "PASSWORD",
)


def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _norm_org(s: str) -> str:
    """Soft-compare employers: 'Example Corp' == 'Example Corporation'."""
    s = (s or "").lower().strip()
    s = re.sub(r"\b(corporation|corp|inc|llc|ltd|co)\.?\b", "", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def check_resume_profile_consistency(
    parsed: dict,
    expect_email: str,
    expect_name: str,
    expect_phone: str,
    address: str,
    *,
    profile: dict | None = None,
    values: dict | None = None,
) -> list[str]:
    """Resume contact + experience/education must agree with DUMMY_PROFILE / values."""
    issues: list[str] = []
    profile = profile or DUMMY_PROFILE
    values = values or {}

    # --- contact ---
    if parsed.get("full_name") and parsed["full_name"] != expect_name:
        issues.append(f"name: resume={parsed['full_name']!r} profile={expect_name!r}")
    if parsed.get("email") and parsed["email"].lower() != expect_email.lower():
        issues.append(f"email: resume={parsed['email']!r} profile={expect_email!r}")
    if parsed.get("phone"):
        rd = _digits(parsed["phone"])
        pd = _digits(expect_phone)
        if rd and pd and rd[-10:] != pd[-10:]:
            issues.append(f"phone: resume={parsed['phone']!r} profile={expect_phone!r}")

    # City/state from one address string (Hoboken/NJ vs Springfield/IL class of bug).
    if parsed.get("city") and address and parsed["city"] not in address:
        issues.append(
            f"city: resume={parsed.get('city_state')!r} address={address!r}"
        )
    if parsed.get("state"):
        m = re.search(r"\b([A-Z]{2})\s+\d{5}\b", address or "")
        if m and parsed["state"] != m.group(1):
            issues.append(
                f"state: resume={parsed['state']!r} address_state={m.group(1)!r}"
            )

    # --- experience (fill map must not contradict uploaded PDF) ---
    exp = profile.get("experience") or {}
    expect_co = (values.get(CURRENT_COMPANY) or exp.get("current_company") or "").strip()
    expect_title = (values.get(CURRENT_TITLE) or exp.get("current_title") or "").strip()
    if parsed.get("current_company") and expect_co:
        if _norm_org(parsed["current_company"]) != _norm_org(expect_co):
            issues.append(
                f"company: resume={parsed['current_company']!r} profile={expect_co!r}"
            )
    if parsed.get("current_title") and expect_title:
        if _norm_title(parsed["current_title"]) != _norm_title(expect_title):
            issues.append(
                f"title: resume={parsed['current_title']!r} profile={expect_title!r}"
            )

    # --- education: each DUMMY_PROFILE degree school must appear in resume edu lines ---
    edu_lines = " | ".join(parsed.get("education") or []).lower()
    for deg in (profile.get("education") or {}).get("degrees") or []:
        school = (deg.get("school") or "").strip()
        degree = (deg.get("degree") or "").strip()
        if school:
            # Match on the institution head ("University of Alabama", "GITAM")
            head = school.split(",")[0].strip().lower()
            if head and edu_lines and head not in edu_lines:
                issues.append(
                    f"school: profile={school!r} not found in resume education={parsed.get('education')!r}"
                )
        if degree:
            # Only "M.S., Subject" / "B.S., Subject" carry a subject token.
            # Level-only labels ("Master's Degree") skip subject checks.
            m = re.match(r"^[mb]\.?[as]\.?\s*,\s*(.+)$", degree, flags=re.I)
            if m:
                subj = m.group(1).strip().lower()
                if subj and edu_lines and subj not in edu_lines:
                    issues.append(
                        f"degree: profile={degree!r} subject missing from resume education={parsed.get('education')!r}"
                    )
        disc = (deg.get("discipline") or deg.get("major") or "").strip().lower()
        if disc and edu_lines and disc not in edu_lines:
            # Soft: allow resume to use shorter/longer catalog wording
            if not any(tok in edu_lines for tok in disc.split() if len(tok) > 3):
                issues.append(
                    f"discipline: profile={disc!r} missing from resume education={parsed.get('education')!r}"
                )

    # --- value map must echo profile contact/experience (no silent overwrite) ---
    if values:
        if values.get(NAME_FULL) and values[NAME_FULL] != expect_name:
            issues.append(
                f"values.NAME_FULL={values[NAME_FULL]!r} != profile={expect_name!r}"
            )
        if values.get(PHONE):
            vd, pd = _digits(str(values[PHONE])), _digits(expect_phone)
            if vd and pd and vd[-10:] != pd[-10:]:
                issues.append(
                    f"values.PHONE={values[PHONE]!r} != profile={expect_phone!r}"
                )
        if expect_co and values.get(CURRENT_COMPANY):
            if _norm_org(str(values[CURRENT_COMPANY])) != _norm_org(expect_co):
                issues.append(
                    f"values.CURRENT_COMPANY={values[CURRENT_COMPANY]!r} != profile={expect_co!r}"
                )

    return issues


def check_value_map_completeness(values: dict) -> list[str]:
    """Required Layer 0/1 keys must be non-empty and pass type validators."""
    issues = []
    for key in _REQUIRED_VALUE_KEYS:
        val = values.get(key)
        if not val or not validate_filled(key, str(val)):
            issues.append(f"missing_or_invalid:{key}={val!r}")
    # EEO: gender/disability/veteran/hispanic prefer concrete dummy answers;
    # race stays Decline. Never invent free-text beyond DUMMY_PROFILE.
    eeo_expect = {
        "GENDER": ("male", "man"),
        "VETERAN": ("not a", "no,", "i am not"),
        "DISABILITY": ("do not have a disability", "don't have a disability", "no disability", "not disabled"),
        "HISPANIC": ("no", "not hispanic", "not latino"),
        "RACE": ("decline", "prefer not", "wish not", "do not want", "don't want"),
    }
    for key, needles in eeo_expect.items():
        val = str(values.get(key) or "").lower()
        if not val:
            issues.append(f"eeo_missing:{key}")
            continue
        if not any(n in val for n in needles):
            issues.append(f"eeo_unexpected:{key}={values.get(key)!r}")
    base = DUMMY_PROFILE["contact"]["email"].lower()
    email = str(values.get(EMAIL) or "").lower()
    if email and not (email == base or email.startswith(base.split("@")[0] + "+")):
        issues.append(f"email_not_dummy_alias:{email!r}")
    return issues


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-consistency", action="store_true",
                    help="Fail if resume contact drifts from run identity / DUMMY_PROFILE")
    ap.add_argument("--json", action="store_true", help="Machine-readable dump")
    ap.add_argument("--base-fixture", action="store_true",
                    help="Skip random allocation; check static fixture vs base DUMMY_PROFILE")
    ap.add_argument("--compile-resume", action="store_true",
                    help="Compile a run-specific resume PDF (tectonic)")
    args = ap.parse_args()

    leaks = assert_dummy_is_clean()
    address = DUMMY_ADDRESS
    if args.base_fixture:
        profile, address, is_dummy = load_profile()
        assert is_dummy
        values = build_value_map(profile, address)
        values.update({
            k: v for k, v in resume_value_map(parse_resume(DUMMY_PDF)).items()
            if v and not values.get(k)
        })
        email = values.get(EMAIL) or DUMMY_PROFILE["contact"]["email"]
        parsed = parse_resume(DUMMY_PDF)
        resume_pdf = DUMMY_PDF
        compiled = False
        alias_token = ""
    else:
        identity = prepare_dummy_run(compile_pdf=args.compile_resume)
        values = dict(identity.values)
        email = identity.email
        is_dummy = True
        resume_pdf = identity.resume_pdf
        compiled = identity.compiled
        alias_token = identity.alias_token
        if compiled:
            parsed = parse_resume(resume_pdf)
        else:
            # Logical path: values carry the run email; fixture PDF still has base.
            parsed = dict(parse_resume(DUMMY_PDF))
            parsed["email"] = email

    expect_name = DUMMY_PROFILE.get("personal", {}).get("full_name", "")
    expect_phone = DUMMY_PROFILE.get("contact", {}).get("phone", "")
    issues = check_resume_profile_consistency(
        parsed,
        email,
        expect_name,
        expect_phone,
        address,
        profile=DUMMY_PROFILE,
        values=values,
    )
    issues.extend(check_value_map_completeness(values))
    # When not compiling, fixture PDF email != run email by design — don't hard-fail.
    if not args.base_fixture and not compiled:
        issues = [i for i in issues if not i.startswith("email:")]

    filled = {k: v for k, v in values.items() if v and validate_filled(k, str(v))}
    empty = [k for k, v in values.items() if not v]

    from learning import learned_cheat_sheet_rows
    from field_map import PATTERNS, AUTOCOMPLETE_MAP

    rows = []
    for ftype, pattern in PATTERNS.items():
        val = values.get(ftype)
        if val and validate_filled(ftype, str(val)):
            gist = pattern.split("|")[0]
            rows.append(f"  - fields about {gist!r} -> {val!r}")
    for token, ftype in AUTOCOMPLETE_MAP.items():
        val = values.get(ftype) if ftype else None
        if val and validate_filled(ftype, str(val)):
            rows.append(f'  - autocomplete="{token}" -> {val!r}')
    learned = learned_cheat_sheet_rows()
    if learned:
        rows.append("  --- learned ---")
        rows.extend(learned)
    cheat = ("KNOWN FIELD MAPPING:\n" + "\n".join(rows)) if rows else ""
    cheat_chars = len(cheat)
    report = {
        "is_dummy": is_dummy,
        "dummy_pdf": str(resume_pdf),
        "email": email,
        "email_alias": email,
        "alias_token": alias_token,
        "identity_email": email,
        "resume_compiled": compiled,
        "identity": {
            "name": values.get("NAME_FULL"),
            "email": values.get("EMAIL"),
            "phone": values.get("PHONE"),
            "address": address,
            "city": values.get("ADDRESS_CITY"),
            "state": values.get("ADDRESS_STATE"),
            "zip": values.get("ADDRESS_ZIP"),
        },
        "layer01_filled": len(filled),
        "layer01_empty_keys": empty,
        "cheat_sheet_chars": cheat_chars,
        "dummy_vs_real_leaks": leaks,
        "consistency_issues": issues,
        "resume_parsed": {
            "full_name": parsed.get("full_name"),
            "email": parsed.get("email"),
            "phone": parsed.get("phone"),
            "city_state": parsed.get("city_state"),
            "current_company": parsed.get("current_company"),
            "current_title": parsed.get("current_title"),
            "education": parsed.get("education"),
        },
        "values": filled,
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("=== fastfill dry-run (0 LLM, DeepSeek-V4-Flash not called) ===")
        print(f"dummy: {is_dummy}  pdf: {Path(resume_pdf).name}")
        print(f"email_alias: {email}")
        print(f"identity: {report['identity']}")
        print(f"Layer 0/1 resolved: {len(filled)} fields (Flash should only see leftovers)")
        print(f"cheat sheet: {cheat_chars} chars (stable prefix → prompt-cache friendly)")
        print(f"dummy↔real leak check: {'CLEAN' if not leaks else f'{leaks} LEAK(S)'}")
        if issues:
            print("consistency warnings:")
            for i in issues:
                print(f"  - {i}")
        else:
            print("consistency: resume contact matches run identity / DUMMY_PROFILE")
        print("\nResolved values:")
        for k in sorted(filled):
            print(f"  {k:22s} = {filled[k]!r}")

    if args.check_consistency and (leaks or issues):
        # Soft: city-only advisory kept for back-compat messaging (now hard above).
        hard = [i for i in issues if not i.startswith("city drift")]
        if leaks or hard:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
