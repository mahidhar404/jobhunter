# Project plague report (2026-08-06)

> **Final status (2026-08-06):** Canonical FIXED / OPEN / INTENTIONAL / DEFERRED ledger → **[`ats_notes/BUG_FIX_STATUS_PLAGUE.md`](BUG_FIX_STATUS_PLAGUE.md)**. Remaining open: **ATS2-017** (Ashby zip-never-mounts residual). **CHR3-005** mitigated FIXED (PID focus helpers + docs; Dock icon still shared). Findings below are the original hunt ledger — do not treat severity tables here as live status.

**Sources:** `BUG_REPORT_REMAINING.md` (carry-forward) + coordinator UI skim (`index.html` / `app.js`) + sibling deep dives:
- **UI-001** agent `953d8af5` — Dashboard UI / use cases
- **FILL3** agent `c8a3202a` — Fastfill / pause / Flash / Ready / CAPTCHA
- **ATS3** agent `52ec9a32` — ATS selects / multipage / zip / thrash
- **CHR3** agent `4263e0cf` — Chrome / PartyRock / Dock / hybrid

**Method:** Merge + verify high-impact UI claims in code. **Dashboard UI High/Medium (section A + DASH2 carry-forward) fixed 2026-08-06** — see status notes below. Sibling FILL3/ATS3/CHR3 still open unless noted.

**Safety unchanged:** never-submit, never CAPTCHA solve, never invent EEO, dummy-only automation PII.

---

## Fixed this pass (dashboard UI — 2026-08-06)

| ID | Fix |
|----|-----|
| **UI-001** / **UI-014** / **UI-015** | Skipped mission chip; Cancel/Skip surfaces job under Skipped + Restore; pipeline Cancelled/Skipped auto-switches to Skipped queue |
| **UI-002** / **UI-008** | Fill disabled on Ready/CAPTCHA; multi-job busy uses `fill_hold_active`; server 409 on same-job Start/Fast fill while hold live |
| **UI-003** | Classic Fast fill blocked on Ready/CAPTCHA + other-job hold |
| **UI-004** / **UI-023** | Ops Skip button wired to `skipJob` |
| **UI-005** / **DASH2-005** | List/`resume_on_disk` disk truth; GET `/resume` uses `resolve_job_resume_file` |
| **UI-006** / **DASH2-008** | Real “Fill with resume” disabled without PDF |
| **UI-007** / **DASH2-009** | Resume upload/clear blocked mid-fill (UI + server 409) |
| **UI-009** | Removed Retry fill radio |
| **UI-010** / **UI-011** | Tailor copy clarifies force; Skip-no-PDF hidden when PartyRock OFF |
| **UI-013** | Ops copy Start/Fast fill → Fill |
| **UI-016** | Classic Restore sole primary for cancelled/skipped |
| **UI-017** / **DASH2-007** | Hybrid uses `_session_running_local` |
| **UI-018** / **DASH2-010** | Reconcile agent-retry only for `tailoring`/`resuming` |
| **UI-019** / **DASH2-011** | `_parse_test_mode` fail-closed without flag |
| **UI-020** / **DASH2-012** | Treat-as persisted in localStorage |
| **UI-021** / **UI-022** | Removed dead `primaryAction` / Ops `hybridFillDummy` |
| **UI-024**–**026** / **UI-028**–**029** | markSubmitted errors; encodeURIComponent; escapeAttr; one Empty Deleted; no has_description leak |
| **UI-027** / **DASH2-014** | Hold ingest abort uses full `FILL_ABORT_STATUSES` |
| **UI-030** | Documents → **Copy path** vs **Open PDF** by state |
| **UI-031** | Discover running: stop glyph + Abort label (orange); idle keeps start icon |
| **UI-032** | Fill/Resume menus click-pin via pop title / touch first-tap; Escape/outside dismiss |
| **UI-034** | `ops-preview.html` kept as redirect-only (bookmarks → `/`) |
| **UI-035** | Ready exit copy + dossier hint (Mark applied / close browser; no Cancel) |
| **UI-036** | Multi-opening documented as tag/filter/sort only (+ tooltips) |
| **UI-037** | Mark applied pre-Ready: stronger confirm |
| **UI-038** | Delete on applied: stronger confirm |
| **UI-039** | Profile util banner: real PII ≠ Test Mode dummy |
| **UI-040** | Posted “Older than 30d” excludes unknown dates |
| **DASH2-018** | `pipeline_milestone` no-ops all patches when aborted; abort before compile/fit |

