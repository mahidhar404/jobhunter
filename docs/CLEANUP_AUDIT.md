# Cleanup Audit — job-hunter

**Date:** 2026-08-08 · **Type:** READ-ONLY analysis (nothing deleted/moved/edited except this file).
**Scope:** identify everything NOT needed by the current, in-use system.
**Method:** Glob/Grep/Read across the repo + two parallel exploration passes (dead-Python and UI). Every "unused" claim carries grep-level evidence. Ambiguous items are marked **NEEDS CONFIRMATION** rather than "safe to remove".

---

## Executive summary

The repo is fundamentally healthy: the **live system is small and well-wired** — `dashboard/server.py` (stdlib HTTP server at `:8787`) serves exactly one UI (`index.html` + `app.js` + `job_sort.js`, the "Ops" UI) and shells out to a well-defined set of `scripts/` + `scripts/fastfill/` + `skyvern_runtime/scripts/hybrid_fill.py`. The live Ops UI has **zero dead fetch calls and zero unwired controls**. `requirements.txt` has **no unused declared dependencies** (all 8 are imported; optional deps are correctly commented out). The cruft is concentrated in **(a)** a frozen second UI ("Classic") plus a design mock that are no longer served, **(b)** a cluster of orphaned one-off/experiment/old-engine scripts, **(c)** documented-but-dead OpenClaw code left in place after the 2026-08-08 decoupling, **(d)** stale local backup/lock files (git-ignored, PII-bearing) that should be trashed, and **(e)** doc overlap (three OpenClaw docs, an already-implemented restructure proposal). None of the high-confidence items are on the live path, so cleanup is low risk if sequenced as recommended.

Source of truth for "what's wired up": `README.md`, `docs/OPENCLAW_REMOVED.md` (authoritative OpenClaw change log), `docs/PORTABILITY.md`, and `dashboard/server.py` route dispatch.

---

## A. Safe to remove (High confidence dead)

| Path | What | Evidence | Conf | Action |
|---|---|---|---|---|
| `scripts/safety_filters.py` | Python module | Literal `safety_filters` returns **zero matches repo-wide** — no import, subprocess, doc, shell, or Docker ref. Not a test, not an entrypoint. | High | Delete |
| `dashboard/static/dossier_mock.html` (27 KB) | Design mock page | No route in `server.py` (no `_send_file`, no redirect), no `<a>`/`<script>` link from any served page. Only referenced in `ats_notes/*` as "Not product". | High | Delete |
| `_openclaw_env(...)` in `dashboard/server.py:2047` | Function (PATH shim) | Defined but **never called** (grep for `_openclaw_env(` finds only the definition). `docs/OPENCLAW_REMOVED.md` explicitly says it's "kept as dead code". | High | Delete (or document) |
| `OPENCLAW_BIN` constant `dashboard/server.py:85` | Env-resolved binary path | Only occurrence is the definition; not referenced elsewhere. Doc confirms "now unused". | High | Delete |
| `scripts/fast_discovery.py` | Standalone JSON-API discovery experiment | `docs/PORTABILITY.md:121` + `requirements.txt:34` both state "**not** wired into the dashboard". No `import` anywhere. | High | Delete |
| `scripts/relevance.py` | Relevance scorer | Imported **only** by orphaned `scripts/bench_builtin_terms.py:26`. Live discovery uses inline keyword lists. Dead-by-transitivity. | High | Delete (with `bench_builtin_terms.py`) |
| `scripts/bench_builtin_terms.py` | Manual benchmark CLI | Only external ref is a *comment* in `scrape_builtin.py:75`. Never imported/invoked. | High | Delete |

---

## B. Likely redundant / consolidate

### B1. Dual UI — "Classic" is frozen, "Ops" is live
`server.py:5830` returns **HTTP 302 → `/`** for `classic` / `classic.html` / `classic.js`; `server.py:5836` does the same for `ops-preview*`. So these are not served as live UI:

| Path | Size | Status | Evidence |
|---|---|---|---|
| `dashboard/static/classic.js` | 129 KB (3075 ln) | Frozen | Redirect-only; still referenced by parity guard tests + comments, not by any served page. |
| `dashboard/static/classic.html` | 47 KB (1056 ln) | Frozen | Redirect-only; loads `classic.js` but HTML itself never served. |
| `dashboard/static/ops-preview.html` | 371 B | Redirect stub | Body is a `<meta refresh url=/>`; kept for old bookmarks. |

