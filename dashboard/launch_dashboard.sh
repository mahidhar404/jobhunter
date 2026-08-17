#!/bin/bash
# One-click launcher (Desktop "Job Hunter Dashboard.app" → this script):
#   1) Start dashboard/server.py if port 8787 is down
#   2) Open the UI in a *dedicated* Chromium profile, hosted by a browser that
#      is NOT /Applications/Google Chrome.app (see resolve_ui_browser)
#   3) Wait on the server PID we own (or the listener PID if already up)
#
# Lifecycle: the UI heartbeats (POST /api/heartbeat) track connected tabs.
# Closing the last dashboard tab/window (or header Quit / Cmd+Q on the
# applet) POSTs /api/shutdown and stops the server; stalled heartbeats alone
# do NOT quit. This wrapper then kills JH-associated browsers and exits — so
# "Job Hunter Dashboard" leaves the Dock. Children (discovery scrapes,
# fast fills) are process-group-killed by the server on shutdown; this
# script also tears down:
#   - dashboard UI browser (dashboard_ui_profile / --app=:8787)
#   - PartyRock manual Chrome (partyrock_chrome_profile) — only if already open
#   - OpenClaw managed browser (PartyRock CDP :18800 / ~/.openclaw/browser/…)
#   - Playwright "Google Chrome for Testing" (form fill) — only if already open
# Never kill the user's daily Chrome profile.
# Launch / Refresh open only the dashboard UI window — never PartyRock CDP or a
# form-fill browser (those start on Start/fill when needed). The UI window now
# runs on Chrome-for-Testing too, but under dashboard_ui_profile, which the
# form-fill teardown paths explicitly exclude.
#
# Refresh (POST /api/restart): server writes logs/dashboard_restart.flag,
# may kill non-hold tracked children + legacy partyrock_chrome_profile leftovers,
# exits; this wrapper respawns the server only and does *not* open a new Chrome
# window (the existing window reloads in place). OpenClaw CDP browser is
# left running across Refresh (server stops it only on full quit; fill CfT
# count/kill paths exclude openclaw/user-data — CHR3-003). CHR2-003 / CHR3-001/002:
# fill CfT CAPTCHA/Ready hold windows AND the fill/agent procs are preserved
# on Refresh (finally honors preserve_fill_cft).
# Fallback when no launcher is waiting: server spawns
#   launch_dashboard.sh --restart
#
# Single-instance: mkdir lock at logs/dashboard_launcher.lockdir. A second
# copy (double-click or --restart while primary is alive) exits 0 without
# tearing down Chrome — it focuses the existing dashboard UI via System Events
# (unix id), never `tell application … activate` (that spawns blank CfT windows).
# Dock click on the running applet hits AppleScript `on reopen` →
# launch_dashboard.sh --focus-ui (same focus path, no lock / no teardown).
# Mid-restart EXIT must not kill the dashboard window.
#
# PID files (under logs/ — gitignored):
#   dashboard_server.pid    — server.py
#   dashboard_launcher.pid  — this wrapper (while waiting)
#   dashboard_chrome.pid    — best-effort main Chrome for the dedicated profile
# Never kill unrelated Chrome — only processes whose argv includes one of:
#   --user-data-dir=<repo>/dashboard_ui_profile (or legacy dashboard_chrome_profile)
#   --user-data-dir=<repo>/partyrock_chrome_profile
#   --user-data-dir=~/.openclaw/browser/openclaw/user-data
#   --remote-debugging-port=18800
#   or the "Google Chrome for Testing" binary (Playwright)
# Disable UI lifecycle with JOB_HUNTER_UI_LIFECYCLE=0.
#
# Note: KeepAlive LaunchAgent (com.jobhunter.dashboard-server) fights this
# model — leave it unloaded / KeepAlive=false so a UI quit stays quit.

set -euo pipefail

# CHR2-009: resolve repo root from this script (no hardcoded absolute path).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DASHBOARD_DIR="$ROOT/dashboard"
DASHBOARD_PORT="${JOBHUNTER_DASHBOARD_PORT:-8787}"
URL="http://127.0.0.1:${DASHBOARD_PORT}"
LOG_FILE="$ROOT/logs/dashboard_server.out"
PID_FILE="$ROOT/logs/dashboard_server.pid"
LAUNCHER_PID_FILE="$ROOT/logs/dashboard_launcher.pid"
UI_PID_FILE="$ROOT/logs/dashboard_chrome.pid"
RESTART_FLAG="$ROOT/logs/dashboard_restart.flag"
DASHBOARD_PORT_FILE="$ROOT/logs/dashboard_port"
LOCK_DIR="$ROOT/logs/dashboard_launcher.lockdir"
# Dedicated profile so we can quit this window without touching the user's
# normal browsing profile (same idea as partyrock_chrome_profile/).
# Separate dir from the legacy one: that profile was written by Chrome stable,
# and Chromium refuses to open a profile from a newer build.
UI_PROFILE="$ROOT/dashboard_ui_profile"
LEGACY_UI_PROFILE="$ROOT/dashboard_chrome_profile"
PARTYROCK_PROFILE="$ROOT/partyrock_chrome_profile"
OPENCLAW_BROWSER_PROFILE="${HOME}/.openclaw/browser/openclaw/user-data"
OPENCLAW_CDP_PORT=18800

