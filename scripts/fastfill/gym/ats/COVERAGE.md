# ATS Gym — Adversarial Coverage Matrix

Hyper-exhaustive verify coverage: every **fail_taxonomy** fix class and **ALLOWED_PLAYBOOKS** widget family maps to at least one gym HTML case or unit fixture in `adversarial.py` / `detection_matrix.py`.

Dummy-only · never submit · never CAPTCHA · never invent EEO.

## Six-dimension matrix → case ids

| Dimension | Cell | Case id(s) | Test / assert |
|-----------|------|------------|---------------|
| **detect-field** | filled text readback | unit | `is_verified_fill_row` matching readback |
| **detect-field** | empty placeholder | unit | Select… / Select One never verified |
| **detect-field** | wrong value readback | unit | degree readback mismatch → not verified |
| **detect-field** | click without readback | unit | verified=True without readback → False |
| **detect-field** | help text not gap | `wd_radio_aria_checked` | CURRENT TEAMMATES instruction stripped |
| **detect-field** | aria-checked answered | `wd_radio_aria_checked` | Radio No via aria-checked → no gaps/misses |
| **detect-field** | open listbox uncommitted | `false_complete_listbox_open` | aria-expanded=true + placeholder → fail gold |
| **detect-option** | chip committed | unit | HOW_HEARD chip + option_clicked |
| **detect-option** | filter uncommitted | unit | filter token without chip → uncommitted |
| **detect-option** | decline not race | `gh_race_decline` | decline alias only; never concrete race |
| **detect-option** | typable must click | `gh_typable_commit` | typing ≠ commit until option click |
| **detect-option** | decline HTML fixture | `gh_race_decline` | `is_decline_like_alias` on fixture options |
| **page-complete** | ready honest | unit | `can_claim_ready` when gates pass |
| **page-complete** | required empty | unit | `required_empty_after_fill` blocks Ready |
| **page-complete** | listbox open | unit | `listbox_open` blocks Ready |
| **page-complete** | gaps block | unit | `gaps_after_save` blocks Ready + cycle SUCCESS |
| **page-complete** | false midwizard | `midwizard_sticky_submit` | ADVANCE + empty required → fail gold |
| **page-complete** | listbox HTML | `false_complete_listbox_open` | uncommitted open menu fails gold |
| **page-complete** | review hold | unit | FINAL footer allows review when complete |
| **page-complete** | multipage to review | `workday_multipage_to_review` | contact→experience→education→review FINAL footer |
| **page-complete** | battle multipage | `workday_battle_multipage` | 5-step high-intent: fiber/IL/dual-FoS/date-spin/phone/sticky-Submit/empty-cycle/HH-open-while-State → Review |
| **what-next** | footer Next → ADVANCE | unit | `footer_primary_wizard_incomplete` |
| **what-next** | footer Submit → FINAL | unit | Submit → not wizard incomplete |
| **what-next** | sticky advance wins | `midwizard_sticky_submit` | Submit+Next visible → ADVANCE primary |
| **what-next** | auth reveal (HTML) | `workday_auth_gate` | `workday_auth_gate_action` → reveal_email |
| **what-next** | auth reveal (unit) | unit | sign-in-with-email hidden → reveal_email |
| **what-next** | auth create account | unit | create form present → create_account |
| **what-next** | settle listbox | unit | `_click_next_advance` checks listbox_still_open |
| **what-next** | cycle demotes incomplete | unit | mid-wizard SUCCESS demoted |
| **thrash** | field lock skip | `gh_howheard_multiselect` | second fill → skipped_already_correct |
| **thrash** | wrong autofill reclaim | `workday_wrong_autofill_relock`, `workday_education_fos_wrong_chip` | Arts-Other + CS intent → reclaim not lock |
| **thrash** | thrash demotes | unit | thrash_retouches demotes SUCCESS |
| **thrash** | how-heard priority | `gh_howheard_multiselect` | LinkedIn before Indeed; no alias walk |
| **thrash** | arrowdown waste | unit | stable GH menu ArrowDown ≤1 |
| **crossfill** | phone ≠ jobboard | `crossfill_phone_country` | phone country chip; LinkedIn not on phone field |
| **crossfill** | accommodations ≠ consent | `crossfill_accommodations` | ACCOMMODATIONS vs MARKETING_CONSENT |
| **crossfill** | noncompete ≠ work auth | unit | noncompete label ≠ WORK_AUTH |
| **crossfill** | privacy ≠ name | unit | privacy notice ≠ NAME_FULL |

