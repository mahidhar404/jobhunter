#!/usr/bin/env python3
"""Record-once / replay selector maps for repeated ATS tenants.

After a successful verified page fill, persist selector → field_type keyed by
``platform + path fingerprint``. Next run prefers replay before classify/Flash.
Invalidate an entry (or single selector) when verify misses.

Storage: ``scripts/fastfill/replay_cache.json`` — selector→type only, never values.

CLI::

    skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py --list
    skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py --clear
    skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py --sanitize
    skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py \\
        --record-from skyvern_runtime/real_job_results/fast_fill_gh_universal_smoke.json
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
CACHE_PATH = HERE / "replay_cache.json"

# Bump when fingerprint normalization changes; load migrates via url_sample.
CACHE_VERSION = 2

# Keys that must never appear in persisted map rows (PII / secrets / answers).
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "value",
        "readback",
        "picked",
        "shown",
        "email",
        "password",
        "phone",
        "address",
        "label",
        "text",
        "answer",
        "profile",
    }
)

# Keys that must never appear in playbook cache rows.
_FORBIDDEN_PLAYBOOK_KEYS = _FORBIDDEN_ROW_KEYS | frozenset(
    {"value", "readback", "picked", "shown", "answer", "profile"}
)

_JOB_ID_RE = re.compile(
    r"^(?:"
    r"\d{4,}"  # greenhouse / icims numeric ids
    r"|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"  # uuid
    r"|[0-9a-f]{16,}"  # long hex
    r"|[A-Za-z0-9_-]*\d[A-Za-z0-9_-]{5,}"  # mixed token with digit (applytojob, WD job)
    r")$",
    re.I,
)

# Ashby custom questions use opaque UUID name= — these verify-miss after resume
# parse remounts. Prefer label / name*=linkedin style selectors instead.
_UUID_NAME_SEL_RE = re.compile(
    r"""name\s*=\s*["']?[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}["']?""",
    re.I,
)
_URL_FIELD_TYPES = frozenset({"LINKEDIN", "GITHUB", "PORTFOLIO"})
_PREFERRED_URL_SELECTORS: dict[str, str] = {
    "LINKEDIN": (
        ".ashby-application-form-field-entry:has(label:has-text('LinkedIn')) "
        "input[type=text], "
        ".ashby-application-form-field-entry:has(label:has-text('LinkedIn')) "
        "input[type=url], "
        "[class*=\"_fieldEntry_\"]:has(label:has-text('LinkedIn')) input, "
        "input[name*='linkedin' i], input[placeholder*='LinkedIn' i]"
    ),
    "GITHUB": (
        ".ashby-application-form-field-entry:has(label:has-text('GitHub')) "
        "input[type=text], "
        "input[name*='github' i], input[placeholder*='GitHub' i]"
    ),
    "PORTFOLIO": (
        ".ashby-application-form-field-entry:has(label:has-text('Portfolio')) "
        "input[type=text], "
        ".ashby-application-form-field-entry:has(label:has-text('Website')) "
        "input:not([type=hidden]), "
        "input[placeholder*='Portfolio' i], input[placeholder*='Website' i]"
    ),
}


def _is_uuid_only_selector(selector: str) -> bool:
    """True when selector is essentially input[name=<uuid>] (Ashby opaque id)."""
    sel = (selector or "").strip()
    if not sel:
        return False
    if not _UUID_NAME_SEL_RE.search(sel):
        return False
    # Allow if it also has a semantic hint
    low = sel.lower()
    if any(
        tok in low
        for tok in (
            "linkedin",
            "github",
            "portfolio",
            "website",
            "has-text",
            "label:",
            "placeholder",
        )
    ):
        return False
    return True


def _rewrite_url_selector(ftype: str, selector: str) -> str | None:
    """Replace UUID-only URL selectors with label-based equivalents; else None=drop."""
    ft = (ftype or "").upper()
    if ft not in _URL_FIELD_TYPES:
        return selector
    if _is_uuid_only_selector(selector):
        return _PREFERRED_URL_SELECTORS.get(ft)
    return selector


def _normalize_host(host: str) -> str:
    h = (host or "").lower().strip()
    if h.startswith("www."):
        h = h[4:]
    return h


def _looks_like_job_id(seg: str) -> bool:
    s = (seg or "").strip()
    if not s or s in {".", ".."}:
        return False
    return bool(_JOB_ID_RE.fullmatch(s))


