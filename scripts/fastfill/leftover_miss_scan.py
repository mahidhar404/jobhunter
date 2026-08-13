"""Post Layer 0/1 miss scan → flash_candidates.

Catches fields L0/L1/widgets left blank that historically never entered
``report["leftovers"]`` (esp. Ashby/Lever radios, unselected Yes/No buttons,
empty required selects). Flash only sees ``flash_candidate`` leftovers.

Safety: never invents EEO values; never CAPTCHA; never submit. Dummy policy
answers remain Flash's job when ``--flash-leftovers`` is on.
"""

from __future__ import annotations

import re
from typing import Any

# Live DOM: unanswered choice groups + empty required selects.
# Radios are scanned as groups (not per-input). Ashby Yes/No buttons included.
UNANSWERED_CHOICE_JS = """() => {
  const out = [];
  const isVisible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
    return r.width > 0 && r.height > 0;
  };
  const isEmptyUi = (raw) => {
    const t = (raw || '').trim().toLowerCase();
    if (!t) return true;
    if (t === 'type here...' || t === 'type here' || t.startsWith('type here')) return true;
    if (t === 'start typing...' || t.startsWith('start typing')) return true;
    if (t === 'select' || t === 'select one' || t.startsWith('select ')) return true;
    if (t === 'choose' || t === '—' || t === '-') return true;
    return false;
  };
  const sanitizeLabel = (raw) => {
    let t = String(raw || '').replace(/\\s+/g, ' ').trim();
    t = t.replace(/\\b(current teammates|please apply via|internal career site|employee referral portal)[\\s\\S]*/i, '').trim();
    return t.slice(0, 160);
  };
  const choiceGroupAnswered = (radios) => {
    if (!radios || !radios.length) return false;
    if (radios.some((r) => r.checked || r.getAttribute('aria-checked') === 'true')) return true;
    const root = radios[0].closest('[data-automation-id*="formField"], fieldset, [role="radiogroup"], [role="group"]')
      || radios[0].parentElement;
    if (root) {
      if (root.querySelector('input[type="radio"]:checked')) return true;
      if (root.querySelector('[role="radio"][aria-checked="true"]')) return true;
      if (root.querySelector('input[type="radio"][aria-checked="true"]')) return true;
    }
    return false;
  };
  const labelNear = (el) => {
    const wrap = el.closest(
      '.ashby-application-form-field-entry, [class*="_fieldEntry_"], '
      + 'fieldset, [role="group"], [data-automation-id*="formField"], '
      + 'label, .application-question, .question, [class*="question"]'
    );
    let lab = '';
    // Prefer fieldset / formField question text over inner Yes/No labels.
    const fieldRoot = el.closest('[data-automation-id*="formField"], fieldset, [role="radiogroup"], [role="group"]');
    if (fieldRoot) {
      const leg = fieldRoot.querySelector(
        'legend, label.ashby-application-form-question-title, '
        + 'label[class*="_heading_"], [data-automation-id*="label"]'
      );
      lab = sanitizeLabel((leg && (leg.innerText || leg.textContent)) || '');
    }
    if (!lab && wrap) {
      const L = wrap.querySelector(
        'legend, label.ashby-application-form-question-title, '
        + 'label[class*="_heading_"], [data-automation-id*="label"], label'
      );
      lab = sanitizeLabel((L && (L.innerText || L.textContent)) || '');
      if (!lab || /^(yes|no)$/i.test(lab)) {
        lab = sanitizeLabel(wrap.innerText || wrap.textContent || '');
      }
    }
    if (!lab || /^(yes|no)$/i.test(lab)) {
      if (fieldRoot) {
        lab = sanitizeLabel(fieldRoot.innerText || fieldRoot.textContent || '');
      }
    }
    if (!lab) {
      lab = sanitizeLabel(el.getAttribute('aria-label') || el.name || el.id || '');
    }
    return lab;
  };
  const requiredish = (el, label) => {
    if (el.required || el.getAttribute('aria-required') === 'true') return true;
    if (/\\*/.test(label || '')) return true;
    if (el.closest('[data-required="true"], .required, [aria-required="true"]')) return true;
    // Optional radios (EEO / marketing without * / required) are NOT required misses
    return false;
  };
  const push = (row) => {
    const label = sanitizeLabel(String(row.label || ''));
    if (!label) return;
    if (/^(current teammates|please apply via)/i.test(label)) return;
    // Skip honeypot / cookie / pure consent noise that is never Flash-worthy
    const low = label.toLowerCase();
    if (/^yes$|^no$/.test(low) && low.length < 4) return;
    out.push({
      label,
      kind: row.kind || 'choice',
      reason: row.reason || 'unanswered_choice',
      name: String(row.name || '').slice(0, 80),
      selector: String(row.selector || '').slice(0, 160),
    });
  };

  // --- Native radio groups (one row per name) ---
  const radioNames = new Set();
  document.querySelectorAll('input[type=radio][name]').forEach((r) => {
    if (r.name) radioNames.add(r.name);
  });
  for (const name of radioNames) {
    const group = Array.from(
      document.querySelectorAll('input[type=radio][name="' + CSS.escape(name) + '"]')
    ).filter(isVisible);
    if (!group.length) continue;
    if (choiceGroupAnswered(group)) continue;
    const el = group[0];
    const label = labelNear(el);
    if (!requiredish(el, label)) continue;
    push({
      label: label || name,
      kind: 'radio_group',
      reason: 'unanswered_radio_group',
      name,
      selector: 'input[type=radio][name="' + name + '"]',
    });
  }

  // --- Required / aria-required selects still on Select… ---
  document.querySelectorAll(
    'select[required], select[aria-required="true"], select'
  ).forEach((el) => {
    if (!isVisible(el)) return;
    const label = labelNear(el);
    const star = /\\*/.test(label);
    if (!el.required && el.getAttribute('aria-required') !== 'true' && !star) return;
    const v = (el.value || '').trim();
    const idx = el.selectedIndex;
    const optText = (el.options && idx >= 0 && el.options[idx])
      ? (el.options[idx].text || '').trim() : '';
    if (!isEmptyUi(v) && idx > 0 && !isEmptyUi(optText) && !/^select/i.test(optText)) return;
    if (idx > 0 && optText && !isEmptyUi(optText) && !/^select/i.test(optText)) return;
    if (idx <= 0 || isEmptyUi(v) || isEmptyUi(optText) || /^select/i.test(optText)) {
      push({
        label: label || el.name || el.id || 'select',
        kind: 'select',
        reason: 'empty_required_select',
        name: el.name || '',
        selector: el.name ? ('select[name="' + el.name + '"]') : '',
      });
    }
  });

  // --- Greenhouse react-select / aria-required combobox still Select… ---
  document.querySelectorAll(
    '.select__container, .select-shell, [role="combobox"][aria-required="true"]'
  ).forEach((root) => {
    if (!isVisible(root)) return;
    const label = labelNear(root);
    const star = /\\*/.test(label);
    const req = root.getAttribute('aria-required') === 'true'
      || !!(root.querySelector && root.querySelector('[aria-required="true"]'))
      || star;
    if (!req && !star) return;
    const wrap = root.closest('[data-automation-id*="formField"]') || root.parentElement;
    const waid = ((wrap && wrap.getAttribute('data-automation-id')) || '').toLowerCase();
    const wtxt = ((wrap && (wrap.innerText || wrap.textContent)) || '').replace(/\\s+/g, ' ');
    if (/countryphonecode|phone.?country|phonenumber--country/.test(waid)
        && /united\\s*states(\\s*of\\s*america)?\\s*\\(\\s*\\+\\s*1\\s*\\)/i.test(wtxt)) {
      return;
    }
    const sv = root.querySelector('.select__single-value');
    const shown = ((sv && (sv.textContent || sv.innerText)) || '').trim();
    if (shown && !isEmptyUi(shown) && !/^select/i.test(shown)) return;
    const aria = (root.getAttribute('aria-label') || '').trim();
    if (aria && !isEmptyUi(aria) && !/^select/i.test(aria) && root.getAttribute('role') === 'combobox') {
      // open combobox may expose current value in aria-label — treat non-select as filled
      if (!/^select/i.test(aria) && aria.length > 2 && !isEmptyUi(aria)) return;
    }
    push({
      label: label || aria || 'combobox',
      kind: 'combobox',
      reason: 'empty_required_select',
      name: '',
      selector: '',
    });
  });

  // --- Ashby Yes/No button pairs (not native radios) ---
  document.querySelectorAll(
    '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
  ).forEach((el) => {
    if (!isVisible(el)) return;
    const labEl = el.querySelector(
      'label.ashby-application-form-question-title, label[class*="_heading_"], label'
    );
    const label = ((labEl && (labEl.innerText || labEl.textContent)) || '').replace(/\\s+/g, ' ').trim();
    if (!label) return;
    const yesnoWrap = el.querySelector('[class*="_yesno_"]');
    let yesnoBtns = yesnoWrap
      ? Array.from(yesnoWrap.querySelectorAll('button, [role="button"]'))
      : [];
    if (!yesnoWrap) {
      yesnoBtns = Array.from(el.querySelectorAll('button, [role="button"]')).filter((b) => {
        const t = (b.innerText || b.textContent || '').trim().toLowerCase();
        return t === 'yes' || t === 'no';
      });
    }
    const hasYes = yesnoBtns.some((b) => /^yes$/i.test((b.innerText || b.textContent || '').trim()));
    const hasNo = yesnoBtns.some((b) => /^no$/i.test((b.innerText || b.textContent || '').trim()));
    if (!(hasYes && hasNo)) return;
    const scope = yesnoWrap || el;
    const on = scope.querySelector(
      'button[aria-pressed="true"], button[class*="selected"], button[data-selected="true"]'
    );
    let selected = on ? (on.innerText || on.textContent || '').trim() : '';
    if (!selected) {
      for (const b of yesnoBtns) {
        const cls = (b.className || '').toLowerCase();
        if (/selected|active|pressed/.test(cls)) {
          selected = (b.innerText || b.textContent || '').trim();
          break;
        }
        if (b.getAttribute('aria-pressed') === 'true'
            || b.getAttribute('data-selected') === 'true') {
          selected = (b.innerText || b.textContent || '').trim();
          break;
        }
      }
    }
    if (selected) return;
    // Native radios inside entry already covered — skip if any radio checked
    const radios = el.querySelectorAll('input[type=radio]');
    if (radios.length && choiceGroupAnswered(Array.from(radios))) return;
    push({
      label,
      kind: 'yesno_segmented',
      reason: 'unanswered_ashby_yesno',
      name: el.getAttribute('data-field-path') || '',
      selector: '',
    });
  });

  // --- Ashby field-entry radio GROUPS (one row per question, not by name) ---
  document.querySelectorAll(
    '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
  ).forEach((el) => {
    if (!isVisible(el)) return;
    const labEl = el.querySelector(
      'label.ashby-application-form-question-title, label[class*="_heading_"], label'
    );
    const label = sanitizeLabel((labEl && (labEl.innerText || labEl.textContent)) || '');
    if (!label) return;
    const native = Array.from(el.querySelectorAll('input[type=radio]')).filter(isVisible);
    const roles = Array.from(el.querySelectorAll('[role=radio]')).filter(isVisible);
    const group = native.length ? native : roles;
    if (!group.length) return;
    if (group.length < 2 && !(group.length === 1 && requiredish(group[0], label))) return;
    if (choiceGroupAnswered(native.length ? native : roles)) return;
    if (!requiredish(group[0], label) && !/\\*/.test(label)) return;
    push({
      label,
      kind: 'radio_group',
      reason: 'unanswered_radio_group',
      name: el.getAttribute('data-field-path') || (group[0].name || ''),
      selector: '',
    });
  });

  // --- Ashby required consent checkboxes (TERMS dummy-yes; skip marketing) ---
  document.querySelectorAll(
    '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
  ).forEach((el) => {
    if (!isVisible(el)) return;
    const labEl = el.querySelector(
      'label.ashby-application-form-question-title, label[class*="_heading_"], label'
    );
    let label = sanitizeLabel((labEl && (labEl.innerText || labEl.textContent)) || '');
    const checks = Array.from(el.querySelectorAll('input[type=checkbox]')).filter(isVisible);
    if (!checks.length) return;
    if (checks.some((c) => c.checked)) return;
    if (!label) {
      const c0 = checks[0];
      label = sanitizeLabel(
        ((c0.labels && c0.labels[0] && c0.labels[0].innerText) || c0.name || 'I agree')
      );
    }
    const low = label.toLowerCase();
    if (/marketing|newsletter|sms|promotional|talent\\s*community|opt[\\s_-]*in/.test(low)) return;
    const consentish = /^consent\\s*\\*?$/.test(low)
      || /i\\s+(agree|consent)|terms\\s*(and|&)\\s*conditions|privacy|data[\\s_-]*consent/.test(low);
    if (!consentish && !/\\*/.test(label)) return;
    if (!consentish) return;
    push({
      label,
      kind: 'checkbox',
      reason: 'unanswered_ashby_consent',
      name: checks[0].id || el.getAttribute('data-field-path') || '',
      selector: '',
    });
  });

  // Dedup by label+kind
  const seen = new Set();
  const uniq = [];
  for (const row of out) {
    const k = ((row.label || '') + '|' + (row.kind || '')).toLowerCase().slice(0, 100);
    if (seen.has(k)) continue;
    seen.add(k);
    uniq.push(row);
  }
  return uniq.slice(0, 60);
}
"""


