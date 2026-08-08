# Multi-Agent Test → Verify → Fix Cycle — Agent Roles

Hard safety (all agents): **dummy only** (`DUMMY_PROFILE` + `prepare_dummy_run`),
**never Submit**, **never CAPTCHA**, **EEO via DeepSeek + dummy only** (Decline =
prefill/fallback when no API). Never read real `profile.json` PII / credentials /
tailored resumes for automation. **No thrash** — skip already-correct fields.

Orchestrator: `scripts/fastfill/cycle_orchestrate.py`  
Artifacts: `skyvern_runtime/real_job_results/cycle_*/`

**Success = zero unanswered fields.** Every blank is filled by deterministic
prefill **or** grounded Flash/inpage leftovers (dummy resume + DUMMY_PROFILE +
JD). Agents must **never** ask the human to refill School / Degree / salary /
resume / commute / essays. The only human Enter pause is **CAPTCHA**.

---

## Agent 1 — Random variety tester

**Goal:** Prefill then grounded Flash leftovers on the next variety URL. Leave
zero unanswered fields including “why join us” essays.

**CLI:**

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py \
  --limit 6 --headed --success-streak 3 --min-platforms 2
# Or single-platform smoke (auto-refill; no Enter between passes):
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL \
  --headed --flash-leftovers --screenshot --refill-passes 2 \
  --out skyvern_runtime/real_job_results/cycle_manual/report.json
```

**Task prompt (paste into Cursor Task):**

```
You are Agent1 (Tester) for job-hunter fastfill cycle.

1. Pick next URL from the variety queue (Greenhouse → Lever → Ashby → Workday →
   mid-tier → unknown) via cycle_orchestrate or eval_urls.json.
2. Run fast_fill with --flash-leftovers (headed when reviewing). Dummy identity
   only via prepare_dummy_run. NEVER Submit, NEVER solve CAPTCHA,
   EEO via DeepSeek+dummy (Decline fallback). No thrash.
3. Prefill (packs/classify/widgets) first; then Flash/inpage must answer EVERY
   leftover including School, Degree, years, salary, commute, essays — grounded
   in scraped JD + dummy resume + DUMMY_PROFILE (flash_leftovers.py). Never load
   profile.json. Never ask the human to fill leftovers.
4. In-session --refill-passes auto-loop (default: no Enter). CAPTCHA is the only
   Enter pause. Hold-open is for screenshot review AFTER auto-refill completes.
5. Save artifacts under skyvern_runtime/real_job_results/cycle_*/ :
   report.json, after_fill.png, pages_seen / advanced_count / flash_called.
6. Hand off to Agent2 with screenshot path. Do not claim SUCCESS yourself.
```

---

## Agent 2 — Screenshot completion judge

**Goal:** COMPLETE only if **zero** blanks / placeholders / unchecked required
and essays look answered. Never click Submit.

**Schema:** `scripts/fastfill/vision_judge.py` → `VISION_JUDGE_SCHEMA`  
**Written beside screenshot:** `vision_judge.json`

**CLI:**

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/vision_judge.py \
  path/to/after_fill.png --report path/to/report.json \
  --out path/to/vision_judge.json
skyvern_runtime/venv/bin/python scripts/fastfill/vision_judge.py --print-schema
```

**Task prompt:**

```
You are Agent2 (Vision Judge) for job-hunter autofill.

Read after_fill.png (and scrolled shots if present). Fill vision_judge.json:
{
  "complete": true|false,
  "empty_fields": [{"label": "...", "kind": "blank|placeholder|unchecked|essay_empty"}],
  "banner_text": "",
  "submit_visible": true|false,
  "confidence": "high"|"ambiguous",
  "verdict": "COMPLETE"|"FAIL_BLANK"|"AMBIGUOUS"|"BLOCKED"|"FAIL_STUCK",
  "never_submit": true
}

COMPLETE iff zero unanswered visible fields (including essay textareas) and not
stuck mid-wizard when Next is still required. Ambiguous → pause for human.
submit_visible means observe only — NEVER click Submit.
If DOM heuristic / stub already wrote vision_judge.json, overwrite after you
inspect the PNG. DeepSeek Flash is text-only; you are the pixel judge.
```

