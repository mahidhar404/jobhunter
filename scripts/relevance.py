"""Shared title-relevance keywords + token-safe matching for discovery.

Single source of truth used by scrape_ats.py (pre-filter while fetching)
and dedup_listings.py (qualify step). Keep this mechanical — no LLM.
"""
from __future__ import annotations

import re

# Deliberately broad across the whole data/AI/ML circle — data analysis,
# data cleaning, and data engineering are all in-scope alongside ML/AI,
# not just the narrower "engineer"/"scientist" titles.
RELEVANT_KEYWORDS = [
    # Machine learning / AI - engineering & research
    "machine learning", "ml engineer", "mle", "ml ops", "mlops",
    "ml platform", "ml infrastructure", "ml research", "ai engineer",
    "ai infrastructure", "artificial intelligence", "ai researcher",
    "ai/ml", "applied scientist", "research scientist", "research engineer",
    "deep learning", "reinforcement learning", "computer vision",
    "nlp", "natural language processing", "llm", "llms", "generative ai", "genai",
    "prompt engineer", "conversational ai", "foundation model",
    "recommender system", "recommendation system", "ranking engineer",
    "search relevance", "speech recognition", "speech scientist",
    "ai safety", "responsible ai", "feature engineering",
    "perception engineer", "model training", "model deployment",
    "predictive analytics", "predictive model", "time series",
    "anomaly detection", "data annotation", "data labeling",

    # Data science / analysis
    "data scientist", "data science", "data analyst", "data analysis",
    "data analytics", "analytics engineer", "statistician",
    "business intelligence",

    # Data engineering / infrastructure
    "data engineer", "data engineering", "data platform",
    "data infrastructure", "data pipeline", "data architect",
    "data warehouse", "data lake", "data modeling", "database engineer",
    "etl", "elt", "dataops", "big data", "data cleaning", "data quality",
    "data wrangling",
]

# Precompiled once: substring matching caused live FPs like keyword "llm"
# matching inside "EnroLLment" / "FulfiLLment". Require a token boundary
# on both sides (non-alnum or string edge). Multi-word keywords allow
# flexible whitespace/hyphen/slash separators between tokens.
_KEYWORD_RES: list[re.Pattern[str]] = []


def _compile_keyword(kw: str) -> re.Pattern[str]:
    parts = re.split(r"[\s/]+", kw.strip().lower())
    parts = [re.escape(p) for p in parts if p]
    if not parts:
        return re.compile(r"(?!)")  # never matches
    body = r"[\s/_-]+".join(parts)
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])", re.I)


for _kw in RELEVANT_KEYWORDS:
    _KEYWORD_RES.append(_compile_keyword(_kw))


def is_relevant(title: str | None) -> bool:
    """True if title contains at least one relevant keyword as a whole token."""
    t = str(title or "")
    if not t.strip():
        return False
    return any(p.search(t) for p in _KEYWORD_RES)
