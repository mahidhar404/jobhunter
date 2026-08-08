#!/usr/bin/env python3
"""Tests for scripts/discovery_filters.py."""
from discovery_filters import (
    auto_delete_reason,
    detect_work_mode,
    detect_work_mode_fallback,
    extract_inr_salary,
    extract_min_required_yoe,
    extract_min_required_yoe_fallback,
    extract_salary,
    extract_salary_fallback,
    extract_salary_with_source,
    is_clearly_non_us_location,
    is_excluded_title,
    is_india_location,
    is_intel_agency_employer,
    location_matches_regions,
    normalize_regions,
    region_for_location,
    requires_excessive_experience,
    requires_security_clearance,
    requires_us_citizen_or_greencard,
    should_keep_listing,
)


def test_senior_allowed():
    assert not is_excluded_title("Senior Machine Learning Engineer")
    assert not is_excluded_title("Senior Data Scientist")
    assert not is_excluded_title("Junior Data Engineer")
    assert not is_excluded_title("Mid-level Analytics Engineer")
    assert not is_excluded_title("Associate Data Scientist")
    assert not is_excluded_title("Entry Level Data Analyst")


def test_above_senior_excluded():
    assert is_excluded_title("Staff AI Engineer, GenAI")
    assert is_excluded_title("Staff Data Scientist")
    assert is_excluded_title("Principal Engineer - AI/ML")
    assert is_excluded_title("Senior Staff Machine Learning Engineer")
    assert is_excluded_title("Director Data Engineering")
    assert is_excluded_title("Head of Data Science")
    assert is_excluded_title("Distinguished AI Engineer (Office of the CTO)")
    assert is_excluded_title("VP of Machine Learning")
    assert is_excluded_title("Vice President, Data Science")
    assert is_excluded_title("Chief Data Officer")
    assert is_excluded_title("Engineering Manager, ML")
    assert is_excluded_title("Mgr, Data Platform Operations")
    assert is_excluded_title("Lead Data Scientist")
    assert is_excluded_title("AI Research Fellow")
    assert is_excluded_title("Supervisor, Fulfillment Center")
    assert is_excluded_title("SVP, Engineering")
    assert is_excluded_title("Software Architect")
    assert is_excluded_title("AI Data Architect")
    assert is_excluded_title("Solutions Architect")
    assert is_excluded_title("Senior Architect")
    assert is_excluded_title("AI Architect")
    assert not should_keep_listing(title="Senior Software Architect", location="Remote, US")


def test_staff_not_staffing():
    assert not is_excluded_title("Staffing Coordinator for Data Team")  # staffing ≠ staff
    # "lead" must not match "leadership" as a bare token inside another word
    assert not is_excluded_title("Leadership Development Data Analyst")


def test_us_location_keep():
    assert not is_clearly_non_us_location("")
    assert not is_clearly_non_us_location(None)
    assert not is_clearly_non_us_location("Remote")
    assert not is_clearly_non_us_location("San Francisco, CA")
    assert not is_clearly_non_us_location("Remote, US")
    assert not is_clearly_non_us_location("United States")
    assert not is_clearly_non_us_location("USA")
    assert not is_clearly_non_us_location("US")
    assert not is_clearly_non_us_location("New York, NY, USA")
    assert not is_clearly_non_us_location("Bay Area")
    assert not is_clearly_non_us_location("NYC Office")
    # Multi-location including US
    assert not is_clearly_non_us_location("San Francisco / London")
    assert not is_clearly_non_us_location("India; United States")
    assert not is_clearly_non_us_location("Remote US & Canada")
    # Dotted U.S. must count as a US signal (trailing period breaks \\b)
    assert not is_clearly_non_us_location("U.S.")
    assert not is_clearly_non_us_location("U.S. / Canada")
    assert not is_clearly_non_us_location("Remote, U.S.")
    assert not is_clearly_non_us_location("U.S.A.")
    # Comma + state abbrev keeps US cities that share non-US hub names
    assert not is_clearly_non_us_location("Rome, NY")
    assert not is_clearly_non_us_location("Paris, TX")


