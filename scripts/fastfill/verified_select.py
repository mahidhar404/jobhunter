"""Verified combobox / select commit — shared by GH, Workday, Ashby, Flash.

Universal typable-dropdown algorithm (default for ALL typable selects):
  1. Open / focus the dropdown
  2. Split intended value into words
     (Springfield, Illinois, United States → Springfield / Illinois / United / States;
      Yes → Yes)
  3. Type incrementally word-by-word (accumulate: w1, w1+w2, …)
  4. After each chunk, wait for listbox options and score closest matches
  5. If multiple options still match → type the next word to narrow
  6. When a clear closest match exists → **click that option** (never leave filter text)
  7. Verify committed selection (single-value / aria / chip) — not uncommitted input
  8. Yes/No dropdowns use the same path (type Yes → wait → click Yes)
  9. Skip only when already **committed** correct

Never Enter-to-submit. Dummy-only. Never submit.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, Iterable

_log = logging.getLogger("verified_select")

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
    if ftype in ("ADDRESS_CITY", "LOCATION"):
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


def how_heard_source_committed(
    shown: str | None,
    candidates: Iterable[str] | None = None,
) -> bool:
    """True when Workday How-Heard / source multi-select has a committed chip/token.

    Requires ``N items selected`` chrome (≥1). Category headers and bare filter
    text (``Indeed`` typed into the search box, ``Internet job board``) are
    never enough — Walmart hierarchical menus leave that text without a chip.
    """
    s = (shown or "").strip()
    if not s or is_multiselect_uncommitted(s):
        return False
    if not multiselect_has_chip(s):
        # No chip chrome → typed filter / category header / single-value lookalike
        return False
    # Chip present. Optional: prefer that it mentions an intended leaf when given.
    cands = [str(c).strip() for c in (candidates or []) if str(c or "").strip()]
    if not cands:
        return True
    leaf_cands = [c for c in cands if not is_how_heard_category_option(c)] or cands
    sl = s.lower()
    for c in leaf_cands:
        if soft_value_match(c, s) or c.lower() in sl:
            return True
    # Chip exists for some other concrete source — still committed (stop thrash)
    return True


async def settle_open_listbox(page) -> None:
    """Close open prompt/listbox menus after a successful commit (never Submit)."""
    try:
        from captcha_pause import press_escape_unless_captcha

        await press_escape_unless_captcha(page)
    except Exception:
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass


def _default_score_option(opt: str, alias: str) -> int:
    if states_are_confusable(alias, opt):
        return 0
    go, ga = _gender_polarity_side(opt), _gender_polarity_side(alias)
    if go and ga and go != ga:
        return 0
    try:
        from gh_select import _score_option

        s = int(_score_option(opt, alias) or 0)
        if s > 0 and states_are_confusable(alias, opt):
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


# Option-scoring semantic fallback: OFF by default. Only fires when the lexical/
# exact/soft scorers found nothing (score 0), never overriding polarity or
# state-confusable guards (those already returned 0 above). Capped BELOW soft(80)
# and exact(100) so a fuzzy paraphrase can never outrank a real lexical match.
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
            r for r in ranked if not reject_confusable_state_option(intent, r[2])
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
    # 2) Workday prompt / multiselect list icon beside the input
    icon_sels = [
        '[data-automation-id="promptIcon"]',
        '[data-automation-id="multiSelectContainer"] [data-automation-id="promptIcon"]',
        'div[data-automation-id="multiSelectContainer"] button',
        '[data-automation-id="formField-source"] button',
        '[data-automation-id="formField-how_heard"] button',
        'button[aria-label*="Select" i]',
        'button[title*="Select" i]',
    ]
    for sel in icon_sels:
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
    # 3) Last resort: Enter on the focused filter (loads suggestions on some WD prompts)
    if allow_enter:
        try:
            if filter_input is not None:
                await filter_input.focus()
            await page.keyboard.press("Enter")
            detail["nudges"].append("Enter_filter")
            await page.wait_for_timeout(400)
        except Exception as e:
            detail["enter_error"] = str(e)[:80]
    return detail


# ChamPro-style Workday fiber searchSelect (ported idea — not their plugin).
# Typing alone often yields "No Items"; fiber onKeyDown Tab triggers async search.
_FIBER_SEARCH_SELECT_JS = """
async (el, args) => {
  const value = String((args && args.value) || '');
  const aliases = Array.isArray(args && args.aliases) ? args.aliases.map(String) : [];
  const waitMs = Math.max(400, Math.min(2200, Number((args && args.wait_ms) || 1200)));
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
  const getOpts = () => [...document.querySelectorAll(
    '[data-automation-id="promptOption"],[role="option"],'
    + '[class*="dropdown-results"],[class*="suggestion"],'
    + '[class*="select__option"]'
  )].filter(vis).filter((x) => {
    const t = txt(x);
    return t && t.length > 0 && t.length < 120;
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
) -> dict[str, Any]:
    """Workday/async typeahead via React fiber onKeyDown Tab (ChamPro searchSelect).

    Prefer this over typing-only for HOW_HEARD / SCHOOL / SOURCE prompts.
    Falls back to caller (nudge_listbox / Playwright click) when status != picked.
    Never blurs the filter (blur can clear unpicked autocomplete).
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
        try:
            await filter_input.click(timeout=2000, force=True)
        except Exception:
            try:
                await filter_input.focus()
            except Exception:
                pass
        raw = await filter_input.evaluate(
            _FIBER_SEARCH_SELECT_JS,
            {"value": primary, "aliases": cands[1:], "wait_ms": wait_ms},
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
                idx = raw.get("optionIndex")
                try:
                    clicked = await filter_input.evaluate(
                        """({idx, text}) => {
                          const vis = (el) => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                          };
                          const txt = (el) => (el.innerText || el.textContent || '').trim();
                          const opts = [...document.querySelectorAll(
                            '[data-automation-id="promptOption"],[role="option"],'
                            + '[class*="dropdown-results"],[class*="suggestion"],'
                            + '[class*="select__option"]'
                          )].filter(vis).filter((x) => {
                            const t = txt(x);
                            return t && t.length > 0 && t.length < 120;
                          });
                          let el = (typeof idx === 'number' && opts[idx]) ? opts[idx] : null;
                          if (!el) el = opts.find((o) => txt(o) === text) || null;
                          if (!el) return false;
                          el.click();
                          return true;
                        }""",
                        {"idx": idx, "text": picked_f},
                    )
                except Exception as e:
                    detail["status"] = "click_failed"
                    detail["error"] = str(e)[:80]
                    detail["option_clicked"] = False
                    detail["ok"] = False
                    return detail
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


_HOW_HEARD_OPTION_SELS = [
    '[data-automation-id="promptOption"]',
    '[role="option"]',
    '[data-automation-id*="promptOption" i]',
]

_HOW_HEARD_INPUT_SELS = (
    'input[name="source--source"], '
    '[data-automation-id="source--source"], '
    '[data-automation-id="formField-source"] input, '
    '[data-automation-id="formField-how_heard"] input, '
    '[data-automation-id="multiSelectContainer"] input'
)

_HOW_HEARD_WRAP_SELS = (
    '[data-automation-id="formField-source"], '
    '[data-automation-id="formField-how_heard"], '
    '[data-automation-id="formField-howDidYouHear"], '
    '[data-automation-id="multiSelectContainer"]'
)


async def _read_how_heard_wrap_text(page) -> str:
    """Chip chrome for how-heard / source (prefer formField, not filter alone)."""
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
            snip = ((await loc.inner_text()) or "").strip()
            if snip:
                return snip[:240]
        except Exception:
            continue
    return ""


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
            key = text.lower()
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


async def _click_option_by_text(page, text: str, *, timeout_ms: int = 4000) -> bool:
    """Click the first visible option whose text soft-matches *text*."""
    want = (text or "").strip()
    if not want:
        return False
    opts = await _list_how_heard_options(page)
    best = None
    best_score = 0
    for o in opts:
        t = str(o.get("text") or "")
        tl, wl = t.lower(), want.lower()
        if tl == wl:
            sc = 100
        elif soft_value_match(want, t):
            sc = 90
        elif wl in tl or tl in wl:
            sc = 70
        else:
            sc = 0
        if sc > best_score:
            best_score = sc
            best = o
    if not best or best_score < 70:
        return False
    sel = str(best.get("selector") or _HOW_HEARD_OPTION_SELS[0])
    idx = int(best.get("index") or 0)
    try:
        await page.locator(sel).nth(idx).click(timeout=timeout_ms)
        return True
    except Exception:
        try:
            loc = page.get_by_role("option", name=want, exact=False).first
            await loc.click(timeout=timeout_ms)
            return True
        except Exception:
            return False


async def fill_hierarchical_how_heard(
    page,
    filter_input: Any,
    *,
    leaf_candidates: list[str] | None = None,
    category_candidates: list[str] | None = None,
    wait_ms: int = 450,
) -> dict[str, Any]:
    """Walmart-style Workday how-heard: type → open subsection → pick leaf → chip.

    Flow:
      1. Type a concrete leaf (Indeed / LinkedIn / …)
      2. If a leaf option is visible, click it
      3. Else if category/subsection headers appear, open the right one, then
         click the leaf inside the subsection
      4. Verify via formField chip chrome (``N items selected``); never treat
         category filter text as committed
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
        ][:3]
        cats = list(category_candidates or how_heard_category_candidates())[:3]
    except Exception:
        leaves = list(leaf_candidates or ["Indeed", "LinkedIn", "Company Website"])[:3]
        cats = list(category_candidates or ["Internet job board", "Job Board"])[:3]

    detail["leaves"] = leaves
    detail["categories"] = cats

    async def _ensure_open() -> None:
        try:
            await filter_input.click(timeout=2000, force=True)
        except Exception:
            try:
                await filter_input.focus()
            except Exception:
                pass

    async def _type_query(q: str) -> None:
        await _ensure_open()
        try:
            await filter_input.fill("")
        except Exception:
            pass
        try:
            await filter_input.fill(str(q)[:80])
        except Exception:
            try:
                await page.keyboard.type(str(q)[:80], delay=15)
            except Exception:
                pass
        # Prefer a short settle over heavy nudge (nudge can hang on fixture DOMs)
        try:
            await page.wait_for_timeout(wait_ms)
        except Exception:
            pass
        opts_now = await _list_how_heard_options(page)
        if not opts_now:
            try:
                await nudge_listbox_after_type(page, filter_input, allow_enter=True)
                await page.wait_for_timeout(wait_ms)
            except Exception:
                pass

    async def _chip_ok(leaf: str) -> tuple[bool, str]:
        snip = await _read_how_heard_wrap_text(page)
        ok = how_heard_source_committed(snip, [leaf, *leaves])
        if is_multiselect_uncommitted(snip):
            ok = False
        return ok, snip

    snip0 = await _read_how_heard_wrap_text(page)
    if how_heard_source_committed(snip0, leaves):
        detail.update(
            {
                "status": "already_committed",
                "ok": True,
                "verified": True,
                "committed": True,
                "readback": snip0[:120],
                "picked": snip0[:120],
                "skipped_already_correct": True,
            }
        )
        return detail

    async def _try_pick_leaf(leaf: str, *, path: str, subsection: str = "") -> bool:
        opts = await _list_how_heard_options(page)
        for o in opts:
            if o.get("is_category"):
                continue
            t = str(o.get("text") or "")
            if soft_value_match(leaf, t) or t.lower() == leaf.lower():
                clicked = await _click_option_by_text(page, t)
                detail["option_clicked"] = clicked
                detail["picked"] = t
                detail["path"] = path
                if subsection:
                    detail["subsection"] = subsection
                try:
                    await page.wait_for_timeout(350)
                except Exception:
                    pass
                ok, snip = await _chip_ok(leaf)
                if ok:
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
                            "value": leaf,
                        }
                    )
                    return True
        return False

    for leaf in leaves:
        detail["attempted_leaf"] = leaf
        await _type_query(leaf)
        opts = await _list_how_heard_options(page)
        detail["options_after_leaf_type"] = [o.get("text") for o in opts[:12]]

        if await _try_pick_leaf(leaf, path="leaf_direct"):
            return detail

        # Open a visible category / known category, then pick leaf
        cat_opts = [o for o in opts if o.get("is_category")]
        nav_cats: list[str] = []
        for o in cat_opts:
            t = str(o.get("text") or "")
            if t and t not in nav_cats:
                nav_cats.append(t)
        for c in cats:
            if c not in nav_cats:
                nav_cats.append(c)
        nav_cats = nav_cats[:3]

        try:
            from fill_step_log import note_step

            note_step(
                None,
                action="how_heard_hierarchy_open",
                label="how_heard",
                field_type="HOW_HEARD",
                after=leaf,
                via="hierarchical_how_heard",
                reason=f"opts={len(opts)} cats={len(cat_opts)}",
            )
        except Exception:
            pass

        for cat in nav_cats:
            visible = [str(o.get("text") or "") for o in cat_opts]
            if visible and not any(soft_value_match(cat, v) for v in visible):
                # Category not in current list — type it to surface
                await _type_query(cat)
                opts = await _list_how_heard_options(page)
                cat_opts = [o for o in opts if o.get("is_category")]
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
            if await _try_pick_leaf(leaf, path="category_then_leaf", subsection=cat):
                return detail
            # Filter inside subsection
            try:
                await filter_input.fill("")
                await filter_input.fill(leaf[:80])
                await page.wait_for_timeout(wait_ms)
            except Exception:
                pass
            if await _try_pick_leaf(
                leaf, path="category_then_leaf_filtered", subsection=cat
            ):
                return detail

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
) -> tuple[Any, list[str]]:
    """Poll until listbox options appear. Returns (locator, texts).

    When ``root`` is a locator (e.g. a react-select ``.select__container``),
    options are located *within* it so overlapping option text across sibling
    selects can't cross-click (GH mounts every select menu at once — the
    Hispanic "Decline To Self Identify" would otherwise win a RACE Decline
    click). Falls back to a page-wide scan if the scoped root finds nothing
    (menus portalled to <body> on some tenants).
    """
    sels = selectors or [
        ".select__option",
        "[id*='react-select'][id*='option']",
        "[role='listbox'] [role='option']",
        "[role='option']",
        '[data-automation-id="promptOption"]',
        '[data-automation-id*="promptOption" i]',
    ]
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
                try:
                    t = (await loc.nth(i).inner_text()).strip()
                except Exception:
                    t = ""
                # ATS2-001: never drop rows — empty/placeholder become "" so
                # texts[i] always matches loc.nth(i). Stripping Select… used to
                # remap Illinois→Idaho (texts=[Idaho,Illinois] → nth(1)=Idaho).
                if t and not is_placeholder_select_value(t):
                    texts.append(t)
                else:
                    texts.append("")
            if any(texts):
                return loc, texts
        # Mid-poll nudge once when Workday/async prompts stay empty
        if nudge and not nudged and loop_i == max(1, loops // 3):
            nudged = True
            try:
                await nudge_listbox_after_type(
                    page, filter_input, allow_enter=allow_enter_nudge
                )
            except Exception:
                pass
        try:
            await page.wait_for_timeout(poll_ms)
        except Exception:
            break
    if root is not None:
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
            allow_enter_nudge=allow_enter_nudge,
            root=None,
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
    opts, texts = await wait_for_option_texts(page, timeout_ms=timeout_ms)
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
    clear = clear_closest_match(
        ranked, at_last_word=True, min_score=40, intent=primary
    )
    if not clear:
        detail["error"] = "no_matching_option"
        return detail
    best_i, picked, best_s = clear
    try:
        await opts.nth(best_i).click(timeout=timeout_ms)
        detail["option_clicked"] = True
        detail["picked"] = picked
        detail["score"] = best_s
    except Exception as e:
        detail["error"] = f"option_click_failed:{e}"[:120]
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
    """Word-by-word type → wait → narrow → click closest option.

    Never uses Enter as form Submit. For async Workday How-Heard prompts,
    may nudge with ArrowDown / prompt icon / filter-Enter so options load.
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
    # Workday "How Did You Hear" / School / State prompts need async option load.
    needs_async_nudge = ftype_u in (
        "HOW_HEARD",
        "SOURCE",
        "SCHOOL",
        "ADDRESS_STATE",
    ) or (
        "how did you hear" in lab_l
        or "school" in lab_l
        or "countryregion" in lab_l
        or lab_l.endswith("state")
    )
    prefer_fiber = needs_async_nudge and ftype_u in (
        "HOW_HEARD",
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
        "mode": "typable_dropdown_word_by_word",
        "option_clicked": False,
        "words": words[:12],
        "steps": [],
        "typed_frag": "",
        "aliases_tried": cands[:12],
    }
    if not cands:
        detail["error"] = "no_aliases"
        return detail

    # Root fix for Workday async prompts: fiber searchSelect before type/nudge.
    if prefer_fiber and filter_input is not None:
        try:
            fiber = await fiber_search_select(
                page,
                filter_input,
                primary,
                aliases=cands,
                wait_ms=min(2200, max(1200, timeout_ms // 2)),
            )
            detail["fiber_search_select"] = {
                k: fiber.get(k)
                for k in ("status", "picked", "score", "options", "error", "algorithm")
            }
            picked_f = str(fiber.get("picked") or "")
            if (
                fiber.get("option_clicked")
                and picked_f
                and not reject_confusable_state_option(primary, picked_f)
            ):
                detail.update(
                    {
                        "option_clicked": True,
                        "picked": picked_f,
                        "score": fiber.get("score"),
                        "algorithm": "fiber_search_select",
                        "options": (fiber.get("options") or [])[:12],
                    }
                )
                return detail
        except Exception as e:
            detail["fiber_error"] = str(e)[:80]

    opts = (root or page).locator(
        (option_selectors or [".select__option", "[role='option']"])[0]
    )
    texts: list[str] = []
    ranked: list[tuple[int, int, str]] = []

    async def _wait_rank(*, allow_enter: bool = False):
        o, t = await wait_for_option_texts(
            page,
            selectors=option_selectors,
            timeout_ms=min(timeout_ms, 3200 if needs_async_nudge else 2200),
            filter_input=filter_input,
            nudge=needs_async_nudge,
            allow_enter_nudge=allow_enter and needs_async_nudge,
            root=root,
        )
        # ATS-001/015: drop confusable states; keep original indices for click.
        # Never fall back to unfiltered list (``] or t``) — that remaps Illinois→Idaho.
        filtered, orig_idx = filter_options_preserving_indices(t, primary)
        ranked = remap_ranked_to_original(
            rank_option_matches(filtered, cands, score_fn), orig_idx
        )
        return o, filtered, ranked

    if not use_type or not words:
        opts, texts, ranked = await _wait_rank()
        clear = clear_closest_match(ranked, at_last_word=True, min_score=40, intent=primary)
        detail["options"] = texts[:12]
        if not clear:
            detail["error"] = "no_matching_option"
            return detail
        best_i, picked, best_s = clear
        try:
            await opts.nth(best_i).click(timeout=timeout_ms)
            detail.update(
                {"option_clicked": True, "picked": picked, "score": best_s}
            )
        except Exception as e:
            detail["error"] = f"option_click_failed:{e}"[:120]
        return detail

    try:
        await filter_input.fill("")
    except Exception:
        pass

    # ATS3-013: try full intended string once before word-by-word clears/retypes.
    if use_type and len(words) > 1:
        full = " ".join(words)
        ok_full = await _type_into_filter(filter_input, full, timeout_ms=timeout_ms)
        if ok_full:
            detail["typed_frag"] = full
            try:
                await page.wait_for_timeout(550 if needs_async_nudge else 320)
            except Exception:
                pass
            opts, texts, ranked = await _wait_rank(allow_enter=False)
            if not texts and needs_async_nudge:
                try:
                    nudge = await nudge_listbox_after_type(
                        page, filter_input, allow_enter=True
                    )
                    detail.setdefault("nudges", []).append(nudge)
                    await page.wait_for_timeout(400)
                except Exception:
                    pass
                opts, texts, ranked = await _wait_rank(allow_enter=True)
            clear = clear_closest_match(
                ranked, at_last_word=True, intent=primary
            ) or _early_unique_high_match(ranked, intent=primary, min_score=80)
            detail["steps"].append(
                {
                    "typed": full[:80],
                    "n_options": len(texts),
                    "best": (clear[2], clear[1][:60]) if clear else None,
                    "top": [(s, t[:40]) for s, _, t in ranked[:3]],
                    "full_first": True,
                }
            )
            detail["options"] = texts[:12]
            if clear:
                best_i, picked, best_s = clear
                try:
                    await opts.nth(best_i).click(timeout=timeout_ms)
                    detail.update(
                        {
                            "option_clicked": True,
                            "picked": picked,
                            "score": best_s,
                            "narrowed_at_word": len(words),
                            "full_string_first": True,
                        }
                    )
                    return detail
                except Exception as e:
                    detail["error"] = f"option_click_failed:{e}"[:120]
            # Reset filter before word-by-word fallback
            try:
                await filter_input.fill("")
            except Exception:
                pass

    # ATS3-013: for long multi-word intents, only try head / head+2 / full
    # (skip middle retypes that rarely change the list).
    word_indices = list(range(len(words)))
    if len(words) > 3:
        word_indices = sorted({0, min(1, len(words) - 1), len(words) - 1})

    for step_n, i in enumerate(word_indices):
        typed = " ".join(words[: i + 1])
        if step_n == 0:
            ok_type = await _type_into_filter(filter_input, typed, timeout_ms=timeout_ms)
        else:
            # Append only the new trailing words since last typed fragment
            prev = detail.get("typed_frag") or ""
            if typed.startswith(prev) and prev:
                suffix = typed[len(prev) :]
                ok_type = await _append_into_filter(
                    filter_input, suffix, timeout_ms=timeout_ms
                )
                if not ok_type:
                    ok_type = await _type_into_filter(
                        filter_input, typed, timeout_ms=timeout_ms
                    )
            else:
                ok_type = await _type_into_filter(
                    filter_input, typed, timeout_ms=timeout_ms
                )
        if not ok_type:
            try:
                await page.keyboard.type(typed[:80], delay=25)
            except Exception:
                detail["error"] = "type_failed"
                return detail
        detail["typed_frag"] = typed
        try:
            # Workday prompt search is async — give it more than a paint frame.
            await page.wait_for_timeout(650 if needs_async_nudge else 380)
        except Exception:
            pass
        # First pass: ArrowDown/icon only. If still empty on last word, allow
        # filter-Enter (observed needed for Quantiphi How-Heard suggestions).
        opts, texts, ranked = await _wait_rank(allow_enter=False)
        if not texts and needs_async_nudge:
            try:
                nudge = await nudge_listbox_after_type(
                    page,
                    filter_input,
                    allow_enter=(i == len(words) - 1),
                )
                detail.setdefault("nudges", []).append(nudge)
                await page.wait_for_timeout(500)
            except Exception:
                pass
            opts, texts, ranked = await _wait_rank(
                allow_enter=(i == len(words) - 1)
            )
        at_last = i == len(words) - 1
        clear = clear_closest_match(ranked, at_last_word=at_last, intent=primary)
        if not clear and not at_last:
            clear = _early_unique_high_match(ranked, intent=primary, min_score=80)
        detail["steps"].append(
            {
                "typed": typed[:80],
                "n_options": len(texts),
                "best": (clear[2], clear[1][:60]) if clear else None,
                "top": [(s, t[:40]) for s, _, t in ranked[:3]],
            }
        )
        try:
            from fill_step_log import note_step

            note_step(
                report,
                action="select_word_by_word",
                label=str(label or "")[:80],
                field_type=str(field_type or "")[:48],
                before=detail.get("typed_frag") or "",
                after=typed[:80],
                via="verified_select",
                layer="typable_dropdown",
                reason="narrow_options",
                extra={
                    "word_i": i + 1,
                    "n_options": len(texts),
                    "picked": clear[1][:60] if clear else None,
                },
            )
        except Exception:
            pass
        detail["options"] = texts[:12]
        if clear:
            best_i, picked, best_s = clear
            try:
                await opts.nth(best_i).click(timeout=timeout_ms)
                detail.update(
                    {
                        "option_clicked": True,
                        "picked": picked,
                        "score": best_s,
                        "narrowed_at_word": i + 1,
                    }
                )
                return detail
            except Exception as e:
                detail["error"] = f"option_click_failed:{e}"[:120]
                continue

    if ranked:
        clear = clear_closest_match(ranked, at_last_word=True, min_score=50, intent=primary)
        if clear:
            best_i, picked, best_s = clear
            try:
                await opts.nth(best_i).click(timeout=timeout_ms)
                detail.update(
                    {
                        "option_clicked": True,
                        "picked": picked,
                        "score": best_s,
                        "narrowed_at_word": len(words),
                        "forced_last": True,
                    }
                )
                return detail
            except Exception as e:
                detail["error"] = f"option_click_failed:{e}"[:120]
                return detail
    detail["error"] = detail.get("error") or "no_clear_closest_match"
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
    """Universal typable dropdown: open → word-by-word → click → verify commit."""
    score_fn = score_fn or _default_score_option
    value = normalize_select_answer(label, str(value or ""), field_type=field_type)
    cands = list(aliases or [])
    if value and value not in cands:
        cands = [value, *cands]
    detail: dict[str, Any] = {
        "mode": "typable_dropdown",
        "algorithm": "word_by_word",
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
    if shown0 and not is_placeholder_select_value(shown0):
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
        try:
            await control.click(timeout=timeout_ms, force=True)
        except Exception:
            try:
                await control.click(timeout=timeout_ms)
            except Exception as e:
                out["error"] = f"open_failed:{e}"[:100]
                return out
        try:
            await page.wait_for_timeout(180)
        except Exception:
            pass
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
                    "or @data-automation-id='multiSelectContainer' "
                    "or contains(@data-automation-id,'multiSelect')][1]"
                ).first
                if await wrap.count():
                    chip = ((await wrap.inner_text()) or "").strip()
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
    }
)

_SELECT_LABEL_RE = re.compile(
    r"based\s+in\s+any\s+of\s+these\s+states|require\s+.*sponsorship|"
    r"authorized\s+to\s+work|employment\s+eligibility|gender|race|ethnicity|"
    r"veteran|disabilit|\bschool\b|\bdegree\b|\bdiscipline\b|\bmajor\b|"
    r"field\s+of\s+study|salary|how\s+did\s+you\s+hear|"
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
        "LATIN_AMERICA",
        "US_RESIDENCE",
    ) or re.search(
        r"authorized\s+to\s+work|require\s+.*sponsorship|live\s+in\s+the\s+united|"
        r"willing\s+to\s+relocate|background\s+check",
        label_l,
    ):
        low = raw.lower()
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
