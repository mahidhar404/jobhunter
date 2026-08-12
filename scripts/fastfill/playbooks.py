#!/usr/bin/env python3
"""Allowlisted named interaction strategies for fastfill widgets.

Deterministic detection from field metadata (no network). LLM may only pick
playbook ids from ``ALLOWED_PLAYBOOKS``.
"""
from __future__ import annotations

import argparse
import json
import re
from typing import Any

ALLOWED_PLAYBOOKS = frozenset(
    {
        "native_select",
        "react_select_portal",
        "typable_commit",
        "workday_how_heard",
        "date_spinner",
        "text_input",
        "checkbox",
        "radio",
    }
)

_REACT_SELECT_CLASS_RE = re.compile(
    r"(?:select__|react-select|css-[a-z0-9]+-control)",
    re.I,
)
_TYPABLE_HINT_RE = re.compile(
    r"(?:typable|autocomplete|typeahead|autosuggest|combobox-input)",
    re.I,
)
_HOW_HEARD_RE = re.compile(
    r"(?:how[\s_-]*heard|how[\s_-]*did[\s_-]*you[\s_-]*hear|referral[\s_-]*source|"
    r"hierarchical|source[\s_-]*category)",
    re.I,
)
_DATE_SPINNER_RE = re.compile(
    r"(?:"
    r"\bdate\b|\bmonth\b|\byear\b|\bday\b|\bbirth\b|"
    r"start[\s_-]*date|end[\s_-]*date|"
    r"dateSection|datePicker|date-spinner|month-spinner|year-spinner"
    r")",
    re.I,
)


def is_allowed_playbook(name: str) -> bool:
    return (name or "").strip() in ALLOWED_PLAYBOOKS


def detect_playbook(field_meta: dict) -> str:
    """Heuristic playbook id from tag/role/class/platform/label (no network)."""
    meta = field_meta or {}
    tag = str(meta.get("tag") or "").lower().strip()
    role = str(meta.get("role") or "").lower().strip()
    ftype = str(meta.get("type") or meta.get("input_type") or "").lower().strip()
    classes = str(meta.get("class") or meta.get("className") or meta.get("classes") or "")
    label = str(meta.get("label") or meta.get("aria_label") or meta.get("ariaLabel") or "")
    automation_id = str(meta.get("automation_id") or meta.get("automationId") or "")
    platform = str(meta.get("platform") or "").lower().strip()
    name = str(meta.get("name") or meta.get("id") or "")

    haystack = " ".join((label, automation_id, name, classes, platform)).lower()

    if tag == "select":
        return "native_select"

    if ftype == "checkbox" or (tag == "input" and ftype == "checkbox"):
        return "checkbox"

    if ftype == "radio" or (tag == "input" and ftype == "radio"):
        return "radio"

    if _HOW_HEARD_RE.search(haystack) or automation_id.lower() in {
        "how_heard",
        "formfield-how_heard",
        "referralsource",
    }:
        return "workday_how_heard"

    if _DATE_SPINNER_RE.search(haystack) or _DATE_SPINNER_RE.search(automation_id):
        return "date_spinner"

    if role == "combobox":
        if _REACT_SELECT_CLASS_RE.search(classes):
            return "react_select_portal"
        if _TYPABLE_HINT_RE.search(haystack) or meta.get("typable") or meta.get("autocomplete"):
            return "typable_commit"
        # Generic combobox without react-select hints → typable_commit
        if platform in ("greenhouse", "lever", "ashby"):
            return "typable_commit"
        return "react_select_portal"

    if role in ("listbox", "option") and _REACT_SELECT_CLASS_RE.search(classes):
        return "react_select_portal"

    return "text_input"


