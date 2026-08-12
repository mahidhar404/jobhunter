"""Page-session field locks + thrash detection for autofill honesty.

Control loop: Discover → Act once → Verify commit → **Lock** → Decide
(Next / leftover / LLM / stop).

After a commit-verified fill, lock the field identity for the current page
session. Refill, phase re-entry, alias walks, and Flash must skip locks.
A re-touch attempt on a locked field increments ``thrash_retouches`` and must
not re-act (safety net — callers should filter locks before calling fill).

Locks clear on page ADVANCE (new page session). Dummy-only; never-submit.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

# Once any identity for these types is locked, every identity of that type
# is treated as locked (page-singleton widgets — resume, etc.).
SINGLETON_LOCK_TYPES = frozenset({"RESUME_UPLOAD", "WORKED_HERE_BEFORE", "HOW_HEARD"})


def field_identity_key(
    *,
    field_type: str | None = None,
    label: str | None = None,
    selector: str | None = None,
    automation_id: str | None = None,
    field_id: str | None = None,
) -> str:
    """Stable identity for a field within one page session.

    Prefer automation-id / field-id, then type+label, then selector.
    """
    ft = (field_type or "").strip().upper() or "UNTYPED"
    aid = (automation_id or field_id or "").strip()
    lab = re.sub(r"\s+", " ", (label or "").strip().lower())[:80]
    sel = (selector or "").strip()[:120]
    if aid:
        return f"{ft}|aid:{aid}"
    if lab:
        return f"{ft}|lab:{lab}"
    if sel:
        return f"{ft}|sel:{sel}"
    return ft


@dataclass
class LockEntry:
    key: str
    field_type: str | None = None
    label: str | None = None
    selector: str | None = None
    automation_id: str | None = None
    readback: str | None = None
    via: str | None = None
    locked_at: float = 0.0
    page_epoch: int = 0


@dataclass
class FieldLockSession:
    """Mutable page-session lock registry bound to one fill run."""

    thrash_retouches: int = 0
    page_epoch: int = 0
    _locks: dict[str, LockEntry] = field(default_factory=dict)
    _attempt_counts: dict[str, int] = field(default_factory=dict)
    _thrash_by_key: dict[str, int] = field(default_factory=dict)
    _page_started_at: float = field(default_factory=time.monotonic)
    _first_fill_after_advance_at: float | None = None
    _advance_count: int = 0
    _lock_skips: int = 0

    def identity_key(self, **kwargs: Any) -> str:
        return field_identity_key(**kwargs)

    def is_locked(
        self,
        *,
        field_type: str | None = None,
        label: str | None = None,
        selector: str | None = None,
        automation_id: str | None = None,
        field_id: str | None = None,
        key: str | None = None,
    ) -> bool:
        ft = (field_type or "").strip().upper()
        if ft in SINGLETON_LOCK_TYPES and ft in self.locked_types():
            return True
        k = key or field_identity_key(
            field_type=field_type,
            label=label,
            selector=selector,
            automation_id=automation_id,
            field_id=field_id,
        )
        return k in self._locks

    def lock(
        self,
        *,
        field_type: str | None = None,
        label: str | None = None,
        selector: str | None = None,
        automation_id: str | None = None,
        field_id: str | None = None,
        readback: str | None = None,
        via: str | None = None,
        key: str | None = None,
    ) -> LockEntry:
        """Lock after commit-verified fill. Idempotent for same key."""
        ft = (field_type or "").strip().upper() or None
        # Prefer stable aid for resume so pack/ensure/phase_c share one key.
        if ft == "RESUME_UPLOAD" and not (automation_id or field_id):
            automation_id = "file-upload-input-ref"
        k = key or field_identity_key(
            field_type=field_type,
            label=label,
            selector=selector,
            automation_id=automation_id,
            field_id=field_id,
        )
        now = time.monotonic()
        if self._first_fill_after_advance_at is None:
            self._first_fill_after_advance_at = now
        existing = self._locks.get(k)
        if existing is not None:
            if readback:
                existing.readback = str(readback)[:120]
            if via:
                existing.via = via
            return existing
        entry = LockEntry(
            key=k,
            field_type=(field_type or "").strip().upper() or None,
            label=(label or "")[:120] or None,
            selector=(selector or "")[:160] or None,
            automation_id=(automation_id or field_id or "")[:80] or None,
            readback=(str(readback)[:120] if readback is not None else None),
            via=(via or None),
            locked_at=now,
            page_epoch=self.page_epoch,
        )
        self._locks[k] = entry
        return entry

    def note_attempt(self, key: str) -> int:
        """Count a fill/select attempt (first-touch or otherwise)."""
        self._attempt_counts[key] = self._attempt_counts.get(key, 0) + 1
        return self._attempt_counts[key]

    def note_retouch(self, key: str) -> int:
        """Safety-net: locked field was targeted again. Increments thrash."""
        self.thrash_retouches += 1
        self._thrash_by_key[key] = self._thrash_by_key.get(key, 0) + 1
        self._lock_skips += 1
        return self.thrash_retouches

    def gate(
        self,
        *,
        field_type: str | None = None,
        label: str | None = None,
        selector: str | None = None,
        automation_id: str | None = None,
        field_id: str | None = None,
    ) -> dict[str, Any]:
        """Decide whether an action may proceed.

        Returns::
            {"action": "proceed"|"lock_skip", "key": str, "thrash": bool, ...}
        """
        ft = (field_type or "").strip().upper()
        # Prior-employer radios: lock is page-singleton — flash must not retouch
        # under a different label key (Sandoz "employed by a Sandoz Company?").
        try:
            from field_map import WORKED_HERE_BEFORE as WHB, is_worked_here_label

            wh_locked = WHB in self.locked_types()
            if wh_locked and (
                ft == WHB
                or is_worked_here_label(label or "")
                or "candidateispreviousworker" in (selector or "").lower()
            ):
                entry = next(
                    (e for e in self._locks.values() if e.field_type == WHB),
                    None,
                )
                k = (
                    entry.key
                    if entry is not None
                    else field_identity_key(
                        field_type=WHB,
                        label=label,
                        selector=selector,
                        automation_id=automation_id or "worked_here_before",
                        field_id=field_id,
                    )
                )
                self.note_retouch(k)
                return {
                    "action": "lock_skip",
                    "key": k,
                    "thrash": True,
                    "thrash_retouches": self.thrash_retouches,
                    "readback": entry.readback if entry else None,
                    "locked_via": entry.via if entry else None,
                    "attempt_count": self._attempt_counts.get(k, 0),
                    "singleton_type": WHB,
                }
        except Exception:
            pass
        # How-heard: one chip per page — block alias/fiber/Flash revisits on any variant.
        try:
            from field_map import HOW_HEARD as HH

            hh_locked = HH in self.locked_types()
            aid_l = (automation_id or field_id or "").lower()
            sel_l = (selector or "").lower()
            lab_l = (label or "").lower()
            if hh_locked and (
                ft == HH
                or aid_l in ("how_heard", "source--source", "source")
                or "source--source" in sel_l
                or re.search(r"how\s+did\s+you\s+hear|where\s+did\s+you\s+hear", lab_l)
            ):
                entry = next(
                    (e for e in self._locks.values() if e.field_type == HH),
                    None,
                )
                k = (
                    entry.key
                    if entry is not None
                    else field_identity_key(
                        field_type=HH,
                        label=label,
                        selector=selector,
                        automation_id=automation_id or "how_heard",
                        field_id=field_id,
                    )
                )
                self.note_retouch(k)
                return {
                    "action": "lock_skip",
                    "key": k,
                    "thrash": True,
                    "thrash_retouches": self.thrash_retouches,
                    "readback": entry.readback if entry else None,
                    "locked_via": entry.via if entry else None,
                    "attempt_count": self._attempt_counts.get(k, 0),
                    "singleton_type": HH,
                }
        except Exception:
            pass
        # Resume (and other singletons): any prior lock for the type blocks
        # every selector/label variant — same class of thrash as how-heard.
        if ft in SINGLETON_LOCK_TYPES and ft in self.locked_types():
            entry = next(
                (e for e in self._locks.values() if e.field_type == ft),
                None,
            )
            k = (
                entry.key
                if entry is not None
                else field_identity_key(
                    field_type=field_type,
                    label=label,
                    selector=selector,
                    automation_id=automation_id or "file-upload-input-ref",
                    field_id=field_id,
                )
            )
            self.note_retouch(k)
            return {
                "action": "lock_skip",
                "key": k,
                "thrash": True,
                "thrash_retouches": self.thrash_retouches,
                "readback": entry.readback if entry else None,
                "locked_via": entry.via if entry else None,
                "attempt_count": self._attempt_counts.get(k, 0),
                "singleton_type": ft,
            }
        k = field_identity_key(
            field_type=field_type,
            label=label,
            selector=selector,
            automation_id=automation_id,
            field_id=field_id,
        )
        if k in self._locks:
            self.note_retouch(k)
            entry = self._locks[k]
            return {
                "action": "lock_skip",
                "key": k,
                "thrash": True,
                "thrash_retouches": self.thrash_retouches,
                "readback": entry.readback,
                "locked_via": entry.via,
                "attempt_count": self._attempt_counts.get(k, 0),
            }
        attempt_n = self.note_attempt(k)
        return {
            "action": "proceed",
            "key": k,
            "thrash": False,
            "attempt_count": attempt_n,
        }

    def clear_for_new_page(self) -> dict[str, Any]:
        """Clear locks after ADVANCE to a new page. Keeps cumulative thrash metrics."""
        cleared = len(self._locks)
        prev_ttf = self.time_to_first_fill_s()
        self._locks.clear()
        self.page_epoch += 1
        self._advance_count += 1
        self._page_started_at = time.monotonic()
        self._first_fill_after_advance_at = None
        return {
            "cleared_locks": cleared,
            "page_epoch": self.page_epoch,
            "prior_time_to_first_fill_s": prev_ttf,
        }

    def time_to_first_fill_s(self) -> float | None:
        if self._first_fill_after_advance_at is None:
            return None
        return round(self._first_fill_after_advance_at - self._page_started_at, 3)

    def locked_keys(self) -> list[str]:
        return sorted(self._locks.keys())

    def locked_types(self) -> set[str]:
        out: set[str] = set()
        for e in self._locks.values():
            if e.field_type:
                out.add(e.field_type)
        return out

    def metrics(self) -> dict[str, Any]:
        return {
            "thrash_retouches": int(self.thrash_retouches),
            "lock_skips": int(self._lock_skips),
            "locked_count": len(self._locks),
            "locked_keys": self.locked_keys(),
            "per_field_attempts": dict(sorted(self._attempt_counts.items())),
            "thrash_by_key": dict(sorted(self._thrash_by_key.items())),
            "time_to_first_fill_after_advance_s": self.time_to_first_fill_s(),
            "page_epoch": int(self.page_epoch),
            "advance_count": int(self._advance_count),
        }

    def lock_skip_result(
        self,
        gate_info: dict[str, Any],
        *,
        automation_id: str | None = None,
        field_type: str | None = None,
    ) -> dict[str, Any]:
        """Standard fill-result dict when gate says lock_skip."""
        return {
            "automation_id": automation_id,
            "type": field_type,
            "status": "filled",
            "reason": "field_locked_skip",
            "skipped_already_correct": True,
            "skipped_locked": True,
            "verified": True,
            "ok": True,
            "readback": gate_info.get("readback"),
            "value": gate_info.get("readback"),
            "thrash_retouch": True,
            "field_lock_key": gate_info.get("key"),
        }


def attach_field_locks(report: dict, *, reset: bool = False) -> FieldLockSession:
    """Attach / reuse FieldLockSession on ``report['_field_locks']``."""
    existing = report.get("_field_locks")
    if isinstance(existing, FieldLockSession) and not reset:
        return existing
    session = FieldLockSession()
    report["_field_locks"] = session
    return session


def get_field_locks(report: dict | None) -> FieldLockSession | None:
    if not report:
        return None
    sess = report.get("_field_locks")
    return sess if isinstance(sess, FieldLockSession) else None


def resolve_lock_report(report: dict | None) -> dict | None:
    """Prefer parent step report (Workday nested) when present."""
    if not report:
        return None
    parent = report.get("_step_report")
    if isinstance(parent, dict):
        return parent
    return report


def gate_field_action(
    report: dict | None,
    *,
    field_type: str | None = None,
    label: str | None = None,
    selector: str | None = None,
    automation_id: str | None = None,
    field_id: str | None = None,
) -> dict[str, Any] | None:
    """Gate via report locks. None = no session (proceed). Else gate dict."""
    sess = get_field_locks(resolve_lock_report(report))
    if sess is None:
        return None
    return sess.gate(
        field_type=field_type,
        label=label,
        selector=selector,
        automation_id=automation_id,
        field_id=field_id,
    )


def lock_verified_field(
    report: dict | None,
    row: dict | None = None,
    *,
    field_type: str | None = None,
    label: str | None = None,
    selector: str | None = None,
    automation_id: str | None = None,
    field_id: str | None = None,
    readback: str | None = None,
    via: str | None = None,
) -> LockEntry | None:
    """Lock after commit-verified fill. No-op without session / verification."""
    sess = get_field_locks(resolve_lock_report(report))
    if sess is None:
        return None
    row = row or {}
    ft = field_type or row.get("type")
    lab = label if label is not None else row.get("label")
    sel = selector if selector is not None else row.get("selector")
    aid = (
        automation_id
        if automation_id is not None
        else (row.get("automation_id") or row.get("field_id"))
    )
    rb = readback
    if rb is None:
        rb = row.get("readback") or row.get("picked") or row.get("value")
    v = via or row.get("via")
    return sess.lock(
        field_type=str(ft) if ft else None,
        label=str(lab) if lab else None,
        selector=str(sel) if sel else None,
        automation_id=str(aid) if aid else None,
        field_id=field_id,
        readback=str(rb) if rb is not None else None,
        via=str(v) if v else None,
    )


def clear_locks_on_advance(report: dict | None) -> dict[str, Any] | None:
    """Clear page-session locks after a successful ADVANCE click."""
    sess = get_field_locks(resolve_lock_report(report))
    if sess is None:
        return None
    info = sess.clear_for_new_page()
    parent = resolve_lock_report(report)
    if parent is not None:
        parent["field_lock_advance_clear"] = info
        try:
            from fill_step_log import note_step

            note_step(
                parent,
                action="field_locks_cleared",
                reason=f"cleared={info.get('cleared_locks')} epoch={info.get('page_epoch')}",
                via="field_lock",
            )
        except Exception:
            pass
    return info


def fold_lock_metrics(report: dict) -> dict[str, Any]:
    """Persist serializable lock/thrash metrics onto the report."""
    sess = get_field_locks(resolve_lock_report(report))
    if sess is None:
        report.setdefault(
            "field_lock",
            {
                "thrash_retouches": 0,
                "locked_count": 0,
                "per_field_attempts": {},
                "time_to_first_fill_after_advance_s": None,
            },
        )
        return report["field_lock"]
    metrics = sess.metrics()
    report["field_lock"] = metrics
    report["thrash_retouches"] = metrics["thrash_retouches"]
    report["time_to_first_fill_after_advance_s"] = metrics[
        "time_to_first_fill_after_advance_s"
    ]
    return metrics


def apply_thrash_verdict_gate(report: dict) -> dict:
    """Demote SUCCESS when thrash_retouches > 0 (dummy honesty).

    Lock-skip safety-net firings mean something re-targeted a committed field —
    that is wasted work. Prefer FAIL over dishonest SUCCESS; surface metrics
    always. Does not invent blockers for incomplete mid-wizard FAILs.
    """
    metrics = fold_lock_metrics(report)
    thrash = int(metrics.get("thrash_retouches") or report.get("thrash_retouches") or 0)
    if thrash <= 0:
        return report
    report["thrash_detected"] = True
    report.setdefault("thrash_warning", f"thrash_retouches={thrash}")
    if report.get("verdict") == "SUCCESS":
        report["verdict"] = "FAIL"
        report["verdict_reason"] = "thrash_retouches"
        report["thrash_demoted"] = True
    return report


def page_complete_should_advance(
    *,
    required_empty: list | None,
    footer_kind: str | None,
    footer_label: str | None = None,
    gaps_blocking: bool = False,
) -> bool:
    """True when page is done and footer is ADVANCE → click Next once.

    Never review-hold while footer is ADVANCE. Callers must still route the
    click through button_gate (never FINAL).
    """
    if required_empty:
        return False
    if gaps_blocking:
        return False
    from page_progress import footer_primary_wizard_incomplete

    decision = footer_primary_wizard_incomplete(footer_kind, footer_label)
    # True means ADVANCE / incomplete wizard → should advance (not hold)
    return decision is True


def analyze_step_log_waste(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Offline analysis of fill_steps for thrash / stalls / duplicate actions.

    Used by tests and post-run memory notes. Pure — no Playwright.
    """
    from datetime import datetime

    by_field: dict[str, list[dict]] = {}
    duplicate_fills: list[dict] = []
    how_heard_attempts: list[dict] = []
    resume_upload_attempts: list[dict] = []
    long_gaps: list[dict] = []
    open_menu_idle: list[dict] = []
    lock_skips: list[dict] = []
    thrash_events: list[dict] = []

    prev_ts: datetime | None = None
    prev_step: dict | None = None

    act_fill = {
        "fill_text",
        "select_word_by_word",
        "click_yes_no",
        "upload_resume",
        "combobox",
        "fill_select",
    }

    for step in steps:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "")
        ft = str(step.get("field_type") or step.get("label") or "")
        key = f"{ft}|{str(step.get('label') or '')[:40]}"
        if action in act_fill or action in (
            "skip_already_correct",
            "lock_skip",
            "thrash_retouch",
            "field_locked_skip",
            "upload_resume_start",
            "upload_resume_verified",
        ):
            by_field.setdefault(key, []).append(step)
        if action in act_fill:
            prior = [
                s
                for s in by_field.get(key, [])
                if s is not step and str(s.get("action") or "") in act_fill
            ]
            if prior:
                duplicate_fills.append(
                    {
                        "field": key,
                        "step": step.get("step"),
                        "action": action,
                        "prior_steps": [p.get("step") for p in prior],
                    }
                )
        if action in ("upload_resume", "upload_resume_start"):
            resume_upload_attempts.append(step)
        if "heard" in ft.lower() or "heard" in str(step.get("label") or "").lower():
            how_heard_attempts.append(step)
        if action in ("lock_skip", "field_locked_skip") or step.get("skipped_locked"):
            lock_skips.append(step)
        if action == "thrash_retouch" or step.get("thrash_retouch"):
            thrash_events.append(step)
        if action in ("menu_open", "listbox_open") or "listbox" in action:
            open_menu_idle.append(step)

        ts_raw = step.get("ts")
        ts: datetime | None = None
        if isinstance(ts_raw, str):
            for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
                try:
                    ts = datetime.strptime(ts_raw, fmt)
                    break
                except ValueError:
                    continue
        if ts and prev_ts and prev_step is not None:
            gap = (ts - prev_ts).total_seconds()
            if gap >= 2.5:
                long_gaps.append(
                    {
                        "gap_s": gap,
                        "after_step": prev_step.get("step"),
                        "after_action": prev_step.get("action"),
                        "after_field": prev_step.get("field_type"),
                        "before_step": step.get("step"),
                        "before_action": step.get("action"),
                        "before_field": step.get("field_type"),
                    }
                )
        if ts:
            prev_ts = ts
            prev_step = step

    multi_touch = {
        k: len(v)
        for k, v in by_field.items()
        if sum(1 for s in v if str(s.get("action") or "") in act_fill) > 1
    }
    return {
        "step_count": len(steps),
        "duplicate_fills": duplicate_fills,
        "multi_touch_fields": multi_touch,
        "how_heard_attempts": len(how_heard_attempts),
        "how_heard_steps": [
            {
                "step": s.get("step"),
                "action": s.get("action"),
                "reason": s.get("reason"),
                "after": s.get("after"),
            }
            for s in how_heard_attempts
        ],
        "resume_upload_attempts": len(resume_upload_attempts),
        "resume_upload_steps": [
            {
                "step": s.get("step"),
                "action": s.get("action"),
                "reason": s.get("reason"),
                "via": s.get("via"),
                "after": s.get("after"),
            }
            for s in resume_upload_attempts
        ],
        "long_gaps_ge_2_5s": long_gaps,
        "lock_skips": len(lock_skips),
        "thrash_events": len(thrash_events),
        "open_menu_signals": len(open_menu_idle),
        "waste_score": len(duplicate_fills)
        + len(multi_touch)
        + max(0, len(how_heard_attempts) - 1)
        + max(0, len(resume_upload_attempts) - 1)
        + sum(1 for g in long_gaps if g["gap_s"] >= 5 and g.get("after_action") in act_fill),
    }


