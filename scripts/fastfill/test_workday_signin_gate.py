#!/usr/bin/env python3
"""Integration test: Workday sign-in-only gate → reveal → create account.

Drives the REAL ``exp_workday_selectors`` auth helpers against a fake Workday
gate rendered via ``page.set_content`` (Playwright, headless). Reproduces
Yogesh's flow:

  1. Land on a sign-in gate with SSO/social + "Sign in with email" (no email
     field visible, no obvious Create Account).
  2. Click "Sign in with email" → email + password + "Create Account" link
     appear.
  3. Click "Create Account" → create-account form (verifyPassword + submit).

Asserts the gate-action decision at each step and that the actual gated clicks
(never-submit backstop) navigate the multi-step gate. Dummy only; never submits
an application, never CAPTCHA.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import exp_workday_selectors as w  # noqa: E402

# Fake Workday-style gate. Reveal + switch are wired in JS so the actual helper
# clicks drive real DOM transitions (data-automation-id matches the selectors).
GATE_HTML = """
<!doctype html><html><head><meta charset="utf-8"><title>Sign In</title></head>
<body>
  <div id="sso">
    <h1>Sign In</h1>
    <button type="button" data-automation-id="ssoGoogle">Sign in with Google</button>
    <button type="button" data-automation-id="signInWithEmail"
            onclick="revealEmail()">Sign in with email</button>
  </div>

  <div id="formhost"></div>

  <script>
    // Forms are injected on demand so DOM element counts reflect the real gate
    // state at each step (Workday mounts the create fields only after the switch).
    function revealEmail() {
      document.getElementById('sso').style.display = 'none';
      document.getElementById('formhost').innerHTML =
        '<label>Email <input data-automation-id="email" type="email"></label>' +
        '<label>Password <input data-automation-id="password" type="password"></label>' +
        '<button type="button" data-automation-id="signInSubmitButton">Sign In</button>' +
        '<a href="#" data-automation-id="createAccountLink" ' +
        'onclick="revealCreate();return false;">Create Account</a>';
    }
    function revealCreate() {
      document.getElementById('formhost').innerHTML =
        '<label>Email <input data-automation-id="email" type="email"></label>' +
        '<label>Password <input data-automation-id="password" type="password"></label>' +
        '<label>Verify Password ' +
        '<input data-automation-id="verifyPassword" type="password"></label>' +
        '<div data-automation-id="createAccountCheckbox" role="checkbox" ' +
        'aria-checked="false" ' +
        'onclick="this.setAttribute(\\'aria-checked\\',\\'true\\')">I agree</div>' +
        '<button type="button" data-automation-id="createAccountSubmitButton">' +
        'Create Account</button>';
    }
  </script>
</body></html>
"""


# Direct sign-in gate: email+password + Create Account visible immediately (common case).
DIRECT_GATE_HTML = """
<!doctype html><html><head><meta charset="utf-8"><title>Sign In</title></head>
<body>
  <h1>Sign In to Apply</h1>
  <label>Email <input data-automation-id="email" type="email"></label>
  <label>Password <input data-automation-id="password" type="password"></label>
  <button type="button" data-automation-id="signInSubmitButton">Sign In</button>
  <a href="#" data-automation-id="createAccountLink"
     onclick="revealCreate();return false;">Create Account</a>
  <div id="formhost"></div>
  <script>
    function revealCreate() {
      document.getElementById('formhost').innerHTML =
        '<label>Email <input data-automation-id="email" type="email"></label>' +
        '<label>Password <input data-automation-id="password" type="password"></label>' +
        '<label>Verify Password ' +
        '<input data-automation-id="verifyPassword" type="password"></label>' +
        '<div data-automation-id="createAccountCheckbox" role="checkbox" ' +
        'aria-checked="false" ' +
        'onclick="this.setAttribute(\\'aria-checked\\',\\'true\\')">I agree</div>' +
        '<button type="button" data-automation-id="createAccountSubmitButton">' +
        'Create Account</button>';
    }
  </script>
</body></html>
"""


async def _probe_action(page, *, prefer_stored_signin: bool) -> str:
    return w.workday_auth_gate_action(
        has_create_form=await w._create_account_form(page),
        has_signin_form=await w._password_only_signin(page),
        has_email_field=await w._email_field_present(page),
        has_sign_in_with_email=await w._sign_in_with_email_present(page),
        has_create_account_link=await w._create_account_link_present(page),
        prefer_stored_signin=prefer_stored_signin,
    )


async def _run_direct_signin_gate() -> None:
    """Common case: sign-in form visible on landing — switch_then_create, not sign_in."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(DIRECT_GATE_HTML, wait_until="domcontentloaded")
            assert await w._email_field_present(page)
            assert await w._password_only_signin(page)
            assert not await w._create_account_form(page), "link must not count as create form"
            assert await w._create_account_link_present(page)
            assert await _probe_action(page, prefer_stored_signin=False) == "switch_then_create"
            assert await _probe_action(page, prefer_stored_signin=True) == "sign_in"
            switch = await w._switch_to_create_account(page)
            assert any(c.get("action") == "clicked" for c in switch), switch
            assert await w._create_account_form(page)
            assert await _probe_action(page, prefer_stored_signin=False) == "create_account"
        finally:
            await browser.close()


async def _run() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(GATE_HTML, wait_until="domcontentloaded")

            # Step 1: sign-in-only gate, email hidden behind "Sign in with email".
            assert await w._sign_in_with_email_present(page), "reveal button missing"
            assert not await w._email_field_present(page), "email should be hidden"
            assert not await w._create_account_form(page)
            assert await _probe_action(page, prefer_stored_signin=False) == "reveal_email"

            # Step 2: reveal the email form via the real gated click.
            reveal = await w._reveal_email_auth_form(page)
            assert any(c.get("action") == "clicked" for c in reveal), reveal
            assert await w._email_field_present(page), "email field never appeared"
            assert await w._password_only_signin(page), "expected sign-in form"
            assert await w._create_account_link_present(page), "create link missing"

            # No stored creds ⇒ switch to Create Account, never sign in with a
            # fresh (unregistered) dummy email.
            assert (
                await _probe_action(page, prefer_stored_signin=False)
                == "switch_then_create"
            )
            # If host creds were stored, prefer Sign In instead.
            assert (
                await _probe_action(page, prefer_stored_signin=True) == "sign_in"
            )

            # Step 3: switch to the create-account form via the real gated click.
            switch = await w._switch_to_create_account(page)
            assert any(c.get("action") == "clicked" for c in switch), switch
            assert await w._create_account_form(page), "create form never appeared"
            assert (
                await _probe_action(page, prefer_stored_signin=False)
                == "create_account"
            )
        finally:
            await browser.close()


def main() -> int:
    asyncio.run(_run_direct_signin_gate())
    asyncio.run(_run())
    print("test_workday_signin_gate: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
