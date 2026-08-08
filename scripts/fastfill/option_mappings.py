"""Learned option aliases (auto-apply style) — persist successful select picks.

Key: platform + host + field_type|label → chosen option text.
Dummy/test runs only store non-PII option strings (never emails/passwords).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATH = Path(
    os.environ.get("FASTFILL_OPTION_MAPPINGS")
    or (_ROOT / "option_mappings.json")
)

_EMAILISH = re.compile(r"@|password|pswd", re.I)


def _mapping_chosen_ok(canonical: str, chosen: str) -> bool:
    """Reject confusable / dial-poisoned chosen options (ATS2-005)."""
    can = (canonical or "").strip()
    ch = (chosen or "").strip()
    if not ch:
        return False
    if _EMAILISH.search(ch):
        return False
    try:
        from verified_select import reject_confusable_state_option, soft_value_match

        if can and reject_confusable_state_option(can, ch):
            return False
        # Dial code chosen for a non-dial canonical (state/how-heard/etc.)
        try:
            from gh_select import looks_like_dial_code_option

            if looks_like_dial_code_option(ch) and can and not looks_like_dial_code_option(
                can
            ):
                return False
        except Exception:
            pass
        if can and len(can) >= 2 and not soft_value_match(can, ch):
            # Allow synonym chips (Indeed vs Internet) for how-heard — only
            # block when clearly confusable already handled; soft fail soft.
            # Still block when chosen is a US state abbrev mismatch.
            if reject_confusable_state_option(can, ch):
                return False
    except Exception:
        pass
    return True


def mappings_path() -> Path:
    return _DEFAULT_PATH


def _norm_label(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip().lower())[:120]


def mapping_key(
    *,
    platform: str,
    host: str,
    field_type: str = "",
    label: str = "",
) -> str:
    plat = (platform or "unknown").strip().lower() or "unknown"
    h = (host or "").strip().lower().split(":")[0]
    ft = (field_type or "").strip().upper()
    lab = _norm_label(label)
    tail = ft or lab or "unknown"
    return f"{plat}|{h}|{tail}"


def load_mappings(path: Path | None = None) -> dict[str, Any]:
    p = path or mappings_path()
    try:
        if not p.is_file():
            return {"version": 1, "entries": {}}
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "entries": {}}
        data.setdefault("version", 1)
        data.setdefault("entries", {})
        if not isinstance(data["entries"], dict):
            data["entries"] = {}
        return data
    except Exception:
        return {"version": 1, "entries": {}}


def save_mappings(data: dict[str, Any], path: Path | None = None) -> None:
    p = path or mappings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def lookup_aliases(
    *,
    platform: str,
    host: str,
    field_type: str = "",
    label: str = "",
    canonical: str = "",
    path: Path | None = None,
) -> list[str]:
    """Return learned option strings to try before/with static aliases."""
    data = load_mappings(path)
    key = mapping_key(
        platform=platform, host=host, field_type=field_type, label=label
    )
    entry = data["entries"].get(key) or {}
    out: list[str] = []
    chosen = str(entry.get("chosen_option") or "").strip()
    can = (canonical or "").strip()
    # ATS2-005: do not prefer a poisoned chosen_option ahead of canonical
    if chosen and _mapping_chosen_ok(can or str(entry.get("canonical") or ""), chosen):
        out.append(chosen)
    for a in entry.get("aliases") or []:
        s = str(a or "").strip()
        if (
            s
            and s not in out
            and not _EMAILISH.search(s)
            and _mapping_chosen_ok(can or chosen, s)
        ):
            out.append(s)
    if can and can not in out:
        out.insert(0, can)
    # Also try canonical-keyed entry
    if field_type and canonical:
        k2 = mapping_key(
            platform=platform,
            host=host,
            field_type=field_type,
            label=canonical,
        )
        e2 = data["entries"].get(k2) or {}
        c2 = str(e2.get("chosen_option") or "").strip()
        if (
            c2
            and c2 not in out
            and not _EMAILISH.search(c2)
            and _mapping_chosen_ok(can, c2)
        ):
            out.append(c2)
    return out


def upsert_mapping(
    *,
    platform: str,
    host: str,
    field_type: str = "",
    label: str = "",
    canonical: str,
    chosen_option: str,
    path: Path | None = None,
) -> dict[str, Any]:
    """Record a successful verified select. Skips email/password-looking values."""
    chosen = (chosen_option or "").strip()
    can = (canonical or "").strip()
    if not chosen or _EMAILISH.search(chosen) or _EMAILISH.search(can):
        return {}
    # ATS2-005: refuse to persist confusable / dial-poisoned mappings
    if not _mapping_chosen_ok(can, chosen):
        return {}
    data = load_mappings(path)
    key = mapping_key(
        platform=platform, host=host, field_type=field_type, label=label
    )
    prev = data["entries"].get(key) or {}
    aliases = list(prev.get("aliases") or [])
    if can and can not in aliases and can != chosen:
        aliases.append(can)
    if prev.get("chosen_option") and prev["chosen_option"] != chosen:
        old = str(prev["chosen_option"])
        if old not in aliases and _mapping_chosen_ok(can, old):
            aliases.append(old)
    hits = int(prev.get("hits") or 0) + 1
    data["entries"][key] = {
        "canonical": can or prev.get("canonical") or "",
        "chosen_option": chosen,
        "aliases": aliases[:12],
        "hits": hits,
        "field_type": (field_type or "").upper(),
        "label": _norm_label(label),
    }
    save_mappings(data, path)
    return data["entries"][key]
