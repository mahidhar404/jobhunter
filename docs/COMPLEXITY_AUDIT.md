# Complexity Audit — job-hunter

**Date:** 2026-08-08 · **Type:** READ-ONLY analysis (nothing edited/refactored except this file).
**Scope:** find code that IS used but is *more complex than it needs to be* — over-engineering and needless complexity. This is distinct from the dead-code sweep in `docs/CLEANUP_AUDIT.md` (commit `d20c339`); already-deleted/unused items are **not** re-flagged here.
**Method:** Glob/Grep/Read + four parallel per-area exploration passes (server, frontend+runtime, fastfill engine, discovery+scrapers), then hand-verification of the headline counts and AST-accurate function line counts. Every quantitative claim below was spot-checked against the source.

---

## Executive summary

The repo is **less over-engineered than its raw size (~86k lines) suggests.** Most of the bulk is *essential* domain surface — ATS DOM quirks, region/clearance/salary policy regexes, and never-submit/CAPTCHA/PII safety gates — and it is largely well-factored (the fastfill engine centralizes dropdown scoring in one `verified_select` brain rather than copy-pasting it per ATS; the India scrapers share `india_scrape_common.py`; JSON serialization uses `json.dumps` everywhere, not hand-rolled string building). The genuine over-engineering is concentrated and mechanical, not architectural, and falls into three buckets: **(1) a handful of enormous "god functions"** — five functions exceed 500 lines, topped by `run_fast_fill_async` at **1,692 lines** and `server.py`'s `_run_tailor_then_fill_body` at **586** — most with **zero direct test coverage**; **(2) high-volume copy-paste** — a job-lookup/404 handler prologue repeated ~15× in `server.py`, ~18–22 near-identical `fetch→json→alert→poll` handlers in `app.js`, and `normalize_company` copied verbatim across 4 files; and **(3) data that is expressed as hand-unrolled code** — HTTP routing written as 15 copy-pasted `parts[...]` index checks, and region token lists duplicated between two regexes in `discovery_filters.py`. The cheapest, safest wins are the duplication consolidations (small, pure-function, often test-covered). The god-function decompositions are the biggest maintainability wins but carry the most risk precisely because the largest functions are untested — **write characterization tests first.**

### Top 5 highest-impact simplifications

| # | Simplification | Location | Effort | Risk | Tests? | Why it wins |
|---|---|---|---|---|---|---|
| 1 | Consolidate `normalize_company`/`normalize_title` (verbatim in 4 files) + the ~12 `_norm*` one-liners + confusable-state table into a shared `text_norm.py`/leaf module | `dedup_jobs.py`, `dedup_listings.py`, `write_discovered_jobs.py`, `tracker.py`; `scripts/fastfill/*` | S | Low | Partial | Pure functions, kills drift risk, ~4 copies → 1 import |
| 2 | Collapse the `server.py` job-lookup + 404 prologue (repeated ~15×) into one `_locked_job()` guard/decorator; unify the 25 `{"error":"not found"},404` sites | `dashboard/server.py` handlers | M | Low–Med | Per-handler tests exist | Highest single-file dedup volume; mechanical |
| 3 | Add a `apiPost()` helper for the ~18–22 duplicated `fetch→json().catch→alert→poll` handlers in `app.js` | `dashboard/static/app.js` | M | Low–Med | No JS unit tests | Removes the most frontend copy-paste |
| 4 | Data-drive the region regexes: extract `INDIA_CITIES`/`INDIA_STATES`/`US_STATE_ABBREVS` once, build regexes via `"\|".join(...)` | `scripts/discovery_filters.py` | M | Med | `test_discovery_filters.py` (good) | Removes a genuine drift hazard (token added to one regex, missed in twin) |
| 5 | Test-then-decompose the top god functions (`run_fast_fill_async` 1692, `fill_from_extract` 960, `_run_tailor_then_fill_body` 586) into named phase/per-field helpers | `fast_fill.py`, `server.py` | L | Med–High | **None** on the biggest | Biggest readability/maintainability win; gated on adding tests first |

