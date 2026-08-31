# India + Worldwide Lanes (2026-08-25)

Architecture note for agents.

## Lanes

- `india` — any India-related location (onsite/hybrid/India-remote); pay INR/LPA
- `worldwide` — non-India any mode + US remote; **US onsite/hybrid pruned** (`us_onsite_or_hybrid`)

Settings: `discover_india`, `discover_worldwide` (replaces `discover_us`).

## Key files

- `scripts/discovery_filters.py` — `lane_for_job`, `listing_matches_lanes`, INR + native salary
- `dashboard/discovery_sources.py` — full board catalog + `scrape_status`
- `scripts/scrape_ww_boards.py` — worldwide JSON/RSS adapters
- `scripts/backfill_lanes.py` — stamp `lane` on existing jobs
- `dashboard/static/app.js` — Lane filter, currency-aware pay, Discover toggles

## Runtime (launchd-supervised, 2026-08-25)

`bash start_dashboard.sh` installs + bootstraps the `com.jobhunter.dashboard`
LaunchAgent (`dashboard/com.jobhunter.dashboard.plist.template` →
`~/Library/LaunchAgents/`). launchd KeepAlive auto-respawns the server within
~5s of ANY death (crash, pkill, SIGKILL, agent-session reaping) while
`~/Library/Application Support/jobhunter/dashboard_keepalive.flag` exists.
UI Quit deletes the flag (quit stays quit); UI Refresh is a soft reload
(lifecycle off — server never restarts). macOS TCC notes: launchd cannot open
StandardOutPath inside ~/Desktop and /bin/bash is TCC-denied there, so the
plist execs the venv python running `dashboard/launchd_run.py`, which
redirects output to `logs/dashboard_server.out` and execs `server.py`.

## Board scraper status (2026-08-25 audit, second pass)

### The bug that hid every worldwide board

`scrape_ww_boards.py` main() did `rows = filter_out_known_listings(rows, skip)`
without unpacking — that helper returns `(kept, skipped_count)`. So `rows`
became a 2-tuple and `write_listings` wrote `[[...rows...], N]` to disk. Every
one of the 17 worldwide boards produced a file that parses as JSON but has two
elements, `dedup_listings.py` saw no listings, and **zero worldwide jobs ever
reached jobs.json**. The tell in the logs was `skip-urls: dropped -2 known`
(negative) and `wrote 2 listings` from every board regardless of what it
fetched. Fixed + covered by
`scripts/test_scrape_ww_boards.py::WwMainWritesListingsTests`.

### Stop no longer discards scraped listings

`_kill_all_discovery_procs()` killed every registered subprocess on abort,
including the dedup / write-into-jobs.json steps that pass
`protect_from_abort=True`. The protection was a single global bool that any of
the ~28 parallel scrapes reset to False as it launched, so it rarely held.
Stopping a run therefore left thousands of rows in `listings/*.json` that never
became jobs. Protection is now a per-track-key set
(`_discovery_protected_keys`); abort kills scrapes only and the post-batch
final flush merges every leftover listing file. Covered by
`dashboard/test_discovery_abort_flush.py`.

### Per-board results after the fix (rows scraped, 2026-08-25)

| board | before | after | what was wrong |
|---|---|---|---|
| arbeitnow | 0 (corrupt) | 117 | tuple bug only |
| nodesk | 0 | 102 | both feeds 404; scrape the HTML index (job URLs are one segment deep, sharing the prefix with category pages) |
| himalayas | 0 | 50 | API ignores `?page=`; paginate with `?offset=` |
| weworkremotely | 0 | 37 | tuple bug + only 3 of 6 useful category feeds |
| yc_jobs | 2 | 23 | HN Algolia `tags=job` is tiny; YC's own `/jobs/role/<role>` is server-rendered |
| relocate_me | 0 | 22 | `/search` 301s to `/international-jobs`; parse the `.jobs-list__job` grid |
| landing_jobs | 0 | 22 | wrong field map (no `company`, `locations` is a list); `published_at` can be a year old, so use `updated_at` |
| dynamitejobs | 0 | 21 | job links are `/company/<co>/remote-job/<slug>`, and only category pages carry them |
| working_nomads | 0 | 12 | tuple bug only |
| themuse | 0 | 12 | tuple bug only |
| jsremotely | 0 | 11 | tuple bug only |
| justremote | 0 | 9 | SPA with no jobs in HTML; its own JSON API is `justremote-api.herokuapp.com/api/v1/jobs` |
| workew | 0 | 7 | `?feed=job_feed` + `posts_per_page=100` + keyword queries |
| jobspresso | 0 | 2 | `/feed/` is the blog; jobs are on `?feed=job_feed` |
| authentic_jobs | 0 | 1 | adapter fine — board is genuinely stale (newest relevant post 21d old) |

