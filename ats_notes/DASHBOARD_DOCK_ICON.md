# Dashboard dock icon / blank Chrome windows

Date: 2026-08-06  
Scope: Desktop `Job Hunter Dashboard.app` + Chrome-for-Testing focus.

## Root cause

1. **Dock click did nothing visible** — while the AppleScript applet blocked on
   `launch_dashboard.sh`, a Dock re-click only activated the invisible applet.
   There was no `on reopen` handler, and `open_dashboard_ui` reused an existing
   UI PID without raising the window.
2. **Blank Chrome windows** — `tell application "Google Chrome for Testing" to
   activate` (fill focus / CAPTCHA / visible-fill helper) goes through Launch
   Services. When CfT was started via Playwright `executable_path` or the
   dashboard binary launch, activate often **spawns a new default-profile
   blank window**. Re-executing CfT against an already-live
   `dashboard_ui_profile` also hits Chromium's singleton handoff → blank extra
   window.

## Fix

| Path | Behavior |
|------|----------|
| `on reopen` + `launch_dashboard.sh --focus-ui` | Dock click focuses/creates UI; no lock, no teardown |
| `focus_dashboard_ui` | System Events `unix id` of dashboard **main** PID |
| `open_dashboard_ui` | Focus if alive; else clear stale Singleton* locks; launch once |
| `bring_chrome_testing_to_front` / `run_fill_visible.sh` | Focus fill mains by PID; never LS `activate` |
| Fill teardown | Still excludes `dashboard_ui_profile` / `--app=:8787` + OpenClaw PartyRock |

### Triple CfT / one Dock icon (CHR3-005 — mitigated)

UI (`dashboard_ui_profile`), PartyRock (`openclaw/user-data` `:18800`), and Playwright fill (`--remote-debugging-pipe`) are **three** Chrome-for-Testing processes with **one** Dock icon (`com.google.chrome.for.testing`). Clicking the Dock icon cycles the wrong window unless focus is PID-scoped — Dock split requires a different bundle ID (out of scope).

| Role | Window title cue | Focus |
|------|------------------|-------|
| Dashboard UI | **JOB HUNT · OPS** | Dock / `launch_dashboard.sh --focus-ui` |
| PartyRock | PartyRock app title | `./open_partyrock.sh` only |
| Form fill | Job posting title | `launch_dashboard.sh --focus-fill` · CAPTCHA helpers prefer `--remote-debugging-pipe` |

- CAPTCHA / fill helpers → **fill PID** (`--remote-debugging-pipe` preferred; never UI/PartyRock) — CHR3-006
- Print inventory: `./dashboard/launch_dashboard.sh --cft-roles`

**Rebuild Desktop app** after AppleScript / launcher focus edits:

```bash
./dashboard/rebuild_desktop_app.sh
```

Injects repo ROOT into the applet (CHR2-009). Rebuild notes are also printed by that script.

See also: `TOOLS.md` (Triple CfT table), `ats_notes/CHROME_PARTYROCK_HUNT.md`.

## Verify

1. Open dashboard via Desktop icon; click Dock icon again → same UI focuses, no blank window.
2. Start a headed fill → fill CfT focuses; dashboard stays up; no blank window.
3. Quit fill / kill excess fill Chromes → dashboard UI remains.
