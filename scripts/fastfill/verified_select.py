"""Verified combobox / select commit — shared by GH, Workday, Ashby, Flash.

Primary select path (enumerate-then-score — Elanco Degree A.A. lesson):
  Failure mode we fix: type-intended-first + loose soft-match committed
  Associate/A.A. when Master's was intended (shared token \"degree\", virtualized
  list showing A.* first).

  Target algorithm for Workday/GH typable selects:
  1. Open the dropdown (never Enter-to-submit)
  2. Enumerate listed options (scroll virtualized listboxes to load more)
  3. Similarity-score EVERY option against intended dummy aliases
     (exact > token > semantic); reject confusable pairs
     (Master's≠A.A./Associate; Bachelor's≠A.A.; US≠Australia; IL≠ID)
  4. Commit the best option only if score ≥ threshold; else leave blank/leftover
  5. Type-to-filter ONLY when the list is huge/empty after enumerate AND the
     filter token is a sanitized country/degree fragment (\"Master\", \"United
     States\") — never free-form push of wrong values
  6. Verify committed readback soft-matches intended — never placeholder_cleared alone

Dummy-only. Never submit.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, Iterable

_log = logging.getLogger("verified_select")

# Enumerate-first: prefer scoring the full option list before typing.
_ENUMERATE_FIRST_TYPES = frozenset(
    {
        "DEGREE",
        "SCHOOL",
        "DISCIPLINE",
        "MAJOR",
        "FIELD_OF_STUDY",
        "ADDRESS_COUNTRY",
        "ADDRESS_STATE",
        "PHONE_COUNTRY_CODE",
        "PHONE_DEVICE",
        "HOW_HEARD",
        "GENDER",
        "HISPANIC",
        "RACE",
        "VETERAN",
        "DISABILITY",
        "WORK_AUTH",
        "SPONSORSHIP",
        "RELOCATION",
        "NOTICE_PERIOD",
        "AGE_18",
        "SALARY_EXPECTED",
        "SALARY_CURRENT",
    }
)
_HUGE_OPTION_LIST = 48  # type-to-filter only when enumerate saw this many (or 0)
_DEGREE_COMMIT_MIN = 70  # never commit weak Associate-via-\"degree\" soft matches
_DEFAULT_COMMIT_MIN = 50

# UI placeholders that mean "nothing committed"
_PLACEHOLDER_RE = re.compile(
    r"^(select(\s*(one|an?\s+option|a\s+value|\.{0,3}))?|choose(\s+one)?|"
    r"start\s+typing.*|type\s+here.*|—|-|\u2014|\u2013)$",
    re.I,
)


def is_placeholder_select_value(text: str | None) -> bool:
    """True when display is blank or a Select… / Choose… placeholder."""
    t = (text or "").strip()
    if not t:
        return True
    if _PLACEHOLDER_RE.match(t):
        return True
    if t.lower().startswith("select") and len(t) < 24:
        return True
    return False


def is_location_field(field_type: str = "", label: str = "") -> bool:
    """True for Places / City+State+Country autocomplete fields."""
    ftype = (field_type or "").upper()
    if ftype in ("ADDRESS_CITY", "LOCATION", "EXPERIENCE_LOCATION"):
        return True
    lab = (label or "").lower()
    if re.search(r"\blocation\b|city\s*,\s*country|city\s+and\s+country", lab):
        return True
    return False


_SHORT_TOKEN_MAX = 3

# Canonical US state map — keep in verified_select so IL↔Illinois matching never
# depends on importing exp_workday_selectors (circular / silent ImportError).
_US_STATE_NAMES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


def expand_state_value(value: str) -> list[str]:
    """Return candidate strings for a state/province combobox (abbrev + full)."""
    v = (value or "").strip()
    if not v:
        return []
    out = [v]
    up = v.upper()
    if len(up) == 2 and up in _US_STATE_NAMES:
        full = _US_STATE_NAMES[up]
        out = [full, up]
    else:
        for abbr, name in _US_STATE_NAMES.items():
            if name.lower() == v.lower():
                out = [name, abbr]
                break
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x.lower() not in seen:
            seen.add(x.lower())
            uniq.append(x)
    return uniq


def _gender_polarity_side(text: str) -> str | None:
    """Return M/F/X when text is a gendered option; None otherwise.

    ATS3-005 / FILL2-001: ``male`` ⊂ ``female`` and ``man`` ⊂ ``woman`` must
    never soft-match across polarity.
    """
    sl = (text or "").lower()
    if not sl:
        return None
    if re.search(r"\bfemale\b|\bwom[ae]n\b|\bgirl\b", sl):
        return "F"
    if re.search(r"\bmale\b|\bmen\b|\bman\b|\bboy\b", sl):
        return "M"
    if re.search(r"\bnon[\s_-]*binary\b|\benby\b", sl):
        return "X"
    return None


def soft_value_match(expected: str, actual: str) -> bool:
    """Soft substring match with word-boundary guard for short tokens (state abbrevs).

    Prevents false positives such as ``"id" in "idaho"`` or ``"il" in "illinois"``
    matching the wrong field when the shorter token is not a whole word in the longer
    string. Tokens of length 4+ keep bidirectional substring **except** gender
    polarity (Male ⊄ Female) and confusable US states (Illinois ≠ Idaho).
    """
    exp = (expected or "").strip()
    act = (actual or "").strip()
    if not exp or not act:
        return False
    if states_are_confusable(exp, act):
        return False
    ge, ga = _gender_polarity_side(exp), _gender_polarity_side(act)
    if ge and ga and ge != ga:
        return False
    el, al = exp.lower(), act.lower()
    if el == al:
        return True
    if len(el) <= _SHORT_TOKEN_MAX or len(al) <= _SHORT_TOKEN_MAX:
        shorter, longer = (el, al) if len(el) <= len(al) else (al, el)
        if len(shorter) <= _SHORT_TOKEN_MAX:
            return bool(re.search(rf"\b{re.escape(shorter)}\b", longer))
        return False
    # Token-boundary substring — never accept ``male`` inside ``female``.
    def _bounded(needle: str, hay: str) -> bool:
        if not needle or needle not in hay:
            return False
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", hay))

    if _bounded(el, al) or _bounded(al, el):
        return True
    return False


# Job-board / platform tokens that must never be typed into Country Phone Code.
_NON_COUNTRY_SEARCH_RE = re.compile(
    r"\b("
    r"indeed|linkedin|glassdoor|monster|ziprecruiter|zip[\s_-]*recruiter|"
    r"dice|wellfound|lever|greenhouse|workday|ashby|"
    r"internet[\s_-]*job[\s_-]*boards?|job[\s_-]*boards?|"
    r"company[\s_-]*website|employee[\s_-]*referral|campus[\s_-]*recruit|"
    r"career[\s_-]*fair|recruiter|referral|social[\s_-]*media"
    r")\b",
    re.I,
)

_US_COUNTRY_NAME_RE = re.compile(
    r"united\s*states|\busa\b|\bu\.s\.a\.?\b|\bu\.s\.?\b",
    re.I,
)


def _strip_country_dial(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    try:
        from gh_select import country_name_from_dial_option, looks_like_dial_code_option

        if looks_like_dial_code_option(t):
            return (country_name_from_dial_option(t) or t).strip()
    except Exception:
        pass
    # Bare "Australia (+61)" / "United States of America (+1)"
    m = re.match(r"^(.+?)\s*\(\s*\+\d", t)
    if m:
        return m.group(1).strip()
    m = re.match(r"^(.+?)\s+\+\d", t)
    if m:
        return m.group(1).strip()
    return t


def is_us_country_name(text: str) -> bool:
    """True when text clearly names the United States (address or dial row)."""
    raw = (text or "").strip()
    if not raw:
        return False
    name = _strip_country_dial(raw).lower()
    if not name:
        return False
    # Reject NANP territories that are not the US mainland/USA label
    if re.search(
        r"anguilla|jamaica|barbados|bahamas|canada|bermuda|cayman|"
        r"puerto\s*rico|virgin\s*islands|american\s*samoa|guam",
        name,
    ):
        return False
    return bool(_US_COUNTRY_NAME_RE.search(name)) or name in {
        "us",
        "u.s",
        "u.s.",
        "usa",
        "u.s.a",
        "u.s.a.",
    }


def looks_like_country_option(text: str) -> bool:
    """Heuristic: dial-coded row or country-ish label (not device / how-heard)."""
    t = (text or "").strip()
    if not t or len(t) < 2:
        return False
    low = t.lower()
    if low in {
        "mobile",
        "cell",
        "cellular",
        "home",
        "work",
        "office",
        "landline",
        "telephone",
        "fax",
        "other",
        "yes",
        "no",
    }:
        return False
    # Capco/GH EEO race menus: multi-word decline / ethnicity labels must NEVER
    # count as countries — reject_confusable_country_option was dropping
    # "I don't wish to answer" and leaving only Asian/White (highlight thrash).
    if re.search(
        r"decline|prefer\s+not|wish\s+to\s+answer|want\s+to\s+answer|"
        r"self[\s_-]*identif|rather\s+not|choose\s+not|"
        r"\brace\b|ethnic|hispanic|latino|alaska\s+native|"
        r"african\s+american|pacific\s+islander|two\s+or\s+more|"
        r"american\s+indian|native\s+hawaiian|asian\b|"
        r"non[\s_-]*binary|\bgender\b|\bsex\b|veteran|disabilit|"
        r"\bmale\b|\bfemale\b|\bwhite\b|\bblack\b",
        low,
    ):
        return False
    if _NON_COUNTRY_SEARCH_RE.search(t):
        return False
    try:
        from gh_select import looks_like_dial_code_option

        if looks_like_dial_code_option(t):
            return True
    except Exception:
        if re.search(r"\(\s*\+\d{1,4}\s*\)|\+\s*\d{1,4}\b", t):
            return True
    name = _strip_country_dial(t)
    # Prefer multi-word countries or well-known single-token countries
    if " " in name.strip() and re.fullmatch(r"[A-Za-z][A-Za-z .'-]{2,60}", name):
        return True
    if name.lower() in {
        "australia",
        "austria",
        "canada",
        "china",
        "france",
        "germany",
        "india",
        "ireland",
        "israel",
        "italy",
        "japan",
        "mexico",
        "netherlands",
        "singapore",
        "spain",
        "switzerland",
        "taiwan",
        "thailand",
        "ukraine",
        "anguilla",
        "jamaica",
        "barbados",
        "bahamas",
        "bermuda",
        "guam",
    }:
        return True
    if is_us_country_name(name):
        return True
    return False


def reject_confusable_country_option(intent: str, option: str) -> bool:
    """True when intent and option are different countries (US ≉ Australia).

    Blocks semantic false-positives that scored Australia≈United States at 70
    and committed the wrong address / phone dial country (Morningstar Workday).

    Never applies to EEO decline / race-ethnicity prose — Capco GH race menus
    were filtered to Asian/White only when intent was \"Decline to self identify\".
    """
    intent_s = (intent or "").strip()
    option_s = (option or "").strip()
    if not intent_s or not option_s:
        return False
    try:
        from gh_select import is_decline_like_alias

        if is_decline_like_alias(intent_s) or is_decline_like_alias(option_s):
            return False
    except Exception:
        if re.search(
            r"decline|prefer\s+not|wish\s+to\s+answer|want\s+to\s+answer",
            f"{intent_s} {option_s}",
            re.I,
        ):
            return False
    i_name = _strip_country_dial(intent_s)
    o_name = _strip_country_dial(option_s)
    if not i_name or not o_name:
        return False
    il, ol = i_name.lower().strip(), o_name.lower().strip()
    if il == ol:
        return False

    def _strong_country(raw: str, name: str) -> bool:
        """Dial-coded or known country — not weak multi-word EEO prose."""
        if is_us_country_name(name) or is_us_country_name(raw):
            return True
        try:
            from gh_select import looks_like_dial_code_option

            if looks_like_dial_code_option(raw):
                return True
        except Exception:
            if re.search(r"\(\s*\+\d{1,4}\s*\)|\+\s*\d{1,4}\b", raw):
                return True
        # Known single-token / US long-form only (not bare multi-word heuristic)
        return bool(
            looks_like_country_option(raw)
            and (
                is_us_country_name(name)
                or " " not in name.strip()
                or re.search(
                    r"united\s+states|united\s+kingdom|saudi\s+arabia|"
                    r"south\s+africa|new\s+zealand|south\s+korea|"
                    r"costa\s+rica|hong\s+kong|puerto\s+rico",
                    name,
                    re.I,
                )
            )
        )

    # Require a strong country signal on at least one side — Decline↔Asian must
    # never enter the confusable-country reject path.
    if not (_strong_country(intent_s, i_name) or _strong_country(option_s, o_name)):
        return False
    # Lexical containment OK for US long forms (United States ⊂ … of America)
    contained = il in ol or ol in il
    us_i, us_o = is_us_country_name(i_name), is_us_country_name(o_name)
    if us_i and not us_o:
        return True
    if us_o and not us_i and looks_like_country_option(intent_s):
        return True
    if contained:
        return False
    # Distinct dial-coded / country-named rows must never soft-or-semantic merge
    if looks_like_country_option(intent_s) and looks_like_country_option(option_s):
        return True
    return False


def is_safe_phone_country_search(query: str) -> bool:
    """False when query is a job-board / platform / non-country token."""
    q = (query or "").strip()
    if not q:
        return False
    if _NON_COUNTRY_SEARCH_RE.search(q):
        return False
    if re.search(r"https?://|www\.|@", q, re.I):
        return False
    # Must look like a country name or dial code — not company/board free text
    if looks_like_country_option(q) or is_us_country_name(q) or re.search(
        r"^\+?\d{1,4}$", q
    ):
        return True
    return False


def phone_country_code_search_query(value: str | None = None) -> str:
    """Search string for Country Phone Code — always a country/dial token.

    Dummy / US profile runs must search ``United States`` (to land +1), never
    how-heard / job-board names that Flash or wrong-field thrash may inject.
    """
    v = (value or "").strip()
    if v and is_safe_phone_country_search(v) and (
        is_us_country_name(v) or re.search(r"^\+?1$", v)
    ):
        return "United States"
    if v and is_safe_phone_country_search(v) and not is_us_country_name(v):
        # Explicit non-US country only when caller intentionally passed one;
        # dummy autofill always prefers US — coerce unsafe / board tokens only.
        if not _NON_COUNTRY_SEARCH_RE.search(v):
            return _strip_country_dial(v) or "United States"
    return "United States"


def phone_country_code_candidates(values: dict | None = None) -> list[str]:
    """Ordered Country Phone Code option candidates for dummy US fills."""
    preferred = None
    if values:
        for key in (
            "PHONE_COUNTRY_CODE",
            "phone_country_code",
            "ADDRESS_COUNTRY",
            "country",
        ):
            raw = values.get(key) if isinstance(values, dict) else None
            if raw and is_safe_phone_country_search(str(raw)):
                preferred = str(raw).strip()
                break
    search = phone_country_code_search_query(preferred)
    out = [
        "United States of America (+1)",
        "United States (+1)",
        "United States of America",
        "United States",
        "+1",
    ]
    if search and search not in out:
        out.insert(0, search)
    # Dedupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for c in out:
        k = c.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    return uniq


# Confusable US state pairs — typing "IL" / filtering "I…" must never accept Idaho.
# ATS3-007: expand abbrev collisions (VA/VT, MI/MN, ME/MD, NE/NV, CO/CT, …).
_STATE_CONFUSABLE_PAIRS: tuple[frozenset[str], ...] = (
    frozenset({"illinois", "il", "idaho", "id"}),
    frozenset({"mississippi", "ms", "missouri", "mo"}),
    frozenset({"arkansas", "ar", "arizona", "az"}),
    frozenset({"alabama", "al", "alaska", "ak"}),
    frozenset({"north carolina", "nc", "north dakota", "nd"}),
    frozenset({"south carolina", "sc", "south dakota", "sd"}),
    frozenset({"virginia", "va", "vermont", "vt"}),
    frozenset({"michigan", "mi", "minnesota", "mn"}),
    frozenset({"maine", "me", "maryland", "md"}),
    frozenset({"nebraska", "ne", "nevada", "nv"}),
    frozenset({"colorado", "co", "connecticut", "ct"}),
    frozenset({"massachusetts", "ma", "maine", "me"}),
    frozenset({"washington", "wa", "wisconsin", "wi"}),
    frozenset({"kansas", "ks", "kentucky", "ky"}),
)


def _state_tokens(text: str) -> set[str]:
    """Normalize to abbrev + full name tokens for a state-ish string."""
    v = (text or "").strip()
    if not v:
        return set()
    out: set[str] = {v.lower()}
    for x in expand_state_value(v):
        out.add(x.lower())
    # Strip "State of " / trailing codes
    out.add(re.sub(r"^state\s+of\s+", "", v.lower()).strip())
    return {t for t in out if t}


def states_are_confusable(intent: str, option: str) -> bool:
    """True when intent and option are different confusable US states (IL vs ID)."""
    a = _state_tokens(intent)
    b = _state_tokens(option)
    if not a or not b:
        return False
    if a & b:
        return False  # same state family
    for pair in _STATE_CONFUSABLE_PAIRS:
        if (a & pair) and (b & pair):
            return True
    return False


def reject_confusable_state_option(intent: str, option: str) -> bool:
    """True when ``option`` must not be clicked for ``intent`` (Idaho for Illinois)."""
    if not intent or not option:
        return False
    return states_are_confusable(intent, option)


def filter_options_preserving_indices(
    texts: list[str],
    intent: str,
    *,
    reject_fn: Callable[[str, str], bool] | None = None,
) -> tuple[list[str], list[int]]:
    """Filter confusable options; return (filtered_texts, original_indices).

    ATS-001/015: ranking indices must map back to the unfiltered locator.
    Never falls back to the full list when every option is rejected — empty
    filtered means no click (do not reintroduce Idaho for Illinois).
    ATS2-001: ``texts`` may contain empty/placeholder slots so indices stay
    aligned with ``locator.nth(i)`` — those slots are skipped here.
    """
    reject = reject_fn or reject_confusable_state_option
    filtered: list[str] = []
    orig: list[int] = []
    for i, t in enumerate(texts or []):
        if not t or is_placeholder_select_value(t):
            continue
        if intent and reject(intent, t):
            continue
        filtered.append(t)
        orig.append(i)
    return filtered, orig


def remap_ranked_to_original(
    ranked: list[tuple[int, int, str]],
    orig_indices: list[int],
) -> list[tuple[int, int, str]]:
    """Map rank indices from a filtered list back to original locator indices."""
    out: list[tuple[int, int, str]] = []
    for score, fi, text in ranked or []:
        if 0 <= fi < len(orig_indices):
            out.append((score, orig_indices[fi], text))
    return out


def _norm_digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


_WORKDAY_INTERNAL_ID_RE = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
    re.I,
)


def looks_like_workday_internal_id(text: str) -> bool:
    """True when readback is a Workday hash/UUID (not human label text)."""
    t = (text or "").strip()
    if not t or " " in t:
        return False
    if _WORKDAY_INTERNAL_ID_RE.match(t):
        return True
    if re.fullmatch(r"[a-f0-9]{32}", t, re.I):
        return True
    return False


def degree_display_matches_intent(shown: str, intent: str) -> bool:
    """Master's/Master shown value matches Master's intent — ignore hash-id readbacks."""
    s = (shown or "").strip()
    want = (intent or "").strip()
    if not s or not want:
        return False
    if looks_like_workday_internal_id(s):
        return False
    sl, wl = s.lower(), want.lower()
    if "master" in wl and re.search(r"\bmaster", sl):
        return True
    return value_matches_readback(want, s, mode="combobox")


def value_matches_readback(expected: str, actual: str, *, mode: str = "fill") -> bool:
    """True if read-back is non-empty and soft-matches intended value.

    Shared by fast_fill (mode=fill) and Workday selectors (fill/combobox/phone).
    Ashby placeholders like ``Type here...`` never match.
    Greenhouse Country* commits as ``+1`` after picking ``United States +1``.
    """
    exp = (expected or "").strip()
    act = (actual or "").strip()
    if not exp:
        return False
    if mode == "combobox":
        if not act or act.lower() in ("select one", "select", "—", "-"):
            return False
    else:
        try:
            from ashby_widgets import is_empty_ui_value

            if is_empty_ui_value(act):
                return False
        except Exception:
            if not act:
                return False
    # GH Dragos Country*: intended United States, shown +1 / United States +1
    try:
        from gh_select import (
            country_name_from_dial_option,
            is_dial_only_display,
            looks_like_dial_code_option,
        )

        if looks_like_dial_code_option(act) or is_dial_only_display(act):
            country = country_name_from_dial_option(act) or ""
            if country and soft_value_match(exp, country):
                return True
            # Bare +1: accept when expected is a US country alias
            if is_dial_only_display(act) and re.search(
                r"united\s*states|\busa\b|\bus\b", exp, re.I
            ):
                return True
        if looks_like_dial_code_option(exp):
            country = country_name_from_dial_option(exp)
            if country and soft_value_match(country, act):
                return True
    except Exception:
        pass
    if mode == "phone" or _norm_digits(exp):
        ed, ad = _norm_digits(exp), _norm_digits(act)
        if ed and ad and (ed in ad or ad in ed or ed[-7:] == ad[-7:]):
            return True
    if soft_value_match(exp, act):
        return True
    try:
        if field_of_study_taxonomy_match(exp, act):
            return True
    except Exception:
        pass
    for cand in expand_state_value(exp):
        if cand != exp and soft_value_match(cand, act):
            return True
    ed, ad = _norm_digits(exp), _norm_digits(act)
    if ed and ad and (ed in ad or ad in ed or ed[-7:] == ad[-7:]):
        return True
    return False


def is_multiselect_uncommitted(shown: str | None) -> bool:
    """True when Workday/Ashby multi-select UI shows no committed selection."""
    t = (shown or "").strip().lower()
    if not t:
        return True
    if "0 items selected" in t:
        return True
    if re.search(r"\b0\s+item(s)?\s+selected\b", t):
        return True
    return False


def multiselect_has_chip(shown: str | None) -> bool:
    """True when Workday multi-select readback shows ≥1 committed item."""
    t = (shown or "").strip().lower()
    if not t or is_multiselect_uncommitted(t):
        return False
    if re.search(r"\b([1-9]\d*)\s+items?\s+selected\b", t):
        return True
    return False


def workday_wrap_text_has_chip(wrap_text: str | None) -> bool:
    """True when formField innerText shows committed chip chrome or a chip label."""
    s = (shown or "").strip() if (shown := wrap_text) else ""
    if not s or is_multiselect_uncommitted(s):
        return False
    if multiselect_has_chip(s):
        return True
    if is_placeholder_select_value(s):
        return False
    sl = s.lower()
    if "select one" in sl or sl.startswith("search"):
        return False
    # NXP-class FoS: bare chip label at top (Science-Computer) without "N items selected"
    if re.search(r"\bfield of study\b", sl):
        tail = re.sub(r"(?i)^field of study\*?\s*", "", s).strip()
        if tail and len(tail) <= 80 and "select" not in tail.lower():
            return True
    return False


def _fos_taxonomy_tokens(text: str) -> set[str]:
    return {
        t
        for t in re.split(r"[\s\-/&,]+", (text or "").lower())
        if t and t not in {"of", "and", "the", "other"}
    }


def field_of_study_taxonomy_match(intent: str, shown: str) -> bool:
    """Workday FoS taxonomy tokens (Science-Computer) ≈ Computer Science intent."""
    if soft_value_match(intent, shown):
        return True
    it, st = _fos_taxonomy_tokens(intent), _fos_taxonomy_tokens(shown)
    if not it or not st:
        return False
    if {"computer", "science"} <= it and {"computer", "science"} <= st:
        return True
    if len(it & st) >= 2:
        return True
    if "computer" in it and "computer" in st:
        return True
    return False


_FOS_GENERIC_ALIASES = frozenset({"other", "cs"})


def _fos_intent_matches_candidate(candidate: str, shown: str) -> bool:
    """Match FoS chip readback to intent — never via bare ``Other`` ⊂ ``Arts-Other``.

    Always score against the committed chip label only — open listbox option
    soup must not fake a Science-Computer match while Arts-Other is selected.
    """
    c = (candidate or "").strip()
    s_raw = (shown or "").strip()
    if not c or not s_raw:
        return False
    s = fos_committed_chip_label(s_raw) or s_raw
    cl = c.lower()
    # ``Other`` alias is for typing fallback only — never verifies Arts-Other chip
    if cl in _FOS_GENERIC_ALIASES:
        return False
    if field_of_study_taxonomy_match(c, s) or soft_value_match(c, s):
        return True
    if len(cl) <= 3:
        return False
    sl = s.lower()
    # Hyphenated Workday taxonomy (Science-Computer, Arts-Other) — token match only
    if re.search(r"[a-z]+-[a-z]+", sl, re.I):
        return field_of_study_taxonomy_match(c, s)
    return cl in sl


def field_of_study_committed(
    shown: str | None,
    candidates: Iterable[str] | None = None,
    *,
    dom_chip: bool = False,
) -> bool:
    """True when Workday Field of Study shows a committed chip matching intent.

    Delegates to ``field_done.field_is_done_from_readback`` — single source of truth.
    """
    from field_done import field_is_done_from_readback

    cands = [str(c).strip() for c in (candidates or []) if str(c or "").strip()]
    meta: dict = {"type": "FIELD_OF_STUDY", "dom_chip": dom_chip}
    if cands:
        meta["aliases_tried"] = cands
    intent = cands[0] if cands else None
    return field_is_done_from_readback(shown, meta, intent).ok


def fos_committed_chip_label(shown: str | None) -> str:
    """Extract committed FoS chip label; ignore open listbox option soup.

    Live gym wrong-chip fixture wrap text looks like::
      Field of Study* Arts-Other × Arts-Other Science-Computer Computer Science …
    Matching intent against the full wrap falsely reports Science-Computer done
    while Arts-Other is still the chip. Prefer the label before × / first token.
    """
    s = re.sub(r"\s+", " ", (shown or "").strip())
    if not s:
        return ""
    s2 = re.sub(r"(?i)^field\s+of\s+study\*?\s*", "", s).strip()
    s2 = re.sub(r"(?i)^discipline\*?\s*", "", s2).strip()
    s2 = re.sub(r"(?i)^major\*?\s*", "", s2).strip()
    m = re.search(r"(?i)\b\d+\s+items?\s+selected[,:]?\s*(.+)$", s2)
    if m:
        # "1 item selected, Arts-Other Arts-Other" → Arts-Other
        tail = m.group(1).strip()
        first = re.split(r"[,/|]", tail)[0].strip()
        # Duplicate chip echo: "Arts-Other Arts-Other"
        parts = first.split()
        if len(parts) >= 2 and parts[0].lower() == parts[1].lower():
            return parts[0][:80]
        # Hyphenated taxonomy takes precedence over trailing option soup
        hy = re.match(r"^([A-Za-z]+-[A-Za-z]+(?:-[A-Za-z]+)?)", first)
        if hy:
            return hy.group(1)[:80]
        return first[:80]
    if "×" in s2:
        return s2.split("×", 1)[0].strip()[:80]
    # Bare short chip (Science-Computer) — no option soup
    if len(s2) <= 80 and s2.count(" ") <= 3:
        return s2[:80]
    # Option soup without ×: first taxonomy token only
    hy = re.match(r"^([A-Za-z]+-[A-Za-z]+(?:-[A-Za-z]+)?)", s2)
    if hy:
        return hy.group(1)[:80]
    return s2.split()[0][:80] if s2 else ""


async def read_workday_formfield_chip(locator) -> str:
    """Read formField wrap text when deleteSelected / selectedItem chip is present.

    Prefers ``selectedItem`` / pill text only so open ``promptOption`` listbox
    labels cannot pollute FoS intent matching (Arts-Other + open CS list).
    Hidden chip wraps (display:none) must return empty — not a false commit.
    """
    try:
        raw = await locator.evaluate(
            """(el) => {
              const wrap = el.closest('[data-automation-id*="formField"]') || el;
              const isShown = (node) => {
                if (!node) return false;
                let p = node;
                while (p && p.nodeType === 1) {
                  const st = window.getComputedStyle(p);
                  if (st.display === 'none' || st.visibility === 'hidden') return false;
                  p = p.parentElement;
                }
                return true;
              };
              // Prefer committed chip node text — never full wrap with listbox soup.
              const selected = wrap.querySelector(
                '[data-automation-id*="selectedItem"], [data-automation-id*="selectedChip"], '
                + '[data-automation-id*="pill"]'
              );
              if (selected && isShown(selected)) {
                const clone = selected.cloneNode(true);
                clone.querySelectorAll('button').forEach((b) => b.remove());
                const t = (clone.innerText || clone.textContent || '')
                  .replace(/\\s+/g, ' ').trim();
                if (t) return t.slice(0, 120);
              }
              const chip = wrap.querySelector(
                '[data-automation-id="deleteSelected"], '
                + 'button[aria-label*="delete" i], button[aria-label*="remove" i], '
                + '[aria-label*="clear selection" i]'
              );
              // Hidden delete chrome = no committed chip (empty / display:none wrap).
              if (chip && !isShown(chip)) return '';
              const inp = wrap.querySelector('input:not([type="hidden"])');
              const filterEmpty = !inp || !(inp.value || '').trim();
              // innerText excludes display:none descendants — preferred for soup-free read.
              const wt = (wrap.innerText || wrap.textContent || '')
                .replace(/\\s+/g, ' ').trim();
              if (chip && isShown(chip) && wt) {
                // Truncate at × so promptOption labels after delete chrome are ignored.
                const beforeX = wt.split('×')[0].replace(/\\s+/g, ' ').trim();
                return (beforeX || wt).slice(0, 240);
              }
              // NXP-class FoS: bare chip label (Science-Computer) with empty filter
              if (filterEmpty && wt && !/select one/i.test(wt)) {
                const tail = wt.replace(/^Field of Study\\*?\\s*/i, '').trim();
                if (tail && tail.length <= 80 && !/^(search|type)/i.test(tail)
                    && /[A-Za-z]-[A-Za-z]/.test(tail)) {
                  return wt.slice(0, 240);
                }
              }
              // How-Heard / source: "N items selected, Leaf" chrome without
              // deleteSelected (battle gym + some tenants). Strip open listbox
              // soup so promptOption labels cannot fake a chip.
              const clone = wrap.cloneNode(true);
              clone.querySelectorAll(
                '[role="listbox"], [data-automation-id="promptLeafNode"], .menu'
              ).forEach((n) => n.remove());
              const clean = (clone.innerText || clone.textContent || '')
                .replace(/\\s+/g, ' ').trim();
              if (/\\b([1-9]\\d*)\\s+items?\\s+selected\\b/i.test(clean)) {
                return clean.slice(0, 240);
              }
              return '';
            }"""
        )
        return str(raw or "").strip()
    except Exception:
        return ""


def is_uncommitted_filter_text(
    shown: str | None,
    typed_frag: str | None,
    *,
    picked: str | None = None,
    from_input: bool = False,
) -> bool:
    """True when display still looks like typed filter text, not a committed option."""
    s = (shown or "").strip()
    frag = (typed_frag or "").strip()
    if not s:
        return False
    sl = s.lower()
    # Workday chip chrome: "1 item selected, Indeed" is committed, not a place filter
    if multiselect_has_chip(s):
        return False
    # Picked option that soft-matches display = committed token (Indeed chip / single-value)
    # — never thrash aliases because shown == typed filter string after a real pick.
    if picked and soft_value_match(picked, s):
        return False
    # Location filter paste "City, State, Country" is never committed from input alone
    if from_input and "," in s and len(s) >= 12:
        return True
    if "," in s and len(s) >= 12 and re.search(r"[a-z]{2,}\s*,\s*[a-z]{2,}", sl):
        # OFCCP / EEO option prose ("No, I do not have a disability…") is committed.
        # Do NOT blanket-exempt all "Yes,/No," lines — location essays also start that way.
        if re.search(
            r"disabilit|veteran|hispanic|latino|\bgender\b|decline|"
            r"wish to answer|want to answer|prefer not|sponsorship|"
            r"authorized|citizen|clearance|protected",
            sl,
        ):
            return False
        # Comma-separated place line without proof of list pick
        if not picked or sl != (picked or "").strip().lower():
            return True
    if picked and sl == (picked or "").strip().lower() and not from_input:
        return False
    # Long committed EEO option labels are OK; other long comma/space blobs are filter thrash
    if len(s) > 48 and (s.count(" ") >= 4 or "," in s):
        if re.search(
            r"disabilit|veteran|hispanic|latino|\bgender\b|decline|"
            r"wish to answer|want to answer|prefer not|sponsorship|"
            r"authorized|citizen|clearance|protected",
            sl,
        ):
            return False
        return True
    if not frag:
        return False
    fl = frag.lower()
    if sl == fl:
        return True
    if len(fl) >= 6 and (sl.startswith(fl) or fl.startswith(sl)):
        return True
    return False


def is_how_heard_category_option(text: str | None) -> bool:
    """True when option text is a Workday how-heard category/subsection header."""
    try:
        from fill_verify import is_how_heard_category_option as _cat

        return bool(_cat(text))
    except Exception:
        t = " ".join(str(text or "").strip().lower().split())
        return t in {
            "internet job board",
            "job board",
            "job boards",
            "social media",
            "social network",
            "internet",
        }


def looks_like_phone_country_or_address_chip(text: str | None) -> bool:
    """True when chrome is a dial/address country chip — never a how-heard source.

    Live Morningstar/Elanco: bare ``multiSelectContainer`` often wraps Country
    Phone Code (``United States (+1)``). How-heard must not treat that as a
    committed source, and must never type ``Indeed`` into that filter.
    """
    s = (text or "").strip()
    if not s:
        return False
    low = s.lower()
    # Explicit phone / country field labels in wrap text
    if re.search(
        r"country\s*phone\s*code|phone\s*country|calling\s*code|dial\s*code|"
        r"countryphonecode|phonenumber--countryphonecode|"
        r"\baddress\s*country\b|country\s*/\s*region",
        low,
    ):
        return True
    # Dial-coded rows: "United States (+1)", "Australia (+61)"
    if re.search(r"\(\s*\+\d{1,4}\s*\)|\+\s*\d{1,4}\b", s):
        # Exclude how-heard leaves that somehow include + (none today)
        if not _NON_COUNTRY_SEARCH_RE.search(s):
            return True
    # Country-only chip without job-board tokens
    if looks_like_country_option(s) and not _NON_COUNTRY_SEARCH_RE.search(s):
        # "1 item selected, United States" without (+N) still dial/address
        if re.search(
            r"united\s*states|\baustralia\b|\bcanada\b|\bunited\s*kingdom\b|\busa\b",
            low,
        ) and not re.search(
            r"indeed|linkedin|glassdoor|job\s*board|referral|hear about",
            low,
        ):
            return True
    return False


_PHONE_COUNTRY_EMPTY_ROW_RE = re.compile(
    r"countryphonecode|phonenumber--country|phone.?country|phonecountry|"
    r"country\s*phone\s*code|dial\s*code|calling\s*code",
    re.I,
)

# Shared Workday probe: Country Phone Code chip while filter input stays empty.
PHONE_COUNTRY_WRAP_COMMITTED_JS = """(wrap) => {
  if (!wrap) return false;
  const aid = (wrap.getAttribute('data-automation-id') || '').toLowerCase();
  const isPhoneCountry = /countryphonecode|phone.?country|phonenumber--country/.test(aid)
    || /country\\s*phone\\s*code|phone\\s*country/i.test(
      (wrap.innerText || wrap.textContent || '').slice(0, 140)
    );
  if (!isPhoneCountry) return false;
  const wt = (wrap.innerText || wrap.textContent || '').replace(/\\s+/g, ' ');
  if (/united\\s*states(\\s*of\\s*america)?\\s*\\(\\s*\\+\\s*1\\s*\\)/i.test(wt)) return true;
  if (/united\\s*states(\\s*of\\s*america)?/i.test(wt) && /\\(\\s*\\+\\s*1\\s*\\)/.test(wt)) return true;
  const chip = wrap.querySelector(
    '[data-automation-id="deleteSelected"], [data-automation-id*="selectedItem"], '
    + '[aria-label*="delete" i], [aria-label*="remove" i], button[aria-label*="clear" i]'
  );
  return !!(chip && /united\\s*states|\\(\\s*\\+\\d{1,4}/i.test(wt));
}"""

PHONE_COUNTRY_FIELD_PROBE_JS = """() => {
  const probe = """ + PHONE_COUNTRY_WRAP_COMMITTED_JS + """;
  const sels = [
    '[data-automation-id*="formField-countryPhoneCode" i]',
    '[data-automation-id*="formField-phoneNumber--countryPhoneCode" i]',
    '[data-automation-id*="formField-phoneCountry" i]',
    '[data-automation-id*="countryPhoneCode" i]',
    '[data-automation-id*="phoneNumber--countryPhoneCode" i]',
  ];
  for (const sel of sels) {
    for (const field of document.querySelectorAll(sel)) {
      const wrap = field.closest('[data-automation-id*="formField"]') || field;
      if (probe(wrap)) {
        return (wrap.innerText || wrap.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 160);
      }
    }
  }
  return '';
}"""


def is_committed_us_phone_country_readback(text: str | None) -> bool:
    """True when chip/readback is a committed US Country Phone Code (+1).

    NXP/Markel-class Workday: ``United States of America (+1)`` with remove
    chip — filter input stays empty but field is visually complete.
    """
    s = (text or "").strip()
    if not s or not is_us_country_name(s):
        return False
    if re.search(r"\(\s*\+\s*1\s*\)", s):
        return True
    return bool(looks_like_phone_country_or_address_chip(s))


def phone_country_empty_row(row: dict | None) -> bool:
    """True when a required_empty / gap row refers to Country Phone Code."""
    if not isinstance(row, dict):
        return False
    blob = " ".join(
        [
            str(row.get("id") or ""),
            str(row.get("label") or ""),
            str(row.get("automation_id") or ""),
        ]
    )
    return bool(_PHONE_COUNTRY_EMPTY_ROW_RE.search(blob))


def phone_country_verified_snips_from_report(report: dict | None) -> list[str]:
    """Committed Country Phone Code readbacks from verified fill rows."""
    if not isinstance(report, dict):
        return []
    out: list[str] = []
    for f in report.get("filled") or []:
        if not isinstance(f, dict):
            continue
        ftype = str(f.get("type") or "")
        aid = str(f.get("automation_id") or "").lower()
        if ftype not in ("PHONE_COUNTRY_CODE", "phone_country_code") and not (
            "countryphonecode" in aid or "phonenumber--countryphonecode" in aid
        ):
            continue
        if not f.get("verified"):
            continue
        rb = str(f.get("readback") or "").strip()
        if rb and is_committed_us_phone_country_readback(rb):
            out.append(rb)
    return out


def filter_phone_country_false_empties(
    rows: list[dict] | None,
    live_snip: str | None = None,
    *,
    fallback_snips: Iterable[str] | None = None,
) -> list[dict]:
    """Drop phone-country required_empty rows when live chip shows US (+1)."""
    if not rows:
        return []
    committed = is_committed_us_phone_country_readback(live_snip)
    if not committed and fallback_snips:
        for snip in fallback_snips:
            if is_committed_us_phone_country_readback(snip):
                committed = True
                break
    if not committed:
        for r in rows:
            if phone_country_empty_row(r) and is_committed_us_phone_country_readback(
                str(r.get("label") or "")
            ):
                committed = True
                break
    if not committed:
        return list(rows)
    return [r for r in rows if not phone_country_empty_row(r)]


async def read_phone_country_field_snip(page) -> str:
    """Live DOM: Country Phone Code wrap text when US (+1) chip is committed."""
    try:
        return str(await page.evaluate(PHONE_COUNTRY_FIELD_PROBE_JS) or "").strip()
    except Exception:
        return ""


def how_heard_scope_reject_aid(automation_id: str | None) -> bool:
    """True when an automation-id is phone/address country — not how-heard."""
    aid = (automation_id or "").lower()
    if not aid:
        return False
    return bool(
        re.search(
            r"countryphonecode|phonenumber--countryphonecode|phone.?country|"
            r"phonecountry|calling.?code|dial.?code|"
            r"addresssection_country(?!region)|formfield-country(?!region)",
            aid,
        )
    )


def committed_how_heard_leaf(shown: str | None) -> str | None:
    """Canonical valid source leaf if *shown* names one (CareerBuilder, Glassdoor, …).

    Used to treat any known job-board chip as done — do not reopen to fight
    CareerBuilder vs Glassdoor. Unknown agency chips (Antal Talent) return None.
    """
    s = (shown or "").strip()
    if not s or is_multiselect_uncommitted(s):
        return None
    if looks_like_phone_country_or_address_chip(s):
        return None
    try:
        from fill_verify import (
            HOW_HEARD_SOURCE_PRIORITY,
            how_heard_option_matches_priority,
            is_how_heard_category_option as _cat,
        )
    except Exception:
        return None
    for canonical, _patterns in HOW_HEARD_SOURCE_PRIORITY:
        try:
            if not how_heard_option_matches_priority(canonical, s):
                continue
        except Exception:
            continue
        if _cat(canonical):
            # GH may commit "Job Board" / "Other" as a chip; nav headers are not leaves.
            if canonical.lower() in ("job board", "other") and multiselect_has_chip(s):
                return canonical
            continue
        return canonical
    return None


def how_heard_source_committed(
    shown: str | None,
    candidates: Iterable[str] | None = None,
) -> bool:
    """True when Workday How-Heard / source multi-select has a committed chip/token.

    Requires ``N items selected`` chrome (≥1). Category headers and bare filter
    text (``Indeed`` typed into the search box, ``Internet job board``) are
    never enough — Walmart hierarchical menus leave that text without a chip.

    Any *valid* source leaf (LinkedIn, Indeed, CareerBuilder, Glassdoor, …) is
    done — do not reopen to swap siblings. Unknown chips (Antal Talent) still
    fail when callers passed priority leaves.

    Country Phone Code / Address Country chips must never count as how-heard.
    """
    s = (shown or "").strip()
    if not s or is_multiselect_uncommitted(s):
        return False
    if looks_like_phone_country_or_address_chip(s):
        return False
    if not multiselect_has_chip(s):
        # No chip chrome → typed filter / category header / single-value lookalike
        return False
    # Chip present + known valid source → done (CareerBuilder vs Glassdoor: keep).
    if committed_how_heard_leaf(s):
        return True
    cands = [str(c).strip() for c in (candidates or []) if str(c or "").strip()]
    if not cands:
        return True
    leaf_cands = [c for c in cands if not is_how_heard_category_option(c)] or cands
    sl = s.lower()
    for c in leaf_cands:
        if soft_value_match(c, s) or c.lower() in sl:
            return True
    # Callers passed priority leaves — unrelated chips (e.g. Antal Talent when
    # LinkedIn was intended) are NOT committed; do not stop thrash on wrong chip.
    return False



async def settle_open_listbox(page) -> None:
    """Close open prompt/listbox menus after a successful commit (never Submit)."""
    try:
        await page.evaluate(
            """() => {
              document.querySelectorAll('[aria-expanded="true"]').forEach((el) => {
                if (el && el.blur) el.blur();
              });
              document.body.click();
              const active = document.activeElement;
              if (active && active.blur) active.blur();
            }"""
        )
        await page.wait_for_timeout(120)
    except Exception:
        pass
    try:
        from captcha_pause import press_escape_unless_captcha

        await press_escape_unless_captcha(page)
    except Exception:
        # FILL3-019: never raw Escape — fail closed when captcha gate unavailable.
        pass


async def fos_widget_expanded(page) -> bool:
    """True when a FoS/Major combobox still has aria-expanded=true."""
    try:
        return bool(
            await page.evaluate(
                """() => {
                  const inFos = (el) => {
                    const w = el.closest('[data-automation-id]');
                    const id = (w && w.getAttribute('data-automation-id') || '').toLowerCase();
                    const txt = (w && (w.innerText || w.textContent) || '').toLowerCase();
                    return id.includes('fieldofstudy') || id.includes('discipline')
                      || id.includes('major') || txt.includes('field of study');
                  };
                  return [...document.querySelectorAll('[aria-expanded="true"]')]
                    .some(inFos);
                }"""
            )
        )
    except Exception:
        return False


async def force_close_fos_widget(page) -> None:
    """Close FoS/Major dropdown chrome — Escape, blur, body click, Tab.

    Workday portals ``promptOption`` listboxes under ``body`` (not inside
    ``formField-major``). Nested-only hide left live NXP Expanded after a
    correct Science-Computer chip (1301Z ``listbox_still_open``).

    Settle may close chrome only — never rewrite a committed chip. Never
    ``display:none`` listboxes/options here: that sticks and hangs reclaim
    clicks on gym/live option portals.
    """
    try:
        await page.evaluate(
            """() => {
              const inFos = (el) => {
                if (!el) return false;
                const w = el.closest('[data-automation-id]');
                const id = (w && w.getAttribute('data-automation-id') || '').toLowerCase();
                const txt = (w && (w.innerText || w.textContent) || '').toLowerCase();
                return id.includes('fieldofstudy') || id.includes('discipline')
                  || id.includes('major') || txt.includes('field of study')
                  || txt.includes('major');
              };
              document.querySelectorAll('[aria-expanded="true"]').forEach((el) => {
                if (inFos(el) || el.getAttribute('role') === 'combobox') {
                  if (el.blur) el.blur();
                  el.setAttribute('aria-expanded', 'false');
                }
              });
              const active = document.activeElement;
              if (active && (inFos(active) || active.getAttribute('role') === 'combobox')) {
                if (active.blur) active.blur();
                active.setAttribute('aria-expanded', 'false');
              }
              document.body.click();
            }"""
        )
        await page.wait_for_timeout(120)
    except Exception:
        pass
    try:
        from captcha_pause import press_escape_unless_captcha

        await press_escape_unless_captcha(page)
        await page.wait_for_timeout(60)
        await press_escape_unless_captcha(page)
    except Exception:
        pass
    try:
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(60)
    except Exception:
        pass
    await settle_open_listbox(page)


async def fos_chip_committed_on_page(
    page,
    candidates: list[str] | None = None,
    intent: str | None = None,
) -> bool:
    """True when every visible FoS wrap shows a chip matching intent."""
    from field_done import field_is_done_from_readback

    cands = [str(c).strip() for c in (candidates or []) if str(c or "").strip()]
    meta: dict = {"type": "FIELD_OF_STUDY", "dom_chip": True}
    if cands:
        meta["aliases_tried"] = cands
    intent_val = intent or (cands[0] if cands else None)
    try:
        wraps = page.locator(
            '[data-automation-id*="fieldOfStudy"],'
            '[data-automation-id*="discipline"],'
            '[data-automation-id*="major"],'
            '[data-automation-id*="formField"]'
        )
        n = await wraps.count()
    except Exception:
        return False
    saw_chip = False
    for i in range(min(n, 12)):
        wrap = wraps.nth(i)
        try:
            if not await wrap.count():
                continue
            wt = (await wrap.inner_text() or "").strip()
            if "field of study" not in wt.lower() and not any(
                k in (await wrap.get_attribute("data-automation-id") or "").lower()
                for k in ("fieldofstudy", "discipline", "major")
            ):
                continue
        except Exception:
            continue
        chip = (await read_workday_formfield_chip(wrap) or "").strip()
        if not chip:
            continue
        saw_chip = True
        if not field_is_done_from_readback(chip, meta, intent_val).ok:
            return False
    return saw_chip


async def settle_fos_widget_until_closed(
    page,
    *,
    max_rounds: int = 6,
    candidates: list[str] | None = None,
    intent: str | None = None,
) -> bool:
    """Close Workday FoS/listbox prompts — body-click + Escape until settled.

    When the FoS chip already matches intent, stop after 2 chrome-close rounds
    even if ``listbox_still_open`` still fires (stale portal chrome). Burning
    6 full rounds per alias walk made live NXP look like page-cycling while
    doing lock_skip no-ops (2227Z steps 028–038).
    """
    settled = False
    chip_ok = False
    rounds = max_rounds
    for round_i in range(rounds):
        await force_close_fos_widget(page)
        try:
            await page.wait_for_timeout(100)
        except Exception:
            pass
        open_lb = await listbox_still_open(page)
        expanded = await fos_widget_expanded(page)
        if candidates or intent:
            chip_ok = await fos_chip_committed_on_page(page, candidates, intent)
        if not open_lb and not expanded:
            settled = True
            break
        if chip_ok and not open_lb:
            settled = True
            break
        if chip_ok:
            # Committed chip: one more force-close then accept — do not thrash.
            if round_i >= 1:
                settled = True
                break
            await force_close_fos_widget(page)
            try:
                await page.wait_for_timeout(80)
            except Exception:
                pass
            settled = True
            break
    return settled


async def how_heard_widget_expanded(page) -> bool:
    """True when How-Heard / source combobox still has aria-expanded=true."""
    try:
        return bool(
            await page.evaluate(
                """() => {
                  const inHh = (el) => {
                    if (!el) return false;
                    const w = el.closest('[data-automation-id]');
                    const id = (w && w.getAttribute('data-automation-id') || '').toLowerCase();
                    const name = (el.getAttribute('name') || '').toLowerCase();
                    const aid = (el.getAttribute('data-automation-id') || '').toLowerCase();
                    const txt = (w && (w.innerText || w.textContent) || '').toLowerCase();
                    return id.includes('formfield-source') || id.includes('how_heard')
                      || id.includes('howdidyouhear') || id.includes('candidatesource')
                      || aid.includes('source--source') || name.includes('source--source')
                      || txt.includes('how did you hear');
                  };
                  return [...document.querySelectorAll('[aria-expanded="true"]')]
                    .some(inHh);
                }"""
            )
        )
    except Exception:
        return False


async def force_close_how_heard_widget(page) -> None:
    """Close How-Heard/source dropdown chrome so it cannot steal State/Next."""
    try:
        await page.evaluate(
            """() => {
              const inHh = (el) => {
                if (!el) return false;
                const w = el.closest('[data-automation-id]');
                const id = (w && w.getAttribute('data-automation-id') || '').toLowerCase();
                const name = (el.getAttribute('name') || '').toLowerCase();
                const aid = (el.getAttribute('data-automation-id') || '').toLowerCase();
                const txt = (w && (w.innerText || w.textContent) || '').toLowerCase();
                return id.includes('formfield-source') || id.includes('how_heard')
                  || id.includes('howdidyouhear') || id.includes('candidatesource')
                  || aid.includes('source--source') || name.includes('source--source')
                  || txt.includes('how did you hear');
              };
              document.querySelectorAll('[aria-expanded="true"]').forEach((el) => {
                if (inHh(el) || el.getAttribute('role') === 'combobox') {
                  if (el.blur) el.blur();
                  el.setAttribute('aria-expanded', 'false');
                }
              });
              const active = document.activeElement;
              if (active && (inHh(active) || active.getAttribute('role') === 'combobox')) {
                if (active.blur) active.blur();
                active.setAttribute('aria-expanded', 'false');
              }
              document.body.click();
            }"""
        )
        await page.wait_for_timeout(120)
    except Exception:
        pass
    try:
        from captcha_pause import press_escape_unless_captcha

        await press_escape_unless_captcha(page)
        await page.wait_for_timeout(60)
    except Exception:
        pass
    await settle_open_listbox(page)


async def how_heard_chip_committed_on_page(page) -> bool:
    """True when How-Heard wrap shows a valid source leaf chip (any sibling)."""
    try:
        snip = await _read_how_heard_wrap_text(page)
    except Exception:
        snip = ""
    if how_heard_source_committed(snip):
        return True
    return bool(committed_how_heard_leaf(snip))


async def listbox_still_open(page) -> bool:
    """True when a visible promptOption / listbox menu is still open mid-widget.

    Chrome-closed (no ``aria-expanded`` + no visible listbox) is NOT open —
    leftover Skills ``promptOption`` chips must not fake ``listbox_still_open``
    after a FoS skip (NXP 1045Z).
    """
    try:
        return bool(
            await page.evaluate(
                """() => {
                  const vis = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const cs = window.getComputedStyle(el);
                    return r.width > 0 && r.height > 0
                      && cs.visibility !== 'hidden' && cs.display !== 'none';
                  };
                  const isSkills = (el) => {
                    let n = el;
                    for (let i = 0; i < 8 && n; i++, n = n.parentElement) {
                      const aid = ((n.getAttribute && n.getAttribute('data-automation-id')) || '').toLowerCase();
                      const cls = (n.className || '').toString().toLowerCase();
                      if (/\\bskills?\\b|suggested.?skill|skill.?chip|formfield-skill/.test(aid + ' ' + cls))
                        return true;
                    }
                    return false;
                  };
                  const menus = document.querySelectorAll(
                    '[role="listbox"],[data-automation-id="promptList"]'
                  );
                  for (const lb of menus) {
                    if (vis(lb) && !isSkills(lb)) return true;
                  }
                  const expanded = [...document.querySelectorAll('[aria-expanded="true"]')]
                    .some((el) => vis(el) && !isSkills(el));
                  if (!expanded) return false;
                  const opts = document.querySelectorAll(
                    '[data-automation-id="promptOption"],[role="option"]'
                  );
                  let n = 0;
                  for (const o of opts) {
                    if (vis(o) && !isSkills(o)) { n++; if (n >= 2) return true; }
                  }
                  return false;
                }"""
            )
        )
    except Exception:
        return False


_FOS_SKIP_REASONS = frozenset(
    {
        "no_matching_option",
        "fos_not_committed",
        "enumerate_below_threshold_no_safe_filter",
        "fos_skip",
    }
)


def fos_skip_allows_advance(report: dict | None) -> bool:
    """True when FoS was skip / no_matching_option — do not block Next on chrome."""
    if not isinstance(report, dict):
        return False
    if report.get("fos_skip") or report.get("fos_no_matching_option"):
        return True
    for bag in (report.get("filled"), report.get("missed")):
        for row in bag or []:
            if not isinstance(row, dict):
                continue
            ft = str(row.get("type") or "").upper()
            aid = str(row.get("automation_id") or "").lower().replace("_", "").replace(
                "-", ""
            )
            fos_ish = ft in _FOS_TYPES or "fieldofstudy" in aid
            if not fos_ish:
                continue
            reason = str(row.get("reason") or row.get("error") or "")
            if (
                row.get("fos_skip")
                or row.get("optional_miss")
                or reason in _FOS_SKIP_REASONS
            ):
                return True
    return False


async def settle_before_advance(page, report: dict | None = None) -> dict:
    """Close mid-widget menus before Save and Continue — never ADVANCE with listbox open."""
    detail: dict[str, Any] = {"settled": False, "was_open": False}
    try:
        fos_cands: list[str] = []
        fos_intent: str | None = None
        if report is not None:
            fill_values = report.get("fill_values") or {}
            fos_intent = (
                fill_values.get("FIELD_OF_STUDY")
                or fill_values.get("DISCIPLINE")
                or fill_values.get("MAJOR")
            )
            try:
                from exp_workday_selectors import _fos_candidates

                fos_cands = _fos_candidates(fill_values, for_fill=False)
            except Exception:
                pass
        await settle_fos_widget_until_closed(
            page, candidates=fos_cands or None, intent=fos_intent
        )
        # How-Heard listbox left open steals State keystrokes (NXP 0842Z / 2244Z).
        hh_chip = await how_heard_chip_committed_on_page(page)
        if hh_chip or await how_heard_widget_expanded(page) or await listbox_still_open(page):
            await force_close_how_heard_widget(page)
        open_now = await listbox_still_open(page)
        expanded_now = await fos_widget_expanded(page)
        hh_exp = await how_heard_widget_expanded(page)
        detail["was_open"] = open_now or expanded_now or hh_exp
        if open_now or expanded_now or hh_exp:
            await force_close_fos_widget(page)
            await force_close_how_heard_widget(page)
            try:
                await page.wait_for_timeout(200)
            except Exception:
                pass
            still_lb = await listbox_still_open(page)
            still_exp = await fos_widget_expanded(page)
            still_hh = await how_heard_widget_expanded(page)
            still = still_lb or still_exp or still_hh
            if still:
                # NXP: committed US (+1) chip can leave stale listbox chrome open.
                snip = await read_phone_country_field_snip(page)
                if is_committed_us_phone_country_readback(snip):
                    try:
                        await page.evaluate(
                            """() => {
                              document.body.click();
                              const active = document.activeElement;
                              if (active && active.blur) active.blur();
                            }"""
                        )
                        await page.wait_for_timeout(150)
                        await settle_open_listbox(page)
                        await page.wait_for_timeout(120)
                    except Exception:
                        pass
                    still_lb = await listbox_still_open(page)
                    still_exp = await fos_widget_expanded(page)
                    still_hh = await how_heard_widget_expanded(page)
                    still = still_lb or still_exp or still_hh
                    if still and is_committed_us_phone_country_readback(snip):
                        detail["phone_country_chip_override"] = True
                        still = False
            if still:
                # NXP 1045Z: FoS skip / no_matching_option + chrome closed
                # (Skills chips leftover) must not block Experience Next.
                chrome_open = await fos_widget_expanded(page) or await how_heard_widget_expanded(
                    page
                )
                if not chrome_open and fos_skip_allows_advance(report):
                    await force_close_fos_widget(page)
                    detail["fos_skip_override"] = True
                    still = False
            if still:
                # NXP 1301Z: Science-Computer chip committed but Major listbox Expanded.
                # Mirror phone-country override — committed FoS must not block ADVANCE
                # when portal chrome refuses to drop after force-close.
                chip_ok = await fos_chip_committed_on_page(
                    page, fos_cands or None, fos_intent
                )
                if not chip_ok and report is not None:
                    chip_ok = any(
                        isinstance(f, dict)
                        and str(f.get("type") or "").upper()
                        in ("FIELD_OF_STUDY", "DISCIPLINE", "MAJOR")
                        and (
                            f.get("verified")
                            or f.get("ok")
                            or f.get("skipped_already_correct")
                        )
                        for f in (report.get("filled") or [])
                    )
                if chip_ok:
                    await force_close_fos_widget(page)
                    await settle_fos_widget_until_closed(
                        page, candidates=fos_cands or None, intent=fos_intent
                    )
                    detail["fos_chip_override"] = True
                    still = False
            if still:
                # 0842Z: valid How-Heard leaf chip committed but portal listbox
                # still open — close and do not block other fields / Next.
                hh_ok = hh_chip or await how_heard_chip_committed_on_page(page)
                if hh_ok:
                    await force_close_how_heard_widget(page)
                    detail["how_heard_chip_override"] = True
                    still = False
            detail["still_open"] = still
            detail["settled"] = not still
            if report is not None:
                report["mid_widget_open"] = bool(still)
                report["listbox_open"] = bool(still)
                if detail.get("fos_chip_override"):
                    report["fos_chip_override"] = True
                if detail.get("fos_skip_override"):
                    report["fos_skip_override"] = True
                if detail.get("how_heard_chip_override"):
                    report["how_heard_chip_override"] = True
        elif report is not None:
            report["mid_widget_open"] = False
            report["listbox_open"] = False
            detail["settled"] = True
    except Exception as e:
        detail["error"] = str(e)[:80]
    return detail


def _default_score_option(opt: str, alias: str) -> int:
    if states_are_confusable(alias, opt):
        return 0
    if reject_confusable_country_option(alias, opt):
        return 0
    go, ga = _gender_polarity_side(opt), _gender_polarity_side(alias)
    if go and ga and go != ga:
        return 0
    try:
        from gh_select import _score_option

        s = int(_score_option(opt, alias) or 0)
        if s > 0 and states_are_confusable(alias, opt):
            return 0
        if s > 0 and reject_confusable_country_option(alias, opt):
            return 0
        if s > 0:
            return s
        return _semantic_option_bonus(opt, alias)
    except Exception:
        o = (opt or "").lower().strip()
        a = (alias or "").lower().strip()
        if not o or not a:
            return 0
        if o == a:
            return 100
        # ATS3-016: token-boundary only — never Male⊂Female via raw ``a in o``.
        if soft_value_match(alias, opt):
            return 80
        return _semantic_option_bonus(opt, alias)


# Option-scoring semantic fallback: ON by default (FASTFILL_SEMANTIC_OPTIONS=1).
# Only fires when the lexical/exact/soft scorers found nothing (score 0), never
# overriding polarity or state-confusable guards (those already returned 0 above).
# Capped BELOW soft(80) and exact(100) so a fuzzy paraphrase can never outrank a
# real lexical match. Kill with FASTFILL_SEMANTIC_MATCH=0 or _OPTIONS=0.
_SEMANTIC_OPTION_THRESHOLD = float(
    os.environ.get("FASTFILL_SEMANTIC_OPTIONS_THRESHOLD", "0.8") or 0.8
)


def _semantic_option_bonus(opt: str, alias: str) -> int:
    # Default ON. FASTFILL_SEMANTIC_MATCH=0 master kill switch; or
    # FASTFILL_SEMANTIC_OPTIONS=0 disables just option scoring. Only fires when
    # the exact/soft scorers found nothing, and is capped BELOW soft(80).
    if os.environ.get("FASTFILL_SEMANTIC_MATCH", "1") == "0":
        return 0
    if os.environ.get("FASTFILL_SEMANTIC_OPTIONS", "1") == "0":
        return 0
    # Never semantic-merge distinct countries (United States ≉ Australia @ ~0.8).
    if reject_confusable_country_option(alias, opt):
        return 0
    # Dial/address country: lexical + dial only — semantic false friends (US↔AU)
    # caused Morningstar-class wrong phone country. Disable entirely.
    try:
        from gh_select import looks_like_dial_code_option

        if looks_like_dial_code_option(opt) or looks_like_dial_code_option(alias):
            return 0
    except Exception:
        pass
    if looks_like_country_option(opt) or looks_like_country_option(alias):
        return 0
    try:
        from semantic_match import semantic_sim

        s = semantic_sim(alias, opt)
    except Exception:
        return 0
    return 70 if s >= _SEMANTIC_OPTION_THRESHOLD else 0


def select_readback_ok(
    shown: str | None,
    candidates: Iterable[str],
    *,
    typed_frag: str | None = None,
    picked: str | None = None,
    score_fn: Callable[[str, str], int] | None = None,
    min_score: int = 50,
) -> bool:
    """True when committed display matches an intended alias (not placeholder/filter).

    Never accept raw substring matches for short tokens (IL≠Idaho). Never accept
    ``picked == shown`` alone when shown does not match a candidate — that path
    false-verified Workday state when the click target text disagreed with DOM.
    Never accept phone dial-code displays (+1 / United States +1) when candidates
    are address-country names (live Greenhouse Dragos).
    """
    s = (shown or "").strip()
    if is_placeholder_select_value(s):
        return False
    if is_multiselect_uncommitted(s):
        return False
    if is_uncommitted_filter_text(s, typed_frag, picked=picked):
        return False
    cands = [c for c in (candidates or []) if c]
    # Dial-code shown: bare "+1" alone never verifies unless candidates include
    # dial aliases; "United States +1" is handled by score_fn country-name strip.
    try:
        from gh_select import is_dial_only_display, looks_like_dial_code_option

        if is_dial_only_display(s) and not any(
            is_dial_only_display(c) or looks_like_dial_code_option(c) for c in cands
        ):
            # Allow rescue via picked country+dial when provided
            if not (
                picked
                and looks_like_dial_code_option(picked)
                and any(
                    soft_value_match(c, picked)
                    or soft_value_match(c, country_name_safe(picked))
                    for c in cands
                )
            ):
                return False
    except Exception:
        pass
    if not cands:
        return bool(picked) and s.lower() == (picked or "").strip().lower()
    score_fn = score_fn or _default_score_option
    best = 0
    for alias in cands:
        a = (alias or "").strip()
        if not a:
            continue
        if soft_value_match(a, s):
            return True
        best = max(best, int(score_fn(s, a) or 0))
    # picked may confirm the display line, but only when picked itself matches a cand
    if picked:
        pl = (picked or "").strip()
        if pl and soft_value_match(pl, s):
            if any(soft_value_match(c, pl) or int(score_fn(pl, c) or 0) >= min_score for c in cands):
                return True
        # GH Country*: shown=+1, picked=United States +1 → accept when picked scores
        try:
            from gh_select import is_dial_only_display, looks_like_dial_code_option

            if is_dial_only_display(s) and looks_like_dial_code_option(pl):
                if any(int(score_fn(pl, c) or 0) >= min_score or soft_value_match(c, pl) for c in cands):
                    return True
        except Exception:
            pass
    return best >= min_score


def country_name_safe(text: str) -> str:
    try:
        from gh_select import country_name_from_dial_option

        return country_name_from_dial_option(text) or text
    except Exception:
        return text or ""


def _salary_amount_tokens(text: str) -> list[str]:
    """Extract comma-grouped salary amounts (e.g. 80,000 from $80,000 - $100,000)."""
    return re.findall(r"\d{1,3}(?:,\d{3})+|\d{4,}", text or "")


def _is_salary_like(text: str) -> bool:
    t = text or ""
    return bool("$" in t or re.search(r"\d{1,3},\d{3}", t) or re.search(r"\d{5,}", t))


def _is_school_like(text: str) -> bool:
    t = (text or "").lower()
    return bool(re.search(r"\b(university|college|institute|school)\b", t))


def _fuzzy_salary_score(opt: str, alias: str) -> int:
    """Cross-format salary band matching ($80,000-$100,000 vs 80,000 - 100,000)."""
    o_nums = _salary_amount_tokens(opt)
    a_nums = _salary_amount_tokens(alias)
    if not o_nums and not a_nums:
        ol, al = (opt or "").lower(), (alias or "").lower()
        if any(x in al for x in ("negotiable", "open", "flexible", "discuss")) and any(
            x in ol for x in ("negotiable", "open", "flexible", "discuss", "competitive")
        ):
            return 72
        return 0
    if o_nums and a_nums:
        if o_nums == a_nums:
            return 98
        if len(o_nums) >= 2 and len(a_nums) >= 2 and o_nums[:2] == a_nums[:2]:
            return 97
        if o_nums[0] == a_nums[0]:
            return 84
        if a_nums[0] in opt or o_nums[0] in alias:
            return 78
    elif o_nums and a_nums == []:
        if o_nums[0] in alias:
            return 76
    elif a_nums and o_nums == []:
        if a_nums[0] in opt:
            return 76
    return 0


def _fuzzy_school_score(opt: str, alias: str) -> int:
    """Institution-name matching ignoring city/state suffixes."""
    o_head = re.split(r"[,;]", opt or "", maxsplit=1)[0].strip().lower()
    a_head = re.split(r"[,;]", alias or "", maxsplit=1)[0].strip().lower()
    if not o_head or not a_head:
        return 0
    if o_head == a_head:
        return 96
    if o_head in a_head or a_head in o_head:
        return 88
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
    }
    o_sig = [w for w in re.split(r"\W+", o_head) if len(w) > 3 and w not in stop]
    a_sig = [w for w in re.split(r"\W+", a_head) if len(w) > 3 and w not in stop]
    if o_sig and a_sig:
        overlap = sum(1 for w in a_sig if w in o_sig or any(w in x or x in w for x in o_sig))
        if overlap >= min(len(a_sig), len(o_sig)):
            return 70
        if overlap >= 1 and (a_sig[0] == o_sig[0] or a_sig[-1] == o_sig[-1]):
            return 62
    return 0


def split_select_words(value: str) -> list[str]:
    """Split intended select answer into typeahead words."""
    raw = (value or "").strip()
    if not raw:
        return []
    if re.fullmatch(r"yes|no", raw, re.I):
        return [raw[0].upper() + raw[1:].lower()]
    # Salary bands: keep comma-grouped amounts; type low bound then full range
    if _is_salary_like(raw):
        nums = _salary_amount_tokens(raw)
        if nums:
            steps = [nums[0]]
            if len(nums) >= 2:
                steps.append(f"{nums[0]} {nums[1]}")
            return steps
    # School / long institution: type institution head (before city comma), word-by-word
    if _is_school_like(raw) and "," in raw:
        raw = raw.split(",")[0].strip()
    words: list[str] = []
    for chunk in re.split(r"[,/|]+", raw):
        for w in chunk.split():
            cleaned = re.sub(r"^[^\w.+']+|[^\w.+']+$", "", w)
            if cleaned:
                words.append(cleaned)
    return words


def rank_option_matches(
    texts: list[str],
    aliases: Iterable[str],
    score_fn: Callable[[str, str], int] | None = None,
) -> list[tuple[int, int, str]]:
    """Return (score, index, text) sorted best-first.

    Prefer non-Decline aliases when any preferred alias scores ≥50 — otherwise
    exact Decline aliases (score 100) beat soft preferred matches (90) and keep
    wrong EEO answers (live grvty Disability/Veteran).
    Earlier aliases win equal-score ties (Master before Bachelor).
    """
    score_fn = score_fn or _default_score_option
    cands = [c for c in aliases if c]
    try:
        from gh_select import is_decline_like_alias
    except Exception:

        def is_decline_like_alias(t: str) -> bool:  # type: ignore
            al = (t or "").lower()
            return any(
                x in al
                for x in ("decline", "prefer not", "wish to answer", "want to answer")
            )

    preferred = [c for c in cands if not is_decline_like_alias(c)]
    use = preferred if preferred else cands

    def _rank(pool: list[str]) -> list[tuple[int, int, str]]:
        salary_ctx = any(_is_salary_like(c) for c in pool)
        school_ctx = any(_is_school_like(c) for c in pool)
        ranked: list[tuple[int, int, str, int]] = []
        for i, t in enumerate(texts):
            if not t or is_placeholder_select_value(t):
                continue
            # Bare dial-only rows ("+1") with no country name — skip unless
            # aliases intentionally include dial codes.
            try:
                from gh_select import (
                    country_name_from_dial_option,
                    is_dial_only_display,
                    looks_like_dial_code_option,
                )

                if is_dial_only_display(t) and not any(
                    looks_like_dial_code_option(c) or is_dial_only_display(c)
                    for c in pool
                ):
                    continue
                # "United States +1" is a valid GH Country* option — score via
                # country_name_from_dial_option inside _score_option.
                _ = country_name_from_dial_option  # used by score_fn path
            except Exception:
                pass
            best_s = 0
            best_ai = 999
            for ai, alias in enumerate(pool):
                s = int(score_fn(t, alias) or 0)
                if salary_ctx or _is_salary_like(t):
                    s = max(s, _fuzzy_salary_score(t, alias))
                if school_ctx or _is_school_like(t):
                    s = max(s, _fuzzy_school_score(t, alias))
                if s > best_s or (s == best_s and ai < best_ai):
                    best_s = s
                    best_ai = ai
            if best_s > 0:
                ranked.append((best_s, i, t, best_ai))
        # Higher score first; earlier alias wins ties; earlier option index last
        ranked.sort(key=lambda x: (-x[0], x[3], x[1]))
        return [(s, i, t) for s, i, t, _ai in ranked]

    ranked_pref = _rank(use)
    if preferred and ranked_pref and ranked_pref[0][0] >= 50:
        return ranked_pref
    # Fallback: include Decline aliases when preferred scored nothing useful
    if preferred and use is preferred:
        return _rank(cands)
    return ranked_pref


def commit_min_score_for(field_type: str = "", label: str = "") -> int:
    """Minimum score to commit an option. Degree/country/FoS are stricter."""
    ftype = str(field_type or "").upper()
    lab = str(label or "").lower()
    if ftype == "DEGREE" or re.search(r"\bdegree\b|qualification|education level", lab):
        return _DEGREE_COMMIT_MIN
    if ftype in (
        "PHONE_COUNTRY_CODE",
        "ADDRESS_COUNTRY",
        "FIELD_OF_STUDY",
        "DISCIPLINE",
        "MAJOR",
        "GENDER",
    ) or re.search(
        r"country[\s_-]*phone|phone[\s_-]*country|field of study|\bmajor\b|gender|\bsex\b",
        lab,
    ):
        return _DEGREE_COMMIT_MIN  # same bar as degree — no soft early commit
    if ftype == "SCHOOL" or re.search(r"\bschool\b|university|college", lab):
        return 65
    return _DEFAULT_COMMIT_MIN


def sanitized_typeahead_token(
    field_type: str,
    value: str,
    aliases: list[str] | None = None,
) -> str:
    """Safe filter fragment for huge lists — never free-form essays.

    Degree → \"Master\" / \"Bachelor\"; country → short country name; else \"\".
    Empty means: do not type-to-filter (leave blank if enumerate missed).
    """
    ftype = str(field_type or "").upper()
    cands = [c for c in ([value] + list(aliases or [])) if c]
    # Phone dial BEFORE gh_select fallback (which would return Indeed raw)
    if ftype == "PHONE_COUNTRY_CODE":
        raw = (value or (cands[0] if cands else "")).strip()
        return phone_country_code_search_query(raw)[:28]
    try:
        from gh_select import _type_fragment_for

        frag = _type_fragment_for(ftype, cands) if ftype else ""
        if frag:
            # Never push job-board tokens for address country either
            if ftype == "ADDRESS_COUNTRY" and not (
                is_safe_phone_country_search(frag) or looks_like_country_option(frag)
            ):
                frag = ""
            else:
                return str(frag)[:28]
    except Exception:
        pass
    if ftype == "DEGREE" or re.search(r"master|bachelor|doctor|associate", (value or ""), re.I):
        v = (value or "").lower()
        if re.search(r"master|\bm\.?s\.?\b", v):
            return "Master"
        if re.search(r"bachelor|\bb\.?s\.?\b", v):
            return "Bachelor"
        if re.search(r"ph\.?d|doctor", v):
            return "Doctor"
        return ""
    if ftype == "ADDRESS_COUNTRY":
        raw = (value or (cands[0] if cands else "")).strip()
        if not is_safe_phone_country_search(raw) and not looks_like_country_option(raw):
            return "United States"
        # Strip dial codes; keep country head only
        head = re.split(r"\s*\+|,", raw)[0].strip()
        if len(head) >= 4 and not re.search(r"require|sponsor|essay|describe", head, re.I):
            if is_safe_phone_country_search(head) or looks_like_country_option(head):
                return head[:28]
            return "United States"
        return ""
    if ftype in ("DISCIPLINE", "MAJOR", "FIELD_OF_STUDY"):
        for a in cands:
            if a and "computer" in a.lower():
                return "Computer Science"
        return (cands[0][:28] if cands else "") or ""
    return ""


def pick_best_scored_option(
    texts: list[str],
    aliases: Iterable[str],
    score_fn: Callable[[str, str], int] | None = None,
    *,
    intent: str = "",
    min_score: int = 70,
) -> tuple[int, str, int] | None:
    """Pure enumerate→score→threshold pick. None when no option clears the bar.

    Used by tests and by the live enumerate-first path so Master's never
    commits A.A./Associate when those are the only visible weak soft-matches.
    """
    score_fn = score_fn or _default_score_option
    cands = [c for c in aliases if c]
    primary = (intent or "").strip() or (cands[0] if cands else "")
    if not texts or not cands:
        return None

    def _reject(intent_s: str, opt: str) -> bool:
        if reject_confusable_state_option(intent_s, opt):
            return True
        if reject_confusable_country_option(intent_s, opt):
            return True
        return False

    filtered, orig_idx = filter_options_preserving_indices(
        texts, primary, reject_fn=_reject
    )
    if not filtered:
        return None
    ranked = remap_ranked_to_original(
        rank_option_matches(filtered, cands, score_fn), orig_idx
    )
    # Strict threshold: do NOT use at_last_word weak floors (those accepted
    # Associate Degree at 65 when Master's was intended).
    clear = clear_closest_match(
        ranked,
        at_last_word=False,
        min_score=min_score,
        intent=primary,
    )
    if not clear:
        return None
    _i, _t, sc = clear
    if sc < min_score:
        return None
    return clear


async def enumerate_listbox_options(
    page,
    *,
    selectors: list[str] | None = None,
    root: Any | None = None,
    filter_input: Any | None = None,
    timeout_ms: int = 2500,
    max_scrolls: int = 10,
    max_options: int = 200,
    field_type: str = "",
    portal_fallback: bool | None = None,
) -> tuple[Any, list[str]]:
    """Collect option texts, scrolling the listbox to load virtualized rows.

    Scroll / ArrowDown only while the unique option set grows. When a pass
    yields no new texts (after one scroll + one ArrowDown nudge), stop — do
    not re-walk a short fully-loaded menu to max_scrolls (GH highlight thrash).
    Long virtualized Workday lists still multi-scroll while new rows appear.

    Returns (locator, texts) with texts aligned to locator indices when possible.
    Dummy-only helper — never submits.
    """
    ftype = str(field_type or "").upper()
    fallback = (
        False
        if ftype in _FOS_TYPES
        else (True if portal_fallback is None else bool(portal_fallback))
    )
    opts, texts = await wait_for_option_texts(
        page,
        selectors=selectors,
        timeout_ms=timeout_ms,
        filter_input=filter_input,
        nudge=True,
        allow_enter_nudge=False,
        root=root,
        field_type=field_type,
        portal_fallback=fallback,
        max_options=max_options,
    )
    if not texts:
        return opts, []

    seen: list[str] = list(texts)
    # Scroll listbox container to force virtualized rows to mount
    scroll_targets = [
        '[role="listbox"]',
        '[data-automation-id="promptOption"]',
        ".select__menu",
        ".select__menu-list",
        '[class*="menu-list"]',
        '[class*="MenuList"]',
    ]

    async def _merge_visible() -> None:
        _, more = await wait_for_option_texts(
            page,
            selectors=selectors,
            timeout_ms=600,
            filter_input=None,
            nudge=False,
            root=root,
            max_options=max_options,
            field_type=field_type,
            portal_fallback=fallback,
        )
        for t in more:
            if t and t not in seen:
                seen.append(t)

    for _ in range(max_scrolls):
        before = len(seen)
        scope = root or page
        for sel in scroll_targets:
            try:
                box = scope.locator(f"{sel}:visible").first
                if await box.count() == 0:
                    continue
                await box.evaluate(
                    """(el) => {
                      el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + Math.max(180, el.clientHeight || 200));
                    }"""
                )
                try:
                    await page.wait_for_timeout(120)
                except Exception:
                    pass
                await _merge_visible()
                # One container per pass — avoid multi-selector ArrowDown stacks
                break
            except Exception:
                continue

        if len(seen) <= before:
            # Scroll added nothing (or no scroll target). One ArrowDown may
            # advance a virtualized window that ignores scrollTop; then re-check.
            try:
                await page.keyboard.press("ArrowDown")
                await page.wait_for_timeout(80)
            except Exception:
                pass
            try:
                await _merge_visible()
            except Exception:
                pass

        if len(seen) <= before:
            # Option set stable — early-exit (no A→B→C→D highlight loops)
            break
        if len(seen) >= max_options:
            break
    # Re-query locator so indices match the latest DOM when possible
    opts2, texts2 = await wait_for_option_texts(
        page,
        selectors=selectors,
        timeout_ms=800,
        filter_input=None,
        nudge=False,
        root=root,
        max_options=max_options,
        field_type=field_type,
        portal_fallback=fallback,
    )
    # Prefer the union of scrolled texts for scoring; click uses live locator
    merged = list(texts2) if texts2 else list(seen)
    for t in seen:
        if t and t not in merged:
            merged.append(t)
    return (opts2 if texts2 else opts), merged[:max_options]


def clear_closest_match(
    ranked: list[tuple[int, int, str]],
    *,
    at_last_word: bool = False,
    min_score: int = 50,
    unique_margin: int = 12,
    intent: str = "",
) -> tuple[int, str, int] | None:
    """Return (index, text, score) when a clear closest option exists."""
    if intent:
        ranked = [
            r
            for r in ranked
            if not reject_confusable_state_option(intent, r[2])
            and not reject_confusable_country_option(intent, r[2])
        ]
    if not ranked:
        return None
    best_s, best_i, best_t = ranked[0]
    if best_s >= 95:
        return best_i, best_t, best_s
    above = [r for r in ranked if r[0] >= min_score]
    if len(above) == 1:
        return above[0][1], above[0][2], above[0][0]
    # Salary/school lists: accept clear leader with smaller margin
    margin = unique_margin
    if best_s >= 70:
        margin = min(unique_margin, 8)
    if (
        len(ranked) >= 2
        and best_s >= min_score
        and (best_s - ranked[1][0]) >= margin
    ):
        return best_i, best_t, best_s
    # ATS3-008: raise weak floors — mediocre school/salary/how-heard must not commit.
    if len(ranked) == 1 and best_s >= 50:
        return best_i, best_t, best_s
    # Single visible option that soft-matches intent (Workday state Illinois)
    if len(ranked) == 1 and intent and soft_value_match(intent, best_t):
        return best_i, best_t, max(best_s, 60)
    # Last typed word: accept only a clear fuzzy match
    if at_last_word and best_s >= 55:
        return best_i, best_t, best_s
    if at_last_word and len(ranked) == 1 and best_s >= 50:
        return best_i, best_t, best_s
    if at_last_word and best_s >= 50 and len(above) == 1:
        return above[0][1], above[0][2], above[0][0]
    return None


async def nudge_listbox_after_type(
    page,
    filter_input: Any | None = None,
    *,
    allow_enter: bool = False,
) -> dict[str, Any]:
    """Trigger async option lists after typing (Workday How-Heard prompts, etc.).

    Prefer ArrowDown / prompt-icon click. Enter is opt-in and only while the
    filter input stays focused — never used as a form Submit. Workday source
    prompts often need a key nudge before ``promptOption`` rows appear.

    CRITICAL: never click page-global ``promptIcon`` / bare ``multiSelectContainer``
    — that reopened Country Phone Code after typing Indeed (Morningstar class).
    Icons are scoped to the active filter's formField / multiSelect ancestor.
    """
    detail: dict[str, Any] = {"nudges": []}
    if filter_input is not None:
        try:
            await filter_input.click(timeout=1500, force=True)
            detail["nudges"].append("refocus_filter")
        except Exception:
            pass
    # 1) ArrowDown opens listbox without submitting the page
    try:
        await page.keyboard.press("ArrowDown")
        detail["nudges"].append("ArrowDown")
        await page.wait_for_timeout(280)
    except Exception as e:
        detail["arrow_error"] = str(e)[:80]
    # 2) Workday prompt / multiselect list icon — SCOPED to active filter only
    icon_clicked = False
    if filter_input is not None:
        scoped_sels = [
            "xpath=ancestor::*[@data-automation-id='formField-source' "
            "or contains(@data-automation-id,'formField-how') "
            "or contains(@data-automation-id,'formField-candidateSource') "
            "or contains(@data-automation-id,'formField-school') "
            "or contains(@data-automation-id,'formField-degree') "
            "or contains(@data-automation-id,'formField-fieldOfStudy') "
            "or contains(@data-automation-id,'formField-discipline') "
            "or contains(@data-automation-id,'formField-major') "
            "or @data-automation-id='multiSelectContainer'][1]"
            "//*[@data-automation-id='promptIcon']",
            "xpath=ancestor::*[@data-automation-id='multiSelectContainer' "
            "or contains(@data-automation-id,'formField-')][1]"
            "//button[contains(@aria-label,'Select') or @data-automation-id='promptIcon']",
        ]
        for sel in scoped_sels:
            try:
                loc = filter_input.locator(sel).first
                if await loc.count() == 0:
                    continue
                if not await loc.is_visible(timeout=400):
                    continue
                # Reject if ancestor is phone/country dial widget
                try:
                    wrap_aid = await loc.evaluate(
                        """el => {
                          const w = el.closest('[data-automation-id*="formField"],'
                            + '[data-automation-id*="phone"],[data-automation-id*="Phone"]');
                          return (w && w.getAttribute('data-automation-id')) || '';
                        }"""
                    )
                except Exception:
                    wrap_aid = ""
                if how_heard_scope_reject_aid(str(wrap_aid or "")):
                    detail["nudges"].append(f"icon_skipped_dial:{str(wrap_aid)[:40]}")
                    continue
                await loc.click(timeout=2000)
                detail["nudges"].append(f"icon:scoped:{sel[:48]}")
                await page.wait_for_timeout(320)
                icon_clicked = True
                break
            except Exception:
                continue
    if not icon_clicked:
        # Fallback: only explicitly scoped how-heard / source formFields (never bare)
        for sel in (
            '[data-automation-id="formField-source"] [data-automation-id="promptIcon"]',
            '[data-automation-id="formField-how_heard"] [data-automation-id="promptIcon"]',
            '[data-automation-id="formField-howDidYouHear"] [data-automation-id="promptIcon"]',
            '[data-automation-id="formField-source"] button[aria-label*="Select" i]',
            '[data-automation-id="formField-how_heard"] button[aria-label*="Select" i]',
        ):
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                if not await loc.is_visible(timeout=400):
                    continue
                await loc.click(timeout=2000)
                detail["nudges"].append(f"icon:{sel[:48]}")
                await page.wait_for_timeout(320)
                break
            except Exception:
                continue
    # 3) Never Enter — MCP NXP: pressEnter false always (Enter submits / commits
    # filter text instead of a promptOption chip).
    if allow_enter:
        detail["nudges"].append("enter_skipped_mcp")
    return detail


# ChamPro-style Workday fiber searchSelect (ported idea — not their plugin).
# Typing alone often yields "No Items"; fiber onKeyDown Tab triggers async search.
_FIBER_SEARCH_SELECT_JS = """
async (el, args) => {
  const value = String((args && args.value) || '');
  const aliases = Array.isArray(args && args.aliases) ? args.aliases.map(String) : [];
  const waitMs = Math.max(400, Math.min(2200, Number((args && args.wait_ms) || 1200)));
  const strictPrompt = !!(args && args.strict_prompt);
  if (!el || !value) return { status: 'no-el', options: [] };
  const vis = (x) => {
    try {
      const r = x.getBoundingClientRect();
      return r.width > 0 && r.height > 0
        && window.getComputedStyle(x).visibility !== 'hidden';
    } catch (e) { return false; }
  };
  const txt = (x) => ((x && (x.innerText || x.textContent)) || '').replace(/\\s+/g, ' ').trim();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  try { el.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) {}
  try { el.focus(); } catch (e) {}
  let setter = null;
  try {
    setter = Object.getOwnPropertyDescriptor(
      Object.getPrototypeOf(el), 'value'
    ).set;
  } catch (e) {}
  const setNative = (v) => {
    try {
      if (setter) setter.call(el, v);
      else el.value = v;
      if (el._valueTracker) el._valueTracker.setValue('');
    } catch (e) {
      try { el.value = v; } catch (e2) {}
    }
  };
  const fireSearch = (v) => {
    setNative(v);
    const pk = Object.keys(el).find((k) => k.startsWith('__reactProps'));
    const p = pk ? el[pk] : null;
    const lastCh = String(v).slice(-1) || 'a';
    try {
      el.dispatchEvent(new InputEvent('input', {
        bubbles: true, data: String(v), inputType: 'insertText'
      }));
    } catch (e) {
      try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch (e2) {}
    }
    try {
      if (p && p.onChange) {
        p.onChange({
          target: el, currentTarget: el,
          preventDefault() {}, stopPropagation() {}
        });
      }
    } catch (e) {}
    try {
      el.dispatchEvent(new KeyboardEvent('keydown', { key: lastCh, bubbles: true }));
      el.dispatchEvent(new KeyboardEvent('keyup', { key: lastCh, bubbles: true }));
    } catch (e) {}
    try {
      if (p && p.onKeyDown) {
        p.onKeyDown({
          key: 'Tab', target: { value: v },
          preventDefault() {}, stopPropagation() {}
        });
      }
    } catch (e) {}
  };
  const optSel = strictPrompt
    ? '[data-automation-id="promptOption"],[role="option"]'
    : '[data-automation-id="promptOption"],[role="option"],'
      + '[class*="dropdown-results"],[class*="suggestion"],'
      + '[class*="select__option"]';
  const getOpts = () => [...document.querySelectorAll(optSel)].filter(vis).filter((x) => {
    const t = txt(x);
    if (!t || t.length === 0 || t.length >= 120) return false;
    if (strictPrompt) {
      const wrap = x.closest('[data-automation-id]') || x;
      const aid = ((wrap.getAttribute && wrap.getAttribute('data-automation-id')) || '').toLowerCase();
      if (/skill/.test(aid)) return false;
    }
    return true;
  });
  const cands = [value, ...aliases].map((s) => String(s || '').trim()).filter(Boolean);
  const tokensFrom = (s) => String(s).toLowerCase().split(/[\\s,/|+=()-]+/)
    .filter((t) => t.length >= 3);
  fireSearch(value);
  await sleep(waitMs);
  let opts = getOpts();
  if (!opts.length) {
    const tok0 = tokensFrom(value)[0];
    if (tok0 && tok0 !== value.toLowerCase()) {
      fireSearch(tok0);
      await sleep(Math.min(waitMs, 1200));
      opts = getOpts();
    }
  }
  if (!opts.length) {
    return { status: 'no-opt', options: [], algorithm: 'fiber_search_select' };
  }
  const optionTexts = opts.map((o) => txt(o));
  // Score: prefer unique best token overlap across primary + aliases
  const allTokens = [...new Set(cands.flatMap(tokensFrom))];
  // ATS2-003 / ATS3-007: confusable US states + dial codes — reject BEFORE click
  const CONFUSABLE = [
    [['illinois', 'il'], ['idaho', 'id']],
    [['mississippi', 'ms'], ['missouri', 'mo']],
    [['arkansas', 'ar'], ['arizona', 'az']],
    [['alabama', 'al'], ['alaska', 'ak']],
    [['north carolina', 'nc'], ['north dakota', 'nd']],
    [['south carolina', 'sc'], ['south dakota', 'sd']],
    [['virginia', 'va'], ['vermont', 'vt']],
    [['michigan', 'mi'], ['minnesota', 'mn']],
    [['maine', 'me'], ['maryland', 'md']],
    [['nebraska', 'ne'], ['nevada', 'nv']],
    [['colorado', 'co'], ['connecticut', 'ct']],
    [['massachusetts', 'ma'], ['maine', 'me']],
    [['washington', 'wa'], ['wisconsin', 'wi']],
    [['kansas', 'ks'], ['kentucky', 'ky']],
  ];
  const tokSet = (s) => {
    const low = String(s || '').toLowerCase().trim();
    const out = new Set([low]);
    for (const part of low.split(/[\\s,/|-]+/)) if (part) out.add(part);
    return out;
  };
  const isConfusable = (intent, opt) => {
    const a = tokSet(intent), b = tokSet(opt);
    for (const [left, right] of CONFUSABLE) {
      const aL = left.some((t) => a.has(t)), aR = right.some((t) => a.has(t));
      const bL = left.some((t) => b.has(t)), bR = right.some((t) => b.has(t));
      if ((aL && bR) || (aR && bL)) return true;
    }
    return false;
  };
  const looksDial = (t) => {
    const s = String(t || '');
    return /\\(\\+\\d{1,4}\\)|\\+\\d{1,4}\\b/.test(s) && /[A-Za-z]{2,}/.test(s);
  };
  const intentIsDial = cands.some((c) => looksDial(c) || /phone\\s*code|country\\s*code|dial/i.test(c));
  const tokenBound = (needle, hay) => {
    if (!needle || !hay || !hay.includes(needle)) return false;
    if (needle.length <= 3) {
      return hay === needle || hay.startsWith(needle + ' ')
        || hay.startsWith(needle + ',') || hay.startsWith(needle + '-');
    }
    const i = hay.indexOf(needle);
    if (i < 0) return false;
    const beforeOk = i === 0 || /[^a-z0-9]/.test(hay[i - 1]);
    const afterOk = (i + needle.length >= hay.length)
      || /[^a-z0-9]/.test(hay[i + needle.length]);
    return beforeOk && afterOk;
  };
  const scored = opts.map((o, i) => {
    const ot = txt(o).toLowerCase();
    let s = allTokens.filter((t) => ot.includes(t)).length;
    for (const c of cands) {
      const cl = c.toLowerCase();
      if (ot === cl) s += 100;
      else if (ot.startsWith(cl) || cl.startsWith(ot)) s += 40;
      // ATS3-006: token-boundary only — raw substring over-scores near-misses
      else if (tokenBound(cl, ot) || tokenBound(ot, cl)) s += 20;
    }
    return { i, s, t: txt(o), o };
  }).filter((x) => x.s > 0)
    .filter((x) => !cands.some((c) => isConfusable(c, x.t)))
    .filter((x) => intentIsDial || !looksDial(x.t))
    .sort((a, b) => b.s - a.s);
  if (!scored.length) {
    return {
      status: 'no-opt',
      options: optionTexts.slice(0, 12),
      algorithm: 'fiber_search_select',
    };
  }
  if (scored.length > 1 && scored[1].s === scored[0].s && scored[0].s < 100) {
    return {
      status: 'ambiguous',
      options: scored.slice(0, 6).map((x) => x.t),
      algorithm: 'fiber_search_select',
    };
  }
  const best = scored[0];
  // ATS3-004: do NOT click here — Python validates confusable/dial then clicks.
  return {
    status: 'scored',
    picked: best.t,
    score: best.s,
    optionIndex: best.i,
    options: optionTexts.slice(0, 12),
    algorithm: 'fiber_search_select',
  };
}
"""


async def fiber_search_select(
    page,
    filter_input: Any,
    value: str,
    *,
    aliases: list[str] | None = None,
    wait_ms: int = 1500,
    field_type: str = "",
    strict_prompt: bool = False,
) -> dict[str, Any]:
    """Workday/async typeahead via React fiber onKeyDown Tab (ChamPro searchSelect).

    Prefer this over typing-only for SCHOOL / SOURCE prompts (not How-Heard —
    How-Heard is click → category → leaf → chip, never type-as-commit).
    Falls back to caller (nudge_listbox / Playwright click) when status != picked.
    Never blurs the filter (blur can clear unpicked autocomplete).
    FoS: ``strict_prompt`` so Skills suggested chips are never scored/clicked.
    """
    detail: dict[str, Any] = {
        "algorithm": "fiber_search_select",
        "option_clicked": False,
        "status": "not_attempted",
    }
    primary = (value or "").strip()
    if not primary or filter_input is None:
        detail["status"] = "no-el"
        detail["error"] = "missing_input_or_value"
        return detail
    cands = [primary]
    for a in aliases or []:
        s = str(a or "").strip()
        if s and s not in cands:
            cands.append(s)
    try:
        # Ensure the locator resolves to a real input before fiber walk
        await scroll_widget_into_view(filter_input)
        try:
            await filter_input.click(timeout=2000, force=True)
        except Exception:
            try:
                await filter_input.focus()
            except Exception:
                pass
        ftype = str(field_type or "").upper()
        strict = bool(strict_prompt) or ftype in _FOS_TYPES
        raw = await filter_input.evaluate(
            _FIBER_SEARCH_SELECT_JS,
            {
                "value": primary,
                "aliases": cands[1:],
                "wait_ms": wait_ms,
                "strict_prompt": strict,
            },
        )
        if not isinstance(raw, dict):
            detail["status"] = "bad_result"
            detail["error"] = str(raw)[:80]
            return detail
        detail.update(raw)
        picked_f = str(raw.get("picked") or "")
        status = str(raw.get("status") or "")
        # ATS3-004: JS returns scored candidate without click; validate then click.
        if status in ("picked", "scored") and picked_f:
            rejected = False
            if reject_confusable_state_option(primary, picked_f):
                detail["status"] = "confusable_rejected"
                detail["error"] = f"confusable:{picked_f[:40]}"
                rejected = True
            elif reject_confusable_country_option(primary, picked_f):
                detail["status"] = "country_rejected"
                detail["error"] = f"country:{picked_f[:40]}"
                rejected = True
            else:
                try:
                    from gh_select import looks_like_dial_code_option

                    if looks_like_dial_code_option(picked_f) and not any(
                        looks_like_dial_code_option(c) for c in cands
                    ):
                        detail["status"] = "dial_rejected"
                        detail["error"] = f"dial_code:{picked_f[:40]}"
                        rejected = True
                except Exception:
                    pass
                # Gender polarity + job-board into country: soft_value_match gate
                if not rejected and not soft_value_match(primary, picked_f):
                    # Allow dial-shaped intent where soft may fail on (+1) chrome
                    try:
                        from gh_select import looks_like_dial_code_option

                        dial_ok = looks_like_dial_code_option(picked_f) and any(
                            looks_like_dial_code_option(c) or is_us_country_name(c)
                            for c in cands
                        )
                    except Exception:
                        dial_ok = False
                    if not dial_ok:
                        detail["status"] = "soft_match_rejected"
                        detail["error"] = f"soft:{picked_f[:40]}"
                        rejected = True
            if rejected:
                detail["option_clicked"] = False
                detail["ok"] = False
                # Never clicked when status was scored; Escape only if legacy picked.
                # FILL3-019: never Escape while CAPTCHA is on-screen.
                if status == "picked":
                    try:
                        from captcha_pause import press_escape_unless_captcha

                        await press_escape_unless_captcha(page)
                        try:
                            await filter_input.fill("")
                        except Exception:
                            pass
                    except Exception:
                        pass
                return detail
            if status == "scored":
                # Re-query by exact text (MCP) — never reuse fiber's stale optionIndex.
                clicked = await click_option_exact_text(
                    page,
                    picked_f,
                    timeout_ms=4000,
                    allow_soft=True,
                    field_type=ftype,
                )
                if not clicked:
                    detail["status"] = "click_failed"
                    detail["error"] = "option_gone"
                    detail["option_clicked"] = False
                    detail["ok"] = False
                    return detail
                try:
                    await page.wait_for_timeout(280)
                except Exception:
                    pass
            detail["status"] = "picked"
            detail["option_clicked"] = True
            detail["ok"] = True
            # Close search menu after commit — do not leave mid-widget idle open
            try:
                await settle_open_listbox(page)
            except Exception:
                pass
        else:
            detail["ok"] = False
        return detail
    except Exception as e:
        detail["status"] = "error"
        detail["error"] = str(e)[:120]
        detail["ok"] = False
        return detail


# Workday stubborn TEXT (NXP addressLine2 empty_readback): Playwright fill
# paints the DOM, then fiber re-render wipes it unless __reactProps$.onChange
# commits React state. Native setter + InputEvent. NOT for ADDRESS_STATE /
# ADDRESS_COUNTY (promptOption combobox — path-drift typed Sangamon as text).
_STUBBORN_TEXT_TYPES = frozenset({"ADDRESS_LINE1", "ADDRESS_LINE2"})
_STUBBORN_TEXT_AID_NEEDLES = (
    "addressline1",
    "addressline2",
    "address--addressline1",
)

_FIBER_TEXT_COMMIT_JS = """
(el, value) => {
  const v = String(value || '');
  if (!el) return { ok: false, error: 'no-el', algorithm: 'fiber_text_commit' };
  try { el.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) {}
  try { el.focus(); } catch (e) {}
  const tag = (el.tagName || '').toUpperCase();
  const proto = (tag === 'TEXTAREA')
    ? (window.HTMLTextAreaElement && HTMLTextAreaElement.prototype)
    : (window.HTMLInputElement && HTMLInputElement.prototype);
  const desc = proto && Object.getOwnPropertyDescriptor(proto, 'value');
  const setNative = (x) => {
    try {
      if (desc && desc.set) desc.set.call(el, x);
      else el.value = x;
      try { if (el._valueTracker) el._valueTracker.setValue(''); } catch (e) {}
    } catch (e) {
      try { el.value = x; } catch (e2) {}
    }
  };
  setNative('');
  try {
    el.dispatchEvent(new InputEvent('beforeinput', {
      bubbles: true, cancelable: true,
      inputType: 'deleteContentBackward', data: null
    }));
  } catch (e) {}
  setNative(v);
  let beforeinput_ok = false;
  try {
    beforeinput_ok = el.dispatchEvent(new InputEvent('beforeinput', {
      bubbles: true, cancelable: true,
      inputType: 'insertText', data: v
    }));
  } catch (e) {}
  try {
    el.dispatchEvent(new InputEvent('input', {
      bubbles: true, cancelable: false,
      inputType: 'insertText', data: v
    }));
  } catch (e) {
    try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch (e2) {}
  }
  try { el.dispatchEvent(new Event('change', { bubbles: true })); } catch (e) {}
  // Tab commits gym/live Fiber stubs that listen for keydown (never Enter —
  // Enter can submit a Workday step). Playwright fill() does not dispatch Tab.
  try {
    el.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Tab', code: 'Tab', keyCode: 9, which: 9,
      bubbles: true, cancelable: true
    }));
  } catch (e) {}
  let fiber = false;
  let fiberKey = '';
  try {
    const pk = Object.keys(el).find((k) => k.startsWith('__reactProps'));
    const p = pk ? el[pk] : null;
    if (pk) fiberKey = String(pk);
    if (p && typeof p.onChange === 'function') {
      p.onChange({
        target: el, currentTarget: el,
        preventDefault() {}, stopPropagation() {}
      });
      fiber = true;
    }
  } catch (e) {}
  return {
    ok: true,
    value: (el.value || ''),
    fiber_onChange: fiber,
    fiber_key: fiberKey,
    beforeinput_not_canceled: beforeinput_ok,
    algorithm: 'fiber_text_commit'
  };
}
"""


def is_stubborn_text_field(
    *,
    automation_id: str = "",
    field_type: str = "",
    selector: str = "",
) -> bool:
    """True for Workday addressLine2-style controlled text.

    ADDRESS_STATE / countryRegion and ADDRESS_COUNTY / regionSubdivision1 are
    promptOption comboboxes — never stubborn fiber text.
    """
    ft = str(field_type or "").upper()
    if ft in ("ADDRESS_STATE", "ADDRESS_COUNTRY", "ADDRESS_COUNTY"):
        return False
    if ft in _STUBBORN_TEXT_TYPES:
        return True
    blob = f"{automation_id} {selector}".lower().replace("_", "").replace("-", "")
    return any(
        n.replace("_", "").replace("-", "") in blob for n in _STUBBORN_TEXT_AID_NEEDLES
    )


async def fiber_text_commit(locator, value: str) -> dict[str, Any]:
    """Native value setter + InputEvent + ``__reactProps$.onChange`` (ChamPro).

    Commits Workday-like controlled TEXT so fiber re-render does not empty_readback.
    Caller must still ``verify_before_touch`` / ``commit_fill`` / field locks.
    Never use this as the Illinois State picker — that stays role_click/promptOption.
    """
    detail: dict[str, Any] = {
        "algorithm": "fiber_text_commit",
        "ok": False,
        "fiber_onChange": False,
    }
    want = str(value or "")
    if locator is None:
        detail["error"] = "no-locator"
        return detail
    try:
        try:
            await locator.click(timeout=2000, force=True)
        except Exception:
            try:
                await locator.focus()
            except Exception:
                pass
        raw = await locator.evaluate(_FIBER_TEXT_COMMIT_JS, want)
        if isinstance(raw, dict):
            detail.update(raw)
            detail["ok"] = bool(raw.get("ok"))
        else:
            detail["error"] = str(raw)[:80]
    except Exception as e:
        detail["error"] = str(e)[:120]
        detail["ok"] = False
    return detail


async def fill_text_fiber_then_read(
    locator,
    value: str,
    *,
    stubborn: bool = False,
    page=None,
) -> dict[str, Any]:
    """Fill text: stubborn fields fiber-first; others fill then fiber on miss.

    Waits past typical fiber re-render (~120ms) before returning so callers
    can trust readback. Does not lock or commit_fill — Tech10 stays at caller.
    """
    want = str(value or "")
    out: dict[str, Any] = {
        "algorithm": "playwright_fill",
        "ok": False,
        "fiber_onChange": False,
        "stubborn": bool(stubborn),
    }
    if locator is None:
        out["error"] = "no-locator"
        return out

    async def _pw_fill() -> None:
        try:
            await locator.fill(want, timeout=4000)
        except Exception:
            try:
                await locator.click(timeout=2000, force=True, click_count=3)
            except Exception:
                pass

    async def _settle() -> None:
        waiter = page
        if waiter is None:
            try:
                waiter = locator.page
            except Exception:
                waiter = None
        if waiter is not None:
            try:
                await waiter.wait_for_timeout(120)
            except Exception:
                pass

    if stubborn:
        fiber = await fiber_text_commit(locator, want)
        out.update(fiber)
        await _settle()
        shown = ""
        try:
            shown = (await locator.input_value()) or ""
        except Exception:
            shown = str(fiber.get("value") or "")
        if want and shown.strip() and (
            shown.strip().lower() == want.strip().lower()
            or want.strip().lower() in shown.strip().lower()
        ):
            out["ok"] = True
            out["value"] = shown
            return out
        await _pw_fill()
        fiber = await fiber_text_commit(locator, want)
        out.update(fiber)
        out["retried_after_fill"] = True
        await _settle()
        return out

    await _pw_fill()
    shown = ""
    try:
        shown = (await locator.input_value()) or ""
    except Exception:
        shown = ""
    if want and shown.strip() and (
        shown.strip().lower() == want.strip().lower()
        or want.strip().lower() in shown.strip().lower()
    ):
        out["ok"] = True
        out["value"] = shown
        return out
    fiber = await fiber_text_commit(locator, want)
    out.update(fiber)
    out["empty_readback_fiber_retry"] = True
    await _settle()
    return out


_HOW_HEARD_OPTION_SELS = [
    '[data-automation-id="promptOption"]',
    '[role="option"]',
    '[data-automation-id*="promptOption" i]',
]

# NEVER bare multiSelectContainer — Workday reuses that for Country Phone Code.
# Typing Indeed into the first multiSelectContainer is the Morningstar live bug.
_HOW_HEARD_INPUT_SELS = (
    'input[name="source--source"], '
    '[data-automation-id="source--source"], '
    '[data-automation-id="formField-source"] input, '
    '[data-automation-id="formField-how_heard"] input, '
    '[data-automation-id="formField-howDidYouHear"] input, '
    '[data-automation-id="formField-candidateSource"] input, '
    '[data-automation-id="formField-source"] [data-automation-id="multiSelectContainer"] input, '
    '[data-automation-id="formField-how_heard"] [data-automation-id="multiSelectContainer"] input, '
    '[data-automation-id="formField-howDidYouHear"] [data-automation-id="multiSelectContainer"] input, '
    '[data-automation-id="formField-candidateSource"] [data-automation-id="multiSelectContainer"] input'
)

_HOW_HEARD_WRAP_SELS = (
    '[data-automation-id="formField-source"], '
    '[data-automation-id="formField-how_heard"], '
    '[data-automation-id="formField-howDidYouHear"], '
    '[data-automation-id="formField-candidateSource"]'
)


_PHONE_COUNTRY_AID_RE = re.compile(
    r"countryphonecode|phonenumber--countryphonecode|phone.?country|"
    r"phonecountry|calling.?code|dial.?code|"
    r"addresssection_country(?!region)|formfield-country(?!region)",
    re.I,
)


async def is_how_heard_safe_filter_input(loc) -> bool:
    """False when *loc* sits inside Country Phone Code / Address Country.

    Guards hierarchical how-heard / fiber loops that previously used bare
    ``multiSelectContainer`` and typed job-board names into dial-code filters.
    """
    try:
        meta = await loc.evaluate(
            """el => {
              const wrap = el.closest(
                '[data-automation-id*="formField"], [data-automation-id="multiSelectContainer"]'
              ) || el.parentElement;
              const aid = (
                (wrap && wrap.getAttribute('data-automation-id')) ||
                el.getAttribute('data-automation-id') ||
                el.getAttribute('name') ||
                el.id ||
                ''
              ).toLowerCase();
              const label = (
                (wrap && (wrap.innerText || '')) || ''
              ).toLowerCase().slice(0, 220);
              return {aid, label};
            }"""
        ) or {}
    except Exception:
        return False
    aid = str(meta.get("aid") or "")
    label = str(meta.get("label") or "")
    if how_heard_scope_reject_aid(aid) or _PHONE_COUNTRY_AID_RE.search(aid):
        return False
    if looks_like_phone_country_or_address_chip(label):
        # Allow when wrap also clearly names how-heard / source
        if not re.search(
            r"how\s*(did|do)\s*you\s*hear|where\s*did\s*you\s*hear|"
            r"hear about|candidate\s*source|\bsource\b",
            label,
        ):
            return False
    # Prefer explicit source / hear aids; reject unknown bare containers that
    # look like dial rows only.
    if "multiselectcontainer" in aid and not re.search(
        r"source|how_?heard|hearabou|candidate", aid
    ):
        if re.search(
            r"country\s*phone|phone\s*country|\(\s*\+\d|united\s*states\s*\(\+",
            label,
        ):
            return False
    return True


async def _read_how_heard_wrap_text(page) -> str:
    """Chip chrome for how-heard / source (prefer formField, not filter alone).

    Strips open ``role=listbox`` soup so promptOption rows cannot fake a chip,
    but keeps ``selectedItem`` / ``N items selected`` chrome (never strip
    committed pills — 0842Z leftover was label-only after listbox scrape).
    """
    for sel in _HOW_HEARD_WRAP_SELS.split(", "):
        try:
            loc = page.locator(sel.strip()).first
            if await loc.count() == 0:
                continue
            try:
                if not await loc.is_visible(timeout=200):
                    continue
            except Exception:
                pass
            snip = str(
                await loc.evaluate(
                    """(el) => {
                      const wrap = el.closest('[data-automation-id*="formField"]') || el;
                      const selected = wrap.querySelector(
                        '[data-automation-id*="selectedItem"], '
                        + '[data-automation-id*="selectedChip"], '
                        + '[data-automation-id*="pill"]'
                      );
                      if (selected) {
                        const t = (selected.innerText || selected.textContent || '')
                          .replace(/\\s+/g, ' ').trim();
                        if (t) return t.slice(0, 240);
                      }
                      const clone = wrap.cloneNode(true);
                      clone.querySelectorAll('[role="listbox"], .menu').forEach((n) => n.remove());
                      return (clone.innerText || clone.textContent || '')
                        .replace(/\\s+/g, ' ').trim().slice(0, 240);
                    }"""
                )
                or ""
            ).strip()
            if not snip:
                continue
            if looks_like_phone_country_or_address_chip(snip):
                continue
            return snip[:240]
        except Exception:
            continue
    return ""


def _norm_how_heard_label(text: str | None) -> str:
    """Normalize option/chip text; strip drill chevrons (``Website >``)."""
    t = " ".join(str(text or "").strip().split())
    return re.sub(r"\s*[>›»]\s*$", "", t).strip().lower()


async def _list_how_heard_options(page, *, max_n: int = 40) -> list[dict[str, Any]]:
    """Visible prompt/listbox options with category heuristics."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sel in _HOW_HEARD_OPTION_SELS:
        try:
            loc = page.locator(sel)
            n = await loc.count()
        except Exception:
            n = 0
        if n <= 0:
            continue
        for i in range(min(n, max_n)):
            el = loc.nth(i)
            try:
                if not await el.is_visible(timeout=120):
                    continue
                text = ((await el.inner_text()) or "").strip()
            except Exception:
                continue
            if not text or len(text) > 120:
                continue
            key = _norm_how_heard_label(text)
            if key in seen:
                continue
            seen.add(key)
            meta: dict[str, Any] = {"text": text, "index": i, "selector": sel}
            try:
                meta.update(
                    await el.evaluate(
                        """el => {
                          const t = (el.innerText || el.textContent || '').trim();
                          const ariaExp = el.getAttribute('aria-expanded');
                          const hasPopup = el.getAttribute('aria-haspopup');
                          const role = el.getAttribute('role') || '';
                          const cls = (el.className || '').toString();
                          const chevron = !!(
                            el.querySelector('[data-automation-id*="icon" i], svg, [class*="caret" i], [class*="chevron" i], [class*="arrow" i]')
                          );
                          return {
                            ariaExpanded: ariaExp,
                            hasPopup: hasPopup,
                            role: role,
                            chevron: chevron,
                            classHint: cls.slice(0, 80),
                          };
                        }"""
                    )
                )
            except Exception:
                pass
            is_cat = is_how_heard_category_option(text)
            # Expandable rows often have chevron / aria-expanded even when label
            # is not in our static category list.
            if not is_cat and (
                meta.get("chevron")
                or meta.get("ariaExpanded") in ("true", "false")
                or str(meta.get("hasPopup") or "").lower() in ("true", "menu", "listbox")
                or str(text or "").rstrip().endswith(">")
            ):
                # Don't treat concrete leaves (Indeed) as expandable categories
                leafish = text.lower() in {
                    "indeed",
                    "linkedin",
                    "company website",
                    "careerbuilder",
                    "google for jobs",
                    "other",
                }
                if not leafish:
                    is_cat = True
            meta["is_category"] = bool(is_cat)
            out.append(meta)
        if out:
            break
    return out