def _norm_key(value: Any) -> str:
    return " ".join(str(value or "").lower().split())[:100]


def _compact_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _row_compact(row: dict | None) -> str:
    if not isinstance(row, dict):
        return ""
    return _compact_token(
        " ".join(
            str(row.get(k) or "")
            for k in ("label", "automation_id", "type", "name", "id", "selector")
        )
    )


_ABSENT_LEFTOVER_REASONS = frozenset(
    {
        "not_in_dom",
        "not_visible",
        "radio_not_found",
        "selector_missing",
        "no_matching_option",
    }
)

# Pack-required optional aids that 0842 NXP never mounts (US address, no prior-worker radio).
_OPTIONAL_ABSENT_COMPACT = (
    "addressline2",
    "regionsubdivision1",
    "workedherebefore",
)


def _reason_core(reason: str) -> str:
    r = str(reason or "").lower().strip()
    if r.startswith("l01_miss_scan:"):
        r = r.split(":", 1)[-1]
    if r.startswith("live_required_empty:"):
        r = r.split(":", 1)[-1]
    return r


def _row_is_phone_country(row: dict | None) -> bool:
    compact = _row_compact(row)
    if "countryphonecode" in compact:
        return True
    lab = str((row or {}).get("label") or "").lower()
    return "phone" in lab and "country" in lab