def _normalize_path(path: str, platform: str) -> str:
    """Collapse job-specific path segments so the same tenant shares a key."""
    raw = (path or "/").rstrip("/") or "/"
    parts = [p for p in raw.split("/") if p]
    plat = (platform or "unknown").lower().strip() or "unknown"

    if plat == "greenhouse":
        # /board/jobs/<id> → /board/jobs
        if len(parts) >= 2 and parts[1] == "jobs":
            return "/" + "/".join(parts[:2])
        if parts and parts[0] == "jobs":
            return "/jobs"
    elif plat == "lever":
        # /company/<posting> → /company
        if parts:
            return "/" + parts[0]
    elif plat == "ashby":
        if parts:
            return "/" + parts[0]
    elif plat == "applytojob":
        if parts and parts[0].lower() == "apply":
            return "/apply"
    elif plat == "workday":
        # /SiteName/job/... → /SiteName
        if parts:
            return "/" + parts[0]
    elif plat == "icims":
        if parts and parts[0].lower() == "jobs":
            return "/jobs"
    elif plat in ("bamboohr", "workable", "smartrecruiters", "recruitee", "jobvite"):
        out: list[str] = []
        for p in parts:
            out.append("*" if _looks_like_job_id(p) else p)
        return "/" + "/".join(out) if out else "/"

    # Generic / unknown: drop trailing job-id-ish segments; star mid-path ids.
    out = []
    for i, p in enumerate(parts):
        if _looks_like_job_id(p):
            if i == len(parts) - 1:
                continue
            out.append("*")
        else:
            out.append(p)
    return "/" + "/".join(out) if out else "/"


def page_fingerprint(url: str, platform: str = "") -> str:
    """Stable tenant key: platform + host + normalized path (no query/fragment)."""
    parsed = urlparse(url or "")
    plat = (platform or "unknown").lower().strip() or "unknown"
    host = _normalize_host(parsed.netloc or "")
    path = _normalize_path(parsed.path or "/", plat)
    raw = f"{plat}|{host}|{path}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def fingerprint_debug(url: str, platform: str = "") -> dict[str, str]:
    """Explain the fingerprint inputs (for CLI / tests)."""
    parsed = urlparse(url or "")
    plat = (platform or "unknown").lower().strip() or "unknown"
    host = _normalize_host(parsed.netloc or "")
    path = _normalize_path(parsed.path or "/", plat)
    raw = f"{plat}|{host}|{path}"
    return {
        "platform": plat,
        "host": host,
        "path": path,
        "raw": raw,
        "fingerprint": hashlib.sha256(raw.encode()).hexdigest()[:24],
    }


def _scrub_row(row: Any) -> dict[str, str] | None:
    """Keep only selector+type; drop any PII-bearing keys.

    UUID-only LINKEDIN/GITHUB/PORTFOLIO selectors are rewritten to label-based
    equivalents (or dropped) — they verify-miss after Ashby resume parse.
    """
    if not isinstance(row, dict):
        return None
    sel = str(row.get("selector") or "").strip()
    ftype = str(row.get("type") or row.get("automation_id") or "").strip()
    if not sel or not ftype:
        return None
    if ftype.upper() in {"RESUME_UPLOAD", "FILE", "ATTACHMENT"}:
        return None
    rewritten = _rewrite_url_selector(ftype, sel)
    if not rewritten:
        return None
    return {"selector": rewritten, "type": ftype}


