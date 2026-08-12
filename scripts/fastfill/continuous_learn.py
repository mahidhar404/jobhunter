#!/usr/bin/env python3
"""Continuous learning loop for fastfill — experience → better Layer 0/1 + Flash.

Extends (does not fork) ``record_replay``, ``learning.py`` (policy facts), and
``field_attempt_log`` (UNFILLABLE_AFTER_2). DeepSeek is never fine-tuned; we
persist verified structure + sanitized shapes so the next run prefers winning
selectors and grounds Flash leftovers.

Storage (under ``scripts/fastfill/learning_store/``)::

    experience.jsonl   — append-only verified / failed field outcomes
    selector_stats.json — per platform+host+selector success rates
    lessons.json / lessons.md — label-pattern → avoid strategy (fail ≥2)

Safety
------
- Test Mode ON (dummy): may store dummy values + value_shape for replay maps.
- Test Mode OFF (real): store selectors / types / success only — never raw PII.
- Always sanitize emails/phones/passwords to placeholders before disk write.
- never_submit unchanged.

CLI::

    skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --self-test
    skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --sanitize
    skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --stats
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
STORE_DIR = HERE / "learning_store"
EXPERIENCE_PATH = STORE_DIR / "experience.jsonl"
SELECTOR_STATS_PATH = STORE_DIR / "selector_stats.json"
LESSONS_JSON_PATH = STORE_DIR / "lessons.json"
LESSONS_MD_PATH = STORE_DIR / "lessons.md"

_log = logging.getLogger("continuous_learn")

# Placeholders — never store cleartext contact secrets in the corpus.
PLACEHOLDER_EMAIL = "{{EMAIL}}"
PLACEHOLDER_PHONE = "{{PHONE}}"
PLACEHOLDER_PASSWORD = "{{PASSWORD}}"
PLACEHOLDER_NAME = "{{NAME}}"
PLACEHOLDER_ADDRESS = "{{ADDRESS}}"
PLACEHOLDER_URL = "{{URL}}"
PLACEHOLDER_PII = "{{PII}}"

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)
_PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(?:\+?1[\s.\-]?)?"
    r"(?:\(\d{3}\)|\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}"
    r"(?!\d)",
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# Field types whose values are always PII — never persist cleartext even in dummy
# mode when test_mode is False; in dummy mode we may keep them for replay.
_PII_FIELD_TYPES = frozenset(
    {
        "EMAIL",
        "PHONE",
        "PASSWORD",
        "PASSWORD_CONFIRM",
        "NAME_FIRST",
        "NAME_LAST",
        "NAME_FULL",
        "NAME_MIDDLE",
        "ADDRESS_LINE1",
        "ADDRESS_CITY",
        "ADDRESS_STATE",
        "ADDRESS_ZIP",
        "ADDRESS_COUNTRY",
        "SSN",
    }
)

_SHAPE_MAP = {
    "EMAIL": "email",
    "PHONE": "phone",
    "PASSWORD": "password",
    "PASSWORD_CONFIRM": "password",
    "NAME_FIRST": "name",
    "NAME_LAST": "name",
    "NAME_FULL": "name",
    "LINKEDIN": "url",
    "GITHUB": "url",
    "PORTFOLIO": "url",
    "ADDRESS_LINE1": "address",
    "ADDRESS_CITY": "city",
    "ADDRESS_STATE": "state",
    "ADDRESS_ZIP": "postal",
    "ADDRESS_COUNTRY": "country",
}

MAX_EXPERIENCE_LINES = 5000  # soft rotate: keep newest
MAX_FLASH_SIMILAR = 5
MIN_SELECTOR_ATTEMPTS_FOR_DEMOTION = 2
DEMOTION_SUCCESS_RATE = 0.35  # below this → demote / deprioritize


def _ensure_store() -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize_host(url: str) -> str:
    try:
        host = (urlparse(url or "").netloc or "").lower().strip()
    except Exception as e:
        _log.debug("normalize_host failed for %r: %s", (url or "")[:80], e)
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host


def value_shape(value: str | None, field_type: str | None = None) -> str:
    """Classify a value into a non-PII shape token for structure learning."""
    ft = (field_type or "").strip().upper()
    if ft in _SHAPE_MAP:
        return _SHAPE_MAP[ft]
    v = (value or "").strip()
    if not v:
        return "empty"
    if _EMAIL_RE.search(v) or ("@" in v and "." in v.split("@")[-1]):
        return "email"
    if _PHONE_RE.search(v):
        return "phone"
    if _SSN_RE.search(v):
        return "ssn"
    if v.lower().startswith("http://") or v.lower().startswith("https://"):
        return "url"
    if len(v) > 120:
        return "essay"
    if re.fullmatch(r"\d+(\.\d+)?", v):
        return "number"
    if len(v) <= 40 and " " not in v and any(c.isupper() for c in v) and any(
        c.isdigit() for c in v
    ):
        # credential-ish short token
        try:
            from field_map import DUMMY_PROFILE

            if v == (DUMMY_PROFILE.get("account") or {}).get("password"):
                return "password"
        except Exception as e:
            _log.debug("value_shape password probe failed: %s", e)
    return "text"


def sanitize_value(
    value: str | None,
    *,
    field_type: str | None = None,
    test_mode: bool = True,
) -> str | None:
    """Strip / placeholder PII. Dummy test_mode may keep non-contact dummy text.

    Real mode (test_mode=False): always return None for value persistence —
    callers should store type/selector/shape only.
    """
    if value is None:
        return None
    v = str(value)
    if not v.strip():
        return None

    ft = (field_type or "").strip().upper()

    # Real profile runs: never persist values — structure only.
    if not test_mode:
        return None

    # Always redact cleartext contact / secrets even for dummy corpus hygiene
    # when the string looks like PII (defense in depth).
    if _EMAIL_RE.search(v):
        return PLACEHOLDER_EMAIL
    if _PHONE_RE.search(v):
        return PLACEHOLDER_PHONE
    if _SSN_RE.search(v):
        return PLACEHOLDER_PII

    try:
        from field_map import DUMMY_PROFILE

        pwd = (DUMMY_PROFILE.get("account") or {}).get("password")
        if pwd and v == pwd:
            return PLACEHOLDER_PASSWORD
    except Exception as e:
        _log.debug("sanitize password probe failed: %s", e)

    # Credential shape (mixed case + digit + symbol, no spaces/email)
    core = v.strip()
    if (
        " " not in core
        and "@" not in core
        and len(core) >= 8
        and any(c.isupper() for c in core)
        and any(c.islower() for c in core)
        and any(c.isdigit() for c in core)
        and any(not c.isalnum() for c in core)
    ):
        return PLACEHOLDER_PASSWORD

    if ft in {"EMAIL"}:
        return PLACEHOLDER_EMAIL
    if ft in {"PHONE"}:
        return PLACEHOLDER_PHONE
    if ft in {"PASSWORD", "PASSWORD_CONFIRM"}:
        return PLACEHOLDER_PASSWORD

    # Dummy mode: allow short dummy policy / select values for Flash replay hints
    if len(v) > 200:
        return v[:80] + "…{{TRUNCATED}}"
    return v


def _scrub_experience_row(row: dict[str, Any], *, test_mode: bool) -> dict[str, Any]:
    """Build a durable experience row — never cleartext real PII."""
    ftype = str(row.get("type") or row.get("field_type") or "").strip() or None
    raw_val = row.get("value") or row.get("readback") or row.get("verified_value")
    shape = value_shape(str(raw_val) if raw_val is not None else None, ftype)
    sanitized = sanitize_value(raw_val if isinstance(raw_val, str) else None, field_type=ftype, test_mode=test_mode)
    label = str(row.get("label") or "")[:120] or None
    # Labels can embed emails in rare ATS UIs — scrub.
    if label and _EMAIL_RE.search(label):
        label = _EMAIL_RE.sub(PLACEHOLDER_EMAIL, label)
    out: dict[str, Any] = {
        "ts": row.get("ts") or _utc_now(),
        "platform": str(row.get("platform") or "unknown").lower(),
        "host": str(row.get("host") or ""),
        "selector": str(row.get("selector") or "")[:200] or None,
        "type": ftype,
        "label": label,
        "value_shape": shape,
        "verified": bool(row.get("verified", row.get("ok", False))),
        "ok": bool(row.get("ok", row.get("verified", False))),
        "via": str(row.get("via") or row.get("layer") or "")[:60] or None,
        "test_mode": bool(test_mode),
    }
    # Dummy runs may keep sanitized value for type→value replay maps
    if test_mode and sanitized is not None and ftype and ftype.upper() not in _PII_FIELD_TYPES:
        # Policy / select / essay leftovers — useful for Flash grounding
        out["value"] = sanitized
    elif test_mode and sanitized is not None and ftype and ftype.upper() in _PII_FIELD_TYPES:
        # Store placeholder only so shape replay can resolve via DUMMY_PROFILE
        out["value"] = sanitized  # already a placeholder for contact types
    # real mode: no value key
    return out


def append_experience(rows: list[dict[str, Any]], *, test_mode: bool = True) -> int:
    """Append scrubbed experience rows to experience.jsonl. Returns count written."""
    if not rows:
        return 0
    _ensure_store()
    written = 0
    with EXPERIENCE_PATH.open("a", encoding="utf-8") as fh:
        for row in rows:
            if not isinstance(row, dict):
                continue
            scrubbed = _scrub_experience_row(row, test_mode=test_mode)
            if not scrubbed.get("selector") and not scrubbed.get("type"):
                continue
            fh.write(json.dumps(scrubbed, ensure_ascii=False) + "\n")
            written += 1
    _maybe_rotate_experience()
    return written


def _maybe_rotate_experience() -> None:
    if not EXPERIENCE_PATH.is_file():
        return
    try:
        lines = EXPERIENCE_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_EXPERIENCE_LINES:
        return
    keep = lines[-MAX_EXPERIENCE_LINES:]
    EXPERIENCE_PATH.write_text("\n".join(keep) + "\n", encoding="utf-8")


def load_experience(*, limit: int | None = None) -> list[dict[str, Any]]:
    if not EXPERIENCE_PATH.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in EXPERIENCE_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except OSError:
        return []
    if limit is not None and limit > 0:
        return out[-limit:]
    return out


def _load_selector_stats() -> dict[str, Any]:
    if not SELECTOR_STATS_PATH.is_file():
        return {"version": 1, "platforms": {}}
    try:
        data = json.loads(SELECTOR_STATS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "platforms": {}}
    if not isinstance(data, dict):
        return {"version": 1, "platforms": {}}
    data.setdefault("version", 1)
    data.setdefault("platforms", {})
    return data


def _save_selector_stats(data: dict[str, Any]) -> None:
    _ensure_store()
    SELECTOR_STATS_PATH.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _stats_key(platform: str, host: str, selector: str) -> str:
    return f"{(platform or 'unknown').lower()}|{host}|{selector}"


def update_selector_stats(
    rows: list[dict[str, Any]],
    *,
    platform: str,
    host: str = "",
) -> dict[str, Any]:
    """Update success/fail counts for selectors. Returns summary."""
    data = _load_selector_stats()
    plats = data.setdefault("platforms", {})
    plat = (platform or "unknown").lower()
    bucket = plats.setdefault(plat, {"selectors": {}, "updated_at": _utc_now()})
    sels = bucket.setdefault("selectors", {})
    updated = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        sel = str(row.get("selector") or "").strip()
        if not sel:
            continue
        ftype = str(row.get("type") or row.get("field_type") or "")
        entry = sels.setdefault(
            sel,
            {
                "type": ftype,
                "host": host,
                "success": 0,
                "fail": 0,
                "attempts": 0,
            },
        )
        if ftype:
            entry["type"] = ftype
        if host:
            entry["host"] = host
        ok = bool(row.get("verified", row.get("ok", False)))
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        if ok:
            entry["success"] = int(entry.get("success") or 0) + 1
        else:
            entry["fail"] = int(entry.get("fail") or 0) + 1
        attempts = max(int(entry["attempts"]), 1)
        entry["success_rate"] = round(int(entry["success"]) / attempts, 3)
        entry["updated_at"] = _utc_now()
        updated += 1
    bucket["updated_at"] = _utc_now()
    _save_selector_stats(data)
    return {"platform": plat, "updated": updated}


def demote_selector(platform: str, selector: str, *, host: str = "") -> bool:
    """Mark a selector as demoted after chronic verify=false."""
    if not selector:
        return False
    data = _load_selector_stats()
    plat = (platform or "unknown").lower()
    bucket = data.setdefault("platforms", {}).setdefault(
        plat, {"selectors": {}, "updated_at": _utc_now()}
    )
    entry = bucket.setdefault("selectors", {}).setdefault(
        selector,
        {"type": "", "host": host, "success": 0, "fail": 0, "attempts": 0},
    )
    entry["fail"] = int(entry.get("fail") or 0) + 1
    entry["attempts"] = int(entry.get("attempts") or 0) + 1
    attempts = max(int(entry["attempts"]), 1)
    entry["success_rate"] = round(int(entry.get("success") or 0) / attempts, 3)
    entry["demoted"] = True
    entry["demoted_at"] = _utc_now()
    if host:
        entry["host"] = host
    bucket["updated_at"] = _utc_now()
    _save_selector_stats(data)
    return True


def selector_success_rate(platform: str, selector: str) -> float | None:
    data = _load_selector_stats()
    plat = (platform or "unknown").lower()
    entry = ((data.get("platforms") or {}).get(plat) or {}).get("selectors", {}).get(
        selector
    )
    if not entry:
        return None
    if entry.get("demoted"):
        return 0.0
    if "success_rate" in entry:
        return float(entry["success_rate"])
    attempts = int(entry.get("attempts") or 0)
    if attempts <= 0:
        return None
    return int(entry.get("success") or 0) / attempts


def preferred_selectors(
    platform: str,
    *,
    host: str = "",
    field_type: str | None = None,
    min_rate: float = 0.5,
    min_attempts: int = 1,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """High-success selectors for a platform (optionally host / type filtered).

    Sorted by success_rate desc, then attempts desc. Demoted selectors excluded.
    """
    data = _load_selector_stats()
    plat = (platform or "unknown").lower()
    sels = ((data.get("platforms") or {}).get(plat) or {}).get("selectors") or {}
    rows: list[dict[str, Any]] = []
    ft_want = (field_type or "").strip().upper()
    for sel, entry in sels.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("demoted"):
            continue
        attempts = int(entry.get("attempts") or 0)
        if attempts < min_attempts:
            continue
        rate = float(entry.get("success_rate") or 0.0)
        if rate < min_rate:
            continue
        if host and entry.get("host") and entry.get("host") != host:
            # Prefer same-host but still allow platform-wide if host empty on entry
            pass
        etype = str(entry.get("type") or "")
        if ft_want and etype.upper() != ft_want:
            continue
        rows.append(
            {
                "selector": sel,
                "type": etype,
                "success_rate": rate,
                "attempts": attempts,
                "host": entry.get("host") or "",
            }
        )
    rows.sort(key=lambda r: (-r["success_rate"], -r["attempts"]))
    return rows[:limit]


def rank_replay_rows(
    rows: list[dict[str, str]],
    *,
    platform: str,
    host: str = "",
) -> list[dict[str, str]]:
    """Sort replay map rows so high-success selectors run first."""
    if not rows:
        return []

    def score(r: dict[str, str]) -> tuple[float, int]:
        sel = r.get("selector") or ""
        rate = selector_success_rate(platform, sel)
        if rate is None:
            return (0.5, 0)  # unknown → neutral middle
        # demoted / low → negative priority
        return (float(rate), 1)

    return sorted(rows, key=lambda r: score(r), reverse=True)


def type_value_replay_map(
    platform: str,
    *,
    host: str = "",
    test_mode: bool = True,
) -> dict[str, str]:
    """Map field_type → last sanitized dummy value from experience (test_mode only).

    Real mode returns {}. Callers should resolve placeholders via DUMMY_PROFILE.
    """
    if not test_mode:
        return {}
    out: dict[str, str] = {}
    for row in reversed(load_experience()):
        if str(row.get("platform") or "").lower() != (platform or "unknown").lower():
            continue
        if host and row.get("host") and row.get("host") != host:
            continue
        if not row.get("ok") and not row.get("verified"):
            continue
        ftype = str(row.get("type") or "").upper()
        val = row.get("value")
        if not ftype or not val or not isinstance(val, str):
            continue
        if val.startswith("{{") and val.endswith("}}"):
            continue  # placeholder — resolve at fill time from DUMMY_PROFILE
        if ftype not in out:
            out[ftype] = val
    return out


def resolve_dummy_value_for_shape(field_type: str, values: dict | None = None) -> str | None:
    """Map a classified type to current dummy values (never real profile)."""
    ft = (field_type or "").upper()
    if values and ft in values and values[ft]:
        return str(values[ft])
    try:
        from field_map import DUMMY_PROFILE, build_value_map

        vm = build_value_map(DUMMY_PROFILE)
        return vm.get(ft)
    except Exception as e:
        _log.debug("dummy value lookup failed for %s: %s", ft, e)
        return None


def similar_leftover_answers(
    leftovers: list[dict],
    *,
    platform: str = "",
    host: str = "",
    top_n: int = MAX_FLASH_SIMILAR,
) -> list[dict[str, Any]]:
    """Past sanitized leftover-style answers similar to current leftovers.

    Matches on normalized label tokens / field type. Values are already
    sanitized in experience (placeholders or dummy policy text).
    """
    if not leftovers or top_n <= 0:
        return []

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").lower().strip())

    want_types = {
        str(L.get("type") or L.get("automation_id") or "").upper()
        for L in leftovers
        if isinstance(L, dict)
    }
    want_labels = {
        norm(str(L.get("label") or ""))
        for L in leftovers
        if isinstance(L, dict) and L.get("label")
    }
    want_tokens: set[str] = set()
    for lab in want_labels:
        want_tokens.update(t for t in lab.split() if len(t) >= 4)

    # Semantic recall (default OFF until Phase 6 A/B promotes it). Enable with
    # FASTFILL_ANSWER_MEMORY=1 or FASTFILL_SEMANTIC_MEMORY=1. Also off when the
    # master FASTFILL_SEMANTIC_MATCH=0. When on, a past answer also matches if
    # its label is a paraphrase (semantic_sim >= threshold) of any wanted label.
    # Disabled => prior lexical behavior (type / exact / token overlap only).
    # Only widens leftover-prompt recall; never overrides Layer 0/1 fills.
    # Matches are still sanitized experience values and ranked below type/exact.
    _sem_on = (
        os.environ.get("FASTFILL_SEMANTIC_MATCH", "1") != "0"
        and (
            os.environ.get("FASTFILL_ANSWER_MEMORY", "0") == "1"
            or os.environ.get("FASTFILL_SEMANTIC_MEMORY", "0") == "1"
        )
    )
    try:
        _sem_thresh = float(os.environ.get("FASTFILL_SEMANTIC_MEMORY_THRESHOLD", "0.72") or 0.72)
    except (TypeError, ValueError):
        _sem_thresh = 0.72
    _sem_sim = None
    if _sem_on:
        try:
            from semantic_match import semantic_sim as _sem_sim
        except Exception:
            _sem_sim = None
    want_labels_list = [wl for wl in want_labels if wl]

    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    plat = (platform or "").lower()

    for row in reversed(load_experience()):
        if not row.get("ok") and not row.get("verified"):
            # Prefer successful past answers; skip pure fails
            continue
        if plat and str(row.get("platform") or "").lower() != plat:
            continue
        ftype = str(row.get("type") or "").upper()
        label = norm(str(row.get("label") or ""))
        val = row.get("value")
        if not val or not isinstance(val, str):
            continue
        if val.startswith("{{") and val.endswith("}}"):
            continue
        # Field-kind sanitizers — never inject job-board tokens into phone/country
        # or Indeed into education, etc.
        try:
            from verified_select import (
                _NON_COUNTRY_SEARCH_RE,
                is_safe_phone_country_search,
            )

            if ftype in ("PHONE_COUNTRY_CODE", "ADDRESS_COUNTRY"):
                if not is_safe_phone_country_search(val) or _NON_COUNTRY_SEARCH_RE.search(
                    val
                ):
                    continue
            elif ftype and ftype not in ("HOW_HEARD", "SOURCE") and _NON_COUNTRY_SEARCH_RE.search(
                val
            ):
                # Job-board leaf only belongs on how-heard/source
                if re.search(
                    r"\b(indeed|linkedin|glassdoor|monster|ziprecruiter)\b",
                    val,
                    re.I,
                ):
                    continue
        except Exception:
            pass
        match = False
        rank = 0.0
        if ftype and ftype in want_types:
            match, rank = True, 2.0  # type match is the strongest signal
        elif label and label in want_labels:
            match, rank = True, 1.5  # exact label match next
        elif label and want_tokens:
            toks = set(label.split())
            if len(toks & want_tokens) >= 2:
                match, rank = True, 1.0  # token overlap
        # Require type agreement when both sides typed — block cross-kind poison
        if match and ftype and want_types and ftype not in want_types:
            # Token/label-only hit with mismatched type → reject
            if rank < 2.0:
                match = False
        if _sem_sim is not None and label and want_labels_list:
            sem_score = max((_sem_sim(label, wl) for wl in want_labels_list), default=0.0)
            if not match and sem_score >= _sem_thresh:
                match, rank = True, sem_score  # paraphrase recall (< lexical ranks)
            elif match:
                rank += sem_score * 0.001  # tie-break only; never reorders tiers
        if not match:
            continue
        key = f"{ftype}|{label}|{val[:40]}"
        if key in seen:
            continue
        seen.add(key)
        hit = {
            "type": ftype or None,
            "label": row.get("label"),
            "value": val[:200],
            "value_shape": row.get("value_shape"),
            "platform": row.get("platform"),
            "via": "experience",
        }
        if _sem_sim is not None:
            hit["_score"] = rank
        hits.append(hit)
        # Lexical mode keeps the cheap early break; semantic mode collects all
        # matches first so it can rank the best paraphrases.
        if _sem_sim is None and len(hits) >= top_n:
            break

    if _sem_sim is not None and hits:
        hits.sort(key=lambda h: h.pop("_score", 0.0), reverse=True)
        hits = hits[:top_n]
    return hits


def format_similar_for_flash(similar: list[dict[str, Any]]) -> str:
    if not similar:
        return ""
    lines = [
        "PAST SIMILAR LEFTOVER ANSWERS (sanitized; reuse strategy, not verbatim PII):"
    ]
    for i, s in enumerate(similar, 1):
        lines.append(
            f"  {i}. label={s.get('label')!r} type={s.get('type')!r} "
            f"→ {s.get('value')!r}"
        )
    return "\n".join(lines)


def _load_lessons() -> dict[str, Any]:
    if not LESSONS_JSON_PATH.is_file():
        return {"version": 1, "lessons": []}
    try:
        data = json.loads(LESSONS_JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "lessons": []}
    if not isinstance(data, dict):
        return {"version": 1, "lessons": []}
    data.setdefault("lessons", [])
    return data


def _save_lessons(data: dict[str, Any]) -> None:
    _ensure_store()
    LESSONS_JSON_PATH.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lessons = data.get("lessons") or []
    lines = [
        "# Continuous learning — lessons",
        "",
        "Auto-generated from UNFILLABLE_AFTER_2 / chronic selector fails.",
        "Avoid strategies below on matching label patterns.",
        "",
    ]
    if not lessons:
        lines.append("_No lessons yet._")
    else:
        for L in lessons[-80:]:
            lines.append(
                f"- **{L.get('label_pattern') or L.get('field_type') or '?'}** "
                f"({L.get('platform') or 'any'}): avoid `{L.get('avoid_strategy')}` "
                f"— {L.get('reason') or ''} "
                f"(seen={L.get('seen', 1)}, updated={L.get('updated_at')})"
            )
    LESSONS_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def record_lesson(
    *,
    label: str | None = None,
    field_type: str | None = None,
    platform: str = "",
    avoid_strategy: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Persist a short lesson: label pattern → strategy to avoid."""
    data = _load_lessons()
    lab = re.sub(r"\s+", " ", (label or "").strip().lower())[:80]
    pattern = lab or (field_type or "").upper() or "unknown"
    avoid = (avoid_strategy or "generic_retry").strip()[:120]
    lessons: list = data.setdefault("lessons", [])
    for L in lessons:
        if (
            L.get("label_pattern") == pattern
            and L.get("avoid_strategy") == avoid
            and str(L.get("platform") or "") == str(platform or "")
        ):
            L["seen"] = int(L.get("seen") or 0) + 1
            L["updated_at"] = _utc_now()
            if reason:
                L["reason"] = reason[:160]
            _save_lessons(data)
            return L
    entry = {
        "label_pattern": pattern,
        "field_type": (field_type or "").upper() or None,
        "platform": platform or "",
        "avoid_strategy": avoid,
        "reason": (reason or "")[:160],
        "seen": 1,
        "updated_at": _utc_now(),
    }
    lessons.append(entry)
    _save_lessons(data)
    return entry


