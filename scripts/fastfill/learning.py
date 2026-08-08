"""Persistent learning loop — policy facts only (never PII).

``learned_fields.json`` is a GLOBAL allow-list of cross-employer *policy*
answers (notice period, sponsorship, EEO Decline, how-did-you-hear, phone
*device type*, relocation willingness, work authorization). Static layers
(``field_map`` / ``DUMMY_PROFILE``) own contact and profile PII.

Hard contract
-------------
ALLOWED to learn: short reusable policy / voluntary-disclosure answers that
transfer across employers without identifying a person.

NEVER learn (blocked at write + ignored at read + dropped by ``--sanitize``):
  - emails, phone *numbers*, SSNs, passwords / secrets
  - names, mailing addresses, city/state/zip, LinkedIn/portfolio URLs
  - salary, essays, job-specific screening, employer/title history
  - education degree dropdowns, contaminated aria labels

After any Flash/hybrid run that may have written learnings::

    skyvern_runtime/venv/bin/python scripts/fastfill/learning.py --sanitize

``lookup_learned`` / ``learned_cheat_sheet_rows`` also filter at read time so
an unsanitized on-disk store cannot poison fills.

Loop
----
  1. Before a run, fold sanitized learnings into the cheat sheet (Layer
     learned-allow-list) — zero-cost lookup like a Layer-1 hit.
  2. After a run, mine Layer-2 actions for new (label, value) pairs the
     static layers did not cover; ``record_learning`` only persists if
     ``is_reusable_learning`` passes.

Scope is deliberately GLOBAL, not per-platform: "What is your notice period?"
means the same thing on any ATS. Keying by platform would re-learn the same
policy question forever.
"""

import json
import re
from pathlib import Path

LEARNED_STORE = Path(__file__).resolve().parent / "learned_fields.json"

# A label this short or generic ("Yes", "No", "Name") is too ambiguous to key a
# global lookup on safely - it would confidently misapply an unrelated answer
# to the next form that happens to reuse the same short word as a DIFFERENT
# question's label.
MIN_LABEL_LEN = 8
MAX_LEARNED_VALUE_LEN = 200

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_RE = re.compile(
    r"(?<!\d)"  # not preceded by digit
    r"(?:\+?1[\s.\-]?)?"
    r"(?:\(\d{3}\)|\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}"
    r"(?!\d)",
)

# Free-text / essay prompts — answers are run-specific narrative, not reusable facts.
_ESSAY_LABEL_PATTERNS = (
    r"\bdescribe\b",
    r"\bexplain\b",
    r"pros[\s_-]*and[\s_-]*cons",
    r"tell[\s_-]*us[\s_-]*about",
    r"\bessay\b",
)

# Short policy questions whose answers transfer across employers (notice period, EEO, etc.).
# Intentionally excludes address/education/contact — those stay in DUMMY_PROFILE.
_REUSABLE_POLICY_LABEL_PATTERNS = (
    r"notice[\s_-]*period",
    r"earliest[\s_-]*start",
    r"when[\s_-]*is[\s_-]*the[\s_-]*earliest",
    r"when[\s_-]*can[\s_-]*you[\s_-]*start",
    # ATS2-007: how-heard / gender chips are tenant-specific — do NOT learn
    # globally ("Internet job board", "Male"). Shared policy supplies those.
    r"sponsorship",
    r"require[\s_-]*sponsor",
    r"work[\s_-]*authorization",
    r"legally[\s_-]*authorized",
    r"\brelocation\b",
    r"\brelocate\b",
    r"race[\s_-]*select",
    r"ethnicity[\s_-]*which",
    r"veteran[\s_-]*status",
    # Device type (Mobile) only — never the phone number itself.
    r"phone[\s_-]*device[\s_-]*type",
)

