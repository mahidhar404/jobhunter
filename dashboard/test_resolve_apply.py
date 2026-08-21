#!/usr/bin/env python3
"""Dashboard hook for LinkedIn → ATS apply-URL resolution.

Dummy fixtures only — no live search, no applicant PII.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server as srv  # noqa: E402

APP_JS = HERE / "static" / "app.js"


class _FakeHandler:
    _handle_resolve_apply = srv.Handler._handle_resolve_apply

    def __init__(self):
        self.responses = []

    def _send_json(self, payload, status=200, **_kwargs):
        self.responses.append((status, payload))

    @property
    def last(self):
        return self.responses[-1]


def test_resolve_apply_is_posted_job_action():
    assert "resolve-apply" in srv.Handler._JOB_ACTION_POST
    method, takes_payload = srv.Handler._JOB_ACTION_POST["resolve-apply"]
    assert method == "_handle_resolve_apply"
    assert takes_payload is True


def test_handle_resolve_apply_writes_by_default():
    handler = _FakeHandler()
    fake_out = {
        "ok": True,
        "id": "li-1",
        "confidence": "high",
        "url": "https://emedlabsllc.applytojob.com/apply/FI24qAupbj/Analytics-Engineer",
        "apply_url": "https://emedlabsllc.applytojob.com/apply/FI24qAupbj/Analytics-Engineer",
        "dry_run": False,
    }
    with mock.patch("resolve_apply_urls.resolve_job_id", return_value=fake_out) as fn:
        handler._handle_resolve_apply("li-1", {})
    fn.assert_called_once()
    kwargs = fn.call_args.kwargs
    assert kwargs.get("write") is True
    status, payload = handler.last
    assert status == 200
    assert payload["confidence"] == "high"
    assert "applytojob.com" in payload["url"]


def test_handle_resolve_apply_honors_dry_run_payload():
    handler = _FakeHandler()
    fake_out = {"ok": True, "id": "li-1", "confidence": "medium", "dry_run": True}
    with mock.patch("resolve_apply_urls.resolve_job_id", return_value=fake_out) as fn:
        handler._handle_resolve_apply("li-1", {"write": False})
    assert fn.call_args.kwargs.get("write") is False
    assert handler.last[0] == 200


def test_handle_resolve_apply_missing_job_is_404():
    handler = _FakeHandler()
    with mock.patch(
        "resolve_apply_urls.resolve_job_id",
        return_value={"ok": False, "error": "no job found with id 'ghost'"},
    ):
        handler._handle_resolve_apply("ghost", {})
    status, payload = handler.last
    assert status == 404
    assert "error" in payload


def test_ui_exposes_resolve_ats_action():
    src = APP_JS.read_text(encoding="utf-8")
    assert "resolveApplyUrl" in src
    assert "/resolve-apply" in src
    assert "Resolve ATS" in src
    assert "isAggregatorHost" in src
    assert "applyUrlNeedsResolution" in src
    assert "meta-host unresolved" in src
    assert "not_logged_in" in src
    assert "open_linkedin_resolve.sh" in src
    assert "blocked_captcha" in src
    assert "applyResolveLabel" in src
    assert "applyResolveNoteHtml" in src
    assert "apply_resolve_status" in src
    assert "Resolve failed:" in src
    assert "no external apply" in src


def test_ui_mentions_linkedin_login_hint():
    src = APP_JS.read_text(encoding="utf-8")
    assert "Open LinkedIn resolve browser first" in src


def test_handle_resolve_apply_surfaces_resolve_fields():
    handler = _FakeHandler()
    fake_out = {
        "ok": True,
        "id": "li-1",
        "confidence": "low",
        "url": None,
        "reason": "no_external_apply",
        "apply_resolve_status": "no_external",
        "apply_resolve_reason": "no_external_apply",
        "apply_resolve_at": "2026-08-20T00:00:00+00:00",
        "apply_resolve_message": "No offsite Apply redirect found on LinkedIn.",
        "dry_run": False,
    }
    with mock.patch("resolve_apply_urls.resolve_job_id", return_value=fake_out):
        handler._handle_resolve_apply("li-1", {})
    status, payload = handler.last
    assert status == 200
    assert payload["apply_resolve_status"] == "no_external"
    assert payload["apply_resolve_reason"] == "no_external_apply"


def test_fill_cft_excludes_linkedin_cdp_port():
    markers = srv._fill_cft_exclude_markers()
    assert any("linkedin_resolve_profile" in m for m in markers)
    assert any("18801" in m for m in markers)

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("OK test_resolve_apply")
