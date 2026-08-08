# Fast fill coverage matrix

**Universal** means: attempt every apply URL. Known ATS use selector packs;
unknown / non-ATS company career pages use the generic DOM path. Leftovers are
listed honestly. DeepSeek-V4-Flash is **opt-in only** (`--flash-leftovers`).

**Authoritative SLO + URL list:** [`eval_urls.json`](eval_urls.json) (enforced by
[`eval_suite.py`](eval_suite.py)). This doc mirrors those gates; do not invent
stricter numbers here without updating `eval_urls.json` first.

## Paths

| `detect_platform` | Pack | Fill path (`coverage_path`) | In `eval_urls` |
|-------------------|------|-----------------------------|----------------|
| greenhouse | GH_SELECTOR_PACK | selector_pack + extract + gh_select | yes (5) |
| workday | WD_SELECTOR_PACK | workday_multipage (Phase A–E) | yes (5) |
| lever | LEVER_SELECTOR_PACK | selector_pack + extract | yes (4) |
| ashby | ASHBY_SELECTOR_PACK | selector_pack + extract | yes (4) |
| icims | ICIMS_SELECTOR_PACK | selector_pack + extract **inside iframes** (`iframe_ctx`) | yes (2) |
| smartrecruiters | SMARTRECRUITERS_PACK | selector_pack + extract | yes (1) |
| workable | WORKABLE_PACK | selector_pack + extract | yes (1) |
| bamboohr | BAMBOOHR_PACK | selector_pack + extract | yes (1) |
| rippling | RIPPLING_PACK (`data-testid=input-*` + placeholders) | selector_pack + extract | yes (1) |
| personio | PERSONIO_PACK (`#field-*` + `documents.cv`) | selector_pack + extract | no |
| jobvite | JOBVITE_PACK | selector_pack + extract | no |
| taleo | TALEO_PACK (JSF dialogForm / personal_info / ResumeUpload) | selector_pack + extract | no |
| successfactors | SUCCESSFACTORS_PACK (fbclc_* + RCM/CSB) | selector_pack + extract | no |
| phenom | PHENOM_PACK (`cntryFields.*` / phoneWidget; white-label detect via utm/jobSeqNo/EXTERNAL*) | selector_pack + extract | no |
| dayforce | DAYFORCE_PACK (formcontrolname + camelCase) | selector_pack + extract | yes (1) |
| ukg | UKG_PACK (UltiPro FirstName/FamilyName/Email) | selector_pack + extract | no |
| recruitee, oracle | GENERIC-based thin pack | selector_pack + generic_dom | recruitee / oracle yes (1 each) |
| applytojob | APPLYTOJOB_PACK (resumator-*-value) | selector_pack + extract | yes (1) |
| breezy | BREEZY_PACK (cName/cEmail/cResume; skip hp_*) | selector_pack + extract | no |
| jobscore | JOBSCORE_PACK (candidate_card[*]) | selector_pack + extract | no |
| gem | GEM_PACK (label-adjacent nameless inputs) | selector_pack + extract | no |
| dover | DOVER_PACK (firstName/email/linkedinUrl) | selector_pack + extract | no |
| **unknown** (non-ATS) | **GENERIC_SELECTOR_PACK** | **generic_dom** (first-class, not a dead end) | yes (5) |

## SLO gates (per platform)

Source: `eval_urls.json` → `slo`. Checked in `eval_suite._check_row` / `_slo_rollup`.
Reachability blockers (`captcha`, `akamai`, `cloudflare`, `login_wall`, `job_closed`,
`404`, …) skip **fill-quality** gates; safety gates still apply.

