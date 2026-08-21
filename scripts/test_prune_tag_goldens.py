#!/usr/bin/env python3
"""Golden prune/tag fixtures — dummy JDs only, no applicant PII."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from discovery_filters import (  # noqa: E402
    auto_delete_reason,
    detect_work_mode,
    extract_min_required_yoe,
    should_keep_listing,
    stamp_clearance_us_person_tags,
)

GOLDENS = Path(__file__).resolve().parent / "fixtures" / "prune_tag_goldens"


def _load_goldens() -> list[dict]:
    rows = []
    for path in sorted(GOLDENS.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_path"] = path.name
        rows.append(data)
    assert rows, f"no golden fixtures in {GOLDENS}"
    return rows


def test_prune_tag_goldens() -> None:
    for row in _load_goldens():
        expect = row["expect"]
        kwargs = dict(
            title=row.get("title"),
            company=row.get("company"),
            location=row.get("location"),
            description=row.get("description"),
        )
        reason = auto_delete_reason(**kwargs)
        keep = should_keep_listing(**kwargs)
        assert keep is bool(expect["keep"]), (
            f"{row['_path']}: keep={keep} expected {expect['keep']} "
            f"(reason={reason!r})"
        )
        assert reason == expect.get("reason"), (
            f"{row['_path']}: reason={reason!r} expected {expect.get('reason')!r}"
        )
        tags = stamp_clearance_us_person_tags(**kwargs)
        for key, val in (expect.get("tags") or {}).items():
            assert tags.get(key) is val, (
                f"{row['_path']}: tags[{key}]={tags.get(key)!r} expected {val!r}"
            )
        if "work_mode" in expect:
            mode = detect_work_mode(
                title=row.get("title"),
                location=row.get("location"),
                description=row.get("description"),
            )
            assert mode == expect["work_mode"], (
                f"{row['_path']}: work_mode={mode!r} expected {expect['work_mode']!r}"
            )
        if "min_yoe" in expect:
            yoe = extract_min_required_yoe(
                title=row.get("title"),
                description=row.get("description"),
            )
            assert yoe == expect["min_yoe"], (
                f"{row['_path']}: min_yoe={yoe!r} expected {expect['min_yoe']!r}"
            )


def test_unable_to_sponsor_never_usc_prune_in_goldens() -> None:
    """Regression: no-sponsorship language must keep (under-prune)."""
    path = GOLDENS / "must_keep_no_visa_sponsor.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    assert should_keep_listing(
        title=row["title"],
        location=row["location"],
        description=row["description"],
    )
    assert auto_delete_reason(
        title=row["title"],
        location=row["location"],
        description=row["description"],
    ) is None


if __name__ == "__main__":
    test_prune_tag_goldens()
    test_unable_to_sponsor_never_usc_prune_in_goldens()
    print("OK test_prune_tag_goldens")