def _scrub_playbook_row(row: Any, *, field_type: str = "") -> dict[str, Any] | None:
    """Keep only allowlisted playbook stats; drop PII-bearing keys."""
    if not isinstance(row, dict):
        return None
    pb = str(row.get("playbook") or row.get("playbook_id") or "").strip()
    if not pb:
        return None
    try:
        from playbooks import is_allowed_playbook
    except ImportError:
        return None
    if not is_allowed_playbook(pb):
        return None
    ft = str(field_type or row.get("field_type") or "").strip()
    if not ft:
        return None
    sel = str(row.get("selector") or "").strip()
    success = int(row.get("success") or 0)
    fail = int(row.get("fail") or 0)
    updated = str(
        row.get("updated")
        or row.get("updated_at")
        or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    return {
        "playbook": pb,
        "selector": sel,
        "success": max(0, success),
        "fail": max(0, fail),
        "updated": updated,
    }


def _scrub_playbooks(raw: Any) -> dict[str, dict[str, Any]]:
    """Normalize playbooks dict keyed by field_type."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for ft, row in raw.items():
        ftype = str(ft or "").strip()
        if not ftype:
            continue
        clean = _scrub_playbook_row(row, field_type=ftype)
        if clean:
            out[ftype] = clean
    return out


def _scrub_entry(entry: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for r in entry.get("map") or []:
        clean = _scrub_row(r)
        if not clean or clean["selector"] in seen:
            continue
        seen.add(clean["selector"])
        rows.append(clean)
    playbooks = _scrub_playbooks(entry.get("playbooks"))
    out = {
        "platform": str(entry.get("platform") or "unknown"),
        "url_sample": str(entry.get("url_sample") or "")[:180],
        "updated_at": str(
            entry.get("updated_at")
            or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ),
        "map": rows,
    }
    if playbooks:
        out["playbooks"] = playbooks
    return out


def _merge_maps(
    a: list[dict[str, str]], b: list[dict[str, str]]
) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for rows in (a, b):
        for r in rows:
            sel = r.get("selector")
            if not sel or sel in seen:
                continue
            seen.add(sel)
            out.append({"selector": sel, "type": r["type"]})
    return out


def sanitize_cache(*, write: bool = True) -> dict[str, Any]:
    """Rekey to current fingerprint rules and scrub PII keys from map rows.

    Also rewrites UUID-only URL field selectors to label-based equivalents.
    """
    data = _load(migrate=False)
    old_entries = dict(data.get("entries") or {})
    new_entries: dict[str, Any] = {}
    dropped_pii = 0
    rekeyed = 0
    rewritten_uuid_urls = 0

    for old_key, entry in old_entries.items():
        if not isinstance(entry, dict):
            continue
        before_rows = entry.get("map") or []
        for r in before_rows:
            if not isinstance(r, dict):
                continue
            sel = str(r.get("selector") or "")
            ft = str(r.get("type") or "").upper()
            if ft in _URL_FIELD_TYPES and _is_uuid_only_selector(sel):
                rewritten_uuid_urls += 1
        scrubbed = _scrub_entry(entry)
        for r in before_rows:
            if isinstance(r, dict) and any(k in r for k in _FORBIDDEN_ROW_KEYS):
                dropped_pii += 1
                break
        url = scrubbed.get("url_sample") or ""
        plat = scrubbed.get("platform") or "unknown"
        new_key = page_fingerprint(url, plat) if url else old_key
        if new_key != old_key:
            rekeyed += 1
        if new_key in new_entries:
            prev = new_entries[new_key]
            scrubbed["map"] = _merge_maps(prev.get("map") or [], scrubbed["map"])
            # Prefer newer updated_at / longer url_sample
            if (scrubbed.get("updated_at") or "") < (prev.get("updated_at") or ""):
                scrubbed["updated_at"] = prev["updated_at"]
            if len(prev.get("url_sample") or "") > len(scrubbed.get("url_sample") or ""):
                scrubbed["url_sample"] = prev["url_sample"]
        if not scrubbed["map"] and not scrubbed.get("playbooks"):
            continue
        if new_key in new_entries and scrubbed.get("playbooks"):
            prev_pbs = new_entries[new_key].get("playbooks") or {}
            merged_pbs = dict(prev_pbs)
            for ft, row in (scrubbed.get("playbooks") or {}).items():
                if ft not in merged_pbs:
                    merged_pbs[ft] = row
                else:
                    prev = merged_pbs[ft]
                    merged_pbs[ft] = {
                        **row,
                        "success": max(int(prev.get("success") or 0), int(row.get("success") or 0)),
                        "fail": max(int(prev.get("fail") or 0), int(row.get("fail") or 0)),
                    }
            scrubbed["playbooks"] = merged_pbs
        new_entries[new_key] = scrubbed

    result = {
        "version": CACHE_VERSION,
        "entries": new_entries,
        "rekeyed": rekeyed,
        "dropped_pii_entries": dropped_pii,
        "rewritten_uuid_url_selectors": rewritten_uuid_urls,
        "before": len(old_entries),
        "after": len(new_entries),
    }
    if write:
        _save({"version": CACHE_VERSION, "entries": new_entries})
    return result


def _load(*, migrate: bool = True) -> dict[str, Any]:
    if not CACHE_PATH.is_file():
        return {"version": CACHE_VERSION, "entries": {}}
    try:
        data = json.loads(CACHE_PATH.read_text())
    except Exception:
        return {"version": CACHE_VERSION, "entries": {}}
    if not isinstance(data, dict):
        return {"version": CACHE_VERSION, "entries": {}}
    data.setdefault("entries", {})
    if not isinstance(data["entries"], dict):
        data["entries"] = {}
    ver = int(data.get("version") or 1)
    if migrate and ver < CACHE_VERSION:
        sanitize_cache(write=True)
        return _load(migrate=False)
    data["version"] = CACHE_VERSION
    return data


def _save(data: dict[str, Any]) -> None:
    payload = {
        "version": int(data.get("version") or CACHE_VERSION),
        "entries": data.get("entries") or {},
    }
    # Final hygiene pass before disk write
    clean_entries: dict[str, Any] = {}
    for k, entry in payload["entries"].items():
        if not isinstance(entry, dict):
            continue
        scrubbed = _scrub_entry(entry)
        if scrubbed["map"] or scrubbed.get("playbooks"):
            clean_entries[k] = scrubbed
    payload["entries"] = clean_entries
    CACHE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def lookup_replay(url: str, platform: str) -> list[dict[str, str]]:
    """Return list of {selector, type} for this fingerprint, or [].

    When continuous_learn selector stats exist, high-success selectors are
    ordered first so Layer replay prefers proven DOM paths. Also merges in
    platform-level preferred selectors not yet in this tenant's map.
    """
    key = page_fingerprint(url, platform)
    entry = (_load().get("entries") or {}).get(key) or {}
    rows = entry.get("map") or []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for r in rows:
        clean = _scrub_row(r)
        if clean and clean["selector"] not in seen:
            seen.add(clean["selector"])
            out.append(clean)
    # Merge high-success platform selectors learned across hosts
    try:
        from continuous_learn import preferred_selectors, rank_replay_rows
        from urllib.parse import urlparse

        host = (urlparse(url or "").netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        for pref in preferred_selectors(platform, host=host, min_rate=0.6, min_attempts=1, limit=15):
            sel = pref.get("selector") or ""
            ftype = pref.get("type") or ""
            if not sel or sel in seen or not ftype:
                continue
            clean = _scrub_row({"selector": sel, "type": ftype})
            if clean and clean["selector"] not in seen:
                seen.add(clean["selector"])
                out.append(clean)
        out = rank_replay_rows(out, platform=platform, host=host)
    except Exception:
        pass
    return out


def record_successful_fills(
    url: str,
    platform: str,
    filled: list[dict],
    *,
    max_rows: int = 40,
) -> int:
    """Persist verified fills as selector→type (never store values / PII)."""
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for f in filled or []:
        if not isinstance(f, dict):
            continue
        if f.get("ok") is False or f.get("verified") is False:
            continue
        # Skip deferred widget hints and file uploads (handled elsewhere)
        if f.get("reason") == "widget_deferred":
            continue
        if (f.get("type") or "").upper() in {"RESUME_UPLOAD", "FILE", "ATTACHMENT"}:
            continue
        clean = _scrub_row(f)
        if not clean or clean["selector"] in seen:
            continue
        seen.add(clean["selector"])
        rows.append(clean)
        if len(rows) >= max_rows:
            break
    if not rows:
        return 0
    data = _load()
    key = page_fingerprint(url, platform)
    data.setdefault("entries", {})[key] = {
        "platform": (platform or "unknown").lower().strip() or "unknown",
        "url_sample": (url or "")[:180],
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "map": rows,
    }
    _save(data)
    return len(rows)


def record_from_report(report: dict | str | Path) -> int:
    """Record selector→type map from a prior fast_fill JSON report (no browser)."""
    if isinstance(report, (str, Path)):
        data = json.loads(Path(report).read_text())
    else:
        data = report
    if not isinstance(data, dict):
        raise ValueError("report must be a JSON object")
    url = str(data.get("url") or "")
    platform = str(data.get("platform") or "unknown")
    if not url:
        raise ValueError("report missing url")
    return record_successful_fills(url, platform, data.get("filled") or [])


def lookup_playbook(url: str, platform: str, field_type: str) -> str | None:
    """Return cached playbook id for field_type on this fingerprint, or None."""
    ftype = str(field_type or "").strip()
    if not ftype:
        return None
    key = page_fingerprint(url, platform)
    entry = (_load().get("entries") or {}).get(key) or {}
    playbooks = _scrub_playbooks(entry.get("playbooks"))
    row = playbooks.get(ftype)
    if not row:
        return None
    pb = str(row.get("playbook") or "").strip()
    return pb or None


def record_playbook_hit(
    url: str,
    platform: str,
    field_type: str,
    playbook_id: str,
    selector: str = "",
    *,
    ok: bool = True,
) -> bool:
    """Persist playbook strategy hit for a field type (no PII values).

    Returns False when playbook_id is not allowlisted. Increments success when
    ok=True, else increments fail.
    """
    from playbooks import is_allowed_playbook

    pb = str(playbook_id or "").strip()
    ftype = str(field_type or "").strip()
    if not pb or not ftype:
        return False
    if not is_allowed_playbook(pb):
        return False

    data = _load()
    key = page_fingerprint(url, platform)
    entries = data.setdefault("entries", {})
    entry = entries.get(key)
    if not isinstance(entry, dict):
        entry = {
            "platform": (platform or "unknown").lower().strip() or "unknown",
            "url_sample": (url or "")[:180],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "map": [],
        }
    playbooks = _scrub_playbooks(entry.get("playbooks"))
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    prev = playbooks.get(ftype) or {}
    success = int(prev.get("success") or 0)
    fail = int(prev.get("fail") or 0)
    if ok:
        success += 1
    else:
        fail += 1
    sel = str(selector or prev.get("selector") or "").strip()
    playbooks[ftype] = {
        "playbook": pb,
        "selector": sel,
        "success": success,
        "fail": fail,
        "updated": now,
    }
    entry["playbooks"] = playbooks
    entry["updated_at"] = now
    entry["platform"] = (platform or entry.get("platform") or "unknown").lower().strip() or "unknown"
    if url:
        entry["url_sample"] = (url or "")[:180]
    entries[key] = _scrub_entry(entry)
    _save(data)
    return True


def invalidate(url: str, platform: str, selector: str | None = None) -> None:
    """Drop whole entry or one selector after a verify miss."""
    data = _load()
    key = page_fingerprint(url, platform)
    entries = data.setdefault("entries", {})
    entry = entries.get(key)
    if not entry:
        return
    if not selector:
        entries.pop(key, None)
        _save(data)
        return
    rows = [
        r
        for r in (entry.get("map") or [])
        if not (isinstance(r, dict) and r.get("selector") == selector)
    ]
    if not rows:
        entries.pop(key, None)
    else:
        entry["map"] = rows
        entry["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save(data)


def list_entries(*, verbose: bool = False) -> dict[str, Any]:
    data = _load()
    entries = data.get("entries") or {}
    summary = []
    for key, entry in sorted(entries.items(), key=lambda kv: kv[1].get("updated_at") or ""):
        if not isinstance(entry, dict):
            continue
        item = {
            "fingerprint": key,
            "platform": entry.get("platform"),
            "url_sample": entry.get("url_sample"),
            "updated_at": entry.get("updated_at"),
            "selectors": len(entry.get("map") or []),
        }
        if verbose:
            item["map"] = entry.get("map") or []
        summary.append(item)
    return {
        "path": str(CACHE_PATH),
        "version": data.get("version"),
        "count": len(summary),
        "entries": summary if not verbose else entries,
        "summary": summary,
    }


def clear_cache() -> None:
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()


def _self_test() -> int:
    """Deterministic tests for playbook cache + sanitize (no disk side effects)."""
    from playbooks import detect_playbook, is_allowed_playbook

    assert detect_playbook({"tag": "select"}) == "native_select"
    assert not is_allowed_playbook("free_form_click")

    assert record_playbook_hit(
        "https://boards.greenhouse.io/acme/jobs/123",
        "greenhouse",
        "DEGREE",
        "free_form_click",
    ) is False
    assert lookup_playbook(
        "https://boards.greenhouse.io/acme/jobs/123",
        "greenhouse",
        "DEGREE",
    ) is None

    # In-memory scrub tests (no forbidden keys leak)
    dirty_pb = _scrub_playbook_row(
        {
            "playbook": "react_select_portal",
            "selector": "#degree",
            "success": 2,
            "fail": 0,
            "value": "secret",
            "email": "x@y.com",
        },
        field_type="DEGREE",
    )
    assert dirty_pb is not None
    assert dirty_pb["playbook"] == "react_select_portal"
    assert "value" not in dirty_pb and "email" not in dirty_pb

    assert _scrub_playbook_row({"playbook": "bad_id"}, field_type="X") is None

    entry = _scrub_entry(
        {
            "platform": "greenhouse",
            "url_sample": "https://example.com/j/1",
            "map": [{"selector": "input#x", "type": "EMAIL", "value": "leak@x.com"}],
            "playbooks": {
                "DEGREE": {
                    "playbook": "typable_commit",
                    "selector": ".degree",
                    "success": 1,
                    "fail": 0,
                    "updated": "2026-01-01T00:00:00Z",
                    "answer": "Masters",
                }
            },
        }
    )
    assert entry["map"] == [{"selector": "input#x", "type": "EMAIL"}]
    assert entry["playbooks"]["DEGREE"]["playbook"] == "typable_commit"
    assert "answer" not in entry["playbooks"]["DEGREE"]

    # Backward compat: entries without playbooks key scrub cleanly
    legacy = _scrub_entry(
        {"platform": "lever", "url_sample": "https://jobs.lever.co/co", "map": []}
    )
    assert "playbooks" not in legacy

    print("record_replay self-test OK")
    return 0


async def apply_replay_map(page, url: str, platform: str, values: dict) -> list[dict]:
    """Fill from cached selector map using current dummy values. 0 LLM.

    On missing selector or verify miss, invalidates that selector (and the whole
    entry if miss rate is high). Never writes values into the cache.
    UUID-only LINKEDIN/GITHUB/PORTFOLIO selectors are skipped + invalidated
    (prefer label-based fill via ashby_widgets / pack).
    """
    from field_map import validate_filled, value_ok_for_field_shape  # local import

    _DOM_LABEL_JS = """(el) => {
      const skipOpt = /^(yes|no|female|male|non-binary|select\\.\\.\\.)$/i;
      let block = el.closest('.application-question, fieldset, li, .section, div');
      for (let i = 0; i < 8 && block; i++) {
        const lines = (block.innerText || '').split('\\n')
          .map((l) => l.trim())
          .filter((l) => l.length > 15 && !skipOpt.test(l));
        if (lines.length) return lines[0].slice(0, 200);
        block = block.parentElement;
      }
      return '';
    }"""

    rows = lookup_replay(url, platform)
    if not rows:
        return []

    filled: list[dict] = []
    misses = 0
    attempts = 0

    for row in rows:
        sel = row["selector"]
        ftype = row["type"]
        # Drop stale UUID-only URL selectors immediately (Ashby LinkedIn bug class)
        if str(ftype).upper() in _URL_FIELD_TYPES and _is_uuid_only_selector(sel):
            invalidate(url, platform, sel)
            preferred = _PREFERRED_URL_SELECTORS.get(str(ftype).upper())
            if preferred:
                sel = preferred
            else:
                misses += 1
                continue
        val = values.get(ftype)
        if val is None or val == "":
            continue
        if not validate_filled(ftype, str(val)):
            continue
        attempts += 1
        loc = page.locator(sel).first
        try:
            if await loc.count() == 0:
                invalidate(url, platform, row["selector"])
                misses += 1
                continue
            if not await loc.is_visible(timeout=600):
                continue
            dom_label = ""
            try:
                dom_label = await loc.evaluate(_DOM_LABEL_JS)
            except Exception:
                dom_label = ""
            if not value_ok_for_field_shape(str(val), label=dom_label, ftype=ftype):
                invalidate(url, platform, row["selector"])
                misses += 1
                continue
            tag = (await loc.evaluate("el => (el.tagName || '').toLowerCase()")) or ""
            role = (await loc.get_attribute("role")) or ""
            if tag == "input" and ((await loc.get_attribute("type")) or "").lower() == "file":
                continue  # file upload handled elsewhere
            if role == "combobox" or tag == "button":
                # Leave widgets to pack/helpers — still count as replay hint only
                filled.append(
                    {
                        "via": "replay_hint",
                        "layer": "replay",
                        "selector": sel,
                        "type": ftype,
                        "ok": False,
                        "reason": "widget_deferred",
                    }
                )
                continue
            # SKIP thrash: never clear/retype when live value already matches
            try:
                existing = (await loc.input_value() or "").strip()
            except Exception:
                existing = ""
            _empty_ui = (
                not existing
                or existing.lower() in ("", "type here...", "start typing...")
                or existing.lower().startswith("type here")
                or existing.lower().startswith("start typing")
                or existing.lower().startswith("select ")
            )
            if (
                not _empty_ui
                and (
                    str(val).lower() in existing.lower()
                    or existing.lower() in str(val).lower()
                )
            ):
                filled.append(
                    {
                        "via": "replay",
                        "layer": "replay",
                        "selector": sel,
                        "type": ftype,
                        "value": val,
                        "readback": existing[:120],
                        "verified_value": existing[:120],
                        "ok": True,
                        "verified": True,
                        "reason": "already_correct_skip",
                        "skipped_already_correct": True,
                    }
                )
                continue
            await loc.fill(str(val), timeout=4000)
            readback = ""
            try:
                readback = (await loc.input_value() or "").strip()
            except Exception:
                pass
            ok = bool(readback) and not (
                readback.lower() in ("", "type here...", "start typing...")
            ) and (
                str(val).lower() in readback.lower() or readback.lower() in str(val).lower()
            )
            if not ok:
                invalidate(url, platform, row["selector"])
                # Also invalidate preferred if we rewrote
                if sel != row["selector"]:
                    invalidate(url, platform, sel)
                misses += 1
                filled.append(
                    {
                        "via": "replay",
                        "layer": "replay",
                        "selector": sel,
                        "type": ftype,
                        "value": val,
                        "readback": readback[:120] if readback else "",
                        "verified_value": None,
                        "ok": False,
                        "verified": False,
                        "reason": "verify_miss_empty_readback",
                        "flash_candidate": True,
                    }
                )
                continue
            filled.append(
                {
                    "via": "replay",
                    "layer": "replay",
                    "selector": sel,
                    "type": ftype,
                    "value": val,
                    "readback": readback[:120],
                    "verified_value": readback[:120],
                    "ok": True,
                    "verified": True,
                }
            )
        except Exception:
            invalidate(url, platform, row["selector"])
            misses += 1

    # Stale map: too many verify/selector misses → drop whole tenant entry
    if attempts and misses >= max(2, (attempts + 1) // 2):
        invalidate(url, platform)

    # Only return verified ok rows (failed URL attempts stay out of filled;
    # callers that need leftovers should use flash_candidate from elsewhere)
    return [f for f in filled if f.get("ok")]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description=(
            "Record-once / replay cache for fastfill (selector→type only; no PII values).\n\n"
            "Examples:\n"
            "  record_replay.py --list\n"
            "  record_replay.py --list --verbose\n"
            "  record_replay.py --clear\n"
            "  record_replay.py --sanitize\n"
            "  record_replay.py --record-from path/to/fast_fill_report.json\n"
            "  record_replay.py --fingerprint URL --platform greenhouse"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--list",
        action="store_true",
        help="List cached tenant fingerprints (selector counts; no values)",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="With --list, include full selector→type maps",
    )
    ap.add_argument(
        "--clear",
        action="store_true",
        help="Delete replay_cache.json entirely",
    )
    ap.add_argument(
        "--sanitize",
        action="store_true",
        help="Scrub PII keys and rekey entries to current fingerprint rules",
    )
    ap.add_argument(
        "--record-from",
        metavar="JSON",
        help="Record selector→type from a prior fast_fill report (no browser)",
    )
    ap.add_argument("--fingerprint", metavar="URL", help="Print fingerprint debug for URL")
    ap.add_argument("--platform", default="", help="Platform hint for --fingerprint")
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="Run deterministic playbook-cache and sanitize tests",
    )
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    if args.clear:
        clear_cache()
        print(json.dumps({"cleared": True, "path": str(CACHE_PATH)}))
    elif args.sanitize:
        print(json.dumps(sanitize_cache(write=True), indent=2))
    elif args.record_from:
        n = record_from_report(args.record_from)
        print(
            json.dumps(
                {
                    "recorded": n,
                    "path": str(CACHE_PATH),
                    "source": args.record_from,
                },
                indent=2,
            )
        )
    elif args.fingerprint:
        print(json.dumps(fingerprint_debug(args.fingerprint, args.platform), indent=2))
    else:
        # Default / --list
        print(json.dumps(list_entries(verbose=args.verbose), indent=2, default=str))