**Deferred (product):** none — **UI-033** FIXED (Classic frozen; `/classic` → Ops `/`).

---

## Executive summary

Critical/High from prior hunts were largely cleared (~96 IDs fixed; prior Critical/High remaining = **0**). This third pass re-opens the plague ledger with **new High/Critical user pain** that previous passes missed or that regressions reintroduced:

1. **Refresh kills fill hold** (CHR3-001/002) and can **orphan-kill PartyRock login** (CHR3-003 — Critical). Docs claim the opposite.
2. **Ops Cancel orphans jobs** — cancelled/skipped leave Progress/Open and have **no mission tab** (UI-001). Restore exists but you often cannot reach the job.
3. **Ready hold still allows Fill** on the same job (UI-002) — second Start while CfT hold is live.
4. **Dashboard Flash+hold+refill** silently defers Skyvern then may **FAIL as “Flash failed”** when `invoked=false` (FILL3-001). Alert noise blocks Ready (FILL3-003 / FILL2-S01).
5. **Ashby zip / Location** still broken in live debt (ATS2-017 + ATS3-001/002); **Male⊂Female soft-match** still in shared matcher (ATS3-005); fiber click-then-reject leaves wrong options (ATS3-004).

**Honest tally (open plague, not historical fixed):**

| Bucket | Approx count |
|--------|-------------:|
| Prior Medium carry-forward | ~18 |
| Prior Low carry-forward | ~11 |
| New UI-* | ~40 (4 High) |
| New FILL3-* | ~20 (3 High) |
| New ATS3-* | ~16 (5 High) |
| New CHR3/PR3/HYB3-* | ~15 (1 Critical, 3 High) |
| **New Critical** | **1** (CHR3-003) |
| **New High (user-visible)** | **~15** |

The product is safer than two days ago on known autofill/select bugs, but **operator UX and Chrome lifecycle are still a plague**: dual UIs, overlapping PartyRock skip controls, Refresh lies, and Ready/Flash honesty gaps.

---

## Unfixed from previous hunt (carry-forward)

Copied/verified from `ats_notes/BUG_REPORT_REMAINING.md` (post-DASH2). Sibling hunts re-confirmed these are still open unless noted.

### Still open — Critical / High (prior)

*None from prior report.*

### Still open — Medium (prior)

#### Dashboard (`DASH2-*`)

| ID | Title | Sibling note |
|----|-------|--------------|
| **DASH2-005** | UI trusts stale `resume_path`; server uses disk truth | **FIXED** = UI-005 |
| **DASH2-007** | `hybrid_fill` still uses slow gateway `is_session_running`; Start uses local | **FIXED** = UI-017 |
| **DASH2-008** | Real “Fill with resume” without PDF still runs PartyRock (copy honest; behavior unchanged) | **FIXED** = UI-006 |
| **DASH2-009** | Mid-fill resume upload has no in-progress guard | **FIXED** = UI-007 |
| **DASH2-010** | Reconcile auto-retry can spawn agent on non-agent fills | **FIXED** = UI-018 |
| **DASH2-011** | `_parse_test_mode` defaults True (raw API silent dummy) | **FIXED** = UI-019 / FILL3-018 |
| **DASH2-012** | Treat-as-on-file / Skip-no-PDF is memory-only (not localStorage) | **FIXED** = UI-020 |

#### Fill (`FILL2-*`)

| ID | Title | Sibling note |
|----|-------|--------------|
| **FILL2-S01** | Alert noise as gaps | Confirmed → FILL3-003 High |
| **FILL2-S02** | CAPTCHA vs fill-pause stdin / dual continue | Confirmed → FILL3-015 |
| **FILL2-S03** | Pause overlay z-index | Confirmed → FILL3-002 High |

