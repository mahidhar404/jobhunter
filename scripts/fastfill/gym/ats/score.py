"""Score a Playwright page against gym gold fixtures."""
from __future__ import annotations

import re
from typing import Any


def _soft_norm(value: str | None) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s@.+$-]", "", text)
    return text.strip()


async def _read_committed(page, selector: str) -> str:
    loc = page.locator(selector).first
    if await loc.count() == 0:
        return ""

    tag = (await loc.evaluate("el => el.tagName")).lower()

    # Prefer explicit commit markers (Workday chips / fiber stubs) over raw input.
    for attr in ("data-committed", "data-value"):
        attr_val = await loc.get_attribute(attr)
        if attr_val:
            return attr_val
    # Child chip / selectedItem often holds the commit when selector is a wrap.
    child_chip = loc.locator("[data-committed], [data-automation-id='selectedItem']").first
    if await child_chip.count():
        child_attr = await child_chip.get_attribute("data-committed")
        if child_attr:
            return child_attr
        child_text = (await child_chip.inner_text()).strip()
        if child_text:
            # Strip delete-chip glyph noise
            return child_text.replace("×", "").strip()

    if tag in ("input", "textarea", "select"):
        if tag == "input":
            input_type = (await loc.get_attribute("type") or "").lower()
            if input_type in ("radio", "checkbox"):
                if not await loc.is_checked():
                    return ""
        val = await loc.input_value()
        if val:
            return val
        attr = await loc.get_attribute("value")
        if attr and tag != "input":
            return attr

    for attr in ("value",):
        attr_val = await loc.get_attribute(attr)
        if attr_val:
            return attr_val

    role = await loc.get_attribute("role")
    if role == "combobox":
        inner = loc.locator(".single-value, [class*='singleValue'], .select__single-value")
        if await inner.count():
            text = (await inner.first.inner_text()).strip()
            if text:
                return text
        text = (await loc.inner_text()).strip()
        if text:
            return text

    single = loc.locator(".single-value, [class*='singleValue']")
    if await single.count():
        text = (await single.first.inner_text()).strip()
        if text:
            return text

    if tag == "select":
        selected = loc.locator("option:checked")
        if await selected.count():
            return (await selected.first.inner_text()).strip()

    text = (await loc.inner_text()).strip()
    return text


async def _detect_footer_kind(page) -> str:
    advance = page.locator(
        'button:has-text("Next"), button:has-text("Continue"), '
        'button:has-text("Save and Continue"), input[type="submit"][value*="Next"]'
    )
    final = page.locator(
        'button:has-text("Submit"), button:has-text("Apply"), '
        'input[type="submit"][value*="Submit"], input[type="submit"][value*="Apply"]'
    )
    has_advance = await advance.count() > 0 and await advance.first.is_visible()
    has_final = await final.count() > 0 and await final.first.is_visible()

    if has_advance and has_final:
        return "ADVANCE"
    if has_advance:
        return "ADVANCE"
    if has_final:
        return "FINAL"
    return "NONE"


async def score_auth_gate(page, gold: dict) -> dict[str, Any]:
    """Score Workday auth-gate fixture via DOM probes (no field commits)."""
    import sys
    from pathlib import Path

    fastfill = Path(__file__).resolve().parent.parent.parent
    if str(fastfill) not in sys.path:
        sys.path.insert(0, str(fastfill))

    from exp_workday_selectors import (
        _create_account_form,
        _create_account_link_present,
        _email_field_present,
        _password_only_signin,
        _sign_in_with_email_present,
        workday_auth_gate_action,
    )

    spec = gold.get("auth_gate") or {}
    has_create = await _create_account_form(page)
    has_email = await _email_field_present(page)
    has_reveal = await _sign_in_with_email_present(page)
    has_signin = await _password_only_signin(page)
    has_ca_link = await _create_account_link_present(page)
    action = workday_auth_gate_action(
        has_create_form=has_create,
        has_signin_form=has_signin,
        has_email_field=has_email,
        has_sign_in_with_email=has_reveal,
        has_create_account_link=has_ca_link,
        prefer_stored_signin=False,
    )
    expected_action = spec.get("initial_action", "reveal_email")
    ok = action == expected_action
    if spec.get("has_sign_in_with_email") is not None:
        ok = ok and has_reveal == spec["has_sign_in_with_email"]
    if spec.get("has_email_field") is not None:
        ok = ok and has_email == spec["has_email_field"]
    if spec.get("has_create_form") is not None:
        ok = ok and has_create == spec["has_create_form"]
    if spec.get("has_create_account_link") is not None:
        ok = ok and has_ca_link == spec["has_create_account_link"]
    detail = f"action={action!r} expected={expected_action!r}"
    if not ok:
        detail += (
            f"; reveal={has_reveal} email={has_email} create={has_create}"
            f" signin={has_signin} ca_link={has_ca_link}"
        )
    return {"ok": ok, "field_results": [], "footer_ok": True, "detail": detail, "auth_action": action}


