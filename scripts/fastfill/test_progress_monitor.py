#!/usr/bin/env python3
"""Unit tests: progress monitor + adaptive policy (no live ATS). Dummy-only."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from adapt_policy import LIVE_ONLY_ACTION, pick_next_action, probe_code_gaps  # noqa: E402
from progress_monitor import (  # noqa: E402
    classify_lanes,
    collect_blockers,
    render_progress_md,
    scan_flight,
)


def test_gym_green_is_not_live_pass() -> None:
    lanes = classify_lanes(gym_pass=True, live_pass=False)
    assert lanes["gym_pass"] is True
    assert lanes["live_pass"] is False
    assert lanes["production_ready"] is False


def test_policy_empty_readback_is_live_only() -> None:
    """Source-grep must not claim Fiber wired or dispatch another gym gap."""
    gaps = {
        "tighten_commit_fill_lock": True,
        "wire_empty_cycle_workday": True,
        "fiber_text_commit_addr2_county": False,
        "illinois_state_pack": False,
        "fos_ontology_unlock": False,
        "keep_batch_fill": False,
        "live_headed_flight_log": False,
    }
    nxt = pick_next_action(
        blockers=["pack_incomplete", "empty_readback", "addressline2"],
        code_gaps=gaps,
        live_pass=False,
        gym_pass=True,
    )
    assert nxt["id"] == LIVE_ONLY_ACTION
    assert nxt["live_only"] is True
    assert nxt["code_gap"] is False
    assert "flight.log" in nxt["reason"]


def test_policy_all_wired_is_live_only() -> None:
    gaps = {
        "tighten_commit_fill_lock": False,
        "wire_empty_cycle_workday": False,
        "fiber_text_commit_addr2_county": False,
        "illinois_state_pack": False,
        "fos_ontology_unlock": False,
        "keep_batch_fill": False,
        "live_headed_flight_log": False,
    }
    nxt = pick_next_action(
        blockers=["pack_incomplete", "empty_readback"],
        code_gaps=gaps,
        live_pass=False,
        gym_pass=True,
    )
    assert nxt["id"] == LIVE_ONLY_ACTION
    assert nxt["live_only"] is True


def test_scan_flight_blockers(tmp_path: Path | None = None) -> None:
    td = tmp_path or Path(tempfile.mkdtemp())
    log = td / "flight.log"
    log.write_text(
        '[flight 0040] action=STOP gate=pack:miss(pack_incomplete)\n'
        '[flight 0041] advance=STOP(empty_cycle)\n'
        'empty_readback addressSection_addressLine2\n',
        encoding="utf-8",
    )
    found = scan_flight(str(log))
    assert "pack_incomplete" in found
    assert "empty_cycle" in found
    assert "empty_readback" in found


def test_collect_blockers_prefers_gate_reason() -> None:
    blockers = collect_blockers(
        flight={"flight_log": None, "flight_jsonl": None},
        status={"blockers": ["empty_readback"]},
        gate={"advance_blocked_reason": "pack_incomplete", "reached_review": False},
    )
    assert blockers[0] == "pack_incomplete"
    assert "empty_readback" in blockers


def test_progress_md_never_claims_live_from_gym() -> None:
    md = render_progress_md(
        {
            "timestamp": "2026-08-13T0000Z",
            "gym_pass": True,
            "live_pass": False,
            "last_run": {"gate_verdict": "FAIL"},
            "blockers": ["pack_incomplete"],
            "next_action": {
                "id": LIVE_ONLY_ACTION,
                "title": "live headed flight.log required",
                "reason": "no gym gap",
                "live_only": True,
                "code_gap": False,
            },
        }
    )
    assert "gym_pass ≠ live_pass" in md
    assert "Not production-ready" in md
    assert json.dumps({"gym_pass": True, "live_pass": True}) not in md


def test_probe_code_gaps_keys() -> None:
    gaps = probe_code_gaps(HERE)
    for key in (
        "tighten_commit_fill_lock",
        "wire_empty_cycle_workday",
        "fiber_text_commit_addr2_county",
        "illinois_state_pack",
        "fos_ontology_unlock",
        "keep_batch_fill",
    ):
        assert key in gaps


def main() -> None:
    test_gym_green_is_not_live_pass()
    test_policy_empty_readback_is_live_only()
    test_policy_all_wired_is_live_only()
    test_scan_flight_blockers()
    test_collect_blockers_prefers_gate_reason()
    test_progress_md_never_claims_live_from_gym()
    test_probe_code_gaps_keys()
    print("test_progress_monitor: OK")


if __name__ == "__main__":
    main()
