#!/usr/bin/env python3
"""Unit tests for scrape_builtin consecutive-empty early-stop (mocked HTTP)."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_builtin as sb  # noqa: E402


def _html_with_jobs(*paths: str) -> str:
    if not paths:
        # Must be truthy: fetch_html returning ""/None means fetch failure.
        return "<html><body>no jobs</body></html>"
    return "\n".join(f'<a href="{p}">job</a>' for p in paths)


def _page_from_url(url: str) -> int:
    """Built In pages use ?page=N; page 1 omits the param."""
    qs = parse_qs(urlparse(url).query)
    return int(qs.get("page", ["1"])[0])


class CollectJobUrlsEarlyStopTests(unittest.TestCase):
    def test_days_since_updated_window(self):
        self.assertEqual(sb.DEFAULT_DAYS_SINCE_UPDATED, 7)
        self.assertIn(sb.DAYS_SINCE_UPDATED, sb.SUPPORTED_DAYS_SINCE_UPDATED)

    def test_pagination_uses_query_param_not_path_segment(self):
        seen: list[str] = []

        def fake_fetch(url: str) -> str | None:
            seen.append(url)
            return _html_with_jobs("/job/a/1") if _page_from_url(url) == 1 else _html_with_jobs()

        with (
            mock.patch.object(sb, "SEARCH_TERMS", ["data scientist"]),
            mock.patch.object(sb, "fetch_html", side_effect=fake_fetch),
            mock.patch.object(sb, "SEARCH_PAGE_DELAY_S", 0),
            mock.patch.object(sb, "log"),
        ):
            sb.collect_job_urls(max_pages_per_term=5)

        # Page 2+ must use ?page=N; a /N path segment re-serves page 1.
        self.assertTrue(all(not u.split("?", 1)[0].rstrip("/").endswith("/2") for u in seen))
        page2 = [u for u in seen if _page_from_url(u) == 2]
        self.assertTrue(page2)
        self.assertTrue(all("&page=2" in u for u in page2))
        self.assertTrue(all("country=USA" in u for u in seen))

    def test_early_stop_after_three_consecutive_empty_pages(self):
        # page1: new URLs; pages 2-4: only duplicates / empty → stop before page 5
        pages = {
            1: _html_with_jobs("/job/a/1", "/job/b/2"),
            2: _html_with_jobs("/job/a/1"),  # already seen
            3: _html_with_jobs(),  # zero listings
            4: _html_with_jobs("/job/b/2"),  # already seen → 3rd consecutive empty
            5: _html_with_jobs("/job/c/3"),  # must not be fetched
        }
        fetched_pages: list[int] = []

        def fake_fetch(url: str) -> str | None:
            page = _page_from_url(url)
            fetched_pages.append(page)
            self.assertIn("daysSinceUpdated=", url)
            self.assertIn("country=USA", url)
            return pages.get(page, _html_with_jobs())

        with (
            mock.patch.object(sb, "SEARCH_TERMS", ["data scientist"]),
            mock.patch.object(sb, "fetch_html", side_effect=fake_fetch),
            mock.patch.object(sb, "SEARCH_PAGE_DELAY_S", 0),
            mock.patch.object(sb, "log"),
        ):
            urls = sb.collect_job_urls(max_pages_per_term=15)

        self.assertEqual(fetched_pages, [1, 2, 3, 4])
        self.assertEqual(
            set(urls),
            {f"{sb.BASE}/job/a/1", f"{sb.BASE}/job/b/2"},
        )

    def test_new_urls_reset_consecutive_empty_counter(self):
        pages = {
            1: _html_with_jobs("/job/a/1"),
            2: _html_with_jobs("/job/a/1"),  # empty of new
            3: _html_with_jobs("/job/b/2"),  # new → reset streak
            4: _html_with_jobs(),
            5: _html_with_jobs("/job/b/2"),
            6: _html_with_jobs(),  # 3rd consecutive empty after page 3's reset
            7: _html_with_jobs("/job/c/3"),  # must not be fetched
        }
        fetched_pages: list[int] = []

        def fake_fetch(url: str) -> str | None:
            page = _page_from_url(url)
            fetched_pages.append(page)
            return pages.get(page, _html_with_jobs())

        with (
            mock.patch.object(sb, "SEARCH_TERMS", ["ai engineer"]),
            mock.patch.object(sb, "fetch_html", side_effect=fake_fetch),
            mock.patch.object(sb, "SEARCH_PAGE_DELAY_S", 0),
            mock.patch.object(sb, "log"),
        ):
            urls = sb.collect_job_urls(max_pages_per_term=15)

        self.assertEqual(fetched_pages, [1, 2, 3, 4, 5, 6])
        self.assertEqual(
            set(urls),
            {f"{sb.BASE}/job/a/1", f"{sb.BASE}/job/b/2"},
        )

    def test_fetch_failure_aborts_term_immediately(self):
        call_count = {"n": 0}

        def fake_fetch(url: str) -> str | None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _html_with_jobs("/job/a/1")
            return None  # fail on page 2

        with (
            mock.patch.object(sb, "SEARCH_TERMS", ["data engineer"]),
            mock.patch.object(sb, "fetch_html", side_effect=fake_fetch),
            mock.patch.object(sb, "SEARCH_PAGE_DELAY_S", 0),
            mock.patch.object(sb, "log"),
        ):
            urls = sb.collect_job_urls(max_pages_per_term=15)

        self.assertEqual(call_count["n"], 2)
        self.assertEqual(set(urls), {f"{sb.BASE}/job/a/1"})


class ParseJobPageDateTests(unittest.TestCase):
    def _page(self, *, date_posted_snippet: str) -> str:
        job = (
            '{"job":{"companyName":"Acme","title":"Data Engineer",'
            '"howToApply":"https://boards.greenhouse.io/acme/jobs/1",'
            '"isEasyApply":false}}'
        )
        return (
            "<html><head>"
            '<meta name="description" content="Acme is hiring for a Data Engineer '
            'in Austin, TX. Find more details here.">'
            f"{date_posted_snippet}"
            "</head><body>"
            f"<script>Builtin.jobPostInit({job})</script>"
            '<div class="bg-midnight">The Role</div>'
            "<div>Build pipelines. What you'll do: things.</div>"
            "</body></html>"
        )

    def test_extracts_iso_date_posted(self):
        html = self._page(
            date_posted_snippet='<script type="application/ld+json">'
            '{"@type":"JobPosting","datePosted":"2026-08-04"}</script>'
        )
        with mock.patch.object(sb, "fetch_html", return_value=html):
            job = sb.parse_job_page("https://builtin.com/job/x/1", "data engineer")
        self.assertIsNotNone(job)
        self.assertEqual(job["date_posted"], "2026-08-04")

    def test_normalizes_iso_datetime(self):
        html = self._page(
            date_posted_snippet='{"datePosted":"2026-08-04T09:00:00Z"}'
        )
        with mock.patch.object(sb, "fetch_html", return_value=html):
            job = sb.parse_job_page("https://builtin.com/job/x/1", "data engineer")
        self.assertEqual(job["date_posted"], "2026-08-04")

    def test_none_when_absent(self):
        html = self._page(date_posted_snippet="")
        with mock.patch.object(sb, "fetch_html", return_value=html):
            job = sb.parse_job_page("https://builtin.com/job/x/1", "data engineer")
        self.assertIsNone(job["date_posted"])
        self.assertIsNone(job["date_posted_fallback"])

    def test_relative_string_fills_fallback_only(self):
        """"Posted 2 Days Ago" is day-granular chrome - approximate, not exact."""
        html = self._page(date_posted_snippet='<span>Posted 2 Days Ago</span>')
        with mock.patch.object(sb, "fetch_html", return_value=html):
            job = sb.parse_job_page("https://builtin.com/job/x/1", "data engineer")
        expected = (
            datetime.now(timezone.utc).date() - timedelta(days=2)
        ).isoformat()
        self.assertIsNone(job["date_posted"])
        self.assertEqual(job["date_posted_fallback"], expected)

    def test_exact_date_wins_over_relative_string(self):
        html = self._page(
            date_posted_snippet='{"datePosted":"2026-08-04"}<span>Posted 2 Days Ago</span>'
        )
        with mock.patch.object(sb, "fetch_html", return_value=html):
            job = sb.parse_job_page("https://builtin.com/job/x/1", "data engineer")
        self.assertEqual(job["date_posted"], "2026-08-04")
        self.assertIsNone(job["date_posted_fallback"])


class ExtractDatePostedTests(unittest.TestCase):
    def _days_ago(self, n: int) -> str:
        return (datetime.now(timezone.utc).date() - timedelta(days=n)).isoformat()

    def test_relative_variants(self):
        cases = {
            "Posted Today": 0,
            "Posted Yesterday": 1,
            "Posted 5 Hours Ago": 0,
            "Posted 3 Days Ago": 3,
            "Reposted 2 Weeks Ago": 14,
            "Posted 30+ Days Ago": 30,
        }
        for text, days in cases.items():
            with self.subTest(text=text):
                exact, approx = sb.extract_date_posted(f"<span>{text}</span>")
                self.assertIsNone(exact)
                self.assertEqual(approx, self._days_ago(days))

    def test_no_date_anywhere(self):
        self.assertEqual(sb.extract_date_posted("<html>nothing</html>"), (None, None))


if __name__ == "__main__":
    unittest.main()
