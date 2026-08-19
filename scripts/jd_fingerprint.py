#!/usr/bin/env python3
"""High-precision job-description fingerprints for multi-source merge.

Only exact equality of *normalized* JD text (via hash) is used as a merge
signal. Short or empty descriptions never fingerprint, so they never match.
No fuzzy / partial JD similarity — that over-merges different req IDs.
"""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
from typing import Optional

# Substantial JD only — empty/short previews must never collide.
MIN_JD_CHARS = 400
ROOT = Path(__file__).parent.parent
RESUMES_DIR = ROOT / "resumes"

# Volatile listing chrome and standard legal footers are whole-line only.
_BOILERPLATE_LINE_RE = re.compile(
    r"^\s*(?:"
    r"job description|about (?:the )?(?:job|role|position)|"
    r"apply now|click here to apply|"
    r"(?:date )?posted(?:\s+(?:on\s+)?|\s*:\s*).+|"
    r"posted\s+(?:today|yesterday|\d+\s+(?:hours?|days?|weeks?|months?)\s+ago|\d{4}-\d{1,2}-\d{1,2})|"
    r"equal (?:employment )?opportunity employer.*|"
    r"we are an equal opportunity.*|"
    r"eeo(?:/aa)? statement.*"
    r")\s*$",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def description_text(item: dict, *, resumes_dir: Path | None = None) -> str:
    """Prefer jd_full.txt/full listing description; use preview only as fallback."""
    base = Path(resumes_dir) if resumes_dir is not None else RESUMES_DIR
    job_id = str(item.get("id") or "").strip()
    if job_id:
        for filename in ("jd_full.txt", "jd.txt"):
            path = base / job_id / filename
            try:
                if path.is_file():
                    full = path.read_text(errors="replace").strip()
                    if full:
                        return full
            except OSError:
                pass
    candidates: list[str] = []
    for key in ("description", "job_description"):
        raw = item.get(key)
        if raw is None:
            continue
        s = str(raw).strip()
        if s and s.lower() not in ("nan", "none", "null"):
            candidates.append(s)
    return max(candidates, key=len) if candidates else ""


def normalize_jd_text(text) -> str:
    """Strip HTML/volatile footers, lowercase, and collapse whitespace."""
    s = str(text or "")
    if not s:
        return ""
    # Drop zero-width / NBSP noise
    s = s.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    s = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", s)
    s = re.sub(r"(?i)</\s*(?:p|div|li|section|h[1-6])\s*>", "\n", s)
    s = html.unescape(_TAG_RE.sub(" ", s))
    s = "\n".join(
        line for line in s.splitlines()
        if not _BOILERPLATE_LINE_RE.fullmatch(line)
    )
    s = s.lower()
    s = _WS_RE.sub(" ", s).strip()
    return s


def jd_fingerprint(text) -> Optional[str]:
    """SHA-256 of normalized JD, or None if text is too short / empty."""
    norm = normalize_jd_text(text)
    if len(norm) < MIN_JD_CHARS:
        return None
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def item_jd_fingerprint(
    item: dict, *, resumes_dir: Path | None = None
) -> Optional[str]:
    return jd_fingerprint(description_text(item, resumes_dir=resumes_dir))


def same_jd_fingerprint(
    a: dict, b: dict, *, resumes_dir: Path | None = None
) -> bool:
    """True only when both sides have a substantial identical normalized JD."""
    fa = item_jd_fingerprint(a, resumes_dir=resumes_dir)
    if not fa:
        return False
    fb = item_jd_fingerprint(b, resumes_dir=resumes_dir)
    if not fb:
        return False
    return fa == fb
