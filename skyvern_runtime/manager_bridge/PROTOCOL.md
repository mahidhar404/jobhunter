# Manager ↔ Executor Protocol

File-based bridge between **Claude Code (Manager)** and **Cursor multi-agent (Executor)** for job-hunter fastfill improvement.

**Workspace root:** `/Users/job/.openclaw/workspace/job-hunter`  
**Bridge root:** `/Users/job/.openclaw/workspace/job-hunter/skyvern_runtime/manager_bridge`

## Roles

| Role | Tool | Responsibility |
|------|------|----------------|
| **Manager** | Claude Code CLI | Read status, assign focused tasks, evaluate results, iterate |
| **Executor** | Cursor agents | Ack task, implement/test, post result, update heartbeat |

No exotic APIs — only files under `manager_bridge/`.

## Directories

```
manager_bridge/
  PROTOCOL.md              ← this file
  CLAUDE_MANAGER_PROMPT.md ← paste into Claude Code
  CURSOR_EXECUTOR.md       ← Cursor agent instructions
  STATUS.md                ← living heartbeat (Cursor updates)
  inbox/                   ← Manager posts tasks
  in_progress/             ← Executor ack'd tasks
  outbox/                  ← Executor posts results
  archive/                 ← completed task + result pairs
  schema/task.schema.json  ← optional JSON task format
```

## Task lifecycle

```
Manager post_task.py  →  inbox/TASK-*.md
Executor ack_task.py  →  in_progress/TASK-*.md
Executor work + tests
Executor post_result.py → outbox/RESULT-TASK-*.md
Optional archive       →  archive/
```

## Task format (markdown — preferred)

**Filename:** `inbox/TASK-YYYYMMDD-HHMMSS-slug.md`

```markdown
---
id: TASK-20260731-103000-airwallex-zip
priority: P1
title: Fix Airwallex zip after location commit
created_at: 2026-07-31T10:30:00Z
created_by: claude-manager
status: pending
---

# Fix Airwallex zip after location commit

## Context

Job 3 ten-unseen (Ashby / Airwallex). Location word-by-word select is fixed
(`ten_unseen_03_airwallex_locfix/`). Residual: zip sometimes
`zip_field_not_found_after_location`.

## Acceptance criteria

- After Springfield IL location commit, zip field is found and filled with dummy zip
- `field_attempts.jsonl` shows verified zip read-back
- No thrash on already-committed location
- regression_gates.py PASS

## Constraints

- Dummy profile + fixture resume only — never profile.json PII
- Never click Submit / final Apply
- Max 1 **fill** Chrome-for-Testing main — fill-only `pgrep` before headed fill (exclude Helper / dashboard UI / OpenClaw PartyRock; see `BROWSER_CAP.md`)
- EEO via DeepSeek + DUMMY_PROFILE only
- CAPTCHA: pause for human, do not solve
- `--flash-leftovers --refill-passes 2` for ten-unseen fills

## Files to touch

- scripts/fastfill/ashby_widgets.py
- scripts/fastfill/test_verified_select.py (if needed)

## Done when

Headed retest on Airwallex URL produces zero zip leftover; result in outbox/.
```

## Task format (JSON — optional)

**Filename:** `inbox/TASK-YYYYMMDD-HHMMSS-slug.json`  
Schema: `schema/task.schema.json`

## Result format

**Filename:** `outbox/RESULT-{task_id}.md`

```markdown
---
task_id: TASK-20260731-103000-airwallex-zip
verdict: DONE | PARTIAL | BLOCKED | FAILED
posted_at: 2026-07-31T11:00:00Z
executor: cursor-executor
---

# Result: TASK-20260731-103000-airwallex-zip

## Verdict
DONE

## Summary
...

## Files changed
- scripts/fastfill/ashby_widgets.py

## Artifacts
- skyvern_runtime/real_job_results/ten_unseen_03_airwallex_zipfix/report.json

## Blockers
- (none)

## Next tasks suggested
- Continue ten-unseen job 4 (Personio / Ultralytics)
```

## CLI helpers

From repo root (`/Users/job/.openclaw/workspace/job-hunter`):

```bash
PY=skyvern_runtime/venv/bin/python

# Manager: post task
$PY scripts/manager_bridge/post_task.py \
  --title "Fix Airwallex zip after location" \
  --priority P1 \
  --acceptance "Zip fills after location commit" \
  --file scripts/fastfill/ashby_widgets.py

# Executor: list pending
$PY scripts/manager_bridge/list_inbox.py
$PY scripts/manager_bridge/list_inbox.py --all --json

# Executor: acknowledge
$PY scripts/manager_bridge/ack_task.py TASK-20260731-103000-airwallex-zip --update-status

# Executor: post result
$PY scripts/manager_bridge/post_result.py \
  --task-id TASK-20260731-103000-airwallex-zip \
  --verdict DONE \
  --summary "Zip fills after Springfield commit." \
  --file-changed scripts/fastfill/ashby_widgets.py \
  --artifact skyvern_runtime/real_job_results/ten_unseen_03_airwallex_zipfix/ \
  --archive

# Either side: heartbeat
$PY scripts/manager_bridge/heartbeat.py --note "Job 3 zip retest queued" --check-gates --check-chrome
```

## Manager preflight (every cycle)

Before posting a new task, read:

1. `manager_bridge/STATUS.md`
2. Latest files in `manager_bridge/outbox/`
3. `skyvern_runtime/real_job_results/TEN_UNSEEN_RUN.md`
4. `skyvern_runtime/real_job_results/BUG_FIX_STATUS.md`

## Evaluation criteria

Manager accepts work when:

- **Zero-blank fills** on target form (leftovers honest, not lied)
- **Dropdown select verified** — option clicked, read-back matches (`verified_select`)
- **No thrash** — `already_correct_skip` / `location_already_committed_skip` respected
- **Screenshot COMPLETE** only when vision_judge / page gate agrees (no heuristic false COMPLETE)
- **Unseen jobs** progress on ten-unseen queue without re-running Socure/Tax Relief/Rippling fixtures
- **Safety** — `never_submit: true`, dummy only, regression gates green

## Safety (non-negotiable)

- Dummy profile + per-run compiled PDF only
- Never Submit / final Apply
- Never solve CAPTCHA (headed pause for human)
- EEO: DeepSeek + DUMMY_PROFILE fictional demographics only
- Max **1 fill Chrome-for-Testing** — fill-only count before headed runs (exclude UI / PartyRock; `heartbeat.py --check-chrome` / `BROWSER_CAP.md`)
- Never read real `profile.json` PII or `credentials.json` for automation

## Example cycle

1. **Manager** reads STATUS + outbox + TEN_UNSEEN_RUN → posts P1 zip-fix task
2. **Yogesh** tells Cursor: "Execute manager bridge inbox task"
3. **Cursor** runs `list_inbox.py`, `ack_task.py`, spawns agent, runs headed fill + gates
4. **Cursor** runs `post_result.py --archive` + `heartbeat.py --check-gates`
5. **Manager** reads RESULT, updates plan, posts next task (job 4 ten-unseen)
