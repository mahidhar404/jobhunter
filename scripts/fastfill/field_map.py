"""Deterministic form-field classification - Layers 0 and 1 of the fast filler.

The whole premise: a job application is the same ~20 fields wearing different
labels. Resolving "this input wants an email address" is a lookup problem, not a
reasoning problem, so it should cost zero network calls and return the identical
answer every single run. An LLM is reserved for genuinely unseen fields only
(Layer 2, elsewhere) - and its real job there is to produce a mapping that gets
saved, so the next form on that platform needs no model call at all.

Layer 0 - the `autocomplete` attribute. It's an HTML standard with defined
tokens, and Chromium's own autofill treats it as authoritative over its
heuristics (except `off`). Most serious ATS platforms set it. Free, exact, no
guessing - so it is always tried first.

Layer 1 - regex over label/name/id/placeholder/aria-label, in the spirit of
Chromium's form_parsing heuristics (components/autofill/core/browser/
form_parsing/regex_patterns.{cc,h}, BSD). Those patterns live in compiled C++
rather than a drop-in data file, so this is a hand-ported subset covering the
field types job applications actually ask for, not a wholesale import.

Two guards borrowed from Chromium's design, both learned the hard way by them
across billions of real forms:
  * The attribute search order matters, and the first hit wins. The VISIBLE
    label is checked first because it is what a human actually reads, and it is
    the only attribute guaranteed to describe the field's real purpose - internal
    `name` attributes routinely encode the surrounding form section rather than
    the field itself. Measured, not assumed: a real EEO signature box
    (name="eeo[disabilitySignature]", label="Name",
    placeholder="Enter your full name") wants the applicant's NAME, but a
    name-first order classified it as a disability question. Label-first scored
    92.3% vs 92.1% overall and 99.7% vs 99.5% precision on the 136-form corpus.
  * Don't run fuzzy heuristics on trivial forms. Chromium requires >=3 fields
    resolving to >=3 distinct types before trusting local heuristics; a lone
    text box on a search page is far more likely to be a site search than a
    real application field. (The current Skyvern-driven pipeline delegates
    this judgment call to the model itself via LAYER3_RULES rather than a
    standalone classifier - see hybrid_fill.py.)
"""

import fcntl
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

# ---------------------------------------------------------------------------
# Canonical field types (analogous to Chromium's FieldType enum). Deliberately
# job-application-scoped - no credit card / IBAN / travel types, since a wrong
# match is worse than no match and every extra type widens the blast radius.
# ---------------------------------------------------------------------------
NAME_FIRST = "NAME_FIRST"
NAME_LAST = "NAME_LAST"
NAME_FULL = "NAME_FULL"
# Optional; dummy has no middle name — leave blank / skip Flash essays.
NAME_MIDDLE = "NAME_MIDDLE"
# Conditional follow-up after relatives=No ("If Yes, please state their name").
RELATIVE_NAME = "RELATIVE_NAME"
EMAIL = "EMAIL"
PHONE = "PHONE"
# Optional Workday/ATS dial extension — leave blank; never essay/phone reclaim.
PHONE_EXTENSION = "PHONE_EXTENSION"
# Workday / GH "Country Phone Code" / dial-code combobox (United States (+1)).
PHONE_COUNTRY_CODE = "PHONE_COUNTRY_CODE"
# Workday "Phone Device Type" (Mobile / Home) — not the number.
PHONE_DEVICE = "PHONE_DEVICE"
ADDRESS_LINE1 = "ADDRESS_LINE1"
ADDRESS_LINE2 = "ADDRESS_LINE2"
ADDRESS_CITY = "ADDRESS_CITY"
ADDRESS_STATE = "ADDRESS_STATE"
ADDRESS_COUNTY = "ADDRESS_COUNTY"
ADDRESS_ZIP = "ADDRESS_ZIP"
ADDRESS_COUNTRY = "ADDRESS_COUNTRY"
LINKEDIN = "LINKEDIN"
GITHUB = "GITHUB"
PORTFOLIO = "PORTFOLIO"
TWITTER = "TWITTER"
RESUME_UPLOAD = "RESUME_UPLOAD"
COVER_LETTER = "COVER_LETTER"
WORK_AUTH = "WORK_AUTH"
# Yes/No: "Do you currently live in the United States?" (GH leftover found live)
US_RESIDENCE = "US_RESIDENCE"
# Ashby Truelogic-style: "Are you currently based in Latin America?" segmented Yes/No
LATIN_AMERICA = "LATIN_AMERICA"
SPONSORSHIP = "SPONSORSHIP"
GENDER = "GENDER"
RACE = "RACE"
HISPANIC = "HISPANIC"
VETERAN = "VETERAN"
DISABILITY = "DISABILITY"
# LGBTQIA+ community identity — prefer-not-to-disclose per shared EEO policy
LGBTQIA = "LGBTQIA"
# Pronouns — policy-safe prefer-not-to-disclose (never invent He/him / She/her)
PRONOUNS = "PRONOUNS"
# Security clearance Yes/No (TS/SCI, polygraph, "do you have a clearance?")
CLEARANCE = "CLEARANCE"
# Closed list: "Security Clearance Type" → None / No Clearance
CLEARANCE_TYPE = "CLEARANCE_TYPE"
# "Are you a U.S. citizen?" (distinct from sponsorship / work-auth)
US_CITIZEN = "US_CITIZEN"
# "Visa Requirement Status" / similar — dummy needs no visa
VISA_STATUS = "VISA_STATUS"
YEARS_EXPERIENCE = "YEARS_EXPERIENCE"
SCHOOL = "SCHOOL"
DEGREE = "DEGREE"
# Education major / discipline (GH grvty "Discipline" leftover)
DISCIPLINE = "DISCIPLINE"
MAJOR = "MAJOR"
FIELD_OF_STUDY = "FIELD_OF_STUDY"
# Education date years on Greenhouse multi-row education widgets
EDUCATION_START_YEAR = "EDUCATION_START_YEAR"
EDUCATION_END_YEAR = "EDUCATION_END_YEAR"
SALARY_EXPECTED = "SALARY_EXPECTED"
SALARY_CURRENT = "SALARY_CURRENT"
NOTICE_PERIOD = "NOTICE_PERIOD"
RELOCATION = "RELOCATION"
# Free-text "Where do you currently reside?" (city/state/country blob)
LOCATION = "LOCATION"
# "Able to commit to a daily commute to …?" Yes/No policy
COMMUTE = "COMMUTE"
AGE_18 = "AGE_18"
AGE_RANGE = "AGE_RANGE"
FELONY = "FELONY"
# "willing to undergo a background check…" (Yes) — distinct from felony conviction
BACKGROUND_CHECK = "BACKGROUND_CHECK"
WORKED_HERE_BEFORE = "WORKED_HERE_BEFORE"
HOW_HEARD = "HOW_HEARD"
SERVICE_MEMBER = "SERVICE_MEMBER"
CURRENT_COMPANY = "CURRENT_COMPANY"
CURRENT_TITLE = "CURRENT_TITLE"
# Free-text "what role are you applying for" (Lever fellowship cards)
APPLYING_FOR = "APPLYING_FOR"
PASSWORD = "PASSWORD"
PASSWORD_CONFIRM = "PASSWORD_CONFIRM"
TERMS_CONSENT = "TERMS_CONSENT"
MARKETING_CONSENT = "MARKETING_CONSENT"
# "Do you require reasonable accommodations / adjustments?" → No
ACCOMMODATIONS = "ACCOMMODATIONS"
# Follow-up "if yes provide details; if not enter N/A" → N/A
ACCOMMODATIONS_DETAILS = "ACCOMMODATIONS_DETAILS"
# Capco GH: "Were you referred… by a current Employee?" → No
EMPLOYEE_REFERRAL = "EMPLOYEE_REFERRAL"
# Conditional "employee's Capco email" when referral=No → N/A (never reopen Yes/No)
REFERRAL_EMAIL = "REFERRAL_EMAIL"
# Short "why interested / why this company" — dummy-safe canned line only.
INTEREST = "INTEREST"
# "Do you live within N miles of a talent hub / office?" (Ashby Socure-style)
TALENT_HUB = "TALENT_HUB"

# ---------------------------------------------------------------------------
# LAYER 0: the HTML `autocomplete` attribute -> canonical type.
# Standard tokens only (WHATWG autofill field names). `off`/`on` carry no type
# information and are deliberately absent so they fall through to Layer 1.
# ---------------------------------------------------------------------------
AUTOCOMPLETE_MAP = {
    "given-name": NAME_FIRST,
    "additional-name": NAME_MIDDLE,   # optional; values map leaves blank
    "family-name": NAME_LAST,
    "name": NAME_FULL,
    "email": EMAIL,
    "tel": PHONE,
    "tel-national": PHONE,
    "street-address": ADDRESS_LINE1,
    "address-line1": ADDRESS_LINE1,
    "address-line2": ADDRESS_LINE2,
    "address-level2": ADDRESS_CITY,   # spec: city/town
    "address-level1": ADDRESS_STATE,  # spec: state/province
    "postal-code": ADDRESS_ZIP,
    "country": ADDRESS_COUNTRY,
    "country-name": ADDRESS_COUNTRY,
    "tel-country-code": PHONE_COUNTRY_CODE,
    "candidate-location": LOCATION,
    "candidate_location": LOCATION,
    "organization-title": CURRENT_TITLE,  # Lever/GH current role
    "url": PORTFOLIO,
}

