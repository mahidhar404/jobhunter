# ORCHESTRATION — single source of truth

**Updated:** 2026-07-31T09:05Z  
**Yogesh:** **PAUSE** TEN UNSEEN variety fills — dropdown select+verify mess  

## Authority

**KING / dedicated dropdown agent** owns select+verify + LLM→select-helper routing before more fills.

- Cap **1** Chrome-for-Testing headed main. Hold **≤120s**.
- Dummy-only · never-submit · captcha-wait · EEO via DeepSeek+dummy · no thrash.
- **PAUSED:** do not launch jobs 2–10. Handoff in `skyvern_runtime/real_job_results/TEN_UNSEEN_RUN.md`.
  Last URL: Extend GH `…/extend/jobs/6122551004` (r1 FAIL, r2 incomplete).
- SEEN exclude: `SEEN_EXCLUDE.json` — still ban Socure/TaxRelief/Rippling/Utility/BBH/Stripe/etc.
- Do **not** set `FASTFILL_FORCE_HEADED=1` unless documented here.

---

## Success bar (King)

| Metric | Target |
|--------|--------|
| Streak | **5 consecutive** |
| Verdict | Vision/screenshot **COMPLETE** (zero blanks) — not harness leftovers=0 alone |
| Browser | 1 headed at a time · hold ≤120s · captcha-wait |
| On FAIL | Fix → retest **SAME** url (≤3 rounds) then log debt & continue streak clock (FAIL breaks consec) |

Prior platform SUCCESS (Ashby Socure) may seed King campaign only if King re-verifies screenshot COMPLETE under current rules.

---

## Browser state

| Item | Status |
|------|--------|
| Cap | **1** headed Chrome-for-Testing main |
| Hold | ≤120s (`HOLD_OPEN_SECONDS=90`; long hold only `FASTFILL_ALLOW_LONG_HOLD=1`) |
| Code gate | `refuse_headed_if_chrome_busy()` → `blocker=headed_cap` |
| Live at handoff | GH Tax Relief sponsor2 PID **25967** + Chrome **25995** → `retest_gh_taxrelief_sponsor2/` (let finish; King decides next) |
| Killed (Master) | Headless **23388**; off-queue BBH **25350/25342/25370** |
| Docs | `sota_brainstorm/BROWSER_CAP.md` · `BROWSER_QUEUE.md` · `BROWSER_KILL_LOG.md` · `CHROME_CRASH_DIAGNOSIS.md` |

---

## Queue state (handed to King — preserve order)

| # | Status | Platform | Job | Notes |
|---|--------|----------|-----|-------|
| 1 | in-flight / King | greenhouse | Tax Relief `4759563008` | sponsor2; WORK_AUTH/sponsorship Select… pixel debt |
| 2 | queued debt | rippling | REV Robotics W04 | filled=19 leftovers=3; MARKETING_CONSENT + Search + textbox; `variety_W04_rippling/` |
| 3 | queued retest | lever | **Utility Global** `ba22d077-…` | W01 `lever_widgets` shipped; launch only Chrome mains=0; hold≤120 |
| 4 | queued variety | greenhouse | Stripe | Lead B locked |
| 5 | queued variety | lever | Parallel Domain | after Utility Global |
| 6 | queued variety | workable | Lead B URL | serial rerun |
| 7 | queued variety | workday | Cisco WD | multipage |
| — | parked | ashby | Socure | prior streak seed — King re-verify |
| — | parked | workday | BBH | do not freestyle relaunch |

**Lever Utility Global after GH + Rippling.** No launch until ≤0 other Chrome mains.

---

## Master status

**IDLE — deferred to King.**  
No new headed launches from Master unless King resumes Master as sole executor under this file.

---

## Absorbed (do not re-break)

W01 Lever widgets · W03 honest COMPLETE · W04 attempt-log · W05 captcha sentinel · W06–W08 · GH school/salary · Ashby LinkedIn · arm64 Chromium · hold 90 (was 3600)