MODE="${1:-}"
SERVER_PID=""
STARTED_BY_US=0
# When non-zero, EXIT trap skips killing dashboard Chrome (restart path).
KEEP_CHROME=0
# True while Refresh is bringing the server back — EXIT must not kill Chrome
# even after RESTART_FLAG is deleted.
RESTARTING=0
# Only the lock holder may tear down JH browsers on EXIT.
WE_OWN_LOCK=0

mkdir -p "$ROOT/logs" "$UI_PROFILE"

# Prefer repo venv over bare system python (Desktop applet PATH can be thin).
resolve_dashboard_python() {
  local cand

  cand="${JOB_HUNTER_DASHBOARD_PYTHON:-}"
  if [[ -n "$cand" ]] && [[ -x "$cand" ]]; then
    printf '%s\n' "$cand"
    return 0
  fi

  for cand in \
    "$ROOT/.venv/bin/python3" \
    "$ROOT/skyvern_runtime/venv/bin/python3" \
    "$ROOT/skyvern_runtime/venv/bin/python"; do
    if [[ -x "$cand" ]]; then
      printf '%s\n' "$cand"
      return 0
    fi
  done

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  return 1
}

remember_dashboard_port() {
  echo "${DASHBOARD_PORT}" > "$DASHBOARD_PORT_FILE" 2>/dev/null || true
}

restore_dashboard_port_from_file() {
  local saved=""
  [[ -n "${JOBHUNTER_DASHBOARD_PORT:-}" ]] && return 0
  [[ ! -f "$DASHBOARD_PORT_FILE" ]] && return 0
  saved="$(tr -d '[:space:]' < "$DASHBOARD_PORT_FILE" 2>/dev/null || true)"
  [[ "${saved}" =~ ^[0-9]+$ ]] || return 0
  DASHBOARD_PORT="${saved}"
  URL="http://127.0.0.1:${DASHBOARD_PORT}"
}

dashboard_port_candidates() {
  if [[ -n "${JOBHUNTER_DASHBOARD_PORT:-}" ]]; then
    echo "${JOBHUNTER_DASHBOARD_PORT}"
  else
    echo 8787 8788 8789 8790 8791 8792
  fi
}

# Post-reboot the server may be on :8788+ while UI still points at :8787.
sync_serving_dashboard_port() {
  local p
  for p in $(dashboard_port_candidates); do
    if server_up_on_port "$p"; then
      DASHBOARD_PORT="$p"
      URL="http://127.0.0.1:${p}"
      remember_dashboard_port
      return 0
    fi
  done
  return 1
}

listener_pid_on_port() {
  local port="${1:-$DASHBOARD_PORT}"
  /usr/sbin/lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t 2>/dev/null | head -1 || true
}

listener_pid() {
  listener_pid_on_port "$DASHBOARD_PORT"
}

server_up_on_port() {
  local port="${1:-$DASHBOARD_PORT}"
  curl -sf "http://127.0.0.1:${port}" 2>/dev/null | grep -q 'class="ops-shell"'
}

server_up() {
  # Require the real ops shell — a bare TCP listener or proxy 200 is not enough.
  if server_up_on_port "$DASHBOARD_PORT"; then
    return 0
  fi
  sync_serving_dashboard_port
}

is_our_dashboard_server_pid() {
  local pid="${1:-}"
  [[ -z "${pid}" ]] && return 1
  /bin/ps -p "${pid}" -o command= 2>/dev/null | /usr/bin/grep -qF "${DASHBOARD_DIR}/server.py"
}

