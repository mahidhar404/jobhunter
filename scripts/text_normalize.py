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


def normalize_title(title) -> str:
    title = str(title or "").lower()
    title = re.sub(r"[^a-z0-9 ]+", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title
