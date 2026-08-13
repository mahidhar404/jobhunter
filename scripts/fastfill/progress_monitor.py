#!/usr/bin/env python3
"""Progress monitor — source of “what's happening” for fastfill.

Scans latest flight logs, battle-gym notes, RELIABILITY_STATUS.md, and
reliability_gate.json. Emits progress_state.json + PROGRESS.md with one
next adaptive action. Gym green is never live_pass.

  skyvern_runtime/venv/bin/python scripts/fastfill/progress_monitor.py
  skyvern_runtime/venv/bin/python scripts/fastfill/progress_monitor.py --json
  skyvern_runtime/venv/bin/python scripts/fastfill/progress_monitor.py --self-test

Dummy-only. Never submit. Do not claim live production-ready.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = ROOT / "skyvern_runtime" / "real_job_results"
STATE_PATH = HERE / "progress_state.json"
PROGRESS_MD = HERE / "PROGRESS.md"
GATE_PATH = HERE / "reliability_gate.json"
STATUS_MD = HERE / "RELIABILITY_STATUS.md"
BATTLE_MD = HERE / "gym" / "ats" / "BATTLE_GYM.md"
BATTLE_LAST = HERE / "battle_fill_last.json"
GYM_VS_LIVE = HERE / "GYM_VS_LIVE.md"

from adapt_policy import (  # noqa: E402
    LIVE_ONLY_ACTION,
    pick_next_action,
    probe_code_gaps,
)

_BLOCKER_NEEDLES = (
    "pack_incomplete",
    "empty_cycle",
    "overwrite",
    "empty_readback",
    "lock_skip",
    "no_matching_option",
    "fos_wrong_chip",
    "listbox_still_open",
    "illinois",
    "countryregion",
    "addressline2",
    "regionsubdivision1",
    "thrash",
    "stuck_on_same_page",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _latest_mtime(paths: list[Path]) -> Path | None:
    existing = [p for p in paths if p.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda p: p.stat().st_mtime)


def find_flight_artifacts(results_dir: Path | None = None) -> dict[str, Any]:
    """Latest flight.log / flight.jsonl under real_job_results (and battle persist)."""
    base = results_dir if results_dir is not None else RESULTS
    logs: list[Path] = []
    jsonls: list[Path] = []
    if base.is_dir():
        logs = list(base.rglob("flight.log"))
        jsonls = list(base.rglob("flight.jsonl"))
    persist = Path(tempfile.gettempdir()) / "job-hunter-battle-fill-flight.log"
    live_logs = [
        p
        for p in logs
        if "battle" not in p.name.lower() and "job-hunter-battle" not in str(p)
    ]
    latest_log = _latest_mtime(live_logs) or (
        persist if persist.is_file() else _latest_mtime(logs)
    )
    latest_jsonl = _latest_mtime(jsonls)
    is_battle = bool(latest_log) and (
        "battle" in str(latest_log).lower() or "job-hunter-battle" in str(latest_log)
    )
    return {
        "flight_log": str(latest_log) if latest_log else None,
        "flight_jsonl": str(latest_jsonl) if latest_jsonl else None,
        "present": bool(latest_log or latest_jsonl),
        "live_headed_flight": bool(latest_log) and not is_battle,
        "gym_flight": is_battle,
    }


def _scan_text_blockers(text: str, *, ignore_pass_lines: bool = False) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        if ignore_pass_lines and re.search(
            r"\bpass\b|fixed|absent|gone from|no longer", low
        ):
            continue
        for needle in _BLOCKER_NEEDLES:
            if needle in low and needle not in found:
                found.append(needle)
    return found


def scan_flight(path: str | None) -> list[str]:
    if not path:
        return []
    return _scan_text_blockers(_read_text(Path(path)))


def scan_reliability_status(path: Path | None = None) -> dict[str, Any]:
    p = path or STATUS_MD
    text = _read_text(p)
    blockers = _scan_text_blockers(text, ignore_pass_lines=True)
    live_fail = bool(re.search(r"Verdict:\s*FAIL", text, re.I))
    reached = bool(re.search(r"reached_review[^\n]*true", text, re.I))
    illinois_pass = bool(re.search(r"Illinois[^\n]*PASS", text, re.I))
    if illinois_pass:
        blockers = [b for b in blockers if b not in ("illinois", "countryregion")]
    return {
        "path": str(p) if p.is_file() else None,
        "present": p.is_file(),
        "live_fail": live_fail,
        "reached_review": reached,
        "illinois_pass": illinois_pass,
        "blockers": blockers,
        "as_of": (re.search(r"As of:\*\*\s*(.+)", text) or [None, None])[1],
    }


def scan_reliability_gate(path: Path | None = None) -> dict[str, Any]:
    data = _read_json(path or GATE_PATH) or {}
    gym_pass = data.get("gym_pass")
    live_pass = data.get("live_pass")
    # Honesty: never infer live_pass from gym.
    if live_pass is True and gym_pass is True and not data.get("reached_review"):
        live_pass = False
    return {
        "path": str(path or GATE_PATH) if (path or GATE_PATH).is_file() else None,
        "present": bool(data),
        "pass": bool(data.get("pass")),
        "gym_pass": gym_pass,
        "live_pass": bool(live_pass) if live_pass is not None else False,
        "reached_review": bool(data.get("reached_review")),
        "verdict": data.get("verdict"),
        "advance_blocked_reason": data.get("advance_blocked_reason"),
        "timestamp": data.get("timestamp"),
        "out_dir": data.get("out_dir"),
        "leftover_count": data.get("leftover_count"),
        "filled_count": data.get("filled_count"),
        "confidence_lane": data.get("confidence_lane"),
    }


def scan_battle_gym() -> dict[str, Any]:
    last = _read_json(BATTLE_LAST)
    md = _read_text(BATTLE_MD)
    meta_path = HERE / "gym" / "ats" / "cases" / "workday_battle_multipage" / "meta.json"
    meta = _read_json(meta_path) or {}
    md_pass = bool(re.search(r"\*\*PASS\*\* vs", md))
    last_ok = bool(last.get("ok")) if last else None
    gym_pass = last_ok if last_ok is not None else md_pass
    live_signoff = bool(meta.get("live_signoff"))
    return {
        "gym_pass": bool(gym_pass),
        "live_signoff": live_signoff,
        "fidelity": meta.get("fidelity"),
        "last_ok": last_ok,
        "md_pass": md_pass,
        "battle_last": str(BATTLE_LAST) if BATTLE_LAST.is_file() else None,
        "honesty": "gym_green_is_not_live_win",
    }


def classify_lanes(*, gym_pass: bool | None, live_pass: bool) -> dict[str, Any]:
    """Never treat gym green as live win."""
    return {
        "gym_pass": bool(gym_pass),
        "live_pass": bool(live_pass),
        "honesty": "gym_pass ≠ live_pass — gym green is not a live win",
        "production_ready": False,
    }


def collect_blockers(
    *,
    flight: dict[str, Any],
    status: dict[str, Any],
    gate: dict[str, Any],
) -> list[str]:
    out: list[str] = []
    flight_blockers: list[str] = []
    if flight.get("live_headed_flight"):
        flight_blockers = scan_flight(flight.get("flight_log")) + scan_flight(
            flight.get("flight_jsonl")
        )
    for src in (flight_blockers, status.get("blockers") or []):
        for b in src:
            if b not in out:
                out.append(b)
    reason = str(gate.get("advance_blocked_reason") or "")
    if reason and reason not in out:
        out.insert(0, reason)
    if gate.get("reached_review") is False and "pack_incomplete" in (
        reason,
        *(status.get("blockers") or []),
    ):
        if "pack_incomplete" not in out:
            out.insert(0, "pack_incomplete")
    return out


def build_snapshot(*, results_dir: Path | None = None) -> dict[str, Any]:
    flight = find_flight_artifacts(results_dir)
    status = scan_reliability_status()
    gate = scan_reliability_gate()
    battle = scan_battle_gym()
    blockers = collect_blockers(flight=flight, status=status, gate=gate)
    code_gaps = probe_code_gaps(HERE)
    live_pass = bool(gate.get("live_pass")) and bool(gate.get("reached_review"))
    gym_pass = battle.get("gym_pass")
    lanes = classify_lanes(gym_pass=gym_pass, live_pass=live_pass)
    nxt = pick_next_action(
        blockers=blockers,
        code_gaps=code_gaps,
        live_pass=lanes["live_pass"],
        gym_pass=lanes["gym_pass"],
    )
    last_run = {
        "gate_timestamp": gate.get("timestamp"),
        "gate_verdict": gate.get("verdict"),
        "gate_out_dir": gate.get("out_dir"),
        "status_as_of": status.get("as_of"),
        "flight_log": flight.get("flight_log"),
        "battle_last_ok": battle.get("last_ok"),
        "reached_review": gate.get("reached_review"),
    }
    return {
        "timestamp": _utc_now(),
        "dummy": True,
        "never_submit": True,
        "production_ready": False,
        "gym_pass": lanes["gym_pass"],
        "live_pass": lanes["live_pass"],
        "honesty": lanes["honesty"],
        "last_run": last_run,
        "blockers": blockers,
        "code_gaps": {k: v for k, v in code_gaps.items() if k != "batch_fill_wired"},
        "batch_fill_wired": code_gaps.get("batch_fill_wired"),
        "next_action": nxt,
        "sources": {
            "flight": flight,
            "reliability_status": {k: status[k] for k in ("path", "present", "live_fail")},
            "reliability_gate": {
                k: gate[k]
                for k in ("path", "present", "live_pass", "gym_pass", "verdict")
            },
            "battle_gym": {
                k: battle[k]
                for k in ("gym_pass", "live_signoff", "fidelity", "honesty")
            },
            "gym_vs_live": str(GYM_VS_LIVE) if GYM_VS_LIVE.is_file() else None,
        },
    }


def render_progress_md(snap: dict[str, Any]) -> str:
    nxt = snap.get("next_action") or {}
    last = snap.get("last_run") or {}
    blockers = snap.get("blockers") or []
    blocker_lines = "\n".join(f"- `{b}`" for b in blockers) or "- (none scanned)"
    live_only = bool(nxt.get("live_only"))
    return f"""# Fastfill progress

