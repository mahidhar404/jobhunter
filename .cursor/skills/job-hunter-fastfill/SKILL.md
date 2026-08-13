---
name: job-hunter-fastfill
description: >-
  Blazing-fast job-hunter form fill via scripts/fastfill (Layer 0/1 first,
  DeepSeek-V4-Flash only for leftovers). Use when running fast_fill.py,
  dry_run.py, hybrid_fill, dummy fill demos, Workday apply URLs, learning
  sanitize, eval_suite, record_replay, or optimizing ATS autofill speed.
---

# Job-hunter fastfill

Read `job-hunter-fill-safety` first. Dummy-only; never submit.

## Universal coverage (required)

Fast fill **must** cover:

1. **All major ATS** — Greenhouse, Workday, Lever, Ashby, iCIMS, plus detect/packs
   for SmartRecruiters, Workable, BambooHR, Recruitee, Personio, Jobvite, Taleo,
   SuccessFactors, Dayforce, UKG/UltiPro, Oracle Cloud HCM, Rippling, applytojob,
   Breezy, JobScore, Gem, Dover, Phenom (where URL/host patterns exist).
2. **Non-ATS / unknown** company career pages via **generic DOM** —
   `entry_prepass` → `GENERIC_SELECTOR_PACK` → extract → classify → widgets →
   learned → leftovers. **Never** treat `platform==unknown` as a dead end.

`report.coverage_path`: `workday_multipage` | `generic_dom` |
`selector_pack+generic_dom`. See `scripts/fastfill/coverage_matrix.md`.
Flash is opt-in only.

## Identity (every run)

Every `fast_fill` / Workday / dry_run path uses `run_identity.prepare_dummy_run()` →
`allocate_random_run_email()`:

- Email: `randommail6969+{random12}@gmail.com` (never reuse; persisted in `alias_state.json`)
- Same email compiled into the uploaded resume PDF (tectonic)
- Refuse sequential `+1`, `+2`, … tokens

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/run_identity.py --compile --json
```

## Preferred entry points

```bash
# Zero-LLM coverage check (no Flash call)
skyvern_runtime/venv/bin/python scripts/fastfill/dry_run.py --check-consistency

# Blazing-fast fill (0 LLM; leftovers shape in report["flash"], Flash NOT invoked)
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headed
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headless --out results.json
# Pause fill (headed default): top-right Pause fill / Continue fill overlay;
#   on hold + during CAPTCHA wait the button becomes Continue (resume);
#   CAPTCHA still never auto-solved (FILL-008); disable with --no-fill-pause
#   or FASTFILL_FILL_PAUSE=0
# CAPTCHA (headed default): human solves in browser, then Continue overlay
#   or Enter / .captcha_continue
# Same-session leftover refill while browser stays open (AUTO — no Enter):
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headed --hold-open \
  --flash-leftovers --refill-passes 2
# Optional debug only: --refill-wait-enter (default OFF)

# Optional: thin DeepSeek-V4-Flash ONLY for leftovers (default OFF — opt in; ≤5 steps)
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headless --flash-leftovers

# Eval suite (Flash OFF) + record/replay + scorecard
skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py --limit 3
skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py --platform unknown
skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py --list
# Multi-agent cycle (Flash ON, grounded essays, variety queue)
skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py --dry-run
skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py --limit 3 --headed
# Headed: hold-open + captcha wait + refill-passes=2 (same page; no BLOCKED×3)
# Headed cycle: hold-open + captcha wait + refill-passes=2 (same page; no BLOCKED×3)
skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py --sanitize
skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py --clear
skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py \
  --record-from skyvern_runtime/real_job_results/fast_fill_gh_universal_smoke.json
skyvern_runtime/venv/bin/python scripts/fastfill/scorecard_fast.py
skyvern_runtime/venv/bin/python scripts/fastfill/scorecard_fast.py \
  --dir skyvern_runtime/eval_results
