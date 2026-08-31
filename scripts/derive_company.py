#!/usr/bin/env python3
"""Recover the company name a scraper failed to capture.

Some sources ship listings with no company field at all — Hirist's sitemap and
Cutshort's sitemap give only a URL and a title, NoDesk and Landing.jobs put the
company in the URL, and We Work Remotely puts it in the title. dedup_listings
drops every row with no company (`no_company`), so those sources contributed
thousands of scraped rows and zero jobs.

The company is almost always recoverable from data we already hold, so this is
pure parsing — no extra HTTP.

Rules per site:
  weworkremotely  title is "Company: Role"          -> split on the first ": "
  landing_jobs    URL is /at/<company>/<job-slug>   -> path segment
  nodesk          slug is <company>-<role-words>    -> slug minus title words
  cutshort        slug is <Role-Words>-<Company>-<id> -> slug minus title/city/id
  hirist          title is "Company - Role - ..."   -> prefix, when it is not
                                                        itself a role phrase
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Words that mean a phrase is a job title, not an employer name.
ROLE_WORDS = {
    "engineer", "engineering", "developer", "development", "scientist",
    "analyst", "analytics", "manager", "lead", "architect", "consultant",
    "specialist", "administrator", "designer", "programmer", "intern",
    "internship", "trainee", "associate", "executive", "officer", "head",
    "director", "principal", "senior", "junior", "staff", "sr", "jr",
    "fullstack", "full", "stack", "backend", "frontend", "devops", "sde",
    "mlops", "data", "software", "python", "java", "react", "node", "cloud",
    "ai", "ml", "qa", "test", "testing", "support", "sales", "marketing",
    "product", "project", "business", "technical", "tech", "web", "mobile",
    "android", "ios", "security", "network", "database", "system", "systems",
    "platform", "site", "reliability", "research", "machine", "learning",
    "deep", "computer", "vision", "nlp", "llm", "gen", "generative",
}

# Indian metros that pad Cutshort slugs between the role and the company.
CITY_WORDS = {
    "bengaluru", "bangalore", "hyderabad", "mumbai", "pune", "chennai",
    "delhi", "noida", "gurugram", "gurgaon", "kolkata", "ahmedabad", "jaipur",
    "indore", "kochi", "coimbatore", "chandigarh", "nagpur", "vadodara",
    "thiruvananthapuram", "bhubaneswar", "mysore", "mysuru", "faridabad",
    "ghaziabad", "remote", "india", "anywhere", "ncr",
}

_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def _tokens(text: str | None) -> list[str]:
    return [t for t in _TOKEN_RE.split((text or "").lower()) if t]


def _looks_like_role(phrase: str) -> bool:
    """True when every meaningful word in the phrase is role vocabulary."""
    toks = _tokens(phrase)
    if not toks:
        return True
    return all(t in ROLE_WORDS or t.isdigit() for t in toks)


def _titleize(slug_words: list[str]) -> str:
    """Keep whatever casing the slug already carried; only fix all-lowercase."""
    out = []
    for w in slug_words:
        out.append(w if any(c.isupper() for c in w) else w.capitalize())
    return " ".join(out).strip()


def _is_opaque_id(token: str) -> bool:
    """Cutshort ends slugs with an 8-ish char opaque id: Rqw1mekJ, EMFogiZR.

    Some carry no digit at all, so "has a digit" is not enough — the other
    tell is camel-ish noise, a lowercase letter immediately followed by an
    uppercase one, which real company words do not have.
    """
    if not re.fullmatch(r"[A-Za-z0-9]{6,12}", token or ""):
        return False
    if not any(c.isalpha() for c in token):
        return False
    if any(c.isdigit() for c in token):
        return True
    return bool(re.search(r"[a-z][A-Z]", token))


def from_weworkremotely(title: str | None) -> tuple[str, str]:
    """"Gusto, Inc.: Staff Software Engineer" -> ("Gusto, Inc.", "Staff …")."""
    raw = (title or "").strip()
    if ":" not in raw:
        return "", raw
    company, _, rest = raw.partition(":")
    company, rest = company.strip(), rest.strip()
    if not company or not rest or _looks_like_role(company):
        return "", raw
    return company, rest


def from_landing_jobs(url: str | None) -> str:
    """https://landing.jobs/at/<company>/<job-slug>."""
    parts = [p for p in urlparse(url or "").path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "at":
        return _titleize(parts[1].split("-"))
    return ""


def from_slug_minus_title(url: str | None, title: str | None, *,
                          company_first: bool) -> str:
    """Company words are the slug words the title does not account for."""
    parts = [p for p in urlparse(url or "").path.split("/") if p]
    if not parts:
        return ""
    words = [w for w in parts[-1].split("-") if w]
    if words and _is_opaque_id(words[-1]):
        words = words[:-1]
    title_toks = set(_tokens(title))
    leftover = [
        w for w in words
        if w.lower() not in title_toks and w.lower() not in CITY_WORDS
    ]
    if not leftover:
        return ""
    if company_first:
        # NoDesk: <company>-<role…>; keep the run before the first title word.
        head: list[str] = []
        for w in words:
            if w.lower() in title_toks:
                break
            if w.lower() in CITY_WORDS:
                continue
            head.append(w)
        leftover = head or leftover
    if len(leftover) > 5:          # nothing sane is this long — bail
        return ""
    return _titleize(leftover)


def company_from_role_slug(slug: str | None) -> tuple[str, str]:
    """Split ``<Role-Words>-[<City>…]-<Company>-<id>`` with no title to lean on.

    ``from_slug_minus_title`` needs a role-only title to subtract; when the
    title was itself derived from the slug that is circular. Here the company
    is the trailing run of words that are not role vocabulary.
    """
    words = [w for w in (slug or "").split("-") if w]
    if words and _is_opaque_id(words[-1]):
        words = words[:-1]
    words = [w for w in words if w.lower() not in CITY_WORDS]
    if not words:
        return "", ""
    tail: list[str] = []
    for w in reversed(words):
        if w.lower() in ROLE_WORDS:
            break
        tail.append(w)
    tail.reverse()
    if not tail or len(tail) == len(words) or len(tail) > 5:
        # All role words, or nothing looks like a role — do not guess.
        return "", " ".join(words).strip()
    role = " ".join(words[:len(words) - len(tail)]).strip()
    return _titleize(tail), role


# Words that end a company name. Used when the separator between employer and
# role has been lost (Hirist's sitemap flattens "Company - Role" into one slug).
CORPORATE_SUFFIXES = {
    "technologies", "technology", "systems", "software", "solutions", "labs",
    "lab", "inc", "ltd", "limited", "pvt", "private", "llp", "corp",
    "corporation", "group", "consulting", "consultancy", "services",
    "networks", "digital", "infotech", "analytics", "media", "ventures",
    "studios", "works", "global", "india",
}
# A title starting with one of these is a role, never an employer.
_ROLE_LEAD = {
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "associate",
    "assistant", "chief", "head", "deputy", "trainee", "intern",
}


def company_from_corporate_suffix(title: str | None) -> tuple[str, str]:
    """"Vunet Systems Golang Developer" -> ("Vunet Systems", "Golang Developer").

    Deliberately conservative: a wrong employer on a listing is worse than no
    employer, because the user tailors a resume to it. Only fires when a
    corporate suffix appears in the first few words and the title does not
    start with seniority vocabulary.
    """
    toks = (title or "").split()
    if len(toks) < 3:
        return "", (title or "").strip()
    low = [t.lower().strip(",.") for t in toks]
    if low[0] in _ROLE_LEAD or low[0] in ROLE_WORDS:
        return "", (title or "").strip()
    for i, tok in enumerate(low[:4]):
        if i and tok in _ROLE_LEAD:
            # Seniority word — the employer name ended before it, so a suffix
            # found later belongs to the role ("Publicis Sapient Senior
            # Software Engineer" is not a company called "… Senior Software").
            break
        if i == 0 or tok not in CORPORATE_SUFFIXES:
            continue
        company = " ".join(toks[: i + 1]).strip()
        role = " ".join(toks[i + 1 :]).strip()
        if not role or len(role.split()) < 2:
            return "", (title or "").strip()
        return company, role
    return "", (title or "").strip()


def from_hirist_title(title: str | None) -> tuple[str, str]:
    """"Tech Mahindra - Senior Business Analyst - X" -> company, role."""
    raw = (title or "").strip()
    if " - " not in raw:
        return "", raw
    head, _, rest = raw.partition(" - ")
    head, rest = head.strip(), rest.strip()
    if not head or not rest:
        return "", raw
    if _looks_like_role(head):     # "Data Scientist - Python" has no company
        return "", raw
    if len(_tokens(head)) > 5:
        return "", raw
    return head, rest


def derive(site: str, *, url: str | None, title: str | None) -> tuple[str, str]:
    """Return (company, cleaned_title). Empty company = not recoverable."""
    site = (site or "").lower()
    if site == "weworkremotely":
        return from_weworkremotely(title)
    if site == "landing_jobs":
        return from_landing_jobs(url), (title or "").strip()
    if site == "nodesk":
        return (from_slug_minus_title(url, title, company_first=True),
                (title or "").strip())
    if site == "cutshort":
        return (from_slug_minus_title(url, title, company_first=False),
                (title or "").strip())
    if site == "hirist":
        company, role = from_hirist_title(title)
        if company:
            return company, role
        # Sitemap rows lost the " - " separator when the slug was flattened.
        return company_from_corporate_suffix(title)
    return "", (title or "").strip()
