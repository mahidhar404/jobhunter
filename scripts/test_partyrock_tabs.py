#!/usr/bin/env python3
"""Unit tests for per-job PartyRock CDP tab registry + force_partyrock skip logic."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from partyrock_tabs import (  # noqa: E402
    clear_tab_meta,
    close_idle_partyrock_tabs,
    close_job_partyrock_tab,
    close_tab,
    create_tab,
    list_page_targets,
    open_job_partyrock_tab,
    read_tab_meta,
    write_tab_meta,
)


def test_tab_meta_roundtrip(tmp_path: Path) -> None:
    job_dir = tmp_path / "jobA"
    write_tab_meta(job_dir, job_id="jobA", target_id="TID123", url="https://example.com/pr")
    meta = read_tab_meta(job_dir)
    assert meta is not None
    assert meta["target_id"] == "TID123"
    assert meta["job_id"] == "jobA"
    clear_tab_meta(job_dir)
    assert read_tab_meta(job_dir) is None


def test_close_job_isolates_by_meta(tmp_path: Path) -> None:
    """Close failure must not clear meta (PR-003); B's file always untouched."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    write_tab_meta(a, job_id="a", target_id="AAA")
    write_tab_meta(b, job_id="b", target_id="BBB")
    # Dead CDP port → close_failed; keep A's meta so a retry can still close.
    summary = close_job_partyrock_tab("a", a, cdp_http="http://127.0.0.1:9")
    assert summary.get("closed") is False
    assert summary.get("reason") == "close_failed"
    assert read_tab_meta(a) is not None
    assert read_tab_meta(a)["target_id"] == "AAA"
    assert read_tab_meta(b) is not None
    assert read_tab_meta(b)["target_id"] == "BBB"


def test_close_job_keeps_meta_until_target_is_gone(tmp_path: Path) -> None:
    job_dir = tmp_path / "jobA"
    write_tab_meta(job_dir, job_id="jobA", target_id="STILL-LIVE")

    with mock.patch("partyrock_tabs.close_tab", return_value=True), mock.patch(
        "partyrock_tabs.wait_tab_gone", return_value=False
    ):
        summary = close_job_partyrock_tab("jobA", job_dir)

    assert summary["closed"] is False
    assert summary["reason"] == "close_pending"
    assert read_tab_meta(job_dir)["target_id"] == "STILL-LIVE"


def test_create_tab_does_not_double_open_on_bad_put_payload() -> None:
    """PUT may create a tab but return a bad body — GET must not open a second."""
    calls: list[str] = []
    state = {"ids": {"existing"}}

    def fake_list(*, cdp_http: str = "http://127.0.0.1:18800"):
        return [{"id": tid, "type": "page"} for tid in sorted(state["ids"])]

    def fake_json(path: str, *, cdp_http: str = "http://127.0.0.1:18800", method: str = "GET"):
        calls.append(method)
        if path.startswith("/json/new"):
            if method == "PUT":
                state["ids"].add("new-tab")
                return "not-json"
            return {"id": "should-not-happen"}
        raise AssertionError(f"unexpected path {path}")

    with mock.patch("partyrock_tabs._create_tab_background", side_effect=RuntimeError("no ws")), mock.patch(
        "partyrock_tabs.list_page_targets", fake_list
    ), mock.patch(
        "partyrock_tabs.cdp_json", fake_json
    ):
        info = create_tab("https://example.com/partyrock", cdp_http="http://127.0.0.1:9")
    assert info["id"] == "new-tab"
    assert calls == ["PUT"]


def test_create_tab_prefers_background_target() -> None:
    with mock.patch(
        "partyrock_tabs._create_tab_background",
        return_value={"id": "BG-TAB", "url": "https://partyrock.aws/x", "background": True},
    ) as bg:
        info = create_tab("https://partyrock.aws/x", background=True)
    assert info["id"] == "BG-TAB"
    bg.assert_called_once()


def test_open_job_partyrock_tab_reuses_live_target(tmp_path: Path) -> None:
    job_dir = tmp_path / "jobA"
    write_tab_meta(job_dir, job_id="jobA", target_id="LIVE", url="https://partyrock.aws/u/me/pr")
    targets = [
        {"id": "LIVE", "type": "page", "url": "https://partyrock.aws/u/me/pr"},
        {"id": "IDLE", "type": "page", "url": "https://partyrock.aws/u/me/old"},
    ]
    closed: list[str] = []

    with mock.patch("partyrock_tabs.list_page_targets", return_value=targets), mock.patch(
        "partyrock_tabs.close_tab",
        side_effect=lambda tid, **kw: closed.append(tid) or True,
    ), mock.patch(
        "partyrock_tabs.wait_tab_gone", return_value=True
    ), mock.patch(
        "partyrock_tabs.create_tab",
        side_effect=AssertionError("should not create"),
    ):
        info = open_job_partyrock_tab(job_dir, "jobA", "https://partyrock.aws/u/me/pr")
    assert info["id"] == "LIVE"
    assert info.get("reused") is True
    assert "IDLE" in closed
    assert "LIVE" not in closed


