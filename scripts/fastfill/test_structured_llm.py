#!/usr/bin/env python3
"""Phase 2 tests: typed {value,confidence} leftover output + retry + gate.

All HTTP is mocked (no network). Verifies:
  - _parse_json_answer tolerates clean / fenced / embedded JSON, rejects junk.
  - call_flash_json_llm retries until a well-formed object parses, else None.
  - _llm_answer prefers typed value, applies the confidence floor, and always
    degrades to the plain text call (never raises, never returns "" on failure).

DUMMY / synthetic fixtures only.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import flash_leftovers as fl  # noqa: E402


def _chat(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


# --- _parse_json_answer --------------------------------------------------


def test_parse_clean_json():
    out = fl._parse_json_answer('{"value": "Male", "confidence": 0.9}')
    assert out == {"value": "Male", "confidence": 0.9}


def test_parse_fenced_json():
    out = fl._parse_json_answer('```json\n{"value": "Yes", "confidence": 1}\n```')
    assert out == {"value": "Yes", "confidence": 1.0}


def test_parse_embedded_json():
    out = fl._parse_json_answer('sure: {"value": "No", "confidence": "bad"} ok')
    assert out == {"value": "No", "confidence": None}


def test_parse_rejects_without_value():
    assert fl._parse_json_answer('{"confidence": 0.5}') is None
    assert fl._parse_json_answer("not json") is None
    assert fl._parse_json_answer("") is None


# --- call_flash_json_llm retry ------------------------------------------


def test_json_llm_retries_until_valid(monkeypatch):
    calls = {"n": 0}

    def fake_post(payload, *, timeout=45):
        calls["n"] += 1
        if calls["n"] == 1:
            return _chat("garbage not json")
        return _chat('{"value": "Master\'s Degree", "confidence": 0.8}')

    monkeypatch.setattr(fl, "_post_chat_completion", fake_post)
    out = fl.call_flash_json_llm("q", retries=2)
    assert out == {"value": "Master's Degree", "confidence": 0.8}
    assert calls["n"] == 2


def test_json_llm_none_when_all_attempts_bad(monkeypatch):
    monkeypatch.setattr(fl, "_post_chat_completion", lambda payload, *, timeout=45: _chat("nope"))
    assert fl.call_flash_json_llm("q", retries=1) is None


def test_json_llm_none_when_no_endpoint(monkeypatch):
    monkeypatch.setattr(fl, "_post_chat_completion", lambda payload, *, timeout=45: None)
    assert fl.call_flash_json_llm("q", retries=2) is None


# --- _llm_answer gate + fallback ----------------------------------------


def test_llm_answer_prefers_typed_value(monkeypatch):
    monkeypatch.setattr(fl, "_STRUCTURED_LLM", True)
    monkeypatch.setattr(fl, "_LLM_MIN_CONFIDENCE", 0.0)
    monkeypatch.setattr(fl, "call_flash_json_llm", lambda *a, **k: {"value": "Austin", "confidence": 0.9})
    monkeypatch.setattr(fl, "call_flash_text_llm", lambda *a, **k: "PLAIN")
    assert fl._llm_answer("q") == "Austin"


def test_llm_answer_falls_back_to_plain_when_typed_none(monkeypatch):
    monkeypatch.setattr(fl, "_STRUCTURED_LLM", True)
    monkeypatch.setattr(fl, "call_flash_json_llm", lambda *a, **k: None)
    monkeypatch.setattr(fl, "call_flash_text_llm", lambda *a, **k: "PLAIN")
    assert fl._llm_answer("q") == "PLAIN"


def test_llm_answer_low_confidence_routes_to_plain(monkeypatch):
    monkeypatch.setattr(fl, "_STRUCTURED_LLM", True)
    monkeypatch.setattr(fl, "_LLM_MIN_CONFIDENCE", 0.5)
    monkeypatch.setattr(fl, "call_flash_json_llm", lambda *a, **k: {"value": "maybe", "confidence": 0.1})
    monkeypatch.setattr(fl, "call_flash_text_llm", lambda *a, **k: "PLAIN")
    assert fl._llm_answer("q") == "PLAIN"


def test_llm_answer_structured_off_uses_plain_only(monkeypatch):
    monkeypatch.setattr(fl, "_STRUCTURED_LLM", False)

    def boom(*a, **k):  # must not be called
        raise AssertionError("json path used while structured disabled")

    monkeypatch.setattr(fl, "call_flash_json_llm", boom)
    monkeypatch.setattr(fl, "call_flash_text_llm", lambda *a, **k: "PLAIN")
    assert fl._llm_answer("q") == "PLAIN"
