#!/usr/bin/env python3
"""Focused unit tests for button_map + button_gate (no browser, no employer).

Safety properties under test:
  1. Every FINAL / Submit-like label is refused by gate_click and is_forbidden.
  2. Save and Continue / Add Work Experience classify ADVANCE and are allowed.
  3. Unknown type=submit without a recognized ENTRY/ADVANCE label is refused.
  4. Substring locator collisions (Apply→Apply and Submit) refused by
     gate_resolved_click / intent_matches_actual.
  5. Phenom-style prefix widenings (Apply Now → Apply Now <title>) still allowed.
  6. Dummy-only: no network, no profile PII, no live clicks.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from button_gate import (  # noqa: E402
    NAV_KINDS,
    gate_click,
    gate_resolved_click,
    intent_matches_actual,
)
from button_map import (  # noqa: E402
    ADVANCE,
    ENTRY,
    FINAL,
    RESUME_ENTRY,
    UNKNOWN,
    classify_button,
    is_forbidden,
    is_safe_navigation,
)

# Labels that must never be clickable (FINAL classify + gate refuse).
MUST_REFUSE = [
    ("Submit", {}),
    ("Submit Application", {}),
    ("Submit My Application", {}),
    ("SUBMIT APPLICATION", {}),
    ("Send application", {}),
    ("Finish Application", {}),
    ("Complete Application", {}),
    ("Continue to submit", {}),
    ("Continue to Submit Application", {}),
    ("Proceed to Apply", {}),
    ("Next to submit", {}),
    ("Review and Submit", {}),
    ("I Agree and Submit", {}),
    ("Apply and Submit", {}),
    ("Submit & Continue", {}),
    ("Submit form", {}),
    ("", {"value": "Submit"}),
    ("", {"aria_label": "Submit application"}),
    ("Complete", {"button_type": "submit"}),  # UNKNOWN type=submit
    ("Go", {"button_type": "submit"}),
]

# ADVANCE navigation / section builders that must be allowed.
MUST_ALLOW_ADVANCE = [
    ("Save and Continue", {"button_type": "submit"}),
    ("Save and Continue*", {}),
    ("Save & Continue", {}),
    ("Save and Next", {}),
    ("Next", {}),
    ("Continue", {}),
    ("Add Work Experience", {}),
    ("Add Another Work Experience", {}),
    ("Add Experience", {}),
    ("Add education", {}),
    ("Add", {}),
]

MUST_ALLOW_ENTRY = [
    ("Apply", {}),
    ("Apply Manually", {}),
    ("Apply now", {}),
    ("Submit interest", {}),
    ("Submit Interest", {}),
]

MUST_ALLOW_RESUME_ENTRY = [
    ("Apply with Resume", {}),
    ("Apply With Resume", {}),
    ("Autofill with Resume", {}),
    ("Use My Last Application", {}),
    ("Use resume", {}),
]

# Intent → actual pairs a has-text / role-prefix locator might resolve to.
# ok=False means the click path must refuse (would have been a FINAL hole).
LOCATOR_COLLISIONS = [
    ("Apply", "Apply", True),
    ("Apply", "Apply and Submit", False),
    ("Apply", "Apply & Submit", False),
    ("Continue", "Continue", True),
    ("Continue", "Continue to Submit Application", False),
    ("Continue", "Continue to submit", False),
    ("Next", "Next", True),
    ("Next", "Next to submit", False),
    ("Save and Continue", "Save and Continue", True),
    ("Apply Now", "Apply Now Software Engineer", True),  # Phenom
    ("Create Account", "Create Account", True),
    ("Sign In", "Sign In", True),
]


def _gate(label: str, kwargs: dict) -> dict:
    return gate_click(
        label,
        button_type=kwargs.get("button_type", ""),
        aria_label=kwargs.get("aria_label", ""),
        value=kwargs.get("value", ""),
    )


def _kind(label: str, kwargs: dict) -> str:
    return classify_button(
        label,
        button_type=kwargs.get("button_type", ""),
        aria_label=kwargs.get("aria_label", ""),
        value=kwargs.get("value", ""),
    )


def test_final_and_submit_always_refused() -> None:
    for label, kwargs in MUST_REFUSE:
        gate = _gate(label, kwargs)
        kind = _kind(label, kwargs)
        display = label or kwargs.get("value") or kwargs.get("aria_label") or ""
        assert gate["ok"] is False, f"gate allowed refuse-target: {display!r} -> {gate}"
        assert kind != ADVANCE, f"refuse-target classified ADVANCE: {display!r}"
        assert kind != ENTRY, f"refuse-target classified ENTRY: {display!r}"
        # Bare Submit* / *Submit* labels must be FINAL + is_forbidden.
        blob = " ".join(
            x for x in (label, kwargs.get("value", ""), kwargs.get("aria_label", "")) if x
        ).lower()
        if "submit" in blob or kind == FINAL:
            assert kind == FINAL, f"submit-like not FINAL: {display!r} -> {kind}"
            assert is_forbidden(
                label,
                button_type=kwargs.get("button_type", ""),
                aria_label=kwargs.get("aria_label", ""),
                value=kwargs.get("value", ""),
            ), f"is_forbidden missed: {display!r}"
            assert not is_safe_navigation(
                label,
                button_type=kwargs.get("button_type", ""),
                aria_label=kwargs.get("aria_label", ""),
                value=kwargs.get("value", ""),
            ), f"FINAL navigable: {display!r}"


def test_save_continue_and_add_experience_advance() -> None:
    for label, kwargs in MUST_ALLOW_ADVANCE:
        kind = _kind(label, kwargs)
        gate = _gate(label, kwargs)
        assert kind == ADVANCE, f"{label!r} -> {kind}, expected ADVANCE"
        assert gate["ok"] is True, f"ADVANCE refused: {label!r} -> {gate}"
        assert gate["kind"] == ADVANCE
        assert not is_forbidden(
            label,
            button_type=kwargs.get("button_type", ""),
            aria_label=kwargs.get("aria_label", ""),
            value=kwargs.get("value", ""),
        )


def test_entry_still_allowed() -> None:
    for label, kwargs in MUST_ALLOW_ENTRY:
        kind = _kind(label, kwargs)
        gate = _gate(label, kwargs)
        assert kind == ENTRY, f"{label!r} -> {kind}, expected ENTRY"
        assert gate["ok"] is True, f"ENTRY refused: {label!r} -> {gate}"


def test_resume_entry_classified_and_allowed() -> None:
    for label, kwargs in MUST_ALLOW_RESUME_ENTRY:
        kind = _kind(label, kwargs)
        gate = _gate(label, kwargs)
        assert kind == RESUME_ENTRY, f"{label!r} -> {kind}, expected RESUME_ENTRY"
        assert gate["ok"] is True, f"RESUME_ENTRY refused: {label!r} -> {gate}"


def test_pick_click_candidates_prefers_resume_over_manual() -> None:
    from fast_fill import pick_click_candidates

    classified = [
        {
            "text": "Apply Manually",
            "kind": "ENTRY",
            "gate_ok": True,
            "href": "",
        },
        {
            "text": "Apply with Resume",
            "kind": "RESUME_ENTRY",
            "gate_ok": True,
            "href": "",
        },
    ]
    ranked = pick_click_candidates(classified, allow_advance=True)
    assert ranked[0]["text"] == "Apply with Resume"
    assert ranked[1]["text"] == "Apply Manually"


def test_final_first_beats_advance_words() -> None:
    """Compounds with continue/next must still be FINAL, not ADVANCE."""
    for label in (
        "Continue to submit application",
        "Continue to submit",
        "Proceed to Apply",
        "Next to submit",
    ):
        assert classify_button(label) == FINAL
        assert gate_click(label)["ok"] is False


def test_answer_clicks_not_forbidden() -> None:
    for t in ("Yes", "No", "Select", "Toggle flyout"):
        assert classify_button(t) == UNKNOWN
        assert not is_forbidden(t)
        # UNKNOWN without type=submit: gate allows (field_map routes answers).
        assert gate_click(t)["ok"] is True
        # But navigation click helpers must not treat UNKNOWN as clickable.
        resolved = gate_resolved_click(t, actual_label=t, allow_kinds=NAV_KINDS)
        assert resolved["ok"] is False, f"UNKNOWN navigable via resolved: {t!r}"


def test_locator_collision_resolved_gate() -> None:
    """has-text/prefix must not click FINAL when intent was safe."""
    for intent, actual, expect_ok in LOCATOR_COLLISIONS:
        matched = intent_matches_actual(intent, actual)
        resolved = gate_resolved_click(intent, actual_label=actual)
        assert matched is expect_ok, (
            f"intent_matches_actual({intent!r}, {actual!r}) -> {matched}, "
            f"expected {expect_ok}"
        )
        assert resolved["ok"] is expect_ok, (
            f"gate_resolved_click({intent!r}, {actual!r}) -> {resolved}, "
            f"expected ok={expect_ok}"
        )
        if not expect_ok:
            # Collision targets that contain submit must also be bare-gate refused.
            if "submit" in actual.lower():
                assert gate_click(actual)["ok"] is False


def test_aria_submit_with_visible_next_refused() -> None:
    """Visible 'Next' + aria Submit must fail closed (Workday hole)."""
    gate = gate_click("Next", aria_label="Submit application")
    assert gate["ok"] is False
    assert classify_button("Next", aria_label="Submit application") == FINAL
    resolved = gate_resolved_click(
        "Next",
        actual_label="Next",
        aria_label="Submit application",
    )
    assert resolved["ok"] is False


def test_unknown_type_submit_resolved_refused() -> None:
    resolved = gate_resolved_click(
        "Complete",
        actual_label="Complete",
        button_type="submit",
    )
    assert resolved["ok"] is False
    assert "submit" in (resolved.get("reason") or "").lower() or resolved.get("kind") in (
        FINAL,
        UNKNOWN,
    )


def test_nav_kinds_constant() -> None:
    assert ENTRY in NAV_KINDS and ADVANCE in NAV_KINDS
    assert FINAL not in NAV_KINDS and UNKNOWN not in NAV_KINDS


def main() -> int:
    tests = [
        test_final_and_submit_always_refused,
        test_save_continue_and_add_experience_advance,
        test_entry_still_allowed,
        test_resume_entry_classified_and_allowed,
        test_pick_click_candidates_prefers_resume_over_manual,
        test_final_first_beats_advance_words,
        test_answer_clicks_not_forbidden,
        test_locator_collision_resolved_gate,
        test_aria_submit_with_visible_next_refused,
        test_unknown_type_submit_resolved_refused,
        test_nav_kinds_constant,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