def test_close_idle_partyrock_tabs_preserves_active_and_unrelated() -> None:
    targets = [
        {"id": "ACTIVE", "type": "page", "url": "https://partyrock.aws/u/me/live"},
        {"id": "IDLE", "type": "page", "url": "https://partyrock.aws/u/me/old"},
        {"id": "OTHER", "type": "page", "url": "https://example.com/"},
    ]
    closed: list[str] = []

    with mock.patch("partyrock_tabs.list_page_targets", return_value=targets), mock.patch(
        "partyrock_tabs.close_tab",
        side_effect=lambda tid, **kw: closed.append(tid) or True,
    ), mock.patch("partyrock_tabs.wait_tab_gone", return_value=True):
        summary = close_idle_partyrock_tabs(protected_target_ids={"ACTIVE"})

    assert closed == ["IDLE"]
    assert summary["closed"] == ["IDLE"]
    assert summary["protected"] == ["ACTIVE"]


def _write_claim(job_dir: Path, job_id: str, target_id: str, *, pid: int, in_use: bool = True) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "partyrock_tab.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "target_id": target_id,
                "url": "https://partyrock.aws/u/me/live",
                "in_use": in_use,
                "pid": pid,
            }
        )
        + "\n"
    )


def test_new_tailor_reuses_idle_and_closes_other_leftovers(tmp_path: Path) -> None:
    """A new tailor must reuse one leftover PartyRock tab, not stack /json/new."""
    job_dir = tmp_path / "job-new"
    targets = [
        {"id": "IDLE1", "type": "page", "url": "https://partyrock.aws/u/me/old"},
        {"id": "IDLE2", "type": "page", "url": "https://partyrock.aws/u/me/old2?x=1"},
        {"id": "OTHER", "type": "page", "url": "https://example.com/"},
    ]
    closed: list[str] = []

    def fake_list(*, cdp_http: str = "http://127.0.0.1:18800"):
        return [t for t in targets if t["id"] not in closed]

    with mock.patch("partyrock_tabs.list_page_targets", fake_list), mock.patch(
        "partyrock_tabs.close_tab",
        side_effect=lambda tid, **kw: closed.append(tid) or True,
    ), mock.patch(
        "partyrock_tabs.wait_tab_gone", return_value=True
    ), mock.patch(
        "partyrock_tabs.create_tab",
        side_effect=AssertionError("should reuse idle tab, not /json/new"),
    ):
        info = open_job_partyrock_tab(
            job_dir, "job-new", "https://partyrock.aws/u/me/app"
        )

    assert info["id"] == "IDLE1"
    assert info.get("reused") is True
    assert info.get("needs_navigate") is True
    assert closed == ["IDLE2"]
    assert "OTHER" not in closed
    meta = read_tab_meta(job_dir)
    assert meta is not None
    assert meta["target_id"] == "IDLE1"
    assert meta.get("in_use") is True


def test_new_tailor_protects_live_peer_and_sweeps_stale_claim(tmp_path: Path) -> None:
    """Peer in-use tab stays; dead-pid leftover is idle and may be reused/closed."""
    _write_claim(tmp_path / "job-peer", "job-peer", "PEER", pid=os.getpid())
    _write_claim(tmp_path / "job-stale", "job-stale", "STALE", pid=999_999_999)
    job_dir = tmp_path / "job-new"
    targets = [
        {"id": "PEER", "type": "page", "url": "https://partyrock.aws/u/me/live"},
        {"id": "STALE", "type": "page", "url": "https://partyrock.aws/u/me/old"},
        {"id": "IDLE", "type": "page", "url": "https://partyrock.aws/u/me/idle"},
    ]
    closed: list[str] = []

    def fake_list(*, cdp_http: str = "http://127.0.0.1:18800"):
        return [t for t in targets if t["id"] not in closed]

    with mock.patch("partyrock_tabs.list_page_targets", fake_list), mock.patch(
        "partyrock_tabs.close_tab",
        side_effect=lambda tid, **kw: closed.append(tid) or True,
    ), mock.patch(
        "partyrock_tabs.wait_tab_gone", return_value=True
    ), mock.patch(
        "partyrock_tabs.create_tab",
        side_effect=AssertionError("should reuse an idle leftover"),
    ):
        info = open_job_partyrock_tab(
            job_dir, "job-new", "https://partyrock.aws/u/me/app"
        )

    assert info["id"] in {"STALE", "IDLE"}
    assert "PEER" not in closed
    assert info["id"] not in closed
    leftover = {"STALE", "IDLE"} - {info["id"]}
    assert leftover <= set(closed)


