"""Discovery-time location + seniority + clearance + YOE + citizenship filters.

Used by dedup_listings.py (block at qualify) and kept in sync with
dashboard/static/app.js (hide untouched discovered jobs
already in jobs.json). Prefer extending this module over adding parallel
filters.

Policy:
  - Location: keep US-based OR undetermined; drop only when clearly non-US.
    Treat worldwide/global/EMEA/APAC/etc. as clearly non-US (not a US
    option). Dotted "U.S." / "U.S.A." count as US signals. ATS strings that
    end in an ISO-3166 alpha-2 country ("Bengaluru, KA, in") resolve on that
    tail first, since half those codes are also US state abbreviations.
  - Title: keep junior/mid/senior/associate/entry IC roles; drop
    leadership / above-senior ladder titles (staff, principal, lead,
    architect, …). Plain "Senior …" titles are allowed, but
    "Senior Architect" (and any other * Architect) is dropped.
  - Clearance / intel: drop when title, company, location, description, or
    URL clearly requires or emphasizes security clearance (TS/SCI,
    polygraph, Secret/Q/L clearance, “clearance required”, eligibility to
    obtain/maintain Secret or TS even if “desirable”, …) OR when the
    employer is an identifiable US intel / IC agency (NSA, CIA, DIA, NGA,
    …). Preferred-only Secret with no obtain language stays. Do NOT drop
    civilian product “Security Engineer” roles at normal tech companies
    that lack clearance-requirement language.
  - YOE: drop when an explicit minimum required experience is **> 6**
    (≥7). Keep ≤6, ranges with lower bound ≤6, no number, undetermined.
  - Citizenship / green card / U.S. Person: drop on explicit citizen-only
    or green-card / permanent-resident **required**, recruiter slang
    (USC/GC, “Visa: USC and GC only”), **U.S. Person required**, ITAR, or
    export-controlled data. Mere “U.S. Person preferred” without required
    stays. Bare “authorized to work”, EEO fluff, and **no visa
    sponsorship** / “unable to sponsor” stay (those are not USC/GC-only).
    Jobs that clearly **do** sponsor H-1B/visas also stay unless a
    citizen/GC/US-Person hard requirement is present.
  - Staffing agencies: drop when the *company name* matches known
    agency/staffing tokens (Insight Global, Randstad, …). JD boilerplate
    about not accepting staffing submissions is ignored.
  - Employment type: drop when ``job_type`` is an explicit non-full-time
    label (contract, C2C, part-time, temp, intern, volunteer, …). Empty /
    unknown / fulltime stays.
  - Work mode (remote/hybrid/onsite): detection for UI only — never prune.
  - Salary: extract for UI display only — never prune. Strict clear
    ranges/figures; softer fallback stamped separately (UI ``~``).
"""
from __future__ import annotations

import os
import re
import unicodedata

# Word-boundary patterns so "staff" does not match "staffing", "lead" does
# not match "leadership", and short tokens like "vp"/"cto" stay precise.
# Mirror the same semantic set in dashboard/static/app.js.
SENIORITY_EXCLUDE_RE = re.compile(
    r"\b("
    r"principal|(?<!technical\s)staff|lead|manager|mgr|director|vp|svp|evp|"
    r"vice[\s-]+president|head\s+of|chief|founder|partner|fellow|"
    r"distinguished|supervisor|architect|cto|ceo|cpo|cfo|coo|cio"
    r")\b",
    re.I,
)

# Human-readable list (docs / JS sync). Matching uses SENIORITY_EXCLUDE_RE.
SENIORITY_EXCLUDE_HINTS = [
    "principal",
    "staff",
    "lead",
    "manager",
    "mgr",
    "director",
    "vp",
    "svp",
    "evp",
    "vice president",
    "head of",
    "chief",
    "founder",
    "partner",
    "fellow",
    "distinguished",
    "supervisor",
    "architect",
    "cto",
    "ceo",
    "cpo",
    "cfo",
    "coo",
    "cio",
]

# Clear non-US geography. Ambiguous bare US-namesake towns that are rare in
# tech postings (London, Paris, Melbourne, Berlin) are treated as non-US —
# multi-location strings that also name a US city/country still keep via
# US_LOCATION_RE. Accented forms are folded before matching (México → mexico).
NON_US_LOCATION_RE = re.compile(
    r"\b("
    # Countries / regions
    r"india|japan|china|singapore|philippines|germany|france|poland|"
    r"mexico|brazil|australia|vietnam|indonesia|malaysia|thailand|"
    r"canada|united\s+kingdom|\buk\b|england|scotland|ireland|wales|"
    r"netherlands|spain|italy|sweden|norway|denmark|switzerland|belgium|"
    r"portugal|austria|finland|israel|south\s+korea|\bkorea\b|taiwan|"
    r"hong\s+kong|dubai|u\.?a\.?e\.?|united\s+arab\s+emirates|"
    r"new\s+zealand|argentina|colombia|chile|peru|ecuador|bolivia|"
    r"uruguay|paraguay|venezuela|guatemala|honduras|nicaragua|"
    r"costa\s+rica|panama|dominican\s+republic|"
    r"saudi\s+arabia|\bksa\b|qatar|kuwait|bahrain|oman|jordan|"
    r"lebanon|egypt|morocco|tunisia|nigeria|kenya|ghana|ethiopia|"
    r"south\s+africa|ukraine|romania|serbia|slovakia|slovenia|"
    r"croatia|hungary|czech(\s+republic)?|\bczechia\b|bulgaria|"
    r"lithuania|latvia|estonia|greece|turkey|turkiye|"
    r"pakistan|bangladesh|sri\s+lanka|nepal|cambodia|myanmar|"
    r"armenia|azerbaijan|kazakhstan|uzbekistan|tajikistan|"
    r"north\s+macedonia|macedonia|belarus|moldova|"
    r"europe|european(\s+union)?|emea|apac|latam|\basia\b|africa|middle\s+east|"
    r"worldwide|\bglobal\b|"
    # Canadian provinces (tech postings rarely mean Ontario, CA)
    r"ontario|quebec|alberta|manitoba|saskatchewan|"
    r"british\s+columbia|nova\s+scotia|new\s+brunswick|"
    r"newfoundland|prince\s+edward|"
    # Indian states / union territories (no US-state collisions)
    r"karnataka|telangana|maharashtra|tamil\s+nadu|kerala|gujarat|"
    r"haryana|uttar\s+pradesh|west\s+bengal|andhra\s+pradesh|"
    r"rajasthan|madhya\s+pradesh|odisha|assam|jharkhand|"
    # Major non-US cities
    r"bangalore|bengaluru|mumbai|delhi|hyderabad|pune|chennai|"
    r"kolkata|gurgaon|gurugram|noida|ahmedabad|jaipur|coimbatore|"
    r"kochi|thiruvananthapuram|trivandrum|indore|bhubaneswar|"
    r"vadodara|nagpur|mysuru|visakhapatnam|lucknow|chandigarh|"
    r"kuala\s+lumpur|penang|bangkok|hanoi|ho\s+chi\s+minh|"
    r"istanbul|athens|zagreb|gdansk|wroclaw|"
    r"tokyo|osaka|shanghai|beijing|shenzhen|manila|jakarta|"
    r"toronto|vancouver|montreal|ottawa|calgary|edmonton|"
    r"kitchener|kitchener-waterloo|mississauga|winnipeg|halifax|"
    r"london|paris|munich|berlin|amsterdam|dublin|zurich|geneva|"
    r"stockholm|copenhagen|oslo|helsinki|lisbon|madrid|barcelona|"
    r"rome|milan|prague|budapest|vienna|brussels|warsaw|krakow|"
    r"bucharest|sofia|belgrade|bratislava|vilnius|tallinn|riga|"
    r"edinburgh|glasgow|"
    r"melbourne|sydney|brisbane|perth|adelaide|auckland|wellington|"
    r"seoul|taipei|tel\s+aviv|jerusalem|haifa|"
    r"sao\s+paulo|rio\s+de\s+janeiro|bogota|medellin|santiago|"
    r"buenos\s+aires|lima|quito|montevideo|"
    r"mexico\s+city|ciudad\s+de\s+mexico|guadalajara|monterrey|"
    r"dubai|abu\s+dhabi|doha|riyadh|jeddah|"
    r"cape\s+town|johannesburg|lagos|nairobi|"
    r"almaty|astana|nur-sultan|san\s+salvador|"
    r"stuttgart|frankfurt|hamburg|cologne|dusseldorf|"
    r"lyon|marseille|toulouse|lille|"
    # ISO-ish country tokens often seen in ATS dumps
    r"\bgbr\b|\bcan\b|\bind\b|\baus\b|\bdeu\b|\bfra\b|\bnld\b|"
    r"\bsgp\b|\birl\b|\bnzl\b|\bpol\b|\bmex\b|\bbra\b|\besp\b|"
    r"\bita\b|\bswe\b|\bnor\b|\bdnk\b|\bche\b|\bbel\b|\bprt\b|"
    r"\baut\b|\bfin\b|\bisr\b|\bkor\b|\btwn\b|\bphl\b|\bare\b|"
    r"\brou\b|\buae\b|\bsau\b|\bqat\b"
    r")\b",
    re.I,
)

# Positive US signals — if present, keep even when a non-US term also appears
# (multi-location postings that include a US option). Split into a "strong"
# half (explicit country / state / city names) and the bare ", XX" state
# abbreviation half: the abbreviation half must be ignored once an ISO-2
# country tail has been resolved, since ATS region codes collide with it
# ("Chennai, TN, in" — TN is Tamil Nadu, not Tennessee).
US_LOCATION_STRONG_RE = re.compile(
    r"\b("
    # Match "U.S." / "U.S.A." without a trailing \\b after the period
    # (`.` is non-word, so \\b after `u\\.s\\.` never fires and dropped
    # multi-location strings like "U.S. / Canada").
    r"united\s+states|u\.s\.a\.?|u\.s(?!\w)|\busa\b|\bus\b|"
    r"remote[,\s/-]*us|us[,\s/-]*remote|us[-\s]?based|us[-\s]?only|"
    r"alabama|alaska|arizona|arkansas|california|colorado|connecticut|"
    r"delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|"
    r"kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|"
    r"minnesota|mississippi|missouri|montana|nebraska|nevada|"
    r"new\s+hampshire|new\s+jersey|new\s+mexico|new\s+york|"
    r"north\s+carolina|north\s+dakota|ohio|oklahoma|oregon|pennsylvania|"
    r"rhode\s+island|south\s+carolina|south\s+dakota|tennessee|texas|"
    r"utah|vermont|virginia|washington|west\s+virginia|wisconsin|wyoming|"
    r"district\s+of\s+columbia|"
    r"san\s+francisco|seattle|austin|boston|chicago|denver|"
    r"atlanta|dallas|houston|miami|phoenix|portland|salt\s+lake|"
    r"los\s+angeles|san\s+diego|san\s+jose|palo\s+alto|mountain\s+view|"
    r"sunnyvale|redmond|bellevue|cupertino|menlo\s+park|foster\s+city|"
    r"oakland|irvine|raleigh|durham|charlotte|nashville|minneapolis|"
    r"pittsburgh|philadelphia|washington,\s*dc|"
    r"new\s+york\s+city|\bnyc\b|bay\s+area|silicon\s+valley"
    r")\b",
    re.I,
)