**Action:** Confirm Classic is retired, then delete `classic.html` + `classic.js` + `ops-preview.html` (and the parity tests that pin them, see H). **Conf: Medium** (kept intentionally as a "frozen" fallback per `UI-033` comment — hence confirm first).

### B2. Triplicated discovery-filter logic
`SENIORITY_EXCLUDE_RE`, `NON_US_LOCATION_RE`, `US_LOCATION_STRONG_RE`, `US_STATE_ABBREV_RE`, `CLEARANCE_*_RE`, `INTEL_AGENCY_*_RE`, `US_STATE_ABBREVS`, `NON_US_ISO2_CODES` are defined **verbatim in `app.js`**, **again in `classic.js`**, and **mirrored in `scripts/discovery_filters.py`**. Byte-parity is enforced by `scripts/test_company_siblings_filter.py:107` (app.js↔classic.js) and `:117` (JS↔Python). Triple maintenance burden.
**Action:** After Classic removal, the app.js↔classic.js copy disappears; consider generating the JS filter constants from the Python source (or a shared JSON) to kill the last duplication. **Conf: Medium** · Merge.
*(Good counter-example: posted-date sort was already extracted into the shared `job_sort.js` — the pattern to follow.)*

### B3. Old Skyvern eval-engine scripts (parallel to the live fastfill engine)
`skyvern_runtime/scripts/{greenhouse_workflow_test,batch_hybrid,scorecard,cost_helper}.py` are a standalone/older eval engine. `scorecard.py` is duplicated by the **live** `scripts/fastfill/scorecard_fast.py` (`ARCHITECTURE_REVIEW.md`). `cost_helper.py` is imported only by orphan `greenhouse_workflow_test.py`; `batch_hybrid.py`/`scorecard.py`/`greenhouse_workflow_test.py` have no external callers. **Live fill still uses** `skyvern_runtime/scripts/hybrid_fill.py` + `real_job_test.py`, so keep those two.
**Action:** Delete the 4 old-engine scripts once the Skyvern LLM-agent eval engine is confirmed retired. **Conf: Medium** · Delete-after-confirm.

### B4. Orphaned experiment / one-off scripts (no importer, no dispatch)
| Path | Evidence | Action |
|---|---|---|
| `scripts/backfill_builtin_posted_dates.py` | Self-ref only; one-shot maintenance CLI, not wired (contrast `backfill_missing_jds.py`, invoked at `server.py:7259`). | Delete/archive |
| `scripts/fastfill/parity_report.py` | Standalone smoke CLI; only ref is `SKILL.md:221` + own docstring. Nothing imports/invokes it. | Delete/archive |
| `scripts/fastfill/cli_entry_prepass.py` | Standalone CLI; never imported; only a comment in `fast_fill.py:33` + a string tag in `scorecard_fast.py:470`. | Delete/archive |
| `scripts/fastfill/offline/build_corpus.py` (+ `offline/corpus.json`, 305 KB) | Offline dev tool needing Postgres; `ARCHITECTURE_REVIEW.md:121` confirms "not imported/called by any other script". | Delete/archive |

