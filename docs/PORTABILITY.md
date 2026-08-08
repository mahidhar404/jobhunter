# Portability / Reproducibility Audit

**Question:** *"If I copy this setup to another system, would it just work?"*
**Author:** job-hunter agent, 2026-08-08. **Method:** static scan of every
`import` in `scripts/`, `scripts/fastfill/`, `dashboard/`, plus `.venv` freeze,
grep for hardcoded paths, and a fresh-venv resolution check of the generated
`requirements.txt`.

> This doc is about whether the **code runs on a fresh machine**. For how to
> share the repo *without leaking PII*, see [`RESTRUCTURE_PROPOSAL.md`](RESTRUCTURE_PROPOSAL.md)
> and `export.sh`. Nothing here moves, renames, or commits files.

---

## 1. Definitive answer

**No — not by a plain `git clone` + run.** But it becomes **yes for the core
dashboard + discovery + resume-tailoring flow** after a short, well-defined
setup: create a venv, `pip install -r requirements.txt`, `playwright install
chromium`, copy the `fixtures/*.example.json` templates to real config, and
install two system binaries. See [§5 fresh-machine setup](#5-fresh-machine-setup).

Two caveats that keep the answer from being an unqualified "yes":

1. **On a different OS or Homebrew layout it partially breaks** without edits —
   several absolute paths are hardcoded to Apple-Silicon Homebrew
   (`/opt/homebrew/bin/...`) and to macOS-only tools (`open`, `osascript`,
   `os.killpg`). Core Python runs; resume-PDF, the OpenClaw agent bridge, and a
   few UX niceties do not until those are made portable (all Would-break items
   are listed below).
2. **The Skyvern-assisted fill path and the OpenClaw agent runtime are separate,
   out-of-band systems** (own venv + Postgres; a separately-installed CLI). They
   are not reproduced by this repo's setup and must be installed independently.

So: **the code itself is fundamentally portable Python** (relative-path,
`Path(__file__)`-anchored, no committed PII), but a fresh machine needs the
dependency set + system binaries + config that were previously undocumented.
This audit supplies the missing `requirements.txt` and the setup steps.

---

## 2. Blockers grouped by severity

### 🟥 Would-break — must fix/setup or the feature errors out

| # | Blocker | Site(s) | Why it breaks elsewhere |
|---|---|---|---|
| 1 | **No `requirements.txt`** (until now) | repo root | Fresh machine has none of `beautifulsoup4/playwright/python-jobspy/trafilatura/openpyxl/fpdf2/pypdf/lxml`. **Fixed by the new `requirements.txt`.** |
| 2 | **Playwright browser binaries** | all Playwright code | `pip install playwright` does **not** fetch Chromium. Needs `python -m playwright install chromium` (a separate ~150 MB download). |
| 3 | **Hardcoded `/opt/homebrew/bin/tectonic`** | `dashboard/server.py:4881`, `scripts/fit_resume_pages.py:46` | Resume→PDF compile fails on Intel mac (`/usr/local`), Linux, or if Tectonic isn't installed. `scripts/fastfill/run_identity.py` honors `TECTONIC_BIN` env; the other two do **not**. |
| 4 | **Hardcoded `/opt/homebrew/bin/openclaw`** | `dashboard/server.py:59`, `scripts/chrome_for_testing.py:23`, `scripts/session_timing_report.py:27`, `dashboard/launch_dashboard.sh:304` | The agent-answer bridge, cron registration, and PartyRock browser control shell out to this exact path. Missing/relocated OpenClaw → exit 127. |
| 5 | **`run_dashboard_supervised.sh` hardcodes `/opt/homebrew/bin/python3`** | `dashboard/run_dashboard_supervised.sh:10` | The launchd path runs the server with a fixed Homebrew interpreter; wrong on Intel/Linux. (Note: `launch_dashboard.sh:525` uses `/usr/bin/env python3` instead — see §3.) |
| 6 | **macOS-only syscalls/binaries**: `os.killpg`/`start_new_session`, `subprocess.run(["open", …])`, `osascript` | `dashboard/server.py` (1269, 1281, 1661, 5329, 5325…), `scripts/chrome_for_testing.py:212` | `open`/`osascript` don't exist on Linux; process-group kill semantics differ. Child-process teardown, "open apply URL", and native dialogs fail on Linux. |

### 🟧 Needs-setup — works once you provide config/services (no code change)

| # | Item | Needed for | Template? |
|---|---|---|---|
| 7 | `profile.json` (real PII) | fastfill real mode, dashboard identity | `fixtures/profile.example.json` ✓ |
| 8 | `credentials.json` (per-ATS logins) | fastfill account creation/login | `fixtures/credentials.example.json` ✓ |
| 9 | `web_keys.json` (per-site passwords) | fastfill password fields | `fixtures/web_keys.example.json` ✓ |
| 10 | `.env` (LLM keys, path/safety toggles) | Flash leftovers, path indirection, real-PII gating | `fixtures/.env.example` ✓ |
| 11 | Adzuna app id/key | `scripts/scrape_adzuna.py` discovery source | env vars (see script) |
| 12 | **Node.js** | JS UI tests (`node dashboard/test_job_sort.js`) **and** the OpenClaw CLI (`#!/usr/bin/env node`) | install Node |
| 13 | **Tectonic** (LaTeX engine) | resume `.tex`→PDF | install Tectonic |
| 14 | **Skyvern runtime** (own venv 3.12 + Postgres + `skyvern_runtime/.env`/`.secrets.env`) | `--flash-leftovers`, `hybrid_fill.py`, `learning.py` DB reads | reinstall per Skyvern's docs |
| 15 | **OpenClaw gateway/agent** runtime | dashboard→agent answer bridge, managed PartyRock browser (CDP :18800), cron | install OpenClaw separately |
| 16 | `option_mappings.json` | fastfill option normalization | env `FASTFILL_OPTION_MAPPINGS` (already supported), else root file |

### 🟩 Works-as-is — portable, no action

- All Python code is anchored with `Path(__file__)` / relative roots (`ROOT =
  Path(__file__).parent.parent`, `parents[1|2]`), not absolute repo paths.
- **No real PII/secrets are committed**; dummy `*.example.json` templates exist
  in `fixtures/`.
- `run_dashboard_supervised.sh` resolves its own repo root (`ROOT="$(cd …)"`).
- Dashboard subprocesses correctly use `PYTHON_BIN = ROOT/.venv/bin/python3`
  (relative to repo), so once the venv exists they use the right interpreter.
- The dashboard HTTP server itself uses only stdlib (`http.server`); the
  third-party deps are needed by the scripts it launches, not the server core.
- `pip install -r requirements.txt` **resolves cleanly on a fresh venv**
  (verified 2026-08-08 via `--dry-run` full resolution — the resolved set
  matches the working `.venv`).

---

## 3. Detail: interpreter & path assumptions

- **`.venv` is CPython 3.14.6.** The code uses PEP 604 `X | None` unions, so
  **Python ≥ 3.10 is the hard minimum**; the project actually runs on 3.12/3.14
  (compiled `.pyc` are cp312/cp314). No `match`/`tomllib` (3.10/3.11-only)
  syntax is used. Recommend **3.12+**.
- **Three different interpreters are referenced** — a portability smell to know
  about:
  - `dashboard/launch_dashboard.sh` starts the server with `/usr/bin/env
    python3` (whatever is first on PATH — must be a Python that has the deps, or
    the server's script imports fail).
  - `dashboard/run_dashboard_supervised.sh` uses `/opt/homebrew/bin/python3`.
  - Subprocesses (scout, tracker, scrape_ats, fast_fill, tailor_resume) use
    `ROOT/.venv/bin/python3` (correct & relative).
  - **Recommendation for a fresh machine:** create `.venv` from your 3.12+
    interpreter and start the server with `.venv/bin/python3 dashboard/server.py`
    so the server and its children share one environment.
- **`web_keys.py` Desktop symlink** (`~/Desktop/Command Center/Documents/
  web_keys.json`, `scripts/fastfill/web_keys.py:23,51`) is a macOS user-specific
  convenience. It's created best-effort and guarded, so it won't crash on
  another machine, but the "Command Center" Finder integration is macOS-only.
- **OpenClaw workspace assumptions**: `Path.home()/.openclaw/...` for
  exec-approvals and the managed browser user-data dir. These resolve per-user
  and only matter when the OpenClaw runtime is present.

## 4. Dependency inventory (derived from imports)

**Direct third-party (main `.venv`)** → pinned in `requirements.txt`:
`beautifulsoup4`, `fpdf2`, `lxml`, `openpyxl`, `playwright`, `pypdf`,
`python-jobspy`, `trafilatura`. (`pandas`, `numpy`, `requests`, `tls-client`,
`markdownify`, `pydantic`, etc. arrive transitively — mostly via
`python-jobspy` and `trafilatura`.)

**Optional / feature-gated (NOT in `.venv`; code degrades gracefully):**
- `httpx` — top-level import in `scripts/fast_discovery.py` (a standalone
  JSON-API discovery experiment; **not** wired into the dashboard). That one
  script won't run until `httpx` is installed.
- `pdfplumber` — `resume_parser.py` uses it inside `try/except` and falls back
  to `pypdf`, so its absence is non-fatal.
- `psycopg` — function-local import in `fastfill/learning.py`; only the
  learn-from-Skyvern-Postgres path needs it.

**Separate environment (do not merge):** `skyvern_runtime/venv` (CPython 3.12)
holds the `skyvern` package and its heavy deps; it also needs Postgres and
`skyvern_runtime/.env` / `.secrets.env`.

---

## 5. Fresh-machine setup

Ordered steps to get the **core flow** (dashboard + discovery + resume tailor)
running. macOS Apple Silicon is the reference platform; Linux notes in §6.

```bash
# 1. Clone
git clone <repo-url> job-hunter && cd job-hunter

# 2. Python env (use 3.12+)
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 3. Playwright browser (separate from the pip package)
python -m playwright install chromium

# 4. Config from templates (these are git-ignored once created; fill with YOUR data)
cp fixtures/profile.example.json     profile.json
cp fixtures/credentials.example.json credentials.json
cp fixtures/web_keys.example.json    web_keys.json
cp fixtures/.env.example             .env
#    edit .env — add LLM keys if you use Flash; keep FASTFILL_ALLOW_REAL unset for dummy mode

# 5. System binaries
#    macOS:
brew install tectonic node          # tectonic = resume->PDF; node = JS tests + OpenClaw CLI
#    (OpenClaw + Skyvern are installed out-of-band — see §5.1 / §5.2)

# 6. Launch the dashboard (start server with the venv interpreter so children match)
.venv/bin/python3 dashboard/server.py
#    then open http://127.0.0.1:8787
#    (dashboard/launch_dashboard.sh is the macOS one-click wrapper; it also
#     manages browsers and needs OpenClaw — plain server.py is enough for core use.)
```

**Minimum viable (no fills, no agent):** steps 1–3 + `profile.json` are enough
to run discovery (`scout.py`, `scrape_ats.py`), dedup, the tracker, and the
dashboard UI. Steps 4–5 unlock resume tailoring and fastfill dummy runs.

### 5.1 OpenClaw runtime (optional, for the agent bridge)
The dashboard shells out to an `openclaw` CLI at `/opt/homebrew/bin/openclaw`
for: answering stuck jobs (resumes the agent session), the managed PartyRock
browser (CDP :18800), and cron registration. Install OpenClaw separately; if
its binary lives elsewhere, edit `OPENCLAW_BIN` (and the other three sites in
§2 #4) or symlink it to the expected path. Without it, the core dashboard still
runs; only agent-answer / PartyRock / cron features are unavailable.

### 5.2 Skyvern runtime (optional, for LLM-assisted fill)
`skyvern_runtime/` is its own project: a separate CPython 3.12 venv with the
`skyvern` package, a Postgres database, and `skyvern_runtime/.env` +
`.secrets.env`. Reinstall it per Skyvern's own documentation. The main fastfill
path (`scripts/fastfill/fast_fill.py`) works without it; only `--flash-leftovers`
and `hybrid_fill.py` need it.

---

## 6. macOS-specific vs. Linux

| Area | macOS (as written) | Linux change required |
|---|---|---|
| Homebrew paths | `/opt/homebrew/bin/{tectonic,openclaw,python3}` | Point at `/usr/bin`/`/usr/local` or make them `PATH`/env-driven (`TECTONIC_BIN` already exists for `run_identity.py`; add the same for `server.py` + `fit_resume_pages.py`, and an env for `OPENCLAW_BIN`). |
| Open a URL | `subprocess.run(["open", url])` (`server.py:5329`) | Use `xdg-open`. |
| Native dialogs / beep | `osascript` (`server.py:5325,5361`) | No-op or a Linux notifier; currently best-effort so non-fatal. |
| Process teardown | `os.killpg` + `start_new_session=True` | Works on Linux (POSIX), but verify; Windows would need a rewrite. |
| Desktop symlink | `~/Desktop/Command Center/Documents` | Cosmetic; guarded, safe to ignore. |
| Server interpreter | `run_dashboard_supervised.sh` → `/opt/homebrew/bin/python3` | Change to `.venv/bin/python3` or `/usr/bin/env python3`. |

**Bottom line for Linux:** core discovery/tracking/dashboard is portable after
`requirements.txt` + `playwright install`; resume-PDF and the "open in browser"
UX need the small path/command swaps above; OpenClaw + Skyvern remain
independent installs.

---

## 7. Verification performed

- `requirements.txt` pins validated against the live `.venv` (all
  "already satisfied").
- **Full fresh-venv `pip install --dry-run -r requirements.txt` resolved
  successfully** (2026-08-08); the resolved package set matches the working
  `.venv` freeze (only unpinned transitive patch bumps differ, e.g. `dateparser`
  1.4.1→1.4.2).
- Core deps import-check passed:
  `import bs4, openpyxl, playwright, jobspy, trafilatura, fpdf, pypdf, lxml,
  pandas, requests` → OK. Playwright Chromium present locally (`chromium-1228`).
