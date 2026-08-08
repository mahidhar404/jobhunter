---
task_id: TEN-UNSEEN-JOB-04
verdict: PARTIAL
posted_at: 2026-07-31T10:48:00Z
executor: cursor-executor
---

# Result: TEN-UNSEEN-JOB-04 — Ultralytics (Personio)

## Verdict
PARTIAL

## Summary
Job 4 headed fill complete (dummy-only, flash-leftovers, captcha-wait, refill×2, verified_select). **Listing hash URL** (`#1893660`) false-positive — only "Search jobs" filled; **apply URL** reaches form. PERSONIO_PACK fills 17 contact fields (name, email, phone, LinkedIn, GitHub). Coverage **0.773** (17 filled / 5 leftovers). Resume upload (`documents.cv` / `#doc-input-cv`) failed verify — `resume_missing` / `live_empty_after_claimed_verified`. Also unclassified: `Available from*`, `Other links`. never_submit=true. Stale Chrome killed after interrupted hold; report finalized from hold_snapshot.

Job 3 zip debt **closed** in TEN_UNSEEN_RUN: Airwallex has no zip field (ea35ddfb DONE).

## Files changed
- (none — run-only)

## Artifacts
- skyvern_runtime/real_job_results/ten_unseen_04_ultralytics/report_apply.json
- skyvern_runtime/real_job_results/ten_unseen_04_ultralytics/report.json (hash URL false run)
- skyvern_runtime/real_job_results/ten_unseen_04_ultralytics/FIXER_TRIGGER.md
- skyvern_runtime/real_job_results/ten_unseen_04_ultralytics/UNFILLABLE_AFTER_2.md

## Blockers
- (none — CAPTCHA not seen)

## Next tasks suggested
- Post **ten-unseen job 5** (Savant Bio / Greenhouse) — serial queue, 1 Chrome
- Fixer P1: Personio resume upload verify + `available_from` / custom_attribute classify
- Update TEN_UNSEEN_CANDIDATES.json job 4 URL to apply path (not listing hash)
