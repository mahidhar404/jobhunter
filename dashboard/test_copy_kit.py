#!/usr/bin/env python3
"""Fast copy / copy-kit: dummy vs real, LaTeX role parse, API 404.

Never reads real profile.json PII — dummy fixtures only.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SERVER_PATH = HERE / "server.py"
APP_JS = HERE / "static" / "app.js"
INDEX_HTML = HERE / "static" / "index.html"

# Distinctive fake-real markers — must NEVER appear in test-mode kits.
_LEAK_EMAIL = "real.pii.leak@example.test"
_LEAK_NAME = "Real Pii Leakerson"
_LEAK_PHONE = "212-555-0199"
_LEAK_COMPANY = "Leakerson Industries"

_TWO_JOB_TEX = r"""
\documentclass{article}
\begin{document}
\section*{Work Experience}
\textbf{Senior Example Engineer} \hfill \textit{March 2025 -- Present}
\textbf{Example Corporation} | Remote
\begin{itemize}
\item Built a fictional pipeline with a made-up metric of 42\%.
\item Partnered with example teams on placeholder work.
\item
\end{itemize}
\textbf{Example Engineer} \hfill \textit{June 2022 -- February 2025}
\textbf{Sample Industries LLC} | Springfield, IL
\begin{itemize}
\item Placeholder bullet for a prior fictional role.
\item   \n
\end{itemize}
\section*{Education}
\textbf{M.S., Example Studies}, Example State University, Springfield, IL (GPA: 3.10/4.0), May 2019
\textbf{B.S., Example Studies}, Example State University, Springfield, IL (GPA: 3.00/4.0), May 2017
\end{document}
"""

_EDU_NO_DATES_TEX = r"""
\section{Work Experience}
\textbf{Analyst} \hfill \textit{Jan 2025 -- Present}\\
\textbf{Fixture Corp} | Hybrid
\begin{itemize}
\item Placeholder work.
\end{itemize}
\section{Education}
\textbf{Master's Degree, Computer Science}, University of Alabama, Tuscaloosa, AL \quad (GPA: 3.10/4.0)\\
\textbf{Bachelor's Degree, Computer Science}, GITAM, Visakhapatnam, India \quad (GPA: 3.00/4.0)
"""

_NO_EDU_TEX = r"""
\section{Work Experience}
\textbf{Analyst} \hfill \textit{Jan 2025 -- Present}\\
\textbf{Fixture Corp} | Remote
\begin{itemize}
\item Placeholder work.
\end{itemize}
\section{Technical Skills}
\textbf{Python}
"""

_EDU_HFILL_TEX = r"""
\section{Education}
\textbf{M.S., Example Studies} \hfill \textit{Aug 2017 -- May 2019}
Example State University, Springfield, IL
\textbf{B.S., Example Studies} \hfill \textit{Aug 2013 -- May 2017}
Example State University, Springfield, IL
"""

_PARAGRAPH_TEX = r"""
\section{Work Experience}
\textbf{Analyst} \hfill \textit{Jan 2025 -- Present}\\
\textbf{Fixture Corp} | Remote
First line of a paragraph description.
Second line after a newline.
• Leading bullet glyph line
- Dash prefixed line
"""


def _load_copy_kit():
    path = HERE / "copy_kit.py"
    spec = importlib.util.spec_from_file_location("jh_copy_kit", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jh_copy_kit"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_server():
    spec = importlib.util.spec_from_file_location("jh_copy_kit_srv", SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jh_copy_kit_srv"] = mod
    spec.loader.exec_module(mod)
    return mod


def _leak_profile() -> dict:
    return {
        "personal": {"full_name": _LEAK_NAME},
        "contact": {"email": _LEAK_EMAIL, "phone": _LEAK_PHONE},
        "links": {"linkedin": "https://www.linkedin.com/in/leakerson"},
        "experience": {
            "current_company": _LEAK_COMPANY,
            "current_title": "Staff Leaker",
            "jobs": [
                {
                    "company": _LEAK_COMPANY,
                    "title": "Staff Leaker",
                    "bullets": ["Leaked a real-looking bullet that tests must not copy in test mode."],
                }
            ],
        },
        "education": {"degrees": [{"school": "Leak U", "degree": "PhD", "discipline": "Secrets"}]},
    }


def test_parse_roles_from_two_job_tex_items():
    ck = _load_copy_kit()
    roles = ck.parse_resume_roles_from_tex(_TWO_JOB_TEX)
    assert len(roles) == 2
    assert roles[0]["company"] == "Example Corporation"
    assert roles[0]["title"] == "Senior Example Engineer"
    assert roles[0]["period"] == "March 2025 – Present"
    assert roles[0]["bullets"] == [
        "Built a fictional pipeline with a made-up metric of 42%.",
        "Partnered with example teams on placeholder work.",
    ]
    assert roles[1]["company"] == "Sample Industries LLC"
    assert roles[1]["title"] == "Example Engineer"
    assert roles[1]["period"] == "June 2022 – February 2025"
    assert roles[1]["bullets"] == ["Placeholder bullet for a prior fictional role."]


def test_format_bullets_block_and_kit_period_rows():
    ck = _load_copy_kit()
    bullets = ["First achievement.", "Second achievement."]
    block = ck.format_bullets_block(bullets)
    assert block == "• First achievement.\n• Second achievement."
    kit = ck.build_copy_kit(
        {"id": "period-kit", "company": "Fixture Corp", "file_id": "99999"},
        test_mode=True,
        tex=_TWO_JOB_TEX,
        profile=_leak_profile(),
    )
    roles_group = _group(kit, "roles")
    role0 = roles_group["roles"][0]
    assert role0["period"] == "March 2025 – Present"
    rows = {r["key"]: r for r in role0["rows"]}
    assert rows["role-0-period"]["value"] == "March 2025 – Present"
    assert rows["role-0-period"]["label"] == "Period"
    assert rows["role-0-company"]["label"] == "Company"
    assert rows["role-0-title"]["label"] == "Title"
    assert "role-0-bullets-all" not in rows
    assert not any(r.get("label") == "Bullet" for r in role0["rows"])
    assert not any(r.get("label") == "All bullets" for r in role0["rows"])
    assert not any(str(r.get("key") or "").endswith("-b0") for r in role0["rows"])
    assert role0["bullets"] == [
        "Built a fictional pipeline with a made-up metric of 42%.",
        "Partnered with example teams on placeholder work.",
    ]
    bulk = role0["bulk_bullets"]
    assert bulk.startswith("• ")
    assert "\n• " in bulk
    assert "Built a fictional pipeline" in bulk
    assert "Partnered with example teams" in bulk
    assert kit["roles"][0]["period"] == "March 2025 – Present"
    assert kit["roles"][0]["bullets"] == role0["bullets"]


def test_parse_roles_omits_empty_bullets_and_splits_paragraphs():
    ck = _load_copy_kit()
    empty_only = ck.parse_resume_roles_from_tex(
        r"""
\section{Experience}
\textbf{Title} \hfill \textit{2024 -- Present}
\textbf{Co}
\begin{itemize}
\item
\item   
\end{itemize}
"""
    )
    assert empty_only == [{"company": "Co", "title": "Title", "bullets": []}] or (
        empty_only and empty_only[0]["company"] == "Co" and empty_only[0]["bullets"] == []
    )
    para = ck.parse_resume_roles_from_tex(_PARAGRAPH_TEX)
    assert len(para) == 1
    assert para[0]["company"] == "Fixture Corp"
    assert para[0]["title"] == "Analyst"
    assert para[0]["period"] == "Jan 2025 – Present"
    bullets = para[0]["bullets"]
    assert "First line of a paragraph description." in bullets
    assert "Second line after a newline." in bullets
    assert "Leading bullet glyph line" in bullets
    assert "Dash prefixed line" in bullets
    assert all(b.strip() for b in bullets)


def test_copy_kit_test_mode_does_not_leak_profile(tmp_path=None):
    ck = _load_copy_kit()
    job = {
        "id": "kit-dummy-1",
        "company": "Fixture Corp",
        "title": "Dummy Role",
        "file_id": "12345",
        "applied_address": "10 Main St, Austin, TX 78701",
    }
    opened = {"profile": False}

    def _forbid_profile(*_a, **_k):
        opened["profile"] = True
        raise AssertionError("test mode must not read profile.json")

    kit = ck.build_copy_kit(
        job,
        test_mode=True,
        tex=_TWO_JOB_TEX,
        profile_loader=_forbid_profile,
        profile=_leak_profile(),
    )
    blob = json.dumps(kit)
    assert opened["profile"] is False
    assert _LEAK_EMAIL not in blob
    assert _LEAK_NAME not in blob
    assert _LEAK_PHONE not in blob
    assert _LEAK_COMPANY not in blob
    assert kit["test_mode"] is True
    assert kit["roles_source"] == "tex"
    assert any(r["company"] == "Example Corporation" for r in kit["roles"])
    contact = _group(kit, "contact")
    values = {row["label"]: row["value"] for row in contact["rows"]}
    assert values["Full name"] == "Test Dummy"
    assert values["Email"] == "randommail6969@gmail.com"
    assert "real.pii" not in values["Email"]
    addr = _group(kit, "address")
    addr_vals = {row["label"]: row["value"] for row in addr["rows"]}
    assert addr_vals["City"] == "Austin"
    assert addr_vals["State"] == "TX"
    assert addr_vals["ZIP"] == "78701"
    assert addr_vals["Street"] == "10 Main St"
    assert kit["resume_filename"] == "Fixture Corp_resume_12345.pdf"


def test_copy_kit_test_mode_uses_tex_experience_not_dummy_dump():
    ck = _load_copy_kit()
    kit = ck.build_copy_kit(
        {"id": "j1", "company": "Acme", "file_id": "00001"},
        test_mode=True,
        tex=_TWO_JOB_TEX,
        profile=_leak_profile(),
    )
    companies = [r["company"] for r in kit["roles"]]
    assert companies == ["Example Corporation", "Sample Industries LLC"]
    assert _LEAK_COMPANY not in companies


def test_copy_kit_test_mode_without_tex_uses_dummy_fixture_not_profile():
    ck = _load_copy_kit()
    dummy_tex = Path(ROOT / "scripts" / "fastfill" / "fixtures" / "dummy_resume.tex").read_text(
        encoding="utf-8"
    )
    kit = ck.build_copy_kit(
        {"id": "j2", "company": "Acme"},
        test_mode=True,
        tex=None,
        dummy_tex=dummy_tex,
        profile=_leak_profile(),
        profile_loader=lambda: (_ for _ in ()).throw(AssertionError("no profile")),
    )
    blob = json.dumps(kit)
    assert _LEAK_EMAIL not in blob
    assert _LEAK_COMPANY not in blob
    assert kit["roles_source"] in ("dummy_tex", "dummy_fixture")
    assert len(kit["roles"]) >= 2
    assert kit["roles"][0]["company"]
    assert kit["roles"][0]["title"]
    assert kit["roles"][0]["bullets"]


def test_copy_kit_real_mode_uses_injected_profile_not_disk():
    ck = _load_copy_kit()
    fake = {
        "personal": {"full_name": "Ada Fixture"},
        "contact": {"email": "ada.fixture@example.test", "phone": "405-555-0188"},
        "links": {
            "linkedin": "https://www.linkedin.com/in/ada-fixture",
            "github": "https://github.com/ada-fixture",
        },
        "experience": {
            "jobs": [
                {
                    "company": "Fixture Labs",
                    "title": "Engineer",
                    "description": "Did a thing.\nDid another thing.",
                }
            ]
        },
        "address": {"country": "United States"},
    }
    kit = ck.build_copy_kit(
        {
            "id": "real-1",
            "company": "Acme",
            "file_id": "54321",
            "applied_address": "22 Oak St, Chicago, IL 60601",
        },
        test_mode=False,
        tex=None,
        profile=fake,
        profile_loader=lambda: fake,
    )
    blob = json.dumps(kit)
    assert _LEAK_EMAIL not in blob
    contact = {row["label"]: row["value"] for row in _group(kit, "contact")["rows"]}
    assert contact["Full name"] == "Ada Fixture"
    assert contact["Email"] == "ada.fixture@example.test"
    assert kit["roles_source"] in ("profile", "profile_experience")
    assert kit["roles"][0]["company"] == "Fixture Labs"
    assert kit["roles"][0]["title"] == "Engineer"
    assert kit["roles"][0]["bullets"] == ["Did a thing.", "Did another thing."]
    group_ids = [g.get("id") for g in kit.get("groups") or []]
    assert "screening" not in group_ids
    assert "eeo" not in group_ids


def test_copy_kit_api_404_unknown_job():
    srv = _load_server()
    handler = srv.Handler.__new__(srv.Handler)
    handler.path = "/api/jobs/does-not-exist/copy-kit?test_mode=1"
    handler.headers = {}
    with mock.patch.object(srv, "read_jobs", return_value={"jobs": []}), mock.patch.object(
        handler, "_send_json"
    ) as send:
        handler.do_GET()
    send.assert_called_once()
    payload, status = send.call_args[0][0], send.call_args[0][1]
    assert status == 404
    assert payload.get("error") == "not found"


def test_copy_kit_api_requires_test_mode():
    srv = _load_server()
    handler = srv.Handler.__new__(srv.Handler)
    handler.path = "/api/jobs/job-1/copy-kit"
    handler.headers = {}
    job = {"id": "job-1", "company": "Acme", "title": "Eng"}
    with mock.patch.object(srv, "read_jobs", return_value={"jobs": [job]}), mock.patch.object(
        handler, "_send_json"
    ) as send:
        handler.do_GET()
    send.assert_called_once()
    payload, status = send.call_args[0][0], send.call_args[0][1]
    assert status == 400
    assert "test_mode" in str(payload.get("error") or "").lower()


def test_copy_kit_api_test_mode_returns_dummy_and_tex_roles(tmp_path=None):
    srv = _load_server()
    handler = srv.Handler.__new__(srv.Handler)
    handler.path = "/api/jobs/kit-job/copy-kit?test_mode=true"
    handler.headers = {}
    job = {
        "id": "kit-job",
        "company": "Fixture Corp",
        "title": "Dummy Role",
        "file_id": "12345",
        "applied_address": "10 Main St, Austin, TX 78701",
    }
    with tempfile.TemporaryDirectory() as td:
        resumes = Path(td)
        job_dir = resumes / "kit-job"
        job_dir.mkdir()
        (job_dir / "resume.tex").write_text(_TWO_JOB_TEX, encoding="utf-8")
        leak = resumes / "profile.json"
        leak.write_text(json.dumps(_leak_profile()), encoding="utf-8")
        captured = {}

        def _capture(obj=None, status=200, **kwargs):
            captured["obj"] = obj
            captured["status"] = status

        with mock.patch.object(srv, "read_jobs", return_value={"jobs": [job]}), mock.patch.object(
            srv, "RESUMES_DIR", resumes
        ), mock.patch.object(srv, "PROFILE_FILE", leak), mock.patch.object(
            handler, "_send_json", _capture
        ):
            handler.do_GET()
        assert captured.get("status", 200) == 200
        kit = captured["obj"]
        blob = json.dumps(kit)
        assert _LEAK_EMAIL not in blob
        assert _LEAK_NAME not in blob
        assert kit["test_mode"] is True
        assert kit["roles"][0]["company"] == "Example Corporation"
        assert kit["resume_filename"] == "Fixture Corp_resume_12345.pdf"


_AS_OF = date(2026, 8, 18)


def test_parse_roles_extracts_location_and_duration():
    ck = _load_copy_kit()
    roles = ck.parse_resume_roles_from_tex(_TWO_JOB_TEX, as_of=_AS_OF)
    assert roles[0]["location"] == "Remote"
    assert roles[0]["period"] == "March 2025 – Present"
    assert roles[0]["duration"] == "1 yr 6 mos"
    assert roles[1]["location"] == "Springfield, IL"
    assert roles[1]["period"] == "June 2022 – February 2025"
    assert roles[1]["duration"] == "2 yrs 9 mos"
    para = ck.parse_resume_roles_from_tex(_PARAGRAPH_TEX, as_of=_AS_OF)
    assert para[0]["location"] == "Remote"
    assert para[0]["duration"] == "1 yr 8 mos"


def test_duration_from_period_inclusive_months():
    ck = _load_copy_kit()
    assert ck.duration_from_period("Jan 2024 – Dec 2024", as_of=_AS_OF) == "1 yr"
    assert ck.duration_from_period("Dec 2020 – May 2021", as_of=_AS_OF) == "6 mos"
    assert ck.duration_from_period("March 2025 – Present", as_of=_AS_OF) == "1 yr 6 mos"
    assert ck.duration_from_period("May 2019", as_of=_AS_OF) == ""
    assert ck.duration_from_period("", as_of=_AS_OF) == ""


def test_parse_education_from_tex_school_degree_period():
    ck = _load_copy_kit()
    edu = ck.parse_education_from_tex(_TWO_JOB_TEX)
    assert len(edu) == 2
    assert edu[0]["degree"] == "M.S., Example Studies"
    assert "Example State University" in edu[0]["school"]
    assert edu[0]["period"] == "May 2019"
    assert edu[1]["degree"] == "B.S., Example Studies"
    assert "Example State University" in edu[1]["school"]
    assert edu[1]["period"] == "May 2017"
    no_dates = ck.parse_education_from_tex(_EDU_NO_DATES_TEX)
    assert len(no_dates) == 2
    assert no_dates[0]["degree"] == "Master's Degree, Computer Science"
    assert no_dates[0]["school"] == "University of Alabama, Tuscaloosa, AL"
    assert no_dates[0]["period"] == ""
    assert no_dates[1]["degree"] == "Bachelor's Degree, Computer Science"
    assert "GITAM" in no_dates[1]["school"]
    assert no_dates[1]["period"] == ""
    assert ck.parse_education_from_tex(_NO_EDU_TEX) == []


def test_parse_dummy_resume_tex_education_location_dates():
    ck = _load_copy_kit()
    tex = (ROOT / "scripts" / "fastfill" / "fixtures" / "dummy_resume.tex").read_text(
        encoding="utf-8"
    )
    roles = ck.parse_resume_roles_from_tex(tex, as_of=_AS_OF)
    assert roles[0]["location"] == "Remote"
    assert roles[0]["period"] == "March 2025 – Present"
    assert roles[0]["duration"]
    assert roles[1]["location"] == "Springfield, IL"
    assert roles[1]["period"] == "June 2022 – February 2025"
    edu = ck.parse_education_from_tex(tex)
    assert len(edu) == 2
    assert edu[0]["degree"] == "M.S., Example Studies"
    assert "Example State University" in edu[0]["school"]
    assert edu[0]["period"] == "May 2019"
    assert edu[1]["degree"] == "B.S., Example Studies"
    assert edu[1]["period"] == "May 2017"
    de = (ROOT / "scripts" / "fastfill" / "fixtures" / "dummy_resume_de.tex").read_text(
        encoding="utf-8"
    )
    de_roles = ck.parse_resume_roles_from_tex(de, as_of=_AS_OF)
    assert de_roles[0]["location"] == "Remote"
    assert de_roles[1]["location"] == "Springfield, IL"
    de_edu = ck.parse_education_from_tex(de)
    assert de_edu[0]["school"].startswith("University of Alabama")
    assert de_edu[0]["period"] == ""
    assert "Leak U" not in json.dumps(de_edu)


def test_copy_kit_includes_education_location_duration_rows():
    ck = _load_copy_kit()
    kit = ck.build_copy_kit(
        {"id": "edu-kit", "company": "Fixture Corp", "file_id": "99999"},
        test_mode=True,
        tex=_TWO_JOB_TEX,
        profile=_leak_profile(),
        as_of=_AS_OF,
    )
    blob = json.dumps(kit)
    assert "Leak U" not in blob
    role0 = _group(kit, "roles")["roles"][0]
    rows = {r["key"]: r for r in role0["rows"]}
    assert rows["role-0-location"]["label"] == "Location"
    assert rows["role-0-location"]["value"] == "Remote"
    assert rows["role-0-period"]["value"] == "March 2025 – Present"
    assert rows["role-0-duration"]["label"] == "Duration"
    assert rows["role-0-duration"]["value"] == "1 yr 6 mos"
    role1 = _group(kit, "roles")["roles"][1]
    rows1 = {r["key"]: r for r in role1["rows"]}
    assert rows1["role-1-location"]["value"] == "Springfield, IL"
    edu = _group(kit, "education")
    assert edu["label"] == "EDUCATION"
    assert len(edu["education"]) == 2
    e0 = {r["key"]: r for r in edu["education"][0]["rows"]}
    assert e0["edu-0-school"]["label"] == "School"
    assert "Example State University" in e0["edu-0-school"]["value"]
    assert e0["edu-0-degree"]["label"] == "Degree"
    assert e0["edu-0-degree"]["value"] == "M.S., Example Studies"
    assert e0["edu-0-period"]["label"] == "Period"
    assert e0["edu-0-period"]["value"] == "May 2019"
    assert kit["education"][0]["degree"] == "M.S., Example Studies"
    no_edu = ck.build_copy_kit(
        {"id": "no-edu"},
        test_mode=True,
        tex=_NO_EDU_TEX,
        profile=_leak_profile(),
        as_of=_AS_OF,
    )
    assert all(g.get("id") != "education" for g in no_edu["groups"])
    assert no_edu["education"] == []


def test_copy_kit_education_not_from_profile():
    ck = _load_copy_kit()
    kit = ck.build_copy_kit(
        {"id": "j-edu", "company": "Acme"},
        test_mode=True,
        tex=_TWO_JOB_TEX,
        profile=_leak_profile(),
        profile_loader=lambda: (_ for _ in ()).throw(AssertionError("no profile")),
    )
    blob = json.dumps(kit)
    assert "Leak U" not in blob
    assert "PhD" not in blob
    schools = [e["school"] for e in kit["education"]]
    assert all("Example State University" in s for s in schools)


def test_parse_education_hfill_period():
    ck = _load_copy_kit()
    edu = ck.parse_education_from_tex(_EDU_HFILL_TEX)
    assert len(edu) == 2
    assert edu[0]["degree"] == "M.S., Example Studies"
    assert edu[0]["period"] == "Aug 2017 – May 2019"
    assert "Example State University" in edu[0]["school"]
    assert edu[1]["degree"] == "B.S., Example Studies"
    assert edu[1]["period"] == "Aug 2013 – May 2017"


def test_parse_skills_from_tex():
    ck = _load_copy_kit()
    skills = ck.parse_skills_from_tex(_NO_EDU_TEX)
    assert skills == "Python"
    de = (ROOT / "scripts" / "fastfill" / "fixtures" / "dummy_resume_de.tex").read_text(
        encoding="utf-8"
    )
    de_skills = ck.parse_skills_from_tex(de)
    assert "Languages & Processing:" in de_skills
    assert "Python" in de_skills
    assert "Cloud & AWS Services:" in de_skills
    assert "S3" in de_skills
    assert "Work Experience" not in de_skills


def test_copy_kit_includes_skills_section():
    ck = _load_copy_kit()
    kit = ck.build_copy_kit(
        {"id": "skills-kit", "company": "Fixture Corp", "file_id": "88888"},
        test_mode=True,
        tex=_NO_EDU_TEX,
        profile=_leak_profile(),
    )
    skills_group = _group(kit, "skills")
    assert skills_group["label"] == "SKILLS"
    row = skills_group["rows"][0]
    assert row["key"] == "skills-all"
    assert row["label"] == "Skills"
    assert row["value"] == "Python"
    assert kit["skills"] == "Python"
    no_skills = ck.build_copy_kit(
        {"id": "no-skills"},
        test_mode=True,
        tex=_TWO_JOB_TEX,
        profile=_leak_profile(),
    )
    assert all(g.get("id") != "skills" for g in no_skills["groups"])
    assert no_skills["skills"] == ""


def test_copy_kit_excludes_screening_and_eeo():
    ck = _load_copy_kit()
    kit = ck.build_copy_kit(
        {"id": "no-screen", "company": "Fixture Corp", "file_id": "1"},
        test_mode=True,
        tex=_TWO_JOB_TEX,
        profile=_leak_profile(),
    )
    group_ids = [g.get("id") for g in kit.get("groups") or []]
    assert "screening" not in group_ids
    assert "eeo" not in group_ids
    blob = json.dumps(kit)
    assert "SCREENING" not in blob
    assert "EEO" not in blob
    assert "Work authorization" not in blob
    assert "Gender" not in blob


def test_fast_copy_ui_contract():
    app = APP_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'theme: "fastcopy"' in app
    assert 'id: "copy-kit-btn"' in app
    assert 'ariaLabel: "Fast copy"' in app
    assert "dossierIconBtnHtml" in app
    assert "btn-icon-fastcopy" in html
    assert "toggleCopyKitPanel" in app
    assert "/copy-kit" in app
    assert "Fast copy · form kit" in app
    assert "Dummy · Test Mode" in app
    assert "Real profile" in app
    assert "writeClipboardText" in app
    assert "copy-kit-panel" in app
    assert "copy-kit-row" in app
    assert "copy-kit-bullets" in app
    assert "copy-kit-bullets-list" in app
    assert "copyKitCopyRow" in app
    assert "copyKitCopyRoleBullets" in app
    assert "copy-kit-row-tick" in app
    assert "copy-kit-row-chip" not in app
    assert "copy-kit-bullets-action" not in app
    assert "copy-kit-bullets-block" not in app
    assert "JD_COPY_ICON_SVG" not in _fn_body(app, "copyKitRowHtml")
    assert "JD_COPY_ICON_SVG" not in _fn_body(app, "copyKitBulletsHtml")
    assert "JD_COPIED_ICON_SVG" in _fn_body(app, "copyKitRowHtml")
    assert "JD_COPIED_ICON_SVG" in _fn_body(app, "copyKitBulletsHtml")
    assert "Copy bullets" not in app.replace('title="Copy bullets"', "")
    assert "copy-kit-row-bullets-all" not in app
    assert 'label === "Bullet"' not in app
    assert "RESUME ROLES" in app
    assert "EDUCATION" in app
    assert "g.education" in app
    assert "edu.school" in _fn_body(app, "renderCopyKitPanel") or "e.school" in _fn_body(
        app, "renderCopyKitPanel"
    )
    assert "edu.period" in _fn_body(app, "renderCopyKitPanel")
    assert "role.location" in _fn_body(app, "renderCopyKitPanel")
    assert "copyKitRowByKey" in app
    row_by = _fn_body(app, "copyKitRowByKey")
    assert "g.education" in row_by or ".education" in row_by
    all_text = _fn_body(app, "copyKitAllText")
    assert ".education" in all_text
    assert "edu.period" in all_text
    assert "executeResumeFace" in app
    assert "openResumeLatexEditor" in app
    assert "executeResumeOnly" in app
    assert ".copy-kit-panel" in html or ".copy-kit-row" in html
    assert ".copy-kit-bullets" in html
    assert ".copy-kit-bullets-list" in html
    assert ".copy-kit-row-tick" in html
    assert ".copy-kit-row:hover" in html
    assert ".copy-kit-row.copied" in html
    assert ".copy-kit-bullets.copied" in html
    assert ".copy-kit-copy" in html
    assert "show more" not in app.lower()
    body_css = _css_rules(html, ".copy-kit-body")
    panel_css = _css_rules(html, ".copy-kit-panel")
    row_css = _css_rules(html, ".copy-kit-row")
    bullets_css = _css_rules(html, ".copy-kit-bullets")
    list_css = _css_rules(html, ".copy-kit-bullets-list")
    assert body_css and panel_css and row_css and bullets_css and list_css
    assert "cursor: pointer" in row_css
    assert "cursor: pointer" in bullets_css
    for chunk in (body_css, panel_css, bullets_css, list_css):
        assert "max-height" not in chunk
        assert "overflow: auto" not in chunk
        assert "overflow-y" not in chunk
        assert "overflow: scroll" not in chunk


def test_dossier_action_icons_one_delete_tick_only():
    """Dossier row: no Skip button (skipJob stays for API); tick-only applied; larger trash."""
    app = APP_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    row = _fn_body(app, "renderDossier")
    assert "SKIP_ICON_SVG" not in app
    assert 'ariaLabel: "Skip"' not in app
    assert "onclick: `skipJob" not in row
    assert "async function skipJob(" in app
    assert "onclick: `deleteJob" in row
    assert "DELETE_ICON_SVG" in row
    assert 'ariaLabel: "Delete"' in row
    applied = app.split("const MARK_APPLIED_ICON_SVG", 1)[1].split("const ", 1)[0]
    assert 'stroke="currentColor"' in applied
    assert "<rect" not in applied
    assert "A1.75 1.75" not in applied
    assert "class=\"icon-trash\"" in app.split("const DELETE_ICON_SVG", 1)[1].split("const ", 1)[0]
    trash_css = _css_rules(html, ".act.btn-icon svg.icon-trash")
    assert "width: 16px" in trash_css
    assert "height: 16px" in trash_css


def _css_rules(html: str, selector: str) -> str:
    token = selector + " {"
    start = html.find(token)
    if start < 0:
        return ""
    end = html.find("}", start)
    if end < 0:
        return html[start:]
    return html[start : end + 1]


def _fn_body(src: str, name: str) -> str:
    token = f"function {name}("
    start = src.find(token)
    if start < 0:
        return ""
    end = src.find("\nfunction ", start + 1)
    if end < 0:
        return src[start:]
    return src[start:end]


def _group(kit: dict, group_id: str) -> dict:
    for g in kit.get("groups") or []:
        if g.get("id") == group_id:
            return g
    raise AssertionError(f"missing group {group_id}: {[g.get('id') for g in kit.get('groups') or []]}")


if __name__ == "__main__":
    test_parse_roles_from_two_job_tex_items()
    test_parse_roles_extracts_location_and_duration()
    test_duration_from_period_inclusive_months()
    test_parse_education_from_tex_school_degree_period()
    test_parse_education_hfill_period()
    test_copy_kit_excludes_screening_and_eeo()
    test_parse_dummy_resume_tex_education_location_dates()
    test_copy_kit_includes_education_location_duration_rows()
    test_copy_kit_education_not_from_profile()
    test_format_bullets_block_and_kit_period_rows()
    test_parse_roles_omits_empty_bullets_and_splits_paragraphs()
    test_copy_kit_test_mode_does_not_leak_profile()
    test_copy_kit_test_mode_uses_tex_experience_not_dummy_dump()
    test_copy_kit_test_mode_without_tex_uses_dummy_fixture_not_profile()
    test_copy_kit_real_mode_uses_injected_profile_not_disk()
    test_copy_kit_api_404_unknown_job()
    test_copy_kit_api_requires_test_mode()
    test_copy_kit_api_test_mode_returns_dummy_and_tex_roles()
    test_fast_copy_ui_contract()
    test_dossier_action_icons_one_delete_tick_only()
    print("OK test_copy_kit")
