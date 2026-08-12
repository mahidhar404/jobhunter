"""Three-way button classification - how the filler walks a multi-step form.

29% of real applications in this corpus span 2+ pages (some 6), so filling one
page is not filling an application. Walking a wizard means repeatedly answering
one question: is this button safe to click?

Three categories, and conflating any two is a bug with real consequences:

  ENTRY    "Apply", "Apply manually", "Apply as guest" - opens the form from a
           job-description page. Safe. Nothing has been filled yet, so it cannot
           possibly submit anything.
  ADVANCE  "Next", "Continue", "Save and continue" - moves to the next step of a
           multi-step form. Safe, and REQUIRED: refusing to click these is why
           earlier runs stalled on step 1 of a 6-step form and reported success.
  FINAL    "Submit application", "Send application" - the irreversible action.
           NEVER clicked. The run stops here and hands off to a human.

Why this is deterministic rather than a model call: an LLM asked "is it safe to
click this?" gives a probabilistic answer to a question whose wrong answer
submits a real job application to a real employer. That must be a lookup with an
explicit, auditable list, and anything unrecognised must fail closed.

Every phrase below is a real button observed in the corpus, not invented. The
two false positives found live today are encoded directly:
  * "save and continue" (Deloitte/Avature) - type=submit, but the agent's own
    reasoning confirmed it advances to "Job specific questions". ADVANCE.
  * "create account" (Labcorp/Phenom) - a login gate, not the employer-facing
    action, and now explicitly permitted (the profile uses a dummy address in
    test mode). ADVANCE.

Currently unwired from Skyvern hybrid_fill: that path does button safety via
prompt rules (LAYER3_RULES) plus a DB-level backstop
(real_job_test._check_no_submit_clicked) instead of calling this module.
Playwright ``fast_fill`` uses ``button_gate`` directly. Kept, not deleted,
because deterministically auto-clicking classified ENTRY/ADVANCE/RESUME_ENTRY
buttons - skipping an LLM round-trip for what is already a solved lookup - is a
real speed optimization for a future pass, not something to bolt on without its
own careful live-testing given the FINAL/never-submit stakes involved.
"""

import re

ENTRY = "ENTRY"
RESUME_ENTRY = "RESUME_ENTRY"  # a strict preference among ENTRY options, not a hazard tier
ADVANCE = "ADVANCE"
FINAL = "FINAL"
UNKNOWN = "UNKNOWN"

# FINAL is tested FIRST and its patterns are the most specific. Order matters
# enormously here: "continue to submit application" contains "continue", so an
# ADVANCE-first order would classify a real submission as safe. Whenever a
# phrase could plausibly belong to two categories, the more dangerous category
# must win.
FINAL_PATTERNS = [
    r"submit\s+application",
    r"submit\s+your\s+application",
    r"submit\s+my\s+application",
    r"send\s+application",
    r"send\s+my\s+application",
    r"finish\s+application",
    r"complete\s+application",
    # Any label that literally contains "submit" is FINAL — except the
    # ENTRY phrase "Submit interest" (opens the form; does not submit).
    # Observed live holes ("Review and Submit", "I Agree and Submit",
    # "Submit My Application") must not stay UNKNOWN.
    r"\bsubmit\b(?!\s+interest\b)",
    r"^send$",
    r"^finish$",
    r"^confirm$",
    r"^done$",
    # Any "continue/next ... submit/apply" compound is a submission wearing an
    # advance-looking prefix.
    r"(continue|next|proceed)\s+to\s+(submit|apply|finish)",
]

ADVANCE_PATTERNS = [
    r"^next$",
    r"^next\s*[→>»]+$",
    r"^continue$",
    r"^save\s*(and|&)\s*continue$",
    r"^save\s*(and|&)\s*next$",
    r"^save$",
    r"^proceed$",
    r"^review$",
    # In-page section builders (Workday "Add" / "Add Work Experience") — not
    # page ADVANCE, but safe to click; classified ADVANCE so gate_click allows
    # them without relying on UNKNOWN fallthrough.
    r"^add$",
    r"^add\s+(another|more|new)$",
    r"^add\s+(another\s+)?(work\s+)?experience$",
    r"^add\s+(another\s+)?(education|school|degree|skill|language)",
    # "Back" is deliberately NOT here. It was originally included alongside
    # forward-navigation words without checking what it actually does -
    # clicking it moves BACKWARD to a previous step, which is never progress,
    # and found live to trigger Workday's own "Discard your changes?"
    # confirmation modal, which then blocked the click entirely (the modal
    # covers the button, so Playwright waits for a click target that's now
    # behind an overlay). Left UNKNOWN so the walker fails closed on it
    # instead of navigating away from filled-in work.
    #
    # Account-creation / sign-in gates: throwaway login, explicitly NOT the
    # employer-facing action. See module docstring. "Sign In" is required for
    # Workday's already-registered path (create → error → Sign In).
    r"^create\s+(an\s+)?account$",
    r"^create\s+a\s+profile$",
    r"^register$",
    r"^sign\s*-?\s*up$",
    r"^sign\s*-?\s*in$",
    r"^log\s*-?\s*in$",
]

