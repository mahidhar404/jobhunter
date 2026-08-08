# job-hunter

Local-first job discovery, resume tailoring, and ATS form-fill tooling with a
stdlib dashboard (`dashboard/server.py`, served at `http://127.0.0.1:8787`).

## Quickstart

Two ways to run it. **Docker** is the fastest path on a fresh machine (no
Homebrew, no manual browser install, no real data required); the **native
venv** gives you the full macOS feature set.

### Option A — Docker (recommended for a fresh machine)

```bash
docker compose up --build          # open http://127.0.0.1:8787
```

Comes up with safe **dummy** data seeded from `fixtures/*.example`. Chromium and
Tectonic are preinstalled in the image. See **[docs/DOCKER.md](docs/DOCKER.md)**
for supplying your own config, persistence, the optional Skyvern profile, and
which features are host-only.

### Option B — Native venv

```bash
python3 -m venv .venv && . .venv/bin/activate      # Python 3.12+ recommended (3.10 min)
pip install -r requirements.txt
python -m playwright install chromium              # browser binaries (separate download)

# config from templates (git-ignored once created — fill with YOUR data)
cp fixtures/profile.example.json     profile.json
cp fixtures/credentials.example.json credentials.json
cp fixtures/web_keys.example.json    web_keys.json
cp fixtures/.env.example             .env

.venv/bin/python3 dashboard/server.py              # open http://127.0.0.1:8787
```

Some features need extra, out-of-band setup (Tectonic for resume PDFs, Node +
OpenClaw for the agent bridge, and the separate Skyvern runtime for
LLM-assisted fill).

## Docs

- **[docs/DOCKER.md](docs/DOCKER.md)** — run the whole stack in containers
  (app + Postgres + optional Skyvern), config, persistence, host-only features.
- **[docs/PORTABILITY.md](docs/PORTABILITY.md)** — will this run on another
  machine? Definitive answer, full blocker inventory, and the ordered
  fresh-machine / Linux setup guide.
- **[docs/RESTRUCTURE_PROPOSAL.md](docs/RESTRUCTURE_PROPOSAL.md)** — repo layout
  + PII-safe export plan (`export.sh`).
- **`PLAYBOOK.md`** — operating rules (never submit, never solve CAPTCHAs, never
  invent EEO answers). Read before running fills.
- **`requirements.txt`** — pinned deps for the dashboard + scripts (the main
  `.venv`). The Skyvern runtime has its own separate environment.
