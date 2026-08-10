#!/usr/bin/env python3
"""Resume upload lock: no re-upload thrash after commit-verify (dummy-only).

Covers Workday-class remount (empty FileList + filename chrome) and force=True
must not bypass lock/verify when attachment still shows.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from field_lock import (  # noqa: E402
    FieldLockSession,
    analyze_step_log_waste,
    attach_field_locks,
    lock_verified_field,
)
from resume_upload import (  # noqa: E402
    RESUME_UPLOAD,
    ensure_resume_uploaded,
    lock_resume_after_verify,
    page_shows_resume_attachment,
    report_has_verified_resume,
    should_skip_resume_reupload,
)


def test_should_skip_when_verified_even_if_force_and_empty_filelist() -> None:
    probe = {
        "present": True,
        "empty": True,
        "uploaded_ui": True,
        "workday_uploaded_ui": True,
    }
    skip, reason = should_skip_resume_reupload(
        already_verified=True,
        locked=False,
        force=True,
        probe=probe,
    )
    assert skip is True
    assert reason == "force_ignored_already_verified"


def test_should_skip_locked_over_force() -> None:
    skip, reason = should_skip_resume_reupload(
        already_verified=False,
        locked=True,
        force=True,
        probe={"present": True, "empty": True},
    )
    assert skip is True
    assert reason == "field_locked_skip"


def test_should_skip_verified_without_force_even_if_probe_empty() -> None:
    """Workday remount: FileList empty but report already verified → skip."""
    skip, reason = should_skip_resume_reupload(
        already_verified=True,
        locked=False,
        force=False,
        probe={"present": True, "empty": True},
    )
    assert skip is True
    assert reason == "already_verified"


def test_force_allows_reupload_only_after_genuine_wipe() -> None:
    skip, reason = should_skip_resume_reupload(
        already_verified=True,
        locked=False,
        force=True,
        probe={"present": True, "empty": True},  # no uploaded_ui
        page_hint=False,
    )
    assert skip is False
    assert reason == ""


def test_page_shows_resume_attachment_workday_ui() -> None:
    assert page_shows_resume_attachment(
        {"present": True, "empty": True, "workday_uploaded_ui": True}
    )
    assert not page_shows_resume_attachment(
        {"present": True, "empty": True}
    )


def test_resume_singleton_lock_blocks_other_selectors() -> None:
    s = FieldLockSession()
    s.lock(
        field_type="RESUME_UPLOAD",
        automation_id="file-upload-input-ref",
        readback="dummy.pdf",
        via="ensure_resume",
    )
    g = s.gate(
        field_type="RESUME_UPLOAD",
        selector="input[type=file]",
        label="Resume / CV",
    )
    assert g["action"] == "lock_skip"
    assert g.get("singleton_type") == "RESUME_UPLOAD"
    assert s.thrash_retouches == 1


def test_lock_resume_after_verify_sets_type_lock() -> None:
    report: dict = {}
    attach_field_locks(report)
    lock_resume_after_verify(report, readback="dummy.pdf", via="test")
    assert report_has_verified_resume(
        {
            "filled": [
                {
                    "type": RESUME_UPLOAD,
                    "verified": True,
                    "ok": True,
                    "readback": "dummy.pdf",
                    "reason": "files_on_input",
                }
            ]
        }
    )
    from field_lock import get_field_locks

    sess = get_field_locks(report)
    assert sess is not None
    assert sess.is_locked(field_type="RESUME_UPLOAD", selector="input[type=file]")


def test_analyze_step_log_counts_duplicate_resume_uploads() -> None:
    waste = analyze_step_log_waste(
        [
            {
                "step": 1,
                "ts": "2026-08-10T08:00:00Z",
                "action": "upload_resume",
                "field_type": "RESUME_UPLOAD",
                "label": "Resume",
                "via": "ensure_resume",
            },
            {
                "step": 2,
                "ts": "2026-08-10T08:00:01Z",
                "action": "upload_resume",
                "field_type": "RESUME_UPLOAD",
                "label": "Resume",
                "via": "ensure_resume",
            },
            {
                "step": 3,
                "ts": "2026-08-10T08:00:02Z",
                "action": "upload_resume_start",
                "field_type": "RESUME_UPLOAD",
                "label": "Resume",
                "via": "phase_c",
            },
        ]
    )
    assert waste["resume_upload_attempts"] == 3
    assert waste["duplicate_fills"]
    assert waste["waste_score"] >= 2


async def _fake_ensure_skips_upload() -> None:
    """ensure_resume_uploaded must not call upload when already verified + force."""
    report: dict = {
        "filled": [
            {
                "type": RESUME_UPLOAD,
                "mode": "file",
                "verified": True,
                "ok": True,
                "readback": "dummy_resume_run_x.pdf",
                "reason": "filename_visible_ui",
                "automation_id": "file-upload-input-ref",
            }
        ]
    }
    attach_field_locks(report)
    lock_verified_field(
        report,
        field_type=RESUME_UPLOAD,
        automation_id="file-upload-input-ref",
        readback="dummy_resume_run_x.pdf",
        via="prior",
    )
    page = MagicMock()
    page.evaluate = AsyncMock(
        return_value={
            "present": True,
            "empty": True,
            "uploaded_ui": True,
            "workday_uploaded_ui": True,
            "selectors": ["workday_upload_ui"],
        }
    )
    page.url = "https://walmart.wd504.myworkdayjobs.com/x"
    # probe_resume_field uses page.evaluate with the probe JS; filename hint too
    async def _eval(js, *args):
        if "needle" in str(js) or (args and args[0]):
            return True
        return {
            "present": True,
            "empty": True,
            "uploaded_ui": True,
            "workday_uploaded_ui": True,
            "selectors": ["workday_upload_ui"],
        }

    page.evaluate = AsyncMock(side_effect=_eval)

    with patch(
        "resume_upload.upload_resume_to_page", new_callable=AsyncMock
    ) as up:
        with patch("resume_upload.resume_pdf_from_values") as pdf:
            pdf.return_value = MagicMock(
                name="dummy_resume_run_x.pdf",
                is_file=MagicMock(return_value=True),
            )
            pdf.return_value.name = "dummy_resume_run_x.pdf"
            # probe_resume_field is async evaluate — patch probe directly
            with patch(
                "resume_upload.probe_resume_field",
                new_callable=AsyncMock,
                return_value={
                    "present": True,
                    "empty": True,
                    "uploaded_ui": True,
                    "workday_uploaded_ui": True,
                    "selectors": ["workday_upload_ui"],
                },
            ):
                out = await ensure_resume_uploaded(
                    page, {}, report, force=True
                )
        assert out.get("attempted") is False
        assert out.get("skipped") in (
            "force_ignored_already_verified",
            "field_locked_skip",
            "already_verified",
        )
        up.assert_not_called()


def test_ensure_resume_force_does_not_reupload_when_verified() -> None:
    asyncio.run(_fake_ensure_skips_upload())


def test_phase_c_skip_helper_matches_report() -> None:
    """Sparse My Experience retry must see already_verified → skip."""
    report = {
        "filled": [
            {
                "type": "RESUME_UPLOAD",
                "verified": True,
                "ok": True,
                "readback": "dummy.pdf",
                "reason": "filename_visible_ui",
            }
        ]
    }
    assert report_has_verified_resume(report)
    skip, reason = should_skip_resume_reupload(
        already_verified=True,
        locked=False,
        force=False,
        probe={"present": True, "empty": True, "workday_uploaded_ui": True},
    )
    assert skip and reason == "already_verified"


if __name__ == "__main__":
    test_should_skip_when_verified_even_if_force_and_empty_filelist()
    test_should_skip_locked_over_force()
    test_should_skip_verified_without_force_even_if_probe_empty()
    test_force_allows_reupload_only_after_genuine_wipe()
    test_page_shows_resume_attachment_workday_ui()
    test_resume_singleton_lock_blocks_other_selectors()
    test_lock_resume_after_verify_sets_type_lock()
    test_analyze_step_log_counts_duplicate_resume_uploads()
    test_ensure_resume_force_does_not_reupload_when_verified()
    test_phase_c_skip_helper_matches_report()
    print("test_resume_upload_lock: OK")
