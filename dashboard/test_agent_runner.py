#!/usr/bin/env python3
"""Tests for the OpenClaw-free DeepSeek agent runner.

Focus: the graceful no-key `stuck` fallback (the core safety guarantee), key
loading precedence, event emission, cancellation, and the shell safety
denylist. No network is used — the model call path is never exercised without
a key, and we don't configure one.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import agent_runner as ar  # noqa: E402


def _clear_key_env(monkeyenv: dict) -> None:
    for k in ("OPENAI_COMPATIBLE_API_KEY", "DEEPSEEK_API_KEY"):
        monkeyenv.pop(k, None)


def test_no_key_returns_stuck_fallback_and_emits_ended():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with mock.patch.object(ar, "ROOT", tmp), \
             mock.patch.object(ar, "LOGS_DIR", tmp / "logs"), \
             mock.patch.dict(ar.os.environ, {}, clear=True):
            log_path = tmp / "logs" / "agent_turn_job-1.log"
            rc = ar.run_turn("agent:job-hunter:job-1", "do the thing",
                             log_path=log_path, timeout_s=60)
            assert rc == ar.EXIT_NO_KEY
            events = ar.read_events("agent:job-hunter:job-1")
            assert any(e["event"] == "session.ended" for e in events)
            assert "DEEPSEEK_API_KEY" in log_path.read_text()


def test_key_loading_precedence_env_first():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "web_keys.json").write_text(json.dumps(
            {"DEEPSEEK_API_KEY": "from-web-keys"}))
        with mock.patch.object(ar, "ROOT", tmp), \
             mock.patch.dict(ar.os.environ, {"DEEPSEEK_API_KEY": "from-env"}, clear=True):
            key, base, model = ar.load_deepseek_config()
            assert key == "from-env"
            assert base.endswith("deepseek.com/v1")
            assert model == "deepseek-v4-flash"


def test_key_loading_from_web_keys_and_credentials():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # web_keys.json nested value.
        (tmp / "web_keys.json").write_text(json.dumps(
            {"llm": {"deepseek_api_key": "nested-web-keys-key"}}))
        with mock.patch.object(ar, "ROOT", tmp), \
             mock.patch.dict(ar.os.environ, {}, clear=True):
            key, _, _ = ar.load_deepseek_config()
            assert key == "nested-web-keys-key"

        # credentials.json fallback when web_keys has no key.
        (tmp / "web_keys.json").write_text(json.dumps({"sites": {}}))
        (tmp / "credentials.json").write_text(json.dumps(
            {"DEEPSEEK_API_KEY": "cred-key"}))
        with mock.patch.object(ar, "ROOT", tmp), \
             mock.patch.dict(ar.os.environ, {}, clear=True):
            key, _, _ = ar.load_deepseek_config()
            assert key == "cred-key"


def test_key_loading_from_dotenv_and_secrets():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / ".env").write_text('export DEEPSEEK_API_KEY="dotenv-key"\n')
        with mock.patch.object(ar, "ROOT", tmp), \
             mock.patch.dict(ar.os.environ, {}, clear=True):
            key, _, _ = ar.load_deepseek_config()
            assert key == "dotenv-key"


def test_shell_denylist_blocks_submit_and_destructive():
    assert ar._shell_is_blocked("python3 scripts/submit_form.py") is not None
    assert ar._shell_is_blocked("solve the captcha now") is not None
    assert ar._shell_is_blocked("rm -rf / --no-preserve-root") is not None
    assert ar._shell_is_blocked("git push origin main") is not None
    # Safe commands pass.
    assert ar._shell_is_blocked("python3 scripts/get_job.py job-1") is None
    assert ar._shell_is_blocked("tectonic resume.tex") is None


def test_cancel_turn_is_noop_when_not_running():
    # Must never raise even if nothing is active.
    ar.cancel_turn("agent:job-hunter:not-running")
    assert "agent:job-hunter:not-running" not in ar.active_turn_keys()


def test_read_write_file_tools_stay_functional():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with mock.patch.object(ar, "ROOT", tmp):
            out = ar._tool_write_file({"path": "sub/note.txt", "content": "hi"})
            assert "wrote" in out
            assert ar._tool_read_file({"path": "sub/note.txt"}) == "hi"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("all agent_runner tests passed")
