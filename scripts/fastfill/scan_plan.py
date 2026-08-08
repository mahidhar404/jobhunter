"""Persist scan.json + plan.json for debug / option learner (auto-apply style)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_scan_plan(
    out_dir: str | Path,
    *,
    fields: list[dict] | None = None,
    plan: list[dict] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Write scan.json + plan.json under out_dir. Returns paths written."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    scan_path = root / "scan.json"
    plan_path = root / "plan.json"
    scan_doc = {
        "version": 1,
        "meta": meta or {},
        "fields": fields or [],
    }
    plan_doc = {
        "version": 1,
        "meta": meta or {},
        "steps": plan or [],
    }
    scan_path.write_text(json.dumps(scan_doc, indent=2)[:500_000] + "\n", encoding="utf-8")
    plan_path.write_text(json.dumps(plan_doc, indent=2)[:500_000] + "\n", encoding="utf-8")
    return {"scan": str(scan_path), "plan": str(plan_path)}


def build_plan_steps_from_filled(filled: list[dict] | None) -> list[dict]:
    """Summarize filled rows into plan steps (strategy tags)."""
    steps = []
    for row in filled or []:
        if not isinstance(row, dict):
            continue
        mode = str(row.get("mode") or row.get("algorithm") or row.get("via") or "")
        strategy = "pack"
        if "fiber" in mode or mode == "fiber_search_select":
            strategy = "searchSelect"
        elif "batch" in mode:
            strategy = "batch"
        elif "flash" in mode.lower() or row.get("via") == "flash":
            strategy = "flash"
        steps.append(
            {
                "type": row.get("type") or row.get("automation_id"),
                "label": str(row.get("label") or row.get("automation_id") or "")[:80],
                "value": str(row.get("value") or "")[:80],
                "strategy": strategy,
                "verified": bool(row.get("verified")),
            }
        )
    return steps
