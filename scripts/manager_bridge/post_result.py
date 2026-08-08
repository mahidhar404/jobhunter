#!/usr/bin/env python3
"""Post a result to manager_bridge/outbox/."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import OUTBOX, ensure_dirs, iso_now, find_task_file, INBOX, IN_PROGRESS, ARCHIVE


def render_result(
    *,
    task_id: str,
    verdict: str,
    summary: str,
    files_changed: list[str],
    artifacts: list[str],
    blockers: list[str],
    next_tasks: list[str],
    executor: str,
) -> str:
    lines = [
        "---",
        f"task_id: {task_id}",
        f"verdict: {verdict}",
        f"posted_at: {iso_now()}",
        f"executor: {executor}",
        "---",
        "",
        f"# Result: {task_id}",
        "",
        "## Verdict",
        verdict,
        "",
        "## Summary",
        summary.strip(),
        "",
        "## Files changed",
    ]
    for f in files_changed:
        lines.append(f"- {f}")
    if not files_changed:
        lines.append("- (none)")
    lines.extend(["", "## Artifacts"])
    for a in artifacts:
        lines.append(f"- {a}")
    if not artifacts:
        lines.append("- (none)")
    lines.extend(["", "## Blockers"])
    for b in blockers:
        lines.append(f"- {b}")
    if not blockers:
        lines.append("- (none)")
    lines.extend(["", "## Next tasks suggested"])
    for n in next_tasks:
        lines.append(f"- {n}")
    if not next_tasks:
        lines.append("- (none)")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Post executor result to outbox")
    parser.add_argument("--task-id", required=True, help="Task id, e.g. TASK-20260731-103000-slug")
    parser.add_argument(
        "--verdict",
        required=True,
        choices=["DONE", "PARTIAL", "BLOCKED", "FAILED"],
        help="Outcome verdict",
    )
    parser.add_argument("--summary", required=True, help="What was done / observed")
    parser.add_argument("--file-changed", action="append", default=[], dest="files_changed")
    parser.add_argument("--artifact", action="append", default=[], dest="artifacts")
    parser.add_argument("--blocker", action="append", default=[], dest="blockers")
    parser.add_argument("--next", action="append", default=[], dest="next_tasks")
    parser.add_argument("--executor", default="cursor-executor")
    parser.add_argument(
        "--archive",
        action="store_true",
        help="Move task from in_progress to archive/ after posting",
    )
    args = parser.parse_args()

    ensure_dirs()
    out = OUTBOX / f"RESULT-{args.task_id}.md"
    out.write_text(
        render_result(
            task_id=args.task_id,
            verdict=args.verdict,
            summary=args.summary,
            files_changed=args.files_changed,
            artifacts=args.artifacts,
            blockers=args.blockers,
            next_tasks=args.next_tasks,
            executor=args.executor,
        ),
        encoding="utf-8",
    )
    print(out)

    if args.archive:
        task_path = find_task_file(args.task_id, IN_PROGRESS, INBOX)
        if task_path:
            dest = ARCHIVE / task_path.name
            task_path.rename(dest)
            print(f"archived task -> {dest}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
