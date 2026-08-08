#!/usr/bin/env python3
"""End-to-end regression test: list filters are scoped per queue section.

The filters used to be global, so configuring "Open" and then visiting
"Applied" leaked Open's mode/sort/search into Applied - and coming back to
Open showed whatever Applied was left on. This drives the real page in a real
browser: it clicks the mission stats to switch sections and uses the actual
<select>/<input> controls, then asserts each section restores its own config.

It serves dashboard/static as-is against a stub /api/* so nothing here touches
jobs.json, the live server, or any real applicant data. Fixture jobs are
entirely made up.

Run: .venv/bin/python dashboard/test_section_filter_state.py
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import date, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "dashboard" / "static"
sys.path.insert(0, str(ROOT / "scripts"))
from pw_fetch_html import resolve_chromium_executable  # noqa: E402

TODAY = date.today()


def _iso(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _job(job_id: str, company: str, status: str, days_ago: int, **extra) -> dict:
    job = {
        "id": job_id,
        "company": company,
        "title": "Data Engineer",
        "location": "Remote, US",
        "source": "greenhouse",
        "status": status,
        "date_posted": _iso(days_ago),
        "created_at": f"{_iso(days_ago)}T09:00:00Z",
        "updated_at": f"{_iso(days_ago)}T09:00:00Z",
    }
    job.update(extra)
    return job


# Spread across buckets (see queueBucket() in app.js) so Open, Applied,
# In progress, Stuck and Ready are all non-empty.
FIXTURE_JOBS = [
    _job("open-1", "Vantage AI", "discovered", 1),
    _job("open-2", "Cobalt Labs", "discovered", 4),
    _job("applied-1", "Borealis Data", "applied", 9, applied_at=f"{_iso(9)}T12:00:00Z"),
    _job("applied-2", "Aster Works", "applied", 12, applied_at=f"{_iso(12)}T12:00:00Z"),
    _job("progress-1", "Umbra Systems", "filling", 2),
    _job("stuck-1", "Northwind Analytics", "blocked_captcha", 3),
    _job("ready-1", "Meridian Robotics", "ready_for_review", 5),
]

_STUB_RESPONSES = {
    "/api/jobs": {"jobs": FIXTURE_JOBS},
    "/api/status": {"ok": True, "running": [], "jobs": FIXTURE_JOBS},
    "/api/profile": {},
    "/api/cron": {"enabled": False, "jobs": []},
    "/api/cron/schedule": {"ok": True},
    "/api/discover/settings": {},
    "/api/prune/settings": {},
    "/api/heartbeat": {"ok": True, "armed": False, "client_count": 1},
}

FILTER_CONTROL_IDS = [
    "search",
    "source-filter",
    "group-by",
    "sort-by",
    "work-mode-filter",
    "yoe-filter",
    "date-filter",
    "salary-filter",
    "status-filter",
    "extras-filter",
]

DEFAULT_CONTROLS = {
    "search": "",
    "source-filter": "",
    "group-by": "none",
    "sort-by": "date",
    "work-mode-filter": "",
    "yoe-filter": "",
    "date-filter": "",
    "salary-filter": "",
    "status-filter": "",
    "extras-filter": "",
}

OPEN_CONTROLS = {
    **DEFAULT_CONTROLS,
    "work-mode-filter": "remote",
    "sort-by": "company",
    "search": "vantage",
}
OPEN_BADGE = "Filters · 3"

APPLIED_CONTROLS = {**DEFAULT_CONTROLS, "yoe-filter": "le5"}
APPLIED_BADGE = "Filters · 1"


class StubHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, *args) -> None:  # keep test output readable
        pass

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/"):
            self._send_json(_STUB_RESPONSES.get(path, {"ok": True}))
            return
        super().do_GET()

    def do_POST(self) -> None:
        self._send_json({"ok": True})


def read_controls(page) -> dict[str, str]:
    """Current value of every list-filter control, as the user sees it."""
    return page.evaluate(
        "ids => Object.fromEntries(ids.map("
        "id => [id, document.getElementById(id)?.value ?? null]))",
        FILTER_CONTROL_IDS,
    )


def badge(page) -> str:
    """The "Filters · N" label text (textContent: the CSS uppercases it)."""
    return (page.locator("#filters-toggle-label").text_content() or "").strip()


def switch_section(page, queue: str) -> None:
    page.click(f'#mission-stats .mstat[data-queue="{queue}"]')
    page.wait_for_timeout(120)


def set_select(page, control_id: str, value: str) -> None:
    page.locator(f"#{control_id}").select_option(value, force=True)
    page.wait_for_timeout(60)


def set_search(page, text: str) -> None:
    page.locator("#search").fill(text)
    page.wait_for_timeout(300)  # the input handler debounces at 180ms


def assert_controls(page, expected: dict[str, str], *, where: str) -> None:
    actual = read_controls(page)
    for control_id, want in expected.items():
        assert actual[control_id] == want, (
            f"{where}: #{control_id} is {actual[control_id]!r}, expected {want!r} "
            f"(all controls: {actual})"
        )


def open_filters_popover(page) -> None:
    page.click("#filters-toggle")
    page.wait_for_timeout(80)


def close_filters_popover(page) -> None:
    if page.locator("#list-filters.open").count():
        page.click("#filters-toggle")
    # The popover also stays visible on :hover / :focus-within, so step away.
    page.mouse.move(4, 4)
    page.wait_for_timeout(80)


def check_per_section_filters(page, base_url: str) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base_url}/index.html")
    page.wait_for_selector(".job-row[data-id]", timeout=15000)

    # Open starts clean, then gets a distinctive config via the real controls.
    assert_controls(page, DEFAULT_CONTROLS, where="open (initial)")
    open_filters_popover(page)
    set_select(page, "work-mode-filter", "remote")
    set_select(page, "sort-by", "company")
    close_filters_popover(page)
    set_search(page, "vantage")
    assert_controls(page, OPEN_CONTROLS, where="open (after configuring)")
    assert badge(page) == OPEN_BADGE, f"open badge {badge(page)!r} != {OPEN_BADGE!r}"

    # Applied must not inherit Open's filters.
    switch_section(page, "applied")
    assert_controls(page, DEFAULT_CONTROLS, where="applied (first visit)")
    assert badge(page) == "Filters", f"applied badge {badge(page)!r} != 'Filters'"

    open_filters_popover(page)
    set_select(page, "yoe-filter", "le5")
    close_filters_popover(page)
    assert_controls(page, APPLIED_CONTROLS, where="applied (after configuring)")
    assert badge(page) == APPLIED_BADGE, f"applied badge {badge(page)!r} != {APPLIED_BADGE!r}"

    # Back to Open: its own three values return, Applied's YOE does not follow.
    switch_section(page, "open")
    assert_controls(page, OPEN_CONTROLS, where="open (restored)")
    assert badge(page) == OPEN_BADGE, f"restored open badge {badge(page)!r} != {OPEN_BADGE!r}"

    # Re-clicking the active section must not wipe its state.
    switch_section(page, "open")
    assert_controls(page, OPEN_CONTROLS, where="open (re-clicked active section)")

    # And Applied still remembers only its own.
    switch_section(page, "applied")
    assert_controls(page, APPLIED_CONTROLS, where="applied (restored)")
    assert badge(page) == APPLIED_BADGE, f"restored applied badge {badge(page)!r} != {APPLIED_BADGE!r}"

    # Other sections are independent too, and start at defaults.
    switch_section(page, "stuck")
    assert_controls(page, DEFAULT_CONTROLS, where="stuck (first visit)")

    # Clearing affects only the active section.
    switch_section(page, "applied")
    open_filters_popover(page)
    page.click("#filters-popover-clear")
    page.wait_for_timeout(120)
    close_filters_popover(page)
    assert_controls(page, DEFAULT_CONTROLS, where="applied (after clear)")
    assert badge(page) == "Filters", f"cleared applied badge {badge(page)!r} != 'Filters'"
    switch_section(page, "open")
    assert_controls(page, OPEN_CONTROLS, where="open (after clearing applied)")
    assert badge(page) == OPEN_BADGE, f"open badge after clear {badge(page)!r} != {OPEN_BADGE!r}"

    # sessionStorage keeps the per-section shape across a reload; the initial
    # queue is Open, so Open's config comes back.
    page.reload()
    page.wait_for_selector(".job-row[data-id]", timeout=15000)
    assert_controls(page, OPEN_CONTROLS, where="open (after reload)")
    switch_section(page, "applied")
    assert_controls(page, DEFAULT_CONTROLS, where="applied (after reload)")

    assert not errors, f"page errors {errors}"
    print("ok: each queue section keeps its own filter config")


def check_legacy_migration(page, base_url: str) -> None:
    """A pre-existing flat blob must survive as the default (Open) section."""
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base_url}/index.html")
    page.wait_for_selector(".job-row[data-id]", timeout=15000)
    page.evaluate(
        "blob => { sessionStorage.clear(); sessionStorage.setItem('opsFilterState', blob); }",
        json.dumps({"sort": "company", "mode": "remote", "search": "vantage"}),
    )
    page.reload()
    page.wait_for_selector(".job-row[data-id]", timeout=15000)
    assert_controls(page, OPEN_CONTROLS, where="open (legacy migration)")
    assert badge(page) == OPEN_BADGE, f"legacy badge {badge(page)!r} != {OPEN_BADGE!r}"
    switch_section(page, "applied")
    assert_controls(page, DEFAULT_CONTROLS, where="applied (legacy migration)")
    assert not errors, f"page errors {errors}"
    print("ok: legacy flat filter state migrates into the Open section")


def main() -> int:
    from playwright.sync_api import sync_playwright

    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            exe = resolve_chromium_executable()
            if not exe:
                print("skip: no Chromium available (run: playwright install chromium)")
                return 0
            browser = pw.chromium.launch(executable_path=exe)
            try:
                for check in (check_per_section_filters, check_legacy_migration):
                    context = browser.new_context()  # fresh sessionStorage each time
                    try:
                        check(context.new_page(), base_url)
                    finally:
                        context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
    print("ok: section filter state tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