---

## A. Reinvented wheels

Mostly **clean** — the codebase reaches for stdlib/deps correctly (`json.dumps`, `urllib.parse`, `hashlib.sha256`, `difflib.SequenceMatcher`, `bs4`, `trafilatura`, `xml.etree`). Few true reinventions; the notable ones:

### A1. `app.js` strict + "fallback" extractor twins — duplicated function bodies
- **Location:** `dashboard/static/app.js` — `extractSalary` (287–329) vs `extractSalaryFallback` (331–368); `extractMinRequiredYoe` (514–542) vs `…Fallback` (544–585); `detectWorkMode` (598–608) vs `…Fallback` (610–621).
- **Now:** Each "fallback" is a ~90% copy of its "strict" sibling; the only real difference is *which regex array* is scanned plus an `approx` flag. `extractSalaryFallback` even re-runs `extractSalary` first (line 332), doubling work.
- **Why over-complex:** Whole control-flow bodies duplicated to vary one input.
- **Simpler:** Parameterize one function `extract(regexes, {approx})` and call it twice, or fold to a single pass over `[strict, fallback]` returning `{value, approx}`.
- **Effort:** M · **Risk:** Med (these gate job visibility) · **Tests:** none direct (JS mirrors `discovery_filters.py`).
- **NEEDS JUDGMENT:** the parallel structure is deliberately kept in lock-step with the Python policy module; dedup must preserve that mapping.

### A2. `agent_runner._json_deep_find` — recursive whole-blob key search
- **Location:** `dashboard/agent_runner.py:121–141`.
- **Now:** Recursively case-insensitive-searches all values (incl. lists) of an arbitrary JSON blob to find an API key.
- **Why over-complex:** Keys live at known paths (`web_keys.json`, `credentials.json`, `{"llm":{...}}`); full recursion is more general than the documented shapes need.
- **Simpler:** A couple of explicit known-path lookups.
- **Effort:** S · **Risk:** Low · **Tests:** covered (`test_agent_runner.py::test_key_loading_from_web_keys_and_credentials`). Minor; cheap either way.

### A3. `hybrid_fill.gist_of` — regex-to-English via 6 chained `re.sub`
- **Location:** `skyvern_runtime/scripts/hybrid_fill.py:211–231`.
- **Now:** Strips regex syntax out of a `field_map.PATTERNS` entry to show the model a human "gist"; comments record a live-caught bug history.
- **Why over-complex:** Reverse-engineering a label from a regex is fragile.
- **Simpler:** Store a human label alongside each pattern in `field_map.py` (`(pattern, label)`), drop the reverse-engineering.
- **Effort:** M · **Risk:** Med (touches `field_map` schema) · **NEEDS JUDGMENT** (cross-module change).

