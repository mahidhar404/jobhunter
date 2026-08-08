"""Post Layer 0/1 miss scan → flash_candidates.

Catches fields L0/L1/widgets left blank that historically never entered
``report["leftovers"]`` (esp. Ashby/Lever radios, unselected Yes/No buttons,
empty required selects). Flash only sees ``flash_candidate`` leftovers.

Safety: never invents EEO values; never CAPTCHA; never submit. Dummy policy
answers remain Flash's job when ``--flash-leftovers`` is on.
"""

from __future__ import annotations

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
  const labelNear = (el) => {
    const wrap = el.closest(
      '.ashby-application-form-field-entry, [class*="_fieldEntry_"], '
      + 'fieldset, [role="group"], [data-automation-id*="formField"], '
      + 'label, .application-question, .question, [class*="question"]'
    );
    let lab = '';
    if (wrap) {
      const L = wrap.querySelector(
        'label.ashby-application-form-question-title, legend, '
        + 'label[class*="_heading_"], label, [class*="question"]'
      );
      lab = ((L && (L.innerText || L.textContent)) || wrap.innerText || '').replace(/\\s+/g, ' ').trim();
    }
    if (!lab) {
      lab = (el.getAttribute('aria-label') || el.name || el.id || '').trim();
    }
    return lab.slice(0, 160);
  };
  const requiredish = (el, label) => {
    if (el.required || el.getAttribute('aria-required') === 'true') return true;
    if (/\\*/.test(label || '')) return true;
    if (el.closest('[data-required="true"], .required, [aria-required="true"]')) return true;
    // Optional radios (EEO / marketing without * / required) are NOT required misses
    return false;
  };
  const push = (row) => {
    const label = String(row.label || '').replace(/\\s+/g, ' ').trim().slice(0, 160);
    if (!label) return;
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
    if (group.some((r) => r.checked)) continue;
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
    if (radios.length && Array.from(radios).some((r) => r.checked)) return;
    push({
      label,
      kind: 'yesno_segmented',
      reason: 'unanswered_ashby_yesno',
      name: el.getAttribute('data-field-path') || '',
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


def leftover_identity_keys(report: dict) -> set[str]:
    """Keys already tracked as leftovers or verified fills."""
    keys: set[str] = set()
    for u in report.get("leftovers") or []:
        if not isinstance(u, dict):
            continue
        for part in (u.get("label"), u.get("type"), u.get("selector"), u.get("name")):
            k = _norm_key(part)
            if k:
                keys.add(k)
    for f in report.get("filled") or []:
        if not isinstance(f, dict):
            continue
        if not (f.get("verified") or f.get("ok")):
            continue
        for part in (f.get("label"), f.get("type"), f.get("selector")):
            k = _norm_key(part)
            if k:
                keys.add(k)
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
    summary["scanned"] = len(misses)
    summary["misses"] = [
        {"label": m.get("label"), "kind": m.get("kind"), "reason": m.get("reason")}
        for m in misses[:40]
    ]
    added = merge_miss_leftovers(report, misses)
    summary["added"] = added

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
    ]
    n = merge_miss_leftovers(report, misses)
    assert n == 3, n
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
            }
        ],
    }
    n3 = merge_miss_leftovers(report2, misses[:1])
    assert n3 == 0, n3
    rows = misses_to_leftover_rows(misses)
    assert rows[0]["via"] == "leftover_miss_scan"
    print("leftover_miss_scan.self_test: OK")


if __name__ == "__main__":
    self_test()
