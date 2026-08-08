"""Ordered step-by-step fill log for debugging thrash, skips, and stuck loops.

Append-only JSONL plus human-readable ``fill_steps.md`` per run — alongside
``field_attempts.jsonl`` (per-field fail counts) but finer-grained: every
meaningful action with monotonic step number, before/after readback, via/layer.

On headed runs, optional per-step screenshots land in ``{out_dir}/steps/NNN.png``
(throttled; always on mismatch/fail). ``steps/index.html`` is written at finalize.

Dummy-only observability — never-submit rules unchanged.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = ROOT / "skyvern_runtime" / "real_job_results"

_log = logging.getLogger("fill_step_log")

# Throttle headed step screenshots (seconds between optional shots)
_STEP_SHOT_THROTTLE_S = float(os.environ.get("FASTFILL_STEP_SHOT_THROTTLE_S") or "2.5")
_FORCE_SHOT_ACTIONS = frozenset(
    {
        "fill_failed",
        "leftover_blank",
        "readback_mismatch",
        "advance_blocked",
        "blocker",
        "captcha",
        "run_end",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stream_enabled() -> bool:
    """Stream each step to stdout (same stream as [identity]/[refill]). FASTFILL_STEP_LOG_STREAM=0 to disable."""
    return (os.environ.get("FASTFILL_STEP_LOG_STREAM") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _format_step_terminal(entry: dict[str, Any]) -> str:
    """One-line human step for live terminal debugging."""
    n = entry.get("step", "?")
    ts = (entry.get("ts") or "")[11:19] or "—"
    action = entry.get("action") or "?"
    fld = entry.get("field_type") or entry.get("label") or "—"
    label = entry.get("label") or ""
    if label and label != fld:
        fld_disp = f"{fld} ({str(label)[:36]})"
    else:
        fld_disp = str(fld)[:48]
    before = entry.get("before")
    after = entry.get("after")
    if before is not None or after is not None:
        change = f'"{before or ""}" → "{after or ""}"'
    else:
        change = ""
    via = entry.get("via") or ""
    reason = entry.get("reason") or ""
    pass_i = entry.get("pass_i")
    bits = [f"[fill-step {n:03d}] {ts} {action} | {fld_disp}"]
    if change:
        bits.append(change)
    if via:
        bits.append(f"via={via}")
    if reason:
        bits.append(f"reason={reason}")
    if pass_i is not None:
        bits.append(f"pass={pass_i}")
    return " ".join(bits)


class FillStepLog:
    """Monotonic step log bound to one fill run artifact directory."""

    def __init__(
        self,
        out_dir: Path | str,
        *,
        run_id: str,
        url: str = "",
        platform: str = "",
        headed: bool = False,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = str(run_id)
        self.url = url or ""
        self.platform = platform or ""
        self.headed = bool(headed)
        self.jsonl_path = self.out_dir / "fill_steps.jsonl"
        self.md_path = self.out_dir / "fill_steps.md"
        self.steps_dir = self.out_dir / "steps"
        self._step = 0
        self._last_shot_epoch = 0.0
        self._shot_paths: dict[int, str] = {}
        self._md_lines: list[str] = [
            "# Fill step log",
            "",
            f"- run_id: `{self.run_id}`",
            f"- platform: `{self.platform}`",
            f"- url: {self.url}",
            f"- started: {_utc_now()}",
            "",
            "| # | time | action | field | before → after | via | reason |",
            "|---|------|--------|-------|----------------|-----|--------|",
        ]
        if self.jsonl_path.is_file():
            try:
                for line in self.jsonl_path.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(e, dict) and e.get("step"):
                        self._step = max(self._step, int(e["step"]))
                        if e.get("screenshot"):
                            self._shot_paths[int(e["step"])] = str(e["screenshot"])
            except OSError:
                pass

    def step(
        self,
        action: str,
        *,
        label: str = "",
        field_type: str = "",
        before: str | None = None,
        after: str | None = None,
        via: str = "",
        layer: str = "",
        reason: str = "",
        pass_i: int | None = None,
        extra: dict[str, Any] | None = None,
        page: Any = None,
        force_screenshot: bool = False,
    ) -> dict[str, Any]:
        """Append one ordered step. Returns the entry dict."""
        self._step += 1
        entry: dict[str, Any] = {
            "step": self._step,
            "ts": _utc_now(),
            "ts_epoch": time.time(),
            "run_id": self.run_id,
            "url": self.url,
            "platform": self.platform,
            "action": str(action or "unknown")[:64],
            "label": (label or "")[:120] or None,
            "field_type": (field_type or "")[:48] or None,
            "before": (str(before)[:120] if before is not None else None),
            "after": (str(after)[:120] if after is not None else None),
            "via": (via or "")[:64] or None,
            "layer": (layer or "")[:32] or None,
            "reason": (reason or "")[:240] or None,
            "pass_i": pass_i,
        }
        if extra:
            entry["extra"] = extra
        # Headed visual truth: throttle OK; always on mismatch/fail
        want_shot = self.headed and (
            force_screenshot
            or entry["action"] in _FORCE_SHOT_ACTIONS
            or (reason or "").lower()
            in (
                "readback_mismatch",
                "unverified_readback",
                "select_not_committed",
            )
            or (extra or {}).get("verified") is False
            or (extra or {}).get("ok") is False
            or (time.time() - self._last_shot_epoch) >= _STEP_SHOT_THROTTLE_S
        )
        if want_shot and page is not None:
            shot_rel = self._schedule_step_screenshot(page, self._step)
            if shot_rel:
                entry["screenshot"] = shot_rel
                self._shot_paths[self._step] = shot_rel
        with self.jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
        b = entry["before"] or "—"
        a = entry["after"] or "—"
        fld = entry["field_type"] or entry["label"] or "—"
        ts_short = entry["ts"][11:19] if entry["ts"] else ""
        self._md_lines.append(
            f"| {self._step} | {ts_short} | `{entry['action']}` | "
            f"{str(fld)[:40]} | {str(b)[:28]} → {str(a)[:28]} | "
            f"{entry.get('via') or '—'} | {(entry.get('reason') or '')[:40]} |"
        )
        if _stream_enabled():
            try:
                print(_format_step_terminal(entry), flush=True)
            except OSError:
                pass
        return entry

    def _schedule_step_screenshot(self, page: Any, step_n: int) -> str | None:
        """Fire-and-forget async screenshot; returns relative path immediately."""
        self.steps_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{step_n:03d}.png"
        path = self.steps_dir / fname
        rel = f"steps/{fname}"
        self._last_shot_epoch = time.time()

        async def _shot() -> None:
            try:
                await page.screenshot(path=str(path), full_page=False, timeout=8000)
            except Exception as e:
                _log.debug("step screenshot %s failed: %s", path, e)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_shot())
            return rel
        except RuntimeError:
            # No running loop — skip (headed fills are async; headless stays quiet)
            _log.debug("no event loop for step screenshot %s", path)
            return None

    def write_steps_index(self) -> Path | None:
        """Generate steps/index.html listing step + image for human review."""
        if not self._shot_paths and not self.steps_dir.is_dir():
            return None
        # Also pick up any PNGs written async after jsonl
        if self.steps_dir.is_dir():
            for png in sorted(self.steps_dir.glob("*.png")):
                try:
                    n = int(png.stem)
                except ValueError:
                    continue
                self._shot_paths.setdefault(n, f"steps/{png.name}")
        if not self._shot_paths:
            return None
        self.steps_dir.mkdir(parents=True, exist_ok=True)
        rows: list[str] = []
        # Merge metadata from jsonl when available
        meta: dict[int, dict] = {}
        if self.jsonl_path.is_file():
            try:
                for line in self.jsonl_path.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(e, dict) and e.get("step"):
                        meta[int(e["step"])] = e
            except OSError:
                pass
        for n in sorted(self._shot_paths):
            e = meta.get(n) or {}
            action = html.escape(str(e.get("action") or ""))
            fld = html.escape(str(e.get("field_type") or e.get("label") or ""))
            reason = html.escape(str(e.get("reason") or ""))
            img = html.escape(Path(self._shot_paths[n]).name)
            rows.append(
                f"<tr><td>{n:03d}</td><td>{action}</td><td>{fld}</td>"
                f"<td>{reason}</td>"
                f'<td><a href="{img}"><img src="{img}" alt="step {n}" '
                f'style="max-width:320px;height:auto;border:1px solid #ccc"/></a></td></tr>'
            )
        body = "\n".join(rows)
        doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>Fill steps — {html.escape(self.run_id)}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 1.5rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 0.4rem 0.6rem; vertical-align: top; }}
th {{ background: #f4f4f4; text-align: left; }}
</style></head><body>
<h1>Fill step screenshots</h1>
<p>run_id: <code>{html.escape(self.run_id)}</code> · platform: {html.escape(self.platform)}</p>
<p>url: {html.escape(self.url)}</p>
<table>
<thead><tr><th>#</th><th>action</th><th>field</th><th>reason</th><th>shot</th></tr></thead>
<tbody>
{body}
</tbody></table>
</body></html>
"""
        index = self.steps_dir / "index.html"
        try:
            index.write_text(doc, encoding="utf-8")
        except OSError as e:
            _log.warning("write steps index failed: %s", e)
            return None
        return index

    def finalize(self) -> None:
        """Write human-readable markdown summary + steps index when shots exist."""
        if getattr(self, "_finalized", False):
            return
        self._finalized = True
        self._md_lines.extend(
            [
                "",
                f"_Updated {_utc_now()} — {self._step} steps_",
            ]
        )
        try:
            self.md_path.write_text("\n".join(self._md_lines) + "\n", encoding="utf-8")
        except OSError:
            pass
        idx = self.write_steps_index()
        if idx:
            self._md_lines.append(f"\n- steps index: `{idx}`\n")
            try:
                self.md_path.write_text("\n".join(self._md_lines) + "\n", encoding="utf-8")
            except OSError:
                pass


