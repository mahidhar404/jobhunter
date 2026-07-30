# Lever - known form structure

Lever's application form uses plain, stable `name` attributes on every
company's form - the most consistent/simple of the platforms
`scrape_ats.py` also scrapes directly for listings.

## Core fields (by `name` attribute)
`name="name"` (full name), `name="email"`, `name="phone"`,
`name="org"` (current employer/organization),
`name="urls[LinkedIn]"`, `name="urls[Github]"` **or**
`name="urls[GitHub]"` (casing varies by company - try both),
`name="urls[Portfolio]"`.

## Known question patterns (matched by visible text, not a fixed id)
- Work authorization and visa-sponsorship are each usually phrased as a
  question containing "authorized to work" / "require sponsorship," with a
  radio button or select right after - match by that text, not a fixed
  selector, since exact wording varies slightly by company.
- A university/school field is often a searchable combobox: click it, type
  into the resulting `input[type="search"]`, press Enter to select.

## Known blocker
Lever shows an hCaptcha for traffic it considers suspicious. If one
appears, this is a stop-and-report situation per the Hard Rules (never
solve a CAPTCHA or bypass bot-detection) - not something to work around.

## Source
Selector/structure knowledge verified against real, working automation
code: https://github.com/ChadLei/Job-Auto-Apply - only the field-mapping
knowledge was taken from this.
