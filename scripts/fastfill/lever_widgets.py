"""Lever-specific screening radios + EEO selects (prefill, not Flash).

Lever contact fields use stable ``name=`` attrs (see LEVER_SELECTOR_PACK).
Custom cards (WORK_AUTH / SPONSORSHIP / EEO) vary by company and are often
missed when CAPTCHA blocks ``extract_form_fields`` — Utility Global left
work-auth/sponsorship unchecked and EEO on Select… (W03 pixel FAIL_BLANK).

This module fills those via label text + radio/select DOM, dummy-only:
  WORK_AUTH → Yes
  SPONSORSHIP → No
  GENDER → Male (Decline fallback)
  VETERAN → not a veteran (Decline fallback)
  DISABILITY → no disability (Decline fallback)
  HISPANIC → No / Not Hispanic (Decline fallback)
  RACE → Decline aliases
Never submit. Never invent EEO free-text beyond DUMMY_PROFILE.
"""

from __future__ import annotations

import re
from typing import Any

from field_map import (
    DISABILITY,
    GENDER,
    HISPANIC,
    PORTFOLIO,
    RACE,
    SPONSORSHIP,
    VETERAN,
    WORK_AUTH,
    classify_field,
)
from gh_select import aliases_for

# Label → canonical type for Lever application-question cards.
_LEVER_QUESTION_TYPES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"authorized\s+to\s+work|legally\s+authorized|work\s+authorization|"
            r"without\s+(the\s+)?need\s+for\s+(visa\s+)?sponsorship",
            re.I,
        ),
        WORK_AUTH,
    ),
    (
        re.compile(
            r"require\s+sponsorship|visa\s+sponsor|sponsorship\s+for\s+employment|"
            r"need\s+sponsorship|immigration\s+sponsorship",
            re.I,
        ),
        SPONSORSHIP,
    ),
    (re.compile(r"\bgender\b|sex\b", re.I), GENDER),
    (re.compile(r"hispanic|latino|latina", re.I), HISPANIC),
    (re.compile(r"\brace\b|ethnicity|racial", re.I), RACE),
    (re.compile(r"veteran", re.I), VETERAN),
    (re.compile(r"disabilit", re.I), DISABILITY),
]

_EEO_TYPES = frozenset({GENDER, HISPANIC, RACE, VETERAN, DISABILITY})

