# Fastfill over-engineering review

**Date:** 2026-08-13  
**Scope:** `scripts/fastfill/` fill stack as it exists *now* (not the July 31 `ARCHITECTURE_REVIEW.md`). Dummy-only. Never-submit assumed.  
**Ask:** We might have over-engineered a lot of stuff — is that causing the issues?  
**Method:** Read current modules + wiring, not the process docs' self-mythology. Unify-oracles may be in flight — this is CURRENT code.

**Honesty:** Not production-ready. Gym green ≠ live. This review does not claim a live Review-ready fill.

### Clutter cut (2026-08-13)

**Deleted:** `exp_fill_strategies_1_3.py`, `exp_approaches_7_9.py`, `exp_semantic_leftover_match.py` (bake-offs; fiber algorithm already in `verified_select`). `field_lock.page_complete_should_advance` (tests-only fourth advance boolean).

**Kept:** `exp_workday_selectors.py`, `verified_select` searchSelect / fiber commit, `field_lock`, `fill_contract`, `field_done`, `flight_recorder`, never-submit, ATS gym cases. `_gate_then_advance` remains as Workday SPA/wait implementation — **not** a second voter; `_contract_advance_page` does not fall through. FormFactory gym quarantined (improvement_cycle only).

**County path-drift:** `build_contact_fill_plan` now uses `_WD_COMBOBOX_AIDS` (includes `regionSubdivision1`). Apt `addressLine2` still pack-required until headed NXP proves optional.

**adapt_policy:** empty_readback addr2/county → `live_headed_flight_log` (source-string ≠ live Fiber).

---

## Executive verdict

**Over-engineering is a contributor and a confidence scam — not the live FAIL mechanism.** NXP still dies on contact `pack_incomplete` because `addressSection_addressLine2` and `addressSection_regionSubdivision1` come back `empty_readback` after Fiber re-render. That is a widget-commit bug (`verified_select.fiber_text_commit` / `fill_text_fiber_then_read` does not stick on real Workday React; county is also filled as TEXT in two-phase while the CSS pack marks it combobox). Extra oracles, gyms, bake-offs, and `PROGRESS.md` did not wipe those inputs. They *did* make the system disagree with itself, train agents on static HTML, and spend cycles on “15 approaches” while the headed browser still shows blank Apt/County. Illinois/thrash improved because those were *real* widget bugs with *real* fixes (`promptOption` click, no intent-as-readback) — not because a ninth judge appeared. Gym Review ≠ live. Blame Fiber first; blame the dual-oracle / gym-as-signoff stack for why you keep not fixing Fiber.

---

## Inventory: how many “systems” exist

Ballpark: **159 Python files**, **~79k non-test LOC + ~19k test LOC**, **69 `test_*.py`**, **24 ATS gym cases**, **2 gyms** (ATS + FormFactory), **15 markdown files** in `scripts/fastfill/` plus root `ARCHITECTURE_REVIEW.md` (stale: July 31, claimed `fast_fill.py` 8.3k — now 13.4k).

### Fill layers (7, sequential on non-Workday)

| # | Layer | Module | Live? |
|---|--------|--------|-------|
| 0 | ENTRY pre-pass | `fast_fill.entry_prepass` + `button_gate` | Wired |
| 0.5 | Selector pack / vanilla batch | `apply_selector_pack` → `batch_fill.batch_fill_simple` then sequential `_fill_selector` | Wired (was unwired; now used) |
| Replay | Tenant selector→type cache | `record_replay.apply_replay_map` | Wired before extract |
| 0+1 | Extract + classify | `field_map` + `fill_from_extract` | Wired (Workday **skips** this) |
| Learned | Policy allow-list | `learning.py` / `learned_fields.json` | Thin; policy facts only |
| Flash | DeepSeek leftovers ≤5 | `flash_leftovers.py` — CLI default OFF, **dashboard / `run_fill_visible.sh` ON** | Wired, opt-in flag |
| Skyvern hybrid | Older engine | `skyvern_runtime/scripts/hybrid_fill.py` via `dashboard/server.py` fallback | Dead unless `fast_fill.py` missing |