skyvern_runtime/venv/bin/python scripts/fastfill/scorecard_fast.py --eval --gate
# Merge gates (no browser): unit honesty + scorecard + eval_summary safety
skyvern_runtime/venv/bin/python scripts/fastfill/regression_gates.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/regression_gates.py
# Live eval hard exits (opt-in; default suite stays diagnostic exit 0):
# skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py --strict-safety
# skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py --strict
```

- Demos / watching: **`--headed`** (CLI default for interactive)
- CI / batch: **`--headless`** (programmatic `run_fast_fill(url)` also defaults headless)
- Dashboard: **Fast fill** / **Start** — Playwright path preferred over hybrid;
  dummy and real both pass `--flash-leftovers` by default (disable: `{"flash_leftovers": false}`
  or `FASTFILL_FLASH_LEFTOVERS=0`)
- **`--flash-leftovers`**: raw CLI default **OFF**. Dashboard Start/Fast fill + `run_fill_visible.sh`
  default **ON**. When on, leftovers-only Skyvern/DeepSeek prompt; never submits.
- Visible watch: `./scripts/fastfill/run_fill_visible.sh URL` (Flash ON; disable with
  `FASTFILL_FLASH_LEFTOVERS=0`; Workday/NXP leftover Flash ON after the pack). Flight recorder ON by default → `flight.log` /
  `flight.jsonl` beside the run (see `LIVE_VISIBILITY.md`).

## Layer order (do not skip)

Non-Workday fill order matches `fast_fill.py`:

1. **ENTRY pre-pass** — `entry_prepass` via `button_gate` (never FINAL); thin ATS /
   unknown get up to 5 entry clicks; may follow Apply into a new tab
2. **Selector pack (Layer 0.5)** — platform pack or `GENERIC_SELECTOR_PACK` on best
   page/iframe (`apply_selector_pack_anywhere` / `fill_target`). Vanilla native
   text/email/tel/textarea, native `<select>`, and simple checkbox snap in via one
   `batch_fill_simple` evaluate; widgets (HOW_HEARD, searchSelect, combobox, file,
   radio, FoS, dates, phone-country, address country/state/city) stay sequential
   `_fill_selector`. Batch misses / empty readback fall back to sequential (fiber
   for stubborn Workday addressLine2/county). Never `asyncio.gather` of Playwright fills.
3. **Replay** — `record_replay.apply_replay_map` (selector→type cache; no PII values;
   invalidate on verify miss)
4. **Extract + classify** — Layer 0 HTML `autocomplete` + Layer 1 label/name/id
   heuristics (`field_map`); same vanilla batch-fill then sequential remainder;
   Greenhouse `gh_select`; custom widgets
5. **Learned allow-list** — `learned_fields.json` via `learning.py` (policy facts only)
6. **Flash leftovers only** — opt-in `--flash-leftovers` → `flash_leftovers.py` (≤5 steps)

Workday skips the generic extract path and runs multiphase
`exp_workday_selectors.workday_two_phase_on_page` instead.

**Iframe / SPA:** `iframe_ctx.py` — after Apply, poll page + child frames; ignore
job-alert/newsletter noise widgets; run extract/packs inside the best apply
iframe (classic iCIMS). Empty iframe extract → one top-page retry
(`fill_context.fallback=top_page_extract`). SPA wait before declaring
`generic_dom_no_fields`. Report: `entry_prepass.spa_wait`,
`entry_prepass.switched_tab`, `fill_context`.

## FAIL criteria (honest metrics)

- `advanced_incomplete` or `validation_after_advance` → verdict **FAIL** (not
  PARTIAL / not SUCCESS). Prefer FAIL-before-ADVANCE over dishonest next-click.
- `filled` only after verified read-back; never label successful fills `"stuck"`.
- `flash_called` / `flash.invoked` false unless `flash_leftovers_requested`.
- `never_submit` always true; `submit_clicked` never true; FINAL clicks = 0.
- Eval (`eval_urls.json` + `eval_suite.py`): Greenhouse reachable
  `coverage≥0.9` / `elapsed≤20s`; Workday page-complete-or-FAIL-before-ADVANCE;
  cross-suite zero dishonest ADVANCE + Flash-off. Reachability blockers
  (CAPTCHA/Akamai/login/404/…) skip fill-quality SLOs.
- Scorecard asserts never_submit + refuses SUCCESS+banner artifacts.
- Default `eval_suite` exit is diagnostic (0). `--strict-safety` / `--strict`
  for CI; merge lane `regression_gates.py` (Agent2/3/4 via `--cycle-dir`).
- `generic_dom_no_fields` leftover = inspect spa_wait/fill_context — not SUCCESS.

## SOP

1. Run `dry_run.py` (optional) to see Layer 0/1 coverage.
2. Run `fast_fill.py` with dummy profile/PDF (`--headless` for batch).
3. Inspect `report["leftovers"]` / `report["flash"]` → only then `--flash-leftovers` if needed.
4. After a Flash run that may have written learnings: sanitize (below).
5. Nightly / batch: `eval_suite.py` against `eval_urls.json` SLOs (`--limit`
   round-robins platforms; includes `unknown` non-ATS URLs).

## Workday (myworkdayjobs.com)

`fast_fill.py` detects Workday and runs multiphase via
`exp_workday_selectors.workday_two_phase_on_page`. Notes in `ats_notes/workday.md`.

- **Phase A (account gate):** Apply → Apply Manually → create/sign-in. Passwords from `web_keys` (`Pswdpswd@912*{Company}`); prefer Sign In when host has stored email+password. Never `FINAL`.
- **Workday leftover Flash ON** — after Layer 0/1 + two-phase pack, inpage/Skyvern Flash fills leftovers only (dates, FoS, How-Heard leftover, essays, screening). Never re-fills already-correct contact/address/How-Heard (cheat sheet / field_lock / steal-blocklist). Never Submit.
- **Phase B (contact):** verified fills; **no ADVANCE** if required empty / pack incomplete; phone-device-type = Mobile (never dial codes). How-Heard / School prompts: fiber `searchSelect` → `nudge_listbox` → Playwright click; then `gaps_after_save` blocks Ready if Save still shows required empties.
- **Phase C–E:** experience (resume + jobs) → voluntary disclosures (Decline) → self-id; stop at review.
- **Ready:** only when `page_progress.can_claim_ready` passes **and** live `vision_judge.judge_page` is complete (FAIL_BLANK / BLOCKED / AMBIGUOUS → not Ready; `vision_judge_live` on report). Hold alone never promotes Ready. Non-empty `gaps_after_save` also blocks Ready.
- Combobox: click → type → click matching option; **never Enter** (filter-Enter only on focused How-Heard search when already allowed).
- CAPTCHA / Akamai → never solve. Headed: human pause (Enter) then continue; headless → `blocker`.
- Resume upload must verify dummy PDF (`prepare_dummy_run`); missing resume = FAIL.
- Learned option aliases: `option_mappings.json` (gitignored); guard-words in `field_map` refuse dangerous mis-maps.
```bash
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py \
  'https://....myworkdayjobs.com/...' --headless \
  --out skyvern_runtime/real_job_results/fast_fill_workday.json