def playbook_hints(name: str) -> dict[str, Any]:
    """Short hints for Flash prompts (how to interact); no PII."""
    hints: dict[str, dict[str, Any]] = {
        "native_select": {
            "strategy": "native_select",
            "steps": [
                "Use page.select_option or locator.select_option on the <select>.",
                "Match option by visible label text; verify selected option after.",
            ],
            "verify": "selected option label matches intended choice",
        },
        "react_select_portal": {
            "strategy": "react_select_portal",
            "steps": [
                "Click combobox control to open portal listbox.",
                "Click matching [role=option] in document or portal root.",
                "Confirm committed display text (not just typed filter).",
            ],
            "verify": "combobox shows chosen label, not placeholder",
        },
        "typable_commit": {
            "strategy": "typable_commit",
            "steps": [
                "Focus combobox, type filter text slowly.",
                "Wait for options; click or Enter to commit selection.",
                "Do not stop after typing — must pick an option.",
            ],
            "verify": "committed value differs from empty/placeholder",
        },
        "workday_how_heard": {
            "strategy": "workday_how_heard",
            "steps": [
                "Two-step hierarchy: pick category then leaf source.",
                "Use data-automation-id formField-how_heard when present.",
                "Chip/display text must show committed source.",
            ],
            "verify": "how-heard display shows leaf source, not category only",
        },
        "date_spinner": {
            "strategy": "date_spinner",
            "steps": [
                "Fill month/day/year spinners or date segments separately.",
                "Use automation-id segments when Workday dateSection present.",
                "Tab or blur between segments to commit.",
            ],
            "verify": "all date segments populated",
        },
        "text_input": {
            "strategy": "text_input",
            "steps": [
                "locator.fill(value) on visible input/textarea.",
                "Skip if readback already matches (already_correct_skip).",
            ],
            "verify": "input_value readback contains intended text",
        },
        "checkbox": {
            "strategy": "checkbox",
            "steps": [
                "Check/uncheck via click or set_checked(true/false).",
                "Target input[type=checkbox] or associated label.",
            ],
            "verify": "is_checked() matches intended state",
        },
        "radio": {
            "strategy": "radio",
            "steps": [
                "Click label or input for intended radio option.",
                "Only one option in group should be selected.",
            ],
            "verify": "intended radio is checked",
        },
    }
    key = name if is_allowed_playbook(name) else "text_input"
    return dict(hints[key])


def _self_test() -> int:
    cases: list[tuple[dict, str]] = [
        ({"tag": "select"}, "native_select"),
        (
            {"role": "combobox", "class": "select__control react-select"},
            "react_select_portal",
        ),
        (
            {"role": "combobox", "typable": True, "label": "School"},
            "typable_commit",
        ),
        (
            {"role": "combobox", "platform": "greenhouse", "label": "Degree"},
            "typable_commit",
        ),
        (
            {"label": "How did you hear about us?", "platform": "workday"},
            "workday_how_heard",
        ),
        (
            {"automation_id": "dateSectionMonth", "platform": "workday"},
            "date_spinner",
        ),
        ({"tag": "input", "type": "checkbox"}, "checkbox"),
        ({"tag": "input", "type": "radio"}, "radio"),
        ({"tag": "input", "label": "First name"}, "text_input"),
    ]
    for meta, want in cases:
        got = detect_playbook(meta)
        assert got == want, f"detect_playbook({meta!r}) = {got!r}, want {want!r}"

    assert is_allowed_playbook("native_select")
    assert not is_allowed_playbook("free_form_click")

    hints = playbook_hints("react_select_portal")
    assert hints["strategy"] == "react_select_portal"
    assert "steps" in hints
    assert "PII" not in json.dumps(hints)

    print("playbooks self-test OK")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fastfill interaction playbooks")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--detect", metavar="JSON", help="Print detect_playbook for JSON meta")
    ap.add_argument("--hints", metavar="ID", help="Print playbook_hints for id")
    args = ap.parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    if args.detect:
        meta = json.loads(args.detect)
        print(json.dumps({"playbook": detect_playbook(meta)}, indent=2))
    elif args.hints:
        print(json.dumps(playbook_hints(args.hints), indent=2))
    else:
        ap.print_help()
        raise SystemExit(0)