Workday bypasses extract and runs `exp_workday_selectors.workday_two_phase_on_page` (Phases A–E). That is a **second orchestrator**, not a layer.

### Completion oracles (at least 7 still vote)

1. `field_done.field_is_done` / `field_is_done_from_readback` / `filled_rows_honest` — **intended** single done contract (docstring admits five modules used to disagree).
2. `fill_verify.is_verified_fill_row` — still the Workday pack metric (`_is_verified_fill`).
3. `form_gaps.collect_form_gaps` / `gaps_block_ready` — ChamPro “gaps after Save”.
4. `leftover_miss_scan.promote_l01_misses` — unanswered-choice DOM scan.
5. `page_progress.can_claim_ready` + `workday_wizard_incomplete` + footer probe.
6. `vision_judge.judge_page` / `apply_live_vision_gate` — Ready gate (DOM + optional screenshot).
7. Contact **pack** itself: `pack_missed` vs `required_empty` in `_phase_b_contact` (`can_advance = not pack_missed and not required_empty`).

### Advance gates (at least 4)

1. `_phase_b_contact` pack gate (STOP `pack_incomplete` **before** any Next click) — this is the live NXP stopper.
2. `fill_contract.advance_page_if_ready` — settle listbox + `filled_rows_honest` + `can_claim_ready` + `fast_fill.try_advance_if_page_complete`.
3. `exp_workday_selectors._gate_then_advance` — FoS settle + `listbox_still_open` + `_required_empty_on_page` + Next click.
4. `field_lock.page_complete_should_advance` — **tests only**, not on the live click path.

`_contract_advance_page` is supposed to unify (2) and (3). **It does not.** See dual-oracles.

### Lock systems (2 real + 1 experiment)

- `field_lock.FieldLockSession` + `lock_verified_field` / `gate_field_action` / FoS ontology unlock — **wired, earns keep**.
- `workday_aid_ontology.AID_FAMILIES` (fos / address_state / how_heard) — wired via `field_lock`.
- `exp_approaches_7_9.FILL_ONCE_BUS_JS` — **unwired bake-off**.

### Judges (3 overlapping)

| Judge | Role | Wired? |
|-------|------|--------|
| `action_judge.judge_field_action` | correct_skip / thrash_rewrite / wrong_autofill | Helper inside supervisor |
| `action_supervisor.ActionSupervisor` / `audit_fill_row` | After every fill: OK / THRASH / WRONG / STUCK | Default ON (`FASTFILL_ACTION_SUPERVISOR`); `commit_fill` fail-closes on supervisor error |
| `vision_judge` | Ready completeness, not a filler | Wired at Ready; LLM screenshot path optional |

`fill_attribution.py` is post-hoc prefill-vs-LLM accounting (cycle Agent3), not a live voter.

### Learning stores (4 overlapping caches)

- `learning_store/experience.jsonl` + `selector_stats.json` + `lessons.json` (`continuous_learn.learn_from_report` at fill end)
- `learned_fields.json` (`learning.py`)
- `record_replay` `replay_cache.json`
- `option_mappings.json` (gitignored Workday aliases)

None of these commit Fiber text. ~838KB experience.jsonl is a diary, not a fill engine.

### Gyms / monitors / gates

- ATS gym: 24 cases (`gym/ats/cases/`), `adversarial.py`, `detection_matrix.py`, `runner.py`, `score.py`
- FormFactory gym: vendor clone; only `improvement_cycle.py` calls it
- Battle gym: `workday_battle_multipage` + `run_battle_fill.py` — local PASS vs gold, `live_signoff: false`
- `reliability_gate.py` (headed NXP) + `reliability_gate.json`
- `regression_gates.gate_tier1` — **gym + `--skip-run` rescore of old artifact**
- `eval_suite.py` / `scorecard_fast.py`
- `progress_monitor.py` + `adapt_policy.py` + `PROGRESS.md` + `watch_progress.sh`
- `flight_recorder.py` — headed default ON
- `cycle_orchestrate.py` + `improvement_cycle.py` + `CYCLE_AGENTS.md` — second control plane
- Dashboard: `dashboard/server.py` prefers Playwright `fast_fill.py`, Flash ON by default

### Monster files (LOC)