port_is_bindable() {
  local port="${1:-}"
  [[ -z "${port}" ]] && return 1
  /usr/bin/env python3 - "$port" <<'PY' 2>/dev/null
import socket, sys
port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", port))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

# Post-reboot: Cursor/MCP or a zombie server.py can hold :8787 without serving ops HTML.
# Scan the default port range for a live dashboard, reclaim our stale listener, or pick a free port.
resolve_dashboard_port() {
  local preferred="${JOBHUNTER_DASHBOARD_PORT:-8787}"
  local ports=()
  local p lp

  if [[ -n "${JOBHUNTER_DASHBOARD_PORT:-}" ]]; then
    ports=("${preferred}")
  else
    ports=(8787 8788 8789 8790 8791 8792)
  fi

  for p in "${ports[@]}"; do
    if server_up_on_port "$p"; then
      DASHBOARD_PORT="$p"
      URL="http://127.0.0.1:${p}"
      remember_dashboard_port
      echo "dashboard already serving on :${p}"
      return 0
    fi
  done

  for p in "${ports[@]}"; do
    lp="$(listener_pid_on_port "$p")"
    if [[ -n "${lp}" ]]; then
      if is_our_dashboard_server_pid "$lp"; then
        echo "stale dashboard listener pid=${lp} on :${p} (not serving ops HTML) — stopping"
        kill "$lp" 2>/dev/null || true
        wait_for_port_free "$p"
      else
        echo "warn: port ${p} held by foreign pid=${lp} (not job-hunter dashboard)" >&2
        /bin/ps -p "${lp}" -o command= 2>/dev/null | sed 's/^/  /' >&2 || true
        if [[ -n "${JOBHUNTER_DASHBOARD_PORT:-}" ]]; then
          return 1
        fi
        continue
      fi
    fi
    if port_is_bindable "$p"; then
      DASHBOARD_PORT="$p"
      URL="http://127.0.0.1:${p}"
      remember_dashboard_port
      return 0
    fi
    echo "warn: port ${p} not bindable (hidden listener) — trying next" >&2
    if [[ -n "${JOBHUNTER_DASHBOARD_PORT:-}" ]]; then
      return 1
    fi
  done
  echo "error: no free dashboard port in ${ports[*]}" >&2
  return 1
}

wait_for_port_free() {
  # Used by --restart: old server is shutting down; wait for the active port to clear.
  local port="${1:-$DASHBOARD_PORT}"
  local i
  for i in $(seq 1 60); do
    if [[ -z "$(listener_pid_on_port "$port")" ]]; then
      return 0
    fi
    sleep 0.25
  done
  echo "warn: port ${port} still busy after wait; continuing anyway" >&2
  return 0
}

# Collect unique PIDs matching one or more pgrep -f patterns.
pgrep_f_pids() {
  local pat pid
  for pat in "$@"; do
    /usr/bin/pgrep -f -- "$pat" 2>/dev/null || true
  done | awk 'NF && !seen[$0]++'
}

# SIGTERM then SIGKILL a set of PIDs (newline-separated).
kill_pids_graceful() {
  local label="$1"
  local pids="$2"
  local pid i
  if [[ -z "${pids}" ]]; then
    return 0
  fi
  echo "stopping ${label} pids=${pids//$'\n'/ }"
  for pid in $pids; do
    kill "$pid" 2>/dev/null || true
  done
  for i in $(seq 1 20); do
    local still=""
    for pid in $pids; do
      if kill -0 "$pid" 2>/dev/null; then
        still="${still}${pid}"$'\n'
      fi
    done
    [[ -z "${still}" ]] && return 0
    sleep 0.25
  done
  for pid in $pids; do
    kill -9 "$pid" 2>/dev/null || true
  done
}

# PIDs for the Job Hunter dashboard UI window only:
#   1) dedicated profile (--user-data-dir=…/dashboard_ui_profile, plus the
#      legacy dashboard_chrome_profile so old windows still get torn down)
#   2) legacy/current app windows (--app=http://127.0.0.1:8787) — never a
#      normal browsing tab (those do not carry --app= on the main process).
# Includes Helpers (safe for teardown). Prefer dashboard_chrome_main_pids for focus.
dashboard_chrome_pids() {
  pgrep_f_pids \
    "--user-data-dir=${UI_PROFILE}" \
    "--user-data-dir=${LEGACY_UI_PROFILE}" \
    "--app=${URL}/" \
    "--app=${URL}"
}

# Main CfT/Chromium process for the dashboard UI (exclude Helper/crashpad).
# Used for Dock focus — activating a Helper unix id is a no-op.
dashboard_chrome_main_pids() {
  local line pid
  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    case "${line}" in
      *Helper*|*crashpad*) continue ;;
    esac
    case "${line}" in
      *"MacOS/Google Chrome for Testing"*|*"MacOS/Chromium"*|*"MacOS/Google Chrome"*|*/chrome\ *|*/chrome)
        ;;
      *)
        continue
        ;;
    esac
    pid="${line%% *}"
    if [[ "${pid}" =~ ^[0-9]+$ ]]; then
      printf '%s\n' "${pid}"
    fi
  done < <(
    /usr/bin/pgrep -lf -- "--user-data-dir=${UI_PROFILE}" 2>/dev/null || true
    /usr/bin/pgrep -lf -- "--user-data-dir=${LEGACY_UI_PROFILE}" 2>/dev/null || true
    /usr/bin/pgrep -lf -- "--app=${URL}/" 2>/dev/null || true
    /usr/bin/pgrep -lf -- "--app=${URL}" 2>/dev/null || true
  ) | awk 'NF && !seen[$0]++'
}

