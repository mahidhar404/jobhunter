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
    writes = []
    handler = object.__new__(srv.Handler)
    handler._send_json = lambda body, status=200: responses.append((body, status))

    with (
        mock.patch.object(srv, "read_jobs", return_value=data),
        mock.patch.object(srv, "write_jobs", side_effect=lambda value: writes.append(value)),
    ):
        handler._handle_edit_applied(
            "example-role",
            {
                "title": "New title",
                "applied_address": "10 Main St, Austin, TX 78701",
                "applied_date": "2026-08-04",
            },
        )

    assert writes
    persisted = writes[0]["jobs"][0]
    assert persisted["title"] == "New title"
    assert persisted["applied_address"] == "10 Main St, Austin, TX 78701"
    assert persisted["applied_at"] == "2026-08-04"
    assert responses[0][1] == 200
    assert responses[0][0]["job"]["applied_address"] == persisted["applied_address"]
