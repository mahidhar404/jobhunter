"""Per-job PartyRock CDP tab registry (OpenClaw browser :18800).

Each tailor run claims one Chrome tab (reuse an idle PartyRock page when
possible, otherwise CDP ``/json/new``) so parallel jobs never overwrite
each other. Normal runs close after collecting the resume; cancel/delete/
stuck closes that job's tracked tab early.

A new tailor sweep closes leftover PartyRock app tabs that are not claimed
by a live concurrent tailor (in-use meta + live pid). Serialized with a
resumes-dir file lock so two Starts cannot close each other's new tabs.

Never stops the shared OpenClaw browser process — only closes PartyRock
page targets on the OpenClaw CfT profile.
"""
from __future__ import annotations

import base64
import fcntl
import json
import os
import socket
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

DEFAULT_CDP_HTTP = "http://127.0.0.1:18800"
TAB_META_NAME = "partyrock_tab.json"
TAB_LOCK_NAME = ".partyrock_tabs.lock"
DEFAULT_RESUMES_DIR = Path(__file__).resolve().parent.parent / "resumes"


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
    in_use: bool = True,
    pid: int | None = None,
) -> Path:
    path = tab_meta_path(job_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "job_id": job_id,
        "target_id": target_id,
        "url": url,
        "title": title,
        "created_at": _now_iso(),
        "in_use": bool(in_use),
    }
    if in_use:
        payload["pid"] = int(pid) if pid is not None else os.getpid()
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def clear_tab_meta(job_dir: Path | str) -> None:
    path = tab_meta_path(job_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def is_partyrock_target(target: dict[str, Any]) -> bool:
    """True for PartyRock app pages (URL host/query), not about:blank leftovers."""
    url = str(target.get("url") or "")
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "partyrock.aws" or host.endswith(".partyrock.aws"):
        return True
    return "partyrock.aws/" in url.lower() or url.lower().rstrip("/") == "https://partyrock.aws"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def in_use_target_ids(resumes_dir: Path | str) -> set[str]:
    """Target ids claimed by a live tailor process (in_use + pid still running)."""
    root = Path(resumes_dir)
    out: set[str] = set()
    if not root.is_dir():
        return out
    for meta_path in root.glob(f"*/{TAB_META_NAME}"):
        try:
            data = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not data.get("in_use"):
            continue
        tid = str(data.get("target_id") or "").strip()
        if not tid:
            continue
        try:
            pid = int(data.get("pid"))
        except (TypeError, ValueError):
            continue
        if _pid_alive(pid):
            out.add(tid)
    return out


@contextmanager
def tab_registry_lock(resumes_dir: Path | str | None = None) -> Iterator[None]:
    """Cross-process lock so parallel Starts cannot sweep each other's new tabs."""
    root = Path(resumes_dir) if resumes_dir is not None else DEFAULT_RESUMES_DIR
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / TAB_LOCK_NAME
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


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


def _browser_websocket_url(*, cdp_http: str = DEFAULT_CDP_HTTP) -> str:
    data = cdp_json("/json/version", cdp_http=cdp_http)
    if isinstance(data, dict):
        ws = str(data.get("webSocketDebuggerUrl") or "").strip()
        if ws:
            return ws
    raise RuntimeError("CDP /json/version missing webSocketDebuggerUrl")


def _ws_connect(ws_url: str, *, timeout_s: float = 15.0) -> socket.socket:
    parsed = urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    sock = socket.create_connection((host, port), timeout=timeout_s)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    sock.sendall(req.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("WebSocket handshake failed (empty response)")
        buf += chunk
    status_line = buf.split(b"\r\n", 1)[0]
    if b" 101 " not in status_line:
        raise RuntimeError(f"WebSocket handshake failed: {status_line.decode('utf-8', 'replace')}")
    sock.settimeout(timeout_s)
    return sock


def _ws_send_text(sock: socket.socket, text: str) -> None:
    data = text.encode("utf-8")
    mask = os.urandom(4)
    header = bytearray([0x81])  # FIN + text opcode
    ln = len(data)
    if ln < 126:
        header.append(0x80 | ln)
    elif ln < 65536:
        header.extend([0x80 | 126, (ln >> 8) & 0xFF, ln & 0xFF])
    else:
        raise ValueError("WebSocket payload too large")
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(header + masked)


def _ws_recv_frame(sock: socket.socket) -> bytes:
    head = sock.recv(2)
    if len(head) < 2:
        raise RuntimeError("WebSocket closed")
    b1, b2 = head[0], head[1]
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        ext = sock.recv(2)
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = sock.recv(8)
        length = struct.unpack("!Q", ext)[0]
    mask_key = sock.recv(4) if masked else b""
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            raise RuntimeError("WebSocket truncated frame")
        payload += chunk
    if masked:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    if b1 & 0x0F == 0x8:  # close
        raise RuntimeError("WebSocket closed by peer")
    return payload


def place_partyrock_window(
    target_id: str,
    *,
    cdp_http: str = DEFAULT_CDP_HTTP,
) -> dict[str, Any] | None:
    """Move the PartyRock Chrome window to the right two-thirds of the usable screen.

    Best-effort: missing screens, CDP, or a vanished target is a no-op.
    """
    tid = (target_id or "").strip()
    if not tid:
        return None
    parsed = urlparse(cdp_http)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 18800
    try:
        with socket.create_connection((host, port), timeout=0.4):
            pass
    except OSError:
        return None
    try:
        from window_geometry import place_cdp_window, work_window_plan

        outer = work_window_plan(role="fill")
        if outer is None:
            return None

        def _call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            return _cdp_browser_call(method, params or {}, cdp_http=cdp_http)

        return place_cdp_window(_call, outer=outer, target_id=tid)
    except Exception:
        return None


def _place_after_claim(info: dict[str, Any], *, cdp_http: str) -> None:
    tid = str((info or {}).get("id") or "").strip()
    if tid:
        place_partyrock_window(tid, cdp_http=cdp_http)


def _cdp_browser_call(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    cdp_http: str = DEFAULT_CDP_HTTP,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """One JSON-RPC call on the browser-level CDP WebSocket."""
    ws_url = _browser_websocket_url(cdp_http=cdp_http)
    msg_id = 1
    payload = json.dumps({"id": msg_id, "method": method, "params": params or {}})
    sock = _ws_connect(ws_url, timeout_s=timeout_s)
    try:
        _ws_send_text(sock, payload)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            raw = _ws_recv_frame(sock)
            data = json.loads(raw.decode("utf-8"))
            if data.get("id") != msg_id:
                continue
            if "error" in data:
                err = data["error"]
                raise RuntimeError(
                    f"CDP {method} failed: {err.get('message') or err}"
                )
            result = data.get("result")
            return result if isinstance(result, dict) else {}
        raise RuntimeError(f"CDP {method} timed out")
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _create_tab_background(
    url: str,
    *,
    cdp_http: str = DEFAULT_CDP_HTTP,
) -> dict[str, Any]:
    """Open a page target without raising Chrome (Target.createTarget background)."""
    result = _cdp_browser_call(
        "Target.createTarget",
        {"url": url, "background": True},
        cdp_http=cdp_http,
    )
    target_id = str(result.get("targetId") or "").strip()
    if not target_id:
        raise RuntimeError(f"Target.createTarget bad payload: {result!r}")
    return {"id": target_id, "url": url, "background": True}


def create_tab(
    url: str,
    *,
    cdp_http: str = DEFAULT_CDP_HTTP,
    background: bool = True,
    place: bool = True,
) -> dict[str, Any]:
    """Open a new page target. Returns payload with ``id`` (Chrome target id).

    When *background* is True (default), prefer CDP Target.createTarget so
    PartyRock tailoring does not steal focus from the OmniDex dashboard.
    Falls back to HTTP ``/json/new`` when the browser WebSocket is unavailable.
    Window bounds (right two-thirds) are applied unless *place* is False
    (caller will place after releasing the tab registry lock).
    """
    if background:
        try:
            info = _create_tab_background(url, cdp_http=cdp_http)
            if place:
                _place_after_claim(info, cdp_http=cdp_http)
            return info
        except Exception:
            pass

    info = _create_tab_http_new(url, cdp_http=cdp_http)
    if place:
        _place_after_claim(info, cdp_http=cdp_http)
    return info


def _create_tab_http_new(url: str, *, cdp_http: str = DEFAULT_CDP_HTTP) -> dict[str, Any]:
    """HTTP ``/json/new`` fallback when browser WebSocket is unavailable."""
    quoted = urllib.parse.quote(url, safe="")
    path = f"/json/new?{quoted}"
    before = _page_target_ids(cdp_http=cdp_http)
    last_err: Exception | None = None
    for method in ("PUT", "GET"):
        try:
            data = cdp_json(path, cdp_http=cdp_http, method=method)
            if isinstance(data, dict) and data.get("id"):
                return data
            new_ids = _page_target_ids(cdp_http=cdp_http) - before
            recovered = _single_new_tab_payload(new_ids, url=url, cdp_http=cdp_http)
            if recovered is not None:
                return recovered
            last_err = RuntimeError(f"CDP /json/new ({method}) bad payload: {data!r}")
        except Exception as e:
            new_ids = _page_target_ids(cdp_http=cdp_http) - before
            recovered = _single_new_tab_payload(new_ids, url=url, cdp_http=cdp_http)
            if recovered is not None:
                return recovered
            last_err = e
            continue
    raise RuntimeError(f"CDP /json/new failed: {last_err!r}")


def _page_target_ids(*, cdp_http: str = DEFAULT_CDP_HTTP) -> set[str]:
    return {
        str(t.get("id"))
        for t in list_page_targets(cdp_http=cdp_http)
        if t.get("id")
    }


def target_exists(target_id: str, *, cdp_http: str = DEFAULT_CDP_HTTP) -> bool:
    """True when *target_id* is still a live CDP page target."""
    tid = (target_id or "").strip()
    if not tid:
        return False
    return tid in _page_target_ids(cdp_http=cdp_http)


def _single_new_tab_payload(
    new_ids: set[str],
    *,
    url: str,
    cdp_http: str = DEFAULT_CDP_HTTP,
) -> dict[str, Any] | None:
    """Build a /json/new-like payload when Chrome opened a tab but HTTP body was bad."""
    if not new_ids:
        return None
    tid = sorted(new_ids)[0]
    for orphan in new_ids:
        if orphan != tid:
            try:
                close_tab(orphan, cdp_http=cdp_http)
            except Exception:
                pass
    return {"id": tid, "url": url}


def open_job_partyrock_tab(
    job_dir: Path | str,
    job_id: str,
    url: str,
    *,
    cdp_http: str = DEFAULT_CDP_HTTP,
    force_new: bool = False,
) -> dict[str, Any]:
    """Claim a PartyRock CDP tab for this job (reuse idle leftover, else /json/new).

    Sweeps other idle PartyRock pages. Tabs claimed by a live concurrent tailor
    (``partyrock_tab.json`` with ``in_use`` + live pid) are left alone.
    """
    job_path = Path(job_dir)
    resumes_dir = job_path.parent
    with tab_registry_lock(resumes_dir):
        claimed: dict[str, Any] | None = None
        if not force_new:
            meta = read_tab_meta(job_path)
            if meta:
                tid = str(meta.get("target_id") or "").strip()
                if tid and target_exists(tid, cdp_http=cdp_http):
                    write_tab_meta(
                        job_path,
                        job_id=job_id,
                        target_id=tid,
                        url=url,
                        in_use=True,
                    )
                    _close_idle_partyrock_tabs_unlocked(
                        protected_target_ids=in_use_target_ids(resumes_dir) | {tid},
                        cdp_http=cdp_http,
                    )
                    claimed = {
                        "id": tid,
                        "url": meta.get("url") or url,
                        "reused": True,
                        "needs_navigate": False,
                    }
        if claimed is None:
            claimed = _claim_idle_or_create_unlocked(
                job_path,
                job_id,
                url,
                resumes_dir=resumes_dir,
                cdp_http=cdp_http,
            )
    _place_after_claim(claimed, cdp_http=cdp_http)
    return claimed


def _claim_idle_or_create_unlocked(
    job_dir: Path,
    job_id: str,
    url: str,
    *,
    resumes_dir: Path,
    cdp_http: str,
) -> dict[str, Any]:
    protected = in_use_target_ids(resumes_dir)
    own = read_tab_meta(job_dir)
    if own:
        protected.discard(str(own.get("target_id") or "").strip())
    idle: list[str] = []
    for target in list_page_targets(cdp_http=cdp_http):
        tid = str(target.get("id") or "").strip()
        if not tid or not is_partyrock_target(target):
            continue
        if tid in protected:
            continue
        idle.append(tid)
    if idle:
        claimed = idle[0]
        for extra in idle[1:]:
            try:
                if close_tab(extra, cdp_http=cdp_http):
                    wait_tab_gone(extra, cdp_http=cdp_http)
            except Exception:
                pass
        write_tab_meta(
            job_dir,
            job_id=job_id,
            target_id=claimed,
            url=url,
            in_use=True,
        )
        return {
            "id": claimed,
            "url": url,
            "reused": True,
            "needs_navigate": True,
        }
    tab_info = create_tab(url, cdp_http=cdp_http, place=False)
    write_tab_meta(
        job_dir,
        job_id=job_id,
        target_id=str(tab_info["id"]),
        url=url,
        in_use=True,
    )
    tab_info["reused"] = False
    tab_info["needs_navigate"] = False
    return tab_info


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


def close_idle_partyrock_tabs(
    *,
    protected_target_ids: set[str] | None = None,
    resumes_dir: Path | str | None = None,
    cdp_http: str = DEFAULT_CDP_HTTP,
) -> dict[str, list[str]]:
    """Close PartyRock pages except targets owned by live concurrent tailors."""
    with tab_registry_lock(resumes_dir):
        protected = {str(tid) for tid in (protected_target_ids or set()) if tid}
        if resumes_dir is not None:
            protected |= in_use_target_ids(resumes_dir)
        return _close_idle_partyrock_tabs_unlocked(
            protected_target_ids=protected,
            cdp_http=cdp_http,
        )


def _close_idle_partyrock_tabs_unlocked(
    *,
    protected_target_ids: set[str],
    cdp_http: str,
) -> dict[str, list[str]]:
    protected = {str(tid) for tid in protected_target_ids if tid}
    closed: list[str] = []
    failed: list[str] = []
    kept: list[str] = []
    for target in list_page_targets(cdp_http=cdp_http):
        tid = str(target.get("id") or "").strip()
        if not tid or not is_partyrock_target(target):
            continue
        if tid in protected:
            kept.append(tid)
            continue
        try:
            if close_tab(tid, cdp_http=cdp_http):
                wait_tab_gone(tid, cdp_http=cdp_http)
                closed.append(tid)
            else:
                failed.append(tid)
        except Exception:
            failed.append(tid)
    return {
        "closed": sorted(closed),
        "failed": sorted(failed),
        "protected": sorted(kept),
    }


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
        acknowledged = close_tab(tid, cdp_http=cdp_http)
        gone = False
        if acknowledged:
            gone = wait_tab_gone(tid, cdp_http=cdp_http)
            if gone:
                clear_tab_meta(job_dir)
        # Keep metadata until the target is confirmed gone so Cancel/Retry can
        # retry an asynchronously failed close instead of orphaning the tab.
        summary["closed"] = gone
        summary["reason"] = (
            "closed" if gone else "close_pending" if acknowledged else "close_failed"
        )
        if not gone:
            # Leave target_id for a retry close, but drop in_use so the next
            # tailor start can sweep this leftover instead of protecting it.
            write_tab_meta(
                job_dir,
                job_id=str(meta.get("job_id") or job_id),
                target_id=tid,
                url=str(meta.get("url") or ""),
                title=str(meta.get("title") or ""),
                in_use=False,
            )
    except Exception as e:
        summary["reason"] = f"error:{e}"
        try:
            write_tab_meta(
                job_dir,
                job_id=str(meta.get("job_id") or job_id),
                target_id=tid,
                url=str(meta.get("url") or ""),
                title=str(meta.get("title") or ""),
                in_use=False,
            )
        except Exception:
            pass
    return summary