# Raise an existing dashboard UI window without spawning Chrome.
# Never `tell application "…" to activate` — Launch Services often opens a
# blank default-profile CfT window when the binary was started with a custom
# --user-data-dir (same bug as fill focus via activate).
focus_dashboard_ui() {
  local pid
  pid="$(dashboard_chrome_main_pids | head -1 || true)"
  if [[ -z "${pid}" ]]; then
    return 1
  fi
  echo "$pid" > "$UI_PID_FILE"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    /usr/bin/osascript -e \
      "tell application \"System Events\" to set frontmost of first process whose unix id is ${pid} to true" \
      >/dev/null 2>&1 || true
  fi
  echo "focused dashboard UI pid=${pid}"
  return 0
}

# Reload an existing dashboard --app window (e.g. after server restart or a prior
# connection-error blank). Uses System Events on the main CfT PID — never
# `tell application "Google Chrome for Testing"` (that spawns blank windows).
reload_dashboard_ui_window() {
  local pid="${1:-}"
  if [[ -z "${pid}" ]]; then
    pid="$(dashboard_chrome_main_pids | head -1 || true)"
  fi
  [[ -z "${pid}" ]] && return 0
  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi
  if ! server_up; then
    echo "warn: skip dashboard UI reload — server not serving ops HTML" >&2
    return 1
  fi
  /usr/bin/osascript -e \
    "tell application \"System Events\"
      set frontmost of first process whose unix id is ${pid} to true
      delay 0.12
      keystroke \"r\" using {command down, shift down}
    end tell" \
    >/dev/null 2>&1 || true
  echo "hard-reloaded dashboard UI pid=${pid}"
  return 0
}

# CHR3-005 / CHR3-006: raise fill Chrome by PID (never UI / PartyRock / daily profile).
# Prefer job_hunter_fill_profiles under repo; fallback legacy CfT --remote-debugging-pipe.
focus_fill_cft() {
  local line pid preferred="" fallback=""
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "focus-fill: non-Darwin — no-op" >&2
    return 1
  fi
  local fill_root="${ROOT}/job_hunter_fill_profiles"
  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    case "${line}" in
      *Helper*|*crashpad*) continue ;;
    esac
    [[ "${line}" != *"Google Chrome"* ]] && continue
    [[ "${line}" == *dashboard_ui_profile* || "${line}" == *"--app=${URL}"* ]] && continue
    [[ "${line}" == *openclaw/user-data* || "${line}" == *"--remote-debugging-port=${OPENCLAW_CDP_PORT}"* ]] && continue
    [[ "${line}" == *"/Applications/Google Chrome.app"* && "${line}" != *job_hunter_fill_profile* && "${line}" != *"${fill_root}"* ]] && continue
    pid="${line%% *}"
    [[ "${pid}" =~ ^[0-9]+$ ]] || continue
    if [[ "${line}" == *"${fill_root}"* ]]; then
      preferred="${pid}"
      break
    fi
    if [[ "${line}" == *--remote-debugging-pipe* ]]; then
      [[ -z "${fallback}" ]] && fallback="${pid}"
    fi
  done < <(/usr/bin/pgrep -lf "Google Chrome" 2>/dev/null || true)
  pid="${preferred:-${fallback}}"
  if [[ -z "${pid}" ]]; then
    echo "focus-fill: no fill Chrome main found (UI/PartyRock/daily profile excluded)" >&2
    return 1
  fi
  /usr/bin/osascript -e \
    "tell application \"System Events\" to set frontmost of first process whose unix id is ${pid} to true" \
    >/dev/null 2>&1 || true
  echo "focused fill Chrome pid=${pid} (job_hunter_fill_profiles; never daily Chrome)"
  return 0
}

# Operator inventory: which CfT process is which (CHR3-005).
print_cft_role_inventory() {
  echo "=== Chrome for Testing roles (one Dock icon; distinguish by argv) ==="
  echo "  UI:     --user-data-dir=…/dashboard_ui_profile  OR  --app=${URL}"
  echo "  PartyRock: --user-data-dir=~/.openclaw/browser/openclaw/user-data  +  :${OPENCLAW_CDP_PORT}"
  echo "  Fill:   Playwright --remote-debugging-pipe  (focus: $0 --focus-fill)"
  echo "  Titles: UI window ≈ 'JOB HUNT · OPS'; PartyRock ≈ app title; Fill ≈ job URL title"
  echo "  Never: tell application \"Google Chrome for Testing\" to activate"
}

# Drop stale Chromium singleton files when no live UI process holds the profile.
# A leftover SingletonLock after a crash makes the next launch hand off to a
# dead owner and can surface as a blank window / silent no-op.
clear_stale_ui_profile_locks() {
  local profile="$1"
  local pids
  pids="$(/usr/bin/pgrep -f -- "--user-data-dir=${profile}" 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    return 0
  fi
  rm -f \
    "${profile}/SingletonLock" \
    "${profile}/SingletonSocket" \
    "${profile}/SingletonCookie" \
    2>/dev/null || true
}

kill_dashboard_chrome() {
  kill_pids_graceful "dashboard UI browser" "$(dashboard_chrome_pids)"
  rm -f "$UI_PID_FILE" 2>/dev/null || true
}