def test_iso2_country_tail_keeps_us():
    """State abbreviations that double as ISO country codes stay US."""
    assert not is_clearly_non_us_location("Indianapolis, IN")
    assert not is_clearly_non_us_location("Dublin, CA")  # Dublin, California
    assert not is_clearly_non_us_location("Ontario, CA")  # Ontario, California
    assert not is_clearly_non_us_location("Wilmington, DE")
    assert not is_clearly_non_us_location("Baton Rouge, LA")
    assert not is_clearly_non_us_location("Nashville, TN")
    assert not is_clearly_non_us_location("Boise, ID")
    assert not is_clearly_non_us_location("Springfield, MA")
    # ATS tails that name the US explicitly
    assert not is_clearly_non_us_location("Milpitas, CA, us")
    assert not is_clearly_non_us_location("Irvine, CA, us")
    assert not is_clearly_non_us_location("Seattle, WA, US")
    assert not is_clearly_non_us_location("Milpitas, CA, USA")
    # A US option in the same string still wins over a foreign tail
    assert not is_clearly_non_us_location("San Francisco, CA / Bengaluru, KA, in")


def test_iso2_country_tail_drops_non_us():
    """SmartRecruiters-style "City, Region, cc" tails (the Sandisk bug)."""
    assert is_clearly_non_us_location("Bengaluru, KA, in")
    assert is_clearly_non_us_location("Hyderabad, TG, in")
    assert is_clearly_non_us_location("Chennai, TN, in")  # TN = Tamil Nadu
    assert is_clearly_non_us_location("Pune, MH, in")
    assert is_clearly_non_us_location("Noida, UP, in")
    assert is_clearly_non_us_location("Batu Kawan, Penang, my")
    assert is_clearly_non_us_location("Toronto, ON, ca")
    assert is_clearly_non_us_location("Dublin, IE")
    assert is_clearly_non_us_location("Munich, de")
    assert is_clearly_non_us_location("Kuala Lumpur, my")
    # Indian state names on their own
    assert is_clearly_non_us_location("Karnataka, India")
    assert is_clearly_non_us_location("Telangana")


def test_sandisk_listing_auto_deletes():
    """Regression: the exact listing that stayed visible in the dashboard."""
    assert auto_delete_reason(
        title="Senior Engineer, Agentic AI & MLOps Engineering (5-8 years)",
        company="Sandisk",
        location="Bengaluru, KA, in",
        url="https://jobs.smartrecruiters.com/Sandisk/744000000000000",
    ) == "non_us_location"
    assert not should_keep_listing(
        title="Senior Engineer, Agentic AI & MLOps Engineering (5-8 years)",
        company="Sandisk",
        location="Bengaluru, KA, in",
    )
    # Same posting in a US office must survive the same rules
    assert should_keep_listing(
        title="Senior Engineer, Agentic AI & MLOps Engineering (5-8 years)",
        company="Sandisk",
        location="Milpitas, CA, us",
    )


def test_non_us_location_drop():
    assert is_clearly_non_us_location("India")
    assert is_clearly_non_us_location("Canada")
    assert is_clearly_non_us_location("Toronto, ON")
    assert is_clearly_non_us_location("London, UK")
    assert is_clearly_non_us_location("London")  # clearly non-US tech hub
    assert is_clearly_non_us_location("Paris")
    assert is_clearly_non_us_location("Munich")
    assert is_clearly_non_us_location("Melbourne")
    assert is_clearly_non_us_location("United Kingdom")
    assert is_clearly_non_us_location("Asia")
    assert is_clearly_non_us_location("Singapore")
    assert is_clearly_non_us_location("Bangalore, Karnataka, India")
    assert is_clearly_non_us_location("Brazil")
    assert is_clearly_non_us_location("EMEA")
    assert is_clearly_non_us_location("European Union")
    assert is_clearly_non_us_location("Worldwide")
    assert is_clearly_non_us_location("Saudi Arabia")
    assert is_clearly_non_us_location("Ukraine")
    assert is_clearly_non_us_location("Ontario Remote")
    assert is_clearly_non_us_location("Ciudad de México")  # accent fold
    assert is_clearly_non_us_location("São Paulo")
    assert is_clearly_non_us_location("Czech Republic - Prague")
    assert is_clearly_non_us_location("Middle East - Doha")
    assert is_clearly_non_us_location("Goiás, BRA")