# MCP NXP: off-screen Select One produced ZERO [role=option] until
# scrollIntoView({block:'center'}) → click → wait 1–1.5s → exact option click.
# pressEnter is always false. Re-query locators after every click.
WIDGET_OPEN_WAIT_MS = 1500
_OPTION_EXACT_SELS = (
    '[role="option"]',
    '[data-automation-id="promptOption"]',
    '[data-automation-id*="promptOption" i]',
)
_FOS_TYPES = frozenset({"FIELD_OF_STUDY", "DISCIPLINE", "MAJOR"})
_SKILLS_AID_RE = re.compile(r"\bskills?\b|suggested.?skill|skill.?chip|formfield-skill", re.I)


async def scroll_widget_into_view(locator) -> bool:
    """MCP: ``scrollIntoView({block:'center'})`` before widget click.

    Playwright ``scroll_into_view_if_needed`` only peeks the edge; off-screen
    Workday Select One then opens with zero ``[role=option]`` / promptOption.
    """
    if locator is None:
        return False
    try:
        await locator.evaluate(
            """el => {
              try { el.scrollIntoView({ block: 'center', inline: 'nearest' }); }
              catch (e) {}
            }"""
        )
        return True
    except Exception:
        try:
            await locator.scroll_into_view_if_needed()
            return True
        except Exception:
            return False


