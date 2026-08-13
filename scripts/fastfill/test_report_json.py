#!/usr/bin/env python3
"""Unit tests: report JSON serialization (circular refs / live handles)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


class _FakeLocator:
    """Minimal stub mimicking Playwright Locator in report rows."""


class _FakePage:
    def __init__(self) -> None:
        self.locator = lambda _sel: _FakeLocator()


def test_report_for_json_strips_private_keys():
    from fast_fill import report_for_json

    out = report_for_json(
        {
            "never_submit": True,
            "url": "https://example.com",
            "_page": _FakePage(),
            "_attempt_log": object(),
        }
    )
    assert "_page" not in out
    assert "_attempt_log" not in out
    assert out["never_submit"] is True
    json.dumps(out)


def test_report_for_json_breaks_circular_ref():
    from fast_fill import report_for_json

    report: dict = {"never_submit": True, "filled": []}
    report["workday"] = {"phase_b": report}
    out = report_for_json(report)
    dumped = json.dumps(out, indent=2)
    assert "never_submit" in dumped
    assert "<circular>" in dumped
    assert "Circular reference" not in dumped


def test_report_for_json_strips_locator_stub():
    from fast_fill import report_for_json

    loc = _FakeLocator()
    report = {
        "never_submit": True,
        "filled": [{"type": "EMAIL", "readback": "a@b.com", "locator": loc}],
        "leftovers": [{"label": "Phone", "page_ref": _FakePage()}],
    }
    out = report_for_json(report)
    json.dumps(out)
    assert out["filled"][0]["locator"] == "<_FakeLocator>"
    assert out["leftovers"][0]["page_ref"] == "<_FakePage>"


def test_report_for_json_nested_step_report_cycle():
    from fast_fill import report_for_json

    parent: dict = {"never_submit": True, "platform": "workday"}
    child: dict = {"experiment": "workday_two_phase", "_step_report": parent}
    parent["workday_nested"] = child
    out = report_for_json(parent)
    json.dumps(out, indent=2)


if __name__ == "__main__":
    test_report_for_json_strips_private_keys()
    test_report_for_json_breaks_circular_ref()
    test_report_for_json_strips_locator_stub()
    test_report_for_json_nested_step_report_cycle()
    print("ok")
