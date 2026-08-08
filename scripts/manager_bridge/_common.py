"""Shared paths and helpers for manager_bridge CLI tools."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_ROOT = REPO_ROOT / "skyvern_runtime" / "manager_bridge"
INBOX = BRIDGE_ROOT / "inbox"
IN_PROGRESS = BRIDGE_ROOT / "in_progress"
OUTBOX = BRIDGE_ROOT / "outbox"
ARCHIVE = BRIDGE_ROOT / "archive"
STATUS_FILE = BRIDGE_ROOT / "STATUS.md"

TASK_ID_RE = re.compile(r"^TASK-\d{8}-\d{6}-[a-z0-9-]+$")
TASK_FILE_RE = re.compile(r"^(TASK-\d{8}-\d{6}-[a-z0-9-]+)\.(md|json)$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "task"


def make_task_id(title: str) -> str:
    ts = utc_now().strftime("%Y%m%d-%H%M%S")
    return f"TASK-{ts}-{slugify(title)}"


def ensure_dirs() -> None:
    for d in (INBOX, IN_PROGRESS, OUTBOX, ARCHIVE, BRIDGE_ROOT / "schema"):
        d.mkdir(parents=True, exist_ok=True)


def find_task_file(task_id: str, *dirs: Path) -> Path | None:
    for d in dirs:
        for ext in ("md", "json"):
            p = d / f"{task_id}.{ext}"
            if p.is_file():
                return p
    return None


def parse_task_md(text: str) -> dict:
    """Parse YAML-ish frontmatter + body fields from markdown task."""
    data: dict = {}
    fm_match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    body = text
    if fm_match:
        body = text[fm_match.end() :]
        for line in fm_match.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                data[k.strip()] = v.strip()

    sections = {
        "context": r"## Context\s*\n(.*?)(?=\n## |\Z)",
        "acceptance_criteria": r"## Acceptance criteria\s*\n(.*?)(?=\n## |\Z)",
        "constraints": r"## Constraints\s*\n(.*?)(?=\n## |\Z)",
        "files_to_touch": r"## Files to touch\s*\n(.*?)(?=\n## |\Z)",
        "done_when": r"## Done when\s*\n(.*?)(?=\n## |\Z)",
    }
    for key, pat in sections.items():
        m = re.search(pat, body, re.DOTALL | re.IGNORECASE)
        if not m:
            continue
        block = m.group(1).strip()
        if key in ("acceptance_criteria", "constraints", "files_to_touch"):
            items = []
            for line in block.splitlines():
                line = line.strip()
                if line.startswith("- "):
                    items.append(line[2:].strip())
                elif line.startswith("* "):
                    items.append(line[2:].strip())
            data[key] = items
        else:
            data[key] = block

    return data


def load_task(path: Path) -> dict:
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    raw = path.read_text(encoding="utf-8")
    data = parse_task_md(raw)
    stem = path.stem
    data.setdefault("id", stem)
    return data


def render_task_md(task: dict) -> str:
    lines = [
        "---",
        f"id: {task['id']}",
        f"priority: {task['priority']}",
        f"title: {task['title']}",
        f"created_at: {task.get('created_at', iso_now())}",
        f"created_by: {task.get('created_by', 'claude-manager')}",
        "status: pending",
        "---",
        "",
        f"# {task['title']}",
        "",
        "## Context",
        task.get("context", "").strip(),
        "",
        "## Acceptance criteria",
    ]
    for item in task.get("acceptance_criteria", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Constraints"])
    for item in task.get("constraints", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Files to touch"])
    for item in task.get("files_to_touch", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Done when", task.get("done_when", "").strip(), ""])
    return "\n".join(lines)


def list_task_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    files = []
    for p in sorted(directory.iterdir()):
        if p.is_file() and TASK_FILE_RE.match(p.name):
            files.append(p)
    return files
