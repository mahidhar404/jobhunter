"""Per-field fill attempt tracking + UNFILLABLE_AFTER_2 must-fix signal.

Every fill attempt (success or fail) is append-only JSONL. After 2 failures on
the same field key within a run (across refill passes), write/update
UNFILLABLE_AFTER_2.md and optionally append to AUTONOMOUS_LOOP.md.

Dummy-only / never-submit rules are unchanged — this is observability + Fixer
queue, not a bypass.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = ROOT / "skyvern_runtime" / "real_job_results"
AUTONOMOUS_LOOP = DEFAULT_RESULTS / "AUTONOMOUS_LOOP.md"

_FAIL_THRESHOLD = 2


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def field_key(
    *,
    field_type: str | None = None,
    label: str | None = None,
    selector: str | None = None,
    field_id: str | None = None,
) -> str:
    """Stable key for counting attempts across refill passes."""
    ft = (field_type or "").strip().upper() or "UNTYPED"
    lab = re.sub(r"\s+", " ", (label or "").strip().lower())[:80]
    sel = (selector or "").strip()[:120]
    fid = (field_id or "").strip()[:80]
    if fid:
        return f"{ft}|id:{fid}"
    if lab:
        return f"{ft}|lab:{lab}"
    if sel:
        return f"{ft}|sel:{sel}"
    return ft


class FieldAttemptLog:
    """Append-only attempt log bound to one fill run / cycle artifact dir."""

    def __init__(
        self,
        cycle_dir: Path | str,
        *,
        run_id: str,
        url: str = "",
        platform: str = "",
        autonomous_loop: Path | str | None = None,
    ) -> None:
        self.cycle_dir = Path(cycle_dir)
        self.cycle_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = str(run_id)
        self.url = url or ""
        self.platform = platform or ""
        self.jsonl_path = self.cycle_dir / "field_attempts.jsonl"
        self.unfillable_md = self.cycle_dir / "UNFILLABLE_AFTER_2.md"
        self.fixer_trigger = self.cycle_dir / "FIXER_TRIGGER.md"
        self.autonomous_loop = Path(autonomous_loop) if autonomous_loop else AUTONOMOUS_LOOP
        self._fail_counts: dict[str, int] = {}
        self._success_counts: dict[str, int] = {}
        self._unfillable_keys: set[str] = set()
        self._entries: list[dict[str, Any]] = []
        # Reload prior JSONL in same dir (retests / process restarts)
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
                    if not isinstance(e, dict):
                        continue
                    self._entries.append(e)
                    k = str(e.get("field_key") or "")
                    if not k:
                        continue
                    # Only current-run fails feed the unfillable cap (docstring:
                    # "within a run across refill passes"). Cross-run history is
                    # kept for markdown/stats but must not poison the next smoke.
                    same_run = str(e.get("run_id") or "") == str(self.run_id or "")
                    if e.get("success"):
                        self._success_counts[k] = self._success_counts.get(k, 0) + 1
                    elif same_run:
                        self._fail_counts[k] = self._fail_counts.get(k, 0) + 1
                        if self._fail_counts[k] >= _FAIL_THRESHOLD:
                            self._unfillable_keys.add(k)
            except OSError:
                pass

    def record(
        self,
        *,
        field_type: str | None = None,
        label: str | None = None,
        selector: str | None = None,
        field_id: str | None = None,
        via: str | None = None,
        layer: str | None = None,
        success: bool,
        error: str | None = None,
        readback: str | None = None,
        value: str | None = None,
        attempt_number: int | None = None,
        pass_i: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = field_key(
            field_type=field_type, label=label, selector=selector, field_id=field_id
        )
        if success:
            self._success_counts[key] = self._success_counts.get(key, 0) + 1
            fail_n = self._fail_counts.get(key, 0)
        else:
            self._fail_counts[key] = self._fail_counts.get(key, 0) + 1
            fail_n = self._fail_counts[key]

        if attempt_number is None:
            attempt_number = (
                self._success_counts.get(key, 0) + self._fail_counts.get(key, 0)
            )

        entry: dict[str, Any] = {
            "ts": _utc_now(),
            "ts_epoch": time.time(),
            "run_id": self.run_id,
            "url": self.url,
            "platform": self.platform,
            "field_key": key,
            "field_type": field_type,
            "label": (label or "")[:120] or None,
            "selector": (selector or "")[:160] or None,
            "field_id": field_id,
            "via": via,
            "layer": layer,
            "attempt_number": int(attempt_number),
            "pass_i": pass_i,
            "success": bool(success),
            "fail_count_for_key": fail_n,
            "error": (error or "")[:240] or None,
            "readback": (readback or "")[:120] or None,
            "value_preview": (str(value)[:80] if value is not None else None),
        }
        if extra:
            entry["extra"] = extra

        self._entries.append(entry)
        with self.jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")

        if (not success) and fail_n >= _FAIL_THRESHOLD and key not in self._unfillable_keys:
            self._unfillable_keys.add(key)
            self._log_unfillable_loud(entry)
        elif (not success) and fail_n >= _FAIL_THRESHOLD:
            # Already flagged — still refresh markdown + fixer trigger
            self.write_unfillable_markdown()
            self.write_fixer_trigger()

        return entry

    def record_from_row(
        self,
        row: dict[str, Any],
        *,
        success: bool | None = None,
        pass_i: int | None = None,
        via_override: str | None = None,
        error_override: str | None = None,
    ) -> dict[str, Any]:
        """Record from a filled[] or leftovers[] row."""
        if row.get("skipped_already_correct") or row.get("reason") in (
            "already_correct_skip",
            "already_correct_keep",
            "deferred_ashby_location_zip",
        ):
            return {}
        if success is None:
            if row.get("ok") is False:
                success = False
            elif row.get("verified") is False and row.get("ok") is not True:
                success = False
            elif row.get("reason") in (
                "live_empty_after_claimed_verified",
                "empty_readback_never_filled",
                "resume_missing",
                "resume_upload_failed",
                "gh_select_failed",
                "no_value",
                "unclassified",
            ):
                success = False
            else:
                success = bool(row.get("verified") or row.get("ok"))
        err = error_override or row.get("error") or row.get("reason")
        return self.record(
            field_type=row.get("type"),
            label=row.get("label"),
            selector=row.get("selector"),
            field_id=row.get("id") or row.get("field_id"),
            via=via_override or row.get("via"),
            layer=row.get("layer"),
            success=bool(success),
            error=str(err)[:240] if err else None,
            readback=row.get("readback") or row.get("stale_readback"),
            value=row.get("value") or row.get("verified_value"),
            pass_i=pass_i,
        )

    def ingest_pass(
        self,
        report: dict[str, Any],
        *,
        pass_i: int | None = None,
        phase: str = "fill",
    ) -> dict[str, Any]:
        """Record successes from filled[] and failures from leftover flash candidates.

        Call after each fill phase / refill pass. Leftovers that remain blank count
        as a failed attempt for that pass (so 2 refill passes → UNFILLABLE).
        """
        summary = {"recorded_ok": 0, "recorded_fail": 0, "unfillable": []}
        seen_fail_keys: set[str] = set()

        for row in report.get("filled") or []:
            if not isinstance(row, dict):
                continue
            # already-correct skips are not new attempts
            if row.get("skipped_already_correct") or row.get("reason") in (
                "already_correct_skip",
                "already_correct_keep",
                "deferred_ashby_location_zip",
            ):
                continue
            # Only count verified successes here; demoted rows should be leftovers
            ok = bool(row.get("verified") or (row.get("ok") and row.get("mode") == "file"))
            if not ok:
                continue
            self.record_from_row(row, success=True, pass_i=pass_i, via_override=row.get("via") or phase)
            summary["recorded_ok"] += 1

        for row in report.get("leftovers") or []:
            if not isinstance(row, dict):
                continue
            if row.get("flash_candidate") is False:
                continue
            if row.get("reason") == "unfillable_after_2":
                continue
            if row.get("skipped_already_correct") or row.get("reason") in (
                "already_correct_skip",
                "already_correct_keep",
                "deferred_ashby_location_zip",
            ):
                continue
            k = field_key(
                field_type=row.get("type"),
                label=row.get("label"),
                selector=row.get("selector"),
                field_id=row.get("id") or row.get("field_id"),
            )
            if k in seen_fail_keys:
                continue
            if k in self._unfillable_keys:
                continue
            seen_fail_keys.add(k)
            self.record_from_row(
                row,
                success=False,
                pass_i=pass_i,
                via_override=row.get("via") or phase,
            )
            summary["recorded_fail"] += 1

        summary["unfillable"] = self.unfillable_fields()
        report["field_attempt_log"] = {
            "jsonl": str(self.jsonl_path),
            "unfillable_md": str(self.unfillable_md),
            "fixer_trigger": str(self.fixer_trigger),
            "unfillable_count": len(self._unfillable_keys),
            "unfillable_keys": sorted(self._unfillable_keys),
            "fail_counts": dict(self._fail_counts),
            "run_id": self.run_id,
        }
        return summary

    def fail_count_for(
        self,
        *,
        field_type: str | None = None,
        label: str | None = None,
        selector: str | None = None,
        field_id: str | None = None,
    ) -> int:
        """Failures recorded for this field key (0 if never failed)."""
        key = field_key(
            field_type=field_type, label=label, selector=selector, field_id=field_id
        )
        return int(self._fail_counts.get(key, 0))

    def is_unfillable(
        self,
        *,
        field_type: str | None = None,
        label: str | None = None,
        selector: str | None = None,
        field_id: str | None = None,
    ) -> bool:
        """True after ≥2 fails — stop rewrite thrash; Fixer owns the class bug."""
        key = field_key(
            field_type=field_type, label=label, selector=selector, field_id=field_id
        )
        return key in self._unfillable_keys or self._fail_counts.get(key, 0) >= _FAIL_THRESHOLD

    def unfillable_fields(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for key in sorted(self._unfillable_keys):
            fails = [e for e in self._entries if e.get("field_key") == key and not e.get("success")]
            last = fails[-1] if fails else {}
            out.append(
                {
                    "field_key": key,
                    "fail_count": self._fail_counts.get(key, 0),
                    "field_type": last.get("field_type"),
                    "label": last.get("label"),
                    "last_error": last.get("error"),
                    "last_via": last.get("via"),
                    "must_fix": True,
                }
            )
        return out

    def write_unfillable_markdown(self) -> Path:
        rows = self.unfillable_fields()
        lines = [
            f"# UNFILLABLE AFTER {_FAIL_THRESHOLD} FAILURES",
            "",
            f"- run_id: `{self.run_id}`",
            f"- platform: `{self.platform}`",
            f"- url: {self.url}",
            f"- updated: {_utc_now()}",
            f"- jsonl: `{self.jsonl_path}`",
            "",
            "**Agent4 must-fix** — broad field-class fixes, then retest.",
            "",
        ]
        if not rows:
            lines.append("_No fields have hit the 2-fail threshold yet._")
        else:
            lines.append("| field_key | type | label | fails | last error |")
            lines.append("|-----------|------|-------|-------|------------|")
            for r in rows:
                lines.append(
                    f"| `{r['field_key']}` | {r.get('field_type') or ''} | "
                    f"{(r.get('label') or '')[:60]} | {r.get('fail_count')} | "
                    f"{(r.get('last_error') or '')[:80]} |"
                )
            lines.append("")
            lines.append("## Must-fix items")
            for r in rows:
                lines.append(
                    f"- **{r.get('field_type') or 'UNTYPED'}** "
                    f"`{r.get('label') or r['field_key']}` — "
                    f"{r.get('fail_count')} fails; last via={r.get('last_via')}; "
                    f"err={r.get('last_error')}"
                )
        self.unfillable_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self.unfillable_md

    def _log_unfillable_loud(self, entry: dict[str, Any]) -> None:
        key = entry.get("field_key")
        msg = (
            f"[UNFILLABLE_AFTER_2] field_key={key!r} type={entry.get('field_type')!r} "
            f"label={entry.get('label')!r} fails={entry.get('fail_count_for_key')} "
            f"via={entry.get('via')!r} error={entry.get('error')!r} "
            f"→ {self.unfillable_md}"
        )
        print(msg, flush=True)
        self.write_unfillable_markdown()
        self.write_fixer_trigger()
        self._append_autonomous_loop(entry)
        # Durable continuous-learning lesson (label pattern → avoid strategy)
        try:
            from continuous_learn import demote_selector, record_lesson

            record_lesson(
                label=str(entry.get("label") or ""),
                field_type=str(entry.get("field_type") or ""),
                platform=self.platform,
                avoid_strategy=str(entry.get("via") or "retry_same_selector"),
                reason=f"UNFILLABLE_AFTER_2: {entry.get('error') or ''}"[:160],
            )
            sel = str(entry.get("selector") or "").strip()
            if sel:
                demote_selector(self.platform, sel)
        except Exception:
            pass

    def write_fixer_trigger(self) -> Path:
        """Agent4 inbox: must-fix after 2 fails (class-level, not URL one-offs)."""
        rows = self.unfillable_fields()
        lines = [
            "# FIXER TRIGGER — UNFILLABLE_AFTER_2",
            "",
            f"- run_id: `{self.run_id}`",
            f"- platform: `{self.platform}`",
            f"- url: {self.url}",
            f"- updated: {_utc_now()}",
            f"- unfillable: `{self.unfillable_md}`",
            f"- jsonl: `{self.jsonl_path}`",
            "",
            "**Agent4 (Fixer):** broad field-class fixes, then write `FIX_APPLIED.md`",
            "in this directory (or `FIX_SKIPPED.md`). Retest same URL ≤2.",
            "Dummy only · never Submit · never CAPTCHA · EEO via DeepSeek+dummy "
            "(Decline fallback). No thrash on already-correct fields.",
            "",
            "## Must-fix classes",
            "",
        ]
        if not rows:
            lines.append("_No unfillable fields yet._")
        else:
            for r in rows:
                ft = r.get("field_type") or "UNTYPED"
                err = r.get("last_error") or ""
                hint = _fixer_hint(str(ft), str(err), str(r.get("last_via") or ""))
                lines.append(
                    f"- **{ft}** `{r.get('label') or r['field_key']}` — "
                    f"{r.get('fail_count')} fails; err=`{err}`; via={r.get('last_via')}"
                )
                lines.append(f"  - suggested: {hint}")
            lines.append("")
            lines.append("## Done criteria")
            lines.append("- Code fix landed for the class(es) above")
            lines.append("- `FIX_APPLIED.md` written (paths changed + what was fixed)")
            lines.append("- Retest same URL with `--refill-passes 2` (dummy, never submit)")
        self.fixer_trigger.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self.fixer_trigger

    def _append_autonomous_loop(self, entry: dict[str, Any]) -> None:
        try:
            self.autonomous_loop.parent.mkdir(parents=True, exist_ok=True)
            block = (
                f"\n### UNFILLABLE_AFTER_2 — {entry.get('field_type') or 'UNTYPED'} "
                f"({_utc_now()})\n"
                f"- field_key: `{entry.get('field_key')}`\n"
                f"- label: {entry.get('label')}\n"
                f"- fails: {entry.get('fail_count_for_key')}\n"
                f"- via: {entry.get('via')} / error: {entry.get('error')}\n"
                f"- platform: {self.platform} / run: `{self.run_id}`\n"
                f"- log: `{self.jsonl_path}`\n"
                f"- must-fix: `{self.unfillable_md}`\n"
                f"- fixer: `{self.fixer_trigger}`\n"
            )
            with self.autonomous_loop.open("a", encoding="utf-8") as fh:
                fh.write(block)
        except OSError:
            pass


def _fixer_hint(field_type: str, error: str, via: str) -> str:
    err = (error or "").lower()
    via_l = (via or "").lower()
    if "resume" in err or field_type == "RESUME_UPLOAD":
        return "resume_upload.py / file-input detect + verify dummy PDF"
    if "gh_select" in err or "no matching option" in err or "gh_select" in via_l:
        return "gh_select.py + dummy SCHOOL/DEGREE/salary option lists"
    if "live_empty" in err or "claimed_verified" in err:
        return "demote filled→leftover; pack selectors; SPA remount reassert"
    if "flash" in via_l:
        return "prefill pack / field_map so deterministic type skips Flash"
    return "field_map / selector pack / widget for this type; then retest"


def attach_attempt_log(
    report: dict[str, Any],
    *,
    cycle_dir: Path | str | None = None,
    run_id: str | None = None,
) -> FieldAttemptLog:
    """Create / reuse FieldAttemptLog on report (report['_attempt_log'])."""
    existing = report.get("_attempt_log")
    if isinstance(existing, FieldAttemptLog):
        return existing
    url = str(report.get("url") or "")
    platform = str(report.get("platform") or "")
    rid = run_id or str(
        report.get("alias_token")
        or report.get("email_alias")
        or report.get("identity_email")
        or f"run_{int(time.time())}"
    )
    if cycle_dir is None:
        rp = report.get("report_path") or report.get("screenshot") or report.get("hold_snapshot")
        if rp:
            cycle_dir = Path(str(rp)).parent
        else:
            cycle_dir = DEFAULT_RESULTS / f"field_attempts_{rid}"
    log = FieldAttemptLog(cycle_dir, run_id=rid, url=url, platform=platform)
    report["_attempt_log"] = log
    report["field_attempt_log_path"] = str(log.jsonl_path)
    return log


def note_attempt(report: dict[str, Any], **kwargs: Any) -> dict[str, Any] | None:
    """Convenience: record one attempt if a log is attached (no-op otherwise)."""
    log = report.get("_attempt_log")
    if not isinstance(log, FieldAttemptLog):
        # Lazy-attach under real_job_results if report has identity
        if report.get("alias_token") or report.get("url"):
            log = attach_attempt_log(report)
        else:
            return None
    return log.record(**kwargs)


def self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        log = FieldAttemptLog(td, run_id="test_run", url="https://example.com", platform="greenhouse")
        log.record(field_type="SCHOOL", label="School*", via="gh_select", success=False, error="gh_select_failed", pass_i=1)
        log.record(field_type="SCHOOL", label="School*", via="gh_select", success=False, error="gh_select_failed", pass_i=2)
        assert "SCHOOL|lab:school*" in log._unfillable_keys
        assert log.unfillable_md.is_file()
        assert log.fixer_trigger.is_file()
        assert "FIXER TRIGGER" in log.fixer_trigger.read_text()
        assert log.jsonl_path.is_file()
        lines = log.jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 2
        # success resets nothing — still unfillable once flagged
        log.record(field_type="EMAIL", label="Email", via="pack", success=True)
        assert len(log.unfillable_fields()) == 1
    print("field_attempt_log self_test OK")


if __name__ == "__main__":
    self_test()