## fail_taxonomy → coverage

| Taxonomy code | Gym class | Case / fixture | Assert |
|---------------|-----------|----------------|--------|
| `FAIL_MIDWIZARD` | advance_honesty | `midwizard_sticky_submit`, `workday_multipage_to_review`, `workday_battle_multipage` | Footer ADVANCE when required empty; sticky Submit decoy; battle chain reaches Review FINAL |
| `FAIL_MIDWIZARD` | false_complete | `false_complete_listbox_open`, unit | `can_claim_ready` refuses `listbox_open`, `advance_blocked_reason` |
| `FAIL_WRONG_VALUE` | cross_fill | `crossfill_accommodations`, `crossfill_phone_country`, unit | accommodations≠consent; phone≠jobboard; noncompete≠WORK_AUTH; privacy≠NAME_FULL |
| `FAIL_WRONG_VALUE` | click_wrong | unit, `gh_race_decline` | `soft_value_match` Male≠Female; degree pick rejects A.A.; decline only |
| `FAIL_THRASH` | thrash | `gh_howheard_multiselect`, `workday_wrong_autofill_relock`, unit | field_lock gate; thrash demotes SUCCESS; wrong autofill reclaim |
| `FAIL_BLANK` | false_incomplete | `wd_radio_aria_checked` | `form_gaps` + `leftover_miss_scan` empty when aria-checked No |
| `FAIL_BLANK` | false_incomplete | unit | instruction-only gap filter (CURRENT TEAMMATES…) |
| `FAIL_BLANK` | select_commit | `gh_*`, `portal_listbox` | fill + readback commit |
| `FAIL_BLANK` | auth_gate | `workday_auth_gate`, unit | sign-in-with-email → reveal; create form wins |
| `BLOCKED` | blocked | unit | captcha → non-fixable |
| `SUCCESS` | playbooks | unit | `detect_playbook` for all ALLOWED_PLAYBOOKS ids |

## ALLOWED_PLAYBOOKS → coverage

| Playbook | Case / test | Proves |
|----------|-------------|--------|
| `native_select` | `workday_multipage`, `workday_multipage_to_review`, `midwizard_sticky_submit` | ADVANCE/FINAL footer gate |
| `react_select_portal` | `gh_react_select`, `gh_race_decline`, `portal_listbox`, `false_complete_listbox_open`, `crossfill_phone_country` | portal listbox; single commit; false-complete guard |
| `typable_commit` | `gh_typable_commit` | type filter ≠ commit; one option click |
| `workday_how_heard` | `gh_howheard_multiselect`, `crossfill_phone_country` | LinkedIn priority chip; no alias thrash; phone≠how-heard |
| `date_spinner` | unit (`test_verified_select`), `workday_battle_multipage` stub | prefilled 08/2017 + Present skip (not live Fiber spins) |
| `text_input` | `salary_blank_skip`, `workday_auth_gate` | blank skip honesty; auth gate probe |
| `checkbox` | `crossfill_accommodations`, unit | TERMS_CONSENT / accommodations classify |
| `radio` | `wd_radio_aria_checked`, `crossfill_accommodations` | aria-checked readback; accommodations radio |

## Click accuracy bar (zero waste · zero wrong)

| Class | Test | Assert |
|-------|------|--------|
| **No wasted ArrowDown** | `test_enumerate_stable_arrowdown_at_most_one` | Stable GH degree menu: ArrowDown ≤1 |
| **No reopen committed select** | `test_field_lock_prevents_second_select_click` | Second `fill_gh_select` → `skipped_already_correct` |
| **No alias walk after commit** | `test_how_heard_single_priority_commit` | First priority leaf only (LinkedIn before Indeed) |
| **No duplicate fill in steps** | `test_fill_steps_single_how_heard_attempt` | `analyze_step_log_waste` flags double select |
| **No wrong degree** | `test_degree_pick_rejects_aa_for_masters` | pick_best_scored → Master's not A.A. |
| **No gender substring** | `test_soft_match_rejects_male_in_female` | Male ⊄ Female |
| **No invented race** | `test_race_decline_never_picks_concrete_race` | decline alias only |

## HTML cases (`cases/`)

