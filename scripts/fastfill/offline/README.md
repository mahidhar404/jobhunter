# Offline corpus tools

Not on the live fill path. Used for mining Skyvern HTML scrapes into type-label
evaluation data (`corpus.json` — structure only, no PII values).

```bash
# Requires SKYVERN_DB_PASSWORD + local Postgres
skyvern_runtime/venv/bin/python scripts/fastfill/offline/build_corpus.py
```
