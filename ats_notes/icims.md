# iCIMS - known form structure

## Known blocker (already hit in production - see Panasonic, blocked_captcha)
iCIMS shows a CAPTCHA challenge on the Referral, Login, Password Reset,
Basic Profile, and Profile Creation pages - at most once per session, but
it will commonly appear during account creation on this platform. Per the
Hard Rules, this is a stop-and-log-`Blocked-CAPTCHA` situation, not
something to work around - don't spend a turn trying variations on it.

## Structural notes
- iCIMS asks for location in multiple separate places (mailing address,
  work-authorization location, and sometimes a separate "preferred
  location" question) - treat each as its own distinct answer, don't
  assume filling one covers the others.
- Skills are sometimes a checkbox/keyword-select list rather than a
  free-text field.

## Source
https://community.icims.com (iCIMS's own support/knowledge base) plus this
project's own real run (Panasonic, blocked at the hCaptcha login step on
2026-07-25).
