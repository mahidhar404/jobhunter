#!/usr/bin/env python3
"""Unit tests: GH job-boards resume upload helpers (no browser). Dummy-only."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from gh_select import is_post_resume_reassert_via  # noqa: E402
from resume_upload import (  # noqa: E402
    gh_upload_filename_visible,
    is_resume_attachment_row,
    report_has_verified_resume,
)


def test_gh_upload_filename_visible():
    assert gh_upload_filename_visible("Resume/CV*\n\ndummy_resume_de.pdf")
    assert gh_upload_filename_visible("Attached: test_applicant.pdf")
    assert not gh_upload_filename_visible("Resume/CV*\nAttach\nAttach\nDropbox")
    assert not gh_upload_filename_visible("")


def test_is_resume_attachment_row():
    assert is_resume_attachment_row({"type": "RESUME_UPLOAD", "mode": "file"})
    assert is_resume_attachment_row({"type": "NAME_FIRST", "mode": "file"})
    assert not is_resume_attachment_row(
        {
            "type": "NAME_FIRST",
            "mode": "fill",
            "via": "greenhouse_post_resume_reassert",
        }
    )
    assert not is_resume_attachment_row(
        {"type": "EMAIL", "via": "greenhouse_post_resume_reassert"}
    )


def test_post_resume_reassert_via_not_resume_row():
    assert is_post_resume_reassert_via("greenhouse_post_resume_reassert")
    assert is_post_resume_reassert_via("greenhouse_reassert")
    assert not is_post_resume_reassert_via("ensure_resume")
    assert not is_post_resume_reassert_via("gh_select")


def test_report_has_verified_resume_gh_ui_reason():
    assert report_has_verified_resume(
        {
            "filled": [
                {
                    "type": "RESUME_UPLOAD",
                    "mode": "file",
                    "verified": True,
                    "ok": True,
                    "readback": "dummy_resume_de.pdf",
                    "reason": "gh_upload_ui",
                }
            ]
        }
    )
    assert not report_has_verified_resume(
        {
            "filled": [
                {
                    "type": "NAME_FIRST",
                    "via": "greenhouse_post_resume_reassert",
                    "verified": True,
                    "readback": "Test",
                }
            ]
        }
    )


def test_demote_resume_probe_skips_reassert_rows():
    """Regression: greenhouse_post_resume_reassert must not match resume demotion."""
    row = {
        "type": "EMAIL",
        "via": "greenhouse_post_resume_reassert",
        "mode": "fill",
    }
    assert not is_resume_attachment_row(row)


if __name__ == "__main__":
    test_gh_upload_filename_visible()
    test_is_resume_attachment_row()
    test_post_resume_reassert_via_not_resume_row()
    test_report_has_verified_resume_gh_ui_reason()
    test_demote_resume_probe_skips_reassert_rows()
    print("test_resume_upload_gh: OK")
