#!/usr/bin/env python3
"""JD edit: persist jd_full.txt + jobs.json preview; blue copy/edit icons.

Local jobs files only — never submits an application. Dummy JD text only.
"""
from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import server as srv  # noqa: E402

STATIC = HERE / "static"
APP_JS = STATIC / "app.js"
INDEX_HTML = STATIC / "index.html"


def _fn_body(src: str, name: str) -> str:
    token = f"function {name}("
    start = src.find(token)
    if start < 0:
        return ""
    end = src.find("\nfunction ", start + 1)
    if end < 0:
        return src[start:]
    return src[start:end]


def test_trim_job_description_preview():
    short = "Short dummy JD for a fictional role."
    assert srv._trim_job_description_preview(short) == short
    long = ("word " * 200).strip()
    preview = srv._trim_job_description_preview(long)
    assert len(preview) < len(long)
    assert preview.endswith(" … [full text in resumes/<id>/jd_full.txt]")
    assert long.startswith(preview.split(" … ")[0])


def test_validated_job_description_accepts_aliases_and_rejects_bad():
    assert srv._validated_job_description({"job_description": "  Hello  "}) == "  Hello  "
    assert srv._validated_job_description({"description": "Alt key"}) == "Alt key"
    try:
        srv._validated_job_description({})
    except ValueError as error:
        assert "required" in str(error)
    else:
        raise AssertionError("missing JD field was accepted")
    try:
        srv._validated_job_description({"job_description": 12})
    except ValueError as error:
        assert "string" in str(error)
    else:
        raise AssertionError("non-string JD was accepted")
    try:
        srv._validated_job_description({"job_description": "x" * (srv._JD_MAX_CHARS + 1)})
    except ValueError as error:
        assert "too long" in str(error)
    else:
        raise AssertionError("oversized JD was accepted")


def test_persist_job_description_writes_full_and_preview():
    with tempfile.TemporaryDirectory() as td:
        resumes = Path(td) / "resumes"
        job = {"id": "dummy-role", "job_description": "old preview"}
        body = ("alpha " * 120).strip()
        with mock.patch.object(srv, "RESUMES_DIR", resumes):
            path = srv.persist_job_description(job, body)
        assert path.read_text(encoding="utf-8") == body
        assert job["job_description"].endswith("[full text in resumes/<id>/jd_full.txt]")
        assert "alpha" in job["job_description"]


def _handle_jd(job_id, payload, data, resumes):
    responses = []
    handler = object.__new__(srv.Handler)
    handler._send_json = lambda body, status=200: responses.append((body, status))

    @contextmanager
    def _fake_locked(*, allow_purge=False):
        yield data

    with (
        mock.patch.object(srv, "RESUMES_DIR", resumes),
        mock.patch.object(srv, "locked_jobs_for_write", _fake_locked),
    ):
        handler._handle_jd_edit(job_id, payload)
    return responses


def test_jd_edit_handler_round_trip():
    data = {
        "jobs": [
            {
                "id": "open-role",
                "status": "new",
                "title": "Dummy Engineer",
                "company": "Fixture Co",
                "job_description": "old preview",
                "resume_on_disk": False,
                "timeline": [],
            }
        ]
    }
    edited = "Edited dummy JD. Build fictional models. No real PII."
    with tempfile.TemporaryDirectory() as td:
        resumes = Path(td) / "resumes"
        responses = _handle_jd("open-role", {"job_description": edited}, data, resumes)
        persisted = data["jobs"][0]
        assert persisted["job_description"] == edited
        assert (resumes / "open-role" / "jd_full.txt").read_text(encoding="utf-8") == edited
        assert responses[0][1] == 200
        body, _status = responses[0]
        assert body["ok"] is True
        assert body["job_description"] == edited
        assert body["source"] == "jd_full.txt"
        assert body["job"]["id"] == "open-role"
        assert body["job"]["has_description"] is True
        assert persisted["timeline"][-1]["event"] == "jd_edit"


def test_jd_edit_handler_description_alias_and_reload():
    data = {
        "jobs": [
            {
                "id": "reload-role",
                "status": "new",
                "title": "Dummy Analyst",
                "company": "Fixture Co",
                "job_description": "",
                "resume_on_disk": False,
                "timeline": [],
            }
        ]
    }
    edited = "Round-trip dummy JD after reload."
    with tempfile.TemporaryDirectory() as td:
        resumes = Path(td) / "resumes"
        responses = _handle_jd("reload-role", {"description": edited}, data, resumes)
        assert responses[0][1] == 200
        with mock.patch.object(srv, "RESUMES_DIR", resumes):
            raw, source = srv.load_raw_job_description(data["jobs"][0])
        assert source == "jd_full.txt"
        assert raw == edited
        assert srv.sanitize_job_description_for_display(raw) == edited


def test_jd_edit_rejects_invalid_and_missing_job():
    responses = []
    handler = object.__new__(srv.Handler)
    handler._send_json = lambda body, status=200: responses.append((body, status))
    handler._handle_jd_edit("any", {"job_description": 1})
    assert responses[0][1] == 400

    data = {
        "jobs": [
            {
                "id": "other",
                "status": "new",
                "job_description": "keep me",
                "resume_on_disk": False,
                "timeline": [],
            }
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        resumes = Path(td) / "resumes"
        missing = _handle_jd(
            "missing-id",
            {"job_description": "should not write"},
            data,
            resumes,
        )
        assert missing[0][1] == 404
        assert data["jobs"][0]["job_description"] == "keep me"
        assert not (resumes / "missing-id" / "jd_full.txt").exists()


def test_jd_action_is_registered():
    assert "jd" in srv.Handler._JOB_ACTION_POST
    method, takes_payload = srv.Handler._JOB_ACTION_POST["jd"]
    assert method == "_handle_jd_edit"
    assert takes_payload is True


def test_jd_edit_ui_blue_copy_and_edit_icons():
    app = APP_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "openJdEditor(" in app
    assert "saveJdEdit(" in app
    assert "cancelJdEdit(" in app
    assert "jdToolbarHtml(" in app
    assert "jd-edit-btn" in app
    assert "jd-edit-textarea" in app
    assert "/jd" in app
    assert "Edit job description" in app
    assert "APPLY_URL_EDIT_ICON_SVG" in _fn_body(app, "jdEditButtonHtml")
    assert "JD_COPY_ICON_SVG" in _fn_body(app, "jdCopyButtonHtml")
    assert "${jdToolbarHtml(job)}" in app
    assert ".jd-copy-btn" in html
    assert ".jd-edit-btn" in html
    assert ".jd-toolbar" in html
    copy_edit_rule = html.split(".jd-copy-btn,\n  .jd-edit-btn {", 1)[1].split("}", 1)[0]
    assert "color: var(--blue)" in copy_edit_rule
    assert "border: 1px solid var(--blue-edge)" in copy_edit_rule
    hover = html.split(".jd-copy-btn:hover,\n  .jd-edit-btn:hover", 1)[1].split("}", 1)[0]
    assert "#5aadff" in hover
    copied = html.split(".jd-copy-btn.copied", 1)[1].split("}", 1)[0]
    assert "var(--green)" in copied
    assert ">Copy job description</button>" not in app
    assert ">Edit job description</button>" not in app


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("OK test_jd_edit")
