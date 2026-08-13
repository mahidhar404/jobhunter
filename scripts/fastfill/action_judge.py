"""Per-action fill judgment — correct_skip | needed_fill | thrash_rewrite.

Lightweight helper for fill_steps / phase logs. Dummy-only observability.
"""
from __future__ import annotations

import re
from typing import Any

from verified_select import value_matches_readback

try:
    from field_done import dummy_springfield_location_shown
except Exception:  # pragma: no cover
    def dummy_springfield_location_shown(readback: str | None) -> bool:
        s = re.sub(r"\s+", " ", (readback or "").strip().lower())
        return "springfield" in s and bool(re.search(r"\b(il|illinois)\b", s))


_PLACEHOLDER = re.compile(
    r"^(type here|select one|mm|yyyy|—|-|\.\.\.)$", re.I
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def is_committed_autofill_text(readback: str) -> bool:
    """Non-empty readback that is not a Workday placeholder."""
    raw = (readback or "").strip()
    if len(raw) < 2:
        return False
    if _PLACEHOLDER.match(raw):
        return False
    return True


def judge_field_action(
    *,
    field: str,
    before: str | None,
    after: str | None,
    intent: str | None,
    action: str = "fill",
    locked: bool = False,
) -> dict[str, Any]:
    """Return verdict dict for one field touch decision."""
    b = (before or "").strip()
    a = (after or "").strip()
    want = (intent or "").strip()
    verdict = "needed_fill"
    reason = ""

    loc_field = "LOCATION" in (field or "").upper()
    dummy_loc = loc_field and dummy_springfield_location_shown(b)

    if locked:
        verdict = "correct_skip"
        reason = "field_locked"
    elif dummy_loc and dummy_springfield_location_shown(a or b):
        verdict = "correct_skip"
        reason = "dummy_location_shown"
    elif want and b and a and b != a and value_matches_readback(want, b, mode="fill"):
        verdict = "thrash_rewrite"
        reason = "rewrote_already_correct"
    elif want and value_matches_readback(want, b, mode="fill"):
        verdict = "correct_skip"
        reason = "before_matches_intent"
    elif not want and is_committed_autofill_text(b):
        verdict = "correct_skip"
        reason = "autofill_committed"
    elif want and b and value_matches_readback(want, b, mode="fill"):
        verdict = "correct_skip"
        reason = "already_correct"
    elif b and a and _norm(b) == _norm(a) and is_committed_autofill_text(b):
        if want and not value_matches_readback(want, b, mode="fill"):
            verdict = "wrong_autofill"
            reason = "autofill_mismatch_intent"
        else:
            verdict = "correct_skip"
            reason = "unchanged"
    elif b and a and b != a:
        if want and value_matches_readback(want, b, mode="fill"):
            verdict = "thrash_rewrite"
            reason = "rewrote_already_correct"
        elif is_committed_autofill_text(b) and not want:
            verdict = "thrash_rewrite"
            reason = "rewrote_autofill"
    elif not b and a:
        verdict = "needed_fill"
        reason = "empty_to_filled"
    elif not b and not a:
        verdict = "needed_fill"
        reason = "still_empty"

    return {
        "field": field,
        "before": b[:120] if b else "",
        "after": a[:120] if a else "",
        "intent": want[:120] if want else "",
        "action": action,
        "verdict": verdict,
        "reason": reason,
        "thrash": verdict == "thrash_rewrite",
    }


def record_action_judge(report: dict | None, row: dict[str, Any]) -> None:
    """Append judgment to report action_judge list (bounded)."""
    if not report or not isinstance(report, dict):
        return
    lst = report.setdefault("action_judge", [])
    if len(lst) >= 500:
        return
    lst.append(row)
    if row.get("thrash"):
        report["thrash_rewrites"] = int(report.get("thrash_rewrites") or 0) + 1
