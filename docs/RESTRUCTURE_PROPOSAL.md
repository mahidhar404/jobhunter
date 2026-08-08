# Restructure & Export Proposal

**Status:** Proposal (read this, then approve the risky moves).
**Author:** job-hunter agent, 2026-08-08.
**Goal:** Make the repo (1) safe — all real PII/secrets isolated in one place — and (2) cleanly *exportable* so other people can run the code with **dummy data only**, never leaking Yogesh's personal data.

> **What is already done in this pass** (safe, additive, non-breaking): hardened `.gitignore`, added `.cursorignore`, added dummy `*.example.json` + `.env.example` templates, created `private/` (git-ignored) with a README, and added a read-only `export.sh`. **Nothing was moved, renamed, or deleted.** See [§7 Phased migration](#7-phased-migration-plan).

---

## 1. TL;DR / Priority findings

- **Good news — no real PII is currently tracked in git.** All the sensitive files (`profile.json`, `credentials.json`, `web_keys.json`, `option_mappings.json`, `jobs.json`, `listings/`, `logs/`, `memory/`, `resumes/`, `application_tracker.xlsx`, `skyvern_runtime/.env`, `.secrets.env`) are already in `.gitignore` and are **not** in `git ls-files`. Secret files are also `chmod 600`.
- The only "personal-ish" tracked files are **safe**: `addresses.json` (self-described *privacy placeholders*, not the real address), `scripts/fastfill/learned_fields.json` (generic ATS answers like "Yes"/"Immediately available"; no name/email/phone found), and the empty template docs `USER.md` / `IDENTITY.md`.
- **The real problem is layout, not leakage:** ~20 data/secret/artifact files and dirs sit at the repo root mixed with code, docs, and agent-memory. There is no single private folder, no example templates, and no export path — so "share the repo" today means "manually figure out what's safe," which is how leaks happen later.
- **Constraint that shapes everything:** dozens of scripts hardcode workspace-root-relative paths (`Path(__file__).parents[1|2] / "profile.json"`, etc.). Moving those files **breaks code** unless we add config indirection first. So file moves are **proposal-only** and must be coordinated with the in-flight India-discovery work.

---

## 2. Current top-level layout (what each thing is)

| Path | Kind | Contains real PII/secrets? | Git-tracked? | Notes |
|---|---|---|---|---|
| `README`* / `PLAYBOOK.md` / `TOOLS.md` / `AGENTS.md` / `PRODUCTION.md` / `ARCHITECTURE_REVIEW.md` / `BUG_FIX_STATUS.md` | Docs | No | Mixed (some tracked, some untracked) | Project + agent docs |
| `SOUL.md` / `USER.md` / `IDENTITY.md` / `HEARTBEAT.md` | Agent identity/memory | No (currently empty templates) | Tracked | `USER.md` *will* hold personal context over time → treat as private-leaning |
| `memory/` (`YYYY-MM-DD.md`) | Agent memory | Low, but personal | **Not tracked** (gitignored via `.openclaw`? — actually just absent) | Daily logs; should be explicitly private |
| `MEMORY.md` | Agent long-term memory | Personal | Not present yet | Referenced by AGENTS.md; keep private when created |
| `dashboard/` | Code (Flask server + static UI) | No | Partly tracked | **In-flight India work — do not move** |
| `scripts/` | Code (scrapers, fastfill, tracker, …) | No | Partly tracked | **In-flight India work — do not move** |
| `skyvern_runtime/` | Code + runtime | `.env`, `.secrets.env` = secrets | Code tracked; secrets/runtime gitignored | Big runtime dirs already ignored |
| `ats_notes/` | Docs (per-ATS field notes) | No | Partly tracked | Fine to share |
| `sota_brainstorm/` | Docs | No | Not tracked | Fine to share |
| `.cursor/skills/` | Tooling (agent skills) | No | Not tracked | Fine to share |
| **`profile.json`** (+ `.bak*`) | **Data / PII** | **Yes — name, email, phone, EEO, education** | Gitignored | `chmod 600` |
| **`credentials.json`** | **Secret** | **Yes — ATS site emails + passwords** | Gitignored | `chmod 600` |
| **`web_keys.json`** | **Secret** | **Yes — per-site account passwords** | Gitignored | `chmod 600`; symlinks to `~/Desktop/Command Center/Documents/` |
| **`option_mappings.json`** | Data (semi-sensitive) | Low | Gitignored | `chmod 600` |
| `addresses.json` | Data (fixture) | **No — dummy placeholders** | **Tracked** | Safe to share as-is |
| `jobs.json` (+ `.bak*`, `.lock`) | Data (regenerable, 6.7 MB) | Indirect (application history) | Gitignored | `chmod 600` |
| `blocked_urls.json` (1.3 MB) | Data (regenerable) | No | Gitignored | |
| `application_tracker.xlsx` (+ `.lock`) | Data | **Yes — application history** | Gitignored | |
| `listings/` (210 MB, ~140 dirs) | Artifacts (scraped JD/HTML) | Low | Gitignored | Regenerable |
| `logs/` (1.8 MB, ~160 files) | Artifacts | Low–med (may embed PII in traces) | Gitignored | Regenerable |
| `resumes/` (49 MB, ~6300 entries) | Artifacts / **PII** | **Yes — tailored resumes (real name)** | Gitignored | Regenerable from profile |
| `partyrock_chrome_profile/` / `dashboard_chrome_profile/` / `dashboard_ui_profile/` | Browser profiles | **Yes — cookies/sessions** | Gitignored | Never share |
| `skyvern_runtime/*_profile`, `pgdata`, `videos`, … | Runtime | Med | Gitignored | Regenerable |
| `.venv/` | Env | No | Gitignored | Regenerable |
| `partyrock.json` / `open_partyrock.sh` / `openclaw-workspace-state.json` | Config/state | `openclaw-workspace-state.json` = state | Mixed | State file gitignored |

\* `README.md` not present at root today — recommend adding one (see [§6 Onboarding](#6-onboarding-a-new-contributor)).

---

## 3. PII / sensitive-file inventory (authoritative)

Legend: **PII** = personally identifying; **SECRET** = credential; **ART** = regenerable artifact that may embed PII.

| File / dir | Class | Real data? | Git-tracked | Referenced by code paths |
|---|---|---|---|---|
| `profile.json` | PII | Yes | No (ignored) | `scripts/fastfill/field_map.py` (`parents[2]/profile.json`), dashboard, many tests |
| `profile.json.bak*` | PII | Yes | No (ignored) | — |
| `credentials.json` | SECRET | Yes | No (ignored) | `scripts/fastfill/web_keys.py` (`ROOT/credentials.json`) |
| `web_keys.json` | SECRET | Yes | No (ignored) | `scripts/fastfill/web_keys.py` (`ROOT/web_keys.json` + desktop symlink) |
| `option_mappings.json` | data | Low | No (ignored) | `scripts/fastfill/option_mappings.py` (env `FASTFILL_OPTION_MAPPINGS` **already supported** → falls back to `ROOT/option_mappings.json`) |
| `skyvern_runtime/.env`, `.secrets.env` | SECRET | Yes | No (ignored) | skyvern runtime |
| `jobs.json` (+ `.bak*`, `.lock`) | ART/PII | Yes | No (ignored) | `scripts/jobs_lock.py` (`parent.parent/jobs.json`), `get_job.py`, `dedup_jobs.py`, dashboard, many |
| `application_tracker.xlsx` (+ `.lock`) | PII | Yes | No (ignored) | `scripts/tracker.py` |
| `blocked_urls.json` (+ `.lock`) | ART | No | No (ignored) | `scripts/blocked_urls.py` |
| `listings/` | ART | Low | No (ignored) | scrapers, dashboard |
| `logs/` | ART | Low–med | No (ignored) | many |
| `resumes/` | PII (ART) | Yes | No (ignored) | `scripts/tailor_resume.py`, `resume_publish.py`, tracker |
| `memory/`, `MEMORY.md` | PII (agent) | Med | No (not present in git) | agent only |
| `USER.md` | PII (agent) | Not yet (template) | **Tracked** | agent only — **flag: will accrue PII** |
| `*_chrome_profile/`, `dashboard_ui_profile/`, skyvern profiles | SECRET (sessions) | Yes | No (ignored) | browser automation |
| `openclaw-workspace-state.json` | state | Med | No (ignored) | runtime |
| `addresses.json` | fixture | **No (dummy)** | Tracked | `scripts/pick_address.py` — **safe to keep tracked** |
| `scripts/fastfill/learned_fields.json` | data | **No PII found** | Tracked | fastfill — safe |

**Leak check result: PASS.** No real PII or secret is currently committed. The one item to watch is **`USER.md`** (tracked, currently empty; if it later gains personal context it becomes a tracked leak — see recommendation in [§7](#7-phased-migration-plan)).

---

## 4. Proposed clean layout (before → after)

The intent: **one private folder** for everything real, and everything else is shareable. Code reads data locations through **one config module / env vars** with safe defaults, so the private folder can live anywhere.

### Before (today, abridged)
```
job-hunter/
├── profile.json            # PII        ┐
├── credentials.json        # SECRET     │
├── web_keys.json           # SECRET     │ scattered at root,
├── option_mappings.json    # data       │ mixed with code + docs
├── jobs.json               # ART/PII    │
├── application_tracker.xlsx# PII        │
├── blocked_urls.json       # ART        │
├── addresses.json          # fixture (dummy, tracked)
├── listings/  logs/  resumes/           ┘ big artifacts
├── memory/  MEMORY.md  USER.md  SOUL.md   # agent memory/identity
├── dashboard/  scripts/  skyvern_runtime/ # code
├── ats_notes/  sota_brainstorm/  *.md     # docs
└── .gitignore
```

### After (target)
```
job-hunter/
├── README.md                    # NEW: what this is + quickstart
├── docs/                        # all human docs
│   ├── RESTRUCTURE_PROPOSAL.md  # (this file)
│   ├── PLAYBOOK.md  TOOLS.md  PRODUCTION.md  ARCHITECTURE_REVIEW.md  ...
│   └── ats_notes/  sota_brainstorm/
├── src/ (or keep scripts/ + dashboard/ as-is)   # CODE — unchanged for now
│   ├── scripts/  dashboard/  skyvern_runtime/
│   └── jobhunter_paths.py       # NEW: single source of truth for data locations
├── fixtures/                    # SHAREABLE dummy data (safe to commit)
│   ├── profile.example.json
│   ├── credentials.example.json
│   ├── web_keys.example.json
│   ├── addresses.json           # already-dummy, moved here
│   └── .env.example
├── private/                     # GIT-IGNORED — the ONLY place real data lives
│   ├── README.md                # NEW: explains contents + that it's ignored
│   ├── profile.json  credentials.json  web_keys.json  option_mappings.json
│   ├── jobs.json  blocked_urls.json  application_tracker.xlsx
│   ├── resumes/  listings/  logs/
│   ├── memory/  MEMORY.md  USER.md
│   └── browser_profiles/  (chrome/dashboard/skyvern session dirs)
├── .gitignore  .cursorignore
└── export.sh                    # NEW: produce a PII-free shareable copy
```

> The `src/` rename is **optional** and the most invasive. A lighter-touch variant keeps `scripts/` and `dashboard/` where they are and only introduces `private/`, `fixtures/`, and the `jobhunter_paths.py` config module. **Recommended: do the light variant first.**

---

## 5. PII isolation plan (with exact code changes)

### 5.1 The config-indirection module (enabler for every move)

Add **one** module, `scripts/jobhunter_paths.py` (importable as `jobhunter_paths`), that resolves data locations from env vars with **backward-compatible defaults** (current root). Nothing moves until this exists and is adopted.

```python
# scripts/jobhunter_paths.py  (illustrative)
import os
from pathlib import Path

# Workspace root = repo root (this file lives in scripts/)
ROOT = Path(__file__).resolve().parent.parent

# PRIVATE_DIR defaults to ROOT (today's behaviour). Point it at ./private later.
PRIVATE_DIR = Path(os.environ.get("JOBHUNTER_PRIVATE_DIR", ROOT))
FIXTURES_DIR = Path(os.environ.get("JOBHUNTER_FIXTURES_DIR", ROOT / "fixtures"))

def private(name: str) -> Path:
    return PRIVATE_DIR / name

PROFILE_JSON      = private("profile.json")
CREDENTIALS_JSON  = private("credentials.json")
WEB_KEYS_JSON     = private("web_keys.json")
OPTION_MAPPINGS   = Path(os.environ.get("FASTFILL_OPTION_MAPPINGS", private("option_mappings.json")))
JOBS_JSON         = private("jobs.json")
BLOCKED_URLS_JSON = private("blocked_urls.json")
TRACKER_XLSX      = private("application_tracker.xlsx")
RESUMES_DIR       = private("resumes")
LISTINGS_DIR      = private("listings")
LOGS_DIR          = private("logs")
```

**Default = current behaviour**, so introducing the module changes nothing. Migration then becomes: (a) set `JOBHUNTER_PRIVATE_DIR=./private`, (b) `git mv` the files into `private/`, (c) swap hardcoded paths for `jobhunter_paths.X` file-by-file.

### 5.2 Exact hardcoded references that must change when files move

Found via `grep`. These are the concrete edit sites (do **not** touch the in-flight India files until that work merges):

| Data file | Reference site(s) | Current expression |
|---|---|---|
| `profile.json` | `scripts/fastfill/field_map.py:1024, :1579` | `Path(__file__).resolve().parents[2] / "profile.json"` |
| `credentials.json`, `web_keys.json` | `scripts/fastfill/web_keys.py:20-24, :103, :183` | `ROOT = HERE.parents[1]; ROOT / "web_keys.json"` etc. (+ desktop symlink) |
| `option_mappings.json` | `scripts/fastfill/option_mappings.py:18-19` | **already env-aware** (`FASTFILL_OPTION_MAPPINGS` → `_ROOT/option_mappings.json`) |
| `jobs.json` | `scripts/jobs_lock.py:29`, `scripts/dedup_jobs.py:477`, `scripts/get_job.py`, `session_timing_report.py`, dashboard | `Path(__file__).parent.parent / "jobs.json"` |
| `application_tracker.xlsx` | `scripts/tracker.py` | root-relative |
| `blocked_urls.json` | `scripts/blocked_urls.py` | root-relative |
| `addresses.json` | `scripts/pick_address.py` | root-relative (stays; it's a fixture) |
| `resumes/`, `listings/`, `logs/` | `tailor_resume.py`, `resume_publish.py`, scrapers, `dashboard/server.py` (**56 path refs — in-flight**) | root-relative |

**`dashboard/server.py` alone has ~56 path references and is being edited by the India work right now** — that's the single biggest reason to (a) land the config module, (b) wait for the India merge, and (c) migrate dashboard last.

### 5.3 Special cases

- **`web_keys.py` desktop symlink** (`~/Desktop/Command Center/Documents/web_keys.json`) points into a user-specific path. For export this must be disabled/guarded (e.g. only create when `JOBHUNTER_PRIVATE_DIR == ROOT`). Note for the owner.
- **`.bak*` files** (`profile.json.bak*`, `jobs.json.bak*`): move into `private/` too; they contain the same PII.
- **`USER.md` / `MEMORY.md` / `memory/`**: agent-personal. Recommend relocating under `private/` (or a `private/agent/`), and **untracking `USER.md`** (`git rm --cached USER.md`, keep a `USER.example.md` template).

---

## 6. Export strategy (share code, not data)

Three options evaluated; **recommend Option A + C together**.

### Option A — `.gitignore` + committed dummy fixtures (baseline, DONE)
- Private files are gitignored; **fixtures** (`*.example.json`, `addresses.json`, dummy resume PDFs) are committed.
- A contributor clones, copies each `*.example.json` → real name, fills in their own data.
- **Pros:** zero tooling, works with normal git sharing. **Cons:** relies on discipline; doesn't produce a self-contained "clean copy" for non-git sharing (zip/Drive).

### Option B — `git archive` clean copy
- `git archive` only emits *tracked* files, so it's PII-free **by construction** (secrets aren't tracked). `git archive --format=zip -o /tmp/job-hunter-share.zip HEAD`.
- **Pros:** guaranteed clean, trivial. **Cons:** only includes committed content — misses the new (uncommitted) example templates until they're committed; can't run without the owner first committing fixtures.

### Option C — `export.sh` (rsync allowlist, DONE)
- Copies **code + docs + fixtures** into a clean dir, **excluding** every PII/secret/artifact path, then **verifies** no known-secret filename slipped through and fails loudly if so. Works from the working tree (no commit needed) and produces a runnable, shareable folder with dummy data wired in.
- **Pros:** self-contained, non-git-dependent, defense-in-depth verification. **Cons:** must keep the exclude list in sync with `.gitignore` (script derives excludes from a single list to reduce drift).

**Recommendation:** keep the gitignore+fixtures baseline (A) for day-to-day, use `export.sh` (C) to hand someone a clean zip/folder, and optionally `git archive` (B) as a belt-and-suspenders check that tracked content is clean.

### Onboarding a new contributor
Add a `README.md` "Quickstart" (proposal — not auto-created to avoid clobbering any in-flight README plans):
1. `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt` *(note: no `requirements.txt` exists yet — recommend generating one from `.venv`)*.
2. `cp fixtures/profile.example.json profile.json` (and `credentials.example.json`, `web_keys.example.json`, `.env.example` → `.env`). Fill with **your own** data — never commit them.
3. Run the dashboard / scripts as documented in `PLAYBOOK.md`.
4. Everything real lives in `private/` (git-ignored); never put real data anywhere else.

---

## 7. Phased migration plan

### Phase 0 — SAFE, additive, done in this pass (no approval needed)
- ✅ Harden `.gitignore` (merge; nothing removed).
- ✅ Add `.cursorignore` so the agent can't read real PII files.
- ✅ Add dummy templates: `profile.example.json`, `credentials.example.json`, `web_keys.example.json`, `.env.example`.
- ✅ Create `private/` (git-ignored) + `private/README.md`.
- ✅ Add `export.sh` (read-only; moves nothing).

### Phase 1 — SAFE, needs a tiny review (no file moves)
- Add `scripts/jobhunter_paths.py` with **root defaults** (behaviour-identical). Adopt it in *new* code only.
- Untrack `USER.md` (`git rm --cached`) and add `USER.example.md`. *(1-line safety win.)*
- Generate `requirements.txt` (e.g. `pip freeze` curated) for onboarding.

### Phase 2 — RISKY, owner approval + coordinate with India work
- **Wait for the India-discovery feature to merge** (it edits `dashboard/server.py`, `scripts/scout.py`, `scrape_ats.py`, `dedup_listings.py`, `write_discovered_jobs.py`, `discovery_filters.py`, `ats_companies.json`, and adds `scrape_internshala/hirist/cutshort/adzuna.py`). Do not move any of those before then.
- Set `JOBHUNTER_PRIVATE_DIR=./private`; `git mv` PII/secret/artifact files into `private/`; swap hardcoded paths to `jobhunter_paths.*` **file-by-file, running tests after each** (`scripts/` has many `test_*.py`).
- Migrate `dashboard/server.py` **last** (56 refs) and disable the `web_keys.py` desktop symlink under export.

### Phase 3 — Optional cosmetic
- `docs/` consolidation, optional `src/` grouping, `README.md` quickstart.

### Owner follow-ups requiring sign-off
1. Approve Phase 2 file moves (and confirm timing vs. India work).
2. Decide `private/` vs `data/private/` naming.
3. Untrack `USER.md`? (recommended yes).
4. Any real secret ever committed historically? (current HEAD is clean; if history matters, a separate `git filter-repo` scrub would be needed — **not** done here.)

---

## 8. What this pass changed (safe/non-breaking)
See git status; summary in the chat report. No files moved, renamed, or deleted; no commits made.
