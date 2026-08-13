"""Greenhouse react-select (custom widget) fill — 0-LLM, Playwright only.

Greenhouse job-boards use react-select comboboxes whose extract selectors look
like `label:has-text('Country') input:visible`. That fails because the
`<input class="select__input">` is a *sibling* of the label (inside
`.select-shell`), not a descendant, and often fails Playwright `:visible`.

Fill pattern (never Enter — Enter can submit the whole form):
  1. Find `label.select__label` by text
  2. Click `.select__control` in the same `.select__container`
  3. Type a filter fragment into the combobox input (optional)
  4. Click the best-matching `.select__option` (not intl-tel-input options)

DUMMY_PROFILE values often need soft aliases against employer-specific option
lists (e.g. HOW_HEARD "Internet job board" → "Other"; VETERAN decline →
"I don’t wish to answer").
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from verified_select import (
    _fuzzy_salary_score,
    _fuzzy_school_score,
    _is_salary_like,
    _is_school_like,
    fill_typable_dropdown,
    is_placeholder_select_value,
    normalize_select_answer,
    read_gh_select_display,
    select_readback_ok,
    typable_dropdown_narrow_and_click,
    wait_for_option_texts,
)

# Canonical type → preferred option strings (first match wins after exact value).
OPTION_ALIASES: dict[str, list[str]] = {
    "ADDRESS_COUNTRY": [
        "United States",
        "United States of America",
        "United States +1",
        "USA",
        "US",
    ],
    "ADDRESS_CITY": [
        # build_value_map supplies city; callers usually pass "Springfield" etc.
    ],
    "WORK_AUTH": ["Yes", "I am authorized", "Authorized"],
    "US_RESIDENCE": [
        "Yes",
        "Yes, I currently live in the United States",
        "I live in the United States",
        "United States",
    ],
    "LATIN_AMERICA": ["Yes", "Yes, I am based in Latin America"],
    "TALENT_HUB": ["No", "No, I do not", "I do not"],
    "SPONSORSHIP": [
        # Prefer explicit "will not / do not require" before bare "No"
        # (bare "No" soft-matched trap "No, I will require visa sponsorship").
        "No, I will not require sponsorship",
        "No, I do not require sponsorship",
        "I will not require sponsorship",
        "I do not require sponsorship",
        "Will not require sponsorship",
        "Do not require sponsorship",
        "No sponsorship required",
        "No, I will not require",
        "No",
        "I do not",
        "Do not require",
        "I will not require",
        # Lever citizenship multi-choice (egen): dummy does not need sponsorship
        "US Citizen",
        "U.S. Citizen",
        "Citizen",
        "Permanent Resident",
        "Permanent resident",
    ],
    "TERMS_CONSENT": [
        "I agree",
        "I consent",
        "Yes",
        "Agree",
        "Consent",
        "Accept",
    ],
    "ACCOMMODATIONS": [
        "No",
        "No, I do not",
        "I do not require",
        "I do not need",
        "Do not require",
        "No accommodations needed",
    ],
    "ACCOMMODATIONS_DETAILS": [
        "N/A",
        "NA",
        "n/a",
        "Not applicable",
        "None",
    ],
    "EMPLOYEE_REFERRAL": ["No", "No, I was not", "I was not referred", "N/A"],
    "REFERRAL_EMAIL": [
        "N/A",
        "NA",
        "n/a",
        "Not applicable",
        "None",
        "No",
    ],
    "HOW_HEARD": [
        "LinkedIn",
        "Indeed",
        "BuiltIn",
        "Built In",
        "Glassdoor",
        "ZipRecruiter",
        "Monster",
        "CareerBuilder",
        "Company Website",
        "Job Board",
        "Internet job board",
        "Other",
    ],
    "WORKED_HERE_BEFORE": ["No", "No, I have not", "I have not"],
    "COMMUTE": ["Yes", "Yes, I can", "I am able", "Able to commute"],
    "MARKETING_CONSENT": [
        "No",
        "No, I do not",
        "I do not consent",
        "Decline",
        "Opt out",
        "I do not",
        "Do not consent",
    ],
    "BACKGROUND_CHECK": [
        "Yes",
        "Yes, I am willing",
        "I am willing",
        "I agree",
        "Agree",
        "Consent",
    ],
    "SALARY_EXPECTED": [
        # Common Greenhouse band options (Tax Relief / similar boards)
        "$80,000 - $100,000",
        "$100,000 - $120,000",
        "$65,000 - $80,000",
        "120,000+",
        "$120,000+",
        "$45,000 - $65,000",
        "Open / negotiable within the posted range",
        "Negotiable",
        "Open",
        "Flexible",
        "Discuss",
        "Competitive",
        "Other",
    ],
    "DEGREE": [
        # Doctorate / PhD first when value is doctoral (aliases_for filters polarity)
        "Doctorate",
        "Doctoral Degree",
        "Doctoral",
        "Ph.D.",
        "Ph.D",
        "PhD",
        "Doctor of Philosophy",
        # Master's — never list Bachelor ahead (tie → index 0 Bachelor won live)
        "Master's Degree",
        "Masters Degree",
        "Master's",
        "Masters",
        "Master of Science",
        "Master",
        "M.S.",
        "MS",
        "Graduate",
        # Bachelor only as fallback when value itself is Bachelor's
        "Bachelor's Degree",
        "Bachelor's",
        "Bachelor",
        "B.S.",
        "BS",
    ],
    "DISCIPLINE": [
        "Computer Science",
        "Computer science",
        "CS",
        "Computing",
        "Computer Science and Engineering",
        "Software Engineering",
        "Information Technology",
        "Other",
    ],
    "MAJOR": [
        "Computer Science",
        "Computer science",
        "CS",
        "Computing",
        "Computer Science and Engineering",
        "Software Engineering",
        "Other",
    ],
    "FIELD_OF_STUDY": [
        "Computer Science",
        "Computer science",
        "CS",
        "Computing",
        "Other",
    ],
    "SCHOOL": [
        # Dummy primary: University of Alabama, Tuscaloosa (curated GH lists)
        "University of Alabama, Tuscaloosa",
        "University of Alabama",
        "The University of Alabama",
        "Alabama",
        "University of Alabama at Tuscaloosa",
        "UA",
        # Dummy secondary (resume B.S.)
        "GITAM, Visakhapatnam, India",
        "GITAM University",
        "GITAM",
        "Other",
    ],
    "LOCATION": [
        # Yes/No "based in any of these states?" (Extend GH) — Yes first
        "Yes",
        "Yes, I am",
        "Illinois",
        "Springfield, IL, USA",
        "Springfield, IL",
        "Springfield",
        "United States",
        "USA",
        "Remote",
        "No",
    ],
    "RELOCATION": [
        "Yes",
        "Yes, willing to relocate",
        "Willing to relocate",
        "I am willing to relocate",
        "Yes - I am willing to relocate",
        "Open to relocation",
    ],
    "NOTICE_PERIOD": [
        "Immediately available",
        "Immediately",
        "Available immediately",
        "ASAP",
        "2 weeks",
        "Two weeks",
    ],
    "AGE_RANGE": [
        "Prefer not to disclose",
        "Prefer not to say",
        "Prefer not to answer",
        "Decline to self identify",
        "Decline to answer",
        "I don't wish to answer",
        "Choose not to disclose",
    ],
    "LGBTQIA": [
        "Prefer not to disclose",
        "Prefer not to say",
        "Prefer not to answer",
        "Decline to self identify",
        "Decline to answer",
        "I don't wish to answer",
        "I do not wish to answer",
        "Choose not to disclose",
        "Decline",
    ],
    "PRONOUNS": [
        "Prefer not to say",
        "Prefer not to disclose",
        "Prefer not to answer",
        "Decline to answer",
        "I prefer not to say",
        "I prefer not to disclose",
        "Choose not to disclose",
        "Decline",
    ],
    # Dummy EEO: preferred answers first; Decline kept as fallback when missing.
    "GENDER": [
        "Male",
        "Man",
        "Decline to Self Identify",
        "Decline to self identify",
        "Decline to self-identify",  # Lever EEO hyphenated wording
        "Decline To Self Identify",
        "Decline to answer",
        "Prefer not to say",
        "Prefer not to answer",
        "I don't wish to answer",
        "I do not wish to answer",
        "Decline",
    ],
    "HISPANIC": [
        # Dummy policy: No / Not Hispanic — Decline is fallback only
        "No",
        "Not Hispanic or Latino",
        "No, I am not Hispanic or Latino",
        "I am not Hispanic or Latino",
        "Not Hispanic/Latino",
        "Decline to Self Identify",
        "Decline to self identify",
        "Decline to self-identify",
        "Decline to answer",
        "Prefer not to say",
        "Prefer not to answer",
        "I don't wish to answer",
        "Decline",
    ],
    "RACE": [
        "Decline to Self Identify",
        "Decline to self identify",
        "Decline to self-identify",
        "Decline to answer",
        "Prefer not to say",
        "Prefer not to answer",
        "I don't wish to answer",
        "Decline",
    ],
    "VETERAN": [
        "I am not a protected veteran",
        "I am not a veteran",
        "Not a protected veteran",
        "No, I am not a veteran",
        "No, I am not a protected veteran",
        "Not a Veteran",
        "No",
        "I decline to self-identify for protected veteran status",
        "I don’t wish to answer",
        "I don't wish to answer",
        "I do not wish to answer",
        "Decline to Self Identify",
        "Decline to self identify",
        "Decline to self-identify",
        "Decline to answer",
        "Prefer not to say",
        "Prefer not to answer",
        "Decline",
    ],
    # OFCCP / GH Disability Status — prefer "No disability"; Decline fallback
    "DISABILITY": [
        "No, I do not have a disability",
        "No, I don't have a disability",
        "I do not have a disability",
        "I don't have a disability",
        "I do not have a disability and have not had one in the past",
        "Not disabled",
        "No disability",
        "No",
        "I do not want to answer",
        "I don't want to answer",
        "I don’t want to answer",
        "Decline to Self Identify",
        "Decline to self identify",
        "Decline to self-identify",
        "Decline to answer",
        "Prefer not to say",
        "Prefer not to answer",
        "Decline",
    ],
    "CLEARANCE": [
        "No",
        "No, I do not",
        "I do not have a clearance",
        "I do not currently hold a security clearance",
        "None",
        "No Clearance",
    ],
    "CLEARANCE_TYPE": [
        "None",
        "No Clearance",
        "No clearance",
        "None Required",
        "Not Applicable",
        "N/A",
        "No Security Clearance",
        "I do not have a security clearance",
        "No",
    ],
    "US_CITIZEN": [
        "Yes",
        "Yes, I am a U.S. citizen",
        "Yes, I am a US citizen",
        "I am a U.S. citizen",
        "U.S. Citizen",
        "US Citizen",
        "Citizen",
    ],
    "VISA_STATUS": [
        "No visa required",
        "Not required",
        "None",
        "N/A",
        "US Citizen",
        "U.S. Citizen",
        "Citizen",
        "No Visa",
        "Does not require a visa",
    ],
}

# EEO types: typing the canonical "Decline…" string often filters the menu to
# zero (options use "I do not want to answer" / "I don't wish…"). Prefer a
# short fragment that appears in those options, or skip typing.
_EEO_TYPES = frozenset({"GENDER", "HISPANIC", "RACE", "VETERAN", "DISABILITY", "LGBTQIA", "PRONOUNS"})

# Yes/No policy selects — always word-by-word via verified_select (Yes / No),
# never custom filter fragments like "authorized" that fail to commit.
_YESNO_SELECT_TYPES = frozenset(
    {
        "WORK_AUTH",
        "US_RESIDENCE",
        "US_CITIZEN",
        "CLEARANCE",
        "TALENT_HUB",
        "WORKED_HERE_BEFORE",
        "COMMUTE",
        "RELOCATION",
        "BACKGROUND_CHECK",
        "AGE_18",
        "MARKETING_CONSENT",
        "NOTICE_PERIOD",
        "FELONY",
        "EMPLOYEE_REFERRAL",
        "ACCOMMODATIONS",
    }
)


def is_post_resume_reassert_via(via: str) -> bool:
    """True for GH contact refill after resume parse — not a resume attachment row."""
    v = (via or "").lower()
    return "post_resume_reassert" in v or v in ("greenhouse_reassert",)


def aliases_for(field_type: str, value: str) -> list[str]:
    """Ordered candidate option strings: exact value first, then type aliases.

    For DEGREE: when value indicates Master's, drop Bachelor aliases so they
    cannot soft-match-tie and win by option-list index (live grvty bug).
    For HOW_HEARD: use shared priority list (LinkedIn → Indeed → …).
    """
    if field_type == "HOW_HEARD":
        try:
            from field_map import HOW_HEARD
            from fill_verify import how_heard_candidates

            return how_heard_candidates({HOW_HEARD: value} if value else None)
        except Exception:
            pass
    out: list[str] = []
    raw_aliases = list(OPTION_ALIASES.get(field_type or "", []))
    vlow = (value or "").lower()
    if field_type == "DEGREE":
        wants_doctorate = bool(
            re.search(r"\bph\.?d\.?\b|\bdoctorate\b|\bdoctoral\b|\bdoctor\s+of\b", vlow)
        )
        wants_associate = bool(
            re.search(
                r"\bassociates?\b|\ba\.?\s*a\.?\b|\ba\.?\s*s\.?\b|\bassoc\.?\b",
                vlow,
            )
        ) and not wants_doctorate
        wants_master = (
            bool(re.search(r"\bm\.?s\.?\b|\bmasters?\b|\bgraduate\b", vlow))
            and not wants_doctorate
            and not wants_associate
            and not bool(re.search(r"\bb\.?s\.?\b|\bbachelors?\b", vlow))
        )
        wants_bachelor = (
            bool(re.search(r"\bb\.?s\.?\b|\bbachelors?\b", vlow))
            and not wants_doctorate
            and not wants_associate
            and not bool(re.search(r"\bm\.?s\.?\b|\bmasters?\b", vlow))
        )
        _drop_assoc = r"associate|\ba\.?\s*a\.?\b|\ba\.?\s*s\.?\b|\bassoc\.?\b"
        _drop_doc = r"ph\.?d|doctorate|doctoral|doctor\s+of"
        if wants_doctorate:
            # Keep doctorate aliases + value; drop Master/Bachelor/Associate
            raw_aliases = [
                a
                for a in raw_aliases
                if re.search(r"ph\.?d|doctorate|doctoral|doctor\s+of", a, re.I)
                or not re.search(
                    rf"master|bachelor|\bm\.?s\.?\b|\bb\.?s\.?\b|graduate|{_drop_assoc}",
                    a,
                    re.I,
                )
            ]
        elif wants_master:
            raw_aliases = [
                a
                for a in raw_aliases
                if not re.search(
                    rf"bachelor|\bb\.?s\.?\b|{_drop_doc}|{_drop_assoc}",
                    a,
                    re.I,
                )
            ]
        elif wants_bachelor:
            raw_aliases = [
                a
                for a in raw_aliases
                if not re.search(
                    rf"master|\bm\.?s\.?\b|graduate|{_drop_doc}|{_drop_assoc}",
                    a,
                    re.I,
                )
            ]
        elif wants_associate:
            raw_aliases = [
                a
                for a in raw_aliases
                if not re.search(
                    rf"master|bachelor|\bm\.?s\.?\b|\bb\.?s\.?\b|graduate|{_drop_doc}",
                    a,
                    re.I,
                )
            ]
    for cand in [value, *raw_aliases]:
        if cand and cand not in out:
            out.append(cand)
    return out


def is_decline_like_alias(text: str) -> bool:
    """True for Decline / prefer-not / wish-not-to-answer option strings."""
    al = (text or "").lower()
    if not al:
        return False
    return any(
        x in al
        for x in (
            "decline",
            "prefer not",
            "wish to answer",
            "want to answer",
            "choose not to",
            "rather not",
            "do not wish",
            "don't wish",
            "do not want to answer",
            "don't want to answer",
        )
    )


def preferred_aliases_for(field_type: str, value: str) -> list[str]:
    """Aliases for already-correct checks: exclude Decline fallbacks when a
    concrete preferred answer exists (else Decline wrongly keeps as 'correct')."""
    all_cands = aliases_for(field_type, value)
    preferred = [c for c in all_cands if not is_decline_like_alias(c)]
    return preferred if preferred else all_cands


_DIAL_CODE_OPT_RE = re.compile(
    r"\(\s*\+\d{1,4}\s*\)|\+\s*\d{1,4}\b|^\s*\+\d{1,4}\s*$"
)


def looks_like_dial_code_option(text: str) -> bool:
    """True for phone-country rows like 'United States +1' / 'USA (+1)' / '+1'.

    Greenhouse Dragos Country* options are dial-coded (``United States +1``) and
    commit display ``+1`` + ``iti__us`` flag — not a bare country name.
    """
    t = (text or "").strip()
    if not t:
        return False
    if _DIAL_CODE_OPT_RE.search(t):
        return True
    low = t.lower()
    if "+" in t and any(
        x in low for x in ("united states", "united kingdom", "canada", "australia")
    ):
        return True
    return False


def country_name_from_dial_option(text: str) -> str:
    """Strip trailing/+parenthetical dial codes: 'United States +1' → 'United States'."""
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"\(\s*\+\d{1,4}\s*\)", "", t).strip()
    t = re.sub(r"\s*\+\d{1,4}\s*$", "", t).strip()
    if re.match(r"^\+\d{1,4}$", t):
        return ""
    return t


def is_dial_only_display(text: str) -> bool:
    """True for committed GH country display that is only '+1' / '+44'."""
    return bool(re.match(r"^\s*\+\d{1,4}\s*$", (text or "").strip()))


# Common country → intl-tel-input ISO2 flag class suffix (ATS-011).
_COUNTRY_ITI_ISO: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"united\s*states|\busa\b|\bus\b", re.I), "us"),
    (re.compile(r"united\s*kingdom|\buk\b|great\s*britain|\bengland\b", re.I), "gb"),
    (re.compile(r"\bcanada\b", re.I), "ca"),
    (re.compile(r"\baustralia\b", re.I), "au"),
    (re.compile(r"\bindia\b", re.I), "in"),
    (re.compile(r"\bgermany\b", re.I), "de"),
    (re.compile(r"\bfrance\b", re.I), "fr"),
    (re.compile(r"\bireland\b", re.I), "ie"),
    (re.compile(r"\bnetherlands\b|\bholland\b", re.I), "nl"),
    (re.compile(r"\bsingapore\b", re.I), "sg"),
    (re.compile(r"\bjapan\b", re.I), "jp"),
    (re.compile(r"\bmexico\b", re.I), "mx"),
    (re.compile(r"\bbrazil\b", re.I), "br"),
    (re.compile(r"\bchina\b", re.I), "cn"),
    (re.compile(r"\bspain\b", re.I), "es"),
    (re.compile(r"\bitaly\b", re.I), "it"),
]


def expected_iti_flag_isos(cands: list[str], picked: str = "") -> set[str]:
    """ISO2 codes expected for ADDRESS_COUNTRY candidates / picked dial option."""
    blobs = [*(cands or []), picked or ""]
    out: set[str] = set()
    for blob in blobs:
        name = country_name_from_dial_option(blob) or blob
        for pat, iso in _COUNTRY_ITI_ISO:
            if pat.search(name or ""):
                out.add(iso)
    return out


def iti_flag_matches_country(flag_class: str, cands: list[str], picked: str = "") -> bool:
    """ATS-011: True when .iti__flag class ISO matches intended country (not any flag)."""
    cls = (flag_class or "").lower()
    m = re.search(r"\biti__([a-z]{2})\b", cls)
    if not m:
        return False
    flag_iso = m.group(1)
    expected = expected_iti_flag_isos(cands, picked)
    if not expected:
        # Unknown country mapping — do not soft-accept arbitrary flags
        return False
    return flag_iso in expected


def _score_option(opt_text: str, alias: str) -> int:
    o_raw, a = (opt_text or "").strip(), (alias or "").lower().strip()
    if not o_raw or not a:
        return 0
    # Confusable US states — keep LOCAL (do NOT import verified_select;
    # circular import with verified_select._default_score_option → recursion).
    # ATS2-014: full pair table, not only IL↔ID.
    o_l = o_raw.lower().strip()
    _CONFUSABLE = (
        (frozenset({"illinois", "il"}), frozenset({"idaho", "id"})),
        (frozenset({"mississippi", "ms"}), frozenset({"missouri", "mo"})),
        (frozenset({"arkansas", "ar"}), frozenset({"arizona", "az"})),
        (frozenset({"alabama", "al"}), frozenset({"alaska", "ak"})),
        (frozenset({"north carolina", "nc"}), frozenset({"north dakota", "nd"})),
        (frozenset({"south carolina", "sc"}), frozenset({"south dakota", "sd"})),
        (frozenset({"virginia", "va"}), frozenset({"vermont", "vt"})),
        (frozenset({"michigan", "mi"}), frozenset({"minnesota", "mn"})),
        (frozenset({"maine", "me"}), frozenset({"maryland", "md"})),
        (frozenset({"nebraska", "ne"}), frozenset({"nevada", "nv"})),
        (frozenset({"colorado", "co"}), frozenset({"connecticut", "ct"})),
        (frozenset({"massachusetts", "ma"}), frozenset({"maine", "me"})),
        (frozenset({"washington", "wa"}), frozenset({"wisconsin", "wi"})),
        (frozenset({"kansas", "ks"}), frozenset({"kentucky", "ky"})),
    )
    a_tok = {a, a.split(",")[0].strip()}
    o_tok = {o_l, o_l.split(",")[0].strip()}
    for left, right in _CONFUSABLE:
        if (a_tok & left and o_tok & right) or (a_tok & right and o_tok & left):
            return 0
        # Full-name cross match when abbrev tokens are absent
        a_left = any(t in a for t in left if len(t) > 2)
        a_right = any(t in a for t in right if len(t) > 2)
        o_left = any(t in o_l for t in left if len(t) > 2)
        o_right = any(t in o_l for t in right if len(t) > 2)
        if (a_left and o_right) or (a_right and o_left):
            return 0
    # GH Country* options look like "United States +1" — score the country name.
    if looks_like_dial_code_option(o_raw) and not looks_like_dial_code_option(alias):
        country = country_name_from_dial_option(o_raw)
        if not country:
            return 0
        o_raw = country
    elif looks_like_dial_code_option(alias) and not looks_like_dial_code_option(o_raw):
        # Alias is dial-coded, option is bare country — compare names
        alias_country = country_name_from_dial_option(alias)
        if alias_country:
            a = alias_country.lower().strip()
    o = o_raw.lower().strip()
    if o == a:
        return 100

    # Decline option must never soft-match a concrete preferred alias (and vice
    # versa). Live grvty: "I do not want to answer" exact-matched Decline alias
    # at 100 and beat "I do not have a disability" soft-match at 80/90.
    try:
        opt_decline = is_decline_like_alias(opt_text)
        alias_decline = is_decline_like_alias(alias)
    except Exception:
        opt_decline = alias_decline = False
    if opt_decline != alias_decline:
        # Only decline↔decline (or concrete↔concrete) may score; cross = 0
        # Exception: exact equality already returned 100 above.
        return 0

    # Salary bands — normalize digit groups across $ / spacing variants
    if _is_salary_like(opt_text) or _is_salary_like(alias):
        sal = _fuzzy_salary_score(opt_text, alias)
        if sal:
            return sal

    # School / institution — compare name head before city comma
    if _is_school_like(opt_text) or _is_school_like(alias):
        sch = _fuzzy_school_score(opt_text, alias)
        if sch:
            return sch

    # Degree polarity: Doctorate≠Master≠Bachelor≠Associate
    # Live Elanco Workday: "Master's Degree" soft-matched "Associate … Degree"
    # via shared token "degree" (score 65) when Master's rows were not yet
    # virtualized — never allow Associate/A.A. to score against Master's.
    def _degree_level(s: str) -> str | None:
        sl = (s or "").lower()
        if re.search(r"\bph\.?d\b|\bdoctorate\b|\bdoctoral\b|\bdoctor\s+of\b", sl):
            return "doc"
        if re.search(
            r"\bassociates?\b|\ba\.a\.?\b|\ba\.s\.?\b|\bassoc\.?\b|"
            r"associate\s+of\s+(arts|science)",
            sl,
        ):
            return "assoc"
        if re.search(r"\bmasters?\b|\bm\.?s\.?\b|\bm\.?a\.?\b|\bgraduate\b", sl):
            return "master"
        if re.search(r"\bbachelors?\b|\bb\.?s\.?\b|\bb\.?a\.?\b", sl):
            return "bach"
        return None

    a_lvl, o_lvl = _degree_level(a), _degree_level(o)
    if a_lvl and o_lvl and a_lvl != o_lvl:
        return 0
    # Bare "degree" must not soft-match across levels when one side is leveled
    if (a_lvl or o_lvl) and (
        re.fullmatch(r"degrees?", a) or re.fullmatch(r"degrees?", o)
    ):
        return 0

    # Polarity: Never score a Yes-* option against a No-* alias (and vice versa).
    # Prevents "No sponsorship required" tokens matching "Yes, I will require…".
    a_no = bool(
        re.match(r"^(no\b|i do not|i don'?t|i will not|do not |don'?t )", a)
        or a.startswith("no ")
        or a in ("no",)
    )
    a_yes = bool(re.match(r"^(yes\b|i am |i will require)", a) or a == "yes")
    o_no = bool(re.match(r"^no\b", o))
    o_yes = bool(re.match(r"^yes\b", o))
    if a_no and o_yes:
        return 0
    if a_yes and o_no:
        return 0

    # Sponsorship traps: "No, I will require visa sponsorship" starts with No
    # but means the opposite of SPONSORSHIP=No. Reject before substring scoring.
    # Found live: Tax Relief GH Select… left blank OR wrong option after soft match.
    requires_sponsor = bool(
        re.search(
            r"\b(will|do|would)\s+require\b|\brequire[sd]?\s+(visa\s+)?sponsor",
            o,
        )
    ) and not bool(
        re.search(
            r"will\s+not\s+require|do\s+not\s+require|don'?t\s+require|"
            r"not\s+require\s+(visa\s+)?sponsor|no\s+sponsorship\s+required",
            o,
        )
    )
    alias_means_no_sponsor = a_no or any(
        x in a
        for x in (
            "not require",
            "do not require",
            "will not require",
            "no sponsorship",
            "citizen",
            "permanent resident",
        )
    )
    if requires_sponsor and alias_means_no_sponsor:
        return 0
    if "require sponsorship" in o and alias_means_no_sponsor and not re.search(
        r"not\s+require|no\s+sponsorship", o
    ):
        return 0

    # Short Yes/No aliases: dedicated logic only — never `a in o` (would score
    # 80 on "No, I will require visa sponsorship" via leading "No").
    if a in ("yes", "no"):
        if o == a or o.startswith(a + ",") or o.startswith(a + " "):
            if a == "no" and requires_sponsor:
                return 0
            if a == "no" and o.strip() in (
                "require sponsorship",
                "yes, i require sponsorship",
            ):
                return 0
            # Prefer clear "will not / do not require" over bare "No, …"
            if a == "no" and re.search(
                r"will\s+not\s+require|do\s+not\s+require|don'?t\s+require|"
                r"no\s+sponsorship\s+required",
                o,
            ):
                return 96
            return 90
        return 0

    # FILL2-001: gender polarity — "male"⊂"female", "man"⊂"woman" must never soft-match.
    def _gender_side(s: str) -> str | None:
        sl = (s or "").lower()
        if re.search(r"\bfemale\b|\bwom[ae]n\b|\bgirl\b", sl):
            return "F"
        if re.search(r"\bmale\b|\bmen\b|\bman\b|\bboy\b", sl):
            return "M"
        if re.search(r"\bnon[\s_-]*binary\b|\benby\b", sl):
            return "X"
        return None

    ga, go = _gender_side(a), _gender_side(o)
    if ga and go and ga != go:
        return 0

    def _bounded_sub(needle: str, hay: str) -> bool:
        """True when needle appears as its own token span (not inside female/woman)."""
        if not needle or not hay:
            return False
        if needle in hay and (
            len(needle) <= 3
            or re.search(
                rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
                hay,
            )
        ):
            if len(needle) <= 3:
                return hay == needle or hay.startswith(needle)
            return True
        return False

    if _bounded_sub(a, o):
        # Short aliases (state abbrevs): prefix only — "il"→Illinois yes, "il"→Idaho no.
        if len(a) <= 3:
            if o == a or o.startswith(a):
                return 80
            return 0
        return 80
    if _bounded_sub(o, a):
        if len(o) <= 3:
            if a == o or a.startswith(o):
                return 70
            return 0
        return 70
    words = [w for w in re.split(r"\W+", a) if len(w) > 2]
    # For No-sponsor aliases, ignore generic tokens that appear on Yes options
    if alias_means_no_sponsor:
        words = [
            w
            for w in words
            if w
            not in (
                "sponsorship",
                "sponsor",
                "required",
                "require",
                "visa",
                "employment",
            )
        ]
    # Degree labels: drop generic "degree"/"science"/"arts" so "Master's Degree"
    # cannot soft-match "Associate of Arts Degree" via shared tail tokens.
    if a_lvl or o_lvl:
        words = [
            w
            for w in words
            if w
            not in (
                "degree",
                "degrees",
                "science",
                "arts",
                "of",
                "the",
                "and",
            )
        ]
    if words and all(w in o for w in words):
        return 60
    # School / institution: significant token (Alabama, Stanford) beats virtualized lists
    stop = {
        "university",
        "college",
        "school",
        "institute",
        "the",
        "and",
        "of",
        "at",
        "in",
        # Never let bare "degree" promote Associate when Master's is intended
        "degree",
        "degrees",
    }
    sig = [w for w in words if len(w) > 3 and w not in stop]
    if sig and any(w in o for w in sig):
        return 65
    if "decline" in a and "decline" in o:
        return 55
    if "wish" in a and "wish" in o and "answer" in o:
        return 55
    if "prefer not" in a and "prefer not" in o:
        return 55
    # Decline-profile ↔ OFCCP disability wording (found live on Greenhouse)
    decline_like = any(
        x in a
        for x in (
            "decline",
            "prefer not",
            "wish to answer",
            "want to answer",
        )
    )
    refuse_opt = any(
        x in o
        for x in (
            "do not want to answer",
            "don't want to answer",
            "don’t want to answer",
            "do not wish to answer",
            "don't wish to answer",
            "don’t wish to answer",
            "prefer not",
            "decline to answer",
            "decline to self",
            "decline",
        )
    )
    if decline_like and refuse_opt:
        return 58
    # Citizenship / OPT options for SPONSORSHIP=No (need no visa help)
    if a in ("us citizen", "u.s. citizen", "citizen", "permanent resident"):
        if o == a or a in o or o in a:
            return 95
    return 0


def _type_fragment_for(field_type: str, cands: list[str]) -> str:
    """What to type into the combobox filter — avoid fragments that zero the list.

    EEO: NEVER type Decline fragments ("want to answer" / "wish to answer") when a
    preferred concrete answer exists — that filtered the live menu to only Decline
    (grvty Disability/Veteran). Prefer a short token from the preferred value.
    """
    if field_type in _EEO_TYPES:
        preferred = [c for c in cands if c and not is_decline_like_alias(c)]
        pool = preferred or list(cands)
        for a in pool:
            al = a.lower()
            # Concrete disability / veteran / hispanic / gender tokens
            if "not have a disability" in al or "don't have a disability" in al:
                return "do not have a disability"
            if "no disability" in al or al == "not disabled":
                return "no disability"
            if "not a protected veteran" in al or "not a veteran" in al:
                return "not a protected veteran"
            if "not hispanic" in al or "not latino" in al:
                return "not hispanic"
            if al in ("male", "man", "female", "woman", "non-binary", "nonbinary"):
                return a.split()[0][:12]
            if al in ("no", "yes") and field_type in ("HISPANIC", "VETERAN", "DISABILITY"):
                return a[:3]
        # Only when preferred list is empty: Decline-filter fragments
        if not preferred:
            for a in cands:
                al = a.lower()
                if "want to answer" in al:
                    return "want to answer"
                if "wish to answer" in al:
                    return "wish to answer"
                if "prefer not" in al:
                    return "prefer not"
        return ""  # show full list; scoring picks preferred then Decline fallback
    if field_type == "SPONSORSHIP":
        # Prefer "will not require" / "do not require" over bare "No" so menus
        # that contain trap "No, I will require…" filter to the safe option.
        for a in cands:
            al = a.lower()
            if "will not require" in al or "do not require" in al:
                return "will not require"
            if "not require" in al:
                return "not require"
            if "no sponsorship" in al:
                return "no sponsorship"
        # Empty filter → full list; scoring rejects require-sponsorship traps
        return ""
    if field_type == "WORK_AUTH":
        for a in cands:
            al = a.lower()
            if "authorized" in al:
                return "authorized"
            if al == "yes":
                return "Yes"
        return ""
    if field_type == "HOW_HEARD":
        # Walk priority order for type-filter tokens (never full "Internet job board").
        try:
            from fill_verify import how_heard_leaf_candidates

            for a in how_heard_leaf_candidates():
                al = (a or "").lower()
                if al in (
                    "linkedin",
                    "indeed",
                    "builtin",
                    "built in",
                    "glassdoor",
                    "ziprecruiter",
                    "monster",
                    "careerbuilder",
                ):
                    return a[:16]
        except Exception:
            pass
        for a in cands:
            al = (a or "").lower()
            if al in ("linkedin", "indeed", "glassdoor", "builtin", "built in"):
                return a[:16]
        for a in cands:
            al = (a or "").lower()
            if "job board" in al or al == "job boards":
                return "Job Board"
            if al == "online":
                return "Online"
            if al == "other":
                return "Other"
        return ""
    if field_type == "SCHOOL":
        # Long "University of X, City" fragments often zero GH school menus
        # (virtualized). Prefer institution token (Alabama), NOT city (Tuscaloosa)
        # — typing Tuscaloosa zeros Tax Relief / similar curated lists (UNFILLABLE).
        frags = _school_type_fragments(cands)
        return frags[0] if frags else ""
    if field_type == "SALARY_EXPECTED":
        # Short numeric token only. Truncating "$80,000 - $100,000" to 12 chars
        # produced "$80,000-$10" which zeroed band menus (UNFILLABLE salary).
        for a in cands:
            if "$" in a or re.search(r"\d{2,3},\d{3}|\d{5,}", a):
                m = re.search(r"\d{1,3}(?:,\d{3})+|\d{5,}", a)
                if m:
                    return m.group(0)[:12]
                m2 = re.search(r"(\d{2,3})", a)
                if m2:
                    return m2.group(1)
        return ""
    if field_type == "DEGREE":
        # Type "Master" / "Bachelor" level token — not "M.S., Example Studies"
        for a in cands:
            al = a.lower()
            if re.search(r"master", al):
                return "Master"
            if re.search(r"bachelor", al):
                return "Bachelor"
        return ""
    if field_type in ("DISCIPLINE", "MAJOR", "FIELD_OF_STUDY"):
        for a in cands:
            if a and "computer" in a.lower():
                return "Computer Science"
        return (cands[0][:28] if cands else "") or ""
    frag = cands[0] if cands else ""
    return frag[:28] if len(frag) > 28 else frag


def _school_type_fragments(cands: list[str]) -> list[str]:
    """Ordered typeahead fragments for SCHOOL (institution before city)."""
    stop = {
        "university",
        "college",
        "school",
        "institute",
        "the",
        "and",
        "of",
        "at",
        "in",
        "visakhapatnam",
        "india",
        "tuscaloosa",  # city suffix — do not type first
    }
    out: list[str] = []
    for raw in cands:
        if not raw:
            continue
        # Prefer left of comma ("University of Alabama") then tokens
        left = raw.split(",")[0].strip()
        toks = [
            w
            for w in re.split(r"\W+", left)
            if len(w) > 3 and w.lower() not in stop
        ]
        for t in toks:
            frag = t[:24]
            if frag and frag not in out:
                out.append(frag)
        if left and left not in out and len(left) <= 40:
            out.append(left[:28])
    return out


def _label_needle(label: str) -> str:
    """Short stable substring for locating the Greenhouse select label."""
    t = (label or "").replace("*", "").strip()
    # Truncate long custom questions; first clause is enough.
    t = re.split(r"[?\n]", t)[0].strip()
    # Long multi-state lists: keep the lead-in, not the state dump.
    if re.search(r"based\s+in\s+any\s+of\s+these\s+states", t, re.I):
        return "based in any of these states"
    # Multi WORK_AUTH: "without sponsorship" ≠ "legally authorized" — order matters.
    # Needle MUST be a literal substring of the live label (Tax Relief has
    # "without the need…"; older copy may omit "the").
    # Prefer leftmost short needle for without-need (avoid long authorized… span)
    m_wo = re.search(
        r"without\s+(?:the\s+)?need\s+for\s+(?:visa\s+)?sponsorship",
        t,
        re.I,
    )
    if m_wo:
        return m_wo.group(0)
    m_wo2 = re.search(
        r"authorized\s+.{0,40}without\s+.{0,20}sponsorship",
        t,
        re.I,
    )
    if m_wo2:
        return m_wo2.group(0)
    if re.search(
        r"will\s+you\s+.*require\s+(immigration\s+)?sponsorship|"
        r"require\s+sponsorship\s+for\s+employment\s+visa|"
        r"immigration\s+sponsorship|"
        r"require\s+sponsorship\s+to\s+work",
        t,
        re.I,
    ):
        # Needle must appear in the live label text. Dragos uses
        # "require sponsorship to work…" (no "immigration") — searching
        # "require immigration sponsorship" → label not found.
        if re.search(r"immigration\s+sponsorship", t, re.I):
            return "require immigration sponsorship"
        return "require sponsorship"
    # SMS / marketing consent — needle must appear in the label.
    # Tax Relief: "Do you consent to receiving SMS…" has SMS, not "marketing".
    if re.search(r"\bsms\b", t, re.I):
        return "SMS"
    if re.search(r"text[\s_-]*messages?", t, re.I):
        m_txt = re.search(r"text[\s_-]*messages?", t, re.I)
        return m_txt.group(0) if m_txt else "text message"
    if re.search(
        r"marketing|promotional|newsletter|"
        r"talent[\s_-]*community|opt[\s_-]*in",
        t,
        re.I,
    ):
        m_mkt = re.search(
            r"marketing|promotional|newsletter|talent[\s_-]*community|opt[\s_-]*in",
            t,
            re.I,
        )
        return m_mkt.group(0) if m_mkt else "marketing"
    if re.search(r"authorized\s+to\s+work", t, re.I):
        return "authorized to work"
    return t[:60] if t else ""


def _label_key(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip().lower())[:50]


async def _pick_best_label_match(page, needle: str, full_label: str):
    """When several GH labels share a needle, pick the closest to full_label."""
    full = (full_label or "").replace("*", "").strip().lower()
    best = None
    best_score = -1
    for sel in ("label.select__label", "label"):
        loc = page.locator(sel).filter(
            has_text=re.compile(re.escape(needle[:40]), re.I)
        )
        try:
            n = await loc.count()
        except Exception:
            n = 0
        for i in range(min(n, 12)):
            cand = loc.nth(i)
            try:
                txt = (await cand.inner_text()).replace("\n", " ").strip().lower()
            except Exception:
                continue
            if not txt:
                continue
            score = len(set(full.split()) & set(txt.split()))
            if full[:60] in txt or txt[:60] in full:
                score += 50
            if needle.lower() in txt:
                score += 10
            if score > best_score:
                best_score = score
                best = cand
        if best is not None:
            return best
    soft = re.escape(needle[:24])
    return page.locator("label").filter(has_text=re.compile(soft, re.I)).first


def _shown_matches_cands(shown: str, cands: list[str], *, field_type: str = "") -> bool:
    """True when react-select display already matches *preferred* aliases.

    Decline fallbacks are excluded when preferred answers exist — otherwise a
    wrongly-filled Decline keeps as already_correct (live grvty Disability/Veteran).
    """
    check = list(cands)
    if field_type in _EEO_TYPES:
        preferred = [c for c in cands if c and not is_decline_like_alias(c)]
        if preferred:
            check = preferred
    return select_readback_ok(shown, check, score_fn=_score_option, min_score=50)


async def _resolve_gh_select_container(page, label: str, *, timeout_ms: int = 4000):
    """Locate label + `.select__container` + `.select__control` for a GH select."""
    needle = _label_needle(label)
    if not needle:
        return None, None, None, {"ok": False, "error": "empty label"}

    lab = await _pick_best_label_match(page, needle, label)
    if await lab.count() == 0:
        return None, None, None, {
            "ok": False,
            "error": f"label not found: {needle!r}",
            "field_absent": True,
        }

    container = page.locator(".select__container").filter(has=lab).first
    if await container.count() == 0:
        container = lab.locator(
            "xpath=ancestor::div[contains(@class,'select')][1]"
        ).first
    if await container.count() == 0:
        container = lab.locator(
            "xpath=ancestor::div[contains(@class,'select__container') or "
            "contains(@class,'select-shell') or contains(@class,'field')][1]"
        ).first

    control = container.locator(".select__control").first
    if await control.count() == 0:
        control = page.locator(".select__container").filter(has=lab).locator(
            ".select__control"
        ).first
    if await control.count() == 0:
        return lab, container, None, {
            "ok": False,
            "error": "no select__control",
            "field_absent": False,
        }
    return lab, container, control, None


async def fill_gh_select(
    page,
    label: str,
    value: str,
    *,
    field_type: str = "",
    aliases: Iterable[str] | None = None,
    timeout_ms: int = 4000,
    report: dict | None = None,
) -> dict:
    """Open → type/filter → wait options → click option → verify readback.

    Returns {ok, picked, shown, error?, aliases_tried, retried?}.
    Never presses Enter.
    If ``.select__single-value`` already matches intended aliases, SKIP —
    do not reopen / clear / press_sequentially (Tax Relief thrash fix).
    On verify fail: retry once with click-option (no type); then mark fail.
    """
    # Collapse LLM essays ("Yes, I am currently based in Illinois…") → Yes/No
    value = normalize_select_answer(label, str(value or ""), field_type=field_type)
    cands = list(aliases) if aliases is not None else aliases_for(field_type, value)
    # Ensure normalized value is first candidate
    if value and value not in cands:
        cands = [value, *cands]
    if not cands:
        return {"ok": False, "error": "no value/aliases"}

    async def _contract(row: dict) -> dict:
        if report is None:
            return row
        try:
            from fill_contract import commit_fill

            captured = dict(row)
            captured.setdefault("type", field_type)
            captured.setdefault("value", value)

            async def _noop() -> dict:
                return captured

            fr = await commit_fill(
                page,
                {"type": field_type, "mode": "gh_select"},
                value,
                _noop,
                via="gh_select",
                report=report,
                before=str(captured.get("shown") or captured.get("readback") or ""),
            )
            return {**captured, **fr.row}
        except Exception:
            return row

    lab, container, control, err = await _resolve_gh_select_container(
        page, label, timeout_ms=timeout_ms
    )
    if err:
        return err
    assert container is not None and control is not None

    # SKIP thrash: already-correct select — never reopen / retype
    try:
        shown0 = await read_gh_select_display(container)
        if _shown_matches_cands(shown0, cands, field_type=field_type):
            try:
                from fill_step_log import note_step

                note_step(
                    report,
                    action="skip_already_correct",
                    label=label[:80],
                    field_type=field_type[:48],
                    before=shown0[:120],
                    after=shown0[:120],
                    via="gh_select",
                    layer="react_select",
                    reason="already_correct_skip",
                )
            except Exception:
                pass
            return await _contract({
                "ok": True,
                "picked": shown0,
                "shown": shown0,
                "skipped_already_correct": True,
                "aliases_tried": cands,
                "verified": True,
                "type": field_type,
                "value": value,
                "readback": shown0,
            })
    except Exception:
        pass

    async def _escape_menu() -> None:
        try:
            from captcha_pause import press_escape_unless_captcha

            await press_escape_unless_captcha(page)
        except Exception:
            pass

    async def _attempt(*, use_type: bool) -> dict:
        """One open → word-by-word type → wait → click → verify cycle."""
        await control.click(timeout=timeout_ms)
        await page.wait_for_timeout(140)

        inp = container.locator(
            "input.select__input, input[role='combobox']"
        ).first
        filter_loc = inp if await inp.count() else control

        primary = cands[0] if cands else value
        if field_type in _YESNO_SELECT_TYPES:
            # verified_select word-by-word: Yes / No only (never "authorized" filter).
            primary = str(value or (cands[0] if cands else ""))
        elif field_type in _EEO_TYPES:
            frag = _type_fragment_for(field_type, cands)
            if frag:
                primary = frag
        elif field_type in ("DEGREE", "DISCIPLINE", "MAJOR", "FIELD_OF_STUDY"):
            frag = _type_fragment_for(field_type, cands)
            if frag:
                primary = frag
        elif field_type == "SCHOOL":
            # Word-by-word on institution head (no city) via typable_dropdown_narrow_and_click
            primary = ""
            for c in cands:
                head = c.split(",")[0].strip()
                if head and len(head) >= 4:
                    primary = head
                    break
            if not primary:
                frags = _school_type_fragments(cands)
                primary = frags[0] if frags else (cands[0] if cands else value)
        elif field_type == "SPONSORSHIP":
            # Prefer alias text for word split ("will" → "not" → "require" …).
            primary = cands[0] if cands else value
        elif field_type == "HOW_HEARD":
            try:
                from fill_verify import how_heard_leaf_candidates

                leaves = how_heard_leaf_candidates()
                primary = leaves[0] if leaves else (cands[0] if cands else value)
            except Exception:
                primary = cands[0] if cands else value
        elif field_type == "SALARY_EXPECTED":
            # Full band text for word-by-word narrow (not bare numeric fill-only)
            for c in cands:
                if re.search(r"\d{1,3}(?:,\d{3})+", c):
                    primary = c
                    break
            else:
                one = _type_fragment_for(field_type, cands)
                if one:
                    primary = one

        click = await typable_dropdown_narrow_and_click(
            page,
            filter_input=filter_loc,
            value=str(primary),
            aliases=cands,
            score_fn=_score_option,
            timeout_ms=timeout_ms,
            use_type=use_type,
            option_selectors=[
                ".select__option",
                "[id*='react-select'][id*='option']",
                "[role='listbox'] [role='option']",
                "[role='option']",
            ],
            # Scope options to THIS select's container: GH mounts every select
            # menu at once, so a page-wide click on "Decline To Self Identify"
            # (shared by Hispanic + Race) clobbered the sibling select.
            root=container,
            field_type=field_type or "",
            label=label or "",
            report=report,
        )
        texts_note = list(click.get("options") or [])
        typed_frag = str(click.get("typed_frag") or "")
        best_s = int(click.get("score") or 0)

        if not click.get("option_clicked"):
            # EEO: clear filter that zeroed Decline options, retry click-only
            if use_type and field_type in _EEO_TYPES and await inp.count():
                try:
                    await inp.fill("")
                    await page.wait_for_timeout(220)
                except Exception:
                    await control.click(timeout=timeout_ms)
                    await page.wait_for_timeout(180)
                click = await typable_dropdown_narrow_and_click(
                    page,
                    filter_input=filter_loc,
                    value=str(primary),
                    aliases=cands,
                    score_fn=_score_option,
                    timeout_ms=timeout_ms,
                    use_type=False,
                    option_selectors=[".select__option", "[role='option']"],
                    root=container,
                    field_type=field_type or "",
                    label=label or "",
                    report=report,
                )
                texts_note = list(click.get("options") or texts_note)
                best_s = int(click.get("score") or best_s)
            if not click.get("option_clicked"):
                opts, texts = await wait_for_option_texts(
                    page, timeout_ms=min(timeout_ms, 1600)
                )
                texts_note = [t for t in (texts or []) if t] or texts_note
                # ATS2-013: never guess salary band (~⅔) or "Create …" school —
                # fail closed when scoring/narrowing did not pick a clear option.
                await _escape_menu()
                return {
                    "ok": False,
                    "error": click.get("error") or "no matching option",
                    "options": texts_note[:12],
                    "aliases_tried": cands,
                    "typed_frag": typed_frag,
                    "verified": False,
                    "steps": click.get("steps"),
                }

        picked = click.get("picked") or ""
        await page.wait_for_timeout(220)
        shown = await read_gh_select_display(container)
        if not shown and field_type == "ADDRESS_COUNTRY" and picked:
            for _poll in range(4):
                try:
                    await page.wait_for_timeout(120)
                except Exception:
                    pass
                shown = await read_gh_select_display(container)
                if shown and not is_placeholder_select_value(shown):
                    break
                try:
                    raw = (await control.inner_text()).strip()
                    if raw and not is_placeholder_select_value(raw):
                        shown = raw.split("\n")[0].strip()
                        break
                except Exception:
                    pass

        # GH Dragos Country*: options are "United States +1", committed shown is
        # "+1" + iti__us flag. Accept via select_readback_ok(picked=…) rescue.
        ok = select_readback_ok(
            shown or picked,
            cands,
            typed_frag=typed_frag,
            picked=picked,
            score_fn=_score_option,
            min_score=50,
        )
        # Extra: dial-only shown + US flag in container + picked matches country
        if (
            not ok
            and field_type == "ADDRESS_COUNTRY"
            and is_dial_only_display(shown)
            and picked
            and _score_option(picked, cands[0] if cands else "United States") >= 50
        ):
            try:
                flag = container.locator(".iti__flag").first
                if await flag.count():
                    cls = ((await flag.get_attribute("class")) or "").lower()
                    # US / USA / United States → iti__us
                    want_us = any(
                        re.search(r"united\s*states|\busa\b|\bus\b", c, re.I)
                        for c in cands
                    )
                    if want_us and "iti__us" in cls:
                        ok = True
                    elif not want_us:
                        # ATS-011: require flag ISO to match intended country
                        ok = iti_flag_matches_country(cls, cands, picked)
            except Exception:
                pass
        if is_placeholder_select_value(shown) and not (
            field_type == "ADDRESS_COUNTRY" and picked and looks_like_dial_code_option(picked)
        ):
            ok = False
        if not shown and not picked:
            ok = False
        # Never promote bare dial-only shown without a matching country pick
        if (
            not ok
            and picked
            and not is_dial_only_display(picked)
            and _shown_matches_cands(picked, cands, field_type=field_type)
        ):
            if (
                not shown
                or _shown_matches_cands(shown, cands, field_type=field_type)
                or (
                    field_type == "ADDRESS_COUNTRY"
                    and is_dial_only_display(shown)
                    and looks_like_dial_code_option(picked)
                )
            ):
                ok = True
                shown = shown or picked
        return {
            "ok": ok,
            "picked": picked,
            "shown": shown or picked,
            "score": best_s,
            "aliases_tried": cands,
            "typed_frag": typed_frag,
            "option_clicked": True,
            "verified": ok,
            "error": None if ok else "readback_uncommitted_or_mismatch",
            "steps": click.get("steps"),
            "options": texts_note[:12],
        }

    # Attempt 1: type filter then click option
    result = await _attempt(use_type=True)
    if result.get("ok") and result.get("verified"):
        return await _contract(result)

    # Attempt 2 (retry once): reopen, no type, click matching option from full list
    try:
        await _escape_menu()
        await page.wait_for_timeout(120)
    except Exception:
        pass
    retry = await _attempt(use_type=False)
    retry["retried"] = True
    retry["first_error"] = result.get("error")
    if retry.get("ok") and retry.get("verified"):
        return await _contract(retry)

    # Both failed — do not thrash further
    await _escape_menu()
    return await _contract({
        "ok": False,
        "error": retry.get("error") or result.get("error") or "select_verify_failed",
        "picked": retry.get("picked") or result.get("picked"),
        "shown": retry.get("shown") or result.get("shown") or "",
        "options": retry.get("options") or result.get("options") or [],
        "aliases_tried": cands,
        "retried": True,
        "verified": False,
        "option_clicked": bool(
            retry.get("option_clicked") or result.get("option_clicked")
        ),
    })


async def fill_other_specify(page, text: str) -> bool:
    """Fill Greenhouse 'If Other, please specify…' after HOW_HEARD=Other."""
    if not text:
        return False
    patterns = [
        re.compile(r"if\s*'?other'?", re.I),
        re.compile(r"please specify how you heard", re.I),
        re.compile(r"please specify", re.I),
    ]
    for pat in patterns:
        lab = page.locator("label").filter(has_text=pat).first
        if await lab.count() == 0:
            continue
        # Prefer input in the same field block
        box = lab.locator("xpath=following::input[not(@type='hidden')][1]").first
        if await box.count() == 0:
            box = page.locator("input[type=text]:visible").nth(0)
        try:
            await box.fill(text, timeout=3000)
            return True
        except Exception:
            continue
    return False


def self_test() -> None:
    """Pure unit checks (no browser)."""
    wo = _label_needle(
        "Are you authorized to work in the US without the need for visa sponsorship?"
    )
    assert wo.lower() == "without the need for visa sponsorship"
    # Tax Relief style (same "the need" wording)
    tra = (
        "Are you currently authorized to work in the United States without "
        "the need for visa sponsorship, now or in the future?"
    )
    assert _label_needle(tra).lower() in tra.lower()
    assert _label_needle(
        "Are you legally authorized to work in the United States?"
    ) == "authorized to work"
    assert _label_needle(
        "Will you now require immigration sponsorship for employment?"
    ) == "require immigration sponsorship"
    assert _label_needle(
        "Will you now or in the future require sponsorship to work in the United States?*"
    ) == "require sponsorship"
    # SMS labels must resolve to a substring present in the label (not bare "marketing")
    sms_lab = "Do you consent to receiving SMS from TRA at the number provided?"
    assert _label_needle(sms_lab) == "SMS"
    assert "SMS" in sms_lab
    txt_lab = "Would you like to receive SMS text messages about recruiting events?"
    assert _label_needle(txt_lab) == "SMS"
    mkt_lab = "Would you like to receive marketing emails about our products?"
    assert "marketing" in _label_needle(mkt_lab).lower()

    assert _score_option("Yes", "Yes") >= 90
    assert _score_option("No, I will require visa sponsorship", "No") == 0
    assert _score_option("No, I will not require sponsorship", "No") >= 90
    # ATS2-014: confusable states beyond IL/ID (use full names — "MS" hits M.S. degree regex)
    assert _score_option("Missouri", "Mississippi") == 0
    assert _score_option("Mississippi", "Mississippi") >= 70
    assert _score_option("Arizona", "Arkansas") == 0
    assert _score_option("Arkansas", "Arkansas") >= 70
    assert _score_option("Idaho", "IL") == 0
    assert _score_option("Illinois", "IL") >= 70
    assert _score_option("Missouri", "MS") == 0  # confusable reject before degree path
    # Phone dial-code country options (GH Dragos Country*) score via name strip
    assert looks_like_dial_code_option("United States +1")
    assert looks_like_dial_code_option("+1")
    assert not looks_like_dial_code_option("United States")
    assert country_name_from_dial_option("United States +1") == "United States"
    assert _score_option("United States +1", "United States") >= 80
    assert _score_option("United States", "United States") >= 90
    # Bare +1 alone is not a country match without picked rescue
    assert not _shown_matches_cands("+1", ["United States", "USA", "US"])
    assert _shown_matches_cands(
        "United States +1", aliases_for("ADDRESS_COUNTRY", "United States")
    )

    assert _shown_matches_cands("Yes", ["Yes", "No"])
    assert not _shown_matches_cands("Select...", ["Yes"])

    assert is_post_resume_reassert_via("greenhouse_post_resume_reassert")
    assert not is_post_resume_reassert_via("ensure_resume")

    trap = "No, I will require visa sponsorship"
    assert _score_option(trap, "No") == 0

    # Salary band cross-format
    assert _score_option("$80,000-$100,000", "$80,000 - $100,000") >= 95
    assert _score_option("80,000 - 100,000", "$80,000 - $100,000") >= 80

    # School head match (city suffix in alias only)
    assert _score_option("University of Alabama", "University of Alabama, Tuscaloosa") >= 88
    assert _score_option("The University of Alabama", "University of Alabama") >= 85

    # Degree polarity + Master preferred
    assert _score_option("Bachelor's Degree", "Master") == 0
    assert _score_option("Master's Degree", "Master") >= 70
    assert _score_option("Bachelor's Degree", "Bachelor") >= 70
    assert _score_option("Associate Degree", "Master's Degree") == 0
    assert _score_option("A.A.", "Master's Degree") == 0
    assert _score_option("Associate of Arts Degree", "Master") == 0
    # EEO type fragment must NOT filter to Decline when preferred exists
    dis_cands = aliases_for("DISABILITY", "I do not have a disability")
    frag = _type_fragment_for("DISABILITY", dis_cands)
    assert "want to answer" not in frag.lower()
    assert "wish to answer" not in frag.lower()
    vet_cands = aliases_for("VETERAN", "I am not a protected veteran")
    frag_v = _type_fragment_for("VETERAN", vet_cands)
    assert "wish to answer" not in frag_v.lower()
    # Decline must not count as already-correct when preferred exists
    assert not _shown_matches_cands(
        "I do not want to answer", dis_cands, field_type="DISABILITY"
    )
    assert _shown_matches_cands(
        "No, I do not have a disability and have not had one in the past",
        dis_cands,
        field_type="DISABILITY",
    )
    # Master's aliases drop Bachelor — Bachelor cannot win the tie
    master_cands = aliases_for("DEGREE", "Master's Degree")
    assert not any(re.search(r"bachelor", c, re.I) for c in master_cands)
    from verified_select import clear_closest_match, rank_option_matches

    ranked = rank_option_matches(
        ["Bachelor's Degree", "Master's Degree", "Associate Degree"],
        master_cands,
        _score_option,
    )
    clear = clear_closest_match(ranked, at_last_word=True, min_score=40)
    assert clear and "Master" in clear[1], clear

    print("gh_select.self_test: OK")


if __name__ == "__main__":
    self_test()