def test_clearance_and_intel_excluded():
    # NSA / agency by company (even without the word "clearance")
    assert is_intel_agency_employer("National Security Agency")
    assert is_intel_agency_employer("NSA")
    assert is_intel_agency_employer("CIA")
    assert is_intel_agency_employer("Defense Intelligence Agency")
    assert is_intel_agency_employer("NGA")
    assert requires_security_clearance(
        title="Research Scientist",
        company="National Security Agency",
        location="Fort Meade, MD",
    )
    assert not should_keep_listing(
        title="Research Scientist / Computer Systems Researcher",
        company="National Security Agency",
        location="Fort Meade, MD, US",
    )
    # IC careers portal URL
    assert is_intel_agency_employer(
        "Some Odd Label",
        url="https://apply.intelligencecareers.gov/job-description/1260810",
    )

    # Clearance requirement language
    assert requires_security_clearance(title="MLOps Engineer – CI/CD & Simulation - TS/SCI")
    assert requires_security_clearance(
        title="Data Engineer",
        description="Candidates must have Secret clearance to start.",
    )
    assert requires_security_clearance(title="AI/ML Engineer (Active TS/SCI)")
    assert requires_security_clearance(
        title="Data Scientist",
        description="TS/SCI required; polygraph may be required.",
    )
    assert requires_security_clearance(
        title="Research Scientist with Security Clearance",
    )
    assert requires_security_clearance(
        title="AI Engineer, Special Programs - Top Secret Clearance",
        company="SpaceX",
    )
    assert requires_security_clearance(
        title="Data Engineer (DoD Secret | Remote)",
    )
    assert not should_keep_listing(
        title="MLOps Engineer",
        company="Parsons",
        location="Remote, US",
        description="This role requires an active TS/SCI clearance.",
    )
    # ATS "Clearance:" label + Public Trust (titles and JD)
    assert requires_security_clearance(
        title="AI/ML Engineer",
        description="Clearance: Secret with ability to get a DHS Suitability",
    )
    assert requires_security_clearance(
        title="Senior Applied AI Engineer",
        description="Clearance: Ability to obtain and maintain a Public Trust",
    )
    assert requires_security_clearance(title="Senior Data Scientist (Public Trust)")
    assert requires_security_clearance(title="AI Engineer (Public Trust)")
    assert requires_security_clearance(
        description="Must be able to obtain and maintain a Public Trust clearance.",
    )
    # Work-auth mislabeled as Clearance — keep
    assert not requires_security_clearance(
        description="Clearance: Must be able to work in the U.S. without employer sponsorship",
    )
    # Explicit no clearance required — keep (unless another positive signal)
    assert not requires_security_clearance(
        description="CLEARANCE REQUIRED FOR START: No\nTRAVEL: Yes",
    )
    assert not requires_security_clearance(
        description="Clearance required: No",
    )
    assert not requires_security_clearance(
        description="Clearance Required\n:\nNone\nWhat You Will Do",
    )
    assert not requires_security_clearance(
        description="**Clearance Required** |  None\n**What You Will Do**",
    )
    # Required: No but TYPE is Secret / truncated TYPE — still drop
    assert requires_security_clearance(
        description="CLEARANCE REQUIRED FOR START: No\nCLEARANCE TYPE: Secret",
    )
    assert requires_security_clearance(
        description="CLEARANCE REQUIRED FOR START: No\nCLEARANCE TYPE: … [full text in resumes/x/jd_full.txt]",
    )
    # Soft clearance requirements (no level named) — drop
    assert requires_security_clearance(
        description="scaling platform ops while meeting security and clearance requirements.",
    )
    # Truncated Built In clearance section — drop
    assert requires_security_clearance(
        description="Travel Required\n:\nNone\nClearance … [full text in resumes/<id>/jd_full.txt]",
    )
    # Prose "public trust" — keep
    assert not requires_security_clearance(
        description="Research that informs national policy and earns public trust.",
    )


def test_civilian_security_engineer_kept():
    # Product security at a normal tech co — no clearance words → keep
    assert not requires_security_clearance(
        title="Security Engineer",
        company="Google",
        location="Mountain View, CA",
        description="Build detection and response for consumer products.",
    )
    assert should_keep_listing(
        title="Security Engineer",
        company="Google",
        location="Mountain View, CA",
        description="Build detection and response for consumer products.",
    )
    # Staff Security Engineer still dropped by seniority rule
    assert is_excluded_title("Staff Security Engineer")
    assert not should_keep_listing(
        title="Staff Security Engineer",
        company="Stripe",
        location="Remote, US",
        description="Application security for payments products.",
    )


