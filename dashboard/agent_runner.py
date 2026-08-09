#!/usr/bin/env python3
"""OpenClaw-free agent runner: a direct DeepSeek tool-loop.

This module replaces the ``openclaw agent --agent job-hunter --session-key
<key> --message <msg>`` subprocess that the dashboard used to shell out to.
It exposes the same ``run_turn`` entry point the dashboard's
``run_agent_message`` orchestrator needs, backed by a self-contained
OpenAI-compatible tool-calling loop against DeepSeek-V4-Flash — the exact
model the OpenClaw ``job-hunter`` agent already used (auth profile
``deepseek:manual``). The repo already talks to this API directly in
``scripts/fastfill/flash_leftovers.py``; this reuses that pattern.

HONEST SCOPE (see docs/OPENCLAW_REMOVED.md Appendix §A6): the *model* is
reproducible, but the OpenClaw *harness* is not. Open-ended "read a file and
fix the bug / fix the LaTeX and recompile" turns are **best-effort** here and
may not match OpenClaw's judgment. When no ``DEEPSEEK_API_KEY`` is configured
— or the loop cannot proceed — this DOES NOT crash: it returns a non-zero
exit code so the caller surfaces the job as ``stuck`` for a human, exactly the
safe fallback the old ``exit 127`` (missing binary) path produced.

Cancellation: turns run in-process (in the caller's daemon thread). A per
session-key ``threading.Event`` replaces OpenClaw's ``OPENCLAW_DIRECT_ABORT``
trick — ``cancel_turn(key)`` signals the loop to stop between steps.

Live activity: each turn appends structured events to
``logs/agent_events_<key>.jsonl`` (including a terminal ``session.ended``
event) so the dashboard reconcile loop's auto-retry trigger and the activity
feed can key off our own lifecycle events instead of ``openclaw sessions
tail``.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"

# No-key / cannot-proceed exit code. Matches the historical "openclaw binary
# missing → exit 127" behavior the caller already degrades on (marks stuck).
EXIT_NO_KEY = 127
EXIT_ERROR = 1
EXIT_CANCELLED = 143  # SIGTERM-ish; mirrors the old aborted-turn exit.

# Bound the open-ended loop so a runaway/looping turn can't burn tokens or run
# forever. These are intentionally modest; the fallback on exhaustion is stuck.
MAX_STEPS = 16
SHELL_TIMEOUT_S = 240
MODEL_HTTP_TIMEOUT_S = 90
MAX_TOOL_OUTPUT_CHARS = 12000
MAX_FILE_READ_CHARS = 60000

# --- Active-turn registry (replaces gateway_running_session_keys) ----------
# Our turns run in-process, so this in-memory set is authoritative for the
# double-start guard — no gateway round-trip needed.
_turns_lock = threading.Lock()
_active_turns: dict[str, threading.Event] = {}


def active_turn_keys() -> set[str]:
    """Session keys with an agent turn currently running in this process."""
    with _turns_lock:
        return set(_active_turns.keys())


def is_turn_active(session_key: str) -> bool:
    with _turns_lock:
        return session_key in _active_turns


def cancel_turn(session_key: str) -> None:
    """Signal a running turn to stop at the next step boundary.

    Replaces OpenClaw's ``abort_gateway_session`` (connect a throwaway client
    to force ``OPENCLAW_DIRECT_ABORT``). No-op if the key isn't running.
    """
    with _turns_lock:
        ev = _active_turns.get(session_key)
    if ev is not None:
        ev.set()


def _register_turn(session_key: str) -> threading.Event:
    ev = threading.Event()
    with _turns_lock:
        _active_turns[session_key] = ev
    return ev


def _unregister_turn(session_key: str) -> None:
    with _turns_lock:
        _active_turns.pop(session_key, None)


# --- Key loading -----------------------------------------------------------

def _read_env_file_key(path: Path, names: tuple[str, ...]) -> str | None:
    """Pull a KEY=value (or ``export KEY=value``) from a dotenv-style file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        for name in names:
            if line.startswith(name + "="):
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                if raw:
                    return raw
    return None


