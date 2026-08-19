#!/usr/bin/env python3
"""Generate-resume-only: Start payload + pipeline stops before fill.

Dummy fixtures only — never real applicant PII or tailored resumes.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import server as srv  # noqa: E402

APP_JS = HERE / "static" / "app.js"
INDEX_HTML = HERE / "static" / "index.html"


class _FakeStartHandler:
    """Drives ``_handle_start`` without standing up HTTP."""

    _job = srv.Handler._job
    _locked_job = srv.Handler._locked_job
    _handle_start = srv.Handler._handle_start

    def __init__(self):
        self.responses = []

    def _send_json(self, payload, status=200, **_kwargs):
        self.responses.append((status, payload))

    @property
    def last(self):
        return self.responses[-1]


def _dummy_job(job_id="resume-only-1", **extra):
    job = {
        "id": job_id,
        "status": "discovered",
        "status_detail": "Open",
        "title": "Dummy ML Role",
        "company": "Fixture Corp",
        "location": "Remote",
        "apply_url": "https://example.test/apply/dummy",
        "session_key": f"agent:job-hunter:job-{job_id}",
        "job_description": "Dummy JD for generate-only tests — not real PII.",
    }
    job.update(extra)
    return job


def test_parse_resume_only_accepts_resume_only_and_skip_fill():
    assert srv._parse_resume_only({}) is False
    assert srv._parse_resume_only(None) is False
    assert srv._parse_resume_only({"resume_only": True}) is True
    assert srv._parse_resume_only({"resume_only": "true"}) is True
    assert srv._parse_resume_only({"resume_only": False}) is False
    assert srv._parse_resume_only({"skip_fill": True}) is True
    assert srv._parse_resume_only({"skip_fill": "1"}) is True
    assert srv._parse_resume_only({"skip_fill": False}) is False
    # resume_only wins when both are present
    assert srv._parse_resume_only({"resume_only": False, "skip_fill": True}) is False


def test_handle_start_resume_only_forces_tailor_and_skips_fill_handoff():
    job_id = "start-resume-only"
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        jobs_file = td_path / "jobs.json"
        lock_file = td_path / "jobs.json.lock"
        jobs_file.write_text(json.dumps({"revision": 1, "jobs": [_dummy_job(job_id)]}))
        lock_file.touch()
        captured = {}

        class _CaptureThread:
            def __init__(self, target=None, args=(), kwargs=None, daemon=None):
                captured["target"] = target
                captured["args"] = args
                captured["kwargs"] = dict(kwargs or {})

            def start(self):
                captured["started"] = True

        handler = _FakeStartHandler()
        with (
            mock.patch.object(srv, "JOBS_FILE", jobs_file),
            mock.patch.object(srv, "JOBS_LOCK_FILE", lock_file),
            mock.patch.object(srv._jobs_lock_mod, "JOBS_FILE", jobs_file),
            mock.patch.object(srv._jobs_lock_mod, "LOCK_FILE", lock_file),
            mock.patch.object(srv, "_session_running_local", return_value=False),
            mock.patch.object(srv, "_prewarm_openclaw_browser_async"),
            mock.patch.object(srv, "clear_fill_activity"),
            mock.patch.object(srv, "append_fill_activity"),
            mock.patch.object(
                srv, "partyrock_url", return_value="https://partyrock.example.test/"
            ),
            mock.patch.object(srv.threading, "Thread", _CaptureThread),
        ):
            handler._handle_start(
                job_id,
                {
                    "test_mode": True,
                    "resume_only": True,
                    "skip_partyrock": True,
                    "force_partyrock": False,
                },
            )

        status, payload = handler.last
        assert status == 200, payload
        assert payload.get("ok") is True
        assert payload.get("resume_only") is True
        assert payload.get("skip_partyrock") is False
        assert payload.get("force_partyrock") is True
        assert payload.get("fill_after_tailor") == "none"
        assert captured.get("started") is True
        kw = captured["kwargs"]
        assert kw.get("resume_only") is True
        assert kw.get("skip_partyrock") is False
        assert kw.get("force_partyrock") is True
        assert kw.get("test_mode") is True

        saved = json.loads(jobs_file.read_text())["jobs"][0]
        assert saved["status"] == "tailoring"
        assert "fill" not in (saved.get("status_detail") or "").lower() or "no fill" in (
            saved.get("status_detail") or ""
        ).lower() or "resume only" in (saved.get("status_detail") or "").lower()


def test_skip_path_resume_only_does_not_start_fill():
    job = _dummy_job("j-skip")
    resume = srv.ROOT / "resumes" / "j-skip" / "resume.pdf"
    rec = {
        "read_jobs": mock.MagicMock(return_value={"jobs": [job]}),
        "write_jobs": mock.MagicMock(),
        "resolve_job_resume_file": mock.MagicMock(return_value=resume),
        "clear_fill_activity": mock.MagicMock(),
        "append_fill_activity": mock.MagicMock(),
        "pipeline_milestone": mock.MagicMock(),
        "run_hybrid_fill_dummy": mock.MagicMock(),
        "_publish_resume_by_company": mock.MagicMock(return_value=None),
        "_job_fill_aborted": mock.MagicMock(return_value=False),
        "_pipeline_stop_if_aborted": mock.MagicMock(return_value=False),
    }

    @contextmanager
    def _fake_locked(**_kwargs):
        yield {"jobs": [job]}

    patches = [mock.patch.object(srv, name, m) for name, m in rec.items()]
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8], patches[9], \
         mock.patch.object(srv, "locked_jobs_for_write", _fake_locked):
        srv._run_tailor_then_fill_body(
            "j-skip",
            test_mode=True,
            skip_partyrock=True,
            force_partyrock=False,
            resume_only=True,
        )
    rec["run_hybrid_fill_dummy"].assert_not_called()
    ms = rec["pipeline_milestone"].call_args
    assert ms.kwargs.get("status") == "resume_ready"
    detail = (ms.kwargs.get("status_detail") or ms.kwargs.get("detail") or "").lower()
    assert "fill" in detail or "ready" in detail


def test_pipeline_resume_only_stops_before_fill():
    """Stubbed PartyRock/tectonic: generate-only publishes PDF and does not fill."""
    job_id = "pipe-resume-only"
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        jobs_file = td_path / "jobs.json"
        lock_file = td_path / "jobs.json.lock"
        resumes = td_path / "resumes" / job_id
        resumes.mkdir(parents=True)
        (resumes / "jd_full.txt").write_text(
            "Dummy job description for generate-only pipeline test.\n",
            encoding="utf-8",
        )
        jobs_file.write_text(
            json.dumps(
                {
                    "revision": 1,
                    "jobs": [
                        _dummy_job(
                            job_id,
                            status="tailoring",
                            status_detail="Waiting on resume from PartyRock…",
                            fill_gen=1,
                        )
                    ],
                }
            )
        )
        lock_file.touch()

        def _fake_subprocess(cmd, log_name, timeout_s, **kwargs):
            log_path = td_path / "logs"
            log_path.mkdir(exist_ok=True)
            out = log_path / log_name
            out.write_text("ok\n", encoding="utf-8")
            cmd0 = " ".join(str(c) for c in cmd)
            if "tailor_resume" in cmd0 or "TAILOR" in cmd0.upper():
                (resumes / "resume.tex").write_text(
                    "\\documentclass{article}\\begin{document}Dummy\\end{document}\n",
                    encoding="utf-8",
                )
                return 0, out
            if "tectonic" in cmd0:
                (resumes / "resume.pdf").write_bytes(
                    b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ntrailer<<>>\n%%EOF\n"
                )
                return 0, out
            if "fit_resume_pages" in cmd0:
                return 0, out
            return 0, out

        fill_calls: list[tuple] = []

        def _fake_fill(*args, **kwargs):
            fill_calls.append((args, kwargs))

        done = threading.Event()
        err: list[BaseException] = []

        def _run() -> None:
            try:
                with (
                    mock.patch.object(srv, "JOBS_FILE", jobs_file),
                    mock.patch.object(srv, "JOBS_LOCK_FILE", lock_file),
                    mock.patch.object(srv, "ROOT", td_path),
                    mock.patch.object(srv, "RESUMES_DIR", td_path / "resumes"),
                    mock.patch.object(srv, "INBOUND_MEDIA_DIR", td_path / "inbound"),
                    mock.patch.object(srv._jobs_lock_mod, "JOBS_FILE", jobs_file),
                    mock.patch.object(srv._jobs_lock_mod, "LOCK_FILE", lock_file),
                    mock.patch.object(srv, "_run_subprocess_step", _fake_subprocess),
                    mock.patch.object(
                        srv, "_ensure_openclaw_managed_browser", return_value={}
                    ),
                    mock.patch.object(srv, "close_job_partyrock_tab", return_value=None),
                    mock.patch.object(
                        srv,
                        "partyrock_url",
                        return_value="https://partyrock.example.test/",
                    ),
                    mock.patch.object(
                        srv, "partyrock_mode_label", return_value="Testing"
                    ),
                    mock.patch.object(srv, "run_hybrid_fill_dummy", _fake_fill),
                    mock.patch.object(
                        srv, "_publish_resume_by_company", return_value=None
                    ),
                    mock.patch.object(srv, "_cleanup_old_inbound_resumes"),
                    mock.patch.object(srv, "run_agent_message"),
                ):
                    tok = srv._bind_fill_run_ctx(job_id, 1)
                    try:
                        srv._run_tailor_then_fill_body(
                            job_id,
                            test_mode=True,
                            skip_partyrock=False,
                            force_partyrock=True,
                            restore_status="discovered",
                            fill_run_gen=1,
                            resume_only=True,
                        )
                    finally:
                        srv._fill_run_ctx.reset(tok)
            except BaseException as e:
                err.append(e)
            finally:
                done.set()

        t = threading.Thread(target=_run, name="resume-only-pipe", daemon=True)
        t.start()
        if not done.wait(15.0):
            raise AssertionError("generate-only pipeline hung (>15s)")
        t.join(timeout=1.0)
        if err:
            raise err[0]

        saved = json.loads(jobs_file.read_text(encoding="utf-8"))
        job = next(j for j in saved["jobs"] if j["id"] == job_id)
        assert not fill_calls, f"fill must not start on generate-only: {fill_calls}"
        assert job.get("resume_path"), job
        assert job.get("resume_on_disk") is True, job
        assert job["status"] == "resume_ready", job
        assert job["status"] not in srv.IN_PROGRESS_STATUSES, job
        assert job["status"] != "discovered", job
        from stats_aggregate import queue_bucket

        assert queue_bucket(job["status"]) == "progress", job
        detail = (job.get("status_detail") or "").lower()
        assert "fill when you want" in detail or "resume ready" in detail, job
        assert job["status"] not in ("navigating", "filling", "tailoring")


def test_persist_compiled_resume_only_does_not_prepare_fill():
    job_id = "persist-resume-only"
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        jobs_file = td_path / "jobs.json"
        lock_file = td_path / "jobs.json.lock"
        resumes = td_path / "resumes" / job_id
        resumes.mkdir(parents=True)
        resume_pdf = resumes / "resume.pdf"
        resume_pdf.write_bytes(
            b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
        )
        jobs_file.write_text(
            json.dumps(
                {
                    "revision": 1,
                    "jobs": [
                        _dummy_job(
                            job_id,
                            status="tailoring",
                            fill_gen=2,
                        )
                    ],
                }
            )
        )
        lock_file.touch()
        with (
            mock.patch.object(srv, "JOBS_FILE", jobs_file),
            mock.patch.object(srv, "JOBS_LOCK_FILE", lock_file),
            mock.patch.object(srv, "ROOT", td_path),
            mock.patch.object(srv._jobs_lock_mod, "JOBS_FILE", jobs_file),
            mock.patch.object(srv._jobs_lock_mod, "LOCK_FILE", lock_file),
        ):
            tok = srv._fill_run_ctx.set((job_id, 2))
            try:
                srv._persist_compiled_resume_after_tectonic(
                    job_id,
                    resume_pdf=resume_pdf,
                    compile_ok=True,
                    compile_exit=0,
                    compile_log=td_path / "tectonic_dummy.log",
                    resume_only=True,
                )
            finally:
                srv._fill_run_ctx.reset(tok)
        saved = json.loads(jobs_file.read_text(encoding="utf-8"))
        job = next(j for j in saved["jobs"] if j["id"] == job_id)
        assert job["status"] == "tailoring", job
        assert "Preparing fill" not in (job.get("status_detail") or "")
        assert job.get("resume_path")


def test_app_js_generate_resume_only_menu_and_payload():
    src = APP_JS.read_text(encoding="utf-8")
    popover = src.split("function renderFillPopover", 1)[1].split(
        "function renderResumePopover", 1
    )[0]
    assert "Generate resume only" in popover
    assert "no form fill" in popover.lower() or "then stop" in popover.lower()
    assert 'id="fill-menu-btn"' in src
    assert "toggleFillMenu" in src
    start_mode = src.split("async function startJobFillMode", 1)[1].split(
        "\nfunction timelineKind", 1
    )[0]
    assert "resume-only" in start_mode
    assert "resumeOnly: true" in start_mode or "resumeOnly:true" in start_mode
    start_fn = src.split("async function startJob(jobId", 1)[1].split(
        "\nfunction toggleTestMode(", 1
    )[0]
    assert "resume_only:" in start_fn
    # Face click keeps Fill / Tailor + fill; generate-only is a menu action.
    face = src.split("function fillModeLabel", 1)[1].split("function fillFaceLabel", 1)[0]
    assert "Generate resume only" not in face
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "fill-menu-btn" in html
    assert "#fill-wrap > #fill-btn" in html


if __name__ == "__main__":
    test_parse_resume_only_accepts_resume_only_and_skip_fill()
    test_handle_start_resume_only_forces_tailor_and_skips_fill_handoff()
    test_skip_path_resume_only_does_not_start_fill()
    test_pipeline_resume_only_stops_before_fill()
    test_persist_compiled_resume_only_does_not_prepare_fill()
    test_app_js_generate_resume_only_menu_and_payload()
    print("OK test_resume_only")