| File | Lines | Job |
|------|------:|-----|
| `fast_fill.py` | **13 446** | Non-WD orchestrator + advance + Flash glue + dashboard CLI |
| `exp_workday_selectors.py` | **12 278** | Workday A–E + contact pack + `_gate_then_advance` |
| `verified_select.py` | **5 525** | searchSelect, fiber text, How-Heard, FoS settle |
| `ashby_widgets.py` | 2 749 | Ashby (no gym cases) |
| `flash_leftovers.py` | 2 262 | Leftover LLM |
| `field_map.py` | 1 914 | Classify + dummy compose |
| `gh_select.py` | 1 770 | Greenhouse |
| `page_progress.py` | 1 241 | Fingerprints + Ready |
| `field_lock.py` | 1 107 | Locks |
| `vision_judge.py` | 883 | Ready judge |
| `field_done.py` | 871 | Done oracle |
| `fill_contract.py` | 653 | Touch/commit/advance contract |
| `action_supervisor.py` | 649 | Per-action audit |

Three files ≈ **31k lines**. That is where dual paths hide.

---

## Dual oracles / redundancy map

**Unify-oracles is incomplete.** `field_done.py` claims to be the one done contract. Advance fallthrough is **fixed**; pack vs `field_is_done` still has parallel voters.

### Smoking gun: `_contract_advance_page` → `_gate_then_advance` — **FIXED 2026-08-13**

Contract not-ready now `return False` (no legacy Next). `_gate_then_advance` is SPA/wait implementation only, not a second voter. Historical snippet below is what the review found; do not re-introduce the fallthrough.

<details><summary>Pre-fix snippet (do not restore)</summary>

```1574:1605:scripts/fastfill/exp_workday_selectors.py
async def _contract_advance_page(page, report: dict, phase: dict) -> bool:
    """Advance via fill_contract when honest; else legacy gate."""
    ...
        if adv.ready:
            ...
            return True
        if adv.reason in ("filled_rows_not_honest", "wizard_incomplete"):
            phase["advance_contract_blocked"] = adv.reason
    ...
    return await _gate_then_advance(page, report, phase)
```

</details>

If `advance_page_if_ready` says **not ready** — including `filled_rows_not_honest` / `wizard_incomplete` — it only **logs** `advance_contract_blocked` and **still calls the legacy gate** *(pre-fix)*. Call sites were Phase C/D/E. Battle gym documented the symptom; **legacy no longer advances**.

That is not defense in depth. That is two governors, one of which is ignored.

`fill_contract.advance_page_if_ready` itself then calls `fast_fill.try_advance_if_page_complete` — a **third** Next-clicker — after `can_claim_ready`. So “unified advance” is: contract settle → honesty → Ready → *generic* try-advance → (on miss) Workday `_gate_then_advance` which re-does FoS settle + `listbox_still_open` + required-empty.

Contact page usually never reaches this: `_phase_b_contact` STOPs on `pack_incomplete` first (line ~6833). Dual advance is why **later pages / gym** lie; it is not why NXP is stuck on My Information.

### Same decision, two (or more) modules

| Decision | Voters | Disagreement mode |
|----------|--------|-------------------|
| Is this field filled? | `field_done.field_is_done*` vs `fill_verify.is_verified_fill_row` (`_is_verified_fill`) vs `action_judge` vs supervisor `OK` vs `verified=True` on the row | Pack can miss while `field_is_done` live-reprobe recovers State (Illinois hack). Addr2/county never get that reprobe. |
| Already correct → skip? | `fill_contract.verify_before_touch` vs `_lock_already_correct_skip` vs `gate_field_action` vs `action_judge.correct_skip` | Extra skip voters → reclaim blocked (wrong FoS chip / wrong autofill). Ontology unlock exists *because* this happened. |
| Page complete enough to Next? | Contact `pack_missed` vs `_required_empty_on_page` vs `form_gaps` vs `filled_rows_honest` vs `try_advance_if_page_complete` vs `_gate_then_advance` | Pack treats dummy-mapped Apt/County as required even if DOM does not. `form_gaps` then re-lists the same empties. |
| Listbox still open? | `verified_select.settle_before_advance` (contract) **and** `_gate_then_advance` FoS/`listbox_still_open` block (duplicated ~7120–7201) | Same settle twice; `fos_chip_override` can paper over a still-open portal. |
| Ready / Review? | `can_claim_ready` + `workday_wizard_incomplete` + `apply_live_vision_gate` + `gaps_block_ready` + leftover scan | Fail-closed (good). Also refuses Ready for essay leftovers that Flash was supposed to handle. |
| County widget type | `_WD_COMBOBOX_AIDS` includes `addressSection_regionSubdivision1` (CSS pack) vs `build_contact_fill_plan.combobox_aids` **omits** it → two_phase fills county as **text + fiber** | Live two-phase path types Sangamon into a searchSelect. Pack path would combobox. **Path drift from two fill engines.** |
| Should we Advance at all? | `field_lock.page_complete_should_advance` (unit only) vs everything above | Dead helper. Fourth definition of the same boolean. |

