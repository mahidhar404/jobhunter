# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup: camera names and locations, SSH hosts and aliases, preferred TTS voices, speaker/room names, device nicknames, anything environment-specific.

## Dashboard desktop app

Desktop icon `Job Hunter Dashboard.app` (AppleScript → `dashboard/launch_dashboard.sh`) starts `dashboard/server.py` on `:8787`, then opens **Chrome for Testing `--app=http://127.0.0.1:8787/`** with a **dedicated** profile at `dashboard_ui_profile/` (Ops UI only — Classic is frozen and `/classic` redirects to `/`; not `file://`, not your daily Chrome profile). The custom icon source and generated macOS icon are `dashboard/JobHunterDashboard.png` and `dashboard/JobHunterDashboard.icns`. Rebuild the Desktop app after editing the AppleScript with the helper so `osacompile` does not reset the icon:

```bash
./dashboard/rebuild_desktop_app.sh
```

The equivalent manual steps are:

```bash
APP=~/Desktop/Job\ Hunter\ Dashboard.app
osacompile -o "$APP" dashboard/JobHunterDashboard.applescript
cp dashboard/JobHunterDashboard.icns "$APP/Contents/Resources/applet.icns"
# Required: osacompile puts the stock applet art in Assets.car and points
# CFBundleIconName at it, which overrides CFBundleIconFile/applet.icns.
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$APP/Contents/Info.plist"
rm -f "$APP/Contents/Resources/Assets.car"
codesign --force --deep --sign - "$APP"   # editing Resources breaks the ad-hoc seal
touch "$APP/Contents/Info.plist" "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
killall Finder   # only if the Desktop still shows the old icon
```

**Icon gotchas.** Copying a new `applet.icns` alone does nothing — `CFBundleIconName` + `Assets.car` win. The artwork must also be a **full-bleed opaque 1024×1024 tile**: macOS masks icons into its own rounded-square shape, and a transparent glyph gets auto-plated onto a dark background (dark artwork then disappears). Regenerate the `.icns` from the PNG with `./dashboard/make_dashboard_icon.sh`.

**Why not daily Google Chrome (important).** The UI window must never be hosted by `/Applications/Google Chrome.app`. Launching that bundle with a custom `--user-data-dir` registers it as *the* running instance of `com.google.Chrome`, so a later Dock/Spotlight “Google Chrome” only **activates that process** — you get the empty, signed-out dashboard profile instead of your daily one. Chrome for Testing has its own bundle id (`com.google.chrome.for.testing`), so Google Chrome stays free for the default profile. `resolve_ui_browser()` in `launch_dashboard.sh` picks, in order: `$JOB_HUNTER_UI_BROWSER` → newest Chrome for Testing in the Playwright cache (`~/Library/Caches/ms-playwright/chromium-*/chrome-mac-{arm64,x64}/`) → `/Applications/Chromium.app`. **If none exist it fails loud** (CHR2-007) — no Google Chrome.app fallback. Reinstall with `python3 -m playwright install chromium`.

**Triple CfT / one Dock icon (CHR3-005 — mitigated).** Three Chrome-for-Testing processes can be live at once and share **one** Dock “Google Chrome for Testing” icon (`com.google.chrome.for.testing`). Dock click cycles the wrong window unless focus is PID-scoped — **cannot** split Dock icons without a separate bundle ID.

| Role | How to spot (argv / title) | Focus command |
|------|----------------------------|---------------|
| Dashboard UI | `dashboard_ui_profile` / `--app=:8787` · window title ≈ **JOB HUNT · OPS** | Dock / `launch_dashboard.sh --focus-ui` |
| PartyRock / OpenClaw | `~/.openclaw/browser/openclaw/user-data` / `:18800` · PartyRock tab title | `./open_partyrock.sh` only |
| Form fill / hold | Playwright `--remote-debugging-pipe` · job URL title | `launch_dashboard.sh --focus-fill` · CAPTCHA/`bring_chrome_testing_to_front` |

Inventory without focusing: `./dashboard/launch_dashboard.sh --cft-roles`.

