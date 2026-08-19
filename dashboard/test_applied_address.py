from pathlib import Path
from unittest import mock

import server as srv


STATIC = Path(__file__).parent / "static"


def test_format_address_pick_requires_complete_address():
    assert srv._format_address_pick(
        {"line1": "10 Main St", "city": "Austin", "state": "TX", "zip": "78701"}
    ) == "10 Main St, Austin, TX 78701"
    assert srv._format_address_pick(
        {"line1": "10 Main St", "city": "Austin", "state": "TX"}
    ) is None


def test_applied_tables_render_persisted_address_with_unknown_fallback():
    for filename in ("app.js",):
        source = (STATIC / filename).read_text()
        assert 'trackingSortHeader("address", "Address")' in source
        assert 'appliedAddressText(job)' in source
        assert 'class="cell-address cell-muted"' in source
        assert 'title="${escapeHtml(address)}"' in source
        assert 'address || "—"' in source


def test_applied_address_is_sortable():
    for filename in ("app.js",):
        source = (STATIC / filename).read_text()
        assert '["address", "Address"]' in source
        assert 'case "address":' in source


def test_applied_edit_payload_is_allowlisted_and_normalized():
    fields = srv._validated_applied_edit(
        {
            "title": "  Senior Engineer ",
            "company": " Example Co ",
            "location": "",
            "applied_address": " 10 Main St, Austin, TX 78701 ",
            "applied_date": "2026-08-04",
            "status_detail": " Followed up ",
            "apply_url": "https://example.com/apply",
            "source": "manual",
            "status": "deleted",
        }
    )
    assert fields == {
        "title": "Senior Engineer",
        "company": "Example Co",
        "location": "",
        "applied_address": "10 Main St, Austin, TX 78701",
        "applied_at": "2026-08-04",
        "status_detail": "Followed up",
        "apply_url": "https://example.com/apply",
        "source": "manual",
    }


def test_applied_edit_rejects_invalid_date():
    try:
        srv._validated_applied_edit({"applied_date": "2026-99-40"})
    except ValueError as error:
        assert "valid YYYY-MM-DD" in str(error)
    else:
        raise AssertionError("invalid applied date was accepted")


def test_applied_table_hide_toggle_session_focus():
    for filename in ("app.js",):
        source = (STATIC / filename).read_text()
        assert "appliedTableHidden" in source
        assert "toggleAppliedTableHidden()" in source
        assert "setAppliedTableHidden(" in source
        assert "applied-tracking-hidden" in source
        assert "applied-table-toggle" in source
        assert "applied-focus-bar" in source
        assert "applied-table-back" in source
        assert "appliedFocus: true" in source
        assert 'if (next === "applied") appliedTableHidden = false' in source
        assert "opsAppliedTableHidden" not in source


def test_applied_rows_have_leftmost_edit_control_and_editor_actions():
    for filename in ("app.js",):
        source = (STATIC / filename).read_text()
        edit_header = source.index('<th class="cell-edit"')
        company_header = source.index('trackingSortHeader("company", "Company")')
        assert edit_header < company_header
        assert 'openAppliedEditor(' in source
        assert 'saveAppliedJob(' in source
        assert 'cancelAppliedEditor()' in source
        assert "/edit" in source


def test_applied_edit_handler_persists_and_returns_updated_job():
    data = {
        "jobs": [
            {
                "id": "example-role",
                "status": "applied",
                "title": "Old title",
                "company": "Example",
            }
        ]
    }
    responses = []
    handler = object.__new__(srv.Handler)
    handler._send_json = lambda body, status=200: responses.append((body, status))

    from contextlib import contextmanager

    @contextmanager
    def _fake_locked(*, allow_purge=False):
        yield data

    with mock.patch.object(srv, "locked_jobs_for_write", _fake_locked):
        handler._handle_edit_applied(
            "example-role",
            {
                "title": "New title",
                "applied_address": "10 Main St, Austin, TX 78701",
                "applied_date": "2026-08-04",
            },
        )

    persisted = data["jobs"][0]
    assert persisted["title"] == "New title"
    assert persisted["applied_address"] == "10 Main St, Austin, TX 78701"
    assert persisted["applied_at"] == "2026-08-04"
    assert responses[0][1] == 200
    assert responses[0][0]["job"]["applied_address"] == persisted["applied_address"]