def test_should_keep_listing():
    assert should_keep_listing(title="Senior Data Scientist", location="Remote, US")
    assert should_keep_listing(title="Data Engineer", location="")
    assert not should_keep_listing(title="Staff Data Engineer", location="San Francisco, CA")
    assert not should_keep_listing(title="Data Scientist", location="India")
    assert not should_keep_listing(title="Data Scientist", location="London")


def test_yoe_extract_and_excessive():
    assert extract_min_required_yoe(description="3+ years of experience required") == 3
    assert extract_min_required_yoe(description="5-7 years of experience") == 5
    assert extract_min_required_yoe(description="minimum of 8 years experience") == 8
    assert extract_min_required_yoe(description="YOE: 4") == 4
    assert extract_min_required_yoe(description="founded 10 years ago") is None
    # Company tenure blurb must not override real "2+ years" requirement
    assert extract_min_required_yoe(
        description=(
            "As part of PROG Holdings, a FinTech holding company with more than "
            "20 years of experience, we're focused on people first.\n"
            "* 2\\+ years experience"
        )
    ) == 2
    assert not requires_excessive_experience(
        description=(
            "holding company with more than 20 years of experience. "
            "2+ years of experience required."
        )
    )
    # Adjective between years and experience (common ATS phrasing)
    assert extract_min_required_yoe(
        description="Experience: 4+ years of professional experience building ML"
    ) == 4
    # Escaped dash from markdown-ish scrapes
    assert extract_min_required_yoe(description="1\\-3 years of experience") == 1
    assert extract_min_required_yoe(
        description="5\\-8 years of professional AI/ML experience"
    ) == 5
    assert not requires_excessive_experience(description="6+ years of experience")
    assert not requires_excessive_experience(description="5-7 years of experience")
    assert requires_excessive_experience(description="7+ years of experience")
    assert requires_excessive_experience(description="minimum of 10 years experience")
    assert should_keep_listing(
        title="Data Scientist",
        location="Remote, US",
        description="3+ years of experience building ML systems.",
    )
    assert not should_keep_listing(
        title="Data Scientist",
        location="Remote, US",
        description="Requires 8+ years of experience in ML.",
    )


def test_yoe_fallback_display_only():
    # Hyphenated adjectives — strict \w+ misses "hands-on"; fallback catches
    hands_on = "2+ years of hands-on engineering experience"
    assert extract_min_required_yoe(description=hands_on) is None
    assert extract_min_required_yoe_fallback(description=hands_on) == 2
    # Truncated "exper" + slash in software/ML
    truncated = "7+ years of professional software/ML engineering exper"
    assert extract_min_required_yoe(description=truncated) is None
    assert extract_min_required_yoe_fallback(description=truncated) == 7
    # Cognition-style: "4+ years in a … role" — strict miss, fallback hit
    phrase = "* 4+ years in a data engineering, data science, or full-stack data role"
    assert extract_min_required_yoe(description=phrase) is None
    assert extract_min_required_yoe_fallback(description=phrase) == 4
    # Fallback must never prune even if N > 6
    assert not requires_excessive_experience(
        description="10+ years in a software engineering role"
    )
    assert extract_min_required_yoe_fallback(
        description="10+ years in a software engineering role"
    ) == 10
    # Truncated high YOE still display-only (no prune)
    assert not requires_excessive_experience(description=truncated)
    # Strict hit → fallback returns None (caller uses strict)
    assert extract_min_required_yoe_fallback(
        description="4+ years of experience in data engineering"
    ) is None
    # Tenure still ignored
    assert extract_min_required_yoe_fallback(
        description="holding company with more than 20 years in a leadership role"
    ) is None
    assert extract_min_required_yoe_fallback(
        description="holding company with more than 20 years of experience"
    ) is None
    # Extra catch-alls
    assert extract_min_required_yoe_fallback(description="5+ years' experience") == 5
    assert extract_min_required_yoe_fallback(description="experience: 3+ years") == 3
    assert extract_min_required_yoe_fallback(
        description="at least 4 years of software engineering"
    ) == 4
    # Range with truncated exper — lower bound (strict may also hit via optional OF_EXP)
    range_fb = "2-4 years of hands-on production ML systems exper"
    strict_r = extract_min_required_yoe(description=range_fb)
    if strict_r is None:
        assert extract_min_required_yoe_fallback(description=range_fb) == 2
    else:
        assert strict_r == 2
        assert extract_min_required_yoe_fallback(description=range_fb) is None


