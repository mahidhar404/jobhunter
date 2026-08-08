# Claude Manager — post tasks here

Inbox is **empty**. Post new work with:

```bash
cd /Users/job/.openclaw/workspace/job-hunter
PY=skyvern_runtime/venv/bin/python

$PY scripts/manager_bridge/post_task.py \
  --title "Your task title" \
  --priority P1 \
  --context "What and why — URLs, artifact dirs, prior run ids." \
  --acceptance "Concrete pass signal 1" \
  --acceptance "Concrete pass signal 2" \
  --constraint "Dummy only; never Submit; max 1 fill CfT; CAPTCHA human wait" \
  --file scripts/fastfill/fast_fill.py \
  --done-when "Result posted to outbox/ with verdict and artifacts"
```

**Before posting:** read `STATUS.md`, `CURSOR_HANDSHAKE.md`, and latest `outbox/RESULT-*.md`.

**Verify Cursor is alive:** `skyvern_runtime/manager_bridge/CURSOR_HANDSHAKE.md`
