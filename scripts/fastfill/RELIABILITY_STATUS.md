# Workday Reliability Status

**As of:** 2026-08-12 ~23:02Z  
**Run:** `nxp_reliability_gate_20260812T2300Z` (Illinois State fix)  
**Dummy-only; never_submit; no commit.**

> **Honesty:** Gym/unit green ≠ this live verdict. See [`GYM_VS_LIVE.md`](./GYM_VS_LIVE.md).
> `live_pass` only from headed `reliability_gate` / flight recorder — not gym.

---

## Live visibility (flight recorder)

When debugging “cycling / doing nothing / overwrite” **while watching the browser**, use the flight recorder — not only this status rollup:

- Guide: [`LIVE_VISIBILITY.md`](./LIVE_VISIBILITY.md)
- Artifacts: `{out_dir}/flight.log` + `flight.jsonl` (headed / `run_fill_visible.sh` default ON)
- Force: `FASTFILL_FLIGHT=1` or `--flight-recorder`

---

## Verdict: FAIL (not Review-ready)

`reached_review: false`. Do **not** treat as PARTIAL success.

| Bar | Result |
|-----|--------|
| No runaway / empty-page cycling | **PASS** — FoS alias thrash still dead |
| Locked values not rewritten | **PASS** (prior + this run) |
| FoS / listbox advance block | **PASS** — `listbox_still_open` absent |
| Illinois `countryRegion` fill | **PASS on 2300Z** — `state_committed` / verified; **gone from leftovers** |
| `reached_review` | **FAIL** — still `pack_incomplete` on contact |
| thrash / wrong / never_submit | **PASS** — thrash=0, wrong=0, never_submit=true, submit=false |

---

## What this scope fixed (Illinois)

**2244Z blocker:** `addressSection_countryRegion` → `no_matching_option` → `pack_incomplete`.

**Causes:**
1. ArrowDown nudge on State *button* toggled the prompt closed before option click
2. `promptOption` rows often lack `role=option` (click path missed them)
3. How-Heard listbox left open stole chrome
4. `commit_fill` treated intent (`IL`/`Illinois`) as DOM readback when `readback` was null → dishonest `verified=True` while still missed

**Fixes:** settle chrome first; nudge only with a real filter input; click `promptOption`; clear `no_matching_option` on real commit; never use intent as readback; live State re-probe before pack gate. Offline: `test_workday_address_state.py` + gym `workday_address_state_illinois`.

**Live proof (2300Z):**  
`ADDRESS_STATE … reason=state_committed` · filled row shows Illinois/IL · **not** in leftovers.

---

## Latest gate (2300Z)

```json
{
  "pass": false,
  "reached_review": false,
  "thrash_rewrites": 0,
  "wrong_values": 0,
  "verdict": "FAIL",
  "advance_blocked_reason": "pack_incomplete",
  "pages_completed": [{"name": "contact", "reason": "stopped_at_contact"}],
  "filled_count": 22,
  "leftover_count": 7
}
```

**Illinois is no longer the pack blocker.** Next real leftovers / pack noise:

1. `addressSection_addressLine2` — `empty_readback` (Apt 1A)
2. `addressSection_regionSubdivision1` — `empty_readback` (county Sangamon)
3. `worked_here_before` — `radio_not_found` (optional_miss class)
4. `phonenumber--countryphonecode` — false empty (chip already US +1)

---

## Prior evidence

| Run | Issue |
|-----|-------|
| 2227Z | FoS alias settle thrash — **fixed** |
| 2237Z | `listbox_still_open` — **fixed** |
| 2244Z | `pack_incomplete` / Illinois `no_matching_option` — **fixed on 2300Z** |
| 2258Z | Abort: browser closed mid-resume (`TargetClosedError`) — not a fill verdict |
| 2300Z | Illinois OK; still stuck on contact `pack_incomplete` (line2/county) |

---

## Next single leftover (if continuing)

Clear contact **pack_incomplete** for Address Line 2 and/or County (`regionSubdivision1`) so ADVANCE can leave My Information — or mark truly optional when not required on NXP. Do not reopen FoS/Illinois unless they regress.
