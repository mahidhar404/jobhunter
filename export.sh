#!/usr/bin/env bash
# export.sh — produce a shareable, PII-free copy of this repo.
#
# What it does (READ-ONLY on the source; it never moves/edits your files):
#   1. rsync the repo into a clean build dir, EXCLUDING every PII/secret/artifact.
#   2. Drop in dummy fixtures as the starting profile/credentials so the copy runs.
#   3. VERIFY no known-secret filename or obvious secret slipped through; fail loudly if so.
#
# Usage:
#   ./export.sh [OUTPUT_DIR]        # default: ./export_build
#   ./export.sh --zip [OUTPUT_DIR]  # also produce OUTPUT_DIR.zip
#
# It is intentionally conservative: it copies an ALLOW list is hard, so instead it
# copies everything and applies a DENY list that mirrors .gitignore + extra safety.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAKE_ZIP=0
if [[ "${1:-}" == "--zip" ]]; then MAKE_ZIP=1; shift; fi
OUT="${1:-$SRC/export_build}"

echo "==> Exporting PII-free copy"
echo "    source: $SRC"
echo "    output: $OUT"

# --- DENY list: never copy these (real PII / secrets / big regenerable artifacts) ---
EXCLUDES=(
  # secrets / PII
  "credentials.json" "web_keys.json" "profile.json" "profile.json.bak*"
  "option_mappings.json" "application_tracker.xlsx" "application_tracker.xlsx.lock"
  ".env" ".env.*" "**/.secrets.env" "skyvern_runtime/.env" "skyvern_runtime/.env.*"
  # personal data / artifacts
  "jobs.json" "jobs.json.bak*" "jobs.json.lock" "blocked_urls.json" "blocked_urls.json.lock"
  "listings/" "logs/" "resumes/"
  # agent-personal memory
  "MEMORY.md" "memory/" "USER.md"
  # the dedicated private folder
  "private/"
  # browser sessions
  "*_chrome_profile/" "*_ui_profile/" "skyvern_runtime/*_profile/"
  "skyvern_runtime/venv/" "skyvern_runtime/pgdata/" "skyvern_runtime/postgres_data/"
  "skyvern_runtime/videos/" "skyvern_runtime/downloads/" "skyvern_runtime/trusted_uploads/"
  "skyvern_runtime/har/" "skyvern_runtime/log/" "skyvern_runtime/temp/"
  "skyvern_runtime/archived_logs/" "skyvern_runtime/real_job_results/" "*.log"
  # envs / caches / os / editor-meta (recipient sets up their own)
  ".venv/" "**/__pycache__/" "*.py[cod]" ".pytest_cache/" ".git/" ".firecrawl/"
  ".cursor/" ".cursorignore"
  ".DS_Store" "**/.DS_Store" "openclaw-workspace-state.json" ".openclaw/"
  # export output itself
  "export_build/" "*.export.zip"
)

RSYNC_ARGS=(-a --delete)
for e in "${EXCLUDES[@]}"; do RSYNC_ARGS+=(--exclude "$e"); done

rm -rf "$OUT"
mkdir -p "$OUT"
rsync "${RSYNC_ARGS[@]}" "$SRC"/ "$OUT"/

# --- Seed runnable dummy data from fixtures so the copy works out of the box ---
if [[ -d "$OUT/fixtures" ]]; then
  cp -f "$OUT/fixtures/profile.example.json"     "$OUT/profile.json"     2>/dev/null || true
  cp -f "$OUT/fixtures/credentials.example.json" "$OUT/credentials.json" 2>/dev/null || true
  cp -f "$OUT/fixtures/web_keys.example.json"    "$OUT/web_keys.json"    2>/dev/null || true
  cp -f "$OUT/fixtures/.env.example"             "$OUT/.env"             2>/dev/null || true
  echo "==> Seeded dummy profile/credentials/web_keys/.env from fixtures/"
fi

# --- Verification: fail if any known-secret filename made it into the export ---
echo "==> Verifying export is PII-free"
LEAK=0
check_absent() {
  # $1 = human label, $2 = find expression
  local hits
  hits="$(cd "$OUT" && eval "$2" 2>/dev/null || true)"
  if [[ -n "$hits" ]]; then
    echo "  !! LEAK ($1):"; echo "$hits" | sed 's/^/     /'; LEAK=1
  fi
}
# A real profile.json is OK ONLY if it equals the dummy fixture (full_name == "Test Dummy").
if [[ -f "$OUT/profile.json" ]] && ! grep -q '"Test Dummy"' "$OUT/profile.json"; then
  echo "  !! LEAK: export/profile.json is not the dummy fixture"; LEAK=1
fi
check_absent "secret backups"      "find . -name 'profile.json.bak*' -o -name 'jobs.json.bak*'"
check_absent "browser profiles"    "find . -type d -name '*_chrome_profile' -o -type d -name '*_ui_profile'"
check_absent "private folder"      "find . -type d -name private"
check_absent "app tracker"         "find . -name 'application_tracker.xlsx'"
check_absent "env secrets"         "find . -name '.env' -not -name '.env.example' -o -name '.secrets.env'"
check_absent "large jobs data"     "find . -name 'jobs.json' -size +100k"

if [[ "$LEAK" -ne 0 ]]; then
  echo "==> EXPORT FAILED: potential PII/secret detected. Nothing shared. Fix the excludes above."
  exit 2
fi

echo "==> OK: no known secrets in $OUT"

if [[ "$MAKE_ZIP" -eq 1 ]]; then
  ZIP="${OUT%/}.zip"
  (cd "$(dirname "$OUT")" && zip -qr "$ZIP" "$(basename "$OUT")")
  echo "==> Wrote $ZIP"
fi

echo "==> Done. Share: $OUT"
