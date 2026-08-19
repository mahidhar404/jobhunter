#!/usr/bin/env python3
"""Stamp company_key on jobs.json from the display company string.

Suffix-normalize only (inc/llc/corp/technologies/…). Does not rewrite the
raw ``company`` field used for display. Idempotent. Dummy-safe: no PII.

Usage:
  python3 scripts/backfill_company_key.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import jobs_lock  # noqa: E402
from jobs_lock import locked_jobs_for_write  # noqa: E402
from text_normalize import backfill_company_keys  # noqa: E402

JOBS_FILE = jobs_lock.JOBS_FILE


def main() -> int:
    changed = 0
    total = 0
    with locked_jobs_for_write() as data:
        jobs = data.get("jobs") or []
        total = len(jobs) if isinstance(jobs, list) else 0
        changed = backfill_company_keys(data)
    print(f"company_key backfill: scanned={total} changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
