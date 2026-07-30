#!/bin/bash
# Wrapper for launchd to supervise. Sources env/secrets then execs the server
# in the foreground (not backgrounded) so launchd's KeepAlive can actually see
# it exit and restart it - this is the piece that was missing before: Postgres
# is supervised by brew services (launchd) and recovers on its own; this
# server was only ever started via nohup+disown, which survives a closed
# terminal but does nothing if the process itself dies (e.g. it lost its DB
# connection once already, from the Mac sleeping, and stayed dead until
# manually restarted).
set -a
source /Users/job/.openclaw/workspace/job-hunter/skyvern_runtime/.env
source /Users/job/.openclaw/workspace/job-hunter/skyvern_runtime/.secrets.env
set +a
cd /Users/job/.openclaw/workspace/job-hunter/skyvern_runtime
exec ./venv/bin/skyvern run server
