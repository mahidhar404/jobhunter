---
id: TASK-20260731-104255-ashby-demote-probe-false-negative
priority: P0
title: Fix demote_live_probe false-negative — now confirmed cross-platform (Ashby + Personio)
created_at: 2026-07-31T10:42:55Z
created_by: claude-manager
status: in_progress
updated_at: 2026-07-31T10:49:30Z
---

# Fix demote_live_probe false-negative — now confirmed cross-platform (Ashby + Personio)

**ESCALATED P1 -> P0, 2026-07-31T10:49:30Z:** this task sat un-acked in `inbox/` while
`TEN-UNSEEN-JOB-04` (Ultralytics/Personio) ran anyway and hit the **exact same failure
signature** on a **third field type, on a different platform**: `resume_missing` /
`live_empty_after_claimed_verified` on Personio's resume upload
(`skyvern_runtime/real_job_results/ten_unseen_04_ultralytics/`), directly costing that
job's coverage (17/22 filled, PARTIAL). This is no longer an Airwallex/Ashby-specific
issue — it is a shared `fast_fill.py` mechanism affecting multiple platforms and field
types (combobox, plain text, file upload). **Please do not run job 5 until this is
root-caused** — it will very likely cost coverage there too.

## Context

Prior task `TASK-20260731-103000-airwallex-zip-after-location` is confirmed **DONE** — independently re-verified (not just trusted from the result file): `ashby_zip_absent=true` in `ten_unseen_03_airwallex_zipfix/report.json`, zero `ADDRESS_ZIP` entries anywhere in that run's `field_attempts.jsonl`, and a fresh independent run of `regression_gates.py` passed. No follow-up needed on zip.

That same retest surfaced a different, more consequential issue. `fast_fill.py`'s post-fill re-verification pass (`demote_live_probe`, `reason: live_empty_after_claimed_verified` — see `fast_fill.py` around lines 2843, 3800-3900, and 6660-6700) demoted **3 structurally different field types** on the same Airwallex Ashby posting, reproduced across **two separate runs** in the same artifact set (`run_id 4e8717d625c9` and `run_id 31006062f6d6`):

- `ADDRESS_CITY` (the Location combobox — both a plain-label selector and a scoped-selector variant): 4 + 2 fails
- `LINKEDIN` (plain text input): 2 fails
- `SALARY_EXPECTED` (plain text input): 2 fails

Full detail: `skyvern_runtime/real_job_results/ten_unseen_03_airwallex_zipfix/UNFILLABLE_AFTER_2.md` and `field_attempts.jsonl`.

Because these are structurally unrelated widgets (a combobox vs. two plain text inputs) all failing the same *later* live re-check with the identical error signature — and the error name itself says `claimed_verified`, meaning the initial fill+verify succeeded — the shared `demote_live_probe` mechanism itself is the more likely fault, not each field's own fill logic. Recommend root-causing there first rather than patching each field type separately.

## Acceptance criteria

- Root-cause why fields that were filled and initially verified read empty on the later live-probe re-check. Plausible candidates to rule in/out: (a) the probe runs before a debounced React re-render settles (timing/race), (b) a stale selector is being re-used after the page re-rendered (e.g. after the Location combobox commit, other field DOM nodes get remounted), (c) a genuine site-side reset unrelated to our code. If (c), document that distinction clearly — it needs a different response than a timing/selector bug.
- Fix lands at the actual root-cause location (most likely in `fast_fill.py`'s demote/live-probe logic, not scattered per-widget patches).
- Headed retest on the same Airwallex URL shows `ADDRESS_CITY` / `LINKEDIN` / `SALARY_EXPECTED` are not falsely demoted (either genuinely correct with no thrash, or an honest leftover only if truly unfillable — never a false demote of an actually-correct value).
- `field_attempts.jsonl` on retest shows no repeat `live_empty_after_claimed_verified` for these 3 field types.
- `regression_gates.py` PASS.

## Constraints

- Dummy profile + fixture resume only — never `profile.json` PII or `credentials.json`
- Never click Submit / final Apply (`never_submit: true`)
- Max **1 Chrome** — `pgrep -fl chromium\|chrome\|playwright` before headed fill; abort if >1
- EEO via DeepSeek + DUMMY_PROFILE only
- CAPTCHA: `--captcha-wait --captcha-timeout 180`; do not solve — pause for human
- Fill flags: `--headed --flash-leftovers --refill-passes 2 --hold-seconds 90`
- Do not re-run jobs 1-2 unless explicitly asked

## Files to touch

- `scripts/fastfill/fast_fill.py` (primary — `demote_live_probe` / `live_empty_after_claimed_verified` logic; see line references in Context)
- `scripts/fastfill/test_honest_metrics.py` or `scripts/fastfill/test_page_progress.py` (add/adjust a test if the demote-probe's behavior changes)

## Done when

Headed retest artifacts under `skyvern_runtime/real_job_results/ten_unseen_03_airwallex_demoteprobe/` (or similar) show zero false demotes for these 3 fields, with `regression_gates.py` PASS. Result posted via `post_result.py`.

**If root cause turns out to be a genuine, unfixable site-side reset** (not a bug in our code): document that clearly instead, verdict PARTIAL, and recommend proceeding to ten-unseen job 4 (Ultralytics/Personio, per `TEN_UNSEEN_CANDIDATES.json`) with this noted as a known Airwallex-specific limitation rather than blocking further progress on it.
