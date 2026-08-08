"""Post-L0/1 unanswered choice enumeration → flash_candidates.

After widgets + demote, scan Ashby/Lever (and generic radio groups) for
choice controls Layer 0/1 never listed as leftovers. Emits rows with
``reason: unanswered_choice_group`` and ``flash_candidate: True`` so Flash
sees them when ``--flash-leftovers`` is on.

Dummy-only. Never invents EEO values here — only promotes blanks to leftovers.
Never CAPTCHA / never submit.
"""

from __future__ import annotations

from typing import Any

REASON = "unanswered_choice_group"


def leftover_from_ashby_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Pure: Ashby field-entry → leftover if choice/text still unanswered."""
    if not isinstance(entry, dict):
        return None
    label = str(entry.get("label") or "").strip()
    if not label:
        return None

    if entry.get("yesno") and not entry.get("yesnoSelected"):
        return {
            "label": label[:120],
            "type": None,
            "name": entry.get("path") or entry.get("name") or "",
            "reason": REASON,
            "flash_candidate": True,
            "via": "enumerate_unanswered_choices",
            "platform": "ashby",
            "mode": "yesno",
        }

    radios = entry.get("radios") or []
    if radios and not any(r.get("checked") for r in radios if isinstance(r, dict)):
        # Consent-only checkbox entries without radios are handled elsewhere
        return {
            "label": label[:120],
            "type": None,
            "name": (radios[0].get("name") if isinstance(radios[0], dict) else "")
            or entry.get("path")
            or "",
            "reason": REASON,
            "flash_candidate": True,
            "via": "enumerate_unanswered_choices",
            "platform": "ashby",
            "mode": "radio",
        }

    # Required empty text (* in label) — only when hasText and empty
    lab_star = "*" in label
    if lab_star and entry.get("hasText") and entry.get("textEmpty"):
        return {
            "label": label[:120],
            "type": None,
            "name": entry.get("textName") or entry.get("path") or "",
            "reason": REASON,
            "flash_candidate": True,
            "via": "enumerate_unanswered_choices",
            "platform": "ashby",
            "mode": "text",
        }
    return None


def leftover_from_lever_scan_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Pure: Lever scan row → leftover when radio group has no selection."""
    if not isinstance(row, dict):
        return None
    if row.get("kind") != "radio":
        return None
    if row.get("anyChecked"):
        return None
    label = str(row.get("label") or row.get("name") or "").strip()
    if not label:
        return None
    return {
        "label": label[:120],
        "type": None,
        "name": row.get("name") or "",
        "reason": REASON,
        "flash_candidate": True,
        "via": "enumerate_unanswered_choices",
        "platform": "lever",
        "mode": "radio",
    }


def leftover_from_generic_radio_group(group: dict[str, Any]) -> dict[str, Any] | None:
    """Pure: generic {name,label,anyChecked} → leftover when unanswered."""
    if not isinstance(group, dict):
        return None
    if group.get("anyChecked"):
        return None
    label = str(group.get("label") or group.get("name") or "").strip()
    if not label:
        return None
    return {
        "label": label[:120],
        "type": None,
        "name": group.get("name") or "",
        "reason": REASON,
        "flash_candidate": True,
        "via": "enumerate_unanswered_choices",
        "platform": group.get("platform") or "generic",
        "mode": "radio",
    }


def promote_unanswered_rows(
    report: dict,
    rows: list[dict[str, Any]] | None,
) -> int:
    """Append unanswered rows to report leftovers; skip duplicate labels.

    Returns number newly added.
    """
    if not rows:
        return 0
    leftovers = report.setdefault("leftovers", [])
    if not isinstance(leftovers, list):
        leftovers = []
        report["leftovers"] = leftovers
    seen = {
        (
            str(u.get("label") or "").strip().lower()[:80],
            str(u.get("reason") or ""),
        )
        for u in leftovers
        if isinstance(u, dict)
    }
    # Also treat verified filled labels as answered
    filled_labs = {
        str(f.get("label") or "").strip().lower()[:80]
        for f in (report.get("filled") or [])
        if isinstance(f, dict) and f.get("ok") and f.get("verified") and f.get("label")
    }
    added = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        lab = str(row.get("label") or "").strip().lower()[:80]
        if lab and lab in filled_labs:
            continue
        key = (lab, str(row.get("reason") or REASON))
        if key in seen:
            continue
        leftovers.append(
            {
                **row,
                "reason": row.get("reason") or REASON,
                "flash_candidate": True,
            }
        )
        seen.add(key)
        added += 1
    if added:
        report["leftover_count"] = len(leftovers)
        report["unanswered_choices_promoted"] = (
            int(report.get("unanswered_choices_promoted") or 0) + added
        )
    return added