def lessons_for_platform(platform: str, *, limit: int = 20) -> list[dict[str, Any]]:
    plat = (platform or "").lower()
    lessons = _load_lessons().get("lessons") or []
    out = [
        L
        for L in lessons
        if not plat or not L.get("platform") or str(L.get("platform")).lower() == plat
    ]
    return out[-limit:]


def learn_from_report(report: dict[str, Any]) -> dict[str, Any]:
    """Main finalize hook: append experience, update stats, demote losers, lessons.

    Safe to call on SUCCESS / PARTIAL / FAIL — only verified fills boost selectors;
    verified=false / demoted rows demote and may write lessons when fail≥2.
    """
    if not isinstance(report, dict):
        return {"ok": False, "error": "not a dict"}

    test_mode = bool(report.get("test_mode", report.get("dummy", True)))
    # Real mode flag: dummy=False or test_mode=False
    if report.get("dummy") is False:
        test_mode = False

    url = str(report.get("url") or "")
    platform = str(report.get("platform") or "unknown")
    host = _normalize_host(url)

    filled = [f for f in (report.get("filled") or []) if isinstance(f, dict)]
    leftovers = [L for L in (report.get("leftovers") or []) if isinstance(L, dict)]
    demoted = [
        d for d in (report.get("demoted_false_verified") or []) if isinstance(d, dict)
    ]

    experience_rows: list[dict[str, Any]] = []
    already_fps = report.get("_cl_experience_fps") or set()
    for f in filled:
        fp = (
            platform,
            host,
            str(f.get("selector") or ""),
            str(f.get("type") or f.get("automation_id") or ""),
        )
        row = {
            "platform": platform,
            "host": host,
            "selector": f.get("selector"),
            "type": f.get("type") or f.get("automation_id"),
            "label": f.get("label"),
            "value": f.get("value") or f.get("readback") or f.get("verified_value"),
            "verified": f.get("verified", f.get("ok")),
            "ok": f.get("ok", f.get("verified")),
            "via": f.get("via") or f.get("layer"),
        }
        # Always feed selector_stats; skip experience.jsonl if pack already wrote it
        experience_rows.append(row)
        if fp in already_fps:
            row["_skip_experience_append"] = True
    for d in demoted:
        experience_rows.append(
            {
                "platform": platform,
                "host": host,
                "selector": d.get("selector"),
                "type": d.get("type"),
                "label": d.get("label"),
                "value": None,
                "verified": False,
                "ok": False,
                "via": d.get("via") or "demote",
            }
        )

    n_exp = append_experience(
        [r for r in experience_rows if not r.get("_skip_experience_append")],
        test_mode=test_mode,
    )
    # Strip internal flag before stats
    for r in experience_rows:
        r.pop("_skip_experience_append", None)
    stats = update_selector_stats(experience_rows, platform=platform, host=host)

    demoted_n = 0
    for row in experience_rows:
        if row.get("ok") or row.get("verified"):
            continue
        sel = str(row.get("selector") or "").strip()
        if not sel:
            continue
        rate = selector_success_rate(platform, sel)
        # After update, check chronic failure
        data = _load_selector_stats()
        entry = (
            ((data.get("platforms") or {}).get(platform.lower()) or {})
            .get("selectors", {})
            .get(sel)
            or {}
        )
        fails = int(entry.get("fail") or 0)
        attempts = int(entry.get("attempts") or 0)
        if attempts >= MIN_SELECTOR_ATTEMPTS_FOR_DEMOTION and (
            float(entry.get("success_rate") or 1.0) < DEMOTION_SUCCESS_RATE
            or fails >= 2
        ):
            if demote_selector(platform, sel, host=host):
                demoted_n += 1
                # Also drop from replay cache when chronic
                try:
                    from record_replay import invalidate

                    if url:
                        invalidate(url, platform, sel)
                except Exception as e:
                    _log.warning(
                        "replay invalidate failed plat=%s sel=%s: %s",
                        platform,
                        (sel or "")[:80],
                        e,
                    )

    # Lessons from field_attempt_log unfillable keys / leftovers with ≥2 fail signal
    lessons_n = 0
    fal = report.get("field_attempt_log") or {}
    unfillable_keys = fal.get("unfillable_keys") or []
    for key in unfillable_keys:
        # key shape: TYPE|lab:... or TYPE|sel:...
        parts = str(key).split("|", 1)
        ftype = parts[0] if parts else ""
        label = ""
        if len(parts) > 1 and parts[1].startswith("lab:"):
            label = parts[1][4:]
        record_lesson(
            label=label or None,
            field_type=ftype,
            platform=platform,
            avoid_strategy="retry_same_selector",
            reason="UNFILLABLE_AFTER_2",
        )
        lessons_n += 1

    # Also lesson from leftover flash_candidates that look chronically stuck
    for L in leftovers:
        if L.get("reason") in (
            "unverified_readback",
            "live_empty_after_claimed_verified",
            "verify_miss_empty_readback",
        ):
            sel = str(L.get("selector") or "")
            if sel:
                rate = selector_success_rate(platform, sel)
                if rate is not None and rate < DEMOTION_SUCCESS_RATE:
                    record_lesson(
                        label=str(L.get("label") or ""),
                        field_type=str(L.get("type") or ""),
                        platform=platform,
                        avoid_strategy="low_success_selector",
                        reason=str(L.get("reason") or "")[:80],
                    )
                    lessons_n += 1

    # Feed successful policy fills into learning.py allow-list (dummy / policy only)
    policy_saved = 0
    policy_err: str | None = None
    if test_mode:
        try:
            from learning import record_learning

            for f in filled:
                if not (f.get("ok") or f.get("verified")):
                    continue
                label = f.get("label")
                val = f.get("value") or f.get("readback")
                if label and val and isinstance(val, str):
                    # record_learning already filters to reusable policy facts
                    if record_learning(str(label), str(val), platform):
                        policy_saved += 1
        except Exception as e:
            policy_err = str(e)[:160]

    # Sync high-success selectors into replay cache when we have verified fills
    replay_n = 0
    replay_err: str | None = None
    try:
        from record_replay import record_successful_fills

        replay_n = record_successful_fills(url, platform, filled)
    except Exception as e:
        replay_err = str(e)[:160]

    errors: list[str] = []
    if policy_err:
        errors.append(f"policy:{policy_err}")
    if replay_err:
        errors.append(f"replay:{replay_err}")
    summary = {
        "ok": not errors,
        "experience_appended": n_exp,
        "selector_stats": stats,
        "selectors_demoted": demoted_n,
        "lessons_recorded": lessons_n,
        "policy_learnings": policy_saved,
        "replay_recorded": replay_n,
        "test_mode": test_mode,
        "store": str(STORE_DIR),
    }
    if errors:
        summary["errors"] = errors
    report["continuous_learning"] = summary
    return summary