# Single source of truth for US state / DC abbreviations. Both the
# comma-prefixed abbreviation regex below and the ISO-2 country-tail
# US_STATE_ABBREVS set (further down) are built from this ordered tuple, so a
# code can never be added to one form and missed in the other.
US_STATE_ABBREV_CODES = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
)
# US territories that also appear as ISO-3166 alpha-2 codes. Only the
# country-tail set needs these — the comma-prefixed abbreviation regex keeps
# the states+DC list to preserve its exact matching behavior.
US_TERRITORY_ISO2_CODES = ("PR", "VI", "GU", "AS", "MP")

# Comma-prefixed US state / DC abbreviations ("Rome, NY", "Paris, TX").
# Bare city names that are also non-US hubs still drop without this.
US_STATE_ABBREV_RE = re.compile(
    r",\s*(?:" + "|".join(US_STATE_ABBREV_CODES) + r")\b",
    re.I,
)

US_LOCATION_RE = re.compile(
    rf"(?:{US_LOCATION_STRONG_RE.pattern}|{US_STATE_ABBREV_RE.pattern})",
    re.I,
)

# ATS location dumps (SmartRecruiters, Workday, …) append an ISO-3166 alpha-2
# country to the tail: "Bengaluru, KA, in" / "Milpitas, CA, us". Half of those
# codes collide with US state abbreviations (IN=India/Indiana, DE, CA, MD, …),
# so the tail is resolved explicitly before the state-abbreviation US signal
# runs — otherwise ", in" reads as Indiana and vetoes the non-US decision.
US_STATE_ABBREVS = set(US_STATE_ABBREV_CODES) | set(US_TERRITORY_ISO2_CODES)

# Non-US ISO-3166 alpha-2 codes seen in ATS location tails. "us" is handled
# separately as a positive US signal; codes not listed here fall through to
# the normal city/country matching.
NON_US_ISO2_CODES = {
    "ae", "ar", "at", "au", "bd", "be", "bg", "bh", "br", "by", "ca", "ch",
    "cl", "cn", "co", "cr", "cz", "de", "dk", "do", "eg", "es", "fi", "fr",
    "gb", "gr", "gt", "hk", "hr", "hu", "id", "ie", "il", "in", "it", "jo",
    "jp", "ke", "kr", "kw", "kz", "lk", "lt", "lu", "lv", "ma", "mg", "mx", "my",
    "ng", "nl", "no", "nz", "pa", "pe", "ph", "pk", "pl", "pt", "qa", "ro", "rs",
    "ru", "sa", "se", "sg", "si", "sk", "sv", "th", "tr", "tw", "ua", "uk", "uy",
    "ve", "vn", "za",
}

_ISO2_TOKEN_RE = re.compile(r"^[A-Za-z]{2}$")

# ---------------------------------------------------------------------------
# Region model (multi-region discovery: US default, India opt-in)
#
# US discovery stays the default; India is opt-in. The discovery gate keeps a
# listing when its location matches ANY enabled region:
#   - "us"    → US-based OR undetermined (today's behavior via
#               is_clearly_non_us_location).
#   - "india" → clearly India (cities / states / "India" / ISO ", in" tail)
#               OR remote-India patterns ("Remote - India", "WFH India",
#               "Anywhere in India"). Bare "Remote" alone is NOT India.
# Keep these heuristics in sync with dashboard/static/app.js.
# ---------------------------------------------------------------------------

VALID_REGIONS = ("us", "india")
DEFAULT_REGIONS: tuple[str, ...] = ("us",)
# Env var read by discovery subprocesses (set by dashboard/server.py before
# spawning scout / scrape_ats / scrape_builtin / dedup_listings /
# write_discovered_jobs). Comma-separated, e.g. "us" or "us,india".
DISCOVERY_REGIONS_ENV = "JOBHUNTER_DISCOVERY_REGIONS"

# Clear India geography (cities / states / country). Subset of
# NON_US_LOCATION_RE — no US-state collisions. "ncr" = Delhi NCR.
# Token lists live here as data; INDIA_LOCATION_RE is built from them so a new
# city/state is added in one place. Each token is a regex fragment (e.g.
# r"tamil\s+nadu") joined with "|"; order is preserved to keep the compiled
# pattern identical to the hand-written original.
INDIA_COUNTRY_TOKENS = ("india", "bharat")
INDIA_STATE_TOKENS = (
    r"karnataka", r"telangana", r"maharashtra", r"tamil\s+nadu", r"kerala",
    r"gujarat", r"haryana", r"uttar\s+pradesh", r"west\s+bengal",
    r"andhra\s+pradesh", r"rajasthan", r"madhya\s+pradesh", r"odisha",
    r"assam", r"jharkhand", r"punjab", r"bihar", r"chhattisgarh",
    r"uttarakhand", r"goa",
)
INDIA_CITY_TOKENS = (
    r"bangalore", r"bengaluru", r"mumbai", r"bombay", r"delhi", r"new\s+delhi",
    r"hyderabad", r"pune", r"chennai", r"madras", r"kolkata", r"calcutta",
    r"gurgaon", r"gurugram", r"noida", r"ghaziabad", r"ahmedabad", r"jaipur",
    r"coimbatore", r"kochi", r"cochin", r"thiruvananthapuram", r"trivandrum",
    r"indore", r"bhubaneswar", r"vadodara", r"nagpur", r"mysuru", r"mysore",
    r"visakhapatnam", r"vizag", r"lucknow", r"chandigarh", r"surat", r"nashik",
    r"thane",
)
# "\bncr\b" (Delhi NCR) keeps its own word boundaries inside the group.
INDIA_LOCATION_RE = re.compile(
    r"\b("
    + "|".join((*INDIA_COUNTRY_TOKENS, *INDIA_STATE_TOKENS, *INDIA_CITY_TOKENS))
    + r"|\bncr\b"
    + r")\b",
    re.I,
)

# Remote / WFH that explicitly accepts India.
INDIA_REMOTE_RE = re.compile(
    r"("
    r"remote[,\s/\-]*india|india[,\s/\-]*remote|"
    r"india\s*\(\s*remote\s*\)|remote\s*\(\s*india\s*\)|"
    r"(?:wfh|work\s+from\s+home)[,\s/\-]*india|"
    r"anywhere\s+in\s+india|pan[\s\-]*india|across\s+india"
    r")",
    re.I,
)

# Georgia is ambiguous with the US state. A named Georgian city without an
# explicit US cue resolves it to the country; bare "Georgia" remains US.
GEORGIA_COUNTRY_CITY_RE = re.compile(r"\b(?:tbilisi|batumi)\b", re.I)


def is_india_location(location: str | None) -> bool:
    """True when the location clearly indicates India or remote-India.

    'Bengaluru' / 'Mumbai, India' / 'Karnataka' → True.
    'Bengaluru, KA, in' → True via the ISO-2 country tail.
    'Remote - India' / 'WFH, India' / 'Anywhere in India' → True.
    Bare 'Remote' / 'WFH' / 'Indianapolis, IN' → False (not clearly India).
    """
    loc = _fold_accents(str(location or "")).strip()
    if not loc:
        return False
    tail, head_parts = _location_tail_country(loc)
    if tail and tail.lower() == "in":
        head = ", ".join(head_parts)
        # ", IN" (uppercase) reads as Indiana; only the lowercase ATS spelling
        # with corroboration (extra segment or India/non-US token) is decisive.
        decisive = tail.islower() and (
            len(head_parts) >= 2
            or NON_US_LOCATION_RE.search(head)
            or INDIA_LOCATION_RE.search(head)
        )
        if decisive and not US_LOCATION_STRONG_RE.search(head):
            return True
    if INDIA_REMOTE_RE.search(loc):
        return True
    if INDIA_LOCATION_RE.search(loc):
        return True
    return False


def normalize_regions(regions=None) -> tuple[str, ...]:
    """Coerce a region spec into an ordered tuple of valid region ids.

    ``None`` → resolve from the ``JOBHUNTER_DISCOVERY_REGIONS`` env var, then
    fall back to ``DEFAULT_REGIONS`` (US-only). Accepts a comma string or any
    iterable of ids. Unknown ids are dropped; order follows VALID_REGIONS.
    """
    if regions is None:
        return enabled_regions_from_env()
    if isinstance(regions, str):
        raw = [r.strip().lower() for r in regions.split(",")]
    else:
        raw = [str(r).strip().lower() for r in regions]
    picked = {r for r in raw if r in VALID_REGIONS}
    ordered = tuple(r for r in VALID_REGIONS if r in picked)
    return ordered


def enabled_regions_from_env(default: tuple[str, ...] = DEFAULT_REGIONS) -> tuple[str, ...]:
    """Enabled regions from the discovery env var, or ``default`` if unset."""
    raw = os.environ.get(DISCOVERY_REGIONS_ENV, "")
    picked = {r.strip().lower() for r in raw.split(",") if r.strip()}
    picked &= set(VALID_REGIONS)
    ordered = tuple(r for r in VALID_REGIONS if r in picked)
    return ordered if ordered else tuple(default)


def location_matches_regions(location: str | None, regions=None) -> bool:
    """True when ``location`` is kept under the enabled regions.

    - ``us`` enabled → keep US-based or undetermined (not clearly non-US).
    - ``india`` enabled → keep clearly-India / remote-India.
    Multiple regions keep on the union. Empty region set keeps nothing.
    """
    regs = normalize_regions(regions)
    if not regs:
        return False
    if "us" in regs and not is_clearly_non_us_location(location):
        return True
    if "india" in regs and is_india_location(location):
        return True
    return False


def region_for_location(location: str | None, regions=None) -> str:
    """Best-effort region tag for UI/filtering: 'india' | 'us' | 'unknown'.

    India takes precedence when clearly India; else US when a US signal is
    present or the location is undetermined; else 'unknown'.
    """
    if is_india_location(location):
        return "india"
    loc = str(location or "").strip()
    if not loc:
        return "unknown"
    if not is_clearly_non_us_location(location):
        # Undetermined or explicit US signal — treat US-side as 'us' only when
        # there is a positive US cue; pure-undetermined stays 'unknown'.
        if US_LOCATION_RE.search(_fold_accents(loc)):
            return "us"
        return "unknown"
    return "unknown"


def _location_tail_country(loc: str) -> tuple[str | None, list[str]]:
    """Split "City, Region, cc" into (country code | None, leading segments).

    Returns the raw trailing token (case preserved) only when it looks like a
    bare 2-letter code; everything else keeps the whole string as segments.
    """
    parts = [p.strip() for p in loc.split(",")]
    if len(parts) < 2:
        return None, parts
    tail = parts[-1]
    if not _ISO2_TOKEN_RE.match(tail):
        return None, parts
    return tail, parts[:-1]

