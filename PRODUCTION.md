# Production fill — job-hunter

Dummy autofill by default. **Never submits.** Dashboard **Start** (agent + tailor) is unchanged.

## Dashboard Test Mode

Header toggle (persisted in `localStorage`, default **ON**):

| Test Mode | Fast fill uses |
|-----------|----------------|
| **ON** | `DUMMY_PROFILE` + dummy resume (safe testing) |
| **OFF** | Real `profile.json` + `resumes/<job_id>/resume.pdf` or trusted upload |

Both modes: never auto-submit, never CAPTCHA. Reports include `test_mode` and `dummy`.
Real profile requires explicit opt-in (`FASTFILL_ALLOW_REAL=1` via toggle OFF). See `TOOLS.md` § Dashboard — Fast fill + Test Mode.

CLI fastfill from terminal remains **dummy-only** unless you pass `--real-profile --job-id ID` with the same env guard.

## Continuous learning

Two complementary layers (do not conflate):

| Layer | Module | Role |
|-------|--------|------|
| **Policy** | `scripts/fastfill/learning.py` + `learned_fields.json` | Cross-employer cheat-sheet facts (label → value) for leftover prefill. Sanitized; Test Mode ON may keep dummy text. |
| **Experience** | `scripts/fastfill/continuous_learn.py` + `learning_store/` | Per-run selector success rates, demotions, replay fingerprints, lessons. Hooked from `_finalize(close_step_log=True)` via `learn_from_report`. |

Each successful/partial `fast_fill` run improves the next via persisted experience
(not DeepSeek fine-tuning). See `scripts/fastfill/learning_store/LEARNING.md`.

| Artifact | Role |
|----------|------|
| `learning_store/experience.jsonl` | Verified fills: selector, type, label, host, value_shape (sanitized) |
| `learning_store/selector_stats.json` | Per-platform selector success rates; demote chronic misses |
| `learning_store/lessons.md` | UNFILLABLE_AFTER_2 → avoid strategy |
| `replay_cache.json` | Tenant selector→type replay (ranked by success) |
| `learned_fields.json` | Cross-employer **policy** facts only (`learning.py`) |

**Test Mode ON:** learn dummy shapes + sanitized values. **OFF:** structure only (no PII values).

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --stats
skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --sanitize
skyvern_runtime/venv/bin/python scripts/fastfill/learning.py --sanitize
```

Verify over 2 runs of the same URL: run 2 should show rising `replay_filled_count`
and/or `PAST SIMILAR LEFTOVER ANSWERS` in Flash prompts when leftovers recur.

## Canonical entry points

| Use case | Command |
|----------|---------|
| **Watch a headed fill** (Terminal + browser) | `./scripts/fastfill/run_fill_visible.sh 'APPLY_URL'` |
| **Batch / CI fill** | `skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headless --out results.json` |
| **Pre-flight (no browser)** | `skyvern_runtime/venv/bin/python scripts/fastfill/dry_run.py --check-consistency` |
| **Merge / CI gates** | `skyvern_runtime/venv/bin/python scripts/fastfill/regression_gates.py` |
| **Dashboard fast fill** | Test Mode **ON** → Fast fill (dummy); **OFF** → Fast fill (real) |

Full command reference: `TOOLS.md` and `.cursor/skills/job-hunter-fastfill/SKILL.md`.

## Safety (non-negotiable)

- `DUMMY_PROFILE` + per-run email via `prepare_dummy_run()` — default for CLI and dashboard Test Mode ON
- Real profile only with dashboard Test Mode **OFF** or CLI `--real-profile` + `FASTFILL_ALLOW_REAL=1`
- All clicks through `button_gate` — FINAL/submit refused
- CAPTCHA: headed pause for human; headless → blocker
- FAIL before ADVANCE when required fields empty

See `.cursor/skills/job-hunter-fill-safety/SKILL.md`.

## Typical headed workflow

```bash
# From repo root — opens Terminal.app + headed Chromium + live [fill-step] stream
./scripts/fastfill/run_fill_visible.sh 'https://jobs.ashbyhq.com/.../application'

# Artifacts land in skyvern_runtime/real_job_results/fill_live_*/
#   report.json  fill_steps.jsonl  fill_steps.md  run.log
```

Optional flags (direct CLI, same safety):

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL \
  --headed --refill-passes 2 --hold-seconds 45 --captcha-wait \
  --out skyvern_runtime/real_job_results/my_run/report.json
```

Flash leftovers (`--flash-leftovers`): raw `fast_fill.py` CLI stays **opt-in**
(0 LLM by default). **Dashboard Test Mode Fast fill** and
`run_fill_visible.sh` pass `--flash-leftovers` by default (disable with
`FASTFILL_FLASH_LEFTOVERS=0` or body `{"flash_leftovers": false}`).
Never submits either way.

## Regression fixtures

Kept under `skyvern_runtime/real_job_results/`:

- `fast_fill_ashby.json`, `fast_fill_gh_universal_smoke.json`, `fast_fill_workday.json` — cycle/replay fixtures
- `cycle_live_20260731T062102Z/` — Agent2/3/4 cycle artifacts for `regression_gates.py --cycle-dir`
- `ten_unseen_*` final runs — platform regression proofs

Cleanup log: `skyvern_runtime/real_job_results/PRODUCTION_CLEANUP.md`.
