#!/usr/bin/env python3
"""Pick a synthetic apartment in the exact city shown in the resume header.

Usage:
  python3 pick_address.py RESUME_PDF_OR_TEX_PATH

Remote/US uses the address bank's documented default (Chicago, IL). Unknown
City, ST pairs are generated as synthetic privacy placeholders and persisted.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FASTFILL_DIR = ROOT / "scripts" / "fastfill"
if str(FASTFILL_DIR) not in sys.path:
    sys.path.insert(0, str(FASTFILL_DIR))

from address_resolver import resolve_address_for_resume  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resume_path")
    parser.add_argument(
        "--location",
        default="",
        help="Job-location fallback if the resume header has no city",
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