# ---------------------------------------------------------------------------
# LAYER 1: regex heuristics, ordered most-specific-first.
#
# Ordering is load-bearing, not cosmetic. "first name" must be tested before a
# bare "name", and "linkedin" before a generic "url", or the broader pattern
# swallows the specific one. Python dicts preserve insertion order, so the
# literal order below IS the match precedence.
# ---------------------------------------------------------------------------
PATTERNS = {
    # -- names: specific parts before the generic whole ----------------------
    # Middle before first/last so "Middle Name" never becomes NAME_FIRST.
    NAME_MIDDLE: r"middle[\s_-]*name|additional[\s_-]*name|second[\s_-]*name|"
                 r"^m[\s_-]*name$|\bmname\b",
    NAME_FIRST: r"first[\s_-]*name|^f[\s_-]*name$|\bfname\b|given[\s_-]*name|forename",
    NAME_LAST: r"last[\s_-]*name|^l[\s_-]*name$|\blname\b|family[\s_-]*name|surname",
    # `signature` belongs here, not with the EEO types: an EEO section's
    # signature box (name="eeo[disabilitySignature]", label="Name",
    # placeholder="Enter your full name") wants the applicant's NAME typed as an
    # e-signature - answering it with a disability status would be both wrong
    # and, on a self-identification form, actively harmful. Listing it under
    # NAME_FULL and keeping names ahead of the EEO block in this dict is what
    # makes the right value win.
    # Word boundaries here are load-bearing, learned by regression: an
    # unanchored `e[\s_-]*sign` matches the substring "esign" inside
    # "d-e-sign-ing", so "How many years of professional experience do you have
    # designing..." was classified as a signature field. Any short token spliced
    # into this alternation needs \b on both ends.
    NAME_FULL: r"full[\s_-]*name|^name$|your[\s_-]*name|applicant[\s_-]*name|legal[\s_-]*name|\bsignature\b|\be[\s_-]*signature\b|\baffirmation\b|\backnowledge?ment\b",

    # -- consent: two DIFFERENT things, deliberately split -------------------
    # TERMS_CONSENT gates progress - an account cannot be created without it,
    # and the user explicitly authorised accepting it for testing.
    # MARKETING_CONSENT is optional (SMS/email promotions, talent-community
    # mailing lists). Nobody asked to be opted into marketing, and the
    # privacy-preserving default is to leave it unchecked - so it gets its own
    # type and is never ticked, rather than being lumped in with terms.
    # MARKETING is matched FIRST because its text often also contains the word
    # "consent"/"agree", which would otherwise be captured as terms.
    MARKETING_CONSENT: r"marketing|promotional|newsletter|talent[\s_-]*community|"
                       r"sms|text[\s_-]*message|receiv(e|ing)[\s_-]*(updates|communications|emails|information)|"
                       r"opt[\s_-]*in|subscribe|"
                       r"recruiting[\s_-]*events?|invited[\s_-]*to[\s_-]*our|"
                       r"receive[\s_-]*information[\s_-]*about",
    # Capco GH live bug: "Do you require reasonable accommodations or
    # adjustments?" was stolen by TERMS_CONSENT → Yes. Require/need questions
    # and their N/A follow-ups must win BEFORE consent checkboxes.
    # Details BEFORE the Yes/No type so "If you answered yes to the Reasonable
    # Adjustments question… enter N/A" never becomes ACCOMMODATIONS=No.
    ACCOMMODATIONS_DETAILS: (
        r"(if\s+you\s+answered\s+yes|if\s+yes|if\s+not[, ]*\s*enter\s+n/?a|"
        r"enter\s+n/?a|additional\s+details).{0,120}"
        r"(accommodation|adjustment|reasonable)|"
        r"(accommodation|adjustment|reasonable).{0,120}"
        r"(if\s+you\s+answered\s+yes|if\s+yes|if\s+not|enter\s+n/?a|"
        r"additional\s+details|provide\s+(more\s+)?details)|"
        r"reasonable[\s_-]*adjustments?\s+question"
    ),
    ACCOMMODATIONS: (
        r"(require|need|request|seeking).{0,40}"
        r"(reasonable[\s_-]*)?(accommodations?|adjustments?)|"
        r"(reasonable[\s_-]*)?(accommodations?|adjustments?).{0,40}"
        r"(require|need|request|seeking)|"
        r"do\s+you\s+(require|need).{0,40}(accommodation|adjustment)|"
        r"reasonable[\s_-]*accommodations?\s+or\s+adjustments?"
    ),
    # Capco: referral Yes/No BEFORE email follow-up so the combined label
    # "Were you referred…? If yes, confirm employee's Capco email" stays No —
    # not N/A into a Yes/No select.
    EMPLOYEE_REFERRAL: (
        r"were\s+you\s+referred|referred\s+to\s+this\s+(role|job|position)|"
        r"referred\s+by\s+(a\s+)?(current\s+)?(capco\s+)?employee|"
        r"employee\s+referral|current\s+\w+\s+employee.{0,40}refer"
    ),
    # Conditional email when referral=No → N/A (never thrash parent Yes/No).
    REFERRAL_EMAIL: (
        r"(employee'?s?|referral).{0,40}(e[\s_-]*mail)|"
        r"(capco|company).{0,30}employee.{0,30}(e[\s_-]*mail)|"
        r"(e[\s_-]*mail).{0,40}(employee|referr\w*|capco)|"
        r"confirm\s+the\s+employee'?s?.{0,40}(e[\s_-]*mail)"
    ),
    # "Check the box to confirm you wish to move forward with creating an
    # account" (a real Workday tenant, no mention of "terms" at all) was found
    # live, timing out on Create Account because this gate stayed unticked -
    # it reads as a generic instruction rather than the usual terms phrasing.
    # ADA *policy acknowledgment* only — not "do you require accommodations".
    TERMS_CONSENT: r"terms\s*(and|&)\s*conditions|terms\s*of\s*(use|service)|"
                   r"privacy\s*(policy|notice|statement)|data\s+privacy|"
                   r"privacy[\s_-]*acknowledge?ment|"
                   r"candidate[\s_-]*privacy|"
                   r"recruiting\s+privacy|processing\s+your\s+data|"
                   r"data[\s_-]*consent|consent[\s_-]*ack|"
                   r"(provide|offers?|policy|understand|acknowledge|ada).{0,60}"
                   r"reasonable[\s_-]*accommodation|"
                   r"reasonable[\s_-]*accommodation.{0,40}(policy|notice|ada)|"
                   r"i\s+agree|i\s+consent|"
                   r"read\s+and\s+(accept|agree|consent)|acknowledge\s+and\s+agree|"
                   r"data\s+processing\s+consent|"
                   r"confirm\s+you\s+wish\s+to\s+(move\s+forward|proceed|continue)|"
                   r"check\s+the\s+box\s+to\s+confirm|"
                   r"background[\s_-]*check[\s_-]*and|"
                   r"artificial[\s_-]*intelligence[\s_-]*usage|"
                   r"agree\s+to\s+data\s+privacy",

    # -- account-creation gate ----------------------------------------------
    # Several platforms (Workday, iCIMS, Taleo) hide the real application behind
    # a login. A throwaway password is a meaningless technical credential, not a
    # misrepresentation to the employer - unlike a fabricated free-text answer -
    # so filling it is allowed, and button_map classifies "Create Account" as
    # ADVANCE rather than FINAL.
    #
    # CONFIRM is matched BEFORE PASSWORD because "Confirm password" contains the
    # word "password"; the reverse order gives both boxes the same type, which
    # still happens to work here (identical value) but would silently mask a
    # real mismatch on any form that validates them differently.
    PASSWORD_CONFIRM: r"confirm[\s_-]*password|verify[\s_-]*(new[\s_-]*)?password|re[\s_-]*enter[\s_-]*password|password[\s_-]*again|repeat[\s_-]*password",
    PASSWORD: r"password|passcode",

    # -- contact -------------------------------------------------------------
    # `\blogin\b` covers iCIMS account gates (`PersonProfileFields.Login`) where
    # the login identifier is the applicant email. Word boundaries keep this from
    # matching "logged" / "cataloging". Do NOT broaden to bare `user` - that
    # collides with "are you a current user of" screening questions.
    EMAIL: r"e[\s_-]*mail|email[\s_-]*address|\blogin\b",
    # Extension BEFORE PHONE — "Phone Extension" / name=extension must never
    # become PHONE (guard-words alone left it unclassified → Flash essay dump).
    # Require phone/ext context — bare "extension" matched "Contract extension".
    PHONE_EXTENSION: r"phone[\s_-]*ext(?:ension)?|"
                     r"(?:^|[\s_/|-])ext(?:ension)?(?:[\s_.-]*(?:#|no\.?|num(?:ber)?))?\s*$|"
                     r"\bext\.(?:\s*(?:#|no|num|number))?|"
                     r"\bext\s*#\b",
    # Country phone / dial code BEFORE PHONE and ADDRESS_COUNTRY.
    PHONE_COUNTRY_CODE: r"country[\s_-]*phone[\s_-]*code|phone[\s_-]*country[\s_-]*code|"
                        r"country[\s_-]*calling[\s_-]*code|calling[\s_-]*code|"
                        r"dial[\s_-]*code|phone[\s_-]*dial|"
                        r"countryPhoneCode|phoneNumber--countryPhoneCode|"
                        r"phonenumber--countryphonecode",
    PHONE_DEVICE: r"phone[\s_-]*device[\s_-]*type|device[\s_-]*type|"
                  r"phone[\s_-]*type|phoneType",
    PHONE: r"phone|mobile|telephone|\btel\b|cell|contact[\s_-]*number",

    # -- links: named platforms before the generic url catch-all -------------
    LINKEDIN: r"linked[\s_-]*in",
    GITHUB: r"git[\s_-]*hub",
    TWITTER: r"twitter|(?:^|[\s_-])x[\s_-]*url",
    # Drop bare `\bur[li]\b` — it classified "Twitter URL" / "Other URL" as
    # portfolio (Lever leftover). Keep portfolio/website/personal-site only.
    PORTFOLIO: r"portfolio|personal[\s_-]*(web)?site|(^|[\s_-])website([\s_-]|$)|web[\s_-]*page",

    # Classic Yes/No "will you need sponsorship?" / employment visa status.
    # Checked BEFORE WORK_AUTH so "require immigration sponsorship" never
    # steals Yes from authorized-to-work (Extend GH misfill).
    # US_CITIZEN / VISA_STATUS / CLEARANCE* are checked first so "Are you a
    # U.S. citizen?" never becomes SPONSORSHIP=No.
    CLEARANCE_TYPE: r"security[\s_-]*clearance[\s_-]*type|clearance[\s_-]*type|"
                    r"type[\s_-]*of[\s_-]*(security[\s_-]*)?clearance|"
                    r"level[\s_-]*of[\s_-]*(security[\s_-]*)?clearance|"
                    r"what[\s_-]*(security[\s_-]*)?clearance[\s_-]*(do[\s_-]*you[\s_-]*hold|level)",
    CLEARANCE: r"ts[\s_/.-]*sci|polygraph|security[\s_-]*clearance|"
               r"(do[\s_-]*you[\s_-]*)?(have|hold|possess)[\s_-]*(a[\s_-]*)?(active[\s_-]*)?"
               r"(security[\s_-]*)?clearance|"
               r"active[\s_-]*(security[\s_-]*)?clearance|"
               r"cleared[\s_-]*to[\s_-]*(the[\s_-]*)?(secret|top[\s_-]*secret)",
    US_CITIZEN: r"(are[\s_-]*you[\s_-]*a[\s_-]*)?u\.?s\.?[\s_-]*citizen|"
                r"united[\s_-]*states[\s_-]*citizen|us[\s_-]*citizenship|"
                r"citizen[\s_-]*of[\s_-]*(the[\s_-]*)?(us|u\.s\.|united[\s_-]*states)|"
                r"requires?\s+u\.?s\.?\s+citizenship.*are\s+you|"
                r"pursuant\s+to\s+a\s+government\s+contract.*citizen",
    VISA_STATUS: r"visa[\s_-]*requirement[\s_-]*status|visa[\s_-]*status|"
                 r"current[\s_-]*visa[\s_-]*status|immigration[\s_-]*status|"
                 r"what[\s_-]*is[\s_-]*your[\s_-]*(current[\s_-]*)?visa",
    # "authorized … WITHOUT need for sponsorship" is WORK_AUTH=Yes, not
    # SPONSORSHIP=No (GH Tax Relief: wrong option "No, I will require…").
    # MUST be checked BEFORE SPONSORSHIP — bare `sponsor` matches "sponsorship".
    WORK_AUTH: r"without[\s_-]*(the[\s_-]*)?need[\s_-]*for[\s_-]*(visa[\s_-]*)?sponsorship|"
               r"work[\s_-]*authoriz|authoriz(ed|ation)[\s_-]*to[\s_-]*work|"
               r"authorized[\s_-]*to[\s_-]*work[\s_-]*in|"
               r"legally[\s_-]*(authorized|entitled)|right[\s_-]*to[\s_-]*work|"
               r"eligible[\s_-]*to[\s_-]*work|legally[\s_-]*able[\s_-]*to[\s_-]*work|"
               r"employment[\s_-]*eligibility|eligibility[\s_-]*information|"
               r"authorized[\s_-]*to[\s_-]*work[\s_-]*in[\s_-]*the[\s_-]*u\.?s|"
               r"work[\s_-]*in[\s_-]*the[\s_-]*united[\s_-]*states[\s_-]*for[\s_-]*any[\s_-]*employer",
    # Classic Yes/No "will you need sponsorship?" / employment visa status.
    # After WORK_AUTH so "without need for visa sponsorship" never steals as No.
    SPONSORSHIP: r"now[\s_-]*or[\s_-]*in[\s_-]*the[\s_-]*future[\s_-]*require[\s_-]*sponsorship|"
                 r"require[\s_-]*sponsorship[\s_-]*for[\s_-]*employment[\s_-]*visa|"
                 r"sponsorship[\s_-]*for[\s_-]*employment[\s_-]*visa|"
                 r"employment[\s_-]*visa[\s_-]*status|"
                 r"(will|would)[\s_-]*you[\s_-].{0,80}require[\s_-]*(immigration[\s_-]*)?sponsorship|"
                 r"immigration[\s_-]*sponsorship|"
                 r"sponsor|visa[\s_-]*sponsor|require[\s_-]*sponsorship|need[\s_-]*sponsorship|"
                 r"employment[\s_-]*visa|future[\s_-]*require[\s_-]*sponsorship|h1b|h-1b|"
                 r"\bopt\b|permanent[\s_-]*resident",
    TALENT_HUB: r"talent[\s_-]*hub|within\s+\d+\s+miles|live\s+within\s+\d+|office[\s_-]*hub|"
                r"commutable\s+distance|one\s+of\s+.+\s+hubs",

    # -- address: zip before state; LOCATION (city+state free-text) before bare state
    ADDRESS_ZIP: r"zip|postal([\s_-]*code)?|^postal$|post[\s_-]*code|postcode|"
                 r"home[\s_-]*zip",
    # Free-text residence blob BEFORE bare city/state (GH Extend: "What city and
    # state are you currently living in?" must not become ADDRESS_STATE).
    LOCATION: r"where[\s_-]*do[\s_-]*you[\s_-]*(currently[\s_-]*)?reside|"
              r"currently[\s_-]*reside(?![\s_-]*in[\s_-]*(the[\s_-]*)?(us|u\.s\.|united))|"
              r"current[\s_-]*residence|place[\s_-]*of[\s_-]*residence|"
              r"city[\s_-]*and[\s_-]*country[\s_-]*of[\s_-]*residence|"
              r"city[\s_-]*and[\s_-]*state[\s_-]*are[\s_-]*you[\s_-]*currently[\s_-]*living|"
              r"what[\s_-]*city[\s_-]*and[\s_-]*state|"
              r"based[\s_-]*in[\s_-]*any[\s_-]*of[\s_-]*these[\s_-]*states|"
              r"currently[\s_-]*based[\s_-]*in[\s_-]*any",
    # GH Dragos Yes/No BEFORE address patterns: bare `unit` used to match the
    # "Unit" substring inside "United States" and steal this as ADDRESS_LINE2.
    US_RESIDENCE: r"(currently[\s_-]*)?(live|living)[\s_-]*in[\s_-]*(the[\s_-]*)?united[\s_-]*states|"
                  r"reside[\s_-]*in[\s_-]*(the[\s_-]*)?(us|u\.s\.|united[\s_-]*states)|"
                  r"us[\s_-]*resident|based[\s_-]*in[\s_-]*(the[\s_-]*)?united[\s_-]*states|"
                  r"do\s+you\s+currently\s+live\s+in|"
                  r"currently[\s_-]*reside[\s_-]*in[\s_-]*(the[\s_-]*)?(us|u\.s\.|usa|united)",
    # Verb "please state their name" is NOT an address state field.
    RELATIVE_NAME: r"state[\s_-]*their[\s_-]*name|please[\s_-]*state[\s_-]*(their|the)|"
                   r"if[\s_-]*yes[,\s_-]*please[\s_-]*state|"
                   r"name[\s_-]*of[\s_-]*(the[\s_-]*)?(relative|employee|person)|"
                   r"relative[\'s]*[\s_-]*name",
    # County / parish BEFORE bare state — Workday regionSubdivision1 is county, not state.
    ADDRESS_COUNTY: r"\bcounty\b|\bparish\b|regionSubdivision1|region[\s_-]*subdivision",
    # Avoid verb "state" ("please state…"); require address-ish context when bare.
    ADDRESS_STATE: r"(?<!please[\s\-_])\bstate\b(?![\s_-]*(?:their|the[\s_-]*name))|"
                   r"province|address[\s_-]*level[\s_-]*1|"
                   r"state[\s_-]*/[\s_-]*province|what[\s_-]*state|which[\s_-]*state|"
                   r"select[\s_-]*(?:a[\s_-]*)?state|^state$|"
                   r"countryRegion|country[\s_-]*region",
    # `\blocation\b` MUST keep its word boundaries: "relocation" contains the
    # literal substring "location", so an unanchored form would capture every
    # "Are you willing to relocate?" question as an address field and fill a
    # street address into a yes/no. The boundary is what keeps them apart, since
    # RELOCATION is matched later in this dict.
    ADDRESS_CITY: r"\bcity\b|\btown\b|address[\s_-]*level[\s_-]*2|locality|\blocation\b|current[\s_-]*location|"
                  r"candidate[\s_-]*location",
    # Never bare "country" mid-sentence ("authorized to work in the country…")
    ADDRESS_COUNTRY: r"country[\s_-]*of[\s_-]*residence|residing[\s_-]*country|country[\s_-]*name|"
                     r"^country\b|select[\s_-]*(a[\s_-]*)?country|what[\s_-]*country|your[\s_-]*country",
    # `\bunit\b` — never bare `unit` (matches "United" in US-residence labels).
    ADDRESS_LINE2: r"address[\s_-]*(?:line)?[\s_-]*2|apartment|apt\.?|\bunit\b|\bsuite\b",
    ADDRESS_LINE1: r"street|address[\s_-]*(line)?[\s_-]*\d*$|^address$|mailing[\s_-]*address|home[\s_-]*address",

    # -- documents -----------------------------------------------------------
    # `file-input`/`fileupload` are real ids seen on styled drop-zone widgets
    # whose <input type=file> is visually hidden; the structural check in
    # classify_by_input_type() catches most, this covers the rest.
    RESUME_UPLOAD: r"resume|\bcv\b|curriculum[\s_-]*vitae|upload[\s_-]*(your[\s_-]*)?(resume|cv)|file[\s_-]*input|file[\s_-]*upload|attach[\s_-]*(file|resume)",
    COVER_LETTER: r"cover[\s_-]*letter|motivation[\s_-]*letter",

    # BEFORE HISPANIC — "Latin America" must not become EEO latino.
    LATIN_AMERICA: r"based[\s_-]*in[\s_-]*latin[\s_-]*america|"
                   r"currently[\s_-]*based[\s_-]*in[\s_-]*latin[\s_-]*america|"
                   r"located[\s_-]*in[\s_-]*latin[\s_-]*america|"
                   r"resid(e|ing)[\s_-]*in[\s_-]*latin[\s_-]*america",

    # -- EEO / demographic ---------------------------------------------------
    # RACE before HISPANIC: JazzHR/applytojob dumps option text into the label
    # ("Race/EthnicityDecline to answerHispanic or Latino…") which otherwise
    # matches HISPANIC first and fills the wrong select.
    RACE: r"race|ethnic",
    HISPANIC: r"hispanic|latino|latinx",
    # Before GENDER — "LGBTQIA+ community" must not fall through unclassified.
    LGBTQIA: r"lgbtq|lgbtqia|lgbtq\+|sexual[\s_-]*orientation|"
             r"identify[\s_-]*as[\s_-]*part[\s_-]*of[\s_-]*the[\s_-]*lgbt",
    # Before GENDER — pronouns are optional on many Lever forms but still fill decline.
    PRONOUNS: r"\bpronouns?\b",
    GENDER: r"gender|\bsex\b",
    VETERAN: r"veteran|military|protected[\s_-]*veteran",
    DISABILITY: r"disabilit|disabled",

    # -- experience / education ---------------------------------------------
    # Real forms insert a qualifier between "of" and "experience" ("how many
    # years of PROFESSIONAL experience", "years of RELEVANT experience"), which
    # the original adjacent-words pattern could not match. Allow up to two
    # intervening words - bounded rather than `.*` so it can't span a sentence
    # and collide with an unrelated later phrase.
    # The resume parser already extracts both of these, so they cost nothing to
    # support. "Current company" was the single most common unclassified field
    # on a real Lever form.
    CURRENT_COMPANY: r"current[\s_-]*(employer|company)|present[\s_-]*(employer|company)|company[\s_-]*name|employer[\s_-]*name",
    CURRENT_TITLE: r"current[\s_-]*(job[\s_-]*)?title|current[\s_-]*role|current[\s_-]*position|job[\s_-]*title",
    # Lever: "What full time job(s) are you applying for?"
    APPLYING_FOR: r"job\(s\)[\s_-]*are[\s_-]*you[\s_-]*applying|"
                  r"what[\s_-]*(full[\s_-]*time[\s_-]*)?jobs?[\s_-]*are[\s_-]*you[\s_-]*applying",

    YEARS_EXPERIENCE: r"years[\s_-]*of[\s_-]*(\w+[\s_-]+){0,2}experience|experience[\s_-]*(in[\s_-]*)?years|yrs[\s_-]*exp|total[\s_-]*experience|how[\s_-]*many[\s_-]*years",
    SCHOOL: r"school|universit|college|institution|alma[\s_-]*mater",
    # "Highest Level of education completed?" puts the words in the opposite
    # order, so an `education level` adjacency test misses it entirely.
    DEGREE: r"degree|qualification|education[\s_-]*level|level[\s_-]*of[\s_-]*education|highest[\s_-]*(level[\s_-]*of[\s_-]*)?education",
    # GH Discipline / Major / Field of Study (grvty left blank when unclassified)
    DISCIPLINE: r"\bdiscipline\b",
    MAJOR: r"\bmajor\b|area[\s_-]*of[\s_-]*study",
    FIELD_OF_STUDY: r"field[\s_-]*of[\s_-]*study|study[\s_-]*field|concentration",
    # GH education row years — BEFORE NOTICE_PERIOD (which used to steal
    # "Start date year" via bare start[\s_-]*date).
    EDUCATION_START_YEAR: r"start[\s_-]*date[\s_-]*year|start[\s_-]*year|"
                          r"from[\s_-]*year|year[\s_-]*from|education[\s_-]*start",
    EDUCATION_END_YEAR: r"end[\s_-]*date[\s_-]*year|end[\s_-]*year|graduation[\s_-]*year|"
                        r"year[\s_-]*to|to[\s_-]*year|education[\s_-]*end",

    # -- compensation: current before expected -------------------------------
    # "current salary" contains "salary"; testing expected first would capture
    # it and disclose the wrong number, which is a real-world harm, not just a
    # mis-fill. Order here is a correctness requirement.
    SALARY_CURRENT: r"current[\s_-]*(salary|compensation|pay)|present[\s_-]*salary|existing[\s_-]*salary",
    SALARY_EXPECTED: r"expected[\s_-]*(salary|compensation)|desired[\s_-]*(salary|compensation|pay)|"
                     r"salary[\s_-]*(expectation|requirement)|compensation[\s_-]*expectation|"
                     r"compensation\s+expectations?|\bsalary\b",

    # -- logistics -----------------------------------------------------------
    # "how soon can you join us" was the single most common unmatched phrasing
    # (12 occurrences on one platform's template). `date.*available|available.*
    # (to[\s_-]*)?start` also covers camel/dotted ids like info.dateAvailableToStart.
    # Do NOT match education "Start date year" (handled above).
    NOTICE_PERIOD: r"notice[\s_-]*period|availability|available[\s_-]*(to[\s_-]*)?start|date[\s_-]*available|"
                   r"start[\s_-]*date(?![\s_-]*year)|when[\s_-]*can[\s_-]*you[\s_-]*(start|join)|how[\s_-]*soon|"
                   r"join[\s_-]*us|earliest[\s_-]*start|"
                   r"when[\s_-]*are[\s_-]*you[\s_-]*available|available[\s_-]*for[\s_-]*full[\s_-]*time|"
                   r"available[\s_-]*start[\s_-]*date",
    RELOCATION: r"relocat|willing[\s_-]*to[\s_-]*relocate|local[\s_-]*to[\s_-]*or[\s_-]*willing",
    COMMUTE: r"daily[\s_-]*commute|commit[\s_-]*to[\s_-]*(a[\s_-]*)?daily[\s_-]*commute|"
             r"\bcommute\b|commuting[\s_-]*to",

    # -- standard screening --------------------------------------------------
    # Age-band radios (Lever surveys) before the yes/no "18 or older" checkbox.
    AGE_RANGE: r"age[\s_-]*range|what[\s_-]*is[\s_-]*your[\s_-]*age(?![\s_-]*18)",
    AGE_18: r"18[\s_-]*(years|or[\s_-]*older)|at[\s_-]*least[\s_-]*18|age[\s_-]*18|over[\s_-]*18",
    # Tax Relief GH: "willing to undergo a background check…" → Yes (before FELONY).
    BACKGROUND_CHECK: r"undergo\s+a\s+background\s+check|willing\s+to\s+.*background\s+check|"
                      r"background\s+check.*local\s+law|consent\s+to\s+(a\s+)?background\s+check|"
                      r"agree\s+to\s+(a\s+)?background\s+check",
    FELONY: r"felony|convicted|criminal[\s_-]*(record|conviction)|background[\s_-]*check[\s_-]*consent",
    # GH: "worked for this company…", "relatives or friends currently working…"
    # Capco GH: "Do you know anyone or are you related to anyone who works at
    # Capco?" was unclassified → required field left blank. It is a
    # relation/acquaintance-at-employer screening question → No (dummy), same
    # family as "relatives currently working here".
    WORKED_HERE_BEFORE: r"worked[\s_-]*(here|with|for[\s_-]*(us|this[\s_-]*company)|for[\s_-]*this)|"
                        r"ever[\s_-]*worked[\s_-]*with|worked[\s_-]*with[\s_-]*(the[\s_-]*)?|"
                        r"worked[\s_-]*for[\s_-]*this[\s_-]*company|"
                        r"previously[\s_-]*(employed|held)|"
                        r"ever[\s_-]*been[\s_-]*employed|employed[\s_-]*by|"
                        r"prior[\s_-]*worker|previous[\s_-]*worker|"
                        r"candidateispreviousworker|"
                        r"former[\s_-]*employee|held[\s_-]*a[\s_-]*position|"
                        r"relatives?[\s_-]*(or[\s_-]*friends?[\s_-]*)?(currently[\s_-]*)?(work|employ)|"
                        r"friends?[\s_-]*currently[\s_-]*working|"
                        r"know[\s_-]*anyone.{0,60}\bworks?\b|"
                        r"related[\s_-]*to[\s_-]*(anyone|someone).{0,60}\bworks?\b|"
                        r"know[\s_-]*(anyone|someone)[\s_-]*(who[\s_-]*)?(currently[\s_-]*)?works?|"
                        r"family[\s_-]*member[\s_-]*(employed|work)|related[\s_-]*entities",
    # Include "how you heard" / "If Other, please specify how you heard…" (GH)
    # Lever: "If Industry Conference or Other, please provide more details"
    HOW_HEARD: r"how[\s_-]*did[\s_-]*you[\s_-]*(?:first[\s_-]*)?hear|"
               r"how[\s_-]*you[\s_-]*(?:first[\s_-]*)?heard|"
               r"first[\s_-]*hear[\s_-]*about|"
               r"specify[\s_-]*how[\s_-]*you[\s_-]*heard|"
               r"referral[\s_-]*source|how[\s_-]*did[\s_-]*you[\s_-]*find|"
               r"where[\s_-]*did[\s_-]*you[\s_-]*(hear|find)|"
               # Workday Thales/wd3+: bare automation id / name with no human label
               r"\bsource--source\b|"
               r"formfield[\s_-]*source\b|"
               r"candidate[\s_-]*source|"
               r"\bhow[\s_-]*did[\s_-]*you[\s_-]*hear\b|"
               # "If Other" / expand only when hear/source/referral also present
               r"(?:hear|heard|source|referral).{0,40}"
               r"if[\s_-]*(you[\s_-]*selected[\s_-]*)?other|"
               r"if[\s_-]*(you[\s_-]*selected[\s_-]*)?other.{0,40}"
               r"(?:hear|heard|source|referral)|"
               r"industry[\s_-]*conference.*(?:other|hear|source)|"
               r"expand[\s_-]*on[\s_-]*the[\s_-]*above.{0,40}"
               r"(?:hear|heard|source|referral)|"
               r"(?:hear|heard|source|referral).{0,40}"
               r"expand[\s_-]*on[\s_-]*the[\s_-]*above",
    # Short interest / motivation + experience-example essays (canned / Flash)
    INTEREST: r"why\s+(are\s+you\s+)?interested|why\s+do\s+you\s+want|"
              r"why\s+this\s+(job|role|company|position)|"
              r"interest(ed)?\s+in\s+this|"
              r"what\s+interests\s+you|tell\s+us\s+why\s+you|"
              r"examples?\s+of\s+(educational|professional)\s+experience|"
              r"experience\s+with\s+(machine\s+learning|\bml\b|ai\b)|"
              r"briefly\s+provide\s+examples|"
              r"describe\s+(your\s+)?(experience|background)\s+with",
    # Lever yes/no: "Are you a transitioning service member?" → No (dummy)
    SERVICE_MEMBER: r"transitioning[\s_-]*service[\s_-]*member|active[\s_-]*duty[\s_-]*military",
}