Never `tell application "Google Chrome for Testing" to activate` (raises the wrong window / blank LS windows). Fill focus excludes UI + PartyRock; fill counts/kills exclude them too (CHR3-003). **Rebuild Desktop app** after AppleScript edits: `./dashboard/rebuild_desktop_app.sh` (injects ROOT — CHR2-009).

**Dashboard window flags:**

```bash
--user-data-dir=<repo>/dashboard_ui_profile \
--no-first-run --no-default-browser-check \
--disable-infobars \                # kills the yellow "Chrome for Testing … is only for automated testing" banner
--hide-crash-restore-bubble --disable-session-crashed-bubble \  # teardown SIGKILLs the window
--app=http://127.0.0.1:8787/
```

Measured on CfT 149: `--disable-infobars` reclaims the full 56px banner; **`--test-type` alone does not suppress this particular infobar**, so it is deliberately not used. Flag changes only take effect on a fresh launch — fully quit the dashboard and reopen the Desktop app.

`dashboard_ui_profile/` is a new directory on purpose: the old `dashboard_chrome_profile/` was written by Chrome stable 151, and Chromium refuses to open a profile from a newer build. The legacy path stays in the teardown patterns so any old window still gets closed.

**Quit dialogs:** clean quit (shell exit 0) and Dock Cmd+Q (shell killed by signal) are silent. Only real non-zero launcher failures show an alert — the applet must not pop “exited with status 0”. Launcher is single-instance (`logs/dashboard_launcher.lockdir`); double-click / `--restart` while already running exits 0 without tearing down Chrome — it **focuses** the existing UI via System Events (PID). Dock click while the applet is running hits AppleScript `on reopen` → `launch_dashboard.sh --focus-ui`. Never use `tell application "Google Chrome for Testing" to activate` for focus (Launch Services blank windows); see `ats_notes/DASHBOARD_DOCK_ICON.md`.

**Quit / header X / last window close → stack stops:** `/api/shutdown` kills tracked discovery/fill/agent process groups, then JH-associated browsers (Chrome-for-Testing form-fill, `partyrock_chrome_profile`, OpenClaw PartyRock CDP at `~/.openclaw/browser/openclaw/user-data` / `:18800`). Server exits; launcher also kills those plus the dashboard UI window (`dashboard_ui_profile` / legacy `dashboard_chrome_profile` / `--app=:8787`), then the Dock applet exits. Daily Chrome is never touched. **Idle heartbeat stall does not quit** — heartbeats only track connected tabs; explicit Quit / window close / Cmd+Q / `/api/shutdown` still stop the stack.

**Refresh (↻) → `/api/restart`:** server relaunch; **keeps the dashboard UI window**, the OpenClaw PartyRock CDP browser (never counted/killed as fill CfT — CHR3-003), and (CHR2-003 / CHR3-001/002) any live fill CfT CAPTCHA/Ready hold **plus** the fill/agent process groups waiting on that hold. `serve_forever` finally honors `preserve_fill_cft`. Legacy `partyrock_chrome_profile` leftovers are still cleared. UI reloads in place (does not `window.close()` / does not spawn a second app window).

**Browsers on launch:** only the dashboard UI window (`dashboard_ui_profile` / `--app=:8787`). PartyRock CDP and the form-fill browser are started only when Start/tailor or Playwright fill needs them — never on dashboard launch, refresh, or idle. PartyRock uses OpenClaw’s profile (`~/.openclaw/browser/openclaw/user-data`, CDP `:18800`) on **Chrome for Testing** via `scripts/chrome_for_testing.py` (CHR2-001) — never daily `Google Chrome.app`. Manual `./open_partyrock.sh` shares that same CfT+profile login with `tailor_resume.py`. The UI, PartyRock, and form-fill all may run CfT; teardown tells them apart by `--user-data-dir` / `--app=` / CDP port: `dashboard_ui_profile` **and** `openclaw/user-data` / `:18800` are excluded from form-fill counts/kills in `launch_dashboard.sh`, `server.py::_chrome_for_testing_main_pids`, and `fast_fill.py::count_chrome_for_testing_mains`.