# Legacy partyrock_chrome_profile leftovers (opener retired — PartyRock is
# OpenClaw CfT via ./open_partyrock.sh / chrome_for_testing.py). Still tear
# down any leftover windows and clear stale Singleton* when idle.
partyrock_chrome_pids() {
  pgrep_f_pids "--user-data-dir=${PARTYROCK_PROFILE}"
}

kill_partyrock_chrome() {
  kill_pids_graceful "PartyRock Chrome" "$(partyrock_chrome_pids)"
  # CHR-005: if nothing holds the legacy profile, drop stale singleton locks.
  if [[ -z "$(partyrock_chrome_pids)" ]]; then
    clear_stale_ui_profile_locks "$PARTYROCK_PROFILE"
  fi
}

# OpenClaw managed browser — PartyRock tailor CDP (tailor_resume.py :18800).
openclaw_browser_pids() {
  pgrep_f_pids \
    "--user-data-dir=${OPENCLAW_BROWSER_PROFILE}" \
    "--remote-debugging-port=${OPENCLAW_CDP_PORT}"
}

kill_openclaw_browser() {
  # Prefer the CLI; fall back to argv matching.
  if [[ -x /opt/homebrew/bin/openclaw ]]; then
    /opt/homebrew/bin/openclaw browser stop >/dev/null 2>&1 || true
  elif command -v openclaw >/dev/null 2>&1; then
    openclaw browser stop >/dev/null 2>&1 || true
  fi
  kill_pids_graceful "OpenClaw/PartyRock browser" "$(openclaw_browser_pids)"
}

# Playwright / fast_fill headed browser (never daily Google Chrome).
# Exclude dashboard UI (kill_dashboard_chrome) and OpenClaw PartyRock CDP
# (kill_openclaw_browser) — same CfT binary, not fill (CHR3-003).
chrome_for_testing_pids() {
  # Main binary only — Helpers die with the main process.
  /usr/bin/pgrep -lf "Google Chrome for Testing" 2>/dev/null \
    | grep -v Helper \
    | grep -v crashpad \
    | grep -E 'MacOS/Google Chrome for Testing|/chrome( |$)' \
    | grep -vF -- "--user-data-dir=${UI_PROFILE}" \
    | grep -vF -- "--app=${URL}" \
    | grep -vF -- "--user-data-dir=${OPENCLAW_BROWSER_PROFILE}" \
    | grep -vF -- "--remote-debugging-port=${OPENCLAW_CDP_PORT}" \
    | grep -v openclaw/user-data \
    | awk '{print $1}' \
    | awk 'NF && !seen[$0]++' \
    || true
}

kill_chrome_for_testing() {
  kill_pids_graceful "Chrome-for-Testing" "$(chrome_for_testing_pids)"
}

# Full quit teardown for JH browsers (dashboard Chrome optional).
kill_jh_associated_browsers() {
  local include_dashboard="${1:-1}"
  kill_chrome_for_testing
  kill_partyrock_chrome
  kill_openclaw_browser
  if [[ "${include_dashboard}" -eq 1 ]]; then
    kill_dashboard_chrome
  fi
}

request_server_shutdown() {
  # Best-effort graceful stack teardown (kills tracked discovery/fill trees
  # + JH form-fill / PartyRock browsers inside server.py).
  curl -s -o /dev/null -X POST "$URL/api/shutdown" \
    -H "Content-Type: application/json" \
    -d '{"force":true,"client_id":"launcher-signal"}' \
    --max-time 3 2>/dev/null || true
}

# Which browser binary hosts the dashboard --app window.
#
# It must NOT be /Applications/Google Chrome.app. Launching that bundle with a
# custom --user-data-dir registers it as *the* running instance of
# com.google.Chrome, so a later Dock/Spotlight "Google Chrome" only activates
# the already-running dashboard process — the user gets a signed-out, empty
# profile instead of their daily one. Chrome for Testing / Chromium ship their
# own bundle ids (com.google.chrome.for.testing / org.chromium.Chromium), so
# Google Chrome stays free for the default profile.
#
# Override with JOB_HUNTER_UI_BROWSER=/path/to/binary.
resolve_ui_browser() {
  local cand root arch

  cand="${JOB_HUNTER_UI_BROWSER:-}"
  if [[ -n "$cand" ]] && [[ -x "$cand" ]]; then
    printf '%s\n' "$cand"
    return 0
  fi

  # Chrome for Testing from the Playwright cache (same binary fast_fill uses).
  for root in "${PLAYWRIGHT_BROWSERS_PATH:-}" \
    "$HOME/Library/Caches/ms-playwright" \
    "$HOME/.cache/ms-playwright"; do
    [[ -n "$root" ]] && [[ -d "$root" ]] || continue
    for arch in arm64 x64; do
      while IFS= read -r cand; do
        if [[ -n "$cand" ]] && [[ -x "$cand" ]]; then
          printf '%s\n' "$cand"
          return 0
        fi
      done < <(/bin/ls -td \
        "$root"/chromium-*/"chrome-mac-${arch}"/"Google Chrome for Testing.app"/Contents/MacOS/"Google Chrome for Testing" \
        2>/dev/null || true)
    done
  done

  for cand in \
    "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
    if [[ -x "$cand" ]]; then
      printf '%s\n' "$cand"
      return 0
    fi
  done

  return 1
}

