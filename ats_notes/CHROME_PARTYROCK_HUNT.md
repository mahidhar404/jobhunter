# Chrome / PartyRock hunt fixes (2026-08-06)

Critical/High from the Chrome–PartyRock–hybrid hunt (first pass + CHR2/PR2 second pass).

| ID | Fix |
|----|-----|
| CHR-001 | Desktop applet `on reopen` → `launch_dashboard.sh --focus-ui`; rebuild via `./dashboard/rebuild_desktop_app.sh` |
| CHR-002 | `run_fill_visible.sh` focuses CfT by PID via System Events (never `tell application … activate`) |
| CHR-005 | Stale `SingletonLock` cleared for OpenClaw PartyRock profile when no live process |
| CHR-006 / PR-001 | `./open_partyrock.sh` uses OpenClaw CDP (`~/.openclaw/browser/openclaw/user-data` :18800) — shared login with `tailor_resume.py` |
| **CHR2-001** | Force **Chrome for Testing** for PartyRock CDP (`scripts/chrome_for_testing.py`): pin `browser.executablePath`, replace daily Google Chrome if already on :18800, direct CfT fallback. Never Dock-hijack `Google Chrome.app` |
| CHR-007 | Pre-launch kills **orphans only**; hold/CAPTCHA review windows preserved; `refuse_headed` if a fill main is live |
| CHR-008 | `fcntl.flock` on `logs/chrome_headed.lock` around headed busy-check + `chromium.launch` |
| CHR-010 | Headed preflight / focus / kill counts exclude `dashboard_ui_profile` / `--app=:8787` |
| **CHR2-002** | Start / Fast-fill blocked while another job is Ready/CAPTCHA **and** fill CfT/hold is live (`_find_blocking_start_job`) |
| **CHR2-003** | Refresh (`/api/restart`) preserves fill CfT when CAPTCHA/Ready hold is active (still keeps OpenClaw CDP) |
| PR-003 | `close_job_partyrock_tab` clears `partyrock_tab.json` only after successful close |
| PR-004 | PartyRock URL mode from `PARTYROCK_TEST_MODE` / `--test`/`--real` only — not `TEST_MODE` |
| PR-005 | PartyRock lock acquire timeout (`PARTYROCK_LOCK_TIMEOUT_S`, default 900); no lock held through no-JD agent fill |
| **PR2-001** | `tailor_resume.py` no longer calls `browser.close()` on CDP — disconnect via Playwright context exit only |
| HYB-001 | Direct `hybrid_fill.py` CLI refuses when Playwright `fast_fill.py` exists (`HYBRID_FORCE_SKYVERN=1` to override) |
| **HYB2-001** | Skyvern/`hybrid_fill` / `real_job_test` refuse `create_browser_session` when Playwright CfT fill/hold is live |
| **CHR3-003** | Fill CfT count/kill/preflight also excludes OpenClaw PartyRock (`openclaw/user-data` / `:18800`) |
| **CHR3-001/002** | Refresh finally honors `preserve_fill_cft`; hold Refresh does not SIGTERM fill/agent procs |
| **PR3-001** | Manual PartyRock path = `./open_partyrock.sh` / OpenClaw only (not IDE browser tool) |
| **CHR3-005** | **Mitigated FIXED:** `--focus-fill` / `--cft-roles`; TOOLS + Dock docs + rebuild notes; fill prefers `--remote-debugging-pipe`. Dock icon still shared (bundle ID) — operator path is clear |
| **CHR2-005** | Captcha marker TTL + hybrid/real_job_test in hold check; PartyRock alone ≠ hold |
| **CHR2-007** | Dashboard UI refuses Google Chrome.app fallback (fail loud) |
| **PR2-002/003** | Ensure PartyRock CDP `required=True` on tailor; clear stale Opening status |
| **PR3-002** | Canonical login messaging → `./open_partyrock.sh` only |
| **HYB2-002** | Manager prompts use fill-only Chrome cap (align CHR2-006) |
| **CHR2-009/010** | Script-relative ROOT + rebuild inject; `--focus-ui` errors surfaced |
| **PR2-004** | `/json/new` PUT then GET; `wait_tab_gone` default 5s |

## Verify

1. `python3 scripts/chrome_for_testing.py --ensure-openclaw` then `openclaw browser status` — `detectedPath` / executable is Chrome for Testing (not Google Chrome.app).
2. `./open_partyrock.sh` — CfT/OpenClaw window (Dock “Google Chrome” still opens daily profile).
3. Headed fill → Ready hold → Start another job → 409 (CHR2-002); Refresh keeps fill window **and** fill proc (CHR2-003 / CHR3-001/002); PartyRock CfT alone does not count as fill (CHR3-003).
4. `PARTYROCK_TEST_MODE` unset + `TEST_MODE=0` → PartyRock URL still Testing app default.
5. `python3 scripts/test_partyrock_tabs.py`
6. `python3 dashboard/test_ui_lifecycle.py` + `python3 dashboard/test_fill_parity.py`
6. `python3 -c "import ast; ast.parse(open('scripts/tailor_resume.py').read()); assert 'browser.close()' not in open('scripts/tailor_resume.py').read().split('finally:')[1].split('if target_id')[0]"`