#### ATS (`ATS2-*`)

| ID | Title | Sibling note |
|----|-------|--------------|
| **ATS2-010** | Ready false negative on vision gate exception (fail-closed) | **Intentional** safety; still open |
| **ATS2-011** | Multipage stuck when contact never ADVANCEs | Amplified by ATS3-003 |
| **ATS2-017** | Ashby zip often missing after Location (live debt) | Amplified by ATS3-001/002/012 |

#### Chrome / PartyRock / Hybrid

| ID | Title | Sibling note |
|----|-------|--------------|
| **CHR2-004** | Name-based CfT focus can raise dashboard UI window | = CHR3-006 |
| **CHR2-005** | `fill_hold_or_captcha_active` gaps (hybrid / stale marker TTL) | Worse — PartyRock counted as hold |
| **CHR2-006** | Manager `chrome_count` still meaningless | = CHR3-008 |
| **CHR2-007** | Dashboard UI Google Chrome.app fallback still present | Still open |
| **CHR2-008** | Docs/comments still claim `partyrock_chrome_profile` for open_partyrock | Still open |
| **PR2-002** | CDP attach / `openclaw browser start` best-effort failures | Still open |
| **PR2-003** | Early agent/tailor failure can leave stale status_detail | Tied to Refresh/PR3 |
| **HYB2-002** | Manager prompts still honor-system for Chrome cap | Still open |

### Still open — Low (prior deferred)

| ID | Title |
|----|-------|
| **DASH2-014** | Hold ingest abort set incomplete vs `FILL_ABORT_STATUSES` | **FIXED** = UI-027 |
| **DASH2-015** | `markSubmitted` ignores HTTP errors | **FIXED** earlier pass |
| **DASH2-016** | Mutating fetches omit `encodeURIComponent(jobId)` | **FIXED** earlier pass |
| **DASH2-017** | Fill face `onclick` not wrapped in `escapeAttr` | **FIXED** earlier pass |
| **DASH2-018** | Post-cancel pipeline may still compile/fit/publish before abort gate | **FIXED** this pass |
| **FILL2-008** | Bare label `"extension"` still classifies as `PHONE_EXTENSION` |
| **ATS2-015** | RecursionError catch-and-miss on phone/device |
| **ATS2-016** | Fixed sleeps after Apply/Autofill/advance |
| **CHR2-009** | Hardcoded absolute paths in Desktop applet / launcher |
| **CHR2-010** | `--focus-ui` failure swallowed on Dock reopen |
| **PR2-004** | `/json/new` PUT-only; `wait_tab_gone` 2s |

### Intentionally left open

| ID | Why intentional |
|----|-----------------|
| **ATS2-010** | Vision exception → fail-closed Ready (prefer false Ready denial over false Ready claim) |
| Dummy-only Test Mode / never-submit / never CAPTCHA / shared EEO | Hard safety — not bugs |
| Hold ≠ Ready | Correct: `--hold-open` alone must not promote Ready |

---

## New / UI / use-case findings (merged siblings)

### UI deep dive (`953d8af5`) — highlights

| ID | Sev | Title |
|----|-----|-------|
| **UI-001** | High | Cancelled/skipped jobs unreachable in Ops (no Skipped mission chip; filters AND with current queue) |
| **UI-002** | High | Fill enabled on Ready while hold browser live (same-job re-Start) |
| **UI-003** | High | Classic Fast fill also allowed on Ready |
| **UI-004** | High | Ops has no Skip — only Delete (`skipJob` dead) |
| **UI-005…020** | Med | Stale resume_path, real Fill-without-PDF PartyRock, mid-fill upload, multi-job busy ignores hold, Retry≡with-resume, PartyRock OFF vs Tailor, triple skip paths, classic dual fill, stale “Start/Fast fill” copy, dead Cancelled/Skipped filters, post-Cancel ghost dossier, hybrid slow session check, reconcile agent retry, test_mode default True, Skip-no-PDF memory-only |
| **UI-021…040** | Low | Dead `primaryAction` / Ops `hybridFillDummy` / Ops `skipJob`; markSubmitted errors; encode hygiene; dual Empty Deleted; `has_description`/`lazy` leak; Discover start/abort overload; dual classic/ops; Ready exit weak; etc. |

