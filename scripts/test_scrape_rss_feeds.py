#!/usr/bin/env python3
"""Fixture tests for scrape_rss_feeds (no network)."""
from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_rss_feeds as srf  # noqa: E402


WWR_XML = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Stripe: Machine Learning Engineer</title>
  <link>https://weworkremotely.com/remote-jobs/stripe-ml</link>
  <description><![CDATA[<p>Build models</p>]]></description>
  <pubDate>Mon, 18 Aug 2026 12:00:00 GMT</pubDate>
  <region>USA</region>
</item>
</channel></rss>"""

AJ_XML = """<?xml version="1.0"?>
<rss xmlns:job_listing="https://authenticjobs.com">
<channel>
<item>
  <title>Data Scientist</title>
  <link>https://authenticjobs.com/job/1/openai-data-scientist/</link>
  <pubDate>Mon, 18 Aug 2026 12:00:00 GMT</pubDate>
  <description><![CDATA[About the team]]></description>
  <job_listing:company>OpenAI</job_listing:company>
  <job_listing:location>San Francisco</job_listing:location>
  <job_listing:job_type>Full-time</job_listing:job_type>
</item>
</channel></rss>"""


class RssFeedTests(unittest.TestCase):
    def test_parse_weworkremotely(self):
        root = ET.fromstring(WWR_XML)
        jobs = srf.parse_weworkremotely(root)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Stripe")
        self.assertEqual(jobs[0]["site"], "rss_feeds")

    def test_parse_authenticjobs(self):
        root = ET.fromstring(AJ_XML)
        jobs = srf.parse_authenticjobs(root)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "OpenAI")

    def test_scrape_fixture(self):
        def fake_fetch(url: str):
            if "weworkremotely" in url:
                return ET.fromstring(WWR_XML)
            if "authenticjobs" in url:
                return ET.fromstring(AJ_XML)
            return ET.fromstring("<rss><channel></channel></rss>")

        with mock.patch.object(srf, "fetch_rss", side_effect=fake_fetch), \
             mock.patch.object(srf, "log") as log:
            jobs = srf.scrape()
        self.assertEqual(len(jobs), 2)
        total_msgs = [c.args[0] for c in log.call_args_list]
        self.assertTrue(any("rss-feeds/all" in m for m in total_msgs))

    def test_feeds_include_wwr_devops_and_jobspresso(self):
        urls = [u for _pid, u, _label in srf.FEEDS]
        labels = [label for _pid, _u, label in srf.FEEDS]
        self.assertTrue(any("remote-devops-sysadmin-jobs.rss" in u for u in urls))
        self.assertTrue(any("remote-back-end-programming-jobs.rss" in u for u in urls))
        self.assertTrue(any("jobspresso.co/feed" in u for u in urls))
        self.assertTrue(any("data+engineer" in u for u in urls))
        self.assertIn("weworkremotely-devops", labels)
        self.assertFalse(any("sales" in u or "marketing" in u for u in urls))


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
