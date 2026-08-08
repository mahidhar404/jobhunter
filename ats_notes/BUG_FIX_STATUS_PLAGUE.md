# Project Plague — Final Fix Status (2026-08-06)

**Canonical ledger** for `ats_notes/BUG_REPORT_PROJECT_PLAGUE.md` fix waves.  
**Updated:** 2026-08-07 (Pause never auto-closes fill CfT + hover activity tip)  
**Commit:** none (ledger only)

Cross-checked against `BUG_FIX_STATUS.md` (FILL3/ATS verify), `ats_notes/CHROME_PARTYROCK_HUNT.md`, and light code spot-checks. Where an older “still open” row conflicted with landed code, this file wins.

**Safety unchanged:** never-submit · never CAPTCHA solve · never invent EEO · dummy-only automation PII · Hold ≠ Ready.

---

## 2026-08-07 — Pause auto-close + hover status

| Item | Status |
|------|--------|
| **Pause / hold-open never auto-closes CfT** | **FIXED** — Root cause: dashboard `_run_fill_subprocess_streaming` killed fill after `DUMMY_FILL_PLAYWRIGHT_TIMEOUT_S` (420s) while Pause was engaged (deadline only suspended on hold/Ready). Secondary: `read_fill_pause_state` fail-open on CDP error could look like Continue → fill finished → `browser.close()`. Fixes: `_job_is_fill_paused` / `_job_fill_browser_must_stay_open` suspend kill; fail-closed pause reads; `drain_pause_before_close` before terminal hold/close; `should_keep_fill_browser_open` decision gate. |
| **Pause hover activity tip** | **FIXED** — `window.__jhFillActivity` + overlay tip on mouseenter; updated from Layer 0/1/2 fill loops. |

---

## Summary counts

| Bucket | Count | Notes |
|--------|------:|-------|
| **FIXED** | ~102 unique plague IDs | Includes **CHR3-005** mitigated FIXED; **UI-033** Classic frozen → Ops |
| **OPEN** | **1** | **ATS2-017** (tenant-dependent zip mount residual) |
| **INTENTIONAL** | 3 ID groups + safety red lines | ATS2-010 / FILL3-010; FILL2-008 / FILL3-014; Hold≠Ready + red lines |
| **DEFERRED product** | **0** | — |

Critical remaining: **0**. High remaining: **0**.

---

## Fixed by area

### Chrome / PartyRock / Hybrid

| IDs | What landed |
|-----|-------------|
| **CHR3-001**, **CHR3-002** | Refresh `finally` honors `preserve_fill_cft`; hold Refresh detaches fill/agent (no SIGTERM) |
| **CHR3-003**, **HYB3-001** | OpenClaw PartyRock CfT excluded from fill count/kill/refuse |
| **CHR3-004** | PartyRock-alone ≠ fill hold |
| **CHR3-005** | **Mitigated FIXED:** `--focus-fill` / `--cft-roles`; TOOLS + Dock docs + rebuild notes; fill launch points at PID focus. One Dock icon remains (bundle ID structural) — operator confusion materially reduced |
| **CHR3-006** / **CHR2-004** | Focus prefers fill PID (`--remote-debugging-pipe`); no name-based System Events fallback |
| **CHR3-007**, **CHR3-009** | Docs + lifecycle/fill-parity tests for exclude + preserve |
| **CHR3-008** / **CHR2-006**, **HYB2-002** | Manager / BROWSER_CAP fill-only CfT filters (exclude UI + PartyRock) |
| **CHR2-005** | Captcha marker TTL; hold check includes hybrid / `real_job_test` |
| **CHR2-007** | Dashboard UI **refuses** Google Chrome.app fallback (fail loud) |
| **CHR2-008** | Legacy `partyrock_chrome_profile` mythology scrubbed from launchers |
| **CHR2-009**, **CHR2-010** | Script-relative ROOT + rebuild inject; `--focus-ui` errors surfaced |
| **PR3-001**, **PR3-002**, **PR3-003** | Canonical PartyRock = `./open_partyrock.sh` / OpenClaw only; sign-in wall points there |
| **PR2-002**, **PR2-003** | Tailor ensure PartyRock CDP `required=True`; clear stale “Opening PartyRock…” detail |
| **PR2-004** | `/json/new` PUT then GET; `wait_tab_gone` default 5s |

Also prior Chrome hunt IDs (CHR-001/002/005–008/010, CHR2-001–003, PR-003–005, PR2-001, HYB-001, HYB2-001) remain fixed — see `ats_notes/CHROME_PARTYROCK_HUNT.md`.

### Dashboard / UI

