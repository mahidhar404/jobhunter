#!/usr/bin/env python3
"""Update manager_bridge/STATUS.md heartbeat."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from _common import (
    BRIDGE_ROOT,
    INBOX,
    IN_PROGRESS,
    OUTBOX,
    STATUS_FILE,
    ensure_dirs,
    iso_now,
    list_task_files,
)


def chrome_count() -> str:
    """CHR2-006 / CHR3-008: count fill CfT mains only (not Helpers / UI / PartyRock)."""
    try:
        r = subprocess.run(
            ["pgrep", "-lf", "Google Chrome for Testing"],
            capture_output=True,
            text=True,
        )
        exclude = (
            "Helper",
            "crashpad",
            "dashboard_ui_profile",
            "--app=http://127.0.0.1:8787",
            "openclaw/user-data",
            "--remote-debugging-port=18800",
        )
        n = 0
        for ln in r.stdout.splitlines():
            if not ln.strip():
                continue
            if any(m in ln for m in exclude):
                continue
            if "MacOS/Google Chrome for Testing" not in ln and "/chrome " not in ln:
                continue
            n += 1
        return str(n)
    except Exception:
        return "?"


def regression_gates() -> str:
    repo = Path(__file__).resolve().parents[2]
    venv_py = repo / "skyvern_runtime" / "venv" / "bin" / "python"
    script = repo / "scripts" / "fastfill" / "regression_gates.py"
    if not venv_py.is_file() or not script.is_file():
        return "SKIP (venv/script missing)"
    try:
        r = subprocess.run([str(venv_py), str(script)], capture_output=True, text=True, timeout=120)
        return "PASS" if r.returncode == 0 else "FAIL"
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    except Exception as e:
        return f"ERR ({e})"


def render_status(*, note: str, gates: str | None, chrome: str | None) -> str:
    pending = len(list_task_files(INBOX))
    active = len(list_task_files(IN_PROGRESS))
    results = len([p for p in OUTBOX.glob("RESULT-*.md") if p.is_file()])
    gates_line = gates if gates is not None else "(unchanged — pass --check-gates)"
    chrome_line = chrome if chrome is not None else "(unchanged — pass --check-chrome)"

    body = f"""# Manager bridge — STATUS

**Updated:** {iso_now()}  
**Bridge:** `{BRIDGE_ROOT}`

## Queue snapshot

| Metric | Count |
|--------|------:|
| Inbox pending | {pending} |
| In progress | {active} |
| Outbox results | {results} |

## Health

| Check | Value |
|-------|-------|
| Chrome processes (pgrep) | {chrome_line} |
| regression_gates.py | {gates_line} |

## Active task

_(Cursor updates on ack; Manager reads before posting new work)_

## Ten-unseen pointer

- Queue log: `skyvern_runtime/real_job_results/TEN_UNSEEN_RUN.md`
- Bug fix log: `skyvern_runtime/real_job_results/BUG_FIX_STATUS.md`
- Job 3 Airwallex: location fix landed; optional zip polish
- Job 4+: not started — see `TEN_UNSEEN_CANDIDATES.json`

## Latest note

{note.strip() or "(none)"}

## Manager preflight (every cycle)

1. Read this file + latest `outbox/RESULT-*.md`
2. Read `TEN_UNSEEN_RUN.md` + `BUG_FIX_STATUS.md`
3. Confirm Chrome cap before headed fills
4. Post one focused task to `inbox/` — avoid parallel P0s
"""
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description="Update STATUS.md heartbeat")
    parser.add_argument("--note", default="", help="Freeform note for Latest note section")
    parser.add_argument("--check-gates", action="store_true", help="Run regression_gates.py")
    parser.add_argument("--check-chrome", action="store_true", help="Count Chrome via pgrep")
    args = parser.parse_args()

    ensure_dirs()
    gates = regression_gates() if args.check_gates else None
    chrome = chrome_count() if args.check_chrome else None

    STATUS_FILE.write_text(
        render_status(note=args.note, gates=gates, chrome=chrome),
        encoding="utf-8",
    )
    print(STATUS_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