def attach_fill_step_log(
    report: dict[str, Any],
    *,
    out_dir: Path | str | None = None,
    run_id: str | None = None,
) -> FillStepLog:
    """Create / reuse FillStepLog on report (``report['_fill_step_log']``)."""
    existing = report.get("_fill_step_log")
    if isinstance(existing, FillStepLog):
        if report.get("headed") and not existing.headed:
            existing.headed = True
        return existing
    url = str(report.get("url") or "")
    platform = str(report.get("platform") or "")
    rid = run_id or str(
        report.get("alias_token")
        or report.get("email_alias")
        or report.get("identity_email")
        or f"run_{int(time.time())}"
    )
    if out_dir is None:
        rp = report.get("report_path") or report.get("screenshot")
        if rp:
            out_dir = Path(str(rp)).parent
        else:
            out_dir = DEFAULT_RESULTS / f"fill_steps_{rid}"
    log = FillStepLog(
        out_dir,
        run_id=rid,
        url=url,
        platform=platform,
        headed=bool(report.get("headed")),
    )
    report["_fill_step_log"] = log
    report["fill_step_log_path"] = str(log.jsonl_path)
    report["fill_steps_md_path"] = str(log.md_path)
    return log


def get_step_log(report: dict[str, Any] | None) -> FillStepLog | None:
    log = (report or {}).get("_fill_step_log")
    return log if isinstance(log, FillStepLog) else None