def sanitize_store(*, write: bool = True) -> dict[str, Any]:
    """Re-scrub experience.jsonl — drop cleartext emails/phones/passwords."""
    rows = load_experience()
    cleaned: list[dict[str, Any]] = []
    dropped = 0
    rewritten = 0
    for row in rows:
        tm = bool(row.get("test_mode", True))
        scrubbed = _scrub_experience_row(
            {
                **row,
                "value": row.get("value"),
                "type": row.get("type"),
            },
            test_mode=tm,
        )
        # Detect if original had raw PII
        raw = str(row.get("value") or "")
        if raw and (
            _EMAIL_RE.search(raw)
            or _PHONE_RE.search(raw)
            or _SSN_RE.search(raw)
        ):
            if scrubbed.get("value") != raw:
                rewritten += 1
        # Drop rows that somehow still have email-like values
        val = scrubbed.get("value")
        if isinstance(val, str) and _EMAIL_RE.search(val) and "{{" not in val:
            dropped += 1
            scrubbed.pop("value", None)
        cleaned.append(scrubbed)

    if write:
        _ensure_store()
        with EXPERIENCE_PATH.open("w", encoding="utf-8") as fh:
            for row in cleaned:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {
        "rows": len(cleaned),
        "rewritten_pii": rewritten,
        "dropped_values": dropped,
        "path": str(EXPERIENCE_PATH),
    }


