# job-hunter — Full Architecture Review

**Date:** 2026-07-31
**Scope:** Entire repo at `/Users/job/.openclaw/workspace/job-hunter`, branch `cursor/blazing-fast-fill`, working-tree state (staged + unstaged + untracked). Both live engines reviewed: the newer Playwright-based deterministic engine (`scripts/fastfill/`, ~33K lines) and the older Skyvern/LLM-driven engine (`skyvern_runtime/`).
**Purpose:** Handoff document for a coding agent (Cursor) to act on. Every finding below is anchored to a file:line and includes a concrete, actionable fix. Findings are organized by severity, not by discovery order.
**Method:** Direct line-by-line review of every safety-critical primitive (PII boundary, never-submit gate, CAPTCHA handling, identity/alias system, advance-incomplete gate, resume verification) by the reviewing session itself, plus five parallel deep-dive audits of the large mechanical subsystems (Workday flow, main orchestrator, ATS widget handlers, LLM-leftover/judging layer, eval/test infra). All git diffs against the last known-good state were read in full.

---

## 0. Executive Summary

**Status: all 5 planned deep-dive audits complete, plus full direct review of the safety-critical core. 28 prioritized, actionable findings (§7).**

The good news first: the *foundational* safety primitives — the PII/dummy-identity boundary (`run_identity.py`, `field_map.load_profile`), the never-submit click gate's core logic (`button_gate.py`/`button_map.py`), the CAPTCHA human-pause wait loop's core logic (`captcha_pause.py`), the advance-while-incomplete gate (`page_progress.py`), and resume-upload verification (`resume_upload.py`) — are genuinely well-engineered. They show real iteration against observed live failures, hard fail-closed defaults, and defense-in-depth. This is not vibe-coded scaffolding; it's a mature safety layer, and the deep-dive audits confirm no code path in the ~33K-line codebase clicks a FINAL/submit control without going through it, and no code path presses Enter inside a form field (the exact mechanism that caused a real accidental submission in this project's earlier engine).

But that core is not the whole system, and several places built *on top of* those primitives don't fully honor what they promise:

1. **An operational/governance incident, not a code bug, that already happened.** An unsupervised multi-agent Cursor swarm ran up to 8 headed Chrome instances simultaneously with 3600s holds on an 8GB Mac and crashed it (§1.1). The fix that followed has three independent holes: a code path that bypasses it entirely (§1.2), a check-then-launch race in the fix itself (§1.5), and browser cleanup that isn't guaranteed to run on most error paths (§1.5) — meaning this incident class is still very much reproducible, not closed. The broader coordination model (markdown files as a mutex/queue between concurrent autonomous agents, §1.3) is structurally fragile on top of that.
2. **A real detection gap in the CAPTCHA safety net.** The human-pause mechanism itself is sound, but its only sensor (`iframe_ctx.visible_captcha_challenge`) can't see Akamai, PerimeterX, DataDome, or Arkose challenges — and even where Akamai *is* separately detected elsewhere, it's excluded from the set that triggers the pause (§1.4, §4.2). Workday commonly sits behind Akamai.
3. **Two confirmed "answers the wrong thing, confidently" bugs**, both in the layer above the safety primitives: Ashby's Yes/No/radio widgets can blindly undo an already-correct answer with zero readback (§4.3, the previously-documented toggle bug, still live), and a canned "why interested" string silently answers genuine competency-essay questions instead of routing to grounded LLM answering (§4.4).
4. **A near-complete absence of automated enforcement.** No CI configuration exists anywhere in this repo. Extensive `regression_gates.py`/`eval_suite.py --strict` machinery exists but nothing invokes it automatically, and the one gate script that *is* meant to run gives a false-positive PASS on a clean checkout (§1.6, §4.5).
5. **A recurring "claims verified without an actual readback" pattern** across the large ATS-specific modules, independently found in Workday, Greenhouse, and Ashby code — this matters because the project's own "honest metrics" invariant is the main thing standing between a real bug and a false SUCCESS report.

None of this breaks the hard rules (never submit, never solve CAPTCHA) in the currently-documented default invocation paths, and no real applicant PII was found to leak anywhere. But items 2 and 3 are live, reachable bugs today, not latent risks — see §7 for the ranked fix order.

---

## 1. Critical: Operational / Process Findings

### 1.1 Real incident: Chrome OOM crash from unsupervised concurrent headed browsers

**Severity: CRITICAL (already occurred once)**

Documented by the repo's own `skyvern_runtime/real_job_results/CHROME_CRASH_DIAGNOSIS.md` (dated 2026-07-31, ~03:10 EDT): a multi-agent Cursor swarm (confirmed via crash-report `coalition com.todesktop.230313mzl4w4u92`, Cursor's app bundle ID) ran **8 headed Chrome-for-Testing main processes** (~60 total incl. helpers) simultaneously, three of them holding `hold_seconds=3600`, on an **8GB MacBook Air**. Result: ~3.1GB/4GB swap used, Chrome RSS ~1.4GB, 7 macOS `SIGABRT` crash reports, and cascading `blocker=chromium_missing` failures across unrelated fill attempts.

The user caught this and paused the swarm (`sota_brainstorm/ORCHESTRATION.md`: *"PAUSE TEN UNSEEN variety fills"*). A fix landed: `refuse_headed_if_chrome_busy()` (`scripts/fastfill/fast_fill.py:5880`), which `pgrep`s for existing Chrome-for-Testing mains and refuses a new headed launch (`blocker=headed_cap`) unless `FASTFILL_FORCE_HEADED=1` is set. It's wired into `fast_fill.py`'s own launch path (line 6685) and `cycle_orchestrate.py` (line 396).

**Fix for Cursor:** none needed beyond §1.2 below — the mitigation exists and works where it's called.

### 1.2 The OOM-prevention gate has a known bypass

**Severity: HIGH** — file: `scripts/fastfill/exp_workday_selectors.py:4318-4448` (`run()`, the `--deep` CLI path)

This path launches Chromium directly via `p.chromium.launch(**launch_kwargs)` and **never calls `refuse_headed_if_chrome_busy()`**. It's a second, independent browser-launch pipeline that duplicates what `fast_fill.py` does for its production path, but without the cap check, without the CAPTCHA-resume loop, and without `--hold-open`/refill-passes bounds. This is exactly how a known-fixed incident class re-enters the codebase: a parallel code path nobody remembered to apply the fix to.

**Fix:** Either delete `exp_workday_selectors.py --deep` entirely (confirm nothing production-facing calls it; it appears to be a standalone debug CLI) or make it call `fast_fill.refuse_headed_if_chrome_busy()` before `p.chromium.launch()`, exactly like `fast_fill.py:6685` does.

