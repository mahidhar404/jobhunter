"""Headed CAPTCHA pause — never solve; wait for the human, then continue.

Hard rules:
  - Never solve CAPTCHA / Turnstile / Cloudflare programmatically
  - Never dismiss CAPTCHA widgets/iframes/modals
  - Headless / captcha_wait off: keep blocker=captcha (cannot solve)
  - Headed + captcha_wait: pause, human solves in browser, Enter to continue
    same session — do NOT abort as BLOCKED while waiting

Message shown to the human (exact)::

    CAPTCHA detected — solve it in the browser, then press Enter here to continue

No-TTY headed runs (nohup / background): still wait — poll until the challenge
is gone, or until a sentinel continue-file appears (see
``captcha_continue_sentinel_path``). Never abort without waiting when headed
captcha-wait is on. Never ``skipped_no_tty``.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

CAPTCHA_WAIT_MESSAGE = (
    "CAPTCHA detected — solve it in the browser, then press Enter here to continue "
    "(not the Pause/Continue fill overlay — that is hidden during CAPTCHA; "
    "or: touch .captcha_continue)"
)
DEFAULT_CAPTCHA_TIMEOUT_S = 600  # 10 minutes
CAPTCHA_BLOCKERS = frozenset({"captcha", "cloudflare"})

# Workspace-relative default; override with FASTFILL_CAPTCHA_CONTINUE_FILE
_RESULTS_DIR = (
    Path(__file__).resolve().parents[2] / "skyvern_runtime" / "real_job_results"
)
_DEFAULT_CONTINUE_SENTINEL = _RESULTS_DIR / ".captcha_continue"


def captcha_continue_sentinel_path() -> Path:
    """File whose presence means 'human solved — continue' (no-TTY Enter)."""
    env = (os.environ.get("FASTFILL_CAPTCHA_CONTINUE_FILE") or "").strip()
    return Path(env).expanduser() if env else _DEFAULT_CONTINUE_SENTINEL


def captcha_waiting_marker_paths() -> tuple[Path, Path]:
    """JSON + markdown markers agents/humans can poll while wait is active."""
    base = captcha_continue_sentinel_path().parent
    return base / ".captcha_waiting.json", base / "CAPTCHA_WAITING.md"


def consume_captcha_continue_sentinel() -> bool:
    """Return True and delete sentinel if it exists (atomic-enough for one waiter)."""
    path = captcha_continue_sentinel_path()
    try:
        if path.is_file():
            path.unlink(missing_ok=True)
            return True
    except Exception:
        pass
    return False


def resolve_captcha_wait(*, headed: bool, captcha_wait: bool | None) -> bool:
    """Default ON when headed; OFF when headless. Explicit flag wins."""
    if captcha_wait is not None:
        return bool(captcha_wait)
    return bool(headed)


def write_captcha_waiting_marker(
    *,
    blocker: str,
    timeout_s: float,
    sentinel: Path,
    has_tty: bool,
    page_url: str = "",
) -> dict[str, Any]:
    """Persist wait state for Cursor/nohup operators (cleared on continue/timeout)."""
    json_path, md_path = captcha_waiting_marker_paths()
    payload: dict[str, Any] = {
        "status": "waiting",
        "blocker": blocker,
        "message": CAPTCHA_WAIT_MESSAGE,
        "sentinel": str(sentinel),
        "touch_cmd": f"touch {sentinel}",
        "has_tty": bool(has_tty),
        "timeout_s": float(timeout_s),
        "pid": os.getpid(),
        "page_url": (page_url or "")[:300],
        "ts": time.time(),
        "never_solve": True,
        "never_dismiss": True,
    }
    try:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(payload, indent=2) + "\n")
        md_path.write_text(
            "# CAPTCHA waiting — human gate\n\n"
            f"**{CAPTCHA_WAIT_MESSAGE}**\n\n"
            "- Never solve/dismiss programmatically.\n"
            "- TTY: press Enter in the fill process terminal.\n"
            "- No TTY (Cursor / nohup): solve in Chrome, then either wait until "
            f"the challenge disappears **or**:\n\n"
            f"```bash\ntouch {sentinel}\n```\n\n"
            f"- Timeout: {timeout_s:.0f}s → blocker kept; orchestrator must **not** "
            "burn BLOCKED×3 — next variety URL.\n"
            "- Refill leftovers auto-loop without Enter; Enter is CAPTCHA-only.\n"
            "- Do **not** use the Pause/Continue fill overlay for CAPTCHA "
            "(hidden during wait; Continue fill ≠ CAPTCHA continue).\n"
            f"- Or touch `.fill_continue` (also accepted during CAPTCHA wait).\n"
            f"- pid={os.getpid()} blocker={blocker}\n"
            f"- url={(page_url or '')[:200]}\n"
        )
    except Exception as e:
        payload["marker_error"] = str(e)[:120]
    return payload


def clear_captcha_waiting_marker() -> None:
    json_path, md_path = captcha_waiting_marker_paths()
    for p in (json_path, md_path):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


# CHR2-005: stale .captcha_waiting.json must not forever block orphan kill /
# headed launch after a crashed fill (writer died without clear).
CAPTCHA_MARKER_STALE_GRACE_S = 120.0  # after timeout_s from marker
CAPTCHA_MARKER_DEAD_PID_GRACE_S = 30.0  # writer PID gone
CAPTCHA_MARKER_HARD_MAX_AGE_S = 6 * 3600  # absolute ceiling


def captcha_waiting_marker_active(*, clear_stale: bool = True) -> bool:
    """True only when a *fresh* CAPTCHA wait marker exists (CHR2-005 TTL).

    Stale when: unreadable; older than hard max; past ``timeout_s`` + grace;
    or writer ``pid`` is dead for longer than ``CAPTCHA_MARKER_DEAD_PID_GRACE_S``.
    Clears marker files when stale and *clear_stale* is True.
    """
    json_path, _md = captcha_waiting_marker_paths()
    if not json_path.is_file():
        return False
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        if clear_stale:
            clear_captcha_waiting_marker()
        return False
    if not isinstance(data, dict):
        if clear_stale:
            clear_captcha_waiting_marker()
        return False
    try:
        ts = float(data.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0.0
    try:
        timeout_s = float(data.get("timeout_s") or DEFAULT_CAPTCHA_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout_s = float(DEFAULT_CAPTCHA_TIMEOUT_S)
    age = (time.time() - ts) if ts > 0 else float("inf")
    stale = False
    if age > CAPTCHA_MARKER_HARD_MAX_AGE_S:
        stale = True
    elif age > timeout_s + CAPTCHA_MARKER_STALE_GRACE_S:
        stale = True
    else:
        raw_pid = data.get("pid")
        if raw_pid is not None:
            try:
                pid = int(raw_pid)
                os.kill(pid, 0)
            except (OSError, ValueError, TypeError):
                if age > CAPTCHA_MARKER_DEAD_PID_GRACE_S:
                    stale = True
    if stale:
        if clear_stale:
            clear_captcha_waiting_marker()
        return False
    return True


def bring_chrome_testing_to_front(*, loud: bool = False) -> bool:
    """Best-effort macOS focus so the human sees Playwright's headed browser.

    Prefer System Events ``unix id`` of an existing Chrome-for-Testing *fill*
    main (excludes dashboard ``dashboard_ui_profile`` / ``--app=:8787`` and
    OpenClaw PartyRock ``openclaw/user-data`` / ``:18800`` — CHR3-003/006).
    Prefer ``--remote-debugging-pipe`` (Playwright) over other fill mains.
    Never ``tell application "Google Chrome for Testing" to activate`` and
    never name-based frontmost (raises UI/PartyRock — CHR2-004).
    """
    if sys.platform != "darwin":
        return False
    if (os.environ.get("FASTFILL_CAPTCHA_NO_FOCUS") or "").strip() in (
        "1",
        "true",
        "yes",
    ):
        return False

    root = Path(__file__).resolve().parents[2]
    exclude_markers = (
        f"--user-data-dir={root / 'dashboard_ui_profile'}",
        "--app=http://127.0.0.1:8787",
        f"--user-data-dir={Path.home() / '.openclaw' / 'browser' / 'openclaw' / 'user-data'}",
        "--remote-debugging-port=18800",
        "openclaw/user-data",
    )

    def _fill_main_pids() -> list[int]:
        try:
            out = subprocess.check_output(
                ["pgrep", "-lf", "Google Chrome for Testing"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return []
        preferred: list[int] = []
        other: list[int] = []
        for line in out.splitlines():
            if "Helper" in line or "crashpad" in line:
                continue
            if "MacOS/Google Chrome for Testing" not in line and "/chrome " not in line:
                # Linux chrome-linux binary often ends with /chrome
                if "/chrome" not in line:
                    continue
            if any(m in line for m in exclude_markers):
                continue
            parts = line.strip().split(None, 1)
            if not parts:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if "--remote-debugging-pipe" in line:
                preferred.append(pid)
            else:
                other.append(pid)
        return preferred + other

    for pid in _fill_main_pids():
        try:
            r = subprocess.run(
                [
                    "osascript",
                    "-e",
                    "tell application \"System Events\" to set frontmost of "
                    f"first process whose unix id is {pid} to true",
                ],
                check=False,
                timeout=3,
                capture_output=True,
                text=True,
            )
            if r.returncode == 0:
                if loud:
                    print(f"[browser] focused Chrome-for-Testing pid={pid}", flush=True)
                return True
        except Exception:
            continue

    # No name-based fallback — first "Chrome for Testing" is often the UI.
    return False


def _bring_chrome_testing_to_front() -> None:
    """CAPTCHA pause hook — delegates to :func:`bring_chrome_testing_to_front`."""
    bring_chrome_testing_to_front()


async def page_shows_interactive_captcha(page) -> bool:
    """True when an interactive CAPTCHA challenge is on-screen (never solve it)."""
    from iframe_ctx import visible_captcha_challenge

    try:
        if await visible_captcha_challenge(page):
            return True
    except Exception:
        pass
    # Child frames (Greenhouse / Lever often host recaptcha in iframes)
    try:
        for frame in getattr(page, "frames", []) or []:
            if frame == page.main_frame:
                continue
            try:
                if await visible_captcha_challenge(frame):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _stdin_is_interactive() -> bool:
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


async def wait_for_human_captcha(
    page,
    *,
    headed: bool,
    captcha_wait: bool | None = None,
    timeout_s: float = DEFAULT_CAPTCHA_TIMEOUT_S,
    blocker: str = "captcha",
) -> dict[str, Any]:
    """Pause for a human CAPTCHA solve. Never solves or dismisses the widget.

    Headed + captcha_wait always waits (TTY Enter, challenge-gone, or sentinel
    file) — never returns immediately without waiting when wait is enabled.

    Returns a small dict folded into ``report['captcha_wait']``::

        {
          "waited": bool,
          "continued": bool,   # Enter/sentinel and/or challenge gone
          "solved_gone": bool, # interactive widget no longer visible
          "timed_out": bool,
          "via": "enter"|"gone"|"sentinel"|"timeout"|"skipped_headless"|...,
          "blocker": str,
          "message": str,
        }
    """
    do_wait = resolve_captcha_wait(headed=headed, captcha_wait=captcha_wait)
    sentinel = captcha_continue_sentinel_path()
    out: dict[str, Any] = {
        "waited": False,
        "continued": False,
        "solved_gone": False,
        "timed_out": False,
        "via": None,
        "blocker": blocker,
        "message": CAPTCHA_WAIT_MESSAGE,
        "timeout_s": float(timeout_s),
        "headed": bool(headed),
        "captcha_wait": do_wait,
        "sentinel_path": str(sentinel),
        "has_tty": _stdin_is_interactive(),
    }
    if not do_wait:
        out["via"] = "skipped_headless" if not headed else "skipped_disabled"
        return out

    # Clear a stale sentinel so a prior run cannot auto-continue.
    try:
        sentinel.unlink(missing_ok=True)
    except Exception:
        pass

    out["waited"] = True
    has_tty = bool(out["has_tty"])
    page_url = ""
    try:
        page_url = str(getattr(page, "url", "") or "")
    except Exception:
        page_url = ""

    print(f"\n*** {CAPTCHA_WAIT_MESSAGE} ***\n", flush=True)
    if not has_tty:
        print(
            "[captcha] No TTY for Enter — browser stays open; solve in Chrome, "
            f"then either wait until the challenge disappears or: "
            f"touch {sentinel}",
            flush=True,
        )
    else:
        print(
            f"[captcha] TTY ready — Enter here, or touch {sentinel}, "
            "or wait until the challenge disappears.",
            flush=True,
        )

    write_captcha_waiting_marker(
        blocker=blocker,
        timeout_s=float(timeout_s),
        sentinel=sentinel,
        has_tty=has_tty,
        page_url=page_url,
    )
    _bring_chrome_testing_to_front()

    # FILL3-002 / FILL3-015 / FILL2-S03: hide Pause overlay so it cannot cover
    # the CAPTCHA widget. FILL2-S02: Continue fill ≠ CAPTCHA continue.
    try:
        from fill_pause import set_fill_pause_captcha_gate

        await set_fill_pause_captcha_gate(page, True)
        out["pause_overlay_gated"] = True
    except Exception as e:
        out["pause_overlay_gate_error"] = str(e)[:120]

    loop = asyncio.get_running_loop()
    enter_fut = None
    if has_tty:
        enter_fut = loop.run_in_executor(None, sys.stdin.readline)
    end = time.monotonic() + max(5.0, float(timeout_s))

    def _finish(via: str, *, continued: bool, solved_gone: bool, timed_out: bool = False):
        out["via"] = via
        out["continued"] = continued
        out["solved_gone"] = solved_gone
        out["timed_out"] = timed_out
        clear_captcha_waiting_marker()
        if enter_fut is not None and not enter_fut.done():
            enter_fut.cancel()
        return out

    async def _ungate_pause_overlay() -> None:
        try:
            from fill_pause import set_fill_pause_captcha_gate

            await set_fill_pause_captcha_gate(page, False)
        except Exception:
            pass

    try:
        while True:
            if enter_fut is not None and enter_fut.done():
                try:
                    enter_fut.result()
                except Exception:
                    pass
                still = await page_shows_interactive_captcha(page)
                if still:
                    # FILL-008: challenge still visible — keep waiting; do not
                    # clear blocker / claim captcha_human_solved yet.
                    print(
                        "[captcha] Enter received but challenge still visible — "
                        "keep waiting (solve in browser, then Enter again)…",
                        flush=True,
                    )
                    if has_tty:
                        enter_fut = loop.run_in_executor(None, sys.stdin.readline)
                    await asyncio.sleep(0.75)
                    continue
                print(
                    "[captcha] Enter received — resuming fill "
                    f"(challenge_visible={still})…",
                    flush=True,
                )
                await _ungate_pause_overlay()
                return _finish("enter", continued=True, solved_gone=True)

            # FILL2-S02: accept either captcha sentinel or fill_continue during wait
            # (both mean human wants to resume after solving — never auto-solve).
            fill_sentinel_hit = False
            try:
                from fill_pause import consume_fill_continue_sentinel

                fill_sentinel_hit = consume_fill_continue_sentinel()
            except Exception:
                fill_sentinel_hit = False
            if consume_captcha_continue_sentinel() or fill_sentinel_hit:
                still = await page_shows_interactive_captcha(page)
                if still:
                    print(
                        "[captcha] Continue sentinel seen but challenge still "
                        "visible — keep waiting…",
                        flush=True,
                    )
                    await asyncio.sleep(0.75)
                    continue
                via = "fill_continue_sentinel" if fill_sentinel_hit else "sentinel"
                print(
                    f"[captcha] Continue sentinel seen ({via}) — resuming fill "
                    f"(challenge_visible={still})…",
                    flush=True,
                )
                await _ungate_pause_overlay()
                return _finish(via, continued=True, solved_gone=True)

            still = await page_shows_interactive_captcha(page)
            if not still:
                # Challenge cleared (human solved / page advanced).
                print(
                    "[captcha] Challenge no longer visible — resuming fill…",
                    flush=True,
                )
                await _ungate_pause_overlay()
                return _finish("gone", continued=True, solved_gone=True)

            if time.monotonic() >= end:
                print(
                    f"[captcha] Timed out after {timeout_s:.0f}s — leaving blocker="
                    f"{blocker} (never solved automatically; browser left for review).",
                    flush=True,
                )
                await _ungate_pause_overlay()
                return _finish(
                    "timeout", continued=False, solved_gone=False, timed_out=True
                )

            # Re-assert gate in case remount dropped the class (FILL3-002)
            try:
                from fill_pause import set_fill_pause_captcha_gate

                await set_fill_pause_captcha_gate(page, True)
            except Exception:
                pass

            await asyncio.sleep(0.75)
    except Exception:
        clear_captcha_waiting_marker()
        await _ungate_pause_overlay()
        raise


async def handle_captcha_blocker(
    page,
    report: dict,
    blocker: str,
    *,
    headed: bool,
    captcha_wait: bool | None,
    timeout_s: float = DEFAULT_CAPTCHA_TIMEOUT_S,
) -> str:
    """If CAPTCHA/cloudflare in headed wait mode → pause; else set blocker.

    Returns:
      ``"continued"`` — human solved / Enter; blocker cleared; resume fill
      ``"blocked"`` — keep blocker; caller should stop fill path
    """
    if blocker not in CAPTCHA_BLOCKERS:
        report["blocker"] = blocker
        return "blocked"

    try:
        from fill_step_log import note_step

        note_step(
            report,
            action="captcha_pause",
            reason=CAPTCHA_WAIT_MESSAGE,
            via=f"blocker={blocker}",
            force_screenshot=True,
        )
    except Exception:
        pass
    print(f"[captcha] pause — {CAPTCHA_WAIT_MESSAGE}", flush=True)

    result = await wait_for_human_captcha(
        page,
        headed=headed,
        captcha_wait=captcha_wait,
        timeout_s=timeout_s,
        blocker=blocker,
    )
    report["captcha_wait"] = result
    if result.get("continued"):
        # FILL-008: only clear blocker / mark solved when challenge is gone
        if result.get("solved_gone") is False:
            report["blocker"] = blocker
            report["captcha_human_solved"] = False
            try:
                from fill_step_log import note_step

                note_step(
                    report,
                    action="captcha_still_visible",
                    reason=f"via={result.get('via')} continued_but_challenge_visible",
                    via="captcha_wait",
                )
            except Exception:
                pass
            print(
                "[captcha] continued signal ignored — challenge still visible "
                "(blocker kept; never auto-solved)",
                flush=True,
            )
            return "blocked"
        # Clear captcha blocker so fill/advance can resume
        if report.get("blocker") in CAPTCHA_BLOCKERS:
            report["blocker"] = None
        report.pop("blocker", None)
        # Drop leftover rows that only exist to mark the gate
        report["leftovers"] = [
            u
            for u in (report.get("leftovers") or [])
            if not (
                isinstance(u, dict)
                and str(u.get("reason") or "").startswith("blocker:")
                and any(b in str(u.get("reason")) for b in CAPTCHA_BLOCKERS)
            )
        ]
        report["captcha_human_solved"] = True
        try:
            from fill_step_log import note_step

            note_step(
                report,
                action="captcha_resume",
                reason=f"via={result.get('via')} solved_gone={result.get('solved_gone')}",
                via="captcha_wait",
            )
        except Exception:
            pass
        print("[captcha] resumed fill after human solve (never auto-solved)", flush=True)
        return "continued"

    report["blocker"] = blocker
    try:
        from fill_step_log import note_step

        note_step(
            report,
            action="captcha_blocked",
            reason=f"via={result.get('via')} timed_out={result.get('timed_out')}",
            via="captcha_wait",
        )
    except Exception:
        pass
    return "blocked"


def escape_safe_while_captcha(page_has_captcha: bool) -> bool:
    """Callers must not press Escape while a CAPTCHA is on-screen (would dismiss)."""
    return not page_has_captcha


async def press_escape_unless_captcha(page) -> bool:
    """Press Escape only when no interactive CAPTCHA is visible (FILL3-019).

    Returns True if Escape was sent, False if skipped (CAPTCHA / probe fail /
    press fail). Fail closed: when CAPTCHA presence cannot be determined, do
    not press Escape (would dismiss challenge widgets).
    """
    try:
        has_captcha = await page_shows_interactive_captcha(page)
    except Exception:
        return False
    if not escape_safe_while_captcha(bool(has_captcha)):
        return False
    try:
        await page.keyboard.press("Escape")
        return True
    except Exception:
        return False