open_dashboard_ui() {
  # Single-instance: if the dedicated-profile --app window is alive, focus it
  # and return. Never re-exec the binary against the same user-data-dir — that
  # hits Chromium's singleton handoff and often opens a blank extra window.
  # Refresh relies on the same reuse path (JS reloads in place). If the tab
  # previously loaded while :8787 was down, CfT --app= shows a blank/dark error
  # shell — hard-reload after focus so a live server paints the ops UI.
  # app.js ignores beforeunload shutdown during Cmd+Shift+R (Desktop focus path).
  if focus_dashboard_ui; then
    if server_up; then
      reload_dashboard_ui_window "$(cat "$UI_PID_FILE" 2>/dev/null || true)"
    fi
    return 0
  fi

  clear_stale_ui_profile_locks "$UI_PROFILE"
  clear_stale_ui_profile_locks "$LEGACY_UI_PROFILE"

  local ui_bin
  ui_bin="$(resolve_ui_browser || true)"

  # CHR2-007: never fall back to Google Chrome.app — that hijacks Dock/Spotlight
  # daily Chrome (com.google.Chrome). Prefer CfT/Chromium only; fail loud.
  if [[ -z "${ui_bin}" ]] || [[ ! -x "${ui_bin}" ]]; then
    echo "warn: no Chrome-for-Testing/Chromium found for dashboard UI." >&2
    echo "warn: falling back to Google Chrome tab at ${URL}" >&2
    echo "Fix: python3 -m playwright install chromium" >&2
    echo "Or:  JOB_HUNTER_UI_BROWSER=/path/to/Chrome-for-Testing-or-Chromium" >&2
    if /usr/bin/open -a "Google Chrome" "${URL}/?jh_boot=$(date +%s)" >/dev/null 2>&1; then
      return 0
    fi
    /usr/bin/open "${URL}/?jh_boot=$(date +%s)" >/dev/null 2>&1 || true
    return 0
  fi

  # Direct binary + dedicated user-data-dir: separate process tree we can
  # tear down without touching the daily-driver Chrome profile.
  # --disable-infobars removes the yellow "Chrome for Testing … is only for
  # automated testing" banner from the top of the dashboard. Measured on CfT
  # 149: it reclaims the full 56px bar, while --test-type alone does not
  # suppress this particular infobar, so we do not pay its side effects.
  # Teardown SIGKILLs this window, so also suppress the "Chrome didn't shut
  # down correctly" restore bubble. All scoped to $UI_PROFILE.
  # Do not use `open -a` / AppleScript activate — those spawn blank CfT windows.
  "${ui_bin}" \
    --user-data-dir="$UI_PROFILE" \
    --no-first-run \
    --no-default-browser-check \
    --disable-infobars \
    --hide-crash-restore-bubble \
    --disable-session-crashed-bubble \
    --app="${URL}/?jh_boot=$(date +%s)" \
    >/dev/null 2>&1 &
  # The browser may fork; prefer pgrep over $! for the stable pid file.
  local existing _
  for _ in $(seq 1 40); do
    existing="$(dashboard_chrome_main_pids | head -1 || true)"
    if [[ -n "${existing}" ]]; then
      echo "$existing" > "$UI_PID_FILE"
      echo "opened dashboard UI pid=$existing via ${ui_bin}"
      focus_dashboard_ui || true
      return 0
    fi
    sleep 0.25
  done
  echo "warn: dashboard UI did not appear with expected profile — opening Chrome tab" >&2
  /usr/bin/open -a "Google Chrome" "${URL}/?jh_boot=$(date +%s)" >/dev/null 2>&1 \
    || /usr/bin/open "${URL}/?jh_boot=$(date +%s)" >/dev/null 2>&1 \
    || true
  return 0
}

release_launcher_lock() {
  if [[ "${WE_OWN_LOCK}" -ne 1 ]]; then
    return 0
  fi
  local holder
  holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [[ "${holder}" == "$$" ]]; then
    rm -rf "$LOCK_DIR" 2>/dev/null || true
  fi
  WE_OWN_LOCK=0
}