| IDs | What landed |
|-----|-------------|
| **UI-001**, **UI-014**, **UI-015** | ~~Skipped mission chip~~ **superseded 2026-08-06:** Skip→Deleted; Cancel→Open (resume kept); no Skipped chip; Restore from Deleted |
| **UI-002**, **UI-003**, **UI-008** | Fill/Fast fill blocked on Ready/CAPTCHA + multi-job hold; server 409 |
| **UI-004**, **UI-023** | Ops Skip wired |
| **UI-005** / **DASH2-005**, **UI-006** / **DASH2-008**, **UI-007** / **DASH2-009** | Resume disk truth; real Fill needs PDF; mid-fill upload blocked |
| **UI-009**–**UI-013**, **UI-016** | Retry removed; Tailor/Skip-no-PDF copy; Ops Fill wording; classic Restore |
| **UI-017** / **DASH2-007**, **UI-018** / **DASH2-010**, **UI-019** / **DASH2-011**, **UI-020** / **DASH2-012** | Hybrid local session; reconcile scope; `test_mode` fail-closed; Treat-as localStorage |
| **UI-021**, **UI-022**, **UI-024**–**UI-032**, **UI-034**–**UI-040** | Dead JS cleanup; encode/XSS; Discover abort; pin menus; Ready exit; confirms; Posted 30d; etc. |
| **UI-033** | **FIXED (frozen Classic → Ops redirect):** `/classic`, `/classic.html`, `/classic.js` → 302 `/`; docs stop advertising Classic as a peer UI; source kept on disk |
| **UI-027** / **DASH2-014**, **DASH2-018** | Hold ingest abort uses full abort set; pipeline milestone no-ops when aborted |
| **DASH2-015**–**017** | Earlier hygiene (markSubmitted / encode / escapeAttr) |

### Fill pipeline

| IDs | What landed |
|-----|-------------|
| **FILL3-001**, **FILL3-008**, **FILL3-013** | Flash honesty: deferred Skyvern / `invoked=false` ≠ Flash fail; naming `inpage` vs LLM |
| **FILL3-002** / **FILL2-S03**, **FILL3-015** / **FILL2-S02** | Pause overlay CAPTCHA gate; Continue vs CAPTCHA continue |
| **FILL3-003** / **FILL2-S01** | Alert noise filtered (`looks_like_gap_message`) |
| **FILL3-004**, **FILL3-006**, **FILL3-007**, **FILL3-009** | Flag matrix docs; refill fingerprint gate; mid-refill CAPTCHA; pause UX |
| **FILL3-005** | Early false zip-absent closed (ATS3-002 + ATS3-012). Live mount miss = **ATS2-017** OPEN |
| **FILL3-011**, **FILL3-012**, **FILL3-017**, **FILL3-018**, **FILL3-019**, **FILL3-020** | Autofill FileList gate; Use My Last soft-keep; overlay throttle; `test_mode` required; Escape-unless-CAPTCHA; demote re-Flash skip |
| **FILL3-016** | False sticky-stuck closed (ATS3-003). Contact never-ADVANCE = **ATS2-011** mitigated FIXED |

Detail: `BUG_FIX_STATUS.md`.

### ATS / selects / multipage

| IDs | What landed |
|-----|-------------|
| **ATS3-001**, **ATS3-002**, **ATS3-012** | Location needs click/reveal; HTML zip ≠ N/A; Tab (not Escape) + zip wait |
| **ATS3-003**, **ATS2-011** | Stuck requires `advance_clicked`; SPA poll / sticky-stuck recovery on contact ADVANCE |
| **ATS3-004**–**ATS3-008**, **ATS3-016** | Fiber validate-then-click; gender polarity; confusable states; clear_closest_match |
| **ATS3-006**, **ATS3-009**–**ATS3-011**, **ATS3-013**–**ATS3-015** | Fiber token bound; WD county combobox; age-gate Yes-only; SPA settle; typable full-string-first; GH pack; poll vs fixed sleeps |
| **ATS2-015**, **ATS2-016** | Phone-device RecursionError retry/degrade; Apply/auth sleeps → `_poll_spa_settle` |

Detail: `BUG_FIX_STATUS.md` ATS verify section.

---

## Still OPEN

| ID | Sev | Why still open |
|----|-----|----------------|
| **ATS2-017** | Med | **Residual live debt:** after Location commit, some Ashby tenants show a zip *question* in HTML but the fillable input never mounts. Mitigations this pass: longer event-driven wait (`_wait_ashby_zip_dom_event`), capped scrolls (no forever wheel), one Location re-open, honest taxonomy — `zip_dependent_never_revealed` (HTML has zip, input absent) vs `zip_field_absent_on_form` (true N/A) vs `zip_field_not_found_after_location` (no HTML question). Needs live tenant repro to mark FIXED. |

