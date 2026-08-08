"""Scoped batch in-page fill for simple controls (ChamPro FA.fill idea).

Complex widgets (Workday prompt, GH react-select, file, HOW_HEARD) stay on
Playwright verified paths. This only sets native text/textarea/checkbox/select
values in one evaluate round-trip to cut races.
"""

from __future__ import annotations

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
    const el = document.querySelector(sel);
    if (!el || !vis(el)) {
      out.push({ selector: sel, ok: false, reason: 'not_found' });
      continue;
    }
    try {
      if (mode === 'checkbox') {
        const want = /^(1|true|yes|y|on)$/i.test(value);
        if (el.checked !== want) el.click();
        out.push({ selector: sel, ok: el.checked === want, readback: String(el.checked) });
        continue;
      }
      if (mode === 'select' || el.tagName === 'SELECT') {
        const opts = [...el.options || []];
        let hit = opts.find(o => (o.text || '').trim() === value)
          || opts.find(o => (o.value || '') === value)
          || opts.find(o => (o.text || '').toLowerCase().includes(value.toLowerCase()));
        if (!hit) {
          out.push({ selector: sel, ok: false, reason: 'no_option', options: opts.map(o => (o.text||'').slice(0,40)).slice(0,8) });
          continue;
        }
        el.value = hit.value;
        el.dispatchEvent(new Event('change', { bubbles: true }));
        out.push({ selector: sel, ok: true, readback: (hit.text || '').slice(0, 80) });
        continue;
      }
      // text / textarea — native setter + input/change (no fiber Tab — not searchSelect)
      const proto = Object.getPrototypeOf(el);
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) desc.set.call(el, value);
      else el.value = value;
      if (el._valueTracker) el._valueTracker.setValue('');
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      out.push({ selector: sel, ok: (el.value || '') === value, readback: (el.value || '').slice(0, 120) });
    } catch (e) {
      out.push({ selector: sel, ok: false, reason: String(e).slice(0, 80) });
    }
  }
  return out;
}
"""

# Field types that must NOT use batch fill
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
})


def is_batchable_row(row: dict) -> bool:
    """True when a fill plan row is safe for one-shot in-page set."""
    ftype = str(row.get("type") or row.get("field_type") or "").upper()
    if ftype in BATCH_SKIP_TYPES:
        return False
    mode = str(row.get("mode") or "text").lower()
    if mode in ("combobox", "file", "radio", "prompt", "search_select"):
        return False
    sel = str(row.get("selector") or "").strip()
    if not sel or sel.startswith("xpath="):
        return False
    return bool(row.get("value") is not None)


async def batch_fill_simple(page, plan: list[dict]) -> list[dict[str, Any]]:
    """Fill simple fields in one evaluate. Returns per-row results."""
    rows = [r for r in plan if is_batchable_row(r)]
    if not rows:
        return []
    payload = [
        {
            "selector": r["selector"],
            "value": r.get("value"),
            "mode": r.get("mode") or "text",
        }
        for r in rows
    ]
    try:
        return await page.evaluate(BATCH_FILL_JS, payload) or []
    except Exception as e:
        return [{"ok": False, "reason": f"batch_error:{e}"[:120]}]
