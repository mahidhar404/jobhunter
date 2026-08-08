# Inbox — tasks from Claude Manager

Claude Code (Manager) drops tasks here. Cursor Executor picks them up via `ack_task.py`.

## Filename convention

```
TASK-YYYYMMDD-HHMMSS-slug.md
TASK-YYYYMMDD-HHMMSS-slug.json
```

Example: `TASK-20260731-103000-airwallex-zip-after-location.md`

## Post a task (Manager)

From repo root:

```bash
skyvern_runtime/venv/bin/python scripts/manager_bridge/post_task.py \
  --title "Fix Airwallex zip after location" \
  --priority P1 \
  --context-file /path/to/notes.md
```

Or paste the markdown template from `PROTOCOL.md` / `CLAUDE_MANAGER_PROMPT.md`.

## Lifecycle

1. **inbox/** — pending (Manager posted)
2. **in_progress/** — Cursor acknowledged (`ack_task.py`)
3. **outbox/** — result posted (`post_result.py`)
4. **archive/** — paired task + result moved when done

List pending: `scripts/manager_bridge/list_inbox.py`
