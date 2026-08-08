#!/usr/bin/env python3
"""Page / step progress tracking for multipage autofill honesty.

Detects "thought we finished but still on page 1": Next existed (or ADVANCE
clicked) yet URL/step fingerprint did not change after a complete-fill attempt.

Pure helpers — Playwright is optional (only ``capture_step_fingerprint`` needs a page).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlparse


# Essay / cover-letter / open-narrative leftovers — Flash must answer these
# grounded in dummy resume + DUMMY_PROFILE + scraped JD (cycle leftover mode).
_ESSAY_TYPE_KEYS = frozenset({
    "COVER_LETTER",
    "cover_letter",
    "ESSAY",
    "essay",
    "MOTIVATION",
    "motivation",
})

_ESSAY_LABEL_RE = re.compile(
    r"\b(?:essay|cover[\s_-]*letter|motivation[\s_-]*letter|"
    r"tell[\s_-]*us[\s_-]*about|describe\b|explain\b|"
    r"why[\s_-]*do[\s_-]*you[\s_-]*want|pros[\s_-]*and[\s_-]*cons)\b",
    re.I,
)
# FILL2-004: URL/link/portfolio fields are not essays (even if "additional … URL").
_URL_LINK_FIELD_RE = re.compile(
    r"\b(?:url|urls|link|links|website|linkedin|github|portfolio|homepage)\b",
    re.I,
)

STEP_HINT_JS = """() => {
  // Prefer Workday step containers — URL/title often unchanged across SPA pages.
  const pageIds = [
    'contactInformationPage',
    'applyFlowMyInfoPage',
    'myExperiencePage',
    'applicationQuestionsPage',
    'voluntaryDisclosuresPage',
    'selfIdentificationPage',
    'reviewPage',
    'mainContent',
  ];
  for (const id of pageIds) {
    const el = document.querySelector(`[data-automation-id="${id}"]`);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const t = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    return id + (t ? (':' + t) : '');
  }
  const sels = [
    '[data-automation-id="pageHeader"]',
    '[data-automation-id="wizardProgress"]',
    '[aria-current="step"]',
    '[data-automation-id="stepHeader"]',
    'h1', 'h2',
  ];
  for (const s of sels) {
    const el = document.querySelector(s);
    const t = (el && (el.innerText || el.textContent) || '').trim();
    if (t) return t.slice(0, 120);
  }
  // Visible primary section label as last resort
  const prog = document.querySelector('[data-automation-id="progressBar"]');
  if (prog) {
    const t = (prog.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 120);
    if (t) return 'progress:' + t;
  }
  return '';
}"""


def step_fingerprint(url: str, *, title: str = "", step_hint: str = "") -> str:
    """Navigation fingerprint for stuck-page detection (URL + title + step).

    Distinct from ``record_replay.page_fingerprint`` (tenant replay key): this
    includes query and on-page step text so SPA multipage flows that keep the
    same path still register a change.
    """
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
    path = parsed.path or "/"
    query = parsed.query or ""
    title_n = " ".join((title or "").strip().lower().split())[:160]
    step_n = " ".join((step_hint or "").strip().lower().split())[:160]
    raw = f"{host}|{path}|{query}|{title_n}|{step_n}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


async def capture_step_fingerprint(page) -> dict[str, str]:
    """Live URL/title/step fingerprint for stuck-page detection."""
    url = ""
    title = ""
    step_hint = ""
    try:
        url = page.url or ""
    except Exception:
        pass
    try:
        title = await page.title()
    except Exception:
        pass
    try:
        step_hint = await page.evaluate(STEP_HINT_JS) or ""
    except Exception:
        pass
    fp = step_fingerprint(url, title=title, step_hint=step_hint)
    return {
        "fingerprint": fp,
        "url": url[:300],
        "title": (title or "")[:160],
        "step_hint": (step_hint or "")[:120],
    }


def is_essay_leftover(row: dict | None) -> bool:
    """True for cover-letter / essay / open-narrative leftover rows."""
    if not isinstance(row, dict):
        return False
    if row.get("essay") is True:
        return True
    ftype = str(row.get("type") or "").strip()
    if ftype in _ESSAY_TYPE_KEYS or ftype.upper() in _ESSAY_TYPE_KEYS:
        return True
    label = str(row.get("label") or row.get("automation_id") or "")
    # FILL2-004: LinkedIn/GitHub/Portfolio URL fields must not get essay narratives.
    if _URL_LINK_FIELD_RE.search(label) and not re.search(
        r"\b(?:essay|cover[\s_-]*letter|motivation|tell[\s_-]*us|describe|"
        r"explain|why[\s_-]*do[\s_-]*you)\b",
        label,
        re.I,
    ):
        return False
    return bool(_ESSAY_LABEL_RE.search(label))


def compute_stuck_on_same_page(
    *,
    next_existed: bool,
    fingerprint_before: str | None,
    fingerprint_after: str | None,
    advance_clicked: bool = False,
) -> bool:
    """True if ADVANCE was clicked but URL/step did not change.

    ATS3-003: intentional FAIL-before-ADVANCE (required empties, Next visible,
    ``advance_clicked=False``) must NOT mark stuck — that path correctly refuses
    to click. Only an actual ADVANCE click with unchanged fingerprint is stuck.
    ``next_existed`` alone is insufficient (Workday gate passes False for both).
    """
    before = (fingerprint_before or "").strip()
    after = (fingerprint_after or "").strip()
    if not before or not after:
        return False
    if before != after:
        return False
    # Require a real ADVANCE click — refuse-to-advance is not stuck.
    if advance_clicked:
        return True
    return False


def record_page_seen(report: dict, fingerprint: str, *, meta: dict | None = None) -> None:
    """Append unique step fingerprints to ``report['pages_seen']``."""
    fp = (fingerprint or "").strip()
    if not fp:
        return
    seen = report.setdefault("pages_seen", [])
    if not isinstance(seen, list):
        seen = []
        report["pages_seen"] = seen
    existing = {s if isinstance(s, str) else (s or {}).get("fingerprint") for s in seen}
    if fp in existing:
        return
    if meta:
        row = {"fingerprint": fp, **{k: v for k, v in meta.items() if k != "fingerprint"}}
        seen.append(row)
    else:
        seen.append(fp)


def note_advance_result(
    report: dict,
    *,
    fingerprint_before: str,
    fingerprint_after: str,
    next_existed: bool,
    advance_clicked: bool,
) -> dict[str, Any]:
    """Update pages_seen / advanced_count / stuck_on_same_page on ``report``."""
    report["page_fingerprint_before"] = fingerprint_before
    report["page_fingerprint_after"] = fingerprint_after
    record_page_seen(report, fingerprint_before)
    record_page_seen(report, fingerprint_after)
    moved = bool(
        fingerprint_before
        and fingerprint_after
        and fingerprint_before != fingerprint_after
    )
    if advance_clicked and moved:
        report["advanced_count"] = int(report.get("advanced_count") or 0) + 1
    else:
        report.setdefault("advanced_count", int(report.get("advanced_count") or 0))

    stuck = compute_stuck_on_same_page(
        next_existed=next_existed,
        fingerprint_before=fingerprint_before,
        fingerprint_after=fingerprint_after,
        advance_clicked=advance_clicked,
    )
    # Once stuck on any ADVANCE attempt, keep the flag for the run.
    if stuck:
        report["stuck_on_same_page"] = True
    else:
        report.setdefault("stuck_on_same_page", False)
    return {
        "fingerprint_before": fingerprint_before,
        "fingerprint_after": fingerprint_after,
        "moved": moved,
        "stuck_on_same_page": stuck,
        "advanced_count": report.get("advanced_count"),
    }


READY_BLOCKING_BLOCKERS = frozenset({
    "auth_wall",
    "page_incomplete",
    "validation_errors",
    "captcha",
    "akamai",
    "cloudflare",
    "email_verify",
    "self_id_incomplete",
    "multipage_incomplete",
    "vision_incomplete",
})

# Live DOM/vision judge verdicts that must never promote Ready.
READY_FAIL_VISION_VERDICTS = frozenset({
    "FAIL_BLANK",
    "BLOCKED",
    "AMBIGUOUS",
    "FAIL_STUCK",
})


def _hard_non_essay_leftovers(report: dict) -> list[dict]:
    return [
        u
        for u in (report.get("leftovers") or [])
        if isinstance(u, dict)
        and not is_essay_leftover(u)
        and str(u.get("reason") or "")
        not in ("already_correct_skip", "already_correct_keep")
    ]


def vision_blocks_ready(report: dict) -> bool:
    """True when vision_judge_live forbids Ready (fail-closed on absence).

    Ready requires a live ``vision_judge_live`` dict that is not incomplete /
    FAIL_BLANK / BLOCKED / AMBIGUOUS. Missing vision → blocks Ready.
    """
    vj = report.get("vision_judge_live")
    if not isinstance(vj, dict):
        return True  # fail-closed: no Ready without vision after judge path
    if vj.get("complete") is False:
        return True
    verdict = str(vj.get("verdict") or "").strip().upper()
    if verdict in READY_FAIL_VISION_VERDICTS:
        return True
    if verdict and verdict not in ("COMPLETE", "OK", "PASS", "READY"):
        # Unknown verdict strings: require complete=True explicitly
        if vj.get("complete") is not True:
            return True
    return False


def can_claim_ready(report: dict) -> bool:
    """Preconditions for Ready (ignore current ready_for_review flag).

    True only when it is honest to set/keep ready_for_review — not merely
    because the browser was held open. Requires ``vision_judge_live`` present
    and not blocking (skill: Ready only with live judge complete).
    """
    if report.get("verdict") == "FAIL":
        return False
    if report.get("advanced_incomplete") or report.get("validation_after_advance"):
        return False
    if report.get("required_empty_before_advance") or report.get("required_empty_after_fill"):
        return False
    # ChamPro gaps()-after-Save: hard required/validation leftovers
    gaps = report.get("gaps_after_save")
    if gaps:
        try:
            from form_gaps import gaps_block_ready

            if gaps_block_ready(gaps):
                return False
        except Exception:
            if gaps:
                return False
    if report.get("gaps_block_ready"):
        return False
    blocker = str(report.get("blocker") or "").strip()
    if blocker in READY_BLOCKING_BLOCKERS:
        return False
    if report.get("on_auth_wall"):
        return False
    if report.get("vision_incomplete"):
        return False
    if vision_blocks_ready(report):
        return False
    if _hard_non_essay_leftovers(report):
        return False
    return True


def finalize_ready_flag(report: dict) -> dict:
    """If ready_for_review is True but can_claim_ready is False, clear it.

    Call at end of hold-setup and _finalize paths.
    """
    if report.get("ready_for_review") and not can_claim_ready(report):
        report["ready_for_review"] = False
        report["ready_claim_refused"] = True
        report.setdefault(
            "ready_claim_reason",
            report.get("blocker") or report.get("verdict_reason") or "incomplete",
        )
    return report


async def apply_live_vision_gate(page, report: dict) -> dict:
    """Run judge_page; persist vision_judge_live; refuse Ready on incomplete.

    Headless may only have DOM blank scan (no pixels) — that is enough to gate.
    Never submits. On judge failure, fail closed (AMBIGUOUS / not Ready).
    """
    result: dict
    try:
        from vision_judge import judge_page

        result = await judge_page(page)
    except Exception as e:
        result = {
            "complete": False,
            "empty_fields": [{"label": f"judge_error: {e}", "kind": "blank"}],
            "banner_text": "",
            "submit_visible": False,
            "confidence": "ambiguous",
            "verdict": "AMBIGUOUS",
            "source": "dom",
            "never_submit": True,
            "submit_clicked": False,
            "notes": str(e)[:200],
        }
    if not isinstance(result, dict):
        result = {
            "complete": False,
            "verdict": "AMBIGUOUS",
            "empty_fields": [],
            "never_submit": True,
            "submit_clicked": False,
        }
    result["never_submit"] = True
    result["submit_clicked"] = False
    report["vision_judge_live"] = result

    bad = (
        result.get("complete") is False
        or str(result.get("verdict") or "").strip().upper() in READY_FAIL_VISION_VERDICTS
    )
    if bad:
        report["vision_incomplete"] = True
        if not report.get("blocker"):
            report["blocker"] = "vision_incomplete"
        report["ready_for_review"] = False
        report.setdefault("ready_claim_reason", "vision_incomplete")
    finalize_ready_flag(report)
    return result


def flash_attempt_failed(report: dict) -> bool:
    """True when Flash leftovers were requested, leftovers remain, and Flash failed.

    Successful Flash invoke with leftover essays still listed is NOT a failure —
    essays may honestly remain. Failure = not invoked / error / captcha / bad status.

    FILL3-001 / FILL3-013: ``flash.invoked`` means LLM/Skyvern ran — not that the
    dashboard leftovers path failed. When ``skyvern_deferred`` (hold-open / refill),
    Skyvern is skipped by design and inpage may leave ``invoked=false`` after
    deterministic reclaim / residual honesty. That must NOT demote SUCCESS as
    ``flash_leftovers_failed``.
    """
    if not report.get("flash_leftovers_requested"):
        return False
    leftover_n = int(report.get("leftover_count") or 0)
    if leftover_n <= 0:
        return False
    flash = report.get("flash") if isinstance(report.get("flash"), dict) else {}
    skipped = flash.get("skipped_reason")
    if skipped == "no_leftovers":
        return False
    if skipped == "blocker":
        return True
    if flash.get("error") or flash.get("captcha_blocked"):
        return True
    status = str(flash.get("status") or "").lower()
    if status in ("failed", "terminated", "canceled", "cancelled"):
        return True
    if not flash.get("invoked"):
        # FILL3-001: hold+refill defers Skyvern; invoked=false is expected when
        # inpage ran deterministic-only or residual honesty leftovers remain.
        if flash.get("skyvern_deferred"):
            return False
        # FILL3-013: inpage_ran without LLM is not a "Flash never ran" failure
        # when leftovers are essay-only residual honesty.
        if flash.get("inpage_ran") and not _hard_non_essay_leftovers(report):
            return False
        return True
    # Invoked without hard failure → Flash did not "fail" (leftovers may be essays).
    return False


def reconcile_stale_advance_gate(report: dict) -> dict:
    """Clear advance_blocked when post-fill required empties are gone.

    ``try_advance_if_page_complete`` may run before Flash/refill fills essays.
    A stale ``required_fields_empty`` must not false-FAIL the final report when
    ``required_empty_after_fill`` is empty (Dragos GH residual).

    Safe to call on every finalize: if advance was cleared earlier but leftovers
    later drained to zero, still promote SUCCESS.
    """
    if "required_empty_after_fill" not in report:
        return report
    req_after = report.get("required_empty_after_fill") or []
    if req_after:
        # Still incomplete — keep gate; force FAIL when blanks remain.
        if report.get("verdict") in (None, "SUCCESS", "PARTIAL", ""):
            report["verdict"] = "FAIL"
            report.setdefault("verdict_reason", "required_empty_after_fill")
        return report

    if report.get("advance_blocked_reason") == "required_fields_empty":
        report["advance_blocked_reason"] = None
        report["required_empty_before_advance"] = []
        report["stale_advance_gate_cleared"] = True
        pa = report.get("page_advance")
        if isinstance(pa, dict) and pa.get("advance_blocked_reason") == "required_fields_empty":
            pa["advance_blocked_reason"] = None
            pa["required_empty_before_advance"] = []
            pa["stale_advance_gate_cleared"] = True
        if report.get("blocker") == "page_incomplete":
            report["blocker"] = None

    hard_fail = bool(
        report.get("advanced_incomplete")
        or report.get("validation_after_advance")
        or report.get("stuck_on_same_page")
        or (report.get("demoted_false_verified") or [])
    )
    hard_left = _hard_non_essay_leftovers(report)
    if not hard_fail and not hard_left:
        reason = str(report.get("verdict_reason") or "")
        lift_ok = reason in (
            "",
            "required_empty_after_fill",
            "required_empties_remain",
            "leftovers_remain",
            "demoted_false_verified",
        ) or bool(report.get("stale_advance_gate_cleared"))
        if lift_ok and report.get("verdict") in (None, "FAIL", "PARTIAL", ""):
            report["verdict"] = "SUCCESS"
            report.pop("verdict_reason", None)
            # Only claim Ready when honesty preconditions pass (e.g. not auth_wall).
            if can_claim_ready(report):
                report.setdefault("ready_for_review", True)
    elif report.get("verdict") == "FAIL":
        # Advance gate cleared; keep FAIL but replace stale required_empty reason
        if report.get("verdict_reason") in (
            "required_empty_after_fill",
            "required_empties_remain",
            None,
            "",
        ):
            if report.get("demoted_false_verified"):
                report["verdict_reason"] = "demoted_false_verified"
            elif hard_left:
                report["verdict_reason"] = "leftovers_remain"
    return report


def apply_progress_verdict_gates(report: dict) -> dict:
    """Demote SUCCESS when stuck, required empties remain, or Flash leftovers failed."""
    reconcile_stale_advance_gate(report)

    required_empty = report.get("required_empty_before_advance") or []
    if required_empty and report.get("verdict") == "SUCCESS":
        report["verdict"] = "FAIL"
        report.setdefault("verdict_reason", "required_empties_remain")

    # Post-fill live blanks / false_verified demotions (SPA wipe, LinkedIn, etc.)
    req_after = report.get("required_empty_after_fill") or []
    if req_after and report.get("verdict") == "SUCCESS":
        report["verdict"] = "FAIL"
        report.setdefault("verdict_reason", "required_empty_after_fill")

    demoted = report.get("demoted_false_verified") or []
    if demoted and report.get("verdict") == "SUCCESS":
        report["verdict"] = "FAIL"
        report.setdefault("verdict_reason", "demoted_false_verified")

    before = report.get("page_fingerprint_before")
    after = report.get("page_fingerprint_after")
    if (
        report.get("advanced")
        and before
        and after
        and before == after
    ):
        report["stuck_on_same_page"] = True
        if report.get("verdict") == "SUCCESS":
            report["verdict"] = "FAIL"
            report.setdefault("verdict_reason", "advance_fingerprint_unchanged")

    if report.get("stuck_on_same_page") and report.get("verdict") == "SUCCESS":
        report["verdict"] = "FAIL"
        report.setdefault("verdict_reason", "stuck_on_same_page")

    if flash_attempt_failed(report) and report.get("verdict") == "SUCCESS":
        report["verdict"] = "FAIL"
        report.setdefault("verdict_reason", "flash_leftovers_failed")

    report.setdefault("pages_seen", report.get("pages_seen") or [])
    report.setdefault("advanced_count", int(report.get("advanced_count") or 0))
    report.setdefault("stuck_on_same_page", bool(report.get("stuck_on_same_page")))
    finalize_ready_flag(report)
    return report
