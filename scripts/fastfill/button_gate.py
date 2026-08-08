"""Tiny deterministic button gate for hybrid / agent tooling.

Wraps button_map.is_forbidden so callers can refuse FINAL clicks without an
LLM judgment call. Keep DB-level never-submit as the hard backstop.

Critical: locators like ``button:has-text("Apply")`` or role name prefix
``^Apply\\b`` also match FINAL labels ("Apply and Submit"). Callers MUST
re-gate the *resolved* element's labels via ``gate_resolved_click`` before
any click — never trust the intent label alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from button_map import (  # noqa: E402
    ADVANCE,
    ENTRY,
    FINAL,
    RESUME_ENTRY,
    UNKNOWN,
    _norm,
    classify_button,
    is_forbidden,
)

_SUBMIT_LIKE = (
    "submit",
    "send application",
    "finish application",
    "complete application",
)

# Navigation clicks only — UNKNOWN answers ("Yes"/"No") use field_map paths.
NAV_KINDS = (ENTRY, ADVANCE, RESUME_ENTRY)


def gate_click(
    label: str,
    *,
    button_type: str = "",
    aria_label: str = "",
    value: str = "",
) -> dict:
    """Return {ok, kind, reason} for a proposed button click.

    Fail closed on FINAL. Also refuse any control whose label/value/aria looks
    submit-like (defense in depth if classify drifts), and refuse UNKNOWN
    ``type=submit`` (browsers treat those as form submitters even when the
    visible label is unfamiliar). ENTRY/ADVANCE with type=submit (e.g. Save
    and Continue) remain allowed.
    """
    kind = classify_button(
        label, button_type=button_type, aria_label=aria_label, value=value
    )
    display = (label or value or aria_label or "").strip()
    if is_forbidden(
        label, button_type=button_type, aria_label=aria_label, value=value
    ):
        return {
            "ok": False,
            "kind": kind or FINAL,
            "reason": f"forbidden FINAL/submit-like control: {display!r}",
        }
    blob = " ".join(x for x in (label, value, aria_label) if x).lower()
    # Defense in depth for UNKNOWN / misclassified controls. Skip when
    # classify already returned a safe nav kind (FINAL-first ran first), so
    # ENTRY "Submit interest" is not blocked by the bare "submit" substring.
    if kind not in NAV_KINDS and any(w in blob for w in _SUBMIT_LIKE):
        return {
            "ok": False,
            "kind": kind or FINAL,
            "reason": f"submit-like refused: {display!r}",
        }
    if kind == UNKNOWN:
        # type=submit with no recognized ENTRY/ADVANCE label → refuse.
        # Real "Apply" / "Next" buttons that happen to be type=submit still
        # classify as ENTRY/ADVANCE and are allowed above.
        if (button_type or "").lower() == "submit":
            return {
                "ok": False,
                "kind": kind,
                "reason": f"unknown type=submit refused: {display!r}",
            }
    return {"ok": True, "kind": kind, "reason": "allowed"}


def intent_matches_actual(intent: str, actual: str) -> bool:
    """True when a locator resolved to the intended control, not a wider FINAL.

    Exact (normed) match always wins. Prefix widenings are allowed only when the
    actual label still gates as navigable (Phenom: "Apply Now" → "Apply Now
    Software Engineer"). "Apply" → "Apply and Submit" fails because actual is
    FINAL / submit-like.
    """
    ni = _norm(intent).lower()
    na = _norm(actual).lower()
    if not ni or not na:
        return False
    if ni == na:
        return True
    if not na.startswith(ni):
        return False
    # Require a boundary after intent so "Next" does not match "Nextgen".
    rest = na[len(ni) :]
    if rest and rest[0].isalnum():
        return False
    gate = gate_click(actual)
    return bool(gate.get("ok")) and gate.get("kind") in NAV_KINDS


def gate_resolved_click(
    intent_label: str,
    *,
    actual_label: str = "",
    button_type: str = "",
    aria_label: str = "",
    value: str = "",
    allow_kinds: tuple[str, ...] = NAV_KINDS,
) -> dict:
    """Gate the control under the cursor (actual labels), not just the intent.

    Use after a locator resolves. Refuses FINAL, submit-like, UNKNOWN
    type=submit, non-navigable kinds, and substring collisions where the
    matched node is a different (often FINAL) button.
    """
    display = (actual_label or value or aria_label or "").strip()
    gate = gate_click(
        actual_label,
        button_type=button_type,
        aria_label=aria_label,
        value=value,
    )
    if not gate.get("ok") or gate.get("kind") == FINAL:
        return {
            "ok": False,
            "kind": gate.get("kind") or FINAL,
            "reason": gate.get("reason")
            or f"forbidden FINAL/submit-like control: {display!r}",
            "actual": display,
        }
    kind = gate.get("kind")
    if kind not in allow_kinds:
        return {
            "ok": False,
            "kind": kind,
            "reason": f"kind {kind!r} not in allow_kinds for click: {display!r}",
            "actual": display,
        }
    intent = (intent_label or "").strip()
    if intent:
        candidates = [c for c in (actual_label, value, aria_label) if c and str(c).strip()]
        if not any(intent_matches_actual(intent, c) for c in candidates):
            return {
                "ok": False,
                "kind": kind,
                "reason": (
                    f"locator resolved to different control: "
                    f"intent={intent!r} actual={display!r}"
                ),
                "actual": display,
            }
    return {
        "ok": True,
        "kind": kind,
        "reason": "allowed",
        "actual": display,
    }


async def read_click_labels(locator) -> dict:
    """Read text/type/aria/value from a Playwright Locator (Page or Frame)."""
    text = ""
    aria = ""
    value = ""
    btype = ""
    try:
        text = ((await locator.inner_text()) or "").strip()
        text = " ".join(text.split())
    except Exception:
        text = ""
    try:
        aria = (await locator.get_attribute("aria-label")) or ""
    except Exception:
        aria = ""
    try:
        value = (await locator.get_attribute("value")) or ""
    except Exception:
        value = ""
    try:
        btype = (await locator.get_attribute("type")) or ""
    except Exception:
        btype = ""
    if not text:
        text = (value or aria or "").strip()
    return {
        "text": text,
        "aria_label": aria,
        "value": value,
        "type": btype,
    }


async def gate_locator_click(
    locator,
    *,
    intent_label: str = "",
    allow_kinds: tuple[str, ...] = NAV_KINDS,
) -> dict:
    """Read labels from locator and run gate_resolved_click. Never clicks."""
    labels = await read_click_labels(locator)
    return gate_resolved_click(
        intent_label,
        actual_label=labels.get("text") or "",
        button_type=labels.get("type") or "",
        aria_label=labels.get("aria_label") or "",
        value=labels.get("value") or "",
        allow_kinds=allow_kinds,
    )


if __name__ == "__main__":
    samples = [
        ("Apply Manually", "", ""),
        ("Next", "", ""),
        ("Save and Continue", "submit", ""),
        ("Add Work Experience", "", ""),
        ("Submit Application", "", ""),
        ("Review and Submit", "", ""),  # must refuse (was UNKNOWN hole)
        ("Continue to submit", "", ""),
        ("Create account", "", ""),
        ("Complete", "submit", ""),  # must refuse
        ("", "", "Submit application"),  # aria-only FINAL
    ]
    for label, btype, aria in samples:
        print(
            f"{label!r:40s} type={btype!r:8s} aria={aria!r:24s} "
            f"-> {gate_click(label, button_type=btype, aria_label=aria)}"
        )
    print("\nResolved-click collisions:")
    for intent, actual in (
        ("Apply", "Apply"),
        ("Apply", "Apply and Submit"),
        ("Continue", "Continue to Submit Application"),
        ("Apply Now", "Apply Now Software Engineer"),
        ("Next", "Next"),
    ):
        print(
            f"  intent={intent!r:12s} actual={actual!r:40s} "
            f"-> {gate_resolved_click(intent, actual_label=actual)}"
        )