# Resume-based entry is checked FIRST and separately from plain ENTRY: Workday
# and similar platforms parse the uploaded resume and pre-populate name,
# experience and education automatically, which is strictly better than typing
# every field by hand. Given a choice, this path must always win.
RESUME_ENTRY_PATTERNS = [
    r"autofill\s*(with|from)\s*resume",
    r"apply\s*with\s*resume",
    r"upload\s*resume\s*to\s*apply",
    r"use\s*(my\s*)?resume",
    r"use\s*my\s*last\s*application",
    r"autofill\s*from\s*resume",
]
_RESUME_ENTRY = [re.compile(p, re.I) for p in RESUME_ENTRY_PATTERNS]

ENTRY_PATTERNS = [
    r"^apply$",
    # Phenom / Serco / Capital One: aria often appends the job title
    # ("Apply Now Software Engineer") — use \b, not $.
    r"^apply\s+now\b",
    r"^apply\s+online\b",
    r"^apply\s+for\s+this\s+job\b",
    r"^apply\s+for\s+(this\s+)?(job|position|role|opening)\b",
    r"^apply\s+to\s+(this\s+)?(job|position|role|opening)\b",
    r"^apply\s+manually\b",
    r"^apply\s+as\s+guest\b",
    r"^apply\s+with\b",
    r"^apply\s+externally\b",
    r"^quick\s+apply\b",
    r"^i'?m\s+interested\b",
    r"^express\s+interest\b",
    r"^start\s+(my\s+)?application\b",
    r"^begin\s+application\b",
    # Mid-tier ATS (SmartRecruiters / BambooHR / Workable / Personio)
    r"^continue\s+as\s+guest\b",
    r"^apply\s+without\s+(an?\s+)?account\b",
    r"^start\s+applying\b",
    r"^submit\s+interest\b",
    # Workday / SSO auth gates hide the email+password form behind these buttons
    # (social/SSO shown first). Clicking only REVEALS the email form — it never
    # submits an application — so classify as ENTRY (safe navigation) instead of
    # failing closed on UNKNOWN and stalling at the account gate.
    r"^sign\s*-?\s*in\s+with\s+(your\s+)?email\b",
    r"^continue\s+with\s+(your\s+)?email\b",
    r"^log\s*-?\s*in\s+with\s+(your\s+)?email\b",
    r"^use\s+email\b",
    r"^use\s+email\s+(address\s+)?instead\b",
    # Taleo / SuccessFactors / Dayforce / UKG-UltiPro
    r"^apply\s+for\s+job\b",
    r"^apply\s+here\b",
    r"^apply\s+as\s+a\s+guest\b",
    r"^start\s+your\s+application\b",
    r"^i\s+want\s+to\s+apply\b",
    r"^apply\s+to\s+job\b",
]

_FINAL = [re.compile(p, re.I) for p in FINAL_PATTERNS]
_ADVANCE = [re.compile(p, re.I) for p in ADVANCE_PATTERNS]
_ENTRY = [re.compile(p, re.I) for p in ENTRY_PATTERNS]


