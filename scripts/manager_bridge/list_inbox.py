#!/usr/bin/env python3
"""List pending tasks in manager_bridge/inbox/."""

from __future__ import annotations

import argparse
import json
import sys

from _common import INBOX, IN_PROGRESS, OUTBOX, load_task, list_task_files


def main() -> int:
    parser = argparse.ArgumentParser(description="List manager_bridge inbox tasks")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON array instead of human table",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include in_progress tasks",
    )
    args = parser.parse_args()

    rows = []
    for p in list_task_files(INBOX):
        task = load_task(p)
        rows.append(
            {
                "id": task.get("id", p.stem),
                "priority": task.get("priority", "?"),
                "title": task.get("title", p.stem),
                "file": str(p),
                "status": "pending",
            }
        )

    if args.all:
        for p in list_task_files(IN_PROGRESS):
            task = load_task(p)
            tid = task.get("id", p.stem)
            has_result = (OUTBOX / f"RESULT-{tid}.md").is_file()
            rows.append(
                {
                    "id": tid,
                    "priority": task.get("priority", "?"),
                    "title": task.get("title", p.stem),
                    "file": str(p),
                    "status": "in_progress",
                    "has_result": has_result,
                }
            )

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print("No pending tasks in inbox/")
        return 0

    print(f"{'PRI':<4} {'STATUS':<12} {'ID':<45} TITLE")
    print("-" * 100)
    for r in rows:
        print(f"{r['priority']:<4} {r['status']:<12} {r['id']:<45} {r['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
