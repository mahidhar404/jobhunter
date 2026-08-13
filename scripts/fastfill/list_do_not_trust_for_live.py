#!/usr/bin/env python3
"""List gym cases + unit modules that must NOT be used for live headed signoff.

See GYM_VS_LIVE.md. Dummy-only; never submit.

  skyvern_runtime/venv/bin/python scripts/fastfill/list_do_not_trust_for_live.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = HERE / "gym" / "ats" / "cases"

# Unit modules that look like coverage but never prove live Workday/Ashby/Lever.
DO_NOT_TRUST_UNITS = [
    ("test_workday_search_select.py", "source-inspect fiber strings; no browser"),
    ("test_multipage via adversarial", "workday_multipage_to_review toy wizard"),
    ("test_workday_education_fos_chip.py", "pre-baked FoS chips; no fiber/async"),
    ("test_workday_autofill_skip.py", "static NXP phone HTML"),
    ("test_phone_country_code.py gym path", "pre-baked US(+1) chip"),
    ("test_workday_address_state.py", "static Illinois fixture; no live pack"),
    ("detection_matrix.py / adversarial.py", "gym green ≠ live_pass"),
    ("regression_gates --tier1 gym half", "gym_pass only; not live_pass"),
    ("reliability_gate --skip-run", "re-scores old artifact; not a new live run"),
]


def main() -> int:
    print("=== DO NOT TRUST FOR LIVE SIGNOFF ===\n")
    print("Live truth: flight_recorder + headed reliability_gate (no --skip-run).")
    print("Doc: scripts/fastfill/GYM_VS_LIVE.md\n")

    print("--- Gym cases (all live_signoff=false) ---")
    rows: list[tuple[str, str, str]] = []
    if CASES.is_dir():
        for d in sorted(CASES.iterdir()):
            meta_path = d / "meta.json"
            if not meta_path.is_file():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            fid = str(meta.get("fidelity") or "unset")
            signoff = meta.get("live_signoff")
            rows.append((fid, d.name, f"live_signoff={signoff}"))
    by_fid = {"low": [], "medium": [], "high": [], "unset": []}
    for fid, name, note in rows:
        by_fid.setdefault(fid, []).append((name, note))
    for fid in ("low", "medium", "high", "unset"):
        items = by_fid.get(fid) or []
        if not items:
            continue
        print(f"\n[{fid}] ({len(items)})")
        for name, note in items:
            print(f"  {name}  ({note})")

    high = by_fid.get("high") or []
    if not high:
        print("\n[high] (0) — none; offline gym never reaches live Workday fidelity.")

    print("\n--- Unit / gate modules ---")
    for name, why in DO_NOT_TRUST_UNITS:
        print(f"  {name}")
        print(f"    → {why}")

    print("\n--- Honest live confidence ---")
    print("  flight_recorder.py          per-action live audit (separate track)")
    print("  reliability_gate.py         headed run → live_pass / reached_review")
    print("  RELIABILITY_STATUS.md       latest blunt live verdict")
    return 0


if __name__ == "__main__":
    sys.exit(main())
