#!/usr/bin/env python3
"""Unit tests for configurable dashboard pruning."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


def test_prune_subset_moves_only_selected_reason() -> None:
    jobs = {
        "jobs": [
            {
                "id": "manager",
                "title": "Engineering Manager",
                "location": "New York, NY",
                "status": "discovered",
                "apply_url": "https://example.com/manager",
            },
            {
                "id": "canada",
                "title": "Data Scientist",
                "location": "Toronto, Canada",
                "status": "discovered",
                "apply_url": "https://example.com/canada",
            },
            {
                "id": "started",
                "title": "Engineering Manager",
                "location": "New York, NY",
                "status": "tailoring",
                "apply_url": "https://example.com/started",
            },
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        jobs_file.write_text(json.dumps(jobs))
        with (
            mock.patch.object(srv, "JOBS_FILE", jobs_file),
            mock.patch.object(srv, "JOBS_LOCK_FILE", jobs_file.with_suffix(".json.lock")),
            mock.patch.object(srv, "block_deleted_job", return_value=[]),
        ):
            moved = srv._auto_delete_sweep_once({"non_us_location"})
            saved = json.loads(jobs_file.read_text())["jobs"]

    by_id = {job["id"]: job for job in saved}
    assert moved == 1
    assert by_id["canada"]["status"] == "deleted"
    assert by_id["canada"]["deleted_reason"] == "non_us_location"
    assert by_id["manager"]["status"] == "discovered"
    assert by_id["started"]["status"] == "tailoring"


def test_prune_settings_round_trip_and_defaults() -> None:
    with tempfile.TemporaryDirectory() as td:
        settings_file = Path(td) / "prune_settings.json"
        with mock.patch.object(srv, "PRUNE_SETTINGS_FILE", settings_file):
            defaults = srv.load_prune_settings()
            assert defaults["interval_s"] == 300
            assert set(defaults["reasons"]) == set(srv.PRUNE_REASON_CODES)

            saved = srv.save_prune_settings({
                "interval_s": 3600,
                "reasons": ["clearance_or_intel", "non_us_location"],
            })
            reloaded = srv.load_prune_settings()

    assert saved == reloaded
    assert reloaded == {
        "interval_s": 3600,
        "reasons": ["non_us_location", "clearance_or_intel"],
    }


def test_prune_settings_reject_unknown_reason_and_interval() -> None:
    with tempfile.TemporaryDirectory() as td:
        settings_file = Path(td) / "prune_settings.json"
        with mock.patch.object(srv, "PRUNE_SETTINGS_FILE", settings_file):
            try:
                srv.save_prune_settings({"interval_s": 123, "reasons": ["made_up"]})
            except ValueError as exc:
                assert "interval" in str(exc) or "reason" in str(exc)
            else:
                raise AssertionError("invalid prune settings were accepted")


def test_scheduled_prune_uses_persisted_reason_subset() -> None:
    settings = {
        "interval_s": 900,
        "reasons": ["clearance_or_intel"],
    }
    with mock.patch.object(srv, "_auto_delete_sweep_once", return_value=4) as sweep:
        moved = srv._run_scheduled_prune_once(settings)

    assert moved == 4
    sweep.assert_called_once_with({"clearance_or_intel"})
