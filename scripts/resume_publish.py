#!/usr/bin/env python3
"""Publish a job's resume PDF into resumes/by_company/ for Finder / Command Center.

Desktop ``Command Center/Documents/Resumes`` is a symlink to
``resumes/by_company/``. Tracker and the dashboard share this helper so naming
never drifts:

  <sanitize_filename(Company)>_resume_<file_id>.pdf

``file_id`` is one persistent 5-digit number per job (same as tracker.py /
PLAYBOOK), stored on the job record. Re-publishing the same job overwrites
that job's existing by_company file rather than minting a new number.
"""
from __future__ import annotations

import random
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BY_COMPANY_DIR = ROOT / "resumes" / "by_company"
FILE_ID_DIGITS = 5


def sanitize_filename(name) -> str:
    """Strip path-illegal chars; keep human-readable company text (tracker)."""
    name = re.sub(r'[\\/:*?"<>|]', "", str(name or "")).strip()
    return name or "company"


def ensure_file_id(job: dict, existing_ids: set[str] | None = None) -> str:
    """Return job['file_id'], minting a unique 5-digit id if missing.

    Mutates ``job`` in place. ``existing_ids`` should be every file_id already
    used across jobs.json when minting; safe to pass None in tests.
    """
    existing = job.get("file_id")
    if existing:
        return str(existing)
    published_name = Path(
        str(job.get("resume_by_company_path") or "")
    ).name
    published_match = re.fullmatch(r".+_resume_(\d{5})\.pdf", published_name)
    if published_match:
        recovered = published_match.group(1)
        if recovered not in set(existing_ids or ()):
            job["file_id"] = recovered
            return recovered
    taken = set(existing_ids or ())
    max_n = 10 ** FILE_ID_DIGITS
    for _ in range(200):
        candidate = f"{random.randint(0, max_n - 1):0{FILE_ID_DIGITS}d}"
        if candidate not in taken:
            job["file_id"] = candidate
            return candidate
    raise RuntimeError("could not find a free 5-digit file_id after 200 tries")


def by_company_resume_path(
    company: str,
    file_id: str,
    *,
    by_company_dir: Path | None = None,
) -> Path:
    """Canonical dest: ``<Company>_resume_<ID>.pdf``."""
    dest_dir = Path(by_company_dir) if by_company_dir is not None else BY_COMPANY_DIR
    return dest_dir / f"{sanitize_filename(company)}_resume_{file_id}.pdf"


def conventional_resume_filename(job: dict | None) -> str:
    """Return the shared ``Company_resume_12345.pdf`` display/upload name.

    Prefer the persisted published path because it is the exact stable filename
    shown in Command Center. Fall back to deriving it only when the job already
    has its persistent file id; this helper never allocates ids.
    """
    if not isinstance(job, dict):
        return ""
    published = str(job.get("resume_by_company_path") or "").strip()
    if published:
        name = Path(published).name
        if re.fullmatch(r".+_resume_\d{5}\.pdf", name):
            return name
    file_id = str(job.get("file_id") or "").strip()
    if not file_id:
        return ""
    return by_company_resume_path(
        str(job.get("company") or "company"),
        file_id,
    ).name


def _resolve_existing_dest(
    job: dict,
    by_company_dir: Path,
    *,
    root: Path,
) -> Path | None:
    """Reuse the path already published for this job (stable name per job_id)."""
    rel = (job.get("resume_by_company_path") or "").strip()
    if not rel:
        return None
    p = Path(rel)
    if not p.is_absolute():
        p = root / p
    # Only accept paths under by_company (ignore stale absolute paths elsewhere).
    try:
        p.resolve().relative_to(by_company_dir.resolve())
    except ValueError:
        # Fall back to basename under by_company if metadata pointed elsewhere.
        return by_company_dir / Path(rel).name
    return p


def publish_resume_to_by_company(
    job: dict,
    pdf_path: Path | str,
    *,
    by_company_dir: Path | None = None,
    existing_file_ids: set[str] | None = None,
    root: Path | None = None,
    company: str | None = None,
) -> Path:
    """Copy ``pdf_path`` into by_company with tracker naming; mutate ``job``.

    Idempotent per job: if ``resume_by_company_path`` / ``file_id`` already
    exist, overwrites that same dest instead of allocating a new number.

    Sets ``job["file_id"]`` and ``job["resume_by_company_path"]`` (repo-relative
    when possible). Returns the destination Path.
    """
    src = Path(pdf_path)
    if not src.is_file():
        raise FileNotFoundError(f"resume PDF not found: {src}")
    if src.suffix.lower() != ".pdf":
        raise ValueError(f"by_company publish requires a .pdf, got {src.suffix!r}")

    dest_dir = Path(by_company_dir) if by_company_dir is not None else BY_COMPANY_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(root) if root is not None else ROOT

    file_id = ensure_file_id(job, existing_file_ids)
    dest = _resolve_existing_dest(job, dest_dir, root=repo_root)
    conventional = bool(
        dest is not None
        and re.fullmatch(rf".+_resume_{re.escape(file_id)}\.pdf", dest.name)
    )
    if not conventional:
        co = company if company is not None else (job.get("company") or "company")
        dest = by_company_resume_path(co, file_id, by_company_dir=dest_dir)

    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)

    try:
        rel = str(dest.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        rel = str(dest)
    job["resume_by_company_path"] = rel
    return dest
