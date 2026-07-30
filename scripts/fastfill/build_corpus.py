"""Build a realistic evaluation corpus from Skyvern's saved page scrapes.

The first evaluation pass graded against `actions.skyvern_element_data`, which
carries name/id/placeholder but NOT the visible <label> - it serialises only the
element itself, and a label lives in a sibling node. That understated real
performance badly on platforms whose fields are named with opaque UUIDs
(Ashby 32.6%, Paylocity 36.4%, Gem 0%) where the label is the ONLY signal a
human or a real filler would ever use.

Skyvern also saved 669 full raw page scrapes (`artifacts.artifact_type =
'html_scrape'`). Every scraped element carries a `unique_id` attribute, and
actions record that same id - so labels can be joined back to the fields that
were actually filled, per step. That reconstructs exactly what a Playwright
filler sees at runtime, making the resulting corpus a fair test rather than a
pessimistic one.

Label resolution mirrors what the real filler will do, in the same priority
order the accessibility spec implies:
  1. <label for="<id>">            - explicit association, strongest
  2. ancestor <label>              - implicit wrapping association
  3. aria-labelledby -> that node's text
  4. aria-label attribute
  5. nearest preceding text node   - last resort, weakest
"""

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import psycopg
from lxml import html as lxml_html

DB = dict(host="127.0.0.1", port=5432, dbname="skyvern_production",
          user="skyvern_app", password=os.environ["SKYVERN_DB_PASSWORD"])
OUT = Path(__file__).resolve().parent / "corpus.json"

FIELD_TAGS = {"input", "select", "textarea"}
# Inputs that never carry a user-typed profile value. Excluded so the corpus
# measures form-filling, not button-clicking.
SKIP_INPUT_TYPES = {"submit", "button", "reset", "image", "hidden"}

# ---------------------------------------------------------------------------
# PII: the corpus must never store a literal typed value.
#
# Part of this data came from non-TEST_MODE runs, so `actions.response` holds
# the REAL applicant's email/phone/name/LinkedIn. The evaluation only ever needs
# to know WHICH FIELD TYPE a value identified - never the value itself - so
# ground truth is derived here, at build time, and the raw string is dropped on
# the floor. corpus.json therefore contains form structure and field-type labels
# only, no personal data, and stays safe to keep on disk or share.
# ---------------------------------------------------------------------------
TEST_VALUES = {
    "test dummy": "NAME_FULL", "test": "NAME_FIRST", "dummy": "NAME_LAST",
    "test-dummy@example.com": "EMAIL", "405-555-0100": "PHONE", "4055550100": "PHONE",
    "https://github.com/test-dummy-account": "GITHUB",
    "https://www.linkedin.com/in/test-dummy-000000000": "LINKEDIN",
}
AMBIGUOUS = {
    "yes", "no", "y", "n", "true", "false", "male", "female",
    "asian", "white", "black", "decline to self identify",
    "i don't wish to answer", "prefer not to say",
}


def derive_truth(typed: str, real_values: dict) -> str | None:
    """Field type a value identifies, or None if it identifies nothing unique.

    `real_values` maps the live profile's own values to their type purely so
    non-TEST_MODE rows can still be graded; it is used for comparison only and
    never written out.
    """
    v = (typed or "").strip().lower()
    if not v or v in AMBIGUOUS:
        return None
    if v in TEST_VALUES:
        return TEST_VALUES[v]
    if v.endswith(".pdf"):
        return "RESUME_UPLOAD"
    if "linkedin.com" in v:
        return "LINKEDIN"
    if "github.com" in v:
        return "GITHUB"
    if "@" in v and "." in v.split("@")[-1]:
        return "EMAIL"
    if v in real_values:
        return real_values[v]
    # Digit-dominant != phone. This heuristic originally swept up dates
    # ("01/15/2026" into an "Available to start" field) and salary figures
    # ("190000" into "Salary Range"), then graded the classifier's CORRECT
    # NOTICE_PERIOD / SALARY answers as errors - measurement noise that would
    # have driven real regressions if tuned against. Excluded explicitly:
    #   * date shapes (MM/DD/YYYY, YYYY-MM-DD, and separator-less 8-digit dates)
    #   * bare integers, which are salaries/years/counts, never phone numbers
    # A real phone is 7-15 digits AND carries phone punctuation or a country
    # code, so requiring that is both stricter and more faithful.
    if re.fullmatch(r"\d{1,4}[/-]\d{1,2}[/-]\d{1,4}", v):
        return None
    if re.fullmatch(r"\d+(\.\d+)?", v):
        return None
    # Addresses before phones. A mailing address ("2500 N Lincoln Blvd, Apt 4B,
    # Oklahoma City, OK 73105") carries 10 digits and punctuation, so the phone
    # rule below claimed it - which then graded 17 correctly-classified location
    # fields as failures. Street suffix or a "CITY, ST 12345" tail identifies an
    # address unambiguously; the addresses themselves come from addresses.json
    # and vary per job, so shape matching is required rather than value lookup.
    if re.search(r"\b(st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|ln|lane|way|ct|court|apt|suite|ste|unit)\b\.?", v):
        return "ADDRESS_LINE1"
    if re.search(r",\s*[a-z]{2}\s*\d{5}(-\d{4})?\b", v):
        return "ADDRESS_LINE1"
    digits = sum(c.isdigit() for c in v)
    if 7 <= digits <= 15 and re.search(r"[\s().+-]", v):
        return "PHONE"
    return None


