#!/usr/bin/env python3
"""Company recovery for sources that ship listings without one.

dedup_listings drops every row with a blank company (`no_company`), so Hirist's
and Cutshort's sitemaps, NoDesk, Landing.jobs and We Work Remotely contributed
~2,100 scraped rows and zero jobs. The company is almost always already in the
URL slug or the title.

Precision matters more than recall here: a wrong employer is worse than none,
because the user tailors a resume to it. Every heuristic below has a matching
negative test.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import derive_company as dc  # noqa: E402


class WeWorkRemotelyTests(unittest.TestCase):
    def test_splits_company_colon_role(self):
        self.assertEqual(
            dc.from_weworkremotely("Gusto, Inc.: Staff Software Engineer"),
            ("Gusto, Inc.", "Staff Software Engineer"))

    def test_leaves_a_role_only_title_alone(self):
        self.assertEqual(
            dc.from_weworkremotely("Senior Engineer: Backend"),
            ("", "Senior Engineer: Backend"))

    def test_no_colon_is_not_a_company(self):
        self.assertEqual(dc.from_weworkremotely("Data Engineer"),
                         ("", "Data Engineer"))


class LandingJobsTests(unittest.TestCase):
    def test_company_comes_from_the_at_path_segment(self):
        # An all-lowercase slug cannot reveal an initialism, so "ki" becomes
        # "Ki". The older rule that produced "KI" also produced "PVT LTD".
        self.assertEqual(
            dc.from_landing_jobs("https://landing.jobs/at/ki-performance/senior-ai-engineer"),
            "Ki Performance")

    def test_existing_casing_in_a_slug_is_preserved(self):
        self.assertEqual(
            dc.company_from_role_slug(
                "Senior-Engineer-NeoGenCode-Technologies-EMFogiZR")[0],
            "NeoGenCode Technologies")

    def test_other_paths_yield_nothing(self):
        self.assertEqual(dc.from_landing_jobs("https://landing.jobs/jobs/123"), "")


class SlugMinusTitleTests(unittest.TestCase):
    def test_nodesk_company_prefixes_the_slug(self):
        self.assertEqual(
            dc.from_slug_minus_title(
                "https://nodesk.co/remote-jobs/mural-senior-software-engineer-canvas-core/",
                "Senior Software Engineer, Canvas Core", company_first=True),
            "Mural")

    def test_category_page_has_no_company(self):
        self.assertEqual(
            dc.from_slug_minus_title("https://nodesk.co/remote-jobs/ai/",
                                     "AI Jobs", company_first=True), "")


class OpaqueIdTests(unittest.TestCase):
    def test_cutshort_ids_are_detected_with_and_without_digits(self):
        for token in ("Rqw1mekJ", "koBBDTQ1", "EMFogiZR", "TAtHJFje"):
            self.assertTrue(dc._is_opaque_id(token), token)

    def test_company_words_are_not_mistaken_for_ids(self):
        for token in ("Sprinto", "Egnyte", "Technologies", "Infosys", "Wipro"):
            self.assertFalse(dc._is_opaque_id(token), token)


class RoleSlugTests(unittest.TestCase):
    def test_company_is_the_trailing_non_role_run(self):
        self.assertEqual(
            dc.company_from_role_slug("Senior-Full-stack-Engineer-Sprinto-Rqw1mekJ"),
            ("Sprinto", "Senior Full stack Engineer"))

    def test_cities_between_role_and_company_are_dropped(self):
        company, role = dc.company_from_role_slug(
            "Technical-Lead-Gurugram-Delhi-Noida-Ghaziabad-Faridabad-Procol-HCCin7Ti")
        self.assertEqual(company, "Procol")
        self.assertEqual(role, "Technical Lead")

    def test_multiword_company_with_suffix_survives(self):
        company, _role = dc.company_from_role_slug(
            "Senior-Software-Architect-Bengaluru-NeoGenCode-Technologies-Pvt-Ltd-EMFogiZR")
        self.assertEqual(company, "NeoGenCode Technologies Pvt Ltd")

    def test_all_role_words_yields_no_company(self):
        self.assertEqual(
            dc.company_from_role_slug("Software-Engineer-XyZ123ab")[0], "")


class HiristTitleTests(unittest.TestCase):
    def test_company_dash_role(self):
        self.assertEqual(
            dc.from_hirist_title("Tech Mahindra - Senior Business Analyst - Banking"),
            ("Tech Mahindra", "Senior Business Analyst - Banking"))

    def test_role_dash_skill_is_not_a_company(self):
        self.assertEqual(dc.from_hirist_title("Data Scientist - Python"),
                         ("", "Data Scientist - Python"))


class CorporateSuffixTests(unittest.TestCase):
    """Hirist's sitemap flattens "Company - Role" into one slug."""

    def test_suffix_in_the_leading_words_marks_the_company(self):
        self.assertEqual(
            dc.company_from_corporate_suffix("Vunet Systems Golang Developer Microservices"),
            ("Vunet Systems", "Golang Developer Microservices"))

    def test_seniority_lead_is_never_a_company(self):
        for title in ("Senior Mme Software Developer Lte Epc Core",
                      "Lead Data Scientist Analytics Platform",
                      "Principal Software Engineer Systems"):
            self.assertEqual(dc.company_from_corporate_suffix(title)[0], "", title)

    def test_seniority_after_the_name_stops_the_scan(self):
        # "Publicis Sapient" is the company; "Senior Software Engineer" is not.
        self.assertEqual(
            dc.company_from_corporate_suffix(
                "Publicis Sapient Senior Software Engineer Java")[0], "")

    def test_plain_role_titles_yield_nothing(self):
        for title in ("Java Full Stack Developer", "Azure Integration Architect",
                      "Software Engineer Rust"):
            self.assertEqual(dc.company_from_corporate_suffix(title)[0], "", title)


class DispatchTests(unittest.TestCase):
    def test_derive_routes_each_site(self):
        cases = [
            ("weworkremotely", "https://x/y", "Lattice: Engineering Manager, AI", "Lattice"),
            ("landing_jobs", "https://landing.jobs/at/inscale/x", "Dev", "Inscale"),
            ("cutshort", "https://cutshort.io/job/Sr-Backend-Engineer-Egnyte-koBBDTQ1",
             "sr backend engineer", "Egnyte"),
            ("hirist", "https://www.hirist.tech/j/1", "IndiaMart - AVP Product", "IndiaMart"),
        ]
        for site, url, title, expected in cases:
            self.assertEqual(dc.derive(site, url=url, title=title)[0], expected, site)

    def test_unknown_site_is_a_safe_noop(self):
        self.assertEqual(dc.derive("indeed", url="https://x", title="Engineer"),
                         ("", "Engineer"))


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
