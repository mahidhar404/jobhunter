# Workday - known form structure

Workday's application form is the same React app across every company that
uses it (only branding differs) - fields are consistently tagged with a
`data-automation-id` attribute that stays stable across companies, though
Workday can change it between its own product versions, so treat these as a
strong first guess to confirm with one snapshot, not a guarantee. If a
selector below doesn't match, fall back to normal snapshot-and-fill - don't
retry the same guess repeatedly.

## Flow
1. Requires a Workday "Candidate Home" account before applying - the same
   account works across every company on Workday (check credentials.json
   for an existing login on this domain first).
2. Sign in: `button[data-automation-id="utilityButtonSignIn"]`, then
   `input[data-automation-id="email"]` / `input[data-automation-id="password"]`,
   submit via `button[data-automation-id="signInSubmitButton"]`.
3. Create account (no existing login): `button[data-automation-id="createAccountLink"]`,
   fill `email`/`password`/`verifyPassword`, tick
   `input[data-automation-id="createAccountCheckbox"]`, submit via
   `button[data-automation-id="createAccountSubmitButton"]`.
4. After login: `a[data-automation-id="adventureButton"]` (sometimes needs
   clicking through an intro screen twice), then
   `a[data-automation-id="applyManually"]` to start a manual application
   (as opposed to "Apply with LinkedIn"/"Autofill from resume").
5. Steps run through container divs you can wait on in order:
   `contactInformationPage` -> `myExperiencePage` ->
   (`applicationQuestionsPage` on some tenants, e.g. Cisco) ->
   `voluntaryDisclosuresPage` -> `selfIdentificationPage`. Advance each with
   `button[data-automation-id="bottom-navigation-next-button"]`.

## Field patterns by step

**Contact info**: `legalNameSection_firstName`, `legalNameSection_lastName`,
`addressSection_country` (Country combobox), `addressSection_addressLine1`,
`addressSection_city`, `addressSection_countryRegion` (State/Province —
**not** Country; on wd5+ apply-flow this is `formField-countryRegion`),
`addressSection_postalCode`, `phone-device-type` (combobox — fill **before**
phone number; never fall back to the first listbox option), `phone-number`
(prefer `input[data-automation-id="phone-number"]` / `formField-phoneNumber`
input; scroll + force-click if covered).

After filling, read back each value before counting it. Do **not** click
`bottom-navigation-next-button` / Save and Continue until required visible
fields on the current page are filled (or stop and mark incomplete — never
advance into an Errors Found validation banner).

**phone-device-type** must select Mobile/Home/Work — never a country dial-code
row like `United States of America (+1)`.

**Contact extras (tenant-dependent):** some tenants (e.g. BBH) require
`input[name="emailAddress"]` and a multi-select How Did You Hear control
(`source--source` / `formField-source`) plus `candidateIsPreviousWorker`
Yes/No. Fill these before ADVANCE; promote remaining empties to leftovers.

**Experience**: work entries live under
`div[data-automation-id="workExperience-N"]` (N = 1, 2, 3...), each with
`jobTitle`/`company`/`location` inputs and
`formField-startDate`/`formField-endDate` containers holding
`dateSectionMonth-input`/`dateSectionYear-input` (and often matching
`dateSectionMonth-display` / `dateSectionYear-display` surfaces).

**Date spins (From / To)**: React-controlled. `locator.fill()` and JS
`.value` can make `input.value` look filled while React state stays empty —
ADVANCE then fails with "The field From is required". Working technique:
click the `*-display` (fallback `*-input`) → select-all/backspace →
`press_sequentially` digits → Tab to blur (never Enter; never JS value
alone). Verify **display text** (not placeholder MM/YYYY) and input
readback before counting filled. Prefer filling both From and To via keyboard (To aria-label is plain "Month"/"Year", From is "Month — From*"). `currentlyWorkHere` force-check often sets DOM.checked while React still requires To — leave Present unchecked for dummy fills and bind To explicitly. Evidence: From year 2022 + To year 2023 both verified on Cisco wd5. Multi-job rows remount spins — prefer one experience row for stable fills. Page-complete gate must not ADVANCE when date *inputs* still lack digits (display-only checks false-positive).

Some tenants insert `applicationQuestionsPage` after experience (Cisco step 3);
fill Yes/No with dummy No for sponsorship/conviction patterns, and fill
required **Select One** comboboxes via label→dummy map (how-heard, education
level, work-auth No). Never invent essays. Never skip App Questions while
required Select Ones remain (false `app_questions_absent` → EEO miss).

Education lives under `educationSection`; school/degree use the same
click-type-option combobox pattern (never Enter). Resume upload input:
`input[data-automation-id="file-upload-input-ref"]`.

Fast fill Phase C–E walk these steps after a clean contact ADVANCE, then stop
at review (never final Submit). **SUCCESS requires `ready_for_review`** — never
contact-only SUCCESS.

**Headed Pause fill:** top-right Pause/Continue overlay (`fill_pause.py`) is
ON by default when headed; reinjected after each Workday page ADVANCE.
Disable with `--no-fill-pause` / `FASTFILL_FILL_PAUSE=0`. Never submit.

**Voluntary disclosures (EEO)**: this is a two-part ethnicity question, not
one - `hispanicOrLatino` (Yes/No) is separate from `ethnicityDropdown` (a
race category that doesn't include Hispanic/Latino as an option). See
profile.json's `eeo_demographic.hispanic_or_latino` and `.race_ethnicity`
for the two separate answers. Also present: `gender`, `veteranStatus`
(all comboboxes - click, type, Enter). Finish with `agreementCheckbox`.

**Self-identification (disability)**: this is the standard OFCCP Form
CC-305 - `name` (full legal name as e-signature), a date-of-today picker
via `div[data-automation-id="dateIcon"]` then
`button[data-automation-id="datePickerSelectedToday"]`, then a disability
Yes/No/Decline question.

## Source
Selector/structure knowledge verified against real, working automation
code: https://github.com/ubangura/Workday-Application-Automator - only the
field-mapping knowledge was taken from this; nothing about bypassing
detection or CAPTCHAs was used.
