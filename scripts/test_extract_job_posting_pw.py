#!/usr/bin/env python3
"""Tests for extract_job_posting Playwright tier (mocked)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_job_posting as ejp  # noqa: E402


def test_playwright_tier_after_thin_http() -> None:
    thin = "<html><body><nav>Home Careers</nav></body></html>"
    rich = (
        "<html><head><title>ML Engineer at Acme</title></head><body>"
        + ("<p>We are hiring an ML engineer with deep learning experience. " * 30)
        + "</p></body></html>"
    )
    with patch.object(ejp, "fetch_html", return_value=thin):
        with patch.object(ejp, "KNOWN_ATS_TRIERS", []):
            with patch(
                "pw_fetch_html.fetch_html_playwright", return_value=rich
            ) as pw:
                result = ejp.extract(
                    "https://careers.example.com/jobs/ml-1",
                    allow_playwright=True,
                )
    assert result is not None
    assert result.get("description")
    assert len(result["description"]) >= ejp.MIN_DESCRIPTION_CHARS
    pw.assert_called_once()


def test_unreachable_skips_playwright() -> None:
    with patch("pw_fetch_html.fetch_html_playwright") as pw:
        result = ejp.extract("https://company.myworkdayjobs.com/en-US/job/1")
    assert result is None
    pw.assert_not_called()


def test_jazzhr_html_job_description() -> None:
    html = """
    <html><head><title>Senior Data Engineer - Acme</title>
    <script type="application/ld+json">{"@type":"Organization","name":"Acme"}</script>
    </head><body>
    <div id="job-description">
      Architect data models across SAP Core and PostgreSQL for a new platform.
      """ + ("Experience with ETL pipelines and warehouse design. " * 20) + """
    </div></body></html>
    """
    with patch.object(ejp, "fetch_html", return_value=html):
        result = ejp.extract(
            "https://acme.applytojob.com/apply/abc/Senior-Data-Engineer",
            allow_playwright=False,
        )
    assert result is not None
    assert "Architect data models" in (result.get("description") or "")


def test_posting_url_strips_ats_apply_suffixes() -> None:
    assert ejp.posting_url(
        "https://jobs.lever.co/nextgenfed/335cfd8f-003b-4051-ba96-bce990df5e80/apply"
    ) == "https://jobs.lever.co/nextgenfed/335cfd8f-003b-4051-ba96-bce990df5e80"
    assert ejp.posting_url(
        "https://boards.greenhouse.io/acme/jobs/12345/apply?gh_jid=12345"
    ) == "https://boards.greenhouse.io/acme/jobs/12345"
    assert ejp.posting_url(
        "https://jobs.ashbyhq.com/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/application"
    ) == "https://jobs.ashbyhq.com/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    # JazzHR job pages use /apply/{id}/title — that is the posting, not a suffix.
    jazz = "https://acme.applytojob.com/apply/abc/Senior-Data-Engineer"
    assert ejp.posting_url(jazz) == jazz


def test_try_lever_apply_url_uses_postings_api_lists() -> None:
    apply = (
        "https://jobs.lever.co/nextgenfed/335cfd8f-003b-4051-ba96-bce990df5e80/apply"
    )
    intro = (
        "NextGen is seeking an Application/Agentic AI Engineer to support "
        "mission objectives using ReadiChat. Translate mission requirements "
        "into scalable AI-enabled solutions."
    )
    api = {
        "text": "Application/Agentic AI Engineer",
        "categories": {"location": "Remote"},
        "descriptionPlain": intro,
        "lists": [
            {
                "text": "Position Requirements",
                "content": "<ul><li>" + ("Python and agent orchestration. " * 20) + "</li></ul>",
            }
        ],
    }
    apply_html = "<html><body><p>" + intro + "</p></body></html>"

    def fake_json(url, *args, **kwargs):
        assert "api.lever.co/v0/postings/nextgenfed/335cfd8f-003b-4051-ba96-bce990df5e80" in url
        return api

    with patch.object(ejp, "fetch_json", side_effect=fake_json):
        with patch.object(ejp, "fetch_html", return_value=apply_html) as html:
            result = ejp.extract(apply, allow_playwright=False)
    assert result is not None
    desc = result.get("description") or ""
    assert "Position Requirements" in desc
    assert "Python and agent orchestration" in desc
    assert len(desc) > len(intro)
    html.assert_not_called()


def test_lever_apply_fetches_posting_html_after_api_miss() -> None:
    apply = "https://jobs.lever.co/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/apply"
    posting = "https://jobs.lever.co/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    rich = (
        "<html><head><title>ML Engineer</title></head><body>"
        '<div id="job-description"><h2>Requirements</h2><p>'
        + ("Build and ship models with Python and SQL. " * 40)
        + "</p></div></body></html>"
    )
    fetched: list[str] = []

    def fake_html(url: str):
        fetched.append(url)
        if url == posting:
            return rich
        return "<html><body>Apply now</body></html>"

    with patch.object(ejp, "try_lever", return_value=None), patch.object(
        ejp, "KNOWN_ATS_TRIERS", [ejp.try_lever]
    ):
        with patch.object(ejp, "fetch_html", side_effect=fake_html):
            with patch("pw_fetch_html.fetch_html_playwright") as pw:
                result = ejp.extract(apply, allow_playwright=True)
    assert result is not None
    assert "Requirements" in (result.get("description") or "")
    assert posting in fetched
    pw.assert_not_called()


def test_lever_skips_playwright_on_apply_url_after_api_and_html_miss() -> None:
    apply = "https://jobs.lever.co/jobgether/0daa94cf-2173-427e-a18f-4bbea57e7c7b/apply"
    posting = "https://jobs.lever.co/jobgether/0daa94cf-2173-427e-a18f-4bbea57e7c7b"
    called: list[str] = []

    def fake_pw(url: str):
        called.append(url)
        return None

    with patch.object(ejp, "try_lever", return_value=None), patch.object(
        ejp, "KNOWN_ATS_TRIERS", [ejp.try_lever]
    ):
        with patch.object(ejp, "fetch_html", return_value=None):
            with patch("pw_fetch_html.fetch_html_playwright", side_effect=fake_pw):
                result = ejp.extract(apply, allow_playwright=True)
    assert result is None
    assert apply not in called
    assert posting in called


def test_allow_playwright_false() -> None:
    with patch.object(ejp, "fetch_html", return_value=None):
        with patch.object(ejp, "KNOWN_ATS_TRIERS", []):
            with patch("pw_fetch_html.fetch_html_playwright") as pw:
                result = ejp.extract(
                    "https://careers.example.com/jobs/x",
                    allow_playwright=False,
                )
    assert result is None
    pw.assert_not_called()


def test_jazzhr_prefers_longer_html_over_short_ldjson() -> None:
    html = """
    <html><head><title>Senior Data Engineer - Acme</title>
    <script type="application/ld+json">
    {"@type":"JobPosting","title":"Senior Data Engineer",
     "description":"<p>Acme is hiring in Austin.</p>"}
    </script>
    </head><body>
    <div id="job-description">
      <p>Acme is hiring in Austin.</p>
      <h2>Responsibilities</h2>
      <ul><li>Architect data models across SAP Core and PostgreSQL.</li></ul>
      <h2>Requirements</h2>
      <p>""" + ("Experience with ETL pipelines and warehouse design. " * 25) + """</p>
    </div></body></html>
    """
    with patch.object(ejp, "fetch_html", return_value=html):
        result = ejp.extract(
            "https://acme.applytojob.com/apply/abc/Senior-Data-Engineer",
            allow_playwright=False,
        )
    assert result is not None
    desc = result.get("description") or ""
    assert "Responsibilities" in desc
    assert "Architect data models" in desc
    assert len(desc) > 400


if __name__ == "__main__":
    test_playwright_tier_after_thin_http()
    test_unreachable_skips_playwright()
    test_jazzhr_html_job_description()
    test_jazzhr_prefers_longer_html_over_short_ldjson()
    test_posting_url_strips_ats_apply_suffixes()
    test_try_lever_apply_url_uses_postings_api_lists()
    test_lever_apply_fetches_posting_html_after_api_miss()
    test_lever_skips_playwright_on_apply_url_after_api_and_html_miss()
    test_allow_playwright_false()
    print("ok")
