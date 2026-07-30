# Personio - known form structure

Not yet learned from a real run - `scrape_ats.py` was only just extended to
scrape Personio's public XML listing feed
(`scripts/scrape_ats.py:scrape_personio`), which covers discovery, not the
candidate-facing application form itself. Note that Personio's own
listing feed has no per-job URL - the job_url you'll navigate to is the
company's careers page root (e.g. `https://{company}.jobs.personio.de/`),
so you may need to find and click into the specific role by title first.

If you fill an application on a `*.jobs.personio.com`/`.de` domain, note
what you find here afterward (field selectors, known quirks, known
blockers) so the next job on this platform benefits - same pattern as
workday.md/greenhouse.md/lever.md/ashby.md/icims.md.
