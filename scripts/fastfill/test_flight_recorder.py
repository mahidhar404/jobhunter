#!/usr/bin/env python3
"""Unit tests for flight_recorder — fake sequence write + readback."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def test_flight_record_and_readback(tmp_path: Path | None = None) -> None:
    from flight_recorder import (
        FlightRecorder,
        attach_flight_recorder,
        flight_enabled,
        note_flight,
        redact_intent,
    )

    td = tmp_path or Path(tempfile.mkdtemp())
    rec = FlightRecorder(
        td, run_id="unit1", url="https://example.com/apply", platform="workday", headed=True
    )
    rec.set_context(page="contact", layer="pack")
    rec.record(
        "gate",
        action="skip",
        field_type="PHONE",
        automation_id="phone",
        intent="555-0199",
        gate_kind="lock_skip",
        gate_result="skip",
        gate_reason="field_locked_skip",
        readback="555-0199",
        stream=False,
    )
    rec.record(
        "fill",
        action="fill_text",
        field_type="ADDRESS_LINE1",
        label="Address Line 1",
        intent="123 Main St",
        gate_kind="commit_fill",
        gate_result="OK",
        gate_reason="verified",
        readback="123 Main St",
        layer="workday_contact",
        stream=False,
    )
    rec.record(
        "advance",
        action="STOP",
        advance_decision="STOP",
        advance_reason="empty_cycle",
        gate_kind="budgeted_progress",
        gate_result="STOP",
        gate_reason="empty_cycle",
        stream=False,
    )
    events = rec.read_events()
    assert len(events) == 3
    assert events[0]["field"]["automation_id"] == "phone"
    assert events[0]["gate"]["kind"] == "lock_skip"
    assert events[1]["intent"] == "123 Main St"
    assert events[2]["advance"]["reason"] == "empty_cycle"
    log = rec.log_path.read_text(encoding="utf-8")
    assert "[flight 0001]" in log
    assert "empty_cycle" in log
    assert "ADDRESS_LINE1" in log or "Address Line 1" in log

    # Redaction
    assert "{{EMAIL}}" in (redact_intent("alice@example.com") or "")

    # headed default ON / explicit OFF
    prev = os.environ.pop("FASTFILL_FLIGHT", None)
    try:
        assert flight_enabled(headed=True) is True
        assert flight_enabled(headed=False) is False
        os.environ["FASTFILL_FLIGHT"] = "0"
        assert flight_enabled(headed=True) is False
        os.environ["FASTFILL_FLIGHT"] = "1"
        assert flight_enabled(headed=False) is True
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_FLIGHT", None)
        else:
            os.environ["FASTFILL_FLIGHT"] = prev

    report = {
        "url": "https://example.com/apply",
        "headed": True,
        "alias_token": "tok",
        "platform": "workday",
    }
    attached = attach_flight_recorder(report, out_dir=td / "run2", force=True)
    assert attached is not None
    note_flight(
        report,
        "pack_miss",
        action="STOP",
        page="contact",
        layer="workday_contact",
        advance_decision="STOP",
        advance_reason="pack_incomplete",
        gate_kind="pack",
        gate_result="miss",
        stream=False,
    )
    assert any(
        e.get("advance", {}).get("reason") == "pack_incomplete"
        for e in attached.read_events()
    )
    assert report.get("flight_jsonl_path")
    assert Path(report["flight_log_path"]).is_file()


def test_note_flight_noop_when_disabled() -> None:
    from flight_recorder import note_flight

    prev = os.environ.get("FASTFILL_FLIGHT")
    os.environ["FASTFILL_FLIGHT"] = "0"
    try:
        report = {"url": "https://x.test", "headed": True, "alias_token": "z"}
        assert note_flight(report, "gate", action="skip", stream=False) is None
        assert report.get("_flight_recorder") is None
    finally:
        if prev is None:
            os.environ.pop("FASTFILL_FLIGHT", None)
        else:
            os.environ["FASTFILL_FLIGHT"] = prev


if __name__ == "__main__":
    test_flight_record_and_readback()
    test_note_flight_noop_when_disabled()
    print("test_flight_recorder: OK")
