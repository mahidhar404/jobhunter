#!/usr/bin/env python3
"""Characterization tests for dashboard/server.py refactors.

These pin the *current observable behavior* of the parts of
``_run_tailor_then_fill_body`` that can be exercised without standing up
subprocesses, a browser, or PartyRock — namely the initial skip / reuse
decision (job lookup, the "resume already on disk" fast path, and the
Test-Mode "skip PartyRock" fast path). They must pass against the code as it
was BEFORE any extract-method refactor, and again after, so any behavior drift
in that phase is caught.

The PartyRock-lock / tailor-subprocess / compile / page-fit / address-pick
middle of the function is intentionally NOT characterized here: it drives
subprocesses, a CDP browser, real file artifacts, and a module lock, so a
reliable characterization harness for it needs dedicated scaffolding. That
phase is therefore left un-decomposed (see the refactor report).

No real applicant PII is used — jobs are synthetic fixtures.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


def _base_patches(jobs, *, existing_resume, aborted=False):
    """Common leaf-function stubs; returns a dict of the recording mocks."""
    rec = {
        "read_jobs": mock.MagicMock(return_value={"jobs": jobs}),
        "write_jobs": mock.MagicMock(),
        "resolve_job_resume_file": mock.MagicMock(return_value=existing_resume),
        "clear_fill_activity": mock.MagicMock(),
        "append_fill_activity": mock.MagicMock(),
        "pipeline_milestone": mock.MagicMock(),
        "run_hybrid_fill_dummy": mock.MagicMock(),
        "_publish_resume_by_company": mock.MagicMock(return_value=None),
        "_job_fill_aborted": mock.MagicMock(return_value=aborted),
    }
    patches = [mock.patch.object(srv, name, m) for name, m in rec.items()]
    return rec, patches


def _run(rec_patches, **kwargs):
    _, patches = rec_patches
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8]:
        srv._run_tailor_then_fill_body(**kwargs)


def test_unknown_job_returns_without_side_effects():
    rp = _base_patches([], existing_resume=None)
    _run(rp, job_id="ghost", test_mode=True, skip_partyrock=True)
    rec = rp[0]
    rec["pipeline_milestone"].assert_not_called()
    rec["run_hybrid_fill_dummy"].assert_not_called()
    rec["clear_fill_activity"].assert_not_called()


def test_skip_partyrock_no_apply_url_goes_stuck_no_fill():
    job = {"id": "j1", "apply_url": "", "job_url": ""}
    rp = _base_patches([job], existing_resume=None)
    _run(rp, job_id="j1", test_mode=True, skip_partyrock=True)
    rec = rp[0]
    # Enters the skip branch, clears activity, then stucks on missing apply_url.
    rec["clear_fill_activity"].assert_called_once_with("j1")
    rec["run_hybrid_fill_dummy"].assert_not_called()
    ms = rec["pipeline_milestone"].call_args
    assert ms.kwargs.get("status") == "stuck"
    assert ms.kwargs.get("event") == "error"


def test_skip_partyrock_test_mode_no_resume_hands_to_fill():
    job = {"id": "j1", "apply_url": "https://jobs.example.com/apply"}
    rp = _base_patches([job], existing_resume=None)
    _run(rp, job_id="j1", test_mode=True, skip_partyrock=True, restore_status=None)
    rec = rp[0]
    rec["run_hybrid_fill_dummy"].assert_called_once()
    ck = rec["run_hybrid_fill_dummy"].call_args
    assert ck.args == ("j1",)
    assert ck.kwargs["test_mode"] is True
    assert ck.kwargs["headed"] is True
    assert ck.kwargs["preserve_activity"] is True
    assert ck.kwargs["restore_status"] == srv._dummy_restore_status("discovered")
    # No address is picked on the Test-Mode skip path.
    assert "address_text" not in ck.kwargs


def test_existing_resume_skips_partyrock_and_fills():
    job = {"id": "j1", "apply_url": "https://jobs.example.com/apply"}
    resume = srv.ROOT / "resumes" / "j1" / "resume.pdf"
    rp = _base_patches([job], existing_resume=resume)
    _run(rp, job_id="j1", test_mode=True, skip_partyrock=False)
    rec = rp[0]
    # resume_path persisted under lock, then handed to the fill engine.
    rec["write_jobs"].assert_called()
    rec["_publish_resume_by_company"].assert_called_once()
    rec["run_hybrid_fill_dummy"].assert_called_once()
    ms_events = [c.kwargs.get("event") for c in rec["pipeline_milestone"].call_args_list]
    assert "resume" in ms_events


def test_abort_before_fill_short_circuits():
    job = {"id": "j1", "apply_url": "https://jobs.example.com/apply"}
    rp = _base_patches([job], existing_resume=None, aborted=True)
    _run(rp, job_id="j1", test_mode=True, skip_partyrock=True)
    rec = rp[0]
    rec["run_hybrid_fill_dummy"].assert_not_called()


if __name__ == "__main__":
    test_unknown_job_returns_without_side_effects()
    test_skip_partyrock_no_apply_url_goes_stuck_no_fill()
    test_skip_partyrock_test_mode_no_resume_hands_to_fill()
    test_existing_resume_skips_partyrock_and_fills()
    test_abort_before_fill_short_circuits()
    print("OK test_server_refactor")