def test_work_mode_fallback():
    # Phrase that misses strict remote (\bremote\b) but hits fallback
    assert detect_work_mode(description="we are a distributed team across the US") == "unknown"
    assert detect_work_mode_fallback(description="we are a distributed team across the US") == "remote"
    assert detect_work_mode_fallback(description="flexible work arrangement available") == "hybrid"
    assert detect_work_mode_fallback(location="San Francisco, CA") == "unknown"
    # Soft remote / onsite cues (must not contain bare \bremote\b — that is strict)
    assert detect_work_mode(description="telecommute options available") == "unknown"
    assert detect_work_mode_fallback(description="telecommute options available") == "remote"
    assert detect_work_mode_fallback(description="work from anywhere") == "remote"
    assert detect_work_mode_fallback(description="this is an office-based position") == "onsite"
    assert detect_work_mode_fallback(description="HQ-based team in NYC") == "onsite"
    assert detect_work_mode_fallback(description="3-4 days a week at the office") == "hybrid"
    assert detect_work_mode(description="3-4 days a week at the office") == "unknown"
    # Strict already remote → fallback unused (returns unknown)
    assert detect_work_mode(location="Remote, US") == "remote"
    assert detect_work_mode_fallback(location="Remote, US") == "unknown"
    # Bare hybrid in title is strict (not fallback)
    assert detect_work_mode(title="Hybrid Research Engineer") == "hybrid"
    assert detect_work_mode_fallback(title="Hybrid Research Engineer") == "unknown"


def test_citizenship_and_greencard():
    assert requires_us_citizen_or_greencard(description="US citizens only")
    assert requires_us_citizen_or_greencard(description="U.S. citizenship required")
    assert requires_us_citizen_or_greencard(description="green card required")
    assert requires_us_citizen_or_greencard(
        description="Must be a permanent resident to apply."
    )
    assert not requires_us_citizen_or_greencard(
        description="Must be authorized to work in the U.S."
    )
    assert not requires_us_citizen_or_greencard(
        description="EEO: citizenship status is never considered."
    )
    assert not should_keep_listing(
        title="ML Engineer",
        location="Remote, US",
        description="U.S. citizenship required for this role.",
    )
    assert should_keep_listing(
        title="ML Engineer",
        location="Remote, US",
        description="Must be authorized to work in the U.S. without sponsorship.",
    )


def test_work_mode_detect():
    assert detect_work_mode(location="Remote, US") == "remote"
    assert detect_work_mode(title="Data Scientist (Hybrid)") == "hybrid"
    assert detect_work_mode(description="This is a fully remote role.") == "remote"
    assert detect_work_mode(description="On-site in Austin, TX; must relocate.") == "onsite"
    assert detect_work_mode(description="Hybrid — 3 days a week in the office") == "hybrid"
    assert detect_work_mode(title="Data Engineer", location="Austin, TX") == "unknown"
    # remote + onsite without hybrid → unknown (prefer undetermined)
    assert detect_work_mode(
        description="Remote or on-site available depending on team."
    ) == "unknown"


def test_salary_extract_strict():
    r = extract_salary(description="Compensation: $120,000 - $150,000 per year")
    assert r == {"min": 120000, "max": 150000, "period": "year"}
    r = extract_salary(description="Base salary $120k–$150k")
    assert r == {"min": 120000, "max": 150000, "period": "year"}
    r = extract_salary(description="Pay range 120000-150000 annually")
    assert r == {"min": 120000, "max": 150000, "period": "year"}
    r = extract_salary(description="USD 120k to 150k OTE")
    assert r == {"min": 120000, "max": 150000, "period": "year"}
    r = extract_salary(description="salary: $100k")
    assert r == {"min": 100000, "max": None, "period": "year"}
    r = extract_salary(description="The role pays $145,000.")
    assert r == {"min": 145000, "max": None, "period": "year"}
    # Hourly ignored
    assert extract_salary(description="Pay: $60/hr") is None
    assert extract_salary(description="$45 per hour plus benefits") is None
    # Funding / tenure noise
    assert extract_salary(
        description="We raised $50M in Series B funding last year."
    ) is None
    assert extract_salary(
        description="Company valuation of $1 billion; join our team."
    ) is None
    sal, src = extract_salary_with_source(
        description="Compensation: $120,000 - $150,000"
    )
    assert src == "strict" and sal["min"] == 120000


