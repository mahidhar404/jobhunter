"""Form gaps oracle — ChamPro-style completeness after Save/validation.

Workday (and similar) only surface required errors after Save and Continue.
Coverage % is not enough: collect visible validation + still-empty required
fields and block Ready when hard gaps remain.

Never clicks final Apply/Submit. ADVANCE-only probes are opt-in via caller.
"""

from __future__ import annotations

import re
from typing import Any

# Soft signals that a field still needs a value after Save.
_GAP_LABEL_RE = re.compile(
    r"is\s+required|must\s+have\s+a\s+value|please\s+(enter|select|complete)|"
    r"this\s+field\s+is\s+required|required\s+field|errors?\s+found",
    re.I,
)

# Sibling instructional copy (Owens & Minor "CURRENT TEAMMATES…") — not a blank control.
_INSTRUCTION_GAP_RE = re.compile(
    r"(^current teammates\b|please apply via\b|internal career site\b|"
    r"employee referral portal\b|referral portal\b)",
    re.I,
)


def is_instruction_only_gap(label: str) -> bool:
    """True when scraped label is help/instruction text, not a question control."""
    return bool(_INSTRUCTION_GAP_RE.search(label or ""))


COLLECT_GAPS_JS = """() => {
  const out = [];
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0
      && window.getComputedStyle(el).visibility !== 'hidden';
  };
  const sanitizeLabel = (raw) => {
    let t = String(raw || '').replace(/\\s+/g, ' ').trim();
    t = t.replace(/\\b(current teammates|please apply via|internal career site|employee referral portal)[\\s\\S]*/i, '').trim();
    t = t.replace(/\\bSelect One\\b/ig, '').trim();
    return t.slice(0, 160);
  };
  const questionLabel = (el) => {
    const wrap = el.closest('[data-automation-id*="formField"], fieldset, [role="group"]');
    if (wrap) {
      const leg = wrap.querySelector('legend, [data-automation-id*="label"], label');
      if (leg) {
        const t = sanitizeLabel(leg.innerText || leg.textContent || '');
        if (t) return t;
      }
    }
    const wrap2 = el.closest('[data-automation-id*="formField"], fieldset, label, [role="group"]');
    const raw = ((wrap2 && (wrap2.innerText || wrap2.textContent)) || el.getAttribute('aria-label') || el.name || '');
    return sanitizeLabel(raw);
  };
  const choiceGroupAnswered = (radios, typ) => {
    if (!radios || !radios.length) return false;
    if (radios.some((r) => r.checked || r.getAttribute('aria-checked') === 'true')) return true;
    const root = radios[0].closest('[data-automation-id*="formField"], fieldset, [role="radiogroup"], [role="group"]')
      || radios[0].parentElement;
    if (root) {
      if (root.querySelector('input[type="' + typ + '"]:checked')) return true;
      if (root.querySelector('[role="' + (typ === 'checkbox' ? 'checkbox' : 'radio') + '"][aria-checked="true"]')) return true;
      if (root.querySelector('input[type="' + typ + '"][aria-checked="true"]')) return true;
    }
    return false;
  };
  const push = (label, reason, aid) => {
    const L = sanitizeLabel(label);
    if (!L) return;
    if (/^(current teammates|please apply via)/i.test(L)) return;
    out.push({ label: L, reason: reason || 'invalid', automation_id: aid || '' });
  };
  // Explicit ATS / form error nodes (always keep — Workday etc.)
  for (const sel of [
    '[data-automation-id="errorMessage"]',
    '[data-automation-id="formErrorMessage"]',
    '[data-automation-id="errorBanner"]',
    '[aria-invalid="true"]',
  ]) {
    for (const el of document.querySelectorAll(sel)) {
      if (!vis(el)) continue;
      const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
      if (!t || t.length > 240) continue;
      const wrap = el.closest('[data-automation-id*="formField"], fieldset, [role="group"]');
      const aid = (wrap && wrap.getAttribute('data-automation-id')) || el.getAttribute('data-automation-id') || '';
      push(t, 'error_node', aid);
    }
  }
  // FILL3-003 / FILL2-S01: [role=alert] may be cookie/marketing noise.
  // Tag as alert_node; Python normalize_gaps filters via looks_like_gap_message.
  for (const el of document.querySelectorAll('[role="alert"]')) {
    if (!vis(el)) continue;
    const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
    if (!t || t.length > 240) continue;
    const wrap = el.closest('[data-automation-id*="formField"], fieldset, [role="group"]');
    const aid = (wrap && wrap.getAttribute('data-automation-id')) || el.getAttribute('data-automation-id') || '';
    push(t, 'alert_node', aid);
  }
  // Required empties still visible — form controls only (never bare div[aria-required]).
  for (const el of document.querySelectorAll(
    'input[required], input[aria-required="true"], '
    + 'select[required], select[aria-required="true"], '
    + 'textarea[required], textarea[aria-required="true"], '
    + '[role="combobox"][aria-required="true"], [role="radio"][aria-required="true"]'
  )) {
    if (!vis(el)) continue;
    const tag = (el.tagName || '').toLowerCase();
    const typ = (el.type || '').toLowerCase();
    if (typ === 'hidden' || typ === 'submit' || typ === 'button') continue;
    let empty = false;
    if (tag === 'select') {
      empty = !el.value || el.selectedIndex <= 0;
    } else if (typ === 'radio' || typ === 'checkbox') {
      const name = el.name;
      const group = name
        ? [...document.querySelectorAll('input[type="' + typ + '"][name="' + CSS.escape(name) + '"]')]
        : [el];
      empty = !choiceGroupAnswered(group, typ);
    } else if (el.getAttribute('role') === 'radio') {
      const root = el.closest('[data-automation-id*="formField"], fieldset, [role="radiogroup"], [role="group"]');
      empty = !(root && root.querySelector('[role="radio"][aria-checked="true"], input[type="radio"]:checked, input[type="radio"][aria-checked="true"]'));
    } else {
      // Workday multi-select filter inputs stay "empty" while chips show "N items selected"
      const wrap = el.closest('[data-automation-id*="formField"], [data-automation-id="multiSelectContainer"], fieldset');
      const wrapText = ((wrap && (wrap.innerText || wrap.textContent)) || '').toLowerCase();
      if (/[1-9]\\d*\\s+items?\\s+selected/.test(wrapText)) {
        empty = false;
      } else {
        empty = !(el.value || '').trim();
      }
    }
    if (!empty) continue;
    const wrap = el.closest('[data-automation-id*="formField"], fieldset, label, [role="group"]');
    const lab = questionLabel(el) || el.getAttribute('aria-label') || el.name || '';
    const aid = (wrap && wrap.getAttribute('data-automation-id')) || el.getAttribute('data-automation-id') || '';
    push(lab || aid || 'required', 'required_empty', aid);
  }
  // Dedupe by label
  const seen = new Set();
  const uniq = [];
  for (const g of out) {
    const k = (g.label || '').toLowerCase().slice(0, 80);
    if (seen.has(k)) continue;
    seen.add(k);
    uniq.push(g);
  }
  return uniq.slice(0, 40);
}
"""


