#!/usr/bin/env python3
"""Unit tests: Ashby _systemfield_resume ghost empty filtering (no browser)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from resume_upload import (  # noqa: E402
    filter_resume_leftovers,
    filter_resume_required_empties,
    is_resume_empty_required,
    report_has_verified_resume,
)


def test_is_resume_empty_required_systemfield():
    assert is_resume_empty_required(
        {"id": "_systemfield_resume", "reason": "empty_required_file"}
    )
    assert is_resume_empty_required(
        {"id": "_systemfield_resume", "reason": "empty_resume_file"}
    )
    assert not is_resume_empty_required(
        {"id": "email", "reason": "empty_required_input"}
    )


def test_filter_resume_required_empties_when_verified():
    empties = [
        {"id": "_systemfield_resume", "reason": "empty_required_file"},
        {"id": "_systemfield_resume", "reason": "empty_resume_file"},
        {"id": "linkedin", "reason": "empty_ashby_linkedin"},
    ]
    out = filter_resume_required_empties(empties, resume_verified=True)
    assert len(out) == 1
    assert out[0]["id"] == "linkedin"


def test_filter_resume_leftovers():
    leftovers = [
        {"label": "_systemfield_resume", "reason": "live_required_empty:empty_required_file"},
        {"label": "LinkedIn URL", "type": "LINKEDIN", "reason": "live_empty"},
    ]
    out = filter_resume_leftovers(leftovers)
    assert len(out) == 1
    assert out[0]["label"] == "LinkedIn URL"


def test_report_has_verified_resume_ashby_ui():
    assert report_has_verified_resume(
        {
            "filled": [
                {
                    "type": "RESUME_UPLOAD",
                    "verified": True,
                    "ok": True,
                    "readback": "dummy.pdf",
                    "reason": "ashby_upload_ui",
                }
            ]
        }
    )


if __name__ == "__main__":
    test_is_resume_empty_required_systemfield()
    test_filter_resume_required_empties_when_verified()
    test_filter_resume_leftovers()
    test_report_has_verified_resume_ashby_ui()
    print("test_ashby_resume_systemfield: OK")
