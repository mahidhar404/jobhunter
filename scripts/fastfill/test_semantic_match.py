#!/usr/bin/env python3
"""Phase 3 tests: semantic matcher + gated classify / option-score fallbacks.

Runs entirely on the deterministic lexical backend (no embeddings, no network).
Verifies:
  - lexical_sim is bounded, symmetric-ish, 1.0 on identity, higher for paraphrases.
  - best_match picks the closest candidate.
  - classify_semantic resolves paraphrases only when FASTFILL_SEMANTIC_CLASSIFY=1.
  - The classify fallback never overrides a deterministic layer resolution.
  - _default_score_option's semantic bonus is OFF unless FASTFILL_SEMANTIC_OPTIONS=1,
    and is capped below soft/exact matches.

DUMMY / synthetic fixtures only.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import semantic_match as sm  # noqa: E402


def test_lexical_sim_bounds_and_identity():
    assert sm.lexical_sim("First Name", "First Name") == 1.0
    assert sm.lexical_sim("", "x") == 0.0
    s = sm.lexical_sim("Given name", "First name")
    assert 0.0 < s < 1.0


def test_lexical_sim_paraphrase_beats_unrelated():
    near = sm.lexical_sim("Expected salary", "Salary expectation")
    far = sm.lexical_sim("Expected salary", "Upload your resume")
    assert near > far


def test_best_match_picks_closest():
    idx, score = sm.best_match(
        "given name", ["upload resume", "first name", "phone number"]
    )
    assert idx == 1
    assert score > 0


def test_best_match_empty():
    assert sm.best_match("x", []) == (-1, 0.0)


# --- classify_semantic (field_map) --------------------------------------


def test_classify_semantic_resolves_paraphrase():
    import field_map as fm

    # Unit-level: the fallback maps a paraphrase to its exemplar's type.
    assert fm.classify_semantic({"label": "Given name"}) == "NAME_FIRST"
    assert fm.classify_semantic({"label": "Salary expectation"}) == "SALARY_EXPECTED"
    # Below threshold => no guess.
    assert fm.classify_semantic({"label": "zzz totally unknown blorp"}) is None


def test_classify_field_uses_semantic_layer_only_when_layers_miss(monkeypatch):
    import field_map as fm

    monkeypatch.setenv("FASTFILL_SEMANTIC_CLASSIFY", "1")
    # Force the deterministic layers to miss so the fallback is what resolves it,
    # proving the layer2_semantic tag + gating wiring.
    monkeypatch.setattr(fm, "classify_by_input_type", lambda f: None)
    monkeypatch.setattr(fm, "classify_layer0", lambda f: None)
    monkeypatch.setattr(fm, "classify_layer1", lambda f: None)
    ftype, layer = fm.classify_field({"label": "Given name"})
    assert ftype == "NAME_FIRST"
    assert layer == "layer2_semantic"


def test_classify_semantic_on_by_default(monkeypatch):
    import field_map as fm

    monkeypatch.delenv("FASTFILL_SEMANTIC_CLASSIFY", raising=False)
    monkeypatch.delenv("FASTFILL_SEMANTIC_MATCH", raising=False)
    # Default-ON: a clear paraphrase the deterministic layers miss now resolves
    # via the semantic layer (unit-level function reflects the wired default).
    assert fm._semantic_classify_enabled() is True
    assert fm.classify_semantic({"label": "Salary expectation"}) == "SALARY_EXPECTED"


def test_classify_semantic_disabled_by_flag(monkeypatch):
    import field_map as fm

    # Explicitly disabled: unknown label stays unresolved, and even a paraphrase
    # is not guessed.
    monkeypatch.setenv("FASTFILL_SEMANTIC_CLASSIFY", "0")
    assert fm._semantic_classify_enabled() is False
    field = {"label": "zzz totally unknown blorp", "name": "", "id": "", "placeholder": ""}
    ftype, layer = fm.classify_field(field)
    assert ftype is None
    assert layer == "unresolved"


def test_classify_semantic_kill_switch(monkeypatch):
    import field_map as fm

    monkeypatch.setenv("FASTFILL_SEMANTIC_CLASSIFY", "1")
    monkeypatch.setenv("FASTFILL_SEMANTIC_MATCH", "0")  # master off wins
    assert fm._semantic_classify_enabled() is False


def test_classify_semantic_never_overrides_deterministic(monkeypatch):
    import field_map as fm

    monkeypatch.setenv("FASTFILL_SEMANTIC_CLASSIFY", "1")
    # "Email" resolves in a deterministic layer; the returned layer must NOT be
    # the semantic one (fallback only runs after the layers return None).
    ftype, layer = fm.classify_field(
        {"label": "Email", "name": "email", "id": "", "placeholder": ""}
    )
    assert ftype == "EMAIL"
    assert layer != "layer2_semantic"


# --- _default_score_option gate -----------------------------------------


def test_option_semantic_bonus_disabled_by_flag(monkeypatch):
    import verified_select as vs

    monkeypatch.setenv("FASTFILL_SEMANTIC_OPTIONS", "0")
    monkeypatch.setattr(vs, "_SEMANTIC_OPTION_THRESHOLD", 0.5)
    # Even a strong paraphrase returns 0 when explicitly disabled.
    assert vs._semantic_option_bonus("Master's Degree", "Master Degree") == 0


def test_option_semantic_bonus_on_by_default(monkeypatch):
    import verified_select as vs

    monkeypatch.delenv("FASTFILL_SEMANTIC_OPTIONS", raising=False)
    monkeypatch.delenv("FASTFILL_SEMANTIC_MATCH", raising=False)
    monkeypatch.setattr(vs, "_SEMANTIC_OPTION_THRESHOLD", 0.5)
    # Default-ON: strong lexical overlap clears the threshold and returns the cap.
    assert vs._semantic_option_bonus("Master's Degree", "Master Degree") == 70


def test_option_semantic_bonus_capped_below_soft(monkeypatch):
    import verified_select as vs

    monkeypatch.setenv("FASTFILL_SEMANTIC_OPTIONS", "1")
    monkeypatch.setattr(vs, "_SEMANTIC_OPTION_THRESHOLD", 0.0)
    assert vs._semantic_option_bonus("anything", "else") <= 70


# --- master kill switch (plan rollback flag) ----------------------------


def test_master_kill_switch_disables_classify(monkeypatch):
    import field_map as fm

    monkeypatch.setenv("FASTFILL_SEMANTIC_CLASSIFY", "1")  # per-feature on
    monkeypatch.setenv("FASTFILL_SEMANTIC_MATCH", "0")  # master off wins
    assert fm._semantic_classify_enabled() is False


def test_master_kill_switch_disables_option_bonus(monkeypatch):
    import verified_select as vs

    monkeypatch.setenv("FASTFILL_SEMANTIC_OPTIONS", "1")
    monkeypatch.setenv("FASTFILL_SEMANTIC_MATCH", "0")
    assert vs._semantic_option_bonus("Master's Degree", "Master Degree") == 0
