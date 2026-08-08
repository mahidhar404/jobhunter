---
task_id: TASK-20260731-103000-airwallex-zip-after-location
verdict: DONE
posted_at: 2026-07-31T10:43:57Z
executor: cursor-executor
---

# Result: TASK-20260731-103000-airwallex-zip-after-location

## Verdict
DONE

## Summary
RECONCILED (ea35ddfb supersedes 90f6f916). Airwallex posting has NO zip/postal field — zip_field_absent_on_form is correct (ashby_zip_absent=true); prior FAIL verdict conflated zip with separate Location demote-after-resume-reassert thrash. Fix: _location_committed_on_page probe in fast_fill demote path; skip fill_ashby_location_then_zip on reassert when zip absent + Location committed. Unit tests PASS (test_prefill_thrash_verify, test_verified_select, regression_gates). Residual: SALARY_EXPECTED demote is separate.

## Files changed
- scripts/fastfill/ashby_widgets.py
- scripts/fastfill/fast_fill.py
- scripts/fastfill/test_verified_select.py
- scripts/fastfill/test_prefill_thrash_verify.py

## Artifacts
- skyvern_runtime/real_job_results/ten_unseen_03_airwallex_zipfix/

## Blockers
- (none)

## Next tasks suggested
- Proceed ten-unseen job 4 (Ultralytics/Personio). Optional headed re-verify Airwallex Location hold after loc demote fix.

## Addendum — bridge conflict reconciliation

| Bridge run | Verdict | Key finding |
|------------|---------|-------------|
| [90f6f916](90f6f916-b498-476f-8ac1-8d7d3a3317d2) | FAILED | Location demoted `live_empty_after_claimed_verified` after resume reassert; zip never mounted (`ashby_zip_absent`) |
| [ea35ddfb](ea35ddfb-b06f-4aed-9bce-6c280ab9c1c9) | DONE | DOM probe + headed retest: **no zip field on form**; `zip_field_absent_on_form` guard is the correct fix |

**Resolution:** ea35ddfb **supersedes** 90f6f916 for task scope. Zip is N/A on this Airwallex posting — not a fill failure. The 90f6f916 FAIL conflated zip with a separate Location demote bug (now fixed in `fast_fill.py` + `reassert_ashby_contact_after_resume` skip path). ADDRESS_ZIP leftovers: **0** with `zip_field_absent_on_form`.