# Discover Lever question blocks + radios/selects (page-evaluate, no Playwright refs).
_LEVER_SCAN_JS = """() => {
  const out = [];
  const blocks = Array.from(document.querySelectorAll(
    '.application-question, [class*="application-question"], ' +
    'li.application-question, fieldset, .custom-question, ' +
    '[data-qa*="question"], form .question'
  ));
  // Also walk labels that sit above radio groups (Lever cards)
  const radioNames = new Set();
  for (const inp of document.querySelectorAll('input[type=radio][name]')) {
    radioNames.add(inp.getAttribute('name') || '');
  }
  for (const name of radioNames) {
    if (!name) continue;
    const radios = Array.from(
      document.querySelectorAll(`input[type=radio][name="${CSS.escape(name)}"]`)
    );
    if (!radios.length) continue;
    let label = '';
    const first = radios[0];
    const block = first.closest(
      '.application-question, fieldset, li, [class*="question"], div'
    );
    if (block) {
      const lab = block.querySelector('label, legend, .application-label, h4, p');
      label = ((lab && (lab.innerText || lab.textContent)) || '').trim();
      if (!label) label = (block.innerText || '').split('\\n')[0].trim();
    }
    if (!label) {
      // previous sibling text
      let prev = first.parentElement;
      for (let i = 0; i < 4 && prev; i++) {
        const t = (prev.innerText || '').trim();
        if (t && t.length > 12) { label = t.split('\\n')[0].trim(); break; }
        prev = prev.parentElement;
      }
    }
    const options = radios.map((r) => {
      let olab = '';
      const id = r.id;
      if (id) {
        const l = document.querySelector(`label[for="${CSS.escape(id)}"]`);
        if (l) olab = (l.innerText || l.textContent || '').trim();
      }
      if (!olab) {
        const wrap = r.closest('label');
        if (wrap) olab = (wrap.innerText || wrap.textContent || '').trim();
      }
      if (!olab) olab = r.value || '';
      return { value: r.value || '', label: olab.slice(0, 120), checked: !!r.checked };
    });
    out.push({
      kind: 'radio',
      name,
      label: label.slice(0, 200),
      options,
      anyChecked: options.some((o) => o.checked),
    });
  }
  for (const sel of document.querySelectorAll('select')) {
    const name = sel.getAttribute('name') || sel.id || '';
    let label = '';
    if (sel.id) {
      const l = document.querySelector(`label[for="${CSS.escape(sel.id)}"]`);
      if (l) label = (l.innerText || l.textContent || '').trim();
    }
    if (!label) {
      const block = sel.closest(
        '.application-question, fieldset, li, [class*="question"], div'
      );
      if (block) {
        const lab = block.querySelector('label, legend, .application-label');
        label = ((lab && (lab.innerText || lab.textContent)) || '').trim();
      }
    }
    if (!label) label = name;
    const options = Array.from(sel.options || []).map((o) => ({
      value: o.value || '',
      label: (o.textContent || '').trim().slice(0, 120),
      selected: !!o.selected,
    }));
    const shown = (sel.options[sel.selectedIndex] &&
      (sel.options[sel.selectedIndex].textContent || '').trim()) || '';
    out.push({
      kind: 'select',
      name,
      id: sel.id || '',
      label: label.slice(0, 200),
      options,
      shown: shown.slice(0, 80),
      placeholderish: !shown || /^select/i.test(shown) || shown === '—' || shown === '-',
    });
  }
  // Extra URL text inputs (company-specific "Utility Global" link etc.)
  for (const inp of document.querySelectorAll(
    'input[type=url], input[type=text][name*="url" i], ' +
    'input[type=text][name*="website" i], input[type=text][placeholder*="http" i]'
  )) {
    const name = inp.getAttribute('name') || inp.id || '';
    if (/email|phone|org|linkedin|github|twitter|portfolio|full.?name|^name$/i.test(name))
      continue;
    let label = '';
    if (inp.id) {
      const l = document.querySelector(`label[for="${CSS.escape(inp.id)}"]`);
      if (l) label = (l.innerText || l.textContent || '').trim();
    }
    if (!label) {
      const block = inp.closest('.application-question, fieldset, li, div');
      if (block) {
        const lab = block.querySelector('label, legend');
        label = ((lab && (lab.innerText || lab.textContent)) || '').trim();
      }
    }
    const val = (inp.value || '').trim();
    out.push({
      kind: 'url',
      name,
      id: inp.id || '',
      label: label.slice(0, 200),
      value: val.slice(0, 200),
      empty: !val,
    });
  }
  return out;
}"""


def classify_lever_question(label: str, *, name: str = "") -> str | None:
    """Map Lever question label/name to a catalog type (or None)."""
    blob = f"{label} {name}".strip()
    if not blob:
        return None
    # Prefer explicit patterns over generic classify_field (avoids NAME steal)
    for pat, ftype in _LEVER_QUESTION_TYPES:
        if pat.search(blob):
            return ftype
    # EEO name attrs: eeo[gender], cards[…]gender…
    low = blob.lower()
    if re.search(r"eeo|demographic|voluntary\s+self", low):
        if "gender" in low or "sex" in low:
            return GENDER
        if "hispanic" in low or "latino" in low:
            return HISPANIC
        if "race" in low or "ethnic" in low:
            return RACE
        if "veteran" in low:
            return VETERAN
        if "disabilit" in low:
            return DISABILITY
    fake = {
        "label": label,
        "name": name,
        "id": "",
        "type": "text",
        "placeholder": "",
        "aria_label": label,
        "autocomplete": "",
    }
    ftype, _ = classify_field(fake)
    if ftype in (
        WORK_AUTH,
        SPONSORSHIP,
        GENDER,
        HISPANIC,
        RACE,
        VETERAN,
        DISABILITY,
        PORTFOLIO,
    ):
        return ftype
    return None


