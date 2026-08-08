#!/usr/bin/env python3
"""Smoke tests for dashboard UI lifecycle (heartbeat / multi-tab / timeout / restart)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server as srv  # noqa: E402

_REAL_KILL_JH_BROWSERS = srv._kill_jh_associated_browsers


def _noop_jh_browsers(
    *, stop_openclaw_browser: bool = True, preserve_fill_cft: bool = False
) -> dict:
    """Avoid stopping live PartyRock/CDP Chrome during unit tests."""
    return {
        "chrome_for_testing": [],
        "partyrock_chrome_profile": [],
        "openclaw_browser": None,
        "stop_openclaw_browser": stop_openclaw_browser,
        "fill_cft_preserved": preserve_fill_cft,
    }


def _reset() -> None:
    with srv._ui_lock:
        srv._ui_clients.clear()
        srv._ui_lifecycle_armed = False
    with srv._shutdown_lock:
        srv._shutdown_requested = False
        srv._shutdown_reason = ""
    srv._restart_requested = False
    srv._preserve_fill_cft_on_exit = False
    srv._running_procs.clear()
    os.environ.pop("JOB_HUNTER_UI_LIFECYCLE", None)
    srv._kill_jh_associated_browsers = _noop_jh_browsers  # type: ignore[assignment]
    try:
        if srv.RESTART_FLAG_PATH.exists():
            srv.RESTART_FLAG_PATH.unlink()
    except OSError:
        pass


def test_heartbeat_arms_and_endpoint_shape() -> None:
    _reset()
    body, code = srv.record_ui_heartbeat("tab-a")
    assert code == 200
    assert body["ok"] is True
    assert body["armed"] is True
    assert body["client_count"] == 1
    assert body["heartbeat_timeout_s"] == srv.UI_HEARTBEAT_TIMEOUT_S
    st = srv.ui_lifecycle_status()
    assert st["armed"] is True
    assert st["client_count"] == 1


def test_closing_one_tab_keeps_stack_if_another_heartbeats() -> None:
    _reset()
    srv.record_ui_heartbeat("tab-a")
    srv.record_ui_heartbeat("tab-b")
    body, code = srv.request_ui_shutdown("tab-a")
    assert code == 200
    assert body["shutdown"] is False
    assert body["client_count"] == 1
    assert srv._shutdown_requested is False
    body2, code2 = srv.request_ui_shutdown("tab-b")
    assert code2 == 200
    assert body2["shutdown"] is True
    assert srv._shutdown_requested is True
    assert "shutdown" in (srv._shutdown_reason or "")


def test_heartbeat_timeout_does_not_shutdown() -> None:
    """Stale heartbeats prune clients but must not auto-quit the stack."""
    _reset()
    srv.record_ui_heartbeat("tab-stale")
    # Age the client past the timeout window.
    with srv._ui_lock:
        srv._ui_clients["tab-stale"] = time.time() - (srv.UI_HEARTBEAT_TIMEOUT_S + 5)
    assert srv.check_ui_heartbeat_timeout_for_tests() is False
    assert srv._shutdown_requested is False
    assert (srv._shutdown_reason or "") == ""
    st = srv.ui_lifecycle_status()
    assert st["client_count"] == 0
    assert st["shutdown_requested"] is False


def test_unarmed_server_does_not_timeout() -> None:
    _reset()
    assert srv.check_ui_heartbeat_timeout_for_tests() is False
    assert srv._shutdown_requested is False


def test_lifecycle_disable_env() -> None:
    _reset()
    os.environ["JOB_HUNTER_UI_LIFECYCLE"] = "0"
    body, code = srv.record_ui_heartbeat("tab-x")
    assert code == 200
    assert body["enabled"] is False
    assert body["armed"] is False
    body2, code2 = srv.request_ui_shutdown("tab-x")
    assert code2 == 200
    assert body2["shutdown"] is False
    assert srv._shutdown_requested is False


def test_restart_sets_flag_and_shutdown() -> None:
    """POST /api/restart path: cleanup flags + restart marker for launcher."""
    _reset()
    srv.LAUNCHER_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Pretend launch_dashboard.sh is waiting so we do not spawn a real relaunch.
    srv.LAUNCHER_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    try:
        srv.record_ui_heartbeat("tab-a")
        srv.record_ui_heartbeat("tab-b")
        body, code = srv.request_ui_restart("tab-a")
        assert code == 200
        assert body["ok"] is True
        assert body["restart"] is True
        assert body["shutdown"] is True
        assert body["launcher_will_respawn"] is True
        assert srv._shutdown_requested is True
        assert srv._restart_requested is True
        assert "restart" in (srv._shutdown_reason or "")
        assert srv.RESTART_FLAG_PATH.is_file()
        # Full refresh clears other tabs too.
        assert len(srv._ui_clients) == 0
    finally:
        try:
            if srv.RESTART_FLAG_PATH.exists():
                srv.RESTART_FLAG_PATH.unlink()
        except OSError:
            pass


def test_shutdown_kills_tracked_procs() -> None:
    """Shutdown must SIGTERM/SIGKILL process groups registered in _running_procs."""
    _reset()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=True,
    )
    srv._running_procs["test-orphan"] = proc
    try:
        assert proc.poll() is None
        assert srv.shutdown_dashboard_stack("test kill children") is True
        deadline = time.time() + 10
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        assert proc.poll() is not None, "tracked child should be dead after shutdown"
        assert "test-orphan" not in srv._running_procs
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                proc.kill()
            try:
                proc.wait(timeout=3)
            except Exception:
                pass


def test_kill_pids_term_then_kill_reaps_orphan() -> None:
    """Browser teardown helper must be able to SIGTERM a standalone process."""
    _reset()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=True,
    )
    try:
        assert proc.poll() is None
        killed = srv._kill_pids_term_then_kill([proc.pid], wait_s=3.0)
        assert proc.pid in killed
        deadline = time.time() + 5
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                proc.kill()


def test_jh_browser_paths_are_dedicated() -> None:
    """Safety: teardown targets dedicated JH/OpenClaw dirs, not daily Chrome."""
    assert "dashboard_chrome_profile" in str(srv.DASHBOARD_CHROME_PROFILE)
    assert "dashboard_ui_profile" in str(srv.DASHBOARD_UI_PROFILE)
    assert "partyrock_chrome_profile" in str(srv.PARTYROCK_CHROME_PROFILE)
    assert str(srv.OPENCLAW_BROWSER_USER_DATA).endswith(
        "browser/openclaw/user-data"
    ) or "/.openclaw/browser/openclaw/user-data" in str(srv.OPENCLAW_BROWSER_USER_DATA)
    assert srv.OPENCLAW_BROWSER_CDP_PORT == 18800
    # Must not point at the default macOS Chrome profile.
    home_chrome = str(Path.home() / "Library/Application Support/Google/Chrome")
    assert home_chrome not in str(srv.DASHBOARD_CHROME_PROFILE)
    assert home_chrome not in str(srv.DASHBOARD_UI_PROFILE)
    assert home_chrome not in str(srv.PARTYROCK_CHROME_PROFILE)
    assert home_chrome not in str(srv.OPENCLAW_BROWSER_USER_DATA)


def test_dashboard_ui_not_hosted_by_daily_chrome() -> None:
    """CHR2-007: UI window must not run on /Applications/Google Chrome.app.

    Launching that bundle with a custom --user-data-dir makes it the running
    com.google.Chrome instance, so Dock/Spotlight "Google Chrome" would open
    the empty dashboard profile instead of the user's daily one. Launcher
    must fail loud when CfT/Chromium is missing — never fall back.
    """
    launcher = (Path(srv.__file__).resolve().parent / "launch_dashboard.sh").read_text()
    body = launcher.split("open_dashboard_ui()", 1)[1].split("\nrelease_launcher_lock", 1)[0]
    chrome_app = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    assert chrome_app not in body, "CHR2-007: daily Chrome fallback must be removed"
    assert "refusing Google Chrome.app fallback" in body
    assert "return 1" in body


def test_chrome_for_testing_teardown_skips_dashboard_ui() -> None:
    """Form-fill teardown must not close the dashboard UI window."""
    ui_line = (
        f"12345 /path/Google Chrome for Testing --user-data-dir={srv.DASHBOARD_UI_PROFILE} "
        "--app=http://127.0.0.1:8787/"
    )
    fill_line = "67890 /path/MacOS/Google Chrome for Testing --remote-debugging-pipe"
    with mock.patch.object(
        srv.subprocess, "check_output", return_value=f"{ui_line}\n{fill_line}\n"
    ):
        pids = srv._chrome_for_testing_main_pids()
    assert pids == [67890]


def test_chrome_for_testing_teardown_skips_openclaw_partyrock() -> None:
    """CHR3-003: PartyRock OpenClaw CfT must not count as fill Chrome."""
    openclaw_line = (
        f"11111 /path/MacOS/Google Chrome for Testing "
        f"--user-data-dir={srv.OPENCLAW_BROWSER_USER_DATA} "
        f"--remote-debugging-port={srv.OPENCLAW_BROWSER_CDP_PORT}"
    )
    fill_line = "22222 /path/MacOS/Google Chrome for Testing --remote-debugging-pipe"
    with mock.patch.object(
        srv.subprocess, "check_output", return_value=f"{openclaw_line}\n{fill_line}\n"
    ):
        pids = srv._chrome_for_testing_main_pids()
    assert pids == [22222]
    # CHR3-004: PartyRock alone must not look like a fill hold.
    missing = Path("/nonexistent/.captcha_waiting.json")
    with mock.patch.object(srv, "_chrome_for_testing_main_pids", return_value=[]):
        with mock.patch.object(
            srv.subprocess,
            "check_output",
            side_effect=subprocess.CalledProcessError(1, "pgrep"),
        ):
            with mock.patch(
                "captcha_pause.captcha_waiting_marker_paths",
                return_value=(missing, missing.with_suffix(".md")),
            ):
                assert srv._fill_hold_browser_active() is False


def test_restart_skips_openclaw_browser_stop() -> None:
    """Refresh must not bounce the PartyRock CDP browser."""
    _reset()
    calls: list[dict] = []

    def _fake_kill(
        *, stop_openclaw_browser: bool = True, preserve_fill_cft: bool = False
    ) -> dict:
        calls.append(
            {
                "stop_openclaw": stop_openclaw_browser,
                "preserve_fill_cft": preserve_fill_cft,
            }
        )
        return {
            "chrome_for_testing": [],
            "partyrock_chrome_profile": [],
            "openclaw_browser": None,
            "fill_cft_preserved": preserve_fill_cft,
        }

    srv._kill_jh_associated_browsers = _fake_kill  # type: ignore[assignment]
    real_hold = srv._fill_hold_browser_active
    srv._fill_hold_browser_active = lambda: False  # type: ignore[assignment]
    try:
        srv._restart_requested = True
        assert srv.shutdown_dashboard_stack("ui restart") is True
        assert calls == [{"stop_openclaw": False, "preserve_fill_cft": False}]
        assert srv._preserve_fill_cft_on_exit is False
    finally:
        srv._kill_jh_associated_browsers = _noop_jh_browsers  # type: ignore[assignment]
        srv._fill_hold_browser_active = real_hold  # type: ignore[assignment]


def test_restart_preserves_fill_cft_and_procs_on_hold() -> None:
    """CHR3-001/002/009: Refresh + hold preserves fill CfT flag and skips proc kill."""
    _reset()
    kill_calls: list[dict] = []
    child_calls: list[bool] = []

    def _fake_kill(
        *, stop_openclaw_browser: bool = True, preserve_fill_cft: bool = False
    ) -> dict:
        kill_calls.append(
            {
                "stop_openclaw": stop_openclaw_browser,
                "preserve_fill_cft": preserve_fill_cft,
            }
        )
        return {
            "chrome_for_testing": [],
            "partyrock_chrome_profile": [],
            "openclaw_browser": None,
            "fill_cft_preserved": preserve_fill_cft,
        }

    real_kill_children = srv._kill_all_tracked_child_procs

    def _tracking_kill_children(*, preserve_fill_procs: bool = False) -> None:
        child_calls.append(preserve_fill_procs)
        # Do not actually kill — just record.
        if preserve_fill_procs:
            srv._running_procs.clear()
            return
        real_kill_children(preserve_fill_procs=False)

    sentinel = mock.Mock()
    sentinel.poll.return_value = None
    srv._running_procs["agent:hold-job"] = sentinel
    srv._kill_jh_associated_browsers = _fake_kill  # type: ignore[assignment]
    srv._kill_all_tracked_child_procs = _tracking_kill_children  # type: ignore[assignment]
    real_hold = srv._fill_hold_browser_active
    srv._fill_hold_browser_active = lambda: True  # type: ignore[assignment]
    try:
        srv._restart_requested = True
        assert srv.shutdown_dashboard_stack("ui restart") is True
        assert kill_calls == [{"stop_openclaw": False, "preserve_fill_cft": True}]
        assert child_calls == [True]
        assert srv._preserve_fill_cft_on_exit is True
        # Detached, not SIGTERM'd via real tree kill.
        assert "agent:hold-job" not in srv._running_procs
    finally:
        srv._kill_jh_associated_browsers = _noop_jh_browsers  # type: ignore[assignment]
        srv._kill_all_tracked_child_procs = real_kill_children  # type: ignore[assignment]
        srv._fill_hold_browser_active = real_hold  # type: ignore[assignment]


def test_quit_stops_openclaw_browser() -> None:
    """Full quit must request OpenClaw/PartyRock CDP stop."""
    _reset()
    calls: list[dict] = []

    def _fake_kill(
        *, stop_openclaw_browser: bool = True, preserve_fill_cft: bool = False
    ) -> dict:
        calls.append(
            {
                "stop_openclaw": stop_openclaw_browser,
                "preserve_fill_cft": preserve_fill_cft,
            }
        )
        return {
            "chrome_for_testing": [],
            "partyrock_chrome_profile": [],
            "openclaw_browser": None,
            "fill_cft_preserved": preserve_fill_cft,
        }

    srv._kill_jh_associated_browsers = _fake_kill  # type: ignore[assignment]
    try:
        assert srv.shutdown_dashboard_stack("ui shutdown") is True
        assert calls == [{"stop_openclaw": True, "preserve_fill_cft": False}]
    finally:
        srv._kill_jh_associated_browsers = _noop_jh_browsers  # type: ignore[assignment]


def test_launcher_focuses_existing_ui_not_activate() -> None:
    """Dock/double-click must focus by PID — never Launch Services activate."""
    launcher = (Path(srv.__file__).resolve().parent / "launch_dashboard.sh").read_text()
    assert "focus_dashboard_ui" in launcher
    assert "--focus-ui" in launcher
    assert "whose unix id is" in launcher
    assert 'tell application "Google Chrome for Testing" to activate' not in launcher
    # CHR3-005: fill focus + role inventory (operator confusion mitigation).
    assert "focus_fill_cft" in launcher
    assert "--focus-fill" in launcher
    assert "--cft-roles" in launcher
    assert "print_cft_role_inventory" in launcher
    assert "--remote-debugging-pipe" in launcher
    # open_dashboard_ui must call focus before any re-launch.
    body = launcher.split("open_dashboard_ui()", 1)[1].split("\nrelease_launcher_lock", 1)[0]
    assert "focus_dashboard_ui" in body
    assert "clear_stale_ui_profile_locks" in body
    applescript = (
        Path(srv.__file__).resolve().parent / "JobHunterDashboard.applescript"
    ).read_text()
    assert "on reopen" in applescript
    assert "--focus-ui" in applescript
    # CHR2-009: rebuild injects ROOT; source uses placeholder.
    assert "__JOB_HUNTER_ROOT__" in applescript
    # CHR2-010: focus failures must surface (not bare try/end try).
    assert "focus failed" in applescript.lower() or "Job Hunter Dashboard focus failed" in applescript
    rebuild = (
        Path(srv.__file__).resolve().parent / "rebuild_desktop_app.sh"
    ).read_text()
    assert "__JOB_HUNTER_ROOT__" in rebuild
    assert "sed" in rebuild
    assert "--focus-fill" in rebuild
    assert "CHR3-005" in rebuild


if __name__ == "__main__":
    test_heartbeat_arms_and_endpoint_shape()
    test_closing_one_tab_keeps_stack_if_another_heartbeats()
    test_heartbeat_timeout_does_not_shutdown()
    test_unarmed_server_does_not_timeout()
    test_lifecycle_disable_env()
    test_restart_sets_flag_and_shutdown()
    test_shutdown_kills_tracked_procs()
    test_kill_pids_term_then_kill_reaps_orphan()
    test_jh_browser_paths_are_dedicated()
    test_dashboard_ui_not_hosted_by_daily_chrome()
    test_chrome_for_testing_teardown_skips_dashboard_ui()
    test_chrome_for_testing_teardown_skips_openclaw_partyrock()
    test_launcher_focuses_existing_ui_not_activate()
    test_restart_skips_openclaw_browser_stop()
    test_restart_preserves_fill_cft_and_procs_on_hold()
    test_quit_stops_openclaw_browser()
    srv._kill_jh_associated_browsers = _REAL_KILL_JH_BROWSERS  # type: ignore[assignment]
    print("ok: ui lifecycle heartbeat/shutdown/restart smoke passed")
