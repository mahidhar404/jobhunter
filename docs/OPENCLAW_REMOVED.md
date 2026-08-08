# OpenClaw Removed — Change Log & Operating Notes

**Date:** 2026-08-08. **Goal:** make the entire job-hunter system run with the
`openclaw` binary/runtime **completely absent**, preserving current behavior as
closely as possible. This implements the phased plan in
[`OPENCLAW_DECOUPLING.md`](OPENCLAW_DECOUPLING.md). Prior work already made the
`openclaw`/`tectonic` binary resolution non-fatal when missing; this replaces
the actual features so they function without the binary.

No files were moved into `private/` (that migration is owner-gated and
deferred) — all changes are in place.

## New requirements (vs. before)

- **`DEEPSEEK_API_KEY`** — needed for agent turns (stuck-question answering,
  discovery/pipeline self-repair). The runner reads it from, in order: env
  (`OPENAI_COMPATIBLE_API_KEY` / `DEEPSEEK_API_KEY`) → repo-root
  `web_keys.json` → `credentials.json` → `.env` → `skyvern_runtime/.secrets.env`.
  Same provider/model the OpenClaw agent used (`deepseek-v4-flash` via
  `https://api.deepseek.com/v1`), already used directly by
  `scripts/fastfill/flash_leftovers.py`.
  - **Without a key the system still fully runs.** Any agent turn degrades
    gracefully: the job is surfaced as `stuck` for a human (identical to the
    old "openclaw binary missing → exit 127 → stuck" behavior). Discovery
    self-recovery simply leaves discovery failed, as before.
- **One-time browser login** into the persistent Chrome-for-Testing user-data
  dir (`~/.openclaw/browser/openclaw/user-data`, CDP :18800) for PartyRock /
  ATS logins. Same one-time human step as today; cookies persist across
  launches. Run `./open_partyrock.sh` once if login is needed.

## What runs with a `DEEPSEEK_API_KEY` vs. degrades to `stuck`

| Turn | With key | Without key |
|---|---|---|
| Answer a stuck screening question | DeepSeek tool-loop answers/continues | `stuck` (human answers in dashboard) |
| No-JD manual pipeline (fetch JD → tailor → fill) | best-effort tool-loop | `stuck` |
| Manual PartyRock tailor fallback | best-effort (drives via `./open_partyrock.sh` + scripts) | `stuck` |
| PDF/LaTeX compile self-repair | **best-effort** (may fall back to `stuck`) | `stuck` |
| Discovery auto-recovery ("fix tracker.py") | **best-effort** (may fall back to `stuck`) | discovery fails (as before) |
| Auto-retry once (reconcile) | re-runs the turn | n/a |

**Honesty note (in code + here):** open-ended "write code to fix myself" turns
(fix a bug in `tracker.py`, fix arbitrary LaTeX) are **best-effort**. The model
is reproducible; the OpenClaw *harness* is not — judgment/latency differ, and
these rare paths may simply fall back to `stuck`, which is the safe outcome.

## Per-touchpoint change log

| # | Touchpoint | Before (OpenClaw) | After (OpenClaw-free) |
|---|---|---|---|
| 1 | **Scheduling** | `openclaw cron` running `curl -X POST /api/discover` | `dashboard/scheduler.py` — in-process `DiscoveryScheduler` POSTs `/api/discover` at the configured local time; `/api/cron/*` read/write `logs/cron_settings.json`. `_find_cron_job()` returns a cron-shaped dict so the dashboard toggle/schedule UI is unchanged. |
| 2 | **Managed browser** | `openclaw browser start/stop/open` + `config set` | `ensure_partyrock_browser_direct()` in `scripts/chrome_for_testing.py` launches Chrome-for-Testing directly on the same persistent user-data dir + CDP :18800. `_ensure_openclaw_managed_browser` / `_stop_openclaw_managed_browser` no longer call `openclaw`. One-time-login persistence preserved. |
| 3 | **Double-start guard** | `openclaw sessions list` (`gateway_running_session_keys`) | `gateway_running_session_keys()` → `agent_runner.active_turn_keys()` (in-process, authoritative). `dashboard/run_guard.py` adds a cross-process `fcntl.flock` backstop (same pattern as `scripts/jobs_lock.py`) held during each agent turn. Same guarantee: no overlapping discovery/fill runs. |
| 4 | **Approvals allowlist** | `openclaw approvals allowlist add` | `dashboard/approvals_store.py` writes the allowlist glob straight to the local `~/.openclaw/exec-approvals.json` and keeps `ask: off`. |
| 5 | **Env / CLI / media** | `_openclaw_env` PATH shim; `_handle_cli` passthrough; inbound media dir | `_openclaw_env` no longer invoked (kept as dead code); the dashboard CLI box passthrough is disabled (returns a clear "disabled" JSON instead of shelling a missing binary); inbound-media copy left in place (harmless, no binary call). |
| 6 | **Live activity + auto-retry** | `openclaw sessions tail` → `session.ended` | `agent_runner` appends structured events to `logs/agent_events_<key>.jsonl` (incl. terminal `session.ended`); `get_activity()` reads those. Reconcile's auto-retry keys off the same `session.ended` event; in-progress agent turns are also recognized via the active-turn registry so they aren't prematurely orphaned. |
| 7 | **Agent turns** (`run_agent_message`) | `openclaw agent --session-key … --message …` subprocess | `dashboard/agent_runner.py` — a direct DeepSeek OpenAI-compatible tool-loop (tools: `read_file`, `write_file`, `run_shell`, `finish`) run in-process. Same `run_agent_message(session_key, message, timeout_s=…)` interface at all call sites. Cancellation via a per-key `threading.Event` (replaces `OPENCLAW_DIRECT_ABORT`). Hard safety: shell denylist blocks submit/CAPTCHA/destructive commands; system prompt enforces PLAYBOOK red lines. |

## Residual `openclaw` references (all safe / optional)

- `scripts/chrome_for_testing.py` — legacy `ensure_openclaw_executable_is_cft`
  / `ensure_openclaw_partyrock_browser` still contain `openclaw` calls, but the
  dashboard no longer calls them (it uses `ensure_partyrock_browser_direct`).
  They self-skip when `openclaw` is absent (`oc = … or resolve_openclaw_bin()`
  → `None`) and fall back to a direct CfT launch. `open_partyrock.sh` may still
  use them for the one-time human login; that's optional.
- `scripts/session_timing_report.py` — a standalone analytics script over the
  OpenClaw session store; not invoked by the dashboard/pipeline.
- `OPENCLAW_BIN` constant + `OPENCLAW_BROWSER_USER_DATA` path in
  `dashboard/server.py` remain: the former is now unused (documents the
  historical env override `JOBHUNTER_OPENCLAW_BIN`); the latter is just the
  persistent CfT user-data dir path (no binary invocation).

## Tests

New (run as scripts with `.venv/bin/python3`):
`dashboard/test_scheduler.py`, `dashboard/test_run_guard.py`,
`dashboard/test_approvals_store.py`, `dashboard/test_agent_runner.py`
(incl. the no-key `stuck` fallback), `dashboard/test_openclaw_absent.py`
(server wiring end-to-end: no-key → `stuck`, cron endpoints on local settings,
openclaw-free abort/guard).

## Not changed (too risky without owner sign-off)

- The `private/` file migration (owner-gated, deferred).
- Open-ended coding-agent self-repair reliability (B: fix `tracker.py`, E: fix
  LaTeX) — left best-effort with a `stuck` fallback rather than adopting a
  full OSS coding-agent runtime.
- Unrelated in-flight working-tree changes / the uncommitted India feature.
