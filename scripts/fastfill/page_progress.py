#!/usr/bin/env python3
"""Page / step progress tracking for multipage autofill honesty.

Detects "thought we finished but still on page 1": Next existed (or ADVANCE
clicked) yet URL/step fingerprint did not change after a complete-fill attempt.

Pure helpers — Playwright is optional (only ``capture_step_fingerprint`` needs a page).
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any
from urllib.parse import urlparse

# Phase 1 completion-gate strictness. Default ON; set FASTFILL_STRICT_COMPLETION=0
# to roll back to the pre-consolidation behavior (no shared-blank SUCCESS refusal).
_STRICT_COMPLETION = os.environ.get("FASTFILL_STRICT_COMPLETION", "1") != "0"


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
    html_type = str(row.get("html_type") or row.get("html_tag") or "").strip().lower()
    # FILL2-004: LinkedIn/GitHub/Portfolio URL fields must not get essay narratives.
    if _URL_LINK_FIELD_RE.search(label) and not re.search(
        r"\b(?:essay|cover[\s_-]*letter|motivation|tell[\s_-]*us|describe|"
        r"explain|why[\s_-]*do[\s_-]*you)\b",
        label,
        re.I,
    ):
        # A single-line URL <input> is deterministic (one dummy URL). But a
        # multi-line <textarea> asking for "additional links" is open-ended /
        # narrative — treat it as essay so the grounded-answer path can list the
        # dummy URLs and so an honestly-open field never hard-blocks completion.
        if html_type == "textarea":
            return True
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


def outstanding_required_blanks(report: dict) -> list[dict]:
    """Single authoritative list of hard "still-not-done" blanks.

    The ONE definition of "not done" shared by the completion gate
    (``apply_progress_verdict_gates``) and the in-session refill loop, so the two
    can never drift: a fill must not stop or claim SUCCESS/Ready while any of
    these remain. Essays / cover letters are intentionally EXCLUDED (they may
    honestly remain), matching ``_hard_non_essay_leftovers``.
    """
    def _as_row(item: Any, reason: str) -> dict:
        return item if isinstance(item, dict) else {"label": str(item), "reason": reason}

    blanks: list[dict] = []
    for key in ("required_empty_before_advance", "required_empty_after_fill"):
        for item in report.get(key) or []:
            blanks.append(_as_row(item, key))
    blanks.extend(_hard_non_essay_leftovers(report))
    for item in report.get("demoted_false_verified") or []:
        blanks.append(_as_row(item, "demoted_false_verified"))
    return blanks


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


# Workday multipage step order (normalized). Used to refuse Ready / review-hold
# while later wizard steps are still pending (Thales-class early hold bug).
_WD_WIZARD_STEP_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("autofill", ("autofill", "autofill with resume")),
    ("my information", ("my information", "my info", "contact information")),
    ("my experience", ("my experience", "experience", "work experience")),
    (
        "application questions",
        ("application questions", "app questions", "questionnaire"),
    ),
    (
        "voluntary disclosures",
        ("voluntary disclosures", "voluntary disclosure", "eeo"),
    ),
    ("self identify", ("self identify", "self-identify", "self identification")),
    ("review", ("review", "review and submit", "review your application")),
)
_WD_WIZARD_ORDER = tuple(name for name, _aliases in _WD_WIZARD_STEP_ALIASES)


def normalize_workday_step_label(label: str) -> str | None:
    """Map free-text / progress label to a canonical Workday step name."""
    lab = " ".join((label or "").strip().lower().split())
    if not lab:
        return None
    for canon, aliases in _WD_WIZARD_STEP_ALIASES:
        for alias in aliases:
            if alias in lab or lab in alias:
                return canon
    return None


def workday_progress_unfinished_after_current(progress_text: str) -> bool:
    """True when progress text shows unfinished steps after the current one.

    Handles phrases like ``My Experience current`` / ``Application Questions``
    still listed after a completed My Information checkmark.
    """
    text = " ".join((progress_text or "").strip().lower().split())
    if not text:
        return False
    # Explicit pending markers after a known mid-wizard current step
    current_idx: int | None = None
    for i, name in enumerate(_WD_WIZARD_ORDER):
        # "my experience current" / "current: my experience" / aria-current text
        if (
            f"{name} current" in text
            or f"current {name}" in text
            or f"current: {name}" in text
        ):
            current_idx = i
            break
    if current_idx is None:
        # Infer current as the last step mentioned with completed/done check
        # before an unmarked later step name.
        mentioned: list[int] = []
        for i, name in enumerate(_WD_WIZARD_ORDER):
            if name in text:
                mentioned.append(i)
        if not mentioned:
            return False
        # If Review is mentioned as completed/current → not unfinished
        if _WD_WIZARD_ORDER.index("review") in mentioned and (
            "review current" in text
            or "review ✓" in text
            or "review complete" in text
            or "on review" in text
        ):
            return False
        current_idx = max(
            i
            for i in mentioned
            if i < len(_WD_WIZARD_ORDER) - 1
            or "review" not in text
        )
        # Prefer non-review max if Review is only listed as pending
        non_review = [i for i in mentioned if _WD_WIZARD_ORDER[i] != "review"]
        if non_review and "review" in text and "review current" not in text:
            current_idx = max(non_review)
    if current_idx is None:
        return False
    if _WD_WIZARD_ORDER[current_idx] == "review":
        return False
    # Any later step name still present → unfinished after current
    for later in _WD_WIZARD_ORDER[current_idx + 1 :]:
        if later in text:
            return True
    # "pending" / "not started" after current also counts
    if re.search(r"\b(pending|not started|incomplete|remaining)\b", text):
        return True
    return current_idx < len(_WD_WIZARD_ORDER) - 1 and (
        "current" in text or "step" in text
    )


# Live footer / primary nav button (Workday bottom-right Next vs Submit).
# One short DOM evaluate — no waits. Prefer automation-id, then bottom-right.
FOOTER_PRIMARY_PROBE_JS = """() => {
  const visible = (el) => {
    if (!el) return false;
    const st = window.getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return (
      st.display !== 'none' &&
      st.visibility !== 'hidden' &&
      st.opacity !== '0' &&
      r.width > 0 &&
      r.height > 0
    );
  };
  const labelOf = (el) =>
    (el.innerText || el.value || el.getAttribute('aria-label') || '')
      .trim()
      .replace(/\\s+/g, ' ')
      .slice(0, 80);

  const wdNext = document.querySelector(
    '[data-automation-id="bottom-navigation-next-button"]'
  );
  if (wdNext && visible(wdNext)) {
    return {
      label: labelOf(wdNext),
      source: 'workday_bottom_nav',
      automation_id: 'bottom-navigation-next-button',
    };
  }
  const wdSubmitSels = [
    '[data-automation-id="bottom-navigation-submit-button"]',
    '[data-automation-id="submitApplication"]',
    '[data-automation-id="submitBtn"]',
  ];
  for (const s of wdSubmitSels) {
    const el = document.querySelector(s);
    if (el && visible(el)) {
      return {
        label: labelOf(el),
        source: 'workday_bottom_submit',
        automation_id: el.getAttribute('data-automation-id') || '',
      };
    }
  }

  const nodes = Array.from(
    document.querySelectorAll(
      'button, input[type=submit], a[role=button], [role=button]'
    )
  );
  const floor = window.innerHeight * 0.55;
  const cands = [];
  const skipRe = /phone|mobile|device|country.?code|prefix|language|sign.?in|menu/i;
  // Prefer ADVANCE (Next/Continue) over sticky FINAL (Submit) when both are
  // visible mid-wizard — equal prefer + bottom-right otherwise picks Submit.
  const advanceRe = /save\\s+and\\s+continue|save\\s*&\\s*continue|\\bcontinue\\b|\\bnext\\b/i;
  const finalRe = /\\bsubmit\\b|review\\s+and\\s+submit|\\bapply\\b/i;
  for (const el of nodes) {
    if (!visible(el)) continue;
    const t = labelOf(el);
    if (!t) continue;
    const aid = el.getAttribute('data-automation-id') || '';
    if (skipRe.test(t) || skipRe.test(aid)) continue;
    const r = el.getBoundingClientRect();
    if (r.bottom < floor) continue;
    let prefer = 0;
    if (advanceRe.test(t)) prefer = 2;
    else if (finalRe.test(t)) prefer = 1;
    cands.push({
      t,
      r,
      aid,
      prefer,
    });
  }
  if (!cands.length) {
    return { label: '', source: 'none', automation_id: '' };
  }
  cands.sort(
    (a, b) =>
      b.prefer - a.prefer ||
      b.r.bottom - a.r.bottom ||
      b.r.right - a.r.right
  );
  const best = cands[0];
  return {
    label: best.t,
    source: 'bottom_right_heuristic',
    automation_id: best.aid,
  };
}"""


def footer_primary_kind_from_label(
    label: str,
    *,
    button_type: str = "",
    aria_label: str = "",
    value: str = "",
) -> str:
    """Classify a footer/primary nav label via button_map (FINAL vs ADVANCE…)."""
    from button_map import classify_button

    return classify_button(
        label, button_type=button_type, aria_label=aria_label, value=value
    )


def footer_primary_wizard_incomplete(
    kind: str | None,
    label: str | None = None,
) -> bool | None:
    """Footer primary → wizard-incomplete decision.

    Returns:
      True  — ADVANCE / Back / unknown labeled control → refuse review-hold
      False — FINAL (Submit…) → Review end state; eligible for hold (never click)
      None  — no footer signal; keep phase/progress heuristics
    """
    from button_map import ADVANCE, FINAL, UNKNOWN

    lab = (label or "").strip()
    k = (kind or "").strip().upper()
    if not k and lab:
        k = footer_primary_kind_from_label(lab)
    if not k and not lab:
        return None
    if k == FINAL:
        return False
    if k == ADVANCE:
        return True
    # Back-only / unrecognized primary → fail closed toward incomplete
    if lab and (k in (UNKNOWN, "") or re.search(r"^\s*back\b", lab, re.I)):
        return True
    if k and k not in (FINAL,):
        return True
    return None


def attach_footer_primary(
    report: dict,
    *,
    kind: str,
    label: str,
    source: str = "",
    automation_id: str = "",
) -> dict:
    """Persist footer primary fields on the report (no Playwright)."""
    report["footer_primary_kind"] = kind
    report["footer_primary_label"] = (label or "")[:80]
    if source:
        report["footer_primary_source"] = source
    if automation_id:
        report["footer_primary_automation_id"] = automation_id
    decision = footer_primary_wizard_incomplete(kind, label)
    if decision is True:
        report["footer_primary_blocks_review_hold"] = True
    elif decision is False:
        report["footer_primary_blocks_review_hold"] = False
    else:
        report.pop("footer_primary_blocks_review_hold", None)
    return report


async def probe_footer_primary(page, report: dict | None = None) -> dict:
    """One short DOM probe for bottom-right / Workday primary footer button.

    Never clicks. Attaches ``footer_primary_kind`` / ``footer_primary_label``
    when ``report`` is provided. Latency: single ``page.evaluate``, no waits.
    """
    out: dict[str, Any] = {
        "label": "",
        "kind": "",
        "source": "none",
        "automation_id": "",
        "never_submit": True,
    }
    raw: dict[str, Any] = {}
    try:
        raw = await page.evaluate(FOOTER_PRIMARY_PROBE_JS)
    except Exception as e:
        out["error"] = str(e)[:120]
        if report is not None:
            report["footer_primary_probe_error"] = out["error"]
        return out
    if not isinstance(raw, dict):
        raw = {}
    label = str(raw.get("label") or "").strip()
    source = str(raw.get("source") or "none")
    aid = str(raw.get("automation_id") or "")
    kind = footer_primary_kind_from_label(label) if label else ""
    out.update(
        {
            "label": label[:80],
            "kind": kind,
            "source": source,
            "automation_id": aid,
        }
    )
    if report is not None and (label or kind):
        attach_footer_primary(
            report,
            kind=kind or "UNKNOWN",
            label=label,
            source=source,
            automation_id=aid,
        )
    elif report is not None:
        report["footer_primary_kind"] = ""
        report["footer_primary_label"] = ""
        report["footer_primary_source"] = source
        report.pop("footer_primary_blocks_review_hold", None)
    return out


def workday_wizard_incomplete(report: dict) -> bool:
    """True when Workday multipage has not honestly reached Review.

    Used to refuse Ready / ``hold for review`` while Experience / Questions /
    Disclosures / Self-ID still remain. Does not call ``can_claim_ready``
    (no circularity).

    Footer primary (Next/Continue vs Submit) is first-class when probed:
    ADVANCE → incomplete; FINAL → Review end (not incomplete from this signal).
    Applies to any platform when footer was probed — never review-hold while
    Next / Save and Continue is the primary footer control.
    """
    # Live / attached footer primary — first-class for ALL platforms.
    footer_dec = footer_primary_wizard_incomplete(
        report.get("footer_primary_kind"),
        report.get("footer_primary_label"),
    )
    if footer_dec is True:
        return True
    if footer_dec is False:
        # Submit visible as primary → Review end state for hold framing.
        # Still allow Workday phase heuristics below when platform is workday
        # and phase says incomplete (do not early-return False yet).
        pass

    platform = str(report.get("platform") or "").lower()
    coverage = str(report.get("coverage_path") or "")
    wd = report.get("workday") if isinstance(report.get("workday"), dict) else None
    if platform != "workday" and coverage != "workday_multipage" and not wd:
        # Non-Workday: footer ADVANCE already returned True above; FINAL/absent
        # means this helper does not force incomplete.
        return False

    if footer_dec is False:
        return False

    pe = (wd or {}).get("phase_e") if isinstance(wd, dict) else None
    pe = pe if isinstance(pe, dict) else {}
    if pe.get("stopped_at_review"):
        return False

    step = str(
        report.get("workday_current_step")
        or (wd or {}).get("current_step")
        or ""
    ).strip().lower()
    if step and step not in ("review", "unknown", ""):
        return True
    if step == "review":
        return False

    prog = str(
        report.get("workday_wizard_progress")
        or (wd or {}).get("wizard_progress")
        or ""
    )
    if workday_progress_unfinished_after_current(prog):
        return True

    blocker = str(report.get("blocker") or "").strip()
    if blocker in (
        "page_incomplete",
        "multipage_incomplete",
        "contact_incomplete",
        "self_id_incomplete",
    ):
        return True
    if report.get("advance_blocked_reason"):
        return True

    pc = (wd or {}).get("phase_c") if isinstance(wd, dict) else None
    pc = pc if isinstance(pc, dict) else {}
    if pc.get("present") and not pc.get("advanced") and not pc.get("skipped"):
        return True

    # Advanced past contact / experience but never stopped at review
    pb = (wd or {}).get("phase_b") if isinstance(wd, dict) else None
    pb = pb if isinstance(pb, dict) else {}
    started = bool(
        report.get("advanced")
        or report.get("reached_contact")
        or pb.get("advanced")
        or pc.get("advanced")
        or pc.get("present")
    )
    if started and not pe.get("stopped_at_review"):
        return True

    # Workday coverage without an honest Review stop → incomplete for Ready /
    # review-hold. Phase E sets workday_current_step=review before claiming Ready
    # (phase_e may not be attached to report yet during that call).
    if (platform == "workday" or coverage == "workday_multipage" or wd) and not pe.get(
        "stopped_at_review"
    ):
        if str(report.get("workday_current_step") or "").strip().lower() == "review":
            return False
        # Pure early hard-blocker before any apply form is still "not ready for
        # review" — refuse review-hold labeling (browser may still stay open).
        return True
    return False


def may_enter_review_hold(report: dict) -> bool:
    """True only when ``hold for review`` / Ready labeling is honest.

    Mid-wizard Workday (unfinished steps after current, sparse Experience,
    Next still required) must NOT enter review-hold framing. Browser keep-open
    for human inspection can still run under an incomplete status.

    Footer primary ADVANCE (Next / Save and Continue) refuses hold even when
    phase metadata is stale; FINAL (Submit) is Review-eligible (never clicked).
    """
    if workday_wizard_incomplete(report):
        return False
    return can_claim_ready(report)


def can_claim_ready(report: dict) -> bool:
    """Preconditions for Ready (ignore current ready_for_review flag).

    True only when it is honest to set/keep ready_for_review — not merely
    because the browser was held open. Requires ``vision_judge_live`` present
    and not blocking (skill: Ready only with live judge complete).
    """
    if report.get("verdict") == "FAIL":
        return False
    if report.get("stuck_on_same_page"):
        return False
    if workday_wizard_incomplete(report):
        return False
    if report.get("advanced_incomplete") or report.get("validation_after_advance"):
        return False
    if report.get("required_empty_before_advance") or report.get("required_empty_after_fill"):
        return False
    # Open listbox / mid-widget — never Ready while a prompt is still open
    if report.get("listbox_open") or report.get("mid_widget_open"):
        return False
    # Explicit advance gate (listbox / required empties / miss) — never Ready
    if report.get("advance_blocked_reason"):
        return False
    if report.get("hold_incomplete"):
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
        footer_dec = footer_primary_wizard_incomplete(
            report.get("footer_primary_kind"),
            report.get("footer_primary_label"),
        )
        if footer_dec is True:
            reason = "footer_primary_advance"
        elif workday_wizard_incomplete(report):
            reason = "workday_wizard_incomplete"
        else:
            reason = (
                report.get("blocker")
                or report.get("verdict_reason")
                or "incomplete"
            )
        report.setdefault("ready_claim_reason", reason)
    return report


async def apply_live_vision_gate(page, report: dict) -> dict:
    """Run judge_page; persist vision_judge_live; refuse Ready on incomplete.

    Headless may only have DOM blank scan (no pixels) — that is enough to gate.
    Never submits. On judge failure, fail closed (AMBIGUOUS / not Ready).
    Also probes footer primary (Next vs Submit) — light, no waits.
    """
    try:
        await probe_footer_primary(page, report)
    except Exception as e:
        report.setdefault("errors", []).append(
            {"footer_primary_probe": str(e)[:120]}
        )
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

    # Hard non-essay leftovers forbid SUCCESS from ANY source. reconcile_stale_advance_gate
    # already refuses to MINT SUCCESS with hard leftovers, but SUCCESS set elsewhere
    # (e.g. Workday-merged verdict) could otherwise survive when no
    # required_empty_after_fill key is present. This makes the gate the sole authority.
    if (
        _STRICT_COMPLETION
        and report.get("verdict") == "SUCCESS"
        and _hard_non_essay_leftovers(report)
    ):
        report["verdict"] = "FAIL"
        report.setdefault("verdict_reason", "leftovers_remain")

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

    # Field-lock thrash: re-touch of commit-verified fields is wasted work
    try:
        from field_lock import apply_thrash_verdict_gate, fold_lock_metrics

        fold_lock_metrics(report)
        apply_thrash_verdict_gate(report)
    except Exception:
        pass

    report.setdefault("pages_seen", report.get("pages_seen") or [])
    report.setdefault("advanced_count", int(report.get("advanced_count") or 0))
    report.setdefault("stuck_on_same_page", bool(report.get("stuck_on_same_page")))
    finalize_ready_flag(report)
    return report