# ---------------------------------------------------------------------------
# Security clearance / US intel-agency filters
# Mirror the same patterns in dashboard/static/app.js.
# Prefer requirement language over company brand guesses for contractors;
# intel *agencies* are dropped by company/URL even without the word
# "clearance" (NSA postings often omit it in the scraped blurb).
# ---------------------------------------------------------------------------

# Explicit ATS "clearance required: No/None" — strip before matching so
# "CLEARANCE REQUIRED FOR START: No", "Clearance Required: None", and
# "Clearance Not Required" keep unless another positive signal remains
# (e.g. CLEARANCE TYPE: Secret). Word-order negation must be consumed as a
# unit so leftover "security clearance" / "clearance … required" cannot
# re-fire as a false positive.
CLEARANCE_EXPLICITLY_NOT_REQUIRED_RE = re.compile(
    r"("
    r"\bclearance[\s\-]*(?:required|preferred|mandatory|needed)"
    r"(?:\s+for\s+start)?[\s:\-|*]*(?:no|none|n/?a)\b|"
    r"\bno\s+(?:security\s+)?clearance(?:\s+is)?\s+required\b|"
    r"\bdoes\s+not\s+require\s+"
    r"(?:an?\s+)?(?:security\s+)?clearance\b|"
    r"\b(?:an?\s+)?(?:security\s+)?clearance\s+is\s+not\s+required\b|"
    r"\b(?:(?:an?\s+|the\s+)?(?:security\s+)?)?clearance"
    r"[\s:\-|*]*not[\s\-]+(?:required|needed|mandatory|necessary)\b"
    r")",
    re.I,
)

# Preferred-only *Secret* (no obtain/maintain) is not a hard prune.
# Eligibility to obtain/maintain Secret or TS — including "desirable" —
# is a barrier and must NOT be stripped here.
CLEARANCE_PREFERRED_ONLY_RE = re.compile(
    r"("
    r"\b(?:an?\s+)?(?:active\s+)?"
    r"(?:secret|top[\s\-]*secret|ts(?:[\s_/.\-]*sci)?|security)?[\s\-]*"
    r"clearance\s+is\s+preferred\b|"
    r"\b(?:active\s+)?(?:secret|top[\s\-]*secret|security)[\s\-]*"
    r"clearance\s+preferred\b|"
    r"\bclearance[\s\-]*preferred\b|"
    r"\bpreferred[\s:]+(?:an?\s+)?(?:active\s+)?"
    r"(?:secret|top[\s\-]*secret|ts|security)?[\s\-]*clearance\b|"
    r"\bsecurity[\s\-]*clearance\s+verification\b|"
    r"\bclearance\s+verification\b"
    r")",
    re.I,
)

# Strong clearance-requirement / cleared-role signals. Intentionally avoids
# bare "secret" / "security" / "classified as" so product Security Engineer
# and "trade secret" copy stay keepable.
CLEARANCE_REQUIREMENT_RE = re.compile(
    r"("
    # Common cleared-role tokens (title + JD)
    r"\bts[\s_/.\-]*sci\b|"
    r"\btop[\s\-]*secret\b|"
    r"(?<!employee\s)\bpolygraph\b|"
    r"\b(?:ci|full[\s\-]*scope)[\s\-]*poly(?:graph)?\b|"
    r"\b(?:q|l)[\s\-]*clearance\b|"
    r"\bdoe[\s\-]*(?:q|l)\b|"
    r"\bdod[\s\-]*(?:secret|top[\s\-]*secret|ts|clearance)\b|"
    r"\bsecret[\s\-]*clearance\b|"
    r"\bsecurity[\s\-]*clearance\b|"
    r"\bactive[\s\-]*(?:ts|sci|secret|top[\s\-]*secret|security)?[\s\-]*clearance\b|"
    r"\b(?:ts|secret|top[\s\-]*secret)[\s\-]*cleared\b|"
    r"\bcleared[\s\-]*(?:candidate|personnel|position|role|engineer|scientist)\b|"
    # Soft "clearance requirements" (plural) + required/mandatory/…
    r"\bclearance[\s\-]*(?:required|mandatory|needed|necessary|"
    r"eligibility|level|requirements?)\b|"
    # Requirement verbs near "clearance"
    r"\b(?:must|require[ds]?|required|need(?:s|ed)?|possess(?:es|ing)?|"
    r"hold(?:s|ing)?|obtain(?:able|ing)?|eligible\s+for|"
    r"ability\s+to\s+obtain|able\s+to\s+obtain|"
    r"currently\s+(?:hold|have)|have\s+an?\s+active)"
    r".{0,48}clearance\b|"
    r"\bclearance(?![\s:\-|*]*\bnot\b).{0,24}(?:required|mandatory|needed)\b|"
    # Classified-work phrasing (not "classified as full-time")
    r"\bclassified\s+(?:information|environment|program|material|data|"
    r"systems?|networks?|work|facility|facilities)\b|"
    r"\b(?:handle|access|process|work\s+(?:with|on))\s+classified\b|"
    r"\bsci[\s\-]*clearance\b|"
    r"\bsap(?:/sar)?\s+clearance\b|"
    # ATS "Clearance:" / "Clearance Type:" with a known level / obtain language.
    # Avoids work-auth mislabels like "Clearance: Must be able to work in the U.S."
    r"\bclearance\s*:\s*(?:secret|top[\s\-]*secret|ts(?:[\s_/.\-]*sci)?|sci|"
    r"public\s+trust|(?:doe[\s\-]*)?[ql]|active)\b|"
    r"\bclearance\s*:.{0,48}(?:obtain|eligible|public\s+trust|secret|"
    r"ts[\s_/.\-]*sci|polygraph)\b|"
    r"\bclearance[\s\-]*(?:type|level)\s*:\s*(?:secret|top[\s\-]*secret|"
    r"ts(?:[\s_/.\-]*sci)?|sci|public\s+trust|(?:doe[\s\-]*)?[ql]|active|"
    r"confidential)\b|"
    # Truncated Built In clearance metadata ("Clearance … [full text…]")
    r"\bclearance(?:[\s\-]*(?:required(?:\s+for\s+start)?|type|level))?"
    r"\s*:?\s*(?:\u2026|\.\.\.)\s*\[\s*full\s+text\b|"
    # Public Trust (federal suitability) — not prose "earns public trust"
    r"\(\s*public\s+trust\s*\)|"
    r"\bpublic\s+trust\s+clearance\b|"
    r"\b(?:must|require[ds]?|required|need(?:s|ed)?|possess(?:es|ing)?|"
    r"hold(?:s|ing)?|obtain(?:able|ing)?|eligible\s+for|"
    r"ability\s+to\s+obtain|able\s+to\s+obtain|"
    r"currently\s+(?:hold|have)|have\s+an?\s+active|maintain(?:ing)?)"
    r".{0,48}public\s+trust\b|"
    r"\bpublic\s+trust(?:\s+clearance)?[\s\-]*"
    r"(?:required|mandatory|needed)\b"
    r")",
    re.I,
)

# US intelligence community / IC agencies (company field). Short acronyms
# use word boundaries. Do not list commercial defense primes here — those
# are dropped only when clearance language (or an IC apply URL) appears.
INTEL_AGENCY_COMPANY_RE = re.compile(
    r"("
    r"national\s+security\s+agency|\bnsa\b|"
    r"central\s+intelligence(?:\s+agency)?|\bcia\b|"
    r"defense\s+intelligence(?:\s+agency)?|\bdia\b|"
    r"national\s+geospatial(?:[\s\-]+intelligence)?(?:\s+agency)?|\bnga\b|"
    r"national\s+reconnaissance\s+office|\bnro\b|"
    r"office\s+of\s+the\s+director\s+of\s+national\s+intelligence|\bodni\b|"
    r"national\s+counterterrorism\s+center|\bnctc\b|"
    r"defense\s+counterintelligence\s+and\s+security\s+agency|\bdcsa\b|"
    r"intelligence\s+community\s+agency|"
    r"u\.?s\.?\s+intelligence\s+community|"
    r"\bic\s+agency\b"
    r")",
    re.I,
)

# Apply / careers hosts that are IC agency portals (even when company
# string is odd / abbreviated).
INTEL_AGENCY_URL_RE = re.compile(
    r"("
    r"intelligencecareers\.gov|"
    r"(?:^|[\./])nsa\.gov|"
    r"(?:^|[\./])cia\.gov|"
    r"(?:^|[\./])dia\.mil|"
    r"(?:^|[\./])nga\.mil|"
    r"(?:^|[\./])nro\.gov|"
    r"(?:^|[\./])dni\.gov|"
    r"(?:^|[\./])dcsa\.mil"
    r")",
    re.I,
)

# Human-readable sync list for docs / tests.
CLEARANCE_EXCLUDE_HINTS = [
    "TS/SCI",
    "Top Secret",
    "polygraph",
    "Secret clearance",
    "security clearance",
    "Q clearance",
    "clearance required",
    "clearance requirements",
    "Clearance: Secret / Public Trust",
    "Clearance Type: Secret",
    "Clearance … [full text…]",
    "Public Trust clearance",
    "NSA / National Security Agency",
    "CIA / DIA / NGA / NRO / ODNI",
]

# ---------------------------------------------------------------------------
# Years of experience (YOE) — drop only when min required > 6
# ---------------------------------------------------------------------------

# Max acceptable minimum YOE (inclusive). Anything with min > this drops.
MAX_ACCEPTABLE_MIN_YOE = 6

# Optional adjective(s) between "years of" and "experience"
# (e.g. "4+ years of professional experience").
_YOE_OF_EXP = r"(?:of\s+)?(?:\w+\s+){0,3}(?:experience|exp\.?|yoe)"

