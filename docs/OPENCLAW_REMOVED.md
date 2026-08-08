# OpenClaw Removed — Change Log & Operating Notes

**Date:** 2026-08-08. **Goal:** make the entire job-hunter system run with the
`openclaw` binary/runtime **completely absent**, preserving current behavior as
closely as possible. This implements the phased decoupling plan, whose full
feasibility/design study is now folded into the [Appendix](#appendix-openclaw-decoupling-design-study-folded-in-2026-08-08)
below (formerly the separate `OPENCLAW_DECOUPLING.md`, merged 2026-08-08 to keep
a single canonical OpenClaw doc). Prior work already made the
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

---

# Appendix: OpenClaw decoupling design study (folded in 2026-08-08)

> This appendix is the former `docs/OPENCLAW_DECOUPLING.md` feasibility/design
> study, merged here (2026-08-08) so there is a single canonical OpenClaw doc.
> It was a **study**; the plan below has since been **implemented** — see the
> per-touchpoint change log above for the as-built result. Kept for the
> touchpoint map, reliability ratings, and evidence appendix (unique here).

**Original question:** *Can we make the existing system behave EXACTLY as it
does today, but with ZERO dependency on the OpenClaw binary/runtime — reliably?*
**Method:** exhaustive grep of the repo (excluding `agent-transcripts/`) for
every OpenClaw touchpoint, read of the surrounding code, plus inspection of the
live OpenClaw config (`~/.openclaw/openclaw.json`), the registered cron job,
`~/.openclaw/exec-approvals.json`, and the agent session store.

## A0. Verdict

**Yes — with caveats.** Everything OpenClaw does for this project **except the
general-purpose LLM agent** can be replaced with **behavior-identical** local
mechanisms at low risk. The agent itself can be reproduced **functionally and
with the same underlying model** (DeepSeek-V4-Flash — see §A3), but **not
byte-for-byte**: its exact judgment, tool-loop shape, latency, and session-memory
semantics are properties of the OpenClaw harness, not of anything in this repo.

Two things can only be *approximated*, never made identical without OpenClaw:

1. **LLM agent judgment/latency** on the open-ended fallback turns (fetch-a-JD,
   manual-PartyRock, "fix the LaTeX", "fix the bug in `tracker.py`"). A different
   harness ⇒ different prompts, tool schemas, token accounting ⇒ different
   (though comparable) behavior.
2. **Self-healing code/LaTeX repair** (`run_agent_message` asked to *"find a bug
   in tracker.py and fix it"* or *"read the .tex file, fix the LaTeX error,
   recompile"*). That is a full coding-agent capability; reproducing it reliably
   requires adopting an agent framework, and even then the fixes won't be
   identical.

**The authenticated browser is NOT a blocker** — the code already contains a
direct Chrome-for-Testing launch fallback that doesn't need OpenClaw (§A2.4).

### Rating tally (by mechanism)

| Rating | Count | Mechanisms |
|---|---|---|
| 🟢 behavior-identical, trivial/low effort | **7** | cron scheduling; double-start guard; managed browser launch; approvals allowlist; `_openclaw_env` PATH shim; CLI passthrough; media-inbound dir |
| 🟡 doable, minor behavior change | **3** | live-activity event stream (`sessions tail`); auto-retry trigger; abort-a-running-turn |
| 🔴 hard / cannot be truly identical | **1** | the LLM **agent turns** (`run_agent_message`) — functional-equivalent is 🟡, byte-identical is 🔴 |

## A1. What OpenClaw actually is here

`OPENCLAW_BIN` (`dashboard/server.py`) resolves to `openclaw` (a Node CLI). The
dashboard shells out to it for six distinct behaviors. From
`~/.openclaw/openclaw.json`: the **`job-hunter` agent** runs model
**`deepseek/deepseek-v4-flash`** with tools `exec` (`security: full`, `ask: off`)
and `browser`. Auth profile `deepseek:manual` (`mode: api_key`) — OpenClaw is
calling the **DeepSeek API with an API key**, then wrapping it in a tool-use
agent loop with browser + shell + filesystem tools and persistent sessions. So
"the OpenClaw agent" = **DeepSeek-V4-Flash + an agentic harness (tools +
sessions)**. The model is reproducible via a direct API key; the *harness* is
the part that isn't in this repo.

## A2. Touchpoint map (every call site)

### A2.1 Agent turns — `run_agent_message` (the crux) 🔴

Command was: `openclaw agent --agent job-hunter --session-key <key> --message
<msg> --timeout <s> --thinking <medium|high>`. Sessions lived in
`~/.openclaw/agents/job-hunter/sessions/<uuid>.jsonl`; `--session-key` resumes
the same conversation (per-job memory). Call sites (A–H): answer stuck question;
discovery auto-recovery (fix `tracker.py`); no-JD manual pipeline; tailor
fallback; PDF-compile self-repair (fix LaTeX); auto-retry once (reconcile);
exec-approval resume; abort a running turn. **When the binary is absent:**
`subprocess.Popen` yields exit `127`, and the job is marked `stuck` ("Agent fill
aborted (exit 127). Never submitted."). It degrades, it does not crash.

### A2.2 Session state — `is_session_running` / `gateway_running_session_keys` 🟢
`openclaw sessions list … --json` → running session keys, used as the
double-Start guard. Absent → wrapped in try/except → empty set → degrades to
local process tracking. The Start path already uses the OpenClaw-free
`_session_running_local`.

### A2.3 Live activity — `get_activity` (`sessions tail`) 🟡
`openclaw sessions tail …` → activity events consumed by the reconcile loop to
detect `session.ended`. Absent → single error event → auto-retry never fires,
jobs fall to `to_orphan_stuck`. Degraded but safe.

### A2.4 Managed browser — `_ensure_openclaw_managed_browser` 🟢 (login caveat 🟡)
`openclaw browser start/stop/open` targeting Chrome-for-Testing on
`--user-data-dir=~/.openclaw/browser/openclaw/user-data`, CDP port 18800. **Key
finding:** the code already has a full OpenClaw-free fallback
(`launch_cft_with_openclaw_profile`) that launches CfT directly with those flags.
So the managed browser is nearly decoupled already.

### A2.5 Scheduling — `openclaw cron` 🟢
The registered cron job is **just a scheduled `curl` to `POST /api/discover`**
(`{"argv":["sh","-lc","curl -s -X POST http://127.0.0.1:8787/api/discover"]}`,
`0 9 * * *`). OpenClaw adds nothing beyond being a cron daemon.

### A2.6 Approvals — `openclaw approvals allowlist add` 🟢
On approve, writes to `~/.openclaw/exec-approvals.json` and keeps `ask: off`.
Exists only to feed the agent's `exec` tool; it's a plain local JSON file.

### A2.7 Env / identity / misc 🟢
`_openclaw_env` only prepends `/opt/homebrew/bin` + `/usr/local/bin` to `PATH` so
the `openclaw` node shebang resolves under a GUI/LaunchAgent launch — unneeded
once `openclaw` is gone. `_handle_cli` = dashboard CLI-box passthrough (dev
convenience). `INBOUND_MEDIA_DIR` existed only for the OpenClaw browser tool's
upload sandbox; plain Playwright uploads from anywhere.
`scripts/session_timing_report.py` = analytics over the session store, out of
scope for "system works as today."

## A3. Is there a direct API path already? (yes)

The OpenClaw agent's model is **`deepseek/deepseek-v4-flash`**, and the repo
already calls that exact model directly (no OpenClaw) in
`scripts/fastfill/flash_leftovers.py` via `DEEPSEEK_API_KEY` →
`https://api.deepseek.com/v1/chat/completions`. `fixtures/.env.example` already
reserves `DEEPSEEK_API_KEY`. So the LLM itself is fully reproducible; the
Skyvern/fastfill fill path is already OpenClaw-free and is the code's *preferred*
fill mechanism. What is **not** in the repo is the agent *harness* (tool loop,
browser/exec/fs tools, session persistence).

## A4. Replacement design + reliability ratings

| Touchpoint | Replacement mechanism | Rating |
|---|---|---|
| **Cron** | `launchd`/system cron or an in-process timer firing the same `POST /api/discover`; `/api/cron/*` on a local `cron_settings.json`. | 🟢 |
| **Double-start guard** | Local PID/lock per session key (reuse `jobs_lock.py` `fcntl.flock`) or the existing `_running_procs` map. | 🟢 |
| **Managed browser** | Promote the already-present `launch_cft_with_openclaw_profile` to primary; `tailor_resume.py` attaches to CDP 18800 unchanged. | 🟢 |
| — login persistence | One-time human login into the persistent user-data dir; cookies persist. | 🟡 |
| **Approvals allowlist** | Local `allowlist.json` gate if a replacement exec tool remains; else delete. | 🟢 |
| **`_openclaw_env` PATH shim** | Remove (only existed to find the node shebang). | 🟢 |
| **CLI passthrough** | Remove the dashboard CLI box or repoint at safe local scripts. | 🟢 |
| **media-inbound dir** | Remove; Playwright uploads read `resumes/<id>/resume.pdf` directly. | 🟢 |
| **Live activity** | Replacement runner appends structured events to a per-session log; reconcile reads it for a `session.ended`-equivalent. | 🟡 |
| **Auto-retry trigger** | Key off the runner's own exit/last-event, or make reconcile purely process-based. | 🟡 |
| **Abort a running turn** | Kill the local runner process/task (we own it). | 🟡 |
| **Agent turns (A–H)** | (a) route fills through fastfill/Skyvern (default); (b) thin DeepSeek tool-loop for narrow fallbacks; (c) for fix-`tracker.py`/fix-LaTeX, adopt a coding agent or drop and mark `stuck`. | 🔴 byte / 🟡 functional |

## A5. Phased implementation plan (as executed)

- **Phase 1 — Non-agent touchpoints (🟢):** cron → in-process scheduler;
  managed browser → direct CfT primary; double-start guard → local +
  `flock`; approvals / `_openclaw_env` / CLI passthrough / media-inbound removed
  or localized. New input: one-time PartyRock/ATS login (status quo).
- **Phase 2 — Activity + retry plumbing (🟡):** replace `sessions tail`
  consumption with reads of the runner's own event log; adjust reconcile's
  `session.ended` detection. `orphan_stuck` safety net stays.
- **Phase 3 — The agent (🟡/🔴):** route all fills through fastfill/Skyvern
  (provide `DEEPSEEK_API_KEY`); thin DeepSeek runner for narrow turns; for
  open-ended self-repair, recommend surfacing `stuck` for a human rather than a
  non-deterministic coding agent.

## A6. What CANNOT be reliably reproduced without OpenClaw

1. **Byte-identical agent behavior/latency.** The agent is DeepSeek-V4-Flash
   *inside OpenClaw's harness*; any replacement harness changes prompt assembly,
   tool schemas, retry/timeout handling, token accounting → outputs/timing
   differ (quality comparable). "Exactly as today" for the agent is **not
   achievable**; "equivalent" is.
2. **Open-ended self-repair** (fix a real bug in `tracker.py`; fix arbitrary
   LaTeX and recompile). A general coding-agent capability; best handled by
   surfacing `stuck` for a human unless you commit to a coding-agent runtime.
3. **OpenClaw session memory/resume semantics.** `--session-key` resumes a
   specific stored conversation with OpenClaw's exact context/compaction policy;
   a local runner can persist/resume its own transcripts but won't match.

Everything else (cron, browser launch, double-start guard, approvals, activity,
env, uploads) **can** be made behavior-identical, and much already had a
non-OpenClaw code path in the repo.

## A7. Evidence appendix (key sites)

- Binary + env: `dashboard/server.py` `OPENCLAW_BIN`, `_openclaw_env`.
- Agent turns: `run_agent_message` + call sites (answer/recover/no-JD/tailor/
  compile/reconcile/approval), plus `abort_gateway_session`.
- Sessions: `gateway_running_session_keys`, `is_session_running`, local check,
  `get_activity`.
- Managed browser: `_ensure_openclaw_managed_browser` / `_stop_...`;
  `scripts/chrome_for_testing.py` direct-CfT fallback + user-data dir/CDP 18800;
  `scripts/tailor_resume.py` `CDP_URL`.
- Cron: live payload = `curl -s -X POST http://127.0.0.1:8787/api/discover`.
- Approvals: `_handle_command_decision`, `_ensure_job_hunter_ask_off`,
  `~/.openclaw/exec-approvals.json`.
- Model/provider: `~/.openclaw/openclaw.json` agent `job-hunter` model
  `deepseek/deepseek-v4-flash`, auth `deepseek:manual`. Direct API path already
  in `scripts/fastfill/flash_leftovers.py`.
