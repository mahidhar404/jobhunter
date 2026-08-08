# Continuous learning (fastfill)

How each fill run improves the next — **without** fine-tuning DeepSeek.

> Data lives here (`learning_store/`) because `learning.py` already owns the
> policy-facts module name. Code: `scripts/fastfill/continuous_learn.py`.

## What improves

| Store | Improves |
|-------|----------|
| `experience.jsonl` | Structure memory: selector, type, label, host, value_shape |
| `selector_stats.json` | Prefer high-success selectors; demote chronic verify-miss |
| `lessons.json` / `lessons.md` | After UNFILLABLE_AFTER_2 → avoid retrying the same strategy |
| `../learned_fields.json` (via `learning.py`) | Cross-employer **policy** answers only |
| `../replay_cache.json` (via `record_replay`) | Tenant selector→type maps (no PII values) |

## Loop

1. **After fill** (`fast_fill` finalize → `continuous_learn.learn_from_report`):
   - Append verified / failed outcomes to `experience.jsonl` (sanitized)
   - Update per-platform selector success rates
   - Demote selectors with low success (≥2 fails or rate &lt; 0.35) and invalidate replay
   - Write lessons from `UNFILLABLE_AFTER_2` keys
   - Persist policy facts via `learning.record_learning` (allow-list only)
   - Refresh `record_replay` selector map

2. **Next fill (same platform/host)**:
   - `record_replay.apply_replay_map` ranks cached selectors by success rate
   - High-success learned selectors preferred before generic extract
   - Dummy test mode: type→value hints from experience resolve through `DUMMY_PROFILE`
   - `--flash-leftovers`: top similar past leftover answers injected into Flash prompt

3. **Failure learning**:
   - `field_attempt_log` still emits `UNFILLABLE_AFTER_2.md` + `FIXER_TRIGGER.md`
   - Continuous learn also appends a durable lesson (label pattern → avoid strategy)

## Safety

| Mode | What is stored |
|------|----------------|
| Test Mode ON (dummy) | Selectors + types + sanitized dummy values / placeholders |
| Test Mode OFF (real) | Selectors + types + success only — **never** raw emails/phones/passwords |

- Sanitize always replaces email/phone/SSN/password shapes with `{{EMAIL}}` etc.
- `never_submit` unchanged; never CAPTCHA; never invent EEO from real profile.

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --sanitize
skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --stats
skyvern_runtime/venv/bin/python scripts/fastfill/learning.py --sanitize   # policy store
skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py --sanitize
```

## Verify improvement over 2 runs

1. Run the same apply URL twice (dummy, headed or headless):

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headless \
  --out /tmp/fill_run1.json
skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --stats
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headless \
  --out /tmp/fill_run2.json
```

2. Expect run 2:
   - `report.continuous_learning.experience_appended` on both runs
   - `report.replay_filled_count` ≥ run 1 (or equal if pack already covered)
   - `learning_store/selector_stats.json` shows rising `success` for working selectors
   - With `--flash-leftovers`, run 2 prompt contains `PAST SIMILAR LEFTOVER ANSWERS` when prior leftovers existed

## Related

- Policy facts: `../learning.py` + `learned_fields.json`
- Replay cache: `../record_replay.py` + `replay_cache.json`
- Attempt / Fixer: `../field_attempt_log.py`
- Corpus mining (offline): `../offline/build_corpus.py` — separate; type labels only
