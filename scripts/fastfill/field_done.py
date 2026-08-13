"""Unified field-is-done contract for fastfill (dummy-only, never submit).

Single completion API: ``field_is_done`` / ``field_is_done_from_row`` /
``field_is_done_from_readback``.  ``fill_verify.is_verified_fill_row`` is a
thin wrapper over this module.

Do not add a parallel oracle — pack metrics, leftovers, gym score, Ready,
and advance must consult this SSoT (vision_judge is a Ready *input* to the
same gate, not a second completion/advance voter).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from field_map import (
    DEGREE,
    FIELD_OF_STUDY,
    HOW_HEARD,
    NAME_FIRST,
    NAME_LAST,
    PHONE_COUNTRY_CODE,
)


@dataclass(frozen=True)
class DoneVerdict:
    ok: bool
    reason: str
    readback: Any = None


def _field_type(field_meta: dict) -> str:
    return str(
        field_meta.get("type")
        or field_meta.get("field_type")
        or field_meta.get("automation_id")
        or ""
    ).upper()


def _is_degree_field(ftype: str) -> bool:
    """DEGREE plus Workday select_one:Degree / formField-degree / education/degree."""
    u = (ftype or "").upper().replace(" ", "")
    if not u:
        return False
    if "FIELD" in u and "STUDY" in u:
        return False
    if u in (DEGREE, "DEGREE"):
        return True
    return "DEGREE" in u


def _intent_aliases(intent: str | None, field_meta: dict) -> list[str]:
    out: list[str] = []
    if intent:
        out.append(str(intent).strip())
    for key in ("value", "picked", "aliases_tried", "option_text"):
        v = field_meta.get(key)
        if isinstance(v, list):
            out.extend(str(x).strip() for x in v if x)
        elif v:
            out.append(str(v).strip())
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        k = x.lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq


def field_is_done_from_readback(
    readback: Any,
    field_meta: dict,
    intent: str | None = None,
) -> DoneVerdict:
    """Sync completion check from a readback string or date-spin dict."""
    from verified_select import (
        how_heard_source_committed,
        is_committed_us_phone_country_readback,
        is_multiselect_uncommitted,
        is_placeholder_select_value,
        multiselect_has_chip,
        soft_value_match,
        value_matches_readback,
        workday_wrap_text_has_chip,
    )

    ftype = _field_type(field_meta)
    cands = _intent_aliases(intent, field_meta)

    if isinstance(readback, dict):
        try:
            from workday_date_readback import (
                committed_spin_parts,
                date_spin_matches,
                should_skip_end_date,
            )

            present_checked = bool(
                field_meta.get("present_checked")
                or str(intent or "").strip().lower() in ("present", "current", "now")
            )
            end_disabled = bool(
                field_meta.get("end_disabled") or field_meta.get("to_disabled")
            )
            month, year = committed_spin_parts(readback)
            if should_skip_end_date(
                present_checked=present_checked, end_enabled=not end_disabled
            ) and not (month and year):
                return DoneVerdict(True, "present_disabled_end_skip", readback)
            intent_m = str(field_meta.get("month") or cands[0] if cands else "")
            intent_y = str(field_meta.get("year") or (cands[1] if len(cands) > 1 else ""))
            if not intent_m and not intent_y and cands:
                parts = str(cands[0]).replace("/", "-").split("-")
                if len(parts) >= 2:
                    intent_m, intent_y = parts[0], parts[1]
            ok_m = not intent_m or date_spin_matches(month, intent_m, kind="month")
            ok_y = not intent_y or date_spin_matches(year, intent_y, kind="year")
            if month and year and ok_m and ok_y:
                return DoneVerdict(True, "date_spin_committed", readback)
            # 1154Z: any committed MM/YYYY (01/2024) is done — skip-lock, do not
            # fight resume autofill that differs from dummy parser intent.
            if month and year:
                return DoneVerdict(True, "autofill_committed_skip", readback)
            if month or year:
                return DoneVerdict(False, "date_spin_partial", readback)
            return DoneVerdict(False, "date_spin_empty", readback)
        except Exception:
            return DoneVerdict(False, "date_spin_probe_error", readback)

    rb = str(readback or "").strip()
    if not rb:
        return DoneVerdict(False, "empty_readback", rb)

    if (
        rb.lower() in ("present", "current", "now")
        or str(intent or "").strip().lower() in ("present", "current", "now")
    ) and (
        field_meta.get("widget") == "date_spin"
        or field_meta.get("mode") == "date_spin"
        or "DATE" in ftype
        or field_meta.get("present_checked")
        or field_meta.get("end_disabled")
    ):
        return DoneVerdict(True, "present_disabled_end_skip", rb)

    try:
        from ashby_widgets import is_empty_ui_value

        if is_empty_ui_value(rb):
            return DoneVerdict(False, "placeholder_or_uncommitted", rb)
    except Exception:
        pass

    if ftype in (PHONE_COUNTRY_CODE, "PHONE_COUNTRY_CODE", "phone_country_code"):
        if is_committed_us_phone_country_readback(rb):
            return DoneVerdict(True, "phone_country_chip", rb)
        val = (intent or (cands[0] if cands else "United States (+1)")).strip()
        if val and (
            value_matches_readback(val, rb, mode="combobox")
            or is_committed_us_phone_country_readback(rb)
        ):
            return DoneVerdict(True, "phone_country_chip", rb)
        return DoneVerdict(False, "phone_country_not_us", rb)

    if ftype in ("EXPERIENCE_LOCATION", "LOCATION", "ADDRESS_CITY"):
        try:
            from verified_select import location_display_matches, location_search_query

            city = location_search_query(intent or (cands[0] if cands else "") or "")
            if location_display_matches(
                rb,
                cands or None,
                city=city or "Springfield",
                state="IL",
                state_full="Illinois",
            ):
                return DoneVerdict(True, "location_display_match", rb)
        except Exception:
            pass
        # 1116Z: dummy Springfield already shown (comma optional — "Springfield IL")
        if dummy_springfield_location_shown(rb):
            return DoneVerdict(True, "location_display_match", rb)

    if ftype in (FIELD_OF_STUDY, "FIELD_OF_STUDY", "DISCIPLINE", "MAJOR"):
        dom_chip = bool(field_meta.get("dom_chip"))
        s = rb.strip()
        from verified_select import (
            _fos_intent_matches_candidate,
            fos_committed_chip_label,
            is_multiselect_uncommitted,
            is_placeholder_select_value,
            looks_like_phone_country_or_address_chip,
        )

        # Match against committed chip only — not open promptOption soup.
        chip = fos_committed_chip_label(s) or s
        if (
            not chip
            or is_multiselect_uncommitted(chip)
            or looks_like_phone_country_or_address_chip(chip)
            or is_placeholder_select_value(chip)
        ):
            return DoneVerdict(False, "fos_uncommitted", rb)
        has_chip = (
            multiselect_has_chip(s)
            or dom_chip
            or workday_wrap_text_has_chip(s)
            or bool(fos_committed_chip_label(s))
        )
        if not has_chip:
            return DoneVerdict(False, "fos_uncommitted", rb)
        if cands:
            if any(_fos_intent_matches_candidate(c, chip) for c in cands):
                return DoneVerdict(True, "fos_chip_match", chip)
            return DoneVerdict(False, "fos_chip_wrong_value", chip)
        return DoneVerdict(True, "fos_chip_match", chip)

    if ftype in (HOW_HEARD, "how_heard"):
        from verified_select import (
            is_how_heard_category_option,
            is_uncommitted_filter_text,
            looks_like_phone_country_or_address_chip,
        )

        if how_heard_source_committed(rb, cands):
            return DoneVerdict(True, "how_heard_chip", rb)
        # Exact leaf token without chip chrome (Indeed == Indeed) is keepable.
        # Never accept category headers, dial chips, or typed filter fragments.
        rb_l = rb.strip().lower()
        for token in cands:
            tok = str(token or "").strip()
            if (
                tok
                and rb_l == tok.lower()
                and not is_how_heard_category_option(tok)
                and not looks_like_phone_country_or_address_chip(rb)
                and len(rb.strip()) >= 3
            ):
                return DoneVerdict(True, "how_heard_leaf_token", rb)
        if cands and any(
            is_uncommitted_filter_text(rb, str(c), from_input=True) for c in cands if c
        ):
            return DoneVerdict(False, "how_heard_uncommitted", rb)
        return DoneVerdict(False, "how_heard_uncommitted", rb)

    if _is_degree_field(ftype):
        from verified_select import degree_display_matches_intent, looks_like_workday_internal_id

        if looks_like_workday_internal_id(rb):
            return DoneVerdict(False, "degree_hash_readback", rb)
        # Intent + aliases_tried only — a wrong ``picked`` (gh_select A.A. vs
        # Master's) must not count as done.
        want = (intent or "").strip()
        degree_cands: list[str] = []
        if want:
            degree_cands.append(want)
        aliases = field_meta.get("aliases_tried")
        if isinstance(aliases, list):
            degree_cands.extend(str(a).strip() for a in aliases if a)
        try:
            from dummy_answers import DEGREE_ALIASES

            if want and any(
                degree_display_matches_intent(want, a) or degree_display_matches_intent(a, want)
                for a in DEGREE_ALIASES
            ):
                degree_cands.extend(DEGREE_ALIASES)
        except Exception:
            pass
        seen_d: set[str] = set()
        uniq_d: list[str] = []
        for x in degree_cands:
            k = x.lower()
            if k and k not in seen_d:
                seen_d.add(k)
                uniq_d.append(x)
        if uniq_d and any(degree_display_matches_intent(rb, c) for c in uniq_d):
            return DoneVerdict(True, "degree_match", rb)
        if uniq_d:
            return DoneVerdict(False, "text_mismatch", rb)
        return DoneVerdict(False, "text_mismatch", rb) if rb else DoneVerdict(
            False, "empty_readback", rb
        )

    # ADDRESS_STATE: expand IL ↔ Illinois before soft-match
    from field_map import ADDRESS_STATE as _ADDRESS_STATE

    if ftype in (_ADDRESS_STATE, "ADDRESS_STATE") or "countryregion" in ftype.lower():
        from verified_select import expand_state_value

        expanded: list[str] = []
        for c in cands:
            expanded.extend(expand_state_value(c) or [c])
        # de-dupe
        seen_e: set[str] = set()
        state_cands: list[str] = []
        for x in expanded:
            k = x.lower()
            if k and k not in seen_e:
                seen_e.add(k)
                state_cands.append(x)
        if is_placeholder_select_value(rb) or is_multiselect_uncommitted(rb):
            return DoneVerdict(False, "placeholder_or_uncommitted", rb)
        if state_cands and any(
            value_matches_readback(c, rb, mode="combobox") or soft_value_match(c, rb)
            for c in state_cands
        ):
            return DoneVerdict(True, "state_match", rb)
        if state_cands:
            return DoneVerdict(False, "text_mismatch", rb)
        return DoneVerdict(True, "nonempty_readback", rb) if rb else DoneVerdict(
            False, "empty_readback", rb
        )

    mode = str(field_meta.get("mode") or "")
    if mode in ("radio", "yesno") or field_meta.get("kind") == "radio_group":
        picked = str(field_meta.get("picked") or field_meta.get("value") or intent or "")
        if field_meta.get("aria_checked") is True or rb.lower() in ("true", "checked"):
            return DoneVerdict(True, "aria_checked", rb)
        if picked and (soft_value_match(picked, rb) or picked.lower() in rb.lower()):
            return DoneVerdict(True, "radio_picked", rb)
        return DoneVerdict(False, "radio_unanswered", rb)

    if is_placeholder_select_value(rb) or is_multiselect_uncommitted(rb):
        return DoneVerdict(False, "placeholder_or_uncommitted", rb)

    if intent:
        # Match intended dummy (and aliases_tried) — a wrong ``picked`` must not
        # count as done (gh_select A.A. vs Master's).
        intent_cands: list[str] = [str(intent).strip()]
        aliases = field_meta.get("aliases_tried")
        if isinstance(aliases, list):
            intent_cands.extend(str(a).strip() for a in aliases if a)
        if any(value_matches_readback(c, rb, mode="fill") for c in intent_cands if c):
            return DoneVerdict(True, "text_match", rb)
        if multiselect_has_chip(rb):
            return DoneVerdict(False, "chip_wrong_value", rb)
        return DoneVerdict(False, "text_mismatch", rb)

    if cands:
        if any(value_matches_readback(c, rb, mode="fill") for c in cands):
            return DoneVerdict(True, "text_match", rb)
        if multiselect_has_chip(rb):
            return DoneVerdict(False, "chip_wrong_value", rb)
        return DoneVerdict(False, "text_mismatch", rb)

    return DoneVerdict(True, "nonempty_readback", rb)


def field_is_done_from_row(row: dict | None, intent: str | None = None) -> DoneVerdict:
    """Completion check from a fill report row (post-fill readback).

    Do not add a parallel oracle — ``is_verified_fill_row`` delegates here.
    """
    if not isinstance(row, dict):
        return DoneVerdict(False, "missing_row", None)
    if row.get("ok") is False or row.get("verified") is False:
        rb = row.get("readback") or row.get("shown")
        return DoneVerdict(False, "row_marked_unverified", rb)
    if row.get("status") == "stuck":
        return DoneVerdict(False, "stuck", row.get("readback"))

    try:
        from resume_upload import is_resume_attachment_row

        if is_resume_attachment_row(row) and (
            row.get("verified") is True or row.get("ok") is True
        ):
            rb_res = row.get("readback") or row.get("shown") or row.get("value")
            return DoneVerdict(True, "resume_attachment", rb_res)
    except Exception:
        pass

    rb = row.get("readback") if row.get("readback") is not None else row.get("shown")
    if isinstance(rb, dict):
        try:
            from workday_date_readback import normalize_spin_readback

            rb = normalize_spin_readback(rb) or rb
        except Exception:
            pass
    intent_val = intent or str(
        row.get("value") or row.get("picked") or row.get("option_text") or ""
    )
    meta = dict(row)
    verdict = field_is_done_from_readback(rb, meta, intent_val)
    if verdict.ok:
        return verdict

    ftype = _field_type(meta)
    if ftype in (PHONE_COUNTRY_CODE, "PHONE_COUNTRY_CODE", "phone_country_code"):
        for alt_key in ("shown", "picked", "readback_before"):
            alt = row.get(alt_key)
            if not alt or alt == rb:
                continue
            alt_v = field_is_done_from_readback(alt, meta, intent_val)
            if alt_v.ok:
                return alt_v
    return verdict


def _fos_aria_snapshot(page, aid: str = "") -> Any:
    """Cheap ARIA/selectedItem oracle for FoS expanded/chip (no fiber walk)."""
    return page.evaluate(
        """(aid) => {
          const roots = [];
          if (aid) {
            const hit = document.querySelector('[data-automation-id="' + aid + '"]');
            if (hit) roots.push(hit);
          }
          document.querySelectorAll(
            '[data-automation-id*="fieldOfStudy"],'
            + '[data-automation-id*="discipline"],'
            + '[data-automation-id*="major"]'
          ).forEach((n) => roots.push(n));
          for (const wrap of roots) {
            const selected = wrap.querySelector(
              '[data-automation-id="selectedItem"],'
              + '[aria-selected="true"],'
              + '[aria-checked="true"]'
            );
            if (selected) {
              const t = (
                selected.getAttribute('aria-label')
                || selected.getAttribute('data-committed')
                || selected.innerText
                || ''
              ).trim();
              if (t) return t.slice(0, 240);
            }
            const al = (wrap.getAttribute('aria-label') || '').trim();
            if (al) return al.slice(0, 240);
          }
          return '';
        }""",
        aid or "",
    )


async def field_is_done(
    page,
    field_meta: dict,
    intent: str | None = None,
) -> DoneVerdict:
    """Live DOM completion check — single source of truth."""
    ftype = _field_type(field_meta)
    aid = str(field_meta.get("automation_id") or field_meta.get("id") or "")

    if ftype in (PHONE_COUNTRY_CODE, "PHONE_COUNTRY_CODE", "phone_country_code"):
        from verified_select import read_phone_country_field_snip

        rb = await read_phone_country_field_snip(page)
        return field_is_done_from_readback(rb, {**field_meta, "type": PHONE_COUNTRY_CODE}, intent)

    if ftype in (FIELD_OF_STUDY, "FIELD_OF_STUDY", "DISCIPLINE", "MAJOR"):
        from verified_select import read_workday_formfield_chip

        sel_candidates: list[str] = []
        if aid:
            sel_candidates.append(f'[data-automation-id="{aid}"]')
        sel_candidates.extend(
            [
                '[data-automation-id*="fieldOfStudy"]',
                '[data-automation-id*="Field of Study"]',
                '[data-automation-id*="discipline"]',
                '[data-automation-id*="major"]',
            ]
        )
        meta = {**field_meta, "type": FIELD_OF_STUDY, "dom_chip": True}
        chips: list[str] = []
        matching = False
        for sel in sel_candidates:
            loc = page.locator(sel).first
            try:
                if not await loc.count():
                    continue
            except Exception:
                continue
            rb = await read_workday_formfield_chip(loc)
            if not rb:
                try:
                    rb = str(await loc.inner_text(timeout=1500) or "").strip()[:240]
                except Exception:
                    rb = ""
            if not rb or rb in chips:
                continue
            chips.append(rb)
            v = field_is_done_from_readback(rb, meta, intent)
            if v.ok:
                matching = True
            elif v.reason in ("fos_chip_wrong_value", "chip_wrong_value", "text_mismatch"):
                return DoneVerdict(False, "fos_chip_wrong_value", rb)
        # Secondary oracle: ARIA selectedItem / aria-label when chip text missed.
        if not matching:
            try:
                aria_rb = str(await _fos_aria_snapshot(page, aid) or "").strip()
            except Exception:
                aria_rb = ""
            if aria_rb and aria_rb not in chips:
                chips.append(aria_rb)
                v = field_is_done_from_readback(aria_rb, meta, intent)
                if v.ok:
                    matching = True
                elif v.reason in ("fos_chip_wrong_value", "chip_wrong_value", "text_mismatch"):
                    return DoneVerdict(False, "fos_chip_wrong_value", aria_rb)
        if matching:
            return DoneVerdict(True, "fos_chip_match", chips[0] if chips else "")
        rb = chips[0] if chips else ""
        return field_is_done_from_readback(rb, meta, intent)

    if ftype in (HOW_HEARD, "how_heard"):
        from verified_select import read_workday_formfield_chip

        # Alias ids like ``how_heard`` are not in the DOM (live is
        # ``source--source`` / ``formField-source``). Never inner_text() a
        # missing locator — Playwright waits the default 30s.
        rb = ""
        try:
            rb = str(
                await page.evaluate(
                    """() => {
                      const wrap = document.querySelector(
                        '[data-automation-id="formField-source"], '
                        + '[data-automation-id*="formField-source"], '
                        + '[data-automation-id*="formField-howHeard"], '
                        + '[data-automation-id*="formField-howDidYouHear"], '
                        + '[data-automation-id*="formField-candidateSource"]'
                      );
                      if (!wrap) return '';
                      const clone = wrap.cloneNode(true);
                      clone.querySelectorAll(
                        '[role="listbox"], [data-automation-id="promptLeafNode"], .menu'
                      ).forEach((n) => n.remove());
                      return (clone.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 240);
                    }"""
                )
                or ""
            ).strip()
        except Exception:
            rb = ""
        if not rb:
            sels: list[str] = []
            aid_l = aid.lower()
            if aid and aid_l not in ("how_heard", "howheard", "source"):
                sels.append(f'[data-automation-id="{aid}"]')
            sels.extend(
                [
                    '[data-automation-id="formField-source"]',
                    '[data-automation-id="source--source"]',
                    '[data-automation-id*="howHeard"]',
                    '[data-automation-id*="formField-source"]',
                ]
            )
            for sel in sels:
                loc = page.locator(sel).first
                try:
                    if not await loc.count():
                        continue
                except Exception:
                    continue
                try:
                    rb = await read_workday_formfield_chip(loc)
                except Exception:
                    rb = ""
                if not rb:
                    try:
                        rb = str(await loc.inner_text(timeout=1500) or "").strip()[:240]
                    except Exception:
                        rb = ""
                if rb:
                    break
        return field_is_done_from_readback(rb, {**field_meta, "type": HOW_HEARD}, intent)

    # Workday State/Province (addressSection_countryRegion) — IL ↔ Illinois
    from field_map import ADDRESS_STATE as _ADDRESS_STATE

    if ftype in (_ADDRESS_STATE, "ADDRESS_STATE") or "countryregion" in aid.lower():
        from verified_select import (
            expand_state_value,
            is_placeholder_select_value,
            read_workday_formfield_chip,
        )

        sels = []
        if aid:
            sels.append(f'[data-automation-id="{aid}"]')
        sels.extend(
            [
                '[data-automation-id="formField-countryRegion"]',
                '[data-automation-id="addressSection_countryRegion"]',
                '[data-automation-id*="countryRegion" i]',
            ]
        )
        meta = {**field_meta, "type": _ADDRESS_STATE}
        aliases = expand_state_value(intent or "") or ([intent] if intent else [])
        for sel in sels:
            loc = page.locator(sel).first
            try:
                if not await loc.count():
                    continue
            except Exception:
                continue
            rb = ""
            try:
                rb = await read_workday_formfield_chip(loc)
            except Exception:
                rb = ""
            if not rb:
                try:
                    rb = str(await loc.inner_text(timeout=1500) or "").strip()[:240]
                except Exception:
                    rb = ""
            if not rb or is_placeholder_select_value(rb):
                continue
            # Prefer expanded aliases so IL intent matches Illinois chip
            if aliases:
                meta_aliases = {**meta, "aliases_tried": aliases}
                v = field_is_done_from_readback(rb, meta_aliases, intent)
            else:
                v = field_is_done_from_readback(rb, meta, intent)
            return v
        return DoneVerdict(False, "empty_readback", "")

    mode = str(field_meta.get("mode") or field_meta.get("kind") or "")
    if mode in ("radio", "yesno", "radio_group"):
        name = field_meta.get("name") or field_meta.get("selector") or ""
        try:
            rb = await page.evaluate(
                """(name) => {
                  if (!name) return '';
                  const checked = document.querySelector(
                    'input[type=radio][name="' + name + '"][aria-checked="true"],'
                    + 'input[type=radio][name="' + name + '"]:checked'
                  );
                  if (!checked) return '';
                  const lab = checked.closest('label');
                  return (lab ? lab.innerText : checked.value || 'checked').trim();
                }""",
                str(name),
            )
            meta = {**field_meta, "mode": "radio", "aria_checked": bool(rb)}
            return field_is_done_from_readback(str(rb or ""), meta, intent)
        except Exception as e:
            return DoneVerdict(False, f"radio_probe_error:{e}", None)

    if field_meta.get("widget") == "date_spin" or "date" in ftype.lower():
        spin_aid = aid or field_meta.get("automation_id") or ""
        try:
            rb = await page.evaluate(
                """(aid) => {
                  const wrap = aid
                    ? document.querySelector('[data-automation-id="' + aid + '"]')
                    : document.querySelector('[data-automation-id*="dateSection"]');
                  if (!wrap) return null;
                  const spins = wrap.querySelectorAll('input[type=text], input:not([type=hidden])');
                  const month = spins[0] ? (spins[0].value || '').trim() : '';
                  const year = spins[1] ? (spins[1].value || '').trim() : '';
                  const displays = wrap.querySelectorAll('[data-automation-id*="dateSection"] span, .spinbutton');
                  return { month_input: month, year_input: year };
                }""",
                spin_aid,
            )
            if isinstance(rb, dict):
                return field_is_done_from_readback(rb, {**field_meta, "widget": "date_spin"}, intent)
        except Exception as e:
            return DoneVerdict(False, f"date_spin_probe_error:{e}", None)

    # Native text / select fallback
    sel = field_meta.get("selector") or (f'#{aid}' if aid else "")
    rb = ""
    if sel:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                try:
                    rb = str(await loc.input_value(timeout=1500) or "").strip()
                except Exception:
                    rb = ""
                if not rb:
                    try:
                        rb = str(await loc.inner_text(timeout=1500) or "").strip()
                    except Exception:
                        rb = ""
        except Exception:
            pass
    return field_is_done_from_readback(rb, field_meta, intent)


def filter_phone_country_false_empties(
    rows: list[dict] | None,
    phone_snip: str = "",
) -> list[dict]:
    """Drop required-empty / gap rows when phone country chip is committed."""
    if not rows:
        return []
    meta = {"type": PHONE_COUNTRY_CODE}
    verdict = field_is_done_from_readback(phone_snip, meta, "United States (+1)")
    if not verdict.ok:
        return list(rows)
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)
            continue
        blob = " ".join(
            str(r.get(k) or "")
            for k in ("label", "id", "automation_id", "reason")
        ).lower()
        if "phone" in blob and "country" in blob:
            continue
        if "countryphonecode" in blob.replace(" ", ""):
            continue
        out.append(r)
    return out


_ABSENT_FIELD_REASONS = frozenset({
    "not_in_dom",
    "not_visible",
    "radio_not_found",
    "no_matching_option",
})

# Contact Next must not invent Apt / county when the widget is absent (NXP 0842Z).
_OPTIONAL_ABSENT_AID_NEEDLES = (
    "addressline2",
    "address-line2",
    "regionsubdivision1",
    "region_subdivision",
)


def _norm_empty_id(empty_row: dict) -> str:
    return (
        str(
            empty_row.get("id")
            or empty_row.get("automation_id")
            or empty_row.get("name")
            or ""
        )
        .lower()
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
    )


def is_optional_absent_empty(empty_row: dict | None) -> bool:
    """True for Apt/county rows that are not_in_dom / not_visible (not live required)."""
    if not isinstance(empty_row, dict):
        return False
    reason = str(empty_row.get("reason") or "").lower()
    if reason not in _ABSENT_FIELD_REASONS and reason != "optional_miss":
        return False
    eid = _norm_empty_id(empty_row)
    if any(n.replace("_", "").replace("-", "") in eid for n in _OPTIONAL_ABSENT_AID_NEEDLES):
        return True
    label = str(empty_row.get("label") or "").lower()
    if "county" in label or "parish" in label:
        return True
    if "address line 2" in label or "addressline2" in label.replace(" ", ""):
        return True
    if "apt" in label.split() or "apartment" in label or "suite" in label:
        # Avoid "United States" / "aptitude" — only short apt/unit labels
        if "united" in label:
            return False
        return True
    return False


def filled_rows_honest(report: dict) -> bool:
    """True when every *claimed* fill in filled[] passes field_is_done_from_row.

    Unverified ``not_in_dom`` / probe rows from earlier packs must not poison
    a later page's advance gate. Rows that claim verified/ok still must match.
    """
    for row in report.get("filled") or []:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "")
        if reason in _ABSENT_FIELD_REASONS:
            continue
        if row.get("verified") is not True and row.get("ok") is not True:
            continue
        if not field_is_done_from_row(row).ok:
            return False
    return True


async def filter_gaps_false_incomplete(page, gaps: list[dict] | None) -> list[dict]:
    """Remove DOM gaps that field_is_done says are actually complete."""
    if not gaps:
        return []
    kept: list[dict] = []
    phone_meta = {"type": PHONE_COUNTRY_CODE}
    phone_v = await field_is_done(page, phone_meta, "United States (+1)")
    for g in gaps:
        if not isinstance(g, dict):
            kept.append(g)
            continue
        label = str(g.get("label") or "").lower()
        aid = str(g.get("automation_id") or "").lower()
        if phone_v.ok and ("phone" in label and "country" in label or "countryphonecode" in aid):
            continue
        kept.append(g)
    return filter_phone_country_false_empties(kept, str(phone_v.readback or ""))


_DATE_EMPTY_REASONS = frozenset({
    "empty_required_date_spin",
    "empty_required_date_display",
    "empty_required_date_field",
})

_SKIP_DONE_REASONS = frozenset({
    "already_correct_skip",
    "autofill_committed_skip",
    "present_disabled_end_skip",
    "field_locked_skip",
    "already_correct_keep",
    "already_verified_or_locked",
})

# (empty-id needle, type aliases, Workday automation-id token)
_EXPERIENCE_EMPTY_KEYS = (
    ("jobtitle", ("EXPERIENCE_TITLE", "CURRENT_TITLE"), "jobTitle"),
    ("companyname", ("EXPERIENCE_COMPANY", "CURRENT_COMPANY"), "company"),
    ("company", ("EXPERIENCE_COMPANY", "CURRENT_COMPANY"), "company"),
    ("location", ("EXPERIENCE_LOCATION", "LOCATION", "CURRENT_LOCATION"), "location"),
)


def dummy_springfield_location_shown(readback: str | None) -> bool:
    """True when dummy Springfield, IL (comma optional) is already displayed."""
    s = re.sub(r"\s+", " ", (readback or "").strip().lower())
    if "springfield" not in s:
        return False
    return bool(re.search(r"\b(il|illinois)\b", s))


def _row_is_skip_done(row: dict) -> bool:
    if row.get("skipped_already_correct") or row.get("skipped_locked"):
        return True
    return str(row.get("reason") or "") in _SKIP_DONE_REASONS


def _empty_is_date_field(empty_row: dict) -> bool:
    """From*/To* / startDate / endDate — including unclassified leftover theater."""
    eid = str(empty_row.get("id") or "").lower()
    label = str(empty_row.get("label") or "").lower()
    reason = str(empty_row.get("reason") or "").lower()
    if reason in _DATE_EMPTY_REASONS or "empty_required_date" in reason:
        return True
    blob = (
        f"{eid} {label}".replace(" ", "").replace("-", "").replace("_", "").replace("*", "")
    )
    lab = label.strip().rstrip("*")
    if lab in ("from", "to") or "startdate" in blob or "enddate" in blob:
        return True
    return False


def _row_has_committed_text(row: dict) -> bool:
    rb = row.get("readback")
    if isinstance(rb, dict):
        try:
            from workday_date_readback import committed_spin_parts

            month, year = committed_spin_parts(rb)
            return bool(month and year)
        except Exception:
            blob = " ".join(str(v) for v in rb.values() if v)
            return bool(re.search(r"\d", blob))
    s = str(rb or "").strip()
    if not s:
        return False
    low = s.lower()
    if low in ("select one", "select", "mm", "yyyy", "mm / yyyy", "present"):
        return low == "present"
    return True


def _experience_row_matches_empty(row: dict, empty_row: dict) -> bool:
    """Match Job Title*/Company* empties to workExperience-1/jobTitle (case-insensitive)."""
    eid = str(empty_row.get("id") or "").lower().replace("_", "").replace("-", "")
    label = str(empty_row.get("label") or "").lower().replace(" ", "")
    aid = str(row.get("automation_id") or "").lower()
    ftype = str(row.get("type") or "").upper().replace("-", "").replace("_", "")
    for key, exp_fts, exp_aid in _EXPERIENCE_EMPTY_KEYS:
        if key not in eid and key not in label:
            continue
        if exp_aid.lower() in aid:
            return True
        if any(ft.replace("_", "") in ftype for ft in exp_fts):
            return True
    return False


def _skip_locked_covers_empty(report: dict | None, empty_row: dict) -> bool:
    """True when field_lock already skip-locked this experience/date empty."""
    if not report or not isinstance(empty_row, dict):
        return False
    eid = str(empty_row.get("id") or "").lower()
    label = str(empty_row.get("label") or "").lower()
    reason = str(empty_row.get("reason") or "")
    try:
        from field_lock import get_field_locks, resolve_lock_report

        sess = get_field_locks(resolve_lock_report(report))
    except Exception:
        sess = None
    if sess is None:
        return False
    for key, exp_fts, exp_aid in _EXPERIENCE_EMPTY_KEYS:
        compact_e = eid.replace("_", "").replace("-", "")
        if key not in compact_e and key not in label.replace(" ", ""):
            continue
        aid = f"workExperience-1/{exp_aid}"
        try:
            if sess.is_locked(field_type=exp_fts[0], automation_id=aid):
                return True
        except Exception:
            continue
    if reason in _DATE_EMPTY_REASONS or _empty_is_date_field(empty_row):
        is_end = "end" in eid or (label.strip().startswith("to") and "today" not in label)
        is_start = "start" in eid or "from" in label
        aids: list[str] = []
        if is_start:
            aids.append("workExperience-1/startDate")
        if is_end:
            aids.append("workExperience-1/endDate")
        for aid in aids:
            try:
                if sess.is_locked(field_type="EXPERIENCE_DATE", automation_id=aid):
                    return True
            except Exception:
                continue
    return False


def _intent_from_report(report: dict | None, field_type: str) -> str | None:
    if not report:
        return None
    ft = field_type.upper()
    for row in report.get("filled") or []:
        if not isinstance(row, dict):
            continue
        row_ft = str(row.get("type") or "").upper()
        if row_ft == ft:
            return str(row.get("value") or row.get("readback") or "") or None
        aid = str(row.get("automation_id") or "")
        if ft in ("EXPERIENCE_TITLE", "CURRENT_TITLE") and (
            "jobtitle" in aid.lower() or "JOBTITLE" in row_ft.replace("_", "").replace("-", "")
        ):
            return str(row.get("value") or row.get("readback") or "") or None
        if ft in ("EXPERIENCE_COMPANY", "CURRENT_COMPANY") and (
            "company" in aid.lower() or "COMPANY" in row_ft
        ):
            return str(row.get("value") or row.get("readback") or "") or None
        if ft in ("EXPERIENCE_LOCATION", "LOCATION") and "location" in (
            aid.lower() + " " + row_ft.lower()
        ):
            return str(row.get("value") or row.get("readback") or "") or None
        if ft in ("NAME_FIRST", NAME_FIRST) and "firstname" in aid.lower().replace("_", ""):
            return str(row.get("value") or row.get("readback") or "") or None
        if ft in ("NAME_LAST", NAME_LAST) and "lastname" in aid.lower().replace("_", ""):
            return str(row.get("value") or row.get("readback") or "") or None
        if ft in (PHONE_COUNTRY_CODE, "PHONE_COUNTRY_CODE") and "countryphonecode" in aid.lower().replace(
            " ", ""
        ):
            return str(row.get("value") or row.get("readback") or "") or None
    for bag_key in ("fill_values", "_contact_values"):
        bag = report.get(bag_key)
        if isinstance(bag, dict) and bag.get(ft):
            return str(bag.get(ft) or "") or None
        if isinstance(bag, dict) and bag.get(field_type):
            return str(bag.get(field_type) or "") or None
    return None


def _row_covers_empty(row: dict, empty_row: dict) -> bool:
    """True when a filled row satisfies this required_empty entry."""
    eid = str(empty_row.get("id") or "").lower()
    label = str(empty_row.get("label") or "").lower()
    reason = str(empty_row.get("reason") or "")
    aid = str(row.get("automation_id") or "").lower()
    ftype = str(row.get("type") or "").upper()

    done = field_is_done_from_row(row).ok
    skip_done = _row_is_skip_done(row)
    committed = _row_has_committed_text(row)
    # Title/company: skip-lock only covers when dummy text is already shown (never blank).
    # Dates: skip-lock / Present-disabled is enough even if display lags.
    date_empty = _empty_is_date_field(empty_row)
    if not done:
        if date_empty and skip_done:
            pass
        elif skip_done and committed and _experience_row_matches_empty(row, empty_row):
            pass
        else:
            return False

    if _experience_row_matches_empty(row, empty_row):
        if done or (skip_done and committed):
            return True

    if date_empty or ("date" in reason and reason in _DATE_EMPTY_REASONS):
        if row.get("mode") == "date_spin" or "date" in ftype or skip_done:
            aid_c = aid.replace("-", "").replace("_", "").replace("/", "")
            blob = (
                f"{eid} {label}".lower()
                .replace(" ", "")
                .replace("-", "")
                .replace("_", "")
                .replace("*", "")
            )
            lab = label.strip().rstrip("*")
            if ("startdate" in blob or lab == "from") and "startdate" in aid_c:
                return True
            if ("enddate" in blob or lab == "to") and "enddate" in aid_c:
                return True
            # Display placeholder (MM/YYYY) false-empty when spin inputs committed
            if reason == "empty_required_date_display" and (done or skip_done):
                if "startdate" in aid_c and ("from" in label or "mm" in label):
                    return True
                if "enddate" in aid_c and "to" in label:
                    return True

    if "email" in eid or "email" in label:
        if ftype == "EMAIL" or "email" in aid:
            return True

    if ftype in (FIELD_OF_STUDY, "FIELD_OF_STUDY", "DISCIPLINE", "MAJOR"):
        if "field of study" in label or "discipline" in label:
            return True

    if ftype in (HOW_HEARD, "HOW_HEARD"):
        if "how did you hear" in label or "source" in eid:
            return True

    # Workday State/Province (addressSection_countryRegion) — do not false-empty
    # when ADDRESS_STATE is already verified (NXP 2244Z pack_incomplete).
    if ftype in ("ADDRESS_STATE",) or "countryregion" in aid:
        if (
            "countryregion" in eid
            or "country region" in label
            or ("state" in label and "province" in label)
            or (
                ("state" in label or "province" in label)
                and "country phone" not in label
                and "phone country" not in label
            )
        ):
            return True

    compact_e = eid.replace(" ", "").replace("-", "").replace("_", "")
    compact_a = aid.replace(" ", "").replace("-", "").replace("_", "")
    if ftype in (NAME_FIRST, "NAME_FIRST") or "firstname" in compact_a:
        if "firstname" in compact_e or ("first" in label and "name" in label):
            return True
    if ftype in (NAME_LAST, "NAME_LAST") or "lastname" in compact_a:
        if "lastname" in compact_e or ("last" in label and "name" in label):
            return True
    if (
        ftype in (PHONE_COUNTRY_CODE, "PHONE_COUNTRY_CODE", "COUNTRYPHONECODE")
        or "countryphonecode" in compact_a
    ):
        if (
            "countryphonecode" in compact_e
            or ("phone" in label and "country" in label)
            or ("phone" in eid and "country" in eid)
        ):
            return True

    return False


async def _live_probe_empty_row(page, empty_row: dict, report: dict | None) -> bool:
    """Live DOM probe — True when field_is_done says this empty is false."""
    eid = str(empty_row.get("id") or "").lower()
    label = str(empty_row.get("label") or "").lower()
    reason = str(empty_row.get("reason") or "")

    if _skip_locked_covers_empty(report, empty_row):
        return True

    _dummy_intents = {
        "EXPERIENCE_TITLE": "Applied AI/ML Analyst",
        "EXPERIENCE_COMPANY": "Example Corp",
        "EXPERIENCE_LOCATION": "Springfield, IL",
    }
    for key, ftypes, exp_aid in _EXPERIENCE_EMPTY_KEYS:
        if key in eid or (key in label and "*" in label):
            ftype = ftypes[0]
            intent = _intent_from_report(report, ftype) or _dummy_intents.get(ftype)
            meta = {"type": ftype, "automation_id": f"workExperience-1/{exp_aid}"}
            try:
                sel = (
                    f'input[name="{exp_aid}" i], '
                    f'input[data-automation-id="{exp_aid}"], '
                    f'[data-automation-id="{exp_aid}"] input:not([type=hidden]), '
                    f'[data-automation-id*="{exp_aid}"] input:not([type=hidden]), '
                    f'input[aria-label*="{exp_aid.replace("jobTitle", "Job Title")}" i]'
                )
                loc = page.locator(sel).first
                if await loc.count():
                    rb = ""
                    try:
                        rb = str(await loc.input_value() or "").strip()
                    except Exception:
                        rb = ""
                    if not rb:
                        try:
                            rb = str(await loc.inner_text() or "").strip()
                        except Exception:
                            rb = ""
                    if rb and field_is_done_from_readback(rb, meta, intent).ok:
                        return True
                    if ftype == "EXPERIENCE_LOCATION" and dummy_springfield_location_shown(rb):
                        return True
            except Exception:
                pass
            try:
                if (await field_is_done(page, meta, intent)).ok:
                    return True
            except Exception:
                pass

    if reason in _DATE_EMPTY_REASONS or _empty_is_date_field(empty_row):
        is_end = "end" in eid or ("to" in label and "today" not in label)
        if is_end:
            try:
                present_disabled = await page.evaluate(
                    """() => {
                      const cur = document.querySelector(
                        'input[name="currentlyWorkHere"], '
                        + 'input[type=checkbox][data-automation-id*="currentlyWork" i]'
                      );
                      if (!cur || !(cur.checked || cur.getAttribute('aria-checked') === 'true'))
                        return false;
                      const field = document.querySelector(
                        '[data-automation-id="formField-endDate"]'
                      );
                      if (!field) return false;
                      const ins = [...field.querySelectorAll('input:not([type=hidden])')];
                      return ins.length > 0 && ins.every((el) =>
                        el.disabled || el.getAttribute('aria-disabled') === 'true'
                      );
                    }"""
                )
                if present_disabled:
                    return True
            except Exception:
                pass
        is_start = "start" in eid or "from" in label
        # 1154Z / 1138: live formField-startDate/endDate with committed digits
        # (input OR display 01/2024) is done even when filled[] missed skip-lock.
        try:
            committed = await page.evaluate(
                """(isEnd) => {
                  const aid = isEnd ? 'formField-endDate' : 'formField-startDate';
                  const field = document.querySelector(
                    '[data-automation-id="' + aid + '"]'
                  ) || document.querySelector(
                    '[data-automation-id*="' + aid + '"]'
                  );
                  if (!field) return false;
                  const monthIns = [...field.querySelectorAll(
                    'input[data-automation-id="dateSectionMonth-input"]'
                  )];
                  const yearIns = [...field.querySelectorAll(
                    'input[data-automation-id="dateSectionYear-input"]'
                  )];
                  const mDisp = field.querySelector(
                    '[data-automation-id="dateSectionMonth-display"]'
                  );
                  const yDisp = field.querySelector(
                    '[data-automation-id="dateSectionYear-display"]'
                  );
                  const ph = (t) => {
                    const s = String(t || '').trim().toUpperCase();
                    return !s || s === 'MM' || s === 'YYYY' || s === 'M' || s === 'Y';
                  };
                  const monthOk = monthIns.some((el) => {
                    const t = (el.value || '').trim();
                    return t && !ph(t) && /\\d/.test(t);
                  }) || (mDisp && /\\d/.test(mDisp.innerText || ''));
                  const yearOk = yearIns.some((el) => {
                    const t = (el.value || '').trim();
                    return t && !ph(t) && /\\d/.test(t);
                  }) || (yDisp && /\\d/.test(yDisp.innerText || ''));
                  return !!(monthOk && yearOk);
                }""",
                bool(is_end and not is_start) if is_end or is_start else False,
            )
            if committed and (is_start or is_end):
                return True
        except Exception:
            pass
        ft = "EXPERIENCE_DATE"
        for row in (report or {}).get("filled") or []:
            if not isinstance(row, dict):
                continue
            aid = str(row.get("automation_id") or "").lower()
            ftype = str(row.get("type") or "").lower()
            if row.get("mode") != "date_spin" and "date" not in ftype and "date" not in aid:
                continue
            if is_start and "startdate" not in aid:
                continue
            if is_end and "enddate" not in aid:
                continue
            if _row_is_skip_done(row) and _row_has_committed_text(row):
                return True
            intent = f"{row.get('month') or ''}/{row.get('year') or ''}"
            meta = {"widget": "date_spin", "mode": "date_spin", "type": ft}
            if row.get("month"):
                meta["month"] = row["month"]
            if row.get("year"):
                meta["year"] = row["year"]
            try:
                if (await field_is_done(page, meta, intent)).ok:
                    return True
            except Exception:
                if field_is_done_from_row(row).ok:
                    return True

    if "field of study" in label or ("discipline" in label and "majority" not in label):
        intent = _intent_from_report(report, "FIELD_OF_STUDY") or "Computer Science"
        try:
            if (await field_is_done(page, {"type": FIELD_OF_STUDY, "dom_chip": True}, intent)).ok:
                return True
        except Exception:
            pass

    if "how did you hear" in label or "source" in eid:
        intent = _intent_from_report(report, HOW_HEARD)
        try:
            if (await field_is_done(page, {"type": HOW_HEARD}, intent)).ok:
                return True
        except Exception:
            pass

    if (
        "countryregion" in eid
        or "country region" in label
        or (
            ("state" in label or "province" in label)
            and "country phone" not in label
            and "phone country" not in label
        )
    ):
        from field_map import ADDRESS_STATE as _AS

        intent = _intent_from_report(report, _AS) or "IL"
        try:
            if (
                await field_is_done(
                    page,
                    {
                        "type": _AS,
                        "automation_id": "addressSection_countryRegion",
                    },
                    intent,
                )
            ).ok:
                return True
        except Exception:
            pass

    compact_e = eid.replace(" ", "").replace("-", "").replace("_", "")
    if "firstname" in compact_e or ("first" in label and "name" in label):
        intent = _intent_from_report(report, NAME_FIRST) or "Test"
        try:
            loc = page.locator(
                'input[name="legalName--firstName"], '
                '[data-automation-id="legalNameSection_firstName"], '
                'input[name*="firstName" i]'
            ).first
            if await loc.count():
                rb = str(await loc.input_value() or "").strip()
                if field_is_done_from_readback(
                    rb, {"type": NAME_FIRST}, intent
                ).ok:
                    return True
        except Exception:
            pass
        try:
            if (
                await field_is_done(
                    page,
                    {
                        "type": NAME_FIRST,
                        "automation_id": "legalNameSection_firstName",
                        "selector": 'input[name="legalName--firstName"]',
                    },
                    intent,
                )
            ).ok:
                return True
        except Exception:
            pass
    if "lastname" in compact_e or ("last" in label and "name" in label):
        intent = _intent_from_report(report, NAME_LAST) or "Dummy"
        try:
            loc = page.locator(
                'input[name="legalName--lastName"], '
                '[data-automation-id="legalNameSection_lastName"], '
                'input[name*="lastName" i]'
            ).first
            if await loc.count():
                rb = str(await loc.input_value() or "").strip()
                if field_is_done_from_readback(
                    rb, {"type": NAME_LAST}, intent
                ).ok:
                    return True
        except Exception:
            pass
        try:
            if (
                await field_is_done(
                    page,
                    {
                        "type": NAME_LAST,
                        "automation_id": "legalNameSection_lastName",
                        "selector": 'input[name="legalName--lastName"]',
                    },
                    intent,
                )
            ).ok:
                return True
        except Exception:
            pass
    if (
        "countryphonecode" in compact_e
        or ("phone" in label and "country" in label)
        or ("phone" in eid and "country" in eid)
    ):
        intent = _intent_from_report(report, PHONE_COUNTRY_CODE) or "United States (+1)"
        try:
            if (
                await field_is_done(
                    page,
                    {"type": PHONE_COUNTRY_CODE},
                    intent,
                )
            ).ok:
                return True
        except Exception:
            pass

    return False


async def filter_required_empty_false_incomplete(
    page,
    report: dict | None,
    empties: list[dict] | None,
) -> list[dict]:
    """Drop required-empty rows when field_is_done / filled[] say complete."""
    if not empties:
        return []
    try:
        from verified_select import (
            phone_country_verified_snips_from_report,
            read_phone_country_field_snip,
        )

        live_snip = await read_phone_country_field_snip(page)
        fallbacks = phone_country_verified_snips_from_report(report or {})
        snip = live_snip or (fallbacks[0] if fallbacks else "")
        rows = filter_phone_country_false_empties(empties, snip)
    except Exception:
        rows = list(empties)

    kept: list[dict] = []
    for e in rows:
        if not isinstance(e, dict):
            kept.append(e)
            continue
        if is_optional_absent_empty(e):
            continue
        try:
            from workday_date_readback import (
                is_date_spin_theater_label,
                is_optional_gpa_label,
            )

            if is_date_spin_theater_label(e.get("label")) or is_date_spin_theater_label(
                e.get("id")
            ) or is_optional_gpa_label(e.get("label")):
                continue
        except Exception:
            pass
        try:
            from verified_select import fos_skip_allows_advance

            blob = " ".join(
                str(e.get(k) or "")
                for k in ("label", "id", "automation_id")
            ).lower()
            if fos_skip_allows_advance(report) and (
                "field of study" in blob
                or "fieldofstudy" in blob.replace(" ", "").replace("-", "").replace("_", "")
                or "discipline" in blob
                or re.search(r"\bmajor\b", blob)
            ):
                continue
        except Exception:
            pass
        covered = _skip_locked_covers_empty(report, e)
        if report and not covered:
            for row in report.get("filled") or []:
                if isinstance(row, dict) and _row_covers_empty(row, e):
                    covered = True
                    break
        if not covered:
            try:
                covered = await _live_probe_empty_row(page, e, report)
            except Exception:
                covered = False
        if not covered:
            kept.append(e)
    return kept


def filter_required_empty_from_report(
    report: dict,
    empties: list[dict] | None,
) -> list[dict]:
    """Sync filter using filled[] + phone snips only (no live page)."""
    if not empties:
        return []
    try:
        from verified_select import phone_country_verified_snips_from_report

        snips = phone_country_verified_snips_from_report(report)
        snip = snips[0] if snips else ""
        rows = filter_phone_country_false_empties(empties, snip)
    except Exception:
        rows = list(empties)
    kept: list[dict] = []
    for e in rows:
        if not isinstance(e, dict):
            kept.append(e)
            continue
        if is_optional_absent_empty(e):
            continue
        try:
            from workday_date_readback import (
                is_date_spin_theater_label,
                is_optional_gpa_label,
            )

            if is_date_spin_theater_label(e.get("label")) or is_date_spin_theater_label(
                e.get("id")
            ) or is_optional_gpa_label(e.get("label")):
                continue
        except Exception:
            pass
        if _skip_locked_covers_empty(report, e):
            continue
        if any(
            isinstance(row, dict) and _row_covers_empty(row, e)
            for row in (report.get("filled") or [])
        ):
            continue
        kept.append(e)
    return kept
