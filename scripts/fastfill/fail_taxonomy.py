#!/usr/bin/env python3
"""Unified fail taxonomy for the exhaustive improvement cycle.

Maps a cycle decision + report signals into a single code used for fix priority,
decision logs, and plateau signatures. Dummy/report metadata only — no PII.
"""
from __future__ import annotations

from typing import Any

# Priority for Phase B fix queue (lower = fix first).
FIX_PRIORITY = (
    "FAIL_MIDWIZARD",
    "FAIL_WRONG_VALUE",
    "FAIL_THRASH",
    "FAIL_BLANK",
    "FAIL_STUCK",
)

NON_FIXABLE = frozenset(
    {
        "SUCCESS",
        "BLOCKED",
        "FAIL_ENV",
        "SAFETY_ABORT",
        "COOLDOWN",
        "PAUSE_BOT_PRESSURE",
    }
)


def _footer_is_advance(report: dict) -> bool:
    kind = str(
        report.get("footer_primary_kind")
        or report.get("footer_kind")
        or report.get("primary_footer_kind")
        or ""
    ).upper()
    if kind in ("ADVANCE", "NEXT", "SAVE_AND_CONTINUE"):
        return True
    # Explicit false Ready signals from page_progress
    if report.get("hold_incomplete") is True:
        return True
    if report.get("footer_primary_blocks_review_hold") is True:
        return True
    if report.get("ready_for_review") and report.get("advanced_incomplete"):
        return True
    return False


def _end_state_ok(report: dict) -> bool:
    """True when the run is allowed to claim form-complete (not page-local)."""
    if report.get("hold_incomplete"):
        return False
    if report.get("advanced_incomplete"):
        return False
    if report.get("stuck_on_same_page"):
        return False
    if report.get("validation_after_advance"):
        return False
    if report.get("required_empty_before_advance") or report.get("required_empty_after_fill"):
        return False
    if report.get("demoted_false_verified"):
        return False
    if report.get("vision_incomplete"):
        return False
    if report.get("listbox_open") or report.get("mid_widget_open"):
        return False
    if str(report.get("advance_blocked_reason") or "").strip():
        return False
    if report.get("gaps_block_ready") or (report.get("gaps_after_save") or []):
        return False
    if int(report.get("thrash_retouches") or 0) > 0:
        return False
    if _footer_is_advance(report):
        # Claiming SUCCESS/ready while ADVANCE is still the primary control is mid-wizard.
        if report.get("ready_for_review") or str(report.get("verdict") or "").upper() == "SUCCESS":
            return False
        # Even without those flags, if caller asked for end-state check on a "complete" claim:
        if report.get("claim_complete") or report.get("vision_complete_claim"):
            return False
    phase = str(report.get("workday_phase") or report.get("wizard_phase") or "").lower()
    if phase and phase in ("contact", "my experience", "experience", "auth", "account", "apply"):
        if report.get("ready_for_review") or str(report.get("verdict") or "").upper() == "SUCCESS":
            return False
    return True


def classify_attempt(
    report: dict | None = None,
    decision: dict | None = None,
) -> dict[str, Any]:
    """Return {code, reasons, fixable, signature} for one fill attempt."""
    report = report or {}
    decision = decision or {}
    reasons: list[str] = list(decision.get("reasons") or [])
    code = str(decision.get("verdict") or report.get("verdict") or "FAIL_BLANK").upper()

    # Safety first
    if report.get("submit_clicked") is True or report.get("never_submit") is False:
        return {
            "code": "SAFETY_ABORT",
            "reasons": reasons + ["safety"],
            "fixable": False,
            "signature": "SAFETY_ABORT",
        }

    blocker = str(report.get("blocker") or "")
    if blocker in ("captcha", "cloudflare", "akamai") or code == "BLOCKED":
        return {
            "code": "BLOCKED",
            "reasons": reasons or [f"blocker:{blocker or 'captcha'}"],
            "fixable": False,
            "signature": f"BLOCKED:{blocker or 'captcha'}",
        }

    if code == "FAIL_ENV" or report.get("chromium_fail_fast"):
        return {
            "code": "FAIL_ENV",
            "reasons": reasons or ["fail_env"],
            "fixable": False,
            "signature": "FAIL_ENV",
        }

    # Mid-wizard lie (even if decision said SUCCESS)
    if not _end_state_ok(report) or "advanced_incomplete" in reasons:
        return {
            "code": "FAIL_MIDWIZARD",
            "reasons": reasons + ["mid_wizard_end_state"],
            "fixable": True,
            "signature": _sig("FAIL_MIDWIZARD", report),
        }

    wrong = report.get("wrong_value_fields") or []
    if wrong or any(str(r).startswith("wrong_value") for r in reasons):
        return {
            "code": "FAIL_WRONG_VALUE",
            "reasons": reasons + [f"wrong_value_n:{len(wrong)}"],
            "fixable": True,
            "signature": _sig("FAIL_WRONG_VALUE", report, wrong),
        }

    thrash = report.get("thrash_events") or []
    if thrash or any("thrash" in str(r).lower() for r in reasons):
        return {
            "code": "FAIL_THRASH",
            "reasons": reasons + [f"thrash_n:{len(thrash)}"],
            "fixable": True,
            "signature": _sig("FAIL_THRASH", report, thrash),
        }

    if code == "FAIL_STUCK" or "stuck_on_same_page" in reasons:
        return {
            "code": "FAIL_STUCK",
            "reasons": reasons,
            "fixable": True,
            "signature": _sig("FAIL_STUCK", report),
        }

    if decision.get("success") is True and code == "SUCCESS":
        return {
            "code": "SUCCESS",
            "reasons": [],
            "fixable": False,
            "signature": "SUCCESS",
        }

    # Default blank / vision incomplete
    if code not in ("SUCCESS", "FAIL_BLANK", "FAIL_STUCK", "BLOCKED", "FAIL_ENV"):
        code = "FAIL_BLANK"
    return {
        "code": code if code in FIX_PRIORITY or code in NON_FIXABLE else "FAIL_BLANK",
        "reasons": reasons,
        "fixable": True,
        "signature": _sig(code if code in FIX_PRIORITY else "FAIL_BLANK", report),
    }


