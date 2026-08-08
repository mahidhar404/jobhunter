#!/usr/bin/env python3
"""End-to-end regression test for the dashboard's "Sort by Posted" control.

Comparator unit tests live in test_job_sort.js. They kept passing while the
live list still looked unsorted, because the bug was never in the comparator -
the age column rendered a created_at fallback that disagreed with it. So this
test drives the whole path a user does: load the real shipped page in a real
browser, pick "Posted" from the real <select>, and read the rendered rows.

It serves dashboard/static as-is against a stub /api/* so nothing here touches
jobs.json, the live server, or any real applicant data. Fixture jobs are
entirely made up.

Run: .venv/bin/python dashboard/test_posted_sort_ui.py
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


# Deliberately built so a created_at-based age column would look *wrong*:
# every undated row was "discovered" today, so the old code labelled them 0d
# and floated them visually to the top of an otherwise correct sort.
FIXTURE_JOBS = [
    {
        "id": "no-date-b",
        "company": "Umbra Systems",
        "title": "Data Engineer",
        "location": "Austin, TX",
        "source": "builtin",
        "status": "discovered",
        "date_posted": None,
        "created_at": f"{TODAY.isoformat()}T09:00:00Z",
        "updated_at": f"{TODAY.isoformat()}T09:00:00Z",
    },
    {
        "id": "mid",
        "company": "Cobalt Labs",
        "title": "Machine Learning Engineer",
        "location": "Remote, US",
        "source": "greenhouse",
        "status": "discovered",
        "date_posted": _iso(6),
        "created_at": f"{_iso(6)}T09:00:00Z",
        "updated_at": f"{_iso(6)}T09:00:00Z",
    },
    {
        "id": "no-date-a",
        "company": "Aster Works",
        "title": "Analytics Engineer",
        "location": "Denver, CO",
        "source": "lever",
        "status": "discovered",
        "date_posted": None,
        "created_at": f"{TODAY.isoformat()}T10:00:00Z",
        "updated_at": f"{TODAY.isoformat()}T10:00:00Z",
    },
    {
        "id": "oldest",
        "company": "Borealis Data",
        "title": "Data Scientist",
        "location": "Chicago, IL",
        "source": "ashby",
        "status": "discovered",
        "date_posted": _iso(20),
        "created_at": f"{_iso(20)}T09:00:00Z",
        "updated_at": f"{_iso(20)}T09:00:00Z",
    },
    {
        "id": "newest",
        "company": "Vantage AI",
        "title": "AI Engineer",
        "location": "Seattle, WA",
        "source": "greenhouse",
        "status": "discovered",
        "date_posted": _iso(0),
        "created_at": f"{_iso(0)}T08:00:00Z",
        "updated_at": f"{_iso(0)}T08:00:00Z",
    },
    {
        # Only an approximate date (relative "Posted N Days Ago" on the source
        # page). Must sort on that date, and must render with the "~" marker.
        "id": "approx",
        "company": "Northwind Analytics",
        "title": "Data Engineer",
        "location": "Boston, MA",
        "source": "builtin",
        "status": "discovered",
        "date_posted": None,
        "date_posted_fallback": _iso(3),
        "created_at": f"{TODAY.isoformat()}T11:00:00Z",
        "updated_at": f"{TODAY.isoformat()}T11:00:00Z",
    },
]

EXPECTED_ORDER = ["newest", "approx", "mid", "oldest", "no-date-a", "no-date-b"]
EXPECTED_AGES = ["0d", "~3d", "6d", "20d", "—", "—"]

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


def read_rows(page, row_selector: str) -> tuple[list[str], list[str]]:
    """(row ids, age labels) exactly as rendered, top to bottom."""
    return (
        page.eval_on_selector_all(
            row_selector, "els => els.map(e => e.getAttribute('data-id'))"
        ),
        page.eval_on_selector_all(
            row_selector,
            "els => els.map(e => (e.querySelector('.age')?.textContent || '').trim())",
        ),
    )


def assert_monotonic_desc(page, ids: list[str]) -> None:
    """Posted dates must be non-increasing, with unknowns only at the end."""
    times = page.evaluate(
        "ids => ids.map(id => jobPostedDisplay(window.__sortFixture[id]).time)", ids
    )
    seen_unknown = False
    for job_id, t in zip(ids, times):
        if t is None:
            seen_unknown = True
            continue
        assert not seen_unknown, f"dated row {job_id} rendered after an unknown-date row"
    dated = [t for t in times if t is not None]
    assert dated == sorted(dated, reverse=True), f"posted dates not newest-first: {ids}"


def check_ui(page, base_url: str, *, page_name: str, row_selector: str) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base_url}/{page_name}")
    page.wait_for_selector(f"{row_selector}[data-id]", timeout=15000)
    page.evaluate(
        "jobs => { window.__sortFixture = Object.fromEntries(jobs.map(j => [j.id, j])); }",
        FIXTURE_JOBS,
    )

    sort_select = page.locator("#sort-by")
    # Start from a different sort so selecting Posted has to actually re-sort,
    # rather than passing because "date" happened to be the initial value.
    sort_select.select_option("company", force=True)
    ids_by_company, _ = read_rows(page, row_selector)
    assert ids_by_company != EXPECTED_ORDER, (
        f"{page_name}: company sort produced the Posted order; "
        "the control isn't driving the sort"
    )

    sort_select.select_option("date", force=True)
    ids, ages = read_rows(page, row_selector)

    assert ids == EXPECTED_ORDER, f"{page_name}: row order {ids} != {EXPECTED_ORDER}"
    assert_monotonic_desc(page, ids)
    if row_selector == ".job-row":  # only the ops UI renders an age column
        assert ages == EXPECTED_AGES, f"{page_name}: age labels {ages} != {EXPECTED_AGES}"

    assert not errors, f"{page_name}: page errors {errors}"
    print(f"ok: {page_name} Posted sort -> {ids}")
    if row_selector == ".job-row":
        print(f"    age column -> {ages}")


def main() -> int:
    from playwright.sync_api import sync_playwright

    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            # Same resolver the scrapers use - PLAYWRIGHT_BROWSERS_PATH isn't
            # always where the browsers actually landed.
            exe = resolve_chromium_executable()
            if not exe:
                print("skip: no Chromium available (run: playwright install chromium)")
                return 0
            browser = pw.chromium.launch(executable_path=exe)
            try:
                for page_name, row_selector in (
                    ("index.html", ".job-row"),
                ):
                    context = browser.new_context()  # fresh sessionStorage each time
                    try:
                        check_ui(
                            context.new_page(),
                            base_url,
                            page_name=page_name,
                            row_selector=row_selector,
                        )
                    finally:
                        context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
    print("ok: posted sort UI tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
