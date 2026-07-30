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

import json
import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Canonical field types (analogous to Chromium's FieldType enum). Deliberately
# job-application-scoped - no credit card / IBAN / travel types, since a wrong
# match is worse than no match and every extra type widens the blast radius.
# ---------------------------------------------------------------------------
NAME_FIRST = "NAME_FIRST"
NAME_LAST = "NAME_LAST"
NAME_FULL = "NAME_FULL"
EMAIL = "EMAIL"
PHONE = "PHONE"
ADDRESS_LINE1 = "ADDRESS_LINE1"
ADDRESS_CITY = "ADDRESS_CITY"
ADDRESS_STATE = "ADDRESS_STATE"
ADDRESS_ZIP = "ADDRESS_ZIP"
ADDRESS_COUNTRY = "ADDRESS_COUNTRY"
LINKEDIN = "LINKEDIN"
GITHUB = "GITHUB"
PORTFOLIO = "PORTFOLIO"
RESUME_UPLOAD = "RESUME_UPLOAD"
COVER_LETTER = "COVER_LETTER"
WORK_AUTH = "WORK_AUTH"
SPONSORSHIP = "SPONSORSHIP"
GENDER = "GENDER"
RACE = "RACE"
HISPANIC = "HISPANIC"
VETERAN = "VETERAN"
DISABILITY = "DISABILITY"
YEARS_EXPERIENCE = "YEARS_EXPERIENCE"
SCHOOL = "SCHOOL"
DEGREE = "DEGREE"
SALARY_EXPECTED = "SALARY_EXPECTED"
SALARY_CURRENT = "SALARY_CURRENT"
NOTICE_PERIOD = "NOTICE_PERIOD"
RELOCATION = "RELOCATION"
AGE_18 = "AGE_18"
FELONY = "FELONY"
WORKED_HERE_BEFORE = "WORKED_HERE_BEFORE"
HOW_HEARD = "HOW_HEARD"
CURRENT_COMPANY = "CURRENT_COMPANY"
CURRENT_TITLE = "CURRENT_TITLE"
PASSWORD = "PASSWORD"
PASSWORD_CONFIRM = "PASSWORD_CONFIRM"
TERMS_CONSENT = "TERMS_CONSENT"
MARKETING_CONSENT = "MARKETING_CONSENT"

# ---------------------------------------------------------------------------
# LAYER 0: the HTML `autocomplete` attribute -> canonical type.
# Standard tokens only (WHATWG autofill field names). `off`/`on` carry no type
# information and are deliberately absent so they fall through to Layer 1.
# ---------------------------------------------------------------------------
AUTOCOMPLETE_MAP = {
    "given-name": NAME_FIRST,
    "additional-name": None,          # middle name - profile has no value for it
    "family-name": NAME_LAST,
    "name": NAME_FULL,
    "email": EMAIL,
    "tel": PHONE,
    "tel-national": PHONE,
    "street-address": ADDRESS_LINE1,
    "address-line1": ADDRESS_LINE1,
    "address-level2": ADDRESS_CITY,   # spec: city/town
    "address-level1": ADDRESS_STATE,  # spec: state/province
    "postal-code": ADDRESS_ZIP,
    "country": ADDRESS_COUNTRY,
    "country-name": ADDRESS_COUNTRY,
    "url": PORTFOLIO,
    "organization-title": None,       # current job title - not in profile
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
                       r"sms|text[\s_-]*message|receiv(e|ing)[\s_-]*(updates|communications|emails)|"
                       r"opt[\s_-]*in|subscribe",
    # "Check the box to confirm you wish to move forward with creating an
    # account" (a real Workday tenant, no mention of "terms" at all) was found
    # live, timing out on Create Account because this gate stayed unticked -
    # it reads as a generic instruction rather than the usual terms phrasing.
    TERMS_CONSENT: r"terms\s*(and|&)\s*conditions|terms\s*of\s*(use|service)|"
                   r"privacy\s*(policy|notice|statement)|i\s+agree|i\s+consent|"
                   r"read\s+and\s+(accept|agree|consent)|acknowledge\s+and\s+agree|"
                   r"data\s+processing\s+consent|"
                   r"confirm\s+you\s+wish\s+to\s+(move\s+forward|proceed|continue)|"
                   r"check\s+the\s+box\s+to\s+confirm",

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
    EMAIL: r"e[\s_-]*mail|email[\s_-]*address",
    PHONE: r"phone|mobile|telephone|\btel\b|cell|contact[\s_-]*number",

    # -- links: named platforms before the generic url catch-all -------------
    LINKEDIN: r"linked[\s_-]*in",
    GITHUB: r"git[\s_-]*hub",
    PORTFOLIO: r"portfolio|personal[\s_-]*(web)?site|website|\bur[li]\b|web[\s_-]*page",

    # -- address: zip/state/city before the generic street line --------------
    ADDRESS_ZIP: r"zip|postal[\s_-]*code|post[\s_-]*code|postcode",
    ADDRESS_STATE: r"\bstate\b|province|region|address[\s_-]*level[\s_-]*1",
    # `\blocation\b` MUST keep its word boundaries: "relocation" contains the
    # literal substring "location", so an unanchored form would capture every
    # "Are you willing to relocate?" question as an address field and fill a
    # street address into a yes/no. The boundary is what keeps them apart, since
    # RELOCATION is matched later in this dict.
    ADDRESS_CITY: r"\bcity\b|\btown\b|address[\s_-]*level[\s_-]*2|locality|\blocation\b|current[\s_-]*location",
    ADDRESS_COUNTRY: r"country",
    # `address line 2` was previously unmatchable: the old alternation anchored
    # on an optional "1" then end-of-string, so "Address line 2" fell through.
    ADDRESS_LINE1: r"street|address[\s_-]*(line)?[\s_-]*\d*$|^address$|mailing[\s_-]*address|home[\s_-]*address",

    # -- documents -----------------------------------------------------------
    # `file-input`/`fileupload` are real ids seen on styled drop-zone widgets
    # whose <input type=file> is visually hidden; the structural check in
    # classify_by_input_type() catches most, this covers the rest.
    RESUME_UPLOAD: r"resume|\bcv\b|curriculum[\s_-]*vitae|upload[\s_-]*(your[\s_-]*)?(resume|cv)|file[\s_-]*input|file[\s_-]*upload|attach[\s_-]*(file|resume)",
    COVER_LETTER: r"cover[\s_-]*letter|motivation[\s_-]*letter",

    # -- work authorization --------------------------------------------------
    # Sponsorship is checked before the broader authorization pattern: the
    # phrase "require sponsorship to work" contains "work", and the two demand
    # OPPOSITE answers (authorized=Yes but sponsorship=No), so collapsing them
    # would produce a confidently wrong answer rather than no answer.
    SPONSORSHIP: r"sponsor|visa[\s_-]*sponsor|require[\s_-]*sponsorship|need[\s_-]*sponsorship|h1b|h-1b",
    WORK_AUTH: r"work[\s_-]*authoriz|authoriz(ed|ation)[\s_-]*to[\s_-]*work|legally[\s_-]*(authorized|entitled)|right[\s_-]*to[\s_-]*work|eligible[\s_-]*to[\s_-]*work",

    # -- EEO / demographic ---------------------------------------------------
    HISPANIC: r"hispanic|latino|latinx",
    RACE: r"race|ethnic",
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

    YEARS_EXPERIENCE: r"years[\s_-]*of[\s_-]*(\w+[\s_-]+){0,2}experience|experience[\s_-]*(in[\s_-]*)?years|yrs[\s_-]*exp|total[\s_-]*experience|how[\s_-]*many[\s_-]*years",
    SCHOOL: r"school|universit|college|institution|alma[\s_-]*mater",
    # "Highest Level of education completed?" puts the words in the opposite
    # order, so an `education level` adjacency test misses it entirely.
    DEGREE: r"degree|qualification|education[\s_-]*level|level[\s_-]*of[\s_-]*education|highest[\s_-]*(level[\s_-]*of[\s_-]*)?education",

    # -- compensation: current before expected -------------------------------
    # "current salary" contains "salary"; testing expected first would capture
    # it and disclose the wrong number, which is a real-world harm, not just a
    # mis-fill. Order here is a correctness requirement.
    SALARY_CURRENT: r"current[\s_-]*(salary|compensation|pay)|present[\s_-]*salary|existing[\s_-]*salary",
    SALARY_EXPECTED: r"expected[\s_-]*(salary|compensation)|desired[\s_-]*(salary|compensation|pay)|salary[\s_-]*(expectation|requirement)|compensation[\s_-]*expectation|\bsalary\b",

    # -- logistics -----------------------------------------------------------
    # "how soon can you join us" was the single most common unmatched phrasing
    # (12 occurrences on one platform's template). `date.*available|available.*
    # (to[\s_-]*)?start` also covers camel/dotted ids like info.dateAvailableToStart.
    NOTICE_PERIOD: r"notice[\s_-]*period|availability|available[\s_-]*(to[\s_-]*)?start|date[\s_-]*available|start[\s_-]*date|when[\s_-]*can[\s_-]*you[\s_-]*(start|join)|how[\s_-]*soon|join[\s_-]*us|earliest[\s_-]*start",
    RELOCATION: r"relocat",

    # -- standard screening --------------------------------------------------
    AGE_18: r"18[\s_-]*(years|or[\s_-]*older)|at[\s_-]*least[\s_-]*18|age[\s_-]*18|over[\s_-]*18",
    FELONY: r"felony|convicted|criminal[\s_-]*(record|conviction)|background[\s_-]*check[\s_-]*consent",
    WORKED_HERE_BEFORE: r"worked[\s_-]*(here|for[\s_-]*us)|previously[\s_-]*employed|former[\s_-]*employee|relative[\s_-]*(employed|work)|family[\s_-]*member[\s_-]*(employed|work)",
    HOW_HEARD: r"how[\s_-]*did[\s_-]*you[\s_-]*hear|referral[\s_-]*source|source|where[\s_-]*did[\s_-]*you[\s_-]*(hear|find)",
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
    return AUTOCOMPLETE_MAP.get(token)


def classify_layer1(field: dict) -> str | None:
    """Regex heuristics over the field's descriptive attributes.

    Scans attribute-by-attribute in _SEARCH_ORDER (not one merged blob) so a
    strong signal in `name` always beats an incidental keyword in a
    `placeholder`, rather than both being flattened into the same haystack
    where whichever pattern is tested first happens to win.
    """
    for attr in _SEARCH_ORDER:
        blob = _text_blob(field, attr)
        if not blob:
            continue
        for ftype, rx in _COMPILED.items():
            if rx.search(blob):
                return ftype
    return None


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
# Default-on (`FASTFILL_REAL_PROFILE=1` opts out) because this module is
# development tooling: the failure mode of accidentally using real data is
# sending a real person's details to an employer, while the failure mode of
# accidentally using dummy data is a test that's obviously wrong. Defaulting to
# the safe side of that asymmetry is the whole point.
DUMMY_PROFILE = {
    "personal": {"full_name": "Test Dummy"},
    "contact": {"email": "randommail6969@gmail.com", "phone": "405-555-0100"},
    "links": {
        "github": "https://github.com/test-dummy-account",
        "linkedin": "https://www.linkedin.com/in/test-dummy-000000000",
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
    "education": {"degrees": [
        {"degree": "M.S., Example Studies", "school": "University of Alabama, Tuscaloosa",
         "graduation_date": "May 2019"},
        # GITAM (not University of Alabama) - matches the dummy resume PDF's
        # own B.S. entry exactly ("B.S., Computer Science and Engineering,
        # GITAM, Visakhapatnam, India"). The two degrees have genuinely
        # different schools in the resume text itself; using the same school
        # for both here would create a fresh cross-source mismatch the moment
        # anything reads this second entry, the same class of bug as the
        # Hoboken/NJ vs Springfield/IL address conflict above.
        {"degree": "B.S., Example Studies", "school": "GITAM, Visakhapatnam, India",
         "graduation_date": "May 2017"},
    ]},
    "experience": {"total_years_of_experience": 3.0},
    "eeo_demographic": {
        "gender": "Decline to self identify",
        "hispanic_or_latino": "Decline to self identify",
        "race_ethnicity": "Decline to self identify",
        "veteran_status": "Decline to self identify",
        "disability_status": "Decline to self identify",
    },
    "address": {"country": "United States"},
    "work_preferences": {"relocation": "Yes, willing to relocate",
                         "notice_period": "Immediately available"},
    "standard_screening_answers": {"age_18_or_older": True,
                                   "worked_here_before_or_relative_employed": "No",
                                   "felony_conviction": "No"},
    # NEVER "LinkedIn" here - LinkedIn is reserved exclusively for the LinkedIn
    # profile-URL field and must never answer "how did you hear about this job".
    # Priority is Internet job board, then Indeed.
    "custom_question_answers": {"how_did_you_hear_about_this_job": "Internet job board"},
    # Throwaway credential for test-account creation only. Deliberately literal
    # and obviously fake rather than randomly generated, so it is reproducible
    # and can never be mistaken for a real secret. Satisfies the common
    # complexity rule (upper, lower, digit, symbol, 12+ chars) that ATS
    # registration forms enforce.
    "account": {"password": "TestDummy!2026x"},
}
DUMMY_ADDRESS = "100 Example Ave, Apt 1A, Springfield, IL 62701"

# The dummy resume PDF (compiled from fixtures/dummy_resume_de.tex via tectonic)
# used by every live test harness that needs an actual file to upload.
DUMMY_PDF = Path(__file__).resolve().parent / "fixtures" / "dummy_resume_de.pdf"

# Gmail `+`-alias state: moved here from the now-retired fill_form.py Playwright
# walker (superseded by hybrid_fill.py, which drives Skyvern instead of
# reimplementing a browser agent's perception loop by hand) since these are
# pure, browser-independent functions with no reason to live inside a walker.
ALIAS_STATE_FILE = Path(__file__).resolve().parent / "alias_state.json"


def make_alias_email(base_email: str, n: int) -> str:
    """Gmail `+`-alias variant of base_email. n=0 returns base_email unchanged.

    All variants deliver to the SAME real inbox the user controls - this is
    the whole point versus a sequential randommail1@/randommail2@ scheme,
    which the user was right to reject: those low-entropy addresses almost
    certainly belong to real strangers, and creating accounts against them
    would send unsuspecting people unsolicited verification email. A `+` alias
    only ever reaches a mailbox the user already gave us.
    """
    if n <= 0 or "@" not in base_email:
        return base_email
    local, _, domain = base_email.partition("@")
    return f"{local}+{n}@{domain}"


def load_next_alias_index(key: str) -> int:
    if not ALIAS_STATE_FILE.exists():
        return 0
    try:
        return json.loads(ALIAS_STATE_FILE.read_text()).get(key, 0)
    except Exception:
        return 0


def save_next_alias_index(key: str, n: int) -> None:
    state = {}
    if ALIAS_STATE_FILE.exists():
        try:
            state = json.loads(ALIAS_STATE_FILE.read_text())
        except Exception:
            pass
    state[key] = n
    ALIAS_STATE_FILE.write_text(json.dumps(state, indent=1))


def load_profile(force_real: bool = False) -> tuple[dict, str, bool]:
    """Return (profile, address_text, is_dummy).

    Dummy by default. Real data requires an explicit FASTFILL_REAL_PROFILE=1,
    so using it is always a deliberate act rather than an oversight.
    """
    if force_real or os.environ.get("FASTFILL_REAL_PROFILE") == "1":
        real = json.load(open(Path(__file__).resolve().parents[2] / "profile.json"))
        return real, "", False
    return DUMMY_PROFILE, DUMMY_ADDRESS, True


def build_value_map(profile: dict, address_text: str = "") -> dict:
    full = profile.get("personal", {}).get("full_name", "")
    first, _, last = full.partition(" ")
    degrees = profile.get("education", {}).get("degrees") or [{}]
    eeo = profile.get("eeo_demographic", {})
    screening = profile.get("standard_screening_answers", {})

    # City/state, parsed from the SAME address_text ADDRESS_ZIP already reads
    # below - a real bug found live (2026-07-30): ADDRESS_CITY and
    # ADDRESS_STATE were declared as detectable field TYPES (Layer 1 correctly
    # recognized a "City" field as one) but never actually given a VALUE here,
    # so every City/State field fell through to Layer 2 with zero mapping to
    # go on. The model then had to invent a city each time with no source of
    # truth, and it inconsistently picked one ("Hoboken, NJ") that contradicts
    # the zip we DO confidently supply (62701, which is really Springfield,
    # IL) - a real Workday posting's own validation rejected the mismatch
    # outright, and a separate Ashby posting watchdog-cancelled after 4
    # repeated failed attempts to input the same self-contradicting value.
    # Parsing all three (city/state/zip) from ONE string is what the existing
    # zip-parsing comment below already argues for: exactly one source of
    # truth, so they can never drift apart again.
    _csz = re.search(r",\s*([A-Za-z .'\-]+?),\s*([A-Z]{2})\s+\d{5}", address_text) if address_text else None

    return {
        NAME_FIRST: first,
        NAME_LAST: last,
        NAME_FULL: full,
        EMAIL: profile.get("contact", {}).get("email", ""),
        PHONE: profile.get("contact", {}).get("phone", ""),
        LINKEDIN: profile.get("links", {}).get("linkedin", ""),
        GITHUB: profile.get("links", {}).get("github", ""),
        WORK_AUTH: "Yes",
        SPONSORSHIP: "No",
        GENDER: eeo.get("gender", ""),
        HISPANIC: eeo.get("hispanic_or_latino", ""),
        RACE: eeo.get("race_ethnicity", ""),
        VETERAN: eeo.get("veteran_status", ""),
        DISABILITY: eeo.get("disability_status", ""),
        YEARS_EXPERIENCE: str(profile.get("experience", {}).get("total_years_of_experience", "")),
        SCHOOL: degrees[0].get("school", ""),
        DEGREE: degrees[0].get("degree", ""),
        RELOCATION: profile.get("work_preferences", {}).get("relocation", ""),
        NOTICE_PERIOD: profile.get("work_preferences", {}).get("notice_period", ""),
        AGE_18: "Yes" if screening.get("age_18_or_older") else "No",
        FELONY: screening.get("felony_conviction", "No"),
        WORKED_HERE_BEFORE: screening.get("worked_here_before_or_relative_employed", "No"),
        HOW_HEARD: profile.get("custom_question_answers", {}).get("how_did_you_hear_about_this_job", ""),
        PASSWORD: profile.get("account", {}).get("password", ""),
        PASSWORD_CONFIRM: profile.get("account", {}).get("password", ""),
        ADDRESS_LINE1: address_text,
        # Found live: no source ever supplied a bare ZIP, so a required
        # "Postal Code" field on a real Workday step sat empty and stopped a
        # multi-page walk cold. The resume gives "City, ST" but never a zip
        # (real resumes don't print one), so it has to come from the one
        # place a full address string exists - parsed out here rather than
        # stored as a separate profile field, so there is exactly one source
        # of truth for the address and the two can never drift apart.
        ADDRESS_ZIP: (re.search(r"\b(\d{5})(-\d{4})?\b", address_text).group(1)
                     if address_text and re.search(r"\b(\d{5})(-\d{4})?\b", address_text) else ""),
        ADDRESS_CITY: _csz.group(1) if _csz else "",
        ADDRESS_STATE: _csz.group(2) if _csz else "",
        ADDRESS_COUNTRY: profile.get("address", {}).get("country", "United States"),
        # Deliberately absent, and that absence is the correct behaviour:
        #   SALARY_EXPECTED / SALARY_CURRENT - profile stores a *rule* keyed to
        #     the posting's stated range, not a number; resolving it needs the
        #     job description, so it belongs to Layer 2.
        #   COVER_LETTER - free text, must never be fabricated.
        #   RESUME_UPLOAD - a file path handled by the upload step, not typed.
        # Leaving them unmapped routes them to Layer 2 rather than letting a
        # confident-but-wrong value through.
    }


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
    LINKEDIN: lambda v: "linkedin.com" in v.lower(),
    GITHUB: lambda v: "github.com" in v.lower(),
    ADDRESS_ZIP: lambda v: any(c.isdigit() for c in v),
}


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

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, (str, int, float)) and not isinstance(node, bool):
            s = str(node).strip().lower()
            # Short/common tokens ("no", "yes", "3.0") collide by coincidence and
            # would make this check noise rather than signal.
            if len(s) >= 6 and s in real_blob:
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
