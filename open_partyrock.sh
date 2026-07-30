#!/bin/bash
# Opens a single, dedicated Chrome window/profile just for PartyRock - separate
# from both your daily-driver Chrome profile and Skyvern's own automation
# browser windows (those are throwaway Playwright-controlled Chromium
# instances, unrelated to this). Sign in once here; the login persists in this
# profile's own directory across every future run of this script, so you never
# need to sign in again unless you clear this specific profile folder.
PROFILE_DIR="/Users/job/.openclaw/workspace/job-hunter/partyrock_chrome_profile"

open -na "Google Chrome" --args \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  "https://partyrock.aws"
