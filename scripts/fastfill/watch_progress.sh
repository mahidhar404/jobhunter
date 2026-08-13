#!/usr/bin/env bash
# Print the latest fastfill PROGRESS.md (source of "what's happening").
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
MD="$HERE/PROGRESS.md"
if [[ ! -f "$MD" ]]; then
  echo "No PROGRESS.md yet. Run:"
  echo "  skyvern_runtime/venv/bin/python scripts/fastfill/progress_monitor.py"
  exit 1
fi
cat "$MD"