# Job-specific screening, PII fields (handled by static layers), or contaminated labels.
_JOB_SPECIFIC_LABEL_PATTERNS = (
    r"\bwhy[\s_-]+\w",
    r"\bwhy[\s_-]*do[\s_-]*you[\s_-]*want",
    r"salary",
    r"compensation",
    r"job[\s_-]*title",
    r"\bemployer\b",
    r"most[\s_-]*recent",
    r"legal[\s_-]*birth",
    r"\b(?:first|last|full|given|family)[\s_-]*name\b",
    r"\bname\b",
    r"mailing[\s_-]*address",
    r"full[\s_-]*mailing",
    r"\baddress\b",
    r"current[\s_-]*city",
    r"\bcity\b",
    r"city[\s_-]*and[\s_-]*country[\s_-]*of[\s_-]*residence",
    r"\bstate\b",
    r"\bdegree\b",
    r"\bzip(?:[\s_-]*code)?\b",
    r"postal[\s_-]*code",
    r"relocating[\s_-]*to[\s_-]*[a-z]",
    r"provide[\s_-]*an[\s_-]*example",
    r"could[\s_-]*you[\s_-]*provide",
    r"open[\s_-]*source[\s_-]*data",
    r"data[\s_-]*volume",
    r"data[\s_-]*storage",
    r"data[\s_-]*platform",
    r"data[\s_-]*stack",
    r"linkedin",
    r"portfolio",
    # Contact PII — block email/phone *number* labels. "phone device type" is
    # excluded via negative lookahead so the policy allow-list can still keep it.
    r"phone[\s_-]*number",
    r"\bphone\b(?![\s_-]*device)",
    r"\bmobile[\s_-]*(?:number|phone)\b",
    r"email[\s_-]*address",
    r"\bemail\b",
    r"\be-?mail\b",
    r"country[\s_-]*calling[\s_-]*code",
    r"^https://",
    r"select[\s_-]*country[\s_-]*calling[\s_-]*code\s*:",
    r"state[\s_-]*new[\s_-]*jersey",
    r"^yes[\s_-]*required$",
)


def normalize_label(text: str) -> str:
    """Canonical key for a field signal, so trivial punctuation/wording
    differences ("Notice Period*" vs "Notice period (required)") still hit the
    same learned entry instead of silently missing it."""
    t = (text or "").lower().strip()
    t = re.sub(r"[*✱:]+$", "", t).strip()
    t = re.sub(r"\((required|optional)\)", "", t).strip()
    t = re.sub(r"\s+", " ", t)
    return t


def _looks_like_secret(value: str) -> bool:
    """True for anything that must NEVER be learned/reused globally.

    Covers the specific generated password this project uses (imported
    lazily to avoid a circular import - learning.py is imported BY
    field_map.py's callers, not the reverse) plus the general shape of a
    generated credential (mixed case + digit + symbol, no spaces, no `@`) so a
    future password change is still caught by shape, not just by exact match.
    """
    if not value:
        return True
    try:
        from field_map import DUMMY_PROFILE
        if value == DUMMY_PROFILE.get("account", {}).get("password"):
            return True
    except Exception:
        pass
    v = value.strip()
    if " " in v or "@" in v or len(v) < 8:
        return False
    has_upper = any(c.isupper() for c in v)
    has_lower = any(c.islower() for c in v)
    has_digit = any(c.isdigit() for c in v)
    has_symbol = any(not c.isalnum() for c in v)
    return has_upper and has_lower and has_digit and has_symbol


_GENERIC_PLACEHOLDER_PHRASES = (
    "start typing", "type here", "type to search", "search...", "select...",
    "choose...", "enter value", "click to select", "please select",
)


