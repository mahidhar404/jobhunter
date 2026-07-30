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
   `voluntaryDisclosuresPage` -> `selfIdentificationPage`. Advance each with
   `button[data-automation-id="bottom-navigation-next-button"]`.

## Field patterns by step

**Contact info**: `legalNameSection_firstName`, `legalNameSection_lastName`,
`addressSection_addressLine1`, `addressSection_city`,
`addressSection_countryRegion` (searchable combobox: click, type, Enter),
`addressSection_postalCode`, `phone-device-type` (combobox), `phone-number`.

**Experience**: work entries live under
`div[data-automation-id="workExperience-N"]` (N = 1, 2, 3...), each with
`jobTitle`/`company`/`location` inputs and
`formField-startDate`/`formField-endDate` containers holding
`dateSectionMonth-input`/`dateSectionYear-input`. Date fields are
React-controlled - click/focus then send real keyboard input, never set
`.value` via JS (this matches the existing Hard Rule on date spinbuttons).
Education lives under `educationSection`; school/degree use the same
click-type-Enter combobox pattern. Resume upload input:
`input[data-automation-id="file-upload-input-ref"]`.

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