def _phone_country_done_in_report(report: dict | None) -> bool:
    """True when a verified fill already committed US (+1) phone country."""
    if not isinstance(report, dict):
        return False
    for f in report.get("filled") or []:
        if not isinstance(f, dict):
            continue
        if not _row_is_phone_country(f) and str(f.get("type") or "").upper() not in (
            "PHONE_COUNTRY_CODE",
            "COUNTRYPHONECODE",
        ):
            continue
        done = False
        try:
            from fill_verify import is_verified_fill_row

            done = bool(is_verified_fill_row(f))
        except Exception:
            done = False
        if done:
            return True
        rb = str(f.get("readback") or "")
        if (f.get("verified") or f.get("ok")) and re.search(
            r"united\s*states.*\(\s*\+\s*1\s*\)|[1-9]\d*\s+items?\s+selected",
            rb,
            re.I,
        ):
            return True
    return False


def is_invented_leftover(row: dict | None, report: dict | None = None) -> bool:
    """True when leftover is pack theater, not a live unanswered widget.

    0842 classes: addressLine2/county ``not_in_dom``, worked_here ``radio_not_found``,
    ``phonenumber--countryphonecode`` live_required_empty while chip already US +1.
    Never demotes First/Last Name or other real required empties.
    """
    if not isinstance(row, dict):
        return False
    reason = str(row.get("reason") or "").lower()
    core = _reason_core(reason)
    compact = _row_compact(row)

    if any(tok in compact for tok in _OPTIONAL_ABSENT_COMPACT):
        if core in _ABSENT_LEFTOVER_REASONS or reason in _ABSENT_LEFTOVER_REASONS:
            return True
        if "radio_not_found" in reason or "not_in_dom" in reason:
            return True

    if _row_is_phone_country(row) and _phone_country_done_in_report(report):
        return True

    try:
        from workday_date_readback import (
            is_date_spin_theater_label,
            is_optional_gpa_label,
        )

        blob = " ".join(
            str(row.get(k) or "")
            for k in ("label", "automation_id", "id", "name", "type")
        )
        if is_date_spin_theater_label(row.get("label")) or is_date_spin_theater_label(
            row.get("id")
        ) or is_date_spin_theater_label(row.get("automation_id")):
            return True
        if is_date_spin_theater_label(blob) and (
            core in (
                "unclassified",
                "empty_required_date_display",
                "empty_required_date_spin",
                "empty_required_date_field",
                "offscreen_skip",
            )
            or "unclassified" in reason
            or "date_display" in reason
            or "offscreen_skip" in reason
        ):
            return True
        if is_optional_gpa_label(row.get("label")) or is_optional_gpa_label(
            row.get("automation_id")
        ) or is_optional_gpa_label(row.get("id")):
            return True
    except Exception:
        pass

    # 1116Z: Job Title*/From*/To* leftovers while experience skip-if-done already
    # committed dummy values — same class as contact invented requireds.
    try:
        from field_done import filter_required_empty_from_report

        fake = {
            "id": str(
                row.get("automation_id")
                or row.get("id")
                or row.get("name")
                or row.get("label")
                or ""
            )[:80],
            "label": str(row.get("label") or "")[:160],
            "reason": core or "empty_required_input",
        }
        if fake["id"] or fake["label"]:
            kept = filter_required_empty_from_report(report or {}, [fake])
            if not kept:
                return True
    except Exception:
        pass
    return False


