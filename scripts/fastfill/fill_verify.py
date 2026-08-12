"""Shared honest-fill verification (no Playwright / no fast_fill imports).

Used by ``fast_fill.is_verified_fill_row`` and Workday ``_is_verified_fill``
so both paths apply the same read-back gates. Keep this module free of
``fast_fill`` / ``exp_workday_selectors`` imports to avoid cycles.
"""

from __future__ import annotations

from typing import Any

from ashby_widgets import is_empty_ui_value
from field_map import HOW_HEARD
from resume_upload import is_resume_attachment_row
from verified_select import (
    how_heard_source_committed,
    is_multiselect_uncommitted,
    is_placeholder_select_value,
    is_uncommitted_filter_text,
    soft_value_match,
    value_matches_readback,
)


def _how_heard_chip_matches_intent(rb: str, *, intended: str, picked: str | None) -> bool:
    """True when readback chip names picked/intended leaf — not an unrelated source."""
    if picked and soft_value_match(str(picked), rb):
        return True
    if (
        intended
        and not is_how_heard_category_option(intended)
        and (
            soft_value_match(intended, rb)
            or intended.lower() in rb.lower()
        )
    ):
        return True
    return False


def _gh_select_readback_verified(row: dict, rb: str) -> bool:
    """gh_select rows must show committed display matching aliases — never click-claimed."""
    display = rb or str(row.get("shown") or row.get("picked") or "").strip()
    if not display or is_placeholder_select_value(display):
        return False
    ftype = str(row.get("type") or "")
    aliases = row.get("aliases_tried")
    if isinstance(aliases, list) and aliases:
        cands = [str(a) for a in aliases if a]
    elif ftype in (HOW_HEARD, "how_heard"):
        cands = how_heard_candidates({HOW_HEARD: row.get("value") or ""})
    else:
        val = str(row.get("value") or row.get("picked") or "")
        cands = [val] if val else []
        picked = str(row.get("picked") or "")
        if picked and picked not in cands:
            cands.append(picked)
    if not cands:
        return bool(display)
    try:
        from gh_select import _score_option
        from verified_select import select_readback_ok

        return select_readback_ok(
            display,
            cands,
            picked=str(row.get("picked") or ""),
            score_fn=_score_option,
            min_score=50,
        )
    except Exception:
        val = str(row.get("value") or row.get("picked") or "")
        return bool(val and value_matches_readback(val, display, mode="fill"))


