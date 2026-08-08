# Remaining bugs status — merged report

**Date:** 2026-08-06 (post-DASH2 pass)  
**Sources:** first-pass fix agents + second-pass hunts + continuation + DASH2 agent `15357a7b`  
**Continuation commits (claimed):** `f0eaecbe` (Fill), `c09bde18` (ATS), `e90e0f6e` (Chrome/PR/Hybrid)  
**Method:** Prior report + code/tests confirming IDs closed. Verified `PartyRockLockAborted` + orphan `_force_stuck_orphaned_in_progress` in `server.py`.  
**No commits.** Report-only.

---

## Fixed (by ID)

### ATS — first pass (`b4ae88b4`) + continuation (`c09bde18`)

| ID | Sev | Summary |
|----|-----|---------|
| **ATS-001** | Critical | Filtered option index remap |
| **ATS-002** | Critical | Autofill Continue only after verified resume |
| **ATS-003** | Critical | “Use My Last” removed from dummy/test apply selectors |
| **ATS-004** | High | Resume filename scoped to upload chrome / FileList |
| **ATS-005** | High | Upload-present requires file controls |
| **ATS-006** | High | Phone device: Mobile ≠ Home |
| **ATS-007** | High | Dial detector → `gh_select.looks_like_dial_code_option` |
| **ATS-008** | High | Bare `+1` no longer “already correct” (+ **ATS2-002** post-click US name / NANP exclude) |
| **ATS-009** | High | iframe ADVANCE checks form fields + required empties |
| **ATS-010** | High | Lever radio polarity before skip |
| **ATS-011** | High | Non-US Country* needs matching `iti__XX` flag |
| **ATS-012** | High | Disability: require radio actually checked |
| **ATS-013** | Medium | `click_best_option` intent + dial reject + remap |
| **ATS-014** | Medium | Fiber confusable reject (+ **ATS2-003** reject before click) |
| **ATS-015** | Medium | Empty filtered list no longer falls back to full list |
| **ATS-016** | Medium | Relocate/commute → Yes before catch-all No |
| **ATS-017** | Medium | Ashby consent verifies `is_checked` |
| **ATS-018** | Medium | Ashby Flash choices require aria/checked (+ **ATS2-006** not always-first) |
| **ATS2-001** | Critical | Placeholder/empty slots keep locator indices (`wait_for_option_texts`) |
| **ATS2-002** | High | US phone post-click requires country name; NANP non-US fail |
| **ATS2-003** | Medium | Fiber confusable/dial reject before click |
| **ATS2-004** | Medium | Ashby `_value_already_correct` word-boundary / soft match |
| **ATS2-005** | Medium | `option_mappings` refuse confusable / dial-poisoned chosen-first |
| **ATS2-006** | Medium | Ashby Flash checkbox group scores options (not always first) |
| **ATS2-007** | Medium | Learning allow-list drops global how-heard / gender Male |
| **ATS2-008** | Medium | GENERIC pack: state not bare text; phone selectors tightened |
| **ATS2-009** | Medium | WD_SELECTOR_PACK includes phone-device-type |
| **ATS2-012** | Medium | `_click_matching_option` soft/word-boundary over raw substring |
| **ATS2-013** | Medium | GH salary ~⅔ / SCHOOL “Create …” fallbacks blocked |
| **ATS2-014** | Medium | GH `_score_option` full confusable state table |

### Dashboard — first pass (`9f27b079`) + DASH2 (`15357a7b`) — DONE

| ID | Summary |
|----|---------|
| **DASH-001** | Hold suspends kill deadline |
| **DASH-002** | Abort checks before milestones / agent fallback / fill |
| **DASH-003** | Soft-delete kills proc + aborts; fill-end never undeletes |
| **DASH-004** | `applied` in `FILL_ABORT_STATUSES` |
| **DASH-005** | Crash→stuck no-ops when aborted |
| **DASH-006** | Restore accepts `deleted` + `unblock_job` |
| **DASH-007** | Classic XSS: `escapeHtml(question)` + id escapes |
| **DASH-008 / 009** | Cancel only when run in progress |
| **DASH-010** | Real Fill-without-PDF no longer false-claims skip PartyRock |
| **DASH-014** | Abort before agent PartyRock fallback |
| **DASH-018** | Ops hybridFill 409 wording |
| **DASH2-001** | Cancel mid PartyRock lock wait → `PartyRockLockAborted` |
| **DASH2-002** | Orphaned local fill → age/startup `_force_stuck_orphaned_in_progress` |
| **DASH2-003** | Classic Deleted/Skipped Restore |
| **DASH2-004** | Agent fallback missing tex → force stuck |
| **DASH2-006** | Mark as applied mid-fill kills fill proc |
| **DASH2-013** | Ready hold copy no longer says “Cancel when done” |

