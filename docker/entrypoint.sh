#!/usr/bin/env bash
# entrypoint.sh — prepare the container for the job-hunter dashboard.
#
# Two jobs, both idempotent and safe to re-run on every start:
#   1. Redirect the app's ROOT-relative runtime state (jobs.json, listings/,
#      logs/, resumes/, application_tracker.xlsx, blocked_urls.json) onto a
#      persisted data volume mounted at $JOBHUNTER_DATA_DIR (default /app/data)
#      via symlinks. The code still writes to /app/<name> (paths are hardcoded
#      to the repo root — the private/ migration is deferred), but the bytes
#      land on the volume and survive restarts. No PII is ever baked into the
#      image because these live on the volume, not the build layer.
#   2. Seed DUMMY config (profile/credentials/web_keys/.env) from the shipped
#      fixtures/*.example templates IF the real files are absent, so a fresh
#      `docker compose up` opens the dashboard with no real data. If you
#      bind-mount your own real config files, they already exist and seeding
#      is skipped — your files win.
set -euo pipefail

APP_DIR="${APP_DIR:-/app}"
DATA_DIR="${JOBHUNTER_DATA_DIR:-/app/data}"

cd "$APP_DIR"

# --- 1. Persist runtime state on the data volume via symlinks ---
mkdir -p "$DATA_DIR"

# Directories the app writes into (created on demand by the code otherwise).
for d in listings logs resumes; do
  mkdir -p "$DATA_DIR/$d"
  # Only symlink if the app path isn't a real (bind-mounted) directory already.
  if [ ! -e "$APP_DIR/$d" ] || [ -L "$APP_DIR/$d" ]; then
    ln -sfn "$DATA_DIR/$d" "$APP_DIR/$d"
  fi
done

# Single-file state. Symlink target may not exist yet — the app creates it on
# first write, landing the file on the volume.
for f in jobs.json application_tracker.xlsx blocked_urls.json; do
  if [ ! -e "$APP_DIR/$f" ] || [ -L "$APP_DIR/$f" ]; then
    ln -sfn "$DATA_DIR/$f" "$APP_DIR/$f"
  fi
done

# --- 2. Seed dummy config from fixtures when nothing real is mounted ---
seed() {
  # $1 = fixture template, $2 = destination filename (relative to APP_DIR)
  if [ ! -e "$APP_DIR/$2" ] && [ -f "$APP_DIR/$1" ]; then
    cp "$APP_DIR/$1" "$APP_DIR/$2"
    echo "entrypoint: seeded dummy $2 from $1"
  fi
}
seed fixtures/profile.example.json     profile.json
seed fixtures/credentials.example.json credentials.json
seed fixtures/web_keys.example.json    web_keys.json
seed fixtures/.env.example             .env

echo "entrypoint: data dir = $DATA_DIR ; dashboard host = ${JOBHUNTER_DASHBOARD_HOST:-127.0.0.1}:${JOBHUNTER_DASHBOARD_PORT:-8787}"

exec "$@"
