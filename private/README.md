# private/ — real data lives here (git-ignored)

This folder is the single home for everything sensitive: real PII, secrets,
credentials, application history, and regenerable artifacts. Everything in here
is git-ignored except this README (see the private/ rules in .gitignore).
Nothing here is ever shared or exported.

## What belongs here (target state)

Once the migration in docs/RESTRUCTURE_PROPOSAL.md (Phase 2) is approved and done,
these move in from the repo root:

- Secrets: credentials.json, web_keys.json, option_mappings.json
- PII: profile.json (+ .bak*), application_tracker.xlsx
- Data/artifacts: jobs.json (+ .bak*), blocked_urls.json, resumes/, listings/, logs/
- Agent memory: MEMORY.md, memory/, USER.md
- Browser sessions: the various *_chrome_profile/ / *_ui_profile/ dirs

NOT MOVED YET. As of this proposal, the files above still live at the repo
root because dozens of scripts hardcode root-relative paths. Do NOT move them
until the config module (scripts/jobhunter_paths.py) is adopted and the in-flight
India-discovery work has merged. See the proposal for the exact plan.

## Getting started (new contributor)

You never receive real data. Create your own from the templates in fixtures/:

    cp fixtures/profile.example.json     profile.json
    cp fixtures/credentials.example.json credentials.json
    cp fixtures/web_keys.example.json    web_keys.json
    cp fixtures/.env.example             .env

Then fill them with YOUR OWN values. These stay local and git-ignored — never commit them.

## Golden rule

Real personal data must never leave this machine or land in the shared/exported
surface. If unsure whether something is safe to share, it goes in private/.