def _looks_like_generic_placeholder(text: str) -> bool:
    """True for a `placeholder` attribute that is a FORMAT HINT or generic UI
    prompt, not the field's actual question - found live: a phone field with
    placeholder "1-415-555-1234..." learned as if that literal string were a
    real label, and worse, an autocomplete widget with placeholder "Start
    typing..." learned globally with whatever value happened to be typed into
    IT specifically (a city). "Start typing..."/"Type here..." are near-
    universal placeholders reused across completely unrelated field types
    (city autocomplete, company search, skill tags) - reusing one instance's
    answer for every future field sharing that generic hint text is exactly
    the cross-contamination MIN_LABEL_LEN was meant to prevent, except these
    happen to be long enough in characters while still carrying zero semantic
    meaning about what the field actually asks.
    """
    t = text.strip().lower()
    if not t:
        return True
    if any(phrase in t for phrase in _GENERIC_PLACEHOLDER_PHRASES):
        return True
    # An example VALUE (a phone number, an email address) rather than a
    # question - typically trailing "..." with mostly digits/symbols, or an
    # "@"-containing example address.
    core = t.rstrip(".").strip()
    digit_ratio = sum(c.isdigit() for c in core) / max(len(core), 1)
    if digit_ratio > 0.3 and t.endswith("..."):
        return True
    if "@" in t and t.endswith("..."):
        return True
    return False


def load_learned() -> dict:
    if not LEARNED_STORE.exists():
        return {}
    try:
        return json.loads(LEARNED_STORE.read_text())
    except Exception:
        return {}


def _save_learned(store: dict) -> None:
    LEARNED_STORE.write_text(json.dumps(store, indent=1, sort_keys=True))


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _looks_like_pii_value(value: str) -> bool:
    """Block emails/phones/SSNs (and bare contact-shaped strings) from learning.

    Contact facts belong in DUMMY_PROFILE / field_map static layers, not in a
    label→value memory that can contaminate the next tenant's form. Also rejects
    values that are *only* an email/phone even when the label looked policy-like.
    """
    v = (value or "").strip()
    if not v:
        return True
    if _EMAIL_RE.search(v) or _PHONE_RE.search(v) or _SSN_RE.search(v):
        return True
    # Whole-value email-ish / phone-ish without needing punctuation variants.
    if "@" in v and "." in v.split("@")[-1]:
        return True
    digits = re.sub(r"\D", "", v)
    if len(digits) >= 10 and len(digits) <= 15 and sum(c.isdigit() for c in v) >= 7:
        # Likely a phone typed without separators, or with extra punctuation.
        if not re.search(r"[a-zA-Z]{3,}", v):
            return True
    return False


def is_reusable_learning(label: str, value: str) -> bool:
    """True only for safe, cross-employer policy facts.

    Essays, job-specific screening, PII, secrets, and contaminated aria labels
    (e.g. 'select country calling code: romania') must never enter the store —
    they either burn tokens in the cheat sheet or mis-fill the next form.
    """
    key = normalize_label(label)
    val = (value or "").strip()
    if len(key) < MIN_LABEL_LEN or not val:
        return False
    if len(val) > MAX_LEARNED_VALUE_LEN:
        return False
    if _looks_like_secret(val) or _looks_like_pii_value(val):
        return False
    if _matches_any(key, _ESSAY_LABEL_PATTERNS):
        return False
    if _matches_any(key, _JOB_SPECIFIC_LABEL_PATTERNS):
        return False
    # Allow-list policy questions; everything else stays out of global memory
    # until a human promotes it. Cheap + prevents Flash from reusing narrative.
    if not _matches_any(key, _REUSABLE_POLICY_LABEL_PATTERNS):
        return False
    return True


def record_learning(label: str, value: str, platform: str) -> bool:
    """Persist a (label -> value) resolution for reuse on ANY future form.
    Returns True if it was actually saved (False for filtered-out entries)."""
    if not is_reusable_learning(label, value):
        return False
    key = normalize_label(label)
    store = load_learned()
    entry = store.get(key, {"value": value, "seen": 0, "platforms": [], "label_example": label})
    entry["value"] = value  # most recent resolution wins - facts can legitimately change
    entry["seen"] = entry.get("seen", 0) + 1
    plats = entry.setdefault("platforms", [])
    if platform and platform not in plats:
        plats.append(platform)
    store[key] = entry
    _save_learned(store)
    return True


