#!/usr/bin/env python3
"""Same-company sibling filters must match discovery_filters policy.

Mirrors dashboard/static/app.js companySiblings() which applies
isExcludedTitle, isClearlyNonUsLocation, and jobRequiresClearance so
principal/staff/director/…, non-US, and clearance/intel roles never appear
in the mini table or Same company count.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from discovery_filters import (  # noqa: E402
    NON_US_ISO2_CODES,
    US_STATE_ABBREVS,
    auto_delete_reason,
    is_clearly_non_us_location,
    is_excluded_title,
    requires_excessive_experience,
    requires_security_clearance,
    requires_us_citizen_or_greencard,
)

APP_JS = ROOT / "dashboard" / "static" / "app.js"
CLASSIC_JS = ROOT / "dashboard" / "static" / "classic.js"


def sibling_kept(*, title: str, location: str = "Remote, US", company: str = "Acme",
                 description: str = "") -> bool:
    if is_excluded_title(title):
        return False
    if is_clearly_non_us_location(location):
        return False
    if requires_security_clearance(
        title=title, company=company, location=location, description=description
    ):
        return False
    if requires_excessive_experience(title=title, description=description):
        return False
    if requires_us_citizen_or_greencard(title=title, description=description):
        return False
    return True


def test_sibling_yoe_independent():
    """High-YOE sibling hidden; low-YOE peer still listed (per-job only)."""
    assert not sibling_kept(
        title="Data Scientist",
        description="Requires 8+ years of experience in ML.",
    )
    assert sibling_kept(
        title="Data Scientist",
        description="3+ years of experience building models.",
    )
    assert auto_delete_reason(
        title="Data Scientist",
        location="Remote, US",
        description="Requires 8+ years of experience.",
    ) == "excessive_yoe"


def _extract_js_re(src: str, name: str) -> re.Pattern:
    m = re.search(rf"const {name} = /(.+)/i;", src)
    assert m, f"{name} missing from JS"
    return re.compile(m.group(1), re.I)


def _extract_js_set(src: str, name: str) -> set[str]:
    m = re.search(rf"const {name} = new Set\(\[(.*?)\]\);", src, re.S)
    assert m, f"{name} missing from JS"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def main() -> int:
    js = APP_JS.read_text(encoding="utf-8")
    classic = CLASSIC_JS.read_text(encoding="utf-8")
    assert "function companySiblings" in js
    assert "isExcludedTitle(j.title)" in js, (
        "companySiblings must filter with isExcludedTitle"
    )
    assert "jobRequiresClearance(j)" in js, (
        "companySiblings must filter with jobRequiresClearance"
    )
    assert "jobRequiresClearance" in classic
    assert "CLEARANCE_REQUIREMENT_RE" in classic
    assert "CLEARANCE_EXPLICITLY_NOT_REQUIRED_RE" in js
    assert "CLEARANCE_EXPLICITLY_NOT_REQUIRED_RE" in classic
    assert "blob.replace(CLEARANCE_EXPLICITLY_NOT_REQUIRED_RE" in js
    assert "blob.replace(CLEARANCE_EXPLICITLY_NOT_REQUIRED_RE" in classic
    # Regex bodies must stay in sync across dashboards (\/ escaping aside).
    for name in (
        "SENIORITY_EXCLUDE_RE",
        "NON_US_LOCATION_RE",
        "US_LOCATION_STRONG_RE",
        "US_STATE_ABBREV_RE",
        "CLEARANCE_REQUIREMENT_RE",
        "CLEARANCE_EXPLICITLY_NOT_REQUIRED_RE",
        "INTEL_AGENCY_COMPANY_RE",
        "INTEL_AGENCY_URL_RE",
    ):
        a = _extract_js_re(js, name).pattern
        c = _extract_js_re(classic, name).pattern
        assert a == c, f"{name} app.js != classic.js"

    # ISO-2 country-tail tables (Bengaluru, KA, in) must match Python.
    for name, py_set in (
        ("US_STATE_ABBREVS", US_STATE_ABBREVS),
        ("NON_US_ISO2_CODES", NON_US_ISO2_CODES),
    ):
        a = _extract_js_set(js, name)
        c = _extract_js_set(classic, name)
        assert a == c, f"{name} app.js != classic.js"
        assert a == py_set, f"{name} JS != discovery_filters.py"

    seniority_re = _extract_js_re(js, "SENIORITY_EXCLUDE_RE")
    clearance_re = _extract_js_re(js, "CLEARANCE_REQUIREMENT_RE")
    clearance_none_re = _extract_js_re(js, "CLEARANCE_EXPLICITLY_NOT_REQUIRED_RE")
    agency_re = _extract_js_re(js, "INTEL_AGENCY_COMPANY_RE")

    def js_clearance_hit(blob: str) -> bool:
        cleaned = clearance_none_re.sub(" ", blob)
        return bool(clearance_re.search(cleaned))

    keep = [
        ("Senior Machine Learning Engineer", "Acme", ""),
        ("Junior Data Scientist", "Acme", ""),
        ("Machine Learning Engineer", "Acme", ""),
        ("Mid-level Data Engineer", "Acme", ""),
        ("Software Engineer II", "Acme", ""),
        ("Security Engineer", "Google", "Build product security for consumer apps."),
        ("Data Scientist", "Acme", "CLEARANCE REQUIRED FOR START: No"),
        ("AI Engineer", "Guidehouse", "Clearance Required\n:\nNone"),
        (
            "Developer",
            "Ripple Effect",
            "Clearance: Must be able to work in the U.S. without employer sponsorship",
        ),
    ]
    drop_title = [
        "Principal Machine Learning Engineer",
        "Staff Software Engineer, Core Matching",
        "Director of AI",
        "VP of Engineering",
        "Lead Machine Learning Engineer",
        "Engineering Manager",
        "Staff Architect",
        "Staff Security Engineer",
    ]
    drop_clearance = [
        ("MLOps Engineer - TS/SCI", "Parsons", ""),
        ("Data Engineer", "Acme", "Must have Secret clearance."),
        ("Research Scientist", "National Security Agency", ""),
        ("AI Engineer", "NSA", ""),
        ("AI/ML Engineer", "Castalia", "Clearance: Secret with ability to get DHS Suitability"),
        ("Senior Data Scientist (Public Trust)", "Praescient", ""),
        ("AI Engineer", "LTS", "Clearance: Ability to obtain and maintain a Public Trust"),
        ("Data Engineer", "Booz", "meeting security and clearance requirements."),
        (
            "Engineer",
            "Guidehouse",
            "Travel Required\n:\nNone\nClearance … [full text in resumes/<id>/jd_full.txt]",
        ),
        (
            "Data Engineer",
            "Northrop",
            "CLEARANCE REQUIRED FOR START: No\nCLEARANCE TYPE: Secret",
        ),
    ]

    for title, company, desc in keep:
        assert sibling_kept(title=title, company=company, description=desc), (
            f"py should keep: {title} @ {company}"
        )
        assert not seniority_re.search(title), f"js seniority should keep: {title}"
        blob = f"{title} {company} {desc}"
        assert not js_clearance_hit(blob), f"js clearance should keep: {title}"
        assert not agency_re.search(company), f"js agency should keep: {company}"

    for t in drop_title:
        assert not sibling_kept(title=t), f"py should hide: {t}"
        assert seniority_re.search(t), f"js should hide: {t}"

    for title, company, desc in drop_clearance:
        assert not sibling_kept(title=title, company=company, description=desc), (
            f"py should hide clearance: {title} @ {company}"
        )
        blob = f"{title} {company} {desc}"
        assert js_clearance_hit(blob) or agency_re.search(company), (
            f"js should hide clearance: {title} @ {company}"
        )

    test_sibling_yoe_independent()
    assert "MAX_ACCEPTABLE_MIN_YOE" in js
    assert "extractMinRequiredYoe" in js
    assert "jobRequiresExcessiveYoe" in js or "requiresExcessiveExperience" in js
    assert "CITIZENSHIP_OR_GC_REQUIREMENT_RE" in js

    print("test_company_siblings_filter: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
