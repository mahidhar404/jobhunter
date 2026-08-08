# Outbox — results from Cursor Executor

Cursor posts one result file per completed (or blocked) task.

## Filename convention

```
RESULT-{task_id}.md
```

Example: `RESULT-TASK-20260731-103000-airwallex-zip-after-location.md`

## Post a result (Executor)

```bash
skyvern_runtime/venv/bin/python scripts/manager_bridge/post_result.py \
  --task-id TASK-20260731-103000-airwallex-zip-after-location \
  --verdict DONE \
  --summary "Zip fills after Springfield location commit on Airwallex Ashby form."
```

Manager reads results here before assigning the next task. See `PROTOCOL.md` for the full result template.