**As of:** {snap.get("timestamp")}
**Honesty:** gym_pass ≠ live_pass. Gym green is **not** a live win. Not production-ready.

| Lane | Result |
|------|--------|
| gym_pass | `{snap.get("gym_pass")}` |
| live_pass | `{snap.get("live_pass")}` |
| production_ready | `false` |

## Last run

- gate: `{last.get("gate_timestamp") or "—"}` verdict=`{last.get("gate_verdict") or "—"}` reached_review=`{last.get("reached_review")}`
- artifacts: `{last.get("gate_out_dir") or "—"}`
- flight.log: `{last.get("flight_log") or "none (live headed flight.log required for live truth)"}`
- battle_fill last ok: `{last.get("battle_last_ok")}`

## Blockers

{blocker_lines}

Known names: pack_incomplete, empty_cycle, overwrite, empty_readback, FoS, Illinois.

## Next adaptive action

**`{nxt.get("id")}`** — {nxt.get("title")}

- reason: {nxt.get("reason")}
- code_gap: `{nxt.get("code_gap")}`
- live_only: `{live_only}`

{"Stop gym ticks. Run headed `./scripts/fastfill/run_fill_visible.sh URL` and paste `flight.log`." if live_only else "Implement this one fix, then re-run unit tests / `run_battle_fill.py`. Re-run this monitor."}

