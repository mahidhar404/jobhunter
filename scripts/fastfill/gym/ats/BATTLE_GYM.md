# Workday Battle Gym

**Purpose:** A single multi-page static fixture that is *closer* to live Workday multipage behavior than the thin `workday_multipage_to_review` case — for battle-testing advance honesty, widget commits, and page locks **without** a real tenant login.

Dummy-only · never submit · never CAPTCHA · never invent EEO.

---

## Verdict

**Yes, with caveats.** A massive Workday-mirror gym is worth it **only if** it encodes the failure modes that make local tests misleading — not if it is a prettier multi-step form with plain `<input>` fields.

What makes it *not* misleading:

| Trap | Why local gyms usually lie |
|------|----------------------------|
| Fiber-controlled inputs | Plain `fill()` “works” in HTML; live Fiber clears/re-renders |
| Portaled / open listboxes | Keystrokes land in the wrong menu; Advance while `aria-expanded=true` |
| Async / hierarchical How-Heard | Category filter ≠ leaf chip; open menu steals focus |
| Wrong autofill chips (FoS) | Lock-skip on Arts-Other when intent is Science-Computer |
| Dual FoS alias (Major vs Discipline) | Correct Discipline chip lock-skips the wrong Major chip |
| Illinois vs Idaho | Confusable `promptOption` rows without `role=option` |
| Phone country vs number | How-heard / dial-code values land on the wrong widget |
| Date spin skip | Empty end-date while Present is checked is *correct*; thrash fails |
| Sticky mid-wizard Submit | Footer shows Submit beside Next; primary is still ADVANCE |
| Empty-cycle fingerprint | Next click while listbox open does not change SPA step |
| HH open while State filled | Committing Illinois must not close How-Heard; Next still refused |
| Validation banners | Next click must FAIL before ADVANCE when required empty |
| SPA step fingerprints | Page identity must change (`contactInformationPage` → … → `reviewPage`) |
| Lock across pages | Values must survive step transitions; Review shows FINAL only |

What **still** requires live headed + [flight recorder](../../LIVE_VISIBILITY.md):

- Real Workday Fiber / React hydration timing
- Auth gate (create / sign-in) + `web_keys`
- CAPTCHA / Akamai headed pause
- Resume upload verify + filename-visible filelist
- Virtualized menus / scrollTop option lists
- Tenant label drift / i18n
- True Fiber date spinners (`dateSection` here is a **prefilled stub**)
- True portal listboxes outside the form root
- Network-driven async option load from Workday APIs

**Live truth:** `flight.log` / `flight.jsonl` from a headed run — not gym green alone.

---

## What already exists vs gap

| Asset | Fidelity | Covers |
|-------|----------|--------|
| `workday_multipage_to_review` | low | 4-step chain → Review FINAL (plain inputs) |
| `workday_address_state_illinois` | medium | Illinois + open How-Heard steal |
| `workday_education_fos_*` / wrong chip | low–medium | FoS chip / reclaim |
| `workday_education_fos_dual_alias` | medium | Major Arts-Other + Discipline Science-Computer |
| `workday_how_heard_hierarchical_chip` | medium | Website → Web - LinkedIn |
| `false_complete_listbox_open` | medium | Open listbox ≠ complete |
| `workday_wrong_autofill_relock` | medium | Arts-Other reclaim |
| `crossfill_phone_country` | medium | Phone country ≠ job board |
| `midwizard_sticky_submit` | medium | Submit+Next → ADVANCE primary |
| **`workday_battle_multipage` (this)** | **high intent** | **All of the above in one 5-step SPA** |

Gap closed by battle gym: **composition** — traps interact across page advances (open listbox blocks Next; Illinois does not close How-Heard; wrong Major blocks Education→Questions even when Discipline matches; Review only after Questions commit).

Gap remaining: anything that needs a real `myworkdayjobs.com` session (see table above).

---

## Case layout

```
scripts/fastfill/gym/ats/cases/workday_battle_multipage/
  form.html   # 5-step SPA-ish fixture + injectors
  gold.json   # dummy committed values + FINAL footer + spa fingerprint
  meta.json   # fidelity: high, live_signoff: false
```

**Pages:** My Information → My Experience → My Education → Application Questions → Review

**Widgets / injectors (v1, two escalation cycles):**

1. Contact — fiber-stubborn address; hierarchical How-Heard (starts open); Illinois `promptOption`; phone country vs number; last-name wrong autofill `Test`→`Dummy`; sticky Submit decoy; Next no-op while listbox open; **How-Heard stays open after Illinois**
2. Experience — required title/company; date-spin start `08/2017` already committed (skip); Present checked → end date empty/disabled
3. Education — dual FoS: Major Arts-Other must reclaim Science-Computer; Discipline already Science-Computer (keep)
4. Questions — work-auth listbox starts expanded; Next refused until closed + Yes
5. Review — Submit shown (FINAL); sticky decoy hidden; click blocked in JS

Gold is **honest** (not weakened): last name must be Dummy not Test; Major must be Science-Computer not Arts-Other; phone is `405-555-0100` not a country code; Illinois + Web - LinkedIn both required; date spins stay `08/2017`.

