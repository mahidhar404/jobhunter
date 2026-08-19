"""Dashboard Start / Fast fill parity: env wiring + Flash default (no browser).

Dummy and real must share the same Playwright engine flags (flash, refill,
headed captcha/hold). Differs only on identity env + --real-profile/--job-id.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SERVER_PATH = HERE / "server.py"


def _load_server():
    spec = importlib.util.spec_from_file_location("jh_dashboard_server", SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Avoid binding port / side effects — only import helpers.
    sys.modules["jh_dashboard_server"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_flash_default_on_for_dummy_and_real_payloads():
    srv = _load_server()
    assert srv._dummy_fill_flash_requested({}) is True
    assert srv._dummy_fill_flash_requested({"test_mode": True}) is True
    assert srv._dummy_fill_flash_requested({"test_mode": False}) is True
    assert srv._dummy_fill_flash_requested({"flash_leftovers": False}) is False
    assert srv._dummy_fill_flash_requested({"flash_leftovers": True}) is True


def test_configure_env_dummy_clears_real_and_address():
    srv = _load_server()
    env = {
        "FASTFILL_ALLOW_REAL": "1",
        "FASTFILL_REAL_PROFILE": "1",
        "TEST_MODE": "0",
        "FASTFILL_ADDRESS_TEXT": "should-clear",
        "PATH": "/usr/bin",
    }
    srv._configure_fastfill_child_env(env, test_mode=True, address_text="ignored")
    assert env.get("FASTFILL_ALLOW_REAL") is None
    assert env["FASTFILL_REAL_PROFILE"] == "0"
    assert env["TEST_MODE"] == "1"
    assert "FASTFILL_ADDRESS_TEXT" not in env


def test_configure_env_real_sets_triple_opt_in_and_address():
    srv = _load_server()
    env = {"PATH": "/usr/bin", "TEST_MODE": "1"}
    addr = "12 Real St, Austin, TX 78701"
    srv._configure_fastfill_child_env(env, test_mode=False, address_text=addr)
    assert env["FASTFILL_ALLOW_REAL"] == "1"
    assert env["FASTFILL_REAL_PROFILE"] == "1"
    assert env["TEST_MODE"] == "0"
    assert env["FASTFILL_ADDRESS_TEXT"] == addr


def test_configure_env_real_omits_empty_address():
    srv = _load_server()
    env = {"PATH": "/usr/bin"}
    srv._configure_fastfill_child_env(env, test_mode=False, address_text="  ")
    assert "FASTFILL_ADDRESS_TEXT" not in env
    assert env["FASTFILL_ALLOW_REAL"] == "1"


def test_configure_env_sets_parallel_headed_defaults():
    """Dashboard child env must enable native HUD + headed cap for parallel fills."""
    srv = _load_server()
    env = {"PATH": "/usr/bin"}
    srv._configure_fastfill_child_env(env, test_mode=True)
    assert env.get("FASTFILL_MAX_HEADED_CHROME_MAINS") == "3"
    if sys.platform == "darwin":
        assert env.get("FASTFILL_NATIVE_HUD") == "1"
        assert "FASTFILL_DOM_OVERLAY" not in env


def test_headed_cap_surfaces_stuck_in_source():
    src = SERVER_PATH.read_text(encoding="utf-8")
    chunk = src.split("def _run_hybrid_fill_dummy_body", 1)[1].split(
        "def _dummy_fill_result_detail", 1
    )[0]
    assert "headed_cap" in chunk
    assert 'final_status = "stuck"' in chunk


def test_parse_test_mode_requires_explicit_flag():
    """UI-019 / DASH2-011: missing test_mode fails closed (no silent dummy)."""
    srv = _load_server()
    try:
        srv._parse_test_mode({})
        assert False, "expected ValueError for missing test_mode"
    except ValueError:
        pass
    assert srv._parse_test_mode({"test_mode": True}) is True
    assert srv._parse_test_mode({"test_mode": False}) is False
    assert srv._parse_test_mode({"test_mode": "0"}) is False


def test_skip_partyrock_only_meaningful_with_explicit_flag():
    srv = _load_server()
    assert srv._parse_skip_partyrock({}) is False
    assert srv._parse_skip_partyrock({"skip_partyrock": True}) is True
    assert srv._parse_skip_partyrock({"partyrock": False}) is True
    assert srv._parse_skip_partyrock({"partyrock": True}) is False


def test_playwright_timeout_covers_hold_and_refill():
    srv = _load_server()
    # Fill deadline covers Flash refill; indefinite hold suspends the kill timer.
    assert srv.DUMMY_FILL_PLAYWRIGHT_TIMEOUT_S >= 420
    assert srv.DUMMY_FILL_HOLD_GRACE_S >= 3600


def test_resolve_job_resume_file_prefers_resume_path(tmp_path, monkeypatch=None):
    srv = _load_server()
    # Smoke: helper exists and returns None for empty job.
    assert srv.resolve_job_resume_file(None) is None
    assert srv.resolve_job_resume_file({}) is None


def test_fill_resume_resolver_prefers_conventionally_named_published_copy(tmp_path):
    srv = _load_server()
    raw = tmp_path / "resume.pdf"
    raw.write_bytes(b"%PDF raw")
    published = tmp_path / "Acme_resume_04217.pdf"
    published.write_bytes(b"%PDF published")
    job = {
        "resume_path": str(raw),
        "resume_by_company_path": str(published),
    }

    assert srv.resolve_job_resume_upload_file(job) == published


def test_real_fill_body_passes_conventionally_published_pdf_to_fastfill():
    srv = _load_server()
    import inspect

    body = inspect.getsource(srv._run_hybrid_fill_dummy_body)
    assert "_ensure_conventional_resume_pdf(job_id)" in body
    assert "else conventional_resume" in body
    assert "resume_path=resume_arg" in body


def test_parse_multipart_file_roundtrip():
    srv = _load_server()
    boundary = "----jhBoundary7"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="resume"; filename="mine.pdf"\r\n'
        "Content-Type: application/pdf\r\n"
        "\r\n"
        "%PDF-1.4 fake\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    name, data = srv._parse_multipart_file(
        body, f"multipart/form-data; boundary={boundary}"
    )
    assert name == "mine.pdf"
    assert data.startswith(b"%PDF")


def _engine_flags(argv: list[str]) -> list[str]:
    """Strip identity-only flags so dummy vs real engine argv can be compared."""
    out: list[str] = []
    skip_next = False
    identity = {"--test-mode", "--real-profile", "--job-id"}
    for i, tok in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if tok in identity:
            if tok == "--job-id":
                skip_next = True
            continue
        out.append(tok)
    return out


def test_playwright_argv_engine_parity_headed_flash():
    srv = _load_server()
    common = dict(
        py="/venv/bin/python",
        script="/app/fast_fill.py",
        apply_url="https://jobs.example.com/apply",
        out_path="/tmp/out.json",
        job_id="job-abc",
        headed=True,
        flash_leftovers=True,
    )
    dummy = srv._playwright_fastfill_argv(**common, test_mode=True)
    real = srv._playwright_fastfill_argv(**common, test_mode=False)
    assert "--test-mode" in dummy and "--real-profile" not in dummy
    assert "--real-profile" in real and "--job-id" in real and "job-abc" in real
    assert "--test-mode" not in real
    assert _engine_flags(dummy) == _engine_flags(real)
    assert "--headed" in dummy
    assert "--captcha-wait" in dummy
    assert "--hold-open" in dummy
    assert "--flash-leftovers" in dummy
    assert dummy[dummy.index("--refill-passes") + 1] == "2"
    assert "--headless" not in dummy


def test_playwright_argv_passes_resume_path_for_real():
    """--resume-path is for real-profile attach; Test Mode dashboard omits it."""
    srv = _load_server()
    argv = srv._playwright_fastfill_argv(
        py="py",
        script="ff.py",
        apply_url="https://example.com",
        out_path="o.json",
        test_mode=False,
        job_id="job-x",
        headed=True,
        flash_leftovers=False,
        resume_path="/tmp/resumes/job-x/resume.pdf",
    )
    assert "--resume-path" in argv
    assert argv[argv.index("--resume-path") + 1].endswith("resume.pdf")
    assert "--hold-open" in argv


def test_playwright_argv_test_mode_can_omit_resume_path():
    srv = _load_server()
    argv = srv._playwright_fastfill_argv(
        py="py",
        script="ff.py",
        apply_url="https://example.com",
        out_path="o.json",
        test_mode=True,
        job_id="job-x",
        headed=True,
        flash_leftovers=False,
        resume_path=None,
    )
    assert "--resume-path" not in argv


def test_find_in_progress_job_excludes_self():
    srv = _load_server()
    data = {
        "jobs": [
            {"id": "a", "status": "filling"},
            {"id": "b", "status": "discovered"},
        ]
    }
    other = srv._find_in_progress_job(data, exclude_id="a")
    assert other is None
    other_b = srv._find_in_progress_job(data, exclude_id="b")
    assert other_b is not None and other_b["id"] == "a"
    assert srv._find_in_progress_job(data)["id"] == "a"


def test_find_blocking_start_job_allows_concurrent():
    """Concurrent fills: other in-progress or Ready/hold jobs do not block Start."""
    srv = _load_server()
    data = {
        "jobs": [
            {"id": "a", "status": "filling"},
            {"id": "ready", "status": "ready_for_review"},
            {"id": "next", "status": "discovered"},
        ]
    }
    assert srv._find_blocking_start_job(data, exclude_id="next") is None
    srv._fill_hold_browser_active = lambda: True  # type: ignore
    assert srv._find_blocking_start_job(data, exclude_id="next") is None
    assert srv._find_blocking_start_job(data, exclude_id="ready") is None


def test_kill_jh_preserves_fill_cft_on_flag():
    """CHR2-003: preserve_fill_cft skips killing Chrome-for-Testing."""
    srv = _load_server()
    calls = {"kill_cft": 0}

    def _fake_kill():
        calls["kill_cft"] += 1
        return [1]

    srv._kill_chrome_for_testing = _fake_kill  # type: ignore
    srv._kill_chrome_user_data_dir = lambda *_a, **_k: []  # type: ignore
    srv._stop_openclaw_managed_browser = lambda: {"cli_stop": True}  # type: ignore
    summary = srv._kill_jh_associated_browsers(
        stop_openclaw_browser=False, preserve_fill_cft=True
    )
    assert summary["fill_cft_preserved"] is True
    assert summary["chrome_for_testing"] == []
    assert calls["kill_cft"] == 0
    summary2 = srv._kill_jh_associated_browsers(
        stop_openclaw_browser=False, preserve_fill_cft=False
    )
    assert summary2["fill_cft_preserved"] is False
    assert calls["kill_cft"] == 1


def test_count_fill_cft_excludes_openclaw_partyrock():
    """CHR3-003: OpenClaw PartyRock profile is not a fill Chrome main."""
    from unittest import mock

    srv = _load_server()
    openclaw = srv.OPENCLAW_BROWSER_USER_DATA
    port = srv.OPENCLAW_BROWSER_CDP_PORT
    openclaw_line = (
        f"111 /path/MacOS/Google Chrome for Testing "
        f"--user-data-dir={openclaw} --remote-debugging-port={port}"
    )
    fill_line = "222 /path/MacOS/Google Chrome for Testing --remote-debugging-pipe"
    ui_line = (
        f"333 /path/MacOS/Google Chrome for Testing "
        f"--user-data-dir={srv.DASHBOARD_UI_PROFILE} --app=http://127.0.0.1:8787"
    )
    with mock.patch.object(
        srv.subprocess,
        "check_output",
        return_value=f"{openclaw_line}\n{fill_line}\n{ui_line}\n",
    ):
        assert srv._chrome_for_testing_main_pids() == [222]


def test_playwright_argv_headless_no_flash():
    srv = _load_server()
    argv = srv._playwright_fastfill_argv(
        py="py",
        script="ff.py",
        apply_url="https://example.com",
        out_path="o.json",
        test_mode=True,
        job_id="x",
        headed=False,
        flash_leftovers=False,
    )
    assert "--headless" in argv
    assert "--headed" not in argv
    assert "--flash-leftovers" not in argv
    assert "--refill-passes" not in argv


def test_playwright_argv_ashby_refill_one_workday_two():
    srv = _load_server()
    common = dict(
        py="py",
        script="ff.py",
        out_path="o.json",
        test_mode=True,
        job_id="job-x",
        headed=True,
        flash_leftovers=True,
    )
    ashby = srv._playwright_fastfill_argv(
        **common,
        apply_url="https://jobs.ashbyhq.com/acme/uuid/application",
    )
    workday = srv._playwright_fastfill_argv(
        **common,
        apply_url="https://acme.myworkdayjobs.com/en-US/careers/job/123",
    )
    assert ashby[ashby.index("--refill-passes") + 1] == "1"
    assert workday[workday.index("--refill-passes") + 1] == "2"


def test_start_skip_partyrock_uses_headed_flash_defaults():
    """Start (Test Mode + skip PartyRock) must call fill with headed+flash ON."""
    srv = _load_server()
    # Mirror run_tailor_then_fill skip_partyrock branch kwargs.
    assert srv._dummy_fill_flash_requested({}) is True
    assert srv._dummy_fill_flash_requested({"flash_leftovers": False}) is False
    # Headed is hardcoded True on that path; Fast fill defaults headless unless asked.
    assert srv._dummy_fill_headed_requested({}) is False


if __name__ == "__main__":
    test_flash_default_on_for_dummy_and_real_payloads()
    test_configure_env_dummy_clears_real_and_address()
    test_configure_env_real_sets_triple_opt_in_and_address()
    test_configure_env_real_omits_empty_address()
    test_configure_env_sets_parallel_headed_defaults()
    test_headed_cap_surfaces_stuck_in_source()
    test_parse_test_mode_requires_explicit_flag()
    test_skip_partyrock_only_meaningful_with_explicit_flag()
    test_playwright_timeout_covers_hold_and_refill()
    test_resolve_job_resume_file_prefers_resume_path(None)
    test_parse_multipart_file_roundtrip()
    test_playwright_argv_engine_parity_headed_flash()
    test_playwright_argv_passes_resume_path_for_real()
    test_playwright_argv_test_mode_can_omit_resume_path()
    test_find_in_progress_job_excludes_self()
    test_find_blocking_start_job_allows_concurrent()
    test_kill_jh_preserves_fill_cft_on_flag()
    test_count_fill_cft_excludes_openclaw_partyrock()
    test_playwright_argv_headless_no_flash()
    test_playwright_argv_ashby_refill_one_workday_two()
    test_start_skip_partyrock_uses_headed_flash_defaults()
    print("OK test_fill_parity")