Coordinator verification: mission-stats has Stuck/Ready/Progress/Open/Applied only — **no Skipped**. `primaryAction`, `hybridFillDummy`, `skipJob` are defined in `app.js` and never called from Ops UI.

### FILL3 deep dive (`c8a3202a`) — highlights

| ID | Sev | Title |
|----|-----|-------|
| **FILL3-001** | High | Dashboard Flash+hold+refill silently kills Skyvern; verdict may FAIL as “Flash failed” when `invoked=false` |
| **FILL3-002** | High | Pause overlay can cover / intercept CAPTCHA clicks (S03 upgraded) |
| **FILL3-003** | High | Alert noise → Ready false negative (S01 confirmed; `looks_like_gap_message` dead) |
| **FILL3-004…020** | Med/Low | Flag matrix lies; Ashby zip scroll thrash; refill/demote loops; mid-refill CAPTCHA; dishonest `flash_zero_fill`; pause checkpoint-only; Ready honesty stack; Autofill filename-only verify; real Use My Last thrash; Flash naming collision; CAPTCHA Enter vs Continue; multipage stall; overlay inject spam; refill force_flash thrash |

### ATS3 deep dive (`52ec9a32`) — highlights

| ID | Sev | Title |
|----|-----|-------|
| **ATS3-001** | High | Ashby Location false-commit from display match alone |
| **ATS3-002** | High | `_ashby_zip_field_present` conflates “not mounted yet” with “zip N/A” |
| **ATS3-003** | High | Generic ADVANCE marks `stuck_on_same_page` on intentional FAIL-before-ADVANCE |
| **ATS3-004** | High | Fiber click-then-reject leaves wrong option committed |
| **ATS3-005** | High | `soft_value_match` still has Male⊂Female (FILL2-001 incomplete in shared matcher) |
| **ATS3-006…016** | Med/Low | Fiber substring scoring; incomplete confusable states; weak `clear_closest_match`; WD county as text; age-gate “No”; SPA fingerprint stuck; Escape+Tab zip race; typable thrash; thin GH pack; fixed sleeps; soft `a in o` gender |

### CHR3 / PR3 / HYB3 deep dive (`4263e0cf`) — highlights

| ID | Sev | Title |
|----|-----|-------|
| **CHR3-003** | **Critical** | PartyRock OpenClaw CfT counted as fill Chrome → orphan-kill login/tabs or false headed_cap |
| **CHR3-001** | High | Refresh `finally` undoes `preserve_fill_cft` |
| **CHR3-002** | High | Refresh always kills tracked fill/tailor procs |
| **PR3-001** | High | Manual PartyRock “browser tool” ≠ OpenClaw login (docs lie; sign-in wall) |
| **CHR3-004…009, PR3-002/003, HYB3-001** | Med | PartyRock counted as hold; triple CfT Dock confusion; name-focus raises UI; Refresh docs lie; cap over-count; test gap; fragmented login paths; hybrid refuses on PartyRock alone |

---

## Unnecessary UI / dead features

Remove, freeze, or redesign — ranked by operator confusion:

