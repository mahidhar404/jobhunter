#!/usr/bin/env python3
"""Unit tests for configurable dashboard pruning."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import server as srv  # noqa: E402


def test_prune_subset_moves_only_selected_reason() -> None:
    jobs = {
        "jobs": [
            {
                "id": "manager",
                "title": "Engineering Manager",
                "location": "New York, NY",
                "status": "discovered",
                "apply_url": "https://example.com/manager",
            },
            {
                "id": "canada",
                "title": "Data Scientist",
                "location": "Toronto, Canada",
                "status": "discovered",
                "apply_url": "https://example.com/canada",
            },
            {
                "id": "started",
                "title": "Engineering Manager",
                "location": "New York, NY",
                "status": "tailoring",
                "apply_url": "https://example.com/started",
            },
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        jobs_file.write_text(json.dumps(jobs))
        with (
            mock.patch.object(srv, "JOBS_FILE", jobs_file),
            mock.patch.object(srv, "JOBS_LOCK_FILE", jobs_file.with_suffix(".json.lock")),
            mock.patch.object(srv, "block_deleted_job", return_value=[]),
        ):
            moved = srv._auto_delete_sweep_once({"non_us_location"})
            saved = json.loads(jobs_file.read_text())["jobs"]

    by_id = {job["id"]: job for job in saved}
    assert moved == 1
    assert by_id["canada"]["status"] == "deleted"
    assert by_id["canada"]["deleted_reason"] == "non_us_location"
    assert by_id["manager"]["status"] == "discovered"
    assert by_id["started"]["status"] == "tailoring"


def test_anduril_us_person_ts_sweep_prunes_and_tags() -> None:
    """Full JD (not truncated preview) drives US Person + obtain-TS prune and chips."""
    jd = (
        "U.S. Person status is required as this position needs to access "
        "export controlled data. Eligibility to obtain/maintain a US Top Secret "
        "clearance is also desirable."
    )
    jobs = {
        "jobs": [
            {
                "id": "anduril-ml-infra",
                "title": "Software Engineer - ML Infrastructure",
                "company": "Anduril",
                "location": "Costa Mesa, CA, US",
                "status": "discovered",
                "job_description": (
                    "Anduril intro … [full text in resumes/<id>/jd_full.txt]"
                ),
                "apply_url": "https://example.com/anduril-ml",
            },
            {
                "id": "keep-sponsor",
                "title": "ML Engineer",
                "company": "Acme",
                "location": "Remote, US",
                "status": "discovered",
                "job_description": "We are unable to sponsor visas for this role.",
                "apply_url": "https://example.com/keep-sponsor",
            },
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        jobs_file = root / "jobs.json"
        jobs_file.write_text(json.dumps(jobs))
        jd_dir = root / "anduril-ml-infra"
        jd_dir.mkdir()
        (jd_dir / "jd_full.txt").write_text(jd, encoding="utf-8")
        with (
            mock.patch.object(srv, "JOBS_FILE", jobs_file),
            mock.patch.object(srv, "JOBS_LOCK_FILE", jobs_file.with_suffix(".json.lock")),
            mock.patch.object(srv, "RESUMES_DIR", root),
            mock.patch.object(srv, "block_deleted_job", return_value=[]),
        ):
            moved = srv._auto_delete_sweep_once(
                {"clearance_or_intel", "citizenship_or_greencard"}
            )
            saved = json.loads(jobs_file.read_text())["jobs"]

    by_id = {job["id"]: job for job in saved}
    assert moved == 1
    assert by_id["anduril-ml-infra"]["status"] == "deleted"
    assert by_id["anduril-ml-infra"]["deleted_reason"] in (
        "clearance_or_intel",
        "citizenship_or_greencard",
    )
    assert by_id["anduril-ml-infra"]["clearance"] is True
    assert by_id["anduril-ml-infra"]["us_person"] is True
    assert by_id["keep-sponsor"]["status"] == "discovered"
    assert by_id["keep-sponsor"]["clearance"] is False
    assert by_id["keep-sponsor"]["us_person"] is False


def test_prune_settings_round_trip_and_defaults() -> None:
    with tempfile.TemporaryDirectory() as td:
        settings_file = Path(td) / "prune_settings.json"
        with mock.patch.object(srv, "PRUNE_SETTINGS_FILE", settings_file):
            defaults = srv.load_prune_settings()
            assert defaults["interval_s"] == 300
            assert set(defaults["reasons"]) == set(srv.PRUNE_REASON_CODES)

            saved = srv.save_prune_settings({
                "interval_s": 3600,
                "reasons": ["clearance_or_intel", "non_us_location"],
            })
            reloaded = srv.load_prune_settings()

    assert saved == reloaded
    assert reloaded == {
        "interval_s": 3600,
        "reasons": ["non_us_location", "clearance_or_intel"],
    }


def test_prune_settings_missing_keys_do_not_keyerror() -> None:
    """Partial on-disk JSON must not raise KeyError in load or schedule helpers."""
    with tempfile.TemporaryDirectory() as td:
        settings_file = Path(td) / "prune_settings.json"
        with mock.patch.object(srv, "PRUNE_SETTINGS_FILE", settings_file):
            settings_file.write_text("{}")
            loaded = srv.load_prune_settings()
            assert "interval_s" in loaded and "reasons" in loaded
            # Schedule helper tolerates a thin dict (no KeyError).
            assert srv._run_scheduled_prune_once({}) == 0
            settings_file.write_text(json.dumps({"reasons": ["stale_listing"]}))
            loaded2 = srv.load_prune_settings()
            assert loaded2["interval_s"] == 300
            assert loaded2["reasons"] == ["stale_listing"]


def test_prune_settings_reject_unknown_reason_and_interval() -> None:
    with tempfile.TemporaryDirectory() as td:
        settings_file = Path(td) / "prune_settings.json"
        with mock.patch.object(srv, "PRUNE_SETTINGS_FILE", settings_file):
            try:
                srv.save_prune_settings({"interval_s": 123, "reasons": ["made_up"]})
            except ValueError as exc:
                assert "interval" in str(exc) or "reason" in str(exc)
            else:
                raise AssertionError("invalid prune settings were accepted")


def test_scheduled_prune_uses_persisted_reason_subset() -> None:
    settings = {
        "interval_s": 900,
        "reasons": ["clearance_or_intel"],
    }
    with mock.patch.object(srv, "_auto_delete_sweep_once", return_value=4) as sweep:
        moved = srv._run_scheduled_prune_once(settings)

    assert moved == 4
    sweep.assert_called_once_with({"clearance_or_intel"})


def test_prune_uses_persisted_discovery_regions() -> None:
    jobs = {
        "jobs": [{
            "id": "india-role",
            "title": "Data Scientist",
            "location": "Bihar",
            "status": "discovered",
            "apply_url": "https://example.com/india-role",
        }]
    }
    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        jobs_file.write_text(json.dumps(jobs))
        with (
            mock.patch.object(srv, "JOBS_FILE", jobs_file),
            mock.patch.object(srv, "JOBS_LOCK_FILE", jobs_file.with_suffix(".json.lock")),
            mock.patch.object(srv, "enabled_discovery_regions", return_value=["us", "india"]),
            mock.patch.object(srv, "block_deleted_job", return_value=[]),
        ):
            moved = srv._auto_delete_sweep_once({"non_us_location"})
            saved = json.loads(jobs_file.read_text())["jobs"][0]

    assert moved == 0
    assert saved["status"] == "discovered"


def test_stale_listing_prune_is_optional_and_defaults_on() -> None:
    assert srv.STALE_LISTING_MAX_AGE_DAYS == 10
    too_old = (datetime.now(timezone.utc) - timedelta(days=11)).date().isoformat()
    still_fresh = (datetime.now(timezone.utc) - timedelta(days=9)).date().isoformat()
    jobs = {
        "jobs": [
            {
                "id": "old-role",
                "title": "Data Scientist",
                "location": "Remote, US",
                "date_posted": too_old,
                "status": "discovered",
                "apply_url": "https://example.com/old-role",
            },
            {
                "id": "fresh-role",
                "title": "Data Scientist",
                "location": "Remote, US",
                "date_posted": still_fresh,
                "status": "discovered",
                "apply_url": "https://example.com/fresh-role",
            },
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        settings_file = Path(td) / "prune_settings.json"
        jobs_file.write_text(json.dumps(jobs))
        with (
            mock.patch.object(srv, "JOBS_FILE", jobs_file),
            mock.patch.object(srv, "JOBS_LOCK_FILE", jobs_file.with_suffix(".json.lock")),
            mock.patch.object(srv, "PRUNE_SETTINGS_FILE", settings_file),
            mock.patch.object(srv, "block_deleted_job", return_value=[]),
        ):
            assert "stale_listing" in srv.load_prune_settings()["reasons"]
            assert srv._auto_delete_sweep_once({"stale_listing"}) == 1
            saved = {j["id"]: j for j in json.loads(jobs_file.read_text())["jobs"]}

    assert saved["old-role"]["status"] == "deleted"
    assert saved["old-role"]["deleted_reason"] == "stale_listing"
    assert saved["fresh-role"]["status"] == "discovered"


def test_stale_does_not_use_created_at() -> None:
    """Discovery time must not count as a posted date for stale prune."""
    old_created = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    jobs = {
        "jobs": [
            {
                "id": "undated-old-discovery",
                "title": "ML Engineer",
                "location": "Austin, TX",
                "status": "discovered",
                "apply_url": "https://example.com/undated",
                "date_posted": None,
                "created_at": old_created,
                "job_description": "3+ years of experience building models.",
            },
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        jobs_file.write_text(json.dumps(jobs))
        with (
            mock.patch.object(srv, "JOBS_FILE", jobs_file),
            mock.patch.object(srv, "JOBS_LOCK_FILE", jobs_file.with_suffix(".json.lock")),
            mock.patch.object(srv, "block_deleted_job", return_value=[]),
        ):
            moved = srv._auto_delete_sweep_once({"stale_listing"})
            saved = {j["id"]: j for j in json.loads(jobs_file.read_text())["jobs"]}
    assert moved == 0
    assert saved["undated-old-discovery"]["status"] == "discovered"


def test_stale_uses_exact_date_posted_only() -> None:
    """Stale prune / hide must ignore date_posted_fallback (UI ``~``)."""
    old_approx = (datetime.now(timezone.utc) - timedelta(days=45)).date().isoformat()
    jobs = {
        "jobs": [
            {
                "id": "fallback-only",
                "title": "ML Engineer",
                "location": "Austin, TX",
                "status": "discovered",
                "apply_url": "https://example.com/fallback-posted",
                "date_posted": None,
                "date_posted_fallback": old_approx,
                "job_description": "3+ years of experience building models.",
            },
            {
                "id": "exact-old",
                "title": "Analyst",
                "location": "Remote, US",
                "status": "discovered",
                "apply_url": "https://example.com/exact-old",
                "date_posted": old_approx,
                "job_description": "2+ years of experience.",
            },
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        jobs_file.write_text(json.dumps(jobs))
        with (
            mock.patch.object(srv, "JOBS_FILE", jobs_file),
            mock.patch.object(srv, "JOBS_LOCK_FILE", jobs_file.with_suffix(".json.lock")),
            mock.patch.object(srv, "block_deleted_job", return_value=[]),
        ):
            moved = srv._auto_delete_sweep_once({"stale_listing"})
            saved = {j["id"]: j for j in json.loads(jobs_file.read_text())["jobs"]}

    assert moved == 1
    assert saved["fallback-only"]["status"] == "discovered"
    assert saved["exact-old"]["status"] == "deleted"
    assert saved["exact-old"]["deleted_reason"] == "stale_listing"

    js = (ROOT / "dashboard" / "static" / "app.js").read_text()
    stale_fn = js.split("function isStaleListing(job)", 1)[1].split(
        "function ", 1
    )[0]
    assert "jobPostedDisplay" in stale_fn
    assert "date_posted_fallback" not in stale_fn


def test_approx_stamps_do_not_trigger_prune_sweep() -> None:
    """``~`` fallback stamps (YOE / posted) must not move jobs to Deleted."""
    old_approx = (datetime.now(timezone.utc) - timedelta(days=45)).date().isoformat()
    jobs = {
        "jobs": [
            {
                "id": "approx-yoe-only",
                "title": "Data Scientist",
                "location": "Remote, US",
                "status": "discovered",
                "apply_url": "https://example.com/approx-yoe",
                # Display-only ~7; no strict min_yoe / no hard YOE in JD text
                "min_yoe": None,
                "min_yoe_fallback": 10,
                "job_description": (
                    "7+ years of professional software/ML engineering exper"
                ),
            },
            {
                "id": "approx-posted-only",
                "title": "ML Engineer",
                "location": "Austin, TX",
                "status": "discovered",
                "apply_url": "https://example.com/approx-posted",
                "date_posted": None,
                "date_posted_fallback": old_approx,
                "job_description": "3+ years of experience building models.",
            },
            {
                "id": "strict-old-posted",
                "title": "Analyst",
                "location": "Remote, US",
                "status": "discovered",
                "apply_url": "https://example.com/strict-old",
                "date_posted": old_approx,
                "job_description": "2+ years of experience.",
            },
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        jobs_file = Path(td) / "jobs.json"
        jobs_file.write_text(json.dumps(jobs))
        with (
            mock.patch.object(srv, "JOBS_FILE", jobs_file),
            mock.patch.object(srv, "JOBS_LOCK_FILE", jobs_file.with_suffix(".json.lock")),
            mock.patch.object(srv, "block_deleted_job", return_value=[]),
        ):
            moved = srv._auto_delete_sweep_once(
                {"excessive_yoe", "stale_listing"}
            )
            saved = {
                j["id"]: j for j in json.loads(jobs_file.read_text())["jobs"]
            }

    assert moved == 1
    assert saved["approx-yoe-only"]["status"] == "discovered"
    assert saved["approx-posted-only"]["status"] == "discovered"
    assert saved["strict-old-posted"]["status"] == "deleted"
    assert saved["strict-old-posted"]["deleted_reason"] == "stale_listing"


def test_app_js_open_excludes_needs_url_listings() -> None:
    """URL-less recovered stubs stay out of Open counts/list, not All."""
    js = (ROOT / "dashboard" / "static" / "app.js").read_text()
    assert "function isNeedsUrlListing(job)" in js
    open_case = js.split('case "open":', 1)[1].split("case ", 1)[0]
    assert "isNeedsUrlListing" in open_case
    count_fn = js.split("function countBucket(bucket", 1)[1].split(
        "function ", 1
    )[0]
    assert 'bucket === "open" && isNeedsUrlListing' in count_fn
    # Must NOT bury stubs via isHiddenUntouchedListing (All should still see them).
    hide_fn = js.split("function isHiddenUntouchedListing(job)", 1)[1].split(
        "function ", 1
    )[0]
    # Comment may mention isNeedsUrlListing; the return predicate must not call it.
    assert "|| isNeedsUrlListing" not in hide_fn
    assert "isNeedsUrlListing(job)" not in hide_fn
    assert "needs_url" not in hide_fn


def test_app_js_stale_and_yoe_hide_ignore_approx() -> None:
    """Client hide mirrors server: strict YOE / exact posted only."""
    js = (ROOT / "dashboard" / "static" / "app.js").read_text()
    assert "function jobMinYoe(job)" in js
    assert "Strict YOE only" in js
    assert "jobRequiresExcessiveYoe(job)" in js
    # jobRequiresExcessiveYoe must call jobMinYoe, not jobMinYoeDisplay
    yoe_fn = js.split("function jobRequiresExcessiveYoe(job)", 1)[1].split(
        "function ", 1
    )[0]
    assert "jobMinYoe(job)" in yoe_fn
    assert "jobMinYoeDisplay" not in yoe_fn
    assert "min_yoe_fallback" not in yoe_fn
    stale_fn = js.split("function isStaleListing(job)", 1)[1].split(
        "function ", 1
    )[0]
    assert "jobPostedDisplay" in stale_fn
    assert "approx" in stale_fn
    assert "date_posted_fallback" not in stale_fn
    assert "STALE_LISTING_MAX_AGE_DAYS = 10" in js
    assert "Listing posted more than 10 days ago" in js


def test_unresolved_apply_url_in_prune_defaults_and_alias() -> None:
    assert "unresolved_apply_url" in srv.PRUNE_REASON_CODES
    assert "closed_posting" in srv.PRUNE_REASON_CODES
    with tempfile.TemporaryDirectory() as td:
        settings_file = Path(td) / "prune_settings.json"
        with mock.patch.object(srv, "PRUNE_SETTINGS_FILE", settings_file):
            defaults = srv.load_prune_settings()
            assert "unresolved_apply_url" in defaults["reasons"]
            assert "closed_posting" in defaults["reasons"]
            # Legacy alias accepted when saving
            saved = srv.save_prune_settings({
                "interval_s": 300,
                "reasons": ["apply_resolve_failed", "stale_listing", "dead_apply_url"],
            })
            assert saved["reasons"] == [
                "stale_listing",
                "unresolved_apply_url",
                "closed_posting",
            ]


def test_unresolved_apply_url_chip_in_ui() -> None:
    js = (ROOT / "dashboard" / "static" / "app.js").read_text()
    html = (ROOT / "dashboard" / "static" / "index.html").read_text()
    assert "Unresolved URL" in js
    assert "unresolved_apply_url" in js
    assert 'data-prune-reason="unresolved_apply_url"' in html
    assert ".tag.unresolved-url" in html
    assert 'data-prune-reason="closed_posting"' in html
    assert ".tag.closed-posting" in html
    assert "dead/404" in js
    assert "closed/lever" in js


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("OK test_prune_controls")