```

## Flash leftovers API shape

| Field | Meaning |
|-------|---------|
| `mode` | always `leftovers_only` |
| `invoked` | `true` only when `--flash-leftovers` actually called Skyvern |
| `max_steps` | capped at 5 |
| `already_filled` / `cheat_sheet` | Layer 0/1 fills — Flash must not re-derive |
| `leftovers` | unresolved `flash_candidate` fields only (truncated) |
| `never_submit` | always `true` |

## Eval + replay

- **Eval:** `eval_suite.py` → artifacts under `skyvern_runtime/eval_results/`
  (`eval_<platform>_NN.json` + `eval_summary.json`). Flash forced OFF.
  `--limit` diversifies across platforms; `--platform` filters (incl. `unknown`).
  Exit 0 is diagnostic; `--strict-safety` / `--strict` for hard gates.
- **Gates:** `regression_gates.py` — unit honesty + `scorecard --eval --gate` +
  eval_summary safety; optional `--cycle-dir` for Agent2/3/4 merge blockers.
- **Replay:** `replay_cache.json` stores selector→type keyed by platform+host+path
  fingerprint. Written after verified fills; applied before extract; no values.
  CLI: `--list` / `--clear`.

## Learning hygiene

`learned_fields.json` may store **only** cross-employer reusable **policy** facts. Never learn emails, phones, passwords, essays, salary, addresses, or other PII.

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/learning.py --sanitize
```

## Continuous learning (experience loop)

Each fill improves the next via `continuous_learn` → `learning_store/`
(experience.jsonl, selector_stats, lessons) + ranked `record_replay`. Not ML
fine-tuning. Test Mode ON = dummy shapes; OFF = structure only (no PII values).

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --stats
skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --sanitize
```

See `scripts/fastfill/learning_store/LEARNING.md`.

## Action supervisor (per-action audit)

After every fill/click/select, `action_supervisor.py` re-reads DOM readback and
judges before the next action:

| Verdict | Behavior |
|---------|----------|
| **OK** | Continue |
| **THRASH** | Lock field + skip (no retry) — thrash_rewrite or N touches same value |
| **WRONG** | One corrective re-fill, re-audit |
| **STUCK** | Log to report `supervisor_stuck[]`, mark unverified |

- Wired in `_fill_selector` (pack/generic) and Workday `_fill_experience_text_field`
- Logs: `{attempt_dir}/action_audit.jsonl` + `fill_steps` rows with `action=action_audit`
- Disable: `FASTFILL_ACTION_SUPERVISOR=0`; thrash N: `FASTFILL_SUPERVISOR_THRASH_N=3`

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/test_action_supervisor.py
skyvern_runtime/venv/bin/python scripts/fastfill/action_supervisor.py --self-test
```

## Flight recorder (live decision trace)

