"""Scoped batch in-page fill for simple controls (ChamPro FA.fill idea).

Complex widgets (Workday prompt, GH react-select, file, HOW_HEARD) stay on
Playwright verified paths. This only sets native text/textarea/checkbox/select
values in one evaluate round-trip to cut races.
"""

from __future__ import annotations

import re
from typing import Any

BATCH_FILL_JS = """
(plan) => {
  const out = [];
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0
      && window.getComputedStyle(el).visibility !== 'hidden';
  };
  for (const row of (plan || [])) {
    const sel = row.selector;
    const value = row.value == null ? '' : String(row.value);
    const mode = row.mode || 'text';
    let el = null;
    try {
      el = document.querySelector(sel);
    } catch (e) {
      out.push({ selector: sel, ok: false, reason: 'bad_selector' });
      continue;
    }
    if (!el || !vis(el)) {
      out.push({ selector: sel, ok: false, reason: 'not_found' });
      continue;
    }
    const role = (el.getAttribute('role') || '').toLowerCase();
    const itype = (el.getAttribute('type') || '').toLowerCase();
    if (role === 'combobox' || role === 'listbox' || role === 'searchbox'
        || itype === 'file' || itype === 'radio') {
      out.push({ selector: sel, ok: false, reason: 'widget_skip' });
      continue;
    }
    try {
      if (mode === 'checkbox' || itype === 'checkbox') {
        const want = /^(1|true|yes|y|on)$/i.test(value);
        if (el.checked === want) {
          out.push({
            selector: sel, ok: true, readback: String(el.checked),
            reason: 'already_correct_skip',
          });
          continue;
        }
        el.click();
        const ok = el.checked === want;
        out.push({
          selector: sel, ok: ok, readback: String(el.checked),
          reason: ok ? undefined : 'checkbox_mismatch',
        });
        continue;
      }
      if (mode === 'select' || el.tagName === 'SELECT') {
        const opts = [...el.options || []];
        const curText = ((el.options[el.selectedIndex] || {}).text || '').trim();
        const curVal = el.value || '';
        if (curText === value || curVal === value) {
          out.push({
            selector: sel, ok: true, readback: (curText || curVal).slice(0, 80),
            reason: 'already_correct_skip',
          });
          continue;
        }
        let hit = opts.find(o => (o.text || '').trim() === value)
          || opts.find(o => (o.value || '') === value)
          || opts.find(o => (o.text || '').toLowerCase().includes(value.toLowerCase()));
        if (!hit) {
          out.push({ selector: sel, ok: false, reason: 'no_option', options: opts.map(o => (o.text||'').slice(0,40)).slice(0,8) });
          continue;
        }
        el.value = hit.value;
        el.dispatchEvent(new Event('change', { bubbles: true }));
        const rb = (hit.text || '').trim();
        out.push({
          selector: sel, ok: !!rb, readback: rb.slice(0, 80),
          reason: rb ? undefined : 'empty_readback',
        });
        continue;
      }
      // text / textarea — native setter + input/change (no fiber Tab — not searchSelect)
      const cur = el.value || '';
      if (cur === value && value !== '') {
        out.push({
          selector: sel, ok: true, readback: cur.slice(0, 120),
          reason: 'already_correct_skip',
        });
        continue;
      }
      const proto = Object.getPrototypeOf(el);
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) desc.set.call(el, value);
      else el.value = value;
      if (el._valueTracker) el._valueTracker.setValue('');
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      const rb = el.value || '';
      out.push({
        selector: sel,
        ok: rb === value && rb !== '',
        readback: rb.slice(0, 120),
        reason: rb ? (rb === value ? undefined : 'readback_mismatch') : 'empty_readback',
      });
    } catch (e) {
      out.push({ selector: sel, ok: false, reason: String(e).slice(0, 80) });
    }
  }
  return out;
}
"""

