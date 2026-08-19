#!/usr/bin/env python3
"""Fast-copy form kit: fill-parity values + resume role rows from LaTeX.

Never invents EEO. Test Mode never reads profile.json.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "fastfill"))

from field_map import (  # noqa: E402
    ADDRESS_CITY,
    ADDRESS_COUNTRY,
    ADDRESS_LINE1,
    ADDRESS_LINE2,
    ADDRESS_STATE,
    ADDRESS_ZIP,
    DUMMY_ADDRESS,
    DUMMY_PROFILE,
    EMAIL,
    GITHUB,
    LINKEDIN,
    NAME_FIRST,
    NAME_FULL,
    NAME_LAST,
    PHONE,
    PHONE_COUNTRY_CODE,
    PORTFOLIO,
    build_value_map,
)
from resume_publish import conventional_resume_filename  # noqa: E402

DUMMY_TEX_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "fastfill"
    / "fixtures"
    / "dummy_resume_de.tex"
)

_SECTION_RE = re.compile(r"\\(?:section|subsection)\*?\{([^{}]+)\}")
_ITEMIZE_RE = re.compile(r"\\begin\{itemize\}(.*?)\\end\{itemize\}", re.S)
_DATEISH_RE = re.compile(r"(?:19|20)\d{2}|\bpresent\b|\bcurrent\b|\bnow\b", re.I)
_GPA_RE = re.compile(r"\s*\(\s*GPA\s*:[^)]*\)", re.I)
_MONTH_NUM = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH_ALT = (
    "january|february|march|april|may|june|july|august|september|october|"
    "november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec"
)
_DATE_TOKEN_RE = re.compile(
    rf"^(?:(?P<month>{_MONTH_ALT})\.?\s+)?(?P<year>(?:19|20)\d{{2}})$",
    re.I,
)
_EDU_DATE_TAIL_RE = re.compile(
    rf"(?:,\s*)?((?:(?:{_MONTH_ALT})\.?\s+)?(?:19|20)\d{{2}}"
    rf"(?:\s*[-–—]+\s*(?:present|current|now|"
    rf"(?:(?:{_MONTH_ALT})\.?\s+)?(?:19|20)\d{{2}}))?)\s*$",
    re.I,
)
_PERIOD_SPLIT_RE = re.compile(r"\s+[–—]\s+|\s+--\s+|\s+-\s+")
_WRAP_CMDS = (
    "textbf",
    "textit",
    "emph",
    "mbox",
    "textrm",
    "texttt",
    "textsf",
    "textsl",
    "MakeUppercase",
)


def latex_to_plain(text: str) -> str:
    """Best-effort visible text from a resume.tex fragment. Does not invent."""
    s = str(text or "")
    for _ in range(12):
        s2 = s
        for cmd in _WRAP_CMDS:
            s2 = re.sub(
                rf"\\{cmd}\*?\{{\s*([^{{}}]*)\s*\}}",
                r"\1",
                s2,
            )
        if s2 == s:
            break
        s = s2
    s = (
        s.replace(r"\%", "%")
        .replace(r"\&", "&")
        .replace(r"\$", "$")
        .replace(r"\_", "_")
        .replace(r"\#", "#")
        .replace(r"\{", "{")
        .replace(r"\}", "}")
        .replace(r"\~", " ")
        .replace("~", " ")
        .replace(r"$\rightarrow$", "->")
        .replace(r"$\to$", "->")
        .replace(r"\rightarrow", "->")
    )
    s = re.sub(r"\\(?:hfill|quad|qquad|noindent|raggedright|centering)\b", " ", s)
    s = re.sub(
        r"\\(?:vspace|hspace|needspace|rEntryHead|setstretch|titlerule)\*?"
        r"(?:\[[^\]]*\])?(?:\{[^{}]*\})?",
        " ",
        s,
    )
    s = s.replace(r"\\", "\n")
    s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?", " ", s)
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip()


def split_description_lines(text: str) -> list[str]:
    """Split a paragraph / item blob into pasteable lines. Skip empties."""
    if not (text or "").strip():
        return []
    out: list[str] = []
    for raw in re.split(r"\n+", str(text)):
        s = raw.strip()
        if not s:
            continue
        s = re.sub(r"^[\u2022•·]+\s*", "", s).strip()
        s = re.sub(r"^[-*]\s+", "", s).strip()
        if s:
            out.append(s)
    return out


def _command_contents(tex: str, cmd: str) -> list[str]:
    token = "\\" + cmd
    out: list[str] = []
    i = 0
    n = len(tex)
    while i < n:
        j = tex.find(token, i)
        if j < 0:
            break
        k = j + len(token)
        if k < n and tex[k] == "*":
            k += 1
        if k >= n or tex[k] != "{":
            i = j + 1
            continue
        k += 1
        depth = 1
        start = k
        while k < n and depth:
            if tex[k] == "{":
                depth += 1
            elif tex[k] == "}":
                depth -= 1
            k += 1
        out.append(tex[start : k - 1])
        i = k
    return out


def _is_experience_section(name: str) -> bool:
    n = re.sub(r"\s+", " ", latex_to_plain(name).strip().lower())
    n = n.replace("professional summary", "summary")
    if n in {
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "work history",
        "relevant experience",
    }:
        return True
    return False


def _is_education_section(name: str) -> bool:
    n = re.sub(r"\s+", " ", latex_to_plain(name).strip().lower())
    return n in {
        "education",
        "academic background",
        "academics",
        "education and training",
    }


def _is_skills_section(name: str) -> bool:
    n = re.sub(r"\s+", " ", latex_to_plain(name).strip().lower())
    if n in {
        "skills",
        "technical skills",
        "core skills",
        "key skills",
        "core competencies",
        "technical competencies",
        "technologies",
        "technical expertise",
        "areas of expertise",
    }:
        return True
    return "skill" in n and "experience" not in n


def _section_body(tex: str, predicate) -> str:
    kept = []
    for ln in str(tex or "").splitlines():
        if ln.lstrip().startswith("%"):
            continue
        kept.append(ln)
    src = "\n".join(kept)
    matches = list(_SECTION_RE.finditer(src))
    for i, m in enumerate(matches):
        if predicate(m.group(1)):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(src)
            return src[m.end() : end]
    return ""


def _experience_body(tex: str) -> str:
    return _section_body(tex, _is_experience_section)


def _looks_like_dates(text: str) -> bool:
    return bool(_DATEISH_RE.search(text or ""))


def _normalize_period(text: str) -> str:
    s = re.sub(r"\s+", " ", str(text or "").strip())
    s = re.sub(r"\s*--\s*", " – ", s)
    s = re.sub(r"\s*-\s*(?=present\b|current\b|now\b)", " – ", s, flags=re.I)
    return s.strip()


def _parse_date_token(token: str, *, end: bool, as_of: date) -> tuple[int, int] | None:
    t = re.sub(r"\s+", " ", str(token or "").strip())
    if re.fullmatch(r"present|current|now", t, re.I):
        return as_of.year, as_of.month
    m = _DATE_TOKEN_RE.match(t)
    if not m:
        return None
    year = int(m.group("year"))
    mon_s = (m.group("month") or "").lower().rstrip(".")
    if mon_s:
        month = _MONTH_NUM.get(mon_s)
        if not month:
            return None
    else:
        month = 12 if end else 1
    return year, month


def _format_duration(months: int) -> str:
    if months <= 0:
        return ""
    years, mos = divmod(months, 12)
    parts: list[str] = []
    if years == 1:
        parts.append("1 yr")
    elif years > 1:
        parts.append(f"{years} yrs")
    if mos == 1:
        parts.append("1 mo")
    elif mos > 1:
        parts.append(f"{mos} mos")
    return " ".join(parts)


def duration_from_period(period: str, *, as_of: date | None = None) -> str:
    """Inclusive month span from a start–end period. Single dates yield ''."""
    as_of = as_of or date.today()
    s = _normalize_period(period)
    if not s:
        return ""
    parts = [p.strip() for p in _PERIOD_SPLIT_RE.split(s, maxsplit=1) if p.strip()]
    if len(parts) < 2:
        return ""
    start = _parse_date_token(parts[0], end=False, as_of=as_of)
    end = _parse_date_token(parts[1], end=True, as_of=as_of)
    if not start or not end:
        return ""
    months = (end[0] - start[0]) * 12 + (end[1] - start[1]) + 1
    return _format_duration(months)


def _period_from_header(header: str) -> str:
    """Extract dates from ``\\textbf{Title} \\hfill \\textit{dates}`` headers."""
    for ln in header.splitlines():
        if r"\hfill" not in ln:
            continue
        parts = re.split(r"\\hfill\b", ln, maxsplit=1)
        if len(parts) < 2:
            continue
        tail = latex_to_plain(parts[1])
        if tail and _looks_like_dates(tail):
            return _normalize_period(tail)
    for it in _command_contents(header, "textit"):
        plain = latex_to_plain(it)
        if plain and _looks_like_dates(plain):
            return _normalize_period(plain)
    return ""


def _location_from_header(header: str) -> str:
    """Company line is ``\\textbf{Co} | Remote`` / ``City, ST``."""
    for ln in header.splitlines():
        plain = latex_to_plain(ln)
        if "|" not in plain or _looks_like_dates(plain):
            continue
        loc = plain.split("|", 1)[1].strip()
        loc = loc.split("|")[0].strip(" ,;")
        if loc:
            return loc
    return ""


def format_bullets_block(bullets: list[str]) -> str:
    """Join role bullets for ATS paste — one ``•`` line per bullet."""
    lines = [str(b).strip() for b in bullets if str(b or "").strip()]
    if not lines:
        return ""
    return "\n".join(f"• {line}" for line in lines)


def _company_title_from_header(header: str) -> tuple[str, str]:
    bfs = [latex_to_plain(x) for x in _command_contents(header, "textbf")]
    bfs = [re.split(r"\s*\|\s*", x, maxsplit=1)[0].strip() for x in bfs if x.strip()]
    its = [latex_to_plain(x) for x in _command_contents(header, "textit")]
    title_it = next((x for x in its if x and not _looks_like_dates(x)), "")
    if title_it and bfs:
        return bfs[0], title_it
    title = bfs[0] if bfs else ""
    company = bfs[1] if len(bfs) >= 2 else ""
    if not company:
        for ln in header.splitlines():
            plain = latex_to_plain(ln)
            if "|" in plain and not _looks_like_dates(plain):
                company = plain.split("|", 1)[0].strip()
                break
    return company, title


def _bullets_from_itemize(inner: str) -> list[str]:
    parts = re.split(r"\\item\b", inner)
    out: list[str] = []
    for part in parts[1:]:
        plain = latex_to_plain(part)
        out.extend(split_description_lines(plain))
    return out


def _strip_header_commands(chunk: str) -> str:
    s = chunk
    s = re.sub(r"\\(?:rEntryHead|vspace|hspace|needspace)\*?\{[^{}]*\}", " ", s)
    s = re.sub(r"\\(?:hfill|quad)\b", " ", s)
    return s


def _role_payload(
    *,
    company: str,
    title: str,
    period: str,
    location: str,
    bullets: list[str],
    as_of: date | None,
) -> dict:
    return {
        "company": company,
        "title": title,
        "period": period,
        "duration": duration_from_period(period, as_of=as_of) if period else "",
        "location": location,
        "bullets": bullets,
    }


def parse_resume_roles_from_tex(tex: str, *, as_of: date | None = None) -> list[dict]:
    """Parse companies, titles, location, dates, and bullets from a resume.tex.

    Understands PartyRock ``\\textbf{Title} \\hfill dates`` / ``\\textbf{Co}``
    entries and ``\\item`` lists. Empty bullets are omitted. Does not invent.
    """
    body = _experience_body(tex)
    if not body.strip():
        return []
    roles: list[dict] = []
    matches = list(_ITEMIZE_RE.finditer(body))
    if not matches:
        role = _role_from_block(body, as_of=as_of)
        return [role] if role else []
    pos = 0
    for m in matches:
        header = body[pos : m.start()]
        bullets = _bullets_from_itemize(m.group(1))
        company, title = _company_title_from_header(header)
        period = _period_from_header(header)
        location = _location_from_header(header)
        if company or title:
            roles.append(
                _role_payload(
                    company=company,
                    title=title,
                    period=period,
                    location=location,
                    bullets=bullets,
                    as_of=as_of,
                )
            )
        pos = m.end()
    leftover = body[pos:].strip()
    if leftover and "\\textbf{" in leftover:
        extra = _role_from_block(leftover, as_of=as_of)
        if extra:
            roles.append(extra)
    return roles


def _role_from_block(chunk: str, *, as_of: date | None = None) -> dict | None:
    chunk = _strip_header_commands(chunk).strip()
    if not chunk:
        return None
    itemize = _ITEMIZE_RE.search(chunk)
    if itemize:
        header = chunk[: itemize.start()]
        bullets = _bullets_from_itemize(itemize.group(1))
        rest = chunk[itemize.end() :]
        bullets.extend(split_description_lines(latex_to_plain(rest)))
    else:
        header, rest = _split_header_and_rest(chunk)
        bullets = split_description_lines(latex_to_plain(rest))
        if not header.strip():
            header = chunk
    company, title = _company_title_from_header(header)
    period = _period_from_header(header)
    location = _location_from_header(header)
    if not company and not title:
        return None
    return _role_payload(
        company=company,
        title=title,
        period=period,
        location=location,
        bullets=bullets,
        as_of=as_of,
    )


def _split_header_and_rest(chunk: str) -> tuple[str, str]:
    lines = chunk.splitlines()
    header: list[str] = []
    bf = 0
    i = 0
    while i < len(lines):
        ln = lines[i]
        stripped = ln.strip()
        if not stripped:
            if header:
                i += 1
                continue
            i += 1
            continue
        if "\\textbf{" in ln:
            bf += 1
            header.append(ln)
            i += 1
            if bf >= 2:
                break
            continue
        if header and re.search(r"\\(?:hfill|textit|vspace|rEntryHead)", ln):
            header.append(ln)
            i += 1
            continue
        if header:
            break
        i += 1
    return "\n".join(header), "\n".join(lines[i:])


def _education_from_chunk(chunk: str) -> dict | None:
    bfs = [latex_to_plain(x).strip() for x in _command_contents(chunk, "textbf")]
    bfs = [x for x in bfs if x]
    if not bfs:
        return None
    degree = bfs[0]
    plain = latex_to_plain(chunk)
    lines = [
        ln.strip()
        for ln in (split_description_lines(plain) or [plain.strip()])
        if ln.strip()
    ]
    line = lines[0] if lines else degree
    line = _GPA_RE.sub("", line)
    line = re.sub(r"\s+", " ", line).strip(" ,;")
    period = ""
    m = _EDU_DATE_TAIL_RE.search(line)
    if m:
        period = _normalize_period(m.group(1))
        line = line[: m.start()].strip(" ,;")
    if not period:
        period = _period_from_header(chunk)
        if period:
            m2 = _EDU_DATE_TAIL_RE.search(line)
            if m2:
                line = line[: m2.start()].strip(" ,;")
    school = line
    if degree and school.lower().startswith(degree.lower()):
        school = school[len(degree) :].lstrip(" ,;|-")
    school = school.strip(" ,;")
    if not school:
        for ln in lines[1:]:
            candidate = _GPA_RE.sub("", ln)
            candidate = re.sub(r"\s+", " ", candidate).strip(" ,;")
            if candidate and not _looks_like_dates(candidate):
                school = candidate
                break
    if not degree and not school:
        return None
    return {"school": school, "degree": degree, "period": period}


def parse_education_from_tex(tex: str) -> list[dict]:
    """Parse school / degree / dates from the Education section. Does not invent."""
    body = _section_body(tex, _is_education_section)
    if not body.strip():
        return []
    starts = [m.start() for m in re.finditer(r"\\textbf\*?\s*\{", body)]
    if not starts:
        itemize = _ITEMIZE_RE.search(body)
        if itemize:
            starts = [
                itemize.start() + m.start()
                for m in re.finditer(r"\\item\b", itemize.group(0))
            ]
    if not starts:
        entry = _education_from_chunk(body)
        return [entry] if entry else []
    out: list[dict] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        entry = _education_from_chunk(body[start:end])
        if entry:
            out.append(entry)
    return out


def parse_skills_from_tex(tex: str) -> str:
    """Parse the Skills section as plain text for one-shot paste. Does not invent."""
    body = _section_body(tex, _is_skills_section)
    if not body.strip():
        return ""
    itemize = _ITEMIZE_RE.search(body)
    if itemize:
        bullets = _bullets_from_itemize(itemize.group(1))
        if bullets:
            return "\n".join(bullets)
    lines_out: list[str] = []
    for raw_ln in body.splitlines():
        ln = raw_ln.strip()
        if not ln or ln.startswith("%"):
            continue
        if re.match(r"\\begin\{", ln):
            break
        plain = latex_to_plain(ln)
        plain = re.sub(r"\s+", " ", plain).strip()
        if plain:
            lines_out.append(plain)
    return "\n".join(lines_out)


def roles_from_profile_experience(profile: dict | None) -> list[dict]:
    """Best-effort roles from a profile experience block (PDF-only fallback)."""
    exp = (profile or {}).get("experience") or {}
    if not isinstance(exp, dict):
        return []
    jobs = exp.get("jobs") or exp.get("positions") or exp.get("history") or []
    out: list[dict] = []
    if isinstance(jobs, list):
        for item in jobs:
            if not isinstance(item, dict):
                continue
            company = str(
                item.get("company") or item.get("employer") or ""
            ).strip()
            title = str(
                item.get("title") or item.get("role") or item.get("position") or ""
            ).strip()
            period = str(item.get("period") or item.get("dates") or "").strip()
            if not period:
                start = str(
                    item.get("start") or item.get("start_date") or ""
                ).strip()
                end = str(item.get("end") or item.get("end_date") or "").strip()
                if start and end:
                    period = f"{start} – {end}"
                elif start:
                    period = start
            bullets: list[str] = []
            listed = False
            for key in ("bullets", "highlights", "items"):
                raw = item.get(key)
                if isinstance(raw, list):
                    listed = True
                    for b in raw:
                        bullets.extend(split_description_lines(str(b)))
                    break
            if not listed:
                desc = (
                    item.get("description")
                    or item.get("details")
                    or item.get("summary")
                    or ""
                )
                bullets = split_description_lines(str(desc))
            bullets = [b for b in bullets if b.strip()]
            if company or title:
                out.append(
                    _role_payload(
                        company=company,
                        title=title,
                        period=period,
                        location="",
                        bullets=bullets,
                        as_of=None,
                    )
                )
    if out:
        return out
    company = str(exp.get("current_company") or "").strip()
    title = str(exp.get("current_title") or "").strip()
    if company or title:
        return [
            _role_payload(
                company=company,
                title=title,
                period="",
                location="",
                bullets=[],
                as_of=None,
            )
        ]
    return []


def _default_dummy_tex() -> str:
    if DUMMY_TEX_FIXTURE.is_file():
        return DUMMY_TEX_FIXTURE.read_text(encoding="utf-8", errors="replace")
    return ""


def _resolve_roles(
    *,
    tex: str | None,
    test_mode: bool,
    profile: dict | None,
    dummy_tex: str | None,
    as_of: date | None = None,
) -> tuple[list[dict], list[dict], str, str]:
    if tex and str(tex).strip():
        # Tex is source of truth even when it has no parseable roles — do not
        # invent dummy/profile jobs on top of a real resume.tex.
        return (
            parse_resume_roles_from_tex(tex, as_of=as_of),
            parse_education_from_tex(tex),
            parse_skills_from_tex(tex),
            "tex",
        )
    if test_mode:
        src = dummy_tex if dummy_tex is not None else _default_dummy_tex()
        if src and str(src).strip():
            roles = parse_resume_roles_from_tex(src, as_of=as_of)
            education = parse_education_from_tex(src)
            skills = parse_skills_from_tex(src)
            if roles or education or skills:
                return roles, education, skills, "dummy_tex"
        dummy_roles = roles_from_profile_experience(DUMMY_PROFILE)
        return dummy_roles, [], "", ("dummy_fixture" if dummy_roles else "none")
    roles = roles_from_profile_experience(profile or {})
    return roles, [], "", ("profile" if roles else "none")


def _street_from_values(vals: dict) -> str:
    full = str(vals.get(ADDRESS_LINE1) or "").strip()
    city = str(vals.get(ADDRESS_CITY) or "").strip()
    state = str(vals.get(ADDRESS_STATE) or "").strip()
    zipc = str(vals.get(ADDRESS_ZIP) or "").strip()
    unit = str(vals.get(ADDRESS_LINE2) or "").strip()
    street = full
    if city and state:
        tail = rf",\s*{re.escape(city)}\s*,\s*{re.escape(state)}\s*{re.escape(zipc)}\s*$"
        street = re.sub(tail, "", street, flags=re.I)
    if unit:
        street = re.sub(rf",\s*{re.escape(unit)}\s*,", ",", street, flags=re.I)
        street = re.sub(rf",\s*{re.escape(unit)}\s*$", "", street, flags=re.I)
    return street.strip(" ,")


def _row(key: str, label: str, value: object, **extra) -> dict | None:
    v = str(value or "").strip()
    if not v:
        return None
    out = {"key": key, "label": label, "value": v}
    out.update(extra)
    return out


def _group(gid: str, label: str, rows: list[dict | None], **extra) -> dict | None:
    clean = [r for r in rows if r]
    if not clean and "roles" not in extra:
        return None
    out = {"id": gid, "label": label, "rows": clean}
    out.update(extra)
    return out


def _groups_from_values(
    values: dict,
    *,
    roles: list[dict],
    education: list[dict],
    resume_filename: str,
    skills: str = "",
) -> list[dict]:
    groups: list[dict] = []
    contact = _group(
        "contact",
        "CONTACT",
        [
            _row("first", "First", values.get(NAME_FIRST)),
            _row("last", "Last", values.get(NAME_LAST)),
            _row("full", "Full name", values.get(NAME_FULL)),
            _row("email", "Email", values.get(EMAIL)),
            _row("phone", "Phone", values.get(PHONE)),
            _row("phone_country", "Phone country", values.get(PHONE_COUNTRY_CODE)),
        ],
    )
    if contact:
        groups.append(contact)
    street = _street_from_values(values)
    address = _group(
        "address",
        "ADDRESS · this job",
        [
            _row("street", "Street", street),
            _row("city", "City", values.get(ADDRESS_CITY)),
            _row("state", "State", values.get(ADDRESS_STATE)),
            _row("zip", "ZIP", values.get(ADDRESS_ZIP)),
            _row("country", "Country", values.get(ADDRESS_COUNTRY)),
        ],
    )
    if address:
        groups.append(address)
    links = _group(
        "links",
        "LINKS",
        [
            _row("linkedin", "LinkedIn", values.get(LINKEDIN)),
            _row("github", "GitHub", values.get(GITHUB)),
            _row("portfolio", "Portfolio", values.get(PORTFOLIO)),
        ],
    )
    if links:
        groups.append(links)
    resume = _group(
        "resume",
        "RESUME FILE",
        [_row("resume_filename", "Resume file", resume_filename)],
    )
    if resume:
        groups.append(resume)
    skills_text = str(skills or "").strip()
    if skills_text:
        skills_group = _group(
            "skills",
            "SKILLS",
            [_row("skills-all", "Skills", skills_text)],
        )
        if skills_group:
            groups.append(skills_group)
    role_groups = []
    for i, role in enumerate(roles):
        bullets = [
            str(b).strip()
            for b in (role.get("bullets") or [])
            if str(b or "").strip()
        ]
        rows = [
            _row(f"role-{i}-company", "Company", role.get("company")),
            _row(f"role-{i}-title", "Title", role.get("title")),
            _row(f"role-{i}-location", "Location", role.get("location")),
            _row(f"role-{i}-period", "Period", role.get("period")),
            _row(f"role-{i}-duration", "Duration", role.get("duration")),
        ]
        rows = [r for r in rows if r]
        if not rows and not bullets:
            continue
        role_groups.append(
            {
                "company": str(role.get("company") or "").strip(),
                "title": str(role.get("title") or "").strip(),
                "location": str(role.get("location") or "").strip(),
                "period": str(role.get("period") or "").strip(),
                "duration": str(role.get("duration") or "").strip(),
                "bullets": bullets,
                "bulk_bullets": format_bullets_block(bullets),
                "rows": rows,
            }
        )
    if role_groups:
        groups.append(
            {
                "id": "roles",
                "label": "RESUME ROLES",
                "rows": [],
                "roles": role_groups,
            }
        )
    edu_groups = []
    for i, edu in enumerate(education or []):
        rows = [
            _row(f"edu-{i}-school", "School", edu.get("school")),
            _row(f"edu-{i}-degree", "Degree", edu.get("degree")),
            _row(f"edu-{i}-period", "Period", edu.get("period")),
        ]
        rows = [r for r in rows if r]
        if not rows:
            continue
        edu_groups.append(
            {
                "school": str(edu.get("school") or "").strip(),
                "degree": str(edu.get("degree") or "").strip(),
                "period": str(edu.get("period") or "").strip(),
                "rows": rows,
            }
        )
    if edu_groups:
        groups.append(
            {
                "id": "education",
                "label": "EDUCATION",
                "rows": [],
                "education": edu_groups,
            }
        )
    return groups


def build_copy_kit(
    job: dict | None,
    *,
    test_mode: bool,
    tex: str | None = None,
    profile: dict | None = None,
    profile_loader=None,
    dummy_tex: str | None = None,
    as_of: date | None = None,
) -> dict:
    """Compose a paste kit. Test Mode never calls ``profile_loader``."""
    job = job if isinstance(job, dict) else {}
    if test_mode:
        profile_used = DUMMY_PROFILE
    else:
        if profile is None and callable(profile_loader):
            profile = profile_loader()
        profile_used = profile if isinstance(profile, dict) else {}
    address = str(job.get("applied_address") or "").strip()
    if not address and test_mode:
        address = DUMMY_ADDRESS
    values = build_value_map(profile_used, address)
    roles, education, skills, roles_source = _resolve_roles(
        tex=tex,
        test_mode=bool(test_mode),
        profile=None if test_mode else profile_used,
        dummy_tex=dummy_tex,
        as_of=as_of,
    )
    filename = conventional_resume_filename(job) if job else ""
    groups = _groups_from_values(
        values,
        roles=roles,
        education=education,
        resume_filename=filename,
        skills=skills,
    )
    return {
        "id": job.get("id") or "",
        "test_mode": bool(test_mode),
        "roles_source": roles_source,
        "roles": roles,
        "education": education,
        "skills": skills,
        "resume_filename": filename,
        "groups": groups,
    }