When the headed browser looks wrong but reports are opaque, turn on the
**flight recorder** — chronological why/what for every meaningful gate:

- **Files:** `{out_dir}/flight.jsonl` + `{out_dir}/flight.log` (also streams `[flight NNNN]` lines)
- **Default:** ON when `--headed` / `run_fill_visible.sh`; OFF headless
- **Force:** `FASTFILL_FLIGHT=1` or `--flight-recorder`; off: `FASTFILL_FLIGHT=0` / `--no-flight-recorder`
- **Hooks:** `fill_contract` verify/commit/advance, `field_lock` leftover drops,
  `page_progress` empty-cycle STOP, Workday `pack_incomplete`, flash EEO filter

```bash
# Visible run (flight ON by default)
./scripts/fastfill/run_fill_visible.sh 'https://….myworkdayjobs.com/…'
# Then paste back: skyvern_runtime/real_job_results/fill_live_*/flight.log

skyvern_runtime/venv/bin/python scripts/fastfill/flight_recorder.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/test_flight_recorder.py
```

See `scripts/fastfill/LIVE_VISIBILITY.md` for browser→log mapping and remaining blind spots.

## Progress monitor (adaptive next fix)

Source of “what's happening” + one next adaptive action. **Gym green ≠ live win** (`gym_pass` never sets `live_pass`).

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/progress_monitor.py
skyvern_runtime/venv/bin/python scripts/fastfill/progress_monitor.py --json
./scripts/fastfill/watch_progress.sh
```

Writes `scripts/fastfill/progress_state.json` + `PROGRESS.md`. Policy: overwrite/lock_skip → tighten lock; empty_cycle → page_progress budgets; empty_readback addr2/county → fiber_text_commit; Illinois → state pack; FoS steal → ontology unlock-if-wrong; vanilla → keep `batch_fill`. When no gym-fixable gap remains: **live headed flight.log required**.

## Key modules

| Module | Role |
|--------|------|
| `fast_fill.py` | Orchestrator (Workday multipage + optional Flash) |
| `batch_fill.py` | Instant Layer 0/1 vanilla fill (one `page.evaluate`); widgets sequential |
| `flight_recorder.py` | Live decision trace (`flight.jsonl` / `flight.log`) — headed default ON |
| `progress_monitor.py` | Adaptive progress: scan flight/gym/gate → `PROGRESS.md` + one next fix |
| `adapt_policy.py` | Highest-ROI next action (gym ≠ live) |
| `page_progress.py` | Advance gates + `can_claim_ready` / `apply_live_vision_gate` |
| `vision_judge.py` | DOM `judge_page` + screenshot heuristic (Ready gate) |
| `web_keys.py` | Per-site ATS passwords (`web_keys.json`) |
| `coverage_matrix.md` | Universal ATS + non-ATS path table |
| `iframe_ctx.py` | Apply iframe / SPA form discovery |
| `exp_workday_selectors.py` | Workday Phase A–E |
| `flash_leftovers.py` | Leftovers-only prompt + capped Skyvern invoke (grounded JD + shared policy + run unique identity/edu; answers essays) |
| `fill_attribution.py` | Prefill vs LLM attribution (`analyze_fill_attribution`) |
| `vision_judge.py` | Agent2 COMPLETE schema (zero blanks; beside `after_fill.png`) |
| `cycle_orchestrate.py` | Variety Test→Verify→Fix cycle driver |
| `CYCLE_AGENTS.md` | Agent1–4 Task prompts |
| `record_replay.py` | Tenant selector map cache |
| `eval_suite.py` / `eval_urls.json` | Fixed SLO eval (ATS + unknown) |
| `scorecard_fast.py` | Coverage + honest ADVANCE asserts |
| `regression_gates.py` | Merge/CI gates (unit + scorecard + Agent2/3/4) |
| `field_map.py` | Classify + unique `DUMMY_PROFILE` identity; `compose_fill_values(shared, unique)` |
| `dummy_answers.py` | **Shared source of truth** — `SHARED_FILL_POLICY` / `shared_values()` / `DETERMINISTIC_ANSWERS` (identical for dummy + real) |
| `captcha_pause.py` | Headed CAPTCHA human pause (Enter / sentinel) |
| `fill_pause.py` | Headed in-page Pause/Continue overlay between field actions |
| `action_supervisor.py` | Per-action audit loop (OK/THRASH/WRONG/STUCK) → `action_audit.jsonl` |
| `action_judge.py` | Lightweight verdict helper (`correct_skip` / `thrash_rewrite`) |
| `button_gate.py` | Never-submit click gate |
| `run_identity.py` | Per-run random email + resume compile; `prepare_real_run` composes shared + unique |
