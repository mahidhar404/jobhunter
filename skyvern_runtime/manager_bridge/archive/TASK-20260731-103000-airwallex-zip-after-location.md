---
id: TASK-20260731-103000-airwallex-zip-after-location
priority: P1
title: Review Airwallex zip-after-location commit path
created_at: 2026-07-31T10:30:00Z
created_by: claude-manager
status: in_progress
---

# Review Airwallex zip-after-location commit path

## Context

Ten-unseen **job 3** (Ashby / Airwallex).

- URL: `https://jobs.ashbyhq.com/airwallex/089d131a-0b3c-4f5d-9eb4-db8643eb42fc`
- Prior artifacts: `skyvern_runtime/real_job_results/ten_unseen_03_airwallex_locfix/`
- Location word-by-word select is **fixed** (`option_clicked: true` for Springfield, IL)
- Residual from last run: `zip_field_not_found_after_location` in some passes; HOW_HEARD had a fill_error (Ashby should skip `gh_select`)

Read `TEN_UNSEEN_RUN.md` and `field_attempts.jsonl` in locfix dir before changing code.

## Acceptance criteria

- After location commit (Springfield, Illinois, United States), zip field is discovered and filled with dummy zip from profile
- `field_attempts.jsonl` shows zip attempt with verified read-back (not just type-without-select)
- Location thrash guards still fire (`location_already_committed_skip` when appropriate)
- Headed retest writes artifacts to `skyvern_runtime/real_job_results/ten_unseen_03_airwallex_zipfix/`
- `skyvern_runtime/venv/bin/python scripts/fastfill/regression_gates.py` exits 0 if code changed

## Constraints

- Dummy profile + fixture resume only — never profile.json PII or credentials.json
- Never click Submit / final Apply (`never_submit: true`)
- Max **1 Chrome** — run `pgrep -fl chromium\|chrome\|playwright` before headed fill; abort if >1
- EEO via DeepSeek + DUMMY_PROFILE only
- CAPTCHA: `--captcha-wait --captcha-timeout 180`; do not solve — pause for human
- Fill flags: `--headed --flash-leftovers --refill-passes 2 --hold-seconds 90`
- Do not re-run jobs 1–2 unless explicitly asked

## Files to touch

- `scripts/fastfill/ashby_widgets.py` (primary — zip after location)
- `scripts/fastfill/test_verified_select.py` (add/adjust test if behavior changes)
- Optional: `scripts/fastfill/verified_select.py` only if shared select logic needs tweak

## Done when

Headed retest report shows **zero zip leftover** (or honest BLOCKED with CAPTCHA note in outbox). Result posted via `post_result.py` with artifact path and verdict DONE or BLOCKED.

**Alternate if zip is already fixed:** Document evidence from locfix3 artifacts, post PARTIAL with recommendation to proceed to **ten-unseen job 4** (see `TEN_UNSEEN_CANDIDATES.json`).