acquire_launcher_lock() {
  # Atomic mkdir lock (macOS has no flock(1) by default).
  local i stale_pid
  for i in $(seq 1 40); do
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      echo $$ > "$LOCK_DIR/pid"
      WE_OWN_LOCK=1
      echo $$ > "$LAUNCHER_PID_FILE"
      return 0
    fi
    stale_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [[ -n "${stale_pid}" ]] && ! kill -0 "${stale_pid}" 2>/dev/null; then
      echo "stale launcher lock (pid=${stale_pid}) — reclaiming"
      rm -rf "$LOCK_DIR" 2>/dev/null || true
      continue
    fi
    if [[ -n "${stale_pid}" ]] && kill -0 "${stale_pid}" 2>/dev/null; then
      if ! /bin/ps -p "${stale_pid}" -o command= 2>/dev/null | /usr/bin/grep -q "launch_dashboard.sh"; then
        echo "stale launcher lock (pid=${stale_pid} not launch_dashboard) — reclaiming"
        rm -rf "$LOCK_DIR" 2>/dev/null || true
        continue
      fi
    fi
    # Another live launcher owns the stack.
    if [[ "$MODE" == "--restart" ]]; then
      echo "another launcher is live (pid=${stale_pid:-unknown}) — leaving restart to it"
      exit 0
    fi
    # Double-click Desktop icon while already running: focus existing UI (or
    # recreate if the window died), exit quietly so the applet shows no dialog.
    if server_up; then
      echo "dashboard already launched by pid=${stale_pid:-unknown} — focusing UI"
      open_dashboard_ui
      exit 0
    fi
    sleep 0.25
  done
  restore_dashboard_port_from_file
  if sync_serving_dashboard_port; then
    echo "dashboard already serving on ${URL} — focusing UI (lock held by pid=${stale_pid:-unknown})"
    open_dashboard_ui
    exit 0
  fi
  echo "could not acquire launcher lock; see $LOCK_DIR" >&2
  exit 1
}

start_dashboard_server() {
  # Returns 0 when the dashboard port is serving; 1 on failure (caller may retry).
  STARTED_BY_US=0
  SERVER_PID=""

  if ! resolve_dashboard_port; then
    echo "dashboard port resolution failed; see logs above" >&2
    return 1
  fi

  if server_up; then
    SERVER_PID="$(listener_pid)"
    echo "dashboard already running (pid=${SERVER_PID:-unknown}) on :${DASHBOARD_PORT}"
    return 0
  fi

  cd "$ROOT" || return 1
  local py_bin
  py_bin="$(resolve_dashboard_python || true)"
  if [[ -z "${py_bin}" ]] || [[ ! -x "${py_bin}" ]]; then
    echo "error: no python3 found (.venv, skyvern_runtime/venv, or PATH)" >&2
    return 1
  fi
  # No nohup: if the desktop applet / this wrapper is force-quit, the
  # background server should not quietly survive forever. Explicit UI quit
  # (header × / last window close / Cmd+Q → /api/shutdown) is the primary
  # stop path; idle heartbeat stall does not shut the server down.
  /usr/bin/env JOBHUNTER_DASHBOARD_PORT="${DASHBOARD_PORT}" \
    "${py_bin}" "$DASHBOARD_DIR/server.py" >> "$LOG_FILE" 2>&1 &
  SERVER_PID=$!
  STARTED_BY_US=1
  disown "$SERVER_PID" 2>/dev/null || true
  echo "$SERVER_PID" > "$PID_FILE"
  echo "started dashboard server pid=$SERVER_PID on :${DASHBOARD_PORT}"

  local _
  for _ in $(seq 1 40); do
    if server_up; then
      return 0
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      wait "$SERVER_PID" 2>/dev/null || true
      echo "dashboard server exited before becoming ready; see $LOG_FILE" >&2
      # Post-reboot: port race or stale listener — re-resolve and let caller retry.
      if sync_serving_dashboard_port; then
        SERVER_PID="$(listener_pid)"
        echo "recoverable: ops shell up on :${DASHBOARD_PORT} after early exit"
        return 0
      fi
      wait_for_port_free "$DASHBOARD_PORT"
      resolve_dashboard_port || true
      return 1
    fi
    sleep 0.25
  done
  if ! server_up; then
    echo "dashboard failed to become ready on $URL; see $LOG_FILE" >&2
    kill "$SERVER_PID" 2>/dev/null || true
    return 1
  fi
  return 0
}

wait_for_server_exit() {
  local wait_pid="${SERVER_PID}"
  if [[ -z "$wait_pid" ]] && [[ -f "$PID_FILE" ]]; then
    wait_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  fi
  if [[ -z "$wait_pid" ]]; then
    wait_pid="$(listener_pid)"
  fi

  if [[ -n "$wait_pid" ]]; then
    echo "waiting on dashboard pid=$wait_pid (Quit/close window to stop; Refresh keeps this Chrome window; idle does not quit)"
    while kill -0 "$wait_pid" 2>/dev/null; do
      sleep 1
    done
    echo "dashboard pid=$wait_pid exited"
    wait "$wait_pid" 2>/dev/null || true
  else
    echo "warn: could not resolve dashboard pid; not waiting" >&2
  fi
}

