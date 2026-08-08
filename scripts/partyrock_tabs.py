"""Per-job PartyRock CDP tab registry (OpenClaw browser :18800).

Each tailor run opens its own Chrome tab via CDP ``/json/new`` so parallel
jobs never overwrite each other. Tabs stay open after tailor until the
dashboard marks the job applied (or cancel/delete closes that job's tab).

Never stops the shared OpenClaw browser process — only closes the tracked
target id for one job.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CDP_HTTP = "http://127.0.0.1:18800"
TAB_META_NAME = "partyrock_tab.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tab_meta_path(job_dir: Path | str) -> Path:
    return Path(job_dir) / TAB_META_NAME


def read_tab_meta(job_dir: Path | str) -> dict[str, Any] | None:
    path = tab_meta_path(job_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    tid = str(data.get("target_id") or "").strip()
    if not tid:
        return None
    return data


def write_tab_meta(
    job_dir: Path | str,
    *,
    job_id: str,
    target_id: str,
    url: str = "",
    title: str = "",
) -> Path:
    path = tab_meta_path(job_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "job_id": job_id,
        "target_id": target_id,
        "url": url,
        "title": title,
        "created_at": _now_iso(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def clear_tab_meta(job_dir: Path | str) -> None:
    path = tab_meta_path(job_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def cdp_json(path: str, *, cdp_http: str = DEFAULT_CDP_HTTP, method: str = "GET") -> Any:
    base = cdp_http.rstrip("/")
    req = urllib.request.Request(f"{base}{path}", method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw.decode("utf-8", errors="replace")


def create_tab(url: str, *, cdp_http: str = DEFAULT_CDP_HTTP) -> dict[str, Any]:
    """Open a new page target. Returns Chrome ``/json/new`` payload (has ``id``).

    PR2-004: try PUT first (Chromium), then GET — some builds only accept one.
    """
    quoted = urllib.parse.quote(url, safe="")
    path = f"/json/new?{quoted}"
    last_err: Exception | None = None
    for method in ("PUT", "GET"):
        try:
            data = cdp_json(path, cdp_http=cdp_http, method=method)
            if isinstance(data, dict) and data.get("id"):
                return data
            last_err = RuntimeError(f"CDP /json/new ({method}) bad payload: {data!r}")
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"CDP /json/new failed: {last_err!r}")


def close_tab(target_id: str, *, cdp_http: str = DEFAULT_CDP_HTTP) -> bool:
    """Close one CDP target by id. Returns True if Chrome acknowledged close."""
    tid = (target_id or "").strip()
    if not tid:
        return False
    try:
        result = cdp_json(f"/json/close/{urllib.parse.quote(tid, safe='')}", cdp_http=cdp_http)
    except urllib.error.HTTPError as e:
        # Already gone / unknown target — treat as closed.
        if e.code in (404, 500):
            return True
        raise
    except urllib.error.URLError:
        return False
    if result is None:
        return True
    if isinstance(result, str):
        return "closing" in result.lower() or "closed" in result.lower() or not result.strip()
    return True


def wait_tab_gone(target_id: str, *, cdp_http: str = DEFAULT_CDP_HTTP, timeout_s: float = 5.0) -> bool:
    """Poll until target disappears from /json/list (close is async).

    PR2-004: default 5s (was 2s) — Chromium close can race under load.
    """
    tid = (target_id or "").strip()
    if not tid:
        return True
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ids = {t.get("id") for t in list_page_targets(cdp_http=cdp_http)}
        if tid not in ids:
            return True
        time.sleep(0.05)
    return False


def list_page_targets(*, cdp_http: str = DEFAULT_CDP_HTTP) -> list[dict[str, Any]]:
    data = cdp_json("/json/list", cdp_http=cdp_http)
    if not isinstance(data, list):
        return []
    return [t for t in data if isinstance(t, dict) and t.get("type") == "page"]


def close_job_partyrock_tab(
    job_id: str,
    job_dir: Path | str,
    *,
    cdp_http: str = DEFAULT_CDP_HTTP,
) -> dict[str, Any]:
    """Close only this job's tracked PartyRock tab; leave other jobs' tabs alone."""
    meta = read_tab_meta(job_dir)
    summary: dict[str, Any] = {
        "job_id": job_id,
        "closed": False,
        "target_id": None,
        "reason": "no_meta",
    }
    if not meta:
        return summary
    tid = str(meta.get("target_id") or "").strip()
    summary["target_id"] = tid
    if not tid:
        clear_tab_meta(job_dir)
        summary["reason"] = "empty_target_id"
        return summary
    try:
        ok = close_tab(tid, cdp_http=cdp_http)
        if ok:
            wait_tab_gone(tid, cdp_http=cdp_http)
            # PR-003: only drop meta after Chrome acknowledged close — keep
            # meta on failure so a retry can still target the live tab.
            clear_tab_meta(job_dir)
        summary["closed"] = bool(ok)
        summary["reason"] = "closed" if ok else "close_failed"
    except Exception as e:
        summary["reason"] = f"error:{e}"
    return summary
