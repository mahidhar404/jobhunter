---
task_id: GH-RESUME-DROPZONE-P0
verdict: PARTIAL
posted_at: 2026-07-31T11:15:00Z
executor: cursor-executor
---

# Result: GH job-boards resume dropzone P0 (jobs 5 & 8 class)

## Verdict
PARTIAL — P0 resume + contact-wipe **closed**; sponsorship/custom-Q debt remains on Savant Bio

## Summary
Fixed Greenhouse **job-boards** `.file-upload` resume path for Savant Bio / Scout AI class boards:

1. **`resume_upload.py`:** `#resume` priority, GH UI verify after remount (`gh_upload_ui`), GH Attach click targets, skip cover-letter input, `already_uploaded_ui` short-circuit when selector pack pre-uploads.
2. **`fast_fill.py`:** `is_resume_attachment_row` — stop demoting `greenhouse_post_resume_reassert` contact rows when resume FileList probe lies empty; `_should_run_gh_post_resume_reassert` skips reassert when resume unverified.
3. **`gh_select.py`:** `is_post_resume_reassert_via()` helper + self_test.

## Retest (Savant Bio, 1 Chrome headless)
- Artifacts: `skyvern_runtime/real_job_results/ten_unseen_05_savantbio_retest/report.json`
- `resume_verified: true`, `resume_gate: verified`, `gh_upload_ui`
- Contact fields **kept** — `demoted_false_verified: []` (no post-resume wipe)
- `never_submit: true`
- Overall verdict FAIL — SPONSORSHIP label + custom questions (not P0)

## Unit tests
- `scripts/fastfill/test_resume_upload_gh.py` (new)
- `gh_select.self_test` updated

## Files changed (no commit per task)
- `scripts/fastfill/resume_upload.py`
- `scripts/fastfill/fast_fill.py`
- `scripts/fastfill/gh_select.py`
- `scripts/fastfill/test_resume_upload_gh.py`
- `skyvern_runtime/real_job_results/TEN_UNSEEN_RUN.md`

## Next
- P1: GH sponsorship label needle for Savant/Scout visa-status wording
- Optional Scout AI retest; serial jobs 9–10