async def option_is_skills_suggested(el, text: str = "") -> bool:
    """True when the node lives under Skills suggested-chip chrome (not FoS)."""
    blob = str(text or "")
    try:
        meta = await el.evaluate(
            """el => {
              const w = el.closest('[data-automation-id], [class*="skill" i]') || el;
              const aid = (w.getAttribute && w.getAttribute('data-automation-id')) || '';
              const cls = (w.className || '').toString();
              return (aid + ' ' + cls).slice(0, 240);
            }"""
        )
        blob = f"{meta} {blob}"
    except Exception:
        pass
    return bool(_SKILLS_AID_RE.search(blob or ""))


async def open_list_widget(
    page,
    locator,
    *,
    wait_ms: int = WIDGET_OPEN_WAIT_MS,
    root: Any | None = None,
    field_type: str = "",
) -> dict[str, Any]:
    """scrollIntoView({block:'center'}) → click → wait for options. Never Enter.

    Polls up to ``wait_ms`` (1–1.5s) for ``[role=option]`` / promptOption.
    Callers must re-query option locators after this returns (handles go stale).
    Dummy-only — never submits.
    """
    detail: dict[str, Any] = {"opened": False, "options": [], "algorithm": "mcp_open_list"}
    if locator is None:
        detail["error"] = "no_locator"
        return detail
    await scroll_widget_into_view(locator)
    try:
        await locator.click(timeout=4000, force=True)
    except Exception:
        try:
            await locator.click(timeout=4000)
        except Exception as e:
            detail["error"] = f"open_failed:{e}"[:120]
            return detail
    detail["opened"] = True
    timeout = max(1000, min(int(wait_ms or WIDGET_OPEN_WAIT_MS), 1500))
    try:
        _opts, texts = await wait_for_option_texts(
            page,
            timeout_ms=timeout,
            nudge=False,
            allow_enter_nudge=False,
            root=root,
            field_type=field_type,
            portal_fallback=str(field_type or "").upper() not in _FOS_TYPES,
        )
        detail["options"] = [t for t in (texts or []) if t][:24]
    except Exception as e:
        detail["wait_error"] = str(e)[:80]
    return detail