| # | Item | Verdict | Why |
|---|------|---------|-----|
| 1 | **Fill mode “Retry fill”** | **Remove or redefine** | Identical to “Fill with resume” (`startJobFillMode` same skip/force). |
| 2 | **Skip PartyRock (no PDF) when PartyRock header is OFF** | **Remove / hide** | Triple path to same fill-only outcome (toggle + checkbox + Fill-with-resume). |
| 3 | **Ops `hybridFillDummy` + primary Fast-fill button path** | **Remove dead JS** | Unused in Ops; classic still dual Start/Fast fill. |
| 4 | **`primaryAction()`** | **Delete** | Never called; dossier builds CTAs ad hoc. |
| 5 | **Ops `skipJob()` without Skip button** | **Wire Skip XOR delete fn** | Classic has Skip; Ops only Delete → soft-triage black hole (UI-004) + Cancel black hole (UI-001). |
| 6 | **Pipeline filter Cancelled / Skipped** | **Remove until Skipped queue exists** | Always empty on reachable mission queues. |
| 7 | **Mission-stat gap (no Skipped)** | **Add chip or stop producing skipped/cancelled** | Cancel currently orphans jobs from all tabs. |
| 8 | **Classic Fast fill** | **Collapse into Start/Fill** | PartyRock-off Start already fill-only; dual endpoints confuse. |
| 9 | **Second Empty Deleted** (filters + prune) | **Keep one** | Duplicate destructive. |
| 10 | **`has_description` / `lazy` chip** | **Remove** | Dev leak in Evidence header. |
| 11 | **`/classic` long-term dual UI** | **Done (UI-033):** Classic frozen; 302 → Ops `/` | Was root of Start vs Fill drift |
| 12 | **`ops-preview.html` stub** | **Redirect-only OK** | Bookmarks → `/`; stub kept (UI-034). |
| 13 | **`dossier_mock.html`** | **Keep as mock or park** | Not product; Fine for design, not shipping. |
| 14 | **`hybrid_fill` naming** when `fast_fill` always wins | **Rename / delete Skyvern path for dashboard** | FILL3: Skyvern deferred on hold+refill; gates still punish `invoked=false`. |
| 15 | **`looks_like_gap_message` unused** | **Wire or delete** | Designed filter abandoned → Ready FN. |
| 16 | **Legacy `partyrock_chrome_profile` mythology** | **Scrub docs/comments** | Opener retired; noise + CHR2-008. |
| 17 | **Overlapping PartyRock openers** | **One canonical path** | Prefer `./open_partyrock.sh` / CfT helper; stop advising generic browser tool. |
| 18 | **Manager `chrome_count` / raw pgrep recipes** | **Fix or remove** | Meaningless over-count. |
| 19 | **Ops Skip-no-PDF checkbox in Real Mode** | **Hide when disabled** | Noise that only says “Test Mode only”. |
| 20 | **Classic Restore + Retry both primary on cancelled** | **Pick one recovery** | Competing CTAs. |

---

## Prioritized top 15 user-visible plagues

Ordered by “Yogesh hits this while operating the product today”:

| Rank | ID(s) | Plague | Why it hurts |
|-----:|-------|--------|--------------|
| 1 | **CHR3-003** | PartyRock CfT treated as fill Chrome | After tailor, fill can **kill PartyRock login/tabs** or refuse headed run. |
| 2 | **CHR3-001 / 002** | Refresh destroys hold/fill | Docs say Refresh preserves CAPTCHA/Ready; actually kills procs + CfT in `finally`. |
| 3 | **UI-001** | Cancel → job vanishes from Ops lists | Cannot find cancelled/skipped to Restore; ghost dossier only until click-away. |
| 4 | **UI-002 / UI-003** | Fill/Fast fill on Ready hold | Starts second pipeline on same job while review browser is open. |
| 5 | **FILL3-002 / S03** | Pause overlay covers CAPTCHA | Human cannot click challenge; Enter ≠ Continue. |
| 6 | **FILL3-001 / 013** | Flash “failed” after dashboard defer | Skyvern skipped by design; scorecard/verdict still blames Flash. |
| 7 | **FILL3-003 / S01** | Alert noise blocks Ready | Cookie/info `[role=alert]` → never Ready on complete form. |
| 8 | **ATS2-017 + ATS3-001/002** | Ashby zip missing / Location false-commit | Blank zip; Airwallex-class live debt. |
| 9 | **ATS3-005** | Male⊂Female soft-match still live | Wrong gender skip/accept after GH-only fix. |
| 10 | **ATS3-004** | Fiber click-then-reject | Wrong state/dial sticks; thrash reopen. |
| 11 | **ATS2-011 + ATS3-003** | Multipage stuck / false stuck flag | Contact never advances; intentional FAIL marked stuck. |
| 12 | **PR3-001** | PartyRock sign-in wall + docs lie | Agent/browser-tool path ≠ OpenClaw cookies. |
| 13 | **UI-005 / DASH2-005** | Green Resume / “on file” when PDF missing | UI lies; Start may PartyRock anyway. |
| 14 | **UI-009 / 011 / 013** | Fill modes + triple skip + “Start” copy | Operator cannot tell which control wins. |
| 15 | **CHR3-005 / 006** | Triple CfT one Dock icon | Focus jumps to dashboard UI; looks like “dual Chrome”. |

