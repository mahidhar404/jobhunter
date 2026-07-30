"""Extract answers from the tailored resume PDF - the per-job source of truth.

Reads the PDF, not the .tex. The PDF is the artifact actually uploaded to the
employer, so it is what the application must be consistent with; parsing the
LaTeX source would describe a document the employer never sees, and any
divergence introduced at compile time would go unnoticed.

Decided by the user, and the data backs it up: for current company, current
city, job titles and dates the RESUME is authoritative, not profile.json.
PartyRock tailors a fresh resume per job and the header city genuinely differs
between them (Alpharetta GA, Austin TX, Arlington VA, Irving TX, Jersey City NJ,
Herndon VA, Englewood CO, San Jose CA across the saved set), as does the current
employer. A form asking "what city are you currently located in?" must be
answered from THAT job's resume or the application contradicts its own
attachment. No single stored value can be correct.

Extraction strategy, per the user: a resume contains exactly ONE email, ONE
phone, ONE LinkedIn and ONE name. So those are found by scanning the whole
document for the unique pattern rather than by parsing header layout - layout is
the fragile part (two real templates disagree on whether the name and contact
line share a block), while an email is recognisable as an email wherever it sits.

Everything is Optional. A caller must treat None as "unknown - ask Layer 2 or
leave blank", never as an empty string to type: silently filling "" is
indistinguishable, to an employer, from a deliberate blank answer.
"""

import re
from pathlib import Path

# Section headings render uppercase in the PDF (\MakeUppercase in titleformat).
_SEC_EXPERIENCE = re.compile(r"^\s*(WORK\s+EXPERIENCE|EXPERIENCE|EMPLOYMENT)\s*$", re.I)
_SEC_EDUCATION = re.compile(r"^\s*EDUCATION\s*$", re.I)
_SEC_ANY = re.compile(r"^[A-Z][A-Z &/]{3,40}$")

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
# Matches "Jan 2025 - Present", "March 2025 -- Dec 2024", en/em dashes included.
_DATE_RANGE = re.compile(
    rf"((?:{_MONTH}\s+)?\d{{4}}\s*[-–—]+\s*(?:Present|Current|Now|(?:{_MONTH}\s+)?\d{{4}}))",
    re.I,
)
_PRESENT = re.compile(r"\b(present|current|now)\b", re.I)


def extract_text(pdf_path: str | Path) -> str:
    """PDF -> plain text. pdfplumber first (better layout fidelity), pypdf as a
    fallback so a single dependency problem cannot take the parser out."""
    p = Path(pdf_path)
    if not p.exists():
        return ""
    try:
        import pdfplumber
        with pdfplumber.open(str(p)) as pdf:
            return "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
    except Exception:
        return ""


def parse_resume(pdf_path: str | Path) -> dict:
    """Return the fields a job application actually asks for."""
    text = extract_text(pdf_path)
    out = {
        "full_name": None, "email": None, "phone": None,
        "linkedin": None, "github": None,
        "city": None, "state": None, "city_state": None,
        "current_company": None, "current_title": None, "current_start": None,
        "is_currently_employed": None,
        "positions": [], "education": [],
    }
    if not text.strip():
        return out

    lines = [ln.strip() for ln in text.split("\n")]

    # -- unique values, scanned document-wide --------------------------------
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    if m:
        out["email"] = m.group(0).rstrip(".")
    # Requires the 3-3-4 digit shape, so GPAs ("3.62/4.0"), years and percentage
    # metrics cannot masquerade as a phone number.
    m = re.search(r"(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b", text)
    if m:
        out["phone"] = m.group(0).strip()
    m = re.search(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w%-]+", text, re.I)
    if m:
        out["linkedin"] = m.group(0)
    m = re.search(r"(?:https?://)?(?:www\.)?github\.com/[\w-]+", text, re.I)
    if m:
        out["github"] = m.group(0)

    # -- name: the first meaningful line -------------------------------------
    # Every observed template renders the name largest and first, so in
    # extracted text it is line 0. Guarded against a line that is really contact
    # details or a section heading.
    for ln in lines[:6]:
        if not ln or "@" in ln or _SEC_ANY.match(ln):
            continue
        if re.search(r"\d{3}", ln):
            continue
        if 1 <= len(ln.split()) <= 5:
            out["full_name"] = ln
            break

    # -- city/state: header region only --------------------------------------
    # Bounded to the lines before the first section heading. Employer locations
    # ("Florham Park, NJ") and the school's city ("Tuscaloosa, AL") appear later
    # and would otherwise win.
    head_end = next((i for i, ln in enumerate(lines) if _SEC_ANY.match(ln)), min(6, len(lines)))
    for ln in lines[:head_end]:
        m = re.search(r"([A-Z][A-Za-z .'-]+),\s*([A-Z]{2})\b", ln)
        if m:
            out["city"], out["state"] = m.group(1).strip(), m.group(2).strip()
            out["city_state"] = f"{out['city']}, {out['state']}"
            break

    # -- section boundaries --------------------------------------------------
    def section_span(matcher):
        start = next((i for i, ln in enumerate(lines) if matcher.match(ln)), None)
        if start is None:
            return None
        end = next((j for j in range(start + 1, len(lines))
                    if _SEC_ANY.match(lines[j]) and not matcher.match(lines[j])), len(lines))
        return start + 1, end

    # -- work experience -----------------------------------------------------
    # Entries render as a "Title ....... Jan 2025 - Present" line (the \hfill
    # becomes whitespace) followed by "Company | Location".
    span = section_span(_SEC_EXPERIENCE)
    if span:
        s, e = span
        i = s
        while i < e:
            ln = lines[i]
            dm = _DATE_RANGE.search(ln)
            if dm and not ln.startswith("•"):
                title = ln[:dm.start()].strip(" .·-")
                dates = dm.group(1).strip()
                company = location = None
                if i + 1 < e:
                    nxt = lines[i + 1]
                    if nxt and not nxt.startswith("•") and not _DATE_RANGE.search(nxt):
                        if "|" in nxt:
                            parts = [p.strip() for p in nxt.split("|")]
                            company, location = parts[0], (parts[1] if len(parts) > 1 else None)
                        else:
                            company = nxt.strip()
                if title:
                    out["positions"].append({
                        "title": title, "company": company, "location": location,
                        "dates": dates, "is_current": bool(_PRESENT.search(dates)),
                    })
            i += 1

        current = next((p for p in out["positions"] if p["is_current"]), None)
        # Fall back to the first listed role only when nothing says "Present":
        # resumes are reverse-chronological, so the top entry is still the most
        # recent even if its end date has passed.
        chosen = current or (out["positions"][0] if out["positions"] else None)
        if chosen:
            out["current_company"] = chosen["company"]
            out["current_title"] = chosen["title"]
            out["current_start"] = chosen["dates"]
            out["is_currently_employed"] = bool(current)

    # -- education -----------------------------------------------------------
    span = section_span(_SEC_EDUCATION)
    if span:
        s, e = span
        for ln in lines[s:e]:
            if len(ln) > 8 and not ln.startswith("•"):
                out["education"].append(ln)

    return out