### Fill — first pass (`fc5a41cc`) + continuation (`f0eaecbe`)

| ID | Summary |
|----|---------|
| **FILL-001** | Cookie dismiss: exact roles + `gate_locator_click` + FINAL refuse |
| **FILL-002** | No bare `"Decline"`; refuse EEO Decline labels |
| **FILL-003** | `PHONE_EXTENSION` Flash-forbidden + deferred / stripped |
| **FILL-004** | Phone/ext needs phone context; contract/file guard-words |
| **FILL-005** | `can_claim_ready` fail-closed without vision complete |
| **FILL-006** | In-page `validate_eeo_against_catalog` |
| **FILL-007** | Dummy resume → `assert_dummy_resume_path` only |
| **FILL-008** | Enter/sentinel while CAPTCHA visible → keep waiting |
| **FILL-009** | CAPTCHA probe exception → skip cookie dismiss |
| **FILL-010** | Optional radios `requiredish` false by default |
| **FILL-011** | Real mode: no dummy password fallback |
| **FILL-016** | CLI refuses `--resume-path` with `--test-mode` |
| **FILL2-001** | Gender polarity: `male`⊄`female` / `man`⊄`woman` soft-match |
| **FILL2-002** | Miss-scan `requiredish` false unless required/`*`/aria *(verify)* |
| **FILL2-003** | `collect_form_gaps` / evaluate error fail-closed |
| **FILL2-004** | Essay leftover regex excludes LinkedIn/GitHub/Portfolio URL |
| **FILL2-005** | Name/selector-only phone-ext stripped from Flash handoff |
| **FILL2-006** | Skyvern Flash path holds EEO for catalog/inpage gate |
| **FILL2-007** | Cookie omit bare `"Reject"`/`"Agree"`/`"OK"` |
| **FILL2-009** | Ready/CAPTCHA unit tests align with FILL-005/008 *(verify)* |
| **FILL2-010** | Soft-match floor raised (`best_s > 0` no longer enough) |

### Chrome / PartyRock / Hybrid — first pass (`558f6999`) + continuation (`e90e0f6e`)

| ID | Summary |
|----|---------|
| **CHR-001** | Desktop app rebuilt; `on reopen` → focus UI |
| **CHR-002** | Focus by PID / System Events (no Chrome `activate`) |
| **CHR-005** | Stale Singleton* clear on OpenClaw (+ legacy) profile |
| **CHR-006 / PR-001** | `open_partyrock.sh` → OpenClaw CDP (+ **CHR2-001** CfT binary) |
| **CHR-007** | Orphan kill never kills hold/CAPTCHA; headed refuse instead |
| **CHR-008** | `fcntl.flock` around headed busy-check + launch |
| **CHR-010** | Preflight excludes `dashboard_ui_profile` |
| **CHR2-001** | Force CfT for PartyRock (`chrome_for_testing.py` / `executablePath`) |
| **CHR2-002** | Start blocked while Ready/CAPTCHA + live fill CfT/hold |
| **CHR2-003** | Refresh preserves fill CfT on CAPTCHA/Ready hold |
| **PR-003** | `clear_tab_meta` only after successful close |
| **PR-004** | PartyRock URL from `PARTYROCK_TEST_MODE` only |
| **PR-005** | Lock timeout 900s; no lock through no-JD agent fill |
| **PR2-001** | `tailor_resume.py` disconnect-only (no `browser.close()` on CDP) |
| **HYB-001** | CLI `hybrid_fill.py` refuses when Playwright `fast_fill` exists |
| **HYB2-001** | Skyvern/hybrid refuse session when Playwright CfT fill/hold live |