def test_salary_fallback_display_only():
    # Soft cue — strict may miss "around 120k" without $
    soft = "Competitive compensation around 120k depending on experience"
    strict = extract_salary(description=soft)
    fb = extract_salary_fallback(description=soft)
    if strict is None:
        assert fb is not None and fb["min"] == 120000
        sal, src = extract_salary_with_source(description=soft)
        assert src == "fallback"
    else:
        assert extract_salary_fallback(description=soft) is None
    # up to / starting at
    up = "Total package up to $180k for the right candidate"
    if extract_salary(description=up) is None:
        assert extract_salary_fallback(description=up) == {
            "min": 180000, "max": None, "period": "year"
        }
    bare_k = "Expect 110k-140k depending on level"
    if extract_salary(description=bare_k) is None:
        fb2 = extract_salary_fallback(description=bare_k)
        assert fb2 == {"min": 110000, "max": 140000, "period": "year"}
    # Strict hit → fallback None
    assert extract_salary_fallback(
        description="salary: $100k base"
    ) is None
    # Noise still ignored
    assert extract_salary_fallback(
        description="holding company with more than 20 years; raised $50M"
    ) is None


def test_extract_inr_salary_display_only():
    # Ranges
    assert extract_inr_salary(description="Compensation: 12-18 LPA") == {
        "min_lpa": 12.0, "max_lpa": 18.0, "display": "~₹12–18 LPA"
    }
    assert extract_inr_salary(description="₹8 to 12 lakhs per annum") == {
        "min_lpa": 8.0, "max_lpa": 12.0, "display": "~₹8–12 LPA"
    }
    assert extract_inr_salary(description="INR 10 - 15 lacs") == {
        "min_lpa": 10.0, "max_lpa": 15.0, "display": "~₹10–15 LPA"
    }
    # Single values (fractional trimmed / kept)
    assert extract_inr_salary(description="Salary: 12 LPA")["display"] == "~₹12 LPA"
    r = extract_inr_salary(description="up to ₹12.5 lakhs")
    assert r["min_lpa"] == 12.5 and r["max_lpa"] is None
    # Reversed range normalizes low→high
    assert extract_inr_salary(description="18-12 LPA")["min_lpa"] == 12.0
    # Nothing to parse / out-of-range / USD noise → None
    assert extract_inr_salary(description="Great team and mission") is None
    assert extract_inr_salary(description="$120,000 - $150,000 per year") is None
    assert extract_inr_salary(description="500 LPA") is None  # absurd, out of bounds
    # Never prunes: an India role with an LPA figure still keeps under India
    assert should_keep_listing(
        title="Data Scientist", location="Bengaluru",
        description="Pay: 40 LPA", regions=["india"],
    )


def test_is_india_location():
    # Clear India cities (both spellings)
    assert is_india_location("Bengaluru")
    assert is_india_location("Bangalore, India")
    assert is_india_location("Gurugram")
    assert is_india_location("Gurgaon, Haryana")
    assert is_india_location("Mumbai")
    assert is_india_location("Hyderabad, Telangana")
    assert is_india_location("Karnataka, India")
    assert is_india_location("Bengaluru, KA, in")
    assert is_india_location("Chennai, TN, in")
    # Remote-India patterns
    assert is_india_location("Remote - India")
    assert is_india_location("Remote, India")
    assert is_india_location("WFH India")
    assert is_india_location("Anywhere in India")
    assert is_india_location("Pan India")
    assert is_india_location("India (Remote)")
    # NOT clearly India
    assert not is_india_location("")
    assert not is_india_location(None)
    assert not is_india_location("Remote")
    assert not is_india_location("WFH")
    assert not is_india_location("San Francisco, CA")
    assert not is_india_location("Indianapolis, IN")  # Indiana, not India
    assert not is_india_location("Remote, US")
    assert not is_india_location("London")