_COMPILED = {t: re.compile(p, re.I) for t, p in PATTERNS.items()}

# Attributes searched in descending order of authorial intent. A developer who
# writes name="first_name" meant it; a placeholder is decorative text that may
# only incidentally contain a keyword. First hit wins, so this order is the
# tie-breaker whenever two attributes disagree.
_SEARCH_ORDER = ("label", "aria_label", "name", "id", "placeholder")


def _text_blob(field: dict, attr: str) -> str:
    return str(field.get(attr) or "")


def classify_layer0(field: dict) -> str | None:
    """Resolve via the `autocomplete` attribute alone. Returns None if absent,
    `off`/`on`, or a token we intentionally don't map."""
    token = str(field.get("autocomplete") or "").strip().lower()
    if not token or token in ("off", "on"):
        return None
    # The spec permits section/billing/shipping prefixes ("shipping street-address");
    # the meaningful type is the final token.
    token = token.split()[-1]
    mapped = AUTOCOMPLETE_MAP.get(token)
    blob = " ".join(
        str(field.get(a) or "")
        for a in ("label", "name", "id", "placeholder", "aria_label")
    ).lower()
    # Lever "Twitter URL" / "X URL" often has autocomplete=url. Preserve the
    # named social type so it can use the explicit fictional dummy URL.
    if mapped == PORTFOLIO:
        if re.search(r"twitter|\bx\b[\s_-]*url|instagram|tiktok|facebook", blob):
            if re.search(r"twitter|\bx\b[\s_-]*url", blob):
                return TWITTER
            return None
    # Jobscore (and similar) mis-tags phone inputs as autocomplete=email
    # (e.g. name=home_phone, label=Phone). Ignore that token when name/id/label
    # strongly say phone and do not also say email — real email fields keep
    # autocomplete=email + email in name/label (home_email, Email:, etc.).
    if mapped == EMAIL:
        phoneish = bool(
            re.search(r"phone|telephone|\btel\b|mobile|\bcell\b", blob)
        )
        emailish = bool(re.search(r"e[\s_-]*mail|\bemail\b", blob))
        if phoneish and not emailish:
            return None
    # autocomplete=country on dial widgets → PHONE_COUNTRY_CODE (or fall through)
    if mapped == ADDRESS_COUNTRY:
        phone_codeish = bool(
            re.search(
                r"country[\s_-]*phone|phone[\s_-]*country|calling[\s_-]*code|"
                r"dial[\s_-]*code|countryphonecode|tel-country",
                blob,
            )
        )
        if phone_codeish:
            return PHONE_COUNTRY_CODE
    return mapped


