# Ashby - known form structure

Ashby has no applicant-usable submission API - the documented
`applicationForm.submit` endpoint requires an API key generated from that
specific company's own private Ashby admin dashboard (checked directly:
requires `candidatesWrite` permission via a key only the employer can
generate), so it isn't something usable across companies - same situation
as Greenhouse's separate Harvest API. Browser form-filling is the only real
path here, same as the other platforms.

## Field types
Ashby's own docs describe standard field types per posting: short answer,
long answer, phone, email, multiple choice, checkboxes, date, yes/no.
These are company-configurable per job posting, so there's no fixed
universal selector set the way Workday's `data-automation-id` gives one.

## Observed directly (from a real job-hunter dry run on jobs.ashbyhq.com)
- Location is a single combobox field, not separate city/state/zip inputs.
- Common standard questions seen: visa sponsorship (Yes/No), a
  recording-consent opt-in/out, and a data-processing-consent checkbox.
- Resume upload is a plain file input - remember it only accepts a path
  under `~/.openclaw/media/inbound` (see `run_tailor_then_fill` in
  server.py - the resume is already copied there before your turn starts,
  do not try uploading from `resumes/<id>/resume.pdf` directly).

## Source
Field-type info: https://docs.ashbyhq.com/application-forms. Everything
under "Observed directly" came from this project's own real run, not an
external source - update this section directly if a future run finds
something new and reliably repeatable.