**PID files** (all under `logs/`, gitignored): `dashboard_server.pid`, `dashboard_launcher.pid`, `dashboard_chrome.pid`. KeepAlive LaunchAgent (`com.jobhunter.dashboard-server`) stays unloaded / `KeepAlive=false`.

**Latest-code guarantee (click icon → this tree).** The applet baked-in ROOT is `/Users/job/.openclaw/workspace/job-hunter` (`osadecompile ~/Desktop/'Job Hunter Dashboard.app'/Contents/Resources/Scripts/main.scpt`), `launch_dashboard.sh` resolves ROOT script-relative, and `server.py` uses `ROOT=__file__.parent.parent` — so a click always runs *this* workspace. Fills spawn a **fresh subprocess** `ROOT/.venv` or `skyvern_runtime/venv` python running `ROOT/scripts/fastfill/fast_fill.py` with `cwd=ROOT`, so **uncommitted `scripts/fastfill/` edits are picked up on the very next fill** (no rebuild/restart needed). Only `dashboard/server.py` + `dashboard/static/` changes need a server restart — the long-lived server does not hot-reload, and clicking the icon while it's already running only **focuses** the UI (`on reopen` → `--focus-ui`). To load new server/static code use the header **Refresh (↻)** (`/api/restart`) or fully Quit then reopen the icon. Launched via Finder the applet inherits your login PATH (Homebrew `python3` 3.14); the server is stdlib-only so the restricted-PATH `/usr/bin/python3` (3.9) also works — either way fills use the absolute venv paths.

**Stale-lock recovery.** If a launcher is killed uncleanly (e.g. reaped mid-launch) it can leave `logs/dashboard_launcher.lockdir`; the next click then thinks another instance is live and just focuses/exits without starting. Fix: `rm -rf logs/dashboard_launcher.lockdir` then click again. Also note Cursor/VS Code may auto-forward `:8787` on IPv6 `[::1]` — harmless (the server binds IPv4 `127.0.0.1:8787` and the UI opens the IPv4 URL).

**Process inventory (quit kills these):**

| Process | How identified |
|---------|----------------|
| Dashboard UI window | `--user-data-dir=…/dashboard_ui_profile` (or legacy `…/dashboard_chrome_profile`) or `--app=http://127.0.0.1:8787` |
| PartyRock / OpenClaw CDP | Chrome for Testing + `--user-data-dir=~/.openclaw/browser/openclaw/user-data` + `--remote-debugging-port=18800` (`scripts/chrome_for_testing.py` / `./open_partyrock.sh` + `tailor_resume.py` — never daily Google Chrome) |
| Legacy PartyRock profile | `--user-data-dir=…/partyrock_chrome_profile` (torn down on quit if still open; opener no longer launches it) |
| Form-fill headed browser | `Google Chrome for Testing` main (Playwright / fast_fill), excluding dashboard UI **and** OpenClaw PartyRock |
| Discovery / fill / agent children | `_running_procs` process groups (`start_new_session=True`) |

Colors: the UI is dark-only (`color-scheme: dark` + explicit backgrounds). If an old Chrome app window still looks washed out, fully quit that window (and Chrome app windows for 127.0.0.1), reopen via the Desktop icon, or hard-reload (`Cmd+Shift+R`).

## Fast fill (dummy only) — universal ATS + non-ATS

**Requirement:** cover **all major ATS** (packs + detect) **and** non-ATS /
unknown company career pages via generic DOM — never give up because
`platform==unknown`. See `scripts/fastfill/coverage_matrix.md`.

**Detect / packs:** greenhouse, workday, lever, ashby, icims, smartrecruiters,
workable, bamboohr, recruitee, personio, jobvite, taleo, successfactors,
dayforce, ukg, oracle, rippling, applytojob, breezy, jobscore, gem, dover,
phenom. Everything else → `unknown` + `GENERIC_SELECTOR_PACK`.

**Scope:** deterministic Layer 0/1 + ATS packs first; DeepSeek-V4-Flash **only**
for leftovers (opt-in). Not a vision/computer-use agent.

**Coverage paths (`report.coverage_path`):**

| Platform | Path |
|----------|------|
| workday | `workday_multipage` (Phase A–E) |
| unknown | `generic_dom` |
| other known ATS | `selector_pack+generic_dom` |

