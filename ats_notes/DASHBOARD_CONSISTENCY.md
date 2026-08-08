# Dashboard consistency (Test Mode / PartyRock / Fill)

Date: 2026-08-06  
Scope: OPS dossier controls vs server Start / Fast fill. Dummy / never-submit unchanged.

## Truth table (locked)

| Test Mode | PartyRock | Resume on disk | Default Fill | Attach PDF | Identity |
|-----------|-----------|----------------|--------------|------------|----------|
| ON | OFF | any | Fill only | Dummy only | Dummy |
| ON | ON | no | Tailor + fill | Dummy only | Dummy |
| ON | ON | yes | Fill only (unless Tailor forced) | Dummy only | Dummy |
| OFF | n/a | yes | Fill only | Job/trusted | Real |
| OFF | n/a | no | Tailor + fill | Tailored | Real |

## Controls → expected → fixed

| Control | Expected | Fixed |
|---------|----------|-------|
| Test Mode flask | Dummy identity for Start/fill | Unchanged; clears treat-as-on-file when turning OFF; Fill/Tailor+Fill shows one confirm (“dummy…not real applicant PII”) before `/start` |
| PartyRock toggle (Test Mode) | OFF → fill-only default; ON → may tailor | `defaultFillMode` + hints honor toggle; invalidate Fill mode on toggle |
| Fill face default | Match truth table | No longer forces Tailor when PartyRock off |
| Fill “with-resume” | Skip PartyRock | Disabled in Real Mode with no PDF (UI-006); never claims PartyRock if none |
| Tailor + fill | Force PartyRock | Copy clarifies it overrides PartyRock-off for one run (UI-010) |
| Skip PartyRock (no PDF) | Test Mode + PartyRock ON only | Hidden when PartyRock OFF (UI-011); persisted in localStorage (UI-020) |
| Resume on disk | Filesystem truth for skip | Server `resolve_job_resume_file`; list payload `resume_on_disk` (UI-005) |
| Second Fill while busy | Block | Server 409 if other job in progress **or** Ready/CAPTCHA hold live; UI disables Fill; same-job Ready/CAPTCHA never Fill (UI-002/008) |
| Test Mode PDF attach | Dummy fixture only | Dashboard omits `--resume-path`; fast_fill ignores job-scoped override |
| Fill thread crash | Job → stuck | `run_tailor_then_fill` / `run_hybrid_fill_dummy` wrap → stuck + detail |
| Skipped / Cancelled | Soft-delete / reset | **Skip** → Deleted (reason in `deleted_reason`); **Cancel** → Open (`discovered`, resume kept). No Skipped mission chip. Restore from Deleted. |
| Raw API `test_mode` | Explicit required | Missing flag → HTTP 400 (UI-019 fail-closed); dashboard always sends it |

## Single-job rule

One user Start/Fill → that `job_id` only. No auto-cascade to the next queue item. Concurrent Start → HTTP 409. Ready/CAPTCHA with live hold blocks Start/Fast fill on that job and on others.

## Classic UI (frozen)

**UI-033:** Classic is frozen. Ops (`/`) is the only maintained dashboard UI. `/classic`, `/classic.html`, and `/classic.js` HTTP-redirect to `/`. Source files remain on disk for reference but are not a live peer product.

## Triage (Yogesh model — 2026-08-06)

No Skipped mission chip. Unfit jobs go to **Deleted** (review + Restore there). Cancel returns the job to **Open** with resume kept.

**Duplicate merge freshness:** When two listings of the same role merge, the winner inherits the fresher `date_posted` (and among equal-quality ATS links, the fresher posting's `apply_url`). Otherwise a May re-post survivor can stay hidden from Open by the 30-day stale filter while the Aug copy sits under Deleted (SanDisk BI Analyst).
