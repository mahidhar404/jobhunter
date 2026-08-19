#!/usr/bin/env python3
"""Shared text-normalization helpers for job dedup/matching.

These pure functions were previously copied verbatim across several scripts
(dedup_jobs.py, dedup_listings.py, write_discovered_jobs.py, tracker.py).
Consolidated here so the normalization stays consistent across every caller.
"""
from __future__ import annotations

import re


def normalize_company(name) -> str:
    name = str(name or "").lower()
    name = re.sub(r"\b(inc|llc|corp|corporation|ltd|co|company|group|technologies|technology)\b\.?", "", name)
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name


def stamp_company_key(job: dict) -> bool:
    """Set job['company_key'] from the display company string.

    Never rewrites ``company``. Returns True if the stored key changed.
    """
    if not isinstance(job, dict):
        return False
    company = job.get("company")
    if not str(company or "").strip():
        return False
    desired = normalize_company(company)
    if not desired:
        return False
    if job.get("company_key") == desired:
        return False
    job["company_key"] = desired
    return True


def backfill_company_keys(data: dict) -> int:
    """Stamp missing/stale company_key on every job. Returns changed count."""
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return 0
    changed = 0
    for job in jobs:
        if isinstance(job, dict) and stamp_company_key(job):
            changed += 1
    return changed


def normalize_title(title) -> str:
    title = str(title or "").lower()
    title = re.sub(r"[^a-z0-9 ]+", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title