def _json_deep_find(obj, names: tuple[str, ...]) -> str | None:
    """Find the first non-empty string value for any of *names* in a JSON blob.

    Matches top-level keys as well as nested dicts (e.g. ``{"llm":
    {"deepseek_api_key": ...}}``). Case-insensitive on the key.
    """
    wanted = {n.lower() for n in names}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in wanted and isinstance(v, str) and v.strip():
                return v.strip()
        for v in obj.values():
            found = _json_deep_find(v, names)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _json_deep_find(v, names)
            if found:
                return found
    return None


def _json_file_key(path: Path, names: tuple[str, ...]) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _json_deep_find(data, names)


def load_deepseek_config() -> tuple[str | None, str, str]:
    """Resolve (api_key, base_url, model) following the repo's own precedence.

    Key search order (first hit wins), per the task spec — env, then the
    repo's key files, then Skyvern's secrets file:
      1. ``OPENAI_COMPATIBLE_API_KEY`` / ``DEEPSEEK_API_KEY`` env vars
      2. repo-root ``web_keys.json``
      3. repo-root ``credentials.json``
      4. repo-root ``.env``
      5. ``skyvern_runtime/.secrets.env`` (same file flash_leftovers reads)

    Never reads ``profile.json`` (PII). Returns ``(None, ...)`` when no key is
    configured, which the caller treats as graceful degradation → stuck.
    """
    # Prefer the unified resolver (scripts/fastfill/llm_config.py) so base/key/
    # model live in ONE place shared with the fastfill LLM path. Fall back to the
    # inline search below if that import is unavailable (keeps agent_runner
    # standalone).
    try:
        import sys as _sys

        _ff = str(ROOT / "scripts" / "fastfill")
        if _ff not in _sys.path:
            _sys.path.insert(0, _ff)
        from llm_config import resolve_llm_config as _resolve

        _k, _base, _model = _resolve(root=ROOT)
        return (_k or None), _base, _model
    except Exception:
        pass

    names = ("OPENAI_COMPATIBLE_API_KEY", "DEEPSEEK_API_KEY")
    api_key = (
        os.environ.get("OPENAI_COMPATIBLE_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or ""
    ).strip() or None

    if not api_key:
        for candidate in (ROOT / "web_keys.json", ROOT / "credentials.json"):
            if candidate.is_file():
                api_key = _json_file_key(candidate, names)
                if api_key:
                    break

    if not api_key:
        for candidate in (ROOT / ".env", ROOT / "skyvern_runtime" / ".secrets.env"):
            if candidate.is_file():
                api_key = _read_env_file_key(candidate, names)
                if api_key:
                    break

    base = (
        os.environ.get("OPENAI_COMPATIBLE_API_BASE")
        or "https://api.deepseek.com/v1"
    ).rstrip("/")
    model = os.environ.get("OPENAI_COMPATIBLE_MODEL_NAME") or "deepseek-v4-flash"
    return api_key, base, model


# --- Event log (replaces `openclaw sessions tail`) -------------------------

def events_path_for(session_key: str) -> Path:
    log_name = session_key.rsplit(":", 1)[-1]
    return LOGS_DIR / f"agent_events_{log_name}.jsonl"


def _emit_event(events_path: Path, event: str, detail: str = "") -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    rec = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "event": event,
        "detail": (detail or "")[:500],
    }
    try:
        with open(events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def read_events(session_key: str, tail: int = 60) -> list[dict]:
    """Structured lifecycle events for a session — the openclaw-free
    replacement for ``get_activity``'s ``openclaw sessions tail`` parse."""
    path = events_path_for(session_key)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict] = []
    for line in lines[-tail:] if tail > 0 else lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            events.append(rec)
    return events