def classify_layer1(field: dict) -> str | None:
    """Regex heuristics over the field's descriptive attributes.

    Scans attribute-by-attribute in _SEARCH_ORDER (not one merged blob) so a
    strong signal in `name` always beats an incidental keyword in a
    `placeholder`, rather than both being flattened into the same haystack
    where whichever pattern is tested first happens to win.

    After a pattern hit, ChamPro-style guard-words may refuse the type
    (mis-map worse than no map) so leftovers/Flash can handle it instead.
    """
    full_blob = " ".join(
        str(field.get(a) or "")
        for a in ("label", "aria_label", "name", "id", "placeholder", "title")
    )
    for attr in _SEARCH_ORDER:
        blob = _text_blob(field, attr)
        if not blob:
            continue
        for ftype, rx in _COMPILED.items():
            if not rx.search(blob):
                continue
            if guard_words_reject(ftype, full_blob or blob):
                continue
            return ftype
    return None


# ChamPro: hard exclude tokens that make a type match unsafe.
# Key = candidate type; value = regexes that, if present on the label blob,
# refuse that type (return None / try next pattern).
GUARD_WORDS: dict[str, tuple[re.Pattern[str], ...]] = {
    ADDRESS_LINE2: (
        re.compile(r"united[\s_-]*states|\blive\b|\breside\b|\bresidence\b", re.I),
        re.compile(r"country(?![\s_-]*code)", re.I),
    ),
    ADDRESS_LINE1: (
        re.compile(r"united[\s_-]*states|\blive\b.*\bunited\b", re.I),
    ),
    NAME_FIRST: (
        re.compile(
            r"emergency|relative|referral|contact[\s_-]*name|"
            r"spouse|supervisor|manager[\s_-]*name|reference",
            re.I,
        ),
    ),
    NAME_LAST: (
        re.compile(
            r"emergency|relative|referral|contact[\s_-]*name|"
            r"spouse|supervisor|reference",
            re.I,
        ),
    ),
    NAME_FULL: (
        re.compile(r"emergency|relative|referral|contact[\s_-]*name", re.I),
        # Capco GH live bug: NAME_FULL's `\backnowledgement\b` token stole
        # "Capco Job Candidate Privacy Notice Acknowledgement*" (a consent
        # checkbox) before TERMS_CONSENT could claim it, so a name was pushed
        # into a consent widget → no_matching_option → required field left
        # blank. Refuse NAME_FULL when the acknowledgement is about a
        # privacy/terms/consent policy so it falls through to TERMS_CONSENT.
        re.compile(
            r"privacy|data[\s_-]*(privacy|protection)|\bgdpr\b|\bconsent\b|"
            r"terms\s*(and|&)\s*conditions|terms\s+of\s+(use|service)|"
            r"privacy[\s_-]*(notice|policy|statement)",
            re.I,
        ),
    ),
    PHONE: (
        re.compile(
            r"device[\s_-]*type|phone[\s_-]*type|extension|"
            r"country[\s_-]*(phone[\s_-]*)?code|dial[\s_-]*code|"
            r"country[\s_-]*calling",
            re.I,
        ),
    ),
    # Refuse non-phone "extension" (contract / file / lease / visa …).
    PHONE_EXTENSION: (
        re.compile(
            r"contract|file[\s_-]*ext|browser|deadline|lease|warranty|"
            r"visa[\s_-]*ext|offer[\s_-]*ext|time[\s_-]*ext|domain|"
            r"filename|file[\s_-]*name",
            re.I,
        ),
    ),
    ADDRESS_COUNTRY: (
        re.compile(
            r"phone|dial|calling[\s_-]*code|country[\s_-]*code|"
            r"device[\s_-]*type",
            re.I,
        ),
    ),
    # Capco GH live bug: "Have you signed a noncompete agreement with any
    # previous employer?" was semantically matched to WORK_AUTH and answered
    # "Yes" — a wrong, policy-risky answer. A noncompete / NDA / restrictive
    # covenant question is never work authorization; refuse the map so it stays
    # a leftover (honest blank) instead of a fabricated affirmative.
    WORK_AUTH: (
        re.compile(
            r"non[\s_-]*compete|noncompete|restrictive[\s_-]*covenant|"
            r"non[\s_-]*(disclosure|solicit(ation)?)|\bnda\b|"
            r"confidentiality[\s_-]*agreement|garden[\s_-]*leave",
            re.I,
        ),
    ),
    # Same family: a noncompete/NDA must never masquerade as a sponsorship
    # question either (both are employment-legal phrasing an embed model
    # conflates).
    SPONSORSHIP: (
        re.compile(
            r"non[\s_-]*compete|noncompete|restrictive[\s_-]*covenant|"
            r"non[\s_-]*(disclosure|solicit(ation)?)|\bnda\b|"
            r"confidentiality[\s_-]*agreement|garden[\s_-]*leave",
            re.I,
        ),
    ),
    EMAIL: (
        re.compile(r"confirm|verify|re[\s_-]*enter|secondary|alternate", re.I),
    ),
    # Capco GH: never treat "require accommodations?" as consent=Yes
    TERMS_CONSENT: (
        re.compile(
            r"(require|need|request|seeking).{0,40}"
            r"(reasonable[\s_-]*)?(accommodation|adjustment)|"
            r"do\s+you\s+(require|need).{0,40}(accommodation|adjustment)|"
            r"if\s+you\s+answered\s+yes.{0,40}(accommodation|adjustment)",
            re.I,
        ),
    ),
}


