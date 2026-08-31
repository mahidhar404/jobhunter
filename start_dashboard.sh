#!/bin/bash
# Durable local dashboard start (India + Worldwide), supervised by launchd.
#
# The server runs as a LaunchAgent (com.jobhunter.dashboard) with
# KeepAlive: if the process dies for ANY reason (crash, stray pkill, agent
# sandbox reaping, SIGKILL) launchd respawns it within ~5s, as long as
# logs/dashboard_keepalive.flag exists. UI Quit deletes that flag first, so
# an explicit quit stays quit. Browser refresh never kills the server
# (JOB_HUNTER_UI_LIFECYCLE=0 -> /api/restart is a soft reload).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
mkdir -p logs

LABEL="com.jobhunter.dashboard"
PLIST_TEMPLATE="$ROOT/dashboard/${LABEL}.plist.template"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
# Flag must live outside TCC-protected folders (Desktop/Documents): launchd
# itself stats this path for KeepAlive and fails with EX_CONFIG otherwise.
FLAG="$HOME/Library/Application Support/jobhunter/dashboard_keepalive.flag"
GUI_DOMAIN="gui/$(id -u)"

PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then PY="$(command -v python3)"; fi

# Clear legacy launcher state (Dock applet from launch_dashboard.sh) so old
# PID files can't confuse the Refresh path.
rm -f jobs.json.lock
rm -f logs/dashboard_restart.flag logs/dashboard_launcher.pid
rm -rf logs/dashboard_launcher.lockdir

# Merge lane defaults without wiping other settings (source_days, last success).
"$PY" - <<'PYEOF'
import json
from pathlib import Path
Path("logs").mkdir(exist_ok=True)
p = Path("logs/discovery_settings.json")
cur = {}
if p.is_file():
    try:
        cur = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(cur, dict):
            cur = {}
    except Exception:
        cur = {}
# Defaults only — never clobber a lane the user picked in the UI. This used
# to hard-set both lanes on every launch, so "worldwide only" silently
# reverted to "India + worldwide" the next time the dashboard started.
cur.setdefault("discover_india", True)
cur.setdefault("discover_worldwide", True)
cur.pop("discover_us", None)
cur.pop("builtin_days_since_updated", None)
cur.setdefault("source_days", {})
p.write_text(json.dumps(cur, indent=2) + "\n", encoding="utf-8")
print("discovery lanes:", {k: cur[k] for k in ("discover_india", "discover_worldwide")})
PYEOF

# Render the LaunchAgent plist for this checkout + python.
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__ROOT__|$ROOT|g" -e "s|__PYTHON__|$PY|g" -e "s|__FLAG__|$FLAG|g" \
  "$PLIST_TEMPLATE" > "$PLIST_DST"

# Arm keepalive BEFORE (re)loading the agent.
mkdir -p "$(dirname "$FLAG")"
touch "$FLAG"

echo "===== LAUNCHD START $(date -Iseconds) =====" >> logs/dashboard_server.out

# Reload the agent: bootout is a no-op if not loaded. Stop any unmanaged
# server first (old nohup runs) so the port is free for the managed one.
launchctl bootout "$GUI_DOMAIN/$LABEL" 2>/dev/null || true
pkill -f 'dashboard/server.py' 2>/dev/null || true
sleep 1
launchctl bootstrap "$GUI_DOMAIN" "$PLIST_DST"
launchctl kickstart "$GUI_DOMAIN/$LABEL" 2>/dev/null || true

# Wait until HTTP answers and stays up.
ok=0
for i in $(seq 1 20); do
  sleep 1
  if curl -sf -m 2 -o /dev/null http://127.0.0.1:8787/; then
    ok=1
    break
  fi
done
if [[ "$ok" -ne 1 ]]; then
  echo "ERROR: server failed to answer on :8787 — see logs/dashboard_server.out" >&2
  exit 1
fi
sleep 2
if ! curl -sf -m 2 -o /dev/null http://127.0.0.1:8787/; then
  echo "ERROR: server died right after start — see logs/dashboard_server.out" >&2
  exit 1
fi

echo "dashboard up: http://127.0.0.1:8787/  (launchd-supervised: $LABEL, auto-respawn on crash)"
echo "to stop for good: use the Quit button in the UI, or: launchctl bootout $GUI_DOMAIN/$LABEL && rm -f $FLAG"
# Prefer a normal browser tab (not ?desktop=1) so pagehide won't try to quit.
open "http://127.0.0.1:8787/" 2>/dev/null || true