## How to run

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/progress_monitor.py
./scripts/fastfill/watch_progress.sh
```
"""


def write_outputs(snap: dict[str, Any], *, prior: dict[str, Any] | None = None) -> None:
    ticks = list((prior or {}).get("ticks") or [])
    nxt = snap.get("next_action") or {}
    ticks.append(
        {
            "timestamp": snap.get("timestamp"),
            "action_id": nxt.get("id"),
            "live_only": nxt.get("live_only"),
            "blockers": snap.get("blockers"),
            "gym_pass": snap.get("gym_pass"),
            "live_pass": snap.get("live_pass"),
        }
    )
    snap["ticks"] = ticks[-12:]
    STATE_PATH.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
    PROGRESS_MD.write_text(render_progress_md(snap), encoding="utf-8")


def run_monitor(*, results_dir: Path | None = None) -> dict[str, Any]:
    prior = _read_json(STATE_PATH)
    snap = build_snapshot(results_dir=results_dir)
    write_outputs(snap, prior=prior)
    return snap


def _self_test() -> None:
    from adapt_policy import pick_next_action as _pick

    gaps = {
        "tighten_commit_fill_lock": True,
        "wire_empty_cycle_workday": True,
        "fiber_text_commit_addr2_county": False,
        "illinois_state_pack": False,
        "fos_ontology_unlock": False,
        "keep_batch_fill": False,
        "live_headed_flight_log": False,
    }
    a = _pick(
        blockers=["pack_incomplete", "empty_readback", "addressline2"],
        code_gaps=gaps,
        live_pass=False,
        gym_pass=True,
    )
    # empty_readback already wired → next gym-fixable gap (lock, then empty_cycle)
    assert a["id"] in (
        "tighten_commit_fill_lock",
        "wire_empty_cycle_workday",
    ), a
    assert a["code_gap"] is True
    assert a["live_only"] is False

    b = _pick(
        blockers=["empty_readback"],
        code_gaps={**gaps, "tighten_commit_fill_lock": False, "wire_empty_cycle_workday": False},
        live_pass=False,
        gym_pass=True,
    )
    assert b["id"] == LIVE_ONLY_ACTION, b
    assert b["live_only"] is True

    lanes = classify_lanes(gym_pass=True, live_pass=False)
    assert lanes["gym_pass"] is True
    assert lanes["live_pass"] is False
    assert lanes["production_ready"] is False

    md = render_progress_md(
        {
            "timestamp": "2026-08-13T0000Z",
            "gym_pass": True,
            "live_pass": False,
            "last_run": {},
            "blockers": ["pack_incomplete"],
            "next_action": b,
        }
    )
    assert "gym_pass ≠ live_pass" in md
    assert LIVE_ONLY_ACTION in md
    print("progress_monitor self-test: OK")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fastfill progress monitor")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--results-dir", type=Path, default=None)
    args = ap.parse_args()
    if args.self_test:
        _self_test()
        return 0
    snap = run_monitor(results_dir=args.results_dir)
    if args.json:
        print(json.dumps(snap, indent=2, default=str))
    else:
        nxt = snap.get("next_action") or {}
        print(f"gym_pass={snap.get('gym_pass')} live_pass={snap.get('live_pass')}")
        print(f"blockers={snap.get('blockers')}")
        print(f"next={nxt.get('id')} live_only={nxt.get('live_only')}")
        print(f"wrote {PROGRESS_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