def pick_radio_option(
    ftype: str,
    desired: str,
    options: list[dict],
) -> dict | None:
    """Pick best radio option for WORK_AUTH/SPONSORSHIP (Yes/No + aliases)."""
    cands = aliases_for(ftype, desired)
    best = None
    best_score = -1
    for opt in options or []:
        olab = str(opt.get("label") or opt.get("value") or "").strip()
        if not olab:
            continue
        o_low = olab.lower()
        score = 0
        for i, alias in enumerate(cands):
            a = (alias or "").strip().lower()
            if not a:
                continue
            if o_low == a:
                score = max(score, 100 - i)
            elif o_low.startswith(a + " ") or o_low.startswith(a + ","):
                score = max(score, 90 - i)
            elif a in ("yes", "no") and (o_low == a or o_low.startswith(a + ",")):
                score = max(score, 92 - i)
            elif a in o_low and len(a) > 3:
                score = max(score, 70 - i)
        # SPONSORSHIP=No must not pick "Yes, I require sponsorship"
        if ftype == SPONSORSHIP and str(desired).lower() in ("no", "false"):
            if re.search(r"\byes\b.*require|will\s+require|need\s+sponsor", o_low):
                score = 0
            if re.search(
                r"will\s+not\s+require|do\s+not\s+require|don'?t\s+require|"
                r"no\s+sponsorship|^no$|"
                r"^no,\s*(i\s+will\s+not|i\s+do\s+not|i\s+don'?t)",
                o_low,
            ):
                score = max(score, 96)
        if ftype == WORK_AUTH and str(desired).lower() in ("yes", "true"):
            if re.search(r"\bno\b.*not\s+authorized|unauthorized", o_low):
                score = 0
            if o_low == "yes" or o_low.startswith("yes"):
                score = max(score, 95)
        if score > best_score:
            best_score = score
            best = opt
    if best_score <= 0:
        return None
    return best


def radio_already_matches_desired(
    ftype: str,
    desired: str,
    options: list[dict],
) -> bool:
    """ATS-010: True only when a checked radio matches the intended Yes/No polarity."""
    checked = [o for o in (options or []) if o.get("checked")]
    if not checked:
        return False
    want = pick_radio_option(ftype, desired, list(options or []))
    if not want:
        return False
    want_lab = str(want.get("label") or want.get("value") or "").strip().lower()
    want_val = str(want.get("value") or "").strip().lower()
    for o in checked:
        ol = str(o.get("label") or "").strip().lower()
        ov = str(o.get("value") or "").strip().lower()
        if want_lab and ol == want_lab:
            return True
        if want_val and ov and ov == want_val:
            return True
    # Wrong polarity stays checked → pick among checked-only fails or differs
    scored = pick_radio_option(ftype, desired, checked)
    if scored is None:
        return False
    sc_lab = str(scored.get("label") or scored.get("value") or "").strip().lower()
    return bool(sc_lab and want_lab and sc_lab == want_lab)


def pick_eeo_select_option(
    ftype: str, options: list[dict], *, desired: str | None = None
) -> dict | None:
    """Pick preferred dummy EEO option; Decline aliases are fallback only."""
    from field_map import DUMMY_PROFILE, build_value_map

    prefer = (desired or "").strip()
    if not prefer:
        try:
            prefer = str(build_value_map(DUMMY_PROFILE).get(ftype) or "")
        except Exception:
            prefer = ""
    if not prefer:
        prefer = "Decline to self identify"
    cands = [a.lower() for a in aliases_for(ftype, prefer)]
    best = None
    best_score = -1
    for opt in options or []:
        olab = str(opt.get("label") or opt.get("value") or "").strip()
        if not olab:
            continue
        o_low = olab.lower()
        # Skip empty / Select… placeholders
        if re.match(r"^select|^choose|^—|^-$", o_low):
            continue
        score = 0
        for i, a in enumerate(cands):
            if not a:
                continue
            if o_low == a:
                score = max(score, 100 - i)
            elif a in o_low or o_low in a:
                score = max(score, 80 - i)
        # Preferred concrete answers outrank Decline when both match weakly
        if ftype == "GENDER" and re.search(r"\bmale\b|\bman\b", o_low):
            score = max(score, 95)
        if ftype == "VETERAN" and re.search(
            r"not a (protected )?veteran|i am not a veteran|\bno\b", o_low
        ):
            if "decline" not in o_low and "wish" not in o_low:
                score = max(score, 95)
        if ftype == "DISABILITY" and re.search(
            r"do not have a disability|don'?t have a disability|no disability|not disabled",
            o_low,
        ):
            score = max(score, 95)
        if ftype == "HISPANIC" and re.search(
            r"\bno\b|not hispanic|not latino",
            o_low,
        ):
            if "decline" not in o_low and "wish" not in o_low:
                score = max(score, 95)
        if "decline" in o_low or "prefer not" in o_low or "wish not" in o_low:
            score = max(score, 70)  # fallback, not preferred
        if "do not want to answer" in o_low or "don't want to answer" in o_low:
            score = max(score, 68)
        if score > best_score:
            best_score = score
            best = opt
    if best_score <= 0:
        return None
    return best