def resume_value_map(parsed: dict) -> dict:
    """Map parsed resume fields onto field_map's canonical types.

    Salary is deliberately ABSENT: the user's rule is that it must be derived
    from the posting's own description, location, title and company, making it a
    Layer 2 decision with the job description in context - never a stored value.
    """
    from field_map import (
        NAME_FULL, NAME_FIRST, NAME_LAST, EMAIL, PHONE, LINKEDIN, GITHUB,
        ADDRESS_CITY, ADDRESS_STATE, CURRENT_COMPANY, CURRENT_TITLE,
    )
    vm = {}
    if parsed.get("full_name"):
        full = parsed["full_name"]
        first, _, last = full.partition(" ")
        vm[NAME_FULL], vm[NAME_FIRST], vm[NAME_LAST] = full, first, last
    for key, ftype in (("email", EMAIL), ("phone", PHONE),
                       ("linkedin", LINKEDIN), ("github", GITHUB),
                       ("city", ADDRESS_CITY), ("state", ADDRESS_STATE),
                       ("current_company", CURRENT_COMPANY),
                       ("current_title", CURRENT_TITLE)):
        if parsed.get(key):
            vm[ftype] = parsed[key]
    return vm


def redact(parsed: dict) -> dict:
    """Mask identifying values so parser output is safe to print.

    Extraction COUNTS are what a test needs; the values are not. An earlier
    revision printed the real name, email and phone straight to the terminal.
    """
    def mask(v):
        if not v:
            return v
        s = str(v)
        if "@" in s:
            u, _, d = s.partition("@")
            return f"{u[:2]}***@{d}"
        if sum(c.isdigit() for c in s) >= 7:
            return "***-***-" + "".join(c for c in s if c.isdigit())[-2:]
        return (s[:2] + "*" * max(len(s) - 2, 1))[:18]

    out = dict(parsed)
    for k in ("full_name", "email", "phone", "linkedin", "github"):
        if out.get(k):
            out[k] = mask(out[k])
    return out


if __name__ == "__main__":
    import json
    import sys

    # Defaults to the synthetic fixture PDF: running this must never require -
    # or expose - real resume data. `--real` opts in explicitly, and values stay
    # masked even then unless `--show` is passed.
    use_real = "--real" in sys.argv
    show_values = "--show" in sys.argv
    positional = [a for a in sys.argv[1:] if not a.startswith("-")]

    if positional:
        paths = [Path(positional[0])]
    elif use_real:
        paths = sorted((Path(__file__).resolve().parents[2] / "resumes").glob("*/resume.pdf"))
        print("[*** REAL RESUME PDFs ***]  (values masked unless --show)\n")
    else:
        paths = sorted((Path(__file__).resolve().parent / "fixtures").glob("*.pdf"))
        print("[DUMMY fixture PDFs]  (pass --real to parse actual resumes)\n")

    fields = ["full_name", "email", "phone", "city_state",
              "current_company", "current_title"]
    ok = dict.fromkeys(fields, 0)
    cities, companies = {}, {}
    for p in paths:
        d = parse_resume(p)
        for k in fields:
            if d.get(k):
                ok[k] += 1
        if d.get("city_state"):
            cities[d["city_state"]] = cities.get(d["city_state"], 0) + 1
        if d.get("current_company"):
            companies[d["current_company"]] = companies.get(d["current_company"], 0) + 1

    n = len(paths)
    print(f"parsed {n} resume PDFs\n")
    for k in fields:
        print(f"  {k:18s} {ok[k]:4d}/{n}  ({ok[k]/max(n,1)*100:.0f}%)")
    print("\ncity on resume header (varies per job - why it must be parsed):")
    for c, v in sorted(cities.items(), key=lambda x: -x[1])[:8]:
        print(f"   {v:3d}x  {c}")
    print("\ncurrent company extracted:")
    for c, v in sorted(companies.items(), key=lambda x: -x[1])[:6]:
        print(f"   {v:3d}x  {c}")
    if n:
        sample = parse_resume(paths[0])
        if not show_values:
            sample = redact(sample)
        print("\nsample:")
        print(json.dumps({k: v for k, v in sample.items() if k != "positions"}, indent=2)[:700])