---

## Still open — Critical

*None.*

---

## Still open — High

*None.*

---

## Still open — Medium

### Dashboard (`DASH2-*`)

| ID | Title |
|----|-------|
| **DASH2-005** | UI trusts stale `resume_path`; server uses disk truth |
| **DASH2-007** | `hybrid_fill` still uses slow gateway `is_session_running`; Start uses local |
| **DASH2-008** | Real “Fill with resume” without PDF still runs PartyRock (copy honest; behavior unchanged) |
| **DASH2-009** | Mid-fill resume upload has no in-progress guard |
| **DASH2-010** | Reconcile auto-retry can spawn agent on non-agent fills |
| **DASH2-011** | `_parse_test_mode` defaults True (raw API silent dummy) |
| **DASH2-012** | Treat-as-on-file is memory-only (not localStorage) |

### Fill (`FILL2-*`)

| ID | Title |
|----|-------|
| *(suspected, unproven)* | FILL2-S01 alert noise as gaps; FILL2-S02 CAPTCHA vs fill-pause stdin; FILL2-S03 pause overlay z-index |

### ATS (`ATS2-*`)

| ID | Title |
|----|-------|
| **ATS2-010** | Ready false negative on vision gate exception (fail-closed) |
| **ATS2-011** | Multipage stuck when contact never ADVANCEs |
| **ATS2-017** | Ashby zip often missing after Location (live debt / Airwallex-class) |

### Chrome / PartyRock / Hybrid

| ID | Title |
|----|-------|
| **CHR2-004** | Name-based CfT focus can raise dashboard UI window |
| **CHR2-005** | `fill_hold_or_captcha_active` gaps (hybrid / stale marker TTL) |
| **CHR2-006** | Manager `chrome_count` still meaningless (prior CHR-009) |
| **CHR2-007** | Dashboard UI Google Chrome.app fallback still present |
| **CHR2-008** | Docs/comments still claim `partyrock_chrome_profile` for open_partyrock |
| **PR2-002** | CDP attach / `openclaw browser start` best-effort failures |
| **PR2-003** | Early agent/tailor failure can leave stale status_detail |
| **HYB2-002** | Manager prompts still honor-system for Chrome cap |

---

## Still open — Low (deferred)

| ID | Title |
|----|-------|
| **DASH2-014** | Hold ingest abort set incomplete vs `FILL_ABORT_STATUSES` |
| **DASH2-015** | `markSubmitted` ignores HTTP errors |
| **DASH2-016** | Mutating fetches omit `encodeURIComponent(jobId)` |
| **DASH2-017** | Fill face `onclick` not wrapped in `escapeAttr` |
| **DASH2-018** | Post-cancel pipeline may still compile/fit/publish before abort gate |
| **FILL2-008** | Bare label `"extension"` still classifies as `PHONE_EXTENSION` |
| **ATS2-015** | RecursionError catch-and-miss on phone/device |
| **ATS2-016** | Fixed sleeps after Apply/Autofill/advance |
| **CHR2-009** | Hardcoded absolute paths in Desktop applet / launcher |
| **CHR2-010** | `--focus-ui` failure swallowed on Dock reopen |
| **PR2-004** | `/json/new` PUT-only; `wait_tab_gone` 2s |

---

## Recommended next

1. **ATS2-017** — Ashby zip after Location (live debt).
2. **ATS2-011** — multipage ADVANCE stall.
3. Batch remaining Medium Chrome/docs residuals (CHR2-004–008, PR2-002/003, HYB2-002).
4. Remaining DASH2 Medium (005, 007–012) when convenient.
5. Low deferred when convenient.

---

## Tally

| Bucket | Count |
|--------|------:|
| Fixed (all passes + continuation + DASH2) | ~96 IDs |
| **Critical remaining** | **0** |
| **High remaining** | **0** |
| **Medium remaining** | ~18 |
| **Low remaining** | ~11 |

**Safety unchanged:** never-submit, never CAPTCHA solve, never invent EEO, dummy-only automation PII.
