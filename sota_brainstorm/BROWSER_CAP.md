# BROWSER CAP — hard max 1 headed Chromium fill

**Master Orchestrator — IMMEDIATE (2026-07-31)**  
Root cause (`CHROME_CRASH_DIAGNOSIS.md`): ~8 headed Chrome-for-Testing + `hold_seconds=3600` on an **8 GB** Mac → OOM / `SIGABRT` / closed targets.

## Hard rules (all agents)

1. **Max 1 headed** Chrome-for-Testing **browser main** at a time. Not 1–2. Not “just one more.”
2. Before any `--headed` launch, count **fill** mains only (exclude Helper /
   renderer, dashboard UI profile, and OpenClaw PartyRock CDP):

```bash
pgrep -lf 'Google Chrome for Testing' | grep -v Helper | grep -v crashpad \
  | grep -v dashboard_ui_profile | grep -v '--app=http://127.0.0.1:8787' \
  | grep -v openclaw/user-data | grep -v '--remote-debugging-port=18800'
# If count >= 1 → DO NOT launch headed. Wait for Master / ORCHESTRATION.md queue.
```

   Code gate: `fast_fill.count_chrome_for_testing_mains` / hybrid refuse use the
   same exclusions (CHR3-003). Raw `pgrep chrome|chromium|playwright` over-counts.

3. Code gate: `fast_fill` / `cycle_orchestrate` **refuse** headed launch when ≥1 Chrome-for-Testing main already running (`blocker=headed_cap`). Override only with `FASTFILL_FORCE_HEADED=1` (emergency; document why).
4. **Hold ≤120s** for auto / variety / cycle. Defaults: `DEFAULT_HEADED_HOLD_SECONDS=90`, `HOLD_OPEN_SECONDS=90`, `VARIETY_MAX_HOLD_SECONDS=120`. Long hold only via explicit `--hold-seconds N` **and** `FASTFILL_ALLOW_LONG_HOLD=1` (human CAPTCHA review of **one** URL).
5. **Never** stack `--hold-open` / 3600s in multi-agent fleets.
6. Serialize under Master: claim slot in `BROWSER_QUEUE.md`; single queue in `../ORCHESTRATION.md`.
7. Prefer `--headless` for timing / matrix when headed slot is busy.

## Check before launch

```bash
pgrep -lf 'Google Chrome for Testing' | grep -v Helper | grep -v crashpad \
  | grep -v dashboard_ui_profile | grep -v '--app=http://127.0.0.1:8787' \
  | grep -v openclaw/user-data | grep -v '--remote-debugging-port=18800'
pgrep -fl 'fast_fill.py'
```

If ≥1 fill main → **stop**.

## Kill policy (Master only)

- Kill stale holds / excess mains; leave ≤1 live headed (usually the ACTIVE queue job).
- Do **not** kill an active CAPTCHA wait while human is solving.
- Document PIDs in `BROWSER_KILL_LOG.md` and `../ORCHESTRATION.md`.

## Variety CLI (canonical)

```bash
# Only when Chrome mains == 0
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py '<URL>' \
  --headed --flash-leftovers --refill-passes 2 --hold-seconds 90 --captcha-wait \
  --out skyvern_runtime/real_job_results/sota_brainstorm/<run>/report.json
```

Safety unchanged: dummy only · never Submit · never CAPTCHA solve · EEO Decline.
