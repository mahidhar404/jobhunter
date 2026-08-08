---
name: job-hunter-fill-safety
description: >-
  Hard safety rules for job-hunter autofill and form-fill experiments.
  Use when filling ATS forms, running fastfill/hybrid_fill, Skyvern, Playwright
  demos, dummy tests, or any job application browser automation in this repo.
---

# Job-hunter fill safety

## Non-negotiable

1. **Never submit** — do not click Submit / Apply-final / anything `button_map` classifies as `FINAL`. Stop at ready-for-review. Reports must keep `never_submit: true` and `submit_clicked: false`.
2. **Never solve CAPTCHA** or bypass bot-detection — stop, report `blocker`, leave.
3. **EEO via shared catalog + DeepSeek leftovers** — Prefill uses
   `dummy_answers.SHARED_FILL_POLICY` / `DETERMINISTIC_ANSWERS` /
   `shared_values()` (Male / No Hispanic / no disability / not a veteran;
   race stays Decline). Decline aliases remain fallback when the option list
   lacks preferred labels.
   **Dummy and real** both compose the **same** shared policy layer via
   `compose_fill_values` (unique identity/edu/experience stay per-profile).
   Never invent new EEO; never use real applicant demographics.
   Flash leftovers may invent consistent fictional demographics from shared
   catalog + resume only. Never use real applicant EEO.
4. **Dummy only for autofill/tests** — use `DUMMY_PROFILE` + fixture/run resume only. Never real `profile.json` PII, never real credentials, never tailored resumes.
5. **No thrash** — only fill empty / wrong / placeholder fields. Never clear/backspace/retype a value that already matches the intended dummy (log `already_correct_skip`).
6. **Never ADVANCE incomplete** — do not click Save and Continue / Next while required visible fields are empty. Prefer **FAIL before ADVANCE**. Validation banner or `advanced_incomplete` after ADVANCE = verdict **FAIL** (not PARTIAL, not SUCCESS).
7. **Honest metrics** — count a field filled only after verified read-back. Never label successful fills as `"stuck"`. Never claim SUCCESS when `validation_after_advance` is set.
8. **Flash off by default on raw CLI** — `flash_called` / `flash.invoked` only when `--flash-leftovers` / `flash_leftovers_requested` was set. Dashboard Start / Fast fill (dummy **and** real) and `run_fill_visible.sh` pass `--flash-leftovers` by default (disable via `FASTFILL_FLASH_LEFTOVERS=0` or `{"flash_leftovers": false}`). Flash-while-off is an eval FAIL.

## Allowed data sources

| Use | Source |
|-----|--------|
| Profile values (Test Mode ON / default) | Unique: `DUMMY_PROFILE` identity/edu/exp + address. Shared: `dummy_answers.SHARED_FILL_POLICY` / `shared_values()` via `compose_fill_values` |
| Profile values (Test Mode OFF / real) | Unique: real `profile.json` name/phone/email/links/education/experience + real address/resume. Shared: **same** `SHARED_FILL_POLICY` (EEO, work-auth, screening, notice, relocation, how-heard, salary canned, interest canned, consents). Experience never forced from dummy fiction. |
| Resume PDF (dummy) | per-run compiled PDF from `prepare_dummy_run` (fallback `fixtures/dummy_resume_de.pdf`) |
| Resume PDF (real) | tailored `resumes/<job_id>/resume.pdf` or `trusted_uploads/resume.pdf` |
| ATS account passwords (dummy + real) | `web_keys.json` via `scripts/fastfill/web_keys.py` (`Pswdpswd@912*{CompanySanitized}`). Lookup by host before create; upsert after successful create. **Not** `credentials.json` for autofill. |
| Forbidden (default) | `profile.json` contact/PII, `credentials.json` (manual/legacy only), tailored resume paths in Test Mode |

Real profile is allowed **only** when the dashboard **Test Mode** toggle is OFF
(or CLI `--real-profile` with `FASTFILL_ALLOW_REAL=1` + `TEST_MODE=0`). Default
autofill paths remain dummy-only. Dashboard fast fill never auto-submits in either mode.

**Shared policy (both modes):** EEO / screening / prefs come from one file —
`scripts/fastfill/dummy_answers.py` (`SHARED_FILL_POLICY`). Dummy and real
value maps must agree on every `SHARED_VALUE_TYPES` key.

## Universal ATS + non-ATS

Safety applies on **every** path: known ATS packs, Workday multipage, and
`platform==unknown` generic DOM. Unknown is first-class — never skip safety
gates because detect failed. Iframe/SPA fills (`iframe_ctx`) still route every
click through `button_gate`.

## FAIL signals (treat as hard fail)

- `advanced_incomplete: true`
- `validation_after_advance` (top-level or `workday.*`)
- `verdict: SUCCESS` combined with either of the above (scorecard/eval assert)
- `submit_clicked: true` or `never_submit` not true
- `flash_called` while Flash was not requested
- CAPTCHA / Akamai / Cloudflare → never solve. Headed: pause for human
  (`CAPTCHA detected — solve it in the browser, then press Enter here to continue`),
  then resume fill. Headless: `blocker` stop (not a fill SUCCESS)
- Resume/CV must upload+verify dummy PDF from `prepare_dummy_run`; missing = FAIL

## Before any browser fill

- [ ] Confirm path is dummy/test (CLI, dashboard **Fast fill (dummy)**, or hybrid dummy API)
- [ ] Confirm clicks go through `button_gate` / never-submit backstop
- [ ] Confirm page-complete gate before ADVANCE
- [ ] If CAPTCHA or real-submit UI appears → stop and report
- [ ] Confirm run identity came from `prepare_dummy_run` (random email + matching PDF)

## Agent browser (Cursor MCP)

When using `cursor-ide-browser` to inspect a live form: watch/verify only unless the human explicitly asks for manual dummy fills — still never submit, never CAPTCHA, never real PII.