_GENERIC_RADIO_GROUPS_JS = """() => {
  const out = [];
  const isVisible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0
      && window.getComputedStyle(el).visibility !== 'hidden';
  };
  const byName = new Map();
  for (const r of document.querySelectorAll('input[type=radio]')) {
    if (!isVisible(r) || r.disabled) continue;
    const name = r.name || r.id || '';
    if (!name) continue;
    if (!byName.has(name)) byName.set(name, []);
    byName.get(name).push(r);
  }
  for (const [name, radios] of byName.entries()) {
    let label = '';
    const first = radios[0];
    if (first.id) {
      const l = document.querySelector(`label[for="${CSS.escape(first.id)}"]`);
      if (l) label = (l.innerText || l.textContent || '').trim();
    }
    if (!label) {
      const fs = first.closest('fieldset, [role="radiogroup"], [role="group"], .application-question, li, div');
      if (fs) {
        const lab = fs.querySelector('legend, label, .application-label, [class*="heading"]');
        label = ((lab && (lab.innerText || lab.textContent)) || '').trim();
      }
    }
    if (!label) label = name;
    const anyChecked = radios.some((r) => r.checked);
    const required = radios.some(
      (r) => r.required || r.getAttribute('aria-required') === 'true'
    );
    // Prefer required groups; also include groups inside fieldsets with *
    const star = /\\*/.test(label);
    if (!required && !star && radios.length < 2) continue;
    if (!required && !star) {
      // Still promote multi-option unanswered groups (screening radios)
      if (radios.length < 2) continue;
    }
    out.push({
      name,
      label: label.slice(0, 200),
      anyChecked,
      required: required || star,
      count: radios.length,
    });
  }
  return out.slice(0, 40);
}"""


async def enumerate_unanswered_choices(page, platform: str | None = None) -> list[dict]:
    """Live DOM scan → unanswered choice leftovers for Flash handoff."""
    plat = (platform or "").strip().lower()
    out: list[dict] = []

    if plat == "ashby":
        try:
            from ashby_widgets import list_ashby_field_entries

            entries = await list_ashby_field_entries(page)
        except Exception:
            entries = []
        for entry in entries or []:
            # Enrich textEmpty if scanner didn't set it
            if (
                isinstance(entry, dict)
                and entry.get("hasText")
                and "textEmpty" not in entry
            ):
                # list_ashby may expose text value via other keys; treat missing as empty
                tv = entry.get("textValue") or entry.get("value") or ""
                entry = {**entry, "textEmpty": not str(tv).strip()}
            row = leftover_from_ashby_entry(entry if isinstance(entry, dict) else {})
            if row:
                out.append(row)
        return out

    if plat == "lever":
        try:
            from lever_widgets import _LEVER_SCAN_JS

            scanned = await page.evaluate(_LEVER_SCAN_JS)
        except Exception:
            scanned = []
        for row in scanned or []:
            left = leftover_from_lever_scan_row(row if isinstance(row, dict) else {})
            if left:
                out.append(left)
        return out

    # Generic / Workday / unknown — radio groups
    try:
        groups = await page.evaluate(_GENERIC_RADIO_GROUPS_JS)
    except Exception:
        groups = []
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        g = {**g, "platform": plat or "generic"}
        left = leftover_from_generic_radio_group(g)
        if left:
            out.append(left)
    return out


async def scan_and_promote_unanswered(
    page, report: dict, *, platform: str | None = None
) -> dict:
    """Enumerate unanswered choices and merge into report leftovers."""
    plat = platform or report.get("platform") or ""
    rows = await enumerate_unanswered_choices(page, plat)
    added = promote_unanswered_rows(report, rows)
    summary = {
        "platform": plat,
        "scanned": len(rows),
        "promoted": added,
        "labels": [r.get("label") for r in rows[:12]],
    }
    report["unanswered_choices"] = summary
    return summary