### 1.3 Multi-agent coordination is done via shared markdown files, not code

**Severity: HIGH (structural)** — files: `sota_brainstorm/ORCHESTRATION.md`, `sota_brainstorm/BROWSER_CAP.md`, `sota_brainstorm/BROWSER_QUEUE.md`, `sota_brainstorm/BROWSER_KILL_LOG.md`

The "Master / King" role assignment, browser-slot queueing, and "who's allowed to launch next" coordination between concurrent autonomous Cursor agents is implemented as **prose instructions in markdown files that each agent is trusted to read and honor** ("Max 1 headed... not 1-2... Not 'just one more.'"). This is precisely the coordination model that produced the incident in §1.1 — there is no atomic claim/release primitive, no lock, no single source of truth enforced by code. `refuse_headed_if_chrome_busy()` (§1.1) is a good code-level backstop for the *browser* resource specifically, but the broader "who does what next" queue (`ORCHESTRATION.md`'s numbered table) has no equivalent enforcement — it's still purely honor-system for job/URL assignment, and nothing stops two agents from picking the same queued item.

**Fix:** This is a process/architecture decision for the user & Cursor together, not a pure code fix. Minimum viable improvement: a single file-locked JSON "claim" file (same `fcntl.flock` pattern already used correctly in `field_map.py`'s `alias_state.json.lock` and `scripts/jobs_lock.py`) that an agent must atomically claim a queue row from before starting work, instead of a markdown table humans/agents edit by hand.

### 1.4 CAPTCHA detection has real, dangerous blind spots — Akamai, PerimeterX, DataDome, Arkose are invisible to it

**Severity: CRITICAL** — file: `scripts/fastfill/iframe_ctx.py:450-478` (`visible_captcha_challenge`)

This is the **sole** signal `captcha_pause.py`'s human-wait loop uses (via `page_shows_interactive_captcha`, `captcha_pause.py:155-176`) to decide whether a challenge is still on screen and whether it's safe to resume filling. Its detection only recognizes hCaptcha, reCAPTCHA ("bframe"), and Cloudflare Turnstile/challenge, and only matches against `<iframe>` elements. The project's own `job-hunter-fill-safety` SKILL.md explicitly names **Akamai** alongside Cloudflare as a "never solve" blocker — but there is no Akamai (or PerimeterX/HUMAN, DataDome, Arkose Labs/FunCaptcha, GeeTest) detection anywhere in this function, and several of those (Akamai block pages, PerimeterX "press and hold" overlays) aren't even iframe-based, so an iframe-only check structurally cannot see them regardless of signature coverage. `detect_auth_blocker`'s text-phrase fallback (`iframe_ctx.py:430-440`) has the identical gap. **Net effect: a fill could proceed straight through a live Akamai/PerimeterX/DataDome/Arkose bot-challenge because the wait loop never recognizes it as still present** — the pause-and-wait *logic* in `captcha_pause.py` is sound (confirmed in this review's own direct read, §6), but its only sensor has a hole.

**Fix:** Extend `visible_captcha_challenge` with Akamai (`_abck`/`sensor_data` script tags, "Access Denied"/"Reference #" full-page block content — not iframe-scoped), PerimeterX (`px-captcha`, "Please verify you are a human" / press-and-hold overlay), DataDome, and Arkose/FunCaptcha (`arkoselabs.com`/`funcaptcha`) signatures, both as DOM patterns in the detector and as phrases in `detect_auth_blocker`'s `strong_captcha` tuple.

### 1.5 The headed-browser cap itself has a check-then-launch race, and browser cleanup isn't guaranteed on error paths

**Severity: CRITICAL** — files: `scripts/fastfill/fast_fill.py:5844-5901,6684-6710,6821-7781,6937`

Two independent findings from the main-orchestrator audit both point back at the exact incident in §1.1/§1.2, and both undermine the fix that was supposed to close it:

- **Unlocked check-then-launch race**: `refuse_headed_if_chrome_busy()`'s cap of 1 headed Chrome main is enforced via an unlocked `pgrep` snapshot, checked once before `chromium.launch()`. Two concurrent `fast_fill.py` invocations that both `pgrep` before either has actually launched Chrome will both see `count < 1` and both proceed to launch headed — reproducing the multi-headed-Chrome pile-up that caused the original OOM crash. The codebase already has the right fix pattern in active use elsewhere (`field_map.py`'s `alias_state.json.lock` via `fcntl.flock`) — it just wasn't applied here.
  **Fix:** add an flock'd `chrome_headed.lock`, held from the pre-launch check through the actual `chromium.launch()` call.
- **Browser cleanup is only guaranteed in the final ~80 lines of a ~1,100-line async block.** `browser.close()` only runs inside a `try/finally` that starts at line 7734 — everything from page creation (~6719) through line 7733 (the Workday two-phase flow, extract/fill, the in-session refill loop, Flash leftovers — roughly 900 lines) has **no enclosing try/finally**. This isn't theoretical: line 6937 contains an explicit `raise` (inside an `except Exception` handler for `entry_prepass` failures) that propagates straight past the close block. Every exception not caught by one of the file's 158 local `except Exception` blocks skips explicit cleanup and relies only on `async_playwright()`'s weaker context-exit teardown — directly feeding the same OOM failure class.
  **Fix:** wrap the entire `async with async_playwright()` body in one outer `try/finally: await browser.close()` (or factor launch+close into a dedicated context manager) so cleanup is unconditional regardless of where an exception originates.

Combined with §1.2 (the `--deep` path bypassing the cap entirely), this means the OOM-prevention story has three independent holes: a path that skips the check, a race in the check itself, and cleanup that isn't guaranteed to run. **This cluster should be the first fix Cursor makes.**

### 1.6 No CI / automated regression enforcement exists anywhere

**Severity: HIGH** — confirmed via direct search: no `.github/workflows/`, no other CI config file anywhere in the repo.

Extensive gating infrastructure exists (`scripts/fastfill/regression_gates.py`, `eval_suite.py --strict` / `--strict-safety`) but **nothing invokes it automatically**. Every doc reference (`SKILL.md`, `coverage_matrix.md`) shows these run manually from the CLI. `coverage_matrix.md` itself states: *"Default eval_suite exit is diagnostic (0); --strict-safety / --strict for CI"* — but there is no CI to pass `--strict` to. See §5 for the eval-infra agent's detailed findings on this.

**Fix:** Add a minimal `.github/workflows/fastfill-gates.yml` (or equivalent for whatever CI the user actually uses) that runs `regression_gates.py` and `eval_suite.py --strict-safety` on every push/PR to this branch, failing the build on any hard-safety violation. Given there's no evidence of a remote CI runner in use, a lower-effort interim step is a pre-commit/pre-push git hook running the same gates locally.