### Flash vs Layer 0

Dashboard / `run_fill_visible.sh` pass `--flash-leftovers`. `flash_leftovers` calls `field_lock.filter_locked_leftovers` (good) and flight-logs `flash_filter`. If addr2/county never verify, they **never lock**, so Flash is invited to retry Fiber-stubborn fields the pack already failed. Flash is a leftover LLM, not a Fiber committer. That is fighting, not filling.

### Batch fill

`batch_fill.batch_fill_simple` **is wired** (`apply_selector_pack`, `fill_from_extract`). Workday two-phase does **not** use it (widgets). `adapt_policy.keep_batch_fill` is a source-inspect “don’t regress” no-op. Not the live blocker.

---

## What is actually causing live FAIL

**Causal chain (NXP 2300Z, still the bar):**

1. Phase B contact pack (`WD_CONTACT_PACK` + `build_contact_fill_plan`) includes `addressSection_addressLine2` (Apt 1A) and `addressSection_regionSubdivision1` (Sangamon).
2. `_fill_automation_id` → `_fill_automation_id_impl` uses `fill_text_fiber_then_read(..., stubborn=True)` for those aids (`is_stubborn_text_field`).
3. Playwright `fill()` paints the DOM; Workday Fiber re-renders from React state; readback is empty. `fiber_text_commit` (`__reactProps$.onChange` + Tab keydown) is supposed to commit. **On live NXP it does not stick** (gym fiber-stub with `setInterval` wipe ≠ tenant Fiber / hydration).
4. County aggravation: two_phase `combobox_aids` does **not** include `regionSubdivision1`, despite `_WD_COMBOBOX_AIDS` and the ATS3-009 comment (“often Select One combobox”). Live county after Illinois cascade is typically promptOption, not a stubborn `<input>`. Fiber-text on a combobox → empty_readback by construction.
5. Missed rows with those aids stay in `pack_missed` (not `optional_miss`).
6. `can_advance = not pack_missed and not required_empty` → `advance_blocked_reason=pack_incomplete`, `verdict=FAIL`, **no Next click**. Honest. Correct FAIL-before-ADVANCE.
7. Illinois `countryRegion` was a *different* mechanism (`no_matching_option` / ArrowDown on button / intent-as-readback). Fixed 2300Z. Not the current blocker.
8. Phone country leftover is **noise** (US +1 chip already there; `field_done.filter_phone_country_false_empties` exists). `worked_here_before` `radio_not_found` is marked `optional_miss`. Neither should block if pack filter works; they are leftover theater.

**Symptoms that are not the mechanism:** empty_cycle STOP, lock_skip, gym Review, `PROGRESS.md` “next adaptive action”, Flash, Skyvern, vision_judge, learning_store, 15-approach bake-offs.

**If Apt is optional on NXP:** treating `addressLine2` as a pack-required aid is over-engineering that *would* block ADVANCE even after Fiber is fixed for County. `RELIABILITY_STATUS.md` already says “or mark truly optional.” Pack completeness ≠ required-visible. That is a real over-engineering cut.

**Gym Review ≠ live** because battle gym’s “fiber” is a scripted stub + `display:none` steps. `test_workday_search_select.py` asserts `"__reactProps" in src`. Green means the string exists.

---

## Over-engineering that HURTS

Concrete, file + function, not vibes.

