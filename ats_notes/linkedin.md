# LinkedIn - known page structure

`apply_url` is often blank for LinkedIn-sourced listings (the scraper
doesn't always resolve an external apply link at scrape time) - when
that's the case you land on the LinkedIn job posting page itself
(`job_url`) and need to find the real application route from there.
You're already signed into the shared browser session, so a sign-in wall
shouldn't come up.

## The two button types - this distinction matters
- **"Easy Apply"** - stays entirely on LinkedIn, using LinkedIn's own
  hosted application flow (still lets you upload a resume file).
  **Do not use this** - these postings are heavily saturated with
  low-effort mass-applications, and the user has explicitly said not to
  apply through it. If a posting has **only** an Easy Apply button and no
  other application route, this is an automatic skip, not a question to
  ask: set `status` to `skipped_easy_apply` via `scripts/update_job.py`
  with a `status_detail` noting Easy Apply was the only option, and stop
  - don't fill anything.
- **Plain "Apply"** (no "Easy" prefix) - redirects off LinkedIn to the
  company's own external site/ATS. This is the one to follow. Treat
  whatever page you land on after the redirect as the real apply_url and
  continue the normal pipeline there (including checking `ats_notes/` for
  whatever platform you land on, same as any other job).

## If neither button is obviously present
Some postings' actual application method is stated in the job description
text itself rather than a page button (observed live: a posting whose
description named a separate Google Form) - check the description before
concluding you're stuck.

## Source
Observed directly from real runs on jobs.ashbyhq.com... no, from real
LinkedIn-sourced jobs in this project (Chima - found an external Google
Form referenced in the JD text after LinkedIn required sign-in) and the
user's own explicit instructions about Easy Apply.