# --- Tools -----------------------------------------------------------------
# The old OpenClaw agent had exec (security: full) + browser + filesystem
# tools. We give the loop a comparable, deliberately auditable set: read/write
# files, run a shell command, plus a `finish`. Browser work is reached through
# the repo's existing scripts (e.g. `./open_partyrock.sh`, tailor_resume.py)
# via the shell tool rather than a bespoke CDP tool — honest and bounded.

# Hard safety denylist (PLAYBOOK red lines): never submit an application,
# never solve/bypass a CAPTCHA, no obviously destructive/exfil shell.
_SHELL_DENY_SUBSTRINGS = (
    "rm -rf /",
    "rm -rf ~",
    ":(){",  # fork bomb
    "mkfs",
    "shutdown",
    "reboot",
    "git push",
    "curl -x post",  # discourage blind POST submits (lowercased match)
)
_SHELL_DENY_TOKENS = ("submit", "captcha")

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file (relative to repo root or absolute).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write UTF-8 text to a file (creates parent dirs).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "Run a shell command from the repo root (bash -lc). Use the "
                "repo's own scripts (get_job.py, update_job.py, tailor_resume.py, "
                "tectonic, ./open_partyrock.sh). NEVER submit an application, "
                "solve a CAPTCHA, or run destructive commands."
            ),
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "End the turn. Provide a short summary of what you did.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]


def _resolve_in_root(path_str: str) -> Path:
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    return p


def _shell_is_blocked(command: str) -> str | None:
    low = command.lower()
    for bad in _SHELL_DENY_SUBSTRINGS:
        if bad in low:
            return f"blocked (safety denylist matched {bad!r})"
    # Conservative substring match on the PLAYBOOK red-line words: better to
    # refuse a borderline command and fall back to `stuck` than to risk
    # submitting an application or touching a CAPTCHA.
    for tok in _SHELL_DENY_TOKENS:
        if tok in low:
            return (
                f"blocked ({tok!r} — never submit an application or solve a "
                "CAPTCHA; if the app is ready for a human, set status to stuck "
                "instead)"
            )
    return None


def _tool_read_file(args: dict) -> str:
    p = _resolve_in_root(str(args.get("path") or ""))
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_READ_CHARS]
    except OSError as e:
        return f"error: {e}"


def _tool_write_file(args: dict) -> str:
    p = _resolve_in_root(str(args.get("path") or ""))
    content = str(args.get("content") or "")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {p}"
    except OSError as e:
        return f"error: {e}"


