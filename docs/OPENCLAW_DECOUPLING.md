# OpenClaw Decoupling — Feasibility + Design Study

**Question:** *Can we make the existing system behave EXACTLY as it does today,
but with ZERO dependency on the OpenClaw binary/runtime — reliably?*

**Author:** job-hunter agent, 2026-08-08. **Method:** exhaustive grep of the repo
(excluding `agent-transcripts/`) for every OpenClaw touchpoint, read of the
surrounding code, plus inspection of the live OpenClaw config
(`~/.openclaw/openclaw.json`), the registered cron job
(`openclaw cron list --all --json`), `~/.openclaw/exec-approvals.json`, and the
agent session store (`~/.openclaw/agents/job-hunter/sessions/`).

> This is a **study only**. No code was changed and nothing was committed.
> It complements [`PORTABILITY.md`](PORTABILITY.md) (blocker #4/#15 = OpenClaw)
> and [`DOCKER.md`](DOCKER.md) (host-only features), which already flag OpenClaw
> as an out-of-band dependency but do **not** analyze whether it can be removed.

---

## Status: IMPLEMENTED (2026-08-08)

The phased plan below has been **implemented**. The dashboard, discovery,
tailoring, and fastfill now run with the `openclaw` binary/runtime
**completely absent** — no required `openclaw` invocation remains on any core
path (discovery, dashboard start, fill, cron, double-start guard, approvals).
See [`OPENCLAW_REMOVED.md`](OPENCLAW_REMOVED.md) for the per-touchpoint change
log, the new `DEEPSEEK_API_KEY` / one-time-login requirements, and the
`stuck`-fallback behavior. New modules: `dashboard/agent_runner.py`,
`dashboard/scheduler.py`, `dashboard/run_guard.py`,
`dashboard/approvals_store.py`, plus `ensure_partyrock_browser_direct()` in
`scripts/chrome_for_testing.py`. The residual `openclaw` calls in
`scripts/chrome_for_testing.py` (legacy `ensure_openclaw_*`) and
`scripts/session_timing_report.py` (analytics) are **optional / dead-fallback
only** and are never reached on the core paths.

---

## 0. Verdict (read this first)

**Yes — with caveats.** Everything OpenClaw does for this project **except the
general-purpose LLM agent** can be replaced with **behavior-identical** local
mechanisms at low risk. The agent itself can be reproduced **functionally and
with the same underlying model** (DeepSeek-V4-Flash — see §3), but **not
byte-for-byte**: its exact judgment, tool-loop shape, latency, and session-memory
semantics are properties of the OpenClaw harness, not of anything in this repo.

Two things can only be *approximated*, never made identical without OpenClaw:

1. **LLM agent judgment/latency** on the open-ended fallback turns (fetch-a-JD,
   manual-PartyRock, "fix the LaTeX", "fix the bug in `tracker.py`"). A different
   harness ⇒ different prompts, different tool schemas, different token accounting
   ⇒ different (though comparable) behavior.
2. **Self-healing code/LaTeX repair** (`run_agent_message` asked to *"find a bug
   in tracker.py and fix it"* or *"read the .tex file, fix the LaTeX error,
   recompile"*). That is a full coding-agent capability; reproducing it reliably
   requires adopting an agent framework, and even then the fixes won't be
   identical.

**The authenticated browser is NOT a blocker** — the code already contains a
direct Chrome-for-Testing launch fallback that doesn't need OpenClaw (§5.4).

### Rating tally (by mechanism)

| Rating | Count | Mechanisms |
|---|---|---|
| 🟢 behavior-identical, trivial/low effort | **7** | cron scheduling; double-start guard; managed browser launch; approvals allowlist; `_openclaw_env` PATH shim; CLI passthrough; media-inbound dir |
| 🟡 doable, minor behavior change | **3** | live-activity event stream (`sessions tail`); auto-retry trigger; abort-a-running-turn |
| 🔴 hard / cannot be truly identical | **1** | the LLM **agent turns** (`run_agent_message`) — functional-equivalent is 🟡, byte-identical is 🔴 |

### Hardest 2–3 items

1. **`run_agent_message` open-ended recovery** — self-repairing `tracker.py`
   bugs and LaTeX compile errors. No deterministic replacement; needs a real
   coding agent and still won't match.
2. **The agent's browser+shell tool loop** for the *no-JD manual pipeline* and
   *manual-PartyRock fallback* — an autonomous "fetch page → save fields →
   tailor → fill → stop at ready_for_review" loop. Functionally replaceable by
   the existing fastfill/Skyvern path + a thin DeepSeek runner, but that changes
   the shape of what happens.
3. **Session memory/resume + the live-activity event stream** the auto-retry
   loop keys off of (`session.ended` from `openclaw sessions tail`). You must
   emit an equivalent event log from whatever replaces the agent.

### What the user must supply

- **A `DEEPSEEK_API_KEY`** (same provider/model the OpenClaw agent already uses —
  `deepseek/deepseek-v4-flash`; the repo *already* calls
  `https://api.deepseek.com/v1` directly in `scripts/fastfill/flash_leftovers.py`).
- **A one-time browser login** captured into a persistent user-data dir (PartyRock
  + any ATS logins) — this is a one-time human step, same as today.
- **A decision** on the agent-replacement harness: build a minimal DeepSeek
  tool-loop, or adopt an OSS agent runtime. (Optional if you accept dropping the
  open-ended self-repair turns and surfacing them as `stuck` for a human.)

---

## 1. What OpenClaw actually is here

`OPENCLAW_BIN` (`dashboard/server.py:76`) resolves to `openclaw`
(`/opt/homebrew/bin/openclaw`, a Node CLI). The dashboard shells out to it for
six distinct behaviors. Critically, from `~/.openclaw/openclaw.json`:

- The **`job-hunter` agent** runs model **`deepseek/deepseek-v4-flash`** with
  tools `exec` (`security: full`, `ask: off`) and `browser`, workspace
  `/Users/job/.openclaw/workspace/job-hunter`.
- Auth profile `deepseek:manual` (`mode: api_key`) — i.e. OpenClaw is calling the
  **DeepSeek API with an API key**, then wrapping it in a tool-use agent loop with
  browser + shell + filesystem tools and persistent sessions.

So "the OpenClaw agent" = **DeepSeek-V4-Flash + an agentic harness (tools +
sessions)**. The model is reproducible via a direct API key. The *harness* is the
part that isn't in this repo.

---

## 2. Touchpoint map (every call site)

Grep of `openclaw`, `OPENCLAW_BIN`, `run_agent_message`, `_openclaw_env`,
`_ensure_openclaw_managed_browser`, `is_session_running`, `cron`, `sessions`,
`approvals`, `browser` across the repo (excluding `agent-transcripts/`).

### 2.1 Agent turns — `run_agent_message` (the crux) 🔴

Command built at `dashboard/server.py:5116`:

```
openclaw agent --agent job-hunter --session-key <key> --message <msg> \
               --timeout <s> --thinking <medium|high>
```

Sessions live in `~/.openclaw/agents/job-hunter/sessions/<uuid>.jsonl` (+
`.trajectory.jsonl`); **`--session-key` resumes the same conversation**, so the
agent has memory of the job across turns. Each job's `session_key` is stored on
the job record (`job["session_key"]`, e.g. `agent:job-hunter:job-<id>`);
discovery uses `agent:job-hunter:discovery`.

| # | Call site | What the agent is asked to DO | Feature |
|---|---|---|---|
| A | `2770` (`answer_question` → thread) | Resume the job's session with the human's answer to a `stuck` question and continue the pipeline | **Answering stuck jobs** |
| B | `2428` | *"scripts/tracker.py exited N … Check for a bug in tracker.py and fix it."* | **Discovery auto-recovery** (open-ended code repair) |
| C | `4646` | No JD on file: fetch the real posting from `apply_url`, save via `update_job.py`, then tailor + fill, stop at `ready_for_review` | **No-JD manual pipeline** (browser+shell loop) |
| D | `4832` | Automated PartyRock tailor failed: run `./open_partyrock.sh`, drive PartyRock manually, save `resume.tex`, hand back | **Tailor fallback** (browser loop) |
| E | `4930` | `tectonic` failed: read the `.tex`, fix the LaTeX, recompile, continue fill | **PDF-compile self-repair** |
| F | `5654` (reconcile loop) | *"Your previous turn ended without a stopping point … continue from there"* | **Auto-retry once** |
| G | `6196` (`_handle_command_decision`) | Resume with `"Approved. Run this exact command: …"` or a denial | **Exec-approval resume** |
| H | `5174` `abort_gateway_session` | Connect a throwaway client to the same `--session-key` to force `OPENCLAW_DIRECT_ABORT` of a running turn | **Cancel a running turn** |

**Behavior when the binary is absent today:** `run_agent_message` still runs;
`subprocess.Popen` yields exit `127` (missing `node`/binary), and the `exit_code
!= 0` branch (`5134`) marks the job `stuck` with *"Agent fill aborted (exit
127). Never submitted."*. **It degrades, it does not crash** the server — but the
entire class of agent assistance (A–H) is lost. Discovery auto-recovery (B) just
leaves discovery failed; tailor/compile fallbacks (D/E) leave the job `stuck`.

### 2.2 Session state — `is_session_running` / `gateway_running_session_keys` 🟢

- `gateway_running_session_keys` (`474`): `openclaw sessions list --agent
  job-hunter --active 60 --json` → set of running session keys. Used by
  `is_session_running` (`485`) as the **double-Start guard** (and the "what's
  running" display).
- **Absent today:** the call is wrapped in `try/except` (`480`) → returns empty
  set → the guard silently degrades to local process tracking only. Non-fatal.
- The Start request path deliberately uses `_session_running_local` (`501`),
  which is **already OpenClaw-free** (in-process `_running_procs` + discovery
  flag), precisely to avoid the ~15s `sessions list` round-trip.

### 2.3 Live activity — `get_activity` (`sessions tail`) 🟡

- `get_activity` (`5258`): `openclaw sessions tail --agent job-hunter
  --session-key <key> --tail N` → parsed into activity events.
- Consumed by the **reconcile loop** (`5552`) to detect `session.ended` and
  decide auto-retry (F) vs. mark `stuck`, and by the dashboard "Live Activity"
  panel.
- **Absent today:** returns a single `error` event → the `session.ended`-driven
  auto-retry never fires; jobs fall through to the `to_orphan_stuck` path
  (`5615`) instead. Degraded but safe.

### 2.4 Managed browser — `_ensure_openclaw_managed_browser` 🟢 (login caveat 🟡)

- `dashboard/server.py:1475` → `scripts/chrome_for_testing.py:ensure_openclaw_partyrock_browser`.
- Uses `openclaw browser start` / `stop` / `open`, and `openclaw config get/set
  browser.executablePath`. Target: a **Chrome-for-Testing** instance on
  **`--user-data-dir=~/.openclaw/browser/openclaw/user-data`**, **CDP port 18800**.
  `tailor_resume.py` attaches to `http://127.0.0.1:18800` (CDP_URL) to drive
  PartyRock; login/cookies persist in that user-data dir.
- **Key finding:** the code **already** has a full OpenClaw-free fallback,
  `launch_cft_with_openclaw_profile` (`chrome_for_testing.py:194`), that launches
  CfT directly with exactly those flags when `openclaw browser start` is missing
  or brings up the wrong (daily-Chrome) binary. So the managed browser is *nearly
  decoupled already* — OpenClaw is one of two code paths, and the non-OpenClaw one
  exists.
- **Absent today:** `_ensure_openclaw_managed_browser(required=True)` on the
  tailor path raises → job goes `stuck` with *"run `./open_partyrock.sh`"*; the
  prewarm/`required=False` callers warn and continue. But with the direct-CfT
  fallback wired as primary, the port comes up regardless of OpenClaw.

### 2.5 Scheduling — `openclaw cron` 🟢

- `_find_cron_job` (`5214`): `openclaw cron list --all --json`; toggle
  (`7254`): `openclaw cron enable|disable <id>`; schedule (`7299`): `openclaw cron
  edit <id> --cron <expr>`. Endpoints `GET /api/cron`, `POST /api/cron/toggle`,
  `POST /api/cron/schedule`.
- **What the cron job actually does (from the live registration):**

```json
{ "name": "job-hunter-daily", "schedule": {"kind":"cron","expr":"0 9 * * *"},
  "payload": {"kind":"command","argv":["sh","-lc",
              "curl -s -X POST http://127.0.0.1:8787/api/discover"]} }
```

  It is **just a scheduled `curl` to the dashboard's own `POST /api/discover`**.
  (It is currently `"enabled": false` with `consecutiveErrors: 7` — exit 7 =
  curl couldn't connect because the dashboard wasn't up at 09:00.) OpenClaw adds
  nothing here beyond being a cron daemon.

### 2.6 Approvals — `openclaw approvals allowlist add` 🟢

- `_handle_command_decision` (`6188`): on approve, `openclaw approvals allowlist
  add --agent job-hunter "<binary>*"`, then `_ensure_job_hunter_ask_off` (`2774`)
  rewrites `~/.openclaw/exec-approvals.json` to keep `ask: off`.
- This only exists because the **agent's `exec` tool** asks for command approval.
  Its whole purpose is to feed the agent. If the agent is removed/replaced, this
  is moot; if a replacement agent keeps an exec tool, it's a plain local JSON
  file (which is exactly what `exec-approvals.json` already is).

### 2.7 Env / identity / misc 🟢

- `_openclaw_env` (`2049`): only prepends `/opt/homebrew/bin` + `/usr/local/bin`
  to `PATH` so the `#!/usr/bin/env node` shebang on `openclaw` resolves under a
  GUI/LaunchAgent launch. **Needed only to invoke `openclaw`**; unneeded once
  `openclaw` is gone.
- `_handle_cli` (`7208`): dashboard "CLI" box runs `openclaw <args>` — a
  dev/debug convenience passthrough. Drop or repoint.
- `INBOUND_MEDIA_DIR = ~/.openclaw/media/inbound` (`106`, `4995`): resumes are
  copied here **only because the OpenClaw browser tool restricts uploads to that
  dir** (`4989` comment). Plain Playwright fills (fastfill) upload from anywhere,
  so this dir stops mattering without the agent browser tool.
- `EXEC_APPROVALS_FILE`, `OPENCLAW_BROWSER_USER_DATA` — see §2.6/§2.4.
- `scripts/session_timing_report.py` — analytics over the session `.jsonl`
  store; a reporting nicety, out of scope for "system works as today."
- **Out of scope** (agent-workspace niceties, not part of the dashboard/pipeline):
  Discord, heartbeat, `MEMORY.md`, gateway messaging. None are invoked by the
  dashboard or the discovery/tailor/fill pipeline.

---

## 3. Is there a direct API path already? (yes)

The OpenClaw agent's model is **`deepseek/deepseek-v4-flash`**. The repo **already
calls that exact model directly**, without OpenClaw:

```823:875:scripts/fastfill/flash_leftovers.py
        or os.environ.get("DEEPSEEK_API_KEY")
        ...
        or "https://api.deepseek.com/v1"
        ...
    model = os.environ.get("OPENAI_COMPATIBLE_MODEL_NAME") or "deepseek-v4-flash"
        ...
            f"{base}/chat/completions",
```

`fixtures/.env.example` already reserves `DEEPSEEK_API_KEY` (and
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). So:

- **The LLM itself is fully reproducible** via `DEEPSEEK_API_KEY` →
  `https://api.deepseek.com/v1/chat/completions`, same model string.
- **The Skyvern-assisted / fastfill path is already OpenClaw-free** and is the
  code's *preferred* fill mechanism (the agent-browser fill is explicitly avoided
  for Ashby/Bumble — `4485`–`4488`). This means the biggest agent job
  (filling forms) has a deterministic, non-agent replacement **that already
  exists**.

What is **not** in the repo is the agent *harness* — the tool-calling loop, the
browser tool, the exec tool, the filesystem tool, and the session persistence
that turn a DeepSeek chat completion into an autonomous worker. That is the only
genuinely OpenClaw-shaped piece.

---

## 4. Replacement design + reliability ratings

| Touchpoint | Replacement mechanism | Rating | Why |
|---|---|---|---|
| **Cron** (`job-hunter-daily`) | `launchd` (`~/Library/LaunchAgents/*.plist`) or system `cron`, or an in-process `threading.Timer` in the dashboard, firing the same `POST /api/discover`. `/api/cron/*` reads/writes a local `cron_settings.json` instead of `openclaw cron`. | 🟢 | The payload is literally a curl to our own endpoint; the "cron" is byte-for-byte reproducible. In-process timer is even more robust (runs only while the dashboard is up — which is when discovery can succeed anyway). |
| **Double-start guard** (`sessions list`) | Local PID/lock per session key (reuse `scripts/jobs_lock.py`'s `fcntl.flock` pattern) or the existing `_running_procs` map. Start path **already** uses the local check. | 🟢 | The gateway check only catches turns that outlive the CLI client — an OpenClaw-specific concern that disappears when we own the runner. Local tracking is authoritative for local processes. |
| **Managed browser** (`browser start/stop/open`) | Promote the **already-present** `launch_cft_with_openclaw_profile` to primary: `subprocess.Popen(CfT, --user-data-dir=<persistent>, --remote-debugging-port=18800)`; `tailor_resume.py` attaches to CDP 18800 unchanged. Or `playwright.chromium.launch_persistent_context(user_data_dir=…)`. | 🟢 | Mechanism is identical (same CfT binary, same dir, same port). `openclaw browser start` was only ever a wrapper around this launch. |
| — login persistence for that browser | Log in once into the persistent `user-data-dir`; cookies/session persist across launches exactly as today. | 🟡 | Behavior-identical **after** a one-time human login. The login itself can't be automated (and mustn't be — PLAYBOOK). Same as today. |
| **Approvals allowlist** | If replacement agent keeps an exec tool: a local `allowlist.json` gate. If not: delete the touchpoint. | 🟢 | Already a local JSON file; only the `openclaw approvals allowlist add` writer needs swapping for a direct `json.dump`. |
| **`_openclaw_env` PATH shim** | Remove (only existed to find the `openclaw` node shebang). | 🟢 | Dead once `openclaw` is gone. |
| **CLI passthrough** (`_handle_cli`) | Remove the dashboard CLI box, or repoint at safe local scripts. | 🟢 | Dev convenience, not a pipeline feature. |
| **media-inbound dir** | Remove; plain Playwright uploads read `resumes/<id>/resume.pdf` directly. | 🟢 | The dir only existed for the OpenClaw browser tool's upload sandbox. |
| **Live activity** (`sessions tail`) | Have the replacement runner append structured events to a per-session log file (`logs/agent_turn_<key>.log` is already written); reconcile reads that file for `session.ended`-equivalent markers. | 🟡 | Reproducible, but the event vocabulary/timing differs from OpenClaw's, so the "Live Activity" panel content changes shape. |
| **Auto-retry trigger** | Key off the replacement runner's own exit/last-event, or make reconcile purely process-based (the `orphan_stuck` path already exists). | 🟡 | Functionally equivalent; the exact "retry vs. stuck" decision boundary shifts slightly. |
| **Abort a running turn** (`abort_gateway_session`) | Kill the local runner process/`asyncio` task (we own it) instead of the `OPENCLAW_DIRECT_ABORT` trick. | 🟡 | Cleaner locally, but only if the replacement runner is a killable local process. |
| **Agent turns** (`run_agent_message` A–H) | **(a)** Route all form-fill through the existing fastfill/Skyvern path (already default). **(b)** Build a thin DeepSeek-V4-Flash tool-loop (browser-over-CDP + shell + fs) for the narrow fallbacks C/D/F/G. **(c)** For B (fix `tracker.py`) and E (fix LaTeX): either adopt a coding-agent framework, or drop the self-repair and surface `stuck` for a human. | 🔴 (byte-identical) / 🟡 (functional) | The model is reproducible; the *harness* is not. Different prompts/tool-schemas/latency ⇒ comparable-but-different behavior. Open-ended self-repair (B/E) can't be made reliable without a real coding agent, and even then won't match. |

---

## 5. Phased implementation plan (low-risk first)

Each phase is independently shippable and leaves the system working.

### Phase 1 — Non-agent touchpoints (🟢, ~0.5–1 day, very low risk)
Removes OpenClaw from everything except the LLM agent. After this, the dashboard,
discovery, tailoring, and fastfill run with **zero** `openclaw` invocations.

1. **Cron** → in-process scheduler (or launchd) hitting `POST /api/discover`;
   `/api/cron/*` back onto a local `cron_settings.json`.
2. **Managed browser** → make `launch_cft_with_openclaw_profile` the primary in
   `_ensure_openclaw_managed_browser`; keep the OpenClaw call only as an optional
   fast-path. Drop `openclaw browser stop` in favor of the argv-matched kill that
   already exists as the fallback (`_stop_openclaw_managed_browser`).
3. **Double-start guard** → drop `gateway_running_session_keys`; rely on
   `_session_running_local` + a `flock` lock file.
4. **Approvals / `_openclaw_env` / CLI passthrough / media-inbound** → remove or
   localize per §4.
- **Risk:** low. All four already have local fallbacks or are pure conveniences.
- **New input required:** one-time PartyRock/ATS login into the persistent
  user-data dir (already the status quo).

### Phase 2 — Activity + retry plumbing (🟡, ~0.5 day, low risk)
5. Replace `get_activity`/`sessions tail` consumption with reads of the
   already-written `logs/agent_turn_<key>.log`; adjust the reconcile loop's
   `session.ended` detection accordingly. Live Activity panel shows our own
   events.
- **Risk:** low-moderate. Mostly affects UX polish (activity feed) and the
  auto-retry boundary, not correctness. The `orphan_stuck` safety net stays.

### Phase 3 — The agent (🟡/🔴, 2–5+ days, moderate–high risk)
6. **Route all fills through fastfill/Skyvern** (already preferred); delete the
   agent-browser fill fallback paths (C/D partially) so fills never need the
   agent. **Provide `DEEPSEEK_API_KEY`.**
7. **Thin DeepSeek runner** for the remaining narrow, well-bounded turns (answer
   stuck question G/A, "continue from where you left off" F): a small tool-loop
   with a CDP browser tool (reuse `tailor_resume.py`/`partyrock_tabs.py` CDP
   plumbing), a shell tool restricted to `scripts/*.py`, and a per-key session
   log for resume. Emit `session.ended`-style events for Phase 2.
8. **Open-ended self-repair (B: fix `tracker.py`, E: fix LaTeX)** — decide:
   (i) adopt an OSS coding-agent runtime, or (ii) drop it and mark `stuck` for a
   human. **Recommend (ii)** initially — these are rare failure paths and (ii) is
   reliable, whereas any agent here is inherently non-deterministic.
- **Risk:** moderate for 6–7; high/unbounded for 8-(i). Behavior is
  *functionally equivalent*, explicitly **not** byte-identical.

---

## 6. What CANNOT be reliably reproduced without OpenClaw

1. **Byte-identical agent behavior/latency.** The agent is DeepSeek-V4-Flash
   *inside OpenClaw's harness*. Any replacement harness changes prompt assembly,
   tool schemas, ret/timeout handling, and token accounting → outputs and timing
   differ (even if quality is comparable). "Exactly as today" for the agent is
   **not achievable**; "equivalent" is.
2. **Open-ended self-repair** (fix a real bug in `tracker.py`; fix arbitrary
   LaTeX and recompile). This is a general coding-agent capability. Reproducing it
   *reliably* is a project in itself and still won't match OpenClaw's output. Best
   handled by surfacing `stuck` for a human unless you commit to a coding-agent
   runtime.
3. **OpenClaw session memory/resume semantics.** `--session-key` resumes a
   specific stored conversation with OpenClaw's exact context-window/memory
   policy. A local runner can persist and resume its own transcripts, but the
   compaction/recall behavior won't be identical.

Everything else (cron, browser launch, double-start guard, approvals, activity,
env, uploads) **can** be made behavior-identical, and much of it already has a
non-OpenClaw code path in the repo today.

---

## 7. Evidence appendix (key sites)

- Binary + env: `dashboard/server.py:76` (`OPENCLAW_BIN`), `:2049` (`_openclaw_env`).
- Agent turns: `:5095`–`5171` (`run_agent_message`), call sites `:2428, 2770,
  4646, 4832, 4930, 5654, 6196`; abort `:5174`.
- Sessions: `:467` (`gateway_running_session_keys`), `:485`
  (`is_session_running`), `:501` (local), `:5258` (`get_activity`).
- Managed browser: `:1475` (`_ensure_openclaw_managed_browser`), `:1553`
  (`_stop_...`); `scripts/chrome_for_testing.py:194` (direct-CfT fallback), `:222`
  (`ensure_openclaw_partyrock_browser`), `:22`/`:23` (user-data dir + CDP 18800);
  `scripts/tailor_resume.py:54` (`CDP_URL`).
- Cron: `:5214, 5254, 5957, 7246, 7269`; live job payload = `curl -s -X POST
  http://127.0.0.1:8787/api/discover` (currently disabled).
- Approvals: `:6188`, `:2774`; `~/.openclaw/exec-approvals.json`.
- Model/provider: `~/.openclaw/openclaw.json` → agent `job-hunter` model
  `deepseek/deepseek-v4-flash`, auth profile `deepseek:manual`. Direct API path
  already in repo: `scripts/fastfill/flash_leftovers.py:823–875`.
