#!/usr/bin/env python3
"""Single source of truth for the text-LLM endpoint (base / key / model).

Before this module three call sites resolved the endpoint independently
(flash_leftovers, dashboard/agent_runner.load_deepseek_config, and a vision
path). Vision stays separate on purpose — DeepSeek flash rejects image payloads,
so it uses its own OPENAI_API_KEY/VISION_MODEL. This helper unifies the two
*text* resolvers so the base URL — the one seam a gateway (OmniRoute) points at —
is set in exactly one place.

``OPENAI_COMPATIBLE_API_BASE`` is that seam: default is DeepSeek direct, so if a
gateway container is stopped the fill still runs against DeepSeek. Never reads
profile.json (PII); keys come from env, then repo key files, then the gitignored
skyvern_runtime/.secrets.env.

Phase 7 alternative gateway: because everything OpenAI-compatible flows through
this one base URL, a managed OpenRouter can replace the self-hosted OmniRoute
sidecar with zero code change — set (dummy-mode only)::

    OPENAI_COMPATIBLE_API_BASE=https://openrouter.ai/api/v1
    OPENAI_COMPATIBLE_API_KEY=<openrouter key>

The dummy-only gateway guard below applies to any non-DeepSeek base.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_KEY_NAMES = ("OPENAI_COMPATIBLE_API_KEY", "DEEPSEEK_API_KEY")
_DEFAULT_BASE = "https://api.deepseek.com/v1"
_DEFAULT_MODEL = "deepseek-v4-flash"


def resolve_base_model() -> tuple[str, str]:
    """(base_url, model) from env, with DeepSeek-direct defaults."""
    base = (os.environ.get("OPENAI_COMPATIBLE_API_BASE") or _DEFAULT_BASE).rstrip("/")
    model = os.environ.get("OPENAI_COMPATIBLE_MODEL_NAME") or _DEFAULT_MODEL
    return base, model


def _read_env_file_key(path: Path, names: tuple[str, ...]) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export ") :]
        for name in names:
            if line.startswith(name + "="):
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                if raw:
                    return raw
    return None


def _json_deep_find(obj, names: tuple[str, ...]) -> str | None:
    wanted = {n.lower() for n in names}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in wanted and isinstance(v, str) and v.strip():
                return v.strip()
        for v in obj.values():
            found = _json_deep_find(v, names)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _json_deep_find(v, names)
            if found:
                return found
    return None


def _json_file_key(path: Path, names: tuple[str, ...]) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _json_deep_find(data, names)


def resolve_api_key(root: Path | None = None, *, include_key_files: bool = True) -> str:
    """First non-empty key: env -> web_keys/credentials -> .env/.secrets.env.

    Never reads profile.json. ``include_key_files=False`` restricts to env +
    skyvern_runtime/.secrets.env (the minimal set flash_leftovers historically
    used). Returns "" when nothing is configured.
    """
    key = (
        os.environ.get("OPENAI_COMPATIBLE_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or ""
    ).strip()
    if key:
        return key
    root = root or Path(__file__).resolve().parent.parent.parent
    if include_key_files:
        for candidate in (root / "web_keys.json", root / "credentials.json"):
            if candidate.is_file():
                found = _json_file_key(candidate, _KEY_NAMES)
                if found:
                    return found
    env_files = []
    if include_key_files:
        env_files.append(root / ".env")
    env_files.append(root / "skyvern_runtime" / ".secrets.env")
    for candidate in env_files:
        if candidate.is_file():
            found = _read_env_file_key(candidate, _KEY_NAMES)
            if found:
                return found
    return ""


def resolve_llm_config(
    root: Path | None = None, *, include_key_files: bool = True
) -> tuple[str, str, str]:
    """(api_key, base_url, model). api_key is "" when unconfigured."""
    base, model = resolve_base_model()
    return resolve_api_key(root, include_key_files=include_key_files), base, model


def is_gateway_base(base: str) -> bool:
    """True when ``base`` is anything other than the DeepSeek-direct default.

    A non-default base means traffic is routed through a gateway (OmniRoute) and
    possibly its free third-party pools — the only paths that could leak PII to a
    third party.
    """
    return (base or "").rstrip("/") != _DEFAULT_BASE


def assert_dummy_for_gateway(base: str) -> None:
    """Refuse gateway / free-pool routing while real-profile mode is on.

    Real applicant PII must never traverse OmniRoute or its free third-party
    pools. In real-profile mode only the DeepSeek-direct default base is allowed;
    dummy mode (the default) may use any base. Import of field_map is lazy so
    this stays dependency-light and never introduces a cycle.
    """
    if not is_gateway_base(base):
        return
    try:
        from field_map import is_real_profile_mode
    except Exception:
        return
    if is_real_profile_mode():
        raise RuntimeError(
            "gateway/free-pool LLM base refused in real-profile mode: real PII "
            "must use DeepSeek direct. Unset OPENAI_COMPATIBLE_API_BASE or turn "
            "off real-profile mode (this is a dummy-only path)."
        )