def self_test() -> None:
    s = FieldLockSession()
    g1 = s.gate(field_type="HOW_HEARD", automation_id="how_heard")
    assert g1["action"] == "proceed"
    s.lock(
        field_type="HOW_HEARD",
        automation_id="how_heard",
        readback="Indeed",
        via="test",
    )
    g2 = s.gate(field_type="HOW_HEARD", automation_id="how_heard")
    assert g2["action"] == "lock_skip" and g2["thrash"] is True
    assert s.thrash_retouches == 1
    # Alias walk identity (same aid) must stay locked
    g3 = s.gate(field_type="HOW_HEARD", automation_id="how_heard", label="LinkedIn")
    assert g3["action"] == "lock_skip"
    assert s.thrash_retouches == 2
    cleared = s.clear_for_new_page()
    assert cleared["cleared_locks"] == 1
    assert not s.is_locked(field_type="HOW_HEARD", automation_id="how_heard")
    assert page_complete_should_advance(
        required_empty=[], footer_kind="ADVANCE", footer_label="Save and Continue"
    )
    assert not page_complete_should_advance(
        required_empty=[{"id": "x"}], footer_kind="ADVANCE", footer_label="Next"
    )
    assert not page_complete_should_advance(
        required_empty=[], footer_kind="FINAL", footer_label="Submit"
    )
    report: dict = {"verdict": "SUCCESS"}
    attach_field_locks(report)
    get_field_locks(report).thrash_retouches = 2  # type: ignore[union-attr]
    apply_thrash_verdict_gate(report)
    assert report["verdict"] == "FAIL"
    assert report.get("thrash_demoted") is True
    waste = analyze_step_log_waste(
        [
            {"step": 1, "ts": "2026-08-10T06:00:00Z", "action": "fill_text", "field_type": "NAME_FIRST"},
            {"step": 2, "ts": "2026-08-10T06:00:01Z", "action": "fill_text", "field_type": "NAME_FIRST"},
            {"step": 3, "ts": "2026-08-10T06:00:05Z", "action": "select_word_by_word", "field_type": "HOW_HEARD"},
            {"step": 4, "ts": "2026-08-10T06:00:06Z", "action": "select_word_by_word", "field_type": "HOW_HEARD", "after": "LinkedIn"},
        ]
    )
    assert waste["duplicate_fills"]
    assert waste["how_heard_attempts"] == 2
    print("field_lock.self_test: OK")


if __name__ == "__main__":
    self_test()
