#!/usr/bin/env python3
"""Phase 3b tests: evidence-gated semantic answer-memory recall.

No files / network: load_experience is stubbed with fixtures and semantic_sim is
stubbed for deterministic scoring. Verifies:
  - Default OFF: a paraphrase-only past answer (no type/exact/token match) is NOT
    recalled — prior lexical behavior is unchanged.
  - Flag ON: the paraphrase is recalled via semantic similarity.
  - Flag ON ranking: a strong type match outranks a fuzzy paraphrase.

DUMMY / synthetic fixtures only — labels/values here are invented, not real PII.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import continuous_learn as cl  # noqa: E402
import semantic_match as sm  # noqa: E402


_FIXTURES = [
    {  # paraphrase of "salary expectation"; shares no >=4-char token with it
        "ok": True,
        "type": "",
        "label": "Desired compensation",
        "value": "$120,000",
        "platform": "greenhouse",
        "value_shape": "money",
    },
    {  # strong type match
        "ok": True,
        "type": "SALARY_EXPECTED",
        "label": "Comp target",
        "value": "$130,000",
        "platform": "greenhouse",
        "value_shape": "money",
    },
]


def _stub_experience(monkeypatch, rows):
    monkeypatch.setattr(cl, "load_experience", lambda *a, **k: list(rows))


def test_paraphrase_not_recalled_when_disabled(monkeypatch):
    # Explicitly disabled (default is now ON): paraphrase-only row not recalled.
    monkeypatch.setenv("FASTFILL_ANSWER_MEMORY", "0")
    monkeypatch.setenv("FASTFILL_SEMANTIC_MEMORY", "0")

    def boom(a, b):  # semantic must not even be consulted when disabled
        raise AssertionError("semantic_sim called while answer-memory disabled")

    monkeypatch.setattr(sm, "semantic_sim", boom)
    _stub_experience(monkeypatch, [_FIXTURES[0]])
    out = cl.similar_leftover_answers(
        [{"label": "Salary expectation", "type": ""}], platform="greenhouse"
    )
    assert out == []


def test_paraphrase_recalled_by_default(monkeypatch):
    # Default-ON: no flag set, paraphrase recalled via semantic similarity.
    monkeypatch.delenv("FASTFILL_ANSWER_MEMORY", raising=False)
    monkeypatch.delenv("FASTFILL_SEMANTIC_MEMORY", raising=False)
    monkeypatch.delenv("FASTFILL_SEMANTIC_MATCH", raising=False)
    _stub_experience(monkeypatch, [_FIXTURES[0]])

    def fake_sim(a, b):
        if {a.lower(), b.lower()} == {"desired compensation", "salary expectation"}:
            return 0.9
        return 0.0

    monkeypatch.setattr(sm, "semantic_sim", fake_sim)
    out = cl.similar_leftover_answers(
        [{"label": "Salary expectation", "type": ""}], platform="greenhouse"
    )
    assert len(out) == 1 and out[0]["value"] == "$120,000"


def test_paraphrase_recalled_when_on(monkeypatch):
    monkeypatch.setenv("FASTFILL_SEMANTIC_MEMORY", "1")
    _stub_experience(monkeypatch, [_FIXTURES[0]])

    def fake_sim(a, b):
        pair = {a.lower(), b.lower()}
        if pair == {"desired compensation", "salary expectation"}:
            return 0.9
        return 0.0

    monkeypatch.setattr(sm, "semantic_sim", fake_sim)
    out = cl.similar_leftover_answers(
        [{"label": "Salary expectation", "type": ""}], platform="greenhouse"
    )
    assert len(out) == 1
    assert out[0]["value"] == "$120,000"
    assert out[0]["via"] == "experience"
    assert "_score" not in out[0]  # ranking key stripped before return


def test_answer_memory_alias_flag_enables(monkeypatch):
    # The plan's documented flag name (FASTFILL_ANSWER_MEMORY) also enables it.
    monkeypatch.delenv("FASTFILL_SEMANTIC_MEMORY", raising=False)
    monkeypatch.setenv("FASTFILL_ANSWER_MEMORY", "1")
    _stub_experience(monkeypatch, [_FIXTURES[0]])

    def fake_sim(a, b):
        if {a.lower(), b.lower()} == {"desired compensation", "salary expectation"}:
            return 0.9
        return 0.0

    monkeypatch.setattr(sm, "semantic_sim", fake_sim)
    out = cl.similar_leftover_answers(
        [{"label": "Salary expectation", "type": ""}], platform="greenhouse"
    )
    assert len(out) == 1 and out[0]["value"] == "$120,000"


def test_type_match_outranks_paraphrase_when_on(monkeypatch):
    monkeypatch.setenv("FASTFILL_SEMANTIC_MEMORY", "1")
    _stub_experience(monkeypatch, _FIXTURES)

    def fake_sim(a, b):
        pair = {a.lower(), b.lower()}
        if pair == {"desired compensation", "salary expectation"}:
            return 0.9
        return 0.0

    monkeypatch.setattr(sm, "semantic_sim", fake_sim)
    out = cl.similar_leftover_answers(
        [{"label": "Salary expectation", "type": "SALARY_EXPECTED"}],
        platform="greenhouse",
    )
    # Both recalled; the SALARY_EXPECTED type match ranks first.
    assert out[0]["type"] == "SALARY_EXPECTED"
    assert {h["value"] for h in out} == {"$120,000", "$130,000"}