async def fill_lever_widgets(page, values: dict[str, Any]) -> list[dict]:
    """Fill Lever WORK_AUTH / SPONSORSHIP radios + EEO selects + extra URL.

    Safe to call when CAPTCHA is present at page bottom — only touches
    screening/EEO controls above the fold. Dummy values only.
    """
    results: list[dict] = []
    try:
        scanned = await page.evaluate(_LEVER_SCAN_JS)
    except Exception as e:
        return [
            {
                "ok": False,
                "reason": f"lever_scan_failed:{e}"[:120],
                "via": "lever_widgets",
                "flash_candidate": False,
            }
        ]
    if not isinstance(scanned, list):
        return []

    filled_types: set[str] = set()

    for row in scanned:
        if not isinstance(row, dict):
            continue
        kind = row.get("kind")
        label = str(row.get("label") or "")
        name = str(row.get("name") or "")
        ftype = classify_lever_question(label, name=name)
        if not ftype:
            # Unclassified unanswered radios → Flash leftover (do not silent-skip)
            if kind == "radio" and not row.get("anyChecked"):
                results.append(
                    {
                        "ok": False,
                        "verified": False,
                        "type": None,
                        "label": label[:80],
                        "name": name[:80],
                        "via": "lever_widgets",
                        "reason": "unanswered_radio_group",
                        "flash_candidate": True,
                    }
                )
            continue
        if ftype in filled_types and ftype != PORTFOLIO:
            continue

        if kind == "radio" and ftype in (WORK_AUTH, SPONSORSHIP):
            desired = str(values.get(ftype) or ("Yes" if ftype == WORK_AUTH else "No"))
            # ATS-010: already_checked only when checked radio matches desired polarity
            if row.get("anyChecked") and radio_already_matches_desired(
                ftype, desired, list(row.get("options") or [])
            ):
                filled_types.add(ftype)
                results.append(
                    {
                        "ok": True,
                        "verified": True,
                        "type": ftype,
                        "label": label[:80],
                        "via": "lever_widgets",
                        "reason": "already_checked",
                        "value": values.get(ftype),
                    }
                )
                continue
            pick = pick_radio_option(ftype, desired, list(row.get("options") or []))
            if not pick:
                results.append(
                    {
                        "ok": False,
                        "type": ftype,
                        "label": label[:80],
                        "via": "lever_widgets",
                        "reason": "no_matching_radio",
                        "flash_candidate": True,
                    }
                )
                continue
            opt_label = str(pick.get("label") or pick.get("value") or "")
            opt_value = str(pick.get("value") or "")
            clicked = False
            # Prefer label click (Lever wraps input in <label>)
            try:
                if opt_label:
                    loc = page.get_by_label(re.compile(re.escape(opt_label[:40]), re.I))
                    if await loc.count() > 0:
                        await loc.first.click(timeout=2500, force=True)
                        clicked = True
            except Exception:
                clicked = False
            if not clicked and name and opt_value:
                try:
                    sel = f'input[type=radio][name={_css_attr(name)}][value={_css_attr(opt_value)}]'
                    loc = page.locator(sel).first
                    await loc.check(timeout=2500, force=True)
                    clicked = True
                except Exception:
                    try:
                        await page.locator(sel).first.click(timeout=2500, force=True)
                        clicked = True
                    except Exception as e:
                        results.append(
                            {
                                "ok": False,
                                "type": ftype,
                                "label": label[:80],
                                "via": "lever_widgets",
                                "reason": f"radio_click_failed:{e}"[:100],
                                "flash_candidate": True,
                            }
                        )
                        continue
            if not clicked:
                results.append(
                    {
                        "ok": False,
                        "type": ftype,
                        "label": label[:80],
                        "via": "lever_widgets",
                        "reason": "radio_not_clicked",
                        "flash_candidate": True,
                    }
                )
                continue
            # Verify checked
            verified = False
            try:
                if name and opt_value:
                    sel = f'input[type=radio][name={_css_attr(name)}][value={_css_attr(opt_value)}]'
                    verified = bool(await page.locator(sel).first.is_checked())
                if not verified and name:
                    verified = bool(
                        await page.evaluate(
                            """(n) => {
                              const el = document.querySelector(
                                `input[type=radio][name="${CSS.escape(n)}"]:checked`
                              );
                              return !!el;
                            }""",
                            name,
                        )
                    )
            except Exception:
                verified = clicked
            results.append(
                {
                    "ok": verified,
                    "verified": verified,
                    "type": ftype,
                    "label": label[:80],
                    "via": "lever_widgets",
                    "value": desired,
                    "readback": opt_label[:80],
                    "picked": opt_label[:80],
                    "mode": "radio",
                    "flash_candidate": not verified,
                }
            )
            if verified:
                filled_types.add(ftype)
            continue

        # Other radio groups (unclassified or non-auth): promote if unanswered
        if kind == "radio" and not row.get("anyChecked"):
            results.append(
                {
                    "ok": False,
                    "verified": False,
                    "type": ftype,  # may be None for unclassified
                    "label": label[:80],
                    "name": name[:80],
                    "via": "lever_widgets",
                    "reason": "unanswered_radio_group",
                    "flash_candidate": True,
                }
            )
            continue

        if kind == "select" and ftype in _EEO_TYPES:
            desired_eeo = str(values.get(ftype) or "")
            if not row.get("placeholderish") and row.get("shown"):
                shown = str(row.get("shown") or "")
                # Skip only when already matches preferred dummy answer
                already_ok = False
                if desired_eeo:
                    for a in aliases_for(ftype, desired_eeo)[:6]:
                        if a and a.lower() in shown.lower():
                            already_ok = True
                            break
                if already_ok:
                    filled_types.add(ftype)
                    results.append(
                        {
                            "ok": True,
                            "verified": True,
                            "type": ftype,
                            "label": label[:80],
                            "via": "lever_widgets",
                            "reason": "already_correct",
                            "value": shown,
                            "readback": shown,
                        }
                    )
                    continue
            pick = pick_eeo_select_option(
                ftype, list(row.get("options") or []), desired=desired_eeo
            )
            if not pick:
                results.append(
                    {
                        "ok": False,
                        "type": ftype,
                        "label": label[:80],
                        "via": "lever_widgets",
                        "reason": "no_eeo_option",
                        "flash_candidate": True,
                    }
                )
                continue
            opt_label = str(pick.get("label") or "")
            opt_value = str(pick.get("value") or "")
            sel_css = ""
            if row.get("id"):
                sel_css = f"select#{row['id']}"
            elif name:
                sel_css = f"select[name={_css_attr(name)}]"
            else:
                results.append(
                    {
                        "ok": False,
                        "type": ftype,
                        "label": label[:80],
                        "via": "lever_widgets",
                        "reason": "select_no_selector",
                        "flash_candidate": True,
                    }
                )
                continue
            try:
                loc = page.locator(sel_css).first
                try:
                    await loc.select_option(label=opt_label)
                except Exception:
                    await loc.select_option(value=opt_value)
                readback = await loc.evaluate(
                    "el => (el.options[el.selectedIndex]||{}).text || el.value || ''"
                )
                verified = bool(
                    readback
                    and (
                        "decline" in str(readback).lower()
                        or "prefer not" in str(readback).lower()
                        or opt_label[:12].lower() in str(readback).lower()
                    )
                )
                results.append(
                    {
                        "ok": verified,
                        "verified": verified,
                        "type": ftype,
                        "label": label[:80],
                        "via": "lever_widgets",
                        "value": opt_label[:120],
                        "readback": str(readback)[:120],
                        "mode": "select",
                        "selector": sel_css,
                        "flash_candidate": not verified,
                    }
                )
                if verified:
                    filled_types.add(ftype)
            except Exception as e:
                results.append(
                    {
                        "ok": False,
                        "type": ftype,
                        "label": label[:80],
                        "via": "lever_widgets",
                        "reason": f"select_failed:{e}"[:100],
                        "flash_candidate": True,
                    }
                )
            continue

        if kind == "url" and row.get("empty"):
            # Company-specific link (e.g. "Utility Global") — use portfolio URL
            url_val = str(values.get(PORTFOLIO) or values.get("PORTFOLIO") or "").strip()
            if not url_val:
                continue
            # Only if label looks like a link ask (not LinkedIn already packed)
            if re.search(r"linkedin|github|twitter|email|phone", label, re.I):
                continue
            if not (
                re.search(r"http|url|website|link|profile|utility", label, re.I)
                or re.search(r"url|website|link", name, re.I)
            ):
                continue
            sel_css = ""
            if row.get("id"):
                sel_css = f"#{row['id']}"
            elif name:
                sel_css = f"input[name={_css_attr(name)}]"
            else:
                continue
            try:
                loc = page.locator(sel_css).first
                try:
                    existing = (await loc.input_value() or "").strip()
                except Exception:
                    existing = ""
                if existing and url_val[:12] in existing:
                    results.append(
                        {
                            "ok": True,
                            "verified": True,
                            "type": PORTFOLIO,
                            "label": label[:80] or "custom_link",
                            "via": "lever_widgets",
                            "value": url_val[:120],
                            "readback": existing[:120],
                            "selector": sel_css,
                            "reason": "already_correct_skip",
                            "skipped_already_correct": True,
                        }
                    )
                    continue
                await loc.fill(url_val[:200])
                readback = await loc.input_value()
                ok = bool(readback and url_val[:12] in readback)
                results.append(
                    {
                        "ok": ok,
                        "verified": ok,
                        "type": PORTFOLIO,
                        "label": label[:80] or "custom_link",
                        "via": "lever_widgets",
                        "value": url_val[:120],
                        "readback": (readback or "")[:120],
                        "selector": sel_css,
                        "flash_candidate": not ok,
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "ok": False,
                        "type": PORTFOLIO,
                        "label": label[:80],
                        "via": "lever_widgets",
                        "reason": f"url_fill_failed:{e}"[:100],
                        "flash_candidate": True,
                    }
                )

    return results