def is_verified_fill_row(row: dict | None) -> bool:
    """Honest filled metric: only count verified read-back (never status=stuck)."""
    if not isinstance(row, dict):
        return False
    if row.get("ok") is False or row.get("verified") is False:
        return False
    if row.get("status") == "stuck":
        return False
    # Workday date spins store readback as a dict {month_input, year_input, ...}
    raw_rb = row.get("readback") if row.get("readback") is not None else row.get("shown")
    if isinstance(raw_rb, dict):
        parts = [
            str(raw_rb.get(k) or "")
            for k in (
                "month_input",
                "year_input",
                "month_display",
                "year_display",
                "text",
                "value",
            )
            if raw_rb.get(k)
        ]
        raw_rb = " ".join(parts) if parts else ""
    rb = str(raw_rb or "").strip()
    # Placeholder readbacks are never verified — even if verified=True was set.
    if rb and is_empty_ui_value(rb):
        return False
    if is_multiselect_uncommitted(rb):
        return False
    ftype_early = str(row.get("type") or row.get("automation_id") or "")
    if ftype_early in (HOW_HEARD, "how_heard"):
        # Multi-select: filter token ("Internet") without committed chip is not a fill.
        intended_hh = str(row.get("value") or row.get("picked") or row.get("option_text") or "")
        picked_hh = row.get("picked") or row.get("option_text")
        from verified_select import (
            is_how_heard_category_option,
            looks_like_phone_country_or_address_chip,
            multiselect_has_chip,
        )

        if how_heard_source_committed(rb, [intended_hh, str(picked_hh or "")]):
            # Chip chrome must name intended/picked leaf — unrelated chips are not fills.
            if _how_heard_chip_matches_intent(
                rb, intended=intended_hh, picked=str(picked_hh or "") or None
            ):
                return True
        if (
            multiselect_has_chip(rb)
            and picked_hh
            and _how_heard_chip_matches_intent(
                rb, intended=intended_hh, picked=str(picked_hh)
            )
        ):
            return True
        # Exact leaf token alone (Indeed == Indeed) is keepable — some tenants
        # show the leaf without "N items selected" chrome after commit. Never
        # accept category headers or phone/country dial chips as how-heard.
        for token in (str(picked_hh or "").strip(), intended_hh.strip()):
            if (
                token
                and rb
                and rb.strip().lower() == token.lower()
                and not is_how_heard_category_option(token)
                and not looks_like_phone_country_or_address_chip(rb)
                and len(rb.strip()) >= 3
            ):
                return True
        # Option clicked + picked soft-matches intended + display confirms pick.
        # Never accept bare filter fragments ("Internet" for "Internet job board").
        if (
            (row.get("option_clicked") or row.get("committed") is True)
            and intended_hh
            and picked_hh
            and soft_value_match(intended_hh, str(picked_hh))
            and (
                soft_value_match(str(picked_hh), rb)
                or soft_value_match(intended_hh, rb)
            )
            and not (
                rb
                and rb.lower() != intended_hh.lower()
                and rb.lower() in intended_hh.lower()
                and len(rb) < len(intended_hh)
            )
        ):
            return True
        if (
            is_multiselect_uncommitted(rb)
            or "0 items selected" in rb.lower()
            or (rb and "items selected" in rb.lower() and not row.get("option_clicked") and not picked_hh)
            or row.get("committed") is False
            or (
                intended_hh
                and is_uncommitted_filter_text(
                    rb, intended_hh, picked=picked_hh, from_input=True
                )
            )
            or (
                intended_hh
                and rb
                and rb.lower() != intended_hh.lower()
                and rb.lower() in intended_hh.lower()
                and len(rb) < len(intended_hh)
            )
        ):
            return False
    if row.get("verified") is True:
        # Still require non-empty non-placeholder readback for text-like fills
        mode = str(row.get("mode") or "")
        via = str(row.get("via") or "")
        if is_resume_attachment_row(row):
            return True
        # Choice widgets: picked/readback; never blanket-exempt ashby text/URL fills
        if mode in ("yesno", "radio", "checkbox"):
            return bool(row.get("picked") or row.get("readback") or rb)
        if via == "gh_select":
            return _gh_select_readback_verified(row, rb)
        if via.startswith("deterministic_reclaim") and (
            row.get("picked") or rb or row.get("shown")
        ):
            # Reclaim rows use same commit semantics as gh_select
            val = str(row.get("value") or row.get("picked") or "")
            if val and rb and value_matches_readback(val, rb, mode="fill"):
                return True
            if str(row.get("type") or "") == "ADDRESS_COUNTRY" and (
                row.get("picked") or rb
            ):
                if value_matches_readback(
                    val or "United States",
                    str(row.get("picked") or rb),
                    mode="fill",
                ):
                    return True
        if not rb:
            # verified=True with empty/null readback is dishonest for text/URL fills
            return False
        val = str(row.get("value") or row.get("picked") or "")
        if val and not value_matches_readback(val, rb, mode="fill"):
            return False
        # Explicit null verified_value (Agent reports) → never count
        if row.get("verified_value") is None and "verified_value" in row and not rb:
            return False
        return True
    widget = row.get("widget") if isinstance(row.get("widget"), dict) else {}
    if widget.get("verified") is True:
        return True
    if widget.get("ok") and widget.get("option_clicked") and (
        widget.get("option_text") or widget.get("value")
    ):
        return True
    via = str(row.get("via") or "")
    if is_resume_attachment_row(row):
        return row.get("ok") is True
    if rb and not is_empty_ui_value(rb):
        val = str(row.get("value") or row.get("picked") or "")
        if not val or value_matches_readback(val, rb, mode="fill"):
            return True
    if via == "gh_select" and row.get("ok") is not False and (
        row.get("shown") or row.get("picked")
    ):
        return _gh_select_readback_verified(row, rb)
    # Workday pack rows often use status=filled without ok=True
    if row.get("status") == "filled" and row.get("verified") is not False:
        return bool(rb) and rb.lower() not in ("select one", "select")
    return False


# Workday tenants (Walmart etc.) use these as *category* headers, not leaf chips.
HOW_HEARD_CATEGORY_LABELS = frozenset(
    {
        "internet job board",
        "job board",
        "job boards",
        "social media",
        "social network",
        "social networks",
        "career fair",
        "career fairs",
        "university",
        "campus",
        "agency",
        "staffing agency",
        "employee referral",
        "internal",
        "other source",
        "other sources",
    }
)

# Single source of truth: job-board / career-site priority (first match wins).
# Each entry: (canonical label, match aliases for enumerated options).
HOW_HEARD_SOURCE_PRIORITY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LinkedIn", ("linkedin", "linked in")),
    ("Indeed", ("indeed",)),
    ("BuiltIn", ("builtin", "built in", "built-in", "builtin.com")),
    ("Glassdoor", ("glassdoor",)),
    ("ZipRecruiter", ("ziprecruiter", "zip recruiter", "zip-recruiter")),
    ("Monster", ("monster",)),
    ("CareerBuilder", ("careerbuilder", "career builder")),
    (
        "Company Website",
        (
            "company website",
            "company site",
            "company web site",
            "career site",
            "careers site",
            "employer website",
            "corporate website",
        ),
    ),
    ("Job Board", ("job board", "internet job board", "online job board")),
    ("Other", ("other",)),
)

