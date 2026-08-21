#!/bin/bash
# Opens LinkedIn login in the dedicated resolve browser — same mechanism as
# PartyRock (CfT + long-lived user-data-dir + CDP), separate profile.
#
# Profile: <job-hunter>/linkedin_resolve_profile  (gitignored)
# CDP:     http://127.0.0.1:18801
# Binary:  Chrome for Testing (never daily Google Chrome.app)
#
# Leave the window open after sign-in — Resolve ATS attaches via CDP.
# Never: PartyRock/OpenClaw profile (:18800), dashboard UI, or fill profiles.
#
# Usage:
#   ./open_linkedin_resolve.sh
#   ./scripts/linkedin_resolve_login.sh   # same
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="${ROOT}/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY=python3
fi

PROFILE="${JOB_HUNTER_LINKEDIN_RESOLVE_PROFILE:-${ROOT}/linkedin_resolve_profile}"
CDP_PORT="${JOB_HUNTER_LINKEDIN_RESOLVE_CDP_PORT:-18801}"

echo "Opening LinkedIn resolve browser (CfT + CDP :${CDP_PORT})"
echo "  profile=${PROFILE}"
echo "  (PartyRock stays on ~/.openclaw/browser/openclaw/user-data :18800 — separate)"
echo "Sign in until feed/home loads, then leave this window open for Resolve ATS."

if ! "$PY" "$ROOT/scripts/linkedin_resolve_profile.py" --login; then
  echo "error: could not start LinkedIn resolve CDP browser." >&2
  echo "Install CfT: python3 -m playwright install chromium" >&2
  exit 1
fi

echo "LinkedIn resolve CDP browser ready (shared login with Resolve ATS)."
exit 0