async def click_option_exact_text(
    page,
    text: str,
    *,
    root: Any | None = None,
    timeout_ms: int = 4000,
    allow_soft: bool = True,
    field_type: str = "",
) -> bool:
    """Re-query ``[role=option]`` / promptOption and click EXACT text. Never Enter.

    MCP NXP: do not reuse a stale nth() handle after any prior click.
    FoS: only options in *this* popup — never Skills suggested chips.
    """
    want = (text or "").strip()
    if not want:
        return False
    want_n = _norm_how_heard_label(want)
    ftype = str(field_type or "").upper()
    fos = ftype in _FOS_TYPES
    scope = root if root is not None else page

    async def _try_click(el) -> bool:
        try:
            if not await el.is_visible(timeout=400):
                return False
        except Exception:
            return False
        if fos:
            try:
                t = ((await el.inner_text()) or "").strip()
            except Exception:
                t = ""
            if await option_is_skills_suggested(el, t):
                return False
        try:
            await scroll_widget_into_view(el)
        except Exception:
            pass
        try:
            await el.click(timeout=timeout_ms)
            return True
        except Exception:
            try:
                await el.click(timeout=timeout_ms, force=True)
                return True
            except Exception:
                return False

    # 1) Fresh role=option by exact accessible name (MCP exact text).
    try:
        loc = scope.get_by_role("option", name=want, exact=True)
        n = await loc.count()
        for i in range(min(n, 12)):
            if await _try_click(loc.nth(i)):
                return True
    except Exception:
        pass

    # 2) Re-query promptOption / role=option; match exact innerText (or chevron-stripped).
    for sel in _OPTION_EXACT_SELS:
        try:
            loc = scope.locator(sel)
            n = await loc.count()
        except Exception:
            n = 0
        exact_idx: list[int] = []
        soft_idx: list[int] = []
        for i in range(min(n, 80)):
            el = loc.nth(i)
            try:
                if not await el.is_visible(timeout=120):
                    continue
                t = ((await el.inner_text()) or "").strip()
            except Exception:
                continue
            if not t or is_placeholder_select_value(t):
                continue
            if fos and await option_is_skills_suggested(el, t):
                continue
            tn = _norm_how_heard_label(t)
            if t == want or tn == want_n:
                exact_idx.append(i)
            elif allow_soft and soft_value_match(want, t):
                soft_idx.append(i)
        # Re-query nth() at click time (list may have shifted, but same pass).
        for i in exact_idx:
            if await _try_click(loc.nth(i)):
                return True
        if allow_soft and len(soft_idx) == 1:
            if await _try_click(loc.nth(soft_idx[0])):
                return True

    # 3) Portaled menu: FoS must NOT fall back page-wide (Skills chips).
    if root is not None and not fos:
        return await click_option_exact_text(
            page,
            want,
            root=None,
            timeout_ms=timeout_ms,
            allow_soft=allow_soft,
            field_type=field_type,
        )
    return False


