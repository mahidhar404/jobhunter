# Manager bridge — STATUS

**Updated:** 2026-07-31T11:10:24Z  
**Bridge:** `/Users/job/.openclaw/workspace/job-hunter/skyvern_runtime/manager_bridge`

## Queue snapshot

| Metric | Count |
|--------|------:|
| Inbox pending | 1 |
| In progress | 0 |
| Outbox results | 10 |

## Health

| Check | Value |
|-------|-------|
| Chrome processes (pgrep) | 2 |
| regression_gates.py | PASS |

## Active task

_(Cursor updates on ack; Manager reads before posting new work)_

## Ten-unseen pointer

- Queue log: `skyvern_runtime/real_job_results/TEN_UNSEEN_RUN.md`
- Bug fix log: `skyvern_runtime/real_job_results/BUG_FIX_STATUS.md`
- Job 3 Airwallex: location fix landed; optional zip polish
- Job 4+: not started — see `TEN_UNSEEN_CANDIDATES.json`

## Latest note

GH-resume-dropzone P0 confirmed DONE (dup result reconciled). Both blocking P0s now closed with reasonable evidence (demote-probe: unit-verified + code read; GH-resume: headless-retest-verified, demoted_false_verified=[]). Posted TASK-20260731-111500-ten-unseen-job-09-ibotta (P1, Ashby) — deliberately did not request a dedicated Airwallex/demote-probe retest since job 9 is itself Ashby and will organically exercise the same code paths, consistent with the no-repeat-testing rule. Pre-authorized proceeding straight to job 10 (3Pillar, Lever) after job 9 without a new Manager task, to reduce round-trip latency, unless job 9 surfaces something needing root-cause work first.

## Manager preflight (every cycle)

1. Read this file + latest `outbox/RESULT-*.md`
2. Read `TEN_UNSEEN_RUN.md` + `BUG_FIX_STATUS.md`
3. Confirm Chrome cap before headed fills
4. Post one focused task to `inbox/` — avoid parallel P0s