All **Conf: Medium** (they're runnable dev tools, just not part of the live pipeline).

### B5. `scripts/manager_bridge/*` — dormant subsystem
`_common.py` + `post_task.py` + `post_result.py` + `ack_task.py` + `list_inbox.py` + `heartbeat.py` are **entirely self-referential**; a `manager_bridge`-scoped grep (excluding docs) matches only these files + `skyvern_runtime/manager_bridge/schema/task.schema.json`. No subprocess/import/shell/Docker path invokes them. They exist for the (currently dormant) manager/executor protocol under `skyvern_runtime/manager_bridge/*.md`.
**Action:** Remove only if the manager/executor bridge is confirmed abandoned. **Conf: Medium** · Confirm-with-user.

---

## C. Cruft / artifacts (backups, locks, logs, generated files)

**Local (git-ignored, NOT tracked) — safe to trash; some contain PII:**

| Path | Note | Action |
|---|---|---|
| `jobs.json.bak-before-cleanup` (3 MB) | Stale PII backup | `trash` |
| `jobs.json.bak-before-dedup-20260802-062449` (7.5 MB) | Stale PII backup | `trash` |
| `profile.json.bak-before-gapfill` | Stale PII backup | `trash` |
| `jobs.json.lock`, `blocked_urls.json.lock`, `application_tracker.xlsx.lock` (all 0 B) | Runtime flock files | `trash` (regenerated) |

These are correctly `.gitignore`d (none appear in `git ls-files`), so this is **local disk hygiene**, not a repo change. **Conf: High.**

**Tracked generated/runtime state that arguably should be `.gitignore`d (churn / not source):**

| Path | Size | Note |
|---|---|---|
| `scripts/fastfill/learning_store/experience.jsonl` | 317 KB | Accumulating run log |
| `scripts/fastfill/learning_store/selector_stats.json` | 45 KB | Accumulating stats |
| `scripts/fastfill/replay_cache.json` | 68 KB | Replay cache |
| `scripts/fastfill/alias_state.json` | 43 KB | Alias allocation state |
| `skyvern_runtime/greenhouse_workflow_state.json` | 140 B | Workflow scratch state |
| `skyvern_runtime/eval_results/eval_*.json` (9 files) | — | Eval run outputs |

These grow at runtime and create noisy diffs. **BUT** some may be intentionally committed as seed/baseline (e.g. `learned_fields.json` is documented-safe generic answers; `eval_results` may be a reference baseline). **Conf: Low** · Git-ignore-or-confirm (see F).

---

## D. Possibly-unused dependencies

**Result: none unused.** All 8 declared deps in `requirements.txt` are imported by live code:
`beautifulsoup4`(bs4), `fpdf2`(fpdf), `lxml`, `openpyxl`, `playwright`, `pypdf`, `python-jobspy`(jobspy), `trafilatura`. Optional deps (`httpx`, `pdfplumber`, `psycopg[binary]`) are **correctly commented out** with guarded/function-local imports.

Notes:
- `lxml` is directly imported only by orphaned `build_corpus.py`, but it's also a `trafilatura` transitive dep → **keep**.
- **Playwright = core** (drives `fast_fill.py`, `tailor_resume.py`, `pw_fetch_html.py`, `extract_job_posting.py`). **Skyvern = optional**, isolated in its own `skyvern_runtime/` venv (not in this `requirements.txt`); only reached via the LLM-assisted fill path. Both classifications match `README.md`/`docs/PORTABILITY.md`. **Conf: High.**

---

## E. OpenClaw leftovers (post-decoupling)

Authoritative context: `docs/OPENCLAW_REMOVED.md` §"Residual openclaw references". Distinguishing **truly dead** vs **live-with-fallback**:

**Truly dead (see also bucket A):**
- `_openclaw_env()` `server.py:2047` — defined, never called. **Delete.**
- `OPENCLAW_BIN` `server.py:85` — defined, never referenced. **Delete.**

**Live-with-fallback / intentionally kept (do NOT remove without care):**
- `_ensure_openclaw_managed_browser` / `_stop_openclaw_managed_browser` (`server.py:1481/1562`) — **still called** (`:533,4637,4745,1627`) but no longer shell `openclaw`; they now launch Chrome-for-Testing directly. Misleadingly named but LIVE. **Keep** (optionally rename).
- `ensure_openclaw_executable_is_cft` / `ensure_openclaw_partyrock_browser` (`scripts/chrome_for_testing.py:137/299`) — legacy; called only by each other + the file's `__main__` CLI + `open_partyrock.sh` (optional one-time login). Self-skip when `openclaw` absent, fall back to `ensure_partyrock_browser_direct`. **Keep-but-document.**
- `_handle_cli` (`server.py:7176`) — CLI passthrough now returns a `{"disabled": true, "exit_code":127}` stub; **no UI caller** (the CLI box was removed from the frontend). Dead endpoint + orphaned stub. **Confirm then remove endpoint**, or keep as documented no-op.
- `scripts/session_timing_report.py` — standalone analytics over the OpenClaw session store; not invoked by the dashboard/pipeline (`docs/OPENCLAW_REMOVED.md:67`). Only a `PLAYBOOK.md` mention. **Keep-but-document** (or archive).

Confidence on the two "truly dead" items: **High**; on the fallbacks: intentionally retained — leave as-is.

---

## F. NEEDS CONFIRMATION (ambiguous)

| Path | Why ambiguous | Suggested |
|---|---|---|
| `scripts/fastfill/batch_fill.py` | `is_batchable_row` imported **only** by `test_honest_metrics.py:1472`. Feature implemented + tested but unwired in production. | Confirm feature intent; if dropped, remove module + test together. |
| `scripts/fastfill/contamination.py` | `contamination_sweep` imported **only** by `test_honest_metrics.py:1484`; `fast_fill.py:10636` sets a `{"skipped": True}` placeholder and never calls it. | Same as above. |
| Backend endpoints with **no live caller**: `GET /api/allowlist` (`server.py:5916`), `GET /api/partyrock` (`server.py:5933`), `POST /api/jobs/<id>/hybrid_fill_dummy` (`server.py:6057`) | `hybrid_fill_dummy` is called **only** by frozen `classic.js:1849`; the others have no UI/CLI caller. | If Classic is removed and these have no external clients, retire the endpoints. |
| Tracked runtime state (bucket C table 2) | May be committed seed/baseline vs pure runtime churn. | Owner decides git-ignore vs keep-as-seed. |
| `scripts/session_timing_report.py`, `manager_bridge/*` | Dormant but reference documented workflows. | Keep if workflows may resume; else archive. |

---

## G. Docs bloat / consolidation

- **Three overlapping OpenClaw docs:** `docs/OPENCLAW_DECOUPLING.md` (25 KB, plan) + `docs/OPENCLAW_REMOVED.md` (7 KB, change log) + scattered mentions. The decoupling is done → fold the plan's still-relevant bits into the change log and archive the rest. **Merge.**
- **`docs/RESTRUCTURE_PROPOSAL.md` (19 KB):** Phase 0 is already implemented (`.cursorignore`, `fixtures/*.example`, `private/README.md`, `export.sh` all exist). It reads as a proposal but is partly historical → mark implemented / trim. **Keep-but-document.**
- **`ARCHITECTURE_REVIEW.md` (54 KB)** and **root `BUG_FIX_STATUS.md`** + `ats_notes/BUG_REPORT_*` / `BUG_FIX_STATUS_PLAGUE.md` (dated 2026-07/08 bug sweeps) are point-in-time and now largely historical. Consider moving under `docs/` or an `ats_notes/archive/`. **Keep-but-document / Consolidate.**
- **`ats_notes/_hunt_extracts/*.md`** (4 files, ~48 KB): scraped research extracts, not product docs. Candidate to archive. **Confirm.**

---

## H. Tests referencing dead/frozen code

| Test | References | Note |
|---|---|---|
| `scripts/fastfill/test_honest_metrics.py` | `batch_fill` (:1472), `contamination` (:1484) | Sole keep-alive for two otherwise-unwired modules (see F). |
| `scripts/test_company_siblings_filter.py` | app.js↔`classic.js` parity (:107), JS↔Python parity (:117) | Pins the frozen `classic.js`; will need updating if Classic is removed (B1/B2). |
| `dashboard/test_fill_parity.py`, `dashboard/test_openclaw_absent.py`, `dashboard/test_ui_lifecycle.py` | Heavy `openclaw` references | Mostly assert the *absence*/degradation behavior — LIVE and valuable; keep. |
| `scripts/fastfill/test_captcha_resume_hold.py` | imports `cycle_orchestrate` (:25) | Keeps a standalone operator CLI test-covered; keep. |

---

## Recommended cleanup plan (ordered, lowest-risk first)

1. **Local disk hygiene (zero repo risk):** `trash` the stale `*.bak-*` backups and `*.lock` files (bucket C). Not tracked; nothing to commit.
2. **Delete the one truly-dead module:** `scripts/safety_filters.py` (A).
3. **Remove documented dead OpenClaw code:** `_openclaw_env`, `OPENCLAW_BIN` in `server.py` (A/E).
4. **Delete orphaned experiments** with no importer: `fast_discovery.py`, `bench_builtin_terms.py` + `relevance.py`, `backfill_builtin_posted_dates.py`, `fastfill/parity_report.py`, `fastfill/cli_entry_prepass.py`, `fastfill/offline/build_corpus.py` (+`corpus.json`) (A/B4).
5. **Retire the dead design mock:** `dashboard/static/dossier_mock.html` (A).
6. **Retire the frozen Classic UI (confirm first):** delete `classic.html`, `classic.js`, `ops-preview.html`; update `test_company_siblings_filter.py`; retire `hybrid_fill_dummy` + `/api/allowlist` + `/api/partyrock` endpoints if no other client; then de-duplicate the filter regexes into a single Python-sourced module (B1/B2/F).
7. **Retire old Skyvern eval-engine scripts** once confirmed: `skyvern_runtime/scripts/{greenhouse_workflow_test,batch_hybrid,scorecard,cost_helper}.py` (B3).
8. **Decide test-only survivors:** keep or drop `batch_fill.py` + `contamination.py` (and their test) (F).
9. **Confirm `manager_bridge/*`** dormant subsystem fate (B5).
10. **Docs consolidation:** merge the OpenClaw docs, mark RESTRUCTURE_PROPOSAL implemented, archive dated bug reports + hunt extracts (G).
11. **Git-ignore vs keep-as-seed** decision for tracked runtime state (C/F).

*Nothing above was executed — this is analysis only.*