def demote_invented_leftovers(report: dict) -> int:
    """Drop invented leftovers from ``report['leftovers']``. Return count dropped."""
    leftovers = [u for u in (report.get("leftovers") or []) if isinstance(u, dict)]
    kept: list[dict] = []
    dropped: list[dict] = []
    for u in leftovers:
        if is_invented_leftover(u, report):
            dropped.append(u)
        else:
            kept.append(u)
    report["leftovers"] = kept
    report["invented_leftover_count"] = len(dropped)
    if dropped:
        report["invented_leftovers_dropped"] = [
            {
                "label": str(u.get("label") or "")[:80],
                "reason": str(u.get("reason") or "")[:64],
                "automation_id": str(u.get("automation_id") or "")[:80],
            }
            for u in dropped[:20]
        ]
    return len(dropped)


def _verified_worked_here(report: dict) -> bool:
    """True when WORKED_HERE_BEFORE / worked_here_before was verified this run."""
    for f in report.get("filled") or []:
        if not isinstance(f, dict):
            continue
        try:
            from fill_verify import is_verified_fill_row

            if not is_verified_fill_row(f):
                continue
        except Exception:
            if not (f.get("verified") or f.get("ok")):
                continue
        blob = " ".join(
            [
                str(f.get("type") or ""),
                str(f.get("automation_id") or ""),
                str(f.get("readback") or ""),
            ]
        ).lower()
        if "worked_here" in blob or f.get("type") == "WORKED_HERE_BEFORE":
            return True
    return False