async def score_page(page, gold: dict) -> dict[str, Any]:
    """Compare page field committed values and footer against gold fixture."""
    if gold.get("auth_gate"):
        return await score_auth_gate(page, gold)

    field_results: list[dict[str, Any]] = []
    all_ok = True

    for spec in gold.get("fields", []):
        key = spec.get("key", "")
        selector = spec.get("selector", "")
        expected = spec.get("committed", "")
        required = bool(spec.get("required", True))

        actual = await _read_committed(page, selector) if selector else ""
        exp_norm = _soft_norm(expected)
        act_norm = _soft_norm(actual)
        matched = exp_norm == act_norm
        # Same completion SSoT as live fill — taxonomy aliases (Science-Computer
        # vs Computer Science) must not false-fail gold. Do not add a parallel oracle.
        if not matched and expected and actual:
            try:
                import sys
                from pathlib import Path

                fastfill = Path(__file__).resolve().parent.parent.parent
                if str(fastfill) not in sys.path:
                    sys.path.insert(0, str(fastfill))
                from field_done import field_is_done_from_readback

                key_l = str(key or "").lower()
                ftype = str(spec.get("type") or spec.get("field_type") or "")
                if not ftype:
                    if "field_of_study" in key_l or "fos" in key_l or "discipline" in key_l:
                        ftype = "FIELD_OF_STUDY"
                    elif "how" in key_l and "hear" in key_l:
                        ftype = "HOW_HEARD"
                    elif "phone" in key_l and "country" in key_l:
                        ftype = "PHONE_COUNTRY_CODE"
                    elif "degree" in key_l:
                        ftype = "DEGREE"
                meta = {"type": ftype or "TEXT"}
                if str(ftype).upper() in ("FIELD_OF_STUDY", "DISCIPLINE", "MAJOR"):
                    meta["dom_chip"] = True
                if field_is_done_from_readback(actual, meta, expected).ok:
                    matched = True
            except Exception:
                pass

        if required and not matched:
            all_ok = False

        field_results.append(
            {
                "key": key,
                "selector": selector,
                "expected": expected,
                "actual": actual,
                "matched": matched,
                "required": required,
            }
        )

    expected_footer = gold.get("footer_kind", "NONE")
    actual_footer = await _detect_footer_kind(page)
    footer_ok = actual_footer == expected_footer
    if not footer_ok:
        all_ok = False

    expect_advance = gold.get("expect_advance", False)
    if expect_advance and actual_footer != "ADVANCE":
        all_ok = False
        footer_ok = False

    spa_ok = True
    spa = gold.get("spa_fingerprint") or {}
    if spa.get("selector") and spa.get("must_be_visible"):
        loc = page.locator(spa["selector"]).first
        spa_ok = (await loc.count() > 0) and await loc.is_visible()
        if not spa_ok:
            all_ok = False
    progress_step = spa.get("progress_step")
    if progress_step:
        progress = page.locator("#progress, [data-spa-step]").first
        if await progress.count():
            actual_step = await progress.get_attribute("data-spa-step")
            if actual_step != progress_step:
                spa_ok = False
                all_ok = False

    listbox_ok = True
    if gold.get("listbox_must_be_closed"):
        open_boxes = page.locator(
            '[aria-expanded="true"][role="combobox"], '
            '[role="combobox"][aria-expanded="true"]'
        )
        # Visible expanded comboboxes mean uncommitted menus
        n_open = await open_boxes.count()
        visible_open = 0
        for i in range(n_open):
            if await open_boxes.nth(i).is_visible():
                visible_open += 1
        listbox_ok = visible_open == 0
        if not listbox_ok:
            all_ok = False

    detail_parts: list[str] = []
    for fr in field_results:
        if fr["required"] and not fr["matched"]:
            detail_parts.append(f"{fr['key']}: expected {fr['expected']!r} got {fr['actual']!r}")
    if not footer_ok:
        detail_parts.append(f"footer: expected {expected_footer} got {actual_footer}")
    if spa and not spa_ok:
        detail_parts.append("spa_fingerprint mismatch")
    if gold.get("listbox_must_be_closed") and not listbox_ok:
        detail_parts.append("listbox still open")

    return {
        "ok": all_ok,
        "field_results": field_results,
        "footer_ok": footer_ok,
        "spa_ok": spa_ok,
        "listbox_ok": listbox_ok,
        "detail": "; ".join(detail_parts) if detail_parts else "all checks passed",
    }
