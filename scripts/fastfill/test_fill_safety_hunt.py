#!/usr/bin/env python3
"""Unit tests for FILL-001…008 hunt fixes (cookie FINAL, phone-ext Flash, resume, Ready)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _vision_ok() -> dict:
    return {
        "complete": True,
        "verdict": "COMPLETE",
        "empty_fields": [],
        "never_submit": True,
        "submit_clicked": False,
    }


def test_cookie_refuses_final_submit():
    """FILL-001: cookie path must never allow 'I Agree and Submit' FINAL."""
    from button_map import FINAL
    from fast_fill import cookie_control_safe_to_click

    for label in (
        "I Agree and Submit",
        "Agree and Submit",
        "Submit Application",
        "Submit",
        "Review and Submit",
    ):
        gate = cookie_control_safe_to_click(label)
        assert gate.get("ok") is False, label
        assert gate.get("kind") == FINAL or "submit" in str(gate.get("reason") or "").lower()


def test_cookie_refuses_eeo_decline():
    """FILL-002: bare/EEO Decline must not be clicked via cookie dismiss."""
    from fast_fill import cookie_control_safe_to_click

    for label in (
        "Decline",
        "Decline to self identify",
        "Decline To Self Identify",
        "I decline to self-identify",
        "Prefer not to disclose",
    ):
        gate = cookie_control_safe_to_click(label)
        assert gate.get("ok") is False, label


def test_cookie_allows_exact_cookie_labels():
    from fast_fill import cookie_control_safe_to_click

    for label in (
        "Reject all",
        "Accept cookies",
        "Necessary only",
        "Got it",
        "Decline all",
        "Decline cookies",
    ):
        gate = cookie_control_safe_to_click(label)
        assert gate.get("ok") is True, (label, gate)


def test_phone_extension_flash_forbidden():
    """FILL-003: empty PHONE_EXTENSION must not enter Flash/Skyvern handoff."""
    from fill_attribution import is_flash_forbidden_type
    from flash_leftovers import (
        build_leftovers_handoff,
        is_deterministic_leftover,
        partition_flash_leftovers,
    )

    assert is_flash_forbidden_type("PHONE_EXTENSION", label="Phone Extension") is True
    assert is_flash_forbidden_type("", label="Phone Extension") is True

    row = {
        "label": "Phone Extension",
        "type": "PHONE_EXTENSION",
        "reason": "empty",
        "flash_candidate": True,
    }
    assert is_deterministic_leftover(row, values={"PHONE_EXTENSION": ""}) is True
    parts = partition_flash_leftovers([row], values={"PHONE_EXTENSION": ""})
    assert not any(
        r.get("label") == "Phone Extension" for r in parts["flash_leftovers"]
    )
    assert any(
        r.get("label") == "Phone Extension" for r in parts["deferred_deterministic"]
    )

    report = {
        "dummy": True,
        "filled": [],
        "leftovers": [row, {"label": "Cover letter", "type": "COVER_LETTER", "essay": True}],
        "url": "https://example.com/apply",
        "platform": "unknown",
    }
    handoff = build_leftovers_handoff(report, grounded=True)
    assert not any(
        str(r.get("type") or "").upper() == "PHONE_EXTENSION"
        or "extension" in str(r.get("label") or "").lower()
        for r in handoff.get("leftovers") or []
    )
    prompt = handoff.get("prompt") or ""
    # Phone Extension must not appear as a leftover line to answer
    assert "Phone Extension" not in prompt or "ALREADY FILLED" in prompt


def test_phone_extension_pattern_requires_phone_context():
    """FILL-004: bare 'extension' in non-phone labels must not classify as PHONE_EXTENSION."""
    from field_map import PHONE_EXTENSION, classify_field, is_phone_extension_field

    ftype, _ = classify_field(
        {"label": "Phone Extension", "name": "extension", "id": "", "placeholder": ""}
    )
    assert ftype == PHONE_EXTENSION

    ftype2, _ = classify_field(
        {
            "label": "Contract extension date",
            "name": "contract_ext",
            "id": "",
            "placeholder": "",
        }
    )
    assert ftype2 != PHONE_EXTENSION

    ftype3, _ = classify_field(
        {"label": "File extension", "name": "file_ext", "id": "", "placeholder": ""}
    )
    assert ftype3 != PHONE_EXTENSION

    assert is_phone_extension_field("Phone Extension") is True
    assert is_phone_extension_field("Extension") is True  # whole-label / name=extension
    assert is_phone_extension_field("Contract extension date") is False


def test_ready_without_vision_refused():
    """FILL-005: can_claim_ready fail-closed without vision_judge_live."""
    from page_progress import can_claim_ready, finalize_ready_flag, vision_blocks_ready

    clean = {
        "verdict": "SUCCESS",
        "blocker": None,
        "leftovers": [],
        "required_empty_after_fill": [],
        "required_empty_before_advance": [],
        "advanced_incomplete": False,
        "validation_after_advance": False,
    }
    assert vision_blocks_ready(clean) is True
    assert can_claim_ready(clean) is False

    clean["ready_for_review"] = True
    finalize_ready_flag(clean)
    assert clean["ready_for_review"] is False
    assert clean.get("ready_claim_refused") is True

    clean2 = {**clean, "ready_for_review": False, "vision_judge_live": _vision_ok()}
    clean2.pop("ready_claim_refused", None)
    assert can_claim_ready(clean2) is True


def test_resume_pdf_dummy_refuses_job_scoped(tmp_path):
    """FILL-007: test/dummy mode never accepts resumes/<id> PDF."""
    from field_map import RESUME_UPLOAD
    from resume_upload import resume_pdf_from_values

    pdf = tmp_path / "resumes" / "some-job-id" / "resume.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4 dummy")
    prev = {
        k: os.environ.get(k)
        for k in ("FASTFILL_ALLOW_REAL", "FASTFILL_REAL_PROFILE", "TEST_MODE")
    }
    try:
        os.environ.pop("FASTFILL_ALLOW_REAL", None)
        os.environ["FASTFILL_REAL_PROFILE"] = "0"
        os.environ["TEST_MODE"] = "1"
        raised = False
        try:
            resume_pdf_from_values({RESUME_UPLOAD: str(pdf)})
        except RuntimeError as e:
            raised = True
            assert "dummy" in str(e).lower() or "refuse" in str(e).lower()
        assert raised, "expected refuse of job-scoped resume in dummy mode"
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_eeo_validate_refuses_invent():
    """FILL-006: LLM EEO answers beyond SHARED catalog are refused."""
    from flash_leftovers import validate_eeo_against_catalog

    assert validate_eeo_against_catalog("GENDER", "Male") == "Male"
    assert validate_eeo_against_catalog("GENDER", "Female") == "Male"  # refuse invent
    assert "decline" in validate_eeo_against_catalog("RACE", "Asian").lower()
    assert "disability" in validate_eeo_against_catalog(
        "DISABILITY", "I have a disability"
    ).lower() or validate_eeo_against_catalog(
        "DISABILITY", "I have a disability"
    ).startswith("I do not")


def test_fill2_male_not_soft_match_female():
    """FILL2-001: Male must not soft-match Female via substring."""
    from gh_select import _score_option, aliases_for

    assert _score_option("Female", "Male") == 0
    assert _score_option("Woman", "Man") == 0
    options = ["Female", "Non-binary", "Other"]
    best_s, picked = 0, None
    for lab in options:
        for alias in aliases_for("GENDER", "Male"):
            s = _score_option(lab, alias)
            if s > best_s:
                best_s, picked = s, lab
    assert best_s < 50 or picked is None
    # With Male present, exact wins
    assert _score_option("Male", "Male") == 100


def test_fill2_cookie_refuses_ambiguous_short():
    """FILL2-007: bare Reject/Agree/OK must not dismiss cookies."""
    from fast_fill import cookie_control_safe_to_click

    for label in ("Reject", "Agree", "OK", "Yes", "No"):
        gate = cookie_control_safe_to_click(label)
        assert gate.get("ok") is False, label


def test_fill2_essay_not_url_fields():
    """FILL2-004: URL/link leftover labels are not essays."""
    from page_progress import is_essay_leftover

    for lab in (
        "Additional LinkedIn URL",
        "GitHub URL",
        "Portfolio URL",
        "Provide a link to your portfolio",
        "Other website",
    ):
        assert is_essay_leftover({"label": lab}) is False, lab
    assert is_essay_leftover({"label": "Why do you want to work here?"}) is True
    assert is_essay_leftover({"label": "Cover letter"}) is True


def test_fill2_phone_ext_selector_flash_forbidden():
    """FILL2-005: name/selector-only phone-ext must not reach Flash handoff."""
    from fill_attribution import is_flash_forbidden_type
    from flash_leftovers import build_leftovers_handoff, partition_flash_leftovers
    from field_map import is_phone_extension_field

    assert is_phone_extension_field(
        "?", name="extension", selector='input[name=extension]'
    )
    assert is_flash_forbidden_type(
        "", label="?", name="extension", selector='input[name="extension"]'
    )
    row = {
        "type": None,
        "label": "?",
        "name": "extension",
        "selector": "input[name=extension]",
        "flash_candidate": True,
    }
    parts = partition_flash_leftovers([row], values={})
    assert parts["flash_count"] == 0
    assert any(
        (r.get("name") == "extension" or "extension" in str(r.get("selector") or ""))
        for r in parts["deferred_deterministic"]
    )
    handoff = build_leftovers_handoff(
        {
            "dummy": True,
            "filled": [],
            "leftovers": [row],
            "url": "https://example.com/apply",
            "platform": "unknown",
        },
        grounded=True,
    )
    assert not any(
        "extension" in str(r.get("selector") or "").lower()
        or r.get("name") == "extension"
        for r in handoff.get("leftovers") or []
    )


def test_fill2_form_gaps_fail_closed():
    """FILL2-003: collect_form_gaps evaluate error must block Ready."""
    import asyncio
    from form_gaps import collect_form_gaps, gaps_block_ready

    class _BoomPage:
        async def evaluate(self, *_a, **_k):
            raise RuntimeError("dom_eval_failed")

    gaps = asyncio.run(collect_form_gaps(_BoomPage()))
    assert gaps and gaps[0].get("reason") == "probe_error"
    assert gaps_block_ready(gaps) is True


def test_fill2_skyvern_holds_eeo():
    """FILL2-006: Skyvern invoke path holds EEO for catalog/inpage only."""
    import asyncio
    from flash_leftovers import run_flash_leftovers

    report = {
        "dummy": True,
        "filled": [],
        "leftovers": [
            {"label": "Gender", "type": "GENDER", "flash_candidate": True},
            {"label": "Cover letter", "type": "COVER_LETTER", "essay": True},
        ],
        "url": "https://example.com/apply",
        "platform": "greenhouse",
    }

    payload = asyncio.run(
        run_flash_leftovers("https://example.com/apply", report, invoke=False)
    )
    types = {str(r.get("type") or "").upper() for r in payload.get("leftovers") or []}
    assert "GENDER" not in types
    held = payload.get("eeo_held_for_catalog") or []
    assert any(str(r.get("type") or "").upper() == "GENDER" for r in held)
    # Essay remains for Flash
    assert any(
        str(r.get("type") or "").upper() == "COVER_LETTER"
        for r in payload.get("leftovers") or []
    )


def main() -> int:
    test_cookie_refuses_final_submit()
    test_cookie_refuses_eeo_decline()
    test_cookie_allows_exact_cookie_labels()
    test_phone_extension_flash_forbidden()
    test_phone_extension_pattern_requires_phone_context()
    test_ready_without_vision_refused()
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        test_resume_pdf_dummy_refuses_job_scoped(Path(td))
    test_eeo_validate_refuses_invent()
    test_fill2_male_not_soft_match_female()
    test_fill2_cookie_refuses_ambiguous_short()
    test_fill2_essay_not_url_fields()
    test_fill2_phone_ext_selector_flash_forbidden()
    test_fill2_form_gaps_fail_closed()
    test_fill2_skyvern_holds_eeo()
    print("test_fill_safety_hunt: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