def _row_step_key(row: dict[str, Any], *, action: str = "") -> str:
    return "|".join(
        [
            action or "",
            str(row.get("type") or ""),
            str(row.get("label") or "")[:80],
            str(row.get("via") or ""),
            str(row.get("reason") or "")[:40],
            str(
                row.get("readback")
                or row.get("picked")
                or row.get("value")
                or ""
            )[:80],
        ]
    )


def infer_action_from_row(row: dict[str, Any]) -> str:
    reason = str(row.get("reason") or "")
    if row.get("skipped_already_correct") or reason in (
        "already_correct_skip",
        "already_correct_keep",
    ):
        return "skip_already_correct"
    if row.get("mode") in ("yesno", "yesno_segmented") or row.get("mode") == "yesno":
        return "click_yes_no"
    if row.get("mode") in ("file", "gh_upload_ui", "ashby_upload_ui", "file_chooser"):
        return "upload_resume"
    if row.get("select") or str(row.get("mode") or "") in (
        "combobox",
        "typable_dropdown",
        "location_autocomplete",
        "verified_select",
    ):
        return "select_word_by_word"
    if row.get("ok") is False or row.get("verified") is False:
        return "fill_failed"
    return "fill_text"


def log_row_as_step(
    report: dict[str, Any] | None,
    row: dict[str, Any],
    *,
    action: str | None = None,
    before: str | None = None,
    pass_i: int | None = None,
) -> dict[str, Any] | None:
    """Log one filled/skip row once (deduped per run)."""
    if not report or not isinstance(row, dict):
        return None
    act = action or infer_action_from_row(row)
    keys: set[str] = report.setdefault("_step_log_row_keys", set())
    key = _row_step_key(row, action=act)
    if key in keys:
        return None
    keys.add(key)
    return note_step(
        report,
        action=act,
        label=str(row.get("label") or row.get("type") or "")[:80],
        field_type=str(row.get("type") or "")[:48],
        before=before,
        after=str(
            row.get("readback")
            or row.get("picked")
            or row.get("verified_value")
            or row.get("value")
            or ""
        )[:120],
        via=str(row.get("via") or "")[:64],
        layer=str(row.get("layer") or "")[:32],
        reason=str(row.get("reason") or "")[:240] or None,
        pass_i=pass_i,
        extra={
            k: row.get(k)
            for k in ("selector", "mode", "option_clicked", "committed", "ok", "verified")
            if row.get(k) is not None
        }
        or None,
        force_screenshot=bool(
            row.get("ok") is False
            or row.get("verified") is False
            or act in ("fill_failed", "leftover_blank")
        ),
    )