---

## Full inventory by severity

### Critical

| ID | Area | Title |
|----|------|-------|
| **CHR3-003** | Chrome | PartyRock OpenClaw CfT counted as fill Chrome (orphan-kill / false cap) |

### High

| ID | Area | Title |
|----|------|-------|
| **UI-001** | Dashboard | Cancelled/skipped unreachable in Ops |
| **UI-002** | Dashboard | Fill enabled on Ready while hold live |
| **UI-003** | Dashboard | Classic Fast fill allowed on Ready |
| **UI-004** | Dashboard | Ops has no Skip — only Delete |
| **FILL3-001** | Fill | Flash+hold+refill defers Skyvern then may FAIL as Flash failed |
| **FILL3-002** | Fill | Pause overlay covers CAPTCHA |
| **FILL3-003** | Fill | Alert noise → Ready false negative |
| **ATS3-001** | ATS | Ashby Location false-commit from display match |
| **ATS3-002** | ATS | Zip present conflated with not-yet-mounted |
| **ATS3-003** | ATS | Generic ADVANCE false `stuck_on_same_page` |
| **ATS3-004** | ATS | Fiber click-then-reject leaves wrong option |
| **ATS3-005** | ATS | soft_value_match Male⊂Female |
| **CHR3-001** | Chrome | Refresh `finally` undoes preserve_fill_cft |
| **CHR3-002** | Chrome | Refresh always kills fill/tailor procs |
| **PR3-001** | PartyRock | Browser-tool login ≠ OpenClaw session |

### Medium (carry-forward + new, condensed)

**Prior DASH2:** 005, 007, 008, 009, 010, 011, 012  
**Prior FILL2:** S01–S03 (elevated in FILL3 High where noted)  
**Prior ATS2:** 010 (intentional), 011, 017  
**Prior CHR/PR/HYB:** CHR2-004…008, PR2-002/003, HYB2-002  

**New UI Med:** UI-005…020 (many alias prior DASH2)  
**New FILL3 Med:** 004–013, 015–016, 018, 020  
**New ATS3 Med:** 006–014  
**New CHR/PR/HYB Med:** CHR3-004…009, PR3-002/003, HYB3-001  

### Low (deferred)

**Prior:** FILL2-008, ATS2-015/016 (DASH2-014…018 + CHR2-009/010 + PR2-004 fixed in plague passes)  
**New UI Low:** **UI-033** FIXED (Classic frozen → Ops redirect); UI-021–032, 034–040 fixed this / prior pass  
**New FILL3 Low:** 014, 017, 019  
**New ATS3 Low:** 015, 016  
**New CHR Low:** CHR3-L01…L03 (Chrome deferred list cleared in BUG_FIX_STATUS_PLAGUE)  

---

## Recommended cleanup (remove vs fix)

### Fix first (ship-blocking operator pain)

1. **CHR3-003 + HYB3-001** — Exclude OpenClaw PartyRock profile/`user-data` from fill CfT counts and orphan kills.
2. **CHR3-001 + CHR3-002** — Refresh must preserve fill proc + CfT when hold/CAPTCHA active; `finally` must honor `preserve_fill_cft`. Fix docs (CHR3-007).
3. **UI-001 + UI-004** — Add Skipped mission chip (or auto-surface cancelled) + Restore; wire Skip or stop producing skipped.
4. **UI-002 / UI-003 / UI-008** — Treat Ready/CAPTCHA hold as busy for Fill (same job + other jobs).
5. **FILL3-002** — Hide/disable pause overlay while CAPTCHA interactive; unify continue channels (FILL2-S02).
6. **FILL3-001 / 008 / 013** — Don’t FAIL Flash when Skyvern deferred / `invoked=false` by hold+refill design; honest naming.
7. **FILL3-003** — Wire `looks_like_gap_message` (or drop alert scrape).
8. **ATS3-001 / 002 / ATS2-017** — Location commit requires option click; zip presence ≠ N/A until after commit.
9. **ATS3-005 / 004** — Shared soft-match gender polarity; reject before click (not after).
10. **ATS3-003 / ATS2-011** — Don’t sticky-stuck on intentional FAIL-before-ADVANCE; fix multipage contact gate.
11. **PR3-001** — Agent fallback + PLAYBOOK → `./open_partyrock.sh` / OpenClaw only.