def _css_attr(value: str) -> str:
    """Quote a CSS attribute value safely."""
    s = str(value or "")
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    return '"' + s.replace('"', '\\"') + '"'


def self_test() -> None:
    """Pure unit checks (no browser)."""
    assert classify_lever_question(
        "Are you legally authorized to work in the United States for any employer?"
    ) == WORK_AUTH
    assert classify_lever_question(
        "Will you now or in the future require sponsorship for employment visa status?"
    ) == SPONSORSHIP
    assert classify_lever_question("Gender", name="eeo[gender]") == GENDER
    assert classify_lever_question("Race / Ethnicity") == RACE
    assert classify_lever_question("Veteran Status") == VETERAN

    yes = pick_radio_option(
        WORK_AUTH,
        "Yes",
        [{"label": "Yes", "value": "Yes"}, {"label": "No", "value": "No"}],
    )
    assert yes and yes["label"] == "Yes"
    no = pick_radio_option(
        SPONSORSHIP,
        "No",
        [
            {"label": "Yes, I will require sponsorship", "value": "Yes"},
            {"label": "No", "value": "No"},
        ],
    )
    assert no and no["label"] == "No"
    # Must not pick the Yes-require option for SPONSORSHIP=No
    bad = pick_radio_option(
        SPONSORSHIP,
        "No",
        [{"label": "Yes, I will require sponsorship", "value": "Yes"}],
    )
    assert bad is None
    # Trap starts with "No," — must not win for SPONSORSHIP=No
    trap = pick_radio_option(
        SPONSORSHIP,
        "No",
        [{"label": "No, I will require visa sponsorship", "value": "No"}],
    )
    assert trap is None

    # ATS-010: already_checked polarity
    wrong = [
        {"label": "Yes, I will require sponsorship", "value": "Yes", "checked": True},
        {"label": "No", "value": "No", "checked": False},
    ]
    assert radio_already_matches_desired(SPONSORSHIP, "No", wrong) is False
    right = [
        {"label": "Yes, I will require sponsorship", "value": "Yes", "checked": False},
        {"label": "No", "value": "No", "checked": True},
    ]
    assert radio_already_matches_desired(SPONSORSHIP, "No", right) is True

    male = pick_eeo_select_option(
        GENDER,
        [
            {"label": "Select...", "value": ""},
            {"label": "Male", "value": "Male"},
            {"label": "Decline to self-identify", "value": "Decline"},
        ],
    )
    assert male and male["label"].lower() == "male"
    # Decline is fallback when preferred label absent
    dec = pick_eeo_select_option(
        GENDER,
        [
            {"label": "Select...", "value": ""},
            {"label": "Decline to self-identify", "value": "Decline"},
            {"label": "Female", "value": "Female"},
        ],
    )
    assert dec and "decline" in dec["label"].lower()
    print("lever_widgets.self_test: OK")


if __name__ == "__main__":
    self_test()