def test_mark_submitted_resolves_missing_applied_address():
    data = {
        "jobs": [
            {
                "id": "needs-addr",
                "status": "ready_for_review",
                "title": "Engineer",
                "company": "Example",
                "session_key": "agent:job-hunter:job-needs-addr",
                "timeline": [],
            }
        ]
    }
    responses = []
    handler = object.__new__(srv.Handler)
    handler._send_json = lambda body, status=200: responses.append((body, status))

    from contextlib import contextmanager

    @contextmanager
    def _fake_locked(*, allow_purge=False):
        yield data

    with (
        mock.patch.object(srv, "locked_jobs_for_write", _fake_locked),
        mock.patch.object(
            srv,
            "resolve_applied_address_for_job",
            return_value="10 Main St, Austin, TX 78701",
        ) as resolve,
        mock.patch.object(srv, "_kill_process_tree"),
        mock.patch.object(srv, "abort_gateway_session"),
        mock.patch.object(srv, "_release_fill_job"),
        mock.patch.object(srv, "clear_fill_activity"),
        mock.patch.object(srv, "close_job_partyrock_tab", return_value={}),
        mock.patch.object(srv.subprocess, "run"),
    ):
        handler._handle_mark_submitted("needs-addr")

    resolve.assert_called_once()
    persisted = data["jobs"][0]
    assert persisted["status"] == "applied"
    assert persisted["applied_address"] == "10 Main St, Austin, TX 78701"
    assert responses[0][0]["ok"] is True


def test_mark_submitted_keeps_existing_applied_address():
    data = {
        "jobs": [
            {
                "id": "has-addr",
                "status": "ready_for_review",
                "title": "Engineer",
                "company": "Example",
                "applied_address": "99 Keep St, Chicago, IL 60601",
                "session_key": "agent:job-hunter:job-has-addr",
                "timeline": [],
            }
        ]
    }
    handler = object.__new__(srv.Handler)
    handler._send_json = lambda body, status=200: None

    from contextlib import contextmanager

    @contextmanager
    def _fake_locked(*, allow_purge=False):
        yield data

    with (
        mock.patch.object(srv, "locked_jobs_for_write", _fake_locked),
        mock.patch.object(
            srv,
            "resolve_applied_address_for_job",
            side_effect=AssertionError("should not resolve when present"),
        ),
        mock.patch.object(srv, "_kill_process_tree"),
        mock.patch.object(srv, "abort_gateway_session"),
        mock.patch.object(srv, "_release_fill_job"),
        mock.patch.object(srv, "clear_fill_activity"),
        mock.patch.object(srv, "close_job_partyrock_tab", return_value={}),
        mock.patch.object(srv.subprocess, "run"),
    ):
        handler._handle_mark_submitted("has-addr")

    assert data["jobs"][0]["applied_address"] == "99 Keep St, Chicago, IL 60601"


def test_app_js_idle_poll_backoff_and_etag():
    source = (STATIC / "app.js").read_text()
    assert "POLL_JOBS_MS_IDLE = 10000" in source
    assert "POLL_STATUS_MS_IDLE = 5000" in source
    assert "POLL_JOBS_MS_ACTIVE = 3000" in source
    assert "POLL_STATUS_MS_ACTIVE = 1500" in source
    assert "function syncPollTimers" in source
    assert "function hasActivePipelineJobs" in source
    assert "function invalidateJobsListCache()" in source
    assert 'headers["If-None-Match"]' in source
    assert "res.status === 304" in source
    assert "if (lastJobsJSON == null)" in source
    assert "syncPollTimers();" in source
    assert "setInterval(poll, 3000)" not in source
    assert "setInterval(pollStatus, 1500)" not in source


def test_resolve_applied_address_skips_non_us_job_location(tmp_path=None):
    """KZ / non-US jobs must not inherit the Remote→Chicago US apartment."""
    import tempfile
    from pathlib import Path as P

    with tempfile.TemporaryDirectory() as td:
        tex = P(td) / "resume.tex"
        tex.write_text(
            "Dummy Name\n405-555-0100 | Remote | dummy@example.test\n"
            r"\begin{document}" + "\n",
            encoding="utf-8",
        )
        job = {
            "id": "delivery-hero-sr-data-analyst-commercial-team",
            "location": "Almaty, kz",
            "work_mode": "remote",
        }
        with mock.patch.object(srv, "_find_resume_for_address", return_value=tex):
            assert srv.resolve_applied_address_for_job(job) is None



if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("OK test_applied_address")
