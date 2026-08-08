#!/bin/bash
# Opens PartyRock in the OpenClaw managed browser — same profile + CDP :18800
# as scripts/tailor_resume.py. Shared Google login with automated tailor.
#
# PR3-002 CANONICAL human login path. Prefer this over raw
# `openclaw browser start/open` (may still pick Google Chrome.app until pinned)
# and over any IDE/browser tool (no shared cookies → sign-in wall).
#
# NEVER uses /Applications/Google Chrome.app (daily driver). Launching stable
# Chrome with a custom --user-data-dir registers as com.google.Chrome and
# hijacks Dock/Spotlight "Google Chrome" (CHR-006 / CHR2-001 / PR-001).
# OpenClaw auto-detect prefers Google Chrome.app — we force Chrome for Testing
# (or Chromium) via scripts/chrome_for_testing.py before start.
#
# Profile: ~/.openclaw/browser/openclaw/user-data
# CDP:     http://127.0.0.1:18800
# Binary:  Playwright "Google Chrome for Testing" (not daily Chrome)
#
# Dashboard quit also tears down this OpenClaw browser (launch_dashboard.sh /
# server.py). Legacy repo path partyrock_chrome_profile/ is no longer opened
# here (left on disk only for teardown of any leftover old windows).
#
# URL comes from partyrock.json via scripts/partyrock_config.py:
#   Test Mode (default): Ultron-Resume-v3-Testing
#   Real Mode:           Ultron-Resume-v3
#
# Usage:
#   ./open_partyrock.sh                        # Testing app (safe default)
#   ./open_partyrock.sh --test                 # Testing app
#   ./open_partyrock.sh --real                 # Real app
#   PARTYROCK_TEST_MODE=0 ./open_partyrock.sh  # Real app via env (not TEST_MODE)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
OPENCLAW_PROFILE="${HOME}/.openclaw/browser/openclaw/user-data"
OPENCLAW_CDP_PORT=18800
PY="${ROOT}/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY=python3
fi

ARGS=()
case "${1:-}" in
  --real|-r|real) ARGS=(--real) ;;
  --test|-t|test) ARGS=(--test-mode) ;;
  "") ;;
  *)
    echo "usage: $0 [--test|--real]" >&2
    exit 2
    ;;
esac

# bash 3.2 (macOS default) errors on "${ARGS[@]}" with set -u when empty;
# ${ARGS[@]+...} guard keeps no-args (test mode) working.
URL="$("$PY" "$ROOT/scripts/partyrock_config.py" ${ARGS[@]+"${ARGS[@]}"})"
echo "Opening PartyRock (OpenClaw CDP :${OPENCLAW_CDP_PORT}): $URL"
echo "CHR3-005 role: PartyRock CfT — profile=${OPENCLAW_PROFILE} port=${OPENCLAW_CDP_PORT}"
echo "  (same Dock icon as UI/fill; do not use Dock activate — use this script)"
echo "  UI focus:  dashboard/launch_dashboard.sh --focus-ui"
echo "  Fill focus: dashboard/launch_dashboard.sh --focus-fill"

# Drop stale Chromium singleton files when no live process holds the profile
# (CHR-005). A leftover SingletonLock after a crash makes the next start hand
# off to a dead owner.
clear_stale_profile_locks() {
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

clear_stale_profile_locks "$OPENCLAW_PROFILE"

# CHR2-001: pin OpenClaw to Chrome for Testing and bring CDP up (never daily Chrome).
if ! "$PY" "$ROOT/scripts/chrome_for_testing.py" --ensure-partyrock --open-url "$URL"; then
  echo "error: could not start PartyRock on Chrome for Testing / OpenClaw CDP." >&2
  echo "Install CfT: python3 -m playwright install chromium" >&2
  echo "Or set JOB_HUNTER_PARTYROCK_BROWSER=/path/to/Chrome-for-Testing-or-Chromium." >&2
  echo "Do NOT use Google Chrome.app — it hijacks the daily Dock icon." >&2
  exit 1
fi

echo "PartyRock tab opened in OpenClaw/CfT browser (shared login with tailor)."
exit 0
