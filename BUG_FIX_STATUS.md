# BUG FIX STATUS — FILL3 High/Medium fill-pipeline (2026-08-06)

**Updated:** 2026-08-06T05:55-04:00  
**Scope:** `BUG_REPORT_PROJECT_PLAGUE.md` section B + FILL2 carry-forward (fill pipeline only)  
**Commit:** none (per request)

## Fixed this session

| ID | Sev | Fix |
|----|-----|-----|
| **FILL3-001** | High | `flash_attempt_failed`: `skyvern_deferred` + `invoked=false` is not Flash failure; hard `error`/`captcha_blocked` still fails |
| **FILL3-002** / **FILL2-S03** | High | CAPTCHA wait: overlay stays **visible** as **Continue** (resume); gate still makes pause-wait yield (2026-08-09: no longer hide/opacity-0) |
| **FILL3-003** / **FILL2-S01** | High | `[role=alert]` → `alert_node`; filtered via `looks_like_gap_message` in `normalize_gaps` |
| **FILL3-004** | Med | Documented Flash/hold/refill/Skyvern matrix in dashboard `_build_fast_fill_cmd` + `--flash-leftovers` help (defaults kept — hold+refill intentional) |
| **FILL3-005** | Med | **Verified ATS-owned fix present** — `_ashby_zip_field_present` treats HTML zip question as present (ATS3-002); Location commit uses Tab not Escape (ATS3-012). Early `zip_field_absent_on_form` false N/A closed. Remaining live mount miss = **ATS2-017** (`zip_dependent_never_revealed`). |
| **FILL3-006** | Med | Refill loop: leftover+page fingerprint gate — stop on stable no-progress; skip ashby/GH scroll reassert when page fp unchanged |
| **FILL3-007** | Med | Mid-refill CAPTCHA re-pause each pass in `_run_in_session_refill_loop` |
| **FILL3-008** | Med | Honest `flash_zero_fill` message when `invoked=false` (no false “invoked but filled 0”) |
| **FILL3-009** | Med | Pause UX: “between actions / not mid-widget”; wait sites at refill pass start + Workday autofill-resume poll |
| **FILL3-011** | Med | Autofill-with-Resume: `autofill_filename_verify_ok` — no advance on filename_visible when input exists + FileList empty |
| **FILL3-012** | Med | Use My Last (real only): set `prefill_keep_policy=use_my_last_soft_match_keep`; document intentional soft-match keep |
| **FILL3-013** | Med | Naming: `inpage_ran` / `flash_engine=inpage`; `invoked` = LLM only; essay-only + `inpage_ran` exempt from fail |
| **FILL3-015** / **FILL2-S02** | Med | CAPTCHA resume = overlay **Continue** or Enter / `.captcha_continue` / `.fill_continue`; FILL-008 challenge-must-be-gone. Hold also uses Continue → resume fill (2026-08-09) |
| **FILL3-016** | Med | **ATS3-003 surface FIXED** — `compute_stuck_on_same_page` requires `advance_clicked`; FAIL-before-ADVANCE does not sticky-stuck. SPA stuck-clear on DOM move present. See ATS2-011 below (further mitigated this verify pass). |
| **FILL3-017** | Low | Throttle overlay re-inject (2s default; `force=` bypass) |
| **FILL3-018** / **DASH2-011** / **UI-019** | Med | Already fixed: `_parse_test_mode` requires explicit flag (400 if missing); dashboard always sends it |
| **FILL3-019** | Low | `press_escape_unless_captcha` + `escape_safe_while_captcha` adopted on GH / fiber (`verified_select`) / Workday Escape dismiss paths — no raw Escape outside helper |
| **FILL3-020** | Med | Pass-2 `force_flash_demoted`: skip promote/re-Flash when already_correct keep or same leftover fingerprint already attempted |

## Intentionally not changed

| ID | Why |
|----|-----|
| **FILL2-008** / **FILL3-014** | Bare `"Extension"` → phone-ext kept — changing would break ATS phone-ext fields (not safe) |
| **ATS2-010** / **FILL3-010** | Vision fail-closed Ready — intentional safety (`apply_live_vision_gate` → AMBIGUOUS / `vision_incomplete`). Alert noise mitigated by FILL3-003. |
| Hold ≠ Ready / never-submit / never CAPTCHA solve / dummy-only | Unchanged |

## Still open / residual (fill)

