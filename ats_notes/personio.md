# Personio - known form structure

Candidate apply lives on `{company}.jobs.personio.{com,de}/job/{id}?…&apply`
(listing roots like `/?language=en#id` need a job click first).

## Stable selectors (Layer 0.5 `PERSONIO_PACK`)

Observed on Ultralytics (`ultralytics.jobs.personio.de`, 2026-07-30):

| Field | Selector |
|-------|----------|
| First name | `#field-first_name` / `name=first_name` |
| Last name | `#field-last_name` / `name=last_name` |
| Email | `#field-email` / `name=email` |
| Phone | `#field-phone` / `name=phone` |
| Resume | `#doc-input-cv` / `name=documents.cv` (often CSS-hidden; `set_input_files` still works) |
| LinkedIn / GitHub | custom `custom_attribute_*` inputs with matching **placeholders** |

Submit stays disabled until required fields + CV — never click Submit / Apply-final.

## Discovery note

`scrape_ats.py:scrape_personio` hits the public XML listing feed only; it does
not describe the apply form. Prefer `/job/{id}?…&apply` URLs for fill smoke.
