# Live fill visibility (flight recorder)

**Goal:** When the headed browser looks like it’s cycling / doing nothing / overwriting, map that eye-ball event to a concrete decision line — not a post-hoc summary.

Dummy-only. Never submit.

---

## What already existed (and what was still blind)

| Artifact | What it shows | Blind spot |
|----------|---------------|------------|
| `fill_steps.jsonl` / `.md` + `[fill-step]` stdout | Ordered actions + before→after | Often missing *why* (gate kind / supervisor / advance budget) |
| `action_audit.jsonl` | Per-action OK/THRASH/WRONG/STUCK | Separate file; easy to miss while watching the browser |
| `field_attempts.jsonl` / `UNFILLABLE_AFTER_2.md` | Fail counts per field | After-the-fact Fixer queue, not live “what now?” |
| `report.json` | End-state leftovers / verdict / gates | Not chronological; arrives after the pain |
| `reliability_gate.json` / `RELIABILITY_STATUS.md` | Gate pass/fail rollup | Summary of a run, not mid-run decisions |
| `learning_store/` | Cross-run lessons / selector stats | Not live per-field decisions |
| LLM `tracing.py` (`FASTFILL_TRACE`) | Flash/LLM prompts | Not Layer 0/1 DOM decisions |

**Main blind spots this closes:** lock_skip vs rewrite, verify-before-touch touch reason, commit_fill + supervisor + field_done in one line, pack_incomplete STOP, empty-cycle STOP, flash leftover drops.

---

## Reproduce a live run with full trace

```bash
# Preferred: Terminal.app + headed + flight ON (default for visible wrapper)
./scripts/fastfill/run_fill_visible.sh 'https://….myworkdayjobs.com/…'

# Or inline / CLI
FASTFILL_FLIGHT=1 skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL \
  --headed --flash-leftovers --refill-passes 2 --hold-seconds 45 \
  --out skyvern_runtime/real_job_results/fill_live_manual/report.json

# Force on headless / force off headed
FASTFILL_FLIGHT=1 … --headless …
FASTFILL_FLIGHT=0 … --headed …          # or --no-flight-recorder
```

Artifacts land next to `report.json`:

- **`flight.log`** — paste-friendly one-liners (`[flight 0042] …`)
- **`flight.jsonl`** — same events as structured JSON
- Still also: `fill_steps.jsonl`, `action_audit.jsonl`, `run.log` (from `run_fill_visible.sh`)

Disable stream noise: `FASTFILL_FLIGHT_STREAM=0` (files still written).

---

## Map “what I saw in the browser” → log lines

| What you saw | Look for in `flight.log` / `[flight]` |
|--------------|----------------------------------------|
| Field already correct but filler reopens / retypes | `action=touch` + `gate=verify_before_touch:touch(…)` **or** missing prior `gate=…:skip` |
| Filler skips a field that looks empty | `action=skip` + `gate=lock_skip` / `already_correct_skip` + `readback="…"` |
| Same field rewritten repeatedly | Repeated `action=fill…` same `aid=` then `gate=commit_fill:THRASH` or `supervisor` |
| Spinner / “doing nothing” then stop | `action=STOP` + `advance=STOP(empty_cycle\|max_settles\|pack_incomplete)` |
| Save and Continue never clicked | `advance=BLOCKED(pack_incomplete\|required_fields_empty\|listbox_still_open)` |
| Flash ignored EEO / locked leftovers | `event=flash_filter` (`hold_eeo` / `drop_locked_leftovers`) |
| Contact page stuck (Workday) | `event=pack_miss` + `advance=STOP(pack_incomplete)` + `extra.pack_missed` |

Correlate clocks: flight `ts` is UTC; watch the same second as the browser flinch.

---

## Example good trace (shape)

```
[flight 0001] 23:01:02 — layer=fast_fill — intent=— action=run_start gate=flight:on(headed=True) …
[flight 0012] 23:01:18 contact layer=fill_contract EMAIL aid=email intent="{{EMAIL}}" action=skip gate=verify_before_touch:skip_lock(already_correct_skip) readback="{{EMAIL}}" …
[flight 0018] 23:01:24 contact layer=workday_contact ADDRESS_STATE aid=addressSection_countryRegion intent="Illinois" action=fill_select gate=commit_fill:OK(state_committed) readback="Illinois" …
[flight 0025] 23:01:31 contact layer=fill_contract … action=touch gate=verify_before_touch:touch(empty_readback) readback=— …
[flight 0040] 23:02:05 contact layer=workday_contact — intent=— action=STOP gate=pack:miss(pack_incomplete) advance=STOP(pack_incomplete) …
[flight 0041] 23:02:06 — layer=page_progress — action=STOP gate=budgeted_progress:STOP(empty_cycle) advance=STOP(empty_cycle) …
```

---

## Top 5 blind spots this still doesn’t cover

1. **Pixel-perfect UI chrome** — open listbox / toast / spinner without a gate firing (use headed step screenshots in `steps/` when present).
2. **Intra-widget micro-moves** — every ArrowDown / filter keystroke inside a combobox (only commit / settle / reclaim are logged).
3. **Cross-frame SPA races** — iframe swap mid-action may look like “nothing” with no flight line until the next gate.
4. **Human Pause / CAPTCHA wait** — overlay hold time is thin; see `[fill-step]` captcha/pause rows + `run.log`.
5. **Skyvern Flash internal steps** — Flash invoke is summarized; Skyvern’s own click stream is not mirrored into `flight.jsonl`.

---

## Self-test

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/flight_recorder.py --self-test
# or
skyvern_runtime/venv/bin/python scripts/fastfill/test_flight_recorder.py
```
