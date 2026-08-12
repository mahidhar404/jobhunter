#!/usr/bin/env python3
"""Logical-thrash hunt regressions (Capco cycle_20260811T090543481910Z evidence).

Covers wrong-type classification bugs that produced fabricated or blank answers
on the live Capco Greenhouse form:

- LOGIC-001: "Have you signed a noncompete agreement…" was semantically matched
  to WORK_AUTH and answered "Yes" (a wrong, policy-risky affirmative).
- LOGIC-002: "Capco Job Candidate Privacy Notice Acknowledgement*" was stolen by
  NAME_FULL's `\backnowledgement\b` token → a name pushed into a consent widget
  → required field left blank. Must route to TERMS_CONSENT (=Yes/acknowledge).
- LOGIC-003: "Do you know anyone or are you related to anyone who works at
  Capco?" was unclassified → required field left blank. Must route to
  WORKED_HERE_BEFORE (=No).
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def test_noncompete_not_work_auth_or_sponsorship():
    """LOGIC-001: a noncompete/NDA question must never map to WORK_AUTH/SPONSORSHIP."""
    from field_map import (
        SPONSORSHIP,
        WORK_AUTH,
        classify_field,
        guard_words_reject,
    )

    labels = (
        "Have you signed a noncompete agreement with any previous employer?",
        "Have you signed a non-compete agreement with any previous employer?",
        "Are you bound by a restrictive covenant or NDA?",
        "Have you signed a non-disclosure agreement?",
    )
    for lab in labels:
        # Guard is the safety net for when the (default-on) embedding backend
        # would otherwise conflate this with work authorization.
        assert guard_words_reject(WORK_AUTH, lab) is True, lab
        assert guard_words_reject(SPONSORSHIP, lab) is True, lab
        ftype, _layer = classify_field(
            {"label": lab, "name": "", "id": "", "placeholder": ""}
        )
        assert ftype not in (WORK_AUTH, SPONSORSHIP), (lab, ftype)

    # A genuine work-authorization question is unaffected.
    ftype, _ = classify_field(
        {"label": "Are you authorized to work in the country in which you're applying?"}
    )
    assert ftype == WORK_AUTH


def test_privacy_notice_acknowledgement_is_consent_not_name():
    """LOGIC-002: privacy/consent acknowledgement → TERMS_CONSENT, never NAME_FULL."""
    from field_map import NAME_FULL, TERMS_CONSENT, classify_field, guard_words_reject

    for lab in (
        "Capco Job Candidate Privacy Notice Acknowledgement*",
        "Privacy Policy Acknowledgement",
        "I acknowledge the data privacy notice",
    ):
        assert guard_words_reject(NAME_FULL, lab) is True, lab
        ftype, _layer = classify_field(
            {"label": lab, "name": "", "id": "", "placeholder": ""}
        )
        assert ftype != NAME_FULL, (lab, ftype)
        assert ftype == TERMS_CONSENT, (lab, ftype)

    # A plain signature/name acknowledgement (no policy words) still classifies
    # as NAME_FULL so e-signature name fields keep working.
    ftype, _ = classify_field({"label": "Signature (type your full name)"})
    assert ftype == NAME_FULL


def test_know_or_related_to_employee_is_worked_here_before():
    """LOGIC-003: 'know anyone / related to anyone who works here' → WORKED_HERE_BEFORE."""
    from field_map import WORKED_HERE_BEFORE, classify_field

    for lab in (
        "Do you know anyone or are you related to anyone who works at Capco?",
        "Are you related to anyone who currently works at the company?",
        "Do you know someone who works here?",
    ):
        ftype, _layer = classify_field(
            {"label": lab, "name": "", "id": "", "placeholder": ""}
        )
        assert ftype == WORKED_HERE_BEFORE, (lab, ftype)


def test_worked_here_before_answer_is_no():
    """The reclassified relation question resolves to the safe dummy 'No'."""
    from dummy_answers import DETERMINISTIC_ANSWERS

    assert DETERMINISTIC_ANSWERS["WORKED_HERE_BEFORE"] == "No"
    assert DETERMINISTIC_ANSWERS["TERMS_CONSENT"] == "Yes"


def test_worked_with_company_classifies():
    from field_map import WORKED_HERE_BEFORE, classify_field

    ftype, _ = classify_field(
        {
            "label": "Do you now or have you ever worked with Lindblad Expeditions?",
            "type": "textarea",
            "name": "",
        }
    )
    assert ftype == WORKED_HERE_BEFORE


def main() -> int:
    test_noncompete_not_work_auth_or_sponsorship()
    test_privacy_notice_acknowledgement_is_consent_not_name()
    test_know_or_related_to_employee_is_worked_here_before()
    test_worked_here_before_answer_is_no()
    test_worked_with_company_classifies()
    print("test_classify_logic_hunt: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
