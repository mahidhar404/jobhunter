# FastFill Offline Gym

Train and score form-fill **before** live ATS. Two gyms:

1. **ATS gym** (`ats/`) — synthetic Greenhouse/Workday-shaped widgets with `gold.json`
2. **FormFactory** (`formfactory/`) — vendor templates + gold field values

## Control plane (offline → live)

```bash
# 1) Offline gates + gym smoke
skyvern_runtime/venv/bin/python scripts/fastfill/improvement_cycle.py --phase baseline

# 2) Loop gym until SLO (writes OFFLINE_GATE_PASS.json)
skyvern_runtime/venv/bin/python scripts/fastfill/improvement_cycle.py --phase train_offline

# 3) Arm live canary (requires offline pass)
skyvern_runtime/venv/bin/python scripts/fastfill/improvement_cycle.py --phase gate_live

# 4) One diversity canary (eval_suite --limit 7) then HARD STOP
skyvern_runtime/venv/bin/python scripts/fastfill/improvement_cycle.py --phase canary_live --limit 7

# Or all offline→canary in one shot:
skyvern_runtime/venv/bin/python scripts/fastfill/improvement_cycle.py --phase offline_then_canary

# Status (arm/done/offline artifacts)
skyvern_runtime/venv/bin/python scripts/fastfill/improvement_cycle.py --status
skyvern_runtime/venv/bin/python scripts/fastfill/live_gate.py --status
```

Artifacts under `skyvern_runtime/real_job_results/`:

| File | Meaning |
|------|---------|
| `OFFLINE_GATE_PASS.json` | Gym + units green |
| `LIVE_CANARY_ARMED` | Live eval/train allowed once |
| `LIVE_CANARY_DONE.json` | Canary finished — live blocked until re-arm |

Override only with `--force-live` / `FASTFILL_FORCE_LIVE=1`.

## ATS gym

See [ats runner](ats/runner.py). Cases under `ats/cases/*/`.

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/gym/ats/runner.py --self-test
```

## FormFactory

Vendor clone at `formfactory/vendor/` (~58MB). See [formfactory/README.md](formfactory/README.md).

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/gym/formfactory_runner.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/gym/formfactory_runner.py --full
```

If vendor is missing:

```bash
git clone --depth 1 https://github.com/formfactory-ai/formfactory \
  scripts/fastfill/gym/formfactory/vendor
```

## Safety

- Dummy PII only (`Ada Lovelace`, `ada.lovelace+gym@example.com`)
- Never Submit / never CAPTCHA
- Gym scoring reads committed DOM — no external POST
