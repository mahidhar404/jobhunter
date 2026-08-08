# Mining adaptations (ChamPro / auto-apply / Instaply / Skyvern)

Source: Aug 2026 mining pass. Keep Playwright orchestration; mine widget-commit
ideas — do **not** vendor ChamPro’s Claude plugin or auto-submit pipelines.

## Hard rules (unchanged)

- Never final Submit / Apply
- Never solve CAPTCHA
- Never invent EEO
- Autofill experiments: dummy / `--test-mode` only

## Priority table

| Pri | Change | Inspired by | Status |
|-----|--------|-------------|--------|
| P0 | Workday fiber `searchSelect` (Tab `onKeyDown` + `promptOption`) | ChamPro | Implemented |
| P0 | Hard guard-words (mis-map worse than no map) | ChamPro | Implemented |
| P0 | Completeness = `gaps()` after Save, not coverage % | ChamPro | Implemented |
| P1 | Option-alias learning store | auto-apply | Implemented |
| P1 | End-of-page contamination re-read | ChamPro | Implemented |
| P1 | Scoped batch in-page fill (simple fields) | ChamPro | Implemented |
| P2 | Persist `scan.json` + `plan.json` | auto-apply | Implemented |
| P2 | Dual inventory extract (unknown ATS) | Skyvern | Deferred |
| P3 | Resume-first when ATS autofills from resume | ChamPro | If wipe seen |
| Avoid | LLM-first browser-use / Skyvern agent as default | — | Do not |
| Avoid | Auto-submit | auto-apply | Do not |

## Already shipped (do not re-litigate)

- `nudge_listbox_after_type` (symptom fix for How-Heard)
- `web_keys`, honesty Ready, vision Ready gate
- IL ≠ Idaho soft match; US_RESIDENCE `\bunit\b` classify fix

## Live residuals (proof)

See `skyvern_runtime/real_job_results/proof_matrix/VISUAL_GRADES.md`:

- Quantiphi Gate A (How-Heard + previous-worker): **PASS** (`row_contact_searchselect/`)
- Residual: State `countryRegion` Idaho≠Illinois pack incomplete; resume upload probe
- Unknown ATS Cloudflare row: BLOCKED (never-CAPTCHA)

## ChamPro `searchSelect` (root fix)

Typing via `setVal` does **not** trigger Workday async search → “No Items.”
Root path: native value setter + fiber `onChange` + fiber
`onKeyDown({ key: 'Tab', target: { value } })` → wait → click
`[data-automation-id="promptOption"]`. Prefer unique token-scored match;
escalate on ambiguity. Keep `nudge_listbox_after_type` as fallback.

## Implementation order

1. fiber `searchSelect` + previous-worker harden → headed Quantiphi Gate A
2. guard-words → unit + GH regression Gate B
3. gaps-after-Save → Ready wiring Gate C
4. option_mappings learner Gate D
5. contamination + scoped batch Gate E
6. scan/plan artifacts; VISUAL_GRADES rollup; real re-proof only after A–C