async def _click_option_by_text(page, text: str, *, timeout_ms: int = 4000) -> bool:
    """Click a visible option by exact text (soft only if unique). Never Enter."""
    return await click_option_exact_text(
        page, text, timeout_ms=timeout_ms, allow_soft=True
    )


def _rank_how_heard_categories(
    visible: list[str],
    preferred: list[str],
) -> list[str]:
    """Order drill-in categories: Website / Job Board / Internet first."""
    seen: set[str] = set()
    ranked: list[tuple[int, str]] = []
    pref = [_norm_how_heard_label(p) for p in preferred if p]
    keyword_boost = (
        ("website", 950),
        ("job board", 940),
        ("internet", 930),
        ("online job", 920),
    )
    for raw in visible:
        norm = _norm_how_heard_label(raw)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        score = 0
        for i, p in enumerate(pref):
            if p == norm or soft_value_match(p, raw) or p in norm:
                score = max(score, 1000 - i)
        for kw, boost in keyword_boost:
            if kw in norm:
                score = max(score, boost)
        ranked.append((score, raw))
    ranked.sort(key=lambda x: (-x[0], x[1].lower()))
    out = [raw for _, raw in ranked if _ > 0]
    out.extend(raw for sc, raw in ranked if sc <= 0 and raw not in out)
    for p in preferred:
        if p and p not in out:
            out.append(p)
    return out[:4]


def _pick_priority_leaf_option(
    option_texts: list[str],
    leaf_candidates: list[str] | None = None,
) -> str | None:
    """First visible leaf matching priority (handles ``Web - CareerBuilder``)."""
    opts = [str(o).strip() for o in (option_texts or []) if str(o).strip()]
    if not opts:
        return None
    try:
        from fill_verify import (
            how_heard_leaf_candidates,
            is_how_heard_category_option,
            pick_how_heard_from_options,
        )

        leaves = leaf_candidates or how_heard_leaf_candidates()
        non_cat = [o for o in opts if not is_how_heard_category_option(o)]
        picked = pick_how_heard_from_options(non_cat or opts)
        if picked:
            return picked
        for leaf in leaves:
            for opt in opts:
                if is_how_heard_category_option(opt):
                    continue
                if soft_value_match(leaf, opt) or leaf.lower() in opt.lower():
                    return opt
    except Exception:
        for leaf in leaf_candidates or ["LinkedIn", "Indeed"]:
            for opt in opts:
                if soft_value_match(leaf, opt) or leaf.lower() in opt.lower():
                    return opt
    return None


