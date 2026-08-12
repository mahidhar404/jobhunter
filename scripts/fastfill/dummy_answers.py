"""Shared deterministic fill answers — single source for dummy AND real modes.

Architecture
------------
* **Shared** (identical for every run): EEO, work-auth, sponsorship, screening,
  notice, relocation, how_heard, salary canned, interest canned, consents, etc.
  → ``SHARED_FILL_POLICY`` + ``shared_values()`` / ``DETERMINISTIC_ANSWERS``
    (shared keys only).
* **Unique** (per profile): name, phone, email, links, education, resume PDF,
  passwords, address, experience/current role.
  → built from DUMMY_PROFILE or real profile.json via ``build_unique_values``.

``compose_fill_values(unique, shared)`` merges them. Dummy and real must agree
on every shared key; they differ only on unique keys.

Never real PII in this file. Never submit. Decline is last-resort fallback only
when the employer option list lacks preferred labels
(see gh_select.is_decline_like_alias).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Shared policy block — ONE copy for dummy + real (profile sections + values)
# ---------------------------------------------------------------------------
SHARED_FILL_POLICY: dict = {
    "eeo_demographic": {
        "gender": "Male",
        "hispanic_or_latino": "No",
        "race_ethnicity": "Decline to self identify",
        "veteran_status": "I am not a protected veteran",
        "disability_status": "I do not have a disability",
    },
    "work_authorization": {
        "requires_sponsorship": "No",
        "status": "US Citizen",
    },
    "work_preferences": {
        "relocation": "Yes, willing to relocate",
        "notice_period": "Immediately available",
    },
    "standard_screening_answers": {
        "age_18_or_older": True,
        "worked_here_before_or_relative_employed": "No",
        "felony_conviction": "No",
        # Ashby Truelogic LATAM screening — shared policy Yes
        "based_in_latin_america": "Yes",
        "has_security_clearance": "No",
        "security_clearance_type": "None",
        "us_citizen": "Yes",
        "visa_requirement_status": "No visa required",
    },
    "custom_question_answers": {
        "how_did_you_hear_about_this_job": "Internet job board",
        "why_interested": (
            "I'm interested in this role based on the posted description and "
            "how it aligns with my relevant experience."
        ),
        "compensation_expectation": "Open / negotiable within the posted range",
    },
}

# Canonical preferred answers (exact strings fed to selects / value maps).
# Education + NAME_FULL live here for *dummy unique defaults / catalog docs*
# but are NOT applied via shared_values() — those are UNIQUE_VALUE_TYPES.
DETERMINISTIC_ANSWERS: dict[str, str] = {
    # --- EEO / demographics (concrete first; Decline only if option missing) ---
    "GENDER": "Male",
    "HISPANIC": "No",
    "RACE": "Decline to self identify",  # race stays Decline by shared policy
    "VETERAN": "I am not a protected veteran",
    "DISABILITY": "I do not have a disability",
    "AGE_RANGE": "Prefer not to disclose",
    "LGBTQIA": "Prefer not to disclose",
    "PRONOUNS": "Prefer not to say",
    # --- Work auth / screening ---
    "WORK_AUTH": "Yes",
    "US_RESIDENCE": "Yes",
    "US_CITIZEN": "Yes",
    "SPONSORSHIP": "No",
    "CLEARANCE": "No",
    "CLEARANCE_TYPE": "None",
    "VISA_STATUS": "No visa required",
    "BACKGROUND_CHECK": "Yes",
    "AGE_18": "Yes",
    "FELONY": "No",
    "WORKED_HERE_BEFORE": "No",
    "SERVICE_MEMBER": "No",
    "TALENT_HUB": "No",
    "COMMUTE": "Yes",
    "RELOCATION": "Yes, willing to relocate",
    "NOTICE_PERIOD": "Immediately available",
    "MARKETING_CONSENT": "No",
    "TERMS_CONSENT": "Yes",
    "ACCOMMODATIONS": "No",
    "ACCOMMODATIONS_DETAILS": "N/A",
    "EMPLOYEE_REFERRAL": "No",
    "REFERRAL_EMAIL": "N/A",
    "LATIN_AMERICA": "Yes",
    # --- Compensation / sourcing / interest (shared canned) ---
    "SALARY_EXPECTED": "Open / negotiable within the posted range",
    "HOW_HEARD": "Internet job board",
    "INTEREST": (
        "I'm interested in this role based on the posted description and "
        "how it aligns with my relevant experience."
    ),
    # --- Education (DUMMY unique defaults / catalog — not shared_values) ---
    "SCHOOL": "University of Alabama, Tuscaloosa",
    "DEGREE": "Master's Degree",
    "DISCIPLINE": "Computer Science",
    "MAJOR": "Computer Science",
    "FIELD_OF_STUDY": "Computer Science",
    # --- Affirmation default (dummy unique; real uses real name) ---
    "NAME_FULL": "Test Dummy",
}

# Types identical for dummy and real after compose.
SHARED_VALUE_TYPES: frozenset[str] = frozenset(
    {
        "GENDER",
        "HISPANIC",
        "RACE",
        "VETERAN",
        "DISABILITY",
        "AGE_RANGE",
        "LGBTQIA",
        "PRONOUNS",
        "WORK_AUTH",
        "US_RESIDENCE",
        "US_CITIZEN",
        "SPONSORSHIP",
        "CLEARANCE",
        "CLEARANCE_TYPE",
        "VISA_STATUS",
        "BACKGROUND_CHECK",
        "AGE_18",
        "FELONY",
        "WORKED_HERE_BEFORE",
        "SERVICE_MEMBER",
        "TALENT_HUB",
        "COMMUTE",
        "RELOCATION",
        "NOTICE_PERIOD",
        "MARKETING_CONSENT",
        "TERMS_CONSENT",
        "ACCOMMODATIONS",
        "ACCOMMODATIONS_DETAILS",
        "EMPLOYEE_REFERRAL",
        "REFERRAL_EMAIL",
        "LATIN_AMERICA",
        "SALARY_EXPECTED",
        "SALARY_CURRENT",  # empty for both — never invent a figure
        "HOW_HEARD",
        "INTEREST",
    }
)

# Types that differ (or may differ) between dummy and real.
UNIQUE_VALUE_TYPES: frozenset[str] = frozenset(
    {
        "NAME_FIRST",
        "NAME_LAST",
        "NAME_FULL",
        "NAME_MIDDLE",
        "RELATIVE_NAME",
        "EMAIL",
        "PHONE",
        "PHONE_EXTENSION",
        "LINKEDIN",
        "GITHUB",
        "PORTFOLIO",
        "TWITTER",
        "PASSWORD",
        "PASSWORD_CONFIRM",
        "RESUME_UPLOAD",
        "SCHOOL",
        "DEGREE",
        "DISCIPLINE",
        "MAJOR",
        "FIELD_OF_STUDY",
        "EDUCATION_START_YEAR",
        "EDUCATION_END_YEAR",
        "YEARS_EXPERIENCE",
        "CURRENT_COMPANY",
        "CURRENT_TITLE",
        "APPLYING_FOR",
        "ADDRESS_LINE1",
        "ADDRESS_LINE2",
        "ADDRESS_CITY",
        "ADDRESS_STATE",
        "ADDRESS_ZIP",
        "ADDRESS_COUNTY",
        "ADDRESS_COUNTRY",
        "LOCATION",
    }
)

# Label / question patterns → canonical type (documentation + tests).
# Classification still lives in field_map.PATTERNS; this lists coverage intent.
CATALOG_COVERAGE: list[tuple[str, str, str]] = [
    # (field_type, example_label, preferred_answer)
    ("DISABILITY", "Disability Status", "I do not have a disability"),
    ("DISABILITY", "Do you have a disability?", "I do not have a disability"),
    ("VETERAN", "Veteran Status", "I am not a protected veteran"),
    ("VETERAN", "Protected Veteran", "I am not a protected veteran"),
    ("HISPANIC", "Are you Hispanic/Latino?", "No"),
    ("HISPANIC", "Hispanic or Latino", "No"),
    ("GENDER", "Gender", "Male"),
    ("WORK_AUTH", "Are you authorized to work in the US?", "Yes"),
    ("WORK_AUTH", "Employment Eligibility Information*", "Yes"),
    ("WORK_AUTH", "legally authorized to work for any employer", "Yes"),
    ("SPONSORSHIP", "Will you require sponsorship?", "No"),
    ("CLEARANCE", "Do you have a TS/SCI with Polygraph Security Clearance?", "No"),
    ("CLEARANCE_TYPE", "Security Clearance Type*", "None"),
    ("US_CITIZEN", "Are you a U.S. citizen?", "Yes"),
    ("DEGREE", "Degree", "Master's Degree"),
    ("DEGREE", "Highest level of education", "Master's Degree"),
    ("SCHOOL", "School*", "University of Alabama, Tuscaloosa"),
    ("DISCIPLINE", "Discipline", "Computer Science"),
    ("MAJOR", "Major", "Computer Science"),
    ("FIELD_OF_STUDY", "Field of Study", "Computer Science"),
    ("SALARY_EXPECTED", "Desired Salary*", "Open / negotiable within the posted range"),
    ("NAME_FULL", "Affirmation*", "Test Dummy"),
    ("BACKGROUND_CHECK", "willing to undergo a background check", "Yes"),
    ("MARKETING_CONSENT", "SMS / marketing opt-in", "No"),
    (
        "ACCOMMODATIONS",
        "Do you require reasonable accommodations or adjustments?",
        "No",
    ),
    (
        "ACCOMMODATIONS_DETAILS",
        "If you answered yes to the Reasonable Adjustments question, "
        "please provide additional details. If not, enter N/A.",
        "N/A",
    ),
    ("HOW_HEARD", "How did you hear about this job?", "Internet job board"),
    ("RELOCATION", "Are you willing to relocate?", "Yes, willing to relocate"),
    ("NOTICE_PERIOD", "When can you start?", "Immediately available"),
]

# Greenhouse OFCCP-style option strings used in regression tests.
GH_DISABILITY_OPTIONS = [
    "Yes, I have a disability, or have had one in the past",
    "No, I do not have a disability and have not had one in the past",
    "I do not want to answer",
]

GH_VETERAN_OPTIONS = [
    "I identify as one or more of the classifications of a protected veteran",
    "I am not a protected veteran",
    "I don't wish to answer",
]

GH_HISPANIC_OPTIONS = [
    "Yes",
    "No",
    "Decline To Self Identify",
]

GH_DEGREE_OPTIONS = [
    "High School",
    "Associate Degree",
    "Bachelor's Degree",
    "Master's Degree",
    "Doctorate",
]


def answer_for(field_type: str, default: str = "") -> str:
    """Lookup preferred deterministic answer for a canonical field type."""
    return DETERMINISTIC_ANSWERS.get((field_type or "").upper(), default)


def coverage_types() -> list[str]:
    """Unique field types covered by the catalog."""
    return sorted({t for t, _lab, _ans in CATALOG_COVERAGE})


def shared_values() -> dict[str, str]:
    """Type→value for SHARED_VALUE_TYPES only — identical for dummy and real.

    Built from DETERMINISTIC_ANSWERS + SHARED_FILL_POLICY so there is one
    source of truth (no diverging copies in DUMMY_PROFILE vs real overlay).
    """
    eeo = SHARED_FILL_POLICY["eeo_demographic"]
    screening = SHARED_FILL_POLICY["standard_screening_answers"]
    prefs = SHARED_FILL_POLICY["work_preferences"]
    work = SHARED_FILL_POLICY["work_authorization"]
    custom = SHARED_FILL_POLICY["custom_question_answers"]
    out: dict[str, str] = {
        "GENDER": eeo["gender"],
        "HISPANIC": eeo["hispanic_or_latino"],
        "RACE": eeo["race_ethnicity"],
        "VETERAN": eeo["veteran_status"],
        "DISABILITY": eeo["disability_status"],
        "AGE_RANGE": DETERMINISTIC_ANSWERS["AGE_RANGE"],
        "LGBTQIA": DETERMINISTIC_ANSWERS["LGBTQIA"],
        "PRONOUNS": DETERMINISTIC_ANSWERS["PRONOUNS"],
        "WORK_AUTH": DETERMINISTIC_ANSWERS["WORK_AUTH"],
        "US_RESIDENCE": DETERMINISTIC_ANSWERS["US_RESIDENCE"],
        "US_CITIZEN": str(screening.get("us_citizen") or DETERMINISTIC_ANSWERS["US_CITIZEN"]),
        "SPONSORSHIP": str(
            work.get("requires_sponsorship") or DETERMINISTIC_ANSWERS["SPONSORSHIP"]
        ),
        "CLEARANCE": str(
            screening.get("has_security_clearance") or DETERMINISTIC_ANSWERS["CLEARANCE"]
        ),
        "CLEARANCE_TYPE": str(
            screening.get("security_clearance_type")
            or DETERMINISTIC_ANSWERS["CLEARANCE_TYPE"]
        ),
        "VISA_STATUS": str(
            screening.get("visa_requirement_status")
            or DETERMINISTIC_ANSWERS["VISA_STATUS"]
        ),
        "BACKGROUND_CHECK": DETERMINISTIC_ANSWERS["BACKGROUND_CHECK"],
        "AGE_18": "Yes" if screening.get("age_18_or_older", True) else "No",
        "FELONY": str(
            screening.get("felony_conviction") or DETERMINISTIC_ANSWERS["FELONY"]
        ),
        "WORKED_HERE_BEFORE": str(
            screening.get("worked_here_before_or_relative_employed")
            or DETERMINISTIC_ANSWERS["WORKED_HERE_BEFORE"]
        ),
        "SERVICE_MEMBER": DETERMINISTIC_ANSWERS["SERVICE_MEMBER"],
        "TALENT_HUB": DETERMINISTIC_ANSWERS["TALENT_HUB"],
        "COMMUTE": DETERMINISTIC_ANSWERS["COMMUTE"],
        "RELOCATION": str(prefs.get("relocation") or DETERMINISTIC_ANSWERS["RELOCATION"]),
        "NOTICE_PERIOD": str(
            prefs.get("notice_period") or DETERMINISTIC_ANSWERS["NOTICE_PERIOD"]
        ),
        "MARKETING_CONSENT": DETERMINISTIC_ANSWERS["MARKETING_CONSENT"],
        "TERMS_CONSENT": DETERMINISTIC_ANSWERS["TERMS_CONSENT"],
        "ACCOMMODATIONS": DETERMINISTIC_ANSWERS["ACCOMMODATIONS"],
        "ACCOMMODATIONS_DETAILS": DETERMINISTIC_ANSWERS["ACCOMMODATIONS_DETAILS"],
        "EMPLOYEE_REFERRAL": DETERMINISTIC_ANSWERS["EMPLOYEE_REFERRAL"],
        "REFERRAL_EMAIL": DETERMINISTIC_ANSWERS["REFERRAL_EMAIL"],
        "LATIN_AMERICA": str(
            screening.get("based_in_latin_america")
            or DETERMINISTIC_ANSWERS["LATIN_AMERICA"]
        ),
        "SALARY_EXPECTED": str(
            custom.get("compensation_expectation")
            or DETERMINISTIC_ANSWERS["SALARY_EXPECTED"]
        ),
        "SALARY_CURRENT": "",
        "HOW_HEARD": str(
            custom.get("how_did_you_hear_about_this_job")
            or DETERMINISTIC_ANSWERS["HOW_HEARD"]
        ),
        "INTEREST": str(
            custom.get("why_interested") or DETERMINISTIC_ANSWERS["INTEREST"]
        ),
    }
    # Sanity: only shared keys
    assert set(out) == set(SHARED_VALUE_TYPES), (
        set(out) ^ set(SHARED_VALUE_TYPES)
    )
    return out


def apply_shared_policy_to_profile(profile: dict) -> dict:
    """Return a shallow-copied profile with SHARED_FILL_POLICY sections merged in.

    Unique sections (personal/contact/links/education/experience/account/address)
    are preserved. Policy sections are overwritten from the shared block so
    DUMMY_PROFILE and any real-shaped fixture cannot drift.
    """
    out = dict(profile)
    for key, block in SHARED_FILL_POLICY.items():
        out[key] = dict(block) if isinstance(block, dict) else block
    return out


def assert_shared_policy_synced() -> None:
    """Fail if DETERMINISTIC_ANSWERS shared keys drift from SHARED_FILL_POLICY."""
    vals = shared_values()
    for key in SHARED_VALUE_TYPES:
        if key == "SALARY_CURRENT":
            continue
        det = DETERMINISTIC_ANSWERS.get(key)
        if det is None:
            continue
        got = vals.get(key)
        if str(got) != str(det):
            raise AssertionError(
                f"shared drift {key}: shared_values={got!r} DETERMINISTIC={det!r}"
            )
