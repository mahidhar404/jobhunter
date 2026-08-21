"""Heuristics for empty/truncated job descriptions.

Kept separate from write_discovered_jobs so the dashboard can classify JDs
without importing the ATS scrape stack.
"""
from __future__ import annotations

import re

# Line-start headings only. Mid-sentence "mission requirements" is not a JD section.
_JD_SECTION_RE = re.compile(
    r"(?im)^[ \t>#*\-]{0,4}(?:\d+[.)]\s*)?(responsibilit\w*|requirements?|qualifications?|"
    r"what you.?ll do|what we.?re looking|about the role|duties|must have|"
    r"preferred|desired skills?|position requirements?|minimum qualifications?|"
    r"how to apply|benefits)\b",
)
_JD_PREVIEW_SUFFIX_RE = re.compile(
    r"\s*… \[full text in resumes/<id>/jd_full\.txt\]\s*$"
)


def looks_truncated_jd(text: str) -> bool:
    """True when stored/listing text looks like an intro-only posting.

    Typical full JDs run 1,500–3,000+ chars with requirements lists. Lever
    descriptionPlain is often a single paragraph (~400–800 chars) with no
    section headings. Empty is truncated; long complete text is not.
    """
    t = _JD_PREVIEW_SUFFIX_RE.sub("", text or "").strip()
    if not t:
        return True
    if len(t) < 200:
        return True
    if len(t) >= 1800:
        return False
    has_section = bool(_JD_SECTION_RE.search(t))
    if not has_section:
        return True
    ends_mid = not t.rstrip().endswith((".", "!", "?", '"', "'", ")", "]"))
    if len(t) < 1200 and ends_mid:
        return True
    return False
