#!/usr/bin/env python3
"""High-precision job-description fingerprints for multi-source merge.

Only exact equality of *normalized* JD text (via hash) is used as a merge
signal. Short or empty descriptions never fingerprint, so they never match.
No fuzzy / partial JD similarity — that over-merges different req IDs.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

# Substantial JD only — empty/short previews must never collide.
MIN_JD_CHARS = 400

# Trivial ATS / aggregator boilerplate often prepended/appended; stripping is
# best-effort and conservative (whole-line / short phrases only).
_BOILERPLATE_LINE_RE = re.compile(
    r"(?im)^\s*(?:"
    r"job description|about (?:the )?(?:job|role|position)|"
    r"apply now|click here to apply|"
    r"equal opportunity employer.*|"
    r"we are an equal opportunity.*"
    r")\s*$"
)
_WS_RE = re.compile(r"\s+")


def description_text(item: dict) -> str:
    """Prefer full listing description, then jobs.json preview field."""
    for key in ("description", "job_description"):
        raw = item.get(key)
        if raw is None:
            continue
        s = str(raw).strip()
        if s and s.lower() not in ("nan", "none", "null"):
            return s
    return ""


def normalize_jd_text(text) -> str:
    """Lowercase, collapse whitespace, drop a few trivial boilerplate lines."""
    s = str(text or "")
    if not s:
        return ""
    # Drop zero-width / NBSP noise
    s = s.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    s = _BOILERPLATE_LINE_RE.sub(" ", s)
    s = s.lower()
    s = _WS_RE.sub(" ", s).strip()
    return s


def jd_fingerprint(text) -> Optional[str]:
    """SHA-256 of normalized JD, or None if text is too short / empty."""
    norm = normalize_jd_text(text)
    if len(norm) < MIN_JD_CHARS:
        return None
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def item_jd_fingerprint(item: dict) -> Optional[str]:
    return jd_fingerprint(description_text(item))


def same_jd_fingerprint(a: dict, b: dict) -> bool:
    """True only when both sides have a substantial identical normalized JD."""
    fa = item_jd_fingerprint(a)
    if not fa:
        return False
    fb = item_jd_fingerprint(b)
    if not fb:
        return False
    return fa == fb
