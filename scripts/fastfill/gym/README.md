# FastFill Offline Gym

Train and score form-fill **before** live ATS. One gym:

**ATS gym** (`ats/`) — synthetic Greenhouse/Workday-shaped widgets with `gold.json`.

Gym green ≠ live. See `../GYM_VS_LIVE.md`. Dummy PII only; never Submit / never CAPTCHA.

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/gym/ats/runner.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/live_gate.py --status
```

Cases under `ats/cases/*/`. Live canary arming: `live_gate.py` (`--force-live` / `FASTFILL_FORCE_LIVE=1` to bypass).
