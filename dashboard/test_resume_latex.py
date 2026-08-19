#!/usr/bin/env python3
"""Focused tests for dashboard LaTeX resume editing and compilation."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SERVER_PATH = HERE / "server.py"
APP_JS = HERE / "static" / "app.js"
INDEX_HTML = HERE / "static" / "index.html"

_DUMMY_TEX = (
    "\\documentclass{article}\\begin{document}"
    "JobSpecificDummyTex\\end{document}\n"
)
_SIBLING_TEX = (
    "\\documentclass{article}\\begin{document}"
    "SiblingCompileInput\\end{document}\n"
)
_MASTER_TEX = (
    "\\documentclass{article}\\begin{document}"
    "WorkspaceMasterDummy\\end{document}\n"
)


def _fn_body(src: str, name: str) -> str:
    token = f"function {name}("
    start = src.find(token)
    if start < 0:
        return ""
    end = src.find("\nfunction ", start + 1)
    if end < 0:
        return src[start:]
    return src[start:end]


def _get_resume_latex(srv, job: dict, *, root: Path):
    handler = srv.Handler.__new__(srv.Handler)
    handler.path = f"/api/jobs/{job['id']}/resume-latex"
    handler.headers = {}
    with (
        mock.patch.object(srv, "read_jobs", return_value={"jobs": [job]}),
        mock.patch.object(srv, "RESUMES_DIR", root / "resumes"),
        mock.patch.object(srv, "ROOT", root),
        mock.patch.object(handler, "_send_json") as send,
    ):
        handler.do_GET()
    assert send.called, "GET /resume-latex did not respond"
    args = send.call_args[0]
    payload = args[0]
    status = args[1] if len(args) > 1 else 200
    return payload, status


def _load_server():
    spec = importlib.util.spec_from_file_location("jh_resume_latex_srv", SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jh_resume_latex_srv"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_resume_latex_source_uses_existing_or_dummy_sample(tmp_path):
    srv = _load_server()
    job_dir = tmp_path / "job-1"
    job_dir.mkdir()

    source, is_sample = srv._resume_latex_source(job_dir)
    assert is_sample is True
    assert "Candidate Name" in source
    assert "candidate@example.com" in source
    assert "\\usepackage[margin=0.75in]{geometry}" in source
    assert "\\setstretch{1.10}" in source
    assert "\\end{document}" in source

    existing = "\\documentclass{article}\\begin{document}Existing\\end{document}\n"
    (job_dir / "resume.tex").write_text(existing)
    source, is_sample = srv._resume_latex_source(job_dir)
    assert source == existing
    assert is_sample is False


def test_compile_resume_latex_fits_then_atomically_saves(tmp_path, monkeypatch):
    srv = _load_server()
    job_dir = tmp_path / "job-2"
    job_dir.mkdir()
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(list(cmd))
        if Path(cmd[0]).name == "tectonic":
            tex_path = Path(kwargs["cwd"]) / cmd[-1]
            tex_path.with_suffix(".pdf").write_bytes(b"%PDF-1.4 dummy")
            return subprocess.CompletedProcess(cmd, 0, "tectonic ok", "")
        assert "fit_resume_pages.py" in " ".join(cmd)
        return subprocess.CompletedProcess(cmd, 0, "fit ok", "")

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    source = (
        "\\documentclass{article}\n"
        "\\usepackage[margin=0.75in]{geometry}\n"
        "\\usepackage{setspace}\n"
        "\\setstretch{1.10}\n"
        "\\begin{document}Dummy\\end{document}\n"
    )
    result = srv._compile_resume_latex(job_dir, source)

    assert result["ok"] is True
    assert (job_dir / "resume.tex").read_text() == source
    assert (job_dir / "resume.pdf").read_bytes().startswith(b"%PDF")
    assert Path(commands[0][0]).name == "tectonic"
    assert "fit_resume_pages.py" in " ".join(commands[1])
    assert not list(job_dir.glob(".resume-edit-*"))


def test_compile_failure_keeps_previous_resume_and_returns_log(tmp_path, monkeypatch):
    srv = _load_server()
    job_dir = tmp_path / "job-3"
    job_dir.mkdir()
    (job_dir / "resume.tex").write_text("old tex")
    (job_dir / "resume.pdf").write_bytes(b"old pdf")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "! Undefined control sequence")

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    result = srv._compile_resume_latex(
        job_dir,
        "\\documentclass{article}\\begin{document}\\bad\\end{document}",
    )

    assert result["ok"] is False
    assert "Undefined control sequence" in result["error"]
    assert (job_dir / "resume.tex").read_text() == "old tex"
    assert (job_dir / "resume.pdf").read_bytes() == b"old pdf"
    assert not list(job_dir.glob(".resume-edit-*"))


def test_fit_best_effort_still_saves(tmp_path, monkeypatch):
    srv = _load_server()
    job_dir = tmp_path / "job-4"
    job_dir.mkdir()

    def fake_run(cmd, **kwargs):
        if Path(cmd[0]).name == "tectonic":
            tex_path = Path(kwargs["cwd"]) / cmd[-1]
            tex_path.with_suffix(".pdf").write_bytes(b"%PDF-1.4 fitted")
            return subprocess.CompletedProcess(cmd, 0, "tectonic ok", "")
        return subprocess.CompletedProcess(
            cmd, 1, "warn: still 3 page(s) at the tightest tested layout", ""
        )

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    result = srv._compile_resume_latex(
        job_dir,
        "\\documentclass{article}\\begin{document}Long\\end{document}\n",
    )
    assert result["ok"] is True
    assert "best-effort" in result["warning"].lower() or "two-page fit" in result["warning"].lower()
    assert (job_dir / "resume.pdf").read_bytes().startswith(b"%PDF")


def test_copy_tex_beside_pdf_persists_job_source(tmp_path):
    srv = _load_server()
    job_dir = tmp_path / "resumes" / "dummy-persist"
    dest_dir = tmp_path / "resumes" / "by_company"
    job_dir.mkdir(parents=True)
    dest_dir.mkdir(parents=True)
    tex = job_dir / "resume.tex"
    pdf = dest_dir / "FixtureCo_resume_99999.pdf"
    tex.write_text(_DUMMY_TEX, encoding="utf-8")
    pdf.write_bytes(b"%PDF-1.4 dummy")
    copied = srv._copy_tex_beside_pdf(pdf, tex_path=tex)
    assert copied == dest_dir / "FixtureCo_resume_99999.tex"
    assert copied.read_text(encoding="utf-8") == _DUMMY_TEX


def test_resume_latex_get_returns_on_disk_tex_for_saved_pdf(tmp_path):
    srv = _load_server()
    job_id = "dummy-job-tex"
    job_dir = tmp_path / "resumes" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "resume.tex").write_text(_DUMMY_TEX, encoding="utf-8")
    (job_dir / "resume.pdf").write_bytes(b"%PDF-1.4 dummy")
    job = {
        "id": job_id,
        "company": "Fixture Co",
        "title": "Dummy Role",
        "resume_path": f"resumes/{job_id}/resume.pdf",
        "resume_on_disk": True,
    }
    payload, status = _get_resume_latex(srv, job, root=tmp_path)
    assert status == 200, payload
    assert payload.get("ok") is not False
    assert payload.get("is_sample") is False
    assert payload.get("missing_tex") is not True
    assert payload.get("latex") == _DUMMY_TEX
    assert "JobSpecificDummyTex" in payload["latex"]
    assert "Candidate Name" not in payload["latex"]


def test_resume_latex_get_missing_tex_is_error_not_empty_or_sample(tmp_path):
    srv = _load_server()
    job_id = "dummy-pdf-only"
    job_dir = tmp_path / "resumes" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "resume.pdf").write_bytes(b"%PDF-1.4 dummy")
    job = {
        "id": job_id,
        "company": "Fixture Co",
        "title": "Dummy Role",
        "resume_path": f"resumes/{job_id}/resume.pdf",
        "resume_on_disk": True,
    }
    payload, status = _get_resume_latex(srv, job, root=tmp_path)
    latex = payload.get("latex")
    is_success_empty = status == 200 and not payload.get("error") and latex == ""
    is_sample = bool(payload.get("is_sample")) or (
        isinstance(latex, str) and "Candidate Name" in latex
    )
    assert not is_success_empty, payload
    assert not is_sample, payload
    assert payload.get("missing_tex") is True or status >= 400 or payload.get("ok") is False
    assert payload.get("error")
    assert "tex" in str(payload.get("error") or "").lower() or "latex" in str(
        payload.get("error") or ""
    ).lower()


def test_resume_latex_get_uses_sibling_tex_beside_pdf(tmp_path):
    srv = _load_server()
    job_id = "dummy-sibling-tex"
    job_dir = tmp_path / "resumes" / job_id
    job_dir.mkdir(parents=True)
    pdf = job_dir / "FixtureCo_resume_12345.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")
    pdf.with_suffix(".tex").write_text(_SIBLING_TEX, encoding="utf-8")
    job = {
        "id": job_id,
        "company": "Fixture Co",
        "title": "Dummy Role",
        "resume_path": f"resumes/{job_id}/{pdf.name}",
        "resume_on_disk": True,
    }
    payload, status = _get_resume_latex(srv, job, root=tmp_path)
    assert status == 200, payload
    assert payload.get("is_sample") is False
    assert payload.get("latex") == _SIBLING_TEX
    assert "SiblingCompileInput" in payload["latex"]
    assert "Candidate Name" not in payload["latex"]


def test_resume_latex_workspace_master_is_labeled_not_silent(tmp_path):
    srv = _load_server()
    job_id = "dummy-master-tex"
    job_dir = tmp_path / "resumes" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "resume.pdf").write_bytes(b"%PDF-1.4 dummy")
    (tmp_path / "resume.tex").write_text(_MASTER_TEX, encoding="utf-8")
    job = {
        "id": job_id,
        "company": "Fixture Co",
        "title": "Dummy Role",
        "resume_path": f"resumes/{job_id}/resume.pdf",
        "resume_on_disk": True,
    }
    payload, status = _get_resume_latex(srv, job, root=tmp_path)
    assert status == 200, payload
    assert payload.get("latex") == _MASTER_TEX
    assert payload.get("is_sample") is False
    assert payload.get("is_workspace_master") is True
    label = str(payload.get("source_label") or payload.get("path") or "")
    assert "resume.tex" in label


def test_resume_latex_job_tex_wins_over_workspace_master(tmp_path):
    srv = _load_server()
    job_id = "dummy-job-over-master"
    job_dir = tmp_path / "resumes" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "resume.tex").write_text(_DUMMY_TEX, encoding="utf-8")
    (job_dir / "resume.pdf").write_bytes(b"%PDF-1.4 dummy")
    (tmp_path / "resume.tex").write_text(_MASTER_TEX, encoding="utf-8")
    job = {
        "id": job_id,
        "company": "Fixture Co",
        "title": "Dummy Role",
        "resume_path": f"resumes/{job_id}/resume.pdf",
        "resume_on_disk": True,
    }
    payload, status = _get_resume_latex(srv, job, root=tmp_path)
    assert status == 200, payload
    assert payload.get("latex") == _DUMMY_TEX
    assert payload.get("is_workspace_master") is not True
    assert "WorkspaceMasterDummy" not in payload["latex"]


def test_latex_resume_ui_contract():
    app = APP_JS.read_text()
    html = INDEX_HTML.read_text()
    assert app.count(">Edit LaTeX</button>") == 1
    assert "Paste LaTeX…" not in app
    assert "mode = \"edit\"" not in app
    assert "Fit, recompile & save" in app
    assert "resume-latex-editor" in app
    assert "/resume-latex" in app
    assert ".resume-latex-panel" in html


def test_open_latex_handler_uses_api_body_not_empty_success():
    """Edit LaTeX must load the GET body; empty latex is an error, not success."""
    app = APP_JS.read_text()
    open_fn = _fn_body(app, "openResumeLatexEditor")
    snap_fn = _fn_body(app, "snapshotResumeLatexDraft")
    render_fn = _fn_body(app, "renderDossier")
    panel_fn = _fn_body(app, "renderResumeLatexPanel")

    assert "fetch(`/api/jobs/${encodeURIComponent(jobId)}/resume-latex`)" in open_fn
    assert "data.latex || \"\"" not in open_fn
    assert "missing_tex" in open_fn
    assert "typeof data.latex" in open_fn or "data.latex =" in open_fn
    # Snapshot must not copy the loading placeholder over a just-fetched body.
    assert "draft.loading" in snap_fn
    assert "draft.dirty" in snap_fn
    assert "snapshotResumeLatexDraft()" in render_fn
    assert "source_label" in open_fn or "source_label" in panel_fn
    assert "is_workspace_master" in open_fn or "isWorkspaceMaster" in open_fn


def test_open_latex_refetches_after_resume_identity_change():
    """After tailor/generate-resume-only, reopen/reload must not keep an empty cache."""
    app = APP_JS.read_text()
    open_fn = _fn_body(app, "openResumeLatexEditor")
    render_fn = _fn_body(app, "renderDossier")
    assert "resumeLatexDrafts.set(jobId" in open_fn
    assert "loadedFor" in open_fn
    assert "resumePreviewIdentity" in open_fn or "resumePreviewIdentity" in render_fn
    assert "openResumeLatexEditor" in render_fn or "loadedFor" in render_fn


def _with_tmp(fn):
    with tempfile.TemporaryDirectory() as td:
        fn(Path(td))


if __name__ == "__main__":
    class _MP:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)

    _with_tmp(test_resume_latex_source_uses_existing_or_dummy_sample)
    _with_tmp(lambda p: test_compile_resume_latex_fits_then_atomically_saves(p, _MP()))
    _with_tmp(lambda p: test_compile_failure_keeps_previous_resume_and_returns_log(p, _MP()))
    _with_tmp(lambda p: test_fit_best_effort_still_saves(p, _MP()))
    _with_tmp(test_copy_tex_beside_pdf_persists_job_source)
    test_latex_resume_ui_contract()
    test_open_latex_handler_uses_api_body_not_empty_success()
    test_open_latex_refetches_after_resume_identity_change()
    _with_tmp(test_resume_latex_get_returns_on_disk_tex_for_saved_pdf)
    _with_tmp(test_resume_latex_get_missing_tex_is_error_not_empty_or_sample)
    _with_tmp(test_resume_latex_get_uses_sibling_tex_beside_pdf)
    _with_tmp(test_resume_latex_workspace_master_is_labeled_not_silent)
    _with_tmp(test_resume_latex_job_tex_wins_over_workspace_master)
    print("OK test_resume_latex")