1. **`_contract_advance_page` fallback** — extra voter that cannot actually block. Causes dishonest ADVANCE when `filled_rows_not_honest` (battle gym) and extra settle/FoS work when both run. Delete the fallthrough or delete the contract call.

2. **Two done oracles** — `field_done` vs `fill_verify.is_verified_fill_row`. Workday pack still uses `_is_verified_fill`. `filled_rows_honest` can then block contract-advance on rows the pack already counted. Unify-oracles without deleting `is_verified_fill_row` usage is a sixth oracle.

3. **Pack-required optional fields** — `WD_CONTACT_PACK` includes `addressSection_addressLine2`. Dummy always has Apt 1A. Empty readback → `pack_incomplete` even if the tenant does not require line 2. **Extra gate that blocks wrongly** if optional.

4. **County fill-mode drift** — `_WD_COMBOBOX_AIDS` vs `build_contact_fill_plan.combobox_aids`. Live path fiber-texts a combobox. That is complexity (two packs) causing the wrong algorithm, not “Fiber is hard.”

5. **Supervisor fail-closed on audit errors** — `fill_contract.commit_fill` sets `verified=False` / `STUCK` if `audit_fill_row` throws. A logging layer can un-verify a good Fiber commit (or skip reclaim). Disable exists (`FASTFILL_ACTION_SUPERVISOR=0`) because people already smelled this.

6. **Skip voters prevent reclaim** — `verify_before_touch` + lock + `already_correct_skip` + FoS family lock. Needed against thrash; over-applied it lock-skipped Arts-Other / wrong Major. `unlock_fos_if_intent_mismatch` is a patch on a patch. Still a live class.

7. **Gym as merge confidence** — `regression_gates.gate_tier1` runs adversarial + detection_matrix + `reliability_gate --skip-run`. `PROGRESS.md` can show `gym_pass: True` / `live_pass: False` and still spawn “fix the gym” agents. **Gym confidence is the over-engineering that most delays the Fiber fix.**

8. **Flash ON in dashboard while Layer 0 failed** — leftovers include Fiber fields; Flash fights empty widgets, burns steps, can reopen How-Heard. Not the pack_incomplete cause; it is why headed runs look like they are “doing stuff” after the real stop.

9. **`adapt_policy.probe_code_gaps`** — greps source for `fill_text_fiber_then_read` and declares the Fiber gap **closed**. Policy then says `live_headed_flight_log`. Correct destination, dishonest method: “string exists in file” ≠ “NXP readback non-empty.” Same lie as `test_workday_search_select.py`.

10. **Process theater that steals agent slots** — `exp_fill_strategies_1_3.py` (964), `exp_approaches_7_9.py` (517), `exp_semantic_leftover_match.py` (194), `improvement_cycle.py`, `cycle_orchestrate.py`, FormFactory gym, `ARCHITECTURE_REVIEW.md` (stale). None commit NXP county. `PROGRESS.md` / `GYM_VS_LIVE.md` / `BATTLE_GYM.md` / `LIVE_VISIBILITY.md` / `RELIABILITY_STATUS.md` are *useful* if you stop adding more of them.

11. **Monsters** — every addr2 fix must survive `fast_fill._fill_selector`, `exp_workday_selectors._fill_automation_id_impl`, and `verified_select.fill_text_fiber_then_read`, plus contract/supervisor/lock. Drift is guaranteed. Not the Fiber mechanism; it is why the county combobox flag exists in one list and not the other.

---

## Complexity that EARNS ITS KEEP

Do not delete these while chasing “simpler.”

