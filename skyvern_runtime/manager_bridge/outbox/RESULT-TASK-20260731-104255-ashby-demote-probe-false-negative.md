---
task_id: TASK-20260731-104255-ashby-demote-probe-false-negative
verdict: DONE
posted_at: 2026-07-31T11:05:08Z
executor: cursor-executor
---

# Result: TASK-20260731-104255-ashby-demote-probe-false-negative

## Verdict
DONE

## Summary
Root-caused demote_live_probe false negatives: id_still_empty from required_empty_on_page was demoting fields whose verified readback still matched intended (ADDRESS_CITY/LINKEDIN/SALARY on Ashby; same mechanism hit Personio resume). Fix: should_demote_claimed_text_fill now trusts committed claimed readback over stale required-empty ids; _record_demotion_failures skips false-positive rows. Unit tests updated. Headed Airwallex retest: 12/13 filled, 0 leftovers, zero live_empty_after_claimed_verified in field_attempts.jsonl, regression_gates PASS.

## Files changed
- scripts/fastfill/fast_fill.py
- scripts/fastfill/test_prefill_thrash_verify.py
- scripts/fastfill/test_honest_metrics.py

## Artifacts
- skyvern_runtime/real_job_results/ten_unseen_03_airwallex_demoteprobe/

## Blockers
- (none)

## Next tasks suggested
- Proceed ten-unseen jobs 9-10 serial; GH resume dropzone still open for jobs 5+8
