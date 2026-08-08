---
id: TASK-20260731-110600-gh-resume-dropzone-reassert-wipe
priority: P0
title: Fix Greenhouse resume dropzone click + post-resume contact-field wipe (jobs 5 + 8)
created_at: 2026-07-31T11:06:00Z
created_by: claude-manager
status: in_progress
---

# Fix Greenhouse resume dropzone click + post-resume contact-field wipe (jobs 5 + 8)

## Context

Prior task `TASK-20260731-104255-ashby-demote-probe-false-negative` is evaluated **PARTIAL**,
not DONE — the root-cause diagnosis and fix in `fast_fill.py` (`should_demote_claimed_text_fill`,
Location branch, LinkedIn recovery) are sound, independently spot-checked by Manager (logic
read, `regression_gates.py` re-run clean), but the task's explicit acceptance criterion of a
**headed retest on the real Airwallex page with zero repeat `live_empty_after_claimed_verified`**
was not done ("Headed retest deferred... fix is unit-verified against artifact readback
pattern" per the result). Unit tests passing proves the new logic behaves as intended against
synthetic fixtures — it does not prove the live DOM race that caused the original bug is
actually fixed. **Please run that headed retest** (`ten_unseen_03_airwallex_demoteprobe/`,
same URL as before) in the same session as this task if a Chrome slot is free, or as a quick
follow-up before this specific fix is considered closed.

This task is a **higher-priority, separate** issue you already surfaced yourselves in the
jobs 6-8 batch result: **two** ten-unseen jobs failed on the same Greenhouse pattern —

- Job 5 (Savant Bio, `ten_unseen_05_savantbio/`): coverage **0.257** (9/35). Resume upload
  failed with `no_resume_click_target` (see `resume_upload.py:328`), and
  `greenhouse_post_resume_reassert` (the large inline block in `fast_fill.py` around
  lines 4520-4980) then wiped name/email/phone/LinkedIn/work-auth that were already
  correctly filled.
- Job 8 (Scout AI, `ten_unseen_08_scoutai/`): coverage **0.25**, same resume debt.

Since two independent Greenhouse tenants hit the identical failure sequence (resume
dropzone click miss -> reassert pass wipes already-correct contact fields), this is a
shared Greenhouse-path bug, not a one-off. Note: `fast_fill.py`'s
`greenhouse_post_resume_reassert` appears to be a large, GH-specific inline reimplementation
that runs independently of the shared `resume_upload.py` primitive — worth checking whether
the wipe happens because the reassert pass re-probes fields using stale/pre-resume-upload
selectors that no longer match after the page re-renders post-upload, similar in flavor to
the just-fixed demote_live_probe issue but in a different code path.

## Acceptance criteria

- Root-cause why `no_resume_click_target` fires on these two Greenhouse tenants (dropzone
  selector coverage gap in `resume_upload.py`'s `_RESUME_FILE_SELECTORS` list, or a
  timing/visibility issue) — fix the resume upload path itself first.
- Root-cause why `greenhouse_post_resume_reassert` wipes already-correct contact fields
  after the resume step, and fix so a resume upload attempt (success or failure) never
  clears previously-verified name/email/phone/LinkedIn/work-auth values.
- Headed retest on **both** job 5 (Savant Bio) and job 8 (Scout AI) URLs shows resume
  uploads verified and no contact-field regression after the resume step.
- `field_attempts.jsonl` on retest shows no `greenhouse_post_resume_reassert`-attributed
  demotions of fields that were correct before the resume step.
- `regression_gates.py` PASS.

## Constraints

- Dummy profile + fixture resume only — never `profile.json` PII or `credentials.json`
- Never click Submit / final Apply (`never_submit: true`)
- Max **1 Chrome** — `pgrep -fl chromium\|chrome\|playwright` before headed fill; abort if >1
- EEO via DeepSeek + DUMMY_PROFILE only
- CAPTCHA: `--captcha-wait --captcha-timeout 180`; do not solve — pause for human
- Fill flags: `--headed --flash-leftovers --refill-passes 2 --hold-seconds 90`
- Do not start jobs 9-10 until this and the Airwallex retest (see above) are closed —
  both jobs 9/10 could plausibly land on Greenhouse too and hit the same bug
- **Yogesh's explicit instruction (2026-07-31T11:06Z): retest job 5 and job 8 ONCE each
  to confirm the fix, then stop.** Do not loop repeatedly on the same company/URL
  (Airwallex has already been retested 5 times across locfix/locfix2/locfix3/zipfix/
  demoteprobe — that pattern must not repeat here). If a single retest still fails,
  root-cause further from artifacts/logs rather than blindly re-running the same URL
  again; if genuinely stuck after one retest, report BLOCKED/FAILED with diagnosis and
  move to the next queue item rather than retrying a third+ time.

## Files to touch

- `scripts/fastfill/resume_upload.py` (resume dropzone click coverage)
- `scripts/fastfill/fast_fill.py` (`greenhouse_post_resume_reassert`, ~lines 4520-4980)
- Relevant `scripts/fastfill/test_*.py` if behavior changes

## Done when

Headed retest artifacts for both job 5 and job 8 URLs show verified resume upload + zero
contact-field regressions, `regression_gates.py` PASS, result posted via `post_result.py`.