| Keep | Why |
|------|-----|
| `button_gate` / `button_map` never-submit | Hard rule. Accidental Enter/Submit is a real past incident. |
| Dummy `run_identity.prepare_dummy_run` + `compose_fill_values` / `SHARED_FILL_POLICY` | PII boundary. Non-negotiable. |
| `field_lock` against overwrite | Illinois/FoS/How-Heard thrash was real. Lock-after-verified-readback is the no-thrash rule. |
| Workday `searchSelect` / `promptOption` in `verified_select` | Illinois only landed when we stopped treating State as fiber text. **Widgets need this.** |
| FAIL-before-ADVANCE (`pack_incomplete`, `required_empty`, `listbox_still_open`) | Honest metrics. The live FAIL is the correct verdict given empty Apt/County. |
| `fiber_text_commit` **as an algorithm** | Address line 2 *is* Fiber-controlled text. The implementation is insufficient on live, not unnecessary. |
| `page_progress` fingerprints / empty-cycle STOP | Stops “SUCCESS on page 1” and runaway Next. |
| `captcha_pause` headed human wait | Never-solve. Detection still has Akamai holes (`ARCHITECTURE_REVIEW` §1.4) — fix the sensor, don’t add a judge. |
| `flight_recorder` | Only live truth path when the browser and `report.json` disagree. |
| `reliability_gate.py` **headed** (`live_pass`) | Only gate that may mean NXP. |
| `vision_judge` as **Ready gate only** | Not a filler. Blocks false Ready. Keep DOM scan; don’t promote screenshot-LLM to actor. |
| `batch_fill_simple` for vanilla GH/unknown text | Speed; widgets stay sequential. Already wired. |
| FoS / How-Heard hierarchy helpers | Live Workday widgets; gym just over-claims them. |

Workday searchSelect, never-submit, dummy PII, and field locks are **necessary complexity**. The 7th oracle is not.

---

## Simplification plan (priority order)

North star: **one done, one advance, one lock, fiber for stubborn text, packs for widgets.**

| # | Action | ROI | Risk |
|---|--------|-----|------|
| 1 | **Fix live county widget class** — add `addressSection_regionSubdivision1` to `build_contact_fill_plan.combobox_aids`; fill via promptOption/`verified_select`, not `fiber_text_commit`. Prove on headed NXP + `flight.log`. | Highest. May clear half of `pack_incomplete`. | Low if you don’t touch State (already combobox). |
| 2 | **Fiber addr2 on live, not gym** — iterate `fiber_text_commit` against headed NXP readback only (native setter + onChange + Tab already there; likely need blur/input tracker/time-to-hydrate). Stop adding gym `setInterval` stubs. | Highest remaining. Apt 1A. | Medium (tenant-specific Fiber). |
| 3 | **Pack vs required** — `addressLine2` `optional_miss` unless `_required_empty_on_page` says required. Pack completeness must not exceed live required. | High if Apt is optional on NXP. | Low; keep County required if DOM says so. |
| 4 | **One advance** — `_contract_advance_page`: if contract not `ready`, `return False` (no `_gate_then_advance`). Pick **either** `advance_page_if_ready` **or** `_gate_then_advance` as the only Next clicker. Delete the other call path. | High honesty; unblocks unify-oracles. | Medium (battle gym currently relies on legacy fallthrough). |
| 5 | **One done** — Workday pack uses `field_is_done_from_row` / `field_is_done`; make `is_verified_fill_row` a deprecated wrapper. No new “honest” helpers. | High maintainability. | Medium (metric churn). |
| 6 | **One lock** — keep `field_lock` + ontology unlock-if-wrong. Do not add FILL_ONCE bus (`exp_approaches_7_9`). Supervisor THRASH should call the same lock, not a second skip list. | Medium (less reclaim bugs). | Low. |
| 7 | **Freeze gym** — no new `gym/ats/cases/*` until `reliability_gate` headed `reached_review`. Keep the honesty net (`false_complete_listbox_open`, `test_fill_contract`, `test_button_gate`, `test_honest_metrics`). Demote battle gym `fidelity: high` in humans’ heads (meta already `live_signoff: false`). | High (stops false confidence). | None. |
| 8 | **Archive bake-offs** — move `exp_fill_strategies_1_3.py`, `exp_approaches_7_9.py`, `exp_semantic_leftover_match.py` out of the hot path (or delete). Keep the one algorithm that landed (`fiber_text_commit` already copied). | Medium (agent-cycle ROI). | None. |
| 9 | **Demote process plane** — `adapt_policy` / `PROGRESS.md` must not dispatch code work from source-grep. `improvement_cycle` + FormFactory off the default lane. Flight log + headed gate only. | Medium. | None. |
| 10 | **Flash** — keep leftovers-only; do not send Fiber addr2/county to Flash. Dashboard may keep Flash for essays. | Medium (less fighting). | Low. |
| 11 | **Supervisor** — default-on is fine if it cannot un-verify; on `supervisor_error` do **not** fail-closed the row if `field_is_done` already passed. Or default OFF until Fiber works. | Medium (fewer false STUCK). | Low. |
| 12 | **Do not split `fast_fill.py` / `exp_workday_selectors.py` now** — extract after one advance + one done. A 13k-line split during a live FAIL is more theater. | — | High if done first. |

