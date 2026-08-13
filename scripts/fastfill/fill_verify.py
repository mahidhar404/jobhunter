"""Shared honest-fill verification (no Playwright / no fast_fill imports).

``is_verified_fill_row`` is a thin wrapper over ``field_done`` (the SSoT).
How-heard priority helpers stay here. Keep this module free of
``fast_fill`` / ``exp_workday_selectors`` imports to avoid cycles.
"""

from __future__ import annotations

import re
from typing import Any

from field_map import HOW_HEARD
from verified_select import (
    is_placeholder_select_value,
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
    """Honest filled metric — delegates to ``field_done`` SSoT.

    Do not add a parallel oracle here. Phone-country / how-heard / placeholder
    rules live in ``field_is_done_from_row`` / ``field_is_done_from_readback``.
    """
    from field_done import field_is_done_from_row

    return field_is_done_from_row(row).ok


# Workday tenants (Walmart etc.) use these as *category* headers, not leaf chips.
HOW_HEARD_CATEGORY_LABELS = frozenset(
    {
        "internet job board",
        "internet",
        "job board",
        "job boards",
        "website",
        "web site",
        "advertising",
        "event",
        "events",
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
    raw = str(text or "").strip()
    t = " ".join(re.sub(r"\s*[>›»]\s*$", "", raw).strip().lower().split())
    if not t or t == "internet":
        return True
    if t in HOW_HEARD_CATEGORY_LABELS:
        return True
    # Drill rows: "Website >", "Employee Referral >"
    if raw.rstrip().endswith(">") or raw.rstrip().endswith("›"):
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
                # Hierarchical tenants: "Web - LinkedIn" beats bare "LinkedIn"
                # when both are in the open list (Website vs Job Board leaves).
                web_pref = next(
                    (
                        o
                        for o in opts
                        if o.lower().startswith("web -")
                        and _entry_matches(canonical, patterns, o)
                    ),
                    None,
                )
                return web_pref or opt
    return None


def how_heard_leaf_candidates(values: dict[str, Any] | None = None) -> list[str]:
    """Priority-ordered concrete sources — never bare category headers."""
    return how_heard_priority_labels(include_categories=False) or ["LinkedIn"]


def how_heard_category_candidates(values: dict[str, Any] | None = None) -> list[str]:
    """Category/subsection headers used only to *navigate* hierarchical menus."""
    heard = str((values or {}).get(HOW_HEARD) or "").strip()
    # Website first — dummy "Internet job board" is a category, not a reason to
    # rank Job Board above Website (that picks bare LinkedIn instead of Web - LinkedIn).
    out: list[str] = []
    for alt in (
        "Website",
        "Job Board",
        "Internet job board",
        "Internet",
        "Job Boards",
    ):
        if alt.lower() not in {c.lower() for c in out}:
            out.append(alt)
    if heard and is_how_heard_category_option(heard):
        if heard.lower() not in {c.lower() for c in out}:
            out.append(heard)
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