def emit_filled_rows_as_steps(
    report: dict[str, Any] | None,
    *,
    pass_i: int | None = None,
    phase: str = "",
) -> int:
    """Walk report filled[] and emit any rows not yet logged. Returns count emitted."""
    if not report:
        return 0
    n = 0
    for row in report.get("filled") or []:
        if isinstance(row, dict) and log_row_as_step(report, row, pass_i=pass_i):
            n += 1
    if phase and n:
        note_step(
            report,
            action="phase_done",
            reason=f"{phase} emitted {n} steps",
            pass_i=pass_i,
            via=phase,
        )
    return n


def emit_leftover_rows_as_steps(
    report: dict[str, Any] | None,
    *,
    pass_i: int | None = None,
) -> int:
    """Log still-blank leftovers as fill_failed steps."""
    if not report:
        return 0
    n = 0
    for row in report.get("leftovers") or []:
        if not isinstance(row, dict):
            continue
        if row.get("flash_candidate") is False:
            continue
        if log_row_as_step(report, row, action="leftover_blank", pass_i=pass_i):
            n += 1
    return n


def note_step(report: dict[str, Any] | None, **kwargs: Any) -> dict[str, Any] | None:
    """Convenience: log one step if a log is attached (no-op otherwise)."""
    if not report:
        return None
    action = str(kwargs.get("action") or "")
    if action in ("run_start", "run_end"):
        flag = f"_step_logged_{action}"
        if report.get(flag):
            return None
        report[flag] = True
    log = get_step_log(report)
    if log is None:
        if report and (report.get("alias_token") or report.get("url")):
            log = attach_fill_step_log(report)
        else:
            return None
    # Prefer explicit page kwarg; else soft ref from report (headed fills)
    if kwargs.get("page") is None and report.get("_page") is not None:
        kwargs["page"] = report.get("_page")
    return log.step(**kwargs)


def finalize_step_log(report: dict[str, Any] | None) -> None:
    log = get_step_log(report)
    if log is not None:
        log.finalize()


def self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        log = FillStepLog(td, run_id="t1", url="https://example.com", platform="ashby")
        log.step(
            "skip_already_correct",
            label="Email",
            field_type="EMAIL",
            before="test@x.com",
            after="test@x.com",
            via="pack",
            reason="already_correct_skip",
        )
        log.step(
            "click_yes_no",
            label="Latin America?",
            field_type="LATIN_AMERICA",
            before="",
            after="Yes",
            via="ashby_widgets",
            reason="dummy_policy_yes",
        )
        # Simulate headed fail shot path without a real page
        log.headed = True
        log._shot_paths[1] = "steps/001.png"
        (log.steps_dir).mkdir(parents=True, exist_ok=True)
        (log.steps_dir / "001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        log.finalize()
        assert log.jsonl_path.is_file()
        assert log.md_path.is_file()
        assert log._step == 2
        assert "click_yes_no" in log.md_path.read_text()
        idx = log.steps_dir / "index.html"
        assert idx.is_file(), "steps/index.html should exist when shots present"
        assert "001.png" in idx.read_text()
    print("fill_step_log.self_test: OK")


if __name__ == "__main__":
    self_test()