---

## How to run

```bash
# Battle gym only (from repo root)
skyvern_runtime/venv/bin/python -c "
import sys
from pathlib import Path
sys.path.insert(0, str(Path('scripts/fastfill/gym/ats').resolve()))
sys.path.insert(0, str(Path('scripts/fastfill').resolve()))
from adversarial import test_battle_multipage_chain_reaches_review
test_battle_multipage_chain_reaches_review()
print('battle gym OK')
"

# Or full adversarial / detection / gym runner
skyvern_runtime/venv/bin/python scripts/fastfill/gym/ats/adversarial.py
skyvern_runtime/venv/bin/python scripts/fastfill/gym/ats/detection_matrix.py
skyvern_runtime/venv/bin/python scripts/fastfill/gym/ats/runner.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/gym/ats/runner.py --case workday_battle_multipage
```

Empty load of the case **must fail** gold (ADVANCE footer + empty/wrong required + open listbox). The fill chain test proves traps then Review FINAL.

Also see [`GYM_VS_LIVE.md`](../../GYM_VS_LIVE.md) — battle gym narrows the multipage honesty gap; it does not close live truth.

---

## What it proves vs what live must prove

| Proves (gym) | Does **not** prove (live + flight) |
|--------------|-------------------------------------|
| FAIL-before-ADVANCE on open listbox / incomplete / wrong FoS / wrong Major | Fiber re-render after pack fill on real tenant |
| Illinois commit over Idaho confusable **without** closing How-Heard | Real `searchSelect` network option load |
| Hierarchical leaf chip + closed menu | Walmart-scale hierarchy + scroll virtualization |
| Dual FoS: Discipline match ≠ Major reclaim skip | Shared field_lock ontology on live Fiber chips |
| Date-spin skip (prefilled 08/2017 + Present) | Real `dateSection` spinner widgets |
| Phone country vs number | Live country-code chip + device type |
| Sticky Submit decoy ignored; Next is ADVANCE | Real Workday footer stacking |
| SPA step fingerprint unchanged on empty-cycle Next | `pack_incomplete` / empty-cycle STOP under load |
| Score + adversarial refuse empty SUCCESS | Auth / CAPTCHA / resume |

After a live Workday headed run:

```bash
./scripts/fastfill/run_fill_visible.sh 'https://….myworkdayjobs.com/…'
# Paste: skyvern_runtime/real_job_results/fill_live_*/flight.log
```

See `scripts/fastfill/LIVE_VISIBILITY.md`.

---

## Honesty

`meta.fidelity: high` means **intent** (traps + multipage composition), not pixel-perfect Workday. Do not treat battle-gym green as Ready for a real application. Never submit.

Escalation (this gym): Cycle 1 added live-hit traps; Cycle 2 added HH-open-while-State-filled; Cycle 3 added County cascade after Illinois. Gold was **not** weakened to make the chain pass. The adversarial chain test is a scripted honest path — it does **not** prove `fast_fill.py` / `field_lock` / `verified_select` can survive the harder gym on a real tenant.

---

## Local plateau (production filler vs gym)

**Stop:** the next gym change would only duplicate live Workday (real Fiber, auth, CAPTCHA, network-async options, tenant APIs). Do **not** claim live production-ready.

**Cycles completed** (production `run_battle_fill.py` vs `gold.json`, dummy PII, never submit):

1. FAIL — stuck on contact (`required_fields_empty`, HH listbox open). How-Heard `automation_id=how_heard` 30s `inner_text`; chip not locked.
2. FAIL — contact advanced; stuck on experience (`required_dates_empty`). Disabled Present/To was flagged empty.
3–5. FAIL — experience advanced; Major emptied / FoS family lock-skip. Discipline match skipped Major reclaim.
6. **PASS** vs gold. FoS option-click Science-Computer; work-auth Yes; Review FINAL.
7. Harden: County mounts after Illinois (live address cascade). Filler already had `ADDRESS_COUNTY` Sangamon.
8. **PASS** vs hardened gold. County filled after Illinois. Scripted `test_battle_multipage_chain_reaches_review` still green.

**What the filler now passes locally**

- Contact: Dummy last name, fiber address, Illinois, Sangamon county cascade, phone `405-555-0100` (not country code), US (+1), hierarchical **Web - LinkedIn**, HH listbox closed before Next
- Experience: title/company; date-spin skip 08/2017; Present + disabled empty To
- Education: school; Major reclaim **Science-Computer** without dropping Discipline
- Questions: work-auth Yes; listbox closed
- Review: footer FINAL; never Submit

**What still needs live headed + `flight.log`**

- Real Workday Fiber / React hydration (not the gym stub)
- Auth gate (create / sign-in) + `web_keys`
- CAPTCHA / Akamai headed pause
- Network-driven async How-Heard / FoS option load and virtualized menus
- Tenant label drift / i18n
- True `dateSection` spinners
- Resume upload verify
- `filled_rows_not_honest` still fires on some pack rows; legacy gate still advances locally — confirm on live whether that blocks a real tenant

`live_signoff` remains **false**. Gym green ≠ ready to apply.