# Back-compat alias for callers that imported HOW_HEARD_LEAF_LABELS.
HOW_HEARD_LEAF_LABELS = tuple(label for label, _ in HOW_HEARD_SOURCE_PRIORITY)


def is_how_heard_category_option(text: str | None) -> bool:
    """True when option text is a Workday how-heard *subsection/category* header."""
    t = " ".join(str(text or "").strip().lower().split())
    if not t or t == "internet":
        return True
    if t in HOW_HEARD_CATEGORY_LABELS:
        return True
    # "Other Job Board" is a leaf-ish chip Walmart emits; not a navigable category.
    if t.startswith("other ") and "board" in t:
        return False
    if t.endswith(" job board") or t.endswith(" job boards"):
        return True
    return False


def _norm_how_heard_text(text: str | None) -> str:
    return " ".join(str(text or "").strip().lower().split())


def how_heard_priority_labels(*, include_categories: bool = False) -> list[str]:
    """Ordered canonical labels from ``HOW_HEARD_SOURCE_PRIORITY``."""
    out: list[str] = []
    for canonical, _ in HOW_HEARD_SOURCE_PRIORITY:
        if not include_categories and is_how_heard_category_option(canonical):
            if _norm_how_heard_text(canonical) != "job board":
                continue
        if canonical.lower() not in {c.lower() for c in out}:
            out.append(canonical)
    return out


def how_heard_option_matches_priority(priority_label: str, option_text: str) -> bool:
    """True when *option_text* matches a priority entry (canonical or alias)."""
    opt = _norm_how_heard_text(option_text)
    if not opt:
        return False
    want = _norm_how_heard_text(priority_label)
    for canonical, patterns in HOW_HEARD_SOURCE_PRIORITY:
        if _norm_how_heard_text(canonical) != want:
            continue
        canon_norm = _norm_how_heard_text(canonical)
        if opt == canon_norm:
            return True
        for pat in patterns:
            pn = _norm_how_heard_text(pat)
            if pn == opt or pn in opt or opt in pn:
                return True
        if soft_value_match(canonical, option_text):
            return True
        for pat in patterns:
            if soft_value_match(pat, option_text):
                return True
        return False
    return False


def pick_how_heard_from_options(
    options: list[str],
    *,
    include_categories: bool = False,
) -> str | None:
    """Walk source priority; return the first dropdown option that matches."""
    opts = [str(o).strip() for o in (options or []) if str(o).strip()]
    if not opts:
        return None

    def _entry_matches(canonical: str, patterns: tuple[str, ...], opt: str) -> bool:
        if how_heard_option_matches_priority(canonical, opt):
            return True
        on = _norm_how_heard_text(opt)
        for pat in patterns:
            pn = _norm_how_heard_text(pat)
            if pn == on or pn in on or on in pn:
                return True
        return False

    for canonical, patterns in HOW_HEARD_SOURCE_PRIORITY:
        for opt in opts:
            if not include_categories and is_how_heard_category_option(opt):
                # GH may commit "Job Board" as a chip; Workday categories are nav-only.
                if _norm_how_heard_text(canonical) not in (
                    "job board",
                    "internet job board",
                ):
                    continue
            if _entry_matches(canonical, patterns, opt):
                return opt
    return None


def how_heard_leaf_candidates(values: dict[str, Any] | None = None) -> list[str]:
    """Priority-ordered concrete sources — never bare category headers."""
    return how_heard_priority_labels(include_categories=False) or ["LinkedIn"]


def how_heard_category_candidates(values: dict[str, Any] | None = None) -> list[str]:
    """Category/subsection headers used only to *navigate* hierarchical menus."""
    heard = str((values or {}).get(HOW_HEARD) or "").strip()
    out: list[str] = []
    if heard and is_how_heard_category_option(heard):
        out.append(heard)
    for alt in ("Internet job board", "Job Board", "Job Boards"):
        if alt.lower() not in {c.lower() for c in out}:
            out.append(alt)
    return out


def how_heard_candidates(values: dict[str, Any] | None = None) -> list[str]:
    """Ordered HOW_HEARD labels — **priority leaves first**, categories last.

    Never bare ``Internet`` (filter text). Prefer LinkedIn/Indeed over
    ``Internet job board`` / ``Job Board`` so hierarchical tenants (Walmart)
    do not lock onto a category header as if it were a committed chip.
    """
    out: list[str] = []
    for a in (
        *how_heard_leaf_candidates(values),
        *how_heard_category_candidates(values),
    ):
        s = str(a or "").strip()
        if not s or s.lower() == "internet":
            continue
        if s.lower() not in {c.lower() for c in out}:
            out.append(s)
    return out or ["LinkedIn"]