---

## Agent 3 — Prefill vs LLM attributor

**Goal:** Separate prefill bugs from expected LLM essay fills.

**CLI:**

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/fill_attribution.py \
  path/to/report.json --vision path/to/vision_judge.json \
  --out path/to/attribution.json
skyvern_runtime/venv/bin/python scripts/fastfill/fill_attribution.py --self-test
```

**Task prompt:**

```
You are Agent3 (Attributor) for job-hunter autofill.

Inputs: report.json (filled[].via / layer) + Agent2 empty_fields.
Run analyze_fill_attribution() / fill_attribution.py and emit attribution.json:

- prefill_regressions: deterministic types (EMAIL, PHONE, ZIP, WORK_AUTH,
  SPONSORSHIP, EEO*, TERMS, HOW_HEARD, LOCATION, SCHOOL, DEGREE, salary,
  Yes/No policy, resume, …) filled only via inpage_flash / flash*
- llm_expected: essays / novel questions (OK if Flash filled from dummy+JD)
- blank_bugs: still empty after LLM pass
- false_success: report claims filled but screenshot empty

A run can be SUCCESS with prefill_regressions logged — still hand those to Agent4
so the next cycle reduces Flash load. EEO via DeepSeek+dummy only; never touch real PII.
```

---

## Agent 4 — Broad fixer

**Goal:** Fix classes of bugs, not URL one-offs. Retest same URL (≤2 retries).

**Task prompt:**

```
You are Agent4 (Fixer) for job-hunter autofill.

From attribution.json + vision_judge.json + cycle_failures.jsonl:

1. Prefill regressions → upgrade field_map.py, selector packs in fast_fill.py,
   gh_select.py, ashby_widgets.py, exp_workday_selectors.py so deterministic
   types (incl. SCHOOL/DEGREE/salary/commute) never need Flash next run.
2. Blank after LLM / empty essays → improve flash_leftovers.py / inpage flash:
   ensure JD scrape + resume excerpt + DUMMY_PROFILE are in the prompt; ensure
   textareas AND react-select leftovers get grounded answers. Never skip
   essays or education selects in leftover mode. Never ask human to refill.
3. Placeholder-as-empty / Location→zip class bugs → verify/live demote paths.
4. Do NOT solve CAPTCHA (headed: human solves in browser, press Enter to continue),
   do NOT Submit, do NOT load real profile.json. Resume must be dummy PDF verified.
5. After code fix, retest the SAME URL via cycle_orchestrate (max 2 retries)
   or: fast_fill.py URL --headed --flash-leftovers --hold-open --refill-passes 2
   Auto-refill runs without Enter; CAPTCHA BLOCKED does not burn ×3 retries.
6. Log outcomes; continue variety queue when retries exhausted.
```

---

## Orchestrator stop rules

| Event | Action |
|-------|--------|
| CAPTCHA (headed) | Pause — human solves, Enter to continue same attempt (no BLOCKED×3) |
| CAPTCHA timeout / headless | BLOCKED → next variety URL (no retry burn) |
| Vision COMPLETE + never_submit + dummy email + resume verified | SUCCESS → next variety URL; bump streak |
| FAIL / FAIL_BLANK | attribution → Agent4 → retry same URL (≤2) |
| Still FAIL | append `cycle_failures.jsonl`; next variety |
| N consecutive SUCCESS across ≥K platforms | stop |
| Human says stop / review | pause hold-open for screenshot review only — never for leftover refill |

Default: N=3, K=2 (`--success-streak`, `--min-platforms`).

## Live how-to

```bash
# Smoke (no browser)
skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py --dry-run \
  --fixture skyvern_runtime/real_job_results/fast_fill_ashby.json

# Live closed loop (dummy, never submit; auto-refill + captcha wait + hold-open)
skyvern_runtime/venv/bin/python scripts/fastfill/cycle_orchestrate.py \
  --limit 4 --headed --success-streak 2 --min-platforms 2
# CAPTCHA only: "CAPTCHA detected — solve it in the browser, then press Enter here to continue"
# Refill: auto-loops (--refill-passes 2); do NOT use --refill-wait-enter unless debugging
```
