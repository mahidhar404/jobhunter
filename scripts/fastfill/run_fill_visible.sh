#!/usr/bin/env bash
# Visible headed fill: opens macOS Terminal.app + headed browser + live [fill-step] stream.
#
# Why: Cursor agent shells write to a hidden backend terminal — users see nothing
# unless they open Terminal (Ctrl+`) manually. This wrapper spawns Terminal.app.
#
# Usage:
#   ./scripts/fastfill/run_fill_visible.sh 'https://jobs.ashbyhq.com/.../application'
#   ./scripts/fastfill/run_fill_visible.sh URL --inline   # current shell only
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
SELF="${ROOT}/scripts/fastfill/run_fill_visible.sh"
URL=""
INLINE=false
for arg in "$@"; do
  if [[ "$arg" == "--inline" ]]; then
    INLINE=true
  elif [[ -z "$URL" && "$arg" != --* ]]; then
    URL="$arg"
  fi
done
if [[ -z "$URL" ]]; then
  echo "usage: run_fill_visible.sh APPLY_URL [--inline]" >&2
  exit 1
fi

PYTHON="${ROOT}/skyvern_runtime/venv/bin/python"
FILL="${ROOT}/scripts/fastfill/fast_fill.py"

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${OUT_DIR:-${ROOT}/skyvern_runtime/real_job_results/fill_live_${STAMP}}"
mkdir -p "$OUT_DIR"

_preflight_headed_cap() {
  local mains
  # Exclude dashboard UI + OpenClaw PartyRock (CHR3-003) — same CfT binary.
  mains="$(
    pgrep -lf "MacOS/Google Chrome for Testing" 2>/dev/null \
      | grep -v Helper \
      | grep -v dashboard_ui_profile \
      | grep -v '--app=http://127.0.0.1:8787' \
      | grep -v openclaw/user-data \
      | grep -v '--remote-debugging-port=18800' \
      || true
  )"
  if [[ -n "$mains" ]]; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║  WARNING: Chrome-for-Testing already running (headed cap = 1)    ║"
    echo "║  Close stale Chrome-for-Testing windows before headed fill, OR:  ║"
    echo "║    export FASTFILL_NO_KILL_CHROME=1                               ║"
    echo "║    export FASTFILL_FORCE_HEADED=1                                ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo "$mains"
    echo ""
  fi
}

_activate_chrome_testing() {
  # Focus an existing fill CfT main by PID. Never `tell application … activate`
  # (Launch Services opens blank default-profile windows). Prefer Playwright
  # fill (--remote-debugging-pipe); never raise UI or PartyRock (CHR3-006).
  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi
  local line pid preferred="" fallback=""
  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    [[ "${line}" == *Helper* || "${line}" == *crashpad* ]] && continue
    [[ "${line}" != *"MacOS/Google Chrome for Testing"* ]] && continue
    [[ "${line}" == *dashboard_ui_profile* || "${line}" == *"--app=http://127.0.0.1:8787"* ]] && continue
    [[ "${line}" == *openclaw/user-data* || "${line}" == *"--remote-debugging-port=18800"* ]] && continue
    pid="${line%% *}"
    [[ "${pid}" =~ ^[0-9]+$ ]] || continue
    if [[ "${line}" == *--remote-debugging-pipe* ]]; then
      preferred="${pid}"
      break
    fi
    [[ -z "${fallback}" ]] && fallback="${pid}"
  done < <(pgrep -lf "Google Chrome for Testing" 2>/dev/null || true)
  pid="${preferred:-${fallback}}"
  if [[ -n "${pid}" ]]; then
    osascript -e "tell application \"System Events\" to set frontmost of first process whose unix id is ${pid} to true" 2>/dev/null && return 0
  fi
  # No name-based fallback — that raises dashboard UI / PartyRock (CHR2-004).
}

_run() {
  export PYTHONUNBUFFERED=1
  export FASTFILL_STEP_LOG_STREAM=1
  _preflight_headed_cap
  echo "════════════════════════════════════════════════════════════════"
  echo " JOB-HUNTER FILL — live [fill-step] log streams below"
  echo " Headed browser (--headed) opens next. Dummy only. Never submits."
  echo " Flash leftovers ON (--flash-leftovers) for salary/clearance/etc."
  echo " Artifacts: ${OUT_DIR}/"
  echo "════════════════════════════════════════════════════════════════"
  # Always --headed here; never downgrade to headless in the inline path.
  # --flash-leftovers: Layer 0/1 first, then DeepSeek for leftovers (dummy facts).
  # Disable: FASTFILL_FLASH_LEFTOVERS=0 ./scripts/fastfill/run_fill_visible.sh URL
  FLASH_ARGS=(--flash-leftovers)
  case "${FASTFILL_FLASH_LEFTOVERS:-1}" in
    0|false|no|off) FLASH_ARGS=() ;;
  esac
  "${PYTHON}" "${FILL}" "$URL" \
    --headed \
    "${FLASH_ARGS[@]}" \
    --refill-passes 2 \
    --hold-seconds 45 \
    --captcha-wait \
    --out "${OUT_DIR}/report.json" 2>&1 | tee "${OUT_DIR}/run.log"
}

if $INLINE; then
  _run
  exit 0
fi

# Escape a string for embedding inside an AppleScript double-quoted literal.
_applescript_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

_open_terminal() {
  # cd to repo root first — Terminal defaults to ~ which breaks relative paths.
  # Never use AppleScript "POSIX path of" on bash strings (turns / into :).
  local terminal_cmd
  terminal_cmd="cd $(printf '%q' "$ROOT") && export OUT_DIR=$(printf '%q' "$OUT_DIR") && exec $(printf '%q' "$SELF") $(printf '%q' "$URL") --inline"
  local escaped
  escaped=$(_applescript_escape "$terminal_cmd")
  osascript \
    -e 'tell application "Terminal" to activate' \
    -e "tell application \"Terminal\" to do script \"${escaped}\""
}

_fill_started() {
  [[ -f "${OUT_DIR}/run.log" ]] || [[ -f "${OUT_DIR}/fill_steps.jsonl" ]]
}

if command -v osascript >/dev/null 2>&1; then
  if _open_terminal; then
    echo "[run_fill_visible] Opened Terminal.app — watch [fill-step] lines there."
    echo "[run_fill_visible] cmd: cd '${ROOT}' && run_fill_visible.sh --inline"
    echo "[run_fill_visible] Artifacts → ${OUT_DIR}/"
    # Watchdog: confirm Terminal actually started the fill.
    for _i in $(seq 1 25); do
      if _fill_started; then
        echo "[run_fill_visible] Terminal fill started (run.log detected)."
        _activate_chrome_testing
        exit 0
      fi
      sleep 1
    done
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║  Terminal.app opened but fill did NOT start within 25s           ║"
    echo "║  Falling back to INLINE in this shell — browser + logs here.     ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo ""
  else
    echo "[run_fill_visible] osascript failed; falling back to inline." >&2
  fi
else
  echo "[run_fill_visible] osascript unavailable; running inline in this shell." >&2
fi

_run
_activate_chrome_testing