# "7+ years of experience", "minimum of 8 years experience", "10 yrs exp"
# Optional backslash before + (markdown escapes: "2\+ years").
_YOE_PLUS = r"(?:\\?\+)"
_YOE_MIN_PLUS_RE = re.compile(
    r"\b(?:minimum(?:\s+of)?|min(?:imum)?\.?|at\s+least|requires?(?:\s+a)?|"
    r"must\s+have|seeking|looking\s+for|with)\s+"
    rf"(\d{{1,2}})\s*{_YOE_PLUS}\s*"
    r"(?:years?|yrs?\.?)\s*"
    rf"(?:{_YOE_OF_EXP})?\b",
    re.I,
)
_YOE_YEARS_PLUS_RE = re.compile(
    rf"\b(\d{{1,2}})\s*{_YOE_PLUS}\s*(?:years?|yrs?\.?)\s*"
    rf"{_YOE_OF_EXP}\b",
    re.I,
)
_YOE_YEARS_EXPERIENCE_RE = re.compile(
    r"\b(?:minimum(?:\s+of)?|min(?:imum)?\.?|at\s+least|requires?(?:\s+a)?|"
    r"must\s+have|seeking|with)\s+"
    r"(\d{1,2})\s*(?:years?|yrs?\.?)\s*"
    rf"{_YOE_OF_EXP}\b",
    re.I,
)
_YOE_PLAIN_YEARS_EXP_RE = re.compile(
    r"\b(\d{1,2})\s*(?:years?|yrs?\.?)\s*"
    rf"{_YOE_OF_EXP}\b",
    re.I,
)
# "5-7 years", "5–7 yrs", "1\-3 years" (escaped dash), "3 to 5 years"
_YOE_RANGE_RE = re.compile(
    r"\b(\d{1,2})\s*(?:\\?[-–—]|to)\s*(\d{1,2})\s*(?:\+)?\s*"
    r"(?:years?|yrs?\.?)\s*"
    rf"(?:{_YOE_OF_EXP})?\b",
    re.I,
)
# "YOE: 8", "years of experience: 7+"
_YOE_LABEL_RE = re.compile(
    r"\b(?:yoe|years?\s+of\s+experience|years?\s+experience)\s*[:=]\s*"
    r"(\d{1,2})\s*\+?",
    re.I,
)

# Company-history / tenure blurbs — not candidate requirements.
# e.g. "holding company with more than 20 years of experience"
_YOE_TENURE_BEFORE_RE = re.compile(
    r"(?:"
    r"(?:more|over)\s+than|"
    r"(?:nearly|almost|approximately|around|about)|"
    r"(?:for|with)\s+(?:over|more\s+than)|"
    r"(?:founded|established|celebrating)|"
    r"(?:company|holding|firm|business|organization|leader|provider)"
    r"(?:\s+\w+){0,4}\s+with|"
    r"(?:our\s+team\s+has|we\s+have)|"
    r"(?:preferred\s+qualifications?|nice\s+to\s+have)\s*:"
    r")\s*$",
    re.I,
)
# Immediate trailing context: "10 years ago", "12 years old", "18 years of age".
_YOE_TENURE_AFTER_RE = re.compile(
    r"^\s*(?:ago|old|in\s+business|of\s+age|"
    r"of\s+(?:excellence|service|operation|history))\b",
    re.I,
)

# Preferred / approximate YOE near a match — never a hard prune.
_YOE_SOFT_BEFORE_RE = re.compile(
    r"(?:"
    r"~|"
    r"\b(?:preferred|desired|ideally|optional|bonus)\b|"
    r"\bideal(?:ly)?(?:\s+candidate)?\b|"
    r"\bnice\s+to\s+have\b|"
    r"\ba\s+plus\b|"
    r"\bexcited\s+if\s+you\s+have\b|"
    r"\bwe(?:['’]re|\s+are)\s+excited\s+if\b"
    r").{0,100}$",
    re.I | re.S,
)
_YOE_SOFT_AFTER_RE = re.compile(
    r"^\s*(?:is\s+)?(?:preferred|desired|a\s+plus|nice\s+to\s+have|bonus)\b",
    re.I,
)


def _yoe_match_is_soft(blob: str, start: int, end: int | None = None) -> bool:
    """True when the YOE hit is tenure, preferred, or approximate — not required."""
    if _yoe_match_is_company_tenure(blob, start, end):
        return True
    pre = blob[max(0, start - 100) : start]
    if _YOE_SOFT_BEFORE_RE.search(pre):
        return True
    if end is not None and _YOE_SOFT_AFTER_RE.search(blob[end : end + 48]):
        return True
    window_end = len(blob) if end is None else min(len(blob), end + 48)
    chunk = blob[start:window_end]
    if re.search(r"\bequivalent\b", chunk, re.I) and re.search(
        r"\bor\s+$", blob[max(0, start - 24) : start], re.I
    ):
        return True
    return False

