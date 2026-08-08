"""Detect "multiple openings" signals in job titles / descriptions.

Used at discovery write time (write_discovered_jobs.py), manual enrich
(dashboard/server.py), and optional backfill so the dashboard can sort /
filter / tag jobs that advertise more than one hire without scanning JD
files on every list poll.

UI-036: this flag is informational only — filter, sort, and the Multi /
Multi-opening tag. It does **not** change fill, tailor, address pick, or
apply behavior.

Phrase matching is case-insensitive. Prefer high-precision hiring
signals over bare "multiple" / "openings" alone.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
RESUMES_DIR = ROOT / "resumes"

# Count must be >= 2 (avoid "Number of Openings Available 1").
_N_GE_2 = r"(?:[2-9]|\d{2,})\+?"

# Ordered for readability; any match is enough.
MULTI_OPENING_RES: list[re.Pattern[str]] = [
    re.compile(p, re.I)
    for p in (
        # Explicit multi-hire language
        r"\bmultiple\s+positions?\b",
        r"\bmultiple\s+openings?\b",
        r"\bhiring\s+(?:for\s+)?multiple\b",
        r"\bwe(?:'re|\s+are)\s+hiring\s+multiple\b",
        r"\brecruiting\s+for\s+multiple\s+positions?\b",
        r"\bfill(?:ing)?\s+multiple\b.{0,60}\bopenings?\b",
        r"\bseveral\s+(?:positions?|openings?|roles?)\b",
        r"\bmore\s+than\s+one\s+(?:position|opening|role)\b",
        # "multiple roles" but not "multiple roles and responsibilities"
        r"\bmultiple\s+roles?\b(?!\s+and\s+responsibilities)",
        # Numeric headcount
        rf"\b{_N_GE_2}\s+openings?\b",
        rf"\b{_N_GE_2}\s+positions?\s+available\b",
        rf"\bnumber\s+of\s+openings?\s*(?:available)?\s*[:\-]?\s*{_N_GE_2}\b",
        # Title / bracket cues: (Multiple roles), [Multiple Positions Available]
        r"[\(\[]\s*multiple\s+(?:positions?|roles?|openings?)\b",
    )
]


def detect_multi_opening(
    *,
    title: str | None = None,
    description: str | None = None,
) -> bool:
    """True if title/description text signals multiple openings/hires."""
    blob = f"{title or ''}\n{description or ''}".strip()
    if not blob:
        return False
    return any(rx.search(blob) for rx in MULTI_OPENING_RES)


def description_text_for_job(job: dict, resumes_dir: Path | None = None) -> str:
    """Prefer resumes/<id>/jd_full.txt; fall back to jobs.json preview."""
    resumes = resumes_dir or RESUMES_DIR
    job_id = job.get("id") or ""
    if job_id:
        full_path = resumes / job_id / "jd_full.txt"
        if full_path.is_file():
            try:
                return full_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    preview = job.get("job_description") or ""
    return preview if isinstance(preview, str) else ""


def compute_multi_opening(job: dict, resumes_dir: Path | None = None) -> bool:
    """Compute flag from title + best available JD text on disk / record."""
    return detect_multi_opening(
        title=job.get("title") or "",
        description=description_text_for_job(job, resumes_dir),
    )


def apply_multi_opening_flag(job: dict, resumes_dir: Path | None = None) -> bool:
    """Set job['multi_opening']. Returns True if the stored value changed."""
    new_val = compute_multi_opening(job, resumes_dir)
    old = job.get("multi_opening")
    job["multi_opening"] = new_val
    return old != new_val


def backfill_multi_opening_flags(
    data: dict,
    *,
    resumes_dir: Path | None = None,
    only_missing: bool = False,
) -> tuple[int, int]:
    """Scan jobs and set multi_opening.

    Returns (changed_count, true_count).
    If only_missing, skip jobs that already have a boolean multi_opening key.
    """
    changed = 0
    true_count = 0
    for job in data.get("jobs") or []:
        if only_missing and isinstance(job.get("multi_opening"), bool):
            if job["multi_opening"]:
                true_count += 1
            continue
        if apply_multi_opening_flag(job, resumes_dir):
            changed += 1
        if job.get("multi_opening"):
            true_count += 1
    return changed, true_count


def main() -> None:
    sys.path.insert(0, str(Path(__file__).parent))
    from jobs_lock import locked_jobs_for_write

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Only compute for jobs lacking a boolean multi_opening flag",
    )
    args = parser.parse_args()

    with locked_jobs_for_write() as data:
        changed, true_count = backfill_multi_opening_flags(
            data, only_missing=args.only_missing
        )
        n = len(data.get("jobs") or [])
    print(f"scanned={n} changed={changed} multi_opening=true={true_count}")


if __name__ == "__main__":
    main()