---

## What NOT to do

- **More bake-offs** (approaches 10–15, “ML leftover matcher,” CLIP vision filler). `exp_semantic_leftover_match.py` already exists and is unwired. Labels are not the NXP bug.
- **More gym HTML** — especially another “high fidelity” Workday SPA. Battle gym already plateaued (`BATTLE_GYM.md`). Gym cannot grow `__reactProps$` into a tenant.
- **More judges** — no vision-LLM actor, no second supervisor, no Agent5. `vision_judge` stays a Ready gate.
- **ML detector as filler** — `detection_matrix.py` scores static gold. Using it to click is a category error.
- **Another unify layer** that wraps the old ones and fallsthrough (`_contract_advance_page` pattern). Unify means **delete**.
- **Treating `gym_pass` or `reliability_gate --skip-run` as live.** Already documented; agents still do it.
- **Skyvern as the Workday contact engine.** Leftovers only.
- **Production-ready claims** after any of the above. Live bar is still `reached_review` on headed NXP with dummy PII and never-submit.

---

## Dead / unwired / theater (batch_fill-class)

| Asset | Status |
|-------|--------|
| `batch_fill.py` | **Wired now** (`apply_selector_pack` / extract). Not a corpse. |
| `exp_fill_strategies_1_3.py` | **Deleted** 2026-08-13. Fixtures inlined in `test_fiber_text_commit`. |
| `exp_approaches_7_9.py` | **Deleted** 2026-08-13. |
| `exp_semantic_leftover_match.py` | **Deleted** 2026-08-13. |
| `field_lock.page_complete_should_advance` | **Deleted** 2026-08-13. Live gate is `fill_contract` / `can_claim_ready`. |
| `adapt_policy.py` | Fiber empty_readback now `live_headed_flight_log` (no source-grep “wired”). |
| FormFactory gym | **Quarantined** — `improvement_cycle` only; not production fill. |
| `ARCHITECTURE_REVIEW.md` | Stale (8.3k `fast_fill`, July 31). Do not treat as current. |
| Skyvern `hybrid_fill` | Dashboard fallback if Playwright script missing. |
| `learning_store/experience.jsonl` (~838KB) | Appended after runs; does not fill Fiber. |
| `cycle_orchestrate` / `improvement_cycle` | Parallel control plane; not on `fast_fill` happy path. |
| `_gate_then_advance` | Kept as SPA/wait implementation; **not** called on contract not-ready. |

---

## Docs vs code

| Doc | Still true? |
|-----|-------------|
| `RELIABILITY_STATUS.md` | Yes — live FAIL, Illinois fixed, addr2/county `empty_readback`. |
| `GYM_VS_LIVE.md` | Yes — the honesty audit. Follow it. |
| `BATTLE_GYM.md` | Yes that gym ≠ live; **no** that local PASS implies filler health on NXP. |
| `LIVE_VISIBILITY.md` | Yes — use `flight.log`. |
| `PROGRESS.md` | Correctly `live_pass: false`; “no gym-fixable gap” is right. The monitor itself is optional. |
| `ARCHITECTURE_REVIEW.md` | Useful for never-submit / CAPTCHA holes; **file sizes and some bugs are stale**. |
| Fastfill SKILL layer list | Accurate; Workday exception accurate. |

---

## Bottom line for Yogesh

Over-engineering did **not** empty Address Line 2. Fiber did. Over-engineering **is** why county is typed as text, why Apt can block the pack if optional, why two advance functions disagree, and why green gyms keep getting built instead of a headed `flight.log` loop on those two aids. Cut voters until there is one done, one advance, one lock. Keep never-submit, dummy PII, searchSelect, and locks. Fix Fiber/combobox on live NXP. Do not add a tenth system.