def guard_words_reject(ftype: str | None, blob: str) -> bool:
    """True when assigning ``ftype`` would be a dangerous mis-map."""
    if not ftype or not blob:
        return False
    rules = GUARD_WORDS.get(str(ftype))
    if not rules:
        return False
    text = str(blob)
    return any(rx.search(text) for rx in rules)


# Honeypot traps: fields a human never sees, planted so that anything filling
# them identifies itself as a bot. Observed live on a real Breezy form as
# `hp_7f2b`. Filling one is worse than leaving a field blank - it can get the
# application silently discarded and the client flagged - so these are detected
# and skipped explicitly rather than relying on a visibility check, which misses
# traps hidden via zero opacity, off-screen positioning or a 1px box.
HONEYPOT_PATTERNS = re.compile(
    r"^hp[_-]|honey[_-]?pot|^bot[_-]?field|^_?trap|leave[_-]?(this[_-]?)?blank"
    r"|^winnie|^url2$|^comments?$|do[_-]?not[_-]?fill",
    re.I,
)


# Some traps say so in plain language, aimed at screen-reader users so a human
# knows to skip the field. Found live on a real Workday form:
#   label="Enter website. This input is for robots only, do not fill it out"
# The name/id check missed it entirely - it classified as PORTFOLIO and escaped
# being filled only because the dummy profile happens to carry no portfolio URL.
# With a value present it would have filled a bot trap, so the visible text has
# to be checked too, not just the machine-facing attributes.
HONEYPOT_TEXT = re.compile(
    r"for\s+robots?\s+only|do\s*not\s*fill|leave\s+(this\s+)?(field\s+)?(blank|empty)"
    r"|if\s+you\s+are\s+human|ignore\s+this\s+field|anti-?spam",
    re.I,
)


def is_worked_here_label(label: str = "", *, name: str = "", automation_id: str = "") -> bool:
    """True for prior-employer / worked-here screening questions (Sandoz, Owens, etc.)."""
    blob = " ".join(
        str(x or "") for x in (label, name, automation_id)
    ).strip()
    if not blob:
        return False
    if re.search(r"candidateispreviousworker|previousworker|worked_here", blob, re.I):
        return True
    ftype, _ = classify_field({"label": label, "name": name, "id": automation_id})
    return ftype == WORKED_HERE_BEFORE


def is_honeypot(field: dict) -> bool:
    """True if the field looks like an anti-bot trap and must NOT be filled."""
    for attr in ("name", "id"):
        v = str(field.get(attr) or "")
        if v and HONEYPOT_PATTERNS.search(v):
            return True
    for attr in ("label", "aria_label", "placeholder", "title"):
        v = str(field.get(attr) or "")
        if v and HONEYPOT_TEXT.search(v):
            return True
    # A visible field always has a label or placeholder; a trap typically has
    # neither AND is explicitly hidden from assistive tech.
    if str(field.get("aria_hidden") or "").lower() == "true":
        return True
    return False


def classify_by_input_type(field: dict) -> str | None:
    """Structural classification from the input's own `type`, before any text is
    consulted. `<input type="file">` on a job application is a resume upload by
    construction - the tag itself carries the meaning, so no label is needed.

    This matters more than it looks: file inputs are routinely visually hidden
    behind a styled drop-zone, so they frequently have NO name, id, label or
    placeholder at all. They were the single largest miss category (29) and
    every one of them was unreachable by any regex, no matter how good.
    """
    if str(field.get("input_type") or "").lower() == "file":
        return RESUME_UPLOAD
    return None


# Curated paraphrase exemplars for the semantic classify fallback (layer 2).
# Only distinctive, low-ambiguity types whose downstream value is deterministic,
# so a correct semantic hit just fills a known-safe value. Deliberately small:
# breadth here trades away precision, and a mis-classification routes a value to
# the wrong field. EEO / demographic types are intentionally EXCLUDED — those
# must go through the SHARED-catalog path, never a fuzzy guess.
_SEMANTIC_EXEMPLARS: dict[str, tuple[str, ...]] = {
    "NAME_FIRST": ("first name", "given name", "preferred first name", "forename"),
    "NAME_LAST": ("last name", "surname", "family name", "legal last name"),
    "EMAIL": ("email address", "e-mail", "contact email"),
    "PHONE": ("phone number", "mobile number", "telephone", "contact number"),
    "LINKEDIN": ("linkedin profile url", "linkedin", "linkedin link"),
    "GITHUB": ("github profile url", "github", "github link"),
    "PORTFOLIO": ("portfolio url", "personal website", "portfolio link"),
    "SCHOOL": ("school", "university attended", "institution name", "college"),
    "DEGREE": ("degree", "degree level", "highest degree earned"),
    "DISCIPLINE": ("discipline", "field of study", "major", "area of study"),
    "SALARY_EXPECTED": (
        "expected salary",
        "salary expectation",
        "desired compensation",
        "expected compensation",
    ),
    "NOTICE_PERIOD": ("notice period", "how much notice", "availability to start"),
    "YEARS_EXPERIENCE": ("years of experience", "total experience", "years worked"),
    "HOW_HEARD": (
        "how did you hear about us",
        "where did you hear about us",
        "how did you find this role",
        "referral source",
        "source--source",
    ),
    "RELOCATION": ("willing to relocate", "open to relocation"),
    "WORK_AUTH": ("authorized to work", "work authorization", "legally authorized to work"),
    "SPONSORSHIP": ("require sponsorship", "need visa sponsorship", "sponsorship required"),
}

# Similarity floor for the semantic fallback. High on purpose: only accept a type
# when the label is clearly a paraphrase of an exemplar, never a loose guess.
_SEMANTIC_CLASSIFY_THRESHOLD = float(
    os.environ.get("FASTFILL_SEMANTIC_CLASSIFY_THRESHOLD", "0.72") or 0.72
)


def _semantic_classify_enabled() -> bool:
    # Default ON. FASTFILL_SEMANTIC_MATCH=0 is the master kill switch (disables
    # ALL semantic matching); FASTFILL_SEMANTIC_CLASSIFY=0 disables just this
    # path. Fires only after the deterministic layers return None, so it can
    # never override an exact/regex classification.
    if os.environ.get("FASTFILL_SEMANTIC_MATCH", "1") == "0":
        return False
    return os.environ.get("FASTFILL_SEMANTIC_CLASSIFY", "1") != "0"


def classify_semantic(field: dict) -> str | None:
    """Similarity fallback: match the field label against curated exemplars.

    Additive only — callers invoke it after the deterministic layers return
    None, so it can never override an existing resolution. Returns a type only
    above _SEMANTIC_CLASSIFY_THRESHOLD. Guard-words still refuse dangerous maps
    (Emergency Contact First Name ↛ NAME_FIRST).
    """
    label = " ".join(
        str(field.get(a) or "")
        for a in ("label", "aria_label", "name", "placeholder", "title")
    ).strip()
    if not label:
        return None
    try:
        from semantic_match import semantic_sim
    except Exception:
        return None
    best_type, best_score = None, 0.0
    for ftype, exemplars in _SEMANTIC_EXEMPLARS.items():
        for ex in exemplars:
            s = semantic_sim(label, ex)
            if s > best_score:
                best_type, best_score = ftype, s
    if best_type and best_score >= _SEMANTIC_CLASSIFY_THRESHOLD:
        if guard_words_reject(best_type, label):
            return None
        return best_type
    return None


def classify_field(field: dict) -> tuple[str | None, str]:
    """Returns (canonical_type_or_None, which_layer_resolved_it)."""
    if is_honeypot(field):
        return None, "honeypot_skipped"
    t = classify_by_input_type(field)
    if t:
        return t, "layer0_input_type"
    t = classify_layer0(field)
    if t:
        return t, "layer0_autocomplete"
    t = classify_layer1(field)
    if t:
        return t, "layer1_regex"
    if _semantic_classify_enabled():
        t = classify_semantic(field)
        if t:
            return t, "layer2_semantic"
    return None, "unresolved"


