#!/usr/bin/env python3
"""Unit tests: prefill thrash / verify honesty (no browser). Dummy-only."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fast_fill import (  # noqa: E402
    should_demote_claimed_text_fill,
    _value_matches_readback,
)
from field_attempt_log import FieldAttemptLog, attach_attempt_log  # noqa: E402
from resume_upload import report_has_verified_resume  # noqa: E402
from verified_select import is_uncommitted_filter_text, select_readback_ok  # noqa: E402


def test_should_demote_false_when_selector_missing():
    """Fragile GH selectors: no demote without live proof."""
    assert (
        should_demote_claimed_text_fill(
            sel_found=False,
            live_rb="",
            intended="Test",
            claimed_rb="Test",
        )
        is False
    )


def test_should_demote_false_when_committed_matches():
    """Committed readback matching intended → keep verified row."""
    assert (
        should_demote_claimed_text_fill(
            sel_found=True,
            live_rb="Yes",
            intended="Yes",
            claimed_rb="Yes",
        )
        is False
    )
    assert (
        should_demote_claimed_text_fill(
            sel_found=True,
            live_rb="University of Alabama",
            intended="University of Alabama",
            claimed_rb="University of Alabama",
        )
        is False
    )


def test_should_demote_true_when_live_empty():
    # Verified readback still matches — keep row (stale selector / required-empty lie).
    assert (
        should_demote_claimed_text_fill(
            sel_found=True,
            live_rb="",
            intended="Test",
            claimed_rb="Test",
            id_still_empty=True,
        )
        is False
    )
    assert (
        should_demote_claimed_text_fill(
            sel_found=True,
            live_rb="Type here...",
            intended="62701",
            claimed_rb="62701",
            id_still_empty=True,
        )
        is False
    )
    # SPA wipe: live empty and claimed also empty → demote.
    assert (
        should_demote_claimed_text_fill(
            sel_found=True,
            live_rb="",
            intended="Test",
            claimed_rb="",
            id_still_empty=True,
        )
        is True
    )
    assert (
        should_demote_claimed_text_fill(
            sel_found=True,
            live_rb="",
            intended="62701",
            claimed_rb="",
            id_still_empty=False,
        )
        is True
    )


def test_filter_text_not_committed_select():
    """Typed filter essay must not count as committed dropdown value."""
    essay = "Yes, I am currently based in Illinois (Springfield, IL)."
    assert is_uncommitted_filter_text(essay, essay)
    assert not select_readback_ok(essay, ["Yes", "No"], typed_frag=essay)
    assert select_readback_ok("Yes", ["Yes", "No"])
    # Uncommitted long filter must fail select_readback even if it contains "Yes"
    assert not select_readback_ok(essay, ["Yes", "No"], picked="")


def test_ingest_pass_skips_already_correct_leftovers(tmp_path):
    log = FieldAttemptLog(tmp_path, run_id="ingest_skip", url="https://x.com", platform="gh")
    report = {
        "_attempt_log": log,
        "filled": [],
        "leftovers": [
            {
                "type": "EMAIL",
                "label": "Email",
                "reason": "already_correct_skip",
                "skipped_already_correct": True,
                "flash_candidate": True,
            }
        ],
    }
    summary = log.ingest_pass(report, pass_i=1, phase="refill")
    assert summary["recorded_fail"] == 0
    assert log.fail_count_for(field_type="EMAIL", label="Email") == 0


def test_ingest_pass_counts_live_empty_demotion(tmp_path):
    log = FieldAttemptLog(tmp_path, run_id="ingest_dem", url="https://x.com", platform="gh")
    report = {
        "_attempt_log": log,
        "filled": [],
        "leftovers": [
            {
                "type": "NAME_FIRST",
                "label": "Preferred First Name",
                "reason": "live_empty_after_claimed_verified",
                "flash_candidate": True,
            }
        ],
    }
    log.ingest_pass(report, pass_i=1, phase="refill")
    assert log.fail_count_for(field_type="NAME_FIRST", label="Preferred First Name") == 1
    log.ingest_pass(report, pass_i=2, phase="refill")
    assert log.is_unfillable(field_type="NAME_FIRST", label="Preferred First Name")


def test_resume_verified_requires_readback():
    assert not report_has_verified_resume(
        {
            "filled": [
                {
                    "type": "RESUME_UPLOAD",
                    "mode": "file",
                    "verified": True,
                    "ok": True,
                    "readback": "",
                }
            ]
        }
    )
    assert report_has_verified_resume(
        {
            "filled": [
                {
                    "type": "RESUME_UPLOAD",
                    "mode": "file",
                    "verified": True,
                    "ok": True,
                    "readback": "dummy_resume.pdf",
                    "reason": "files_on_input",
                }
            ]
        }
    )


def test_filename_visible_filelist_empty_not_verified():
    """FILL3-011: filename chrome alone is not verified when FileList empty."""
    from resume_upload import autofill_filename_verify_ok, report_has_verified_resume

    assert autofill_filename_verify_ok(filename="resume.pdf") is True
    assert (
        autofill_filename_verify_ok(
            filename="resume.pdf", input_present=True, files_on_input=True
        )
        is True
    )
    assert (
        autofill_filename_verify_ok(
            filename="resume.pdf", input_present=True, files_on_input=False
        )
        is False
    )
    assert (
        autofill_filename_verify_ok(
            filename="resume.pdf", input_present=False, files_on_input=False
        )
        is True
    )

    assert not report_has_verified_resume(
        {
            "filled": [
                {
                    "type": "RESUME_UPLOAD",
                    "mode": "filename_visible",
                    "verified": False,
                    "ok": False,
                    "readback": "resume.pdf",
                    "reason": "filename_visible_filelist_empty",
                    "input_present": True,
                    "files_on_input": False,
                }
            ]
        }
    )
    assert not report_has_verified_resume(
        {
            "filled": [
                {
                    "type": "RESUME_UPLOAD",
                    "mode": "filename_visible",
                    "verified": True,
                    "ok": True,
                    "readback": "resume.pdf",
                    "input_present": True,
                    "files_on_input": False,
                }
            ]
        }
    )
    assert report_has_verified_resume(
        {
            "phase_a_resume": {
                "autofill_ready": {
                    "ready": True,
                    "filename": "resume.pdf",
                    "input_present": False,
                    "files_on_input": False,
                }
            }
        }
    )
    assert not report_has_verified_resume(
        {
            "phase_a_resume": {
                "autofill_ready": {
                    "ready": True,
                    "filename": "resume.pdf",
                    "input_present": True,
                    "files_on_input": False,
                }
            }
        }
    )


def test_unfillable_cap_two_fails(tmp_path):
    log = FieldAttemptLog(tmp_path, run_id="cap2", url="https://x.com", platform="gh")
    for i in range(2):
        log.record(
            field_type="SCHOOL",
            label="School*",
            success=False,
            error="gh_select_failed",
            pass_i=i + 1,
        )
    assert log.is_unfillable(field_type="SCHOOL", label="School*")
    assert log.unfillable_md.is_file()
    assert log.fixer_trigger.is_file()


def test_airwallex_location_already_correct_skip():
    """Springfield, Illinois, United States must not re-attempt when aliases match."""
    from verified_select import (
        location_display_matches,
        location_option_aliases,
    )
    from fast_fill import should_demote_claimed_text_fill

    shown = "Springfield, Illinois, United States"
    aliases = location_option_aliases(
        "Springfield", state="IL", state_full="Illinois", country="United States"
    )
    assert location_display_matches(shown, aliases, city="Springfield")
    assert not should_demote_claimed_text_fill(
        sel_found=True,
        live_rb=shown,
        intended="Springfield",
        claimed_rb=shown,
        field_type="ADDRESS_CITY",
    )


def test_ingest_skips_already_correct_keep_leftovers(tmp_path):
    from field_attempt_log import FieldAttemptLog

    log = FieldAttemptLog(tmp_path, run_id="keep_skip", url="https://x.com", platform="ashby")
    report = {
        "_attempt_log": log,
        "filled": [],
        "leftovers": [
            {
                "type": "ADDRESS_CITY",
                "label": "Location",
                "reason": "already_correct_keep",
                "flash_candidate": True,
            }
        ],
    }
    summary = log.ingest_pass(report, pass_i=1, phase="refill")
    assert summary["recorded_fail"] == 0


def test_attach_attempt_log_on_report():
    with tempfile.TemporaryDirectory() as td:
        report: dict = {"url": "https://example.com/jobs/1", "platform": "greenhouse"}
        log = attach_attempt_log(report, cycle_dir=td, run_id="attach_test")
        assert report.get("_attempt_log") is log
        assert Path(str(report["field_attempt_log_path"])).parent == Path(td)


def test_location_probe_committed_skips_demote():
    """Committed probe dict + display match → do not treat as live-empty demote."""
    from verified_select import (
        is_location_committed,
        location_option_aliases,
    )

    shown = "Springfield, Illinois, United States"
    aliases = location_option_aliases(
        "Springfield", state="IL", state_full="Illinois", country="United States"
    )
    probe = {
        "committed": is_location_committed(
            shown,
            aliases,
            city="Springfield",
            state="IL",
            state_full="Illinois",
            country="United States",
        ),
        "shown": shown,
    }
    assert probe["committed"]


def test_demote_probe_trusts_claimed_when_live_probe_misses():
    """Ashby demote false-negative: live empty but verified readback matches."""
    shown = "Springfield, Illinois, United States"
    assert not should_demote_claimed_text_fill(
        sel_found=True,
        live_rb="",
        intended="Springfield",
        claimed_rb=shown,
        field_type="ADDRESS_CITY",
    )
    salary = "Open / negotiable within the posted range"
    assert not should_demote_claimed_text_fill(
        sel_found=True,
        live_rb="",
        intended=salary,
        claimed_rb=salary,
        field_type="SALARY_EXPECTED",
    )
    li = "https://www.linkedin.com/in/test-dummy-000000000"
    assert not should_demote_claimed_text_fill(
        sel_found=True,
        live_rb="",
        intended=li,
        claimed_rb=li,
        field_type="LINKEDIN",
    )


def test_should_not_demote_when_id_still_empty_but_claimed_valid():
    """Stale required-empty scan must not demote when verified readback still matches."""
    salary = "Open / negotiable within the posted range"
    assert (
        should_demote_claimed_text_fill(
            sel_found=True,
            live_rb="",
            intended=salary,
            claimed_rb=salary,
            field_type="SALARY_EXPECTED",
            id_still_empty=True,
        )
        is False
    )
    li = "https://www.linkedin.com/in/test-dummy-000000000"
    assert (
        should_demote_claimed_text_fill(
            sel_found=True,
            live_rb="",
            intended=li,
            claimed_rb=li,
            field_type="LINKEDIN",
            id_still_empty=True,
        )
        is False
    )
    shown = "Springfield, Illinois, United States"
    assert (
        should_demote_claimed_text_fill(
            sel_found=True,
            live_rb="",
            intended="Springfield",
            claimed_rb=shown,
            field_type="ADDRESS_CITY",
            id_still_empty=True,
        )
        is False
    )


def main() -> int:
    test_should_demote_false_when_selector_missing()
    test_should_demote_false_when_committed_matches()
    test_should_demote_true_when_live_empty()
    test_filter_text_not_committed_select()
    test_airwallex_location_already_correct_skip()
    test_should_not_demote_when_id_still_empty_but_claimed_valid()
    test_location_probe_committed_skips_demote()
    test_demote_probe_trusts_claimed_when_live_probe_misses()
    with tempfile.TemporaryDirectory() as td:
        test_ingest_pass_skips_already_correct_leftovers(Path(td) / "skip")
        test_ingest_pass_counts_live_empty_demotion(Path(td) / "dem")
        test_unfillable_cap_two_fails(Path(td) / "cap")
        test_ingest_skips_already_correct_keep_leftovers(Path(td) / "keep")
    test_resume_verified_requires_readback()
    test_filename_visible_filelist_empty_not_verified()
    test_attach_attempt_log_on_report()
    print("test_prefill_thrash_verify: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
