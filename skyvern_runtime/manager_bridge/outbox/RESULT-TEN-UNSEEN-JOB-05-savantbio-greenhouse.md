---
task_id: TEN-UNSEEN-JOB-05
verdict: FAIL
posted_at: 2026-07-31T10:52:00Z
executor: cursor-executor
---

# Result: TEN-UNSEEN-JOB-05 — Savant Bio (Greenhouse)

## Verdict
FAIL

## Summary
Job 5 headed fill complete (dummy-only, flash-leftovers, captcha-wait, refill×2). Form reached on Greenhouse job-boards. **9 filled / 26 leftovers**, coverage **0.257**. Resume upload failed (`no_resume_click_target`); `greenhouse_post_resume_reassert` wiped contact fields after resume attempt — name/email/phone/LinkedIn/work-auth all demoted. never_submit=true. Flash requested but not invoked (inpage leftovers path skipped).

## Files changed
- (none — run-only)

## Artifacts
- skyvern_runtime/real_job_results/ten_unseen_05_savantbio/report.json
- skyvern_runtime/real_job_results/ten_unseen_05_savantbio/run.log
- skyvern_runtime/real_job_results/ten_unseen_05_savantbio/FIXER_TRIGGER.md
- skyvern_runtime/real_job_results/ten_unseen_05_savantbio/UNFILLABLE_AFTER_2.md

## Blockers
- (none — CAPTCHA not seen)

## Next tasks suggested
- Fixer P1: Greenhouse resume dropzone click + post-resume field reassert order
- Continue **job 6** TodayTix (Lever) — in flight
- Optional: Personio retest after `ten_unseen_04_ultralytics/FIX_APPLIED.md`
