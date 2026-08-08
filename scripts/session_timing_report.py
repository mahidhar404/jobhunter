#!/usr/bin/env python3
"""Break down where time actually went in a job's agent turn(s).

The plain-script pipeline stages (scout/scrape_ats/dedup/tailor) already
log their own timing (see logs/timing.log). This covers the other half:
the agent's own browser-driven fill turn, where the only record of what
happened is the raw session transcript. Reading that by hand (grepping
timestamps, eyeballing gaps) is exactly the kind of thing to automate
instead of redoing manually every time something feels slow.

Usage:
  python3 session_timing_report.py JOB_ID [--top N]

Finds JOB_ID's session_key in jobs.json, locates its session file(s) under
~/.openclaw/agents/job-hunter/sessions/, and prints every event with the
gap since the previous one, then the N slowest gaps at the end (default
10) - that list is usually the real answer to "what's actually slow."
"""
import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
JOBS_FILE = ROOT / "jobs.json"
# Resolve openclaw: explicit env override → PATH → macOS Homebrew default.
OPENCLAW_BIN = (
    (os.environ.get("JOBHUNTER_OPENCLAW_BIN") or "").strip()
    or shutil.which("openclaw")
    or "/opt/homebrew/bin/openclaw"
)


def find_session_key(job_id: str) -> str:
    data = json.loads(JOBS_FILE.read_text())
    for job in data["jobs"]:
        if job["id"] == job_id:
            return job["session_key"]
    raise SystemExit(f"no job found with id {job_id!r}")


def find_session_files(session_key: str) -> list[str]:
    """Matching by grepping every raw session file for the session_key
    string is unreliable - the string can appear incidentally in read/exec
    output (e.g. PLAYBOOK.md's own example text), producing cross-session
    false matches that corrupt the timing report with unrelated gaps. The
    gateway's own session index has the exact file, so ask it instead."""
    out = subprocess.run(
        [OPENCLAW_BIN, "sessions", "list", "--agent", "job-hunter", "--active", "999999", "--json"],
        capture_output=True, text=True, timeout=15,
    ).stdout
    data = json.loads(out) if out.strip() else {}
    return [s["sessionFile"] for s in data.get("sessions", []) if s.get("key") == session_key and s.get("sessionFile")]


def describe_event(d: dict) -> str:
    msg = d.get("message", {})
    role = msg.get("role") or d.get("type") or "?"
    content = msg.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            t = item.get("type")
            if t == "toolCall":
                parts.append(f"toolCall:{item.get('name')}")
            elif t == "text":
                parts.append(f"text:{item['text'][:50]!r}")
            elif t == "thinking":
                parts.append("thinking")
        if parts:
            return f"{role} [{', '.join(parts)}]"
    return role


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    session_key = find_session_key(args.job_id)
    matching_files = find_session_files(session_key)
    if not matching_files:
        raise SystemExit(f"no session file found for {session_key} (is openclaw reachable?)")

    events = []
    for path in matching_files:
        with open(path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = d.get("timestamp")
                if ts:
                    events.append((ts, d))
    events.sort(key=lambda e: e[0])

    print(f"{len(events)} events across {len(matching_files)} session file(s) for {session_key}\n")

    gaps = []
    prev_ts = None
    for ts, d in events:
        gap_s = 0.0
        if prev_ts:
            fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
            try:
                t1 = datetime.strptime(prev_ts, fmt)
                t2 = datetime.strptime(ts, fmt)
                gap_s = (t2 - t1).total_seconds()
            except ValueError:
                pass
        desc = describe_event(d)
        gap_str = f"+{gap_s:6.1f}s" if gap_s else "        "
        print(f"{ts[11:19]} {gap_str}  {desc}")
        gaps.append((gap_s, ts, desc))
        prev_ts = ts

    print(f"\n--- {args.top} slowest gaps (this is where the time actually went) ---")
    for gap_s, ts, desc in sorted(gaps, reverse=True)[:args.top]:
        print(f"{ts[11:19]}  {gap_s:7.1f}s  before: {desc}")


if __name__ == "__main__":
    main()
