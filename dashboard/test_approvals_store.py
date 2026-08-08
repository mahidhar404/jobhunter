#!/usr/bin/env python3
"""Tests for the local exec-approvals allowlist store."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import approvals_store  # noqa: E402


def test_allowlist_add_creates_and_appends():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "exec-approvals.json"
        ag = approvals_store.allowlist_add("python3*", "job-hunter", path=path)
        assert "python3*" in ag["allowlist"]
        assert ag["ask"] == "off"
        # Idempotent — no duplicate.
        approvals_store.allowlist_add("python3*", "job-hunter", path=path)
        # Second distinct pattern appends.
        approvals_store.allowlist_add("tectonic*", "job-hunter", path=path)
        data = json.loads(path.read_text())
        allow = data["agents"]["job-hunter"]["allowlist"]
        assert allow.count("python3*") == 1
        assert "tectonic*" in allow


def test_ensure_ask_off_repairs():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "exec-approvals.json"
        path.write_text(json.dumps({"agents": {"job-hunter": {"ask": "on"}}}))
        approvals_store.ensure_ask_off("job-hunter", path=path)
        data = json.loads(path.read_text())
        assert data["agents"]["job-hunter"]["ask"] == "off"
        assert data["agents"]["job-hunter"]["security"] in ("allowlist", "full")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("all approvals_store tests passed")
