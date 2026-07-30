#!/usr/bin/env python3
"""Pick a placeholder mailing address, anchored on the city PartyRock put
in the compiled resume's header - pure mechanical work (regex extraction
+ metro matching + random pick, no judgment call), so it runs as a script
instead of costing agent tokens to read the whole addresses.json pool
and do the same lookup itself every single fill turn.

Usage:
  python3 pick_address.py RESUME_TEX_PATH
    Prints one JSON object - {"line1", "city", "state", "zip",
    "anchor_city"} - to stdout. Exits 1 with an error on stderr if no
    "City, ST" pattern is found in the resume header, or no matching/
    fallback address entry exists.
"""
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
ADDRESSES_FILE = ROOT / "addresses.json"

# Matches the "phone | City, ST | email" line every compiled resume has.
HEADER_RE = re.compile(r"\d{3}-\d{3}-\d{4}\s*\|\s*([^|]+?)\s*\|")


def extract_city_state(tex_text: str) -> tuple[str, str] | None:
    m = HEADER_RE.search(tex_text)
    if not m:
        return None
    raw = m.group(1).strip()
    if "," not in raw:
        return None
    city, state = (p.strip() for p in raw.split(",", 1))
    return city, state


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: pick_address.py RESUME_TEX_PATH", file=sys.stderr)
        sys.exit(1)

    tex_path = Path(sys.argv[1])
    if not tex_path.exists():
        print(f"error: {tex_path} does not exist", file=sys.stderr)
        sys.exit(1)

    found = extract_city_state(tex_path.read_text())
    if not found:
        print("error: could not find a 'City, ST' pattern in the resume header", file=sys.stderr)
        sys.exit(1)
    anchor_city, anchor_state = found

    pool = json.loads(ADDRESSES_FILE.read_text())
    entries = pool.get("addresses", [])

    metro = None
    for e in entries:
        if e.get("city", "").strip().lower() == anchor_city.lower():
            metro = e.get("metro")
            break

    candidates = []
    if metro:
        candidates = [
            e for e in entries
            if e.get("metro") == metro and e.get("city", "").strip().lower() != anchor_city.lower()
        ]
    if not candidates:
        candidates = [e for e in entries if e.get("state", "").strip().lower() == anchor_state.lower()]
    if not candidates:
        print(f"error: no matching metro or state entry found for {anchor_city}, {anchor_state}", file=sys.stderr)
        sys.exit(1)

    pick = dict(random.choice(candidates))
    pick.pop("metro", None)
    pick["anchor_city"] = f"{anchor_city}, {anchor_state}"
    print(json.dumps(pick, indent=2))


if __name__ == "__main__":
    main()