def _sig(code: str, report: dict, extra: list | None = None) -> str:
    plat = str(report.get("platform") or "unknown").lower()
    ftype = ""
    if extra and isinstance(extra, list) and extra:
        first = extra[0]
        if isinstance(first, dict):
            ftype = str(first.get("type") or first.get("label") or "")[:40]
        else:
            ftype = str(first)[:40]
    return f"{code}:{plat}:{ftype}".rstrip(":")


def apply_midwizard_to_decision(report: dict, decision: dict) -> dict:
    """Demote a SUCCESS decision when end-state gates fail (P1.5)."""
    out = dict(decision)
    if out.get("success") and not _end_state_ok(report):
        out["success"] = False
        out["verdict"] = "FAIL_MIDWIZARD"
        reasons = list(out.get("reasons") or [])
        reasons.append("mid_wizard_end_state")
        # Dedup
        seen: set[str] = set()
        out["reasons"] = [r for r in reasons if not (r in seen or seen.add(r))]
    # Also tag taxonomy on every decision
    classified = classify_attempt(report, out)
    out["taxonomy"] = classified
    if classified["code"] == "FAIL_MIDWIZARD" and out.get("success"):
        out["success"] = False
        out["verdict"] = "FAIL_MIDWIZARD"
    elif classified["code"] in ("FAIL_WRONG_VALUE", "FAIL_THRASH") and out.get("success"):
        out["success"] = False
        out["verdict"] = classified["code"]
        out.setdefault("reasons", []).append(classified["code"].lower())
    return out


def top_fix_class(counts: dict[str, int]) -> str | None:
    """Return highest-priority fixable class with count > 0."""
    for code in FIX_PRIORITY:
        if int(counts.get(code) or 0) > 0:
            return code
    return None


def _self_test() -> None:
    # Mid-wizard: ready + ADVANCE footer (legacy key)
    d = apply_midwizard_to_decision(
        {
            "ready_for_review": True,
            "footer_kind": "ADVANCE",
            "never_submit": True,
            "platform": "workday",
        },
        {"success": True, "verdict": "SUCCESS", "reasons": []},
    )
    assert d["success"] is False and d["verdict"] == "FAIL_MIDWIZARD", d

    # Mid-wizard: page_progress key footer_primary_kind (live reports)
    d2 = apply_midwizard_to_decision(
        {
            "ready_for_review": True,
            "footer_primary_kind": "ADVANCE",
            "footer_primary_label": "Next",
            "never_submit": True,
            "platform": "greenhouse",
        },
        {"success": True, "verdict": "SUCCESS", "reasons": []},
    )
    assert d2["success"] is False and d2["verdict"] == "FAIL_MIDWIZARD", d2

    # Advanced incomplete
    c = classify_attempt(
        {"advanced_incomplete": True, "platform": "ashby", "never_submit": True},
        {"success": False, "verdict": "FAIL", "reasons": ["advanced_incomplete"]},
    )
    assert c["code"] == "FAIL_MIDWIZARD", c

    # CAPTCHA
    c = classify_attempt({"blocker": "captcha"}, {"verdict": "BLOCKED"})
    assert c["code"] == "BLOCKED" and not c["fixable"]

    # Wrong value
    c = classify_attempt(
        {
            "never_submit": True,
            "wrong_value_fields": [{"type": "WORK_START", "shown": "01/2020"}],
            "platform": "workday",
        },
        {"success": False, "verdict": "FAIL", "reasons": []},
    )
    assert c["code"] == "FAIL_WRONG_VALUE", c

    # Thrash
    c = classify_attempt(
        {
            "never_submit": True,
            "thrash_events": [{"type": "HOW_HEARD", "attempts": 5}],
            "platform": "workday",
        },
        {"success": False, "verdict": "FAIL", "reasons": []},
    )
    assert c["code"] == "FAIL_THRASH", c

    assert top_fix_class({"FAIL_BLANK": 2, "FAIL_MIDWIZARD": 1}) == "FAIL_MIDWIZARD"

    # Mid-widget / listbox open must demote SUCCESS
    d3 = apply_midwizard_to_decision(
        {
            "listbox_open": True,
            "never_submit": True,
            "platform": "workday",
            "verdict": "SUCCESS",
        },
        {"success": True, "verdict": "SUCCESS", "reasons": []},
    )
    assert d3["success"] is False and d3["verdict"] == "FAIL_MIDWIZARD", d3

    # hold_incomplete / required_empty must demote SUCCESS
    d4 = apply_midwizard_to_decision(
        {
            "hold_incomplete": True,
            "never_submit": True,
            "platform": "workday",
            "verdict": "SUCCESS",
            "ready_for_review": True,
        },
        {"success": True, "verdict": "SUCCESS", "reasons": []},
    )
    assert d4["success"] is False and d4["verdict"] == "FAIL_MIDWIZARD", d4

    d5 = apply_midwizard_to_decision(
        {
            "required_empty_after_fill": [{"id": "email"}],
            "never_submit": True,
            "platform": "greenhouse",
            "verdict": "SUCCESS",
        },
        {"success": True, "verdict": "SUCCESS", "reasons": []},
    )
    assert d5["success"] is False and d5["verdict"] == "FAIL_MIDWIZARD", d5

    print("fail_taxonomy self-test OK")


if __name__ == "__main__":
    _self_test()
