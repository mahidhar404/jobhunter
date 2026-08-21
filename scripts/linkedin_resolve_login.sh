#!/bin/bash
# Canonical LinkedIn resolve login — same as ./open_linkedin_resolve.sh
# (PartyRock-style CfT + CDP; dedicated profile; leave browser open).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/open_linkedin_resolve.sh"
