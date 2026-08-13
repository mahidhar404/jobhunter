"""Workday date-spin readback helpers (no exp_workday_selectors import).

Extracted to break circular import: field_done → exp_workday_selectors.
Dummy-only; never submit.
"""
from __future__ import annotations

import re


def date_spin_matches(got: str, want: str, *, kind: str) -> bool:
    """True when readback is a real committed value (not MM/YYYY placeholder)."""
    raw = (got or "").strip()
    upper = raw.upper()
    if not raw or upper in {
        "MM", "M", "YYYY", "YY", "DD", "D", "MONTH", "YEAR",
    }:
        return False
    digits = re.sub(r"\D", "", raw)
    want_d = re.sub(r"\D", "", str(want or ""))
    if not digits or not want_d:
        return False
    if kind == "month":
        try:
            return int(digits) == int(want_d)
        except ValueError:
            return False
    return want_d in digits or digits == want_d


def committed_spin_parts(rb: dict) -> tuple[str, str]:
    """Extract (MM, YYYY) from a date-spin readback dict when committed.

    Input digits win. Display digits are a fallback when the input is still
    MM/YYYY (Workday Fiber often paints the span first).
    """
    m_raw = re.sub(r"\D", "", str(rb.get("month_input") or ""))
    y_raw = re.sub(r"\D", "", str(rb.get("year_input") or ""))
    if not m_raw or len(y_raw) < 4:
        m_disp = re.sub(r"\D", "", str(rb.get("month_display") or ""))
        y_disp = re.sub(r"\D", "", str(rb.get("year_display") or ""))
        if not m_raw and m_disp:
            m_raw = m_disp
        if len(y_raw) < 4 and len(y_disp) >= 4:
            y_raw = y_disp
    if not m_raw or not y_raw or len(y_raw) < 4:
        return "", ""
    try:
        return f"{int(m_raw):02d}", y_raw[:4]
    except ValueError:
        return "", ""


def spin_part_matches(
    got_input: str, got_display: str, want: str, *, kind: str
) -> bool:
    """True when input or (placeholder input + committed display) matches want."""
    if date_spin_matches(got_input, want, kind=kind):
        if got_display and date_spin_matches(got_display, got_display, kind=kind):
            return date_spin_matches(got_display, want, kind=kind)
        return True
    if not date_spin_matches(got_input, got_input, kind=kind) and date_spin_matches(
        got_display, want, kind=kind
    ):
        return True
    return False


def should_skip_end_date(*, present_checked: bool, end_enabled: bool) -> bool:
    """Never fight a disabled/readonly To spin.

    NXP / battle gym: Present + disabled empty To is correct.
    Cisco: Present + enabled To still requires a date — do not skip.
    A disabled To is unfillable even if Present was not detected.
    ``present_checked`` is part of the public contract for callers.
    """
    if not end_enabled:
        return True
    return bool(present_checked) and not end_enabled


def spin_intent_parts(intent: str | None) -> tuple[str, str]:
    """Parse MM/YYYY or MM-YYYY intent into (month, year) digit strings."""
    s = str(intent or "").strip()
    if not s:
        return "", ""
    for sep in ("/", "-", " "):
        if sep in s:
            parts = s.split(sep, 1)
            if len(parts) == 2:
                return re.sub(r"\D", "", parts[0]), re.sub(r"\D", "", parts[1])
    digits = re.sub(r"\D", "", s)
    return "", digits


def normalize_spin_readback(readback: object) -> str:
    """Convert date-spin dict readback to ``MM/YYYY`` for supervisor/contract."""
    if isinstance(readback, dict):
        mm, yy = committed_spin_parts(readback)
        if mm and yy:
            return f"{mm}/{yy}"
        mi = str(readback.get("month_input") or "").strip()
        yi = str(readback.get("year_input") or "").strip()
        if mi and yi and date_spin_matches(mi, mi, kind="month") and date_spin_matches(
            yi, yi, kind="year"
        ):
            try:
                m_digits = re.sub(r"\D", "", mi)
                y_digits = re.sub(r"\D", "", yi)[:4]
                return f"{int(m_digits):02d}/{y_digits}"
            except ValueError:
                return f"{mi}/{yi}"
        return ""
    return str(readback or "").strip()


def date_spin_field_meta(field_type: str = "", intent: str | None = None) -> dict:
    """``field_is_done`` meta for Workday month/year spins."""
    month, year = spin_intent_parts(intent)
    meta: dict = {"widget": "date_spin", "mode": "date_spin"}
    ft = (field_type or "").strip()
    if ft:
        meta["type"] = ft
    if month:
        try:
            meta["month"] = f"{int(month):02d}"
        except ValueError:
            meta["month"] = month
    if year:
        meta["year"] = year[:4]
    return meta


def is_date_spin_theater_label(text: str | None) -> bool:
    """True for unclassified Month / Month — From* spin chrome — not a real leftover.

    These are the visible spinbutton names Workday paints next to already-committed
    From/To digits. They must not inflate leftover_count or block ADVANCE.
    """
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw:
        return False
    compact = re.sub(r"[^a-z0-9]+", "", raw.lower())
    if compact in {
        "month",
        "monthfrom",
        "year",
        "yearfrom",
        "yearto",
        "datesectionmonthdisplay",
        "datesectionyeardisplay",
        "datesectionmonthinput",
        "datesectionyearinput",
        "datesectionmonth",
        "datesectionyear",
    }:
        return True
    if re.fullmatch(r"month[\s/|:—–-]*from\*?", raw, re.I):
        return True
    if re.fullmatch(r"year([\s/|:—–-]*(from|to).*)?\*?", raw, re.I):
        return True
    return False


def is_optional_gpa_label(text: str | None) -> bool:
    """Optional education GPA is never a required leftover."""
    t = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return bool(t) and "gpa" in t


def is_date_spin_context(
    *,
    field_type: str = "",
    action: str = "",
    widget: str = "",
    mode: str = "",
    readback: object = None,
) -> bool:
    """True when readback/action metadata indicates a Workday date spin."""
    ft = (field_type or "").upper()
    if ft in ("EXPERIENCE_DATE", "EDUCATION_DATE") or "DATE" in ft:
        return True
    if (widget or mode) == "date_spin" or action == "date_spin":
        return True
    return isinstance(readback, dict) and (
        "month_input" in readback or "year_input" in readback
    )


# Back-compat aliases used by exp_workday_selectors
_date_spin_matches = date_spin_matches
_committed_spin_parts = committed_spin_parts
