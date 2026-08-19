from datetime import datetime, timezone
from unittest import mock

import server as srv
from stats_aggregate import aggregate_stats


NOW = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)


def _job(job_id, status, **fields):
    return {"id": job_id, "status": status, "company": f"Company {job_id}", "title": f"Role {job_id}", **fields}


def test_aggregate_stats_covers_dates_funnel_aging_sources_cities_and_followups():
    jobs = [
        _job(
            "recent-follow-up",
            "applied",
            applied_at="2026-08-10T12:00:00Z",
            source="LinkedIn",
            applied_address="10 Main St, Austin, TX 78701",
        ),
        _job(
            "recent-not-due",
            "applied",
            applied_at="2026-08-12",
            site="linkedin",
            applied_address="22 Oak St, Austin, TX 78702",
        ),
        _job(
            "month-follow-up",
            "applied",
            applied_at="2026-07-20T09:00:00-04:00",
            source="Indeed",
            applied_address={"city": "Chicago", "state": "IL"},
        ),
        _job(
            "old-follow-up",
            "applied",
            applied_at="2026-06-01",
            source="Indeed",
            applied_address="Chicago, IL",
        ),
        _job("open-15", "discovered", created_at="2026-08-02T11:00:00Z", source="Indeed"),
        _job("open-31", "cancelled", created_at="2026-07-17", site="Greenhouse"),
        _job("unknown-open", "unexpected", created_at="not-a-date", source=""),
        _job("ready", "ready_for_review", created_at="2026-01-01", source="LinkedIn"),
        _job("stuck", "blocked_captcha", created_at="2026-01-01", source="Greenhouse"),
        _job("progress", "filling", created_at="2026-01-01", source="Indeed"),
        _job("deleted", "deleted", created_at="2020-01-01", source="DeletedSite"),
        _job("legacy-deleted", "skipped_manual", created_at="2020-01-01", source="DeletedSite"),
        _job("bad-applied-date", "applied", applied_at="not-a-date", source="Manual"),
    ]

    stats = aggregate_stats(jobs, now=NOW)

    assert stats["applied_week"] == 2
    assert stats["applied_month"] == 3
    assert stats["funnel"] == {
        "open": 3,
        "ready": 1,
        "stuck": 1,
        "progress": 1,
        "applied": 5,
    }
    assert stats["open_aging"] == {"over_14d": 2, "over_30d": 1}
    assert stats["by_source"][:5] == [
        {"name": "Indeed", "count": 4},
        {"name": "LinkedIn", "count": 3},
        {"name": "Greenhouse", "count": 2},
        {"name": "Manual", "count": 1},
        {"name": "Unknown", "count": 1},
    ]
    assert stats["by_city"] == [
        {"name": "Austin", "count": 2},
        {"name": "Chicago", "count": 2},
    ]
    assert stats["follow_ups_due"] == [
        {
            "id": "old-follow-up",
            "company": "Company old-follow-up",
            "title": "Role old-follow-up",
            "applied_at": "2026-06-01",
        },
        {
            "id": "month-follow-up",
            "company": "Company month-follow-up",
            "title": "Role month-follow-up",
            "applied_at": "2026-07-20T09:00:00-04:00",
        },
        {
            "id": "recent-follow-up",
            "company": "Company recent-follow-up",
            "title": "Role recent-follow-up",
            "applied_at": "2026-08-10T12:00:00Z",
        },
    ]


def test_aggregate_stats_does_not_mutate_jobs_and_handles_empty_input():
    jobs = [{"id": "one", "status": "discovered"}]
    original = [dict(jobs[0])]

    aggregate_stats(jobs, now=NOW)

    assert jobs == original
    assert aggregate_stats([], now=NOW) == {
        "applied_week": 0,
        "applied_month": 0,
        "funnel": {"open": 0, "ready": 0, "stuck": 0, "progress": 0, "applied": 0},
        "open_aging": {"over_14d": 0, "over_30d": 0},
        "by_source": [],
        "by_city": [],
        "follow_ups_due": [],
    }


def test_resume_ready_counts_as_progress_matching_app_js():
    """Parked cancel/generate-only jobs stay In Progress, not Open or Ready."""
    stats = aggregate_stats(
        [
            _job("parked", "resume_ready"),
            _job("live", "filling"),
            _job("ready", "ready_for_review"),
            _job("captcha", "blocked_captcha"),
        ],
        now=NOW,
    )
    assert stats["funnel"] == {
        "open": 0,
        "ready": 1,
        "stuck": 1,
        "progress": 2,
        "applied": 0,
    }


def test_stats_route_reads_jobs_once_and_returns_aggregate():
    data = {"jobs": [{"id": "one", "status": "discovered"}]}
    expected = {"applied_week": 0}
    handler = object.__new__(srv.Handler)
    handler.path = "/api/stats"
    handler._send_json = mock.MagicMock()

    with (
        mock.patch.object(srv, "read_jobs", return_value=data) as read_jobs,
        mock.patch.object(srv, "aggregate_stats", return_value=expected) as aggregate,
    ):
        handler.do_GET()

    read_jobs.assert_called_once_with()
    aggregate.assert_called_once_with(data["jobs"])
    handler._send_json.assert_called_once_with(expected)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("OK test_stats_aggregate")