**Visible live step log (headed browser + Terminal.app):**

Agent chat runs use a **hidden Cursor backend shell** — you will not see
`[fill-step]` lines unless you open Terminal yourself. Always use this wrapper
for headed fills you want to watch:

```bash
./scripts/fastfill/run_fill_visible.sh 'https://jobs.ashbyhq.com/.../application'
```

Spawns a **macOS Terminal.app** window with live `[fill-step NNN]` output plus
headed Chromium. Artifacts land in
`skyvern_runtime/real_job_results/fill_live_*/` (`report.json`, `fill_steps.jsonl`, `run.log`).
Use `--inline` only when you are already in a terminal you can see (e.g. Cursor
integrated terminal with **View → Terminal** or `` Ctrl+` ``).

**Fill order (non-Workday):** `entry_prepass` → selector pack (best
page/iframe) → `record_replay` cache (ranked by continuous-learn success
rates) → extract/classify (Layer 0 autocomplete + Layer 1 heuristics +
widgets + learned) → leftovers listed; Flash only if `--flash-leftovers`
(prompt may include sanitized past similar leftover answers).

**Continuous learning:** after each fill, `continuous_learn.learn_from_report`
appends sanitized experience, updates selector success rates, demotes chronic
misses, writes lessons on UNFILLABLE_AFTER_2, and refreshes replay. Dashboard
hybrid_fill → fast_fill inherits this automatically. See
`scripts/fastfill/learning_store/LEARNING.md`.

**Iframe / SPA:** `scripts/fastfill/iframe_ctx.py` discovers apply iframes
(iCIMS etc.), ignores job-alert/newsletter noise widgets, and polls after Apply
for delayed SPA forms before `generic_dom_no_fields`. Extract + packs run on
the best frame; empty iframe → one top-page extract retry.
Report keys: `entry_prepass.spa_wait`, `entry_prepass.switched_tab`,
`fill_context` (`kind`/`url`/`fallback`).

**Workday Phase C:** after contact SUCCESS, Add work experience + resume upload
+ job rows; then EEO/self-id when possible. Never ADVANCE incomplete; never
final Submit. Incomplete required / validation banner after ADVANCE → verdict
**FAIL** (`advanced_incomplete` / `validation_after_advance`).

**Hard rules:** `DUMMY_PROFILE` + run-specific resume via `prepare_dummy_run` →
`allocate_random_run_email` (`randommail6969+{12hex}@gmail.com`, never reuse;
same email in form + compiled PDF). Canonical used-set: `alias_state.json`
`used_emails`. Never `profile.json` PII. Never tailored resumes. Never submit.
Never solve CAPTCHA (headed: pause for human, then continue). EEO via DeepSeek
+ dummy resume/DUMMY_PROFILE only (Decline is prefill/fallback). Never ADVANCE an incomplete page (validation banner after
next = **FAIL**). Resume/CV file fields must upload+verify the dummy PDF.
Real applications still use dashboard **Start**.

Canonical commands (from repo root):

```bash
# Zero-LLM: dummy resume ↔ DUMMY_PROFILE consistency + Layer 0/1 coverage
skyvern_runtime/venv/bin/python scripts/fastfill/dry_run.py --check-consistency

# Per-run identity (random email + optional tectonic PDF)
skyvern_runtime/venv/bin/python scripts/fastfill/run_identity.py --compile --json

# Blazing-fast fill (0 LLM; leftovers in report). Prefer --headed for demos.
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headed
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headless --out results.json
# Optional Flash handoff for leftovers only (default OFF; ≤5 steps when on):
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headless --flash-leftovers
# Interactive review: keep browser open after fill (Ctrl+C / ~3600s); dummy only, never submit:
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headed --flash-leftovers --hold-open
# Or: --hold-seconds N (headed default hold is 60s; headless default 0)
# CAPTCHA (headed default ON): pause — solve in browser, then Continue (overlay)
#   / Enter / touch attempt-dir `.captcha_continue` (FASTFILL_CAPTCHA_CONTINUE_FILE)
#   / wait until challenge gone. Checkbox-only widgets are NOT "challenge still
#   visible". 1st Continue while challenge true → warn; 2nd Continue → force-resume.
#   No-TTY: CAPTCHA_WAITING.md + touch sentinel; --no-captcha-wait disables; headless → BLOCKED
# Same-session leftover refill (auto by default — no Enter babysitting):
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headed --hold-open \
  --flash-leftovers --refill-passes 2
# Optional debug only: --refill-wait-enter (default OFF; do not use in Agent cycles)

# Workday multipage (Apply → auth → contact → experience → EEO → self-id); stop before Submit
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py \
  'https://….myworkdayjobs.com/…' --headless \
  --out skyvern_runtime/real_job_results/fast_fill_workday.json

# Inspect leftovers API shape from a report (no browser / no Flash invoke)
skyvern_runtime/venv/bin/python scripts/fastfill/flash_leftovers.py results.json

# Multi-agent Test→Verify→Fix cycle (dummy only; always --flash-leftovers)
skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py --help
skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py --dry-run \
  --fixture skyvern_runtime/real_job_results/fast_fill_ashby.json
# Offline regression lane (also auto-runs at end of every cycle unless FASTFILL_CYCLE_REGRESSION=0):
skyvern_runtime/venv/bin/python scripts/fastfill/regression_deepeval.py --self-test
# Answer-memory A/B (evidence gate; memory stays OFF until promote):
skyvern_runtime/venv/bin/python scripts/fastfill/answer_memory_ab.py --self-test
# Optional OmniRoute cost gateway (compose profile; DeepSeek direct is default fallback):
#   docker compose --profile gateway up -d omniroute
#   OPENAI_COMPATIBLE_API_BASE=http://127.0.0.1:20128/v1  # dummy-mode only
# Fastfill LLM/ML deps (skyvern_runtime/venv 3.12 — never the main 3.14 .venv):
#   skyvern_runtime/venv/bin/python -m pip install -r skyvern_runtime/requirements-fastfill.txt
# Rollback flags: FASTFILL_STRUCTURED_LLM=0 · FASTFILL_SEMANTIC_MATCH=0 ·
#   FASTFILL_ANSWER_MEMORY=0 (default) · unset OPENAI_COMPATIBLE_API_BASE · FASTFILL_TRACE=0
# Live variety loop (headed; never Submit; hold-open + captcha wait + auto-refill):
skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py \
  --limit 4 --headed --success-streak 2 --min-platforms 2
# CAPTCHA: solve in browser, then overlay Continue (or Enter / touch attempt
#   `.captcha_continue`). Challenge-gone resumes; 2nd Continue force-resumes
#   if detector sticky (never auto-solves). no-TTY: CAPTCHA_WAITING.md
# Refill: same-page leftover passes auto-loop (--refill-passes 2); NO Enter by default
#   (School/Degree/salary/essays filled by prefill or Flash — never ask human to refill)
# Attribution / vision helpers:
skyvern_runtime/venv/bin/python scripts/fastfill/fill_attribution.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/vision_judge.py --self-test
# Agent role prompts: scripts/fastfill/CYCLE_AGENTS.md

# Offline ATS gym (gym green ≠ live). Docs: scripts/fastfill/gym/README.md
skyvern_runtime/venv/bin/python scripts/fastfill/gym/ats/runner.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/live_gate.py --status
# Live eval/cycle refuse until ARMED and not DONE (override: --force-live)
skyvern_runtime/venv/bin/python scripts/fastfill/test_stale_skip.py
# Skip budgets (env): FASTFILL_CAPTCHA_TIMEOUT_S=120 FASTFILL_STALE_NO_PROGRESS_S=180
#   FASTFILL_STALE_ZERO_ACTIVITY_S=120 FASTFILL_HOLD_SUPPRESS_GRACE_S=120
#   FASTFILL_LOGIN_WALL_SKIP_S=60 FASTFILL_AGENT4_WAIT_S=45 FASTFILL_FILL_PAUSE=0
# Stale-skip does NOT fire during CAPTCHA wait / hold / Agent4 FIX wait / recent steps;
# CAPTCHA attended budget still skips Stripe-forever walls separately.

# Record/replay cache (selector→type only; no PII values)
skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py --list
skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py --list --verbose
skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py --sanitize
skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py --clear
skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py \
  --record-from skyvern_runtime/real_job_results/fast_fill_gh_universal_smoke.json
skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py \
  --fingerprint 'https://job-boards.greenhouse.io/dragos/jobs/5364876008' \
  --platform greenhouse
skyvern_runtime/venv/bin/python scripts/fastfill/record_replay.py --clear

# Fixed eval suite (Flash OFF; artifacts → skyvern_runtime/eval_results/)
# --limit round-robins across platforms (not first-N of one ATS)
skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py --limit 3
skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py --platform greenhouse
skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py --platform unknown

# Drop contaminated / non-policy entries from learned_fields.json
skyvern_runtime/venv/bin/python scripts/fastfill/learning.py --sanitize

# Continuous learning (experience → better selectors + Flash grounding)
skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --stats
skyvern_runtime/venv/bin/python scripts/fastfill/continuous_learn.py --sanitize
# Details: scripts/fastfill/learning_store/LEARNING.md

# Coverage / latency table; fails on SUCCESS+validation / never_submit breach
skyvern_runtime/venv/bin/python scripts/fastfill/scorecard_fast.py
skyvern_runtime/venv/bin/python scripts/fastfill/scorecard_fast.py \
  --dir skyvern_runtime/eval_results
skyvern_runtime/venv/bin/python scripts/fastfill/scorecard_fast.py --eval --gate

# Merge / regression gates (unit honesty + eval scorecard + summary safety; no browser)
skyvern_runtime/venv/bin/python scripts/fastfill/regression_gates.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/regression_gates.py
# Opt-in live eval with hard exits:
# skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py --strict-safety
# skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py --strict
```

### FAIL criteria (honest metrics)

- `advanced_incomplete` or `validation_after_advance` (incl. Workday nested) →
  verdict **FAIL** — never claim PARTIAL/SUCCESS after dishonest ADVANCE.
- `filled` counts only **verified** read-backs.
- Scorecard refuses `SUCCESS` artifacts that also carry validation banners /
  `advanced_incomplete` (historical FAIL+banner artifacts are allowed as evidence).
- `flash_called` / `flash.invoked` must be false unless `--flash-leftovers` /
  `flash_leftovers_requested` was set (`flash_called_while_off` = FAIL in eval).
- Eval SLOs (`eval_urls.json`): cross-suite `never_submit`,
  `advanced_incomplete=0`, `flash_tokens_when_off=0`; Greenhouse reachable
  forms `min_coverage≥0.9` / `max_seconds≤20`; Workday
  `page_complete_or_fail_before_advance` + zero validation banners on ADVANCE.
  Reachability blockers (CAPTCHA/Akamai/login/404/…) skip fill-quality SLOs.
- Default suite exit is diagnostic (exit 0). Use `--strict-safety` (exit 1 on
  honesty/safety) or `--strict` (also exit 2 on fill-quality). Merge lane:
  `regression_gates.py` (does not kill exploratory fills).
- `generic_dom_no_fields` leftover is honest signal (inspect spa_wait /
  fill_context) — not a dead end, not SUCCESS.

### Dashboard — Fast fill + Test Mode

**Test Mode toggle** (header): persisted in `localStorage` (`jobHunterTestMode`). Default **ON** (safe). Hover shows a popover with **PartyRock on/off** (`jobHunterPartyRock`, default **on**). When PartyRock is off + Test Mode ON, **Start** sends `skip_partyrock: true` and goes straight to dummy fast_fill (no `tailor_resume.py` / PartyRock URL). **Cron** is a checkbox with hover/click popover to set daily discovery time (`POST /api/cron/schedule`); enable/disable still uses `/api/cron/toggle`.

| Test Mode | Fast fill data | PartyRock tailor app |
|-----------|----------------|----------------------|
| **ON** | `DUMMY_PROFILE` + per-run dummy resume (`prepare_dummy_run`) | Testing: `Ultron-Resume-v3-Testing` |
| **OFF** | Real `profile.json` + tailored `resumes/<id>/resume.pdf` or `trusted_uploads/resume.pdf` | Real: `Ultron-Resume-v3` |

URLs live in `partyrock.json` (single source). Resolver: `scripts/partyrock_config.py` (`PARTYROCK_TEST_MODE` / `--test`/`--real` — **not** fill `TEST_MODE`).

**Canonical PartyRock login (PR3-002):** only `./open_partyrock.sh` (optionally `--test` / `--real`). That pins Chrome for Testing + OpenClaw profile/CDP (`:18800`) and shares login with `tailor_resume.py` / dashboard Start. Do **not** use raw `openclaw browser start/open` for human login, and never a Cursor/IDE browser tool — those miss the shared cookies and show a sign-in wall. Dashboard Start auto-ensures CDP via `chrome_for_testing.py` (same CfT path); if it fails, status goes stuck pointing at `./open_partyrock.sh`.

Dashboard: `GET /api/partyrock?test_mode=1|0`; **Start** sends `test_mode` and drives `scripts/tailor_resume.py` with the matching URL.

Both modes: **never auto-submit**, never CAPTCHA. Reports include `"test_mode": true/false` and `"dummy": true/false`.

1. Start the dashboard (`dashboard/launch_dashboard.sh` or `dashboard/server.py`).
2. Open a job that has an `apply_url` (Greenhouse is the best Playwright smoke target).
3. Set **Test Mode** in the header, then click **Fast fill** on the job detail pane.
4. Watch `status_detail` — prefixed `[DUMMY/TEST]` or `[REAL]`. Poll ~every 3s.
5. Prefer path: `scripts/fastfill/fast_fill.py` (Playwright). Fallback: `skyvern_runtime/scripts/hybrid_fill.py` only if Playwright missing (HYB-001: direct hybrid CLI refuses when fast_fill exists unless `HYBRID_FORCE_SKYVERN=1`).
6. Artifacts: `skyvern_runtime/real_job_results/dummy-fill-<id>.json` or `real-fill-<id>.json`; logs under `logs/`.
7. API: `POST /api/jobs/<id>/hybrid_fill_dummy` with `{"test_mode": true}` (default) or `{"test_mode": false}`.
8. CLI real mode (explicit opt-in): `fast_fill.py URL --real-profile --job-id <id> --headless` (sets `FASTFILL_ALLOW_REAL=1`).
9. **Start** (Test Mode ON + PartyRock on): PartyRock Testing app → compile/fit → **headed Playwright
   fast_fill with dummy** (same Live activity feed as Fast fill). **Start** (Test Mode ON + PartyRock
   off): skip tailor → headed dummy fast_fill only. Test Mode OFF: PartyRock
   Real app → agent fill. Never auto-submit either way.

### Application links (ATS over aggregators)

Dashboard **Application link** prefers the employer/ATS apply URL (`apply_url`) when known
(Greenhouse, Lever, Ashby, Workday, iCIMS, company career pages). Aggregator URLs
(LinkedIn, Indeed, etc.) stay as `job_url` / `source_url` fallback — never dropped if
resolution fails. Helpers: `scripts/apply_urls.py`. Listing merge:
`scripts/dedup_listings.py`. Existing `jobs.json` dupes (merge + soft-delete loser):
`scripts/dedup_jobs.py` (winner keeps best ATS `apply_url` + folded `alternate_urls`;
loser → `deleted` / `deleted_reason=duplicate`, not a Skipped holding pen).

### Discovery — ATS board scrape (`scrape_ats.py`)

Dashboard **Discover** continues from `logs/discovery_checkpoint.json` by default
(completed sources skipped; **Fresh run** in the popover clears the checkpoint).
Last outcome (`success`/`failed`/`interrupted`/`partial`) lives in
`dashboard/discovery_last_run.json` and shows in the Discover popover.
Scrapers get `logs/discovery_skip_urls.json` so known jobs.json/blocked URLs
are not re-fetched. → `run_scout_scrape_then_dedup` runs `scripts/scrape_ats.py`
after scout. Platforms with free public board endpoints (no auth / no proxy):

| Platform | Endpoint pattern |
|----------|------------------|
| Greenhouse / Lever / Ashby / Recruitee / Personio | existing |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/{slug}/postings` |
| Workable | `apply.workable.com/api/v1/widget/accounts/{slug}` (+ v2 detail for JD) |
| Rippling | `ats.rippling.com/api/v1/board/{slug}/jobs` |
| Breezy HR | `{slug}.breezy.hr/json` |
| BambooHR | `{slug}.bamboohr.com/careers/list` (+ `/detail` for JD) |

Registry: `ats_companies.json` (self-expands from listing URLs + slug guesses).
**Still excluded:** Workday/iCIMS (Akamai/CAPTCHA), Jobvite/Gem/Dover/Comeet
(no reliable free board JSON), Zip/Glassdoor (aggregators), Taleo/SuccessFactors/Avature.
Self-test: `python3 scripts/test_scrape_ats_platforms.py` (fixtures + optional `--live`).

### Discovery — India region (opt-in)

US is the default; India is opt-in via the Discover popover
(`discover_us`/`discover_india` in `logs/discovery_settings.json`, never both
false; server exports `JOBHUNTER_DISCOVERY_REGIONS` to scrapers). Region gate
lives in `scripts/discovery_filters.py` (mirrored in `app.js` — keep in sync).
When India is on: `scout.py` adds an India pass (`location=India`,
`country_indeed=india`, same `--out` file), the ATS boards keep India roles
(India-heavy slugs seeded in `ats_companies.json`), and the India-only sources
run — `scripts/scrape_internshala.py`, `scripts/scrape_hirist.py`,
`scripts/scrape_cutshort.py`, `scripts/scrape_adzuna.py` (shared helpers in
`scripts/india_scrape_common.py`). Adzuna needs `ADZUNA_APP_ID`/`ADZUNA_APP_KEY`
(env or git-ignored `web_keys.json`); with no keys it skips cleanly (UI shows
skipped, no crash). Built In stays US-only. Ops **Region** filter = `All`/`US`/
`India`; jobs carry a `region` stamp; INR/LPA parsed display-only (never prunes).
Full notes + Wave B backlog: `ats_notes/INDIA_DISCOVERY.md`.

### Discovery — Built In + JD extract
`scripts/scrape_builtin.py` uses adaptive HTTP pacing (delay grows on 429, cools on success) and a **headless Playwright** HTML fallback only after 429 retries are exhausted (`scripts/pw_fetch_html.py`). `scripts/extract_job_posting.py` (manual-add / missing-JD backfill) adds the same Playwright tier when HTTP HTML is missing or too thin. Never used for Workday/iCIMS/LinkedIn; never solves CAPTCHA.

## Manager bridge (Claude Manager ↔ Cursor Executor)

File-based task queue for fastfill improvement — no APIs.

| Item | Path |
|------|------|
| Protocol | `skyvern_runtime/manager_bridge/PROTOCOL.md` |
| Claude Manager prompt (paste into Claude Code) | `skyvern_runtime/manager_bridge/CLAUDE_MANAGER_PROMPT.md` |
| Cursor executor instructions | `skyvern_runtime/manager_bridge/CURSOR_EXECUTOR.md` |
| Living status | `skyvern_runtime/manager_bridge/STATUS.md` |
| Post task (Manager) | `scripts/manager_bridge/post_task.py` |
| List inbox | `scripts/manager_bridge/list_inbox.py` |
| Ack task (Executor) | `scripts/manager_bridge/ack_task.py` |
| Post result (Executor) | `scripts/manager_bridge/post_result.py` |
| Heartbeat | `scripts/manager_bridge/heartbeat.py` |

```bash
cd /Users/job/.openclaw/workspace/job-hunter
PY=skyvern_runtime/venv/bin/python
$PY scripts/manager_bridge/list_inbox.py
$PY scripts/manager_bridge/heartbeat.py --check-gates --check-chrome
```

Yogesh: paste `CLAUDE_MANAGER_PROMPT.md` into Claude Code as Manager; tell Cursor **"Execute manager bridge inbox task"** to run Executor.

## Related

- [Agent workspace](/concepts/agent-workspace)
