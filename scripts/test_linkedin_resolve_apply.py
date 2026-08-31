#!/usr/bin/env python3
"""Unit tests for authenticated LinkedIn → offsite apply URL capture.

No real LinkedIn, no live browser, no applicant PII. Browser I/O is mocked.

Run:
  python3 scripts/test_linkedin_resolve_apply.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import linkedin_resolve_apply as lra  # noqa: E402
import linkedin_resolve_profile as lrp  # noqa: E402


LINKEDIN_JOB = "https://www.linkedin.com/jobs/view/4452248501"
GREENHOUSE = "https://boards.greenhouse.io/acme/jobs/12345"
LEVER = "https://jobs.lever.co/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class TestUrlHelpers(unittest.TestCase):
    def test_is_linkedin_job_url(self):
        self.assertTrue(lra.is_linkedin_job_url(LINKEDIN_JOB))
        self.assertTrue(lra.is_linkedin_job_url("https://linkedin.com/jobs/view/1/"))
        self.assertFalse(lra.is_linkedin_job_url(GREENHOUSE))
        self.assertFalse(lra.is_linkedin_job_url("https://www.indeed.com/viewjob?jk=1"))

    def test_is_acceptable_offsite_apply(self):
        self.assertTrue(lra.is_acceptable_offsite_apply(GREENHOUSE))
        self.assertTrue(lra.is_acceptable_offsite_apply(LEVER))
        self.assertTrue(
            lra.is_acceptable_offsite_apply("https://careers.acme.com/jobs/ml-engineer")
        )
        self.assertFalse(lra.is_acceptable_offsite_apply(LINKEDIN_JOB))
        self.assertFalse(lra.is_acceptable_offsite_apply("https://www.indeed.com/viewjob?jk=1"))
        self.assertFalse(
            lra.is_acceptable_offsite_apply(
                "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/x"
            )
        )

    def test_page_signals_captcha(self):
        self.assertTrue(lra.page_looks_like_captcha("Please complete the captcha to continue"))
        self.assertTrue(lra.page_looks_like_captcha("hCaptcha challenge iframe"))
        self.assertFalse(lra.page_looks_like_captcha("Apply on company website"))

    def test_page_signals_signin(self):
        self.assertTrue(
            lra.page_looks_like_signin_wall(
                "Sign in to view jobs", "https://www.linkedin.com/login"
            )
        )
        self.assertTrue(
            lra.page_looks_like_signin_wall(
                "", "https://www.linkedin.com/checkpoint/lg/login"
            )
        )
        self.assertFalse(lra.page_looks_like_signin_wall("Senior ML Engineer", LINKEDIN_JOB))

    def test_classify_apply_button(self):
        self.assertEqual(lra.classify_apply_control("Easy Apply"), "easy_apply")
        self.assertEqual(lra.classify_apply_control("Apply"), "external_or_unknown")
        self.assertEqual(lra.classify_apply_control("Apply on company website"), "external")
        self.assertEqual(lra.classify_apply_control("Continue to application"), "external")


class TestApplyHrefExtraction(unittest.TestCase):
    def test_direct_external_anchor(self):
        html = (
            '<div class="jobs-apply-button">'
            f'<a href="{GREENHOUSE}">Apply on company website</a>'
            "</div>"
        )
        cands = lra.extract_apply_href_candidates_from_html(html)
        self.assertEqual(lra.pick_offsite_apply_href(cands), GREENHOUSE)

    def test_nested_anchor_under_button(self):
        html = (
            "<button aria-label=\"Apply\">"
            f'<a href="{LEVER}">Apply</a>'
            "</button>"
        )
        self.assertEqual(
            lra.pick_offsite_apply_href(lra.extract_apply_href_candidates_from_html(html)),
            LEVER,
        )

    def test_data_url_attribute(self):
        html = (
            f'<button data-url="{GREENHOUSE}" data-control-name="jobdetails_topcard_inapply">'
            "Apply on company website</button>"
        )
        self.assertEqual(
            lra.pick_offsite_apply_href(lra.extract_apply_href_candidates_from_html(html)),
            GREENHOUSE,
        )

    def test_linkedin_href_and_js_void_ignored(self):
        html = (
            f'<a href="{LINKEDIN_JOB}">Apply</a>'
            '<a href="javascript:void(0)">Apply on company website</a>'
            '<button data-control-name="easy_apply_button">Easy Apply</button>'
        )
        self.assertIsNone(
            lra.pick_offsite_apply_href(lra.extract_apply_href_candidates_from_html(html))
        )
        self.assertTrue(lra.is_useless_apply_href(LINKEDIN_JOB))
        self.assertTrue(lra.is_useless_apply_href("javascript:void(0)"))

    def test_unwrap_linkedin_redirect_query(self):
        wrapped = (
            "https://www.linkedin.com/redir/redirect?"
            f"url={GREENHOUSE.replace(':', '%3A').replace('/', '%2F')}&urlhash=x"
        )
        # parse_qs will unquote; helper should return greenhouse
        self.assertEqual(lra.unwrap_linkedin_redirect_url(wrapped), GREENHOUSE)
        self.assertEqual(
            lra.pick_offsite_apply_href([{"label": "Apply", "href": wrapped}]),
            GREENHOUSE,
        )

    def test_unwrap_linkedin_safety_go(self):
        """Live LinkedIn offsite Apply uses /safety/go/?url=…"""
        dest = "https://careers.activision.com/job/ACPUUSR027920EXTERNAL/Analytics-Engineer"
        from urllib.parse import quote

        wrapped = (
            "https://www.linkedin.com/safety/go/?url="
            + quote(dest, safe="")
            + "&urlhash=v_KC&isSdui=true"
        )
        self.assertEqual(lra.unwrap_linkedin_redirect_url(wrapped), dest)
        picked = lra.pick_offsite_apply_href(
            [{"label": "Apply", "href": wrapped, "source": "href"}]
        )
        self.assertTrue(picked)
        self.assertIn("careers.activision.com", picked)
        self.assertNotIn("linkedin.com", picked)

    def test_embedded_company_apply_url_json(self):
        html = (
            '<code style="display:none">'
            f'{{"companyApplyUrl":"{GREENHOUSE}","easyApply":false}}'
            "</code>"
        )
        self.assertEqual(
            lra.pick_offsite_apply_href(lra.extract_apply_href_candidates_from_html(html)),
            GREENHOUSE,
        )

    def test_workday_href_not_picked(self):
        wd = "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/x"
        html = f'<a href="{wd}">Apply on company website</a>'
        self.assertIsNone(
            lra.pick_offsite_apply_href(lra.extract_apply_href_candidates_from_html(html))
        )

    def test_decide_prefers_href_over_click(self):
        out = lra.decide_from_snapshot(
            job_url=LINKEDIN_JOB,
            current_url=LINKEDIN_JOB,
            page_text="Apply on company website",
            apply_labels=["Apply on company website"],
            profile_ready=True,
            apply_href_candidates=[{"label": "Apply on company website", "href": GREENHOUSE}],
        )
        self.assertEqual(out["reason"], "linkedin_apply_href")
        self.assertEqual(out["url"], GREENHOUSE)
        self.assertEqual(out["confidence"], "high")
        self.assertNotIn("action", out)

    def test_resolve_uses_href_without_click(self):
        calls = {"n": 0}

        def session_fn(job_url, profile_dir, *, click_label=None):
            calls["n"] += 1
            if click_label:
                raise AssertionError("click should not run when href is offsite")
            return {
                "current_url": LINKEDIN_JOB,
                "page_text": "Apply on company website",
                "apply_labels": ["Apply on company website"],
                "apply_href_candidates": [
                    {"label": "Apply on company website", "href": GREENHOUSE}
                ],
            }

        result = lra.resolve_linkedin_apply_url(
            LINKEDIN_JOB,
            profile_dir=Path("/tmp/fake-li-profile"),
            profile_ready=True,
            session_fn=session_fn,
            prefer_http=False,
        )
        self.assertEqual(result["reason"], "linkedin_apply_href")
        self.assertEqual(result["url"], GREENHOUSE)
        self.assertEqual(calls["n"], 1)


class TestHttpCookieFastPath(unittest.TestCase):
    """Cookie + HTTP HTML parse — no Playwright / no live LinkedIn."""

    def test_decide_from_http_html_safety_go(self):
        from urllib.parse import quote

        dest = GREENHOUSE
        wrapped = (
            "https://www.linkedin.com/safety/go/?url="
            + quote(dest, safe="")
            + "&urlhash=abc"
        )
        html = (
            f"<html><title>ML Engineer | Acme | LinkedIn</title><body>"
            f'<a href="{wrapped}">Apply on company website</a>'
            f'<!-- recaptcha script src should not block href parse -->'
            f"</body></html>"
        )
        out = lra.decide_from_http_html(job_url=LINKEDIN_JOB, html=html, final_url=LINKEDIN_JOB)
        self.assertEqual(out.get("confidence"), "high")
        self.assertEqual(out.get("reason"), "linkedin_apply_href")
        self.assertEqual(out.get("url"), GREENHOUSE)
        self.assertEqual(out.get("method"), "linkedin_http")
        self.assertFalse(out.get("captcha"))

    def test_decide_from_http_html_extracts_date_posted(self):
        html = (
            "<html><body>"
            f'<a href="{GREENHOUSE}">Apply on company website</a>'
            '<script type="application/ld+json">'
            '{"@type":"JobPosting","datePosted":"2026-08-12"}'
            "</script>"
            "</body></html>"
        )
        out = lra.decide_from_http_html(job_url=LINKEDIN_JOB, html=html, final_url=LINKEDIN_JOB)
        self.assertEqual(out.get("date_posted"), "2026-08-12")
        self.assertEqual(out.get("date_posted_source"), "linkedin_http")
        self.assertNotIn("date_posted_fallback", out)

    def test_decide_from_http_html_relative_posted_fallback(self):
        html = (
            "<html><body>"
            f'<a href="{GREENHOUSE}">Apply on company website</a>'
            "<span>Reposted 3 Days Ago</span>"
            "</body></html>"
        )
        out = lra.decide_from_http_html(job_url=LINKEDIN_JOB, html=html, final_url=LINKEDIN_JOB)
        self.assertNotIn("date_posted", out)
        self.assertRegex(str(out.get("date_posted_fallback") or ""), r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(out.get("date_posted_source"), "linkedin_http")

    def test_decide_from_http_html_easy_apply_only(self):
        html = (
            "<html><body>"
            '<button data-control-name="easy_apply_button">Easy Apply</button>'
            "</body></html>"
        )
        out = lra.decide_from_http_html(job_url=LINKEDIN_JOB, html=html, final_url=LINKEDIN_JOB)
        self.assertEqual(out.get("reason"), "easy_apply")
        self.assertEqual(out.get("method"), "linkedin_http")
        self.assertIsNone(out.get("url"))

    def test_decide_from_http_html_authwall(self):
        html = "<html><body>Sign in to LinkedIn Join now email password</body></html>"
        out = lra.decide_from_http_html(
            job_url=LINKEDIN_JOB,
            html=html,
            final_url="https://www.linkedin.com/authwall",
        )
        self.assertEqual(out.get("reason"), "not_logged_in")

    def test_resolve_prefers_http_over_browser(self):
        """Happy path: HTTP parse wins; session_fn / CDP never called."""

        def boom_session(*_a, **_k):
            raise AssertionError("browser session must not run when HTTP succeeds")

        html = (
            f'<a href="{GREENHOUSE}">Apply on company website</a>'
        )
        with patch.object(
            lra,
            "http_fetch_linkedin_job",
            return_value={
                "ok": True,
                "html": html,
                "final_url": LINKEDIN_JOB,
                "status_code": 200,
            },
        ):
            result = lra.resolve_linkedin_apply_url(
                LINKEDIN_JOB,
                profile_dir=Path("/tmp/fake-li-profile"),
                profile_ready=True,
                session_fn=boom_session,
                prefer_http=True,
            )
        self.assertEqual(result.get("url"), GREENHOUSE)
        self.assertEqual(result.get("method"), "linkedin_http")
        self.assertEqual(result.get("reason"), "linkedin_apply_href")

    def test_resolve_falls_back_to_session_when_http_authwall(self):
        calls = {"n": 0}

        def session_fn(job_url, profile_dir, *, click_label=None):
            calls["n"] += 1
            return {
                "current_url": LINKEDIN_JOB,
                "page_text": "Apply on company website",
                "apply_labels": ["Apply on company website"],
                "apply_href_candidates": [
                    {"label": "Apply on company website", "href": LEVER}
                ],
            }

        with patch.object(
            lra,
            "http_fetch_linkedin_job",
            return_value={
                "ok": True,
                "html": "Sign in Join now",
                "final_url": "https://www.linkedin.com/login",
                "status_code": 200,
                "authwall": True,
            },
        ):
            result = lra.resolve_linkedin_apply_url(
                LINKEDIN_JOB,
                profile_dir=Path("/tmp/fake-li-profile"),
                profile_ready=True,
                session_fn=session_fn,
                prefer_http=True,
            )
        self.assertEqual(calls["n"], 1)
        self.assertEqual(result.get("url"), LEVER)

    def test_cookie_loader_returns_li_at_without_logging_value(self):
        """load_linkedin_cookies returns dict; public helpers never print values."""
        import inspect

        src = inspect.getsource(lrp.load_linkedin_cookies)
        self.assertNotIn("print(", src)
        # Structure: name->str mapping; empty when no db
        with tempfile.TemporaryDirectory() as td:
            cookies = lrp.load_linkedin_cookies(Path(td))
            self.assertEqual(cookies, {})

    def test_cdp_fallback_skips_networkidle(self):
        import inspect

        src = inspect.getsource(lra.playwright_session_fn)
        self.assertNotIn('"networkidle"', src)
        self.assertNotIn("'networkidle'", src)
        self.assertIn("domcontentloaded", src)

    def test_resolve_concurrency_clamp(self):
        self.assertEqual(lra.clamp_resolve_concurrency(10), 3)
        self.assertEqual(lra.clamp_resolve_concurrency(0), 1)
        self.assertEqual(lra.clamp_resolve_concurrency(2), 2)
        with patch.dict(os.environ, {"LINKEDIN_RESOLVE_CONCURRENCY": "99"}):
            self.assertEqual(lra.resolve_concurrency_from_env(), 3)
        with patch.dict(os.environ, {"LINKEDIN_RESOLVE_CONCURRENCY": "1"}):
            self.assertEqual(lra.resolve_concurrency_from_env(), 1)

    def test_http_concurrency_clamp(self):
        self.assertEqual(lra.clamp_http_concurrency(100), 40)
        self.assertEqual(lra.clamp_http_concurrency(0), 1)
        self.assertEqual(lra.clamp_http_concurrency(20), 20)
        self.assertEqual(lra.clamp_http_concurrency(36), 36)
        self.assertEqual(lra.DEFAULT_HTTP_CONCURRENCY, 36)
        with patch.dict(os.environ, {"LINKEDIN_HTTP_CONCURRENCY": "50"}):
            self.assertEqual(lra.http_concurrency_from_env(), 40)
        with patch.dict(os.environ, {"LINKEDIN_HTTP_CONCURRENCY": "32"}):
            self.assertEqual(lra.http_concurrency_from_env(), 32)
        with patch.dict(os.environ, {"LINKEDIN_HTTP_CONCURRENCY": ""}):
            self.assertEqual(lra.http_concurrency_from_env(), 36)

    def test_http_many_reuses_session_no_cdp(self):
        """Parallel HTTP uses shared session; never touches Playwright."""
        calls = {"session": None, "n": 0}

        def fake_resolve_via_http(url, *, profile_dir=None, cookies=None, session=None, timeout_s=25.0):
            calls["n"] += 1
            if calls["session"] is None:
                calls["session"] = session
            else:
                self.assertIs(session, calls["session"])
            return {
                "confidence": "high",
                "url": GREENHOUSE,
                "reason": "linkedin_apply_href",
                "method": "linkedin_http",
                "needs_cdp": False,
                "score": 1.0,
            }

        with patch.object(lra, "build_linkedin_http_session", return_value=(object(), {"li_at": "x"})), \
             patch.object(lra, "resolve_via_http", side_effect=fake_resolve_via_http), \
             patch.object(lra, "playwright_session_fn") as pw:
            out = lra.resolve_linkedin_http_many(
                [("j1", LINKEDIN_JOB), ("j2", LINKEDIN_JOB)],
                concurrency=4,
            )
        self.assertEqual(len(out), 2)
        self.assertEqual(calls["n"], 2)
        pw.assert_not_called()
        self.assertTrue(all(x.get("url") == GREENHOUSE for x in out))

    def test_resolve_http_high_never_calls_playwright(self):
        with patch.object(
            lra,
            "resolve_via_http",
            return_value={
                "confidence": "high",
                "url": GREENHOUSE,
                "reason": "linkedin_apply_href",
                "method": "linkedin_http",
                "needs_cdp": False,
            },
        ), patch.object(lra, "playwright_session_fn") as pw:
            result = lra.resolve_linkedin_apply_url(
                LINKEDIN_JOB,
                profile_dir=Path("/tmp/fake-li-profile"),
                profile_ready=True,
            )
        self.assertEqual(result.get("url"), GREENHOUSE)
        pw.assert_not_called()

    def test_http_only_skips_cdp_on_needs_cdp(self):
        with patch.object(
            lra,
            "resolve_via_http",
            return_value={
                "confidence": "low",
                "url": None,
                "reason": "http_error",
                "method": "linkedin_http",
                "needs_cdp": True,
                "message": "timeout",
            },
        ), patch.object(lra, "playwright_session_fn") as pw:
            result = lra.resolve_linkedin_apply_url(
                LINKEDIN_JOB,
                profile_dir=Path("/tmp/fake-li-profile"),
                profile_ready=True,
                allow_cdp=False,
            )
        pw.assert_not_called()
        self.assertEqual(result.get("reason"), "http_error")

    def test_allow_cdp_defaults_off_unless_env(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LINKEDIN_ALLOW_CDP", None)
            self.assertFalse(lra.linkedin_allow_cdp_from_env())
        with patch.dict(os.environ, {"LINKEDIN_ALLOW_CDP": "1"}):
            self.assertTrue(lra.linkedin_allow_cdp_from_env())
        with patch.dict(os.environ, {"LINKEDIN_ALLOW_CDP": "0"}):
            self.assertFalse(lra.linkedin_allow_cdp_from_env())

    def test_default_http_only_skips_playwright_on_authwall(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LINKEDIN_ALLOW_CDP", None)
            with patch.object(
                lra,
                "resolve_via_http",
                return_value={
                    "confidence": "low",
                    "url": None,
                    "reason": "authwall",
                    "method": "linkedin_http",
                    "needs_cdp": True,
                    "message": "authwall",
                },
            ), patch.object(lra, "playwright_session_fn") as pw:
                result = lra.resolve_linkedin_apply_url(
                    LINKEDIN_JOB,
                    profile_dir=Path("/tmp/fake-li-profile"),
                    profile_ready=True,
                )
        pw.assert_not_called()
        self.assertEqual(result.get("reason"), "not_logged_in")
        self.assertIn("open_linkedin_resolve", (result.get("message") or "").lower())

    def test_http_timeout_default_is_snappy(self):
        self.assertGreaterEqual(lra.DEFAULT_HTTP_TIMEOUT_S, 8.0)
        self.assertLessEqual(lra.DEFAULT_HTTP_TIMEOUT_S, 12.0)
        import inspect

        sig = inspect.signature(lra.http_fetch_linkedin_job)
        self.assertEqual(sig.parameters["timeout_s"].default, lra.DEFAULT_HTTP_TIMEOUT_S)


class TestProfilePaths(unittest.TestCase):
    def test_default_profile_under_workspace(self):
        path = lrp.linkedin_resolve_profile_dir()
        self.assertTrue(str(path).endswith("linkedin_resolve_profile"))
        self.assertTrue(any(k in str(path) for k in ("mahi-jobhunt", "job-hunter", "jobhunt")))

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict("os.environ", {"JOB_HUNTER_LINKEDIN_RESOLVE_PROFILE": td}):
                self.assertEqual(
                    lrp.linkedin_resolve_profile_dir(),
                    Path(td).expanduser().resolve(),
                )

    def test_login_hint_mentions_cli(self):
        hint = lrp.login_required_message()
        self.assertIn("linkedin_resolve_profile.py --login", hint)
        self.assertIn("open_linkedin_resolve.sh", hint)
        self.assertIn(str(lrp.linkedin_resolve_profile_dir()), hint)

    def test_profile_in_use_message(self):
        msg = lrp.profile_in_use_message(Path("/tmp/fake-li-profile"))
        self.assertIn("without CDP", msg)
        self.assertIn("open_linkedin_resolve.sh", msg)
        self.assertIn("/tmp/fake-li-profile", msg)

    def test_cdp_port_default(self):
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("JOB_HUNTER_LINKEDIN_RESOLVE_CDP_PORT", None)
            self.assertEqual(lrp.linkedin_resolve_cdp_port(), 18801)
            self.assertEqual(lrp.linkedin_resolve_cdp_http(), "http://127.0.0.1:18801")

    def test_wait_for_profile_unlock_when_idle(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(lrp.wait_for_profile_unlock(Path(td), timeout_s=0.2))

    def test_profile_looks_initialized(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertFalse(lrp.profile_looks_initialized(root))
            (root / "Default").mkdir()
            (root / "Default" / "Preferences").write_text("{}", encoding="utf-8")
            self.assertTrue(lrp.profile_looks_initialized(root))

    def test_pgrep_uses_double_dash_separator(self):
        """macOS pgrep treats ``--user-data-dir=`` as a flag without ``--``."""
        import inspect

        src = inspect.getsource(lrp._pgrep_user_data_dir)
        # Must be ``pgrep -f -- <pattern>``, never ``pgrep -f --user-data-dir=...``.
        self.assertIn('"/usr/bin/pgrep"', src)
        self.assertRegex(src, r'\["/usr/bin/pgrep",\s*"-f",\s*"--",\s*pattern\]')
        self.assertIn("--user-data-dir=", src)

    def test_login_browser_cmd_headed_persistent_with_cdp(self):
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td)
            cmd = lrp.login_browser_cmd(Path("/fake/cft"), profile, url="https://www.linkedin.com/login")
        joined = " ".join(cmd)
        self.assertIn(f"--user-data-dir={profile}", joined)
        self.assertIn("--remote-debugging-port=", joined)
        self.assertNotIn("--headless", joined)
        self.assertNotIn("--incognito", joined)
        self.assertTrue(joined.startswith("/fake/cft"))

    def test_ensure_refuses_live_browser_without_cdp(self):
        with patch.object(lrp, "profile_has_live_browser", return_value=True):
            with patch.object(lrp, "cdp_port_open", return_value=False):
                with patch(
                    "chrome_for_testing.resolve_chrome_for_testing",
                    return_value=Path("/usr/bin/true"),
                ):
                    with patch("chrome_for_testing.is_daily_google_chrome", return_value=False):
                        out = lrp.ensure_linkedin_resolve_browser()
        self.assertFalse(out.get("ok"))
        self.assertTrue(out.get("already_open_no_cdp"))
        self.assertIn("without CDP", out.get("error") or "")

    def test_ensure_reuses_running_cdp(self):
        with patch.object(lrp, "profile_has_live_browser", return_value=True):
            with patch.object(lrp, "cdp_port_open", return_value=True):
                with patch.object(lrp, "open_url_via_cdp", return_value=True) as open_fn:
                    with patch(
                        "chrome_for_testing.resolve_chrome_for_testing",
                        return_value=Path("/usr/bin/true"),
                    ):
                        with patch(
                            "chrome_for_testing.is_daily_google_chrome", return_value=False
                        ):
                            out = lrp.ensure_linkedin_resolve_browser(
                                url="https://www.linkedin.com/login"
                            )
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("via"), "already_running")
        open_fn.assert_called_once()

    def test_launch_login_default_uses_ensure(self):
        with patch.object(
            lrp,
            "ensure_linkedin_resolve_browser",
            return_value={"ok": True, "via": "cft_direct", "li_at": False},
        ) as ensure_fn:
            out = lrp.launch_login_browser(wait=False)
        ensure_fn.assert_called_once()
        self.assertTrue(out.get("ok"))

    def test_clear_stale_singleton_skipped_while_live(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lock = root / "SingletonLock"
            lock.write_text("x", encoding="utf-8")
            with patch.object(lrp, "profile_has_live_browser", return_value=True):
                lrp._clear_stale_singleton(root)
            self.assertTrue(lock.is_file())

    def test_clear_stale_singleton_when_idle(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lock = root / "SingletonLock"
            lock.write_text("x", encoding="utf-8")
            with patch.object(lrp, "profile_has_live_browser", return_value=False):
                lrp._clear_stale_singleton(root)
            self.assertFalse(lock.is_file())

    def test_profile_ok_cli_prints_yes_or_no(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with patch.object(lrp, "profile_is_logged_in", return_value=True):
            with redirect_stdout(buf):
                rc = lrp.main(["--profile-ok"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), "yes")

        buf2 = io.StringIO()
        with patch.object(lrp, "profile_is_logged_in", return_value=False):
            with redirect_stdout(buf2):
                rc2 = lrp.main(["--profile-ok"])
        self.assertEqual(rc2, 1)
        self.assertEqual(buf2.getvalue().strip(), "no")

class TestDecideFromSnapshot(unittest.TestCase):
    def test_missing_profile(self):
        out = lra.decide_from_snapshot(
            job_url=LINKEDIN_JOB,
            current_url="",
            page_text="",
            apply_labels=[],
            profile_ready=False,
        )
        self.assertEqual(out["reason"], "not_logged_in")
        self.assertIn("--login", out.get("message") or "")

    def test_captcha(self):
        out = lra.decide_from_snapshot(
            job_url=LINKEDIN_JOB,
            current_url=LINKEDIN_JOB,
            page_text="Please complete the captcha to continue",
            apply_labels=[],
            profile_ready=True,
        )
        self.assertEqual(out["reason"], "blocked_captcha")
        self.assertTrue(out["captcha"])

    def test_signin_wall(self):
        out = lra.decide_from_snapshot(
            job_url=LINKEDIN_JOB,
            current_url="https://www.linkedin.com/login",
            page_text="Sign in",
            apply_labels=[],
            profile_ready=True,
        )
        self.assertEqual(out["reason"], "not_logged_in")

    def test_already_on_ats_after_redirect(self):
        out = lra.decide_from_snapshot(
            job_url=LINKEDIN_JOB,
            current_url=GREENHOUSE,
            page_text="Apply for ML Engineer",
            apply_labels=[],
            profile_ready=True,
        )
        self.assertEqual(out["confidence"], "high")
        self.assertEqual(out["url"], GREENHOUSE)
        self.assertEqual(out["reason"], "linkedin_external_redirect")

    def test_easy_apply_only(self):
        out = lra.decide_from_snapshot(
            job_url=LINKEDIN_JOB,
            current_url=LINKEDIN_JOB,
            page_text="Easy Apply to this role",
            apply_labels=["Easy Apply"],
            profile_ready=True,
        )
        self.assertEqual(out["reason"], "easy_apply")
        self.assertIsNone(out.get("url"))

    def test_needs_click_external(self):
        out = lra.decide_from_snapshot(
            job_url=LINKEDIN_JOB,
            current_url=LINKEDIN_JOB,
            page_text="Apply on company website",
            apply_labels=["Apply on company website"],
            profile_ready=True,
        )
        self.assertEqual(out.get("action"), "click_external_apply")
        self.assertEqual(out.get("click_label"), "Apply on company website")

    def test_workday_offsite_rejected(self):
        out = lra.decide_from_snapshot(
            job_url=LINKEDIN_JOB,
            current_url="https://acme.wd5.myworkdayjobs.com/en-US/careers/job/x",
            page_text="Workday",
            apply_labels=[],
            profile_ready=True,
        )
        self.assertEqual(out["reason"], "unfetchable_ats")
        self.assertEqual(out["confidence"], "low")


class TestResolveWithSessionFn(unittest.TestCase):
    def test_external_redirect_via_session_fn(self):
        def session_fn(job_url, profile_dir):
            return {
                "current_url": GREENHOUSE,
                "page_text": "Greenhouse application",
                "apply_labels": [],
            }

        result = lra.resolve_linkedin_apply_url(
            LINKEDIN_JOB,
            profile_dir=Path("/tmp/fake-li-profile"),
            profile_ready=True,
            session_fn=session_fn,
            prefer_http=False,
            allow_cdp=True,
        )
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["url"], GREENHOUSE)

    def test_click_then_redirect(self):
        calls = {"n": 0}

        def session_fn(job_url, profile_dir, *, click_label=None):
            calls["n"] += 1
            if click_label:
                return {
                    "current_url": GREENHOUSE,
                    "page_text": "ATS form",
                    "apply_labels": [],
                }
            return {
                "current_url": LINKEDIN_JOB,
                "page_text": "Apply on company website",
                "apply_labels": ["Apply on company website"],
            }

        result = lra.resolve_linkedin_apply_url(
            LINKEDIN_JOB,
            profile_dir=Path("/tmp/fake-li-profile"),
            profile_ready=True,
            session_fn=session_fn,
            prefer_http=False,
            allow_cdp=True,
        )
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["url"], GREENHOUSE)
        self.assertGreaterEqual(calls["n"], 2)

    def test_easy_apply_via_session(self):
        def session_fn(job_url, profile_dir, **_kw):
            return {
                "current_url": LINKEDIN_JOB,
                "page_text": "Easy Apply",
                "apply_labels": ["Easy Apply"],
            }

        result = lra.resolve_linkedin_apply_url(
            LINKEDIN_JOB,
            profile_dir=Path("/tmp/fake-li-profile"),
            profile_ready=True,
            session_fn=session_fn,
            prefer_http=False,
            allow_cdp=True,
        )
        self.assertEqual(result["reason"], "easy_apply")

    def test_missing_profile_no_session(self):
        result = lra.resolve_linkedin_apply_url(
            LINKEDIN_JOB,
            profile_dir=Path("/tmp/missing-li-profile-xyz"),
            profile_ready=False,
        )
        self.assertEqual(result["reason"], "not_logged_in")

    def test_session_resolve_defaults_headed(self):
        import inspect

        sig = inspect.signature(lra.playwright_session_fn)
        self.assertFalse(sig.parameters["headless"].default)
        sig2 = inspect.signature(lra.resolve_linkedin_apply_url)
        self.assertFalse(sig2.parameters["headless"].default)

    def test_profile_in_use_surfaces_clear_error(self):
        def session_fn(job_url, profile_dir, **_kw):
            return {
                "current_url": "",
                "page_text": "",
                "apply_labels": [],
                "error": lrp.profile_in_use_message(Path(profile_dir)),
                "reason": "profile_in_use",
            }

        result = lra.resolve_linkedin_apply_url(
            LINKEDIN_JOB,
            profile_dir=Path("/tmp/fake-li-profile"),
            profile_ready=True,
            session_fn=session_fn,
            prefer_http=False,
        )
        self.assertEqual(result["reason"], "profile_in_use")
        self.assertIn("without CDP", result.get("message") or "")

    def test_headless_true_coerced_false_on_live_path(self):
        seen = {}

        def fake_pw(job_url, profile_dir, *, click_label=None, headless=False, timeout_ms=0):
            seen["headless"] = headless
            return {
                "current_url": GREENHOUSE,
                "page_text": "ok",
                "apply_labels": [],
            }

        with patch.object(lra, "playwright_session_fn", side_effect=fake_pw):
            result = lra.resolve_linkedin_apply_url(
                LINKEDIN_JOB,
                profile_dir=Path("/tmp/fake-li-profile"),
                profile_ready=True,
                headless=True,
                prefer_http=False,
                allow_cdp=True,
            )
        self.assertFalse(seen.get("headless"))
        self.assertEqual(result.get("url"), GREENHOUSE)

    def test_playwright_session_errors_when_non_cdp_browser_holds_profile(self):
        """Non-CDP Chromium on the profile blocks attach — never kill it."""
        with patch.object(
            lra,
            "ensure_linkedin_resolve_browser",
            return_value={
                "ok": False,
                "error": lrp.profile_in_use_message(Path("/tmp/fake-li-locked")),
                "already_open_no_cdp": True,
            },
        ):
            snap = lra.playwright_session_fn(
                LINKEDIN_JOB,
                Path("/tmp/fake-li-locked"),
            )
        self.assertEqual(snap.get("reason"), "profile_in_use")
        self.assertIn("without CDP", snap.get("error") or "")

    def test_playwright_session_uses_cdp_ensure(self):
        """Live path must ensure CDP then connect — never launch_persistent_context."""
        import inspect

        src = inspect.getsource(lra.playwright_session_fn)
        self.assertIn("ensure_linkedin_resolve_browser", src)
        self.assertIn("connect_over_cdp", src)
        self.assertNotIn(".launch_persistent_context(", src)
        self.assertNotIn("browser.close(", src)
        self.assertIn("_close_resolve_tabs", src)
        self.assertIn("pages_before", src)
        self.assertIn("_open_job_page_background", src)
        self.assertNotIn("bring_to_front", src)
        self.assertNotIn("osascript", src)
        self.assertIn("steal_focus=False", src)
        bg_src = inspect.getsource(lra._open_job_page_background)
        self.assertIn("Target.createTarget", bg_src)
        self.assertIn("background", bg_src)
        self.assertNotIn("bring_to_front", bg_src)

    def test_close_resolve_tabs_closes_owned_not_preexisting(self):
        """Per-job tab (+ spawned) closed; feed/blank tabs left alone. Never browser.close."""
        feed = mock.Mock(name="feed")
        owned = mock.Mock(name="owned")
        spawned = mock.Mock(name="spawned")
        ctx = mock.Mock()
        ctx.pages = [feed, owned, spawned]
        lra._close_resolve_tabs(ctx, pages_before=[feed])
        owned.close.assert_called_once_with()
        spawned.close.assert_called_once_with()
        feed.close.assert_not_called()

    def test_close_resolve_tabs_noop_on_none_context(self):
        lra._close_resolve_tabs(None, pages_before=[])

    def test_open_job_page_background_uses_cdp_create_target(self):
        """Background Target.createTarget — no new_page / no bring_to_front."""
        owned = mock.Mock(name="owned")
        session = mock.Mock()
        session.send.return_value = {"targetId": "T1"}
        browser = mock.Mock()
        browser.contexts = [mock.Mock(pages=[])]
        browser.new_browser_cdp_session.return_value = session

        # First poll: empty; second: owned appears
        feed = mock.Mock(name="feed")
        browser.contexts[0].pages = [feed]
        calls = {"n": 0}

        def pages_side():
            calls["n"] += 1
            if calls["n"] < 2:
                return [feed]
            return [feed, owned]

        with patch.object(lra, "_pages_in_browser", side_effect=lambda _b: pages_side()):
            page = lra._open_job_page_background(
                browser, browser.contexts[0], LINKEDIN_JOB, timeout_ms=2000
            )
        self.assertIs(page, owned)
        session.send.assert_called_once()
        args, kwargs = session.send.call_args
        self.assertEqual(args[0], "Target.createTarget")
        self.assertTrue((args[1] or {}).get("background"))
        browser.contexts[0].new_page.assert_not_called()

    def test_resolve_ensure_never_steals_focus(self):
        """Dashboard resolve path must call ensure with steal_focus=False."""
        import inspect

        src = inspect.getsource(lra.playwright_session_fn)
        self.assertIn("steal_focus=False", src)
        self.assertNotIn("steal_focus=True", src)


class TestResolveProfileNoActivate(unittest.TestCase):
    def test_profile_module_has_no_osascript_activate(self):
        from pathlib import Path

        src = Path(lrp.__file__).read_text(encoding="utf-8")
        # Executable patterns only (docstrings may say "never osascript")
        self.assertNotIn('["osascript"', src)
        self.assertNotIn("'/usr/bin/osascript'", src)
        self.assertNotIn('"/usr/bin/osascript"', src)
        self.assertNotIn("set frontmost", src)
        self.assertNotIn("tell application", src)

    def test_open_url_via_cdp_defaults_background(self):
        import inspect

        sig = inspect.signature(lrp.open_url_via_cdp)
        self.assertTrue(sig.parameters["background"].default)

    def test_ensure_defaults_no_steal_focus(self):
        import inspect

        sig = inspect.signature(lrp.ensure_linkedin_resolve_browser)
        self.assertFalse(sig.parameters["steal_focus"].default)

    def test_login_browser_requests_focus(self):
        import inspect

        src = inspect.getsource(lrp.launch_login_browser)
        self.assertIn("steal_focus=True", src)


class TestResolveJobIntegration(unittest.TestCase):
    def test_resolve_job_prefers_linkedin_session_when_available(self):
        import resolve_apply_urls as rau

        job = {
            "id": "li-1",
            "company": "Acme",
            "title": "ML Engineer",
            "apply_url": LINKEDIN_JOB,
            "job_url": LINKEDIN_JOB,
            "status": "discovered",
        }
        li_hit = {
            "confidence": "high",
            "url": GREENHOUSE,
            "reason": "linkedin_external_redirect",
            "score": 1.0,
            "method": "linkedin_session",
        }

        def boom_search(_q):
            raise AssertionError("search should not run after LinkedIn session hit")

        with patch.object(rau, "try_linkedin_session_resolve", return_value=li_hit):
            result = rau.resolve_job(
                job, search_fn=boom_search, fetch_fn=lambda u: None, write=False
            )
        self.assertEqual(result.get("confidence"), "high")
        self.assertEqual(result.get("url"), GREENHOUSE)
        self.assertEqual(result.get("method"), "linkedin_session")

    def test_resolve_job_falls_back_to_search_when_session_misses(self):
        import resolve_apply_urls as rau

        job = {
            "id": "li-2",
            "company": "Acme",
            "title": "ML Engineer",
            "apply_url": LINKEDIN_JOB,
            "job_url": LINKEDIN_JOB,
            "job_description": (
                "Unique Acme ML Engineer role with tensor widgets and flux "
                "capacitors for model serving."
            ),
            "status": "discovered",
        }
        page = {
            "title": "ML Engineer",
            "company": "Acme",
            "description": (
                "Unique Acme ML Engineer role with tensor widgets and flux "
                "capacitors for model serving. "
            )
            * 4,
        }
        with patch.object(
            rau,
            "try_linkedin_session_resolve",
            return_value={"confidence": "low", "url": None, "reason": "no_external_apply"},
        ):
            result = rau.resolve_job(
                job,
                search_fn=lambda q: [GREENHOUSE],
                fetch_fn=lambda u: page,
                write=False,
            )
        self.assertIn(result.get("confidence"), ("high", "medium"))
        self.assertEqual(result.get("url"), GREENHOUSE)

    def test_not_logged_in_surfaces_when_search_also_empty(self):
        import resolve_apply_urls as rau

        job = {
            "id": "li-3",
            "company": "Acme",
            "title": "ML Engineer",
            "apply_url": LINKEDIN_JOB,
            "job_url": LINKEDIN_JOB,
            "status": "discovered",
        }
        with patch.object(
            rau,
            "try_linkedin_session_resolve",
            return_value={
                "confidence": "low",
                "url": None,
                "reason": "not_logged_in",
                "message": lrp.login_required_message(),
            },
        ):
            result = rau.resolve_job(
                job,
                search_fn=lambda q: [],
                fetch_fn=lambda u: None,
                write=False,
            )
        self.assertEqual(result.get("reason"), "not_logged_in")
        self.assertIn("--login", result.get("message") or "")

    def test_easy_apply_from_session_short_circuits(self):
        import resolve_apply_urls as rau

        job = {
            "id": "li-4",
            "company": "Acme",
            "title": "ML Engineer",
            "apply_url": LINKEDIN_JOB,
            "job_url": LINKEDIN_JOB,
            "status": "discovered",
        }

        def boom_search(_q):
            raise AssertionError("search should not run for Easy Apply")

        with patch.object(
            rau,
            "try_linkedin_session_resolve",
            return_value={
                "confidence": "low",
                "url": None,
                "reason": "easy_apply",
                "method": "linkedin_session",
            },
        ):
            result = rau.resolve_job(
                job, search_fn=boom_search, fetch_fn=lambda u: None, write=False
            )
        self.assertEqual(result.get("reason"), "easy_apply")

    def test_try_linkedin_session_resolve_defaults_headed(self):
        import inspect
        import resolve_apply_urls as rau

        sig = inspect.signature(rau.try_linkedin_session_resolve)
        self.assertFalse(sig.parameters["headless"].default)

    def test_profile_in_use_short_circuits(self):
        import resolve_apply_urls as rau

        job = {
            "id": "li-5",
            "company": "Acme",
            "title": "ML Engineer",
            "apply_url": LINKEDIN_JOB,
            "job_url": LINKEDIN_JOB,
            "status": "discovered",
        }

        def boom_search(_q):
            raise AssertionError("search should not run while profile locked")

        with patch.object(
            rau,
            "try_linkedin_session_resolve",
            return_value={
                "confidence": "low",
                "url": None,
                "reason": "profile_in_use",
                "message": lrp.profile_in_use_message(),
                "method": "linkedin_session",
            },
        ):
            result = rau.resolve_job(
                job, search_fn=boom_search, fetch_fn=lambda u: None, write=False
            )
        self.assertEqual(result.get("reason"), "profile_in_use")


if __name__ == "__main__":
    unittest.main()