def _miss_is_worked_here_question(miss: dict) -> bool:
    lab = str(miss.get("label") or "").lower()
    return bool(
        re.search(
            r"previously (been )?employed|employed by .+ previously|"
            r"have you been employed|previously worked|worked .+ before",
            lab,
        )
    )


def leftover_identity_keys(report: dict) -> set[str]:
    """Keys already tracked as leftovers or verified fills."""
    keys: set[str] = set()
    phone_country_done = False
    for u in report.get("leftovers") or []:
        if not isinstance(u, dict):
            continue
        for part in (u.get("label"), u.get("type"), u.get("selector"), u.get("name"), u.get("automation_id")):
            k = _norm_key(part)
            if k:
                keys.add(k)
    for f in report.get("filled") or []:
        if not isinstance(f, dict):
            continue
        try:
            from fill_verify import is_verified_fill_row

            if not is_verified_fill_row(f):
                continue
        except Exception:
            if not (f.get("verified") or f.get("ok")):
                continue
        for part in (f.get("label"), f.get("type"), f.get("selector"), f.get("automation_id")):
            k = _norm_key(part)
            if k:
                keys.add(k)
        if _row_is_phone_country(f) or str(f.get("type") or "").upper() in (
            "PHONE_COUNTRY_CODE",
            "COUNTRYPHONECODE",
        ):
            phone_country_done = True
    if phone_country_done:
        keys.add("phonenumber--countryphonecode")
        keys.add("countryphonecode")
        keys.add("phone country code")
    return keys


