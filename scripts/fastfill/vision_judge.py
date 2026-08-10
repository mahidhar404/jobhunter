#!/usr/bin/env python3
"""Agent2 screenshot / page completion judge for the multi-agent fix cycle.

COMPLETE only if zero unanswered visible fields (blanks, placeholders,
unchecked required) and essays look answered. Never clicks Submit.

Schema (written beside after_fill.png as vision_judge.json)::

    {
      "complete": true|false,
      "empty_fields": [{"label": "...", "kind": "blank|placeholder|unchecked", ...}],
      "banner_text": "" | "...",
      "submit_visible": bool,
      "confidence": "high" | "ambiguous",
      "verdict": "COMPLETE" | "FAIL_BLANK" | "AMBIGUOUS" | "BLOCKED",
      "notes": "..."
    }

DeepSeek-V4-Flash is text-only (no vision). Prefer:
  1. Live DOM scan via Playwright page (``judge_page``)
  2. Optional OpenAI-compatible vision if ``OPENAI_API_KEY`` + vision model set
  3. Stub / agent-filled schema from screenshot path alone

Usage::

    from vision_judge import judge_screenshot, VISION_JUDGE_SCHEMA, write_vision_judge

    result = await judge_page(page)
    # or
    result = judge_screenshot(path, report=report)  # heuristic + optional vision LLM
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

VISION_JUDGE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Agent2VisionJudge",
    "type": "object",
    "required": [
        "complete",
        "empty_fields",
        "banner_text",
        "submit_visible",
        "confidence",
    ],
    "properties": {
        "complete": {
            "type": "boolean",
            "description": (
                "true ONLY if zero blanks/placeholders/unchecked required "
                "and essays look answered"
            ),
        },
        "empty_fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["blank", "placeholder", "unchecked", "essay_empty"],
                    },
                    "selector": {"type": "string"},
                    "required": {"type": "boolean"},
                },
            },
        },
        "banner_text": {"type": "string"},
        "submit_visible": {
            "type": "boolean",
            "description": "true if Submit/Apply-final visible — NEVER click it",
        },
        "confidence": {"type": "string", "enum": ["high", "ambiguous"]},
        "verdict": {
            "type": "string",
            "enum": ["COMPLETE", "FAIL_BLANK", "AMBIGUOUS", "BLOCKED", "FAIL_STUCK"],
        },
        "notes": {"type": "string"},
        "source": {
            "type": "string",
            "enum": [
                "dom",
                "vision_llm",
                "heuristic_report",
                "stub",
                "agent",
                "cursor_agent2_pixels",
            ],
        },
        "screenshot": {"type": "string"},
        "never_submit": {"type": "boolean", "const": True},
    },
    "additionalProperties": True,
}

# DOM scan used by judge_page (Playwright page.evaluate).
EMPTY_FIELDS_JS = """() => {
  const PLACEHOLDER_RE = /^(type\\s+here|enter\\s+|select\\s+|choose\\s+|n\\/?a\\.?|\\.{0,3})$/i;
  const out = [];
  const isVisible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
    return r.width > 0 && r.height > 0;
  };
  const labelFor = (el) => {
    const id = el.id;
    if (id) {
      const lab = document.querySelector('label[for="' + CSS.escape(id) + '"]');
      if (lab) return (lab.innerText || lab.textContent || '').trim().slice(0, 120);
    }
    const wrap = el.closest('label');
    if (wrap) return (wrap.innerText || '').trim().slice(0, 120);
    const aria = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
    return (aria || el.name || el.id || '').trim().slice(0, 120);
  };
  const requiredish = (el) =>
    el.required || el.getAttribute('aria-required') === 'true' ||
    /\\*/.test(labelFor(el)) ||
    (el.closest('[data-required], .required') != null);
  // Voluntary EEO/demographic self-ID. Left blank is a COMPLETE state — and on
  // GH cascading-ethnicity tenants the "race" sub-select is intentionally left
  // empty to preserve the required "Hispanic/Latino=No" answer (filling it
  // clobbers Hispanic). Only treat as optional when NOT required.
  const DEMO_RE =
    /race|ethnic|hispanic|latino|gender|veteran|disab|self[-\\s]?identif|sexual\\s+orientation|transgender|pronoun/i;
  const optionalDemographic = (el, label) =>
    !requiredish(el) && DEMO_RE.test(label || '');
  // Non-required derived location components (city / postcode / country / state)
  // on Google-Places-style address autocompletes (Workable): they only populate
  // when a place SUGGESTION is selected, which a fake dummy address can't match,
  // so they stay blank while the combined address field carries the value. Left
  // blank on an optional derived component is a submittable/COMPLETE state.
  const LOCATION_RE =
    /^(city|town|postcode|postal|zip|country|state|province|region|county)\\b/i;
  const optionalLocation = (el, label) =>
    !requiredish(el) && LOCATION_RE.test((label || '').trim());

  document.querySelectorAll('input, textarea, select').forEach((el) => {
    if (!isVisible(el)) return;
    const t = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'file'].includes(t)) return;
    const label = labelFor(el);
    if (/password/i.test(label) && t === 'password') {
      // password may be filled; skip empty check noise on confirm
    }
    if (t === 'checkbox' || t === 'radio') {
      // only flag unchecked when marked required
      if (requiredish(el) && !el.checked) {
        const group = el.name
          ? document.querySelectorAll('input[type="' + t + '"][name="' + CSS.escape(el.name) + '"]')
          : [el];
        const any = Array.from(group).some((g) => g.checked);
        if (!any) {
          out.push({
            label: label || el.name || t,
            kind: 'unchecked',
            required: true,
            selector: el.name ? (t + '[name="' + el.name + '"]') : '',
          });
        }
      }
      return;
    }
    let val = '';
    try { val = (el.value || '').trim(); } catch (e) { val = ''; }
    const ph = (el.getAttribute('placeholder') || '').trim();
    const essayish = el.tagName === 'TEXTAREA' || /why|tell us|cover|essay|describe/i.test(label);
    if (!val) {
      const effLabel = label || ph || el.name || 'field';
      out.push({
        label: effLabel,
        kind: essayish ? 'essay_empty' : (ph ? 'placeholder' : 'blank'),
        required: requiredish(el),
        optional_demographic: optionalDemographic(el, effLabel),
        optional_location: !essayish && optionalLocation(el, effLabel),
        selector: el.name ? el.tagName.toLowerCase() + '[name="' + el.name + '"]' : '',
      });
      return;
    }
    if (ph && val.toLowerCase() === ph.toLowerCase()) {
      out.push({
        label: label || ph,
        kind: 'placeholder',
        required: requiredish(el),
        selector: '',
      });
      return;
    }
    if (PLACEHOLDER_RE.test(val)) {
      out.push({
        label: label || val,
        kind: 'placeholder',
        required: requiredish(el),
        selector: '',
      });
    }
  });
  // Dedup by label+kind
  const seen = new Set();
  const deduped = [];
  for (const row of out) {
    const k = (row.label || '') + '|' + row.kind;
    if (seen.has(k)) continue;
    seen.add(k);
    deduped.push(row);
  }
  return deduped.slice(0, 80);
}"""

BANNER_JS = """() => {
  const sels = [
    '[role="alert"]', '.error', '.errors', '.validation-error',
    '[data-automation-id="errorMessage"]', '.banner--error',
    '.alert-danger', '.form-error',
  ];
  const texts = [];
  for (const s of sels) {
    document.querySelectorAll(s).forEach((el) => {
      const t = (el.innerText || el.textContent || '').trim();
      if (t && t.length < 400) texts.push(t.slice(0, 200));
    });
  }
  return texts.slice(0, 5).join(' | ');
}"""

SUBMIT_VISIBLE_JS = """() => {
  const re = /\\b(submit(\\s+application)?|send\\s+application|finish\\s+application|apply\\s+now)\\b/i;
  const nodes = Array.from(document.querySelectorAll('button, input[type=submit], a[role=button]'));
  for (const el of nodes) {
    const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
    if (!t) continue;
    // Exclude Apply entry / Save and continue
    if (/save\\s+and\\s+continue|next|continue|create\\s+account|sign\\s+in/i.test(t)) continue;
    if (re.test(t)) {
      const st = window.getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (st.display !== 'none' && st.visibility !== 'hidden' && r.width > 0) return true;
    }
  }
  return false;
}"""


def _scan_report_false_fills(report: dict) -> list[dict]:
    """Infer blanks from dishonest filled rows (zip/Ashby placeholder readbacks).

    Report may claim ``verified=True`` while readback is empty or ``Type here...``.
    Those rows become ``empty_fields`` with ``issue=screenshot_empty_but_report_claims_filled``
    so ``fill_attribution`` can flag false_success without waiting for pixel Agent2.
    """
    from ashby_widgets import is_empty_ui_value

    empties: list[dict] = []
    seen: set[str] = set()

    def _add(*, label: str, ftype: str, kind: str, source: str) -> None:
        key = f"{ftype}|{label}|{kind}"
        if key in seen:
            return
        seen.add(key)
        empties.append(
            {
                "label": label[:120],
                "type": ftype,
                "kind": kind,
                "required": True,
                "from": source,
                "issue": "screenshot_empty_but_report_claims_filled",
            }
        )

    for f in report.get("filled") or []:
        if not isinstance(f, dict):
            continue
        if not (f.get("verified") is True or f.get("ok") is True):
            continue
        ftype = str(f.get("type") or "").upper()
        label = str(f.get("label") or f.get("type") or "field")
        raw_rb = f.get("readback") if f.get("readback") is not None else f.get("shown")
        if isinstance(raw_rb, dict):
            raw_rb = " ".join(str(v) for v in raw_rb.values() if v)
        readback = str(raw_rb or "").strip()
        if readback and not is_empty_ui_value(readback):
            continue
        kind = "placeholder" if readback and is_empty_ui_value(readback) else "blank"
        _add(label=label, ftype=ftype, kind=kind, source="report_false_verified_readback")

    live_zip = str(report.get("live_zip_readback") or "").strip()
    zip_claimed = any(
        isinstance(r, dict)
        and str(r.get("type") or "").upper() == "ADDRESS_ZIP"
        and (r.get("verified") is True or r.get("ok") is True)
        for r in report.get("filled") or []
    )
    if zip_claimed and (not live_zip or is_empty_ui_value(live_zip)):
        _add(label="Zip", ftype="ADDRESS_ZIP", kind="placeholder", source="live_zip_readback_empty")

    return empties


def _merge_empty_fields(*groups: list[dict]) -> list[dict]:
    """Dedup empty_fields by label+kind+type."""
    seen: set[str] = set()
    out: list[dict] = []
    for group in groups:
        for row in group:
            if not isinstance(row, dict):
                continue
            key = (
                f"{row.get('type') or ''}|{row.get('label') or ''}|{row.get('kind') or ''}"
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
    return out


def _base_result(**kwargs: Any) -> dict[str, Any]:
    out = {
        "complete": False,
        "empty_fields": [],
        "banner_text": "",
        "submit_visible": False,
        "confidence": "ambiguous",
        "verdict": "AMBIGUOUS",
        "notes": "",
        "source": "stub",
        "never_submit": True,
        "submit_clicked": False,
        "dummy": True,
    }
    out.update(kwargs)
    return out


# Sources that may claim COMPLETE. heuristic_report alone must never SUCCESS
# when a screenshot PNG exists — leftovers=0 is a known liar.
HONEST_COMPLETE_SOURCES = frozenset(
    {"dom", "vision_llm", "agent", "cursor_agent2_pixels"}
)


def _shot_exists(shot: str | Path | None) -> bool:
    """True if screenshot path resolves to a non-empty file (CWD or repo ROOT)."""
    if not shot:
        return False
    raw = Path(str(shot))
    candidates = [raw]
    if not raw.is_absolute():
        candidates.append(ROOT / raw)
        candidates.append(HERE / raw)
    for p in candidates:
        try:
            if p.exists() and p.stat().st_size > 0:
                return True
        except Exception:
            continue
    return False


def finalize_verdict(result: dict) -> dict:
    """Set complete / verdict from empty_fields + confidence.

    Never COMPLETE from ``heuristic_report`` when a screenshot path is attached —
    Agent2 must overwrite via DOM / pixels / vision LLM. leftovers=0 is a liar.
    """
    empties = result.get("empty_fields") or []
    source = str(result.get("source") or "")
    shot = result.get("screenshot")
    shot_exists = _shot_exists(shot)
    # Screenshot *claimed* (path set) → heuristic must not COMPLETE even if
    # the file is temporarily missing (race / wrong CWD). Magnit false SUCCESS.
    shot_claimed = bool(shot)

    # Ban heuristic COMPLETE when PNG exists OR path was provided for a live run
    if source == "heuristic_report" and (shot_exists or shot_claimed):
        result["confidence"] = "ambiguous"
        result["complete"] = False
        if result.get("blocker"):
            result["verdict"] = "BLOCKED"
        elif empties:
            result["verdict"] = "FAIL_BLANK"
        else:
            result["verdict"] = "AMBIGUOUS"
        result["notes"] = (
            (result.get("notes") or "")
            + " Heuristic COMPLETE banned while screenshot path set; "
            "require dom/vision_llm/agent/cursor_agent2_pixels."
        ).strip()
        result["never_submit"] = True
        result["submit_clicked"] = False
        return result

    # COMPLETE requires zero empties (including optional-looking essays on apply forms)
    # and an honest source — stub/heuristic never COMPLETE for SOTA gate consumers.
    # Exception: voluntary EEO/demographic self-ID left blank is a legitimately
    # complete state (and the GH cascading-ethnicity "race" sub-select is left
    # empty on purpose to preserve Hispanic=No). Such non-required demographics
    # stay visible in empty_fields but do NOT block completion.
    blocking_empties = [
        e
        for e in empties
        if not (e.get("optional_demographic") or e.get("optional_location"))
    ]
    complete = len(blocking_empties) == 0 and result.get("confidence") != "ambiguous"
    if result.get("confidence") == "ambiguous" and blocking_empties:
        complete = False
    if complete and source == "heuristic_report":
        # Allow only when no screenshot was provided (dry unit paths).
        if shot:
            complete = False
            result["confidence"] = "ambiguous"
    if complete and source and source not in HONEST_COMPLETE_SOURCES:
        if source in ("stub", "heuristic_report"):
            # stub with empty fields stays AMBIGUOUS (Agent2 pending)
            if source == "stub" or shot:
                complete = False
                result["confidence"] = "ambiguous"
    result["complete"] = bool(complete)
    if result.get("blocker"):
        result["verdict"] = "BLOCKED"
        result["complete"] = False
    elif complete:
        result["verdict"] = "COMPLETE"
        result["confidence"] = result.get("confidence") or "high"
    elif result.get("confidence") == "ambiguous" and not empties:
        result["verdict"] = "AMBIGUOUS"
        result["complete"] = False
    else:
        result["verdict"] = "FAIL_BLANK"
    result["never_submit"] = True
    result["submit_clicked"] = False
    return result


def judge_from_report(report: dict, *, screenshot: str | Path | None = None) -> dict:
    """Heuristic judge from fill report leftovers (no browser / no vision LLM).

    When ``screenshot`` points at an existing PNG, never returns COMPLETE —
    leftovers=0 + verified fills are known to lie (LinkedIn blank, unchecked boxes).
    """
    empties: list[dict] = []
    for u in report.get("leftovers") or []:
        if not isinstance(u, dict):
            continue
        if u.get("flash_candidate") is False:
            continue
        reason = str(u.get("reason") or "")
        if reason.startswith("blocker:"):
            continue
        label = str(u.get("label") or u.get("type") or "field")[:120]
        essay = bool(u.get("essay")) or bool(
            re.search(r"why|tell us|cover|essay|describe", label, re.I)
        )
        empties.append(
            {
                "label": label,
                "kind": "essay_empty" if essay else "blank",
                "required": True,
                "selector": (u.get("selector") or "")[:160],
                "from": "report_leftover",
            }
        )
    for key in ("required_empty_after_fill", "demoted_false_verified"):
        for u in report.get(key) or []:
            if isinstance(u, dict):
                empties.append(
                    {
                        "label": str(u.get("label") or u.get("type") or "required")[:120],
                        "kind": "blank",
                        "required": True,
                        "from": key,
                    }
                )
            elif isinstance(u, str):
                empties.append({"label": u[:120], "kind": "blank", "required": True, "from": key})

    banner = ""
    val = report.get("validation_after_advance")
    if isinstance(val, dict):
        banner = str(val.get("banner") or val.get("text") or "")[:300]
    elif report.get("advanced_incomplete"):
        banner = "advanced_incomplete"

    shot = str(screenshot) if screenshot else report.get("screenshot")
    shot_exists = _shot_exists(shot)
    false_fills = _scan_report_false_fills(report)
    empties = _merge_empty_fields(empties, false_fills)

    confidence = "high"
    if report.get("blocker"):
        confidence = "high"
    elif shot_exists or shot:
        # PNG present or path claimed → never high-confidence COMPLETE from leftovers
        confidence = "ambiguous"
    elif false_fills:
        confidence = "ambiguous"
    elif not empties and report.get("verdict") in ("PARTIAL", None):
        confidence = "ambiguous"

    notes = "Derived from report leftovers / required_empty (no image pixels)."
    if false_fills:
        notes += f" Detected {len(false_fills)} false-verified fill(s) (zip/placeholder)."

    result = _base_result(
        empty_fields=empties,
        banner_text=banner,
        submit_visible=False,
        confidence=confidence,
        source="heuristic_report",
        screenshot=shot,
        blocker=report.get("blocker"),
        notes=notes,
    )
    return finalize_verdict(result)


async def judge_page(page, *, screenshot_path: str | Path | None = None) -> dict:
    """Live DOM judge on a Playwright page (preferred when browser is open)."""
    empties: list[dict] = []
    banner = ""
    submit_visible = False
    try:
        empties = await page.evaluate(EMPTY_FIELDS_JS) or []
    except Exception as e:
        return finalize_verdict(
            _base_result(
                empty_fields=[{"label": f"dom_scan_error: {e}", "kind": "blank"}],
                confidence="ambiguous",
                source="dom",
                notes=str(e)[:200],
                screenshot=str(screenshot_path) if screenshot_path else None,
            )
        )
    try:
        banner = (await page.evaluate(BANNER_JS) or "")[:300]
    except Exception:
        pass
    try:
        submit_visible = bool(await page.evaluate(SUBMIT_VISIBLE_JS))
    except Exception:
        pass

    result = _base_result(
        empty_fields=empties,
        banner_text=banner,
        submit_visible=submit_visible,
        confidence="high",
        source="dom",
        screenshot=str(screenshot_path) if screenshot_path else None,
        notes="DOM empty-field scan; essays must be non-empty textareas.",
    )
    return finalize_verdict(result)


def _try_vision_llm(screenshot: Path) -> dict | None:
    """Optional vision call if a vision-capable OpenAI-compatible endpoint is configured.

    DeepSeek-V4-Flash is text-only — this returns None unless a vision model
    and API key are explicitly available.
    """
    api_key = (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("VISION_API_KEY")
        or ""
    ).strip()
    # Never use OPENAI_COMPATIBLE (DeepSeek flash) — it rejects image payloads.
    base = (
        os.environ.get("VISION_API_BASE")
        or os.environ.get("OPENAI_API_BASE")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    model = (
        os.environ.get("VISION_MODEL")
        or os.environ.get("OPENAI_VISION_MODEL")
        or ""
    ).strip()
    if not api_key or not model or not screenshot.exists():
        return None
    if "deepseek" in base.lower() or "deepseek" in model.lower():
        return None

    try:
        import urllib.request

        b64 = base64.b64encode(screenshot.read_bytes()).decode("ascii")
        mime = "image/png" if screenshot.suffix.lower() == ".png" else "image/jpeg"
        prompt = (
            "You are Agent2 vision judge for a job application form screenshot. "
            "Return ONLY JSON with keys: complete (bool), empty_fields (array of "
            "{label, kind}), banner_text (string), submit_visible (bool), "
            "confidence (high|ambiguous). COMPLETE only if ZERO blanks, "
            "placeholders like 'Type here...', unchecked required boxes, and "
            "essay textareas look answered. Never suggest clicking Submit."
        )
        body = json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{b64}"},
                            },
                        ],
                    }
                ],
                "max_tokens": 800,
                "temperature": 0,
            }
        ).encode()
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        text = data["choices"][0]["message"]["content"]
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        parsed = json.loads(m.group(0))
        result = _base_result(
            complete=bool(parsed.get("complete")),
            empty_fields=list(parsed.get("empty_fields") or []),
            banner_text=str(parsed.get("banner_text") or "")[:300],
            submit_visible=bool(parsed.get("submit_visible")),
            confidence=parsed.get("confidence")
            if parsed.get("confidence") in ("high", "ambiguous")
            else "ambiguous",
            source="vision_llm",
            screenshot=str(screenshot),
            notes=f"vision model={model}",
        )
        return finalize_verdict(result)
    except Exception:
        return None


def judge_screenshot(
    screenshot: str | Path | None,
    *,
    report: dict | None = None,
    allow_stub: bool = True,
) -> dict:
    """Judge a screenshot path; falls back to report heuristic then stub schema."""
    path = Path(screenshot) if screenshot else None
    if path and path.exists():
        vision = _try_vision_llm(path)
        if vision:
            return vision
        if report:
            result = judge_from_report(report, screenshot=path)
            result["notes"] = (
                (result.get("notes") or "")
                + " Vision LLM unavailable (DeepSeek text-only); used report heuristic. "
                "Agent2 may overwrite vision_judge.json after reading the PNG."
            ).strip()
            result["screenshot"] = str(path)
            return result
        if allow_stub:
            return finalize_verdict(
                _base_result(
                    empty_fields=[],
                    confidence="ambiguous",
                    source="stub",
                    screenshot=str(path),
                    notes=(
                        "Screenshot saved; no vision LLM configured. "
                        "Agent2 should fill this schema after inspecting the PNG."
                    ),
                )
            )
    if report:
        return judge_from_report(report, screenshot=path)
    return finalize_verdict(
        _base_result(
            empty_fields=[{"label": "no_screenshot_or_report", "kind": "blank"}],
            confidence="ambiguous",
            source="stub",
            notes="Nothing to judge",
        )
    )


def write_vision_judge(result: dict, path: Path | str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(result)
    sibling_png = out.parent / "after_fill.png"
    if not payload.get("screenshot") and _shot_exists(sibling_png):
        payload["screenshot"] = str(sibling_png)
    payload = finalize_verdict(payload)
    payload["never_submit"] = True
    payload["submit_clicked"] = False
    out.write_text(json.dumps(payload, indent=2))
    # Also dump schema sibling for agents
    schema_path = out.with_name("vision_judge.schema.json")
    if not schema_path.exists():
        schema_path.write_text(json.dumps(VISION_JUDGE_SCHEMA, indent=2))
    return out


def self_test() -> dict[str, Any]:
    report = {
        "leftovers": [
            {
                "label": "Why do you want to join us?",
                "type": "INTEREST",
                "essay": True,
                "flash_candidate": True,
                "reason": "no_dummy_essay",
            }
        ],
        "never_submit": True,
        "verdict": "PARTIAL",
    }
    r = judge_from_report(report)
    assert r["complete"] is False
    assert r["verdict"] == "FAIL_BLANK"
    assert r["empty_fields"][0]["kind"] == "essay_empty"
    assert r["never_submit"] is True

    empty_report = {"leftovers": [], "never_submit": True, "verdict": "SUCCESS"}
    r2 = judge_from_report(empty_report)
    # No screenshot → heuristic may COMPLETE (unit/dry path only)
    assert r2["complete"] is True
    assert r2["verdict"] == "COMPLETE"

    # PNG path present → NEVER COMPLETE from heuristic leftovers=0
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tf.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        png_path = Path(tf.name)
    try:
        r3 = judge_from_report(empty_report, screenshot=png_path)
        assert r3["complete"] is False, "heuristic must not COMPLETE when PNG exists"
        assert r3["verdict"] in ("AMBIGUOUS", "FAIL_BLANK")
        assert r3["source"] == "heuristic_report"
        assert r3["confidence"] == "ambiguous"
        r4 = judge_screenshot(png_path, report=empty_report)
        assert r4["complete"] is False
        # Claimed screenshot path (missing file) still blocks COMPLETE
        missing = png_path.parent / "definitely_missing_after_fill.png"
        r5 = judge_from_report(empty_report, screenshot=missing)
        assert r5["complete"] is False
        assert r5["verdict"] == "AMBIGUOUS"
        # write_vision_judge beside PNG must not persist heuristic COMPLETE
        out_vj = png_path.parent / "vision_judge_selftest.json"
        write_vision_judge(
            {
                "complete": True,
                "empty_fields": [],
                "banner_text": "",
                "submit_visible": False,
                "confidence": "high",
                "source": "heuristic_report",
                "screenshot": str(png_path),
            },
            out_vj,
        )
        written = json.loads(out_vj.read_text())
        assert written["complete"] is False
        out_vj.unlink(missing_ok=True)

        # Ashby zip: verified + placeholder readback → FAIL_BLANK, not COMPLETE
        zip_false = {
            "leftovers": [],
            "verdict": "SUCCESS",
            "never_submit": True,
            "filled": [
                {
                    "type": "ADDRESS_ZIP",
                    "label": "Zip",
                    "via": "ashby_widgets",
                    "ok": True,
                    "verified": True,
                    "value": "62701",
                    "readback": "Type here...",
                }
            ],
        }
        r_zip = judge_from_report(zip_false, screenshot=png_path)
        assert r_zip["complete"] is False, "zip placeholder must block COMPLETE"
        assert r_zip["verdict"] == "FAIL_BLANK"
        assert any(e.get("type") == "ADDRESS_ZIP" for e in r_zip["empty_fields"])
        assert any(
            e.get("issue") == "screenshot_empty_but_report_claims_filled"
            for e in r_zip["empty_fields"]
        )
    finally:
        png_path.unlink(missing_ok=True)

    # Schema keys present
    for k in VISION_JUDGE_SCHEMA["required"]:
        assert k in r
    return {
        "ok": True,
        "fail_blank": r["verdict"],
        "complete": r2["verdict"],
        "png_blocks_heuristic": r3["verdict"],
        "missing_shot_blocks": r5["verdict"],
    }


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("screenshot", nargs="?", type=Path, help="after_fill.png path")
    ap.add_argument("--report", type=Path, help="fast_fill report JSON")
    ap.add_argument("--out", type=Path, help="Write vision_judge.json")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--print-schema", action="store_true")
    args = ap.parse_args()

    if args.print_schema:
        print(json.dumps(VISION_JUDGE_SCHEMA, indent=2))
        return 0
    if args.self_test:
        print(json.dumps(self_test(), indent=2))
        print("self-test OK")
        return 0

    report = json.loads(args.report.read_text()) if args.report else None
    result = judge_screenshot(args.screenshot, report=report)
    if args.out:
        write_vision_judge(result, args.out)
        print(f"wrote {args.out}")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