| Case id | fail_class | Empty scores fail? | Fill test |
|---------|------------|-------------------|-----------|
| `wd_radio_aria_checked` | false_incomplete_radio | **passes** (pre-answered radio) | adversarial gaps |
| `false_complete_listbox_open` | false_complete_listbox | yes | listbox open fails gold |
| `gh_race_decline` | race_decline | yes | `test_fill_gh_race_decline` |
| `gh_react_select` | react_select_portal | yes | `test_fill_gh_react_select_school` |
| `gh_howheard_multiselect` | multiselect_uncommitted | yes | `test_fill_gh_howheard_priority` |
| `gh_typable_commit` | typable_commit | yes | `test_fill_gh_typable_commit` |
| `portal_listbox` | portal_listbox | yes | load-only |
| `crossfill_phone_country` | cross_fill_phone | yes | classify + chip guard |
| `crossfill_accommodations` | cross_fill_accommodations | yes | classify guard |
| `midwizard_sticky_submit` | midwizard_footer | yes | footer ADVANCE |
| `salary_blank_skip` | blank_skip | yes | mini-fill passes |
| `workday_multipage` | multipage_advance | yes | — |
| `workday_multipage_to_review` | multipage_to_review | yes | `test_multipage_chain_reaches_review` |
| `workday_battle_multipage` | battle_multipage | yes | `test_battle_multipage_chain_reaches_review` (v1: dual FoS, date-spin skip, phone country, sticky Submit, empty-cycle, last-name reclaim, HH-open-while-State — see `BATTLE_GYM.md`) |
| `workday_wrong_autofill_relock` | wrong_autofill_relock | yes | `test_wrong_autofill_relock_not_lock` |
| `workday_education_fos_chip` | education_fos_chip | yes | FoS Science-Computer skip |
| `workday_education_fos_wrong_chip` | education_fos_wrong | yes | Arts-Other reclaim |
| `workday_auth_gate` | auth_gate | **passes** (auth probe) | `test_workday_auth_gate_case` |

## Live-only gaps (tenant drift — not gym-reproducible)

**Honesty:** Gym/unit green does **not** mean live headed success. See
[`GYM_VS_LIVE.md`](../../GYM_VS_LIVE.md). Every case has `fidelity` +
`live_signoff: false` in `meta.json`. Live truth = flight recorder + headed
`reliability_gate.py` (`live_pass`).

- Workday fiber `searchSelect` two-step category→leaf (Walmart hierarchical how-heard) — partially mirrored in `workday_battle_multipage` / hierarchical chip (static options only)
- Fiber / controlled text **empty_readback** (NXP addressLine2 / county) — battle gym has a **fiber-stubborn blur-clear** stub only (not real Fiber)
- Virtualized menus with scrollTop (partially covered by `test_enumerate_grows_via_arrowdown_until_stable`)
- Resume upload verify + filelist empty filename-visible
- CAPTCHA / Akamai headed pause
- Real tenant label drift / i18n
- Phase C experience date spins (Workday `dateSection`) — battle gym has a **prefilled stub** (08/2017 + Present) only, not live Fiber spinners
- **Auth gate full click flow** (reveal → create → fill): covered live in `test_workday_signin_gate.py` — gym HTML probes initial `reveal_email` only (Stream B owns gated clicks)
- **Ashby / Lever** — no gym HTML cases at all
- **Battle gym honesty:** `workday_battle_multipage` is high-*intent* composition (v1 cycles: dual FoS Major vs Discipline, date-spin skip stub, phone country vs number, sticky Submit decoy, empty-cycle Next, last-name wrong autofill, HH stays open while State filled). Gold is honest (not weakened). Not pixel-perfect Fiber — see [`BATTLE_GYM.md`](BATTLE_GYM.md); live headed + flight recorder remain source of truth ([`GYM_VS_LIVE.md`](../../GYM_VS_LIVE.md))

## Run

```bash
skyvern_runtime/venv/bin/python scripts/fastfill/gym/ats/adversarial.py
skyvern_runtime/venv/bin/python scripts/fastfill/gym/ats/detection_matrix.py
skyvern_runtime/venv/bin/python scripts/fastfill/gym/ats/runner.py --self-test
skyvern_runtime/venv/bin/python scripts/fastfill/gym/ats/runner.py --list
# Battle gym (5-step Workday-mirror traps):
skyvern_runtime/venv/bin/python -c "
import sys
from pathlib import Path
sys.path[:0] = [str(Path('scripts/fastfill/gym/ats').resolve()), str(Path('scripts/fastfill').resolve())]
from adversarial import test_battle_multipage_chain_reaches_review
test_battle_multipage_chain_reaches_review()
print('ok')
"
```

Definition of done: adversarial suite green + detection matrix green + related unit suites + (optional) 2–3 never-seen WD canary with zero false complete/incomplete on covered classes. See `BATTLE_GYM.md`.
