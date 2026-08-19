#!/usr/bin/env python3
"""Deterministic ATS posting and organization identity extraction."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from apply_urls import collect_all_urls

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_GREENHOUSE_HOSTS = ("greenhouse.io", "greenhouse.com")


def _parts(url: str) -> tuple[str, list[str], dict[str, list[str]]]:
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return "", [], {}
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = [p for p in parsed.path.split("/") if p]
    return host, path, parse_qs(parsed.query)


def posting_identity_for_url(url: str) -> tuple[str | None, str | None]:
    """Return (posting_key, ATS org key) for supported platform URLs."""
    host, path, query = _parts(url)
    lower = [p.lower() for p in path]
    if not host:
        return None, None

    if host.endswith("ashbyhq.com") and len(path) >= 2:
        org, posting_id = lower[0], lower[1]
        if _UUID_RE.fullmatch(posting_id):
            return f"{host}:{posting_id}", f"ashby:{org}"

    if any(host.endswith(suffix) for suffix in _GREENHOUSE_HOSTS):
        org = lower[0] if lower and lower[0] not in ("jobs", "embed") else ""
        if not org:
            org = (query.get("for") or [""])[0].strip().lower()
        posting_id = ""
        if "jobs" in lower:
            idx = lower.index("jobs")
            if idx + 1 < len(lower) and lower[idx + 1].isdigit():
                posting_id = lower[idx + 1]
        if not posting_id:
            token = (query.get("token") or query.get("gh_jid") or [""])[0].lower()
            if token.isdigit():
                posting_id = token
        if posting_id:
            return f"greenhouse.io:{posting_id}", f"greenhouse:{org}" if org else None

    if host.endswith("lever.co") and len(path) >= 2:
        org, posting_id = lower[0], lower[1]
        # Lever posting ids are UUIDs; bare "apply" / city slugs must not become keys.
        if posting_id and _UUID_RE.fullmatch(posting_id):
            return f"{host}:{posting_id}", f"lever:{org}"

    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        org = host.split(".", 1)[0]
        posting_id = ""
        for segment in reversed(lower):
            match = re.search(r"(?:^|_)(r-\d+)$", segment)
            if match:
                posting_id = match.group(1)
                break
        if posting_id:
            return f"{host}:{posting_id}", f"workday:{org}"

    return None, None


def posting_key(item: dict) -> str | None:
    explicit = str((item or {}).get("posting_key") or "").strip().lower()
    if explicit:
        return explicit
    for url in collect_all_urls(item or {}):
        key, _org = posting_identity_for_url(url)
        if key:
            return key
    return None


def ats_org_key(item: dict) -> str | None:
    for url in collect_all_urls(item or {}):
        _key, org = posting_identity_for_url(url)
        if org:
            return org
    return None


def same_ats_org(a: dict, b: dict) -> bool:
    a_org = ats_org_key(a)
    return bool(a_org and a_org == ats_org_key(b))