# Field types that must NOT use batch fill (widgets / fiber / hierarchical).
BATCH_SKIP_TYPES = frozenset({
    "HOW_HEARD",
    "SOURCE",
    "SCHOOL",
    "RESUME_UPLOAD",
    "ADDRESS_COUNTRY",
    "ADDRESS_STATE",
    "ADDRESS_CITY",
    "LOCATION",
    "PHONE_DEVICE_TYPE",
    "PHONE_DEVICE",
    "PHONE_COUNTRY_CODE",
    "FIELD_OF_STUDY",
    "DISCIPLINE",
    "MAJOR",
    "DEGREE",
    "EDUCATION_START_YEAR",
    "EDUCATION_END_YEAR",
})

BATCH_SKIP_MODES = frozenset({
    "combobox",
    "file",
    "radio",
    "prompt",
    "search_select",
    "searchselect",
    "gh_select",
    "date_spin",
    "date",
})

# Playwright-only locator syntax — document.querySelector cannot use these.
_UNSAFE_SELECTOR_RE = re.compile(
    r">>|:has-text\s*\(|:text-matches\s*\(|xpath=|:visible\b|:nth-match\s*\(",
    re.I,
)

_TEXT_MODES = frozenset({"text", "fill", "textarea", "email", "tel", "url", "password"})
_CHECKBOX_TRUE = re.compile(r"^(1|true|yes|y|on)$", re.I)


def selector_is_batch_safe(sel: str) -> bool:
    """True when selector is CSS that querySelector can run."""
    s = (sel or "").strip()
    if not s or s.startswith("xpath="):
        return False
    if _UNSAFE_SELECTOR_RE.search(s):
        return False
    return True


def normalize_batch_mode(mode: str | None) -> str:
    """Map pack/extract mode to JS batch mode (text|select|checkbox)."""
    m = str(mode or "text").lower().replace("-", "_")
    if m in _TEXT_MODES:
        return "text"
    if m == "select":
        return "select"
    if m == "checkbox":
        return "checkbox"
    return m


def is_batchable_row(row: dict) -> bool:
    """True when a fill plan row is safe for one-shot in-page set."""
    ftype = str(row.get("type") or row.get("field_type") or "").upper()
    if ftype in BATCH_SKIP_TYPES:
        return False
    mode = normalize_batch_mode(row.get("mode"))
    if mode in BATCH_SKIP_MODES:
        return False
    if mode not in ("text", "select", "checkbox"):
        return False
    sel = str(row.get("selector") or "").strip()
    if not selector_is_batch_safe(sel):
        return False
    return row.get("value") is not None


def batch_result_verified(plan_row: dict, result: dict | None) -> bool:
    """True when one evaluate result is an honest non-empty match.

    Empty readback is never success (Workday fiber-stubborn addressLine2/county).
    """
    if not isinstance(result, dict) or not plan_row:
        return False
    if result.get("ok") is False:
        return False
    rb = str(result.get("readback") or "").strip()
    if not rb:
        return False
    mode = normalize_batch_mode(plan_row.get("mode") or result.get("mode"))
    want = str(plan_row.get("value") or "")
    if mode == "checkbox":
        want_on = bool(_CHECKBOX_TRUE.match(want.strip()))
        got_on = rb.lower() in ("true", "1", "yes", "on", "checked")
        return want_on == got_on
    try:
        from verified_select import value_matches_readback

        if value_matches_readback(want, rb):
            return True
    except Exception:
        pass
    return rb == want or rb.lower() == want.lower()


async def batch_fill_simple(page, plan: list[dict]) -> list[dict[str, Any]]:
    """Fill simple fields in one evaluate. Returns per-row results."""
    rows = [r for r in plan if is_batchable_row(r)]
    if not rows:
        return []
    payload = [
        {
            "selector": r["selector"],
            "value": r.get("value"),
            "mode": normalize_batch_mode(r.get("mode")),
        }
        for r in rows
    ]
    try:
        return await page.evaluate(BATCH_FILL_JS, payload) or []
    except Exception as e:
        return [{"ok": False, "reason": f"batch_error:{e}"[:120]}]