def stats_summary() -> dict[str, Any]:
    data = _load_selector_stats()
    plats = data.get("platforms") or {}
    summary = []
    for plat, bucket in sorted(plats.items()):
        sels = bucket.get("selectors") or {}
        demoted = sum(1 for e in sels.values() if isinstance(e, dict) and e.get("demoted"))
        summary.append(
            {
                "platform": plat,
                "selectors": len(sels),
                "demoted": demoted,
                "updated_at": bucket.get("updated_at"),
            }
        )
    return {
        "store": str(STORE_DIR),
        "experience_lines": len(load_experience()),
        "platforms": summary,
        "lessons": len(_load_lessons().get("lessons") or []),
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def self_test() -> None:
    import tempfile

    global STORE_DIR, EXPERIENCE_PATH, SELECTOR_STATS_PATH, LESSONS_JSON_PATH, LESSONS_MD_PATH

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # Redirect store into temp
        old = (
            STORE_DIR,
            EXPERIENCE_PATH,
            SELECTOR_STATS_PATH,
            LESSONS_JSON_PATH,
            LESSONS_MD_PATH,
        )
        STORE_DIR = td_path
        EXPERIENCE_PATH = td_path / "experience.jsonl"
        SELECTOR_STATS_PATH = td_path / "selector_stats.json"
        LESSONS_JSON_PATH = td_path / "lessons.json"
        LESSONS_MD_PATH = td_path / "lessons.md"
        try:
            # 1) sanitize strips PII
            assert sanitize_value("alice@example.com", field_type="EMAIL", test_mode=True) == PLACEHOLDER_EMAIL
            assert sanitize_value("405-555-0199", field_type="PHONE", test_mode=True) == PLACEHOLDER_PHONE
            assert sanitize_value("secret", field_type="EMAIL", test_mode=False) is None
            assert "alice" not in (
                sanitize_value("alice@example.com", test_mode=True) or ""
            )

            # 2) experience append
            n = append_experience(
                [
                    {
                        "platform": "greenhouse",
                        "host": "boards.greenhouse.io",
                        "selector": "input#email",
                        "type": "EMAIL",
                        "label": "Email",
                        "value": "randommail6969+abc@gmail.com",
                        "verified": True,
                        "ok": True,
                        "via": "pack",
                    },
                    {
                        "platform": "greenhouse",
                        "host": "boards.greenhouse.io",
                        "selector": "input#first_name",
                        "type": "NAME_FIRST",
                        "label": "First Name",
                        "value": "Test",
                        "verified": True,
                        "ok": True,
                        "via": "pack",
                    },
                    {
                        "platform": "greenhouse",
                        "host": "boards.greenhouse.io",
                        "selector": "select#hear",
                        "type": "HOW_HEARD",
                        "label": "How did you hear about this job?",
                        "value": "Internet job board",
                        "verified": True,
                        "ok": True,
                        "via": "learned",
                    },
                ],
                test_mode=True,
            )
            assert n == 3
            rows = load_experience()
            assert len(rows) == 3
            email_row = next(r for r in rows if r.get("type") == "EMAIL")
            assert email_row.get("value") == PLACEHOLDER_EMAIL
            assert email_row.get("value_shape") == "email"
            how = next(r for r in rows if "hear" in (r.get("label") or "").lower())
            assert how.get("value") == "Internet job board"

            # Real mode — no values
            n2 = append_experience(
                [
                    {
                        "platform": "lever",
                        "host": "jobs.lever.co",
                        "selector": "input[name=email]",
                        "type": "EMAIL",
                        "value": "real.person@company.com",
                        "verified": True,
                        "ok": True,
                    }
                ],
                test_mode=False,
            )
            assert n2 == 1
            real_row = load_experience()[-1]
            assert "value" not in real_row or real_row.get("value") is None
            assert real_row.get("value_shape") == "email"

            # 3) selector stats + prefer high-success
            update_selector_stats(
                [
                    {
                        "selector": "input#good",
                        "type": "EMAIL",
                        "verified": True,
                        "ok": True,
                    },
                    {
                        "selector": "input#good",
                        "type": "EMAIL",
                        "verified": True,
                        "ok": True,
                    },
                    {
                        "selector": "input#bad",
                        "type": "EMAIL",
                        "verified": False,
                        "ok": False,
                    },
                    {
                        "selector": "input#bad",
                        "type": "EMAIL",
                        "verified": False,
                        "ok": False,
                    },
                ],
                platform="greenhouse",
                host="boards.greenhouse.io",
            )
            demote_selector("greenhouse", "input#bad", host="boards.greenhouse.io")
            pref = preferred_selectors("greenhouse", min_rate=0.5, min_attempts=1)
            assert any(p["selector"] == "input#good" for p in pref)
            assert not any(p["selector"] == "input#bad" for p in pref)

            ranked = rank_replay_rows(
                [
                    {"selector": "input#bad", "type": "EMAIL"},
                    {"selector": "input#good", "type": "EMAIL"},
                    {"selector": "input#unknown", "type": "PHONE"},
                ],
                platform="greenhouse",
            )
            assert ranked[0]["selector"] == "input#good"

            # 4) similar leftovers + lessons
            sim = similar_leftover_answers(
                [{"type": "HOW_HEARD", "label": "How did you hear about this job?"}],
                platform="greenhouse",
                top_n=3,
            )
            assert sim and "Internet" in str(sim[0].get("value"))

            record_lesson(
                label="School*",
                field_type="SCHOOL",
                platform="greenhouse",
                avoid_strategy="retry_same_selector",
                reason="UNFILLABLE_AFTER_2",
            )
            assert LESSONS_JSON_PATH.is_file()
            assert LESSONS_MD_PATH.is_file()
            md = LESSONS_MD_PATH.read_text()
            assert "school" in md.lower()
            assert "retry_same_selector" in md
            # 5) learn_from_report integration
            report = {
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "platform": "greenhouse",
                "test_mode": True,
                "dummy": True,
                "filled": [
                    {
                        "selector": "input#email",
                        "type": "EMAIL",
                        "label": "Email",
                        "value": "randommail6969+xyz@gmail.com",
                        "ok": True,
                        "verified": True,
                        "via": "pack",
                    }
                ],
                "leftovers": [],
                "field_attempt_log": {
                    "unfillable_keys": ["SCHOOL|lab:school*"],
                },
            }
            try:
                import record_replay as rr

                _orig_rsf = rr.record_successful_fills
                rr.record_successful_fills = lambda *a, **k: 1
            except Exception:
                _orig_rsf = None
            try:
                summary = learn_from_report(report)
            finally:
                if _orig_rsf is not None:
                    rr.record_successful_fills = _orig_rsf
            assert summary.get("experience_appended", 0) >= 1
            assert report.get("continuous_learning", {}).get("ok") is True
            assert summary.get("lessons_recorded", 0) >= 1

            # sanitize store
            # Inject a dirty line then sanitize
            with EXPERIENCE_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "platform": "x",
                            "type": "EMAIL",
                            "selector": "input",
                            "value": "leak@evil.com",
                            "test_mode": True,
                            "verified": True,
                            "ok": True,
                        }
                    )
                    + "\n"
                )
            result = sanitize_store(write=True)
            assert result["rows"] >= 1
            blob = EXPERIENCE_PATH.read_text()
            assert "leak@evil.com" not in blob

            print("continuous_learn self_test OK")
        finally:
            (
                STORE_DIR,
                EXPERIENCE_PATH,
                SELECTOR_STATS_PATH,
                LESSONS_JSON_PATH,
                LESSONS_MD_PATH,
            ) = old


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Continuous learning for fastfill")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--sanitize", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
    elif args.sanitize:
        print(json.dumps(sanitize_store(write=True), indent=2))
    elif args.stats:
        print(json.dumps(stats_summary(), indent=2))
    else:
        ap.print_help()
        raise SystemExit(0)
