"""Per-action audit loop — supervisor between every fill/click/select.

After each field action the supervisor:
  1. Re-reads DOM readback (when locator/page available)
  2. Judges via ``action_judge.judge_field_action``
  3. Verdict → OK | THRASH (lock+skip) | WRONG (one fix) | STUCK (escalate)
  4. Appends ``action_audit.jsonl`` + a ``fill_steps`` ``action_audit`` row

Thrashing: same field touched N times without readback change → lock+skip.

Dummy-only observability. Never-submit. Disable: ``FASTFILL_ACTION_SUPERVISOR=0``.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from action_judge import judge_field_action, record_action_judge
from verified_select import value_matches_readback

FixFn = Callable[[], Awaitable[str]]

_DEFAULT_THRASH_TOUCHES = int(os.environ.get("FASTFILL_SUPERVISOR_THRASH_N") or "3")


async def _read_locator_readback(loc: Any) -> str:
    """Lazy delegate — avoids importing fast_fill at module load."""
    try:
        from fast_fill import _read_locator_value

        return await _read_locator_value(loc)
    except Exception:
        try:
            return (await loc.input_value()) or ""
        except Exception:
            return ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def supervisor_enabled() -> bool:
    return (os.environ.get("FASTFILL_ACTION_SUPERVISOR") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _field_key(
    *,
    field: str = "",
    field_type: str = "",
    selector: str = "",
    automation_id: str = "",
) -> str:
    aid = (automation_id or field or "").strip()
    if aid:
        return aid
    ft = (field_type or "UNTYPED").strip().upper()
    sel = (selector or "").strip()[:80]
    if sel:
        return f"{ft}|{sel}"
    return ft or "unknown"


class ActionSupervisor:
    """Page-session supervisor: audit after every action, one corrective retry."""

    def __init__(
        self,
        out_dir: Path | str,
        *,
        thrash_limit: int = _DEFAULT_THRASH_TOUCHES,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.out_dir / "action_audit.jsonl"
        self.thrash_limit = max(2, int(thrash_limit))
        self._action_seq = 0
        # field_key -> {readback, touches, fix_attempted}
        self._touch_state: dict[str, dict[str, Any]] = {}

    def _next_action_id(self) -> str:
        self._action_seq += 1
        return f"a{self._action_seq:04d}"

    def _append_audit(self, row: dict[str, Any]) -> None:
        try:
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        except OSError:
            pass

    def _track_touch(self, key: str, readback: str) -> int:
        """Return touch count for same field+readback (thrash detection)."""
        rb = (readback or "").strip()
        st = self._touch_state.get(key)
        if st and st.get("readback") == rb:
            st["touches"] = int(st.get("touches") or 0) + 1
        else:
            st = {"readback": rb, "touches": 1, "fix_attempted": False}
            self._touch_state[key] = st
        return int(st["touches"])

    def _map_supervisor_verdict(
        self,
        judge: dict[str, Any],
        *,
        intent: str,
        after: str,
        touches: int,
        locked: bool,
    ) -> str:
        jv = str(judge.get("verdict") or "")
        if locked:
            return "OK"
        if jv == "thrash_rewrite" or judge.get("thrash"):
            return "THRASH"
        if jv == "wrong_autofill":
            return "WRONG"
        if touches >= self.thrash_limit:
            return "THRASH"
        if intent and after and value_matches_readback(intent, after, mode="fill"):
            return "OK"
        if jv == "correct_skip":
            if intent and after and not value_matches_readback(intent, after, mode="fill"):
                return "WRONG"
            return "OK"
        if intent and after and not value_matches_readback(intent, after, mode="fill"):
            return "WRONG"
        if jv == "needed_fill" and not after.strip():
            return "WRONG"
        if not after.strip() and intent:
            return "WRONG"
        return "OK"

    async def _consult_field_done(
        self,
        page: Any,
        *,
        field_type: str,
        intent: str,
        automation_id: str = "",
        selector: str = "",
        readback: str = "",
        action: str = "",
        widget: str = "",
        mode: str = "",
    ) -> tuple[bool, str]:
        """Unified completion check before OK/skip lock."""
        if page is None or not (field_type or intent):
            return True, ""
        try:
            from field_done import field_is_done, field_is_done_from_readback
            from workday_date_readback import (
                date_spin_field_meta,
                is_date_spin_context,
                normalize_spin_readback,
            )

            meta: dict[str, Any] = {
                "type": field_type,
                "automation_id": automation_id,
                "selector": selector,
            }
            if field_type.upper() in ("FIELD_OF_STUDY", "DISCIPLINE", "MAJOR"):
                meta["dom_chip"] = True
            spin_ctx = is_date_spin_context(
                field_type=field_type,
                action=action,
                widget=widget,
                mode=mode,
                readback=readback,
            )
            if spin_ctx:
                meta.update(date_spin_field_meta(field_type, intent))
            rb_probe: object = readback
            if spin_ctx and readback:
                rb_probe = normalize_spin_readback(readback) or readback
            if rb_probe and (spin_ctx or (not automation_id and not selector)):
                v = field_is_done_from_readback(rb_probe, meta, intent)
            else:
                v = await field_is_done(page, meta, intent)
            return v.ok, str(v.reason or "")
        except Exception:
            return False, "field_done_error"

    async def audit_after_action(
        self,
        report: dict | None,
        *,
        field: str,
        field_type: str = "",
        intent: str = "",
        before: str = "",
        after: str = "",
        action: str = "fill",
        selector: str = "",
        automation_id: str = "",
        locked: bool = False,
        locator: Any = None,
        fix_fn: FixFn | None = None,
        via: str = "",
        page: Any = None,
    ) -> dict[str, Any]:
        """Audit one action; optionally run one corrective fix + re-audit."""
        action_id = self._next_action_id()
        key = _field_key(
            field=field,
            field_type=field_type,
            selector=selector,
            automation_id=automation_id,
        )

        live_after = (after or "").strip()
        if locator is not None:
            live_after = (await _read_locator_readback(locator)).strip() or live_after

        judge = judge_field_action(
            field=field or key,
            before=before,
            after=live_after,
            intent=intent,
            action=action,
            locked=locked,
        )
        record_action_judge(report, judge)

        touches = self._track_touch(key, live_after)
        fix_attempted = False
        supervisor_verdict = self._map_supervisor_verdict(
            judge,
            intent=intent,
            after=live_after,
            touches=touches,
            locked=locked,
        )
        issue = ""

        # Reject OK/autofill lock when unified contract says wrong/incomplete.
        want = (intent or "").strip()
        jv = str(judge.get("verdict") or "")
        if want and supervisor_verdict in ("OK", "THRASH"):
            done_ok, done_reason = await self._consult_field_done(
                page,
                field_type=field_type,
                intent=want,
                automation_id=automation_id or field,
                selector=selector,
                readback=live_after,
                action=action,
            )
            if not done_ok:
                if jv == "correct_skip" and done_reason in (
                    "empty_readback",
                    "field_done_error",
                ):
                    # Already-correct skip: a missed live probe must not force a rewrite.
                    issue = done_reason
                elif done_reason in (
                    "fos_chip_wrong_value",
                    "chip_wrong_value",
                    "text_mismatch",
                    "fos_uncommitted",
                    "empty_readback",
                    "placeholder_or_uncommitted",
                    "how_heard_uncommitted",
                    "phone_country_not_us",
                    "radio_unanswered",
                    "degree_hash_readback",
                ) or jv == "wrong_autofill":
                    supervisor_verdict = "WRONG"
                    issue = done_reason or "field_done_reject"
                elif supervisor_verdict == "THRASH":
                    supervisor_verdict = "STUCK"
                    issue = done_reason or "field_done_reject_no_lock"

        if supervisor_verdict == "WRONG" and fix_fn is not None:
            st = self._touch_state.get(key) or {}
            if not st.get("fix_attempted"):
                st["fix_attempted"] = True
                fix_attempted = True
                try:
                    live_after = (await fix_fn()).strip()
                    judge = judge_field_action(
                        field=field or key,
                        before=before,
                        after=live_after,
                        intent=intent,
                        action=action,
                    )
                    record_action_judge(report, {**judge, "fix_retry": True})
                    supervisor_verdict = self._map_supervisor_verdict(
                        judge,
                        intent=intent,
                        after=live_after,
                        touches=touches,
                        locked=False,
                    )
                    if supervisor_verdict == "WRONG":
                        supervisor_verdict = "STUCK"
                        issue = "fix_retry_still_wrong"
                except Exception as e:
                    supervisor_verdict = "STUCK"
                    issue = f"fix_error:{str(e)[:80]}"

        if supervisor_verdict == "THRASH":
            issue = issue or str(judge.get("reason") or "thrash_detected")
            # Never lock wrong autofill as verified — field_done gate above.
            if issue != "field_done_reject_no_lock":
                self._lock_field(
                    report,
                    field_type=field_type,
                    selector=selector,
                    automation_id=automation_id or field,
                    readback=live_after,
                    via=via or "action_supervisor",
                )
        elif supervisor_verdict == "STUCK":
            issue = issue or "supervisor_stuck"
            if report is not None:
                report.setdefault("supervisor_stuck", []).append(
                    {
                        "action_id": action_id,
                        "field": key,
                        "intent": (intent or "")[:80],
                        "after": live_after[:80],
                    }
                )

        audit_row: dict[str, Any] = {
            "action_id": action_id,
            "ts": _utc_now(),
            "ts_epoch": time.time(),
            "field": field or key,
            "field_type": field_type or None,
            "selector": (selector or "")[:120] or None,
            "automation_id": (automation_id or "")[:120] or None,
            "action": action,
            "before": (before or "")[:120],
            "after": live_after[:120],
            "intent": (intent or "")[:120],
            "judge_verdict": judge.get("verdict"),
            "judge_reason": judge.get("reason"),
            "supervisor_verdict": supervisor_verdict,
            "fix_attempted": fix_attempted,
            "touch_count": touches,
            "issue": issue or None,
            "via": (via or "")[:64] or None,
        }
        self._append_audit(audit_row)

        if report is not None:
            lst = report.setdefault("action_audit", [])
            if len(lst) < 500:
                lst.append(audit_row)
            try:
                from fill_step_log import note_step

                note_step(
                    report,
                    action="action_audit",
                    field_type=field_type or field[:48],
                    label=field[:80],
                    before=before,
                    after=live_after,
                    via=via or "action_supervisor",
                    reason=f"{supervisor_verdict}:{judge.get('reason') or ''}"[:240],
                    extra={
                        "action_id": action_id,
                        "supervisor_verdict": supervisor_verdict,
                        "fix_attempted": fix_attempted,
                        "touch_count": touches,
                    },
                )
            except Exception:
                pass

        return {
            **audit_row,
            "continue": supervisor_verdict in ("OK", "WRONG"),
            "skip_field": supervisor_verdict == "THRASH",
            "stuck": supervisor_verdict == "STUCK",
            "readback": live_after,
        }

    @staticmethod
    def _lock_field(
        report: dict | None,
        *,
        field_type: str,
        selector: str,
        automation_id: str,
        readback: str,
        via: str,
    ) -> None:
        if not report:
            return
        try:
            from field_lock import lock_verified_field

            lock_verified_field(
                report,
                {
                    "readback": readback,
                    "verified": True,
                    "ok": True,
                    "reason": "supervisor_thrash_lock",
                },
                field_type=field_type or None,
                selector=selector or None,
                automation_id=automation_id or None,
                readback=readback,
                via=via,
            )
        except Exception:
            pass


def attach_action_supervisor(report: dict[str, Any]) -> ActionSupervisor | None:
    """Create / reuse ActionSupervisor on report (``report['_action_supervisor']``)."""
    if not supervisor_enabled():
        return None
    existing = report.get("_action_supervisor")
    if isinstance(existing, ActionSupervisor):
        return existing
    out_dir = report.get("_attempt_cycle_dir") or report.get("attempt_dir")
    if not out_dir:
        try:
            from fill_step_log import get_step_log

            step_log = get_step_log(report)
            if step_log is not None:
                out_dir = step_log.out_dir
        except Exception:
            out_dir = None
    if not out_dir:
        try:
            from fill_step_log import DEFAULT_RESULTS

            rid = report.get("run_id") or report.get("alias_token") or "run"
            out_dir = DEFAULT_RESULTS / f"fill_steps_{rid}"
        except Exception:
            return None
    report["_attempt_cycle_dir"] = str(out_dir)
    sup = ActionSupervisor(out_dir)
    report["_action_supervisor"] = sup
    return sup


def get_action_supervisor(report: dict | None) -> ActionSupervisor | None:
    if not report:
        return None
    sup = report.get("_action_supervisor")
    return sup if isinstance(sup, ActionSupervisor) else None


async def audit_fill_row(
    page: Any,
    report: dict | None,
    row: dict[str, Any],
    *,
    before: str | None = None,
    intent: str | None = None,
    locator: Any = None,
) -> dict[str, Any] | None:
    """Convenience: audit a fill result dict from ``_fill_selector`` / pack."""
    if not report or not supervisor_enabled():
        return None
    sup = get_action_supervisor(report) or attach_action_supervisor(report)
    if sup is None:
        return None

    from workday_date_readback import is_date_spin_context, normalize_spin_readback

    ftype = str(row.get("type") or "")
    sel = str(row.get("selector") or "")
    aid = str(row.get("automation_id") or "")
    field = aid or ftype or sel
    want = (intent if intent is not None else str(row.get("value") or "")).strip()
    b = (before if before is not None else str(row.get("readback_before") or "")).strip()
    # Prefer real DOM/chip evidence only — never fall back to intent ``value``
    # (that made ADDRESS_STATE look OK:empty_to_filled while still missed).
    raw_rb = (
        row.get("readback")
        if row.get("readback") is not None
        else row.get("picked")
        if row.get("picked") is not None
        else row.get("verified_value")
        if row.get("verified_value") is not None
        else row.get("option_text")
        if row.get("option_text") is not None
        else ""
    )
    widget = str(row.get("widget") or "")
    mode = str(row.get("mode") or "")
    action = mode or "fill"
    spin_ctx = is_date_spin_context(
        field_type=ftype,
        action=action,
        widget=widget,
        mode=mode,
        readback=raw_rb,
    )
    if spin_ctx:
        action = "date_spin"
        if not ftype:
            ftype = "EXPERIENCE_DATE"
        a = normalize_spin_readback(raw_rb)
    else:
        a = str(raw_rb or "").strip()
    locked = bool(row.get("skipped_locked") or row.get("reason") == "field_locked_skip")

    if locator is None and page is not None and sel and not locked:
        try:
            locator = page.locator(sel).first
            if await locator.count() == 0:
                locator = None
        except Exception:
            locator = None

    fix_fn: FixFn | None = None
    ftype_u = (ftype or "").upper()
    fos_combo = mode in (
        "combobox",
        "fos_reclaim",
        "typable_edu_prompt",
        "fiber_search_select",
    )
    if page is not None and want and ftype_u in ("FIELD_OF_STUDY", "DISCIPLINE", "MAJOR") and fos_combo:
        aid_ref = (aid or field or "formField-fieldOfStudy").replace(
            "education/", ""
        )
        val_ref = want

        async def _fos_fix() -> str:
            from exp_workday_selectors import _fos_candidates, _reclaim_fos_on_wrap

            cands = _fos_candidates({ftype_u: val_ref, "FIELD_OF_STUDY": val_ref})
            result = await _reclaim_fos_on_wrap(
                page, aid_ref, val_ref, cands, report=report
            )
            rb = str(result.get("readback") or "")
            if rb:
                row["readback"] = rb[:120]
            if result.get("verified"):
                row["verified"] = True
                row["ok"] = True
            return rb

        fix_fn = _fos_fix
    elif locator is not None and want and action in ("fill", "combobox", "select", "file"):
            loc_ref = locator
            val_ref = want

            async def _fix() -> str:
                await loc_ref.fill(val_ref, timeout=4000)
                rb = await _read_locator_readback(loc_ref)
                row["readback"] = rb[:120] if rb else ""
                row["value"] = val_ref
                return rb

            fix_fn = _fix

    result = await sup.audit_after_action(
        report,
        field=field,
        field_type=ftype,
        intent=want,
        before=b,
        after=a,
        action=action,
        selector=sel,
        automation_id=aid,
        locked=locked,
        locator=locator,
        fix_fn=fix_fn if want else None,
        via=str(row.get("via") or "")[:64],
        page=page,
    )

    if result.get("skip_field"):
        row["supervisor_skip"] = True
        row["skipped_locked"] = True
        row["reason"] = "supervisor_thrash_lock"
    if result.get("stuck"):
        row["supervisor_stuck"] = True
        row["verified"] = False
        row["ok"] = False
    elif result.get("supervisor_verdict") == "WRONG":
        row["verified"] = False
        row["ok"] = False
        row["skipped_already_correct"] = False
    elif result.get("supervisor_verdict") == "OK" and result.get("readback"):
        row["readback"] = result["readback"]

    return result


def self_test() -> None:
    import asyncio
    import tempfile

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as td:
            report: dict[str, Any] = {"_attempt_cycle_dir": td}
            sup = attach_action_supervisor(report)
            assert sup is not None

            ok = await sup.audit_after_action(
                report,
                field="EMAIL",
                field_type="EMAIL",
                intent="test@example.com",
                before="test@example.com",
                after="test@example.com",
                action="fill",
            )
            assert ok["supervisor_verdict"] == "OK"
            assert ok["fix_attempted"] is False

            thrash = await sup.audit_after_action(
                report,
                field="workExperience-1/jobTitle",
                field_type="EXPERIENCE_TITLE",
                intent="Applied AI/ML Analyst",
                before="Applied AI/ML Analyst",
                after="Senior ML Engineer",
                action="fill",
            )
            assert thrash["supervisor_verdict"] == "THRASH"

            assert sup.audit_path.is_file()
            lines = sup.audit_path.read_text().strip().splitlines()
            assert len(lines) >= 2

    asyncio.run(_run())
    print("action_supervisor.self_test: OK")


if __name__ == "__main__":
    self_test()