def looks_like_gap_message(text: str) -> bool:
    """True when text looks like a validation / required-field gap (not cookie fluff).

    FILL3-003 / FILL2-S01: used to filter ``[role=alert]`` scrape noise so
    marketing/cookie banners do not block Ready.
    """
    return bool(_GAP_LABEL_RE.search(text or ""))


def normalize_gaps(raw: list[Any] | None) -> list[dict[str, str]]:
    """Normalize gap dicts for report / Ready gating.

    ``alert_node`` rows (from ``[role=alert]``) must pass ``looks_like_gap_message``;
    Workday ``error_node`` / required_empty / probe_error always keep.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for g in raw or []:
        if not isinstance(g, dict):
            continue
        label = str(g.get("label") or "").strip()
        if not label:
            continue
        reason = str(g.get("reason") or "gap")[:64]
        # FILL3-003 / FILL2-S01: drop cookie/info alerts that are not validation.
        if reason == "alert_node" and not looks_like_gap_message(label):
            continue
        # Instructional sibling copy (not an unanswered control).
        if reason == "required_empty" and is_instruction_only_gap(label):
            continue
        key = label.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "label": label[:160],
                "reason": reason,
                "automation_id": str(g.get("automation_id") or "")[:80],
            }
        )
    return out


def gaps_block_ready(gaps: list[dict] | None) -> bool:
    """True when hard form gaps must keep Ready false."""
    return bool(normalize_gaps(gaps))


async def collect_form_gaps(page) -> list[dict[str, str]]:
    """Read current validation + required-empty gaps from the live DOM.

    FILL2-003: fail closed on evaluate errors — never return success-shaped [].
    FILL3-003: ``[role=alert]`` filtered through ``looks_like_gap_message``.
    """
    try:
        raw = await page.evaluate(COLLECT_GAPS_JS)
    except Exception as e:
        return [
            {
                "label": f"gap_probe_error: {str(e)[:120]}",
                "reason": "probe_error",
                "automation_id": "",
            }
        ]
    return normalize_gaps(raw if isinstance(raw, list) else [])


def merge_gaps_into_report(report: dict, gaps: list[dict] | None) -> list[dict[str, str]]:
    """Store gaps on report; set blocker when non-empty."""
    norm = normalize_gaps(gaps)
    report["gaps_after_save"] = norm
    if norm:
        report.setdefault("blocker", "page_incomplete")
        report["gaps_block_ready"] = True
    else:
        report["gaps_block_ready"] = False
    return norm