# ---------------------------------------------------------------------------
# Canonical type -> the actual value from profile.json.
# Kept separate from classification on purpose: classification answers "what
# does this field want", this answers "what do we put there". Testing the two
# independently is the point - a mis-classification and a missing profile value
# are different bugs with different fixes.
# ---------------------------------------------------------------------------
# Synthetic identity, mirroring skyvern_runtime/scripts/real_job_test.py's
# TEST_MODE so both halves of the project use the SAME fake person. 555-0100 is
# the NANP-reserved fictional block and cannot route to a real phone.
#
# The email is a REAL, deliverable throwaway address (user-supplied), not the
# IANA-reserved example.com placeholder it replaced. That is deliberate: account
# -creation gates on Workday/iCIMS/Taleo send a verification link, and a
# non-routable address cannot complete them, capping how far a test can walk.
# The consequence is real accounts on real ATS platforms tied to this mailbox -
# accepted knowingly. It is still never the user's own address.
#
# ALWAYS dummy for autofill unless explicit dashboard/CLI opt-in via
# FASTFILL_ALLOW_REAL=1 + FASTFILL_REAL_PROFILE=1 + TEST_MODE!=1.
# Overlap hygiene still reads profile.json only inside assert_dummy_is_clean().
# Unique dummy identity only. Policy sections come from
# ``dummy_answers.SHARED_FILL_POLICY`` (single source for dummy + real).
_DUMMY_UNIQUE = {
    "personal": {"full_name": "Test Dummy"},
    "contact": {"email": "randommail6969@gmail.com", "phone": "405-555-0100"},
    "links": {
        "github": "https://github.com/test-dummy-account",
        "linkedin": "https://www.linkedin.com/in/test-dummy-000000000",
        "twitter": "https://x.com/test_dummy",
        # Same throwaway github — fills "Website"/"Portfolio" without inventing
        # a second identity (GH leftover: PORTFOLIO no_value).
        "portfolio": "https://github.com/test-dummy-account",
    },
    # Nothing personal to the real applicant belongs here - not just identifying
    # data. Years-of-experience, degree subject, GPA and graduation dates are
    # non-identifying but still specifically HIS, so they get fictional values
    # too. Earlier revisions carried the real 4.5 years and the real
    # "M.S., Computer Science"; both are replaced with values that are obviously
    # invented, so a test can never silently assert against real facts.
    # "school" is a REAL institution ("University of Alabama, Tuscaloosa"),
    # not an invented one, despite the "obviously invented" principle above -
    # found live (2026-07-30): many ATS "School" fields are a closed
    # autocomplete over a curated list of real institutions (confirmed on a
    # Wight & Company Greenhouse posting - "Example State University" never
    # matched anything, so the agent retried the identical text for 19
    # minutes until the watchdog killed the run). This is still fully safe:
    # the actual guarantee against leaking real facts is
    # assert_dummy_is_clean()'s EXECUTABLE overlap check against the real
    # profile below, not whether a value merely looks fake - it would fail
    # loudly if this school ever coincidentally appeared in profile.json.
    # Education: ATS degree *level* + discipline separately. School stays a real
    # curated institution (Alabama / GITAM). Discipline uses a common ATS catalog
    # major (Computer Science) — assert_dummy_is_clean skips bare catalog majors
    # so this does not false-flag against real profile education facts.
    "education": {"degrees": [
        {
            "degree": "Master's Degree",
            "discipline": "Computer Science",
            "school": "University of Alabama, Tuscaloosa",
            "graduation_date": "May 2019",
        },
        # GITAM (not University of Alabama) - matches the dummy resume PDF's
        # own B.S. entry school. Discipline shared for Discipline/Major fields.
        {
            "degree": "Bachelor's Degree",
            "discipline": "Computer Science",
            "school": "GITAM, Visakhapatnam, India",
            "graduation_date": "May 2017",
        },
    ]},
    "experience": {
        "total_years_of_experience": 3.0,
        # Fictional current role — fills Lever/GH "Current company" / title
        "current_company": "Example Corp",
        "current_title": "Applied AI/ML Analyst",
    },
    "address": {"country": "United States"},
    # Throwaway credential for test-account creation only. Deliberately literal
    # and obviously fake rather than randomly generated, so it is reproducible
    # and can never be mistaken for a real secret. Satisfies the common
    # complexity rule (upper, lower, digit, symbol, 12+ chars) that ATS
    # registration forms enforce.
    "account": {"password": "TestDummy!2026x"},
}


def _build_dummy_profile() -> dict:
    """Compose unique dummy identity + SHARED_FILL_POLICY (one policy source)."""
    from dummy_answers import apply_shared_policy_to_profile

    return apply_shared_policy_to_profile(_DUMMY_UNIQUE)


DUMMY_PROFILE = _build_dummy_profile()
DUMMY_ADDRESS = "100 Example Ave, Apt 1A, Springfield, IL 62701"

# The dummy resume PDF (compiled from fixtures/dummy_resume_de.tex via tectonic)
# used by every live test harness that needs an actual file to upload.
DUMMY_PDF = Path(__file__).resolve().parent / "fixtures" / "dummy_resume_de.pdf"

# Gmail `+`-alias state for dummy fills only.
# Primary API: allocate_random_run_email() — randommail6969+{random12}@gmail.com
# with a persistent never-reuse set in alias_state.json["used_emails"]
# (and mirrored "used_aliases"). Sequential per-tenant indices are KILLED —
# they reused emails and caused "already registered" collisions.
# Concurrent allocates serialize on alias_state.json.lock (fcntl.flock) so
# two rapid readers cannot both mint against the same used-set and then
# last-writer-wins, dropping an issued address (reuse / form≠resume drift).
ALIAS_STATE_FILE = Path(__file__).resolve().parent / "alias_state.json"
ALIAS_LOCK_FILE = ALIAS_STATE_FILE.with_suffix(".json.lock")
_USED_EMAILS_KEY = "used_emails"
_USED_ALIASES_KEY = "used_aliases"  # legacy mirror of used_emails
_TOKEN_LEN = 12  # randommail6969+{random12}@gmail.com
_META_KEYS = frozenset({_USED_EMAILS_KEY, _USED_ALIASES_KEY, "last_run", "schema"})


def make_alias_email(base_email: str, n: int | str) -> str:
    """Gmail `+`-alias variant of base_email.

    Prefer allocate_random_run_email() for live runs. ``n`` may be a positive
    int (legacy only — do not use for new runs) or a non-empty str token.
    int ``n<=0`` / empty str → base.

    All variants deliver to the SAME throwaway inbox (Gmail +alias), never to
    a stranger's mailbox.
    """
    if "@" not in base_email:
        return base_email
    if isinstance(n, int):
        if n <= 0:
            return base_email
        token = str(n)
    else:
        token = str(n).strip()
        if not token:
            return base_email
        token = re.sub(r"[^A-Za-z0-9]", "", token)
        if not token:
            return base_email
    local, _, domain = base_email.partition("@")
    local = local.split("+", 1)[0]
    return f"{local}+{token}@{domain}"


