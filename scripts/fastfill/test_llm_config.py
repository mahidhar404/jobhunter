#!/usr/bin/env python3
"""Phase 4 tests: unified LLM config helper + dummy-mode gateway guard.

No network. Verifies:
  - resolve_base_model honors env and defaults to DeepSeek-direct.
  - resolve_api_key reads env first, then a fixture .secrets.env, never profile.
  - is_gateway_base only flags non-default bases.
  - assert_dummy_for_gateway raises for a gateway base in real-profile mode,
    allows it in dummy mode, and never blocks the DeepSeek-direct default.

DUMMY / synthetic fixtures only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import llm_config as lc  # noqa: E402


def test_base_model_defaults(monkeypatch):
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_COMPATIBLE_MODEL_NAME", raising=False)
    base, model = lc.resolve_base_model()
    assert base == "https://api.deepseek.com/v1"
    assert model == "deepseek-v4-flash"


def test_base_model_env_override(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_BASE", "http://omniroute:20128/v1/")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL_NAME", "kimi-k2")
    base, model = lc.resolve_base_model()
    assert base == "http://omniroute:20128/v1"  # trailing slash stripped
    assert model == "kimi-k2"


def test_api_key_env_first(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "ENVKEY")
    assert lc.resolve_api_key() == "ENVKEY"


def test_api_key_from_secrets_file(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    (tmp_path / "skyvern_runtime").mkdir()
    (tmp_path / "skyvern_runtime" / ".secrets.env").write_text(
        'export DEEPSEEK_API_KEY="FILEKEY"\n'
    )
    assert lc.resolve_api_key(root=tmp_path) == "FILEKEY"


def test_api_key_never_reads_profile(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    # A profile.json with a key-looking field must be ignored entirely.
    (tmp_path / "profile.json").write_text('{"deepseek_api_key": "LEAK"}')
    assert lc.resolve_api_key(root=tmp_path) == ""


def test_is_gateway_base():
    assert lc.is_gateway_base("http://omniroute:20128/v1") is True
    assert lc.is_gateway_base("https://api.deepseek.com/v1") is False
    assert lc.is_gateway_base("https://api.deepseek.com/v1/") is False


def test_gateway_guard_allows_default_base():
    # DeepSeek-direct is always fine regardless of mode.
    lc.assert_dummy_for_gateway("https://api.deepseek.com/v1")


def test_gateway_guard_allows_dummy_mode(monkeypatch):
    import field_map as fm

    monkeypatch.setattr(fm, "is_real_profile_mode", lambda: False)
    lc.assert_dummy_for_gateway("http://omniroute:20128/v1")  # no raise


def test_gateway_guard_blocks_real_mode(monkeypatch):
    import field_map as fm

    monkeypatch.setattr(fm, "is_real_profile_mode", lambda: True)
    with pytest.raises(RuntimeError, match="real-profile mode"):
        lc.assert_dummy_for_gateway("http://omniroute:20128/v1")
