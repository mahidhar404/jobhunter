# India + Worldwide Discovery

Two discovery lanes replace the old US/India region model:

| Lane | Keep | Pay |
|------|------|-----|
| **India** | Any India-related role (onsite / hybrid / India-remote) | ₹ / LPA / CTC |
| **Worldwide** | Non-India any work mode + **US remote**; **drop US onsite/hybrid** | Native currency |

Settings: `discover_india` + `discover_worldwide` in `logs/discovery_settings.json`
(legacy `discover_us` migrates to `discover_worldwide`). Env:
`JOBHUNTER_DISCOVERY_REGIONS=india,worldwide`.

## Sources

- **India boards:** Internshala, Hirist, Cutshort, Shine, Freshersworld, Naukri, Adzuna IN
- **Shared:** Indeed / LinkedIn (JobSpy), ATS boards — lane stamped after scrape
- **Worldwide feeds:** RemoteOK, Remotive, Jobicy, RSS bundle, Built In, Himalayas,
  Arbeitnow, Landing.jobs, Working Nomads, EuropeRemotely, relocate.me, germanstartups,
  JustRemote, DynamiteJobs, WWR, Jobspresso, Authentic Jobs, NoDesk, JS Remotely, …
- Full ~70-board catalog in `dashboard/discovery_sources.py` with `scrape_status`
  (`active` / `rss` / `api` / `blocked_*` / `catalog` / `dead`). Blocked boards are
  listed in Discover but not scraped (no CAPTCHA solve).

Worldwide adapters: `scripts/scrape_ww_boards.py` + `scripts/ww_scrape_common.py`.
