#!/usr/bin/env python3
"""End-to-end: Ops list filters are per queue family, persisted in localStorage.

Open, Applied, and the pipeline family (Stuck / Ready / In progress) each keep
their own Filters. Switching KPI tabs restores that family's last settings.
Each KPI strip count uses **that queue family's** saved filters (not the
active tab's), so filtering Open does not change Applied / In progress counts.
The selected tab's KPI still equals the sidebar list length.

Storage key: localStorage `opsFilterState` (map keyed by family:
open | applied | pipeline | deleted). Legacy flat blob or per-queue
sessionStorage migrates into that map.

Serves dashboard/static as-is against a stub /api/* so nothing here touches
jobs.json, the live server, or any real applicant data. Fixture jobs are
entirely made up.

Run: .venv/bin/python dashboard/test_section_filter_state.py
"""
from __future__ import annotations

import json
import subprocess
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
FILTER_STORAGE_KEY = "opsFilterState"


def _iso(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _job(job_id: str, company: str, status: str, days_ago: int, **extra) -> dict:
    job = {
        "id": job_id,
        "company": company,
        "title": "Data Engineer",
        "location": extra.pop("location", "Remote, US"),
        "source": extra.pop("source", "greenhouse"),
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
    _job("open-1", "Vantage AI", "discovered", 1, salary_min=150000, region="us"),
    # US location so Open KPI still counts it (India discovery is off by
    # default); stamped region=india so the list Region filter can isolate it.
    _job("open-2", "Cobalt Labs", "discovered", 4, region="india", source="lever"),
    _job("applied-1", "Borealis Data", "applied", 9, applied_at=f"{_iso(9)}T12:00:00Z"),
    _job("applied-2", "Aster Works", "applied", 12, applied_at=f"{_iso(12)}T12:00:00Z"),
    _job("progress-1", "Umbra Systems", "filling", 2),
    _job("progress-2", "Helix Labs", "resume_ready", 2, source="lever"),
    _job("progress-3", "Nimbus AI", "resume_ready", 3, region="india", source="greenhouse"),
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
    "extras-filter",
    "region-filter",
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
    "extras-filter": "",
    "region-filter": "",
}

# Distinctive Open-family filter set.
CONFIGURED_OPEN = {
    **DEFAULT_CONTROLS,
    "search": "vantage",
    "source-filter": "greenhouse",
    "sort-by": "company",
    "work-mode-filter": "remote",
    "salary-filter": "ge100",
    "region-filter": "us",
}
CONFIGURED_OPEN_ACTIVE = 6

# Distinctive Applied-family filter set.
CONFIGURED_APPLIED = {
    **DEFAULT_CONTROLS,
    "yoe-filter": "le5",
    "date-filter": "7d",
}
CONFIGURED_APPLIED_ACTIVE = 2

LEGACY_FLAT = {"sort": "company", "mode": "remote", "search": "vantage"}
LEGACY_OPEN_CONTROLS = {
    **DEFAULT_CONTROLS,
    "search": "vantage",
    "sort-by": "company",
    "work-mode-filter": "remote",
}
LEGACY_OPEN_ACTIVE = 3

# Today preset: US, ≤5 YOE, posted last 2 days; other dropdowns All / None / Posted.
TODAY_CONTROLS = {
    **DEFAULT_CONTROLS,
    "yoe-filter": "le5",
    "date-filter": "2d",
    "region-filter": "us",
}
TODAY_ACTIVE = 3
TODAY_STORED = {
    "search": "",
    "source": "",
    "group": "none",
    "sort": "date",
    "mode": "",
    "yoe": "le5",
    "date": "2d",
    "salary": "",
    "extras": "",
    "region": "us",
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


def read_controls(page) -> dict[str, str]:
    """Current value of every list-filter control, as the user sees it."""
    return page.evaluate(
        "ids => Object.fromEntries(ids.map("
        "id => [id, document.getElementById(id)?.value ?? null]))",
        FILTER_CONTROL_IDS,
    )


def badge(page) -> str:
    """The Filters button label (textContent: the CSS uppercases it)."""
    return (page.locator("#filters-toggle-label").text_content() or "").strip()


def list_job_count(page) -> int:
    """Jobs currently in the left sidebar list (not KPI totals)."""
    return page.locator("#job-list .job-row[data-id]").count()


def expected_badge(active: int, visible: int) -> str:
    if active > 0:
        return f"Filters · {active} · {visible}"
    return f"Filters · {visible}"


def expected_title(active: int, visible: int) -> str:
    jobs = f"{visible} {'job' if visible == 1 else 'jobs'} in this list"
    if active <= 0:
        return jobs
    filters = f"{active} {'filter' if active == 1 else 'filters'}"
    return f"{filters} · {jobs}"


def assert_filters_chrome(page, *, active: int, where: str) -> None:
    visible = list_job_count(page)
    got = badge(page)
    want = expected_badge(active, visible)
    assert got == want, f"{where}: badge {got!r} != {want!r} (list={visible})"
    title = (page.locator("#filters-toggle").get_attribute("title") or "").strip()
    want_title = expected_title(active, visible)
    assert title == want_title, f"{where}: title {title!r} != {want_title!r}"


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
    # Click-pinned open closes via toggle; move away clears any hover timer.
    page.mouse.move(4, 4)
    page.wait_for_timeout(80)


def configure_open_filters(page) -> None:
    open_filters_popover(page)
    set_select(page, "source-filter", "greenhouse")
    set_select(page, "sort-by", "company")
    set_select(page, "work-mode-filter", "remote")
    set_select(page, "salary-filter", "ge100")
    set_select(page, "region-filter", "us")
    close_filters_popover(page)
    set_search(page, "vantage")


def configure_applied_filters(page) -> None:
    open_filters_popover(page)
    set_select(page, "yoe-filter", "le5")
    set_select(page, "date-filter", "7d")
    close_filters_popover(page)


def wait_for_jobs(page) -> None:
    page.wait_for_function(
        """() => {
          const n = document.getElementById('stat-open')?.textContent;
          return n != null && n !== '' && n !== '—';
        }""",
        timeout=15000,
    )


def local_filter_blob(page) -> dict | None:
    raw = page.evaluate(f"() => localStorage.getItem({FILTER_STORAGE_KEY!r})")
    if not raw:
        return None
    return json.loads(raw)


def boot(page, base_url: str) -> None:
    page.goto(f"{base_url}/index.html")
    wait_for_jobs(page)


def check_per_family_filters_persist(page, base_url: str) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    boot(page, base_url)

    assert_controls(page, DEFAULT_CONTROLS, where="open (initial)")
    configure_open_filters(page)
    assert_controls(page, CONFIGURED_OPEN, where="open (after configuring)")
    assert_filters_chrome(page, active=CONFIGURED_OPEN_ACTIVE, where="open (after configuring)")

    stored = local_filter_blob(page)
    assert stored and isinstance(stored.get("open"), dict), (
        "filters must be written as opsFilterState.open"
    )
    assert stored["open"].get("search") == "vantage"
    assert stored["open"].get("region") == "us"

    # Other families start empty until configured.
    switch_section(page, "applied")
    assert_controls(page, DEFAULT_CONTROLS, where="applied (after open filters)")
    configure_applied_filters(page)
    assert_controls(page, CONFIGURED_APPLIED, where="applied (configured)")
    stored = local_filter_blob(page)
    assert stored.get("applied", {}).get("yoe") == "le5"

    # Open family restores on return; Applied keeps its own.
    switch_section(page, "open")
    assert_controls(page, CONFIGURED_OPEN, where="open (restored)")
    switch_section(page, "applied")
    assert_controls(page, CONFIGURED_APPLIED, where="applied (restored)")

    # Pipeline sub-queues share one family.
    switch_section(page, "progress")
    assert_controls(page, DEFAULT_CONTROLS, where="progress (fresh pipeline)")
    open_filters_popover(page)
    set_select(page, "source-filter", "lever")
    close_filters_popover(page)
    switch_section(page, "stuck")
    assert_controls(
        page,
        {**DEFAULT_CONTROLS, "source-filter": "lever"},
        where="stuck (shared pipeline)",
    )
    switch_section(page, "ready")
    assert_controls(
        page,
        {**DEFAULT_CONTROLS, "source-filter": "lever"},
        where="ready (shared pipeline)",
    )
    stored = local_filter_blob(page)
    assert stored.get("pipeline", {}).get("source") == "lever"

    # Poll / re-render must not wipe UI filters.
    page.evaluate("() => typeof poll === 'function' && poll()")
    page.wait_for_timeout(200)
    switch_section(page, "open")
    assert_controls(page, CONFIGURED_OPEN, where="after poll()")

    # Reload restores per-family map.
    page.reload()
    wait_for_jobs(page)
    assert_controls(page, CONFIGURED_OPEN, where="open (after reload)")
    switch_section(page, "applied")
    assert_controls(page, CONFIGURED_APPLIED, where="applied (after reload)")
    switch_section(page, "progress")
    assert_controls(
        page,
        {**DEFAULT_CONTROLS, "source-filter": "lever"},
        where="pipeline (after reload)",
    )

    # Clear only resets the active family.
    switch_section(page, "open")
    open_filters_popover(page)
    page.click("#filters-popover-clear")
    page.wait_for_timeout(120)
    close_filters_popover(page)
    assert_controls(page, DEFAULT_CONTROLS, where="open (after clear)")
    switch_section(page, "applied")
    assert_controls(page, CONFIGURED_APPLIED, where="applied (open cleared, applied kept)")
    switch_section(page, "open")
    assert_controls(page, DEFAULT_CONTROLS, where="open (still cleared)")

    assert not errors, f"page errors {errors}"
    print("ok: per-family filters persist across tabs, poll, reload, clear")


def check_localstorage_new_session(page, base_url: str) -> None:
    """Opening Ops with a seeded per-family map restores each tab's Filters."""
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    blob = {
        "open": {
            "search": "vantage",
            "source": "greenhouse",
            "group": "none",
            "sort": "company",
            "mode": "remote",
            "yoe": "",
            "date": "",
            "salary": "ge100",
            "extras": "",
            "region": "us",
        },
        "applied": {"yoe": "le5", "date": "7d"},
    }
    page.add_init_script(
        "localStorage.setItem(%s, %s);"
        % (json.dumps(FILTER_STORAGE_KEY), json.dumps(json.dumps(blob)))
    )
    boot(page, base_url)
    assert_controls(page, CONFIGURED_OPEN, where="fresh session open")
    switch_section(page, "applied")
    assert_controls(page, CONFIGURED_APPLIED, where="fresh session applied")
    assert not errors, f"page errors {errors}"
    print("ok: localStorage opsFilterState map restores per-family Filters")


def check_legacy_flat_migration(page, base_url: str) -> None:
    """Pre-scoping flat sessionStorage opsFilterState becomes Open-only."""
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    boot(page, base_url)
    page.evaluate(
        "blob => { sessionStorage.clear(); sessionStorage.setItem('opsFilterState', blob); }",
        json.dumps(LEGACY_FLAT),
    )
    page.reload()
    wait_for_jobs(page)
    assert_controls(page, LEGACY_OPEN_CONTROLS, where="open (legacy flat migration)")
    switch_section(page, "applied")
    assert_controls(page, DEFAULT_CONTROLS, where="applied (not legacy flat)")
    stored = local_filter_blob(page)
    assert stored and stored.get("open", {}).get("search") == "vantage", (
        "migrated state must be written to opsFilterState.open"
    )
    assert not errors, f"page errors {errors}"
    print("ok: legacy flat sessionStorage migrates into Open family")


def check_by_queue_migration(page, base_url: str) -> None:
    """Per-queue sessionStorage keeps Open vs Applied; pipeline keys merge."""
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    boot(page, base_url)
    by_queue = {
        "open": {"sort": "company", "mode": "remote", "search": "vantage"},
        "applied": {"yoe": "le5"},
        "stuck": {"source": "lever"},
    }
    page.evaluate(
        "blob => { sessionStorage.clear(); "
        "sessionStorage.setItem('opsFilterStateByQueue', blob); }",
        json.dumps(by_queue),
    )
    page.reload()
    wait_for_jobs(page)
    assert_controls(page, LEGACY_OPEN_CONTROLS, where="open (by-queue migration)")
    switch_section(page, "applied")
    assert_controls(
        page,
        {**DEFAULT_CONTROLS, "yoe-filter": "le5"},
        where="applied (by-queue migration)",
    )
    switch_section(page, "stuck")
    assert_controls(
        page,
        {**DEFAULT_CONTROLS, "source-filter": "lever"},
        where="stuck (legacy key -> pipeline)",
    )
    stored = local_filter_blob(page)
    assert stored.get("pipeline", {}).get("source") == "lever"
    assert not errors, f"page errors {errors}"
    print("ok: per-queue sessionStorage migrates into family map")


def _kpi(page, stat_id: str) -> str:
    return (page.locator(f"#{stat_id}").text_content() or "").strip()


def check_kpi_matches_filtered_list(page, base_url: str) -> None:
    """Each KPI uses its family's filters; active tab KPI matches sidebar."""
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    boot(page, base_url)
    assert _kpi(page, "stat-open") == "2", f"fixture Open KPI {_kpi(page, 'stat-open')!r} != '2'"
    assert _kpi(page, "stat-applied") == "2"
    assert _kpi(page, "stat-progress") == "3"

    open_filters_popover(page)
    set_select(page, "region-filter", "india")
    close_filters_popover(page)
    ids = page.evaluate(
        "() => [...document.querySelectorAll('.job-row[data-id]')].map("
        "el => el.getAttribute('data-id'))"
    )
    assert ids == ["open-2"], f"India region should list only open-2, got {ids}"
    assert_filters_chrome(page, active=1, where="open + india region")
    assert _kpi(page, "stat-open") == "1"
    assert list_job_count(page) == 1
    open_title = page.locator('#mission-stats .mstat[data-queue="open"]').get_attribute("title") or ""
    assert "1 of 2" in open_title, f"Open KPI title should show unfiltered total, got {open_title!r}"
    # Other families keep their own (empty) filters — not Open's India filter.
    assert _kpi(page, "stat-applied") == "2"
    assert _kpi(page, "stat-progress") == "3"

    # Applied uses its own (empty) filter family — not Open's India filter.
    switch_section(page, "applied")
    assert_controls(page, DEFAULT_CONTROLS, where="applied (no open filters)")
    assert_filters_chrome(page, active=0, where="applied unfiltered")
    assert list_job_count(page) == 2, "Applied sidebar should show both applied jobs"
    assert _kpi(page, "stat-applied") == "2"
    # Open KPI still reflects Open family's saved India filter.
    assert _kpi(page, "stat-open") == "1"
    assert not errors, f"page errors {errors}"
    print("ok: KPI counts use per-family filters; families do not leak")


def check_in_progress_kpi_matches_filtered_list(page, base_url: str) -> None:
    """IN PROGRESS number equals sidebar length when Filters hide parked jobs."""
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    boot(page, base_url)
    switch_section(page, "progress")
    assert _kpi(page, "stat-progress") == "3"
    assert list_job_count(page) == 3
    assert_filters_chrome(page, active=0, where="progress unfiltered")

    open_filters_popover(page)
    set_select(page, "sort-by", "company")
    close_filters_popover(page)
    set_search(page, "umbra")
    assert _kpi(page, "stat-progress") == "1"
    assert list_job_count(page) == 1
    ids = page.evaluate(
        "() => [...document.querySelectorAll('.job-row[data-id]')].map("
        "el => el.getAttribute('data-id'))"
    )
    assert ids == ["progress-1"], f"search+sort should leave Umbra only, got {ids}"
    assert_filters_chrome(page, active=2, where="progress + umbra + sort")

    switch_section(page, "stuck")
    assert_controls(
        page,
        {**DEFAULT_CONTROLS, "sort-by": "company", "search": "umbra"},
        where="stuck shares pipeline filters",
    )
    switch_section(page, "open")
    assert_controls(page, DEFAULT_CONTROLS, where="open does not share pipeline")

    switch_section(page, "progress")
    open_filters_popover(page)
    page.click("#filters-popover-clear")
    page.wait_for_timeout(120)
    close_filters_popover(page)
    assert _kpi(page, "stat-progress") == "3"
    assert list_job_count(page) == 3
    assert not errors, f"page errors {errors}"
    print("ok: In Progress KPI matches filtered sidebar; pipeline family shared")


def check_posted_windows_static() -> None:
    """HTML options + Today preset payload; JS unit tests for 1d/2d/3d matching."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    date_block = html.split('id="date-filter"', 1)[1].split("</select>", 1)[0]
    assert 'value="1d"' in date_block and "1 day" in date_block
    assert 'value="2d"' in date_block and "2 days" in date_block
    assert 'value="3d"' in date_block and "Last 3d" in date_block
    assert 'value="7d"' in date_block and "Last 7d" in date_block
    assert 'id="filters-today"' in html
    proc = subprocess.run(
        ["node", str(ROOT / "dashboard" / "test_posted_date_filter.js")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "OK test_posted_date_filter.js" in proc.stdout
    extras_block = html.split('id="extras-filter"', 1)[1].split("</select>", 1)[0]
    assert 'id="status-filter"' not in html
    assert 'value="no_jd"' in extras_block and "No / incomplete JD" in extras_block
    incl = subprocess.run(
        ["node", str(ROOT / "dashboard" / "test_list_filters_inclusive.js")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if incl.returncode != 0:
        sys.stderr.write(incl.stdout)
        sys.stderr.write(incl.stderr)
    assert incl.returncode == 0, incl.stderr or incl.stdout
    assert "OK test_list_filters_inclusive.js" in incl.stdout
    print("ok: posted date windows + Today preset payload (static/js)")


def check_today_preset_and_posted_windows(page, base_url: str) -> None:
    """Today button sets the screenshot preset (POSTED ≤ 2 days) and persists."""
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    boot(page, base_url)

    open_filters_popover(page)
    date_options = page.evaluate(
        """() => [...document.querySelectorAll('#date-filter option')].map(
          o => [o.value, (o.textContent || '').trim()])"""
    )
    by_value = {value: label for value, label in date_options}
    assert by_value.get("1d") == "1 day", f"1 day option missing: {date_options}"
    assert by_value.get("2d") == "2 days", f"2 days option missing: {date_options}"
    assert by_value.get("3d") == "Last 3d", f"Last 3d option missing: {date_options}"
    assert by_value.get("7d") == "Last 7d", f"Last 7d option missing: {date_options}"
    assert page.locator("#filters-today").count() == 1, "Today button missing"

    set_select(page, "date-filter", "3d")
    close_filters_popover(page)
    ids = page.evaluate(
        "() => [...document.querySelectorAll('.job-row[data-id]')].map("
        "el => el.getAttribute('data-id'))"
    )
    assert ids == ["open-1"], f"Last 3d should keep only open-1, got {ids}"

    open_filters_popover(page)
    set_select(page, "date-filter", "7d")
    set_select(page, "source-filter", "lever")
    set_select(page, "group-by", "company")
    set_select(page, "sort-by", "company")
    set_select(page, "work-mode-filter", "remote")
    set_select(page, "yoe-filter", "le3")
    set_select(page, "salary-filter", "ge200")
    set_select(page, "extras-filter", "has_url")
    set_select(page, "region-filter", "india")
    close_filters_popover(page)
    set_search(page, "vantage")

    open_filters_popover(page)
    page.click("#filters-today")
    page.wait_for_timeout(120)
    today_expected = {**TODAY_CONTROLS, "search": "vantage"}
    assert_controls(page, today_expected, where="after Today click")
    stored = local_filter_blob(page)
    assert stored and isinstance(stored.get("open"), dict), (
        "Today must persist to opsFilterState.open"
    )
    for key, want in TODAY_STORED.items():
        if key == "search":
            assert stored["open"].get("search") == "vantage", stored
            continue
        assert stored["open"].get(key) == want, (
            f"stored open.{key}={stored['open'].get(key)!r} != {want!r}"
        )
    assert_filters_chrome(page, active=TODAY_ACTIVE + 1, where="Today + preserved search")

    page.reload()
    wait_for_jobs(page)
    assert_controls(page, today_expected, where="after Today reload")
    assert not errors, f"page errors {errors}"
    print("ok: Today preset applies POSTED 2d + US + ≤5 YOE and persists")


def main() -> int:
    from playwright.sync_api import sync_playwright

    check_posted_windows_static()

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
                for check in (
                    check_per_family_filters_persist,
                    check_localstorage_new_session,
                    check_legacy_flat_migration,
                    check_by_queue_migration,
                    check_kpi_matches_filtered_list,
                    check_in_progress_kpi_matches_filtered_list,
                    check_today_preset_and_posted_windows,
                ):
                    context = browser.new_context()  # fresh storage each time
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