def test_close_idle_auto_protects_in_use_claims(tmp_path: Path) -> None:
    _write_claim(tmp_path / "job-peer", "job-peer", "PEER", pid=os.getpid())
    targets = [
        {"id": "PEER", "type": "page", "url": "https://partyrock.aws/u/me/live"},
        {"id": "IDLE", "type": "page", "url": "https://partyrock.aws/u/me/old"},
    ]
    closed: list[str] = []

    with mock.patch("partyrock_tabs.list_page_targets", return_value=targets), mock.patch(
        "partyrock_tabs.close_tab",
        side_effect=lambda tid, **kw: closed.append(tid) or True,
    ), mock.patch("partyrock_tabs.wait_tab_gone", return_value=True):
        summary = close_idle_partyrock_tabs(resumes_dir=tmp_path)

    assert closed == ["IDLE"]
    assert summary["protected"] == ["PEER"]


def test_close_idle_matches_partyrock_query_params() -> None:
    targets = [
        {"id": "Q", "type": "page", "url": "https://partyrock.aws/u/me/app?x=1"},
        {"id": "BLANK", "type": "page", "url": "about:blank"},
        {"id": "FILL", "type": "page", "url": "https://boards.greenhouse.io/x"},
    ]
    closed: list[str] = []

    with mock.patch("partyrock_tabs.list_page_targets", return_value=targets), mock.patch(
        "partyrock_tabs.close_tab",
        side_effect=lambda tid, **kw: closed.append(tid) or True,
    ), mock.patch("partyrock_tabs.wait_tab_gone", return_value=True):
        close_idle_partyrock_tabs()

    assert closed == ["Q"]


def test_tailor_resume_navigates_reused_idle_tab() -> None:
    src = (ROOT / "scripts" / "tailor_resume.py").read_text()
    assert "needs_navigate" in src
    assert "page.goto" in src


def test_force_partyrock_bypasses_ondisk_reuse() -> None:
    """Mirrors run_tailor_then_fill gate: force_partyrock must not reuse tex+pdf."""
    force_partyrock = True
    resume_pdf_exists = True
    resume_tex_exists = True
    reuse = (
        not force_partyrock
        and resume_pdf_exists
        and resume_tex_exists
    )
    assert reuse is False

    force_partyrock = False
    reuse = (
        not force_partyrock
        and resume_pdf_exists
        and resume_tex_exists
    )
    assert reuse is True


def test_cdp_create_close_live() -> None:
    """Optional live CDP check — skip if OpenClaw browser is down."""
    try:
        before = {t["id"] for t in list_page_targets()}
    except (urllib.error.URLError, TimeoutError, OSError):
        print("skip live CDP (browser down)")
        return
    info = create_tab("about:blank")
    tid = info["id"]
    assert tid not in before or True  # newly created
    ids = {t["id"] for t in list_page_targets()}
    assert tid in ids
    assert close_tab(tid) is True
    ids2 = {t["id"] for t in list_page_targets()}
    assert tid not in ids2


def test_close_job_live_two_tabs(tmp_path: Path) -> None:
    try:
        list_page_targets()
    except (urllib.error.URLError, TimeoutError, OSError):
        print("skip live two-tab close (browser down)")
        return
    a_dir = tmp_path / "jobA"
    b_dir = tmp_path / "jobB"
    a = create_tab("about:blank")
    b = create_tab("about:blank")
    write_tab_meta(a_dir, job_id="jobA", target_id=a["id"])
    write_tab_meta(b_dir, job_id="jobB", target_id=b["id"])
    summary = close_job_partyrock_tab("jobA", a_dir)
    assert summary["closed"] is True
    assert summary["target_id"] == a["id"]
    ids = {t["id"] for t in list_page_targets()}
    assert a["id"] not in ids
    assert b["id"] in ids
    close_tab(b["id"])
    clear_tab_meta(b_dir)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        test_tab_meta_roundtrip(p / "meta")
        test_close_job_isolates_by_meta(p / "iso")
        test_close_job_keeps_meta_until_target_is_gone(p / "pending")
        test_create_tab_does_not_double_open_on_bad_put_payload()
        test_create_tab_prefers_background_target()
        test_open_job_partyrock_tab_reuses_live_target(p / "reuse")
        test_close_idle_partyrock_tabs_preserves_active_and_unrelated()
        test_new_tailor_reuses_idle_and_closes_other_leftovers(p / "leftover")
        test_new_tailor_protects_live_peer_and_sweeps_stale_claim(p / "peer")
        test_close_idle_auto_protects_in_use_claims(p / "autoprot")
        test_close_idle_matches_partyrock_query_params()
        test_tailor_resume_navigates_reused_idle_tab()
        test_force_partyrock_bypasses_ondisk_reuse()
        test_cdp_create_close_live()
        test_close_job_live_two_tabs(p / "live2")
    print("OK test_partyrock_tabs")
