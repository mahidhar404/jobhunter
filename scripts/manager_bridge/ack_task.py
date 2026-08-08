#!/usr/bin/env python3
"""Acknowledge a task: move inbox/ -> in_progress/."""

from __future__ import annotations

import argparse
import re
import sys

from _common import INBOX, IN_PROGRESS, ensure_dirs, find_task_file, iso_now


def patch_status_md(content: str, task_id: str, title: str) -> str:
    line = f"- **{iso_now()}** — ACK `{task_id}` — {title}"
    marker = "## Active task"
    if marker in content:
        return content.replace(marker, f"{marker}\n{line}", 1)
    return content + f"\n{marker}\n{line}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Acknowledge inbox task (move to in_progress)")
    parser.add_argument("task_id", help="Task id or inbox filename stem")
    parser.add_argument("--note", default="", help="Optional ack note appended to frontmatter")
    parser.add_argument("--update-status", action="store_true", help="Append to STATUS.md Active task")
    args = parser.parse_args()

    ensure_dirs()
    task_id = args.task_id
    if not task_id.startswith("TASK-"):
        task_id = re.sub(r"\.(md|json)$", "", task_id)

    src = find_task_file(task_id, INBOX)
    if not src:
        print(f"Task not found in inbox/: {task_id}", file=sys.stderr)
        return 1

    dest = IN_PROGRESS / src.name
    if dest.exists():
        print(f"Already in in_progress/: {dest}", file=sys.stderr)
        return 1

    text = src.read_text(encoding="utf-8")
    if args.note:
        text = text.replace("status: pending", f"status: in_progress\nack_note: {args.note}", 1)
    else:
        text = text.replace("status: pending", "status: in_progress", 1)
    dest.write_text(text, encoding="utf-8")
    src.unlink()
    print(dest)

    if args.update_status:
        from _common import STATUS_FILE, load_task

        task = load_task(dest)
        if STATUS_FILE.is_file():
            STATUS_FILE.write_text(
                patch_status_md(STATUS_FILE.read_text(encoding="utf-8"), task_id, task.get("title", "")),
                encoding="utf-8",
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