def sanitize_learned_store(*, write: bool = True) -> dict:
    """Drop contaminated / non-reusable entries from learned_fields.json.

    Returns {"kept": N, "dropped": N, "dropped_keys": [...]} for telemetry.
    """
    store = load_learned()
    kept, dropped_keys = {}, []
    for key, entry in store.items():
        label = entry.get("label_example", key)
        value = str(entry.get("value", ""))
        if is_reusable_learning(label, value):
            kept[key] = entry
        else:
            dropped_keys.append(key)
    if write:
        _save_learned(kept)
    return {"kept": len(kept), "dropped": len(dropped_keys), "dropped_keys": dropped_keys}


def lookup_learned(label: str) -> str | None:
    """Return a sanitized reusable value for ``label``, or None.

    Exact match on ``normalize_label(label)`` against the store key. Contaminated
    / non-policy entries are ignored even if still present on disk — callers
    (fast_fill Layer learned-allow-list) must never apply unsanitized facts.
    """
    key = normalize_label(label)
    if not key or len(key) < MIN_LABEL_LEN:
        return None
    store = load_learned()
    entry = store.get(key)
    if not entry:
        return None
    example = entry.get("label_example", key)
    value = str(entry.get("value", "")).strip()
    if not is_reusable_learning(example, value):
        return None
    return value


def learned_cheat_sheet_rows(max_rows: int = 40) -> list[str]:
    """Learned facts formatted the same way as the static cheat sheet.

    Both ORDER and TEXT of each row must be stable across runs regardless of
    how confirmation counts change, or DeepSeek's prompt-cache prefix breaks
    on every run that confirms any learned fact - since this block is
    appended to EVERY platform's cheat sheet, one run anywhere disturbing it
    would cache-miss every run everywhere until counts happened to stop
    moving (which, with growing usage, they never fully do). Two things
    change with use and must NOT leak into the row text: the `seen` counter
    (grows on every confirmation) and row order if sorted by it. Sorting by
    label (alphabetical, tied to the JSON store's own sort_keys=True on-disk
    order) fixes order; leaving `seen` out of the row text itself - it's a
    human-debugging signal already surfaced by this module's own __main__,
    not something the model needs to act correctly - fixes content. `seen`
    still decides who gets CUT when truncating, just not what the surviving
    rows say or where they sit.

    Caps at 40 (was 60): smaller stable block = cheaper Flash tokens + better
    cache hits. Only reusable policy facts survive is_reusable_learning().
    """
    store = load_learned()
    # Filter at read-time too so an unsanitized on-disk store can't poison
    # cheat sheets until sanitize_learned_store() is run.
    usable = {
        k: e for k, e in store.items()
        if is_reusable_learning(e.get("label_example", k), str(e.get("value", "")))
    }
    keys = sorted(usable.keys(), key=lambda k: -usable[k].get("seen", 0))[:max_rows]
    rows = []
    for label in sorted(keys):
        entry = usable[label]
        example = entry.get("label_example", label)
        rows.append(f"  - fields about {example!r} (learned) -> {entry['value']!r}")
    return rows