---

## 2. High: Cross-Engine Architecture

### 2.1 Two full, independently-maintained engines coexist with real duplication

The repo runs **two** parallel form-fill engines:
- `scripts/fastfill/` — new, Playwright-driven, deterministic-first (~33K lines)
- `skyvern_runtime/scripts/hybrid_fill.py` — older, Skyvern (LLM)-driven

`dashboard/server.py`'s `run_hybrid_fill_dummy()` (line ~448) prefers the Playwright path and falls back to the Skyvern path only if `fast_fill.py` is missing. Both paths correctly funnel through `prepare_dummy_run()` and assert `never_submit`/`TEST_MODE` — the safety contract is honored on both sides (verified directly, see §6).

However, the **eval/scorecard infrastructure is duplicated, not shared**: `scripts/fastfill/scorecard_fast.py` (717 lines) vs `skyvern_runtime/scripts/scorecard.py` (104 lines); `scripts/fastfill/eval_suite.py` vs `skyvern_runtime/scripts/batch_hybrid.py`. See §5 for the detailed comparison from the eval-infra audit.

**Fix:** Decide (with the user) whether `hybrid_fill.py` is still a going concern or a fallback-of-last-resort that should be simplified/retired now that the Playwright engine covers the same ground faster. If it stays, at minimum share one scorecard/verdict-schema module between the two rather than maintaining two divergent definitions of "what counts as SUCCESS."

### 2.2 `fast_fill.py` is a single 8,328-line file

**Severity: MEDIUM (maintainability)** — see §4 (agent findings) for the detailed breakdown once merged in.

---

## 3. PII / Identity Boundary — Direct Review Findings

Reviewed directly (not delegated) given this is the single highest-stakes boundary in the system: `field_map.py` (full diff), `run_identity.py`, `dashboard/server.py`'s dummy-fill wiring, `real_job_test.py`'s diff, `hybrid_fill.py`'s diff, `dry_run.py`, `build_corpus.py`.

### 3.1 [FIXED, worth a permanent regression test] `real_job_test.py` used to load real `profile.json` unconditionally at import time

Prior code (per the diff): `PROFILE = json.load(open(".../profile.json"))` ran **unconditionally at module import**, regardless of `TEST_MODE`. Since `hybrid_fill.py` and the dashboard's dummy-fill path both `import real_job_test`, every dummy/test run was loading the real applicant's PII into memory even though it (apparently) never got used downstream, because the downstream variable assignments were `TEST_MODE`-gated.

This is now fixed: `PROFILE = None` by default, and the real load only happens in the `else` (non-`TEST_MODE`) branch (`real_job_test.py:582`), with an explicit comment: *"CRITICAL: when TEST_MODE=1 we must NOT open profile.json at all... Real PII stays on disk; autofill never reads it."*

**Fix (recommended, not urgent since already fixed):** Add a unit test asserting that `import real_job_test` with `TEST_MODE=1` set never touches `profile.json` on disk (e.g., assert the file's atime doesn't change, or patch `open` and assert it's never called with that path). This exact bug class (module-level unconditional PII load) is worth a standing regression test given it already happened once silently.

### 3.2 `build_corpus.py`'s real-profile.json reads — confirmed safe, correctly scoped

`scripts/fastfill/build_corpus.py:139,319` loads real `profile.json` directly, and reads real applicant values from Skyvern's Postgres DB (`actions.response`) for **non-`TEST_MODE`** historical runs. This looked alarming on first grep, but the file's own docstring (lines 46-54) documents that raw values are converted to type-labels (`"test dummy" -> "NAME_FULL"`) and the raw string is dropped before anything is written to `corpus.json` — verified directly by reading `corpus.json`'s actual on-disk schema (task_id, label, selector metadata, no value field at all). It is also not imported/called by any other script (grep confirmed) — it's a standalone, manually-invoked dev tool requiring direct Postgres access (`SKYVERN_DB_PASSWORD` env var), not part of any live-fill path.

**No fix needed.** Note for the record only: this should never be wrapped in an automated/scheduled job without a human explicitly re-reviewing that the PII-stripping still holds.

### 3.3 `TERMS_CONSENT` is answered "Yes" unconditionally for every tenant

`scripts/fastfill/field_map.py` (`build_value_map`, ~line 1000): `TERMS_CONSENT: "Yes"` — every checkbox classified as a terms/privacy/data-processing consent gets automatically ticked, for any company's arbitrary legal text, with no per-tenant review. Low risk given this is dummy/test-mode-only (never a real application), but worth being explicit about: this is a blanket "I agree" to unknown legal text, applied identically everywhere.

**Fix:** No code change required if this stays test-only forever. If this pattern is ever reused for a real-PII/production fill path, it should not survive unchanged — flag explicitly in the module docstring that this default is dummy-context-only and must be revisited before any real-identity fill path reuses `build_value_map`.

### 3.4 `INTEREST` field type vs. essay-routing — CONFIRMED REAL, see §4.4 for full detail

The tension flagged for investigation is confirmed real and worse than initially suspected: it can misanswer genuine competency-essay questions, not just "why interested" ones. Full trace and fix in §4.4, finding 1.

---

## 4. Agent-Reported Findings: Large Mechanical Subsystems

### 4.1 Workday flow (`scripts/fastfill/exp_workday_selectors.py`, 4,660 lines) — COMPLETE

Full audit complete. Top findings:

1. **CRITICAL — `exp_workday_selectors.py:4043-4058` (`_phase_e_self_id`)**: sets `verdict="SUCCESS"` / `ready_for_review=True` whenever no validation banner is present, **without checking whether the ADVANCE click actually happened** (`phase["advanced"]` is computed but never read). If the self-identification page's Next button doesn't exist, is disabled, or gets refused by the button gate, this still reports SUCCESS. This directly contradicts the module's own stated invariant (`_finalize_workday_verdict`'s docstring: *"Contact-only or mid-wizard SUCCESS is a metrics lie"*) and `ats_notes/workday.md`'s own rule (*"SUCCESS requires ready_for_review — never contact-only SUCCESS"*).
   **Fix:** gate the SUCCESS/ready_for_review assignment on `phase["advanced"]` being True, or replace the ad hoc click+validate with the same `_gate_then_advance` helper Phases B/C/D already use.
