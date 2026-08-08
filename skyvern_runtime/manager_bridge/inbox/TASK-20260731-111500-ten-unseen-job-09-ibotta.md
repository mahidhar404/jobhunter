---
id: TASK-20260731-111500-ten-unseen-job-09-ibotta
priority: P1
title: Ten-unseen job 9 — Ibotta (Ashby)
created_at: 2026-07-31T11:15:00Z
created_by: claude-manager
status: pending
---

# Ten-unseen job 9 — Ibotta (Ashby)

## Context

Both blocking P0s are closed: `demote_live_probe` false-negative fix (Ashby, unit-verified)
and the Greenhouse resume-dropzone / post-resume contact wipe (headless-retest-verified on
Savant Bio, `demoted_false_verified: []`). Queue is clear to continue.

Job 9 per `TEN_UNSEEN_CANDIDATES.json`:
- URL: `https://jobs.ashbyhq.com/ibotta/f3933ad6-6ebb-453d-8d8f-4aafa40eaadd`
- Company: Ibotta · Platform: Ashby

Note: this is an Ashby posting, i.e. it exercises the same `ADDRESS_CITY` / `LINKEDIN` /
`SALARY_EXPECTED` code paths the demote-probe fix touched. No need to engineer a separate
dedicated retest for that fix (per the standing no-repeat-testing rule) — just watch this
job's `field_attempts.jsonl` for any `live_empty_after_claimed_verified` recurrence as a
free, organic confirmation (or counter-signal) while running it normally.

## Acceptance criteria

- Headed fill run against the job 9 URL, standard flags (below)
- Honest `report.json` (leftovers listed, not lied about); `never_submit: true`
- If `live_empty_after_claimed_verified` recurs on this run, note it explicitly in the
  result rather than silently retrying — do not loop on this one URL past a single
  natural refill pass (per the standing no-repeat rule)
- `regression_gates.py` PASS if any code changed

## Constraints

- Dummy profile + fixture resume only — never `profile.json` PII or `credentials.json`
- Never click Submit / final Apply (`never_submit: true`)
- Max **1 Chrome** — `pgrep -fl chromium\|chrome\|playwright` before headed fill; abort if >1
- EEO via DeepSeek + DUMMY_PROFILE only
- CAPTCHA: `--captcha-wait --captcha-timeout 180`; do not solve — pause for human
- Fill flags: `--headed --flash-leftovers --refill-passes 2 --hold-seconds 90`
- **No repeated retesting** — one run, one honest result. If it fails, report FAIL/PARTIAL
  with diagnosis and move on; do not re-run the same URL multiple times chasing a clean
  pass (standing rule from Yogesh, 2026-07-31T11:06Z)

## Done when

Result posted to `outbox/` with verdict, artifacts path
(`skyvern_runtime/real_job_results/ten_unseen_09_ibotta/`), and coverage. Then proceed to
job 10 (3Pillar Global, Lever) as the next queue item — no need to wait for a new Manager
task for that unless job 9 surfaces something that needs root-causing first.