cleanup_on_exit() {
  rm -f "$LAUNCHER_PID_FILE" 2>/dev/null || true
  # Final quit (not mid-restart / not a non-owner): tear down JH browsers.
  if [[ "${WE_OWN_LOCK}" -eq 1 ]] \
    && [[ "${KEEP_CHROME}" -eq 0 ]] \
    && [[ "${RESTARTING}" -eq 0 ]] \
    && [[ ! -f "$RESTART_FLAG" ]]; then
    kill_jh_associated_browsers 1
  fi
  release_launcher_lock
}

on_signal() {
  echo "launcher signal — shutting down dashboard stack" >&2
  KEEP_CHROME=0
  RESTARTING=0
  rm -f "$RESTART_FLAG" 2>/dev/null || true
  request_server_shutdown
  sleep 0.5
  if [[ -n "${SERVER_PID}" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
  fi
  # Also stop whatever is still listening on :8787 if we started it / own wait.
  local lp
  lp="$(listener_pid)"
  if [[ -n "$lp" ]]; then
    kill "$lp" 2>/dev/null || true
  fi
  if [[ "${WE_OWN_LOCK}" -eq 1 ]]; then
    kill_jh_associated_browsers 1
  fi
  rm -f "$LAUNCHER_PID_FILE" 2>/dev/null || true
  release_launcher_lock
  # EXIT trap still runs; KEEP_CHROME/RESTARTING already clear — skip double kill
  # by marking we already cleaned browsers via KEEP_CHROME stay 0 + lock released.
  WE_OWN_LOCK=0
  exit 0
}

# Traps before lock: cleanup no-ops until WE_OWN_LOCK=1.
trap cleanup_on_exit EXIT
trap on_signal INT TERM HUP

# Dock icon click while applet is already running (AppleScript `on reopen`):
# focus/create the UI only — never take the launcher lock or tear down browsers.
# If the server died but the Dock applet is still alive, upgrade to a full launch
# so double-click always works (do not fail silently with exit 1).
if [[ "$MODE" == "--focus-ui" ]]; then
  restore_dashboard_port_from_file
  if server_up; then
    open_dashboard_ui
    exit 0
  fi
  # Server down: a primary launcher may still be exiting after crash/quit.
  # Wait for lock release or server recovery before full launch.
  echo "focus-ui: server not up — waiting for launcher lock, then full launch" >&2
  for _ in $(seq 1 40); do
    if server_up; then
      open_dashboard_ui
      exit 0
    fi
    stale_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [[ -z "${stale_pid}" ]] || ! kill -0 "${stale_pid}" 2>/dev/null; then
      break
    fi
    if [[ -n "${stale_pid}" ]] && ! /bin/ps -p "${stale_pid}" -o command= 2>/dev/null | /usr/bin/grep -q "launch_dashboard.sh"; then
      rm -rf "$LOCK_DIR" 2>/dev/null || true
      break
    fi
    sleep 0.25
  done
  exec "$0"
fi

# CHR3-005: raise fill CfT by PID (operator helper; never UI / PartyRock).
if [[ "$MODE" == "--focus-fill" ]]; then
  print_cft_role_inventory
  focus_fill_cft
  exit $?
fi

# CHR3-005: print which process is which (no focus).
if [[ "$MODE" == "--cft-roles" ]]; then
  print_cft_role_inventory
  exit 0
fi

restore_dashboard_port_from_file
acquire_launcher_lock

if [[ "$MODE" == "--restart" ]]; then
  echo "launch_dashboard.sh --restart: waiting for old server to release :${DASHBOARD_PORT}"
  RESTARTING=1
  wait_for_port_free
  rm -f "$RESTART_FLAG" 2>/dev/null || true
fi

# Restart loop: Refresh writes RESTART_FLAG then exits the server; we respawn
# the server and keep the existing Chrome window (JS reloads in place).
while true; do
  local_start_attempts=0
  while true; do
    if start_dashboard_server; then
      RESTARTING=0
      break
    fi
    local_start_attempts=$((local_start_attempts + 1))
    if [[ "${local_start_attempts}" -lt 5 ]]; then
      echo "warn: dashboard start failed (attempt ${local_start_attempts}/5); retrying…" >&2
      sleep 0.5
      wait_for_port_free
      continue
    fi
    echo "dashboard server failed to start; giving up" >&2
    # Race / foreign listener: if ops HTML is already served, treat as recoverable.
    if sync_serving_dashboard_port; then
      echo "recoverable: ops shell is up on ${URL} despite start errors — continuing"
      open_dashboard_ui
      exit 0
    fi
    exit 1
  done

  open_dashboard_ui
  wait_for_server_exit

  if [[ -f "$RESTART_FLAG" ]]; then
    echo "restart flag present — bringing server back up (keeping Chrome window)"
    RESTARTING=1
    KEEP_CHROME=1
    rm -f "$RESTART_FLAG" 2>/dev/null || true
    wait_for_port_free
    KEEP_CHROME=0
    # RESTARTING stays 1 until start_dashboard_server succeeds above.
    continue
  fi
  break
done

exit 0