# Tier-2 YOE (display only — never prune). Prefer recall over precision; UI ~.
# Strict uses contiguous \w+ {0,3}; fallback allows hyphens/slashes, truncations
# ("exper"), and short windows to later experience/engineering context.
_YOE_FB_WORD = r"[\w/+&.,'-]+"
# Truncated ATS cuts: experience / exper / exp. / yoe
_YOE_FB_EXP = r"(?:experience|exper(?:ience)?|exp\.?|yoe)"
_YOE_FB_CTX = (
    r"(?:experience|exper(?:ience)?|exp\.?|yoe|engineering|software|development|"
    r"industry|professional|relevant|work(?:ing)?|ml|ai|data)"
)
_YOE_FALLBACK_YEARS_OF_WORDS_EXP_RE = re.compile(
    rf"\b(\d{{1,2}})\s*{_YOE_PLUS}?\s*(?:years?|yrs?\.?)\s+"
    rf"(?:of\s+)?(?:{_YOE_FB_WORD}\s+){{0,8}}"
    rf"{_YOE_FB_EXP}\b",
    re.I,
)
_YOE_FALLBACK_YEARS_APOS_RE = re.compile(
    rf"\b(\d{{1,2}})\s*{_YOE_PLUS}?\s*years?'\s*(?:of\s+)?{_YOE_FB_EXP}\b",
    re.I,
)
# "N+ years … experience/exper" with junk between (up to ~80 chars)
_YOE_FALLBACK_YEARS_NEAR_EXP_RE = re.compile(
    rf"\b(\d{{1,2}})\s*{_YOE_PLUS}?\s*(?:years?|yrs?\.?)\b"
    rf"(?:(?!\b(?:years?|yrs?\.?)\b).){{0,80}}?"
    rf"\b{_YOE_FB_EXP}\b",
    re.I | re.S,
)
# "N+ years" with nearby engineering/software/ML context (no "experience" word)
_YOE_FALLBACK_YEARS_PLUS_CTX_RE = re.compile(
    rf"\b(\d{{1,2}})\s*{_YOE_PLUS}\s*(?:years?|yrs?\.?)\b"
    rf"(?:(?!\b(?:years?|yrs?\.?)\b).){{0,60}}?"
    rf"\b{_YOE_FB_CTX}\b",
    re.I | re.S,
)
_YOE_FALLBACK_AT_LEAST_RE = re.compile(
    r"\b(?:minimum(?:\s+of)?|min(?:imum)?\.?|at\s+least)\s+"
    rf"(\d{{1,2}})\s*{_YOE_PLUS}?\s*(?:years?|yrs?\.?)\s+"
    rf"(?:of\s+)?(?:{_YOE_FB_WORD}\s+){{0,8}}"
    rf"{_YOE_FB_CTX}\b",
    re.I,
)
_YOE_FALLBACK_YEARS_MINIMUM_RE = re.compile(
    rf"\b(\d{{1,2}})\s*{_YOE_PLUS}?\s*(?:years?|yrs?\.?)\s+minimum\b"
    r"(?!\s+age\b)"
    rf"(?:\s+(?:of\s+)?{_YOE_FB_CTX})?",
    re.I,
)
_YOE_FALLBACK_EXP_LABEL_RE = re.compile(
    rf"\b{_YOE_FB_EXP}\s*(?:required)?\s*[:=]\s*"
    rf"(\d{{1,2}})\s*{_YOE_PLUS}?\s*(?:years?|yrs?\.?|yoe)?\b",
    re.I,
)
_YOE_FALLBACK_YOE_ABBREV_RE = re.compile(
    rf"\b(\d{{1,2}})\s*{_YOE_PLUS}?\s*yoe\b",
    re.I,
)
_YOE_FALLBACK_RANGE_RE = re.compile(
    rf"\b(\d{{1,2}})\s*(?:\\?[-–—]|to)\s*(\d{{1,2}})\s*(?:\+)?\s*"
    rf"(?:years?|yrs?\.?)\s+"
    rf"(?:of\s+)?(?:{_YOE_FB_WORD}\s+){{0,8}}"
    rf"{_YOE_FB_EXP}\b",
    re.I,
)
_YOE_FALLBACK_IN_ROLE_RE = re.compile(
    rf"\b(\d{{1,2}})\s*{_YOE_PLUS}\s*(?:years?|yrs?\.?)\s+"
    r"(?:in|as)\s+(?:an?\s+|the\s+)?"
    rf"(?:{_YOE_FB_WORD}\s+){{0,8}}"
    r"(?:role|position|capacity|job|engineer|scientist|analyst)\b",
    re.I,
)
_YOE_FALLBACK_WORKING_AS_RE = re.compile(
    rf"\b(\d{{1,2}})\s*{_YOE_PLUS}\s*(?:years?|yrs?\.?)\s+"
    r"(?:working\s+)?(?:as|as\s+an?)\s+",
    re.I,
)
_YOE_FALLBACK_YEARS_IN_FIELD_RE = re.compile(
    rf"\b(\d{{1,2}})\s*{_YOE_PLUS}\s*(?:years?|yrs?\.?)\s+"
    rf"in\s+(?:{_YOE_FB_WORD}\s+){{0,6}}"
    r"(?:engineering|science|analytics|development|software|data|ml|ai)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Citizenship / green card — explicit hard requirements only
# ---------------------------------------------------------------------------

CITIZENSHIP_OR_GC_REQUIREMENT_RE = re.compile(
    r"("
    r"\b(?:u\.?s\.?|us|united\s+states)\s+citizens?\s+only\b|"
    r"\bonly\s+(?:u\.?s\.?|us|united\s+states)\s+citizens?\b|"
    r"\b(?:u\.?s\.?|us|united\s+states)\s+citizenship\s+required\b|"
    r"\bmust\s+be\s+(?:a\s+)?(?:u\.?s\.?|us|united\s+states)\s+citizen\b|"
    r"\brequire[sd]?\s+(?:u\.?s\.?|us|united\s+states)\s+citizenship\b|"
    r"\bcitizenship\s*(?:requirement|:)\s*(?:u\.?s\.?|us|united\s+states)\b|"
    r"\bgreen\s*card\s+required\b|"
    r"\bmust\s+(?:have|hold|possess)\s+(?:a\s+)?green\s*card\b|"
    r"\brequire[sd]?\s+(?:a\s+)?green\s*card\b|"
    r"\bmust\s+be\s+(?:a\s+)?(?:permanent\s+resident|lawful\s+permanent\s+resident)\b|"
    r"\b(?:permanent\s+resident|lawful\s+permanent\s+resident)\s+(?:status\s+)?"
    r"required\b|"
    r"\bonly\s+(?:u\.?s\.?|us)\s+(?:citizens?|permanent\s+residents?)\b|"
    r"\bgreen\s*card\s+holders?\s+only\b|"
    r"\bonly\s+green\s*card\s+holders?\b|"
    r"\bgreen\s*cards?\s+only\b|"
    r"\b(?:u\.?s\.?|us|united\s+states)\s+citizens?\s+(?:and|or)\s+"
    r"(?:green\s*card(?:\s+holders?)?|permanent\s+residents?|gc)\s+only\b|"
    r"\busc\s*(?:and|&|/)\s*gc(?:\s+only)?\b(?!\s+(?:is\s+)?preferred)|"
    r"\bgc\s*(?:and|&|/)\s*usc(?:\s+only)?\b(?!\s+(?:is\s+)?preferred)|"
    r"\busc\s*/\s*gc\b(?!\s+(?:is\s+)?preferred)|"
    r"\bgc\s*/\s*usc\b(?!\s+(?:is\s+)?preferred)|"
    r"\bvisa\s*:\s*usc\b(?!\s+(?:and\s+gc\s+)?(?:is\s+)?preferred)"
    r")",
    re.I,
)

# Mere "U.S. Person preferred" is not a hard prune. Strip before matching
# required / ITAR / export-controlled language.
US_PERSON_PREFERRED_ONLY_RE = re.compile(
    r"("
    r"\b(?:u\.?s\.?|us)\s+persons?(?:\s+status)?\s+(?:is\s+)?preferred\b|"
    r"\bpreferred[\s:]+(?:a\s+)?(?:u\.?s\.?|us)\s+person(?:\s+status)?\b"
    r")",
    re.I,
)

# ITAR / export-controlled / U.S. Person **required** (not citizenship-only).
# Bare "(ITAR)" in an EAR/OFAC regulation list, and "If access to
# export-controlled … is required" license boilerplate, are not gates.
US_PERSON_IF_EXPORT_BOILERPLATE_RE = re.compile(
    r"\bif\s+access\s+to\s+export[\s\-]?controlled\b.{0,280}?\bis\s+required\b",
    re.I | re.S,
)
US_PERSON_REQUIRED_RE = re.compile(
    r"("
    r"\b(?:u\.?s\.?|us)\s+persons?(?:\s+status)?\s+(?:is\s+)?required\b|"
    r"\brequire[sd]?\s+(?:a\s+)?(?:u\.?s\.?|us)\s+person(?:\s+status)?\b|"
    r"\bmust\s+be\s+(?:a\s+)?(?:u\.?s\.?|us)\s+person\b|"
    r"\bonly\s+(?:u\.?s\.?|us)\s+persons?\b|"
    r"\b(?:u\.?s\.?|us)\s+persons?\s+only\b|"
    r"\bitar\s+requirements?\b|"
    r"\bitar[\s\-]controlled\b|"
    r"\bsubject\s+to\s+itar\b|"
    r"\bitar\b.{0,48}(?:required|restricted|compliance)\b|"
    r"\b(?:requires?|requiring|needs?|needing|must\s+have)\s+access\s+to\s+"
    r"(?:u\.?s\.?\s+)?export[\s\-]?controlled\b|"
    r"\baccess(?:es|ing)?(?:\s+to)?\s+export[\s\-]?controlled\s+"
    r"(?:data|information|items?|material|technology|source)\b"
    r")",
    re.I,
)

# No H-1B / visa help. Distinct from citizen/GC-only so a posting that
# *does* sponsor can still be kept when it lacks a citizen/GC hard gate.
NO_VISA_SPONSORSHIP_RE = re.compile(
    r"("
    r"\bno\s+(?:visa\s+|h-?1b\s+|immigration\s+)?sponsorship\b|"
    r"\bwithout\s+(?:(?:the\s+)?(?:need\s+for\s+)?)?(?:employer\s+|company\s+)?"
    r"(?:visa\s+|h-?1b\s+|immigration\s+)?sponsorship\b|"
    r"\b(?:does|do|will|can)\s+not\s+sponsor\b|"
    r"\bunable\s+to\s+sponsor\b|"
    r"\bcannot\s+sponsor\b|"
    r"\bnot\s+(?:able|willing)\s+to\s+sponsor\b|"
    r"\bno\s+(?:visa\s+)?sponsor(?:ship)?\s+(?:available|provided|offered)\b|"
    r"\bsponsorship\s+(?:is\s+)?(?:not\s+available|unavailable)\b"
    r")",
    re.I,
)

# Positive "we sponsor" — must not match "we do not sponsor".
SPONSORS_VISA_RE = re.compile(
    r"("
    r"\bwe\s+(?:do\s+)?sponsor(?:s)?\s+(?:h-?1b|visas?|work\s+visas?)\b|"
    r"\b(?:company|employer)\s+sponsors?\s+(?:h-?1b|visas?)\b|"
    r"\b(?:visa|h-?1b|immigration)\s+sponsorship\s+(?:is\s+)?"
    r"(?:available|provided|offered|ok|okay)\b|"
    r"\b(?:will|can|may)\s+sponsor\s+(?:h-?1b|visas?|work\s+visas?)\b|"
    r"\bsponsorship\s+(?:is\s+)?(?:available|provided|offered)\b|"
    r"\bopen\s+to\s+(?:visa|h-?1b|immigration)\s+sponsorship\b|"
    r"\bprovides?\s+(?:visa|h-?1b)\s+sponsorship\b"
    r")",
    re.I,
)

# ---------------------------------------------------------------------------
# Work mode (UI only — never used to prune)
# ---------------------------------------------------------------------------

_WORK_MODE_HYBRID_RE = re.compile(
    r"("
    r"\bhybrid\b|"
    r"\bremote\s+and\s+(?:in[\s\-]?office|on[\s\-]?site|onsite)\b|"
    r"\b(?:in[\s\-]?office|on[\s\-]?site|onsite)\s+and\s+remote\b|"
    r"\b\d+\s*(?:days?|x)\s+(?:a|per)\s+week\s+in\s+(?:the\s+)?(?:office|on[\s\-]?site)\b|"
    r"\b\d+\s+days?\s+(?:a|per)\s+week\s+(?:in|at)\s+(?:the\s+|our\s+)?"
    r"(?:\w+\s+){0,6}office\b|"
    r"\boffice\s+\d+\s+days?\s+(?:a|per)\s+week\b|"
    r"\bin\s+(?:the\s+|our\s+)?(?:\w+\s+){0,6}office\s+\d+\s+days?\s+(?:a|per)\s+week\b|"
    r"\b(?:partially|part[\s\-]?time)\s+remote\b"
    r")",
    re.I,
)
_WORK_MODE_REMOTE_RE = re.compile(
    r"("
    r"\bfully\s+remote\b|"
    r"\bremote[\s\-]?first\b|"
    r"\bwork\s+from\s+home\b|"
    r"\bwfh\b|"
    r"\bremote\b"
    r")",
    re.I,
)
_WORK_MODE_ONSITE_RE = re.compile(
    r"("
    r"\bon[\s\-]?site\b|"
    r"\bonsite\b|"
    r"\bin[\s\-]?person\b|"
    r"\bin[\s\-]?office\b|"
    r"\bmust\s+relocate\b|"
    r"\brelocation\s+required\b|"
    r"\bon[\s\-]?campus\b"
    r")",
    re.I,
)
# Softer work-mode cues (display fallback only when strict is unknown).
_WORK_MODE_FALLBACK_HYBRID_RE = re.compile(
    r"("
    r"\bhybrid[\s\-]?(?:preferred|ok|okay|available|possible|friendly|role|position)\b|"
    r"\bopen\s+to\s+hybrid\b|"
    r"\bflexible\s+(?:work\s+)?(?:arrangement|location|schedule)\b|"
    r"\bflex(?:ible)?\s+work\b|"
    r"\bmix\s+of\s+(?:remote|office|on[\s\-]?site|onsite|in[\s\-]?office)\b|"
    r"\b\d+\s*(?:[-–—]\s*\d+\s+)?days?\s+(?:a|per)\s+week\s+(?:in|at|from)\s+"
    r"(?:the\s+)?(?:office|hq|headquarters)\b"
    r")",
    re.I,
)
_WORK_MODE_FALLBACK_REMOTE_RE = re.compile(
    r"("
    r"\bopen\s+to\s+remote\b|"
    r"\bremote[\s\-]?(?:ok|okay|friendly|preferred|available|possible|optional)\b|"
    r"\boptional(?:ly)?\s+remote\b|"
    r"\bwork\s+remotely\b|"
    r"\bcan\s+be\s+remote\b|"
    r"\bdistributed\s+team\b|"
    r"\bwork\s+from\s+anywhere\b|"
    r"\btelecommute\b|"
    r"\btelework\b|"
    r"\bremotely\b|"
    r"\banywhere\s+in\s+(?:the\s+)?(?:u\.?s\.?|united\s+states)\b"
    r")",
    re.I,
)
_WORK_MODE_FALLBACK_ONSITE_RE = re.compile(
    r"("
    r"\boffice[\s\-]?based\b|"
    r"\bheadquarters[\s\-]?based\b|"
    r"\bhq[\s\-]?based\b|"
    r"\bcome\s+into\s+(?:the\s+)?office\b|"
    r"\bin\s+our\s+(?:offices?|hq|headquarters)\b|"
    r"\bnot\s+remote\b|"
    r"\bno\s+remote\b|"
    r"\bon[\s\-]?site\s+only\b|"
    r"\breport(?:ing)?\s+to\s+(?:the\s+)?(?:office|hq)\b|"
    r"\boffice\s+presence\s+required\b|"
    r"\b(?:preferred|optional)\s+on[\s\-]?site\b|"
    r"\bnot\s+a\s+remote\s+(?:position|role|job)\b|"
    r"\bbased\s+at\s+.{0,48}headquarters\b"
    r")",
    re.I,
)

_REMOTE_PRODUCT_AFTER_RE = re.compile(
    r"^\s*(?:access|support|desktop|monitoring|procedure|control|session|tools?)\b",
    re.I,
)
_REMOTE_NEGATED_WINDOW_RE = re.compile(
    r"("
    r"\bnot\s+a\s+remote\s+(?:position|role|job|opportunity)\b|"
    r"\bnot\s+remote\s+(?:position|role|job)\b|"
    r"\bthis\s+is\s+not\s+remote\b|"
    r"\bremote\s+(?:work|employment)?\s+is\s+not\s+available\b|"
    r"\bno\s+remote\s+(?:work|option|positions?)\b|"
    r"\bremote\s+applicants?\s+will\s+not\b|"
    r"\bnot\s+available\s+for\s+remote\b|"
    r"\bnon[\s\-]?remote\b"
    r")",
    re.I,
)
_REMOTE_STRONG_SPAN_RE = re.compile(
    r"fully\s+remote|remote[\s\-]?first|work\s+from\s+home|\bwfh\b",
    re.I,
)
_ONSITE_AMENITY_AFTER_RE = re.compile(
    r"^\s*(?:gym|fitness|cafeteria|cafe|perks?|childcare|parking|"
    r"clinic|doctor|medical|wellness)\b",
    re.I,
)
_ONSITE_INTERVIEW_AFTER_RE = re.compile(
    r"^\s*(?:interview|screening|whiteboarding)\b",
    re.I,
)
_ONSITE_SOFT_BEFORE_RE = re.compile(
    r"\b(?:preferred|optional)\b.{0,24}$",
    re.I | re.S,
)


def _fold_accents(text: str) -> str:
    """NFKD fold so México / São Paulo match ascii country/city tokens."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _remote_match_is_noise(blob: str, m: re.Match) -> bool:
    """True when 'remote' is product language or an explicit not-remote role."""
    span = m.group(0)
    if _REMOTE_STRONG_SPAN_RE.search(span):
        return False
    # "Non-Remote" / "non remote" titles must not count as remote.
    pre = blob[max(0, m.start() - 4) : m.start()]
    if re.search(r"non[\s\-]?$", pre, re.I):
        return True
    post = blob[m.end() : m.end() + 24]
    if _REMOTE_PRODUCT_AFTER_RE.search(post):
        return True
    window = blob[max(0, m.start() - 56) : m.end() + 56]
    return bool(_REMOTE_NEGATED_WINDOW_RE.search(window))


def _onsite_match_is_noise(blob: str, m: re.Match, *, strict: bool) -> bool:
    """True when on-site is an amenity, interview, or (strict) preferred/optional."""
    span = m.group(0)
    if re.search(r"relocate|relocation", span, re.I):
        return False
    post = blob[m.end() : m.end() + 32]
    if _ONSITE_AMENITY_AFTER_RE.search(post) or _ONSITE_INTERVIEW_AFTER_RE.search(post):
        return True
    if strict:
        pre = blob[max(0, m.start() - 24) : m.start()]
        if _ONSITE_SOFT_BEFORE_RE.search(pre):
            return True
    return False


def _work_mode_hit(rx: re.Pattern, blob: str, kind: str, *, strict: bool) -> bool:
    for m in rx.finditer(blob):
        if kind == "remote" and _remote_match_is_noise(blob, m):
            continue
        if kind == "onsite" and _onsite_match_is_noise(blob, m, strict=strict):
            continue
        return True
    return False


def is_excluded_title(title: str | None) -> bool:
    """True if the title indicates leadership / above-senior IC ladder."""
    t = str(title or "")
    if not t.strip():
        return False
    return bool(SENIORITY_EXCLUDE_RE.search(t))


def is_clearly_non_us_location(location: str | None) -> bool:
    """True only when location can be determined as not US-based.

    Empty, 'Remote', bare ambiguous undetermined → False (keep).
    'India', 'Canada', 'London', 'Asia' with no US signal → True (drop).
    'San Francisco / London' or 'Remote, US' → False (keep).
    'Bengaluru, KA, in' → True via the ISO-2 country tail (the bare ", in"
    would otherwise read as Indiana and keep the listing).
    """
    loc = _fold_accents(str(location or "")).strip()
    if not loc:
        return False
    if GEORGIA_COUNTRY_CITY_RE.search(loc) and not re.search(
        r"\b(?:GA|USA|US|U\.S\.A?\.?|United\s+States)\b", loc, re.I
    ):
        return True
    tail, head_parts = _location_tail_country(loc)
    head = ", ".join(head_parts)
    if tail:
        code = tail.lower()
        if code in ("us", "pr", "vi", "gu"):
            return False
        if code in NON_US_ISO2_CODES:
            collides_with_state = tail.upper() in US_STATE_ABBREVS
            # Unambiguous codes (my, gb, sg, …) decide on their own. Codes
            # that double as state abbreviations need the lowercase ATS
            # spelling plus corroboration, so "Dublin, CA" and
            # "Indianapolis, IN" stay US.
            decisive = not collides_with_state or (
                tail.islower()
                and (len(head_parts) >= 2 or NON_US_LOCATION_RE.search(head))
            )
            if decisive and not US_LOCATION_STRONG_RE.search(head):
                return True
    if is_india_location(loc) and not US_LOCATION_STRONG_RE.search(loc):
        return True
    if US_LOCATION_RE.search(loc):
        return False
    return bool(NON_US_LOCATION_RE.search(loc))


def is_intel_agency_employer(company: str | None = None, url: str | None = None) -> bool:
    """True when company or careers URL is a US intel / IC agency."""
    co = str(company or "").strip()
    if co and INTEL_AGENCY_COMPANY_RE.search(co):
        return True
    u = str(url or "").strip()
    if u and INTEL_AGENCY_URL_RE.search(u):
        return True
    return False


def requires_security_clearance(
    *,
    title: str | None = None,
    company: str | None = None,
    location: str | None = None,
    description: str | None = None,
    url: str | None = None,
) -> bool:
    """True if listing clearly requires/emphasizes clearance or is an IC agency.

    Scans title, company, location, description, and URL. Civilian
    "Security Engineer" without clearance-requirement language → False.
    """
    if is_intel_agency_employer(company, url):
        return True
    blob = " ".join(
        str(part or "")
        for part in (title, company, location, description)
    )
    if not blob.strip():
        return False
    # Drop explicit "required: No/None" and preferred-only labels, then
    # re-check hard positives.
    cleaned = CLEARANCE_EXPLICITLY_NOT_REQUIRED_RE.sub(" ", blob)
    cleaned = CLEARANCE_PREFERRED_ONLY_RE.sub(" ", cleaned)
    return bool(CLEARANCE_REQUIREMENT_RE.search(cleaned))


def _yoe_match_is_company_tenure(
    blob: str, start: int, end: int | None = None
) -> bool:
    """True when the match is company age / history, not a candidate requirement."""
    pre = blob[max(0, start - 64) : start]
    if _YOE_TENURE_BEFORE_RE.search(pre):
        return True
    if end is not None and _YOE_TENURE_AFTER_RE.search(blob[end : end + 32]):
        return True
    return False


def extract_min_required_yoe(
    text: str | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
) -> int | None:
    """Return the highest explicit minimum YOE found, or None if undetermined.

    Ranges use the **lower** bound; ``N+`` uses N. Across matches, take the
    max of those mins (any line requiring ≥7 → excessive via helper below).
    Ignores company-tenure blurbs like "with more than 20 years of experience".
    """
    blob = " ".join(
        str(part or "")
        for part in (text, title, description)
        if part
    )
    if not blob.strip():
        return None
    # Normalize markdown escapes so "2\+ years" parses like "2+ years".
    blob = blob.replace("\\+", "+").replace("\\-", "-")
    mins: list[int] = []
    # Spans covered by range matches — skip plain matches that only re-hit
    # the upper bound of "5-7 years".
    range_spans: list[tuple[int, int]] = []
    for m in _YOE_RANGE_RE.finditer(blob):
        if _yoe_match_is_soft(blob, m.start(), m.end()):
            continue
        lo, hi = int(m.group(1)), int(m.group(2))
        mins.append(min(lo, hi))
        range_spans.append(m.span())
    for rx in (
        _YOE_MIN_PLUS_RE,
        _YOE_YEARS_PLUS_RE,
        _YOE_YEARS_EXPERIENCE_RE,
        _YOE_LABEL_RE,
        _YOE_PLAIN_YEARS_EXP_RE,
    ):
        for m in rx.finditer(blob):
            start, end = m.span()
            if any(rs <= start < re_ for rs, re_ in range_spans):
                continue
            if _yoe_match_is_soft(blob, start, end):
                continue
            mins.append(int(m.group(1)))
    if not mins:
        return None
    # Ignore absurd OCR/noise (e.g. 99 years)
    sane = [n for n in mins if 0 < n <= 40]
    return max(sane) if sane else None


def extract_min_required_yoe_fallback(
    text: str | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
) -> int | None:
    """Broader YOE patterns for **display only** when strict extract is None.

    Never used for prune / ``requires_excessive_experience``. UI should prefix
    the number with ``~`` (approximate).
    """
    if extract_min_required_yoe(text, title=title, description=description) is not None:
        return None
    blob = " ".join(
        str(part or "")
        for part in (text, title, description)
        if part
    )
    if not blob.strip():
        return None
    blob = blob.replace("\\+", "+").replace("\\-", "-")
    mins: list[int] = []
    range_spans: list[tuple[int, int]] = []
    for m in _YOE_FALLBACK_RANGE_RE.finditer(blob):
        if _yoe_match_is_soft(blob, m.start(), m.end()):
            continue
        lo, hi = int(m.group(1)), int(m.group(2))
        mins.append(min(lo, hi))
        range_spans.append(m.span())
    for rx in (
        _YOE_FALLBACK_YEARS_OF_WORDS_EXP_RE,
        _YOE_FALLBACK_YEARS_APOS_RE,
        _YOE_FALLBACK_YEARS_NEAR_EXP_RE,
        _YOE_FALLBACK_YEARS_PLUS_CTX_RE,
        _YOE_FALLBACK_AT_LEAST_RE,
        _YOE_FALLBACK_YEARS_MINIMUM_RE,
        _YOE_FALLBACK_EXP_LABEL_RE,
        _YOE_FALLBACK_YOE_ABBREV_RE,
        _YOE_FALLBACK_IN_ROLE_RE,
        _YOE_FALLBACK_WORKING_AS_RE,
        _YOE_FALLBACK_YEARS_IN_FIELD_RE,
    ):
        for m in rx.finditer(blob):
            start = m.start()
            if any(rs <= start < re_ for rs, re_ in range_spans):
                continue
            if _yoe_match_is_soft(blob, start, m.end()):
                continue
            mins.append(int(m.group(1)))
    if not mins:
        return None
    sane = [n for n in mins if 0 < n <= 40]
    return max(sane) if sane else None


def requires_excessive_experience(
    *,
    title: str | None = None,
    description: str | None = None,
    text: str | None = None,
) -> bool:
    """True when an explicit minimum required YOE is > MAX_ACCEPTABLE_MIN_YOE.

    Uses **strict** extract only — fallback YOE never triggers prune.
    """
    ymin = extract_min_required_yoe(text, title=title, description=description)
    return ymin is not None and ymin > MAX_ACCEPTABLE_MIN_YOE


def requires_us_person(
    *,
    title: str | None = None,
    description: str | None = None,
    text: str | None = None,
) -> bool:
    """True on U.S. Person required, ITAR, or export-controlled data.

    Mere “U.S. Person preferred” without required / ITAR / export-controlled
    is False. No-sponsorship language is not a US-Person gate.
    """
    blob = " ".join(
        str(part or "")
        for part in (text, title, description)
        if part
    )
    if not blob.strip():
        return False
    cleaned = US_PERSON_PREFERRED_ONLY_RE.sub(" ", blob)
    cleaned = US_PERSON_IF_EXPORT_BOILERPLATE_RE.sub(" ", cleaned)
    return bool(US_PERSON_REQUIRED_RE.search(cleaned))


def stamp_clearance_us_person_tags(
    *,
    title: str | None = None,
    company: str | None = None,
    location: str | None = None,
    description: str | None = None,
    url: str | None = None,
) -> dict[str, bool]:
    """Card chips: clearance / us_person. Independent of prune timing."""
    return {
        "clearance": requires_security_clearance(
            title=title,
            company=company,
            location=location,
            description=description,
            url=url,
        ),
        "us_person": requires_us_person(title=title, description=description),
    }


def requires_us_citizen_or_greencard(
    *,
    title: str | None = None,
    description: str | None = None,
    text: str | None = None,
) -> bool:
    """True on US-citizen / GC-only hard requirements or U.S. Person/ITAR.

    No-sponsorship language (“unable to sponsor”, “no visa sponsorship”)
    is not USC/GC-only and must not prune.
    """
    blob = " ".join(
        str(part or "")
        for part in (text, title, description)
        if part
    )
    if not blob.strip():
        return False
    if CITIZENSHIP_OR_GC_REQUIREMENT_RE.search(blob):
        return True
    return requires_us_person(title=title, description=description, text=text)


def detect_work_mode(
    *,
    title: str | None = None,
    location: str | None = None,
    description: str | None = None,
) -> str:
    """Return remote | hybrid | onsite | unknown (UI only — never prune).

    Priority: hybrid if hybrid matched; else if both remote and onsite →
    unknown (prefer undetermined over wrong tag); else single mode.
    """
    blob = " ".join(
        str(part or "")
        for part in (title, location, description)
    )
    if not blob.strip():
        return "unknown"
    hybrid = bool(_WORK_MODE_HYBRID_RE.search(blob))
    if hybrid:
        return "hybrid"
    remote = _work_mode_hit(_WORK_MODE_REMOTE_RE, blob, "remote", strict=True)
    onsite = _work_mode_hit(_WORK_MODE_ONSITE_RE, blob, "onsite", strict=True)
    if remote and onsite:
        return "unknown"
    if remote:
        return "remote"
    if onsite:
        return "onsite"
    return "unknown"


def detect_work_mode_fallback(
    *,
    title: str | None = None,
    location: str | None = None,
    description: str | None = None,
) -> str:
    """Softer work-mode guess when strict ``detect_work_mode`` is unknown.

    Display only — never used to prune. Returns remote|hybrid|onsite|unknown.
    """
    if detect_work_mode(title=title, location=location, description=description) != "unknown":
        return "unknown"
    blob = " ".join(
        str(part or "")
        for part in (title, location, description)
    )
    if not blob.strip():
        return "unknown"
    hybrid = bool(_WORK_MODE_FALLBACK_HYBRID_RE.search(blob))
    if hybrid:
        return "hybrid"
    remote = _work_mode_hit(
        _WORK_MODE_FALLBACK_REMOTE_RE, blob, "remote", strict=False
    )
    onsite = _work_mode_hit(
        _WORK_MODE_FALLBACK_ONSITE_RE, blob, "onsite", strict=False
    )
    if remote and onsite:
        return "unknown"
    if remote:
        return "remote"
    if onsite:
        return "onsite"
    return "unknown"


# ---------------------------------------------------------------------------
# Salary (UI only — never used to prune)
# ---------------------------------------------------------------------------

# Annual USD bounds for a sane IC salary stamp.
_SALARY_MIN_ANNUAL = 20_000
_SALARY_MAX_ANNUAL = 1_000_000

# Amount forms: $120,000 | 120,000 | $120k | 120k | USD 120000 | 120000 | $8,488.33
_SAL_NUM_COMMA = r"\d{1,3}(?:,\d{3})+(?:\\?\.\d{2})?"
_SAL_NUM_K = r"\d{2,3}(?:\.\d{1,2})?\s*[kK]"
_SAL_NUM_PLAIN = r"\d{5,7}"
_SAL_NUM = rf"(?:{_SAL_NUM_COMMA}|{_SAL_NUM_K}|{_SAL_NUM_PLAIN})"
_SAL_CUR = r"(?:\$|USD\s+)"
_SAL_AMOUNT = rf"(?:{_SAL_CUR}\s*)?{_SAL_NUM}"
_SAL_SEP = (
    r"(?:\s*(?:/(?:year|yr)|per\s+year)?\s*\\?[-–—]\s*|\s+to\s+)"
)

_SALARY_KW = (
    r"(?:salary|compensation|compensat(?:ed|ion)?|base(?:\s+pay|\s+salary)?|"
    r"pay|ote|total\s+comp(?:ensation)?|\btc\b|remuneration|wages?)"
)

# Clear range: $120k–$150k, 120000-150000, USD 120k to 150k
_SALARY_RANGE_RE = re.compile(
    rf"(?P<a>{_SAL_AMOUNT}){_SAL_SEP}(?P<b>{_SAL_AMOUNT})",
    re.I,
)
# Labeled single / range lead-in: salary: $100k, compensation $120,000
_SALARY_LABEL_RE = re.compile(
    rf"\b{_SALARY_KW}\s*(?:range|band|expectation)?\s*[:=]?\s*"
    rf"(?P<a>{_SAL_AMOUNT})"
    rf"(?:{_SAL_SEP}(?P<b>{_SAL_AMOUNT}))?",
    re.I,
)
# Standalone $ / USD figure (requires currency marker)
_SALARY_DOLLAR_SINGLE_RE = re.compile(
    rf"(?:{_SAL_CUR}\s*)(?P<a>{_SAL_NUM})\b",
    re.I,
)

# Softer fallback cues (display ~ only)
_SALARY_FALLBACK_UP_TO_RE = re.compile(
    rf"\b(?:up\s+to|as\s+high\s+as|capped\s+at|max(?:imum)?(?:\s+of)?)\s+"
    rf"(?P<a>{_SAL_AMOUNT})\b",
    re.I,
)
_SALARY_FALLBACK_FROM_RE = re.compile(
    rf"\b(?:starting\s+at|from|at\s+least|minimum(?:\s+of)?)\s+"
    rf"(?P<a>{_SAL_AMOUNT})\b",
    re.I,
)
_SALARY_FALLBACK_NEAR_KW_RE = re.compile(
    rf"(?:{_SALARY_KW}).{{0,48}}?(?P<a>{_SAL_AMOUNT})"
    rf"(?:{_SAL_SEP}(?P<b>{_SAL_AMOUNT}))?"
    rf"|"
    rf"(?P<a2>{_SAL_AMOUNT})(?:{_SAL_SEP}(?P<b2>{_SAL_AMOUNT}))?.{{0,48}}?"
    rf"(?:{_SALARY_KW})",
    re.I | re.S,
)
_SALARY_FALLBACK_BARE_K_RANGE_RE = re.compile(
    rf"\b(?P<a>\d{{2,3}}(?:\.\d{{1,2}})?\s*[kK]){_SAL_SEP}"
    rf"(?P<b>\d{{2,3}}(?:\.\d{{1,2}})?\s*[kK])\b",
    re.I,
)

_SALARY_HOURLY_AFTER_RE = re.compile(
    r"^\s*(?:/|\s)*(?:hr|hrs|hour|hours|hourly)\b|"
    r"^\s*per\s+hour\b|"
    r"^\s*an\s+hour\b|"
    r"^\s*p/?h\b",
    re.I,
)
_SALARY_HOURLY_BEFORE_RE = re.compile(
    r"(?:hourly|per[\s\-]?hour|/hr|/hour)\s*$",
    re.I,
)
_SALARY_FUNDING_AMOUNT_RE = re.compile(
    r"(?:\$|USD\s*)?\s*\d+(?:\.\d+)?\s*[mMbB]\b|"
    r"(?:\$|USD\s*)?\s*\d[\d,]*(?:\.\d+)?\s*(?:million|billion)\b",
    re.I,
)
_SALARY_FUNDING_CTX_RE = re.compile(
    r"\b(?:series\s+[a-z]|raised|funding\s+round|valuation|seed\s+round|"
    r"venture\s+(?:capital|funding|round|backed)|investment\s+round|"
    r"\barr\b|revenue\s+of)\b",
    re.I,
)
_SALARY_MONTHLY_WINDOW_RE = re.compile(
    r"\b(?:per\s+month|monthly|/mo\b|pay\s+frequency\s*[:=]?\s*monthly)\b",
    re.I,
)


def _parse_salary_amount(raw: str) -> int | None:
    """Parse a salary amount token into annual USD integer (approx)."""
    s = str(raw or "").strip()
    if not s:
        return None
    s = re.sub(r"^(?:\$|USD)\s*", "", s, flags=re.I).strip()
    s = s.replace(",", "").replace(" ", "").replace("\\", "")
    if not s:
        return None
    if re.search(r"[kK]$", s):
        try:
            return int(round(float(s[:-1]) * 1000))
        except ValueError:
            return None
    if re.search(r"[mMbB]$", s):
        return None  # millions — funding / not IC salary
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def _salary_sane(n: int | None) -> bool:
    return n is not None and _SALARY_MIN_ANNUAL <= n <= _SALARY_MAX_ANNUAL


def _salary_is_hourly(blob: str, start: int, end: int) -> bool:
    pre = blob[max(0, start - 24) : start]
    post = blob[end : min(len(blob), end + 24)]
    return bool(
        _SALARY_HOURLY_BEFORE_RE.search(pre) or _SALARY_HOURLY_AFTER_RE.search(post)
    )


def _salary_is_monthly(blob: str, start: int, end: int) -> bool:
    window = blob[max(0, start - 48) : min(len(blob), end + 96)]
    return bool(_SALARY_MONTHLY_WINDOW_RE.search(window))


def _salary_annualize_if_monthly(
    pair: dict | None, blob: str, start: int, end: int
) -> dict | None:
    if not pair:
        return None
    if not _salary_is_monthly(blob, start, end):
        return pair
    lo = pair.get("min")
    hi = pair.get("max")
    if lo is not None and lo >= _SALARY_MIN_ANNUAL:
        return pair  # already annual-sized
    if lo is not None:
        pair = dict(pair)
        pair["min"] = int(round(lo * 12))
        if hi is not None:
            pair["max"] = int(round(hi * 12))
    if not _salary_sane(pair.get("min")):
        return None
    if pair.get("max") is not None and not _salary_sane(pair["max"]):
        pair["max"] = None
    return pair


def _salary_is_funding_noise(blob: str, start: int, end: int) -> bool:
    window = blob[max(0, start - 40) : min(len(blob), end + 40)]
    if _SALARY_FUNDING_AMOUNT_RE.search(window):
        return True
    if _SALARY_FUNDING_CTX_RE.search(window):
        return True
    return False


def _salary_plausible_amount(n: int | None) -> bool:
    """Allow monthly-sized figures; caller annualizes then applies annual bounds."""
    return n is not None and 1_000 <= n <= _SALARY_MAX_ANNUAL


def _salary_pair_from_groups(
    a_raw: str | None,
    b_raw: str | None,
) -> dict | None:
    a = _parse_salary_amount(a_raw) if a_raw else None
    b = _parse_salary_amount(b_raw) if b_raw else None
    if not _salary_plausible_amount(a) and not _salary_plausible_amount(b):
        return None
    if _salary_plausible_amount(a) and _salary_plausible_amount(b):
        lo, hi = (a, b) if a <= b else (b, a)
        return {"min": lo, "max": hi, "period": "year"}
    n = a if _salary_plausible_amount(a) else b
    return {"min": n, "max": None, "period": "year"}


def _salary_blob(
    text: str | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
) -> str:
    return " ".join(
        str(part or "") for part in (text, title, description) if part
    )


def extract_salary(
    text: str | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
) -> dict | None:
    """Strict salary extract for display. Returns {min, max?, period} or None.

    Prefers clear ranges and labeled / $-marked figures. Ignores hourly and
    funding/valuation noise. Display only — never prune.
    """
    blob = _salary_blob(text, title=title, description=description)
    if not blob.strip():
        return None
    candidates: list[dict] = []
    range_spans: list[tuple[int, int]] = []

    def consider(
        m: re.Match,
        a_key: str = "a",
        b_key: str = "b",
        *,
        ignore_funding: bool = False,
    ) -> None:
        if _salary_is_hourly(blob, m.start(), m.end()):
            return
        if not ignore_funding and _salary_is_funding_noise(blob, m.start(), m.end()):
            return
        gd = m.groupdict()
        pair = _salary_pair_from_groups(gd.get(a_key), gd.get(b_key))
        pair = _salary_annualize_if_monthly(pair, blob, m.start(), m.end())
        if pair and _salary_sane(pair.get("min")):
            candidates.append(pair)

    for m in _SALARY_LABEL_RE.finditer(blob):
        consider(m, ignore_funding=True)
        if m.group("b"):
            range_spans.append(m.span())
    for m in _SALARY_RANGE_RE.finditer(blob):
        # Require currency on at least one side OR both plain 5–7 digit /
        # k-suffix amounts (e.g. 120000-150000, 120k-150k).
        a_raw, b_raw = m.group("a"), m.group("b")
        has_cur = bool(
            re.search(r"(?:\$|USD)", a_raw, re.I)
            or re.search(r"(?:\$|USD)", b_raw, re.I)
        )
        both_k_or_plain = bool(
            re.search(r"(?:[kK]|\d{5,7})", a_raw)
            and re.search(r"(?:[kK]|\d{5,7})", b_raw)
        )
        if not (has_cur or both_k_or_plain):
            continue
        consider(m)
        range_spans.append(m.span())
    for m in _SALARY_DOLLAR_SINGLE_RE.finditer(blob):
        span_start, span_end = m.span()
        if any(rs <= span_start < re_ for rs, re_ in range_spans):
            continue
        if _salary_is_hourly(blob, span_start, span_end):
            continue
        if _salary_is_funding_noise(blob, span_start, span_end):
            continue
        pair = _salary_pair_from_groups(m.group("a"), None)
        pair = _salary_annualize_if_monthly(pair, blob, span_start, span_end)
        if pair and _salary_sane(pair.get("min")):
            candidates.append(pair)

    if not candidates:
        return None
    # Prefer a real range over a single; then first match order.
    ranged = [c for c in candidates if c.get("max") is not None]
    pick = ranged[0] if ranged else candidates[0]
    return pick


def extract_salary_fallback(
    text: str | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
) -> dict | None:
    """Softer salary patterns when strict extract is None. Display ``~`` only."""
    if extract_salary(text, title=title, description=description) is not None:
        return None
    blob = _salary_blob(text, title=title, description=description)
    if not blob.strip():
        return None
    candidates: list[dict] = []

    def consider(m: re.Match, a_key: str = "a", b_key: str = "b") -> None:
        if _salary_is_hourly(blob, m.start(), m.end()):
            return
        if _salary_is_funding_noise(blob, m.start(), m.end()):
            return
        gd = m.groupdict()
        a_raw = gd.get(a_key) or gd.get("a2")
        b_raw = gd.get(b_key) or gd.get("b2")
        pair = _salary_pair_from_groups(a_raw, b_raw)
        pair = _salary_annualize_if_monthly(pair, blob, m.start(), m.end())
        if pair and _salary_sane(pair.get("min")):
            candidates.append(pair)

    for m in _SALARY_FALLBACK_NEAR_KW_RE.finditer(blob):
        consider(m)
    for m in _SALARY_FALLBACK_UP_TO_RE.finditer(blob):
        consider(m)
    for m in _SALARY_FALLBACK_FROM_RE.finditer(blob):
        consider(m)
    for m in _SALARY_FALLBACK_BARE_K_RANGE_RE.finditer(blob):
        consider(m)

    if not candidates:
        return None
    ranged = [c for c in candidates if c.get("max") is not None]
    return ranged[0] if ranged else candidates[0]


def extract_salary_with_source(
    text: str | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
) -> tuple[dict | None, str | None]:
    """Return (salary_dict | None, 'strict' | 'fallback' | None)."""
    strict = extract_salary(text, title=title, description=description)
    if strict is not None:
        return strict, "strict"
    fb = extract_salary_fallback(text, title=title, description=description)
    if fb is not None:
        return fb, "fallback"
    return None, None


# ---------------------------------------------------------------------------
# INR / LPA salary (India roles) — display only, never used to prune
#
# Indian postings quote pay as "12 LPA" (lakhs per annum), "12-18 LPA",
# "₹12 lakhs", "INR 12.5 lacs", etc. 1 lakh = 100,000; "LPA" already means
# lakhs *per annum*. These are surfaced as a display string only — India
# roles are kept by region, never pruned on pay. Kept separate from the USD
# salary extractors so a rupee figure never gets mistaken for a USD range.
# ---------------------------------------------------------------------------

# Sane annual bounds in lakhs for an IC/early-to-mid role stamp.
_LPA_MIN = 1.0
_LPA_MAX = 200.0

_INR_CUR = r"(?:₹|\binr\b|\brs\.?)"
_LPA_UNIT = r"(?:lpa|lakhs?(?:\s*(?:per\s+annum|p\.?\s*a\.?))?|lacs?)"
_LPA_NUM = r"\d{1,3}(?:\.\d{1,2})?"

# "12-18 LPA", "12 to 18 lakhs", "₹12–18 LPA", "INR 8 - 12 lacs"
_INR_LPA_RANGE_RE = re.compile(
    rf"(?:{_INR_CUR}\s*)?({_LPA_NUM})\s*(?:[-–—]|to)\s*({_LPA_NUM})\s*{_LPA_UNIT}",
    re.I,
)
# "12 LPA", "₹12.5 lakhs", "INR 8 lacs per annum"
_INR_LPA_SINGLE_RE = re.compile(
    rf"(?:{_INR_CUR}\s*)?({_LPA_NUM})\s*{_LPA_UNIT}",
    re.I,
)


def _lpa_sane(n: float | None) -> bool:
    return n is not None and _LPA_MIN <= n <= _LPA_MAX


def _fmt_lpa(n: float) -> str:
    """Trim a trailing .0 so 12.0 → "12" but 12.5 stays "12.5"."""
    return f"{n:.1f}".rstrip("0").rstrip(".")


def extract_inr_salary(
    text: str | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
) -> dict | None:
    """Parse an Indian LPA / lakh salary into a display-only dict, or None.

    Returns ``{"min_lpa": float, "max_lpa": float | None, "display": str}``.
    ``display`` is UI-ready (``~₹12–18 LPA``). Never used to prune — India
    roles are kept by region, not pay. Rupee-only (never conflated with USD).
    """
    blob = _salary_blob(text, title=title, description=description)
    if not blob.strip():
        return None
    m = _INR_LPA_RANGE_RE.search(blob)
    if m:
        lo = float(m.group(1))
        hi = float(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        if _lpa_sane(lo) and _lpa_sane(hi):
            return {
                "min_lpa": lo,
                "max_lpa": hi,
                "display": f"~₹{_fmt_lpa(lo)}–{_fmt_lpa(hi)} LPA",
            }
    m = _INR_LPA_SINGLE_RE.search(blob)
    if m:
        lo = float(m.group(1))
        if _lpa_sane(lo):
            return {
                "min_lpa": lo,
                "max_lpa": None,
                "display": f"~₹{_fmt_lpa(lo)} LPA",
            }
    return None


# Company-name only. JDs often say "we do not accept staffing agencies".
STAFFING_DENY_HINTS = (
    "staffing",
    "recruiting agency",
    "talent acquisition partners",
    "randstad",
    "robert half",
    "adecco",
    "manpower",
    "kforce",
    "insight global",
)


def looks_like_staffing(company: str | None) -> bool:
    """True when the employer name is a staffing / recruiting agency."""
    blob = str(company or "").lower()
    return any(h in blob for h in STAFFING_DENY_HINTS)


# Explicit non-FT employment types from scrapers (JobSpy / Remotive / RSS).
# Unknown / empty / "fulltime" stay keepable — under-prune when unsure.
_EXCLUDED_JOB_TYPES = frozenset({
    "contract",
    "contractor",
    "c2c",
    "corpcorp",
    "corp2corp",
    "parttime",
    "temporary",
    "temp",
    "intern",
    "internship",
    "freelance",
    "volunteer",
    "seasonal",
})


def is_excluded_job_type(job_type: str | None) -> bool:
    """True when ``job_type`` is an explicit non-full-time employment label."""
    jt = re.sub(r"[\s_\-]+", "", str(job_type or "").lower())
    if not jt or jt in ("fulltime", "fullt", "permanent", "nan", "none", "null"):
        return False
    return jt in _EXCLUDED_JOB_TYPES


def should_keep_listing(
    *,
    title: str | None = None,
    location: str | None = None,
    company: str | None = None,
    description: str | None = None,
    url: str | None = None,
    job_type: str | None = None,
    regions=None,
) -> bool:
    """Discovery keep/drop: False = skip (seniority, region, clearance, YOE, citizen/GC).

    ``regions`` selects which geographies to keep (defaults to the discovery
    env / US-only). See ``location_matches_regions``.
    """
    return auto_delete_reason(
        title=title,
        location=location,
        company=company,
        description=description,
        url=url,
        job_type=job_type,
        regions=regions,
    ) is None


def auto_delete_reason(
    *,
    title: str | None = None,
    location: str | None = None,
    company: str | None = None,
    description: str | None = None,
    url: str | None = None,
    job_type: str | None = None,
    regions=None,
) -> str | None:
    """Return prune reason code, or None if the listing should stay active.

    ``regions`` = enabled discovery regions (``None`` → env / US-only). A
    listing outside every enabled region is dropped with reason
    ``"non_us_location"`` (kept as the stable code for "outside enabled
    regions" so existing prune tooling/labels don't need a schema change).
    """
    if is_excluded_title(title):
        return "management_track"
    if is_excluded_job_type(job_type):
        return "contract"
    if not location_matches_regions(location, regions):
        return "non_us_location"
    if requires_security_clearance(
        title=title,
        company=company,
        location=location,
        description=description,
        url=url,
    ):
        return "clearance_or_intel"
    if requires_excessive_experience(title=title, description=description):
        return "excessive_yoe"
    if requires_us_citizen_or_greencard(title=title, description=description):
        return "citizenship_or_greencard"
    if looks_like_staffing(company):
        return "staffing"
    return None