`jobspresso` / `authentic_jobs` now pull 47 and 22 *relevant* postings, but only
2 and 1 fall inside the recency window. That is the boards' real posting rate,
not a scraper fault — widen them from the UI's per-source days control.

### Recency is now controllable

`scrape_ww_boards.py` takes `--max-days` (default 21, up from a hardcoded 10)
and `_feed_scrape_cmd` passes the UI's per-source `source_days` pin when one is
set. Unpinned boards keep the scraper default — several remote boards keep ads
live for weeks, and the old 10-day window silently zeroed them.

### Dead origins (removed from the runnable set, kept in the catalog as `dead`)

- `europeremotely` — parked domain; every path 403s from `server: Parking/1.0`.
- `germanstartups` — origin refuses the TLS handshake (`tlsv1 alert internal
  error`) from both curl and urllib.

### India lane

- `cutshort` 9 → 953: the keyword paths built from `SEARCH_TERMS`
  (`/jobs/machine-learning`) render an empty shell, so the loop broke after the
  bare `/jobs` page. Real category slugs carry a `-jobs` token, and the public
  jobs sitemap (43k URLs) is now a second source, same as Hirist.
- `naukri` — a dead Chrome (crash / user quit) made every remaining
  term x city log the same "Target page… has been closed". It now relaunches
  once and gives up cleanly on a second death.
- `hirist` (1003), `internshala` (317), `shine` (115), `freshersworld` (238)
  were already healthy.
- `adzuna` still needs ADZUNA_APP_ID / ADZUNA_APP_KEY (free from
  developer.adzuna.com) — no keys, no rows. Nothing else blocks it.

### listings.db — the scrape archive (2026-08-25)

`jobs.json` only holds rows that survive the relevance / lane / prune filters,
and `listings/*.json` are per-source scratch files the next run overwrites.
Anything a scraper found but the pipeline dropped — or anything scraped while a
run was interrupted — used to be unrecoverable. `scripts/listings_db.py` now
archives **every** scraped row into `listings.db` (SQLite, WAL), keyed by
normalized URL, and `_archive_listing_file()` runs *before* dedup so no filter
can drop a row before it is stored.

    python3 scripts/listings_db.py stats
    python3 scripts/listings_db.py backfill          # every listings/*.json
    python3 scripts/listings_db.py export --lane worldwide --out ww.json

It is an archive, not the pipeline: jobs.json stays the source of truth for the
job list, and the UI reads jobs.json. A listing in the archive is not yet a job
in the UI — it has to be merged for that.

### Every board runs — nothing renders as "Disabled"

Boards used to be catalogued `catalog` / `needs_account` / `blocked_captcha`,
so Discover never scheduled them and the UI greyed them out. That hid *why* a
board produced nothing and let a stale classification stick: **wellfound was
marked `blocked_captcha` while its `/jobs` and `/role/*` pages were plain
server-rendered Next.js** with the whole result set in the Apollo cache
(`scrape_wellfound.py`: 0 → 113 rows; the same site serves `angellist_india`,
0 → 43).

Boards with no public listing endpoint now run through
`scripts/scrape_probe_board.py`, which attempts their public URLs every pass
and logs a specific reason, exiting 0 (ran, found nothing, here is why — not a
failure). A board that starts serving listings again picks up with no code
change. Verified 2026-08-25:

| board | probe result |
|---|---|
| otta | redirects to welcometothejungle; its search is an Algolia POST proxy that 404s for public clients |
| turing / hired | roles only behind an account |
| jooble | API needs a partner key (403) |
| producthunt_jobs | 403 to non-browser clients (Cloudflare) |
| hubstaff_talent | redirects to freelancer *profiles*, not job posts |
| jobbatical | board moved behind app.jobbatical.com (login) |
| crossover | client-rendered app, no public API |
| remotetechjobs | serves no job links or feed |
| pangian | redirects to an empty GitHub Pages placeholder |
| outsourcely / topaijobs / angelhub | domains do not resolve |
| europeremotely / germanstartups | parked domain / refused TLS |

All 45 catalogued boards are runnable; `test_all_boards_runnable.py` fails the
build if any board loses its script or lane assignment.

### The list endpoint must not block on a merge