async def _wait_for_how_heard_options(
    page,
    *,
    filter_input: Any | None = None,
    timeout_ms: int = 2400,
    wait_ms: int = 450,
) -> list[dict[str, Any]]:
    """Poll until category or leaf options appear after opening how-heard."""
    deadline = timeout_ms
    step = max(120, wait_ms // 2)
    opts: list[dict[str, Any]] = []
    for _ in range(max(1, deadline // step)):
        opts = await _list_how_heard_options(page)
        if opts:
            return opts
        try:
            await page.wait_for_timeout(step)
        except Exception:
            break
    if not opts and filter_input is not None:
        try:
            await nudge_listbox_after_type(page, filter_input, allow_enter=False)
            await page.wait_for_timeout(wait_ms)
        except Exception:
            pass
        opts = await _list_how_heard_options(page)
    return opts


async def fill_hierarchical_how_heard(
    page,
    filter_input: Any,
    *,
    leaf_candidates: list[str] | None = None,
    category_candidates: list[str] | None = None,
    wait_ms: int = 450,
) -> dict[str, Any]:
    """Workday how-heard: open → drill category → pick leaf → chip → close.

    Matches live NXP/Walmart UX (never type Indeed/LinkedIn into the filter first):
      1. Click/open ``source--source`` combobox (scoped — never phone dial)
      2. Wait for category list (``promptOption`` rows with drill-in / children)
      3. Prefer Website / Job Board / Internet — click to drill in
      4. Pick first priority leaf (``Web - CareerBuilder`` soft-matches CareerBuilder)
      5. Verify chip readback, settle listbox, stop — one honest pass only
    """
    detail: dict[str, Any] = {
        "algorithm": "hierarchical_how_heard",
        "option_clicked": False,
        "ok": False,
        "verified": False,
        "committed": False,
        "status": "not_attempted",
    }
    try:
        from fill_verify import (
            how_heard_category_candidates,
            how_heard_leaf_candidates,
        )

        leaves = [
            c
            for c in (leaf_candidates or how_heard_leaf_candidates())
            if c and not is_how_heard_category_option(c)
        ]
        cats = list(category_candidates or how_heard_category_candidates())
    except Exception:
        leaves = list(leaf_candidates or ["LinkedIn", "Indeed", "CareerBuilder"])[:8]
        cats = list(category_candidates or ["Website", "Job Board", "Internet job board"])

    detail["leaves"] = leaves
    detail["categories"] = cats

    async def _ensure_open(*, clear_filter: bool = True) -> None:
        await scroll_widget_into_view(filter_input)
        try:
            await filter_input.click(timeout=2000, force=True)
        except Exception:
            try:
                await filter_input.focus()
            except Exception:
                pass
        if clear_filter:
            try:
                await filter_input.fill("")
            except Exception:
                pass
        # MCP: wait 1–1.5s for category/leaf list (re-query after click).
        try:
            await wait_for_option_texts(
                page,
                timeout_ms=WIDGET_OPEN_WAIT_MS,
                filter_input=filter_input,
                nudge=False,
                allow_enter_nudge=False,
            )
        except Exception:
            try:
                await page.wait_for_timeout(WIDGET_OPEN_WAIT_MS)
            except Exception:
                pass

    async def _chip_ok(leaf: str) -> tuple[bool, str]:
        snip = await _read_how_heard_wrap_text(page)
        ok = how_heard_source_committed(snip, [leaf, *leaves])
        if is_multiselect_uncommitted(snip):
            ok = False
        return ok, snip

    snip0 = await _read_how_heard_wrap_text(page)
    # Any valid source leaf chip is done — don't reopen to fight CareerBuilder vs Glassdoor.
    if how_heard_source_committed(snip0, leaves) or committed_how_heard_leaf(snip0):
        try:
            await force_close_how_heard_widget(page)
        except Exception:
            try:
                await settle_open_listbox(page)
            except Exception:
                pass
        leaf0 = committed_how_heard_leaf(snip0) or snip0[:120]
        detail.update(
            {
                "status": "already_committed",
                "ok": True,
                "verified": True,
                "committed": True,
                "readback": snip0[:120],
                "picked": leaf0,
                "value": leaf0,
                "skipped_already_correct": True,
            }
        )
        return detail

    async def _commit_leaf(
        leaf_text: str,
        *,
        path: str,
        subsection: str = "",
        canonical: str = "",
    ) -> bool:
        clicked = await _click_option_by_text(page, leaf_text)
        detail["option_clicked"] = clicked
        detail["picked"] = leaf_text
        detail["path"] = path
        if subsection:
            detail["subsection"] = subsection
        canon = canonical or leaf_text
        detail["value"] = canon
        if not clicked:
            return False
        # MCP: wait for CHIP (not typed filter text).
        try:
            await page.wait_for_timeout(800)
        except Exception:
            pass
        ok, snip = await _chip_ok(canon)
        if ok:
            try:
                await force_close_how_heard_widget(page)
            except Exception:
                try:
                    await settle_open_listbox(page)
                except Exception:
                    pass
            detail.update(
                {
                    "status": "picked",
                    "ok": True,
                    "verified": True,
                    "committed": True,
                    "readback": snip[:120],
                }
            )
            return True
        return False

    async def _try_leaf_list(
        opts: list[dict[str, Any]],
        *,
        path: str,
        subsection: str = "",
    ) -> bool:
        texts = [str(o.get("text") or "") for o in opts if not o.get("is_category")]
        picked = _pick_priority_leaf_option(texts, leaves)
        if not picked:
            return False
        detail["attempted_leaf"] = picked
        return await _commit_leaf(
            picked,
            path=path,
            subsection=subsection,
            canonical=picked,
        )

    # --- Step 1: open combobox (no leaf typing) ---
    await _ensure_open(clear_filter=True)
    try:
        await page.wait_for_timeout(wait_ms)
    except Exception:
        pass
    opts = await _wait_for_how_heard_options(
        page, filter_input=filter_input, wait_ms=wait_ms
    )
    detail["options_on_open"] = [o.get("text") for o in opts[:16]]

    cat_opts = [o for o in opts if o.get("is_category")]
    # Flat tenants only: skip top-level leaf pick when category rows are open
    # (otherwise Job Board "LinkedIn" can win before Website → Web - LinkedIn).
    if not cat_opts:
        if await _try_leaf_list(opts, path="leaf_direct_top"):
            return detail

    visible_cats = [str(o.get("text") or "") for o in cat_opts if o.get("text")]
    nav_cats = _rank_how_heard_categories(visible_cats, cats)
    detail["nav_categories"] = nav_cats[:4]

    try:
        from fill_step_log import note_step

        note_step(
            None,
            action="how_heard_hierarchy_open",
            label="how_heard",
            field_type="HOW_HEARD",
            after=leaves[0] if leaves else "",
            via="hierarchical_how_heard",
            reason=f"opts={len(opts)} cats={len(cat_opts)}",
        )
    except Exception:
        pass

    # --- Step 2–4: one honest category drill (no filter thrash) ---
    for cat in nav_cats[:3]:
        detail["attempted_category"] = cat
        if not any(
            soft_value_match(cat, v) or _norm_how_heard_label(cat) == _norm_how_heard_label(v)
            for v in visible_cats
        ):
            # Category not visible — reopen clean once (no typing cat into filter)
            await _ensure_open(clear_filter=True)
            try:
                await page.wait_for_timeout(wait_ms)
            except Exception:
                pass
            opts = await _wait_for_how_heard_options(
                page, filter_input=filter_input, wait_ms=wait_ms
            )
            cat_opts = [o for o in opts if o.get("is_category")]
            visible_cats = [str(o.get("text") or "") for o in cat_opts if o.get("text")]
            if not any(
                soft_value_match(cat, v)
                or _norm_how_heard_label(cat) == _norm_how_heard_label(v)
                for v in visible_cats
            ):
                continue

        opened = await _click_option_by_text(page, cat)
        detail.setdefault("subsection_opens", []).append(
            {"category": cat, "opened": opened}
        )
        if not opened:
            continue
        try:
            await page.wait_for_timeout(wait_ms)
        except Exception:
            pass
        sub_opts = await _wait_for_how_heard_options(
            page, filter_input=filter_input, wait_ms=wait_ms
        )
        detail["options_in_subsection"] = [o.get("text") for o in sub_opts[:16]]
        if await _try_leaf_list(
            sub_opts, path="category_then_leaf", subsection=cat
        ):
            return detail
        # One category path tried — do not type leaf into filter; try next category only
        await _ensure_open(clear_filter=True)
        try:
            await page.wait_for_timeout(wait_ms)
        except Exception:
            pass
        opts = await _wait_for_how_heard_options(
            page, filter_input=filter_input, wait_ms=wait_ms
        )
        visible_cats = [
            str(o.get("text") or "") for o in opts if o.get("is_category") and o.get("text")
        ]

    snip_f = await _read_how_heard_wrap_text(page)
    detail.update(
        {
            "status": "no_leaf_chip",
            "ok": False,
            "verified": False,
            "committed": False,
            "readback": (snip_f or "")[:120],
            "reason": "hierarchical_no_chip",
        }
    )
    try:
        await force_close_how_heard_widget(page)
    except Exception:
        try:
            await settle_open_listbox(page)
        except Exception:
            pass
    return detail


async def wait_for_option_texts(
    page,
    *,
    selectors: list[str] | None = None,
    timeout_ms: int = 2400,
    poll_ms: int = 160,
    max_options: int = 40,
    filter_input: Any | None = None,
    nudge: bool = False,
    allow_enter_nudge: bool = False,
    root: Any | None = None,
    field_type: str = "",
    portal_fallback: bool = True,
) -> tuple[Any, list[str]]:
    """Poll until listbox options appear. Returns (locator, texts).

    When ``root`` is a locator (e.g. a react-select ``.select__container``),
    options are located *within* it so overlapping option text across sibling
    selects can't cross-click (GH mounts every select menu at once — the
    Hispanic "Decline To Self Identify" would otherwise win a RACE Decline
    click). Falls back to a page-wide scan if the scoped root finds nothing
    (menus portalled to <body> on some tenants). FoS sets ``portal_fallback``
    false so Skills suggested chips cannot be collected.
    """
    ftype = str(field_type or "").upper()
    fos = ftype in _FOS_TYPES
    sels = selectors or (
        [
            "[role='listbox'] [role='option']",
            "[role='option']",
            '[data-automation-id="promptOption"]',
            '[data-automation-id*="promptOption" i]',
        ]
        if fos
        else [
            ".select__option",
            "[id*='react-select'][id*='option']",
            "[role='listbox'] [role='option']",
            "[role='option']",
            '[data-automation-id="promptOption"]',
            '[data-automation-id*="promptOption" i]',
        ]
    )
    scope = root if root is not None else page
    loops = max(1, timeout_ms // max(poll_ms, 50))
    last_loc = scope.locator(sels[0])
    nudged = False
    for loop_i in range(loops):
        for sel in sels:
            loc = scope.locator(sel)
            try:
                n = await loc.count()
            except Exception:
                n = 0
            if n <= 0:
                continue
            texts: list[str] = []
            for i in range(min(n, max_options)):
                el = loc.nth(i)
                try:
                    t = (await el.inner_text()).strip()
                except Exception:
                    t = ""
                # ATS2-001: never drop rows — empty/placeholder become "" so
                # texts[i] always matches loc.nth(i). Stripping Select… used to
                # remap Illinois→Idaho (texts=[Idaho,Illinois] → nth(1)=Idaho).
                if fos and t:
                    try:
                        if await option_is_skills_suggested(el, t):
                            texts.append("")
                            continue
                    except Exception:
                        pass
                if t and not is_placeholder_select_value(t):
                    texts.append(t)
                else:
                    texts.append("")
            if any(texts):
                return loc, texts
        # Mid-poll nudge once when Workday/async prompts stay empty.
        # pressEnter is always false (MCP NXP) — never Enter-nudge.
        if nudge and not nudged and loop_i == max(1, loops // 3):
            nudged = True
            try:
                await nudge_listbox_after_type(
                    page, filter_input, allow_enter=False
                )
            except Exception:
                pass
        try:
            await page.wait_for_timeout(poll_ms)
        except Exception:
            break
    if root is not None and portal_fallback and not fos:
        # Scoped root found nothing — menu may be portalled outside the
        # container on this tenant. Retry page-wide so scoping never regresses
        # option discovery (only the cross-select clobber is prevented).
        return await wait_for_option_texts(
            page,
            selectors=selectors,
            timeout_ms=timeout_ms,
            poll_ms=poll_ms,
            max_options=max_options,
            filter_input=filter_input,
            nudge=nudge,
            allow_enter_nudge=False,
            root=None,
            field_type=field_type,
            portal_fallback=False,
        )
    return last_loc, []


async def click_best_option(
    page,
    candidates: list[str],
    *,
    score_fn: Callable[[str, str], int] | None = None,
    timeout_ms: int = 4000,
    device_type: bool = False,
    intent: str = "",
) -> dict[str, Any]:
    """Wait for options, score against aliases, click best match. Never Enter.

    ATS-013: honor ``intent`` / confusable reject and ``device_type`` (reject dial).
    Click indices are always against the unfiltered locator.
    """
    score_fn = score_fn or _default_score_option
    primary = (intent or "").strip() or (
        str(candidates[0]).strip() if candidates else ""
    )
    opts, texts = await wait_for_option_texts(
        page, timeout_ms=max(timeout_ms, 1500), allow_enter_nudge=False
    )
    detail: dict[str, Any] = {
        "option_clicked": False,
        "options": texts[:12],
        "picked": None,
        "score": 0,
    }
    if not texts:
        detail["error"] = "no_options_visible"
        return detail

    def _reject(intent_s: str, opt: str) -> bool:
        if reject_confusable_state_option(intent_s, opt):
            return True
        if reject_confusable_country_option(intent_s, opt):
            return True
        if device_type:
            try:
                from gh_select import looks_like_dial_code_option

                if looks_like_dial_code_option(opt):
                    return True
            except Exception:
                pass
        return False

    filtered, orig_idx = filter_options_preserving_indices(
        texts, primary, reject_fn=_reject
    )
    detail["options"] = filtered[:12] or texts[:12]
    if not filtered:
        detail["error"] = "no_matching_option"
        return detail
    ranked = remap_ranked_to_original(
        rank_option_matches(filtered, candidates, score_fn), orig_idx
    )
    # Shared commit floor — never soft 40 / last-word (Male⊂Female, A.A. class)
    min_s = commit_min_score_for("", primary) if primary else 50
    clear = clear_closest_match(
        ranked,
        at_last_word=False,
        min_score=min_s,
        intent=primary,
    )
    if not clear:
        detail["error"] = "no_matching_option"
        return detail
    best_i, picked, best_s = clear
    clicked = await click_option_exact_text(
        page, picked, timeout_ms=timeout_ms, allow_soft=True
    )
    if not clicked:
        try:
            await opts.nth(best_i).click(timeout=timeout_ms)
            clicked = True
        except Exception as e:
            detail["error"] = f"option_click_failed:{e}"[:120]
            return detail
    if clicked:
        detail["option_clicked"] = True
        detail["picked"] = picked
        detail["score"] = best_s
    return detail


async def _type_into_filter(filter_input, text: str, *, timeout_ms: int = 4000) -> bool:
    """Clear + type filter text. Returns False on hard failure.

    Never hang ``.fill()`` on buttons/non-inputs (Workday State prompt).
    Prefer short fill timeouts; fall back to keyboard type.
    """
    fill_ms = min(int(timeout_ms), 3500)
    try:
        await filter_input.click(timeout=timeout_ms, force=True)
    except Exception:
        pass
    tag = ""
    try:
        tag = (
            await filter_input.evaluate("el => (el.tagName || '').toLowerCase()")
        ) or ""
    except Exception:
        tag = ""
    is_input = tag in ("input", "textarea")
    if is_input:
        try:
            await filter_input.fill("", timeout=fill_ms)
        except Exception:
            try:
                await filter_input.press("Meta+a")
                await filter_input.press("Backspace")
            except Exception:
                pass
        try:
            await filter_input.fill(str(text)[:80], timeout=fill_ms)
            return True
        except Exception:
            pass
    else:
        try:
            await filter_input.press("Meta+a")
            await filter_input.press("Backspace")
        except Exception:
            pass
    try:
        await filter_input.press_sequentially(str(text)[:80], delay=18)
        return True
    except Exception:
        try:
            await filter_input.type(str(text)[:80], delay=20)
            return True
        except Exception:
            return False


async def _append_into_filter(filter_input, text: str, *, timeout_ms: int = 4000) -> bool:
    """ATS3-013: append filter text without clear/retype (reduces word-by-word thrash)."""
    frag = str(text or "")[:40]
    if not frag:
        return True
    try:
        await filter_input.click(timeout=timeout_ms, force=True)
    except Exception:
        pass
    try:
        await filter_input.press_sequentially(frag, delay=16)
        return True
    except Exception:
        pass
    try:
        await filter_input.type(frag, delay=18)
        return True
    except Exception:
        return False


def _early_unique_high_match(
    ranked: list[tuple[int, int, str]],
    *,
    intent: str = "",
    min_score: int = 80,
) -> tuple[int, str, int] | None:
    """ATS3-013: commit mid-loop when a unique high-score option is clear.

    Does not lower ATS3-008 last-word floors — only accelerates early exits.
    """
    if intent:
        ranked = [
            r for r in ranked if not reject_confusable_state_option(intent, r[2])
        ]
    if not ranked:
        return None
    best_s, best_i, best_t = ranked[0]
    if best_s < min_score:
        return None
    above = [r for r in ranked if r[0] >= max(min_score - 10, 70)]
    if len(above) == 1:
        return best_i, best_t, best_s
    if len(ranked) >= 2 and (best_s - ranked[1][0]) >= 15:
        return best_i, best_t, best_s
    return None


async def typable_dropdown_narrow_and_click(
    page,
    *,
    filter_input,
    value: str,
    aliases: list[str],
    score_fn: Callable[[str, str], int] | None = None,
    timeout_ms: int = 5000,
    use_type: bool = True,
    option_selectors: list[str] | None = None,
    report: dict | None = None,
    label: str = "",
    field_type: str = "",
    root: Any | None = None,
) -> dict[str, Any]:
    """Enumerate options → score → click best (type-to-filter only if needed).

    Primary path (Elanco Degree lesson): do NOT push free-form intended text
    first. Open list is assumed already focused by caller; we collect options
    (scroll virtualized), score against aliases, commit only above threshold.
    Typing is a last resort with a sanitized token (\"Master\", not essays).
    """
    score_fn = score_fn or _default_score_option
    cands = [c for c in (aliases or []) if c]
    primary = (value or "").strip() or (cands[0] if cands else "")
    if primary and primary not in cands:
        cands = [primary, *cands]
    words = split_select_words(primary)
    if not words and cands:
        words = split_select_words(cands[0])
    ftype_u = str(field_type or "").upper()
    lab_l = str(label or "").lower()
    min_score = commit_min_score_for(field_type, label)
    # Workday "How Did You Hear" / School / State prompts need async option load.
    needs_async_nudge = ftype_u in (
        "HOW_HEARD",
        "SOURCE",
        "SCHOOL",
        "ADDRESS_STATE",
        "DEGREE",
    ) or (
        "how did you hear" in lab_l
        or "school" in lab_l
        or "degree" in lab_l
        or "countryregion" in lab_l
        or lab_l.endswith("state")
    )
    # HOW_HEARD: never type-as-commit (MCP: click → category → leaf → chip).
    prefer_fiber = needs_async_nudge and ftype_u in (
        "SOURCE",
        "SCHOOL",
        "ADDRESS_STATE",
    )
    # Always type full state name for US abbrevs (IL → Illinois), never bare IL.
    if ftype_u == "ADDRESS_STATE" or "countryregion" in lab_l:
        expanded = expand_state_value(primary)
        if expanded:
            primary = expanded[0]
            cands = list(dict.fromkeys([*expanded, *cands]))
            words = split_select_words(primary)
    detail: dict[str, Any] = {
        "mode": "enumerate_then_score",
        "option_clicked": False,
        "words": words[:12],
        "steps": [],
        "typed_frag": "",
        "aliases_tried": cands[:12],
        "min_score": min_score,
    }
    if not cands:
        detail["error"] = "no_aliases"
        return detail

    # Fiber may open/filter async prompts — commit only when score ≥ threshold.
    # Never accept sc==0 (legacy "fiber didn't report") — that class committed
    # wrong options on SCHOOL / HOW_HEARD / STATE (same family as Degree A.A.).
    if prefer_fiber and filter_input is not None:
        try:
            # Sanitize filter token for enumerate-first kinds (never Indeed→phone)
            fiber_tok = (
                sanitized_typeahead_token(ftype_u, primary, cands) or primary
            )
            fiber = await fiber_search_select(
                page,
                filter_input,
                fiber_tok,
                aliases=cands,
                wait_ms=min(2200, max(1200, timeout_ms // 2)),
                field_type=ftype_u,
            )
            detail["fiber_search_select"] = {
                k: fiber.get(k)
                for k in ("status", "picked", "score", "options", "error", "algorithm")
            }
            picked_f = str(fiber.get("picked") or "")
            sc = int(fiber.get("score") or 0)
            soft_ok = soft_value_match(primary, picked_f) or any(
                soft_value_match(c, picked_f) for c in cands[:8] if c
            )
            fiber_ok = (
                fiber.get("option_clicked")
                and picked_f
                and sc >= min_score
                and not reject_confusable_state_option(primary, picked_f)
                and not reject_confusable_country_option(primary, picked_f)
                and soft_ok
            )
            if fiber_ok:
                detail.update(
                    {
                        "option_clicked": True,
                        "picked": picked_f,
                        "score": sc,
                        "algorithm": "fiber_search_select",
                        "options": (fiber.get("options") or [])[:12],
                    }
                )
                return detail
            if fiber.get("option_clicked") and (sc < min_score or not fiber_ok):
                detail["fiber_below_threshold"] = sc
                detail["fiber_rejected_picked"] = picked_f[:60]
        except Exception as e:
            detail["fiber_error"] = str(e)[:80]

    async def _click_text(_opts_loc, _texts_live: list[str], want: str) -> bool:
        """Re-query + exact text click (MCP). Never Enter, never stale nth()."""
        return await click_option_exact_text(
            page,
            want,
            root=root,
            timeout_ms=timeout_ms,
            allow_soft=True,
            field_type=ftype_u,
        )

    # Fiber may leave a filter token in the input that zeros the option list
    # (Capco HOW_HEARD: fiber typed "Internet job board" → 0 options → enumerate
    # saw an empty menu → no_matching_option). Clear it so the enumerate-first
    # path can score the FULL short list (Job Board / Indeed / Other) and commit.
    if prefer_fiber and filter_input is not None and not detail.get("option_clicked"):
        try:
            cur = (await filter_input.input_value()) or ""
        except Exception:
            cur = ""
        if cur.strip():
            try:
                await filter_input.fill("")
                await page.wait_for_timeout(180)
            except Exception:
                pass

    # --- PRIMARY: enumerate → score → commit (no typing) ---
    opts, texts = await enumerate_listbox_options(
        page,
        selectors=option_selectors,
        root=root,
        filter_input=filter_input,
        timeout_ms=min(timeout_ms, 3200 if needs_async_nudge else 2200),
        max_scrolls=8 if ftype_u in ("DEGREE", "SCHOOL", "HOW_HEARD") else 5,
        field_type=ftype_u,
    )
    detail["enumerated"] = len(texts)
    detail["options"] = texts[:20]
    # HOW_HEARD: priority walk — first matching board/site commits, no alias thrash.
    if ftype_u == "HOW_HEARD" and texts:
        try:
            from fill_verify import pick_how_heard_from_options

            prio_pick = pick_how_heard_from_options(texts)
            if prio_pick:
                clicked = await _click_text(opts, texts, prio_pick)
                if clicked:
                    detail.update(
                        {
                            "option_clicked": True,
                            "picked": prio_pick,
                            "score": 100,
                            "algorithm": "how_heard_priority",
                        }
                    )
                    return detail
        except Exception:
            pass
    pick = pick_best_scored_option(
        texts, cands, score_fn, intent=primary, min_score=min_score
    )
    detail["steps"].append(
        {
            "phase": "enumerate",
            "n_options": len(texts),
            "best": (pick[2], pick[1][:60]) if pick else None,
        }
    )
    if pick:
        _idx, picked, best_s = pick
        # Prefer clicking by live text (virtualized indices may drift after scroll)
        live_opts, live_texts = await wait_for_option_texts(
            page,
            selectors=option_selectors,
            timeout_ms=1500,
            filter_input=None,
            nudge=False,
            root=root,
            field_type=ftype_u,
            portal_fallback=ftype_u not in _FOS_TYPES,
        )
        clicked = await _click_text(live_opts, live_texts or texts, picked)
        if clicked:
            detail.update(
                {
                    "option_clicked": True,
                    "picked": picked,
                    "score": best_s,
                    "algorithm": "enumerate_then_score",
                }
            )
            return detail

    # --- FALLBACK: sanitized type-to-filter only when list huge/empty ---
    # HOW_HEARD / FoS: if this popup has no commit, SKIP — never type into
    # Skills suggested chips or use filter-as-commit (MCP NXP).
    safe_tok = sanitized_typeahead_token(ftype_u, primary, cands)
    skip_type = ftype_u in ("HOW_HEARD", "SOURCE") or ftype_u in _FOS_TYPES
    allow_type = (
        (not skip_type)
        and bool(use_type)
        and bool(safe_tok)
        and (len(texts) == 0 or len(texts) >= _HUGE_OPTION_LIST or pick is None)
    )
    # Degree/country always may try sanitized filter once if enumerate missed
    if (
        not allow_type
        and not skip_type
        and use_type
        and safe_tok
        and ftype_u in _ENUMERATE_FIRST_TYPES
        and pick is None
    ):
        allow_type = True

    if not allow_type:
        if ftype_u in _FOS_TYPES:
            try:
                await force_close_fos_widget(page)
                await settle_open_listbox(page)
            except Exception:
                pass
        detail["error"] = "no_matching_option"
        detail["reason"] = "enumerate_below_threshold_no_safe_filter"
        return detail

    detail["typed_frag"] = safe_tok
    detail["algorithm"] = "sanitize_filter_then_score"
    try:
        await filter_input.fill("")
    except Exception:
        pass
    ok_type = await _type_into_filter(filter_input, safe_tok, timeout_ms=timeout_ms)
    if not ok_type:
        try:
            await page.keyboard.type(safe_tok[:28], delay=25)
        except Exception:
            detail["error"] = "type_failed"
            return detail
    try:
        await page.wait_for_timeout(550 if needs_async_nudge else 320)
    except Exception:
        pass
    if needs_async_nudge:
        try:
            nudge = await nudge_listbox_after_type(
                page, filter_input, allow_enter=False
            )
            detail.setdefault("nudges", []).append(nudge)
        except Exception:
            pass

    opts, texts = await enumerate_listbox_options(
        page,
        selectors=option_selectors,
        root=root,
        filter_input=filter_input,
        timeout_ms=min(timeout_ms, 2800),
        max_scrolls=4,
        field_type=ftype_u,
    )
    detail["enumerated_after_filter"] = len(texts)
    detail["options"] = texts[:20]
    pick = pick_best_scored_option(
        texts, cands, score_fn, intent=primary, min_score=min_score
    )
    detail["steps"].append(
        {
            "phase": "sanitize_filter",
            "typed": safe_tok,
            "n_options": len(texts),
            "best": (pick[2], pick[1][:60]) if pick else None,
        }
    )
    if not pick:
        if ftype_u in _FOS_TYPES:
            try:
                await force_close_fos_widget(page)
                await settle_open_listbox(page)
            except Exception:
                pass
        detail["error"] = "no_matching_option"
        detail["reason"] = "filtered_below_threshold"
        return detail
    _idx, picked, best_s = pick
    live_opts, live_texts = await wait_for_option_texts(
        page,
        selectors=option_selectors,
        timeout_ms=1500,
        filter_input=None,
        nudge=False,
        root=root,
        field_type=ftype_u,
        portal_fallback=ftype_u not in _FOS_TYPES,
    )
    clicked = await _click_text(live_opts, live_texts or texts, picked)
    if not clicked:
        detail["error"] = "option_click_failed:exact_text_miss"
        return detail
    detail.update(
        {
            "option_clicked": True,
            "picked": picked,
            "score": best_s,
            "algorithm": "sanitize_filter_then_score",
        }
    )
    return detail


async def fill_typable_dropdown(
    page,
    *,
    control,
    value: str,
    aliases: list[str] | None = None,
    filter_input: Any | None = None,
    read_committed: Callable[[], Any] | None = None,
    commit_probe: Callable[[], Any] | None = None,
    score_fn: Callable[[str, str], int] | None = None,
    timeout_ms: int = 5000,
    option_selectors: list[str] | None = None,
    field_type: str = "",
    label: str = "",
    report: dict | None = None,
) -> dict[str, Any]:
    """Open → enumerate options → score → commit best (sanitize-filter fallback)."""
    score_fn = score_fn or _default_score_option
    value = normalize_select_answer(label, str(value or ""), field_type=field_type)
    cands = list(aliases or [])
    if value and value not in cands:
        cands = [value, *cands]
    detail: dict[str, Any] = {
        "mode": "typable_dropdown",
        "algorithm": "enumerate_then_score",
        "value": value,
        "option_clicked": False,
        "ok": False,
        "verified": False,
        "committed": False,
    }

    async def _read() -> str:
        if read_committed is None:
            return await read_combobox_display(control)
        try:
            r = read_committed()
            if hasattr(r, "__await__"):
                r = await r  # type: ignore[misc]
            return str(r or "")
        except Exception:
            return ""

    async def _probe() -> bool:
        if commit_probe is None:
            return False
        try:
            r = commit_probe()
            if hasattr(r, "__await__"):
                r = await r  # type: ignore[misc]
            return bool(r)
        except Exception:
            return False

    loc_field = is_location_field(field_type, label)
    reads_from_input = read_committed is not None and loc_field
    city_hint = value or (cands[0] if cands else "")
    filt = filter_input if filter_input is not None else control

    shown0 = await _read()
    dep0 = await _probe()
    ftype_u = str(field_type or "").upper()
    if ftype_u == "PHONE_COUNTRY_CODE" and not shown0:
        try:
            shown0 = await read_phone_country_field_snip(page)
        except Exception:
            shown0 = shown0 or ""
    if shown0 and not is_placeholder_select_value(shown0):
        # Country Phone Code: US (+1) chip with empty filter — never reopen
        if ftype_u == "PHONE_COUNTRY_CODE" and is_committed_us_phone_country_readback(
            shown0
        ):
            detail.update(
                {
                    "ok": True,
                    "verified": True,
                    "committed": True,
                    "skipped_already_correct": True,
                    "reason": "already_correct_skip",
                    "readback": shown0[:120],
                    "picked": shown0[:120],
                }
            )
            return detail
        # Field of Study: committed chip (Science-Computer) — never type Other/filter
        if ftype_u in ("FIELD_OF_STUDY", "DISCIPLINE", "MAJOR") and field_of_study_committed(
            shown0, cands, dom_chip=True
        ):
            detail.update(
                {
                    "ok": True,
                    "verified": True,
                    "committed": True,
                    "skipped_already_correct": True,
                    "reason": "already_correct_skip",
                    "readback": shown0[:120],
                    "picked": shown0[:120],
                }
            )
            return detail
        # Places Location: probe closed menu + blur-stable + alias match (Airwallex)
        if loc_field:
            probe0 = await probe_location_committed(
                page,
                filt if filter_input is not None else control,
                cands,
                commit_probe=commit_probe,
                city=city_hint,
            )
            can_skip = probe0.get("committed") or location_display_matches(
                shown0, cands, city=city_hint
            )
            dep_ok = bool(probe0.get("dependent_revealed") or dep0)
            # When commit_probe is set (Ashby zip), display match alone must not skip —
            # filter text can match without list pick and zip stays hidden.
            if can_skip and (dep_ok or commit_probe is None):
                detail.update(
                    {
                        "ok": True,
                        "verified": True,
                        "committed": True,
                        "skipped_already_correct": True,
                        "reason": "location_already_committed_skip",
                        "readback": (probe0.get("shown") or shown0)[:120],
                        "picked": (probe0.get("shown") or shown0)[:120],
                        "location_probe": {
                            k: probe0.get(k)
                            for k in (
                                "listbox_open",
                                "stable_after_blur",
                                "dependent_revealed",
                            )
                        },
                    }
                )
                return detail
        elif ftype_u in ("FIELD_OF_STUDY", "DISCIPLINE", "MAJOR"):
            pass  # never generic select_readback_ok — Arts-Other ⊃ Other alias trap
        elif select_readback_ok(
            shown0,
            cands,
            picked=shown0,
            score_fn=score_fn,
            min_score=50,
        ) and not is_uncommitted_filter_text(
            shown0,
            value,
            picked=shown0,
            from_input=reads_from_input,
        ):
            detail.update(
                {
                    "ok": True,
                    "verified": True,
                    "committed": True,
                    "skipped_already_correct": True,
                    "reason": "already_correct_skip",
                    "readback": shown0[:120],
                    "picked": shown0[:120],
                }
            )
            try:
                from fill_step_log import note_step

                note_step(
                    report,
                    action="skip_already_correct",
                    label=label[:80],
                    field_type=field_type[:48],
                    before=shown0[:120],
                    after=shown0[:120],
                    via="verified_select",
                    layer="typable_dropdown",
                    reason="already_correct_skip",
                )
            except Exception:
                pass
            return detail

    async def _attempt(*, use_type: bool) -> dict[str, Any]:
        out: dict[str, Any] = {"option_clicked": False}
        opened = await open_list_widget(
            page,
            control,
            wait_ms=WIDGET_OPEN_WAIT_MS,
            field_type=ftype_u,
        )
        if not opened.get("opened"):
            out["error"] = opened.get("error") or "open_failed"
            return out
        click = await typable_dropdown_narrow_and_click(
            page,
            filter_input=filt,
            value=value,
            aliases=cands,
            score_fn=score_fn,
            timeout_ms=timeout_ms,
            use_type=use_type,
            option_selectors=option_selectors,
            report=report,
            label=label,
            field_type=field_type,
        )
        out.update(click)
        if not click.get("option_clicked"):
            # Stop thrash: Location already shows intended dummy line (menu closed)
            if loc_field:
                probe = await probe_location_committed(
                    page,
                    filt,
                    cands,
                    commit_probe=commit_probe,
                    city=city_hint,
                )
                shown_now = str(probe.get("shown") or await _read())
                can_skip = probe.get("committed") or location_display_matches(
                    shown_now, cands, city=city_hint
                )
                dep_ok = bool(probe.get("dependent_revealed") or await _probe())
                if can_skip and (dep_ok or commit_probe is None):
                    out.update(
                        {
                            "ok": True,
                            "verified": True,
                            "committed": True,
                            "skipped_already_correct": True,
                            "reason": "location_already_committed_no_retype",
                            "readback": shown_now[:120],
                            "picked": shown_now[:120],
                        }
                    )
                    return out
            out["error"] = click.get("error") or "option_not_clicked"
            return out
        try:
            await page.wait_for_timeout(450)
        except Exception:
            pass
        # ATS3-012 / ATS2-017: Escape after Location pick can cancel Ashby
        # dependent zip mount. Prefer Tab blur when commit_probe is armed.
        try:
            if commit_probe is not None:
                await page.keyboard.press("Tab")
            else:
                from captcha_pause import press_escape_unless_captcha

                await press_escape_unless_captcha(page)
        except Exception:
            pass
        shown = await _read()
        dep = await _probe()
        picked = str(click.get("picked") or "")
        # Honesty: DOM `shown` must match intended aliases. Never commit on
        # clicked option text alone (ONEOK: picked=Illinois, shown=Idaho).
        committed = False
        if loc_field and location_display_matches(
            shown or "", cands, city=city_hint
        ):
            committed = True
        if shown and select_readback_ok(
            shown,
            cands,
            typed_frag=click.get("typed_frag") if use_type else None,
            picked=picked,
            score_fn=score_fn,
            min_score=50,
        ):
            committed = True
        # Dependent reveal only helps when DOM already matches — never alone.
        if (
            not committed
            and dep
            and click.get("option_clicked")
            and shown
            and select_readback_ok(
                shown, cands, picked=picked, score_fn=score_fn, min_score=50
            )
        ):
            committed = True
        # Clicked Illinois but DOM still Idaho → explicit mismatch
        if (
            picked
            and shown
            and select_readback_ok(
                picked, cands, picked=picked, score_fn=score_fn, min_score=40
            )
            and not select_readback_ok(
                shown, cands, picked=picked, score_fn=score_fn, min_score=50
            )
        ):
            committed = False
            out["error"] = "readback_mismatch_after_click"
        out["readback"] = (shown or picked)[:120]
        out["dependent_revealed"] = dep
        out["committed"] = committed
        out["ok"] = committed
        out["verified"] = committed
        if not committed:
            out["error"] = out.get("error") or "select_not_committed"
        return out

    # Enumerate-first is inside typable_dropdown_narrow_and_click; use_type
    # only gates the sanitized type-to-filter fallback after scoring the list.
    first = await _attempt(use_type=True)
    detail.update(first)
    if detail.get("ok"):
        return detail
    # Wrong virtualized option (e.g. Idaho after aiming for Illinois): clear + retry once
    if first.get("error") == "readback_mismatch_after_click":
        wrong = str(first.get("readback") or "")
        try:
            from captcha_pause import press_escape_unless_captcha

            await press_escape_unless_captcha(page)
        except Exception as e:
            _log.debug("Escape after readback mismatch failed: %s", e)
        try:
            await page.wait_for_timeout(200)
        except Exception as e:
            _log.debug("wait after Escape failed: %s", e)
        # Prefer aliases that do not soft-match the wrong DOM text
        narrowed = [
            c
            for c in cands
            if wrong
            and not soft_value_match(str(c), wrong)
            and str(c).strip().lower() != wrong.strip().lower()
        ]
        if narrowed:
            cands.clear()
            cands.extend(narrowed)
        retry = await _attempt(use_type=True)
        detail["select_retry"] = {
            k: retry.get(k)
            for k in ("ok", "error", "picked", "readback", "committed", "score")
            if k in retry
        }
        detail["select_retry"]["reason"] = "readback_mismatch_after_click"
        if retry.get("ok"):
            detail.update(retry)
            detail["retried_after_mismatch"] = True
            return detail
    # Second pass: enumerate-only (no type) in case filter left the list empty
    second = await _attempt(use_type=False)
    detail["retry"] = {
        k: second.get(k)
        for k in ("ok", "error", "picked", "options", "committed", "score")
    }
    if second.get("ok"):
        detail.update(second)
        detail["retried"] = True
    else:
        detail["error"] = (
            second.get("error") or detail.get("error") or "typable_dropdown_failed"
        )
        detail["ok"] = False
        detail["verified"] = False
        detail["committed"] = False
    # MCP: never leave FoS listbox open after a miss (NXP 1045Z).
    if ftype_u in _FOS_TYPES and not detail.get("ok"):
        try:
            await force_close_fos_widget(page)
            await settle_open_listbox(page)
        except Exception:
            pass
    return detail


async def read_gh_select_display(container) -> str:
    """Committed Greenhouse react-select display (never the filter input value)."""
    try:
        sv = container.locator(".select__single-value").first
        if await sv.count():
            shown = (await sv.inner_text()).strip()
            if shown and not is_placeholder_select_value(shown):
                return shown
        multi = container.locator(".select__multi-value__label")
        n = await multi.count()
        if n:
            parts = []
            for i in range(min(n, 8)):
                try:
                    parts.append((await multi.nth(i).inner_text()).strip())
                except Exception:
                    pass
            joined = ", ".join(p for p in parts if p)
            if joined:
                return joined
        ph = container.locator(".select__placeholder").first
        if await ph.count():
            return ""
    except Exception:
        pass
    return ""


async def read_combobox_display(locator) -> str:
    """Best-effort committed value for ARIA combobox / button / native select."""
    try:
        tag = (await locator.evaluate("el => (el.tagName || '').toLowerCase()"))
        role = ((await locator.get_attribute("role")) or "").lower()
        cls = ((await locator.get_attribute("class")) or "").lower()
        name = ((await locator.get_attribute("name")) or "").lower()
        aid = ((await locator.get_attribute("data-automation-id")) or "").lower()
        if tag == "select":
            raw = await locator.evaluate(
                """el => {
                  const o = el.options && el.selectedIndex >= 0
                    ? el.options[el.selectedIndex] : null;
                  return (o && (o.label || o.text || o.value) || '').trim();
                }"""
            )
            return "" if is_placeholder_select_value(raw) else (raw or "")
        # Workday How-Heard / source multiselect: prefer formField chip chrome
        # over empty filter input (input_value alone causes alias thrash).
        if tag == "input" and (
            "source" in name
            or "source" in aid
            or "how" in aid
            or role == "combobox"
        ):
            try:
                wrap = locator.locator(
                    "xpath=ancestor::*[@data-automation-id='formField-source' "
                    "or contains(@data-automation-id,'formField-source') "
                    "or contains(@data-automation-id,'formField-how') "
                    "or contains(@data-automation-id,'formField-candidateSource') "
                    "or contains(@data-automation-id,'formField-howDidYouHear')][1]"
                ).first
                if await wrap.count():
                    try:
                        wrap_aid = (
                            await wrap.get_attribute("data-automation-id") or ""
                        )
                    except Exception:
                        wrap_aid = ""
                    if how_heard_scope_reject_aid(wrap_aid):
                        return ""
                    chip = ((await wrap.inner_text()) or "").strip()
                    if chip and looks_like_phone_country_or_address_chip(chip):
                        return ""
                    if chip and how_heard_source_committed(chip):
                        return chip[:200]
            except Exception:
                pass
        if "select__" in cls or role == "combobox":
            try:
                container = locator.locator(
                    "xpath=ancestor::div[contains(@class,'select__container') "
                    "or contains(@class,'select-shell')][1]"
                ).first
                if await container.count():
                    shown = await read_gh_select_display(container)
                    if shown:
                        return shown
            except Exception:
                pass
        if tag == "button" or role in ("combobox", "button", "listbox"):
            txt = (await locator.inner_text()).strip()
            if txt and not is_placeholder_select_value(txt):
                return txt
            aria = (await locator.get_attribute("aria-label") or "").strip()
            return "" if is_placeholder_select_value(aria) else aria
        # Workday Country Phone Code filter input (often plain text, not role=combobox)
        if tag == "input":
            pc_name = "countryphonecode" in name or "phonenumber--countryphonecode" in name
            if (
                "countryphonecode" in aid
                or "phonenumber--countryphonecode" in aid
                or "phonecountry" in aid
                or pc_name
                or re.search(r"country\s*phone\s*code|phone\s*country", name, re.I)
            ):
                try:
                    wrap = locator.locator(
                        "xpath=ancestor::*[contains(@data-automation-id,'formField') "
                        "and (contains(@data-automation-id,'countryPhoneCode') "
                        "or contains(@data-automation-id,'phoneNumber--countryPhoneCode') "
                        "or contains(@data-automation-id,'phoneCountry'))][1]"
                    ).first
                    if await wrap.count():
                        chip = ((await wrap.inner_text()) or "").strip()
                        if chip and is_committed_us_phone_country_readback(chip):
                            return chip[:200]
                except Exception:
                    pass
        # Field of Study / discipline / major: filter input empty but chip committed
        if tag == "input" and (
            "fieldofstudy" in aid
            or "fieldofstudy" in name
            or "discipline" in aid
            or "major" in aid
            or role == "combobox"
        ):
            chip_txt = await read_workday_formfield_chip(locator)
            if chip_txt:
                return chip_txt[:200]
            try:
                wrap = locator.locator(
                    "xpath=ancestor::*[contains(@data-automation-id,'formField') "
                    "and (contains(@data-automation-id,'fieldOfStudy') "
                    "or contains(@data-automation-id,'discipline') "
                    "or contains(@data-automation-id,'major'))][1]"
                ).first
                if await wrap.count():
                    chip = ((await wrap.inner_text()) or "").strip()
                    if chip and workday_wrap_text_has_chip(chip):
                        return chip[:200]
            except Exception:
                pass
        if tag == "input" and (
            role == "combobox" or "select__input" in cls or "select__" in cls
        ):
            return ""
    except Exception:
        pass
    return ""


def location_search_query(city: str, *, state: str = "", country: str = "") -> str:
    """Short filter text for Places-like autocomplete (never paste full committed line)."""
    raw = (city or "").strip()
    if not raw:
        return ""
    head = re.split(r"[,/|]", raw)[0].strip()
    return (head or raw)[:40]


def location_option_aliases(
    city: str,
    *,
    state: str = "",
    state_full: str = "",
    country: str = "",
) -> list[str]:
    """Fuzzy aliases for City/State/Country location options (Ashby Places-like)."""
    city_s = (city or "").strip()
    city_head = location_search_query(city_s) or city_s
    st = (state or "").strip()
    st_full = (state_full or "").strip() or (
        "Illinois" if st.upper() == "IL" else st
    )
    ctry = (country or "").strip() or "United States"
    out: list[str] = []
    for cand in (
        f"{city_head}, {st_full}, {ctry}" if city_head and st_full else "",
        f"{city_head}, {st}, {ctry}" if city_head and st else "",
        f"{city_head}, {st_full}" if city_head and st_full else "",
        f"{city_head}, {st}" if city_head and st else "",
        f"{city_head}, {ctry}" if city_head else "",
        city_head,
        city_s,
        st_full,
        ctry,
    ):
        c = (cand or "").strip()
        if c and c not in out:
            out.append(c)
    return out


def location_display_matches(
    shown: str | None,
    aliases: list[str] | None = None,
    *,
    city: str | None = None,
    state: str = "",
    state_full: str = "",
    country: str = "United States",
    score_fn: Callable[[str, str], int] | None = None,
    min_score: int = 50,
) -> bool:
    """True when Places/Location display matches dummy city/state/country aliases.

    Airwallex/Ashby: ``Springfield, Illinois, United States`` is committed when it
    matches aliases — not filter thrash. City-only tokens (no comma) are never
    treated as committed Places picks.
    """
    s = (shown or "").strip()
    if not s or is_placeholder_select_value(s):
        return False
    if "," not in s:
        return False
    cands = [c for c in (aliases or []) if c]
    if not cands:
        st_full = state_full or ("Illinois" if (state or "IL").upper() == "IL" else state)
        cands = location_option_aliases(
            city or location_search_query(s) or "Springfield",
            state=state or "IL",
            state_full=st_full or "Illinois",
            country=country or "United States",
        )
    # Match full City, State, Country lines — never country/city-only aliases alone
    full_cands = [
        a
        for a in cands
        if (a or "").count(",") >= 2
        or (
            "," in (a or "")
            and len([p for p in (a or "").split(",") if p.strip()]) >= 2
        )
    ]
    match_pool = full_cands if full_cands else cands
    if match_pool and select_readback_ok(
        s, match_pool, picked=s, score_fn=score_fn, min_score=min_score
    ):
        sl = s.lower()
        st_full = state_full or (
            "Illinois" if (state or "IL").upper() == "IL" else (state_full or state)
        )
        st_abbr = (state or "IL").strip().lower()
        if st_full and st_full.lower() in sl:
            return True
        if st_abbr and re.search(rf",\s*{re.escape(st_abbr)}\b", sl):
            return True
        return False
    # Fuzzy: city token + state/country tokens in comma-separated Places line
    if "," in s:
        sl = s.lower()
        city_h = location_search_query(city or cands[0] if cands else "")
        if city_h and city_h.lower() in sl:
            st_full = state_full or ("Illinois" if (state or "IL").upper() == "IL" else state)
            if st_full and st_full.lower() in sl and (
                "united states" in sl
                or (country or "United States").lower() in sl
            ):
                return True
    return False


def is_location_committed(
    shown: str | None,
    aliases: Iterable[str],
    *,
    listbox_open: bool = False,
    has_single_value: bool = False,
    aria_selected: bool = False,
    stable_after_blur: bool = False,
    dependent_revealed: bool = False,
    city: str | None = None,
    state: str = "",
    state_full: str = "",
    country: str = "United States",
) -> bool:
    """True when Location matches dummy aliases and menu is not open."""
    if listbox_open:
        return False
    if dependent_revealed or has_single_value or aria_selected or stable_after_blur:
        al = list(aliases) if aliases else []
        return location_display_matches(
            shown, al, city=city, state=state, state_full=state_full, country=country
        )
    return location_display_matches(
        shown,
        list(aliases) if aliases else None,
        city=city,
        state=state,
        state_full=state_full,
        country=country,
    )


async def probe_location_committed(
    page,
    locator,
    aliases: list[str],
    *,
    commit_probe: Callable[[], Any] | None = None,
    city: str = "",
    state: str = "",
    state_full: str = "",
    country: str = "United States",
) -> dict[str, Any]:
    """Probe listbox/ blur / zip signals — skip Location retype when committed."""
    out: dict[str, Any] = {
        "committed": False,
        "shown": "",
        "listbox_open": False,
        "has_single_value": False,
        "aria_selected": False,
        "stable_after_blur": False,
        "dependent_revealed": False,
    }
    shown = await read_location_autocomplete_value(locator)
    out["shown"] = shown
    try:
        container = locator.locator(
            "xpath=ancestor::div[contains(@class,'select__container') "
            "or contains(@class,'select-shell')][1]"
        ).first
        if await container.count():
            sv = await read_gh_select_display(container)
            if sv:
                out["has_single_value"] = True
                shown = sv
                out["shown"] = sv
    except Exception:
        pass
    try:
        lb = page.locator(
            "[role='listbox']:visible [role='option'], "
            ".select__menu-list .select__option"
        ).first
        out["listbox_open"] = (
            await lb.count() > 0 and await lb.is_visible(timeout=120)
        )
    except Exception:
        pass
    try:
        sel = page.locator(
            "[role='option'][aria-selected='true'], "
            ".select__option--is-selected"
        ).first
        out["aria_selected"] = await sel.count() > 0
    except Exception:
        pass
    if commit_probe is not None:
        try:
            r = commit_probe()
            if hasattr(r, "__await__"):
                r = await r  # type: ignore[misc]
            out["dependent_revealed"] = bool(r)
        except Exception:
            pass
    try:
        before = (shown or "").strip()
        if before and not out["listbox_open"]:
            # ATS2-017: when zip/dependent probe is armed, Tab/blur only —
            # Escape can cancel Ashby dependent-field reveal.
            try:
                if commit_probe is not None:
                    await page.keyboard.press("Tab")
                else:
                    from captcha_pause import press_escape_unless_captcha

                    await press_escape_unless_captcha(page)
            except Exception:
                pass
            try:
                await locator.evaluate("el => el.blur && el.blur()")
            except Exception:
                pass
            try:
                await page.wait_for_timeout(280)
            except Exception:
                pass
            after = (await read_location_autocomplete_value(locator) or "").strip()
            out["stable_after_blur"] = bool(before and after and before == after)
            if after:
                shown = after
                out["shown"] = after
    except Exception:
        pass
    out["committed"] = is_location_committed(
        shown,
        aliases,
        listbox_open=out["listbox_open"],
        has_single_value=out["has_single_value"],
        aria_selected=out["aria_selected"],
        stable_after_blur=out["stable_after_blur"],
        dependent_revealed=out["dependent_revealed"],
        city=city,
        state=state,
        state_full=state_full,
        country=country,
    )
    return out


def is_location_uncommitted_display(
    shown: str | None,
    *,
    city: str | None = None,
    option_clicked: bool = False,
    dependent_revealed: bool = False,
    aliases: list[str] | None = None,
    state: str = "",
    state_full: str = "",
    country: str = "United States",
) -> bool:
    """True when Location looks typed but not selected from the list."""
    if dependent_revealed and option_clicked:
        return False
    if dependent_revealed and shown and not is_placeholder_select_value(shown):
        return False
    s = (shown or "").strip()
    if not s or is_placeholder_select_value(s):
        return True
    if option_clicked:
        return False
    # Comma-separated Places line without state+country tokens → filter paste, not committed
    if "," in s:
        sl = s.lower()
        st_full = state_full or ("Illinois" if (state or "IL").upper() == "IL" else state)
        has_state = bool(st_full and st_full.lower() in sl)
        has_country = "united states" in sl or (country or "United States").lower() in sl
        if not (has_state and has_country):
            return True
    # Committed line matching dummy city/state/country → not uncommitted (skip thrash)
    if location_display_matches(
        s,
        aliases,
        city=city,
        state=state,
        state_full=state_full,
        country=country,
    ):
        return False
    if "," in s and len(s) >= 12:
        return True
    city_h = location_search_query(city or "")
    if city_h and city_h.lower() in s.lower() and "," in s:
        return True
    return False


async def read_location_autocomplete_value(locator) -> str:
    """Read Location combobox text without treating it as committed."""
    try:
        tag = (await locator.evaluate("el => (el.tagName || '').toLowerCase()"))
        if tag == "input":
            try:
                return (await locator.input_value() or "").strip()
            except Exception:
                pass
        txt = (await locator.inner_text() or "").strip()
        if txt:
            return txt
        try:
            return (await locator.input_value() or "").strip()
        except Exception:
            return txt
    except Exception:
        return ""


async def fill_location_autocomplete(
    page,
    locator,
    *,
    city: str,
    state: str = "",
    state_full: str = "",
    country: str = "",
    aliases: list[str] | None = None,
    commit_probe: Callable[[], Any] | None = None,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """Ashby/Places Location via universal word-by-word typable dropdown."""
    aliases = list(aliases or []) or location_option_aliases(
        city, state=state, state_full=state_full, country=country
    )
    # Prefer full City, State, Country as the intended value for word split
    intended = aliases[0] if aliases else str(city)

    result = await fill_typable_dropdown(
        page,
        control=locator,
        filter_input=locator,
        value=intended,
        aliases=aliases,
        read_committed=lambda: read_location_autocomplete_value(locator),
        commit_probe=commit_probe,
        timeout_ms=timeout_ms,
        field_type="ADDRESS_CITY",
        label="Location",
        option_selectors=[
            "[role='listbox'] [role='option']",
            "[role='option']",
            ".select__option",
        ],
    )
    result["mode"] = "location_autocomplete"
    return result


async def fill_workday_combobox(
    page,
    control,
    value: str,
    *,
    aliases: list[str] | None = None,
    filter_input: Any | None = None,
    read_committed: Callable[[], Any] | None = None,
    timeout_ms: int = 5000,
    label: str = "",
    field_type: str = "",
    option_selectors: list[str] | None = None,
    reject_option: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Workday listbox combobox via universal word-by-word typable dropdown."""
    score_fn = _default_score_option
    rejectors: list[Callable[[str], bool]] = []
    if reject_option is not None:
        rejectors.append(reject_option)
    # HOW_HEARD: never commit category/subsection headers as the selected option
    if str(field_type or "").upper() == "HOW_HEARD":
        rejectors.append(is_how_heard_category_option)

    if rejectors:

        def _score_with_reject(opt: str, alias: str) -> int:
            for rej in rejectors:
                try:
                    if rej(opt):
                        return 0
                except Exception:
                    pass
            return int(score_fn(opt, alias) or 0)

        score_fn = _score_with_reject

    value_n = normalize_select_answer(label, str(value or ""), field_type=field_type)
    cands = list(aliases or [])
    if value_n and value_n not in cands:
        cands = [value_n, *cands]
    # Prefer leaf aliases for how-heard
    if str(field_type or "").upper() == "HOW_HEARD":
        leaves = [c for c in cands if not is_how_heard_category_option(c)]
        cats = [c for c in cands if is_how_heard_category_option(c)]
        cands = [*leaves, *cats]
        if is_how_heard_category_option(value_n) and leaves:
            value_n = leaves[0]
        # MCP NXP: click → category → leaf → chip. Never type as commit.
        filt = filter_input if filter_input is not None else control
        try:
            hier = await fill_hierarchical_how_heard(
                page,
                filt,
                leaf_candidates=leaves or None,
                category_candidates=cats or None,
            )
            hier["mode"] = "workday_combobox"
            if hier.get("ok") and hier.get("committed"):
                return hier
            if not hier.get("committed"):
                hier["ok"] = False
                hier["verified"] = False
                hier["reason"] = hier.get("reason") or "how_heard_no_chip"
                return hier
        except Exception as e:
            return {
                "mode": "workday_combobox",
                "algorithm": "hierarchical_how_heard",
                "ok": False,
                "verified": False,
                "committed": False,
                "option_clicked": False,
                "error": f"how_heard_hier:{e}"[:120],
                "reason": "how_heard_no_chip",
            }

    detail = await fill_typable_dropdown(
        page,
        control=control,
        filter_input=filter_input if filter_input is not None else control,
        value=value_n,
        aliases=cands,
        read_committed=read_committed,
        score_fn=score_fn,
        timeout_ms=timeout_ms,
        field_type=field_type,
        label=label,
        option_selectors=option_selectors
        or [
            '[role="option"]',
            '[data-automation-id="promptOption"]',
            '[data-automation-id*="option" i]',
        ],
    )
    detail["mode"] = "workday_combobox"
    # HOW_HEARD: category pick without chip chrome is not committed
    if str(field_type or "").upper() == "HOW_HEARD":
        picked = str(detail.get("picked") or "")
        readback = str(detail.get("readback") or "")
        if is_how_heard_category_option(picked) or not how_heard_source_committed(
            readback or picked, cands
        ):
            if not how_heard_source_committed(readback, cands):
                detail["ok"] = False
                detail["verified"] = False
                detail["committed"] = False
                detail["reason"] = detail.get("reason") or "how_heard_category_not_chip"
    return detail


# Field types / labels that must never receive essay loc.fill — only verified commit.
_SELECT_FIELD_TYPES = frozenset(
    {
        "ADDRESS_COUNTRY",
        "PHONE_COUNTRY_CODE",
        "PHONE_DEVICE",
        "ADDRESS_CITY",
        "WORK_AUTH",
        "US_RESIDENCE",
        "SPONSORSHIP",
        "HOW_HEARD",
        "GENDER",
        "HISPANIC",
        "RACE",
        "VETERAN",
        "DISABILITY",
        "AGE_RANGE",
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
        "TALENT_HUB",
        "AGE_18",
        "FELONY",
        "BACKGROUND_CHECK",
        "TERMS_CONSENT",
        "ACCOMMODATIONS",
        "ACCOMMODATIONS_DETAILS",
        "EMPLOYEE_REFERRAL",
        "REFERRAL_EMAIL",
    }
)

_SELECT_LABEL_RE = re.compile(
    r"based\s+in\s+any\s+of\s+these\s+states|require\s+.*sponsorship|"
    r"authorized\s+to\s+work|employment\s+eligibility|gender|race|ethnicity|"
    r"veteran|disabilit|\bschool\b|\bdegree\b|\bdiscipline\b|\bmajor\b|"
    r"field\s+of\s+study|salary|how\s+did\s+you\s+hear|"
    r"were\s+you\s+referred|referred\s+to\s+this|"
    r"yes\s*/\s*no|select\s+one",
    re.I,
)

_SELECT_REASONS = frozenset(
    {
        "gh_select_failed",
        "widget_failed",
        "widget_unverified",
        "select_unverified",
        "select_failed",
        "live_empty_after_claimed_verified",
        "readback_uncommitted_or_mismatch",
        "combo_requires_option_click",
        "combo_requires_verified_select",
        "verified_select_unverified",
        "no matching option",
    }
)


def is_select_field(
    field_type: str = "",
    label: str = "",
    row: dict | None = None,
) -> bool:
    """True when a leftover must use verified select commit (never essay loc.fill)."""
    r = row or {}
    lab = str(label or r.get("label") or "")
    ftype = (field_type or str(r.get("type") or "")).upper()
    # Essays are free-text — never selects (even when reason=no_value)
    try:
        from page_progress import is_essay_leftover

        if is_essay_leftover(r if r else {"label": lab, "type": ftype}):
            return False
    except Exception:
        pass
    if ftype in _SELECT_FIELD_TYPES:
        return True
    html_type = str(r.get("html_type") or "").lower()
    if html_type in ("select-one", "combobox", "search-dropdown", "select"):
        return True
    if r.get("options"):
        return True
    reason = str(r.get("reason") or "")
    if reason in _SELECT_REASONS or "select" in reason.lower():
        return True
    if _SELECT_LABEL_RE.search(lab):
        return True
    return False


async def verified_select(
    page,
    *,
    label: str,
    value: str,
    field_type: str = "",
    selector: str = "",
    aliases: list[str] | None = None,
    timeout_ms: int = 5000,
    report: dict | None = None,
) -> dict[str, Any]:
    """Route ALL select answers through verified commit — never type-and-hope.

    Order: Greenhouse react-select by label → native ``<select>`` → generic combobox.
    Always normalizes LLM essays to short option tokens before typing.
    """
    from gh_select import _score_option, aliases_for, fill_gh_select

    value_n = normalize_select_answer(label, str(value or ""), field_type=field_type)
    cands = list(aliases or [])
    if not cands and field_type:
        try:
            cands = list(aliases_for(field_type, value_n))
        except Exception:
            cands = []
    if value_n and value_n not in cands:
        cands = [value_n, *cands]

    detail: dict[str, Any] = {
        "mode": "verified_select",
        "value": value_n,
        "aliases_tried": cands[:12],
        "ok": False,
        "verified": False,
        "option_clicked": False,
    }

    # 1) Greenhouse react-select (label-driven)
    if label:
        try:
            gh = await fill_gh_select(
                page,
                label,
                value_n,
                field_type=field_type or "",
                aliases=cands,
                timeout_ms=timeout_ms,
                report=report,
            )
            if gh.get("ok") and gh.get("verified", gh.get("ok")):
                detail.update(
                    {
                        "ok": True,
                        "verified": True,
                        "via": "gh_select",
                        "picked": gh.get("picked"),
                        "shown": gh.get("shown"),
                        "readback": gh.get("shown") or gh.get("picked"),
                        "option_clicked": True,
                        "skipped_already_correct": gh.get("skipped_already_correct"),
                    }
                )
                return detail
            detail["gh_select_error"] = gh.get("error")
            detail["gh_select_shown"] = gh.get("shown")
        except Exception as e:
            detail["gh_select_error"] = str(e)[:120]

    # 2) Native select / combobox via selector
    sel = (selector or "").strip()
    if sel:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                tag = await loc.evaluate("el => (el.tagName || '').toLowerCase()")
                role = ((await loc.get_attribute("role")) or "").lower()
                cls = ((await loc.get_attribute("class")) or "").lower()
                if tag == "select":
                    pick_ok = False
                    picked = value_n
                    for cand in cands:
                        try:
                            await loc.select_option(label=str(cand)[:80])
                            pick_ok = True
                            picked = cand
                            break
                        except Exception:
                            try:
                                await loc.select_option(value=str(cand)[:80])
                                pick_ok = True
                                picked = cand
                                break
                            except Exception:
                                continue
                    if not pick_ok:
                        options = await loc.evaluate(
                            """el => Array.from(el.options||[]).map(o => ({
                              value: o.value,
                              label: (o.label||o.text||'').trim()
                            }))"""
                        )
                        # FILL2-010: align with clear_closest_match min_score floor.
                        _SOFT_SELECT_MIN = 50
                        best_v, best_s, best_lab = None, 0, ""
                        for opt in options or []:
                            ol = (opt.get("label") or "").strip()
                            if not ol or is_placeholder_select_value(ol):
                                continue
                            for alias in cands:
                                s = _score_option(ol, alias)
                                if s > best_s:
                                    best_s, best_v, best_lab = s, opt.get("value"), ol
                        if best_v is not None and best_s >= _SOFT_SELECT_MIN:
                            await loc.select_option(value=str(best_v))
                            pick_ok = True
                            picked = best_lab
                    readback = await loc.evaluate(
                        "el => (el.options[el.selectedIndex]||{}).text || el.value || ''"
                    )
                    ok = pick_ok and select_readback_ok(
                        readback, cands, picked=picked, score_fn=_score_option
                    )
                    if ok:
                        detail.update(
                            {
                                "ok": True,
                                "verified": True,
                                "via": "native_select",
                                "picked": picked,
                                "readback": str(readback)[:120],
                                "option_clicked": True,
                            }
                        )
                        return detail
                is_combo = (
                    role == "combobox"
                    or "select__input" in cls
                    or "select__" in cls
                    or tag == "button"
                )
                if is_combo or tag in ("input", "button", "div"):
                    combo = await fill_typable_dropdown(
                        page,
                        control=loc,
                        value=value_n,
                        aliases=cands,
                        read_committed=lambda: read_combobox_display(loc),
                        timeout_ms=timeout_ms,
                        field_type=field_type,
                        label=label,
                        report=report,
                    )
                    if combo.get("ok") and combo.get("verified"):
                        detail.update(
                            {
                                "ok": True,
                                "verified": True,
                                "via": "typable_dropdown",
                                "picked": combo.get("picked"),
                                "readback": combo.get("readback"),
                                "option_clicked": combo.get("option_clicked"),
                                "skipped_already_correct": combo.get(
                                    "skipped_already_correct"
                                ),
                            }
                        )
                        return detail
                    detail["combo_error"] = combo.get("error")
        except Exception as e:
            detail["selector_error"] = str(e)[:120]

    detail["error"] = (
        detail.get("gh_select_error")
        or detail.get("combo_error")
        or detail.get("selector_error")
        or "verified_select_failed"
    )
    return detail


def normalize_select_answer(
    label: str,
    value: str,
    *,
    field_type: str = "",
) -> str:
    """Collapse LLM essays into short option-like tokens for selects.

    Extend failure: DeepSeek returned 'Yes, I am currently based in Illinois…'
    which was typed into the filter and never committed. Prefer 'Yes' / 'No'.
    """
    raw = (value or "").strip()
    if not raw:
        return raw
    label_l = (label or "").lower()
    ftype = (field_type or "").upper()

    # Based-in-states Yes/No (Illinois is in Extend's list → Yes for dummy)
    if re.search(r"based\s+in\s+any\s+of\s+these\s+states|currently\s+based\s+in\s+any", label_l):
        low = raw.lower()
        if re.search(r"\bno\b|not\s+based|none\s+of", low) and "illinois" not in low:
            return "No"
        return "Yes"

    # Ashby LATAM segmented Yes/No — dummy policy Yes (field_map LATIN_AMERICA)
    if ftype == "LATIN_AMERICA" or re.search(
        r"based\s+in\s+latin\s+america|currently\s+based\s+in\s+latin", label_l
    ):
        low = raw.lower()
        if re.match(r"^no\b", low) or low in ("false", "0", "n"):
            return "No"
        return "Yes"

    # Generic Yes/No policy questions
    if ftype in (
        "WORK_AUTH",
        "US_RESIDENCE",
        "SPONSORSHIP",
        "TALENT_HUB",
        "WORKED_HERE_BEFORE",
        "RELOCATION",
        "COMMUTE",
        "BACKGROUND_CHECK",
        "AGE_18",
        "MARKETING_CONSENT",
        "TERMS_CONSENT",
        "ACCOMMODATIONS",
        "LATIN_AMERICA",
        "US_RESIDENCE",
    ) or re.search(
        r"authorized\s+to\s+work|require\s+.*sponsorship|live\s+in\s+the\s+united|"
        r"willing\s+to\s+relocate|background\s+check|"
        r"reasonable\s+accommodations?\s+or\s+adjustments|"
        r"(require|need).{0,40}(accommodation|adjustment)",
        label_l,
    ):
        low = raw.lower()
        if ftype == "ACCOMMODATIONS" or re.search(
            r"(require|need).{0,40}(accommodation|adjustment)|"
            r"reasonable\s+accommodations?\s+or\s+adjustments",
            label_l,
        ):
            if re.search(r"\bno\b|do\s+not|don'?t|not\s+require|not\s+need", low):
                return "No"
            if re.search(r"\byes\b", low):
                return "Yes"
            return "No"
        if ftype == "SPONSORSHIP" or "sponsorship" in label_l:
            # Check No / will-not-require BEFORE need-sponsor (avoids matching
            # "will not need sponsorship" as Yes via "need sponsor").
            if re.search(
                r"\bno\b|will\s+not|do\s+not\s+require|don'?t\s+require|"
                r"not\s+need|no\s+sponsorship|citizen|permanent\s+resident",
                low,
            ):
                return "No"
            if re.search(r"\byes\b.*require|will\s+require|need\s+sponsor", low):
                return "Yes"
            # Prefer No for dummy when ambiguous long essay
            if len(raw) > 40:
                return "No"
        if re.match(r"^yes\b", low) or low in ("true", "1", "y"):
            return "Yes"
        if re.match(r"^no\b", low) or low in ("false", "0", "n"):
            return "No"
        # Long LLM prose on a Yes/No field → first token polarity
        if len(raw) > 48:
            if re.search(r"\byes\b", low) and not re.search(r"\bno\b", low):
                return "Yes"
            if re.search(r"\bno\b", low):
                return "No"

    # EEO: keep short decline-ish answers; truncate essays
    if ftype in ("GENDER", "RACE", "HISPANIC", "VETERAN", "DISABILITY", "AGE_RANGE"):
        if len(raw) > 80:
            return "Decline to self identify"
        return raw[:120]

    # Truncate absurdly long select answers (never type essays into filters)
    if len(raw) > 60 and ftype not in ("SCHOOL", "HOW_HEARD", "LOCATION", "SALARY_EXPECTED"):
        # Prefer first clause
        first = re.split(r"[.\n]", raw)[0].strip()
        return first[:60] if first else raw[:60]
    return raw



def self_test() -> None:
    assert is_placeholder_select_value("Select...")
    assert is_placeholder_select_value("Select one")
    assert is_placeholder_select_value("")
    assert not is_placeholder_select_value("Yes")
    assert not is_placeholder_select_value("Illinois")

    essay = "Yes, I am currently based in Illinois (Springfield, IL)."
    assert is_uncommitted_filter_text(essay, essay)
    assert is_uncommitted_filter_text(essay, essay[:40])
    assert not is_uncommitted_filter_text("Yes", "Yes", picked="Yes")

    assert not select_readback_ok("Select...", ["Yes", "No"])
    assert not select_readback_ok(essay, ["Yes", "No"], typed_frag=essay)
    assert select_readback_ok("Yes", ["Yes", "No"])
    assert select_readback_ok("Yes", ["Yes"], picked="Yes")

    assert normalize_select_answer(
        "Are you currently based in any of these states?\nCalifornia\nIllinois",
        essay,
    ) == "Yes"
    assert normalize_select_answer(
        "Will you require immigration sponsorship?",
        "No, I will not require sponsorship for employment.",
        field_type="SPONSORSHIP",
    ) == "No"
    assert normalize_select_answer(
        "Are you legally authorized to work?",
        "Yes I am authorized",
        field_type="WORK_AUTH",
    ) == "Yes"

    # Word-by-word split
    assert split_select_words("Springfield, Illinois, United States") == [
        "Springfield",
        "Illinois",
        "United",
        "States",
    ]
    assert split_select_words("Yes") == ["Yes"]
    assert split_select_words("no") == ["No"]
    assert split_select_words("Internet job board") == ["Internet", "job", "board"]

    # Salary: comma-grouped amounts stay intact for word-by-word typeahead
    assert split_select_words("$80,000 - $100,000") == ["80,000", "80,000 100,000"]
    sal_ranked = rank_option_matches(
        ["$80,000-$100,000", "$100,000-$120,000", "$65,000-$80,000"],
        ["$80,000 - $100,000"],
    )
    assert sal_ranked and sal_ranked[0][2] == "$80,000-$100,000"
    assert clear_closest_match(sal_ranked, at_last_word=True) is not None

    # School: institution head fuzzy match (city suffix ignored)
    sch_ranked = rank_option_matches(
        [
            "University of Alabama",
            "University of Alaska",
            "Alabama A&M University",
        ],
        ["University of Alabama, Tuscaloosa"],
    )
    assert sch_ranked[0][2] == "University of Alabama"
    assert clear_closest_match(sch_ranked, at_last_word=True) is not None

    ranked = rank_option_matches(
        ["Springfield, Illinois, United States", "Springfield, Ohio, United States"],
        ["Springfield, Illinois, United States", "Springfield"],
    )
    clear = clear_closest_match(ranked, at_last_word=False)
    assert clear is not None and "Illinois" in clear[1]

    # Ambiguous until last word
    ranked2 = [
        (70, 0, "Springfield, Illinois, United States"),
        (68, 1, "Springfield, Massachusetts, United States"),
    ]
    assert clear_closest_match(ranked2, at_last_word=False) is None
    assert clear_closest_match(ranked2, at_last_word=True) is not None

    loc = "Springfield, Illinois, United States"
    aliases = location_option_aliases("Springfield", state="IL", country="United States")
    assert location_display_matches(loc, aliases, city="Springfield", state="IL")
    assert not is_location_uncommitted_display(
        loc, city="Springfield", state="IL", aliases=aliases
    )
    assert is_uncommitted_filter_text(loc, loc, from_input=True)
    assert not is_uncommitted_filter_text("Yes", "Yes", picked="Yes")
    assert is_location_field("ADDRESS_CITY", "Location")
    assert not is_location_uncommitted_display(
        loc, city="Springfield", option_clicked=True, dependent_revealed=True
    )
    assert location_search_query("Springfield, IL, USA") == "Springfield"
    assert any("Springfield" in a for a in aliases)

    # Select vs essay — policy Yes/No is select; cover letter is not
    assert is_select_field("SPONSORSHIP", "Will you require sponsorship?")
    assert is_select_field("LOCATION", "Are you currently based in any of these states?")
    assert not is_select_field(
        "COVER_LETTER", "Why do you want to join us?", {"essay": True}
    )
    assert is_select_field(
        "",
        "School",
        {"html_type": "combobox", "reason": "gh_select_failed"},
    )

    # ONEOK: never accept Idaho when intending Illinois; never accept picked==wrong alone
    from gh_select import _score_option

    assert soft_value_match("IL", "Idaho") is False
    assert soft_value_match("Male", "Female") is False  # ATS3-005
    assert select_readback_ok(
        "Idaho", ["Illinois", "IL"], picked="Idaho", score_fn=_score_option
    ) is False
    assert select_readback_ok(
        "Illinois", ["Illinois", "IL"], picked="Illinois", score_fn=_score_option
    ) is True
    assert _score_option("Idaho", "IL") == 0
    assert _score_option("Illinois", "IL") >= 70

    print("verified_select.self_test: OK")


if __name__ == "__main__":
    self_test()
