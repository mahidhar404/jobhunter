"""Persistent learning loop - the missing piece that makes the system actually
generalize to ANY platform, seen or unseen, and get BETTER over time.

Every prior layer was static: field_map.py resolves what it can from a fixed
rule set, and whatever it can't resolve gets handed to Skyvern's LLM (Layer 2)
fresh, every single run, forever. That's wasteful and it's also not "learning" -
the docstring in field_map.py said the LLM's "real job is to produce a mapping
that gets saved" from the start, but nothing ever saved it.

This module closes that loop:
  1. Before a run, load everything learned so far and fold it into the cheat
     sheet - so a field an earlier run on ANY platform had to reason about is
     now a zero-cost lookup, exactly like a Layer-1 regex hit.
  2. After a run, mine the completed task's real actions for (label, value)
     pairs Layer 2 resolved that the static layers did NOT cover, and persist
     them.

Scope is deliberately GLOBAL, not per-platform. A question like "What is your
notice period?" or "Are you willing to relocate?" means the same thing on any
company's form regardless of which ATS renders it - keying by platform would
mean re-learning the same question on every new company forever, which is
exactly the failure mode this exists to fix. The one thing that must NEVER be
learned globally is a per-account secret (a generated password) - see
_looks_like_secret().

Growth is monotonic and safe: every entry is either a fresh fact discovered
live and immediately usable, or an update to a fact already deemed safe last
time. Nothing here is ever more dangerous than what field_map.py's own
DUMMY_PROFILE already contains.
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


def record_learning(label: str, value: str, platform: str) -> bool:
    """Persist a (label -> value) resolution for reuse on ANY future form.
    Returns True if it was actually saved (False for filtered-out entries)."""
    key = normalize_label(label)
    if len(key) < MIN_LABEL_LEN or not value or _looks_like_secret(value):
        return False
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


def learned_cheat_sheet_rows(max_rows: int = 60) -> list[str]:
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
    """
    store = load_learned()
    keys = sorted(store.keys(), key=lambda k: -store[k].get("seen", 0))[:max_rows]
    rows = []
    for label in sorted(keys):
        entry = store[label]
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
    store = load_learned()
    print(f"learned_fields.json: {len(store)} entries")
    for label, entry in sorted(store.items(), key=lambda kv: -kv[1].get("seen", 0))[:20]:
        print(f"  {entry.get('seen',0):3d}x  {label[:55]:55s} -> {str(entry['value'])[:40]!r}")