2. **HIGH — the `--deep` CLI path bypasses the OOM-prevention cap** (§1.2 above — cross-referenced, same finding).
3. **HIGH — several fill helpers assert `verified: True` with zero readback**: work-experience job title/company/location (`:3064-3074`), self-id legal name (`:3961-3994`), today-date-picker click (`:3987-3994`), app-questions Yes/No radios (`:3837-3844`). Contrast with `_fill_radio_yes_no` (line 852), which correctly calls `is_checked()` before claiming verified. **Fix:** add a readback check to each of these, matching the pattern `_fill_automation_id` already uses correctly elsewhere in the same file.
4. **HIGH — the date-spin "select all" key chord is effectively dead on headless Linux**: `_type_digits_into` (`:2564-2569`) tries `Meta+a` then `Control+a` in a loop that `break`s after the first `press()` call regardless of whether it worked (Playwright's `keyboard.press` essentially never raises), so `Control+a` — the chord that actually works in headless Linux Chromium, the platform this project's own CI/batch runs target per `SKILL.md` — never executes. Only a single `Backspace` follows, which clears at most one digit; a wrong multi-digit date-spin value can end up concatenated rather than replaced. An arrow-key fallback exists and can partially recover, but the primary technique is broken on the project's own target platform for one of the hardest, most failure-prone field types in the whole flow. **Fix:** drop the `break`, press both chords unconditionally, or switch to `loc.fill("")` / triple-click-select instead of OS-specific key chords.
5. **MEDIUM — "How Did You Hear" combobox fill logic is implemented three separate times** with diverging candidate lists (`_fill_contact_extras:1696-1843`, `_dummy_answer_for_wd_label:3320-3322`, and an inline reimplementation in `_phase_b_contact`'s second-chance sweep at `:1964-1976`). **Fix:** consolidate into one `_fill_how_heard()` helper.
6. **MEDIUM — doc/code drift**: `ats_notes/workday.md` instructs checking `credentials.json` for an existing login before creating an account — the code never does this (grep clean) and correctly always mints a fresh dummy identity instead (the safer, SKILL.md-compliant behavior). The doc describes a feature that was never built and shouldn't be; **fix the doc, don't add the feature** (adding it would violate the hard "never read credentials.json for autofill" rule).
7. Additional lower-severity notes: `_US_STATE_NAMES` is US-only (international Workday tenants may silently miss country/region matching); `--deep`'s docstring claims it only runs Phases A-B but it actually drives the full A-E flow; a speculative `checked=True` default on an exception path in `_fill_radio_yes_no`'s BBH branch (`:838-865`) should fail-safe to `False` instead.

**No CAPTCHA-solving code, no `profile.json`/`credentials.json` reads, and no `press("Enter")` anywhere in this file** — confirmed clean via direct grep by the auditing agent.

### 4.2 Main orchestrator (`scripts/fastfill/fast_fill.py`, 8,328 lines) — COMPLETE

**The two most severe findings (unlocked headed-Chrome-cap race; browser cleanup not exception-safe) are cross-referenced at §1.6 above — not repeated here.**

1. **MEDIUM — `iframe_ctx`/`captcha_pause`'s Akamai gap has a second, independent half in this file.** `_detect_blocker` (`fast_fill.py:1963-1981`) correctly classifies Akamai bot-walls as `blocker="akamai"`, a distinct category from `"captcha"`/`"cloudflare"` — and the Workday reached-contact check (`:6836-6840`) explicitly treats all three as equivalent. But **every** gate that actually triggers the human pause-and-resume flow checks `captcha_pause.CAPTCHA_BLOCKERS` (`captcha_pause.py:35`, `frozenset({"captcha", "cloudflare"})` — **akamai excluded**), at 8 separate call sites (`fast_fill.py:6188, 6313, 6770, 6867, 6984, 7066, 7703, 7706`). So even where Akamai *is* correctly detected as a blocker (independent of §1.4's detection-signature gap), it's never routed to the human-solve-then-continue flow — it just dead-ends. Since Workday commonly sits behind Akamai, this silently under-recovers a common, legitimately-recoverable case. SKILL.md's own phrasing groups "CAPTCHA / Akamai / Cloudflare" together for the pause behavior, suggesting parity was the intent.
   **Fix:** add `"akamai"` to `CAPTCHA_BLOCKERS` in `captcha_pause.py` (one-line fix, once §1.4's detection-signature gap is also closed — otherwise Akamai still won't be reliably *detected* even after this routing fix).
2. **MEDIUM — the resume-upload fast path partially reimplements the already-reviewed `resume_upload.py` primitive** (`fast_fill.py:3718-3781`): its own `set_input_files` + filename-readback logic, falling back to the shared `upload_resume_to_page` only when its own selector guess fails. Two independently-maintained "did the resume actually attach" implementations for a safety-relevant check. **Fix:** delete the inline verification block and always call `upload_resume_to_page`.
3. **MEDIUM/LOW — `entry_prepass`'s radio/checkbox fallback can mis-answer on pages with repeated same-labeled question groups** (`fast_fill.py:1102-1113`): when no scoped match is found, it falls back to an *unscoped* `page.get_by_label(regex)`, which on a page with multiple "Yes"/"No" questions sharing generic labels could click the wrong group's radio. Not a submit/CAPTCHA risk, but a real mis-answer risk. **Fix:** drop the unscoped fallback or require it to also match on DOM proximity/name-attribute.
4. **LOW — `--headed` CLI flag is dead**: `main()` only ever reads `args.headless`; `args.headed` (`default=True`) is parsed but never consulted again, so passing `--headed` explicitly does nothing (harmless today only because the default already matches).
5. **Architecture — 8,328 lines in one file, with four concrete, low-risk extraction targets identified**: `detect_platform` + its pattern table (~500 lines, pure data + thin dispatcher → `platform_detect.py`); `run_inpage_flash_leftovers` (~700 lines, already coupled to `flash_leftovers.py` → fold in there); ~1,000 lines of Greenhouse-specific logic embedded inside the generic `fill_from_extract` (`_gh_city_aliases`, `sweep_gh_unfilled_selects`, `reassert_greenhouse_contact_after_resume`, etc. → belongs in `gh_select.py`, already imported for this purpose); `_merge_workday_into_report` (→ belongs next to `workday_two_phase_on_page` in `exp_workday_selectors.py`). Together these four moves would remove ~30% of the file's line count with no behavior change.

**Confirmed clean, no action needed:** every direct `.click()` call site was traced and confirmed to target only data-entry widgets, never FINAL/ADVANCE controls (the one FINAL-adjacent click goes through `gated_click_control`, which is correctly preceded by `gate_locator_click`). Only one `keyboard.press` exists in the whole file and it's `"Escape"`, not `"Enter"` — `fill_custom_widget`'s docstring explicitly states "Never presses Enter — Enter can submit the application form," and the code honors it. The module's "Flash is opt-in only" claim also checked out: both Flash call sites are gated on the caller-supplied flag, and `--matrix` mode hardcodes it off.

### 4.3 ATS widget handlers (`gh_select.py`, `ashby_widgets.py`, `lever_widgets.py`, `iframe_ctx.py`, `verified_select.py`) — COMPLETE

**iframe_ctx.py's CAPTCHA-detection gap is cross-referenced at §1.4 above (CRITICAL) — not repeated here.**

1. **CRITICAL — `ashby_widgets.py:487-548,1116-1176` (`_click_yesno_in_entry`, `_click_option_in_entry`)**: every Yes/No, radio, and checkbox question on Ashby (WORK_AUTH, SPONSORSHIP, TALENT_HUB, WORKED_HERE_BEFORE, TERMS_CONSENT, GENDER, RACE, VETERAN, DISABILITY) clicks blindly with **no pre-click "already correct" check and no post-click DOM readback** — the code returns `verified: True` the instant `.click()` doesn't throw. Root cause: `list_ashby_field_entries`'s JS scan (`:411-484`) never captures `checked` state for radios/checkboxes at all (contrast `lever_widgets.py`'s `_LEVER_SCAN_JS`, which does: `checked: !!r.checked`, line 110). This is precisely the previously-documented Ashby toggle-bug (blindly re-clicking and undoing an already-correct answer) — it is **not fixed**, only worked around for this file's text/URL fields, which correctly do use `_value_already_correct` + `input_value()` readback (`:139-153`, `:725-744`, `:1040-1058`).
   **Fix:** extend the JS scan to report each radio/checkbox's current `checked` state and the yesno buttons' selected-state CSS class; skip the click when state already matches; verify state actually changed post-click before setting `verified: True`.
2. **HIGH — `ashby_widgets.py:1202-1219`**: in the same "lone bottom consent" block, the checkbox branch (`:1180-1201`) correctly uses Playwright's idempotent `.check()`, but the very next block clicks an "I consent" radio via raw `.click(force=True)` with zero prior state check — the identical risk as finding 1, written right next to the pattern that would have prevented it. **Fix:** use `.check()` for the radio too.
3. **HIGH — `ashby_widgets.py:509-548` (`_click_option_in_entry`) has no scoring/polarity protection at all**, unlike `gh_select.py`'s `_score_option` (`:253-403`, built specifically to stop "No" from substring-matching a trap option like "No, I will require visa sponsorship"). Ashby's path does raw substring `get_by_role(...).first` matching with no scoring — if a tenant's exact wording isn't in the curated alias list, this can silently pick an opposite-meaning option. **Fix:** route Ashby's candidate selection through a shared version of `gh_select._score_option` (see finding 5 below for consolidation).
4. **HIGH — `ashby_widgets.py:230-303` (`fill_ashby_location_then_zip`)**: the Location typeahead is filled via `combo.fill(city_val)` then a single fixed `wait_for_timeout(400)` — no polling loop like `verified_select.wait_for_option_texts` (which `gh_select.py` correctly uses for exactly this "zero options until you type" race). On zero matching options, the function does nothing: no retry, no clearing of the stale typed text, and **no `filled.append(...)` at all**, so a failed Location typeahead is silently invisible in the report (not even flagged as a leftover). This is the same historical "autocomplete silently reverts if never confirmed" bug, fixed for Greenhouse's SCHOOL select but never carried over to Ashby's Location field. **Fix:** replace the fixed sleep with `wait_for_option_texts`-style polling; on zero-match, explicitly record `ok: False, reason: "location_no_matching_option", flash_candidate: True` and clear the stale filter text.
5. **HIGH — `lever_widgets.py:262-270` (`pick_radio_option`'s SPONSORSHIP trap guard) is self-canceling.** The first regex correctly zeroes the score for a trap option like `"No, I will require visa sponsorship"` (matches `will\s+require`), but the second regex's trailing `\bno\b` alternative matches the same option's leading "No," and re-boosts the score right back to 96 in the very next line — undoing the protection. Currently masked because a correctly-worded alternative usually scores higher and wins the comparison, but if a tenant's *only* "No"-ish option is phrased as the trap itself, the wrong (opposite-meaning) answer gets selected and reported `verified: True`. The file's own self-test doesn't catch this because its trap fixture (`"Yes, I will require sponsorship"`) doesn't start with "No". **Fix:** remove the bare `\bno\b` alternative from the second regex; add a self-test case using `"No, I will require visa sponsorship"` as the trap.
6. **MEDIUM — three-way duplication of scoring logic**: `gh_select._score_option` (`:253-403`), `lever_widgets.pick_radio_option`/`pick_eeo_select_option` (`:234-314`, shown above to have drifted/broken), and Ashby's complete absence of scoring (finding 3) are three independent, non-shared implementations of "pick the right option without matching the opposite polarity." **Fix:** move `_score_option` into `verified_select.py` (already lazily imported there) and have all three widget modules import the single shared version — this single change would have prevented both the Lever regression (finding 5) and the Ashby gap (finding 3).
7. **MEDIUM — `gh_select.py:764-781`**: the ADDRESS_COUNTRY fallback path falls back to `shown = picked` (assumes the click worked) with **zero DOM confirmation** when both `read_gh_select_display` and `container.inner_text()` come back empty — a narrow but real violation of the project's own "verified read-back only" rule. **Fix:** poll/retry for a non-empty text node before giving up; return `ok: False` if still empty rather than assuming success.
8. **LOW — `gh_select.py:497-538`**: label matching truncates to a 24-60 char substring; two similarly-worded long questions (common in EEO/legal blocks) can collide and `.first` silently resolves to the wrong one with no ambiguity signal.


**Positive finding first:** the "self-test that prints without asserting" antipattern this review specifically worried about was checked for and **not found** — all 8 test files (`test_alias_allocate.py` through `test_workday_app_questions.py`) contain dense, real `assert`/`raise AssertionError` blocks (4 to 81 asserts per file) and correctly propagate failure via uncaught `AssertionError` or explicit `raise SystemExit(1 if failed else 0)`. Test *quality* is good. The problems are all one layer up, in whether anything ever runs them or trusts their absence correctly.

1. **CRITICAL (confirms §1.5) — strict-mode enforcement is never actually invoked anywhere.** Repo-wide grep for `--strict`/`--strict-safety` found exactly one live call site — inside `regression_gates.py`'s own `--run-eval` branch (`regression_gates.py:356-359`), which itself defaults to `False` and needs network access to even reach that code. The only other occurrences are in `TOOLS.md:134-135` and `.cursor/skills/job-hunter-fastfill/SKILL.md:83-84`, both **commented out** as illustrative examples, not live commands. There is no pytest config, no CI, no git hook, no cron anywhere in the repo (`.git/hooks/` has only `*.sample` placeholder files).
2. **HIGH — the one gate script that exists gives a false-positive PASS on a clean checkout.** `gate_scorecard_eval` (`regression_gates.py:62-69`) and `gate_eval_summary_safety` (`:72-96`) silently return `(0, "skip — ...")` when their target artifact directories/files don't exist yet — which is exactly the state of a fresh checkout or first-ever CI run. On a clean checkout, `python regression_gates.py` with zero flags only actually exercises 3 unit self-tests (`gate_unit_honesty`, `:46-59`) — the scorecard and eval-summary lanes silently no-op — **yet the script still prints `"=== regression_gates: PASS ==="`.** This exact skip-on-missing-artifact branch has zero test coverage of its own (`--self-test`, `:227-284`, never exercises it). **Fix:** make missing-artifact a hard fail or a clearly distinct WARN exit code, not silent pass; add a test for the branch.
3. **HIGH — record_replay.py has no file lock on its shared cache, unlike its sibling `alias_state.json`.** Verified directly: the PII-safety claim itself holds up completely — `_scrub_row` (`record_replay.py:216-233`) structurally never copies value/label/email/phone/address fields into the cache (enforced by construction, not just filtering), and every write path routes through it. But `_load`/`_save` (`:331-365`) do a plain read-modify-write on `replay_cache.json` with **no `fcntl.flock`**, even though the exact same directory already has the right pattern in `alias_state.json.lock`, and `dashboard/server.py` can run concurrent fast-fill subprocesses that each call `record_successful_fills` at exit. This is a lost-update/data-loss risk, not a PII risk. **Fix:** reuse the existing `alias_state.json.lock` pattern for `replay_cache.json`.
4. **MEDIUM — old-vs-new engine safety scorecards are structurally incompatible, not just differently named.** `skyvern_runtime/scripts/scorecard.py`'s `categorize()` (`:39-57`) and the new engine's `_SAFETY_FAIL_PREFIXES`/`assert_never_submit`/`assert_honest_advance` (`eval_suite.py:188-203`, `scorecard_fast.py:29-124`) key off completely different report-field vocabularies with zero shared constants or cross-import. Since both engines run in production simultaneously (`dashboard/server.py`'s engine-preference fallback), there is currently no single query that can answer "did any run across both engines ever violate never-submit?" — it requires manually reconciling two data models. **Fix:** extract a shared `safety_taxonomy.py`, or explicitly document the split as intentional if unification isn't worth it.
5. **MEDIUM — `record_replay.py` (708 lines, the file responsible for the PII-safety guarantee) has the thinnest test coverage of anything examined** — one narrow indirect case (`test_honest_metrics.py:254-265`, UUID-selector scrub only). `page_fingerprint` normalization, `sanitize_cache` merge/rekey, and the full `apply_replay_map` fill/invalidate loop are untested.
6. **LOW — `eval_urls.json` hardcodes specific live job postings** (e.g. a Dragos Greenhouse req, a Cisco Workday req) that will close over time and silently fall into the reachability-blocker exclusion path with no staleness alert, quietly shrinking effective SLO coverage.
7. **LOW — pytest-shaped tests are never run via pytest** — no `pytest.ini`/`conftest.py` exists; docs only show manual per-file invocation. **Fix:** add a `pytest.ini` and a documented `pytest scripts/fastfill/ -k test_` command (or a thin runner aggregating all 8 `main()` exit codes).

### 4.4 LLM-leftover/judging/attribution layer (`flash_leftovers.py`, `vision_judge.py`, `fill_attribution.py`, `field_attempt_log.py`, `resume_parser.py`, `cycle_orchestrate.py`) — COMPLETE

1. **CRITICAL — the INTEREST/essay routing conflict (§3.4) is confirmed real, traced end-to-end.** For a label like "Why are you interested in this role?" *or* a genuine competency essay like "Describe your experience with machine learning": `fast_fill.py:4951` classifies it via `field_map.py`'s `INTEREST` pattern (`field_map.py:376-384`) — a pattern deliberately broad enough to also match `examples?\s+of\s+(educational|professional)\s+experience` and `describe\s+(your\s+)?(experience|background)\s+with`. `fill_from_extract` (`fast_fill.py:4990-5107`) explicitly allow-lists INTEREST to bypass the dedup skip, writes the hardcoded canned string (`field_map.py:970-973`, "I'm interested in this role based on the posted description...") to the field, and appends it to `filled` — **never to `leftovers`**. Because it never becomes a leftover, `page_progress.is_essay_leftover()` never sees it, so it never routes to Flash for JD-grounded answering — even in the Flash fallback path, `run_inpage_flash_leftovers` (`fast_fill.py:749-760`) explicitly recognizes INTEREST as essay-like and then does nothing with that recognition (`if ftype == INTEREST and val: pass`). Net effect: **a genuine "describe your ML experience" essay question gets answered with an off-topic "why I'm interested in this role" sentence**, filled deterministically, reported as a normal successful prefill (not even flagged by Agent3's attribution — `fill_attribution.py`'s `_PREFILL_VIA_RE` matches the `extract+classify` via-string as ordinary prefill). This directly contradicts `coverage_matrix.md`'s "Free-text essays / cover letters (never invent)" and the `CYCLE_AGENTS.md` cycle contract.
   **Fix:** narrow the `INTEREST` regex to true "why this company/role" phrasing only; remove the competency-question patterns (`examples of ... experience`, `describe your experience with`) from it entirely — those should classify as `ESSAY`/leftover; route all INTEREST hits through `is_essay_leftover`/Flash grounding instead of the `filled`-bypassing deterministic fill path.
2. **HIGH — the resume-path PII guard into the DeepSeek prompt is a denylist, weaker than the codebase's own allow-list pattern.** `flash_leftovers.py:396-400` (`build_resume_excerpt`) guards against interpolating a real (non-dummy) resume into an LLM prompt via `"tailor" in low or ("/resumes/" in low and "dummy" not in low)` — a denylist. `field_map.py:894-916`'s `assert_dummy_resume_path()` (already reviewed, §6) is a strict allow-list used correctly everywhere else in the pipeline. Any resume path that doesn't happen to contain "tailor" or "/resumes/" would sail through this weaker check and have its full text interpolated into a DeepSeek prompt. Not currently exploited — `resume_pdf` always originates from `prepare_dummy_run` in the live pipeline (confirmed via provenance trace) — but this is the literal chokepoint the "never real PII in an LLM prompt" rule exists to protect, and it shouldn't depend on every caller supplying a correctly-named path.
   **Fix:** call `assert_dummy_resume_path()` directly inside `build_resume_excerpt`, not a bespoke substring check.
3. **MEDIUM — cross-retry escalation in `cycle_orchestrate.py` doesn't actually accumulate.** Each retry (`r0`/`r1`/`r2`) gets a brand-new `attempt_dir`, so `FieldAttemptLog` (`field_attempt_log.py:52-101`) starts a fresh, empty log every retry. The "fail twice → escalate to Fixer" threshold that drives `UNFILLABLE_AFTER_2.md`/`FIXER_TRIGGER.md` — the exact mechanism `cycle_orchestrate.py`'s own printed hint text describes — can only trip from failures *within* one attempt's internal refill passes, never by accumulating *across* the outer retry loop. A field that fails once per retry (never twice within the same attempt) can exhaust all retries and land in `cycle_failures.jsonl` as a generic FAIL without ever producing the actionable Fixer artifacts the retry loop promises.
   **Fix:** persist `field_attempts.jsonl` at the per-URL level across retries, not per-attempt.
4. **MEDIUM — `flash_leftovers.py`'s local deterministic-type set has drifted from the authoritative one.** `answer_leftover_field`'s local `det_types` (`flash_leftovers.py:728-768`) is missing `RESUME_UPLOAD`, `TERMS_CONSENT`, `SERVICE_MEMBER`, all present in `fill_attribution.DETERMINISTIC_TYPES`. Currently masked in the live call path (the caller already computes the correct deterministic row upstream), but `synthesize_grounded_answer()` has no case for TERMS_CONSENT/SERVICE_MEMBER either, so if one of these ever does reach this function as a leftover, it falls through to generic essay boilerplate instead of "Yes"/"No".
   **Fix:** import `fill_attribution.DETERMINISTIC_TYPES` directly instead of maintaining a second hand-written copy.
5. **MEDIUM — three independently-maintained "is this an essay label" regexes have already drifted**: `page_progress.py:29-36`, `fill_attribution.py:138-144`, and an inline JS variant in `vision_judge.py:165` all implement the same concept differently, so Agent1's leftover routing, Agent2's screenshot judge, and Agent3's attribution can reach different conclusions about the same field.
   **Fix:** consolidate into one shared helper; have all three import `page_progress.is_essay_leftover`.
6. **MEDIUM — `CYCLE_AGENTS.md` documentation directly contradicts `fill_attribution.py`'s own (correct) EEO handling.** `CYCLE_AGENTS.md:120-122` lists `EEO*` among "deterministic types" that count as a regression if filled via Flash — but `fill_attribution.py` deliberately excludes EEO from `DETERMINISTIC_TYPES` for exactly this reason, since EEO-via-DeepSeek+dummy-grounding is the *required*, safety-mandated behavior (SKILL.md rule 3). A human or coding agent reading `CYCLE_AGENTS.md` at face value could be misled into "fixing" the one behavior that must not change.
   **Fix:** remove `EEO*` from that line in `CYCLE_AGENTS.md`, or add an explicit note that EEO-via-Flash is expected, not a regression.
7. **LOW/MEDIUM — no truncation detection on long LLM answers.** `flash_leftovers.py`'s `call_flash_text_llm` (`:596-666`) caps essay answers at 600 tokens (80 for EEO) with no `finish_reason` check — a long answer cut off mid-sentence is indistinguishable from a complete one to the caller.
8. **LOW — dead code**: `cycle_orchestrate.py:567-729`'s `while True: ... else: continue` has an unreachable `else` clause (misleading, not a bug); `flash_leftovers.py:317-324`'s `filter_llm_leftovers()` has zero callers anywhere in the repo; `fill_attribution.py:48,80` lists `"LOCATION"` twice in the same frozenset literal (harmless, dedups automatically).

**Confirmed clean, no action needed:** `VISION_JUDGE_SCHEMA` (`vision_judge.py:47-107`) matches `CYCLE_AGENTS.md`'s documented Agent2 JSON shape field-for-field with no drift.

### 4.5 Eval/test/replay infrastructure — COMPLETE

---

## 5. Minor / Hygiene Notes (direct review)

- **`scripts/fastfill/exp_entry_prepass.py` and `exp_fill_from_mapping.py`** are live, imported production code (confirmed via grep: both are imported by `fast_fill.py` and `scorecard_fast.py`), not experiments — the `exp_` naming prefix is misleading about their maturity/status. Consider renaming once stable, so a future contributor doesn't assume they're safe to delete.
- **Dashboard dummy-fill timeout** (`dashboard/server.py`: `DUMMY_FILL_PLAYWRIGHT_TIMEOUT_S = 180`) may be tight relative to `sota_brainstorm/BROWSER_CAP.md`'s own documented hold windows (up to 120s) plus actual multi-phase Workday fill time, which historically ran 300-600s+ in the older engine. Worth confirming empirically whether dashboard-triggered Workday dummy-fills are being killed mid-flight by this timeout before they can finish.
- **`field_map.py`'s education-year heuristic** (`_start_year = str(int(_end_year) - 2)`, ~line 942) assumes every degree is a fixed 2-year program. Harmless for dummy/test data quality, just a cosmetic accuracy note, not a safety issue.

---

## 6. What Was Verified Clean (no action needed)

For completeness — these were points of real historical risk in this project's history, specifically re-checked this session and confirmed still correctly handled in current code:
- `button_gate.gate_click`/`gate_resolved_click` correctly fail-closed on FINAL/submit-like/ambiguous `type=submit` controls (self-test cases in `button_gate.py:__main__` verified by direct read).
- `captcha_pause.py`'s wait-loop *logic* never solves or dismisses a CAPTCHA under any code path; headless always blocks rather than guessing; headed correctly waits indefinitely for a human signal (Enter, sentinel file, or challenge disappearing) with no auto-continue. **Caveat, not a contradiction:** this logic is only as good as its input signal, and that signal (`iframe_ctx.visible_captcha_challenge`) has real detection gaps for non-hCaptcha/reCAPTCHA/Cloudflare providers — see §1.4, the one place this review found a genuine gap adjacent to an otherwise-solid primitive.
- `run_identity.prepare_dummy_run()` hard-asserts dummy profile identity (name/phone/email) before every run and refuses to proceed on mismatch; email allocation is provably non-sequential/non-reused (`alias_state.json` + `fcntl.flock`-guarded read-modify-write, verified in the diff).
- `dashboard/server.py`'s dummy-fill handler independently re-asserts `FASTFILL_REAL_PROFILE=0`/`TEST_MODE=1` in the child env, refuses to construct a command line referencing `profile.json`/`credentials.json`/`tailor_resume`, and the result-reporting path explicitly flags (rather than silently accepting) any child report missing `never_submit=True`.
- `load_next_alias_index`/`save_next_alias_index` (the old, reuse-prone sequential-alias scheme) were converted to hard-raising stubs rather than silently removed, so no code path can accidentally fall back to them.

---

## 7. Prioritized Fix List for Cursor

All findings from this report, ranked. Use this as the actual work queue — everything above is the supporting detail.

### Do first (safety-adjacent, or directly reproduces the documented OOM incident)

1. **Fix the headed-Chrome-cap race + non-exception-safe browser cleanup** — `fast_fill.py:5844-5901,6684-6710` (add `fcntl.flock`-based locking, same pattern as `alias_state.json.lock`) and `fast_fill.py:6821-7781` (wrap the entire fill body in one outer `try/finally: await browser.close()`). §1.5 / §4.2.
2. **Close the `exp_workday_selectors.py --deep` OOM-cap bypass** — call `refuse_headed_if_chrome_busy()` before its own `chromium.launch()`, or delete the path if unused. §1.2 / §4.1.
3. **Extend CAPTCHA detection to Akamai/PerimeterX/DataDome/Arkose** in `iframe_ctx.py:visible_captcha_challenge` (currently hCaptcha/reCAPTCHA/Cloudflare-only, iframe-only) — the human-pause safety net cannot pause for a challenge type it can't see. §1.4.
4. **Wire Akamai into `CAPTCHA_BLOCKERS`** in `captcha_pause.py:35` so a correctly-*detected* Akamai wall actually gets the human-pause treatment instead of dead-ending — do this together with #3, since detection and routing are both currently broken for Akamai. §4.2 finding 1.
5. **Fix the Ashby choice-widget blind-click bug** — `ashby_widgets.py:487-548,1116-1219`: add pre-click state check + post-click readback (extend `list_ashby_field_entries`'s JS scan to capture `checked`/selected-class state first). This is the previously-documented toggle-undo bug, confirmed still live. §4.3 finding 1.
6. **Fix the INTEREST/essay routing bug** — `field_map.py:376-384,970-973` + `fast_fill.py:4990-5107`: narrow the INTEREST regex to true "why this company" phrasing, move competency-essay patterns to the ESSAY/leftover path, route through Flash grounding. §4.4 finding 1.
7. **Fix Workday Phase E's false-SUCCESS path** — `exp_workday_selectors.py:4043-4058`: gate `verdict=SUCCESS`/`ready_for_review=True` on `phase["advanced"]` actually being True. §4.1 finding 1.

### Do next (real correctness/safety gaps, lower likelihood of triggering)

8. Add polarity/scoring protection to Ashby's radio/checkbox matching (`ashby_widgets.py:509-548`) — reuse a shared `_score_option`. §4.3 finding 3.
9. Fix Ashby's Location typeahead silent-failure (`ashby_widgets.py:230-303`) — add polling + explicit failure reporting instead of silent no-op. §4.3 finding 4.
10. Fix Lever's self-canceling SPONSORSHIP-trap regex (`lever_widgets.py:262-270`). §4.3 finding 5.
11. Consolidate `_score_option` into `verified_select.py` and share across `gh_select.py`/`lever_widgets.py`/`ashby_widgets.py` — would have prevented both #8 and #10. §4.3 finding 6.
12. Add several missing readback checks in Workday fill helpers (`exp_workday_selectors.py:3064-3074,3961-3994,3837-3844`). §4.1 finding 3.
13. Fix the dead `Control+a` select-all chord in Workday's date-spin fields (`exp_workday_selectors.py:2564-2569`) — broken specifically on headless Linux, the project's own target platform. §4.1 finding 4.
14. Strengthen the resume-path guard in `flash_leftovers.py:396-400` to use `assert_dummy_resume_path()` instead of a denylist. §4.4 finding 2.
15. Add a lock to `record_replay.py`'s cache read-modify-write (`:331-365`), reusing the existing `alias_state.json.lock` pattern. §4.5 finding 3.

### Process / infrastructure (no single line fix, but high leverage)

16. Stand up real CI (or at minimum a required pre-merge local hook) running `regression_gates.py --run-eval --eval-strict-safety` — currently nothing anywhere enforces any of the extensive gating code that already exists. §1.6 / §4.5 finding 1.
17. Fix `regression_gates.py`'s false-positive PASS on a clean checkout (`:62-69,72-96`) — missing-artifact should not silently pass. §4.5 finding 2.
18. Replace the markdown-file-based multi-agent coordination (`sota_brainstorm/ORCHESTRATION.md`, `BROWSER_QUEUE.md`) with a file-locked claim mechanism, same pattern as `alias_state.json.lock`. §1.3.
19. Add a standing regression test asserting `TEST_MODE=1` imports never touch `profile.json` on disk — this exact bug class (module-level unconditional PII load) already happened once. §3.1.
20. Unify or explicitly document-as-intentionally-separate the two engines' safety-taxonomy vocabularies (`scorecard.py` vs `scorecard_fast.py`/`eval_suite.py`). §4.5 finding 4.
21. Add cross-retry persistence for `field_attempt_log.py`'s escalation counter in `cycle_orchestrate.py`. §4.4 finding 3.
22. Fix the `CYCLE_AGENTS.md` doc line that contradicts required EEO-via-Flash behavior — low effort, meaningfully misleading if left. §4.4 finding 6.

### Lower priority (maintainability, hygiene — batch these together)

23. Split `fast_fill.py` (~30% reducible via 4 identified extraction targets: `detect_platform`, `run_inpage_flash_leftovers`, Greenhouse-specific logic inside `fill_from_extract`, `_merge_workday_into_report`). §4.2 finding 5.
24. Delete `fast_fill.py`'s duplicate resume-upload verification path; always call the shared `upload_resume_to_page`. §4.2 finding 2.
25. Consolidate the three drifted essay-detection regexes into one shared helper. §4.4 finding 5.
26. Add `pytest.ini` so the (already well-written) 8 test files can run as a real suite. §4.5 finding 7.
27. Rename `exp_entry_prepass.py`/`exp_fill_from_mapping.py` — they're live production code despite the "exp_" prefix. §5.
28. Misc small items: dead `--headed` CLI flag (§4.2 finding 4), `gh_select.py` label-collision + unverified-country-fallback (§4.3 findings 7-8), `eval_urls.json` staleness (§4.5 finding 6), `flash_leftovers.py` truncation detection (§4.4 finding 7), assorted dead code (§4.4 finding 8).

---

*This report reflects the working-tree state as of 2026-07-31. It was produced by direct line-by-line review of every safety-critical primitive plus five parallel deep-dive audits covering all ~33,000 lines of `scripts/fastfill/`. Every finding above cites a specific file and line range; none are speculative.*
