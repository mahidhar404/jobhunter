# Gym / unit tests vs live headed fills

**Verdict: YES — misleading for production / live confidence.**

As of 2026-08-12. Dummy-only · never submit. Flight recorder (separate) is the live truth path; this doc is the honesty audit of offline confidence.

Evidence snapshot: `RELIABILITY_STATUS.md` live NXP gate **FAIL** (`reached_review: false`, stuck on contact `pack_incomplete` / `empty_readback` on address line2 + county) while gym + unit suites stay green and `regression_gates --tier1` can still celebrate **gym** + **offline score of a prior live artifact** as if that were merge confidence.

---

## Hard evidence (live red, gym green)

| Live failure (NXP / Workday) | Gym / unit status |
|------------------------------|-------------------|
| FoS alias thrash / settle (2227Z) | `workday_education_fos_*` green — chips **pre-baked** in static HTML |
| `listbox_still_open` (2237Z) | `false_complete_listbox_open` + FoS expanded — CSS menus, not Workday portals |
| Illinois `no_matching_option` (2244Z) | `workday_address_state_illinois` medium — models promptOption; **no fiber**, no async options, no real chrome |
| Contact `pack_incomplete`: `addressLine2` / `regionSubdivision1` **empty_readback** (2300Z) | **Partial:** `workday_battle_multipage` fiber-stubborn blur-clear stub — still not React Fiber / empty_readback on real aids |
| Phone country false empty while US (+1) chip present | `workday_nxp_phone_contact` green — chip **already in HTML**, filter `display:none` |
| Multipage to Review | `workday_multipage_to_review` green — 4 native `<input>`s + `alert()` validation; **zero** Workday SPA. Battle: `workday_battle_multipage` adds traps (still `display:none` steps, not real SPA) |

`reliability_gate.json` (2300Z): `pass: false`, `reached_review: false`. That is live truth. Gym green does not contradict it — gym never saw it.

---

## Top misleading tests / cases (name + why they lie)

1. **`workday_multipage_to_review` + `test_multipage_chain_reaches_review`**  
   Clicks through a toy 4-step DOM wizard with plain text inputs. Live Workday is fiber searchSelect, async options, validation banners, account gate, resume upload, Phase B–E. Green here ≠ Review-ready on NXP.  
   **Partial mitigation:** `workday_battle_multipage` (`fidelity: high` intent) composes fiber-stub + Illinois + hierarchical How-Heard + wrong FoS + listbox-blocks-Next + validation banners in one 5-step SPA — still not real Fiber/auth/resume. See `gym/ats/BATTLE_GYM.md`. Gym green ≠ live_pass.

2. **`workday_education_fos_chip` (+ `test_workday_education_fos_chip` / `test_field_done` FoS path)**  
   Science-Computer chip is **hardcoded in HTML**. Gold demands `second_pass_fill_actions: 0`. Proves “skip when chip text matches,” not “resume autofill + fiber commit + alias settle under open How-Heard.”

3. **`workday_nxp_phone_contact`**  
   US (+1) chip + phone number pre-filled. Detection of “chip present → not required_empty” is useful; claiming NXP contact honesty is not — live still reports country phone as leftover noise and other contact empties.

4. **`workday_address_state_illinois`**  
   Closest regression of a real bug (promptOption without `role=option`, HH listbox open). Still: static option list, button textContent commit, no React fiber, no network latency, no sibling pack fields. Can pass while live `addressLine2`/`county` empty_readback blocks ADVANCE.

5. **`test_workday_search_select.py` (pure source inspection)**  
   Asserts `"__reactProps" in src` / `"fiber_search_select" in source`. **Never opens a browser.** Green means the string exists in the module, not that fiber Tab commit works on tenant DOM.

6. **`workday_auth_gate` / `_direct`**  
   HTML probe for `reveal_email` / create link. COVERAGE.md already admits full click flow is live-only. Easy to over-read as “auth gate covered.”

7. **GH portal fixtures (`gh_react_select`, `portal_listbox`, …)**  
   Hand-rolled `.select__menu` absolute divs — not React-Select portals, not Ashby/Lever widgets. **Zero Ashby/Lever gym cases exist.**

---

## What live does that gym never exercises

- React fiber controlled inputs (`__reactProps` / native setter races) → **empty_readback** after apparent type
- Workday `searchSelect` async option fetch + virtualized lists
- Portaled listboxes that steal focus from State / address (How-Heard open)
- Validation banners after ADVANCE (`validation_after_advance`)
- Real multipage SPA step machine (not `display:none` divs)
- Resume parse → wrong autofill chips that change after network settle
- Account gate full path (CAPTCHA / Akamai / create vs sign-in)
- Pack-incomplete across **many** required aids on one contact page
- Ashby / Lever widget trees (no gym cases at all)
- Timing: headed human pause, refill passes, TargetClosedError

