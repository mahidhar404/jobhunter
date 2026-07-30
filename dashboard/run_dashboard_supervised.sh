#!/bin/bash
# Wrapper for launchd to supervise the job-hunter dashboard server. Mirrors
# skyvern_runtime/run_server_supervised.sh - this process was only ever
# started manually (nohup+disown), which survives a closed terminal but
# does nothing if the process itself dies, and it was found dead with no
# one noticing for over a day.
cd /Users/job/.openclaw/workspace/job-hunter
exec /opt/homebrew/bin/python3 dashboard/server.py