`GET /api/jobs` caches its body under jobs.json's mtime. A merge bumps that
mtime on every write *and* holds LOCK_EX for minutes (it fetches a JD per job),
so every poll missed the cache and blocked in `read_jobs()` — the job list spun
for the whole merge. `read_jobs_nonblocking()` (LOCK_SH | LOCK_NB) now backs
the list path: if a writer holds the lock, serve the previous body instead of
waiting. 30s+ hang → ~0.09s. Covered by `test_jobs_list_nonblocking.py`.

### Coverage optimization (2026-08-26)

Measured with the skip filter off, so the numbers are true scraping capacity,
not "what was new that minute". Raw yield went **609 -> 1,009 rows (+66%)**.

| board | before | after | what was limiting it |
|---|---|---|---|
| arbeitnow | 113 | 216 | stopped at 5 API pages |
| himalayas | 49 | 172 | stopped at offset 400 of a reported 102,586 |
| nodesk | 102 | 162 | 5 category indexes -> 12 |
| wellfound | 111 | 161 | 8 role paths -> 20, incl. `/role/r/…` remote variants |
| jobicy | 38 | 59 | queried `industry` only -> `industry` + `geo` + `tag` |
| themuse | 22 | 53 | 3 categories x 4 pages -> 7 x 8 |
| weworkremotely | 38 | 48 | 6 category feeds -> 8 |
| remoteok | 1 | 3 | 10 tags -> 20 (its bare `/api` is a *general* feed) |

Capped by the source, not the scraper — confirmed, do not "fix": landing_jobs
21 (the API holds ~58 jobs; offset 100+ returns nothing), relocate_me 22,
yc_jobs 23, dynamitejobs 20, jsremotely 11, working_nomads 12, justremote 9,
rss_feeds 8, workew 6, remotive 2 (free API returns 18 rows whatever the
params).

Coverage that measured as pure waste was **removed** again:

- YC filters `/jobs/role/<role>` client-side — engineering and data-science
  share 40 of 41 links, so fanning out over roles is 12 requests for nothing.
- Dynamite Jobs' `remote-devops-jobs` / `remote-engineering-jobs` /
  `remote-it-jobs` return a 200 shell with zero postings.
- Landing.jobs offsets past 100 return nothing.

`scripts/test_scraper_coverage.py` pins each of these so a later edit cannot
silently narrow the scrape.

### Three recency windows that disagree

Optimizing surfaced an incoherence rather than a bug:

- the feed scrapers now fetch **21 days** (`DEFAULT_MAX_DAYS`)
- the pipeline prunes anything *dated* older than **10 days**
  (`STALE_LISTING_MAX_AGE_DAYS`)
- the UI's per-source "days" control was capped at **10**

So a wider scrape only surfaces more *undated* listings; dated rows past 10
days are merged and then pruned. At the time of writing, 0 open jobs were
older than 10 days and 990 of 1,415 had no date at all.

`SOURCE_DAYS_MAX` was raised 10 -> 60 so the control can at least express a
wider window. `STALE_LISTING_MAX_AGE_DAYS` was deliberately left at 10 —
whether a 45-day-old posting is worth applying to is a product decision, not a
scraper one. Measured yield by window:

| board | 21d | 45d | 90d | 365d |
|---|---|---|---|---|
| jobspresso | 1 | 3 | 7 | 47 |
| authentic_jobs | 0 | 7 | 7 | 21 |
| workew | 6 | 21 | 36 | 38 |
| weworkremotely | 48 | 66 | 66 | 68 |
| remoteok | 3 | 17 | 21 | 38 |

### "0 listings" now says which kind of zero

Every scraper filters against skip-urls (every URL already in jobs.json, *any*
status, plus blocked-URL tombstones). On a second pass over the same boards
that legitimately removes 100% of the results — Himalayas found 50 and dropped
50 — and the row read a bare "0 listings", identical to a crashed scraper.
Sources now report `No new roles — N already in your list`.

Note the consequence: a listing scraped once and then dropped by a filter is
permanently invisible *and* permanently un-rescrapeable, because its URL stays
in the skip set. That is why a second same-day run can only ever show zeros.

### Still catalog-only (probed, not scrapeable without CAPTCHA/browser/login)

skipthedrive + remotetechjobs (feeds 404/disabled), outsourcely, pangian,
topaijobs (dead endpoints), hubstaff_talent + jobbatical (JS apps, no API),
crossover (401 API). jooble/usajobs need API keys; glassdoor/ziprecruiter/
wellfound stay blocked_captcha; monster/careerbuilder/simplyhired/google_jobs/
dice heavy anti-bot.