| ID | Status | Notes |
|----|--------|-------|
| **FILL3-010** | **INTENTIONAL** | Ready rare when vision judge fails closed (ATS2-010) + hold≠Ready. |
| **ATS2-017** | **OPEN** (residual live debt) | Event-driven wait + capped scrolls + Location re-open + taxonomy `zip_dependent_never_revealed` vs absent. Still tenant-dependent when HTML has zip but input never mounts. |

---

# ATS verify pass (2026-08-06) — residual “still open” audit

**Updated:** 2026-08-06T05:50-04:00  
**Job:** Re-verify High-ATS claimed fixes vs residual “still open”; re-implement only if missing.  
**Commit:** none

## Verdict table (requested IDs)

| ID | Status | Evidence (one line) |
|----|--------|---------------------|
| **ATS3-006** | **FIXED** | Fiber JS `tokenBound` (+20) replaces raw `includes` — `verified_select._FIBER_SEARCH_SELECT_JS` |
| **ATS3-009** | **FIXED** | `addressSection_regionSubdivision1` in `_WD_COMBOBOX_AIDS` → pack mode `combobox` |
| **ATS3-010** | **FIXED** | Age-gate candidates Yes-only (`["Yes", "I am 18 or older", …]`) — no bare `"No"` |
| **ATS3-012** | **FIXED** | Ashby post-Location: Tab + `_wait_ashby_zip_input` (no Escape); typable dropdown Tab when `commit_probe` armed |
| **ATS3-016** | **FIXED** | `_default_score_option` gender polarity + `soft_value_match` (no raw `a in o`) |
| **ATS2-011** | **FIXED** (mitigated) | Phase B contact ADVANCE now shares SPA poll/`spa_stuck_cleared` with gate; orchestrator recovers left-contact sticky-stuck. Residual: genuine `contact_incomplete` correctly skips C–E. |
| **ATS2-017** | **OPEN** (residual) | Longer event-driven wait + capped scrolls + one Location re-open; honest leftover `zip_dependent_never_revealed` (HTML has zip, input never mounts) vs `zip_field_absent_on_form`. Live tenant debt remains. |
| **ATS2-010** | **INTENTIONAL** | Vision exception → fail-closed Ready (safety) |
| **ATS3-011** | **FIXED** | `_poll_wd_spa_after_advance` + `_wd_spa_moved` / synthetic step_hint |
| **ATS3-013** | **FIXED** | Full-string-first + `_early_unique_high_match` + `_append_into_filter` in typable dropdown |
| **ATS3-014** | **FIXED** | `GH_SELECTOR_PACK` Country/Location/State `.select__control` combobox rows |
| **ATS3-015** | **FIXED** | `_poll_spa_settle` replaces fixed 3.5–4.5s Apply/auth sleeps (w/ ATS2-016) |

## Also verified present (High ATS claimed FIXED)

| ID | Status | Evidence |
|----|--------|----------|
| **ATS3-001** | **FIXED** | Location commit requires `option_clicked` or `dependent_revealed` |
| **ATS3-002** | **FIXED** | HTML zip question ⇒ present even before input mounts |
| **ATS3-003** | **FIXED** | `compute_stuck_on_same_page` requires `advance_clicked` |
| **ATS3-004** | **FIXED** | Fiber returns `scored` without click; Python validates then clicks |
| **ATS3-005** | **FIXED** | Shared `soft_value_match` gender polarity |
| **ATS3-007** | **FIXED** | Expanded `_STATE_CONFUSABLE_PAIRS` (VA/VT, MI/MN, …) |
| **ATS3-008** | **FIXED** | `clear_closest_match` rejects weak last-word floors |

## Code improved this verify pass

1. **ATS2-011** — `_phase_b_contact` used fixed 1.5s fingerprint settle (unlike `_gate_then_advance`). Extracted `_poll_wd_spa_after_advance` / `_clear_false_stuck_after_spa_move`; phase_b + post-phase_b recovery use them.
2. **ATS2-017** — `fill_typable_dropdown` / `probe_location_committed` still Escape’d after Location pick when `commit_probe` armed → now Tab.

## Files touched (ATS verify)

- `scripts/fastfill/exp_workday_selectors.py` — shared SPA settle; phase_b + orchestrator recovery
- `scripts/fastfill/verified_select.py` — Tab when `commit_probe` (Ashby zip)
- `scripts/fastfill/test_ats3_open_fixes.py` — ATS2-011 / ATS3-011 helpers
- `scripts/fastfill/test_verified_select.py` — ATS3-006/012/016 + ATS2-017

## Tests (focused)

```text
test_verified_select: OK
test_ats3_open_fixes: OK
test_workday_app_questions: OK
test_page_progress: OK
```
