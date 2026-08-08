# India Discovery

Opt-in India job discovery layered on top of the existing US pipeline. **US is
the default; India is off unless you turn it on.** Nothing about India changes
US behavior when India is off.

## Region semantics

A listing is "India" when its `location` clearly indicates India (Indian cities,
`India`, ISO `IN` tail) **or** remote/WFH that accepts India (`Remote - India`,
`Anywhere in India`, `WFH`). HQ-in-India-with-a-US-office-location is *not*
India for v1. Source of truth is `scripts/discovery_filters.py`:
`is_india_location`, `location_matches_regions`, `region_for_location`. The Ops
UI mirrors the same heuristics in `dashboard/static/app.js`
(`INDIA_LOCATION_RE` / `isIndiaLocation` / `locationMatchesRegions`) — **keep the
two in sync**.

- Enabled regions come from `logs/discovery_settings.json`
  (`discover_us` default `true`, `discover_india` default `false`; never both
  false). The server exports them to child scrapers via the
  `JOBHUNTER_DISCOVERY_REGIONS` env var (`enabled_regions_from_env()`).
- The qualify/dedup gate keeps a listing only if its location matches an enabled
  region. The drop-reason code is the stable string `non_us_location` and now
  means "outside enabled regions".
- Each written job is stamped with `region` (`us` | `india` | `unknown`) by
  `write_discovered_jobs.py` for the Ops **Region** filter (`All` | `US` |
  `India`).

## Sources

### Reused (no new scraper)

- **Existing ATS boards** (`scrape_ats.py`): India is unlocked purely by the
  region gate + an India-heavy seed added to `ats_companies.json` (Greenhouse /
  Lever / Ashby / SmartRecruiters / Workable — Razorpay, PhonePe, Groww, Meesho,
  CRED, Freshworks, Postman, Sarvam, Atlan, plus large-India-GCC globals). All
  slugs were verified live via `probe_slug` before adding.
- **Indeed / LinkedIn India** (`scout.py`): when India is enabled, scout runs an
  India pass with `location="India"`, `country_indeed="india"` in the same
  process as the US pass, accumulating into the single `--out` listings file
  (no clobber; regions come from `--regions` or `JOBHUNTER_DISCOVERY_REGIONS`).
  LinkedIn India is brittle/low-priority; the Easy-Apply skip stays.
- **Built In** stays **US-only** — we do not fake an India Built In.

### New Wave-A India scrapers

These are India-only (`INDIA_ONLY_SOURCE_IDS` in `dashboard/server.py` and
`app.js`): they only run when India is enabled, and their Discover rows are
greyed/forced-off when India is off (auto-enabled the first time India is turned
on). Shared helpers live in `scripts/india_scrape_common.py` (plain descriptive
User-Agent, polite delays, no login/CAPTCHA). Each has a fixture-based normalize
test.

| Source | Script | Output | Notes |
|--------|--------|--------|-------|
| Internshala | `scripts/scrape_internshala.py` | `listings/<date>-internshala.json` | HTML job cards; software/data categories |
| Hirist | `scripts/scrape_hirist.py` | `listings/<date>-hirist.json` | Prefer XHR/JSON search API; LPA/skills |
| Cutshort | `scripts/scrape_cutshort.py` | `listings/<date>-cutshort.json` | Curated startups; JSON jobs API |
| Adzuna IN | `scripts/scrape_adzuna.py` | `listings/<date>-adzuna-in.json` | Official `in` API; **skips cleanly if no keys** |

All normalize to the shared listing shape (`title, company, location,
job_url/apply_url, date_posted, source`, description snippet) consumed by
`dedup_listings.py` / `write_discovered_jobs.py`.

## Adzuna keys

`scrape_adzuna.py` reads `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` from the environment
first, then from a git-ignored `web_keys.json` at the repo root. If neither is
present it logs `disabled/skipped (adzuna): no Adzuna API keys` and writes an
empty listing file — **no crash**, and the Discover UI shows the source as
skipped. Register for a free key at the Adzuna developer portal. If Adzuna
results are ever displayed/republished, include the required Adzuna attribution.

## INR / LPA salary

Indian salaries are quoted in LPA / lakhs / lacs. `discovery_filters.extract_inr_salary`
parses these into **display-only** fields (`salary_inr_display`,
`salary_inr_min_lpa`, `salary_inr_max_lpa`) added by the writer. They are never
used for pruning (INR salaries must not trip the USD salary-floor logic).

## ToS / safety

All India scrapers only **read** public listing pages / official APIs at
personal, low volume (polite rate limits, plain UA). No login, no CAPTCHA
solving, no application submission — same hard rules as the rest of the project.
Respect each site's robots/ToS; keep volume personal-scale.

## Wave B backlog (not in v1)

- **Naukri** (Akamai anti-bot), **Foundit/Shine**, **Instahyre** (Playwright),
  **Wellfound** (DataDome) — documented here but deferred; do not block v1.
- **NCS (ncs.gov.in)** — skipped (low tech-role signal).
