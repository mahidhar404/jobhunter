#!/usr/bin/env python3
"""OpenClaw-free exec-approvals allowlist store.

Replaces ``openclaw approvals allowlist add --agent job-hunter "<binary>*"``.
The approvals file was already a plain local JSON (``~/.openclaw/exec-
approvals.json``); OpenClaw's CLI was only a writer for it. This edits that
same file directly (append the binary glob to the agent's allowlist) and keeps
``ask: off`` so the agent's exec tool never hangs on an unattended prompt.

Only relevant if the replacement agent keeps an exec-approval flow; kept as a
faithful, dependency-free local store so the ``/api/allowlist`` view and the
command-approval resume path behave as before.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_APPROVALS_FILE = Path.home() / ".openclaw" / "exec-approvals.json"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def ensure_ask_off(agent: str = "job-hunter", *, path: Path | None = None) -> None:
    """Keep the agent on security=allowlist with ask=off (no hang on prompts)."""
    path = path or DEFAULT_APPROVALS_FILE
    data = _load(path)
    ag = data.setdefault("agents", {}).setdefault(agent, {})
    changed = False
    if ag.get("ask") != "off":
        ag["ask"] = "off"
        changed = True
    if ag.get("security") not in ("allowlist", "full"):
        ag["security"] = "allowlist"
        changed = True
    if changed:
        try:
            _save(path, data)
        except OSError as e:
            print(f"warn: could not repair exec-approvals ask field: {e}")


def allowlist_add(pattern: str, agent: str = "job-hunter", *,
                  path: Path | None = None) -> dict:
    """Add an allowlist glob (e.g. ``python3*``) for *agent*, keep ask=off.

    Idempotent; returns the agent's block after the update.
    """
    path = path or DEFAULT_APPROVALS_FILE
    pattern = (pattern or "").strip()
    data = _load(path)
    ag = data.setdefault("agents", {}).setdefault(agent, {})
    allow = ag.setdefault("allowlist", [])
    if not isinstance(allow, list):
        allow = []
        ag["allowlist"] = allow
    if pattern and pattern not in allow:
        allow.append(pattern)
    ag.setdefault("security", "allowlist")
    ag["ask"] = "off"
    try:
        _save(path, data)
    except OSError as e:
        print(f"warn: could not write exec-approvals allowlist: {e}")
    return dict(ag)
