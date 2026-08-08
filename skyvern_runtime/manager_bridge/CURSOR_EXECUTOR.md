# Cursor Executor — manager bridge

How Cursor reads the bridge and executes Manager tasks.

## Trigger

When Yogesh says **"Execute manager bridge task"** or similar:

1. Read `skyvern_runtime/manager_bridge/STATUS.md`
2. List inbox: `skyvern_runtime/venv/bin/python scripts/manager_bridge/list_inbox.py`
3. Pick highest priority pending task (P0 > P1 > P2; FIFO within priority)
4. Ack and update status:

```bash
cd /Users/job/.openclaw/workspace/job-hunter
PY=skyvern_runtime/venv/bin/python

$PY scripts/manager_bridge/ack_task.py TASK-... --update-status
$PY scripts/manager_bridge/heartbeat.py --note "Started TASK-..." --check-chrome
```

## Execution

- Read the task file from `in_progress/` (markdown or JSON)
- Load skills: `.cursor/skills/job-hunter-fastfill/SKILL.md`, `.cursor/skills/job-hunter-fill-safety/SKILL.md`
- Respect **all constraints** in the task (dummy only, never submit, max 1 fill CfT, etc.)
- Touch only files listed unless a blocker requires adjacent fix (document in result)
- Run verification before claiming done:
  - `regression_gates.py` for code changes
  - Headed `fast_fill.py` for fill tasks (with task-specified flags)
  - Unit tests for touched modules

## Post result

```bash
$PY scripts/manager_bridge/post_result.py \
  --task-id TASK-... \
  --verdict DONE|PARTIAL|BLOCKED|FAILED \
  --summary "..." \
  --file-changed path/to/file.py \
  --artifact skyvern_runtime/real_job_results/.../ \
  --blocker "CAPTCHA timeout" \
  --next "Suggested follow-up" \
  --archive

$PY scripts/manager_bridge/heartbeat.py \
  --note "Finished TASK-... verdict=DONE" \
  --check-gates --check-chrome
```

## Verdicts

| Verdict | When |
|---------|------|
| **DONE** | All acceptance criteria met, gates green |
| **PARTIAL** | Meaningful progress; Manager should follow up |
| **BLOCKED** | CAPTCHA, Chrome cap, missing credentials, human needed |
| **FAILED** | Regression or fill attempt failed; include logs |

## Multi-agent pattern

For complex tasks, Cursor may spawn subagents but **one bridge task = one result file**. Coordinate internally; merge into a single `post_result.py` call.

## Do not

- Post real PII in task/result files
- Click Submit on live forms
- Run parallel headed fills (fill CfT cap = 1; exclude UI/PartyRock)
- Mark DONE without running gates when code changed

## Paths (absolute)

| Item | Path |
|------|------|
| Bridge | `/Users/job/.openclaw/workspace/job-hunter/skyvern_runtime/manager_bridge` |
| Inbox | `.../manager_bridge/inbox/` |
| Outbox | `.../manager_bridge/outbox/` |
| Ten-unseen log | `/Users/job/.openclaw/workspace/job-hunter/skyvern_runtime/real_job_results/TEN_UNSEEN_RUN.md` |
| Bug status | `/Users/job/.openclaw/workspace/job-hunter/skyvern_runtime/real_job_results/BUG_FIX_STATUS.md` |