def _norm(text: str) -> str:
    """Collapse whitespace and strip decoration so anchored patterns can match.

    Real buttons carry trailing arrows, asterisks and non-breaking spaces
    ("Next →", "Save and Continue*"), and every ADVANCE/ENTRY pattern is
    anchored with ^...$, so without this normalisation they fail to match and
    fall through to UNKNOWN - which fails closed and stalls the form.
    """
    t = (text or "").replace(" ", " ").replace("​", "")
    t = re.sub(r"[→>»←<«✱*]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t.strip(" .:")


def classify_button(text: str, button_type: str = "", aria_label: str = "",
                    value: str = "") -> str:
    """Classify a button by its visible text.

    Checks each candidate label independently rather than concatenating them: a
    joined blob lets a word from `aria_label` complete a pattern that neither
    string satisfies alone, inventing matches that aren't really there.

    Returns UNKNOWN for anything unrecognised, and UNKNOWN must be treated as
    "do not click" by the caller - failing closed is the only safe default when
    the consequence of a wrong guess is a real submission.
    """
    candidates = [_norm(c) for c in (text, value, aria_label) if c and c.strip()]
    if not candidates:
        return UNKNOWN

    # Dangerous first, across ALL candidate strings, before anything is allowed
    # to match as safe.
    for c in candidates:
        for rx in _FINAL:
            if rx.search(c):
                return FINAL

    for c in candidates:
        for rx in _ADVANCE:
            if rx.match(c):
                return ADVANCE

    for c in candidates:
        for rx in _RESUME_ENTRY:
            if rx.search(c):
                return RESUME_ENTRY

    for c in candidates:
        for rx in _ENTRY:
            if rx.match(c):
                return ENTRY

    return UNKNOWN


def is_safe_navigation(text: str, button_type: str = "", aria_label: str = "",
                       value: str = "") -> bool:
    # NOTE: RESUME_ENTRY counts as safe navigation alongside ENTRY/ADVANCE -
    # see the check below, kept in one `in (...)` so a future new tier cannot
    # be forgotten here the way it would be with two separate comparisons.
    """True only for ENTRY and ADVANCE - i.e. 'may I click this to MOVE THROUGH
    the form?'. UNKNOWN and FINAL both fail closed.

    SCOPE - this answers a navigation question ONLY, and misreading it as a
    general "may I click this?" gate breaks form filling outright. Measured on
    572 real clicks from this corpus: 81.5% classify as UNKNOWN, and they are
    overwhelmingly not navigation at all - "No" (72), "Yes" (48), "Toggle
    flyout" (32), "Attach" (22), "Select", "I'm not local but open to
    relocating". Those are ANSWERS to questions and dropdown affordances, and
    they are clicked through a completely different path: the field is
    classified by field_map, the value comes from the profile, and the click
    lands on the option matching that value.

    So UNKNOWN means "not a navigation button - route this elsewhere", NOT
    "unsafe to ever click". Only FINAL means genuinely do-not-click.
    """
    return classify_button(text, button_type, aria_label, value) in (ENTRY, ADVANCE, RESUME_ENTRY)


def is_forbidden(text: str, button_type: str = "", aria_label: str = "",
                 value: str = "") -> bool:
    """True for the irreversible final-submission control. The one hard veto:
    no caller, on any path (navigation OR answering), may click a button for
    which this returns True."""
    return classify_button(text, button_type, aria_label, value) == FINAL


if __name__ == "__main__":
    # Every case below is a real button string observed in the corpus, plus the
    # adversarial compounds that motivated FINAL-first ordering.
    CASES = [
        ("Apply with Resume", RESUME_ENTRY),
        ("Apply With Resume", RESUME_ENTRY),
        ("Autofill with Resume", RESUME_ENTRY),
        ("Use My Last Application", RESUME_ENTRY),
        ("Apply", ENTRY), ("Apply for this job", ENTRY), ("Apply manually", ENTRY),
        ("Apply as guest", ENTRY), ("Apply now", ENTRY), ("Apply online", ENTRY),
        ("Apply Now Software Engineer", ENTRY),  # Phenom aria + job title
        ("Apply for this position", ENTRY), ("Apply for Job", ENTRY),
        ("Apply to this job", ENTRY), ("Quick Apply", ENTRY),
        ("Apply externally", ENTRY), ("Express Interest", ENTRY),
        ("Submit interest", ENTRY), ("Submit Interest", ENTRY),
        ("Apply here", ENTRY), ("Apply as a guest", ENTRY),
        ("Start your application", ENTRY), ("I want to apply", ENTRY),
        ("Apply to job", ENTRY),
        ("Next", ADVANCE), ("Next →", ADVANCE), ("Continue", ADVANCE),
        ("Save and continue", ADVANCE), ("Save & Continue", ADVANCE), ("Save", ADVANCE),
        ("Create Account", ADVANCE), ("Create a profile", ADVANCE),
        ("Register", ADVANCE), ("Sign up", ADVANCE),
        ("Sign In", ADVANCE), ("Log In", ADVANCE),
        ("Submit", FINAL), ("Submit application", FINAL), ("Submit Application", FINAL),
        ("Submit My Application", FINAL), ("Review and Submit", FINAL),
        ("I Agree and Submit", FINAL), ("Submit & Continue", FINAL),
        ("Send application", FINAL), ("Finish", FINAL), ("Confirm", FINAL),
        ("Continue to submit application", FINAL),   # advance-looking submission
        ("Proceed to Apply", FINAL),
        ("Search", UNKNOWN), ("", UNKNOWN),
        ("Add", ADVANCE), ("Add another", ADVANCE),
        ("Add Work Experience", ADVANCE), ("Add education", ADVANCE),
    ]
    bad = 0
    for text, expected in CASES:
        got = classify_button(text)
        ok = got == expected
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {text!r:34s} -> {got:8s} (expected {expected})")
    print(f"\n{len(CASES)-bad}/{len(CASES)} passed")
    print("\nSafety property - nothing FINAL is navigable or permitted:")
    for text, expected in CASES:
        if expected == FINAL:
            assert not is_safe_navigation(text), f"UNSAFE nav: {text!r}"
            assert is_forbidden(text), f"NOT VETOED: {text!r}"
    print("  verified.")
    print("\nAnswer-clicks are NOT navigation (they route via field_map):")
    for t in ("Yes", "No", "Select", "Toggle flyout"):
        assert not is_forbidden(t), f"answer wrongly vetoed: {t!r}"
        print(f"  {t!r:16s} nav={is_safe_navigation(t)}  forbidden={is_forbidden(t)}")
