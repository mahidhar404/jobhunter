"""Tests for in-progress applied_address backfill."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "scripts" / "fastfill"))

import address_resolver as ar  # noqa: E402
import backfill_in_progress_applied_addresses as bfi  # noqa: E402
import jobs_lock as jl  # noqa: E402
from backfill_in_progress_applied_addresses import IN_PROGRESS_STATUSES, backfill  # noqa: E402


def test_in_progress_statuses_include_resume_ready():
    assert "resume_ready" in IN_PROGRESS_STATUSES


def test_backfill_overwrites_stale_generated_address(tmp_path: Path) -> None:
    jobs_path = tmp_path / "jobs.json"
    resume_dir = tmp_path / "resumes" / "job-1"
    resume_dir.mkdir(parents=True)
    resume_dir.joinpath("resume.tex").write_text(
        r"""
    \begin{center}
    {\LARGE Test Dummy}\\
    405-555-0100 | San Jose, CA | dummy@example.test
    \end{center}
    """,
        encoding="utf-8",
    )
    bank = tmp_path / "addresses.json"
    bank.write_text(
        json.dumps(
            {
                "remote_default": {"city": "Chicago", "state": "IL"},
                "addresses": [
                    {
                        "city": "San Jose",
                        "state": "CA",
                        "zip": "95110",
                        "street": "188 West Saint James Street",
                        "unit": "Unit 2E",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    jobs_path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "job-1",
                        "status": "resume_ready",
                        "resume_path": "resumes/job-1/resume.tex",
                        "applied_address": "7124 Oak Lane, Unit 2E, San Jose, CA 90871",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with (
        mock.patch.object(ar, "DEFAULT_BANK_PATH", bank),
        mock.patch.object(jl, "JOBS_FILE", jobs_path),
        mock.patch.object(jl, "LOCK_FILE", jobs_path.with_suffix(".json.lock")),
        mock.patch.object(bfi, "ROOT", tmp_path),
        mock.patch.object(bfi, "RESUMES_DIR", tmp_path / "resumes"),
    ):
        stats = backfill(dry_run=False)

    assert stats["backfilled"] == 1
    data = json.loads(jobs_path.read_text(encoding="utf-8"))
    addr = data["jobs"][0]["applied_address"]
    assert "95110" in addr
    assert "90871" not in addr


if __name__ == "__main__":
    import tempfile

    test_in_progress_statuses_include_resume_ready()
    with tempfile.TemporaryDirectory() as td:
        test_backfill_overwrites_stale_generated_address(Path(td))
    print("OK test_backfill_in_progress_applied_addresses")
