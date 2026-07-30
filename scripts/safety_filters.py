"""Single source of truth for which real job postings are safe to use in live
testing. Never hand-retype this filter inline in a selection script again -
that's exactly how a real posting (ClearEdge, description: "solving some of
the DoD's most complex technical challenges") got tested live on 2026-07-30
despite the standing rule against defense/clearance-gated postings. The
filter used that day had silently dropped "dod"/"department of defense"/
"federal contractor"/"ts/sci" compared to an earlier, more complete version -
a regression nobody would have caught by eye, because there was no single
place enforcing it stayed complete.
"""
import re

EXCLUDED_TERMS = re.compile(
    r"\b(defense|aerospace|clearance|classified|secret|department of defense|"
    r"dod|federal contractor|top secret|ts/sci|military)\b",
    re.I,
)

EXCLUDED_COMPANIES = re.compile(
    r"\b(lockheed|raytheon|northrop|boeing defense|general dynamics|l3harris|"
    r"bae systems|leidos|booz allen|saic|caci|palantir|anduril)\b",
    re.I,
)

# Not a bug to fix - a scope boundary. Found live (2026-07-30): a Crunchyroll
# Greenhouse posting pulled via the live discovery API turned out to be a
# Tokyo-based, Japanese-language role ("シニア・プロデューサー..."). The whole
# system - DUMMY_PROFILE, LAYER3_RULES, every Layer 0/1 regex pattern - is
# English-only, so a non-English application form was never going to work
# regardless of any browser-level issue; testing it just produced a
# confusing "browser crashed" signal that looked like a real bug but wasn't
# one. Non-ASCII character DENSITY in title/description (not just presence -
# a stray accented name or "café" shouldn't trip this) is the language-
# agnostic signal: a title that's mostly non-Latin script is reliably a
# non-English posting regardless of which specific language.
NON_ASCII_DENSITY_THRESHOLD = 0.15
# Known gap, not silently swept under the rug: this only catches non-Latin
# scripts (Japanese/Chinese/Korean/Arabic/Cyrillic/etc). A French or German
# posting written in the Latin alphabet with just occasional accents (e.g.
# "Développeur Full Stack") has LOW non-ASCII density and passes through
# unfiltered even though its application form is very unlikely to be in
# English. Catching that class properly needs real language detection, not
# a character-density heuristic - not implemented here.


def _non_ascii_density(text: str) -> float:
    if not text:
        return 0.0
    non_ascii = sum(1 for c in text if ord(c) > 127)
    return non_ascii / len(text)


# Not a bug either - the same scope-boundary reasoning as the language filter,
# found the same way. Found live (2026-07-30): a Capco Greenhouse posting
# terminated almost immediately with "several required fields lack known
# mapping values" - which looked like a possible over-eager Rule 11
# termination until the real page was checked directly: the form asks for
# "CCTC/ECTC in LPA" (Indian salary terms), relocating to "Electronic City-
# Bangalore/Pune", and jobs.json's own `location` field for this posting was
# literally 'India'. DUMMY_PROFILE is entirely US-shaped (US address, US
# phone format, "United States" work authorization) - an India-based
# posting's form was never answerable regardless of any bug, same failure
# shape as the Tokyo posting. jobs.json's `location` field is the cheap,
# already-available signal for this, unlike language which needed the title.
NON_US_LOCATION_TERMS = re.compile(
    r"\b(india|japan|china|singapore|philippines|germany|france|poland|"
    r"mexico|brazil|australia|vietnam|indonesia|malaysia|thailand|"
    r"bangalore|mumbai|delhi|hyderabad|pune|chennai|tokyo|shanghai|"
    r"beijing|manila|jakarta)\b",
    re.I,
)


def is_safe_for_testing(job: dict) -> bool:
    """job: a dict with at least 'company', 'title', 'job_description',
    'location' keys (the jobs.json shape). True if this posting is safe to
    run live tests against - no defense/aerospace/clearance signal in
    company, title, or description text, the posting is in English (this
    system's only supported language), and the posting's location is
    US-based (DUMMY_PROFILE's only supported identity shape).

    Note this only screens what jobs.json's own stored text says. A
    clearance requirement - or a location requirement - that ONLY appears on
    the live application form itself (never scraped into job_description or
    a mismatched/missing `location` field) can still slip through - that's a
    real, acknowledged gap, not something this filter claims to close. The
    system's own Layer 3 termination-on-unmappable-field behavior is the
    real backstop for that case, not this filter.
    """
    text = f"{job.get('company', '')} {job.get('title', '')} {job.get('job_description', '')}"
    if EXCLUDED_TERMS.search(text):
        return False
    if EXCLUDED_COMPANIES.search(job.get("company", "")):
        return False
    if _non_ascii_density(job.get("title", "")) > NON_ASCII_DENSITY_THRESHOLD:
        return False
    location = job.get("location", "") or ""
    if NON_US_LOCATION_TERMS.search(location) and "united states" not in location.lower() and "usa" not in location.lower():
        return False
    return True
