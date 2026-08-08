#!/usr/bin/env python3
"""Post a task to manager_bridge/inbox/ (markdown or JSON)."""

from __future__ import annotations

import argparse
import json
import sys

from _common import (
    INBOX,
    ensure_dirs,
    iso_now,
    make_task_id,
    render_task_md,
    slugify,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Post a task to manager_bridge inbox")
    parser.add_argument("--title", required=True, help="Short task title")
    parser.add_argument("--priority", default="P1", choices=["P0", "P1", "P2"])
    parser.add_argument("--context", default="", help="Context paragraph")
    parser.add_argument("--context-file", type=str, help="Read context from file")
    parser.add_argument(
        "--acceptance",
        action="append",
        default=[],
        dest="acceptance_criteria",
        help="Acceptance criterion (repeatable)",
    )
    parser.add_argument(
        "--constraint",
        action="append",
        default=[],
        dest="constraints",
        help="Constraint (repeatable)",
    )
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        dest="files_to_touch",
        help="File path to touch (repeatable)",
    )
    parser.add_argument("--done-when", default="", help="Done-when description")
    parser.add_argument("--id", dest="task_id", help="Override task id")
    parser.add_argument(
        "--format",
        choices=["md", "json"],
        default="md",
        help="Output format (default: md)",
    )
    parser.add_argument("--created-by", default="claude-manager")
    args = parser.parse_args()

    ensure_dirs()

    context = args.context
    if args.context_file:
        from pathlib import Path

        context = Path(args.context_file).read_text(encoding="utf-8").strip()

    task_id = args.task_id or make_task_id(args.title)
    task = {
        "id": task_id,
        "priority": args.priority,
        "title": args.title,
        "context": context,
        "acceptance_criteria": args.acceptance_criteria or ["Executor documents outcome in outbox/"],
        "constraints": args.constraints
        or [
            "Dummy profile + fixture resume only — never profile.json PII",
            "Never click Submit / final Apply",
            "Max 1 Chrome (pgrep before headed fill)",
            "EEO via DeepSeek + DUMMY_PROFILE only",
            "CAPTCHA: pause for human, do not solve",
        ],
        "files_to_touch": args.files_to_touch,
        "done_when": args.done_when or "Result posted to outbox/ with verdict and artifacts",
        "created_at": iso_now(),
        "created_by": args.created_by,
    }

    slug = slugify(args.title)
    if args.format == "json":
        out = INBOX / f"{task_id}.json"
        out.write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    else:
        out = INBOX / f"{task_id}.md"
        out.write_text(render_task_md(task), encoding="utf-8")

    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
