#!/bin/bash
# One-click launcher: starts the dashboard server if it isn't already
# running, then opens it in the default browser. server.py has no
# external deps (stdlib http.server only), so the plain system python3
# is correct here - no venv needed for this specific script.

DASHBOARD_DIR="/Users/job/.openclaw/workspace/job-hunter/dashboard"
URL="http://127.0.0.1:8787"
LOG_FILE="/Users/job/.openclaw/workspace/job-hunter/logs/dashboard_server.out"

if ! curl -s -o /dev/null -w "%{http_code}" "$URL" | grep -q "200"; then
  cd "$DASHBOARD_DIR" || exit 1
  nohup python3 server.py > "$LOG_FILE" 2>&1 &
  # Poll briefly instead of a fixed sleep - most starts finish in ~3-5s.
  for i in $(seq 1 20); do
    if curl -s -o /dev/null -w "%{http_code}" "$URL" | grep -q "200"; then
      break
    fi
    sleep 0.5
  done
fi

open "$URL"