### Remove / simplify (cleanup sprint)

| Remove | Keep / replace with |
|--------|---------------------|
| Retry fill radio | Single “Fill with resume” |
| Ops `hybridFillDummy`, `primaryAction` | `/start` Fill path only |
| Pipeline Cancelled/Skipped filters (until queue exists) | Skipped mission chip |
| One of two Empty Deleted buttons | Single destructive |
| `has_description`/`lazy` UI crumb | Nothing |
| `ops-preview.html` | `/` only |
| Classic dual Fast fill (long-term) | **Done:** freeze `/classic` → Ops; one Fill control on Ops |
| Dead `looks_like_gap_message` *or* unused Skyvern dashboard path | One Flash story |
| Legacy partyrock_chrome_profile comments | OpenClaw CfT docs only |
| Manager raw `chrome_count` | Profile-aware count or drop |

### Fix when convenient (Medium batch)

- DASH2-005/008/009/012 (resume honesty + mid-fill guard + persist Skip-no-PDF *or* drop it)
- DASH2-007/010/011 (hybrid session, reconcile, test_mode default)
- FILL3 flag matrix honesty + refill CAPTCHA + demote thrash gates
- ATS3 confusable table / county combobox / age-gate polarity / GH pack
- CHR2-004…008 docs + focus + Chrome.app fallback
- Low encode/XSS/markSubmitted hygiene

### Do not “fix” (intentional)

- ATS2-010 vision fail-closed
- Hold ≠ Ready
- Never-submit / never CAPTCHA solve / never invent EEO / dummy-only automation PII

---

## Dual-UI / copy truth table (quick)

| Surface | Primary fill | Skip | Cancelled visible? | PartyRock skip controls |
|---------|--------------|------|--------------------|-------------------------|
| **Ops `/` (only live UI)** | Fill popover → `/start` | Yes (Skip + Skipped chip + Restore) | Yes under Skipped | Header toggle + Skip-no-PDF + Fill-with-resume |
| **Classic `/classic`** | **Frozen** — HTTP 302 → Ops `/` (UI-033); not maintained | — | — | — |
| **Server** | `/start` (disk resume truth) + `/hybrid_fill_dummy` | skip API exists | statuses real | `skip_partyrock` / `force_partyrock` / disk resume |

---

## Sibling merge provenance

| Hunt | Agent id | Status |
|------|----------|--------|
| UI | `953d8af5-45a5-4b23-9c62-ae793f304f5f` | Merged |
| FILL3 | `c8a3202a-bfe7-46bb-a505-3c6d263510b5` | Merged |
| ATS3 | `52ec9a32-ae03-41b0-bc86-47201ced10eb` | Merged |
| CHR3 | `4263e0cf-faef-459d-a6cb-b86400415849` | Merged |
| Carry-forward | `ats_notes/BUG_REPORT_REMAINING.md` | Copied + re-verified via siblings |

Full sibling writeups remain in agent transcripts; this file is the consolidated plague ledger for prioritization.

---

## Bottom line

Prior hunt closed Critical/High **classes of autofill/select bugs**. The remaining plague is mostly **operator-facing**: Chrome lifecycle lies (Refresh/PartyRock), Ops list black holes after Cancel, Ready/Flash honesty, and Ashby/select soft-match residuals. Cleanup should **remove redundant Fill/PartyRock controls and freeze classic**, then **fix the top 15** — not open another 90-ID hunt without shipping those fixes.