def test_normalize_regions():
    import os
    os.environ.pop("JOBHUNTER_DISCOVERY_REGIONS", None)
    assert normalize_regions(None) == ("us",)  # env unset → default
    assert normalize_regions(["us"]) == ("us",)
    assert normalize_regions(["india"]) == ("india",)
    assert normalize_regions(["india", "us"]) == ("us", "india")  # ordered
    assert normalize_regions("us,india") == ("us", "india")
    assert normalize_regions(["bogus"]) == ()
    assert normalize_regions([]) == ()


def test_location_matches_regions():
    # US-only (default): matches today's is_clearly_non_us_location behavior
    assert location_matches_regions("San Francisco, CA", ["us"])
    assert location_matches_regions("Remote", ["us"])  # undetermined kept
    assert location_matches_regions("", ["us"])
    assert not location_matches_regions("Bengaluru, KA, in", ["us"])
    assert not location_matches_regions("Mumbai", ["us"])
    # India-only: keep India, drop US and bare-undetermined
    assert location_matches_regions("Bengaluru", ["india"])
    assert location_matches_regions("Remote - India", ["india"])
    assert location_matches_regions("Chennai, TN, in", ["india"])
    assert not location_matches_regions("San Francisco, CA", ["india"])
    assert not location_matches_regions("Remote", ["india"])  # not clearly India
    assert not location_matches_regions("Remote, US", ["india"])
    # Both enabled → union
    assert location_matches_regions("San Francisco, CA", ["us", "india"])
    assert location_matches_regions("Bengaluru", ["us", "india"])
    assert location_matches_regions("Remote", ["us", "india"])
    # Empty region set keeps nothing
    assert not location_matches_regions("San Francisco, CA", [])
    assert not location_matches_regions("Bengaluru", [])


def test_region_for_location():
    assert region_for_location("Bengaluru, KA, in") == "india"
    assert region_for_location("Mumbai") == "india"
    assert region_for_location("Remote - India") == "india"
    assert region_for_location("San Francisco, CA") == "us"
    assert region_for_location("New York, NY, USA") == "us"
    assert region_for_location("Remote") == "unknown"
    assert region_for_location("") == "unknown"
    assert region_for_location("London") == "unknown"


def test_region_aware_auto_delete():
    # India OFF (US-only default) → India dropped as today
    assert auto_delete_reason(
        title="Data Scientist", location="Bengaluru, KA, in", regions=["us"]
    ) == "non_us_location"
    assert not should_keep_listing(
        title="Data Scientist", location="Mumbai", regions=["us"]
    )
    # India ON → India kept; other filters still apply
    assert should_keep_listing(
        title="Data Scientist", location="Bengaluru, KA, in",
        regions=["us", "india"],
    )
    assert should_keep_listing(
        title="ML Engineer", location="Remote - India", regions=["india"],
    )
    # India ON but still drops leadership / clearance / excessive YOE
    assert auto_delete_reason(
        title="Director of Data Science", location="Bengaluru",
        regions=["us", "india"],
    ) == "management_track"
    # India-only drops US roles
    assert auto_delete_reason(
        title="Data Scientist", location="Austin, TX", regions=["india"],
    ) == "non_us_location"


if __name__ == "__main__":
    test_senior_allowed()
    test_above_senior_excluded()
    test_staff_not_staffing()
    test_us_location_keep()
    test_iso2_country_tail_keeps_us()
    test_iso2_country_tail_drops_non_us()
    test_sandisk_listing_auto_deletes()
    test_non_us_location_drop()
    test_clearance_and_intel_excluded()
    test_civilian_security_engineer_kept()
    test_should_keep_listing()
    test_yoe_extract_and_excessive()
    test_yoe_fallback_display_only()
    test_work_mode_fallback()
    test_citizenship_and_greencard()
    test_work_mode_detect()
    test_salary_extract_strict()
    test_salary_fallback_display_only()
    test_extract_inr_salary_display_only()
    test_is_india_location()
    test_normalize_regions()
    test_location_matches_regions()
    test_region_for_location()
    test_region_aware_auto_delete()
    print("ok")
