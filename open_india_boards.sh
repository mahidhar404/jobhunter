#!/bin/bash
# Open headed Chrome with the shared India-boards profile so you can:
#   1) Browse Naukri (confirm search pages load — no Access Denied)
#   2) Sign into Hirist (required for job feed API)
# Scrapers reuse this same profile (india_boards_chrome_profile/).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT/scripts${PYTHONPATH:+:$PYTHONPATH}"
PROFILE="$ROOT/india_boards_chrome_profile"
mkdir -p "$PROFILE"

exec "$ROOT/.venv/bin/python3" - <<'PY'
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent if '__file__' in dir() else Path.cwd()
sys.path.insert(0, str(Path.cwd() / "scripts"))
from india_boards_browser import launch_india_boards_context
import time
pw, ctx, page = launch_india_boards_context(headless=False)
page.goto("https://www.naukri.com/data-scientist-jobs-in-bangalore", wait_until="domcontentloaded")
page2 = ctx.new_page()
page2.goto("https://www.hirist.tech/", wait_until="domcontentloaded")
print("India boards Chrome is open.")
print("  - Naukri: confirm jobs list loads")
print("  - Hirist: Sign in / Sign up once, then leave this window")
print("Press Ctrl+C here when done (cookies stay in india_boards_chrome_profile/).")
try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    pass
finally:
    ctx.close()
    pw.stop()
PY