def misses_to_leftover_rows(misses: list[dict] | None) -> list[dict]:
    """Convert scan rows into flash_candidate leftover dicts."""
    rows: list[dict] = []
    for m in misses or []:
        if not isinstance(m, dict):
            continue
        label = str(m.get("label") or "").strip()
        if not label:
            continue
        rows.append(
            {
                "label": label[:100],
                "type": None,
                "html_type": m.get("kind") or "choice",
                "selector": str(m.get("selector") or "")[:160],
                "name": str(m.get("name") or "")[:80],
                "reason": f"l01_miss_scan:{m.get('reason') or 'unanswered_choice'}",
                "flash_candidate": True,
                "via": "leftover_miss_scan",
                "kind": m.get("kind"),
            }
        )
    return rows


def merge_miss_leftovers(report: dict, misses: list[dict] | None) -> int:
    """Append new miss-scan leftovers; return count added."""
    existing = leftover_identity_keys(report)
    added = 0
    for row in misses_to_leftover_rows(misses):
        if is_invented_leftover(row, report):
            continue
        lab = _norm_key(row.get("label"))
        sel = _norm_key(row.get("selector"))
        name = _norm_key(row.get("name"))
        if (lab and lab in existing) or (sel and sel in existing) or (name and name in existing):
            continue
        if lab and any(
            (lab[:40] in ek or ek[:40] in lab) for ek in existing if ek and len(ek) >= 12
        ):
            continue
        report.setdefault("leftovers", []).append(row)
        for k in (lab, sel, name):
            if k:
                existing.add(k)
        added += 1
    if added:
        report["l01_miss_scan_added"] = int(report.get("l01_miss_scan_added") or 0) + added
    return added


async def scan_unanswered_choices(page) -> list[dict]:
    """Evaluate live DOM for unanswered radios / yesno / empty required selects."""
    try:
        raw = await page.evaluate(UNANSWERED_CHOICE_JS)
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


async def promote_l01_misses(page, report: dict) -> dict:
    """Scan page after L0/1; promote unanswered choices to flash_candidates.

    Also folds classic required_empty rows that demote may have missed when
    radios were skipped by GENERIC_REQUIRED_EMPTY_JS.
    """
    summary: dict[str, Any] = {"scanned": 0, "added": 0, "misses": []}
    misses = await scan_unanswered_choices(page)
    if _verified_worked_here(report):
        misses = [
            m
            for m in misses
            if not _miss_is_worked_here_question(m)
        ]
    summary["scanned"] = len(misses)
    summary["misses"] = [
        {"label": m.get("label"), "kind": m.get("kind"), "reason": m.get("reason")}
        for m in misses[:40]
    ]
    added = merge_miss_leftovers(report, misses)
    dropped = demote_invented_leftovers(report)
    summary["added"] = added
    summary["invented_dropped"] = dropped

    # Sync required_empty_after_fill only for required-looking misses (FILL-010).
    # Optional unanswered radios stay flash_candidates via leftovers, but must
    # not false-FAIL Ready / inflate required_empty.
    empties = list(report.get("required_empty_after_fill") or [])
    empty_ids = {str(e.get("id") or "").lower() for e in empties if isinstance(e, dict)}
    for m in misses:
        label = str(m.get("label") or "")
        reason = str(m.get("reason") or "")
        required_looking = (
            "*" in label
            or "required" in reason.lower()
            or m.get("kind") in ("select", "combobox")
        )
        if not required_looking:
            continue
        eid = str(m.get("name") or m.get("label") or "")[:80]
        if not eid or eid.lower() in empty_ids:
            continue
        empties.append(
            {
                "id": eid,
                "reason": reason[:64],
                "label": label[:160],
            }
        )
        empty_ids.add(eid.lower())
    try:
        from field_done import (
            filter_phone_country_false_empties,
            filter_required_empty_from_report,
        )
        from verified_select import phone_country_verified_snips_from_report

        snips = phone_country_verified_snips_from_report(report)
        snip = snips[0] if snips else ""
        empties = filter_phone_country_false_empties(empties, snip)
        empties = filter_required_empty_from_report(report, empties)
    except Exception:
        pass
    report["required_empty_after_fill"] = empties
    report["l01_miss_scan"] = summary
    return summary


