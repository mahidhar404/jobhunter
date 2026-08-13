#!/usr/bin/env python3
"""Unit tests: Sign-in wall detection + Create-account priority (no browser).

Dummy-only; never submits; never CAPTCHA.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from iframe_ctx import (  # noqa: E402
    auth_advance_priority,
    create_account_link_priority,
    create_account_sentinel_path,
    looks_like_login_context,
    normalize_auth_label,
    sign_in_wall_from_signals,
)
from button_gate import NAV_KINDS, gate_click  # noqa: E402
from exp_workday_selectors import (  # noqa: E402
    CREATE_ACCOUNT_LINK_SELECTORS,
    SIGN_IN_WITH_EMAIL_SELECTORS,
    _workday_prefer_stored_signin,
    workday_auth_gate_action,
)


def test_normalize_sign_in_period():
    assert normalize_auth_label("Sign in.") == "sign in"
    assert normalize_auth_label("Create account") == "create account"


def test_create_account_beats_sign_in_priority():
    ca = create_account_link_priority("Create account")
    assert ca is not None and ca == 0
    assert create_account_link_priority("Sign in.") is None
    assert create_account_link_priority("Sign in with Google") is None
    assert create_account_link_priority("Don't have an account?") is not None
    assert create_account_link_priority("Sign up", href="/register") is not None
    # href alone
    assert create_account_link_priority("", href="https://x.example/signup") is not None


def test_auth_advance_demotes_sign_in():
    create_pri = auth_advance_priority("Create account")
    sign_pri = auth_advance_priority("Sign in.")
    assert create_pri is not None
    assert sign_pri is not None
    assert create_pri < sign_pri  # create wins when both present


def test_stripe_dashboard_is_sign_in_wall():
    assert sign_in_wall_from_signals(
        url="https://dashboard.stripe.com/login",
        body="",
        email_count=0,
        password_count=0,
        appish_count=0,
    )
    assert looks_like_login_context("https://dashboard.stripe.com/login")


def test_sign_in_to_your_account_email_only():
    """Stripe paints heading + email before password — still a wall."""
    assert sign_in_wall_from_signals(
        body="Sign in to your account.\nEmail\n",
        url="https://job-boards.greenhouse.io/stripe/jobs/1",
        email_count=1,
        password_count=0,
        appish_count=0,
    )


def test_sign_in_wall_with_password():
    assert sign_in_wall_from_signals(
        body="Sign in to your account. Forgot your password? Remember me",
        url="https://dashboard.stripe.com/login",
        email_count=1,
        password_count=1,
        appish_count=0,
    )


def test_app_form_not_sign_in_wall():
    assert not sign_in_wall_from_signals(
        body="Apply for this job",
        url="https://job-boards.greenhouse.io/acme/jobs/1",
        email_count=1,
        password_count=0,
        appish_count=2,  # first name / resume
    )


def test_prefer_stored_suppressed_in_test_mode():
    stored = {"email": "a@b.co", "password": "secret"}
    assert not _workday_prefer_stored_signin({"test_mode": True}, stored=stored)
    assert not _workday_prefer_stored_signin({"dummy": True}, stored=stored)
    assert _workday_prefer_stored_signin(
        {"test_mode": False, "dummy": False}, stored=stored
    )


def test_prefer_stored_suppressed_on_force_create():
    stored = {"email": "a@b.co", "password": "secret"}
    assert not _workday_prefer_stored_signin(
        {"test_mode": False, "force_create_account": True}, stored=stored
    )
    assert not _workday_prefer_stored_signin(
        {"test_mode": False, "auth_gate": {"forced": True}}, stored=stored
    )


def test_gate_action_create_form_wins():
    """A create-account form present ⇒ fill + create (highest priority)."""
    assert (
        workday_auth_gate_action(
            has_create_form=True,
            has_signin_form=False,
            has_email_field=True,
            has_sign_in_with_email=False,
            has_create_account_link=True,
            prefer_stored_signin=True,
        )
        == "create_account"
    )


def test_gate_action_reveal_email_when_hidden():
    """SSO/social-first gate: email hidden behind 'Sign in with email' ⇒ reveal."""
    assert (
        workday_auth_gate_action(
            has_create_form=False,
            has_signin_form=False,
            has_email_field=False,
            has_sign_in_with_email=True,
            has_create_account_link=False,
            prefer_stored_signin=False,
        )
        == "reveal_email"
    )
    # Even when a create link is technically detectable, reveal the form first.
    assert (
        workday_auth_gate_action(
            has_create_form=False,
            has_signin_form=False,
            has_email_field=False,
            has_sign_in_with_email=True,
            has_create_account_link=True,
            prefer_stored_signin=False,
        )
        == "reveal_email"
    )


def test_gate_action_signin_only_no_stored_switches_to_create():
    """Sign-in-only gate + Create Account link + no stored creds ⇒ switch+create.

    This is Yogesh's flow: never Sign In with an unregistered dummy email; click
    Create Account to mint a fresh dummy account instead.
    """
    assert (
        workday_auth_gate_action(
            has_create_form=False,
            has_signin_form=True,
            has_email_field=True,
            has_sign_in_with_email=False,
            has_create_account_link=True,
            prefer_stored_signin=False,
        )
        == "switch_then_create"
    )


def test_gate_action_stored_creds_prefers_sign_in():
    """Host has stored email+password (account exists) ⇒ Sign In, not create."""
    assert (
        workday_auth_gate_action(
            has_create_form=False,
            has_signin_form=True,
            has_email_field=True,
            has_sign_in_with_email=False,
            has_create_account_link=True,
            prefer_stored_signin=True,
        )
        == "sign_in"
    )


def test_gate_action_signin_no_create_link_signs_in():
    """Sign-in form with no create path offered ⇒ fall back to Sign In."""
    assert (
        workday_auth_gate_action(
            has_create_form=False,
            has_signin_form=True,
            has_email_field=True,
            has_sign_in_with_email=False,
            has_create_account_link=False,
            prefer_stored_signin=False,
        )
        == "sign_in"
    )


def test_gate_action_none_when_nothing_present():
    assert (
        workday_auth_gate_action(
            has_create_form=False,
            has_signin_form=False,
            has_email_field=False,
            has_sign_in_with_email=False,
            has_create_account_link=False,
            prefer_stored_signin=False,
        )
        == "none"
    )


def test_reveal_and_switch_selectors_present():
    """Selector packs must include the reveal + create-switch controls."""
    blob = " ".join(SIGN_IN_WITH_EMAIL_SELECTORS).lower()
    assert "sign in with email" in blob or "signinwithemail" in blob
    assert "continue with email" in blob
    ca = " ".join(CREATE_ACCOUNT_LINK_SELECTORS).lower()
    assert "createaccountlink" in ca
    assert "create account" in ca


def test_reveal_labels_gate_as_navigation():
    """'Sign in with email' / 'Continue with email' must pass the never-submit
    gate as navigation (else the reveal click is refused → 0 fills)."""
    for lbl in (
        "Sign in with email",
        "Continue with email",
        "Sign In with Email",
        "Log in with email",
    ):
        g = gate_click(lbl)
        assert g.get("ok"), (lbl, g)
        assert g.get("kind") in NAV_KINDS, (lbl, g)
    # Safety backstop preserved: FINAL still refused.
    assert not gate_click("Submit application").get("ok")


def test_sentinel_path_respects_captcha_sibling(tmp_path, monkeypatch):
    sentinel = tmp_path / ".captcha_continue"
    monkeypatch.setenv("FASTFILL_CAPTCHA_CONTINUE_FILE", str(sentinel))
    monkeypatch.delenv("FASTFILL_CREATE_ACCOUNT_FILE", raising=False)
    p = create_account_sentinel_path()
    assert p == tmp_path / ".force_create_account"


def main() -> int:
    # Minimal runner without pytest fixtures for sentinel env test
    test_normalize_sign_in_period()
    test_create_account_beats_sign_in_priority()
    test_auth_advance_demotes_sign_in()
    test_stripe_dashboard_is_sign_in_wall()
    test_sign_in_to_your_account_email_only()
    test_sign_in_wall_with_password()
    test_app_form_not_sign_in_wall()
    test_prefer_stored_suppressed_in_test_mode()
    test_prefer_stored_suppressed_on_force_create()
    test_gate_action_create_form_wins()
    test_gate_action_reveal_email_when_hidden()
    test_gate_action_signin_only_no_stored_switches_to_create()
    test_gate_action_stored_creds_prefers_sign_in()
    test_gate_action_signin_no_create_link_signs_in()
    test_gate_action_none_when_nothing_present()
    test_reveal_and_switch_selectors_present()
    test_reveal_labels_gate_as_navigation()
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        os.environ["FASTFILL_CAPTCHA_CONTINUE_FILE"] = str(
            Path(td) / ".captcha_continue"
        )
        os.environ.pop("FASTFILL_CREATE_ACCOUNT_FILE", None)
        assert create_account_sentinel_path() == Path(td) / ".force_create_account"
    print("test_auth_create_account: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
