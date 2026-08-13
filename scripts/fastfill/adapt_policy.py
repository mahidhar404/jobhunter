"""Adaptive next-action policy for fastfill progress monitor.

Picks ONE highest-ROI gym-fixable action per tick. Gym green is never a
live win — when remaining work needs a headed tenant, return
``live_headed_flight_log``.

Dummy-only. Never submit.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# Symptom → action id (priority order). First matching *code gap* wins.
POLICY_ORDER: tuple[str, ...] = (
    "tighten_commit_fill_lock",
    "wire_empty_cycle_workday",
    "fiber_text_commit_addr2_county",
    "illinois_state_pack",
    "fos_ontology_unlock",
    "keep_batch_fill",
    "live_headed_flight_log",
)

LIVE_ONLY_ACTION = "live_headed_flight_log"

_SYMPTOM_TO_ACTION: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("overwrite", "lock_skip", "thrash_rewrite", "intent_as_readback"),
        "tighten_commit_fill_lock",
    ),
    (
        ("empty_cycle", "page_cycling", "stuck_on_same_page"),
        "wire_empty_cycle_workday",
    ),
    (
        (
            "empty_readback",
            "addressline2",
            "address_line2",
            "regionsubdivision1",
            "addr2",
            "county",
        ),
        "fiber_text_commit_addr2_county",
    ),
    (
        ("illinois", "countryregion", "no_matching_option", "address_state"),
        "illinois_state_pack",
    ),
    (
        ("fos", "field_of_study", "arts-other", "family_steal", "fos_wrong_chip"),
        "fos_ontology_unlock",
    ),
)

_ACTION_TITLES: dict[str, str] = {
    "tighten_commit_fill_lock": (
        "Tighten commit_fill / lock_verified_field: never lock empty or intent-as-readback"
    ),
    "wire_empty_cycle_workday": (
        "Wire page_progress.note_settle_cycle into Workday phase loops "
        "(increment empty_cycle_count)"
    ),
    "fiber_text_commit_addr2_county": (
        "addr2/county empty_readback is live-only — headed flight.log, not source-grep"
    ),
    "illinois_state_pack": (
        "Illinois / countryRegion state pack (promptOption, never fiber text)"
    ),
    "fos_ontology_unlock": (
        "FoS family steal → ontology unlock-if-wrong (Arts-Other vs Science-Computer)"
    ),
    "keep_batch_fill": (
        "Vanilla slowness: batch_fill already wired — do not regress"
    ),
    "live_headed_flight_log": (
        "live headed flight.log required — gym cannot prove remaining live Fiber/auth"
    ),
}


def probe_code_gaps(here: Path | None = None) -> dict[str, bool]:
    """True = still a real code gap. False = already wired (skip)."""
    root = here or HERE
    lock_src = (root / "field_lock.py").read_text(encoding="utf-8")
    contract_src = (root / "fill_contract.py").read_text(encoding="utf-8")
    wd_src = (root / "exp_workday_selectors.py").read_text(encoding="utf-8")
    vs_src = (root / "verified_select.py").read_text(encoding="utf-8")
    ff_src = (root / "fast_fill.py").read_text(encoding="utf-8")
    ont_src = (root / "workday_aid_ontology.py").read_text(encoding="utf-8")

    # lock_verified_field must not fall back to intent/value as DOM readback.
    lock_fn = _extract_def(lock_src, "def lock_verified_field")
    lock_uses_value_fallback = bool(
        re.search(
            r"row\.get\(\s*[\"']value[\"']\s*\)",
            lock_fn,
        )
    )
    lock_refuses_empty = "if not rb" in lock_fn or "if not rb_s" in lock_fn
    tighten_gap = lock_uses_value_fallback or not lock_refuses_empty

    empty_cycle_gap = (
        "note_settle_cycle" not in wd_src
        and "note_workday_phase_cycle" not in wd_src
    )

    # String-in-file ≠ live NXP readback. Fiber addr2/county is never a
    # gym-fixable code gap — remaining work is headed flight.log.
    fiber_gap = False

    illinois_gap = (
        "addressSection_countryRegion" not in wd_src
        or "expand_state_value" not in vs_src
        or "ADDRESS_STATE" not in ont_src
    )

    fos_gap = (
        "unlock_fos_if_intent_mismatch" not in lock_src
        or "unlock_fos_if_intent_mismatch" not in wd_src
    )

    batch_ok = "batch_fill_simple" in ff_src
    contract_locks_on_done = "lock_verified_field" in contract_src and "done_v.ok" in contract_src

    return {
        "tighten_commit_fill_lock": tighten_gap or not contract_locks_on_done,
        "wire_empty_cycle_workday": empty_cycle_gap,
        "fiber_text_commit_addr2_county": fiber_gap,
        "illinois_state_pack": illinois_gap,
        "fos_ontology_unlock": fos_gap,
        "keep_batch_fill": False,  # already wired; never a gap to implement
        "batch_fill_wired": batch_ok,
        "live_headed_flight_log": False,  # not a code gap
    }


def _extract_def(src: str, header: str) -> str:
    idx = src.find(header)
    if idx < 0:
        return ""
    nxt = src.find("\ndef ", idx + len(header))
    return src[idx : nxt if nxt > 0 else idx + 4000]


def _norm_blocker(item: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (item or "").lower()).strip("_")


def pick_next_action(
    *,
    blockers: list[str],
    code_gaps: dict[str, bool] | None = None,
    live_pass: bool = False,
    gym_pass: bool | None = None,
) -> dict[str, Any]:
    """Pick one action. Never promote gym_pass to live_pass."""
    del gym_pass  # honesty: unused for live_pass
    gaps = code_gaps if code_gaps is not None else probe_code_gaps()
    if live_pass:
        return {
            "id": LIVE_ONLY_ACTION,
            "title": _ACTION_TITLES[LIVE_ONLY_ACTION],
            "reason": "live_pass already true — remaining work is headed re-proof",
            "live_only": True,
            "code_gap": False,
        }

    blob = " ".join(_norm_blocker(b) for b in blockers)
    symptom_ids: list[str] = []
    for needles, action_id in _SYMPTOM_TO_ACTION:
        if any(n in blob for n in needles):
            symptom_ids.append(action_id)

    # Fiber/addr2/county: algorithm in source does not prove live commit.
    if "fiber_text_commit_addr2_county" in symptom_ids:
        return {
            "id": LIVE_ONLY_ACTION,
            "title": _ACTION_TITLES[LIVE_ONLY_ACTION],
            "reason": (
                "empty_readback addr2/county needs headed flight.log — "
                "source strings do not prove live Fiber commit"
            ),
            "live_only": True,
            "code_gap": False,
        }

    # Prefer a symptom whose code is still a gap (policy order).
    for action_id in POLICY_ORDER:
        if action_id == "keep_batch_fill":
            continue
        if action_id in symptom_ids and gaps.get(action_id):
            return _action(action_id, gaps, why=f"blocker={action_id}")

    # Symptom matched but already wired → remaining gym-fixable gaps, then live-only.
    for action_id in POLICY_ORDER:
        if action_id in ("keep_batch_fill", LIVE_ONLY_ACTION):
            continue
        if gaps.get(action_id):
            return _action(
                action_id,
                gaps,
                why="current live blocker already wired; next gym-fixable gap",
            )

    return {
        "id": LIVE_ONLY_ACTION,
        "title": _ACTION_TITLES[LIVE_ONLY_ACTION],
        "reason": (
            "No remaining gym-fixable code gap. Need headed "
            "`run_fill_visible.sh` + flight.log (gym ≠ live)."
        ),
        "live_only": True,
        "code_gap": False,
    }


def _action(action_id: str, gaps: dict[str, bool], *, why: str) -> dict[str, Any]:
    return {
        "id": action_id,
        "title": _ACTION_TITLES.get(action_id, action_id),
        "reason": why,
        "live_only": action_id == LIVE_ONLY_ACTION,
        "code_gap": bool(gaps.get(action_id)),
    }


def main() -> int:
    import json

    gaps = probe_code_gaps()
    nxt = pick_next_action(blockers=[], code_gaps=gaps)
    print(json.dumps({"code_gaps": gaps, "next": nxt}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
