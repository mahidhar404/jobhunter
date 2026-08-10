#!/usr/bin/env python3
"""Semantic matching with a deterministic lexical fallback.

Purpose
-------
Job-application forms phrase the same field a hundred ways ("Preferred first
name", "What should we call you?", "Given name"). Deterministic regex layers
(field_map.classify_layer0/1) catch the common phrasings; this module adds a
*similarity* signal for the paraphrases they miss, and for matching a wanted
value against a dropdown's real option labels.

Design
------
- Two backends behind one API:
    * ``lexical`` (default): fully deterministic, dependency-free. Blends token
      Jaccard with difflib's sequence ratio. Deterministic => safe in tests/CI.
    * ``embed`` (optional): sentence-transformers cosine similarity, enabled only
      when ``FASTFILL_SEMANTIC_EMBED=1`` AND the library + model actually load.
      Any import/load failure silently falls back to lexical, so a machine
      without torch (e.g. a CPython build with no wheel) never breaks.
- This module only ever sees form UI text (labels / option strings) and curated
  exemplars. It never receives applicant PII, and the embedding backend never
  transmits anything off-box (local model only).

Both integration points (classify_field fallback, option scoring) are ON by
default using the deterministic lexical backend, and can be disabled per-feature
(FASTFILL_SEMANTIC_CLASSIFY / FASTFILL_SEMANTIC_OPTIONS) or all at once via the
master FASTFILL_SEMANTIC_MATCH=0. The embedding backend is the opt-in upgrade
(FASTFILL_SEMANTIC_EMBED=1); this module just provides the scores.
"""
from __future__ import annotations

import functools
import os
import re
from difflib import SequenceMatcher

_STOPWORDS = frozenset(
    {
        "a", "an", "the", "of", "for", "to", "in", "on", "at", "your", "you",
        "please", "select", "choose", "enter", "is", "are", "do", "does", "have",
        "what", "which", "and", "or", "with", "this", "that", "field", "optional",
        "required", "if", "any", "we", "us", "my", "i", "me",
    }
)

_WORD = re.compile(r"[a-z0-9]+")


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _tokens(s: str) -> frozenset[str]:
    toks = {t for t in _WORD.findall((s or "").lower()) if t not in _STOPWORDS}
    return frozenset(toks)


def lexical_sim(a: str, b: str) -> float:
    """Deterministic similarity in [0,1]: blend of token Jaccard + char ratio."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = _tokens(a), _tokens(b)
    if ta and tb:
        jac = len(ta & tb) / len(ta | tb)
    else:
        jac = 0.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    # Weight token overlap a bit higher than raw character ratio: field labels
    # share vocabulary ("first name" vs "legal first name") more meaningfully
    # than character order.
    return max(0.0, min(1.0, 0.6 * jac + 0.4 * ratio))


def _embed_enabled() -> bool:
    # Default ON, but self-healing: if sentence-transformers/torch aren't
    # installed or the model can't load (e.g. the 3.14 test venv has no torch
    # wheel, or first-run offline), _embed_model() returns None and every caller
    # transparently uses lexical_sim. Set FASTFILL_SEMANTIC_EMBED=0 to force
    # lexical everywhere.
    if os.environ.get("FASTFILL_SEMANTIC_MATCH", "1") == "0":
        return False
    return os.environ.get("FASTFILL_SEMANTIC_EMBED", "1") != "0"


@functools.lru_cache(maxsize=1)
def _embed_model():
    """Load the sentence-transformers model once, or return None if unavailable.

    Model name is overridable via FASTFILL_SEMANTIC_MODEL (default a small, fast,
    CPU-friendly MiniLM). Any failure (missing lib, no wheel, offline first run)
    returns None and the caller uses lexical_sim.
    """
    if not _embed_enabled():
        return None
    name = os.environ.get(
        "FASTFILL_SEMANTIC_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(name)
    except Exception:
        return None


@functools.lru_cache(maxsize=4096)
def _embed_one(text: str):
    model = _embed_model()
    if model is None:
        return None
    try:
        vec = model.encode([text], normalize_embeddings=True)
        return vec[0]
    except Exception:
        return None


def _cosine_norm(u, v) -> float:
    # vectors are already L2-normalized by encode(normalize_embeddings=True)
    return float(sum(a * b for a, b in zip(u, v)))


def semantic_sim(a: str, b: str) -> float:
    """Similarity in [0,1]; embeddings when enabled+available, else lexical."""
    if _embed_enabled():
        ua, ub = _embed_one(a or ""), _embed_one(b or "")
        if ua is not None and ub is not None:
            return max(0.0, min(1.0, (_cosine_norm(ua, ub) + 1.0) / 2.0))
    return lexical_sim(a, b)


def best_match(query: str, candidates) -> tuple[int, float]:
    """Return (index_of_best_candidate, score). (-1, 0.0) when empty.

    Phase 7 vector-store extension point: for small candidate sets (dropdown
    options, the curated exemplar list) this linear scan over cached embeddings
    is more than fast enough. Only if the answer corpus grows large would a real
    vector store (Chroma / FAISS) pay off — it would slot in behind THIS function
    (swap the loop for an index query) without changing any caller.
    """
    best_i, best_s = -1, 0.0
    for i, cand in enumerate(candidates or []):
        s = semantic_sim(query, str(cand))
        if s > best_s:
            best_i, best_s = i, s
    return best_i, best_s
