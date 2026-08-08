#!/usr/bin/env python3
"""Single source of truth for PartyRock tailor app URLs.

Dashboard Test Mode ON  → test URL (Ultron-Resume-v3-Testing)
Dashboard Test Mode OFF → real URL (Ultron-Resume-v3)

Used by tailor_resume.py, open_partyrock.sh, and dashboard/server.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "partyrock.json"

# Fallbacks if partyrock.json is missing (keep in sync with that file).
_DEFAULTS = {
    "test": "https://partyrock.aws/u/yo68749/qmkzfuEtp/Ultron-Resume-v3-Testing",
    "real": "https://partyrock.aws/u/yo68749/VLnKjx0N6/Ultron-Resume-v3",
}


def load_partyrock_urls() -> dict[str, str]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULTS)
    out = dict(_DEFAULTS)
    if isinstance(data.get("test"), str) and data["test"].strip():
        out["test"] = data["test"].strip()
    if isinstance(data.get("real"), str) and data["real"].strip():
        out["real"] = data["real"].strip()
    return out


def partyrock_url(*, test_mode: bool = True) -> str:
    urls = load_partyrock_urls()
    return urls["test"] if test_mode else urls["real"]


def partyrock_mode_label(*, test_mode: bool) -> str:
    return "test" if test_mode else "real"


def build_partyrock_input(job_description: str, location: str) -> str:
    """Structured text pasted into PartyRock's single JD input widget."""
    clean_location = (location or "").strip() or "Unknown"
    return (
        f"Location: {clean_location}\n\n"
        "Job Description:\n"
        f"{(job_description or '').strip()}"
    )


def test_mode_from_env(default: bool = True) -> bool:
    """Resolve PartyRock test/real from PARTYROCK_TEST_MODE only (PR-004).

    Do **not** read ``TEST_MODE`` here — that flag is for dashboard/fill
    dummy-vs-real profile data and must not flip the PartyRock app URL.
    Use ``--test-mode`` / ``--real`` CLI flags, or ``PARTYROCK_TEST_MODE``.

    Explicit 0/false/off/no/real → real. Explicit 1/true/on/yes/test → test.
    Unset → default (True = safe Testing app).
    """
    raw = (os.environ.get("PARTYROCK_TEST_MODE") or "").strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "no", "off", "real"):
        return False
    if raw in ("1", "true", "yes", "on", "test"):
        return True
    return default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print PartyRock URL for test/real mode")
    parser.add_argument(
        "--test-mode",
        action="store_true",
        default=None,
        help="Use Testing app URL",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Use Real app URL",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print {mode,url,test,real} as JSON",
    )
    args = parser.parse_args(argv)

    if args.real and args.test_mode:
        print("error: pass only one of --test-mode / --real", file=sys.stderr)
        return 2
    if args.real:
        test_mode = False
    elif args.test_mode:
        test_mode = True
    else:
        test_mode = test_mode_from_env(default=True)

    urls = load_partyrock_urls()
    url = urls["test"] if test_mode else urls["real"]
    mode = partyrock_mode_label(test_mode=test_mode)
    if args.json:
        print(json.dumps({"mode": mode, "url": url, "test": urls["test"], "real": urls["real"]}))
    else:
        print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
