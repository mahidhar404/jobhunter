#!/usr/bin/env python3
"""Pick a synthetic apartment in the exact city shown in the resume header.

Usage:
  python3 pick_address.py RESUME_PDF_OR_TEX_PATH [--location LOC] [--job-id ID]

Remote/US uses the address bank's documented default (Chicago, IL). Unknown
City, ST pairs are generated as synthetic privacy placeholders and persisted.

Apartment choice is deterministic when --job-id is set (seeded RNG); otherwise
the first bank match in stable sort order is used (never SystemRandom).
"""
import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FASTFILL_DIR = ROOT / "scripts" / "fastfill"
if str(FASTFILL_DIR) not in sys.path:
    sys.path.insert(0, str(FASTFILL_DIR))

from address_resolver import resolve_address_for_resume  # noqa: E402


def address_rng_for_job(job_id: str | None) -> random.Random:
    """Deterministic RNG for apartment picks (same job_id → same apartment)."""
    if job_id:
        digest = hashlib.sha256(f"job-address:{job_id}".encode()).hexdigest()
        return random.Random(int(digest[:16], 16))
    # No job id: sorted-first behavior via Random(0) + resolver still using choice;
    # prefer a fixed seed so repeats are stable within a process.
    return random.Random(0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resume_path")
    parser.add_argument(
        "--location",
        default="",
        help="Job-location fallback if the resume header has no city",
    )
    parser.add_argument(
        "--job-id",
        default="",
        help="Seed apartment pick deterministically for this job",
    )
    args = parser.parse_args()
    tex_path = Path(args.resume_path)
    if not tex_path.exists():
        print(f"error: {tex_path} does not exist", file=sys.stderr)
        sys.exit(1)

    try:
        pick = resolve_address_for_resume(
            tex_path,
            fallback_location=args.location,
            rng=address_rng_for_job(args.job_id or None),
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    pick = dict(pick)
    pick["line1"] = ", ".join(
        part for part in (pick.get("street"), pick.get("unit")) if part
    )
    pick["anchor_city"] = f"{pick['city']}, {pick['state']}"
    print(json.dumps(pick, indent=2))


if __name__ == "__main__":
    main()