def load_real_value_index() -> dict:
    """Map real profile values -> field type, for GRADING ONLY.

    This is the one place real data is genuinely required and cannot be
    substituted: part of the corpus came from non-TEST_MODE runs, so recognising
    which field a recorded value belonged to means comparing against the values
    that were actually used. Dummy values would simply fail to match and those
    rows would be silently dropped, shrinking the test set rather than
    protecting anything.

    The index is a lookup table consumed in-process by derive_truth() and never
    written anywhere; only the resulting TYPE reaches the corpus, and the
    pre-write assertion in main() enforces that. Both the dummy identity's
    values and the real profile's are included, since the corpus mixes runs from
    both modes.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from field_map import build_value_map, DUMMY_PROFILE, DUMMY_ADDRESS  # noqa: E402
    idx = {}
    for prof, addr in ((DUMMY_PROFILE, DUMMY_ADDRESS), (
            json.load(open(Path(__file__).resolve().parents[2] / "profile.json")), "")):
        for ftype, val in build_value_map(prof, addr).items():
            s = str(val or "").strip().lower()
            if s:
                idx.setdefault(s, ftype)
    return idx


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def resolve_label(el, tree) -> tuple[str, str]:
    """Return (label_text, how_it_was_found)."""
    el_id = el.get("id")
    if el_id:
        # Escape quotes in the id before interpolating into XPath - some ATS
        # platforms use ids containing quotes/brackets, which would otherwise
        # produce an invalid expression and lose the label entirely.
        safe = el_id.replace('"', '\\"')
        try:
            for lab in tree.xpath(f'//label[@for="{safe}"]'):
                t = _clean(lab.text_content())
                if t:
                    return t, "label_for"
        except Exception:
            pass

    for anc in el.iterancestors():
        if anc.tag == "label":
            t = _clean(anc.text_content())
            if t:
                return t, "ancestor_label"

    labelledby = el.get("aria-labelledby")
    if labelledby:
        for ref in labelledby.split():
            safe = ref.replace('"', '\\"')
            try:
                for node in tree.xpath(f'//*[@id="{safe}"]'):
                    t = _clean(node.text_content())
                    if t:
                        return t, "aria_labelledby"
            except Exception:
                pass

    aria = _clean(el.get("aria-label"))
    if aria:
        return aria, "aria_label"

    # Last resort: nearest preceding text. Capped at 120 chars because on some
    # layouts the "nearest" text is an entire paragraph of instructions, which
    # is noise rather than a label and would pollute regex matching.
    prev = el.getprevious()
    hops = 0
    while prev is not None and hops < 3:
        t = _clean(prev.text_content())
        if t and len(t) <= 120:
            return t, "preceding_text"
        prev = prev.getprevious()
        hops += 1

    return "", "none"


def main():
    with psycopg.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT step_id, replace(uri,'file://','')
            FROM artifacts WHERE artifact_type='html_scrape' AND step_id IS NOT NULL
        """)
        html_by_step = {}
        for step_id, path in cur.fetchall():
            html_by_step.setdefault(step_id, path)

        cur.execute("""
            SELECT step_id, task_id, skyvern_element_data, COALESCE(response,''), action_type
            FROM actions
            WHERE skyvern_element_data IS NOT NULL
              AND action_type IN ('input_text','select_option','upload_file')
              AND step_id IS NOT NULL
        """)
        actions = cur.fetchall()

    # unique_id is assigned per scrape, so it is only meaningful within its own
    # step - joining globally would silently mix up elements across pages.
    by_step = defaultdict(list)
    for step_id, task_id, ed, typed, atype in actions:
        if isinstance(ed, dict):
            by_step[step_id].append((task_id, ed, typed, atype))

    real_values = load_real_value_index()
    records = []
    parsed_steps = 0
    missing_html = 0
    ungradeable = 0

    for step_id, items in by_step.items():
        path = html_by_step.get(step_id)
        if not path or not Path(path).exists():
            missing_html += len(items)
            continue
        try:
            tree = lxml_html.parse(path).getroot()
        except Exception:
            missing_html += len(items)
            continue
        parsed_steps += 1

        index = {}
        for el in tree.iter():
            if not isinstance(el.tag, str) or el.tag not in FIELD_TAGS:
                continue
            uid = el.get("unique_id")
            if uid:
                index[uid] = el

        for task_id, ed, typed, atype in items:
            truth = derive_truth(typed, real_values)
            if truth is None:
                # Not gradeable (free text, ambiguous yes/no, dropdown label).
                # Dropped here rather than carried along, which also guarantees
                # no unrecognised value can leak into the written corpus.
                ungradeable += 1
                continue
            uid = ed.get("id")
            el = index.get(uid)
            attrs = ed.get("attributes") or {}
            rec = {
                "task_id": task_id,
                "page_url": ed.get("page_url", ""),
                "truth": truth,          # field TYPE only - never the value
                "action_type": atype,
                "name": attrs.get("name"),
                "id": attrs.get("id"),
                "placeholder": attrs.get("placeholder"),
                "aria_label": attrs.get("aria-label"),
                "autocomplete": attrs.get("autocomplete"),
                "input_type": attrs.get("type"),
                "label": "",
                "label_source": "none",
                "has_html": el is not None,
            }
            if el is not None:
                if (el.get("type") or "").lower() in SKIP_INPUT_TYPES:
                    continue
                label, how = resolve_label(el, tree)
                rec["label"] = label
                rec["label_source"] = how
                # Prefer the live DOM's own attributes where the serialised
                # action data had none.
                for k, a in (("name", "name"), ("id", "id"),
                             ("placeholder", "placeholder"),
                             ("aria_label", "aria-label"),
                             ("autocomplete", "autocomplete")):
                    if not rec[k]:
                        rec[k] = el.get(a)
            records.append(rec)

    # Check BEFORE writing, not after - an assert that fires post-write has
    # already put the data on disk, which defeats the entire purpose.
    #
    # Probes are the REAL profile's own values plus the synthetic test identity,
    # not generic strings: "@example.com" occurs legitimately as an ATS form's
    # own placeholder text ("hello@example.com"), which is form structure and
    # useful classification signal, not personal data. Matching on that would be
    # a false positive that trains us to weaken the check.
    # Probe for IDENTIFYING values only - name, email, phone, personal URLs.
    # Deliberately NOT demographic values: "not a veteran" / "asian" / "male"
    # are generic English that appears in the form's OWN option lists (Lever
    # wraps its <select> in the <label>, so the label text legitimately reads
    # "Veteran status... I am a veteran / I am not a veteran / Decline to
    # self-identify"). That is the employer's public page content, not the
    # applicant's answer, and probing for it produces false positives that
    # pressure the check into being weakened - the opposite of what it's for.
    #
    # The structural guarantee is stronger than any probe anyway: this corpus
    # records no answers whatsoever, only field structure plus the field TYPE
    # (`truth`). The single path by which a real value could reach disk is
    # label/placeholder text, which is authored by the website, not the user.
    profile = json.load(open(Path(__file__).resolve().parents[2] / "profile.json"))
    identifying = [
        profile.get("personal", {}).get("full_name", ""),
        profile.get("contact", {}).get("email", ""),
        profile.get("contact", {}).get("phone", ""),
        profile.get("links", {}).get("linkedin", ""),
        profile.get("links", {}).get("github", ""),
        "test-dummy@example.com", "405-555-0100",
        "https://github.com/test-dummy-account",
    ]
    blob = json.dumps(records).lower()
    for probe in identifying:
        p = str(probe).strip().lower()
        if len(p) >= 8:
            assert p not in blob, f"PII leak: corpus contains {p!r}"

    OUT.write_text(json.dumps(records, indent=1))
    with_label = sum(1 for r in records if r["label"])
    src = defaultdict(int)
    for r in records:
        src[r["label_source"]] += 1

    print(f"steps parsed:        {parsed_steps}")
    print(f"records:             {len(records)}")
    print(f"  matched to HTML:   {sum(1 for r in records if r['has_html'])}")
    print(f"  no html_scrape:    {missing_html} (excluded)")
    print(f"  ungradeable:       {ungradeable} (excluded)")
    print(f"  WITH a label:      {with_label}  ({with_label/max(len(records),1)*100:.1f}%)")
    print(f"distinct forms:      {len({r['task_id'] for r in records})}")
    print("PII check:           passed (no raw values in corpus)")
    print("\nlabel resolved by:")
    for k, v in sorted(src.items(), key=lambda x: -x[1]):
        print(f"   {k:18s} {v:5d}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