COVERAGE.md “Live-only gaps” already lists several of these; they are still treated as optional footnotes while merge lane runs gym as tier-1.

---

## Green tests to STOP treating as production confidence

| Stop using for live signoff | Keep for |
|-----------------------------|----------|
| Entire gym suite (`adversarial.py`, `detection_matrix.py`, `runner.py --self-test`) | Logic regression / fail-class guards |
| `regression_gates --tier1` **gym half** | Merge hygiene only — label as `gym_pass`, never `live_pass` |
| `reliability_gate --skip-run` alone | Re-score **last live artifact**; not a new live proof |
| All `fidelity: low` cases (see `meta.json`) | Narrow unit of the labeled fail_class |
| `test_workday_search_select.py`, most `pure_logic` Workday tests | Source / policy contracts |
| `test_multipage_chain_reaches_review` | Footer kind ADVANCE→FINAL on toy DOM |
| FoS / phone “already correct skip” gym paths | Thrash-skip invariants on static chips |

**Do not** promote SUCCESS / Ready / Review from gym gold or unit mocks.

---

## Minimal “honest” set worth keeping

These do **not** predict live Review, but they catch real classes of **dishonest reporting** if they regress:

| Test / case | Why keep |
|-------------|----------|
| `false_complete_listbox_open` | Refuses complete while listbox open + placeholder |
| `midwizard_sticky_submit` | ADVANCE vs sticky Submit honesty |
| `salary_blank_skip` | Visual filler ≠ committed value |
| `test_fill_contract.py` (intent ≠ readback / no verified without done) | Contract that killed Illinois false-verified |
| `test_field_done.py` / `test_page_progress.py` / `test_honest_metrics.py` | Ready / verified / never-submit invariants |
| `test_button_gate.py` | Never-submit |
| `workday_wrong_autofill_relock` / FoS wrong-chip reclaim (medium) | Wrong chip must not lock-skip |
| `reliability_gate.py` **headed live run** (`live_pass`) | Only offline gate that can mean live |
| Flight recorder artifacts (separate work) | Per-action live truth |

Every gym `meta.json` now has `fidelity: low|medium` and `live_signoff: false`. **No case is `high`** — nothing offline matches live Workday fidelity enough to claim it.

---

## Gates: do they celebrate gym as live?

| Gate | Behavior | Honest? |
|------|----------|---------|
| `detection_matrix.py` / `score.py` | Score static HTML vs gold | Fine as gym; **not** live predictor |
| `regression_gates.gate_tier1` | field_done + fill_contract + **adversarial + detection_matrix** + `reliability_gate --skip-run` | **Misleading name** — “tier1” mixes gym green with scoring an **old** live dir; no new headed run |
| `reliability_gate.py` (full headed) | Live NXP (optional Quantiphi) | Correct live lane when actually run |
| `reliability_gate --skip-run` | Re-reads latest `nxp_*` report | Live **score**, not live **proof** if artifact is stale |

Honesty upgrade: gate JSON now separates `gym_pass` vs `live_pass` (see `reliability_gate.py` / `regression_gates.py`). Gym green must never set `live_pass: true`.

---

## What to use instead for live confidence

1. **Flight recorder** (being built separately) — per-action DOM/readback/audit on headed runs. Treat as source of truth for “what autofill did.”
2. **`reliability_gate.py` headed** (no `--skip-run`) against current tenant URL — require `live_pass` / `reached_review`.
3. **Headed checklist** (human watch): contact pack leaves zero required empties; no open listbox; no FoS thrash; Illinois/state chip; line2/county readback; stop before Submit.
4. **`RELIABILITY_STATUS.md`** — latest blunt live verdict; update after each real gate, not after gym green.

Offline gym remains a **regression net for fail_classes**, not a substitute for (1)–(3).

---

## Unit test inventory (65 `test_*.py`)

| Class | Count (approx) | Live predictive? |
|-------|----------------|------------------|
| `pure_logic` | ~35 | No — classify/policy/source |
| `mock_unit` | ~7 | No — MagicMock pages |
| `playwright_gym` | ~6 | No — static fixtures |
| `playwright_inline_html` | ~8 | No — hand HTML |
| `near_live_url` (mentions URL) | ~6 | Mostly still mocks / characterization; not headed E2E |

Ashby/Lever: logic-only (`test_ashby_*`, `test_lever_*`); **no gym HTML**.

---

## Script

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/list_do_not_trust_for_live.py
```

Lists gym cases with `live_signoff: false` / low|medium fidelity and the unit modules that must not be used for live signoff.