def extract_and_save_learnings(db_conn_kwargs: dict, task_id: str, platform: str,
                               already_known_labels: set) -> int:
    """Mine a completed task's real actions for (label, value) pairs Layer 2
    resolved that the static cheat sheet did NOT already cover, and persist
    them. Call once, after a run finishes.

    `already_known_labels` (normalized) must be everything the STATIC cheat
    sheet covered for this run - without it, this would "learn" facts the
    system already had, silently overwriting a correct static answer with
    whatever the LLM happened to produce for a field it was never actually
    asked to reason about.
    """
    import psycopg

    with psycopg.connect(**db_conn_kwargs) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT skyvern_element_data, response, action_type FROM actions "
            "WHERE task_id = %s AND action_type IN ('input_text','select_option') "
            "AND skyvern_element_data IS NOT NULL",
            (task_id,),
        )
        rows = cur.fetchall()

    def extract_label(element_data: dict) -> str:
        ed = element_data or {}
        attrs = ed.get("attributes", {}) or {}
        # aria-label/placeholder FIRST, not raw `text`. Found live: for a
        # <select>, `text` can capture whatever OPTION content is currently
        # rendered/highlighted rather than the field's own question - a real
        # country-code dropdown had its own aria-label read "Select country
        # calling code: Romania" (the widget's OWN accessibility label bakes in
        # its currently-selected state), which is a genuinely different problem
        # from text-vs-attribute priority - see the stability filter below,
        # which is what actually catches this case.
        placeholder = attrs.get("placeholder") or ""
        if placeholder and _looks_like_generic_placeholder(placeholder):
            placeholder = ""  # a format hint ("1-415-555-1234...") or generic
            # UI prompt ("Start typing...") is not the field's question - don't
            # let it stand in for one, and fall through to `text` instead.
        return attrs.get("aria-label") or placeholder or ed.get("text") or ""

    # A stuck/struggling widget shows up as the SAME conceptual field acted on
    # multiple times with DIFFERENT responses within one run - found live on
    # that country-code picker: 10 actions, responses flip-flopping between
    # None / "United States" / "Click the dropdown to close and confirm." That
    # is the model visibly failing to pin the value down, not a confirmed fact.
    #
    # Grouped by LABEL, not element_id: Skyvern re-scrapes the page and assigns
    # a FRESH element_id on every step even for the same physical widget (a
    # documented finding from earlier this session, on a Yes/No toggle) - so
    # grouping by element_id let 10 actions on the conceptually-same struggling
    # button sail through as 10 "different" elements, each with only one
    # observation, none individually flagged as unstable. The widget's own
    # label/aria-label stays constant across re-scrapes even when its element
    # id does not, which is exactly why it doubles as the learned-store's own
    # key - reusing it here for stability-grouping is the same signal, not a
    # coincidence.
    by_label: dict[str, list[str]] = {}
    for element_data, response, _atype in rows:
        if response and response.strip():
            label = extract_label(element_data)
            if label:
                by_label.setdefault(normalize_label(label), []).append(response.strip())
    unstable_labels = {lbl for lbl, resps in by_label.items() if len(set(resps)) > 1}

    saved = 0
    for element_data, response, _atype in rows:
        if not response or not response.strip():
            continue
        label = extract_label(element_data)
        if not label:
            continue
        norm = normalize_label(label)
        if norm in unstable_labels:
            continue  # flip-flopped within this run - not a confirmed fact
        resp = response.strip()
        # Extra safety net: a genuine question label does not normally contain
        # its own answer verbatim.
        if resp.lower() in label.lower() and len(resp) > 3:
            continue
        if norm in already_known_labels:
            continue  # the static layers already answered this - not new knowledge
        if record_learning(label, resp, platform):
            saved += 1
    return saved


if __name__ == "__main__":
    import sys
    if "--sanitize" in sys.argv:
        result = sanitize_learned_store(write=True)
        print(f"sanitize: kept={result['kept']} dropped={result['dropped']}")
        for k in result["dropped_keys"]:
            print(f"  dropped: {k[:100]}")
        if result["kept"]:
            print("kept keys:")
            store = load_learned()
            for k in sorted(store):
                print(f"  kept: {k[:100]} -> {str(store[k].get('value', ''))[:60]!r}")
        raise SystemExit(0)
    store = load_learned()
    print(f"learned_fields.json: {len(store)} entries")
    for label, entry in sorted(store.items(), key=lambda kv: -kv[1].get("seen", 0))[:40]:
        ok = is_reusable_learning(entry.get("label_example", label), str(entry.get("value", "")))
        flag = "ok" if ok else "DROP"
        print(f"  [{flag}] {entry.get('seen',0):3d}x  {label[:50]:50s} -> {str(entry['value'])[:40]!r}")
    print("\nPolicy: store may contain only cross-employer policy facts.")
    print("Never: email, phone number, address/state/zip, name, salary, essays, secrets.")
    print("Run with --sanitize to drop non-policy entries from disk.")