| Gate key | Applies to | Rule | Enforced when |
|----------|------------|------|----------------|
| `slo.greenhouse.min_coverage` | `greenhouse` | coverage ≥ **0.9** | form reachable (no reachability blocker) |
| `slo.greenhouse.max_seconds` | `greenhouse` | elapsed ≤ **20** s | form reachable |
| `slo.greenhouse.flash_tokens` | `greenhouse` | Flash tokens **0** (Flash OFF) | always for suite |
| `slo.workday_contact.page_complete_or_fail_before_advance` | `workday` | complete contact **or** fail before ADVANCE — never SUCCESS after incomplete | always |
| `slo.workday_contact.validation_banners_on_advance` | `workday` | **0** validation banners after ADVANCE | always |
| `slo.cross_suite.never_submit` | **all** platforms | `never_submit=true`, `submit_clicked≠true` | always |
| `slo.cross_suite.advanced_incomplete` | **all** | count **0**; no SUCCESS with `advanced_incomplete` | always |
| `slo.cross_suite.flash_tokens_when_off` | **all** | Flash not invoked when suite runs Flash OFF | always |

### Platform → gate map (eval suite)

| Platform | Fill-quality SLO | Safety SLO |
|----------|------------------|------------|
| greenhouse | `slo.greenhouse` (coverage + latency + flash_tokens) | `cross_suite` |
| workday | `slo.workday_contact` (honest ADVANCE / no validation banner) | `cross_suite` |
| lever, ashby, icims, smartrecruiters, workable, bamboohr, recruitee, rippling, dayforce, applytojob, oracle, unknown | *(none yet — diagnostic coverage only)* | `cross_suite` |
| personio, jobvite, taleo, successfactors, ukg, breezy, jobscore, gem, dover, phenom | not in eval set | n/a until URL added |

Full measurable suite: `eval_suite.py` (not `fast_fill.py --matrix`). Matrix CLI is a
short smoke (GH / Lever / Ashby / Workday) that **embeds a copy of** `slo` for
scorecard alignment.

## Generic / unknown flow (never skip)

1. `entry_prepass` (Apply / Apply now / … via `button_gate`; never FINAL)
   - Prefer Apply links (`href` contains apply) over chatbot "I'm interested"
   - Aria suffixes like "Apply Now Software Engineer" still classify + click
2. SPA / iframe poll (`iframe_ctx.wait_for_form_spa`) after Apply — JD→`/apply`, delayed frames
3. **Ignore job-alert / newsletter email widgets** on JD pages (not form reached)
4. `GENERIC_SELECTOR_PACK` (autocomplete / name / email / phone / file)
5. `extract_form_fields.js` → `classify_field` → fill
6. Custom widgets (`fill_custom_widget` + aliases)
7. Learned allow-list (policy facts only)
8. Leftovers listed in `report["leftovers"]` / `report["flash"]` (Flash off by default)

## What still becomes leftovers (inherent)

- Free-text essays / cover letters (never invent)
- Salary expectations (policy needs job range)
- Employer-specific screening with no dummy answer
- Optional social URLs (Twitter/X) deliberately unclassified as portfolio
- Cloudflare / CAPTCHA / Akamai auth walls → honest `blocker`, never solve

## CLI / JSON emission

```bash
# Short smoke matrix (GH+Lever+Ashby+WD); prefers first eval_urls per platform
skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py --matrix --headless

# Full SLO eval (all eval_urls platforms)
skyvern_runtime/venv/bin/python scripts/fastfill/eval_suite.py
```

`fast_fill --matrix` writes `fast_fill_coverage_matrix.json` with:

- `experiment`: `fast_fill_coverage_matrix`
- `slo`: copy of `eval_urls.json` → `slo` (same gates as eval suite)
- `slo_source`: `scripts/fastfill/eval_urls.json`
- `rows[]`: per-platform filled / leftovers / coverage / blocker / never_submit flags

Scorecard: `scorecard_fast.py` expands matrix `rows` when individual
`fast_fill_<platform>.json` artifacts are absent.

## Safety (all paths)

Dummy only · never Submit · never CAPTCHA · never invent EEO (Decline) ·
verified fills only · no ADVANCE incomplete