def self_test() -> None:
    """Pure unit checks (no browser)."""
    report: dict = {"leftovers": [], "filled": []}
    misses = [
        {
            "label": "Are you authorized to work in the US?*",
            "kind": "radio_group",
            "reason": "unanswered_radio_group",
            "name": "cards[abc]",
            "selector": 'input[type=radio][name="cards[abc]"]',
        },
        {
            "label": "Gender",
            "kind": "select",
            "reason": "empty_required_select",
            "name": "eeo[gender]",
            "selector": 'select[name="eeo[gender]"]',
        },
        {
            "label": "Are you currently based in Latin America?",
            "kind": "yesno_segmented",
            "reason": "unanswered_ashby_yesno",
            "name": "",
            "selector": "",
        },
        {
            "label": "Consent*",
            "kind": "checkbox",
            "reason": "unanswered_ashby_consent",
            "name": "data_consent",
            "selector": "",
        },
    ]
    n = merge_miss_leftovers(report, misses)
    assert n == 4, n
    assert all(u.get("flash_candidate") is True for u in report["leftovers"])
    assert all(str(u.get("reason") or "").startswith("l01_miss_scan:") for u in report["leftovers"])
    # Dedupe on second merge
    n2 = merge_miss_leftovers(report, misses)
    assert n2 == 0, n2
    # Verified fill suppresses re-add
    report2: dict = {
        "leftovers": [],
        "filled": [
            {
                "label": "Are you authorized to work in the US?*",
                "type": "WORK_AUTH",
                "ok": True,
                "verified": True,
                "value": "Yes",
                "readback": "Yes",
            }
        ],
    }
    n3 = merge_miss_leftovers(report2, misses[:1])
    assert n3 == 0, n3
    rows = misses_to_leftover_rows(misses)
    assert rows[0]["via"] == "leftover_miss_scan"

    nxp = {
        "filled": [
            {
                "type": "countryPhoneCode",
                "automation_id": "countryPhoneCode",
                "ok": True,
                "verified": True,
                "value": "United States (+1)",
                "readback": "United States of America (+1)",
            }
        ],
        "leftovers": [
            {
                "label": "addressSection_addressLine2",
                "reason": "not_in_dom",
                "automation_id": "addressSection_addressLine2",
            },
            {
                "label": "phonenumber--countryphonecode",
                "reason": "live_required_empty:empty_required_input",
            },
            {"label": "First Name*", "reason": "live_required_empty:empty_required_input"},
        ],
    }
    assert is_invented_leftover(nxp["leftovers"][0], nxp)
    assert is_invented_leftover(nxp["leftovers"][1], nxp)
    assert not is_invented_leftover(nxp["leftovers"][2], nxp)
    assert demote_invented_leftovers(nxp) == 2
    assert [u["label"] for u in nxp["leftovers"]] == ["First Name*"]
    month_row = {"label": "Month — From*", "reason": "unclassified"}
    month_plain = {"label": "Month", "reason": "unclassified"}
    gpa_row = {"label": "Overall Result (GPA)", "reason": "unclassified"}
    gpa_required = {
        "label": "Overall Result (GPA)",
        "reason": "live_required_empty:empty_required_input",
        "automation_id": "formField-gpa",
    }
    assert is_invented_leftover(month_row, {"leftovers": [month_row]})
    assert is_invented_leftover(month_plain, {"leftovers": [month_plain]})
    assert is_invented_leftover(gpa_row, {"leftovers": [gpa_row]})
    assert is_invented_leftover(gpa_required, {"leftovers": [gpa_required]})
    print("leftover_miss_scan.self_test: OK")


if __name__ == "__main__":
    self_test()
