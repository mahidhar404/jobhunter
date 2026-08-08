#!/usr/bin/env python3
"""Per-site ATS account passwords for fastfill (workspace web_keys.json).

Never log raw passwords — callers must mask as *** in prints.
Dummy/test runs use formula passwords; real mode may reuse stored site keys.
Do NOT put passwords in Flash prompts or learned_fields.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WEB_KEYS_PATH = ROOT / "web_keys.json"
CREDENTIALS_PATH = ROOT / "credentials.json"
DESKTOP_DOCS = Path.home() / "Desktop" / "Command Center" / "Documents"
DESKTOP_SYMLINK = DESKTOP_DOCS / "web_keys.json"

_PASSWORD_PREFIX = "Pswdpswd@912*"
_ATS_MAX_PASSWORD_LEN = 64


def sanitize_company(name: str) -> str:
    """Alphanumerics only; strip spaces and punctuation."""
    return re.sub(r"[^A-Za-z0-9]", "", (name or "").strip())


def make_password(company: str) -> str:
    """Pswdpswd@912*{CompanySanitized}, capped to ATS-safe length (~64)."""
    sanitized = sanitize_company(company) or "Company"
    budget = max(1, _ATS_MAX_PASSWORD_LEN - len(_PASSWORD_PREFIX))
    return f"{_PASSWORD_PREFIX}{sanitized[:budget]}"


def company_from_host(host: str | None) -> str:
    """First label of hostname (quantiphi from quantiphi.wd1.myworkdayjobs.com)."""
    h = (host or "").strip().lower()
    if not h:
        return "Company"
    return (h.split(".")[0] or "Company").capitalize()


def _ensure_desktop_symlink(target: Path) -> None:
    """Symlink ~/Desktop/Command Center/Documents/web_keys.json → workspace file."""
    try:
        DESKTOP_DOCS.mkdir(parents=True, exist_ok=True)
        if DESKTOP_SYMLINK.is_symlink():
            return
        if DESKTOP_SYMLINK.exists():
            warnings.warn(
                f"web_keys desktop path exists and is not a symlink; "
                f"leaving alone: {DESKTOP_SYMLINK}",
                stacklevel=2,
            )
            return
        DESKTOP_SYMLINK.symlink_to(target.resolve())
    except OSError as e:
        warnings.warn(f"web_keys desktop symlink skipped: {e}", stacklevel=2)


def _migrate_from_credentials(data: dict) -> dict:
    """One-time copy of credentials.json sites into empty web_keys (no logging)."""
    sites = data.setdefault("sites", {})
    if sites:
        return data
    if not CREDENTIALS_PATH.is_file():
        return data
    try:
        raw = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return data
    cred_sites = raw.get("sites") if isinstance(raw, dict) else None
    if not isinstance(cred_sites, dict):
        return data
    for host, entry in cred_sites.items():
        if not isinstance(entry, dict):
            continue
        h = str(host or "").strip().lower()
        if not h:
            continue
        sites[h] = {
            "company": entry.get("company") or company_from_host(h),
            "email": entry.get("email") or "",
            "password": entry.get("password") or "",
            "created_at": entry.get("created_at")
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "job_id": entry.get("job_id"),
            "source": entry.get("source") or "credentials_migrate",
        }
    return data


def load_web_keys() -> dict:
    """Load web_keys.json; create empty {"sites": {}} if missing."""
    path = WEB_KEYS_PATH
    default_path = ROOT / "web_keys.json"
    allow_migrate = path.resolve() == default_path.resolve()
    if not path.is_file():
        data: dict[str, Any] = {"sites": {}}
        if allow_migrate:
            data = _migrate_from_credentials(data)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except OSError:
            pass
        if allow_migrate:
            _ensure_desktop_symlink(path)
        return data
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {"sites": {}}
    if not isinstance(data, dict):
        data = {"sites": {}}
    data.setdefault("sites", {})
    if allow_migrate and not data["sites"]:
        before = dict(data.get("sites") or {})
        data = _migrate_from_credentials(data)
        if data.get("sites") != before:
            try:
                path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            except OSError:
                pass
    if allow_migrate:
        _ensure_desktop_symlink(path)
    return data


def lookup(host: str) -> dict | None:
    """Return site entry by hostname, or None."""
    h = (host or "").strip().lower()
    if not h:
        return None
    sites = load_web_keys().get("sites") or {}
    entry = sites.get(h)
    return dict(entry) if isinstance(entry, dict) else None


def upsert(
    host: str,
    *,
    company: str,
    email: str,
    password: str,
    job_id: str | None = None,
    source: str = "fastfill",
) -> None:
    """Create or update a site entry (lazy-creates web_keys.json)."""
    h = (host or "").strip().lower()
    if not h:
        return
    data = load_web_keys()
    sites = data.setdefault("sites", {})
    prev = sites.get(h) if isinstance(sites.get(h), dict) else {}
    sites[h] = {
        "company": company or prev.get("company") or company_from_host(h),
        "email": email or prev.get("email") or "",
        "password": password or prev.get("password") or "",
        "created_at": prev.get("created_at")
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "job_id": job_id if job_id is not None else prev.get("job_id"),
        "source": source or prev.get("source") or "fastfill",
    }
    WEB_KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEB_KEYS_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(WEB_KEYS_PATH, 0o600)
    except OSError:
        pass
    if WEB_KEYS_PATH.resolve() == (ROOT / "web_keys.json").resolve():
        _ensure_desktop_symlink(WEB_KEYS_PATH)


def ensure_password_for_company(
    company: str,
    values: dict,
    *,
    host: str | None = None,
    email: str | None = None,
) -> str:
    """Set PASSWORD and PASSWORD_CONFIRM on values; return password.

    Prefers lookup(host) password when present (and email matches when both set).
    """
    from field_map import EMAIL, PASSWORD, PASSWORD_CONFIRM

    host_n = (host or "").strip().lower() or None
    email_pref = (email or values.get(EMAIL) or "").strip()
    stored = lookup(host_n) if host_n else None
    pw = ""
    if stored and (stored.get("password") or "").strip():
        stored_email = str(stored.get("email") or "").strip()
        if not email_pref or not stored_email or stored_email.lower() == email_pref.lower():
            pw = str(stored["password"]).strip()
    if not pw:
        label = (company or "").strip() or company_from_host(host_n)
        pw = make_password(label)
    values[PASSWORD] = pw
    values[PASSWORD_CONFIRM] = pw
    return pw


def mask_password(pw: str | None) -> str:
    """Always *** for logs — never echo secrets."""
    return "***" if pw else ""