No Critical or High IDs remain open.

---

## INTENTIONAL

| ID / rule | Why not “fixed” |
|-----------|-----------------|
| **ATS2-010** / **FILL3-010** | Vision judge exception → fail-closed Ready (`vision_incomplete` / AMBIGUOUS). Prefer false Ready denial over false Ready claim. Alert noise that amplified this was **FILL3-003 FIXED**. |
| **FILL2-008** / **FILL3-014** | Bare label `"Extension"` → `PHONE_EXTENSION` **kept**. Changing would break real ATS phone-ext fields; tests assert whole-label match. |
| **Hold ≠ Ready** | `--hold-open` alone must not promote Ready. |
| **Safety red lines** | Never submit · never solve CAPTCHA · never invent EEO · dummy-only automation PII. |

---

## DEFERRED product

None. **UI-033** closed: Classic frozen → Ops-only redirect (see Dashboard / UI table).

---

## Recommended next steps

1. **Rebuild Desktop app** after AppleScript / launcher edits: `./dashboard/rebuild_desktop_app.sh` (injects repo ROOT — CHR2-009; prints CHR3-005 focus helpers).
2. **Hard-refresh Ops UI** (`/` — bypass cache) so UI-001+ mission/Skip/Fill changes load.
3. **Optional live hunt for ATS2-017** — Ashby tenant where Location commits but zip input never mounts; capture HTML + timing; look for leftover reason `zip_dependent_never_revealed` (not false N/A).

### Focused regression commands

```bash
python3 dashboard/test_ui_lifecycle.py
python3 dashboard/test_fill_parity.py
python3 scripts/fastfill/test_verified_select.py
python3 scripts/fastfill/test_ats3_open_fixes.py
python3 scripts/test_partyrock_tabs.py
```

Operator CfT helpers:

```bash
./dashboard/launch_dashboard.sh --cft-roles
./dashboard/launch_dashboard.sh --focus-fill   # raise fill CfT by PID
./dashboard/launch_dashboard.sh --focus-ui     # raise dashboard UI only
```

---

## Key code paths

- `dashboard/server.py` — preserve fill CfT, PartyRock exclude, hold TTL, PartyRock ensure `required=True`
- `dashboard/launch_dashboard.sh` / `JobHunterDashboard.applescript` — CfT UI, refuse Chrome.app, ROOT inject, `--focus-ui` / `--focus-fill` / `--cft-roles`
- `scripts/fastfill/fast_fill.py`, `captcha_pause.py`, `page_progress.py` — Flash honesty, pause gate, vision fail-closed
- `scripts/fastfill/ashby_widgets.py`, `verified_select.py`, `exp_workday_selectors.py` — zip/Location, selects, SPA
- `scripts/partyrock_tabs.py`, `open_partyrock.sh`, `skyvern_runtime/scripts/hybrid_fill.py`

## Provenance

| Wave | Source |
|------|--------|
| Dashboard UI | `BUG_REPORT_PROJECT_PLAGUE.md` “Fixed this pass” |
| FILL3 + ATS verify | `BUG_FIX_STATUS.md` |
| Chrome / PartyRock | `ats_notes/CHROME_PARTYROCK_HUNT.md` + this file’s earlier pass |
| Findings (unchanged history) | `ats_notes/BUG_REPORT_PROJECT_PLAGUE.md` |

---

## Triage redesign (2026-08-06) — Yogesh model

Skipped holding pen removed:

| Action | Behavior |
|--------|----------|
| Skip (manual / contract / easy_apply / …) | Soft-delete → **Deleted** (`deleted_reason` + `status_detail`) |
| Skip duplicate / `dedup_jobs.py` | Merge URLs onto winner (ATS apply preferred; among equal ATS, fresher posting URL); promote fresher `date_posted` onto winner so Open stale filter does not hide re-posts; loser → **deleted** / `duplicate` (`duplicate_of` + `merged_from`) |
| Cancel run | Reset → **Open** (`discovered`); keep `resume_path`; clear proc/hold/question |
| Backlog migrate (startup) | `skipped_*` → deleted (reason preserved); `cancelled` → open for retry |

**SanDisk-class fix (2026-08-06):** Dedup used to keep the older survivor's `date_posted` (>30d) while soft-deleting the newer SmartRecruiters re-post. Open then hid the winner via `isHiddenUntouchedListing` / `isStaleListing`, so the user only found the Deleted copy. Merge now takes the fresher posted date (and fresher equal-rank ATS `apply_url`); `dedup_jobs.py` also backfills freshness from already-deleted dupes.

Ops: no `data-queue="skipped"` chip; pipeline Cancelled/Skipped filters removed. Restore from Deleted.