*(Explicitly NOT reinvention — verified and left alone: `server.py` uses `json.dumps` throughout with no manual JSON string-building; `_parse_multipart_file` is a justified stdlib gap after `cgi` removal in 3.13; scrapers use `urllib` because `requests`/`httpx` are **not** direct deps — adding one would add complexity, not remove it; `jd_fingerprint` correctly uses `hashlib`; `scrape_builtin._extract_job_init_json`'s brace-matcher is correct because the JSON is embedded in a JS call.)*

---

## B. Excess indirection / abstraction

Largely **absent**. The registry-looking dicts (`scrape_ats.SCRAPERS`/`PROBE_URLS`/`SLUG_PATTERNS`, `extract_job_posting.KNOWN_ATS_TRIERS`, `fast_fill.PLATFORM_PATTERNS`) each dispatch over 8–10 genuinely-different platforms — that's data-driven done right, not indirection for 1–2 cases. The few real smells:

### B1. Two thin body-wrappers in `server.py`
- **Location:** `run_tailor_then_fill` (4435–4481) → `_run_tailor_then_fill_body`; `run_hybrid_fill_dummy` (4110–4146) → `_run_hybrid_fill_dummy_body`.
- **Now:** Public fn forwards all kwargs to a `_body` and wraps in `try/except → _mark_fill_thread_stuck`; the split exists only to attach crash-handling to a thread target. Duplicated twice.
- **Simpler:** One `run_guarded(fn, where, *a, **kw)` helper at the two `threading.Thread(target=...)` sites, or a `@fill_thread_guard("name")` decorator.
- **Effort:** S · **Risk:** Low · **NEEDS JUDGMENT** (mild; low payoff).

### B2. `_DiscoveryProcSetView` — a class to re-expose a dict
- **Location:** `dashboard/server.py:378–400`.
- **Now:** A "set-like view" implementing `clear/__len__/__iter__/__bool__/discard` over the module dict `_discovery_procs_by_key`, kept explicitly "for back-compat: len/list/clear/discard used by tests and older call sites."
- **Simpler:** Callers/tests use the dict + existing `_register/_unregister/_kill_*` helpers directly; delete the view.
- **Effort:** S · **Risk:** Low · **Tests:** it exists *for* tests — removal means touching them. **NEEDS JUDGMENT.**

### B3. `run_guard` triple-layer single-run guard
- **Location:** `dashboard/run_guard.py` — cross-process flock (41–58) + in-process `_own_keys` set (32–33) + `agent_runner._active_turns` (62–63).
- **Now:** Three layers guarding "one run at a time"; the module docstring calls the flock a "belt-and-suspenders" backstop.
- **Assessment:** The dashboard is single-process and already has `_active_turns`, so the cross-process flock is arguably redundant — but it's cheap, self-releasing on crash, and *documented as intentional defense*. **NEEDS JUDGMENT — lean leave-alone.** Covered by `test_run_guard.py`.

---

## C. Duplication to consolidate

The richest vein of *safe* wins. All counts below were hand-verified.

### C1. `normalize_company` / `normalize_title` — verbatim in 4 files ⭐ (top pick)
- **Location:** `dedup_listings.py:129`, `dedup_jobs.py:59`, `write_discovered_jobs.py:75` (company), `tracker.py:130` (company). Same `re.sub(r"\b(inc|llc|corp|…)\b\.?", …)` typed 4×.
- **Simpler:** Move to a shared `text_norm.py` (or `apply_urls.py`, already imported by 3 of the 4). One import replaces 4 copies.
- **Effort:** S · **Risk:** Low · **Tests:** none dedicated, but pure functions; `test_apply_urls.py` exists. **Easiest clean win.**

### C2. `server.py` job-lookup + 404 prologue — repeated ~15× ⭐
- **Location:** the block `with _lock: data=read_jobs(); job=self._job(data,job_id); if job is None: 404` across `_handle_approve_command/_cancel/_skip/_restore/_mark_submitted/_edit_applied/_claim_ready_announcement/_resume_upload/_resume_clear` + 4 `do_GET` sites. Verified: `parts[0:2]==["api","jobs"]` idiom **15×**; `{"error":"not found"},404` **25×**; raw `next((j for j in data["jobs"] if j["id"]==job_id),None)` ~27×.
- **Why over-complex:** Same lock/read/find/404 dance re-typed everywhere; invites inconsistency (some paths re-read under lock, some don't).
- **Simpler:** `with self._locked_job(job_id) as (data, job): if job is None: return self._not_found()`, or a decorator resolving `job_id`→`job`; consolidate the generator into `self._job`/a `find_job(data,id)`.
- **Effort:** M · **Risk:** Low–Med (mechanical, many handlers) · **Tests:** handlers individually covered (`test_triage_redesign.py`, `test_applied_address.py`, `test_ready_announcement.py`) — refactor verifiable.

### C3. `app.js` "action fetch" handlers — ~18–22 near-identical ⭐
- **Location:** `submitAnswer` (3313), `decideCommand` (3323), `startJob` (3397), `cancelJob` (3517), `skipJob` (3531), `restoreJob` (3559), `markSubmitted` (3577), `uploadJobResume` (3599), `deleteJob` (3632), `runPrune` (3731), `addJobByUrl` (3779), `runDiscover` (3817), `saveAppliedJob` (4227), `saveProfile` (4481), `saveCronSchedule` (4584), … Verified: `res.json().catch` appears **22×**; the `"Content-Type":"application/json"` literal **11×+**.
- **Now:** Each hand-writes `fetch(POST)` + `res.json().catch(()=>({}))` + `!res.ok` alert + `await poll()`; the 409-vs-!ok branch is re-derived per site.
- **Simpler:** One `apiPost(url, body, {failMsg})` helper that throws on `!res.ok`; handlers become `try { await apiPost(...) } catch(e){ alert(e.message) } await poll()`.
- **Effort:** M · **Risk:** Low–Med (a few sites have bespoke 409/success logging — keep those) · **Tests:** no JS unit tests (endpoints covered Python-side).

### C4. `server.py` backfill loops share one skeleton
- **Location:** `_backfill_salary_loop` (7146), `_backfill_yoe_work_mode_loop` (7231, 157 ln), `_backfill_multi_opening_loop` (7075), `_backfill_missing_jds_loop` (7037).
- **Now:** Each: import→check `_*_MARKER`→`with _lock: read_jobs()`→`needs` predicate→iterate→per-field `prev/new` compare + counters→`write_jobs`→marker→print. Salary and yoe/work-mode are structurally identical with different field lists.
- **Simpler:** A `run_backfill(marker, needs_fn, per_job_fn, summary_fn)` driver, or a `(field, extractor)` table the counter iterates. ~150 lines saved.
- **Effort:** M · **Risk:** Med (mutate `jobs.json` fields the UI/prune read) · **Tests:** no `test_backfill*`. **NEEDS JUDGMENT** — add coverage first.

### C5. fastfill `_norm*` one-liners + confusable-state table
- **Location:** `_norm/_norm_digits/_norm_key/_norm_label/_clean_label` defined independently ~12× across fastfill; verified byte-identical `_norm_digits` in `verified_select.py:250` **and** `exp_workday_selectors.py:917`. Separately, `gh_select._CONFUSABLE` (570–585) duplicates the state-pair knowledge in `verified_select.states_are_confusable` across a deliberate circular-import boundary.
- **Simpler:** One `text_norm.py` for the normalizers; move the confusable pair *table* to a leaf module both import (keep logic in place).
- **Effort:** S · **Risk:** Low · **Tests:** partial (`test_address_resolver.py`, `test_verified_select.py`).

### C6. `server.py` osascript dialog helpers + inline bool-coercion
- **Location:** `_send_desktop_notification` (5242) & `_send_answer_dialog` (5301) both re-define `esc(s)` + a threaded `subprocess.run(["osascript",…])`. Separately, the `str(raw).strip().lower() not in ("0","false","no","off")` idiom is re-inlined in `_parse_test_mode` (3944), `_dummy_fill_flash_requested` (3724), `_handle_start` (6805) while a general `_coerce_bool` **already exists at line 1174**.
- **Simpler:** One `_run_osascript_dialog(script)` + `_osa_escape`; route bool parsing through `_coerce_bool` (keep `_parse_test_mode`'s fail-closed branch explicit — safety).
- **Effort:** S · **Risk:** Low.

### C7. India JSON scrapers + the 10-key listing dict
- **Location:** `scrape_hirist.py:43–135` vs `scrape_cutshort.py:39–127` are ~70–80% line-identical (`_records`/`_job_url`/`normalize_jobs`/`scrape`/`main`). The 10-key listing dict (`title,company,site,job_url,job_url_direct,description,date_posted,job_type,location,search_term`) is hand-built **~17×** (`scrape_ats.py` 10 sites + `scrape_builtin.py:425` + `scout.py:152` + 4 India normalizers).
- **Simpler:** Add `normalize_generic_json(records, *, site, base, url_keys, title_keys, …)` + `run_paged_search(...)` to `india_scrape_common.py` (hirist/cutshort → ~40 lines of config each); add a `make_listing(**fields)` schema constructor with sane defaults.
- **Effort:** M · **Risk:** Med / Low–Med · **Tests:** `test_scrape_hirist.py`, `test_scrape_cutshort.py`, `test_scrape_ats_platforms.py`, `test_scrape_builtin.py` exist. **NEEDS JUDGMENT** (endpoint shapes are documented as "may change").

### C8. Two-pass merge cluster loop duplicated across dedup scripts
- **Location:** `dedup_listings.py:272–319` and `dedup_jobs.py:365–466` (`_merge_active_jobs`) both implement "Pass 1 fingerprint clusters → Pass 2 company+title `SequenceMatcher ≥ 0.85`".
- **Simpler:** A shared `cluster_and_merge(items, *, key_fn, match_fn, merge_fn)`; callers pass their own merge callback.
- **Effort:** M–L · **Risk:** Med–High · **Tests:** ⚠ **none** on either dedup module. **Add tests before touching** (see E-note).

---

## D. God functions / files (with line counts)

AST-accurate line counts (verified this pass). Bodies mixing many responsibilities.

### D1. fastfill engine (worst offenders in the repo)
| Lines | Function | Location | Tests? |
|---|---|---|---|
| **1692** | `run_fast_fill_async` | `fast_fill.py:8788` | ❌ none |
| **982** | `_demote_filled_against_required_empty` | `fast_fill.py:3231` | ❌ none |
| **960** | `fill_from_extract` (the main loop) | `fast_fill.py:6539` | ❌ none |
| **654** | `run_inpage_flash_leftovers` | `fast_fill.py:829` | ❌ none |
| **536** | `_phase_c_experience` | `exp_workday_selectors.py:5154` | ❌ none |
| **499** | `reassert_greenhouse_contact_after_resume` | `fast_fill.py:5330` | ❌ none |
| 419 | `fill_ashby_location_then_zip` | `ashby_widgets.py:684` | partial |
| 409 | `fill_ashby_widgets` | `ashby_widgets.py:2218` | partial |
| 361 | `main` | `fast_fill.py:10878` | — |
| 358 | `fill_lever_widgets` | `lever_widgets.py:382` | partial |

Across the 12 fastfill files: **488 functions, 28 over 200 lines, 70 over 100, median 24.**

- **`run_fast_fill_async` (1692):** an orchestrator doing arg/identity resolution, headless resolution, browser prelaunch gate, context, navigation, entry-click loop, per-page fill dispatch, refill passes, CAPTCHA/hold, and finalize — all inline in one `async with async_playwright()`. Mostly straight-line phases → extracts cleanly into `_resolve_run_identity()/_launch_browser_gated()/_run_fill_pipeline()/_run_refill_passes()/_finalize_and_hold()`.
- **`fill_from_extract` (960):** one `for raw in raw_fields` loop mixing classify + per-type special-casing + result-bucketing. Extract `_handle_one_field(field, ctx) -> FieldOutcome`; the loop just appends outcomes. **This is the single change that would make field logic unit-testable.**
- **`_demote_filled_against_required_empty` (982):** partially justified anti-false-verify safety, but bloated by multi-hundred-line inline `page.evaluate` JS blobs (3245–3273) and repeated visibility probing. Hoist inline JS to `.js` files (repo already has `resolve_extract_js()`), share one `is_visible()` helper, split zip-refill vs how-heard-demote.
- **Effort:** L (each) · **Risk:** High — **all top offenders are untested and drive a live browser.** · **Prereq:** characterization tests first.

### D2. `dashboard/server.py`
| Lines | Function | Location |
|---|---|---|
| **586** | `_run_tailor_then_fill_body` | 4484 |
| 282 | `run_scout_scrape_then_dedup` | 2251 |
| 204 | `_run_hybrid_fill_dummy_body` | 4149 |
| 188 | `_handle_start` | 6795 |
| 169 | `reconcile_loop` | 5441 |
| 157 | `_backfill_yoe_work_mode_loop` | 7231 |

- **`_run_tailor_then_fill_body` (586):** one procedure spanning 6+ pipeline stages (skip/reuse decision, JD-fetch branch, PartyRock lock acquire/timeout/abort, tailor subprocess + agent fallback, compile, page-fit, publish, address pick, fill handoff), with the abort check + `with _lock: read_jobs()…write_jobs()` re-read pattern sprinkled ~10×. Extract stage functions + an `if _aborted(job_id): return` helper.
- **Effort:** L · **Risk:** Med–High (core real/test fill path w/ safety semantics) · **Tests:** `test_fill_parity.py`, `test_applied_address.py` exercise parts — extract behind these.
- **`run_scout_scrape_then_dedup` (282):** 6-tuple `source_jobs` packing (poor-man's record) + 3 nested closures threading merge state via `nonlocal`. Use a `@dataclass SourceJob` and lift merge/checkpoint onto a small `DiscoveryRun` object. Covered by `test_discovery_parallel.py`/`test_discovery_resume.py`.

### D3. `app.js` render god functions
| ~Lines | Function | Location |
|---|---|---|
| ~187 | `renderDossier` | 2714–2901 |
| ~145 | `renderList` | 1991–2136 |
| ~145 | `bindOpsChrome` | 3166–3311 |
| ~115 | `renderDiscoverPopover` | 3919–4034 |

- Each fuses template (hand-concatenated HTML strings) + state derivation + imperative DOM re-binding. `renderList` embeds a ~30-line group comparator (2050–2080) inline.
- **Simpler:** split each into `buildXHtml()` (pure string) + `bindX()` (listeners); extract `compareGroups(a,b,sortBy)`.
- **Effort:** M–L · **Risk:** Low (pure extraction, no behavior change) · **Tests:** none (no JS unit coverage).

*(`agent_runner.run_turn` at 93 lines is the only "large" python runtime function and is mostly inherent loop; extracting its inner tool-call loop 500–520 to `_run_tool_calls()` is a minor nicety.)*

---

## E. Dead complexity / unused knobs

### E1. `_dummy_fill_flash_requested(payload=None, query=None)` — params never passed
- **Location:** `dashboard/server.py:3712–3741`. Both call sites (4569, 5043) call it with **no arguments**, so the ~18 lines of JSON-body + query-string parsing can never execute; only the env var + hard-coded `return True` run.
- **Simpler:** Reduce to `_flash_default()` (env, default True) until a caller actually threads request data.
- **Effort:** S · **Risk:** Low · **NEEDS JUDGMENT** (confirm no planned caller).

### E2. `run_fast_fill_async(max_entry_clicks=3)` — near-vestigial knob
- No caller passes a non-default except CLI pass-through (`args.max_entry_clicks`). It *is* CLI-exposed, so not clearly dead. Low priority. **NEEDS JUDGMENT.**

### E3. `discovery_filters` human-sync hint lists
- `SENIORITY_EXCLUDE_HINTS` (52–77) and `CLEARANCE_EXCLUDE_HINTS` (502–517) duplicate their `*_RE` counterparts and are never used for matching (comments: "human-readable sync lists for docs/tests"). Borderline dead weight. **NEEDS JUDGMENT** — leave if a test asserts on them.

### E4. `app.js` `TEMP_APPLIED_COUNT_OVERRIDE = null` (line 3)
- A module-level override nothing sets ("remove when user says undo"). Technically dead — mostly in scope of the prior cleanup, noted here as a live-file constant.

*(Note: `run_hybrid_fill_dummy(headed=...)` is always `True` from in-file callers, but a headless external caller may exist in the fastfill scripts — **NEEDS JUDGMENT**, likely a real knob.)*

---

## F. Justified complexity — explicitly leave alone

These are essential; do **not** treat as over-engineering:

- **Safety / never-submit / PII gates:** `real_job_test._check_no_submit_clicked` (~211 ln) + `_check_enter_keypress`/`_check_post_hoc_submission_confirmed`/`_detect_stuck_loop`/watchdog; `agent_runner._shell_is_blocked` + `_SHELL_DENY_*` denylists; fastfill `assert_not_real_profile_env`/`assert_dummy_resume_path`, anti-contamination email/tag checks, CAPTCHA-pause/headed-cap gates; `server._classify_fill_stdout_line`/`_report_allows_ready` ready-gating; `_parse_test_mode` fail-closed branch. Verbose *by design*.
- **The scoring brain:** `verified_select._default_score_option` / `gh_select._score_option` (sponsorship traps, gender/degree polarity, decline↔OFCCP wording, confusable states, salary/school fuzzy bands). Centralized already — **not** duplicated per ATS.
- **The regex policy tables:** `discovery_filters.py` + the mirrored `app.js` constants for US/India/clearance/intel/YOE/salary and ISO-2 country-tail resolution. (Internal *token-list* duplication is C4-worthy; the depth itself is essential.)
- **Correct stdlib-gap / dep-aware choices:** `_parse_multipart_file` (post-`cgi` removal); `urllib` HTTP (no `requests`/`httpx` direct dep); `_run_subprocess_step`/`_run_fill_subprocess_streaming`/`_LogTail` (cancelable, timeout-bounded runs w/ live log tailing); `_atomic_write_json` + the purpose-scoped `threading.Lock`s.
- **Workday SPA settle:** `exp_workday_selectors._poll_spa_settle` (predicate-poll that replaced fixed sleeps for an ATS with no Playwright-waitable signal). *(The ~69 scattered magic `wait_for_timeout()` sleeps around it are a separate brittleness smell — Med/High risk to touch, recommend leaving unless chasing a specific flake.)*

---

## Recommended order (biggest simplicity win / lowest risk first)

1. **C1** — de-dup `normalize_company`/`normalize_title` into one shared helper. *(S/Low, pure fns.)*
2. **C5** — consolidate the fastfill `_norm*` one-liners + confusable-state table into leaf modules. *(S/Low.)*
3. **C6** — one osascript dialog helper + route bool parsing through the existing `_coerce_bool`. *(S/Low.)*
4. **C2** — collapse `server.py` job-lookup/404 prologue into one guard (`_locked_job`). *(M/Low–Med, handler tests exist.)*
5. **C3** — add `apiPost()` and fold the ~18–22 `app.js` action handlers into it. *(M/Low–Med.)*
6. **D3** — split `app.js` render god functions into `buildXHtml()` + `bindX()`; extract `compareGroups`. *(M/Low, pure extraction.)*
7. **C7** — `make_listing()` schema constructor + shared India JSON normalizer. *(M/Med, scraper tests exist.)*
8. **B1/B2/E1/E3** — retire the thin wrappers, `_DiscoveryProcSetView`, and dead knobs (confirm each). *(S/Low.)*
9. **C4** — unify `server.py` backfill loops behind a `run_backfill()` driver — **add coverage first.** *(M/Med.)*
10. **C8** — unify the dedup cluster/merge loop — **write dedup tests first** (currently zero). *(M–L/Med–High.)*
11. **C4-regex (D-area #4 in Top 5)** — data-drive `discovery_filters.py` region regexes from single token lists. *(M/Med, `test_discovery_filters.py` verifies.)*
12. **D2 then D1** — decompose `server.py` `_run_tailor_then_fill_body`, then the fastfill god functions (`run_fast_fill_async`, `fill_from_extract`) — **only after characterization tests exist.** *(L/High.)*

> ⚠ **Cross-cutting prerequisite:** the highest-value decompositions (D1, C8, C4) sit on top of **untested** code — the 9 largest fastfill functions and both dedup modules have no direct tests. Treat "add characterization tests" as step 0 for those, not an afterthought.

*Nothing above was executed — this is analysis only.*
