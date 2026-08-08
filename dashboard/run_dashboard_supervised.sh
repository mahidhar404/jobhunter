#!/bin/bash
# Optional launchd wrapper for the job-hunter dashboard server.
# Prefer dashboard/launch_dashboard.sh (Desktop app) — that path ties the
# server lifetime to open UI tabs via heartbeat/shutdown. Do NOT use
# KeepAlive=true with UI lifecycle: launchd would resurrect the server after
# the dashboard window closes.
# CHR2-009: resolve repo from this script (no hardcoded absolute path).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec /opt/homebrew/bin/python3 dashboard/server.py