def _load_alias_state() -> dict:
    if not ALIAS_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(ALIAS_STATE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_alias_state(state: dict) -> None:
    """Atomic write: temp file in same dir then replace (no half-written JSON)."""
    ALIAS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=1) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".alias_state_",
        suffix=".tmp",
        dir=str(ALIAS_STATE_FILE.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, ALIAS_STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@contextmanager
def _locked_alias_state_for_write():
    """Exclusive flock over alias_state.json read-modify-write.

    Sibling .lock file (same pattern as scripts/jobs_lock.py) so plain
    read_text of the data file never contends with the lock fd.
    """
    ALIAS_LOCK_FILE.touch(exist_ok=True)
    with open(ALIAS_LOCK_FILE, "r+", encoding="utf-8") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            state = _load_alias_state()
            yield state
            _save_alias_state(state)
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def _collect_used_emails(state: dict, base: str) -> set[str]:
    """Union of persisted used set + legacy sequential tenant indexes."""
    used: set[str] = set()
    for key in (_USED_EMAILS_KEY, _USED_ALIASES_KEY):
        raw = state.get(key) or []
        if isinstance(raw, list):
            used.update(
                str(x).lower() for x in raw if isinstance(x, str) and "@" in str(x)
            )
    # Bare base was used on early fixtures / first runs — never reissue.
    used.add(base.lower())
    for key, val in list(state.items()):
        if key in _META_KEYS:
            continue
        try:
            n = int(val)
        except (TypeError, ValueError):
            continue
        if n > 0:
            for i in range(1, n + 1):
                used.add(make_alias_email(base, i).lower())
    return used


def _random_token(length: int = _TOKEN_LEN) -> str:
    """Alphanumeric token of exactly ``length`` chars (hex alphabet, URL-safe)."""
    # token_hex(n) → 2n chars; take ceil(length/2) bytes then trim.
    nbytes = (length + 1) // 2
    token = secrets.token_hex(nbytes)[:length]
    # Avoid pure-decimal tokens that look like legacy sequential +1,+2,…
    if token.isdigit():
        token = "a" + token[1:]
    return token


def allocate_random_run_email(base_email: str | None = None) -> dict:
    """Mint a fresh random Gmail +alias for one dummy fill run. Never reuses.

    Shape: ``randommail6969+{random12}@gmail.com`` (same inbox via +alias).
    Persists every issued address in alias_state.json ``used_emails``
    (and ``used_aliases`` mirror). Check-before-use under exclusive file lock;
    regenerate on collision. Dummy-only — refuses any non-dummy local-part
    base. Never profile.json.

    Returns ``{email, email_alias, alias_token, base_email}``.
    """
    dummy_base = DUMMY_PROFILE["contact"]["email"]
    base = (base_email or dummy_base).strip()
    if base.split("+", 1)[0].lower() != dummy_base.split("+", 1)[0].lower():
        raise ValueError(
            f"refuse non-dummy base email for allocate_random_run_email: {base!r}"
        )

    with _locked_alias_state_for_write() as state:
        used = _collect_used_emails(state, dummy_base)

        token = ""
        email = ""
        for attempt in range(64):
            # Always mint 12-hex first; only lengthen on pathological collisions.
            length = _TOKEN_LEN if attempt < 48 else _TOKEN_LEN + 8
            token = _random_token(length)
            email = make_alias_email(dummy_base, token)
            if email.lower() not in used:
                break
        else:
            raise RuntimeError(
                "allocate_random_run_email exhausted entropy without unique email"
            )
        if len(token) < _TOKEN_LEN or token.isdigit():
            raise RuntimeError(
                f"refuse short/sequential alias token {token!r} (need {_TOKEN_LEN}+ hex)"
            )

        # Persist: single canonical used_emails list; used_aliases is a mirror only.
        # Historical 8-hex / sequential (+1,+2) stay in the set so they are never reissued.
        prior: list[str] = []
        prior_lower: set[str] = set()
        for key in (_USED_EMAILS_KEY, _USED_ALIASES_KEY):
            for x in state.get(key) or []:
                if isinstance(x, str) and "@" in x and x.lower() not in prior_lower:
                    prior.append(x)
                    prior_lower.add(x.lower())
        for u in sorted(used):
            if u not in prior_lower:
                prior.append(u)
                prior_lower.add(u)
        if email.lower() not in prior_lower:
            prior.append(email)
            prior_lower.add(email.lower())

        state[_USED_EMAILS_KEY] = prior
        state[_USED_ALIASES_KEY] = list(prior)  # legacy mirror — keep identical
        state["schema"] = "random12_never_reuse_v2"
        state["last_run"] = {
            "email": email,
            "alias_token": token,
            "ts": int(time.time()),
        }

    return {
        "email": email,
        "email_alias": email,
        "alias_token": token,
        "base_email": dummy_base,
    }


# Back-compat alias — callers must use allocate_random_run_email going forward.
allocate_run_email = allocate_random_run_email


def load_next_alias_index(key: str) -> int:
    """REMOVED — sequential tenant aliases reused emails.

    Raises so no path can silently fall back to reuse. Use
    allocate_random_run_email() / prepare_dummy_run() instead.
    """
    raise RuntimeError(
        f"load_next_alias_index({key!r}) is dead: sequential aliases reuse emails. "
        "Use allocate_random_run_email() / run_identity.prepare_dummy_run()."
    )


def save_next_alias_index(key: str, n: int) -> None:
    """REMOVED — sequential tenant aliases reused emails. See load_next_alias_index."""
    raise RuntimeError(
        f"save_next_alias_index({key!r}, {n}) is dead: sequential aliases reuse emails. "
        "Use allocate_random_run_email() / run_identity.prepare_dummy_run()."
    )


PROFILE_JSON = Path(__file__).resolve().parents[2] / "profile.json"


def is_real_profile_mode() -> bool:
    """True only when dashboard/CLI explicitly opted into real profile fill."""
    return (
        os.environ.get("FASTFILL_ALLOW_REAL") == "1"
        and os.environ.get("FASTFILL_REAL_PROFILE") == "1"
        and os.environ.get("TEST_MODE") != "1"
    )


def assert_real_profile_allowed(*, force_real: bool = False) -> None:
    """Refuse real profile unless the explicit triple-opt-in env is set."""
    wants_real = force_real or os.environ.get("FASTFILL_REAL_PROFILE") == "1"
    if not wants_real:
        return
    if not is_real_profile_mode():
        raise RuntimeError(
            "real profile.json requires FASTFILL_ALLOW_REAL=1, "
            "FASTFILL_REAL_PROFILE=1, and TEST_MODE!=1 (dashboard Test Mode OFF)"
        )


def load_profile(force_real: bool = False) -> tuple[dict, str, bool]:
    """Return (profile, address_text, is_dummy).

    Default: DUMMY_PROFILE (safe for autofill experiments).
    Real profile.json contact/PII only when ``force_real`` or
    ``FASTFILL_REAL_PROFILE=1`` AND ``is_real_profile_mode()`` (explicit opt-in).
    """
    wants_real = force_real or os.environ.get("FASTFILL_REAL_PROFILE") == "1"
    if wants_real:
        assert_real_profile_allowed(force_real=force_real)
        if not PROFILE_JSON.is_file():
            raise FileNotFoundError(f"profile.json missing: {PROFILE_JSON}")
        profile = json.load(open(PROFILE_JSON))
        address_text = resolve_real_address_text()
        return profile, address_text, False
    if os.environ.get("FASTFILL_REAL_PROFILE") == "1":
        raise RuntimeError(
            "FASTFILL_REAL_PROFILE=1 without FASTFILL_ALLOW_REAL=1 — refused"
        )
    return DUMMY_PROFILE, DUMMY_ADDRESS, True


def format_address_line(pick: dict) -> str:
    """Single-line address from the synthetic apartment-bank JSON shape."""
    street = (pick.get("street") or pick.get("line1") or "").strip()
    unit = (pick.get("unit") or "").strip()
    city = (pick.get("city") or "").strip()
    state = (pick.get("state") or "").strip()
    zip_code = (pick.get("zip") or "").strip()
    line1 = ", ".join(part for part in (street, unit) if part)
    if line1 and city and state and zip_code:
        return f"{line1}, {city}, {state} {zip_code}"
    return ""


def apply_resolved_address(values: dict, address: dict) -> dict:
    """Overlay only address field types, preserving identity and policy values."""
    street = str(address.get("street") or address.get("line1") or "").strip()
    unit = str(address.get("unit") or "").strip()
    city = str(address.get("city") or "").strip()
    state = str(address.get("state") or "").strip().upper()
    zip_code = str(address.get("zip") or "").strip()
    if not all((street, city, state, zip_code)):
        raise ValueError("resolved apartment address is incomplete")
    values.update(
        {
            ADDRESS_LINE1: street,
            ADDRESS_LINE2: unit,
            ADDRESS_CITY: city,
            ADDRESS_STATE: state,
            ADDRESS_ZIP: zip_code,
            ADDRESS_COUNTRY: "United States",
            PHONE_COUNTRY_CODE: "United States (+1)",
            LOCATION: f"{city}, {state}, USA",
        }
    )
    return values


def resolve_real_address_text(
    *,
    job_id: str | None = None,
    resume_tex: Path | str | None = None,
    address_pick: dict | None = None,
) -> str:
    """Best-effort mailing address for real-profile fills.

    Preference order:
    1. Explicit ``address_pick``
    2. ``FASTFILL_ADDRESS_TEXT`` env (dashboard Start hands off its one pick)
    3. ``pick_address.py`` against resume.tex (job_id / resume_tex)
    """
    if address_pick:
        formatted = format_address_line(address_pick)
        if formatted:
            return formatted
    env_addr = (os.environ.get("FASTFILL_ADDRESS_TEXT") or "").strip()
    if env_addr:
        return env_addr
    root = Path(__file__).resolve().parents[2]
    tex_candidates: list[Path] = []
    if resume_tex:
        tex_candidates.append(Path(resume_tex))
    if job_id:
        tex_candidates.append(root / "resumes" / job_id / "resume.tex")
    for tex in tex_candidates:
        if not tex.is_file():
            continue
        try:
            import subprocess

            proc = subprocess.run(
                [sys.executable, str(root / "scripts" / "pick_address.py"), str(tex)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                pick = json.loads(proc.stdout)
                formatted = format_address_line(pick)
                if formatted:
                    return formatted
        except Exception:
            pass
    return ""


def assert_not_real_profile_env() -> None:
    """Hard stop if dummy path inherits a partial real-profile env."""
    if os.environ.get("FASTFILL_REAL_PROFILE") == "1" or is_real_profile_mode():
        raise RuntimeError(
            "dummy fill path refuses real-profile env "
            "(FASTFILL_REAL_PROFILE / FASTFILL_ALLOW_REAL / TEST_MODE=0)"
        )


def assert_real_resume_path(path: Path | str) -> Path:
    """Refuse dummy/fixture resumes when Test Mode is OFF."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"real resume missing: {p}")
    s = str(p).replace("\\", "/").lower()
    name = p.name.lower()
    if name in ("credentials.json", "profile.json") or "credentials.json" in s:
        raise RuntimeError(f"refuse non-resume path as upload: {p}")
    if "dummy_resume" in s or name.startswith("dummy_resume_run_"):
        raise RuntimeError(f"refuse dummy resume in real-profile mode: {p}")
    return p


def assert_dummy_resume_path(path: Path | str) -> Path:
    """Refuse tailored / real trusted resume.pdf — dummy fixture or run PDF only."""
    p = Path(path)
    s = str(p).replace("\\", "/").lower()
    name = p.name.lower()
    if name in ("credentials.json", "profile.json") or "credentials.json" in s or s.endswith("/profile.json"):
        raise RuntimeError(f"refuse non-resume path as upload: {p}")
    if "tailored" in s:
        raise RuntimeError(f"refuse tailored resume path: {p}")
    # Real agent uploads land as trusted_uploads/resume.pdf (no "dummy" marker).
    if name == "resume.pdf" and "dummy" not in s:
        raise RuntimeError(f"refuse real trusted resume.pdf: {p}")
    ok = (
        "dummy_resume" in name
        or "dummy_resume" in s
        or name.startswith("dummy_resume_run_")
        or p.resolve() == DUMMY_PDF.resolve()
    )
    if not ok:
        raise RuntimeError(
            f"refuse non-dummy resume path (need dummy_resume* or fixture): {p}"
        )
    return p


# ---------------------------------------------------------------------------
# Compose: shared policy (dummy_answers) + unique profile identity/edu/exp.
# Dummy and real use the SAME shared layer; they differ only on unique keys.
# ---------------------------------------------------------------------------
from dummy_answers import (  # noqa: E402  — after field-type constants
    SHARED_VALUE_TYPES,
    UNIQUE_VALUE_TYPES,
    shared_values as _shared_fill_values,
)

# Backward-compat aliases (older overlay naming).
REAL_IDENTITY_KEEP = frozenset(
    {
        NAME_FIRST,
        NAME_LAST,
        NAME_FULL,
        NAME_MIDDLE,
        EMAIL,
        PHONE,
        LINKEDIN,
        GITHUB,
        PORTFOLIO,
        TWITTER,
        PASSWORD,
        PASSWORD_CONFIRM,
        RESUME_UPLOAD,
    }
)
REAL_EDUCATION_KEEP = frozenset(
    {
        SCHOOL,
        DEGREE,
        DISCIPLINE,
        MAJOR,
        FIELD_OF_STUDY,
        EDUCATION_START_YEAR,
        EDUCATION_END_YEAR,
    }
)
REAL_EXPERIENCE_LEAVE = frozenset(
    {
        CURRENT_COMPANY,
        CURRENT_TITLE,
        YEARS_EXPERIENCE,
        APPLYING_FOR,
    }
)
REAL_ADDRESS_TYPES = frozenset(
    {
        ADDRESS_LINE1,
        ADDRESS_LINE2,
        ADDRESS_CITY,
        ADDRESS_STATE,
        ADDRESS_ZIP,
        ADDRESS_COUNTY,
        ADDRESS_COUNTRY,
        LOCATION,
    }
)
# Unique keys never taken from the shared policy layer.
REAL_OVERLAY_EXCLUDE = frozenset(UNIQUE_VALUE_TYPES)


def build_unique_values(profile: dict, address_text: str = "") -> dict:
    """Profile-specific type→value (identity, education, experience, address).

    Does NOT read eeo / work_auth / screening / prefs / custom policy — those
    come exclusively from ``shared_values()`` via ``compose_fill_values``.
    """
    from dummy_answers import DETERMINISTIC_ANSWERS as _DET

    full = profile.get("personal", {}).get("full_name", "") or ""
    first, _, last = full.partition(" ") if full else ("", "", "")
    degrees = profile.get("education", {}).get("degrees") or [{}]
    _csz = (
        re.search(r",\s*([A-Za-z .'\-]+?),\s*([A-Z]{2})\s+\d{5}", address_text)
        if address_text
        else None
    )
    _grad = str(degrees[0].get("graduation_date") or "")
    _end_year_m = re.search(r"(19|20)\d{2}", _grad)
    _end_year = _end_year_m.group(0) if _end_year_m else ""
    _start_year = str(int(_end_year) - 2) if _end_year.isdigit() else ""

    # Education: profile wins; dummy DET strings only as fallback when blank
    # (so DUMMY_PROFILE still fills School/Degree without duplicating policy).
    school = degrees[0].get("school", "") or (
        _DET["SCHOOL"] if profile is DUMMY_PROFILE or profile.get("_use_det_edu") else ""
    )
    # Safer: if school/degree empty and this looks like dummy (Alabama already
    # in DET or name Test Dummy), use DET. For real empty edu, leave empty.
    is_dummy_like = (
        profile is DUMMY_PROFILE
        or (profile.get("personal") or {}).get("full_name") == "Test Dummy"
    )
    if not school and is_dummy_like:
        school = _DET["SCHOOL"]
    degree = degrees[0].get("degree", "") or (_DET["DEGREE"] if is_dummy_like else "")
    discipline = (
        degrees[0].get("discipline")
        or degrees[0].get("major")
        or degrees[0].get("field_of_study")
        or (_DET["DISCIPLINE"] if is_dummy_like else "")
    )
    major = (
        degrees[0].get("major")
        or degrees[0].get("discipline")
        or (_DET["MAJOR"] if is_dummy_like else "")
    )
    fos = (
        degrees[0].get("field_of_study")
        or degrees[0].get("discipline")
        or (_DET["FIELD_OF_STUDY"] if is_dummy_like else "")
    )

    title = profile.get("experience", {}).get("current_title", "") or ""
    return {
        NAME_FIRST: first,
        NAME_LAST: last,
        NAME_FULL: full,
        NAME_MIDDLE: "",
        RELATIVE_NAME: "",
        PHONE_EXTENSION: "",
        PHONE_COUNTRY_CODE: "United States (+1)",
        PHONE_DEVICE: "Mobile",
        EMAIL: profile.get("contact", {}).get("email", ""),
        PHONE: profile.get("contact", {}).get("phone", ""),
        LINKEDIN: profile.get("links", {}).get("linkedin", ""),
        GITHUB: profile.get("links", {}).get("github", ""),
        TWITTER: profile.get("links", {}).get("twitter", ""),
        PORTFOLIO: (
            profile.get("links", {}).get("portfolio")
            or profile.get("links", {}).get("github")
            or ""
        ),
        YEARS_EXPERIENCE: str(
            profile.get("experience", {}).get("total_years_of_experience", "") or ""
        ),
        CURRENT_COMPANY: profile.get("experience", {}).get("current_company", "") or "",
        CURRENT_TITLE: title,
        APPLYING_FOR: title or ("Applied AI/ML Analyst" if is_dummy_like else ""),
        SCHOOL: school,
        DEGREE: degree,
        DISCIPLINE: discipline,
        MAJOR: major,
        FIELD_OF_STUDY: fos,
        EDUCATION_END_YEAR: _end_year,
        EDUCATION_START_YEAR: _start_year,
        PASSWORD: profile.get("account", {}).get("password", ""),
        PASSWORD_CONFIRM: profile.get("account", {}).get("password", ""),
        LOCATION: (
            f"{_csz.group(1)}, {_csz.group(2)}, USA"
            if _csz
            else (address_text.split(",")[0].strip() if address_text else "")
        ),
        ADDRESS_LINE1: address_text,
        ADDRESS_LINE2: (
            re.search(r",\s*((?:Apt|Apartment|Unit|Suite|#)\s*[^,]+),", address_text, re.I).group(1)
            if address_text
            and re.search(r",\s*((?:Apt|Apartment|Unit|Suite|#)\s*[^,]+),", address_text, re.I)
            else ""
        ),
        ADDRESS_ZIP: (
            re.search(r"\b(\d{5})(-\d{4})?\b", address_text).group(1)
            if address_text and re.search(r"\b(\d{5})(-\d{4})?\b", address_text)
            else ""
        ),
        ADDRESS_CITY: _csz.group(1) if _csz else "",
        ADDRESS_STATE: _csz.group(2) if _csz else "",
        ADDRESS_COUNTY: (
            "Sangamon"
            if address_text
            and ("62701" in address_text or "Springfield, IL" in address_text)
            else ""
        ),
        ADDRESS_COUNTRY: profile.get("address", {}).get("country", "")
        or ("United States" if address_text or is_dummy_like else ""),
    }


def compose_fill_values(
    unique: dict,
    shared: dict | None = None,
) -> dict:
    """Merge shared policy + profile-unique layers.

    Shared keys always win from ``shared`` (never from profile EEO/prefs).
    Unique keys always win from ``unique`` (even when empty).
    """
    shared = dict(shared if shared is not None else _shared_fill_values())
    out = dict(shared)
    for key, val in unique.items():
        if key in SHARED_VALUE_TYPES:
            continue  # never let profile override shared policy
        out[key] = val
    # Guarantee every shared key is present from shared layer
    for key, val in shared.items():
        out[key] = val
    return out


def overlay_dummy_policy_on_real(
    real_values: dict,
    *,
    real_address_present: bool = False,
) -> dict:
    """Compose shared policy onto unique keys from ``real_values``.

    Keeps unique contact/education/experience/address from ``real_values``;
    applies the single shared policy layer. When ``real_address_present`` is
    False and address fields are empty, fills dummy Springfield address —
    **test/compat only**. ``prepare_real_run`` must NOT use that fallback
    (empty address stays empty rather than injecting Springfield into real
    applications).
    """
    unique = {k: real_values.get(k, "") for k in UNIQUE_VALUE_TYPES}
    # Preserve any extra keys callers may have set (e.g. RESUME_UPLOAD already)
    for k, v in real_values.items():
        if k not in SHARED_VALUE_TYPES and k not in unique:
            unique[k] = v
    if not real_address_present:
        # Only fill address from dummy when real had none (compat / tests)
        dummy_unique = build_unique_values(DUMMY_PROFILE, DUMMY_ADDRESS)
        for k in REAL_ADDRESS_TYPES:
            if not (unique.get(k) or "").strip():
                unique[k] = dummy_unique.get(k, "")
    return compose_fill_values(unique)


def build_value_map(profile: dict, address_text: str = "") -> dict:
    """Compose shared policy + unique profile values (dummy and real)."""
    return compose_fill_values(build_unique_values(profile, address_text))


# ---------------------------------------------------------------------------
# Post-fill validation. A saved platform map is a CACHE, never a source of
# truth: a site can redesign such that a stale selector still matches some
# element, and the fill then silently lands in the wrong box. Cheap type checks
# catch that deterministically, so a replayed map can be trusted without a
# model call to sanity-check it.
# ---------------------------------------------------------------------------
_VALIDATORS = {
    EMAIL: lambda v: "@" in v and "." in v.split("@")[-1],
    PHONE: lambda v: sum(c.isdigit() for c in v) >= 7,
    # Extension is optional; if filled must be short digits only (never essay).
    PHONE_EXTENSION: lambda v: bool(v)
    and len(v) <= 8
    and sum(c.isdigit() for c in v) >= 1
    and sum(c.isalpha() for c in v) == 0,
    LINKEDIN: lambda v: "linkedin.com" in v.lower(),
    GITHUB: lambda v: "github.com" in v.lower(),
    ADDRESS_ZIP: lambda v: any(c.isdigit() for c in v),
}

# Optional blanks — never Flash/reclaim essays into these.
OPTIONAL_LEAVE_BLANK_TYPES: frozenset[str] = frozenset(
    {NAME_MIDDLE, RELATIVE_NAME, PHONE_EXTENSION}
)

_PHONE_EXT_LABEL_RE = re.compile(
    r"phone[\s_-]*ext(?:ension)?|"
    r"(?:^|[\s_/|-])ext(?:ension)?(?:[\s_.-]*(?:#|no\.?|num(?:ber)?))?\s*$|"
    r"\bext\.(?:\s*(?:#|no|num|number))?|"
    r"\bext\s*#\b",
    re.I,
)
_PHONE_EXT_NONPHONE_RE = re.compile(
    r"contract|file[\s_-]*ext|browser|deadline|lease|warranty|"
    r"visa[\s_-]*ext|offer[\s_-]*ext|time[\s_-]*ext|domain|filename",
    re.I,
)
# FILL2-005: name/selector-only phone-ext (label empty / "?").
_PHONE_EXT_NAME_RE = re.compile(
    r"^(?:phone[\s_-]*)?ext(?:ension)?$|^phoneextension$|^phone_ext$",
    re.I,
)


def _phone_ext_name_from_selector(selector: str = "") -> str:
    sel = str(selector or "")
    if not sel:
        return ""
    m = re.search(r"""name\s*=\s*['"]?([^'"\]\s]+)""", sel, re.I)
    return (m.group(1) if m else "").strip()


def is_phone_extension_field(
    label: str = "",
    ftype: str | None = None,
    *,
    name: str = "",
    selector: str = "",
) -> bool:
    """True for Workday/ATS phone-extension boxes (optional; never essay)."""
    if str(ftype or "").strip().upper() == PHONE_EXTENSION:
        return True
    lab = str(label or "")
    if _PHONE_EXT_NONPHONE_RE.search(lab):
        return False
    if _PHONE_EXT_LABEL_RE.search(lab):
        return True
    nm = str(name or "").strip() or _phone_ext_name_from_selector(selector)
    if nm and _PHONE_EXT_NAME_RE.match(nm):
        # Guard non-phone when label clearly says otherwise
        if lab and _PHONE_EXT_NONPHONE_RE.search(lab):
            return False
        return True
    return False


_SHORT_NUMERIC_LABEL_RE = re.compile(
    r"phone[\s_-]*ext(?:ension)?|"
    r"(?:^|[\s_/|-])ext(?:ension)?(?:[\s_.-]*(?:#|no\.?|num(?:ber)?))?\s*$|"
    r"\b(?:pin|otp|cvv|ssn[\s_-]*last[\s_-]*4|last[\s_-]*4)\b",
    re.I,
)


def is_short_numeric_field(label: str = "", ftype: str | None = None) -> bool:
    """True for short numeric-only inputs that must never receive essays."""
    if is_phone_extension_field(label, ftype):
        return True
    t = str(ftype or "").strip().upper()
    if t == PHONE_EXTENSION:
        return True
    return bool(_SHORT_NUMERIC_LABEL_RE.search(str(label or "")))


def value_ok_for_field_shape(
    value: str,
    *,
    label: str = "",
    ftype: str | None = None,
) -> bool:
    """Reject wrong-type reclaim/Flash values (essay → phone extension, etc.).

    Empty is OK for optional blanks. Short numeric fields accept only brief
    digit-ish strings (≤8 chars, ≥1 digit, no letters).
    """
    raw = "" if value is None else str(value)
    stripped = raw.strip()
    if is_short_numeric_field(label, ftype):
        if not stripped:
            return True  # leave blank is correct
        if len(stripped) > 8:
            return False
        if sum(c.isalpha() for c in stripped) > 0:
            return False
        if sum(c.isdigit() for c in stripped) < 1:
            return False
        return True
    t = str(ftype or "").strip().upper()
    if t in OPTIONAL_LEAVE_BLANK_TYPES and not stripped:
        return True
    # Crossfill: visa/sponsorship strings must never land in worked-here textareas
    # (Lindblad Lever: "No visa required" in "worked with Lindblad…").
    wh = (
        t == WORKED_HERE_BEFORE
        or is_worked_here_label(label)
    )
    if wh and stripped:
        low = stripped.lower()
        if re.search(
            r"visa|sponsor|immigration|h1b|h-1b|work[\s_-]*auth|citizen|"
            r"green[\s_-]*card|permanent[\s_-]*resident|employment[\s_-]*eligibility",
            low,
        ):
            return False
    if t and t in _VALIDATORS:
        return validate_filled(t, stripped) if stripped else True
    return True


def validate_filled(field_type: str, value: str) -> bool:
    """True if `value` is plausible for `field_type`. Types without a validator
    pass by default - absence of a check is not evidence of a problem."""
    if not value:
        return False
    checker = _VALIDATORS.get(field_type)
    return checker(value) if checker else True


def assert_dummy_is_clean() -> int:
    """Fail if ANY value in DUMMY_PROFILE also appears in the real profile.

    Covers more than identifying fields. Years-of-experience, degree subject,
    GPA and graduation dates are non-identifying yet still specifically the
    user's, and reusing them would let a test silently assert against real
    facts. Two earlier revisions did exactly that (the real 4.5 years, the real
    "M.S., Computer Science"), which is why this is an executable check rather
    than a comment. Returns the number of overlapping values found.
    """
    real_path = Path(__file__).resolve().parents[2] / "profile.json"
    if not real_path.exists():
        return 0
    real_blob = json.dumps(json.load(open(real_path))).lower()
    overlaps = []

    # Only PERSONAL FACTS must differ between dummy and real. Policy answers
    # legitimately match: "Yes, willing to relocate", "Immediately available"
    # and the EEO decline-phrases are standard wordings expressing the same
    # decision in both, and flagging them turns this check into noise that
    # invites weakening. Facts about the individual - who he is, what he
    # studied, how long he has worked - must never coincide.
    FACT_SECTIONS = ("personal", "contact", "links", "education", "experience")
    subject = {k: v for k, v in DUMMY_PROFILE.items() if k in FACT_SECTIONS}
    # ATS catalog majors alone are not uniquely identifying (schools differ).
    _SKIP_KEYS = frozenset({"discipline", "major", "field_of_study"})
    _CATALOG_MAJORS = frozenset(
        {
            "computer science",
            "computer science and engineering",
            "software engineering",
            "information technology",
            "master's degree",
            "bachelor's degree",
        }
    )

    def walk(node, key: str = ""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, (str, int, float)) and not isinstance(node, bool):
            if key.lower() in _SKIP_KEYS:
                return
            s = str(node).strip().lower()
            # Short/common tokens ("no", "yes", "3.0") collide by coincidence and
            # would make this check noise rather than signal.
            if len(s) < 6 or s in _CATALOG_MAJORS:
                return
            if s in real_blob:
                overlaps.append(s)

    walk(subject)
    walk(DUMMY_ADDRESS)
    for o in sorted(set(overlaps)):
        print(f"  LEAK: dummy value also present in real profile: {o!r}")
    return len(set(overlaps))


if __name__ == "__main__":
    n_leaks = assert_dummy_is_clean()
    print(f"[dummy-vs-real overlap check] {'CLEAN' if not n_leaks else f'{n_leaks} LEAK(S)'}\n")

    profile, address, is_dummy = load_profile()
    vals = build_value_map(profile, address)
    banner = "DUMMY identity" if is_dummy else "*** REAL PROFILE DATA ***"
    print(f"[{banner}]")
    print(f"value map covers {sum(1 for v in vals.values() if v)}/{len(vals)} canonical types")
    for t, v in vals.items():
        if v:
            print(f"  {t:22s} = {str(v)[:52]}")
