# Running job-hunter in Docker

Run the dashboard on any machine — Linux or macOS, `amd64` or `arm64` — with no
Homebrew, no manual `playwright install`, and **no real data**. Clone → `docker
compose up` → open the dashboard.

> This is an alternative to the native venv path in [`PORTABILITY.md`](PORTABILITY.md).
> The containers use the exact same code; only two low-risk, macOS-preserving
> edits were needed to make the hardcoded Homebrew binaries and the loopback bind
> configurable (see [Code changes](#code-changes-that-enable-this) below).

## Quickstart

```bash
git clone <repo-url> job-hunter && cd job-hunter
docker compose up --build          # first run builds the app image
# open http://127.0.0.1:8787
```

On first boot the app **seeds dummy** `profile.json`, `credentials.json`,
`web_keys.json`, and `.env` from the shipped `fixtures/*.example` templates, so
the dashboard comes up populated with safe placeholder data. No PII is baked
into the image, and nothing real is required to start.

Stop with `Ctrl-C`; tear down with `docker compose down` (add `-v` to also drop
the data volume).

## Services

| Service | What it is | Default? |
|---|---|---|
| `app` | The dashboard (`dashboard/server.py`) on `0.0.0.0:8787`, published to `127.0.0.1:8787`. Built on `mcr.microsoft.com/playwright/python:v1.61.0-jammy` (Chromium preinstalled, matches `playwright==1.61.0`) + Tectonic for resume→PDF. | ✅ starts by default |
| `db` | `postgres:16-alpine` with a healthcheck + named volume. Backs the Skyvern learning path (`scripts/fastfill/learning.py`) and the optional `skyvern` service. | ✅ starts by default |
| `omniroute` | **Optional LLM gateway.** Behind the `gateway` compose profile. OpenAI-compatible proxy on `127.0.0.1:20128` (compose DNS `http://omniroute:20128/v1`). DeepSeek stays the default direct base; point `OPENAI_COMPATIBLE_API_BASE` at OmniRoute only when you want free-tier fallback + compression. Dummy-mode only (`llm_config.assert_dummy_for_gateway`). | ❌ only with `--profile gateway` |
| `skyvern` | **Optional, heavy.** Behind the `skyvern` compose profile. A documented placeholder wired to `db` (see [Skyvern](#skyvern-optional-heavy)). | ❌ only with `--profile skyvern` |

## OmniRoute gateway (optional)

Isolated cost/routing sidecar — **not** baked into the app image. If the
gateway is down or unset, fills still hit DeepSeek direct via
`OPENAI_COMPATIBLE_API_BASE` default.

```bash
docker compose --profile gateway up -d omniroute
# Open http://127.0.0.1:20128 — add DeepSeek + optional free-tier providers,
# generate a Bearer token, then in .env (dummy / TEST_MODE only):
#   OPENAI_COMPATIBLE_API_BASE=http://127.0.0.1:20128/v1   # or http://omniroute:20128/v1 in-compose
#   OPENAI_COMPATIBLE_API_KEY=<token>
# Rollback: unset OPENAI_COMPATIBLE_API_BASE (reverts to https://api.deepseek.com/v1).
```

Free-tier pools and gateway analytics must never see real PII — the loop-entry
assert refuses non-DeepSeek bases whenever `field_map.is_real_profile_mode()`.

## Images / versions

- **App base:** `mcr.microsoft.com/playwright/python:v1.61.0-jammy` (multi-arch
  `amd64`/`arm64`; Ubuntu 22.04 "jammy"; Python 3.10; Chromium preinstalled).
- **Tectonic:** installed at build time via the official installer script into
  `/usr/local/bin/tectonic` (arch auto-detected).
- **Postgres:** `postgres:16-alpine`.
- **Python deps:** pinned `requirements.txt` (installed on top of the base;
  `playwright` re-pins to the same `1.61.0` — a no-op that keeps the app
  self-consistent).

## Supplying your own config (real data)

By default you get dummy data. To use **your own** profile/credentials:

1. Create the real files on the host (start from the fixtures):
   ```bash
   cp fixtures/profile.example.json     profile.json
   cp fixtures/credentials.example.json credentials.json
   cp fixtures/web_keys.example.json    web_keys.json
   cp fixtures/.env.example             .env
   # edit them with your data
   ```
2. Uncomment the config bind mounts under the `app` service in
   `docker-compose.yml`:
   ```yaml
   volumes:
     - jobhunter_data:/app/data
     - ./profile.json:/app/profile.json
     - ./credentials.json:/app/credentials.json
     - ./web_keys.json:/app/web_keys.json
     - ./.env:/app/.env
   ```
   When a real file is bind-mounted it already exists, so the dummy seeding is
   skipped and your file wins.

> ⚠️ Per project policy, **never** put real PII into a shared/automation
> context. Keep `FASTFILL_ALLOW_REAL=0` (the compose default) unless you
> explicitly know what you're doing. These files are all git-ignored and
> excluded from the image via `.dockerignore`.

## Persistence

Runtime state is written to `/app/data`, backed by the named volume
`jobhunter_data`, so it survives `docker compose down` / restarts. The entrypoint
symlinks the app's root-relative paths onto that volume:

- `jobs.json`, `application_tracker.xlsx`, `blocked_urls.json` → `/app/data/…`
- `listings/`, `logs/`, `resumes/` → `/app/data/…`

(The code still writes to the repo root because the `private/` migration is
deferred; the symlinks transparently land the bytes on the volume. No PII is
ever baked into an image layer.)

## What works in-container vs. host-only

**Works in the container:**

- The dashboard UI, job discovery/scraping, dedup, tracker, resume tailoring.
- Resume → PDF (Tectonic is installed and on `PATH`).
- Playwright/Chromium automation (browsers preinstalled in the base image).

**Host-only (gracefully degraded in-container):**

- **OpenClaw agent bridge** — answering stuck jobs, the managed PartyRock
  browser (CDP :18800), and cron registration all shell out to an `openclaw`
  CLI that is **not** installed in the image. The dashboard still starts and
  serves; these specific actions just no-op/fail on demand. Run the native
  (host) path if you need them.
- **macOS niceties** — "open apply URL" (`open`), native dialogs/beeps
  (`osascript`). These are macOS-only and are already best-effort/non-fatal.

## Skyvern (optional, heavy)

Skyvern powers `--flash-leftovers` / `hybrid_fill.py`. It is **not** merged into
the app image (its deps are heavy and separate). Start it only when needed:

```bash
docker compose --profile skyvern up --build
```

The `skyvern` service in `docker-compose.yml` is a **documented placeholder**
using the upstream Skyvern image, wired to the `db` service. The repo's own
`skyvern_runtime/` is a separately-vendored setup (its own Python 3.12 venv,
`.env`, and `.secrets.env`) and is **not** reproduced here. Fully containerizing
that exact runtime — including its scripts under `skyvern_runtime/scripts/`
(`hybrid_fill.py`, etc.) and the LLM keys it expects — is an **owner
follow-up**. You will likely need to:

- Point `DATABASE_STRING` at the `db` service (already set).
- Provide LLM keys / Skyvern config via `environment:` or an `env_file:`.
- Confirm the upstream image tag/registry, or build an image from
  `skyvern_runtime/`.

## Architecture notes (arm64 vs amd64)

The Playwright base and `postgres:16-alpine` are both multi-arch, so the stack
runs natively on Apple Silicon (`arm64`) and x86 (`amd64`). Tectonic's installer
auto-detects the arch. If you must force a platform (e.g. building `amd64` on an
M-series Mac), add `platform: linux/amd64` under the service or pass
`--platform` to `docker build`.

## Verifying the compose file

```bash
docker compose config          # lint/normalize (no daemon needed for parse)
docker compose build app       # build just the app image
docker compose up -d            # run detached
curl -sS http://127.0.0.1:8787/ # smoke test the dashboard root
```

## Troubleshooting

- **Tectonic build layer fails (restricted network):** the installer needs
  network at build time. If it's unavailable, resume→PDF won't work but the rest
  of the dashboard is fine. Alternatives: install a Tectonic release binary into
  the image yourself, or `apt-get install -y tectonic` (available in some Ubuntu
  universe snapshots), then keep `JOBHUNTER_TECTONIC_BIN=/usr/local/bin/tectonic`
  (or wherever it lands / on `PATH`).
- **Port already in use:** change the host side of the mapping, e.g.
  `- "127.0.0.1:8788:8787"`.
- **Want the DB reachable from the host:** uncomment the `db` `ports:` block.

## Code changes that enable this

These were the only source edits, all additive and preserving the macOS
default as the final fallback:

- **Tectonic / OpenClaw binary resolution** — now `env var → PATH → the original
  `/opt/homebrew/bin/...` default`, in `dashboard/server.py`,
  `scripts/fit_resume_pages.py`, `scripts/chrome_for_testing.py`,
  `scripts/session_timing_report.py`. Env vars: `JOBHUNTER_TECTONIC_BIN`,
  `JOBHUNTER_OPENCLAW_BIN`.
- **Dashboard bind host/port** — `dashboard/server.py` still defaults to
  `127.0.0.1:8787`, but honors `JOBHUNTER_DASHBOARD_HOST` / `JOBHUNTER_DASHBOARD_PORT`
  (the container sets host `0.0.0.0`).