def _tool_run_shell(args: dict) -> str:
    command = str(args.get("command") or "").strip()
    if not command:
        return "error: empty command"
    blocked = _shell_is_blocked(command)
    if blocked:
        return f"error: {blocked}"
    try:
        proc = subprocess.run(
            ["bash", "-lc", command],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return f"error: command timed out after {SHELL_TIMEOUT_S}s"
    except OSError as e:
        return f"error: {e}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return f"exit={proc.returncode}\n{out}"[:MAX_TOOL_OUTPUT_CHARS]


def _dispatch_tool(name: str, args: dict) -> str:
    if name == "read_file":
        return _tool_read_file(args)
    if name == "write_file":
        return _tool_write_file(args)
    if name == "run_shell":
        return _tool_run_shell(args)
    return f"error: unknown tool {name!r}"


# --- Model call ------------------------------------------------------------

def _chat_completion(base: str, api_key: str, model: str, messages: list[dict],
                      thinking: str) -> dict:
    body = json.dumps({
        "model": model,
        "messages": messages,
        "tools": TOOLS_SCHEMA,
        "tool_choice": "auto",
        "temperature": 0.2 if thinking != "high" else 0.4,
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=MODEL_HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode())


_SYSTEM_PROMPT = (
    "You are the job-hunter agent, operating WITHOUT the OpenClaw harness via a "
    "direct DeepSeek tool loop. Follow PLAYBOOK.md (read it with read_file if you "
    "have not this turn). HARD RULES that override everything: never submit a job "
    "application, never solve or bypass a CAPTCHA, never invent EEO/demographic "
    "answers, and in test mode use only dummy data. Prefer the repo's scripts "
    "(scripts/get_job.py, scripts/update_job.py, scripts/tailor_resume.py, "
    "tectonic, ./open_partyrock.sh) over ad-hoc code. Keep changes minimal and "
    "reversible. If you cannot make safe progress, set the job's status to 'stuck' "
    "with a clear question via scripts/update_job.py and then call finish. Open-"
    "ended self-repair (fixing a bug in a script, fixing LaTeX) is best-effort — if "
    "unsure, stop and mark stuck rather than guessing. Call finish when done."
)


def run_turn(session_key: str, message: str, *, log_path: Path,
             timeout_s: int = 1200, thinking: str = "medium") -> int:
    """Run one agent turn. Returns an exit code (0 = ok; non-zero degrades to
    stuck at the caller). Never raises for expected conditions (no key, model
    error, cancellation, step/timeout exhaustion)."""
    events_path = events_path_for(session_key)
    cancel_ev = _register_turn(session_key)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(msg.rstrip("\n") + "\n")
        except OSError:
            pass

    try:
        api_key, base, model = load_deepseek_config()
        _emit_event(events_path, "session.started", f"turn for {session_key}")
        if not api_key:
            msg = (
                "No DEEPSEEK_API_KEY configured — agent turn cannot run. "
                "Surfacing as stuck for a human (safe fallback). Set "
                "DEEPSEEK_API_KEY (env / web_keys.json / credentials.json / .env) "
                "to enable the direct DeepSeek agent runner."
            )
            log(msg)
            _emit_event(events_path, "session.ended", "no DEEPSEEK_API_KEY (stuck fallback)")
            return EXIT_NO_KEY

        messages: list[dict] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ]
        deadline = time.monotonic() + max(30, timeout_s)

        for step in range(MAX_STEPS):
            if cancel_ev.is_set():
                log("Turn cancelled by user.")
                _emit_event(events_path, "session.ended", "cancelled")
                return EXIT_CANCELLED
            if time.monotonic() > deadline:
                log(f"Turn exceeded timeout ({timeout_s}s).")
                _emit_event(events_path, "session.ended", "timeout")
                return EXIT_ERROR
            try:
                resp = _chat_completion(base, api_key, model, messages, thinking)
            except Exception as e:  # network/provider/parse — degrade to stuck
                log(f"Model call failed: {e}")
                _emit_event(events_path, "session.ended", f"model error: {str(e)[:120]}")
                return EXIT_ERROR

            choice = (resp.get("choices") or [{}])[0]
            assistant = choice.get("message") or {}
            messages.append(assistant)
            tool_calls = assistant.get("tool_calls") or []
            content = (assistant.get("content") or "").strip()
            if content:
                log(f"[assistant] {content}")

            if not tool_calls:
                # No tool call → model is done talking.
                _emit_event(events_path, "session.ended", "success")
                return 0

            for tc in tool_calls:
                fn = (tc.get("function") or {})
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except ValueError:
                    args = {}
                if name == "finish":
                    summary = str(args.get("summary") or "").strip()
                    log(f"[finish] {summary}")
                    _emit_event(events_path, "session.ended", summary[:200] or "success")
                    return 0
                _emit_event(events_path, f"tool.{name}", str(args)[:200])
                result = _dispatch_tool(name, args)
                log(f"[tool {name}] -> {result[:500]}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id") or name,
                    "content": result,
                })

        # Step budget exhausted without a finish — treat as unfinished → stuck.
        log(f"Turn hit max steps ({MAX_STEPS}) without finishing.")
        _emit_event(events_path, "session.ended", "max steps reached")
        return EXIT_ERROR
    finally:
        _unregister_turn(session_key)
